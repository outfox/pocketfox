"""Tests for the agent loop — LLM error handling, image context, and prompt management."""

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from pocketfox.agent.context import ContextBuilder
from pocketfox.agent.entries import ImageEntry, KeptFileEntry
from pocketfox.bus.events import InboundMessage
from pocketfox.bus.queue import MessageBus
from pocketfox.providers.base import LLMResponse, ToolCallRequest
from pocketfox.session.manager import SessionManager

FAKE_B64 = "iVBORw0KGgoAAAANSUhEUg=="


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeProvider:
    """A fake LLM provider that returns pre-configured responses."""

    def __init__(self, responses: list[LLMResponse] | None = None):
        self.responses = list(responses or [])
        self._call_count = 0
        self.calls: list[dict[str, Any]] = []

    def get_default_model(self) -> str:
        return "fake/model"

    async def chat(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(kwargs)
        idx = min(self._call_count, len(self.responses) - 1)
        self._call_count += 1
        return self.responses[idx]


def _ok_response(content: str = "Hello!") -> LLMResponse:
    return LLMResponse(content=content, finish_reason="stop")


def _error_response(msg: str = "AuthenticationError: invalid api key") -> LLMResponse:
    return LLMResponse(content=f"Error calling LLM: {msg}", finish_reason="error")


def _tool_response(tool_name: str = "read_file", tool_id: str = "tc_1") -> LLMResponse:
    return LLMResponse(
        content="",
        finish_reason="stop",
        tool_calls=[
            ToolCallRequest(id=tool_id, name=tool_name, arguments={"path": "/tmp/x"})
        ],
    )


async def _make_loop(
    tmp_path: Path,
    provider: FakeProvider,
    session_manager: SessionManager | None = None,
):
    """Create a minimal AgentLoop with a fake provider."""
    from pocketfox.agent.loop import AgentLoop
    from pocketfox.config.schema import ExecToolConfig, VoiceToolConfig

    bus = MessageBus()
    sm = session_manager or SessionManager(tmp_path)
    loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=tmp_path,
        model="fake/model",
        session_manager=sm,
        exec_config=ExecToolConfig(),
        voice_config=VoiceToolConfig(),
    )
    return loop


# ---------------------------------------------------------------------------
# LLM error handling
# ---------------------------------------------------------------------------


class TestLLMErrorHandling:
    """Verify that LLM errors don't corrupt session state."""

    @pytest.mark.asyncio
    async def test_error_response_not_saved_to_session(self, tmp_path):
        """An error response (finish_reason='error') must not be persisted in session."""
        provider = FakeProvider([_error_response()])
        loop = await _make_loop(tmp_path, provider)

        msg = InboundMessage(
            channel="telegram", sender_id="u1", chat_id="123", content="hi"
        )
        response = await loop._process_message(msg)

        # The error should be communicated to the user
        assert response is not None
        assert "error" in response.content.lower()

        # Session must NOT contain the error as an assistant message
        session = loop.sessions.get_or_create("telegram:123")
        assistant_msgs = [m for m in session.messages if m["role"] == "assistant"]
        for m in assistant_msgs:
            assert "Error calling LLM" not in m["content"]

    @pytest.mark.asyncio
    async def test_session_clean_after_error(self, tmp_path):
        """Session history should be unchanged after an LLM error."""
        provider = FakeProvider([_error_response()])
        loop = await _make_loop(tmp_path, provider)

        session = loop.sessions.get_or_create("telegram:123")
        msgs_before = len(session.messages)

        msg = InboundMessage(
            channel="telegram", sender_id="u1", chat_id="123", content="hi"
        )
        await loop._process_message(msg)

        assert len(session.messages) == msgs_before

    @pytest.mark.asyncio
    async def test_context_usable_after_error(self, tmp_path):
        """After an LLM error, the next message should process normally."""
        provider = FakeProvider([_error_response(), _ok_response("recovered!")])
        loop = await _make_loop(tmp_path, provider)

        msg1 = InboundMessage(
            channel="telegram", sender_id="u1", chat_id="123", content="first"
        )
        resp1 = await loop._process_message(msg1)
        assert "error" in resp1.content.lower()

        msg2 = InboundMessage(
            channel="telegram", sender_id="u1", chat_id="123", content="second"
        )
        resp2 = await loop._process_message(msg2)
        assert resp2.content == "recovered!"

    @pytest.mark.asyncio
    async def test_error_during_tool_loop_does_not_save(self, tmp_path):
        """If the LLM fails on iteration 2 (after tool call), session stays clean."""
        provider = FakeProvider([_tool_response(), _error_response()])
        loop = await _make_loop(tmp_path, provider)

        # Register a dummy tool so execution doesn't fail
        dummy_tool = MagicMock()
        dummy_tool.name = "read_file"
        dummy_tool.execute = AsyncMock(return_value="file contents")
        loop.tools._tools["read_file"] = dummy_tool
        loop.tools.redact_params = MagicMock(return_value={"path": "/tmp/x"})

        msg = InboundMessage(
            channel="telegram", sender_id="u1", chat_id="tool_err", content="read it"
        )
        resp = await loop._process_message(msg)
        assert "error" in resp.content.lower()

        session = loop.sessions.get_or_create("telegram:tool_err")
        assert len(session.messages) == 0

    @pytest.mark.asyncio
    async def test_error_message_includes_details(self, tmp_path):
        """Error response should include structured detail for the user."""
        provider = FakeProvider(
            [_error_response("PermissionDeniedError: insufficient credits")]
        )
        loop = await _make_loop(tmp_path, provider)

        msg = InboundMessage(
            channel="telegram", sender_id="u1", chat_id="123", content="hi"
        )
        resp = await loop._process_message(msg)

        # Should contain a user-friendly message and the raw error detail
        assert "error" in resp.content.lower()
        assert "PermissionDeniedError" in resp.content or "details" in resp.content.lower()


