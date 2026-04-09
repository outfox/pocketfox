"""Tests for ContextBuilder.add_tool_result and the agent loop's image fan-out.

These exist to lock in the fix for the Anthropic structural contract that
all `tool_result` blocks for a given assistant `tool_use` message must live
in the single user message immediately following it. Earlier behaviour
appended an extra image-bearing `user` message between consecutive
`tool_result` blocks, which crashed the provider with:

    "tool_use ids were found without tool_result blocks immediately after"
"""

from pathlib import Path
from typing import Any

import pytest

from pocketfox.agent.context import ContextBuilder


@pytest.fixture
def builder(tmp_path: Path) -> ContextBuilder:
    return ContextBuilder(tmp_path)


def _multimodal_image_result(text: str = "Image: frame.jpg") -> list[dict[str, Any]]:
    """Shape of a ViewImageTool result: [image_url block, text block]."""
    return [
        {
            "type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64,FAKEBASE64=="},
        },
        {"type": "text", "text": text},
    ]


def _assistant_with_tool_uses(ids: list[str]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": tid, "type": "function", "function": {"name": "view_image", "arguments": "{}"}}
            for tid in ids
        ],
    }


class TestAddToolResultStringResult:
    """add_tool_result with a plain string result appends one tool message."""

    def test_string_result_appends_tool_message(self, builder: ContextBuilder) -> None:
        messages: list[dict[str, Any]] = []
        images = builder.add_tool_result(messages, "tool_id_1", "exec", "ok")

        assert images == []
        assert len(messages) == 1
        assert messages[0]["role"] == "tool"
        assert messages[0]["tool_call_id"] == "tool_id_1"
        assert messages[0]["name"] == "exec"
        assert messages[0]["content"] == "ok"


class TestAddToolResultMultimodalResult:
    """Multimodal results must NOT inject a follow-up user message inline."""

    def test_multimodal_result_returns_image_blocks(self, builder: ContextBuilder) -> None:
        messages: list[dict[str, Any]] = []
        result = _multimodal_image_result("Image: a.jpg")

        images = builder.add_tool_result(messages, "tool_id_1", "view_image", result)

        # Exactly one tool message was appended.
        assert len(messages) == 1
        assert messages[0]["role"] == "tool"
        assert messages[0]["tool_call_id"] == "tool_id_1"
        # Text portion went into the tool result.
        assert messages[0]["content"] == "Image: a.jpg"

        # Image blocks were returned to the caller, NOT appended.
        assert len(images) == 1
        assert images[0]["type"] == "image_url"

    def test_multimodal_result_with_no_text_uses_placeholder(self, builder: ContextBuilder) -> None:
        messages: list[dict[str, Any]] = []
        # Image-only multimodal result (no text block).
        result = [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,X=="}},
        ]
        images = builder.add_tool_result(messages, "tid", "view_image", result)
        assert messages[0]["content"] == "(see image below)"
        assert len(images) == 1


class TestAddToolResultParallelDoesNotInterleave:
    """The crash repro: three parallel view_image calls in one assistant turn.

    After three sequential add_tool_result calls, the message sequence must
    be: assistant(tool_uses) → tool, tool, tool — with no user messages in
    between. This is the condition Anthropic enforces.
    """

    def test_three_parallel_tool_results_are_contiguous(
        self, builder: ContextBuilder
    ) -> None:
        messages: list[dict[str, Any]] = [
            _assistant_with_tool_uses(["t1", "t2", "t3"]),
        ]

        pending: list[dict[str, Any]] = []
        for tid, name in [
            ("t1", "Image: frame_01.jpg"),
            ("t2", "Image: frame_03.jpg"),
            ("t3", "Image: frame_05.jpg"),
        ]:
            images = builder.add_tool_result(
                messages, tid, "view_image", _multimodal_image_result(name)
            )
            pending.extend(images)

        # Roles after the assistant message must be three tool messages with
        # no user messages interleaved.
        roles_after_assistant = [m["role"] for m in messages[1:]]
        assert roles_after_assistant == ["tool", "tool", "tool"]

        # IDs preserved and in order.
        assert [m["tool_call_id"] for m in messages[1:]] == ["t1", "t2", "t3"]

        # All three images were buffered for the caller to append in one shot.
        assert len(pending) == 3
        assert all(b["type"] == "image_url" for b in pending)


class TestAgentLoopImageFanOut:
    """End-to-end check on the AgentLoop tool execution loop.

    Drives a fake provider that returns three parallel view_image calls and
    asserts the resulting message sequence is well-formed for Anthropic.
    """

    @pytest.mark.asyncio
    async def test_loop_appends_single_combined_image_user_message(
        self, tmp_path: Path
    ) -> None:
        from unittest.mock import AsyncMock

        from pocketfox.agent.loop import AgentLoop
        from pocketfox.bus.queue import MessageBus
        from pocketfox.config.schema import ExecToolConfig, VoiceToolConfig
        from pocketfox.providers.base import LLMResponse, ToolCallRequest

        # First response: three parallel view_image calls. Second: final text.
        first = LLMResponse(
            content="",
            tool_calls=[
                ToolCallRequest(id="t1", name="view_image", arguments={"path": "/tmp/a.jpg"}),
                ToolCallRequest(id="t2", name="view_image", arguments={"path": "/tmp/b.jpg"}),
                ToolCallRequest(id="t3", name="view_image", arguments={"path": "/tmp/c.jpg"}),
            ],
            finish_reason="tool_calls",
        )
        second = LLMResponse(content="done", finish_reason="stop")

        provider = AsyncMock()
        provider.get_default_model = lambda: "fake/model"
        provider.chat = AsyncMock(side_effect=[first, second])

        loop = AgentLoop(
            bus=MessageBus(),
            provider=provider,
            workspace=tmp_path,
            exec_config=ExecToolConfig(),
            voice_config=VoiceToolConfig(),
        )

        # Stub the registry: view_image always returns a multimodal result.
        async def fake_execute(name: str, params: dict) -> list[dict]:
            return _multimodal_image_result(f"Image: {Path(params['path']).name}")

        loop.tools.execute = fake_execute  # type: ignore[assignment]
        loop.tools.redact_params = lambda name, params: params  # type: ignore[assignment]

        messages: list[dict[str, Any]] = [{"role": "user", "content": "describe"}]
        final = await loop._run_llm_loop(messages, session=None, model="fake/model")

        assert final == "done"

        # Inspect the messages that were sent to the SECOND provider call.
        # Anthropic contract: assistant(tool_use) → ONE message (tool_results)
        # → optional user image message → next assistant.
        sent = provider.chat.call_args_list[1].kwargs["messages"]

        # Find the assistant message that issued the tool calls.
        assistant_idxs = [
            i for i, m in enumerate(sent) if m["role"] == "assistant" and m.get("tool_calls")
        ]
        assert assistant_idxs, "expected an assistant message with tool_calls"
        a_idx = assistant_idxs[-1]

        # The next three messages must be tool_result messages, contiguous.
        assert sent[a_idx + 1]["role"] == "tool"
        assert sent[a_idx + 2]["role"] == "tool"
        assert sent[a_idx + 3]["role"] == "tool"
        assert [sent[a_idx + i]["tool_call_id"] for i in (1, 2, 3)] == ["t1", "t2", "t3"]

        # The combined image user message follows the three tool messages.
        combined = sent[a_idx + 4]
        assert combined["role"] == "user"
        image_blocks = [b for b in combined["content"] if b.get("type") == "image_url"]
        assert len(image_blocks) == 3
