"""Signal channel implementation using signal-cli-rest-api."""

import asyncio
import base64
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiohttp
from loguru import logger

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import SignalConfig


class SignalChannel(BaseChannel):
    """
    Signal channel that connects to signal-cli-rest-api.
    
    Uses the REST API for sending and WebSocket for receiving messages.
    See: https://github.com/bbernhard/signal-cli-rest-api
    """
    
    name = "signal"
    
    def __init__(self, config: SignalConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: SignalConfig = config
        self._ws = None
        self._session: aiohttp.ClientSession | None = None
    
    @property
    def _base_url(self) -> str:
        """Get base URL for REST API."""
        return self.config.api_url.rstrip("/")
    
    @property
    def _ws_url(self) -> str:
        """Get WebSocket URL for receiving messages."""
        # Convert http(s) to ws(s)
        base = self._base_url
        if base.startswith("https://"):
            ws_base = "wss://" + base[8:]
        elif base.startswith("http://"):
            ws_base = "ws://" + base[7:]
        else:
            ws_base = "ws://" + base
        
        return f"{ws_base}/v1/receive/{quote(self.config.phone_number, safe='')}"
    
    async def start(self) -> None:
        """Start the Signal channel by connecting to the REST API WebSocket."""
        self._session = aiohttp.ClientSession()
        self._running = True
        
        logger.info(f"Connecting to Signal API at {self._base_url}...")
        logger.info(f"Using phone number: {self.config.phone_number}")
        
        while self._running:
            try:
                async with self._session.ws_connect(self._ws_url) as ws:
                    self._ws = ws
                    logger.info("Connected to Signal WebSocket")
                    
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                await self._handle_signal_message(msg.data)
                            except Exception as e:
                                logger.error(f"Error handling Signal message: {e}")
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            logger.error(f"WebSocket error: {ws.exception()}")
                            break
                        elif msg.type == aiohttp.WSMsgType.CLOSED:
                            logger.info("WebSocket closed")
                            break
                            
            except asyncio.CancelledError:
                break
            except aiohttp.ClientError as e:
                self._ws = None
                logger.warning(f"Signal connection error: {e}")
                
                if self._running:
                    logger.info("Reconnecting in 5 seconds...")
                    await asyncio.sleep(5)
            except Exception as e:
                self._ws = None
                logger.error(f"Unexpected error in Signal channel: {e}")
                
                if self._running:
                    await asyncio.sleep(5)
    
    async def stop(self) -> None:
        """Stop the Signal channel."""
        self._running = False
        
        if self._ws:
            await self._ws.close()
            self._ws = None
        
        if self._session:
            await self._session.close()
            self._session = None
    
    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through Signal."""
        if not self._session:
            logger.warning("Signal session not initialized")
            return
        
        try:
            payload: dict[str, Any] = {
                "message": msg.content,
                "number": self.config.phone_number,
                "recipients": [msg.chat_id]
            }
            
            # Handle media attachments
            if msg.media:
                base64_attachments = []
                for media_path in msg.media:
                    path = Path(media_path)
                    if path.exists():
                        with open(path, "rb") as f:
                            encoded = base64.b64encode(f.read()).decode("utf-8")
                            base64_attachments.append(encoded)
                    else:
                        logger.warning(f"Media file not found: {media_path}")
                
                if base64_attachments:
                    payload["base64_attachments"] = base64_attachments
            
            url = f"{self._base_url}/v2/send"
            async with self._session.post(url, json=payload) as resp:
                if resp.status != 200 and resp.status != 201:
                    text = await resp.text()
                    logger.error(f"Failed to send Signal message: {resp.status} - {text}")
                else:
                    logger.debug(f"Signal message sent to {msg.chat_id}")
                    
        except Exception as e:
            logger.error(f"Error sending Signal message: {e}")
    
    async def _handle_signal_message(self, raw: str) -> None:
        """Handle a message from the Signal WebSocket."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON from Signal: {raw[:100]}")
            return
        
        # The signal-cli-rest-api sends envelope objects
        envelope = data.get("envelope", {})
        
        # Get sender info
        source = envelope.get("source") or envelope.get("sourceNumber", "")
        source_name = envelope.get("sourceName", "")
        
        if not source:
            # Not a message we care about (e.g., receipt)
            return
        
        # Check for data message (actual text messages)
        data_message = envelope.get("dataMessage", {})
        
        if not data_message:
            # Could be a sync message, typing indicator, receipt, etc.
            sync_message = envelope.get("syncMessage", {})
            if sync_message:
                # Handle sent messages (for multi-device sync)
                sent = sync_message.get("sentMessage", {})
                if sent:
                    # This is a message we sent from another device, ignore
                    return
            return
        
        # Extract message content
        content = data_message.get("message", "")
        timestamp = data_message.get("timestamp", 0)
        
        # Handle group messages
        group_info = data_message.get("groupInfo", {})
        is_group = bool(group_info)
        
        if is_group:
            group_id = group_info.get("groupId", "")
            chat_id = f"group.{group_id}" if group_id else source
        else:
            chat_id = source
        
        # Handle attachments
        attachments = data_message.get("attachments", [])
        media_urls = []
        
        for att in attachments:
            # signal-cli-rest-api provides attachment info
            att_id = att.get("id", "")
            
            if att_id:
                # Could download via /v1/attachments/<id> endpoint
                media_urls.append(f"{self._base_url}/v1/attachments/{att_id}")
        
        # Skip empty messages (could be just an attachment or reaction)
        if not content and not media_urls:
            # Check for reaction
            reaction = data_message.get("reaction", {})
            if reaction:
                emoji = reaction.get("emoji", "")
                target_author = reaction.get("targetAuthor", "")
                logger.debug(f"Signal reaction: {emoji} from {source} on message from {target_author}")
                return
            
            # Check for sticker
            sticker = data_message.get("sticker", {})
            if sticker:
                content = "[Sticker]"
            else:
                return
        
        # Check allowlist
        if self.config.allow_from and source not in self.config.allow_from:
            logger.debug(f"Ignoring Signal message from non-allowed sender: {source}")
            return
        
        logger.info(f"Signal message from {source_name or source}: {content[:50]}...")
        
        await self._handle_message(
            sender_id=source,
            chat_id=chat_id,
            content=content,
            media=media_urls if media_urls else None,
            metadata={
                "timestamp": timestamp,
                "source_name": source_name,
                "is_group": is_group,
                "group_id": group_info.get("groupId") if is_group else None,
            }
        )
