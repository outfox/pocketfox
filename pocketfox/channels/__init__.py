"""Chat channels module with plugin architecture."""

from pocketfox.channels.base import BaseChannel
from pocketfox.channels.manager import ChannelManager

__all__ = ["BaseChannel", "ChannelManager"]