# ---------------------------------------------------------------------------
# Kept image injection into prompt
# ---------------------------------------------------------------------------


class TestKeptImageInjection:
    """Tests for _inject_image_blocks in build_messages."""

    def _add_kept_image(self, ctx_builder: ContextBuilder, tmp_path: Path, name: str = "photo.png"):
        entry = ImageEntry(
            path=tmp_path / name,
            base64_data=FAKE_B64,
            mime_type="image/png",
            caption="test image",
        )
        ctx_builder.add_entry("topic", entry)
        return entry

    def test_image_injected_before_final_user_message(self, tmp_path):
        """Kept images should appear as user/assistant pair before the last user message."""
        builder = ContextBuilder(tmp_path)
        self._add_kept_image(builder, tmp_path)

        messages = builder.build_messages(
            history=[], current_message="What do you see?", channel="test", chat_id="1"
        )

        # Find the image user message
        image_msgs = [
            m for m in messages
            if m["role"] == "user"
            and isinstance(m.get("content"), list)
            and any(
                b.get("type") == "image_url"
                for b in m["content"]
                if isinstance(b, dict)
            )
        ]
        assert len(image_msgs) == 1

        # There should be an "assistant: Noted." ack after the image message
        img_idx = messages.index(image_msgs[0])
        assert messages[img_idx + 1]["role"] == "assistant"
        assert messages[img_idx + 1]["content"] == "Noted."

        # The final message should be the current user message (not the image)
        assert messages[-1]["role"] == "user"

    def test_no_images_no_injection(self, tmp_path):
        """Without kept images, no extra messages are injected."""
        builder = ContextBuilder(tmp_path)

        messages = builder.build_messages(
            history=[], current_message="hello", channel="test", chat_id="1"
        )

        user_msgs = [m for m in messages if m["role"] == "user"]
        assistant_msgs = [m for m in messages if m["role"] == "assistant"]

        # Only the current user message, no assistant ack
        assert len(user_msgs) == 1
        assert len(assistant_msgs) == 0

    def test_images_with_history(self, tmp_path):
        """Kept images should work correctly alongside conversation history."""
        builder = ContextBuilder(tmp_path)
        self._add_kept_image(builder, tmp_path)

        history = [
            {"role": "user", "content": "prev question"},
            {"role": "assistant", "content": "prev answer"},
        ]
        messages = builder.build_messages(
            history=history, current_message="follow up", channel="test", chat_id="1"
        )

        # Roles should alternate correctly (after system)
        roles = [m["role"] for m in messages if m["role"] != "system"]
        for i in range(len(roles) - 1):
            assert roles[i] != roles[i + 1], (
                f"Consecutive same roles at {i}: {roles}"
            )

    def test_multiple_kept_images(self, tmp_path):
        """Multiple kept images should all appear in a single injected user message."""
        builder = ContextBuilder(tmp_path)
        self._add_kept_image(builder, tmp_path, "a.png")
        self._add_kept_image(builder, tmp_path, "b.png")

        messages = builder.build_messages(
            history=[], current_message="describe both", channel="test", chat_id="1"
        )

        image_msgs = [
            m for m in messages
            if m["role"] == "user"
            and isinstance(m.get("content"), list)
            and any(
                b.get("type") == "image_url"
                for b in m["content"]
                if isinstance(b, dict)
            )
        ]
        # All images should be in a single user message
        assert len(image_msgs) == 1
        img_blocks = [
            b for b in image_msgs[0]["content"]
            if isinstance(b, dict) and b.get("type") == "image_url"
        ]
        assert len(img_blocks) == 2


