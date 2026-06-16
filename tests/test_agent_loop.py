"""Tests for the agent loop — LLM error handling, image context, and prompt management."""

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from loom import FileEntry

from pocketfox.agent.context import ContextBuilder
from pocketfox.agent.entries import ImageEntry
from pocketfox.bus.events import InboundMessage
from pocketfox.bus.queue import MessageBus
from pocketfox.providers.base import LLMResponse, ToolCallRequest
from pocketfox.agent.loop import AgentLoop
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


def _tool_response(tool_name: str = "fs_read", tool_id: str = "tc_1") -> LLMResponse:
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

        result = await loop.process_direct("hi", session_key="telegram:123")

        # The error should be communicated to the caller
        assert "error" in result.lower()

        # Session must NOT contain the error as an assistant message
        session = loop.sessions.get_or_create("telegram:123")
        assistant_msgs = [m for m in session.messages if m["role"] == "assistant"]
        for m in assistant_msgs:
            assert "Error calling LLM" not in m["content"]

    @pytest.mark.asyncio
    async def test_session_keeps_user_msg_after_error(self, tmp_path):
        """User message persists in session after an LLM error (no rollback)."""
        provider = FakeProvider([_error_response()])
        loop = await _make_loop(tmp_path, provider)

        session = loop.sessions.get_or_create("telegram:123")
        msgs_before = len(session.messages)

        result = await loop.process_direct("hi", session_key="telegram:123")
        assert "error" in result.lower()

        # User message stays (+1), no assistant message saved
        assert len(session.messages) == msgs_before + 1
        assert session.messages[-1]["role"] == "user"

    @pytest.mark.asyncio
    async def test_context_usable_after_error(self, tmp_path):
        """After an LLM error, the next message should process normally."""
        provider = FakeProvider([_error_response(), _ok_response("recovered!")])
        loop = await _make_loop(tmp_path, provider)

        resp1 = await loop.process_direct("first", session_key="telegram:123")
        assert "error" in resp1.lower()

        resp2 = await loop.process_direct("second", session_key="telegram:123")
        assert resp2 == "recovered!"

    @pytest.mark.asyncio
    async def test_error_during_tool_loop_keeps_user_msg(self, tmp_path):
        """If the LLM fails after tool call, user message persists but no assistant msg."""
        provider = FakeProvider([_tool_response(), _error_response()])
        loop = await _make_loop(tmp_path, provider)

        # Register a dummy tool so execution doesn't fail
        dummy_tool = MagicMock()
        dummy_tool.name = "fs_read"
        dummy_tool.execute = AsyncMock(return_value="file contents")
        loop.tools._tools["fs_read"] = dummy_tool
        loop.tools.redact_params = MagicMock(return_value={"path": "/tmp/x"})

        session = loop.sessions.get_or_create("telegram:tool_err")
        msgs_before = len(session.messages)

        resp = await loop.process_direct("read it", session_key="telegram:tool_err")
        assert "error" in resp.lower()

        # User message persists (+1), no assistant message
        assert len(session.messages) == msgs_before + 1
        assert session.messages[-1]["role"] == "user"

    @pytest.mark.asyncio
    async def test_error_message_includes_details(self, tmp_path):
        """Error response should include structured detail for the user."""
        provider = FakeProvider(
            [_error_response("PermissionDeniedError: insufficient credits")]
        )
        loop = await _make_loop(tmp_path, provider)

        resp = await loop.process_direct("hi", session_key="telegram:123")

        # Should contain a user-friendly message and the raw error detail
        assert "error" in resp.lower()
        assert "PermissionDeniedError" in resp or "details" in resp.lower()


# ---------------------------------------------------------------------------
# Kept image injection into prompt
# ---------------------------------------------------------------------------


