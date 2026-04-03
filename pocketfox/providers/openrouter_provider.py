"""OpenRouter provider — uses the openrouter SDK for LLM calls."""

import json
from typing import Any

from loguru import logger
from openrouter import OpenRouter

from pocketfox.providers.base import LLMProvider, LLMResponse, ToolCallRequest

# Per-model parameter overrides.
_MODEL_OVERRIDES: dict[str, dict[str, Any]] = {
    "kimi-k2.5": {"temperature": 1.0},
}


class OpenRouterProvider(LLMProvider):
    """LLM provider using the OpenRouter SDK.

    OpenRouter is a multi-provider gateway that routes to 300+ models
    (Anthropic, OpenAI, DeepSeek, Gemini, Groq, etc.) through a single
    API key.
    """

    def __init__(
        self,
        api_key: str,
        default_model: str = "anthropic/claude-sonnet-4-6",
        extra_headers: dict[str, str] | None = None,
    ):
        super().__init__(api_key, api_base=None)
        self.default_model = default_model
        self._extra_headers = extra_headers
        self._client = OpenRouter(api_key=api_key)

    @staticmethod
    def _resolve_model(model: str) -> str:
        """Strip legacy 'openrouter/' prefix if present."""
        if model.startswith("openrouter/"):
            return model[len("openrouter/"):]
        return model

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 100000,
        temperature: float = 0.7,
    ) -> LLMResponse:
        model = self._resolve_model(model or self.default_model)

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        # Per-model overrides
        for pattern, overrides in _MODEL_OVERRIDES.items():
            if pattern in model.lower():
                kwargs.update(overrides)
                break

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        if self._extra_headers:
            kwargs["http_headers"] = self._extra_headers

        try:
            result = await self._client.chat.send_async(**kwargs)
            return self._parse_response(result)
        except Exception as e:
            err = str(e)
            # OpenRouter may reject media types the model doesn't advertise.
            # Retry without those blocks so the bot still responds.
            if "support input video" in err or "support input audio" in err:
                logger.warning(f"Retrying without unsupported media: {err}")
                kwargs["messages"] = self._strip_media_blocks(messages)
                try:
                    result = await self._client.chat.send_async(**kwargs)
                    return self._parse_response(result)
                except Exception as retry_err:
                    logger.error(f"OpenRouter API error (retry): {retry_err}")
                    return LLMResponse(
                        content=f"Error calling LLM: {retry_err}",
                        finish_reason="error",
                    )
            logger.error(f"OpenRouter API error: {e}")
            return LLMResponse(
                content=f"Error calling LLM: {e}",
                finish_reason="error",
            )

    @staticmethod
    def _strip_media_blocks(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove video_url / input_audio blocks, keep text and images."""
        _strip_types = {"video_url", "input_audio"}
        out = []
        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, list):
                out.append(msg)
                continue
            filtered = []
            for block in content:
                if isinstance(block, dict) and block.get("type") in _strip_types:
                    media_type = block["type"].replace("_", " ")
                    filtered.append({
                        "type": "text",
                        "text": f"({media_type} attachment — not supported by current model)",
                    })
                else:
                    filtered.append(block)
            out.append({**msg, "content": filtered})
        return out

    @staticmethod
    def _parse_response(result: Any) -> LLMResponse:
        """Parse OpenRouter SDK response into LLMResponse."""
        choice = result.choices[0]
        message = choice.message

        # Content
        content = message.content

        # Tool calls
        tool_calls = []
        for tc in message.tool_calls or []:
            args = tc.function.arguments
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"raw": args}
            tool_calls.append(
                ToolCallRequest(id=tc.id, name=tc.function.name, arguments=args)
            )

        # Usage statistics
        usage: dict[str, int] = {}
        if result.usage:
            usage["prompt_tokens"] = int(result.usage.prompt_tokens)
            usage["completion_tokens"] = int(result.usage.completion_tokens)
            usage["total_tokens"] = int(result.usage.total_tokens)

            # Cache statistics
            details = result.usage.prompt_tokens_details
            if details and hasattr(details, "cached_tokens") and details.cached_tokens:
                usage["cache_read_input_tokens"] = int(details.cached_tokens)
            if details and hasattr(details, "cache_write_tokens") and details.cache_write_tokens:
                usage["cache_creation_input_tokens"] = int(details.cache_write_tokens)

        # Reasoning content (DeepSeek-R1, Kimi, etc.)
        reasoning_content = getattr(message, "reasoning", None)

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            usage=usage,
            reasoning_content=reasoning_content,
        )

    def get_default_model(self) -> str:
        return self.default_model
