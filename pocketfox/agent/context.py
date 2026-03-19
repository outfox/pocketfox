"""Context builder for assembling agent prompts using LOOM."""

import platform
from pathlib import Path
from typing import Any, ClassVar

from loguru import logger
from loom import Context, Entry, StringEntry

from pocketfox.agent.entries import DateTimeEntry, ImageEntry, KeptFileEntry
from pocketfox.agent.memory import MemoryStore
from pocketfox.agent.skills import SkillsLoader


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

    def __init__(
        self,
        workspace: Path,
        default_context_files: list[str] | None = None,
        context_files_map: dict[str, list[str]] | None = None,
    ):
        self.workspace = workspace
        self.memory = MemoryStore(workspace)
        self.skills = SkillsLoader(workspace)
        self._default_files = tuple(default_context_files or ["AGENTS.md", "TOOLS.md"])
        self._context_files_map = {
            k: tuple(v) for k, v in (context_files_map or {}).items()
        }
        self._contexts: dict[tuple[str, ...], Context] = {}
        self._entry_counter: int = 0

    def _resolve_files(self, context_key: str | None) -> tuple[str, ...]:
        """Resolve context_key to the tuple of files to load."""
        if context_key and context_key in self._context_files_map:
            return self._context_files_map[context_key]
        return self._default_files

    @property
    def context(self) -> Context:
        """
        Get the persistent LOOM Context for the default context key.

        Returns:
            The default Context instance.
        """
        return self._get_or_create_context(self._default_files)

    def _get_or_create_context(self, files: tuple[str, ...]) -> Context:
        """Get or create a Context for the given file list."""
        if files not in self._contexts:
            self._contexts[files] = self._create_context(list(files))
        return self._contexts[files]

    def _create_context(self, context_files: list[str]) -> Context:
        """Create a new LOOM Context with sections populated from the given file list."""
        ctx = Context("agent")

        # Foundation: Core identity (always) + configured files
        ctx.foundation.add(
            StringEntry(
                self._get_identity(),
                name="identity",
            )
        )

        include_memory = False
        for filename in context_files:
            if filename == self.MEMORY_FILENAME:
                include_memory = True
                if self.memory.memory_file.exists():
                    ctx.foundation.add(
                        KeptFileEntry(path=self.memory.memory_file, name="Long-term Memory")
                    )
                continue
            file_path = self.workspace / filename
            if file_path.exists():
                ctx.foundation.add(KeptFileEntry(path=file_path, name=filename))

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
                " To use a skill, read its SKILL.md file using the read_file tool.\n"
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
                ctx.topic.add(KeptFileEntry(path=today_file, name="Today's Notes"))

        # Attention: Volatile data (current time)
        ctx.attention.add(DateTimeEntry(name="Current Time"))

        return ctx

    def add_entry(
        self,
        section: str,
        content: str | Entry,
        name: str | None = None,
    ) -> str:
        """
        Add an entry to a section of the persistent context.

        Args:
            section: Section name (foundation, focus, topic, step, attention).
            content: Text content (creates StringEntry) or a pre-built Entry.
            name: Optional name for the entry (ignored if content is an Entry).

        Returns:
            The entry ID (for later removal).

        Raises:
            ValueError: If section name is invalid.
        """
        ctx = self.context
        section_obj = getattr(ctx, section, None)
        if section_obj is None:
            raise ValueError(
                f"Invalid section: {section}. Valid: foundation, focus, topic, step, attention"
            )

        self._entry_counter += 1
        entry_id = f"entry_{self._entry_counter}"

        if isinstance(content, Entry):
            entry = content
        else:
            entry_name = name or entry_id
            entry = StringEntry(content, name=entry_name)
        entry._runtime_id = entry_id  # Tag for later removal
        section_obj.add(entry)

        return entry_id

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
                if getattr(entry, "_runtime_id", None) == entry_id:
                    section.entries.pop(i)
                    return True
        return False

    def clear_kept_entries(self) -> int:
        """Remove all kept entries (images, files) from all persistent contexts.

        Returns:
            Number of entries removed.
        """
        removed = 0
        kept_types = (ImageEntry, KeptFileEntry)
        for ctx in self._contexts.values():
            for section_name in ("foundation", "focus", "topic", "step", "attention"):
                section = getattr(ctx, section_name)
                before = len(section.entries)
                section.entries = [e for e in section.entries if not isinstance(e, kept_types)]
                removed += before - len(section.entries)
        if removed:
            logger.info(f"Cleared {removed} kept entry/entries from context")
        return removed

    # Backward-compatible alias
    clear_kept_images = clear_kept_entries

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
            entry_id = getattr(entry, "_runtime_id", None)
            compiled = entry.compile() if hasattr(entry, "compile") else str(entry)
            preview = compiled[:100] + "..." if len(compiled) > 100 else compiled
            result.append(
                {
                    "id": entry_id,
                    "name": entry.name,
                    "preview": preview,
                }
            )
        return result

    def build_context(
        self,
        channel: str | None = None,
        chat_id: str | None = None,
        context_key: str | None = None,
    ) -> Context:
        """
        Get the persistent LOOM Context, updating session info if provided.

        Args:
            channel: Current channel (telegram, feishu, etc.).
            chat_id: Current chat/user ID.
            context_key: Selects which context_files config to use.
                Falls back to channel name, then default.

        Returns:
            The persistent LOOM Context.
        """
        effective_key = context_key or channel
        ctx = self._get_or_create_context(self._resolve_files(effective_key))

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
        context_key: str | None = None,
    ) -> str:
        """
        Build the system prompt from LOOM context.

        Args:
            skill_names: Optional list of skills to include (unused, for API compat).
            channel: Current channel.
            chat_id: Current chat/user ID.
            context_key: Selects which context_files config to use.

        Returns:
            Complete system prompt as string.
        """
        ctx = self.build_context(channel=channel, chat_id=chat_id, context_key=context_key)
        return ctx.render()

    def _get_identity(self) -> str:
        """Get the core identity section (stable, no volatile data)."""
        workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        os_name = "macOS" if system == "Darwin" else system
        runtime = f"{os_name} {platform.machine()}, Python {platform.python_version()}"

        return f"""# pocketfox 🦊

You are pocketfox, a helpful AI assistant. You have access to tools that allow you to:
- Read, write, and edit files
- Execute shell commands
- Search the web and fetch web pages
- Send messages to users on chat channels
- Spawn subagents for complex background tasks

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
        current_message: str,
        skill_names: list[str] | None = None,
        media: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        cache_ttl: int | None = None,
        context_key: str | None = None,
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
            channel: Current channel (telegram, feishu, etc.).
            chat_id: Current chat/user ID.

        Returns:
            List of messages including system prompt.
        """
        ctx = self.build_context(channel=channel, chat_id=chat_id, context_key=context_key)

        # System prompt: foundation, focus, topic only (stable parts)
        # Cache breakpoint after topic - this caches the entire system prompt
        messages = ctx.to_messages(
            cache_breakpoints=["topic"],
            clear_volatile=False,  # We'll handle step/attention separately
            cache_ttl=cache_ttl,
        )

        # Add history with cache breakpoint on the LAST message
        # This allows Anthropic to cache the entire conversation prefix
        if history:
            breakpoint_idx = len(history) - 1
            cc: dict = {"type": "ephemeral"}
            if cache_ttl is not None:
                cc["max_age_seconds"] = cache_ttl

            for i, msg in enumerate(history):
                if i == breakpoint_idx:
                    # Add cache breakpoint to this message
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        messages.append(
                            {
                                "role": msg["role"],
                                "content": [
                                    {
                                        "type": "text",
                                        "text": content,
                                        "cache_control": cc,
                                    }
                                ],
                            }
                        )
                    else:
                        # Already a list (e.g., with images), add cache_control to last text block
                        content_copy = list(content)
                        for j in range(len(content_copy) - 1, -1, -1):
                            if content_copy[j].get("type") == "text":
                                content_copy[j] = {
                                    **content_copy[j],
                                    "cache_control": cc,
                                }
                                break
                        messages.append({"role": msg["role"], "content": content_copy})
                else:
                    messages.append(msg)

        # Build current user message with attention (volatile datetime) appended
        attention_content = self._compile_attention(ctx)
        user_content = self._build_user_content(current_message, media)

        # Combine user content with attention
        if isinstance(user_content, str):
            if attention_content:
                user_content = f"{user_content}\n\n{attention_content}"
            messages.append({"role": "user", "content": user_content})
        else:
            # user_content is a list (has images)
            if attention_content:
                user_content.append({"type": "text", "text": f"\n\n{attention_content}"})
            messages.append({"role": "user", "content": user_content})

        # Step content (tool outputs, session info) appended after user message
        step_content = self._compile_step(ctx)
        if step_content:
            # Append step as additional context in the user message
            # This keeps it after attention but before assistant response
            last_msg = messages[-1]
            if isinstance(last_msg["content"], str):
                last_msg["content"] = f"{last_msg['content']}\n\n{step_content}"
            else:
                last_msg["content"].append({"type": "text", "text": f"\n\n{step_content}"})

        # Inject kept image blocks into the current user message
        # (must be in a user message — system messages only support text blocks)
        self._inject_image_blocks(messages, ctx)

        # Clear volatile sections now that we've used them
        ctx.step.clear()

        return messages

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

    def _inject_image_blocks(self, messages: list[dict[str, Any]], ctx: Context) -> None:
        """Inject kept images as a dedicated user/assistant pair before the current user message.

        This places the image blocks in the cached history region so they are
        not re-tokenized on every turn.  The pair sits just before the final
        (volatile) user message, maintaining the required user/assistant
        alternation for the Anthropic API.
        """
        image_entries = [e for e in ctx.topic.entries if isinstance(e, ImageEntry)]
        if not image_entries:
            return

        # Build content blocks: all kept images + a label
        image_blocks: list[dict[str, Any]] = []
        for entry in image_entries:
            image_blocks.extend(entry.compile_blocks())
        image_blocks.append({"type": "text", "text": "[Kept images for reference]"})

        # Insert a user/assistant pair just before the final user message
        image_user_msg: dict[str, Any] = {"role": "user", "content": image_blocks}
        image_ack_msg: dict[str, Any] = {"role": "assistant", "content": "Noted."}
        messages.insert(-1, image_user_msg)
        messages.insert(-1, image_ack_msg)

    def _build_user_content(self, text: str, media: list[str] | None) -> str | list[dict[str, Any]]:
        """Build user message content with optional base64-encoded images."""
        if not media:
            return text

        from pocketfox.utils.image import encode_image_file

        images = []
        notes: list[str] = []
        for path in media:
            p = Path(path)
            if not p.is_file():
                continue
            result = encode_image_file(p)
            if result is None:
                notes.append(f"(image {p.name} skipped: exceeds 5 MB limit)")
                continue
            data_uri, _b64, _mime, reencoded = result
            if reencoded:
                notes.append(f"(image {p.name} re-encoded to jpeg)")
            images.append(
                {
                    "type": "image_url",
                    "image_url": {"url": data_uri},
                }
            )

        if notes:
            text = text + "\n" + "\n".join(notes) if text else "\n".join(notes)

        if not images:
            return text
        return images + [{"type": "text", "text": text}]

    def add_tool_result(
        self,
        messages: list[dict[str, Any]],
        tool_call_id: str,
        tool_name: str,
        result: str | list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Add a tool result to the message list.

        Args:
            messages: Current message list.
            tool_call_id: ID of the tool call.
            tool_name: Name of the tool.
            result: Tool execution result — either a string or a list of
                content blocks (for multimodal results like images).

        Returns:
            Updated message list.
        """
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "name": tool_name,
                "content": result,
            }
        )
        return messages

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
            tool_calls: Optional tool calls.
            reasoning_content: Thinking output (Kimi, DeepSeek-R1, etc.).

        Returns:
            Updated message list.
        """
        msg: dict[str, Any] = {"role": "assistant", "content": content or ""}

        if tool_calls:
            msg["tool_calls"] = tool_calls

        # Thinking models reject history without this
        if reasoning_content:
            msg["reasoning_content"] = reasoning_content

        messages.append(msg)
        return messages