class TestKeptImageInjection:
    """Tests for kept-image injection in build_messages."""

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

    def test_images_included_in_context_snapshot(self, tmp_path):
        """Kept images should appear even when current_message is None (/context dump)."""
        builder = ContextBuilder(tmp_path)
        self._add_kept_image(builder, tmp_path)

        messages = builder.build_messages(
            history=[], current_message=None, channel="test", chat_id="1"
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
        assert len(image_msgs) == 1

        # Should have assistant ack after
        img_idx = messages.index(image_msgs[0])
        assert messages[img_idx + 1]["role"] == "assistant"
        assert messages[img_idx + 1]["content"] == "Noted."

    def test_no_images_no_injection_in_snapshot(self, tmp_path):
        """Without kept images, no extra messages in context snapshot."""
        builder = ContextBuilder(tmp_path)

        messages = builder.build_messages(
            history=[], current_message=None, channel="test", chat_id="1"
        )

        user_msgs = [m for m in messages if m["role"] == "user"]
        assert len(user_msgs) == 0


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
    async def test_process_direct_error_keeps_user_msg(self, tmp_path):
        """process_direct keeps user message on error, but no assistant message."""
        provider = FakeProvider([_error_response()])
        loop = await _make_loop(tmp_path, provider)

        session = loop.sessions.get_or_create("test:direct")
        msgs_before = len(session.messages)

        await loop.process_direct("hello", session_key="test:direct")

        # User message persists (+1), no assistant message
        assert len(session.messages) == msgs_before + 1
        assert session.messages[-1]["role"] == "user"


# ---------------------------------------------------------------------------
# run() error delivery
# ---------------------------------------------------------------------------


class TestRunErrorDelivery:
    """Verify that run() delivers errors to the user via the bus."""

    @pytest.mark.asyncio
    async def test_run_delivers_error_on_exception(self, tmp_path):
        """If _run_session_turn raises, the turn loop should deliver an error message."""
        provider = FakeProvider([])
        loop = await _make_loop(tmp_path, provider)

        # Make _run_session_turn raise
        async def exploding_turn(session_key, meta):
            raise RuntimeError("kaboom")

        loop._run_session_turn = exploding_turn

        # Publish an inbound message
        await loop.bus.publish_inbound(
            InboundMessage(channel="telegram", sender_id="u1", chat_id="123", content="hi")
        )

        # Run the loop briefly
        loop._running = True

        async def stop_after_delivery():
            await asyncio.sleep(0.3)
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
    """Tests for fs_read with keep=True."""

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
        kept = [e for e in builder.context.focus.entries if isinstance(e, FileEntry)]
        assert len(kept) == 0

    @pytest.mark.asyncio
    async def test_keep_true_persists_in_focus(self, tmp_path):
        """keep=True adds a FileEntry to the focus section."""
        from pocketfox.agent.tools.filesystem import ReadFileTool

        doc = tmp_path / "SKILL.md"
        doc.write_text("---\nname: test\n---\nDo the thing.", encoding="utf-8")

        builder = self._make_builder(tmp_path)
        tool = ReadFileTool(context_builder=builder)
        result = await tool.execute(path=str(doc), keep=True)

        assert "keeping" in result.lower()
        kept = [e for e in builder.context.focus.entries if isinstance(e, FileEntry)]
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
        """Reading the same file with keep=True twice should deduplicate in output."""
        from pocketfox.agent.tools.filesystem import ReadFileTool

        doc = tmp_path / "ref.md"
        doc.write_text("unique_marker_xyz", encoding="utf-8")

        builder = self._make_builder(tmp_path)
        tool = ReadFileTool(context_builder=builder)
        await tool.execute(path=str(doc), keep=True)
        await tool.execute(path=str(doc), keep=True)

        # LOOM deduplicates by identity at compile time — same resolved path
        # means the content appears only once in the rendered output
        messages = builder.build_messages(
            history=[], current_message="hello", channel="test", chat_id="1"
        )
        system_text = str(messages)
        assert system_text.count("unique_marker_xyz") == 1


# ---------------------------------------------------------------------------
# clear_kept_entries (images + files)
# ---------------------------------------------------------------------------


class TestClearKeptEntries:
    """Tests for ContextBuilder.clear_kept_entries() covering both types."""

    def test_clears_files_and_images(self, tmp_path):
        (tmp_path / "ref.md").write_text("content", encoding="utf-8")
        builder = ContextBuilder(tmp_path)
        builder.add_entry(
            "topic",
            ImageEntry(path=tmp_path / "a.png", base64_data=FAKE_B64, mime_type="image/png"),
        )
        builder.add_entry(
            "focus",
            FileEntry(path=tmp_path / "ref.md"),
        )

        removed = builder.clear_kept_entries()
        assert removed == 2

    def test_alias_clear_kept_images_still_works(self, tmp_path):
        """Backward-compatible alias should clear both types."""
        (tmp_path / "ref.md").write_text("content", encoding="utf-8")
        builder = ContextBuilder(tmp_path)
        builder.add_entry(
            "focus",
            FileEntry(path=tmp_path / "ref.md"),
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

    def test_preserves_bootstrap_file_entries(self, tmp_path):
        """Bootstrap FileEntries (not added via add_entry) must survive clearing."""
        (tmp_path / "AGENTS.md").write_text("bootstrap", encoding="utf-8")
        builder = ContextBuilder(tmp_path, default_context_files=["AGENTS.md"])

        # Force context creation so foundation gets populated
        _ = builder.context
        bootstrap_before = [
            e for e in builder.context.foundation.entries if isinstance(e, FileEntry)
        ]
        assert len(bootstrap_before) == 1

        # Add a runtime-kept file via add_entry
        (tmp_path / "ref.md").write_text("kept", encoding="utf-8")
        builder.add_entry("focus", FileEntry(path=tmp_path / "ref.md"))

        removed = builder.clear_kept_entries()
        assert removed == 1  # Only the runtime-kept entry

        # Bootstrap entry still there
        bootstrap_after = [
            e for e in builder.context.foundation.entries if isinstance(e, FileEntry)
        ]
        assert len(bootstrap_after) == 1


# ---------------------------------------------------------------------------
# Frontmatter role in bootstrap files
# ---------------------------------------------------------------------------


class TestFrontmatterRoleBootstrap:
    """Verify bootstrap FileEntries with frontmatter roles produce correct messages."""

    def test_bootstrap_file_with_assistant_role(self, tmp_path):
        """A workspace file with role: assistant should emit a separate assistant message."""
        agents_file = tmp_path / "AGENTS.md"
        agents_file.write_text(
            "---\nrole: assistant\n---\nI will help you with tasks.",
            encoding="utf-8",
        )

        builder = ContextBuilder(tmp_path, default_context_files=["AGENTS.md"])
        messages = builder.build_messages(
            history=[], current_message="hello", channel="test", chat_id="1"
        )

        roles = [m["role"] for m in messages]
        assert "assistant" in roles

        assistant_msgs = [m for m in messages if m["role"] == "assistant"]
        assistant_text = str(assistant_msgs)
        assert "I will help you with tasks." in assistant_text
        # Frontmatter should be stripped
        assert "---" not in assistant_text

    def test_bootstrap_file_without_frontmatter_stays_system(self, tmp_path):
        """A workspace file without frontmatter should stay in the system message."""
        agents_file = tmp_path / "AGENTS.md"
        agents_file.write_text("You are a helpful assistant.", encoding="utf-8")

        builder = ContextBuilder(tmp_path, default_context_files=["AGENTS.md"])
        messages = builder.build_messages(
            history=[], current_message="hello", channel="test", chat_id="1"
        )

        # All non-user messages should be system (plus the user message at the end)
        non_user = [m for m in messages if m["role"] != "user"]
        assert all(m["role"] == "system" for m in non_user)
        system_text = str(non_user)
        assert "You are a helpful assistant." in system_text


# ---------------------------------------------------------------------------
# FileEntry (loom) live-reload and deletion
# ---------------------------------------------------------------------------


class TestFileEntryLiveReload:
    """Verify loom FileEntry re-reads from disk and handles deletion."""

    def test_compile_reads_current_content(self, tmp_path):
        """compile() should return the file's current content, not stale data."""
        doc = tmp_path / "live.md"
        doc.write_text("version 1", encoding="utf-8")

        entry = FileEntry(path=doc)
        assert "version 1" in entry.compile()

        doc.write_text("version 2", encoding="utf-8")
        assert "version 2" in entry.compile()
        assert "version 1" not in entry.compile()

    def test_compile_handles_deleted_file(self, tmp_path):
        """compile() should return a notice when the file is deleted."""
        doc = tmp_path / "ephemeral.md"
        doc.write_text("exists", encoding="utf-8")

        entry = FileEntry(path=doc)
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
        builder.add_entry("focus", FileEntry(path=doc))

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


# ---------------------------------------------------------------------------
# _merge_consecutive
# ---------------------------------------------------------------------------


class TestMergeConsecutive:
    """Tests for AgentLoop._merge_consecutive."""

    def test_empty(self):
        assert AgentLoop._merge_consecutive([]) == []

    def test_already_alternating(self):
        history = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
        ]
        merged = AgentLoop._merge_consecutive(history)
        assert len(merged) == 3
        assert merged[0]["content"] == "a"

    def test_consecutive_user_messages(self):
        history = [
            {"role": "user", "content": "hello"},
            {"role": "user", "content": "world"},
            {"role": "user", "content": "!"},
        ]
        merged = AgentLoop._merge_consecutive(history)
        assert len(merged) == 1
        assert merged[0]["content"] == "hello\n\nworld\n\n!"

    def test_merge_preserves_media(self):
        history = [
            {"role": "user", "content": "a", "media": ["/img1.png"]},
            {"role": "user", "content": "b", "media": ["/img2.png"]},
        ]
        merged = AgentLoop._merge_consecutive(history)
        assert len(merged) == 1
        assert merged[0]["media"] == ["/img1.png", "/img2.png"]

    def test_merge_media_first_has_none(self):
        history = [
            {"role": "user", "content": "text only"},
            {"role": "user", "content": "with img", "media": ["/img.png"]},
        ]
        merged = AgentLoop._merge_consecutive(history)
        assert len(merged) == 1
        assert merged[0]["media"] == ["/img.png"]

    def test_does_not_mutate_input(self):
        history = [
            {"role": "user", "content": "a"},
            {"role": "user", "content": "b"},
        ]
        AgentLoop._merge_consecutive(history)
        assert history[0]["content"] == "a"
        assert history[1]["content"] == "b"

    def test_same_sender_merges(self):
        history = [
            {"role": "user", "content": "hi", "name": "alice"},
            {"role": "user", "content": "again", "name": "alice"},
        ]
        merged = AgentLoop._merge_consecutive(history)
        assert len(merged) == 1
        assert merged[0]["content"] == "hi\n\nagain"
        assert merged[0]["name"] == "alice"

    def test_different_senders_stay_separate(self):
        history = [
            {"role": "user", "content": "hi", "name": "alice"},
            {"role": "user", "content": "yo", "name": "bob"},
        ]
        merged = AgentLoop._merge_consecutive(history)
        assert len(merged) == 2
        assert [m["name"] for m in merged] == ["alice", "bob"]


# ---------------------------------------------------------------------------
# _prepare_turn_messages
# ---------------------------------------------------------------------------


class TestPrepareTurnMessages:
    """Tests for AgentLoop._prepare_turn_messages."""

    @pytest.mark.asyncio
    async def test_single_user_message(self, tmp_path):
        provider = FakeProvider([_ok_response()])
        loop = await _make_loop(tmp_path, provider)

        session = loop.sessions.get_or_create("test:1")
        session.add_message("user", "hello")

        history, content, media, sender = loop._prepare_turn_messages(session)
        assert history == []
        assert content == "hello"
        assert media is None

    @pytest.mark.asyncio
    async def test_multiple_user_messages_batched(self, tmp_path):
        provider = FakeProvider([_ok_response()])
        loop = await _make_loop(tmp_path, provider)

        session = loop.sessions.get_or_create("test:1")
        session.add_message("user", "msg1")
        session.add_message("user", "msg2")
        session.add_message("user", "msg3")

        history, content, media, sender = loop._prepare_turn_messages(session)
        assert history == []
        assert content == "msg1\n\nmsg2\n\nmsg3"

    @pytest.mark.asyncio
    async def test_with_prior_history(self, tmp_path):
        provider = FakeProvider([_ok_response()])
        loop = await _make_loop(tmp_path, provider)

        session = loop.sessions.get_or_create("test:1")
        session.add_message("user", "old")
        session.add_message("assistant", "reply")
        session.add_message("user", "new1")
        session.add_message("user", "new2")

        history, content, media, sender = loop._prepare_turn_messages(session)
        assert len(history) == 2  # old user + assistant
        assert content == "new1\n\nnew2"

    @pytest.mark.asyncio
    async def test_empty_session(self, tmp_path):
        provider = FakeProvider([_ok_response()])
        loop = await _make_loop(tmp_path, provider)

        session = loop.sessions.get_or_create("test:1")
        history, content, media, sender = loop._prepare_turn_messages(session)
        assert history == []
        assert content is None

    @pytest.mark.asyncio
    async def test_ends_with_assistant(self, tmp_path):
        provider = FakeProvider([_ok_response()])
        loop = await _make_loop(tmp_path, provider)

        session = loop.sessions.get_or_create("test:1")
        session.add_message("user", "q")
        session.add_message("assistant", "a")

        history, content, media, sender = loop._prepare_turn_messages(session)
        assert content is None

    @pytest.mark.asyncio
    async def test_carries_current_sender(self, tmp_path):
        provider = FakeProvider([_ok_response()])
        loop = await _make_loop(tmp_path, provider)

        session = loop.sessions.get_or_create("test:1")
        session.add_message("user", "hello", name="thygrrr")

        history, content, media, sender = loop._prepare_turn_messages(session)
        assert content == "hello"
        assert sender == "thygrrr"


# ---------------------------------------------------------------------------
# Turn queuing via run()
# ---------------------------------------------------------------------------


class TestTurnQueuing:
    """Tests for the ingest-then-turn architecture."""

    @pytest.mark.asyncio
    async def test_message_batching(self, tmp_path):
        """Multiple messages published before run() starts should batch into one turn."""
        provider = FakeProvider([_ok_response("batch reply")])
        loop = await _make_loop(tmp_path, provider)

        session = loop.sessions.get_or_create("telegram:42")
        msgs_before = len(session.messages)

        # Publish multiple messages before starting the loop
        for text in ["msg1", "msg2", "msg3"]:
            await loop.bus.publish_inbound(
                InboundMessage(channel="telegram", sender_id="u1", chat_id="42", content=text)
            )

        loop._running = True

        async def stop_after():
            await asyncio.sleep(0.5)
            loop.stop()

        asyncio.create_task(stop_after())
        await loop.run()

        # 3 new user messages + 1 assistant reply
        new_msgs = session.messages[msgs_before:]
        user_msgs = [m for m in new_msgs if m["role"] == "user"]
        assert len(user_msgs) == 3

        # Provider should have been called once (one turn, not three)
        assert len(provider.calls) == 1

    @pytest.mark.asyncio
    async def test_ingest_saves_immediately(self, tmp_path):
        """_ingest_message should save messages to session without waiting for a turn."""
        provider = FakeProvider([_ok_response()])
        loop = await _make_loop(tmp_path, provider)

        session = loop.sessions.get_or_create("telegram:99")
        msgs_before = len(session.messages)

        msg = InboundMessage(
            channel="telegram", sender_id="u1", chat_id="99", content="saved"
        )
        loop._ingest_message(msg)

        assert len(session.messages) == msgs_before + 1
        assert session.messages[-1]["content"] == "saved"


# ---------------------------------------------------------------------------
# Multi-context routing
# ---------------------------------------------------------------------------


async def _make_routed_loop(tmp_path: Path, provider: FakeProvider, contexts: dict):
    """Create an AgentLoop with a ContextRouter from a context dict."""
    from pocketfox.agent.router import ContextRouter
    from pocketfox.config.schema import ContextConfig, ExecToolConfig, VoiceToolConfig

    ctx_configs = {name: ContextConfig(**cfg) for name, cfg in contexts.items()}
    router = ContextRouter(ctx_configs)
    bus = MessageBus()
    sm = SessionManager(tmp_path)
    loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=tmp_path,
        model="fake/model",
        router=router,
        session_manager=sm,
        exec_config=ExecToolConfig(),
        voice_config=VoiceToolConfig(),
    )
    return loop


