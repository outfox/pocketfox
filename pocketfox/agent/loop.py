"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from pocketfox.agent.context import ContextBuilder
from pocketfox.agent.router import ContextRouter
from pocketfox.agent.subagent import SubagentManager
from pocketfox.agent.task_context import TaskContext, current_task
from pocketfox.agent.tools.cron import CronTool
from pocketfox.agent.tools.filesystem import (
    EditFileTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
)
from pocketfox.agent.tools.message import MessageTool
from pocketfox.agent.tools.registry import ToolRegistry
from pocketfox.agent.tools.shell import ExecTool
from pocketfox.agent.tools.spawn import SpawnTool
from pocketfox.agent.tools.view_image import ViewImageTool
from pocketfox.agent.tools.voice import VoiceTool
from pocketfox.agent.tools.web import WebFetchTool, WebSearchTool
from pocketfox.bus.events import InboundMessage, OutboundMessage
from pocketfox.bus.queue import MessageBus
from pocketfox.providers.base import LLMProvider
from pocketfox.session.manager import SessionManager

if TYPE_CHECKING:
    from pocketfox.config.schema import ExecToolConfig, VoiceToolConfig
    from pocketfox.cron.service import CronService


class AgentLoop:
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Routes to matching contexts (parallel fan-out)
    3. Builds context with history, memory, skills
    4. Calls the LLM
    5. Executes tool calls
    6. Sends responses to resolved output targets
    """

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        router: ContextRouter | None = None,
        model: str | None = None,
        max_tokens: int = 100000,
        max_iterations: int = 50,
        brave_api_key: str | None = None,
        exec_config: ExecToolConfig | None = None,
        voice_config: VoiceToolConfig | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        default_context_files: list[str] | None = None,
    ):
        self.bus = bus
        self.provider = provider
        self.workspace = workspace
        self.router = router
        self.model = model or provider.get_default_model()
        self.max_tokens = max_tokens
        self.max_iterations = max_iterations
        self.brave_api_key = brave_api_key
        self.exec_config = exec_config or ExecToolConfig()
        self.voice_config = voice_config or VoiceToolConfig()
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace

        self.context = ContextBuilder(
            workspace,
            default_context_files=default_context_files,
        )
        self.sessions = session_manager or SessionManager(workspace)
        self.sessions.on_session_reset = self.context.clear_kept_images
        self.tools = ToolRegistry()
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            brave_api_key=brave_api_key,
            exec_config=self.exec_config,
            restrict_to_workspace=restrict_to_workspace,
        )

        self._running = False
        self._register_default_tools()

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        # File tools (restrict to workspace if configured)
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        self.tools.register(ReadFileTool(allowed_dir=allowed_dir, context_builder=self.context))
        self.tools.register(WriteFileTool(allowed_dir=allowed_dir))
        self.tools.register(EditFileTool(allowed_dir=allowed_dir))
        self.tools.register(ListDirTool(allowed_dir=allowed_dir))

        # Shell tool
        self.tools.register(
            ExecTool(
                working_dir=str(self.workspace),
                timeout=self.exec_config.timeout,
                restrict_to_workspace=self.restrict_to_workspace,
                sandbox_dir=self.exec_config.sandbox_dir,
                sandbox_readonly_paths=self.exec_config.sandbox_readonly_paths,
            )
        )

        # Vision tool (view images from filesystem)
        self.tools.register(
            ViewImageTool(
                allowed_dir=allowed_dir,
                context_builder=self.context,
            )
        )

        # Web tools
        self.tools.register(WebSearchTool(api_key=self.brave_api_key))
        self.tools.register(WebFetchTool())

        # Message tool
        message_tool = MessageTool(send_callback=self.bus.publish_outbound)
        self.tools.register(message_tool)

        # Spawn tool (for subagents)
        spawn_tool = SpawnTool(manager=self.subagents)
        self.tools.register(spawn_tool)

        # Cron tool (for scheduling)
        if self.cron_service:
            self.tools.register(CronTool(self.cron_service))

        # Voice tool (TTS)
        if self.voice_config.api_key:
            self.tools.register(
                VoiceTool(
                    api_key=self.voice_config.api_key,
                    default_voice_id=self.voice_config.default_voice_id,
                    default_stability=self.voice_config.default_stability,
                    default_speed=self.voice_config.default_speed,
                    workspace=self.workspace,
                )
            )

    async def run(self) -> None:
        """Run the agent loop, processing messages from the bus."""
        self._running = True
        logger.info("Agent loop started")

        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)

                try:
                    if msg.channel == "system":
                        results = await self._process_system_message(msg)
                    elif self.router:
                        matched = self.router.match(msg.channel, msg.chat_id)
                        if not matched:
                            logger.warning(f"No context for {msg.channel}:{msg.chat_id}, dropping")
                            continue
                        tasks = [self._process_in_context(msg, ctx_name) for ctx_name in matched]
                        results = await asyncio.gather(*tasks, return_exceptions=True)
                    else:
                        # No router — legacy single-context mode
                        out = await self._process_message(msg)
                        results = [[out]] if out else [[]]

                    for result in results:
                        if isinstance(result, Exception):
                            logger.error(f"Error in context processing: {result}")
                            continue
                        if isinstance(result, list):
                            for out_msg in result:
                                if out_msg:
                                    await self.bus.publish_outbound(out_msg)
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
                    await self.bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content=f"Sorry, I encountered an error: {str(e)}",
                        )
                    )
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")

    async def _process_in_context(
        self, msg: InboundMessage, context_name: str
    ) -> list[OutboundMessage]:
        """Process a message within a specific context. Returns outbound messages."""
        # Set task-local context for tools
        tc = TaskContext(context_name=context_name, channel=msg.channel, chat_id=msg.chat_id)
        current_task.set(tc)

        context_files = tuple(self.router.get_context_files(context_name))
        session_key = f"{context_name}:{msg.channel}:{msg.chat_id}"
        session = self.sessions.get_or_create(session_key)

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info(f"[{context_name}] Processing from {msg.channel}:{msg.sender_id}: {preview}")

        # Build initial messages
        messages = self.context.build_messages(
            history=session.get_history(),
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel,
            chat_id=msg.chat_id,
            cache_ttl=msg.cache_ttl,
            context_name=context_name,
            context_files=context_files,
        )

        # Run the LLM loop
        final_content = await self._run_llm_loop(messages, session)

        if final_content is None:
            final_content = "I've completed processing but have no response to give."

        # Log response preview
        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info(f"[{context_name}] Response to {msg.channel}:{msg.sender_id}: {preview}")

        # Save to session
        session.add_message("user", msg.content)
        session.add_message("assistant", final_content)
        self.sessions.save(session)

        # Resolve output targets
        outputs = self.router.get_outputs(context_name, msg.channel, msg.chat_id)
        return [
            OutboundMessage(channel=ch, chat_id=cid, content=final_content) for ch, cid in outputs
        ]

    async def _run_llm_loop(
        self,
        messages: list[dict],
        session: object,
        *,
        is_error_return: bool = True,
    ) -> str | None:
        """Run the iterative LLM + tool execution loop. Returns final text or None."""
        iteration = 0
        while iteration < self.max_iterations:
            iteration += 1

            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=self.model,
                max_tokens=self.max_tokens,
            )

            # Log usage
            if response.usage:
                usage = response.usage
                cache_read = usage.get("cache_read_input_tokens", 0)
                cache_write = usage.get("cache_creation_input_tokens", 0)
                prompt = usage.get("prompt_tokens", 0)
                completion = usage.get("completion_tokens", 0)
                if hasattr(session, "metadata"):
                    session.metadata["last_prompt_tokens"] = prompt
                if cache_read or cache_write:
                    logger.info(
                        f"Usage: {prompt} prompt, {completion} completion | "
                        f"Cache: {cache_read} read, {cache_write} written"
                    )
                else:
                    logger.debug(f"Usage: {prompt} prompt, {completion} completion")

            # LLM error
            if response.finish_reason == "error":
                error_detail = response.content or "Unknown error"
                logger.error(f"LLM error: {error_detail}")
                if is_error_return:
                    return (
                        "I couldn't process your message due to an API error.\n\n"
                        f"<details><summary>Error details</summary>\n\n"
                        f"{error_detail}\n\n</details>"
                    )
                return None

            # Tool calls
            if response.has_tool_calls:
                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages,
                    response.content,
                    tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                )

                for tool_call in response.tool_calls:
                    redacted = self.tools.redact_params(tool_call.name, tool_call.arguments)
                    args_str = json.dumps(redacted, ensure_ascii=False)
                    logger.info(f"Tool call: {tool_call.name}({args_str[:200]})")
                    result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
            else:
                return response.content

        return None

    async def _process_message(self, msg: InboundMessage) -> OutboundMessage | None:
        """Process a single inbound message (legacy non-router path)."""
        if msg.channel == "system":
            results = await self._process_system_message(msg)
            # Return first non-None result
            for r in results:
                if isinstance(r, list):
                    for out in r:
                        if out:
                            return out
            return None

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info(f"Processing message from {msg.channel}:{msg.sender_id}: {preview}")

        # Set task context for tools
        context_name = msg.context_name or "default"
        tc = TaskContext(context_name=context_name, channel=msg.channel, chat_id=msg.chat_id)
        current_task.set(tc)

        session = self.sessions.get_or_create(msg.session_key)

        messages = self.context.build_messages(
            history=session.get_history(),
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel,
            chat_id=msg.chat_id,
            cache_ttl=msg.cache_ttl,
            context_name=context_name,
        )

        final_content = await self._run_llm_loop(messages, session)

        # LLM error — return error directly without saving to session
        if final_content and final_content.startswith("I couldn't process"):
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=final_content)

        if final_content is None:
            final_content = "I've completed processing but have no response to give."

        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info(f"Response to {msg.channel}:{msg.sender_id}: {preview}")

        session.add_message("user", msg.content)
        session.add_message("assistant", final_content)
        self.sessions.save(session)

        return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=final_content)

    async def _process_system_message(self, msg: InboundMessage) -> list[list[OutboundMessage]]:
        """Process a system message (subagent announce). Returns list of output lists."""
        logger.info(f"Processing system message from {msg.sender_id}")

        # Parse origin from chat_id (format: "context_name:channel:chat_id" or "channel:chat_id")
        parts = msg.chat_id.split(":", 2)
        if len(parts) == 3:
            origin_context = parts[0]
            origin_channel = parts[1]
            origin_chat_id = parts[2]
        elif len(parts) == 2:
            origin_context = "default"
            origin_channel = parts[0]
            origin_chat_id = parts[1]
        else:
            origin_context = "default"
            origin_channel = "cli"
            origin_chat_id = msg.chat_id

        # Set task context
        tc = TaskContext(
            context_name=origin_context, channel=origin_channel, chat_id=origin_chat_id
        )
        current_task.set(tc)

        # Resolve context files if router available
        context_files = None
        if self.router and origin_context in self.router._contexts:
            context_files = tuple(self.router.get_context_files(origin_context))

        # Build session key
        if self.router:
            session_key = f"{origin_context}:{origin_channel}:{origin_chat_id}"
        else:
            session_key = f"{origin_channel}:{origin_chat_id}"
        session = self.sessions.get_or_create(session_key)

        messages = self.context.build_messages(
            history=session.get_history(),
            current_message=msg.content,
            channel=origin_channel,
            chat_id=origin_chat_id,
            context_name=origin_context,
            context_files=context_files,
        )

        final_content = await self._run_llm_loop(messages, session)

        if final_content and final_content.startswith("I couldn't process"):
            # LLM error — don't save
            error_msg = (
                "A background task failed due to an API error.\n\n"
                f"<details><summary>Error details</summary>\n\n"
                f"{final_content}\n\n</details>"
            )
            return [
                [OutboundMessage(channel=origin_channel, chat_id=origin_chat_id, content=error_msg)]
            ]

        if final_content is None:
            final_content = "Background task completed."

        session.add_message("user", f"[System: {msg.sender_id}] {msg.content}")
        session.add_message("assistant", final_content)
        self.sessions.save(session)

        # Resolve outputs
        if self.router and origin_context in self.router._contexts:
            outputs = self.router.get_outputs(origin_context, origin_channel, origin_chat_id)
        else:
            outputs = [(origin_channel, origin_chat_id)]

        return [
            [OutboundMessage(channel=ch, chat_id=cid, content=final_content) for ch, cid in outputs]
        ]

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        cache_ttl: int | None = None,
        context_name: str | None = None,
    ) -> str:
        """
        Process a message directly (for CLI or cron usage).

        Args:
            content: The message content.
            session_key: Session identifier.
            channel: Source channel (for context).
            chat_id: Source chat ID (for context).
            cache_ttl: Optional Anthropic prompt cache TTL in seconds.
            context_name: Context to route through.

        Returns:
            The agent's response.
        """
        msg = InboundMessage(
            channel=channel,
            sender_id="user",
            chat_id=chat_id,
            content=content,
            cache_ttl=cache_ttl,
            context_name=context_name,
        )

        response = await self._process_message(msg)
        return response.content if response else ""
