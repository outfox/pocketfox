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
from pocketfox.utils.helpers import truncate_string
from pocketfox.bus.queue import MessageBus
from pocketfox.providers.base import LLMProvider
from pocketfox.session.manager import SessionManager

if TYPE_CHECKING:
    from pocketfox.config.schema import ExecToolConfig, VoiceToolConfig
    from pocketfox.cron.service import CronService

# Sentinel prefix returned by _run_llm_loop on LLM API errors.
# Callers check this to avoid saving error responses to session history.
_LLM_ERROR_PREFIX = "\x00LLM_ERROR\x00"


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
            brave_api_key=brave_api_key,
            exec_config=self.exec_config,
            restrict_to_workspace=restrict_to_workspace,
        )

        self._running = False

        # Per-context turn scheduling state
        self._ctx_events: dict[str, asyncio.Event] = {}
        self._ctx_meta: dict[str, dict[str, dict]] = {}  # context → {session_key → meta}
        self._dynamic_tasks: list[asyncio.Task] = []  # lazily-created context turn loops

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
        """Run the agent loop with separate ingestion and per-context turn execution."""
        self._running = True
        logger.info("Agent loop started")

        tasks: list[asyncio.Task] = [asyncio.create_task(self._ingest_loop())]

        # One independent turn loop per configured context
        if self.router:
            for ctx_name in self.router._contexts:
                self._init_context(ctx_name)
                tasks.append(asyncio.create_task(self._context_turn_loop(ctx_name)))

        # Always have a "default" context for legacy / no-router mode
        if "default" not in self._ctx_events:
            self._init_context("default")
            tasks.append(asyncio.create_task(self._context_turn_loop("default")))

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        finally:
            for t in tasks + self._dynamic_tasks:
                t.cancel()

    def _init_context(self, name: str) -> None:
        """Initialise per-context turn-scheduling state."""
        self._ctx_events[name] = asyncio.Event()
        self._ctx_meta[name] = {}

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        # Wake all context turn loops so they exit cleanly
        for ev in self._ctx_events.values():
            ev.set()
        logger.info("Agent loop stopping")

    # ------------------------------------------------------------------
    # Ingestion — saves messages to sessions, signals context turn loops
    # ------------------------------------------------------------------

    async def _ingest_loop(self) -> None:
        """Continuously consume inbound messages, save to sessions, queue turns."""
        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            try:
                self._ingest_message(msg)
            except Exception as e:
                logger.error(f"Error ingesting message: {e}")
                await self.bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=f"Sorry, I encountered an error: {str(e)}",
                    )
                )

    def _ingest_message(self, msg: InboundMessage) -> None:
        """Route a message, save it to the appropriate session(s), and queue turns."""
        if msg.channel == "system":
            self._ingest_system_message(msg)
            return

        if self.router:
            matched = self.router.match(msg.channel, msg.chat_id)
            if not matched:
                logger.warning(f"No context for {msg.channel}:{msg.chat_id}, dropping")
                return
            for ctx_name in matched:
                ctx_cfg = self.router.get_config(ctx_name)
                session_key = f"{ctx_name}:{msg.channel}:{msg.chat_id}"
                self._save_and_queue(
                    msg, session_key, ctx_name,
                    model=ctx_cfg.model,
                    context_files=tuple(self.router.get_context_files(ctx_name)),
                )
        else:
            ctx_name = msg.context_name or "default"
            self._save_and_queue(msg, msg.session_key, ctx_name)

    def _ingest_system_message(self, msg: InboundMessage) -> None:
        """Parse origin from a system message and save/queue it."""
        parts = msg.chat_id.split(":", 2)
        if len(parts) == 3:
            origin_context, origin_channel, origin_chat_id = parts
        elif len(parts) == 2:
            origin_context = "default"
            origin_channel, origin_chat_id = parts
        else:
            origin_context = "default"
            origin_channel = "cli"
            origin_chat_id = msg.chat_id

        ctx_model = None
        context_files = None
        if self.router and self.router.has_context(origin_context):
            ctx_model = self.router.get_config(origin_context).model
            context_files = tuple(self.router.get_context_files(origin_context))

        if self.router:
            session_key = f"{origin_context}:{origin_channel}:{origin_chat_id}"
        else:
            session_key = f"{origin_channel}:{origin_chat_id}"

        session = self.sessions.get_or_create(session_key)
        system_user_msg = f"[System: {msg.sender_id}] {msg.content}"
        session.add_message("user", system_user_msg)
        self.sessions.save(session)

        preview = truncate_string(msg.content, 83)
        logger.info(f"[{origin_context}] Ingested system msg from {msg.sender_id}: {preview}")

        self._queue_turn(
            origin_context, session_key,
            channel=origin_channel, chat_id=origin_chat_id,
            model=ctx_model, context_files=context_files,
        )

    def _save_and_queue(
        self,
        msg: InboundMessage,
        session_key: str,
        context_name: str,
        model: str | None = None,
        context_files: tuple[str, ...] | None = None,
    ) -> None:
        """Save user message to session and queue a turn for the context."""
        session = self.sessions.get_or_create(session_key)
        session.add_message("user", msg.content, **({"media": msg.media} if msg.media else {}))
        self.sessions.save(session)

        preview = truncate_string(msg.content, 83)
        logger.info(f"[{context_name}] Ingested from {msg.channel}:{msg.sender_id}: {preview}")

        self._queue_turn(
            context_name, session_key,
            channel=msg.channel, chat_id=msg.chat_id,
            model=model, cache_ttl=msg.cache_ttl, context_files=context_files,
        )

    def _queue_turn(
        self,
        context_name: str,
        session_key: str,
        *,
        channel: str,
        chat_id: str,
        model: str | None = None,
        cache_ttl: int | None = None,
        context_files: tuple[str, ...] | None = None,
    ) -> None:
        """Mark a session as needing a turn and signal its context loop."""
        if context_name not in self._ctx_events:
            self._init_context(context_name)
            task = asyncio.create_task(self._context_turn_loop(context_name))
            self._dynamic_tasks.append(task)

        self._ctx_meta[context_name][session_key] = {
            "context_name": context_name,
            "channel": channel,
            "chat_id": chat_id,
            "model": model,
            "cache_ttl": cache_ttl,
            "context_files": context_files,
        }
        self._ctx_events[context_name].set()

    # ------------------------------------------------------------------
    # Per-context turn loops — run independently and in parallel
    # ------------------------------------------------------------------

    async def _context_turn_loop(self, context_name: str) -> None:
        """Process pending turns for a single context. Runs as its own task."""
        event = self._ctx_events[context_name]
        logger.info(f"[{context_name}] Turn loop started")

        while self._running:
            await event.wait()
            event.clear()
            if not self._running:
                break

            # Inner loop: keep processing as long as new sessions appear
            while self._ctx_meta[context_name] and self._running:
                meta_batch = dict(self._ctx_meta[context_name])
                self._ctx_meta[context_name].clear()

                for session_key, meta in meta_batch.items():
                    try:
                        results = await self._run_session_turn(session_key, meta)
                        for out_msg in results:
                            if out_msg:
                                await self.bus.publish_outbound(out_msg)
                    except Exception as e:
                        logger.error(f"Error in turn for {session_key}: {e}")
                        await self.bus.publish_outbound(
                            OutboundMessage(
                                channel=meta["channel"],
                                chat_id=meta["chat_id"],
                                content=f"Sorry, I encountered an error: {str(e)}",
                            )
                        )

    # ------------------------------------------------------------------
    # Turn execution
    # ------------------------------------------------------------------

    async def _run_session_turn(
        self, session_key: str, meta: dict
    ) -> list[OutboundMessage]:
        """Run a single LLM turn for a session with all accumulated messages."""
        context_name = meta["context_name"]
        ctx_model = meta.get("model")
        channel = meta["channel"]
        chat_id = meta["chat_id"]

        tc = TaskContext(
            context_name=context_name, channel=channel, chat_id=chat_id, model=ctx_model
        )
        current_task.set(tc)

        session = self.sessions.get_or_create(session_key)
        history, current_content, current_media = self._prepare_turn_messages(session)

        if current_content is None:
            return []

        logger.info(f"[{context_name}] Starting turn for {channel}:{chat_id}")

        messages = self.context.build_messages(
            history=history,
            current_message=current_content,
            media=current_media,
            channel=channel,
            chat_id=chat_id,
            cache_ttl=meta.get("cache_ttl"),
            context_name=context_name,
            context_files=meta.get("context_files"),
        )

        final_content = await self._run_llm_loop(messages, session, model=ctx_model)

        if self._is_llm_error(final_content):
            error_text = self._strip_error_prefix(final_content)
            outputs = self._resolve_outputs(meta)
            return [
                OutboundMessage(channel=ch, chat_id=cid, content=error_text) for ch, cid in outputs
            ]

        if final_content is None:
            final_content = "I've completed processing but have no response to give."

        preview = truncate_string(final_content, 123)
        logger.info(f"[{context_name}] Response: {preview}")

        session.add_message("assistant", final_content)
        self.sessions.save(session)

        outputs = self._resolve_outputs(meta)
        return [
            OutboundMessage(channel=ch, chat_id=cid, content=final_content) for ch, cid in outputs
        ]

    # ------------------------------------------------------------------
    # History preparation — merge consecutive same-role messages
    # ------------------------------------------------------------------

    def _prepare_turn_messages(
        self, session: object
    ) -> tuple[list[dict], str | None, list[str] | None]:
        """Split session history into (old_history, current_content, current_media).

        Merges consecutive same-role messages so the LLM always sees proper
        user/assistant alternation.  The last user block becomes current_content.
        """
        raw = session.get_history()
        if not raw:
            return [], None, None

        merged = self._merge_consecutive(raw)

        if merged and merged[-1]["role"] == "user":
            current = merged.pop()
            return merged, current["content"], current.get("media")

        logger.warning("No user message at end of session for turn")
        return merged, None, None

    @staticmethod
    def _merge_consecutive(history: list[dict]) -> list[dict]:
        """Merge consecutive messages with the same role.

        Concatenates content with double-newlines.  Combines media lists.
        """
        if not history:
            return []

        merged: list[dict] = []
        for msg in history:
            if merged and merged[-1]["role"] == msg["role"]:
                prev = merged[-1]
                prev["content"] = prev["content"] + "\n\n" + msg["content"]
                if msg.get("media"):
                    if "media" not in prev:
                        prev["media"] = []
                    prev["media"].extend(msg["media"])
            else:
                merged.append({**msg})
        return merged

    def _resolve_outputs(self, meta: dict) -> list[tuple[str, str]]:
        """Resolve output targets from turn metadata."""
        ctx = meta["context_name"]
        if self.router and self.router.has_context(ctx):
            return self.router.get_outputs(ctx, meta["channel"], meta["chat_id"])
        return [(meta["channel"], meta["chat_id"])]

    # ------------------------------------------------------------------
    # LLM loop
    # ------------------------------------------------------------------

    async def _run_llm_loop(
        self,
        messages: list[dict],
        session: SessionManager | object | None = None,
        model: str | None = None,
    ) -> str | None:
        """Run the iterative LLM + tool execution loop.

        Returns final text, or a string prefixed with _LLM_ERROR_PREFIX on
        API error, or None if max iterations exhausted.
        """
        iteration = 0
        while iteration < self.max_iterations:
            iteration += 1

            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=model or self.model,
                max_tokens=self.max_tokens,
            )

            # Log usage
            if response.usage:
                usage = response.usage
                cache_read = usage.get("cache_read_input_tokens", 0)
                cache_write = usage.get("cache_creation_input_tokens", 0)
                prompt = usage.get("prompt_tokens", 0)
                completion = usage.get("completion_tokens", 0)
                if session and hasattr(session, "metadata"):
                    session.metadata["last_prompt_tokens"] = prompt
                if cache_read or cache_write:
                    logger.info(
                        f"Usage: {prompt} prompt, {completion} completion | "
                        f"Cache: {cache_read} read, {cache_write} written"
                    )
                else:
                    logger.debug(f"Usage: {prompt} prompt, {completion} completion")

            # LLM error — return sentinel-prefixed string so callers can detect it
            if response.finish_reason == "error":
                error_detail = response.content or "Unknown error"
                logger.error(f"LLM error: {error_detail}")
                return (
                    f"{_LLM_ERROR_PREFIX}"
                    "I couldn't process your message due to an API error.\n\n"
                    f"<details><summary>Error details</summary>\n\n"
                    f"{error_detail}\n\n</details>"
                )

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

    @staticmethod
    def _is_llm_error(content: str | None) -> bool:
        """Check if content is an LLM error sentinel from _run_llm_loop."""
        return content is not None and content.startswith(_LLM_ERROR_PREFIX)

    @staticmethod
    def _strip_error_prefix(content: str) -> str:
        """Strip the sentinel prefix to get user-facing error text."""
        return content.removeprefix(_LLM_ERROR_PREFIX)

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        cache_ttl: int | None = None,
        context_name: str | None = None,
    ) -> str:
        """Process a message directly (for CLI or cron usage).

        Saves the message to the session, then runs a turn synchronously.
        """
        ctx_name = context_name or "default"
        ctx_model = None
        context_files = None
        if self.router and self.router.has_context(ctx_name):
            ctx_model = self.router.get_config(ctx_name).model
            context_files = tuple(self.router.get_context_files(ctx_name))

        session = self.sessions.get_or_create(session_key)
        session.add_message("user", content)
        self.sessions.save(session)

        tc = TaskContext(
            context_name=ctx_name, channel=channel, chat_id=chat_id, model=ctx_model
        )
        current_task.set(tc)

        history, current_content, current_media = self._prepare_turn_messages(session)

        messages = self.context.build_messages(
            history=history,
            current_message=current_content,
            media=current_media,
            channel=channel,
            chat_id=chat_id,
            cache_ttl=cache_ttl,
            context_name=ctx_name,
            context_files=context_files,
        )

        final_content = await self._run_llm_loop(messages, session, model=ctx_model)

        if self._is_llm_error(final_content):
            return self._strip_error_prefix(final_content)

        if final_content is None:
            final_content = "I've completed processing but have no response to give."

        session.add_message("assistant", final_content)
        self.sessions.save(session)
        return final_content
