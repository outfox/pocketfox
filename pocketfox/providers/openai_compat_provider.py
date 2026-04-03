"""OpenAI-compatible provider for vLLM, AiHubMix, and custom endpoints."""

from typing import Any

import httpx
from loguru import logger

from pocketfox.providers.base import LLMProvider, LLMResponse
from pocketfox.providers.response_parser import parse_chat_response


class OpenAICompatProvider(LLMProvider):
    """LLM provider for any OpenAI-compatible endpoint.

    Covers vLLM / local servers, AiHubMix, and other OpenAI-compatible
    gateways that accept the standard chat completions payload.
    """

    def __init__(
        self,
        api_key: str,
        api_base: str,
        default_model: str,
        extra_headers: dict[str, str] | None = None,
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        # Normalize: ensure api_base doesn't end with /
        self._endpoint = f"{api_base.rstrip('/')}/chat/completions"
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            **(extra_headers or {}),
        }

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 100000,
        temperature: float = 0.7,
    ) -> LLMResponse:
        body: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    self._endpoint,
                    headers=self._headers,
                    json=body,
                    timeout=300.0,
                )
                resp.raise_for_status()
                return parse_chat_response(resp.json())
        except Exception as e:
            logger.error(f"OpenAI-compat API error ({self._endpoint}): {e}")
            return LLMResponse(
                content=f"Error calling LLM: {e}",
                finish_reason="error",
            )

    def get_default_model(self) -> str:
        return self.default_model
