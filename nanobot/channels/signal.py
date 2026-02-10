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

    Uses the REST API for both sending and receiving messages.
    Receives via HTTP polling on GET /v1/receive/{number}.
    See: https://github.com/bbernhard/signal-cli-rest-api
    """

    name = "signal"

    POLL_INTERVAL = 1.0  # seconds between polls

    def __init__(self, config: SignalConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: SignalConfig = config
        self._session: aiohttp.ClientSession | None = None

    @property
    def _base_url(self) -> str:
        """Get base URL for REST API."""
        return self.config.api_url.rstrip("/")

    @property
    def _receive_url(self) -> str:
        """Get URL for receiving messages via HTTP polling."""
        return f"{self._base_url}/v1/receive/{quote(self.config.phone_number, safe='')}"

    async def start(self) -> None:
        """Start the Signal channel by polling the REST API for messages."""
        # Fail fast if phone number is not configured
        if not self.config.phone_number:
            logger.error("Signal phone_number is not configured - cannot start channel")
            return

        timeout = aiohttp.ClientTimeout(total=30)
        self._session = aiohttp.ClientSession(timeout=timeout)
        self._running = True

        logger.info(f"Connecting to Signal API at {self._base_url}...")
        # Redact phone number for privacy (show only last 4 digits)
        redacted = self.config.phone_number
        if len(redacted) > 4:
            redacted = f"{'*' * (len(redacted) - 4)}{redacted[-4:]}"
        logger.info(f"Using phone number: {redacted}")

        try:
            while self._running:
                try:
                    async with self._session.get(self._receive_url) as resp:
                        if resp.status == 200:
                            messages = await resp.json()
                            for msg_data in messages:
                                try:
                                    await self._handle_signal_message(
                                        json.dumps(msg_data)
                                    )
                                except Exception as e:
                                    logger.error(
                                        f"Error handling Signal message: {e}"
                                    )
                        elif resp.status != 204:
                            text = await resp.text()
                            logger.warning(
                                f"Signal receive returned {resp.status}: {text}"
                            )

                    await asyncio.sleep(self.POLL_INTERVAL)

                except asyncio.CancelledError:
                    logger.info("Signal channel task cancelled")
                    raise
                except aiohttp.ClientError as e:
                    logger.warning(f"Signal connection error: {e}")

                    if self._running:
                        logger.info("Reconnecting in 5 seconds...")
                        await asyncio.sleep(5)
                except Exception as e:
                    self._running = False
                    logger.error(f"Unexpected error in Signal channel: {e}")
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop the Signal channel."""
        self._running = False

        if self._session:
            await self._session.close()
            self._session = None

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through Signal."""
        if not self._session:
            logger.warning("Signal session not initialized")
            return

        try:
            # Strip "group." prefix from chat_id for group messages
            # The v2/send endpoint expects raw group IDs in recipients array
            recipient = msg.chat_id
            if recipient.startswith("group."):
                recipient = recipient[6:]  # Remove "group." prefix

            payload: dict[str, Any] = {
                "message": msg.content,
                "number": self.config.phone_number,
                "recipients": [recipient]
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
            send_timeout = aiohttp.ClientTimeout(total=30)
            async with self._session.post(url, json=payload, timeout=send_timeout) as resp:
                if resp.status not in (200, 201):
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

        logger.info(f"Signal message from {source_name or source}")
        logger.debug(f"Signal message preview: {content[:50]}...")

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
