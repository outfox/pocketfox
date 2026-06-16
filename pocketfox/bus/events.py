"""Event types for the message bus."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class InboundMessage:
    """Message received from a chat channel."""

    channel: str  # telegram, discord, slack, whatsapp
    sender_id: str  # Stable user identifier (used for the allowlist)
    chat_id: str  # Chat/channel identifier
    content: str  # Message text
    sender_name: str | None = None  # Human-readable sender (e.g. chat handle "thygrrr")
    timestamp: datetime = field(default_factory=datetime.now)
    media: list[str] = field(default_factory=list)  # Media URLs
    metadata: dict[str, Any] = field(default_factory=dict)  # Channel-specific data
    cache_ttl: int | None = None  # Anthropic prompt cache TTL in seconds
    context_name: str | None = None  # Context to route through

    @property
    def session_key(self) -> str:
        """Unique key for session identification."""
        return f"{self.channel}:{self.chat_id}"


@dataclass
class OutboundMessage:
    """Message to send to a chat channel."""

    channel: str
    chat_id: str
    content: str
    reply_to: str | None = None
    media: list[str] = field(default_factory=list)
    voice: list[str] = field(default_factory=list)  # Audio files to send as voice messages
    metadata: dict[str, Any] = field(default_factory=dict)
