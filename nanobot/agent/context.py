"""Context builder for assembling agent prompts using LOOM."""

import base64
import mimetypes
import platform
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from loom import Context, Entry, StringEntry, FileEntry

from nanobot.agent.memory import MemoryStore
from nanobot.agent.skills import SkillsLoader


class DateTimeEntry(Entry):
    """
    A volatile entry that renders the current date/time.
    
    Always returns a unique identity to prevent caching — this entry
    should be placed after all cache breakpoints to avoid invalidating
    the cached prefix.
    """
    
    def __init__(self, fmt: str = "%Y-%m-%d %H:%M (%A)", name: str | None = None):
        """
        Args:
            fmt: strftime format string.
            name: Entry name (default: "Current Time").
        """
        super().__init__(name or "Current Time")
        self._fmt = fmt
    
    def compile(self) -> str:
        """Compile to current timestamp."""
        return datetime.now().strftime(self._fmt)
    
    def identity(self) -> str:
        """Always unique — volatile entry, never deduplicated."""
        return f"datetime:{id(self)}"
    
    def __repr__(self) -> str:
        return f"DateTimeEntry(fmt={self._fmt!r})"


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
    
    BOOTSTRAP_FILES: ClassVar[list[str]] = [
        "AGENTS.md",
        "SOUL.md",
        "USER.md",
        "TOOLS.md",
        "IDENTITY.md",
        "SECRETS.md",
    ]
    
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.memory = MemoryStore(workspace)
        self.skills = SkillsLoader(workspace)
        self._context: Context | None = None
        self._entry_counter: int = 0
    
    @property
    def context(self) -> Context:
        """
        Get the persistent LOOM Context, creating it if needed.
        
        Returns:
            The persistent Context instance.
        """
        if self._context is None:
            self._context = self._create_context()
        return self._context
    
    def _create_context(self) -> Context:
        """Create a new LOOM Context with all static sections populated."""
        ctx = Context("agent")
        
        # Foundation: Core identity + bootstrap files + long-term memory
        ctx.foundation.add(StringEntry(
            self._get_identity(),
            name="identity",
        ))
        
        for filename in self.BOOTSTRAP_FILES:
            file_path = self.workspace / filename
            if file_path.exists():
                ctx.foundation.add(FileEntry(
                    file_path,
                    name=filename,
                ))
        
        long_term_memory = self.memory.get_long_term_memory()
        if long_term_memory:
            ctx.foundation.add(StringEntry(
                long_term_memory,
                name="Long-term Memory",
            ))
        
        # Focus: Skills
        always_skills = self.skills.get_always_skills()
        if always_skills:
            always_content = self.skills.load_skills_for_context(always_skills)
            if always_content:
                ctx.focus.add(StringEntry(
                    always_content,
                    name="Active Skills",
                ))
        
        skills_summary = self.skills.build_skills_summary()
        if skills_summary:
            ctx.focus.add(StringEntry(
                f"""The following skills extend your capabilities. To use a skill, read its SKILL.md file using the read_file tool.
Skills with available="false" need dependencies installed first - you can try installing them with apt/brew.

{skills_summary}""",
                name="Skills",
            ))
        
        # Topic: Session-specific memory (daily notes)
        session_memory = self.memory.get_session_memory()
        if session_memory:
            ctx.topic.add(StringEntry(
                session_memory,
                name="Today's Notes",
            ))
        
        # Attention: Volatile data (current time)
        ctx.attention.add(DateTimeEntry(name="Current Time"))
        
        return ctx
    
    def add_entry(
        self,
        section: str,
        content: str,
        name: str | None = None,
    ) -> str:
        """
        Add an entry to a section of the persistent context.
        
        Args:
            section: Section name (foundation, focus, topic, step, attention).
            content: The text content to add.
            name: Optional name for the entry.
        
        Returns:
            The entry ID (for later removal).
        
        Raises:
            ValueError: If section name is invalid.
        """
        ctx = self.context
        section_obj = getattr(ctx, section, None)
        if section_obj is None:
            raise ValueError(f"Invalid section: {section}. Valid: foundation, focus, topic, step, attention")
        
        self._entry_counter += 1
        entry_id = f"entry_{self._entry_counter}"
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
            result.append({
                "id": entry_id,
                "name": entry.name,
                "preview": preview,
            })
        return result
    
    def build_context(
        self,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> Context:
        """
        Get the persistent LOOM Context, updating session info if provided.
        
        Args:
            channel: Current channel (telegram, feishu, etc.).
            chat_id: Current chat/user ID.
        
        Returns:
            The persistent LOOM Context.
        """
        ctx = self.context
        
        # Update session info in topic section (remove old, add new)
        # This is the only thing that changes per-request
        if channel and chat_id:
            # Remove existing session entry if present
            for i, entry in enumerate(ctx.topic.entries):
                if entry.name == "Current Session":
                    ctx.topic.entries.pop(i)
                    break
            
            ctx.topic.add(StringEntry(
                f"Channel: {channel}\nChat ID: {chat_id}",
                name="Current Session",
            ))
        
        return ctx
    
    def build_system_prompt(
        self,
        skill_names: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> str:
        """
        Build the system prompt from LOOM context.
        
        Args:
            skill_names: Optional list of skills to include (unused, for API compat).
            channel: Current channel.
            chat_id: Current chat/user ID.
        
        Returns:
            Complete system prompt as string.
        """
        ctx = self.build_context(channel=channel, chat_id=chat_id)
        return ctx.render()
    
    def _get_identity(self) -> str:
        """Get the core identity section (stable, no volatile data)."""
        workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"
        
        return f"""# nanobot 🐈

You are nanobot, a helpful AI assistant. You have access to tools that allow you to:
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

IMPORTANT: When responding to direct questions or conversations, reply directly with your text response.
Only use the 'message' tool when you need to send a message to a specific chat channel (like WhatsApp).
For normal conversation, just respond with text - do not call the message tool.

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
        ctx = self.build_context(channel=channel, chat_id=chat_id)
        
        # System prompt: foundation, focus, topic only (stable parts)
        # Cache breakpoint after topic - this caches the entire system prompt
        messages = ctx.to_messages(
            cache_breakpoints=["topic"],
            clear_volatile=False,  # We'll handle step/attention separately
        )
        
        # Add history with cache breakpoint on the LAST message
        # This allows Anthropic to cache the entire conversation prefix
        if history:
            breakpoint_idx = len(history) - 1
            
            for i, msg in enumerate(history):
                if i == breakpoint_idx:
                    # Add cache breakpoint to this message
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        messages.append({
                            "role": msg["role"],
                            "content": [{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}]
                        })
                    else:
                        # Already a list (e.g., with images), add cache_control to last text block
                        content_copy = list(content)
                        for j in range(len(content_copy) - 1, -1, -1):
                            if content_copy[j].get("type") == "text":
                                content_copy[j] = {**content_copy[j], "cache_control": {"type": "ephemeral"}}
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

    def _build_user_content(self, text: str, media: list[str] | None) -> str | list[dict[str, Any]]:
        """Build user message content with optional base64-encoded images."""
        if not media:
            return text
        
        images = []
        for path in media:
            p = Path(path)
            mime, _ = mimetypes.guess_type(path)
            if not p.is_file() or not mime or not mime.startswith("image/"):
                continue
            b64 = base64.b64encode(p.read_bytes()).decode()
            images.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
        
        if not images:
            return text
        return images + [{"type": "text", "text": text}]
    
    def add_tool_result(
        self,
        messages: list[dict[str, Any]],
        tool_call_id: str,
        tool_name: str,
        result: str
    ) -> list[dict[str, Any]]:
        """
        Add a tool result to the message list.
        
        Args:
            messages: Current message list.
            tool_call_id: ID of the tool call.
            tool_name: Name of the tool.
            result: Tool execution result.
        
        Returns:
            Updated message list.
        """
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": result
        })
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