class TestMultiContextRouting:
    """Verify one inbound message fans out to multiple contexts."""

    @pytest.mark.asyncio
    async def test_fan_out_to_multiple_contexts(self, tmp_path):
        """A message matching two contexts should create two separate sessions."""
        provider = FakeProvider([_ok_response("reply A"), _ok_response("reply B")])
        loop = await _make_routed_loop(tmp_path, provider, {
            "ctx_a": {"inputs": ["telegram:*"], "outputs_responsive": ["telegram:*"]},
            "ctx_b": {"inputs": ["telegram:*"], "outputs_responsive": ["telegram:*"]},
        })

        msg = InboundMessage(
            channel="telegram", sender_id="u1", chat_id="500", content="hello both"
        )
        loop._ingest_message(msg)

        # Both contexts should have the message in separate sessions
        sess_a = loop.sessions.get_or_create("ctx_a:telegram:500")
        sess_b = loop.sessions.get_or_create("ctx_b:telegram:500")
        assert sess_a.messages[-1]["content"] == "hello both"
        assert sess_b.messages[-1]["content"] == "hello both"

        # Both contexts should have pending turns
        assert "ctx_a:telegram:500" in loop._ctx_meta["ctx_a"]
        assert "ctx_b:telegram:500" in loop._ctx_meta["ctx_b"]

    @pytest.mark.asyncio
    async def test_contexts_run_independently(self, tmp_path):
        """Two contexts process turns in parallel without blocking each other."""
        call_order = []
        turn_count = {"ctx_a": 0, "ctx_b": 0}

        # ctx_a will be slow, ctx_b will be fast
        slow_event = asyncio.Event()

        provider = FakeProvider([_ok_response("fast"), _ok_response("slow")])
        loop = await _make_routed_loop(tmp_path, provider, {
            "ctx_a": {"inputs": ["telegram:100"]},
            "ctx_b": {"inputs": ["telegram:200"]},
        })

        original_run_turn = loop._run_session_turn

        async def tracked_turn(session_key, meta):
            ctx = meta["context_name"]
            call_order.append(f"{ctx}_start")
            if ctx == "ctx_a":
                # Simulate slow processing
                await asyncio.sleep(0.3)
            result = await original_run_turn(session_key, meta)
            call_order.append(f"{ctx}_end")
            turn_count[ctx] += 1
            return result

        loop._run_session_turn = tracked_turn

        # Publish messages to both contexts
        await loop.bus.publish_inbound(
            InboundMessage(channel="telegram", sender_id="u1", chat_id="100", content="slow msg")
        )
        await loop.bus.publish_inbound(
            InboundMessage(channel="telegram", sender_id="u1", chat_id="200", content="fast msg")
        )

        async def stop_after():
            await asyncio.sleep(1.0)
            loop.stop()

        asyncio.create_task(stop_after())
        await loop.run()

        # Both contexts should have completed
        assert turn_count["ctx_a"] == 1
        assert turn_count["ctx_b"] == 1

        # ctx_b should have started before ctx_a ended (parallel execution)
        assert "ctx_b_start" in call_order
        assert "ctx_a_end" in call_order
        b_start = call_order.index("ctx_b_start")
        a_end = call_order.index("ctx_a_end")
        assert b_start < a_end, f"ctx_b should start before ctx_a ends: {call_order}"


