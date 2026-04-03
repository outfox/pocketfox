"""Shared parser for OpenAI-compatible chat completion responses."""

import json
from typing import Any

from pocketfox.providers.base import LLMResponse, ToolCallRequest


def parse_chat_response(data: dict[str, Any]) -> LLMResponse:
    """Parse an OpenAI-format chat completion response into LLMResponse.

    Works with OpenRouter, OpenAI, vLLM, and any OpenAI-compatible endpoint.
    """
    choice = data["choices"][0]
    message = choice.get("message", {})

    # Content
    content = message.get("content")

    # Tool calls
    tool_calls = []
    for tc in message.get("tool_calls") or []:
        args = tc["function"]["arguments"]
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {"raw": args}
        tool_calls.append(
            ToolCallRequest(id=tc["id"], name=tc["function"]["name"], arguments=args)
        )

    # Usage statistics
    usage: dict[str, int] = {}
    raw_usage = data.get("usage")
    if raw_usage:
        usage["prompt_tokens"] = int(raw_usage.get("prompt_tokens", 0))
        usage["completion_tokens"] = int(raw_usage.get("completion_tokens", 0))
        usage["total_tokens"] = int(raw_usage.get("total_tokens", 0))

        # Cache statistics — OpenRouter uses prompt_tokens_details.cached_tokens,
        # Anthropic via OpenRouter also populates cache_creation_input_tokens.
        details = raw_usage.get("prompt_tokens_details") or {}
        if cached := details.get("cached_tokens"):
            usage["cache_read_input_tokens"] = int(cached)
        if created := raw_usage.get("cache_creation_input_tokens"):
            usage["cache_creation_input_tokens"] = int(created)

    # Reasoning content (DeepSeek-R1, Kimi, etc.)
    reasoning_content = message.get("reasoning") or message.get("reasoning_content")

    return LLMResponse(
        content=content,
        tool_calls=tool_calls,
        finish_reason=choice.get("finish_reason") or "stop",
        usage=usage,
        reasoning_content=reasoning_content,
    )
