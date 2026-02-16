"""Context builder for assembling agent prompts using LOOM."""

import base64
import mimetypes
import platform
from pathlib import Path
from typing import Any, ClassVar

from loom import Context, StringEntry, FileEntry

from nanobot.agent.memory import MemoryStore
from nanobot.agent.skills import SkillsLoader


class ContextBuilder:
    """
    Builds the context (system prompt + messages) for the agent.
    
    Uses LOOM to assemble bootstrap files, memory, skills, and conversation
    history into a coherent, cacheable context for the LLM.
    
    LOOM Sections (in order, optimized for prefix caching):
    - foundation: Core identity, rarely changes (CACHED)
    - focus: Bootstrap files (AGENTS.md, SOUL.md, etc.) (CACHED)
    - topic: Skills summary, stable per workspace (CACHED)
    - convo: Memory context, changes daily but stable within session
    - step: Session info (channel, chat_id), changes per conversation
    - attention: (reserved for future use)
    
    Cache breakpoints are set after 'foundation' and 'topic' for optimal
    Anthropic prompt caching — the stable prefix is cached, reducing costs by ~90%.
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
    
    def build_context(
        self,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> Context:
        """
        Build a LOOM Context with all sections populated.
        
        Args:
            channel: Current channel (telegram, feishu, etc.).
            chat_id: Current chat/user ID.
        
        Returns:
            A LOOM Context ready for rendering.
        """
        ctx = Context("agent")
        
        # Foundation: Core identity (rarely changes)
        ctx.foundation.add(StringEntry(
            self._get_identity(),
            name="identity",
        ))
        
        # Focus: Bootstrap files (stable per workspace)
        for filename in self.BOOTSTRAP_FILES:
            file_path = self.workspace / filename
            if file_path.exists():
                ctx.focus.add(FileEntry(
                    file_path,
                    name=filename,
                ))
        
        # Topic: Skills (stable per workspace, rarely change) — CACHED
        # Always-loaded skills get full content
        always_skills = self.skills.get_always_skills()
        if always_skills:
            always_content = self.skills.load_skills_for_context(always_skills)
            if always_content:
                ctx.topic.add(StringEntry(
                    always_content,
                    name="Active Skills",
                ))
        
        # Available skills summary
        skills_summary = self.skills.build_skills_summary()
        if skills_summary:
            ctx.topic.add(StringEntry(
                f"""The following skills extend your capabilities. To use a skill, read its SKILL.md file using the read_file tool.
Skills with available="false" need dependencies installed first - you can try installing them with apt/brew.

{skills_summary}""",
                name="Skills",
            ))
        
        # Convo: Memory context (changes daily, but stable within session)
        memory_content = self.memory.get_memory_context()
        if memory_content:
            ctx.convo.add(StringEntry(
                memory_content,
                name="Memory",
            ))
        
        # Step: Session info (changes per conversation)
        if channel and chat_id:
            ctx.step.add(StringEntry(
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
        """Get the core identity section."""
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
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
{now}

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
        # Build context and get messages with cache breakpoints
        # Anthropic allows up to 4 breakpoints - we use 2 for system prompt:
        # - focus: Bootstrap files (AGENTS.md, SOUL.md, etc.) - large stable block (~12k tokens)
        # - convo: Memory context - grows but prefix stays stable within session
        ctx = self.build_context(channel=channel, chat_id=chat_id)
        messages = ctx.to_messages(cache_breakpoints=["focus", "convo"])
        
        # Add history
        messages.extend(history)

        # Current message (with optional image attachments)
        user_content = self._build_user_content(current_message, media)
        messages.append({"role": "user", "content": user_content})

        return messages

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