# ---------------------------------------------------------------------------
# System message ingestion
# ---------------------------------------------------------------------------


class TestSystemMessageIngestion:
    """Verify system messages (subagent announces) are routed correctly."""

    @pytest.mark.asyncio
    async def test_system_message_parsed_and_saved(self, tmp_path):
        """A system message with ctx:channel:chat_id format saves to the right session."""
        provider = FakeProvider([_ok_response()])
        loop = await _make_loop(tmp_path, provider)

        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id="default:telegram:42",
            content="Task completed: weather is sunny",
        )

        session = loop.sessions.get_or_create("telegram:42")
        msgs_before = len(session.messages)

        loop._ingest_message(msg)

        # Should be saved with [System: subagent] prefix
        assert len(session.messages) == msgs_before + 1
        assert session.messages[-1]["role"] == "user"
        assert "[System: subagent]" in session.messages[-1]["content"]
        assert "weather is sunny" in session.messages[-1]["content"]

    @pytest.mark.asyncio
    async def test_system_message_queues_turn_in_correct_context(self, tmp_path):
        """System message routes to the origin context's turn loop."""
        provider = FakeProvider([_ok_response()])
        loop = await _make_routed_loop(tmp_path, provider, {
            "myctx": {"inputs": ["telegram:*"]},
        })

        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id="myctx:telegram:77",
            content="Background task done",
        )
        loop._ingest_message(msg)

        # Turn should be queued in "myctx" context
        assert "myctx:telegram:77" in loop._ctx_meta["myctx"]

    @pytest.mark.asyncio
    async def test_system_message_two_part_format(self, tmp_path):
        """A system message with channel:chat_id format (no context) defaults to 'default'."""
        provider = FakeProvider([_ok_response()])
        loop = await _make_loop(tmp_path, provider)

        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id="cli:direct",
            content="Done",
        )

        session = loop.sessions.get_or_create("cli:direct")
        msgs_before = len(session.messages)

        loop._ingest_message(msg)

        assert len(session.messages) == msgs_before + 1
        # Should queue in "default" context
        assert "cli:direct" in loop._ctx_meta["default"]


