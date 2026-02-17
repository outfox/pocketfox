"""Message bus module for decoupled channel-agent communication."""

from pocketfox.bus.events import InboundMessage, OutboundMessage
from pocketfox.bus.queue import MessageBus

__all__ = ["MessageBus", "InboundMessage", "OutboundMessage"]