# ---------------------------------------------------------------------------
# clear_kept_images
# ---------------------------------------------------------------------------


class TestClearKeptImages:
    """Tests for ContextBuilder.clear_kept_images()."""

    def test_clears_all_images(self, tmp_path):
        builder = ContextBuilder(tmp_path)
        for name in ("a.png", "b.png"):
            entry = ImageEntry(
                path=tmp_path / name, base64_data=FAKE_B64, mime_type="image/png"
            )
            builder.add_entry("topic", entry)

        removed = builder.clear_kept_images()
        assert removed == 2

        # Verify no images remain
        image_entries = [
            e for e in builder.context.topic.entries if isinstance(e, ImageEntry)
        ]
        assert len(image_entries) == 0

    def test_clear_preserves_non_image_entries(self, tmp_path):
        builder = ContextBuilder(tmp_path)
        builder.add_entry("topic", "some text note", name="note")
        entry = ImageEntry(
            path=tmp_path / "img.png", base64_data=FAKE_B64, mime_type="image/png"
        )
        builder.add_entry("topic", entry)

        before_count = len(builder.context.topic.entries)
        removed = builder.clear_kept_images()
        assert removed == 1
        assert len(builder.context.topic.entries) == before_count - 1

    def test_clear_idempotent(self, tmp_path):
        builder = ContextBuilder(tmp_path)
        assert builder.clear_kept_images() == 0
        assert builder.clear_kept_images() == 0


# ---------------------------------------------------------------------------
# Session reset callback
# ---------------------------------------------------------------------------


class TestSessionResetClearsImages:
    """Verify that session reset triggers image cleanup."""

    @pytest.mark.asyncio
    async def test_on_session_reset_clears_images(self, tmp_path):
        provider = FakeProvider([_ok_response()])
        loop = await _make_loop(tmp_path, provider)

        # Add a kept image
        entry = ImageEntry(
            path=tmp_path / "kept.png", base64_data=FAKE_B64, mime_type="image/png"
        )
        loop.context.add_entry("topic", entry)

        # Trigger session reset callback
        loop.sessions.on_session_reset()

        # Images should be cleared
        image_entries = [
            e for e in loop.context.context.topic.entries
            if isinstance(e, ImageEntry)
        ]
        assert len(image_entries) == 0


# ---------------------------------------------------------------------------
# process_direct error handling
# ---------------------------------------------------------------------------


class TestProcessDirectErrors:
    """Verify process_direct handles LLM errors gracefully."""

    @pytest.mark.asyncio
    async def test_process_direct_returns_error_string(self, tmp_path):
        """process_direct should return an error string, not raise."""
        provider = FakeProvider([_error_response()])
        loop = await _make_loop(tmp_path, provider)

        result = await loop.process_direct("hello", session_key="test:direct")
        assert isinstance(result, str)
        assert "error" in result.lower()

    @pytest.mark.asyncio
    async def test_process_direct_error_does_not_save(self, tmp_path):
        """process_direct should not persist error responses to session."""
        provider = FakeProvider([_error_response()])
        loop = await _make_loop(tmp_path, provider)

        await loop.process_direct("hello", session_key="test:direct")

        session = loop.sessions.get_or_create("test:direct")
        assert len(session.messages) == 0


# ---------------------------------------------------------------------------
# run() error delivery
# ---------------------------------------------------------------------------