# ---------------------------------------------------------------------------
# Follow-up turns (messages during in-progress turn)
# ---------------------------------------------------------------------------


class TestFollowUpTurns:
    """Verify messages arriving between turns trigger follow-up turns."""

    @pytest.mark.asyncio
    async def test_message_after_turn_triggers_followup(self, tmp_path):
        """A message published after a turn completes should trigger a second turn."""

        class DelayedInjectProvider(FakeProvider):
            """Provider that signals when the first LLM call completes."""

            def __init__(self, responses, bus):
                super().__init__(responses)
                self._bus = bus
                self.first_call_done = asyncio.Event()

            async def chat(self, **kwargs):
                result = await super().chat(**kwargs)
                if self._call_count == 1:
                    self.first_call_done.set()
                return result

        from pocketfox.config.schema import ExecToolConfig, VoiceToolConfig

        bus = MessageBus()
        provider = DelayedInjectProvider(
            [_ok_response("first reply"), _ok_response("second reply")], bus
        )
        loop = AgentLoop(
            bus=bus,
            provider=provider,
            workspace=tmp_path,
            model="fake/model",
            session_manager=SessionManager(tmp_path),
            exec_config=ExecToolConfig(),
            voice_config=VoiceToolConfig(),
        )

        # Publish the initial message
        await bus.publish_inbound(
            InboundMessage(
                channel="telegram", sender_id="u1",
                chat_id="300", content="initial msg",
            )
        )

        async def inject_after_first_turn():
            await provider.first_call_done.wait()
            # Small delay to let the assistant response save
            await asyncio.sleep(0.15)
            await bus.publish_inbound(
                InboundMessage(
                    channel="telegram", sender_id="u1",
                    chat_id="300", content="followup msg",
                )
            )

        async def stop_after():
            await asyncio.sleep(2.0)
            loop.stop()

        asyncio.create_task(inject_after_first_turn())
        asyncio.create_task(stop_after())
        await loop.run()

        # Provider should have been called twice (two separate turns)
        assert provider._call_count == 2

        # Session should have both user messages and both assistant replies
        session = loop.sessions.get_or_create("telegram:300")
        recent = session.messages[-4:]
        roles = [m["role"] for m in recent]
        assert roles == ["user", "assistant", "user", "assistant"]
