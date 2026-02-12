"""Signal channel implementation using signal-cli-rest-api."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import aiohttp
from loguru import logger

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel, SendError
from nanobot.config.schema import SignalConfig

if TYPE_CHECKING:
    from nanobot.session.manager import SessionManager


class SignalChannel(BaseChannel):
    """
    Signal channel that connects to signal-cli-rest-api.

    Uses the REST API for sending messages and WebSocket for receiving.
    Receives via WebSocket connection to ws://<host>/v1/receive/{number}.
    See: https://github.com/bbernhard/signal-cli-rest-api
    """

    name = "signal"

    def __init__(
        self,
        config: SignalConfig,
        bus: MessageBus,
        groq_api_key: str = "",
        session_manager: SessionManager | None = None,
    ):
        super().__init__(config, bus)
        self.config: SignalConfig = config
        self.groq_api_key = groq_api_key
        self.session_manager = session_manager
        self._session: aiohttp.ClientSession | None = None

    @property
    def _base_url(self) -> str:
        """Get base URL for REST API."""
        return self.config.api_url.rstrip("/")

    @property
    def _ws_url(self) -> str:
        """Get WebSocket URL for receiving messages."""
        # Convert http:// to ws:// and https:// to wss://
        ws_base = self._base_url.replace("http://", "ws://").replace("https://", "wss://")
        return f"{ws_base}/v1/receive/{quote(self.config.phone_number, safe='')}"

    async def start(self) -> None:
        """Start the Signal channel by connecting to WebSocket for messages."""
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
                    # Connect to WebSocket for receiving messages
                    async with self._session.ws_connect(self._ws_url) as ws:
                        logger.info("Signal WebSocket connected")

                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    await self._handle_signal_message(msg.data)
                                except Exception as e:
                                    logger.error(f"Error handling Signal message: {e}")
                            elif msg.type == aiohttp.WSMsgType.ERROR:
                                logger.error(f"Signal WebSocket error: {ws.exception()}")
                                break
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING):
                                logger.info("Signal WebSocket closed")
                                break

                except asyncio.CancelledError:
                    logger.info("Signal channel task cancelled")
                    raise
                except aiohttp.ClientError as e:
                    logger.warning(f"Signal WebSocket connection error: {e}")

                    if self._running:
                        logger.info("Reconnecting in 5 seconds...")
                        await asyncio.sleep(5)
                except Exception as e:
                    logger.error(f"Unexpected error in Signal channel: {e}")
                    if self._running:
                        logger.info("Reconnecting in 5 seconds...")
                        await asyncio.sleep(5)
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop the Signal channel."""
        self._running = False

        if self._session:
            await self._session.close()
            self._session = None

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through Signal.
        
        Raises:
            SendError: If the message could not be delivered.
        """
        if not self._session:
            raise SendError("Signal session not initialized")

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
        
        try:
            async with self._session.post(url, json=payload, timeout=send_timeout) as resp:
                if resp.status not in (200, 201):
                    text = await resp.text()
                    logger.error(f"Failed to send Signal message: {resp.status} - {text}")
                    raise SendError(f"Signal API error {resp.status}: {text}")
                else:
                    logger.debug(f"Signal message sent to {msg.chat_id}")
        except SendError:
            raise  # Re-raise our own errors
        except aiohttp.ClientError as e:
            logger.error(f"Signal network error: {e}")
            raise SendError(f"Signal network error: {e}") from e
        except Exception as e:
            logger.error(f"Error sending Signal message: {e}")
            raise SendError(f"Signal error: {e}") from e

    async def _handle_signal_message(self, raw: str) -> None:
        """Handle a message from the Signal REST API."""
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

        # Handle commands (Signal doesn't have native bot commands)
        message_text = data_message.get("message", "")
        if message_text and message_text.strip().startswith("/"):
            command = message_text.strip().split()[0].lower()
            if command == "/reset":
                await self._handle_reset_command(source, source_name)
                return
            # Unknown commands fall through to normal message handling

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

        # Handle attachments - download them!
        attachments = data_message.get("attachments", [])
        media_paths = []
        content_parts = [content] if content else []

        for att in attachments:
            att_id = att.get("id", "")
            content_type = att.get("contentType", "")
            filename = att.get("filename", "")

            if att_id:
                # Download the attachment
                downloaded_path = await self._download_attachment(
                    att_id, content_type, filename
                )
                if downloaded_path:
                    media_paths.append(downloaded_path)

                    # Transcribe voice/audio
                    if content_type.startswith("audio/"):
                        transcription = await self._transcribe_audio(downloaded_path)
                        if transcription:
                            content_parts.append(f"[transcription: {transcription}]")
                        else:
                            content_parts.append(f"[voice: {downloaded_path}]")
                    elif content_type.startswith("image/"):
                        content_parts.append(f"[image: {downloaded_path}]")
                    elif content_type.startswith("video/"):
                        content_parts.append(f"[video: {downloaded_path}]")
                    else:
                        content_parts.append(f"[file: {downloaded_path}]")

        # Rebuild content with attachment info
        content = "\n".join(content_parts) if content_parts else ""

        # Skip empty messages (could be just an attachment or reaction)
        if not content and not media_paths:
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
            media=media_paths if media_paths else None,
            metadata={
                "timestamp": timestamp,
                "source_name": source_name,
                "is_group": is_group,
                "group_id": group_info.get("groupId") if is_group else None,
            }
        )

    async def _handle_reset_command(self, sender: str, sender_name: str) -> None:
        """Handle /reset command — clear conversation history.

        Args:
            sender: The phone number of the sender.
            sender_name: The display name of the sender.
        """
        chat_id = sender  # For DMs, chat_id is the sender's number
        session_key = f"{self.name}:{chat_id}"

        if self.session_manager is None:
            logger.warning("/reset called but session_manager is not available")
            await self.send(OutboundMessage(
                channel=self.name,
                chat_id=chat_id,
                content="⚠️ Session management is not available."
            ))
            return

        session = self.session_manager.get_or_create(session_key)
        msg_count = len(session.messages)
        session.clear()
        self.session_manager.save(session)

        display_name = sender_name or sender
        logger.info(f"Session reset for {session_key} (cleared {msg_count} messages)")
        await self.send(OutboundMessage(
            channel=self.name,
            chat_id=chat_id,
            content="🔄 Conversation history cleared. Let's start fresh!"
        ))

    async def _download_attachment(
        self, att_id: str, content_type: str, filename: str
    ) -> str | None:
        """Download an attachment from signal-cli-rest-api.

        Args:
            att_id: The attachment ID from signal-cli.
            content_type: MIME type of the attachment.
            filename: Original filename (may be empty).

        Returns:
            Path to the downloaded file, or None on failure.
        """
        if not self._session:
            return None

        # Determine file extension
        ext = self._get_extension(content_type, filename)

        # Create media directory
        media_dir = Path.home() / ".nanobot" / "media" / "signal"
        media_dir.mkdir(parents=True, exist_ok=True)

        # Use attachment ID as filename (truncated for sanity)
        safe_id = att_id.replace("/", "_").replace("\\", "_")[:32]
        file_path = media_dir / f"{safe_id}{ext}"

        try:
            url = f"{self._base_url}/v1/attachments/{att_id}"
            async with self._session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    file_path.write_bytes(data)
                    logger.debug(f"Downloaded Signal attachment to {file_path}")
                    return str(file_path)
                else:
                    text = await resp.text()
                    logger.error(
                        f"Failed to download attachment {att_id}: {resp.status} - {text}"
                    )
                    return None
        except Exception as e:
            logger.error(f"Error downloading Signal attachment: {e}")
            return None

    def _get_extension(self, content_type: str, filename: str) -> str:
        """Get file extension from content type or filename."""
        # Try to get from filename first
        if filename:
            path = Path(filename)
            if path.suffix:
                return path.suffix.lower()

        # Map common MIME types to extensions
        mime_map = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/gif": ".gif",
            "image/webp": ".webp",
            "audio/ogg": ".ogg",
            "audio/mpeg": ".mp3",
            "audio/mp4": ".m4a",
            "audio/aac": ".aac",
            "video/mp4": ".mp4",
            "video/webm": ".webm",
            "application/pdf": ".pdf",
        }

        return mime_map.get(content_type, "")

    async def _transcribe_audio(self, file_path: str) -> str:
        """Transcribe an audio file using Groq.

        Args:
            file_path: Path to the audio file.

        Returns:
            Transcribed text, or empty string on failure.
        """
        if not self.groq_api_key:
            logger.debug("Groq API key not configured, skipping transcription")
            return ""

        try:
            from nanobot.providers.transcription import GroqTranscriptionProvider

            transcriber = GroqTranscriptionProvider(api_key=self.groq_api_key)
            transcription = await transcriber.transcribe(file_path)
            if transcription:
                logger.info(f"Transcribed Signal voice: {transcription[:50]}...")
            return transcription
        except Exception as e:
            logger.error(f"Signal transcription error: {e}")
            return ""