class TestRunErrorDelivery:
    """Verify that run() delivers errors to the user via the bus."""

    @pytest.mark.asyncio
    async def test_run_delivers_error_on_exception(self, tmp_path):
        """If _process_message raises, run() should still deliver an error message."""
        provider = FakeProvider([])
        loop = await _make_loop(tmp_path, provider)

        # Make _process_message raise
        async def exploding_process(msg):
            raise RuntimeError("kaboom")

        loop._process_message = exploding_process

        # Publish an inbound message
        await loop.bus.publish_inbound(
            InboundMessage(channel="telegram", sender_id="u1", chat_id="123", content="hi")
        )

        # Run the loop briefly
        loop._running = True

        async def stop_after_delivery():
            await asyncio.sleep(0.1)
            loop.stop()

        asyncio.create_task(stop_after_delivery())
        await loop.run()

        # Check that an error message was published outbound
        try:
            out = await asyncio.wait_for(loop.bus.consume_outbound(), timeout=0.5)
            assert out.channel == "telegram"
            assert out.chat_id == "123"
            assert "error" in out.content.lower()
        except asyncio.TimeoutError:
            pytest.fail("No error message was published to outbound bus")


# ---------------------------------------------------------------------------
# ReadFileTool keep parameter
# ---------------------------------------------------------------------------


class TestReadFileKeep:
    """Tests for read_file with keep=True."""

    def _make_builder(self, tmp_path):
        return ContextBuilder(tmp_path)

    @pytest.mark.asyncio
    async def test_keep_false_default(self, tmp_path):
        """Default read does not persist content in context."""
        from pocketfox.agent.tools.filesystem import ReadFileTool

        doc = tmp_path / "notes.md"
        doc.write_text("# My notes\nSome content", encoding="utf-8")

        builder = self._make_builder(tmp_path)
        tool = ReadFileTool(context_builder=builder)
        result = await tool.execute(path=str(doc))

        assert "My notes" in result
        assert "keeping" not in result
        kept = [e for e in builder.context.focus.entries if isinstance(e, KeptFileEntry)]
        assert len(kept) == 0

    @pytest.mark.asyncio
    async def test_keep_true_persists_in_focus(self, tmp_path):
        """keep=True adds a KeptFileEntry to the focus section."""
        from pocketfox.agent.tools.filesystem import ReadFileTool

        doc = tmp_path / "SKILL.md"
        doc.write_text("---\nname: test\n---\nDo the thing.", encoding="utf-8")

        builder = self._make_builder(tmp_path)
        tool = ReadFileTool(context_builder=builder)
        result = await tool.execute(path=str(doc), keep=True)

        assert "keeping" in result.lower()
        kept = [e for e in builder.context.focus.entries if isinstance(e, KeptFileEntry)]
        assert len(kept) == 1
        assert "Do the thing." in kept[0].compile()

    @pytest.mark.asyncio
    async def test_keep_true_without_builder(self, tmp_path):
        """keep=True without context_builder still returns content normally."""
        from pocketfox.agent.tools.filesystem import ReadFileTool

        doc = tmp_path / "notes.md"
        doc.write_text("content", encoding="utf-8")

        tool = ReadFileTool()  # No context_builder
        result = await tool.execute(path=str(doc), keep=True)

        assert result == "content"
        assert "keeping" not in result

    @pytest.mark.asyncio
    async def test_kept_file_appears_in_build_messages(self, tmp_path):
        """Kept file content should appear in the system prompt via focus section."""
        from pocketfox.agent.tools.filesystem import ReadFileTool

        doc = tmp_path / "reference.md"
        doc.write_text("Important reference material", encoding="utf-8")

        builder = self._make_builder(tmp_path)
        tool = ReadFileTool(context_builder=builder)
        await tool.execute(path=str(doc), keep=True)

        messages = builder.build_messages(
            history=[], current_message="hello", channel="test", chat_id="1"
        )

        # The kept file should be in the system message content
        system_msgs = [m for m in messages if m["role"] == "system"]
        system_text = str(system_msgs)
        assert "Important reference material" in system_text

    @pytest.mark.asyncio
    async def test_kept_file_deduplicates(self, tmp_path):
        """Reading the same file with keep=True twice should not duplicate."""
        from pocketfox.agent.tools.filesystem import ReadFileTool

        doc = tmp_path / "ref.md"
        doc.write_text("content", encoding="utf-8")

        builder = self._make_builder(tmp_path)
        tool = ReadFileTool(context_builder=builder)
        await tool.execute(path=str(doc), keep=True)
        await tool.execute(path=str(doc), keep=True)

        kept = [e for e in builder.context.focus.entries if isinstance(e, KeptFileEntry)]
        # LOOM deduplicates by identity — same resolved path means same entry
        assert len(kept) <= 2  # at most 2 (add_entry doesn't dedupe, but identity() enables it)


