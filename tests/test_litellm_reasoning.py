"""Tests for reasoning_content / reasoning_details parsing and gateway override routing."""

from types import SimpleNamespace
from typing import Any

from pocketfox.providers.litellm_provider import LiteLLMProvider, _merge_overrides


def _fake_response(
    *,
    content: str = "answer",
    reasoning_content: Any = None,
    reasoning_details: Any = None,
    model_extra: dict[str, Any] | None = None,
) -> SimpleNamespace:
    """Build an object shaped enough like a LiteLLM ModelResponse for _parse_response."""
    message = SimpleNamespace(
        content=content,
        tool_calls=None,
        reasoning_content=reasoning_content,
        reasoning_details=reasoning_details,
        model_extra=model_extra,
    )
    choice = SimpleNamespace(message=message, finish_reason="stop")
    return SimpleNamespace(choices=[choice], usage=None)


def _provider() -> LiteLLMProvider:
    # provider_name="openrouter" routes through the OpenRouter gateway spec
    # without requiring a real api_key / network access.
    return LiteLLMProvider(
        api_key="sk-or-test",
        default_model="openrouter/xiaomi/mimo-v2-pro",
        provider_name="openrouter",
    )


# ---------------------------------------------------------------------------
# _parse_response — reasoning extraction
# ---------------------------------------------------------------------------


def test_parse_reasoning_details_populated_synthesizes_content():
    provider = _provider()
    details = [
        {"type": "reasoning.text", "text": "first step"},
        {"type": "reasoning.text", "text": "second step"},
    ]
    response = _fake_response(
        content="final",
        reasoning_content=None,
        reasoning_details=details,
    )

    parsed = provider._parse_response(response)

    assert parsed.content == "final"
    assert parsed.reasoning_details == details
    assert parsed.reasoning_content == "first step\nsecond step"


def test_parse_reasoning_content_only_backwards_compatible():
    provider = _provider()
    response = _fake_response(
        content="final",
        reasoning_content="thinking...",
        reasoning_details=None,
    )

    parsed = provider._parse_response(response)

    assert parsed.reasoning_content == "thinking..."
    assert parsed.reasoning_details is None


def test_parse_reasoning_details_via_model_extra_fallback():
    provider = _provider()
    details = [{"type": "reasoning.text", "text": "hidden in extras"}]
    response = _fake_response(
        content="final",
        reasoning_content=None,
        reasoning_details=None,
        model_extra={"reasoning_details": details},
    )

    parsed = provider._parse_response(response)

    assert parsed.reasoning_details == details
    assert parsed.reasoning_content == "hidden in extras"


def test_parse_no_reasoning_at_all():
    provider = _provider()
    response = _fake_response(content="final")

    parsed = provider._parse_response(response)

    assert parsed.content == "final"
    assert parsed.reasoning_content is None
    assert parsed.reasoning_details is None


# ---------------------------------------------------------------------------
# _apply_model_overrides — gateway routing
# ---------------------------------------------------------------------------


def test_gateway_override_fires_for_openrouter_mimo():
    provider = _provider()
    kwargs: dict[str, Any] = {"model": "openrouter/xiaomi/mimo-v2-pro", "messages": []}

    provider._apply_model_overrides("openrouter/xiaomi/mimo-v2-pro", kwargs)

    assert kwargs["extra_body"] == {"reasoning": {"enabled": True}}


def test_gateway_override_does_not_fire_for_unrelated_openrouter_model():
    provider = _provider()
    kwargs: dict[str, Any] = {"model": "openrouter/anthropic/claude-3", "messages": []}

    provider._apply_model_overrides("openrouter/anthropic/claude-3", kwargs)

    assert "extra_body" not in kwargs


# ---------------------------------------------------------------------------
# _merge_overrides — extra_body deep merge
# ---------------------------------------------------------------------------


def test_merge_overrides_deep_merges_extra_body():
    kwargs: dict[str, Any] = {"extra_body": {"existing": "value"}}
    overrides = {"extra_body": {"reasoning": {"enabled": True}}}

    _merge_overrides(kwargs, overrides)

    assert kwargs["extra_body"] == {
        "existing": "value",
        "reasoning": {"enabled": True},
    }


def test_merge_overrides_sets_extra_body_when_absent():
    kwargs: dict[str, Any] = {}
    overrides = {"extra_body": {"reasoning": {"enabled": True}}}

    _merge_overrides(kwargs, overrides)

    assert kwargs["extra_body"] == {"reasoning": {"enabled": True}}


def test_merge_overrides_non_extra_body_keys_overwrite():
    kwargs: dict[str, Any] = {"temperature": 0.7}
    overrides = {"temperature": 1.0}

    _merge_overrides(kwargs, overrides)

    assert kwargs["temperature"] == 1.0
