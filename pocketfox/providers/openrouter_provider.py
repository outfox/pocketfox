"""OpenRouter provider — direct HTTP client against the OpenRouter API."""

from typing import Any

import httpx
from loguru import logger

from pocketfox.providers.base import LLMProvider, LLMResponse
from pocketfox.providers.response_parser import parse_chat_response

_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Per-model parameter overrides.
_MODEL_OVERRIDES: dict[str, dict[str, Any]] = {
    "kimi-k2.5": {"temperature": 1.0},
}


class OpenRouterProvider(LLMProvider):
    """LLM provider using the OpenRouter API.

    OpenRouter is a multi-provider gateway that routes to 300+ models
    (Anthropic, OpenAI, DeepSeek, Gemini, Groq, etc.) through a single
    API key — no per-provider env vars or prefix routing needed.
    """

    def __init__(
        self,
        api_key: str,
        default_model: str = "anthropic/claude-sonnet-4-6",
        extra_headers: dict[str, str] | None = None,
    ):
        super().__init__(api_key, api_base=_API_URL)
        self.default_model = default_model
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            **(extra_headers or {}),
        }

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

        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        # Per-model overrides
        for pattern, overrides in _MODEL_OVERRIDES.items():
            if pattern in model.lower():
                body.update(overrides)
                break

        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    _API_URL,
                    headers=self._headers,
                    json=body,
                    timeout=300.0,
                )
                resp.raise_for_status()
                return parse_chat_response(resp.json())
        except Exception as e:
            logger.error(f"OpenRouter API error: {e}")
            return LLMResponse(
                content=f"Error calling LLM: {e}",
                finish_reason="error",
            )

    def get_default_model(self) -> str:
        return self.default_model
