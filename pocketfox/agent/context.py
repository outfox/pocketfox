"""Context builder for assembling agent prompts using LOOM."""

import json
import platform
from pathlib import Path
from typing import Any, Callable, ClassVar

from loguru import logger
from loom.serialize.openai import render_message, render_tool_result

from loom import (
    CacheHint,
    Context,
    Entry,
    FileEntry,
    Message,
    StringEntry,
    TextPart,
    ToolCall,
    ToolResult,
    Transcript,
    encode_media_files,
)
from pocketfox.agent.entries import DateTimeEntry, ImageEntry
from pocketfox.agent.memory import MemoryStore
from pocketfox.agent.skills import SkillsLoader


def _decode_arguments(arguments: str | dict[str, Any]) -> dict[str, Any]:
    """Normalize a tool call's arguments to a dict (loop passes a JSON string)."""
    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except json.JSONDecodeError:
            return {"raw": arguments}
    return arguments or {}


class ContextBuilder:
    """
    Builds the context (system prompt + messages) for the agent.

    Uses LOOM to assemble bootstrap files, memory, skills, and conversation
    history into a coherent, cacheable context for the LLM.

    Message Structure (optimized for Anthropic prefix caching):

    [SYSTEM: foundation - focus - topic]     ← CACHE BREAKPOINT after topic
    [user msg 1]
    [assistant msg 1]
    [user msg 2]
    ...
    [last history user msg]                  ← CACHE BREAKPOINT (grows with convo)
    [current user msg + attention + step]    ← volatile, never cached

    LOOM Sections:
    - foundation: Core identity, bootstrap files (AGENTS.md, SOUL.md, etc.),
      and long-term memory (MEMORY.md) — large stable block (~15k+ tokens)
    - focus: Skills summary — stable per workspace
    - topic: Session-specific context (daily notes, group memory)
      (CACHE BREAKPOINT after topic — caches entire system prompt)
    - convo: NOT used in system prompt — conversation history is added as
      separate user/assistant messages with a cache breakpoint on the last
      history message, allowing the cached prefix to grow with the conversation
    - step: Session info (channel, chat_id) — appended to current user message
    - attention: Volatile data (current time) — appended to current user message

    This structure ensures:
    1. System prompt (~20k tokens) is cached on first request
    2. Conversation history is cached incrementally as it grows
    3. Only the current user message + volatile data is uncached

    Persistent Context:
    The ContextBuilder maintains a persistent LOOM Context that survives
    across message processing. Use add_entry() and remove_entry() to
    dynamically modify the context at runtime.
    """

    MEMORY_FILENAME: ClassVar[str] = "MEMORY.md"

    # Name used for the default context when none is specified
    DEFAULT_CONTEXT_NAME: ClassVar[str] = "_default"

    def __init__(
        self,
        workspace: Path,
        default_context_files: list[str] | None = None,
        max_document_bytes: int = 10 * 1024 * 1024,
        wire_format: str = "openai",
    ):
        self.workspace = workspace
        self.memory = MemoryStore(workspace)
        self.skills = SkillsLoader(workspace)
        self._default_files = tuple(default_context_files or ["AGENTS.md", "TOOLS.md"])
        self._contexts: dict[str, Context] = {}
        self._runtime_entry_ids: set[str] = set()  # loom entry .id values added via add_entry()
        self.max_document_bytes = max_document_bytes
        # Wire format loom serializes to. The live litellm path is OpenAI-shaped;
        # "anthropic" is available for a future native transport.
        self.wire_format = wire_format

    @property
    def context(self) -> Context:
        """
        Get the persistent LOOM Context for the default context.

        Returns:
            The default Context instance.
        """
        return self._get_or_create_context(self.DEFAULT_CONTEXT_NAME, self._default_files)

    def _get_or_create_context(
        self,
        context_name: str,
        context_files: tuple[str, ...],
        prologue: str | None = None,
    ) -> Context:
        """Get or create a Context keyed by context_name."""
        if context_name not in self._contexts:
            self._contexts[context_name] = self._create_context(list(context_files), prologue=prologue)
        return self._contexts[context_name]

    def _create_context(self, context_files: list[str], prologue: str | None = None) -> Context:
        """Create a new LOOM Context with sections populated from the given file list."""
        ctx = Context("agent")

        # Foundation: Core identity (always) + configured files
        ctx.foundation.add(
            StringEntry(
                self._get_identity(),
                name="identity",
            )
        )

        if prologue:
            ctx.foundation.add(StringEntry(prologue, name="Context Prologue"))

        include_memory = False
        for filename in context_files:
            if filename == self.MEMORY_FILENAME:
                include_memory = True
                if self.memory.memory_file.exists():
                    ctx.foundation.add(FileEntry(path=self.memory.memory_file, name="Long-term Memory"))
                continue
            file_path = self.workspace / filename
            if file_path.exists():
                ctx.foundation.add(FileEntry(path=file_path, name=filename))

        # Focus: Skills (always loaded)
        always_skills = self.skills.get_always_skills()
        if always_skills:
            always_content = self.skills.load_skills_for_context(always_skills)
            if always_content:
                ctx.focus.add(
                    StringEntry(
                        always_content,
                        name="Active Skills",
                    )
                )

        skills_summary = self.skills.build_skills_summary()
        if skills_summary:
            skills_preamble = (
                "The following skills extend your capabilities."
                " To use a skill, read its SKILL.md file using the fs_read tool.\n"
                'Skills with available="false" need dependencies installed'
                " first - you can try installing them with apt/brew."
            )
            ctx.focus.add(
                StringEntry(
                    f"{skills_preamble}\n\n{skills_summary}",
                    name="Skills",
                )
            )

        # Topic: Daily notes (only if MEMORY.md is in context_files)
        if include_memory:
            today_file = self.memory.get_today_file()
            if today_file.exists():
                ctx.topic.add(FileEntry(path=today_file, name="Today's Notes"))

        # Attention: Volatile data (current time)
        ctx.attention.add(DateTimeEntry(name="Current Time"))

        return ctx

    def add_entry(
        self,
        section: str,
        content: str | Entry,
        name: str | None = None,
        context_name: str | None = None,
        context_files: tuple[str, ...] | None = None,
    ) -> str:
        """
        Add an entry to a section of the persistent context.

        Args:
            section: Section name (foundation, focus, topic, step, attention).
            content: Text content (creates StringEntry) or a pre-built Entry.
            name: Optional name for the entry (ignored if content is an Entry).
            context_name: Target context (default if None).
            context_files: Files for context creation (used with context_name).

        Returns:
            The entry ID (for later removal).

        Raises:
            ValueError: If section name is invalid.
        """
        if context_name:
            files = context_files or self._default_files
            ctx = self._get_or_create_context(context_name, files)
        else:
            ctx = self.context
        section_obj = getattr(ctx, section, None)
        if section_obj is None:
            raise ValueError(f"Invalid section: {section}. Valid: foundation, focus, topic, step, attention")

        if isinstance(content, Entry):
            entry = content
        else:
            entry = StringEntry(content, name=name)
        self._runtime_entry_ids.add(entry.id)
        section_obj.add(entry)

        return entry.id

    def remove_entry(self, entry_id: str) -> bool:
        """
        Remove an entry from the persistent context by its ID.

        Args:
            entry_id: The entry ID returned by add_entry().

        Returns:
            True if entry was found and removed, False otherwise.
        """
        ctx = self.context
        for section_name in ("foundation", "focus", "topic", "step", "attention"):
            section = getattr(ctx, section_name)
            for i, entry in enumerate(section.entries):
                if entry.id == entry_id:
                    section.entries.pop(i)
                    self._runtime_entry_ids.discard(entry_id)
                    return True
        return False

    def clear_kept_entries(self) -> int:
        """Remove all kept entries (images, files) from all persistent contexts.

        Returns:
            Number of entries removed.
        """
        removed = 0
        removed_ids: set[str] = set()
        for ctx in self._contexts.values():
            for section_name in ("foundation", "focus", "topic", "step", "attention"):
                section = getattr(ctx, section_name)
                kept, cleared = [], []
                for e in section.entries:
                    is_runtime_file = isinstance(e, FileEntry) and e.id in self._runtime_entry_ids
                    if isinstance(e, ImageEntry) or is_runtime_file:
                        cleared.append(e)
                    else:
                        kept.append(e)
                section.entries = kept
                removed += len(cleared)
                removed_ids.update(e.id for e in cleared if e.id)
        self._runtime_entry_ids -= removed_ids
        if removed:
            logger.info(f"Cleared {removed} kept entry/entries from context")
        return removed

    # Backward-compatible alias
    clear_kept_images: Callable[..., int] = clear_kept_entries

    def list_entries(self, section: str) -> list[dict[str, str]]:
        """
        List all entries in a section.

        Args:
            section: Section name.

        Returns:
            List of dicts with 'id', 'name', and 'preview' keys.
        """
        ctx = self.context
        section_obj = getattr(ctx, section, None)
        if section_obj is None:
            raise ValueError(f"Invalid section: {section}")

        result = []
        for entry in section_obj.entries:
            compiled = entry.compile() if hasattr(entry, "compile") else str(entry)
            preview = compiled[:100] + "..." if len(compiled) > 100 else compiled
            result.append(
                {
                    "id": entry.id if entry.id in self._runtime_entry_ids else None,
                    "name": entry.name,
                    "preview": preview,
                }
            )
        return result

    def build_context(
        self,
        channel: str | None = None,
        chat_id: str | None = None,
        context_name: str | None = None,
        context_files: tuple[str, ...] | None = None,
        prologue: str | None = None,
    ) -> Context:
        """
        Get the persistent LOOM Context, updating session info if provided.

        Args:
            channel: Current channel (telegram, feishu, etc.).
            chat_id: Current chat/user ID.
            context_name: Name to key the context cache by.
            context_files: Files to load for this context.
            prologue: Optional system prompt describing this context.

        Returns:
            The persistent LOOM Context.
        """
        name = context_name or self.DEFAULT_CONTEXT_NAME
        files = context_files or self._default_files
        ctx = self._get_or_create_context(name, files, prologue=prologue)

        # Update session info in topic section (remove old, add new)
        # This is the only thing that changes per-request
        if channel and chat_id:
            # Remove existing session entry if present
            for i, entry in enumerate(ctx.topic.entries):
                if entry.name == "Current Session":
                    ctx.topic.entries.pop(i)
                    break

            ctx.topic.add(
                StringEntry(
                    f"Channel: {channel}\nChat ID: {chat_id}",
                    name="Current Session",
                )
            )

        return ctx

    def build_system_prompt(
        self,
        skill_names: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        context_name: str | None = None,
        context_files: tuple[str, ...] | None = None,
        prologue: str | None = None,
    ) -> str:
        """
        Build the system prompt from LOOM context.

        Args:
            skill_names: Optional list of skills to include (unused, for API compat).
            channel: Current channel.
            chat_id: Current chat/user ID.
            context_name: Name to key the context cache by.
            context_files: Files to load for this context.
            prologue: Optional system prompt describing this context.

        Returns:
            Complete system prompt as string.
        """
        ctx = self.build_context(
            channel=channel,
            chat_id=chat_id,
            context_name=context_name,
            context_files=context_files,
            prologue=prologue,
        )
        return ctx.render()

    def _get_identity(self) -> str:
        """Get the core identity section (stable, no volatile data)."""
        workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        os_name = "macOS" if system == "Darwin" else system
        runtime = f"{os_name} {platform.machine()}, Python {platform.python_version()}"

        return f"""# You are an autonomous AI assistant.
You have access to tools that allow you to:
- Read, write, and edit files
- Execute shell commands
- Search the web and fetch web pages
- Send messages to users on chat channels
- Spawn subagents for complex background tasks

## User Identification
Each chat message is attributed to its sender (the author's name travels with
the message; some channels also show it inline as a "[username]" prefix, e.g.
"[alice] hello"). Use the sender to tell different users apart, especially in
group chats. Always address users by name when it helps clarify who you're
responding to.

## Multimodal input
When a user sends an image, audio clip, or video, it is already attached as
a content block in the same message — you can see or hear it directly with
your native multimodal capabilities. A bracketed label like
"[video attached: tiger_clip.mp4]" is just a filename hint; the actual media
is already in your context.

Do NOT try to re-process attached media by running ffmpeg, extracting
frames, using image libraries, or calling fs_read/shell_exec/fs_view_image on it.
Trust that the content is already in your context and respond as if you
naturally perceived it. Only fall back to tool-based processing if the user
explicitly points you at a path to a file that was NOT attached to the
current message (e.g. "summarise the mp4 at ~/downloads/foo.mp4").

## Current Time
If relevant, the current date and time can be found at the end of the context.

## Runtime
{runtime}

## Workspace
Your workspace is at: {workspace_path}
- Memory files: {workspace_path}/memory/MEMORY.md
- Daily notes: {workspace_path}/memory/YYYY-MM-DD.md
- Custom skills: {workspace_path}/skills/{{skill-name}}/SKILL.md

IMPORTANT: When responding to direct questions or conversations,
reply directly with your text response.
Only use the 'message' tool when you need to send a message to a
specific chat channel (like WhatsApp).
For normal conversation, just respond with text - do not call
the message tool.

Always be helpful, accurate, and concise. When using tools, explain what you're doing.
When remembering something, write to {workspace_path}/memory/MEMORY.md"""

    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str | None = None,
        skill_names: list[str] | None = None,
        media: list[str] | None = None,
        sender: str | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        cache_ttl: int | None = None,
        context_name: str | None = None,
        context_files: tuple[str, ...] | None = None,
        prologue: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Build the complete message list for an LLM call.

        Structure (optimized for Anthropic prompt caching):
        [SYSTEM: foundation - focus - topic] ← cache breakpoint after topic
        [history user/assistant messages]    ← cache breakpoint on second-to-last
        [current user message + attention]   ← volatile (datetime etc.)
        [step]                               ← volatile (tool outputs etc.)

        Args:
            history: Previous conversation messages.
            current_message: The new user message.
            skill_names: Optional skills to include.
            media: Optional list of local file paths for images/media.
            sender: Optional human-readable author of the current message. Set
                as the message's structured sender so the wire format attributes
                it (OpenAI ``name`` field / Anthropic ``[name]`` prefix).
            channel: Current channel (telegram, feishu, etc.).
            chat_id: Current chat/user ID.
            context_name: Name to key the context cache by.
            context_files: Files to load for this context.

        Returns:
            List of messages including system prompt.
        """
        ctx = self.build_context(
            channel=channel,
            chat_id=chat_id,
            context_name=context_name,
            context_files=context_files,
            prologue=prologue,
        )

        # loom owns the transcript: the system context (foundation/focus/topic/
        # convo, with cache breakpoints after foundation and topic) plus the
        # ordered conversation. The serializer renders it to self.wire_format.
        transcript = Transcript(
            context=ctx,
            system_sections=["foundation", "focus", "topic", "convo"],
            system_cache_breakpoints=["foundation", "topic"],
            cache_ttl=cache_ttl,
        )

        # History — cache breakpoint on the LAST message so the conversation
        # prefix caches incrementally as it grows.
        if history:
            last_idx = len(history) - 1
            for i, msg in enumerate(history):
                m = self._history_message(msg)
                if i == last_idx:
                    m.cache = CacheHint(ttl=cache_ttl)
                transcript.add(m)

        # Kept images become a user/assistant pair injected just before the
        # final (volatile) user message — placing them in the cached region so
        # they are not re-tokenized every turn.
        kept = [e for e in ctx.topic.entries if isinstance(e, ImageEntry)]
        if kept:
            transcript.messages.extend(self._kept_image_messages(kept))

        # Current user message: user text + media, then volatile attention/step.
        # When current_message is None (context snapshot) we stop after kept
        # images — no user turn, no volatile data.
        if current_message is not None:
            parts, notes = encode_media_files(media, max_document_bytes=self.max_document_bytes)
            text = current_message
            if notes:
                text = (text + "\n" + "\n".join(notes)) if text else "\n".join(notes)
            user_parts: list[Any] = [*parts, TextPart(text)]

            attention_content = self._compile_attention(ctx)
            if attention_content:
                user_parts.append(TextPart(attention_content))
            step_content = self._compile_step(ctx)
            if step_content:
                user_parts.append(TextPart(step_content))

            transcript.add(Message(role="user", content=user_parts, name=sender))

        # Clear volatile sections now that we've used them
        ctx.step.clear()

        return transcript.to_messages(format=self.wire_format)

    def _history_message(self, msg: dict[str, Any]) -> Message:
        """Build a loom Message from a persisted history dict (rebuilding media)."""
        role = msg["role"]
        content = msg.get("content", "")
        name = msg.get("name")
        media = msg.get("media")
        if media and role == "user":
            parts, notes = encode_media_files(media, max_document_bytes=self.max_document_bytes)
            text = content
            if notes:
                text = (text + "\n" + "\n".join(notes)) if text else "\n".join(notes)
            return Message(role=role, content=[*parts, TextPart(text)], name=name)
        return Message(role=role, content=[TextPart(content)], name=name)

    def _kept_image_messages(self, kept: list[ImageEntry]) -> list[Message]:
        """Build the kept-image user/assistant pair from topic ImageEntry items."""
        parts: list[Any] = []
        for entry in kept:
            parts.append(entry.to_image_part())
            parts.append(TextPart(entry.compile()))
        parts.append(TextPart("[Kept images for reference]"))
        return [Message(role="user", content=parts), Message.text("assistant", "Noted.")]

    def _compile_attention(self, ctx: Context) -> str:
        """Compile the attention section (volatile datetime etc.)."""
        seen: set[str] = set()
        parts = []
        if content := ctx.attention.compile(seen):
            parts.append(content)
        return "\n\n".join(parts) if parts else ""

    def _compile_step(self, ctx: Context) -> str:
        """Compile the step section (session info, tool outputs)."""
        if content := ctx.step.compile():
            return content
        return ""

    def add_tool_result(
        self,
        messages: list[dict[str, Any]],
        tool_call_id: str,
        tool_name: str,
        result: str | list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Add a tool result to the message list.

        For multimodal results (list of content blocks containing images),
        the text portion goes into the tool result and any image blocks are
        returned to the caller so they can be appended *after* every tool
        result for the current assistant turn has been written. This is
        required by Anthropic, which mandates that all tool_result blocks for
        a given assistant tool_use message live in the single user message
        immediately following it — interleaving image-bearing user messages
        between tool_result blocks breaks that contract.

        Args:
            messages: Current message list.
            tool_call_id: ID of the tool call.
            tool_name: Name of the tool.
            result: Tool execution result — either a string or a list of
                content blocks (for multimodal results like images).

        Returns:
            Image content blocks from this tool result that the caller should
            buffer and append in a single follow-up user message after all
            tool_results for the current assistant turn have been written.
            Empty list when there are no images.
        """
        tr = ToolResult.from_tool_output(tool_call_id, tool_name, result)
        tool_dict, image_blocks = render_tool_result(tr)
        messages.append(tool_dict)
        return image_blocks

    def add_assistant_message(
        self,
        messages: list[dict[str, Any]],
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Add an assistant message to the message list.

        Args:
            messages: Current message list.
            content: Message content.
            tool_calls: Optional tool calls (OpenAI function-call dict shape).
            reasoning_content: Thinking output (Kimi, DeepSeek-R1, etc.).

        Returns:
            Updated message list.
        """
        if tool_calls:
            calls = [
                ToolCall(
                    id=tc["id"],
                    name=tc["function"]["name"],
                    arguments=_decode_arguments(tc["function"]["arguments"]),
                )
                for tc in tool_calls
            ]
            msg = Message.assistant_tool_call(content, calls, reasoning=reasoning_content)
        else:
            msg = Message(
                role="assistant",
                content=[TextPart(content or "")],
                reasoning=reasoning_content,
            )
        messages.extend(render_message(msg))
        return messages
