"""Message tool for sending messages to users."""

import re
from typing import Any, Awaitable, Callable

from pocketfox.agent.tools.base import Tool
from pocketfox.bus.events import OutboundMessage
from pocketfox.channels.base import SendError


class MessageTool(Tool):
    """Tool to send messages to users on chat channels."""

    def __init__(
        self,
        send_callback: Callable[[OutboundMessage], Awaitable[None]] | None = None,
    ):
        self._send_callback = send_callback

    def set_send_callback(self, callback: Callable[[OutboundMessage], Awaitable[None]]) -> None:
        """Set the callback for sending messages."""
        self._send_callback = callback

    @property
    def name(self) -> str:
        return "message"

    @property
    def description(self) -> str:
        return "Send a message to the user. Use this when you want to communicate something."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The message content to send"},
                "channel": {
                    "type": "string",
                    "description": "Optional: target channel (telegram, discord, etc.)",
                },
                "chat_id": {"type": "string", "description": "Optional: target chat/user ID"},
                "media": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional: list of file paths to send as media (images, audio, documents)"
                    ),
                },
                "voice": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional: list of audio file paths to send"
                        " as voice messages (will be converted to OGG/OPUS)"
                    ),
                },
            },
            "required": ["content"],
        }

    async def execute(
        self,
        content: str,
        channel: str | None = None,
        chat_id: str | None = None,
        media: list[str] | None = None,
        voice: list[str] | None = None,
        **kwargs: Any,
    ) -> str:
        from pocketfox.agent.task_context import get_task_context

        tc = get_task_context()
        channel = channel or tc.channel
        chat_id = chat_id or tc.chat_id

        if not channel or not chat_id:
            return "Error: No target channel/chat specified"

        if not self._send_callback:
            return "Error: Message sending not configured"

        msg = OutboundMessage(
            channel=channel, chat_id=chat_id, content=content, media=media or [], voice=voice or []
        )

        try:
            await self._send_callback(msg)
            attachments = []
            if media:
                attachments.append(f"{len(media)} media")
            if voice:
                attachments.append(f"{len(voice)} voice")
            attachment_info = f" with {', '.join(attachments)}" if attachments else ""
            return f"Message sent to {channel}:{chat_id}{attachment_info}"
        except SendError as e:
            return f"Error: Failed to send message - {str(e)}"
        except Exception as e:
            return f"Error sending message: {str(e)}"

    def redact_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Redact phone numbers in chat_id for logging."""
        result = params.copy()
        chat_id = result.get("chat_id", "")
        # Redact phone numbers (start with + followed by digits)
        if chat_id and re.match(r"^\+\d", chat_id):
            result["chat_id"] = "+***"
        return result
