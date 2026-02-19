"""Tests for parameter redaction in tools."""

from typing import Any

from pocketfox.agent.tools.base import Tool
from pocketfox.agent.tools.message import MessageTool
from pocketfox.agent.tools.registry import ToolRegistry


class SimpleTool(Tool):
    """A simple tool that doesn't override redact_params."""
    
    @property
    def name(self) -> str:
        return "simple"

    @property
    def description(self) -> str:
        return "simple tool"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "data": {"type": "string"},
            },
            "required": ["data"],
        }

    async def execute(self, **kwargs: Any) -> str:
        return "ok"


def test_base_tool_redact_params_returns_unchanged() -> None:
    """Base tool should return params unchanged."""
    tool = SimpleTool()
    params = {"data": "sensitive info", "extra": 123}
    result = tool.redact_params(params)
    assert result == params


def test_message_tool_redacts_phone_numbers() -> None:
    """MessageTool should redact phone numbers in chat_id."""
    tool = MessageTool()
    
    # Phone number should be redacted
    params = {"content": "Hello", "chat_id": "+4916090722506", "channel": "signal"}
    result = tool.redact_params(params)
    assert result["chat_id"] == "+***"
    assert result["content"] == "Hello"  # Content unchanged
    assert result["channel"] == "signal"  # Channel unchanged


def test_message_tool_preserves_non_phone_chat_ids() -> None:
    """MessageTool should not redact non-phone chat_ids."""
    tool = MessageTool()
    
    # Telegram numeric ID - not a phone number
    params = {"content": "Hello", "chat_id": "119853534", "channel": "telegram"}
    result = tool.redact_params(params)
    assert result["chat_id"] == "119853534"
    
    # Discord ID
    params = {"content": "Hello", "chat_id": "discord_user_123", "channel": "discord"}
    result = tool.redact_params(params)
    assert result["chat_id"] == "discord_user_123"


def test_message_tool_handles_missing_chat_id() -> None:
    """MessageTool should handle params without chat_id."""
    tool = MessageTool()
    params = {"content": "Hello"}
    result = tool.redact_params(params)
    assert result == params


def test_registry_redact_params() -> None:
    """Registry should delegate redaction to the correct tool."""
    registry = ToolRegistry()
    registry.register(MessageTool())
    registry.register(SimpleTool())
    
    # Message tool redacts phone numbers
    params = {"content": "Hi", "chat_id": "+491234567890"}
    result = registry.redact_params("message", params)
    assert result["chat_id"] == "+***"
    
    # Simple tool doesn't redact
    params = {"data": "secret"}
    result = registry.redact_params("simple", params)
    assert result["data"] == "secret"


def test_registry_redact_params_unknown_tool() -> None:
    """Registry should return params unchanged for unknown tools."""
    registry = ToolRegistry()
    params = {"data": "secret"}
    result = registry.redact_params("nonexistent", params)
    assert result == params