# ---------------------------------------------------------------------------
# clear_kept_entries (images + files)
# ---------------------------------------------------------------------------


class TestClearKeptEntries:
    """Tests for ContextBuilder.clear_kept_entries() covering both types."""

    def test_clears_files_and_images(self, tmp_path):
        builder = ContextBuilder(tmp_path)
        builder.add_entry(
            "topic",
            ImageEntry(path=tmp_path / "a.png", base64_data=FAKE_B64, mime_type="image/png"),
        )
        builder.add_entry(
            "focus",
            KeptFileEntry(path=tmp_path / "ref.md"),
        )

        removed = builder.clear_kept_entries()
        assert removed == 2

    def test_alias_clear_kept_images_still_works(self, tmp_path):
        """Backward-compatible alias should clear both types."""
        builder = ContextBuilder(tmp_path)
        builder.add_entry(
            "focus",
            KeptFileEntry(path=tmp_path / "ref.md"),
        )
        removed = builder.clear_kept_images()
        assert removed == 1

    def test_preserves_non_kept_entries(self, tmp_path):
        builder = ContextBuilder(tmp_path)
        builder.add_entry("topic", "plain text note", name="note")
        builder.add_entry(
            "topic",
            ImageEntry(path=tmp_path / "a.png", base64_data=FAKE_B64, mime_type="image/png"),
        )

        before = len(builder.context.topic.entries)
        removed = builder.clear_kept_entries()
        assert removed == 1
        assert len(builder.context.topic.entries) == before - 1


# ---------------------------------------------------------------------------
# KeptFileEntry live-reload and deletion
# ---------------------------------------------------------------------------


class TestKeptFileEntryLiveReload:
    """Verify KeptFileEntry re-reads from disk and handles deletion."""

    def test_compile_reads_current_content(self, tmp_path):
        """compile() should return the file's current content, not stale data."""
        doc = tmp_path / "live.md"
        doc.write_text("version 1", encoding="utf-8")

        entry = KeptFileEntry(path=doc)
        assert "version 1" in entry.compile()

        doc.write_text("version 2", encoding="utf-8")
        assert "version 2" in entry.compile()
        assert "version 1" not in entry.compile()

    def test_compile_handles_deleted_file(self, tmp_path):
        """compile() should return a notice when the file is deleted."""
        doc = tmp_path / "ephemeral.md"
        doc.write_text("exists", encoding="utf-8")

        entry = KeptFileEntry(path=doc)
        assert "exists" in entry.compile()

        doc.unlink()
        result = entry.compile()
        assert "removed" in result.lower()
        assert "ephemeral.md" in result

    def test_deleted_file_does_not_crash_build_messages(self, tmp_path):
        """A deleted kept file should not break build_messages()."""
        doc = tmp_path / "gone.md"
        doc.write_text("content", encoding="utf-8")

        builder = ContextBuilder(tmp_path)
        builder.add_entry("focus", KeptFileEntry(path=doc))

        doc.unlink()

        # Should not raise
        messages = builder.build_messages(
            history=[], current_message="hello", channel="test", chat_id="1"
        )
        system_text = str(messages)
        assert "removed" in system_text.lower()

    @pytest.mark.asyncio
    async def test_edit_reflected_in_next_build(self, tmp_path):
        """Editing a kept file should be reflected in the next build_messages call."""
        from pocketfox.agent.tools.filesystem import ReadFileTool

        doc = tmp_path / "evolving.md"
        doc.write_text("initial", encoding="utf-8")

        builder = ContextBuilder(tmp_path)
        tool = ReadFileTool(context_builder=builder)
        await tool.execute(path=str(doc), keep=True)

        # First build sees initial content
        msgs1 = builder.build_messages(
            history=[], current_message="a", channel="test", chat_id="1"
        )
        assert "initial" in str(msgs1)

        # Edit the file
        doc.write_text("updated", encoding="utf-8")

        # Second build sees updated content
        msgs2 = builder.build_messages(
            history=[], current_message="b", channel="test", chat_id="1"
        )
        assert "updated" in str(msgs2)
        assert "initial" not in str(msgs2)
