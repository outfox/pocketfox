"""Telegram channel implementation using python-telegram-bot."""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

from loguru import logger
from telegram import BotCommand, Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel, SendError
from nanobot.config.schema import TelegramConfig

if TYPE_CHECKING:
    from nanobot.session.manager import SessionManager


def _markdown_to_telegram_html(text: str) -> str:
    """
    Convert markdown to Telegram-safe HTML.
    """
    if not text:
        return ""
    
    # 1. Extract and protect code blocks (preserve content from other processing)
    code_blocks: list[str] = []
    def save_code_block(m: re.Match) -> str:
        code_blocks.append(m.group(1))
        return f"\x00CB{len(code_blocks) - 1}\x00"
    
    text = re.sub(r'```[\w]*\n?([\s\S]*?)```', save_code_block, text)
    
    # 2. Extract and protect inline code
    inline_codes: list[str] = []
    def save_inline_code(m: re.Match) -> str:
        inline_codes.append(m.group(1))
        return f"\x00IC{len(inline_codes) - 1}\x00"
    
    text = re.sub(r'`([^`]+)`', save_inline_code, text)
    
    # 3. Headers # Title -> just the title text
    text = re.sub(r'^#{1,6}\s+(.+)$', r'\1', text, flags=re.MULTILINE)
    
    # 4. Blockquotes > text -> just the text (before HTML escaping)
    text = re.sub(r'^>\s*(.*)$', r'\1', text, flags=re.MULTILINE)
    
    # 5. Escape HTML special characters
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    
    # 6. Links [text](url) - must be before bold/italic to handle nested cases
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
    
    # 7. Bold **text** or __text__
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__(.+?)__', r'<b>\1</b>', text)
    
    # 8. Italic _text_ (avoid matching inside words like some_var_name)
    text = re.sub(r'(?<![a-zA-Z0-9])_([^_]+)_(?![a-zA-Z0-9])', r'<i>\1</i>', text)
    
    # 9. Strikethrough ~~text~~
    text = re.sub(r'~~(.+?)~~', r'<s>\1</s>', text)
    
    # 10. Bullet lists - item -> • item
    text = re.sub(r'^[-*]\s+', '• ', text, flags=re.MULTILINE)
    
    # 11. Restore inline code with HTML tags
    for i, code in enumerate(inline_codes):
        # Escape HTML in code content
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00IC{i}\x00", f"<code>{escaped}</code>")
    
    # 12. Restore code blocks with HTML tags
    for i, code in enumerate(code_blocks):
        # Escape HTML in code content
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00CB{i}\x00", f"<pre><code>{escaped}</code></pre>")
    
    return text


class TelegramChannel(BaseChannel):
    """
    Telegram channel using long polling.
    
    Simple and reliable - no webhook/public IP needed.
    """
    
    name = "telegram"
    
    # Commands registered with Telegram's command menu
    BOT_COMMANDS = [
        BotCommand("start", "Start the bot"),
        BotCommand("reset", "Reset conversation history"),
        BotCommand("help", "Show available commands"),
    ]
    
    def __init__(
        self,
        config: TelegramConfig,
        bus: MessageBus,
        groq_api_key: str = "",
        session_manager: SessionManager | None = None,
    ):
        super().__init__(config, bus)
        self.config: TelegramConfig = config
        self.groq_api_key = groq_api_key
        self.session_manager = session_manager
        self._app: Application | None = None
        self._chat_ids: dict[str, int] = {}  # Map sender_id to chat_id for replies
        self._typing_tasks: dict[str, asyncio.Task] = {}  # chat_id -> typing loop task
    
    async def start(self) -> None:
        """Start the Telegram bot with long polling."""
        if not self.config.token:
            logger.error("Telegram bot token not configured")
            return
        
        self._running = True
        
        # Build the application
        builder = Application.builder().token(self.config.token)
        if self.config.proxy:
            builder = builder.proxy(self.config.proxy).get_updates_proxy(self.config.proxy)
        self._app = builder.build()
        
        # Add command handlers
        self._app.add_handler(CommandHandler("start", self._on_start))
        self._app.add_handler(CommandHandler("reset", self._on_reset))
        self._app.add_handler(CommandHandler("help", self._on_help))
        
        # Add message handler for text, photos, voice, stickers, documents
        self._app.add_handler(
            MessageHandler(
                (filters.TEXT | filters.PHOTO | filters.VOICE | filters.AUDIO | filters.Sticker.ALL | filters.Document.ALL) 
                & ~filters.COMMAND, 
                self._on_message
            )
        )
        
        # Add error handler for cleaner network error logging
        self._app.add_error_handler(self._on_error)
        
        logger.info("Starting Telegram bot (polling mode)...")
        
        # Initialize and start polling
        await self._app.initialize()
        await self._app.start()
        
        # Get bot info and register command menu
        bot_info = await self._app.bot.get_me()
        logger.info(f"Telegram bot @{bot_info.username} connected")
        
        try:
            await self._app.bot.set_my_commands(self.BOT_COMMANDS)
            logger.debug("Telegram bot commands registered")
        except Exception as e:
            logger.warning(f"Failed to register bot commands: {e}")
        
        # Start polling (this runs until stopped)
        await self._app.updater.start_polling(
            allowed_updates=["message"],
            drop_pending_updates=True  # Ignore old messages on startup
        )
        
        # Keep running until stopped
        while self._running:
            await asyncio.sleep(1)
    
    async def stop(self) -> None:
        """Stop the Telegram bot."""
        self._running = False
        
        # Cancel all typing indicators
        for chat_id in list(self._typing_tasks):
            self._stop_typing(chat_id)
        
        if self._app:
            logger.info("Stopping Telegram bot...")
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            self._app = None
    
    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through Telegram.
        
        Raises:
            SendError: If the message could not be delivered.
        """
        if not self._app:
            raise SendError("Telegram bot not running")
        
        # Stop typing indicator for this chat
        self._stop_typing(msg.chat_id)
        
        try:
            # chat_id should be the Telegram chat ID (integer)
            chat_id = int(msg.chat_id)
        except ValueError:
            raise SendError(f"Invalid chat_id: {msg.chat_id}")
        
        try:
            # Send voice messages first (if any)
            for voice_path in msg.voice:
                await self._send_voice(chat_id, voice_path)
            
            # Send media files (if any)
            for media_path in msg.media:
                await self._send_media(chat_id, media_path)
            
            # Send text message (if any content)
            if msg.content and msg.content.strip():
                # Convert markdown to Telegram HTML
                html_content = _markdown_to_telegram_html(msg.content)
                await self._app.bot.send_message(
                    chat_id=chat_id,
                    text=html_content,
                    parse_mode="HTML"
                )
        except SendError:
            raise  # Re-raise our own errors
        except Exception as e:
            # Fallback to plain text if HTML parsing fails
            logger.warning(f"HTML parse failed, falling back to plain text: {e}")
            try:
                await self._app.bot.send_message(
                    chat_id=chat_id,
                    text=msg.content
                )
            except Exception as e2:
                logger.error(f"Error sending Telegram message: {e2}")
                raise SendError(f"Telegram error: {e2}") from e2
    
    async def _send_voice(self, chat_id: int, audio_path: str) -> None:
        """Send an audio file as a Telegram voice message.

        Converts the audio to OGG with OPUS codec (required by Telegram for voice messages).
        Supports common audio formats: mp3, wav, m4a, flac, ogg, etc.

        Args:
            chat_id: Telegram chat ID to send to.
            audio_path: Path to the audio file to send.

        Raises:
            SendError: If the voice message could not be sent.
        """
        if not self._app:
            raise SendError("Telegram bot not running")

        import subprocess
        import tempfile
        from pathlib import Path

        path = Path(audio_path)

        if not path.exists():
            raise SendError(f"Voice file not found: {audio_path}")

        try:
            # Create temp file for converted audio
            with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as tmp:
                ogg_path = Path(tmp.name)

            # Convert to OGG/OPUS using ffmpeg
            # -ac 1: mono (voice messages are typically mono)
            # -ar 48000: 48kHz sample rate (OPUS standard)
            # -b:a 64k: 64kbps bitrate (good quality for voice)
            result = subprocess.run(
                [
                    'ffmpeg', '-y', '-i', str(path),
                    '-c:a', 'libopus',
                    '-ac', '1',
                    '-ar', '48000',
                    '-b:a', '64k',
                    str(ogg_path)
                ],
                capture_output=True,
                timeout=30
            )

            if result.returncode != 0:
                raise SendError(f"ffmpeg conversion failed: {result.stderr.decode()}")

            # Send as voice message
            with open(ogg_path, 'rb') as f:
                await self._app.bot.send_voice(chat_id=chat_id, voice=f)

            logger.debug(f"Sent voice message: {audio_path}")

        except SendError:
            raise
        except subprocess.TimeoutExpired:
            raise SendError(f"Voice conversion timed out for {audio_path}")
        except Exception as e:
            raise SendError(f"Failed to send voice {audio_path}: {e}") from e
        finally:
            # Clean up temp file
            if 'ogg_path' in locals() and ogg_path.exists():
                ogg_path.unlink()
    
    async def _send_media(self, chat_id: int, media_path: str) -> None:
        """Send a media file to a chat.

        Routes files to the appropriate Telegram API method based on extension:
        - Images (.jpg, .png, .gif): send_photo
        - Stickers (.webp, .tgs): send_sticker
        - Voice (.ogg, .oga): send_voice (OGG with OPUS codec)
        - Audio (.mp3, .m4a, .wav, .flac): send_audio
        - Video (.mp4, .mov, .avi, .webm): send_video
        - Other: send_document

        Raises:
            SendError: If the media could not be sent.
        """
        if not self._app:
            raise SendError("Telegram bot not running")

        from pathlib import Path
        path = Path(media_path)

        if not path.exists():
            raise SendError(f"Media file not found: {media_path}")

        suffix = path.suffix.lower()

        try:
            with open(path, 'rb') as f:
                if suffix in ('.webp', '.tgs'):
                    # Stickers: .webp (static), .tgs (animated Lottie)
                    await self._app.bot.send_sticker(chat_id=chat_id, sticker=f)
                elif suffix in ('.jpg', '.jpeg', '.png', '.gif'):
                    await self._app.bot.send_photo(chat_id=chat_id, photo=f)
                elif suffix in ('.ogg', '.oga'):
                    # Voice messages must be OGG with OPUS codec
                    await self._app.bot.send_voice(chat_id=chat_id, voice=f)
                elif suffix in ('.mp3', '.m4a', '.wav', '.flac'):
                    await self._app.bot.send_audio(chat_id=chat_id, audio=f)
                elif suffix in ('.mp4', '.mov', '.avi', '.webm'):
                    # Video including .webm (video stickers also work as video)
                    await self._app.bot.send_video(chat_id=chat_id, video=f)
                else:
                    # Send as document (generic file)
                    await self._app.bot.send_document(chat_id=chat_id, document=f)

            logger.debug(f"Sent media: {media_path}")
        except SendError:
            raise
        except Exception as e:
            raise SendError(f"Failed to send media {media_path}: {e}") from e
    
    async def _on_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command."""
        if not update.message or not update.effective_user:
            return
        
        user = update.effective_user
        await update.message.reply_text(
            f"👋 Hi {user.first_name}! I'm nanobot.\n\n"
            "Send me a message and I'll respond!\n"
            "Type /help to see available commands."
        )
    
    async def _on_reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /reset command — clear conversation history."""
        if not update.message or not update.effective_user:
            return
        
        chat_id = str(update.message.chat_id)
        session_key = f"{self.name}:{chat_id}"
        
        if self.session_manager is None:
            logger.warning("/reset called but session_manager is not available")
            await update.message.reply_text("⚠️ Session management is not available.")
            return
        
        session = self.session_manager.get_or_create(session_key)
        msg_count = len(session.messages)
        session.clear()
        self.session_manager.save(session)
        
        logger.info(f"Session reset for {session_key} (cleared {msg_count} messages)")
        await update.message.reply_text("🔄 Conversation history cleared. Let's start fresh!")
    
    async def _on_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /help command — show available commands."""
        if not update.message:
            return
        
        help_text = (
            "🐈 <b>nanobot commands</b>\n\n"
            "/start — Start the bot\n"
            "/reset — Reset conversation history\n"
            "/help — Show this help message\n\n"
            "Just send me a text message to chat!"
        )
        await update.message.reply_text(help_text, parse_mode="HTML")
    
    async def _on_error(self, _update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle errors with cleaner logging for transient network issues.
        
        Args:
            _update: The update that caused the error (may be None for polling errors).
            context: The callback context containing the error.
        """
        error = context.error
        
        # Network errors are expected on flaky connections — log concisely, don't spam stacktrace
        if isinstance(error, (NetworkError, TimedOut)):
            # Extract the root cause message
            cause = str(error)
            if error.__cause__:
                cause = f"{type(error.__cause__).__name__}: {error.__cause__}"
            logger.warning(f"Telegram network error: {cause} — retrying")
            return
        
        # For unexpected errors, log the full context and re-raise
        logger.opt(exception=context.error).error(f"Telegram error: {error}")
        raise error
    
    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming messages (text, photos, voice, documents)."""
        if not update.message or not update.effective_user:
            return
        
        message = update.message
        user = update.effective_user
        chat_id = message.chat_id
        
        # Use stable numeric ID, but keep username for allowlist compatibility
        sender_id = str(user.id)
        if user.username:
            sender_id = f"{sender_id}|{user.username}"
        
        # Store chat_id for replies
        self._chat_ids[sender_id] = chat_id
        
        # Build content from text and/or media
        content_parts = []
        media_paths = []
        
        # Text content
        if message.text:
            content_parts.append(message.text)
        if message.caption:
            content_parts.append(message.caption)
        
        # Handle media files
        media_file = None
        media_type = None
        
        if message.photo:
            media_file = message.photo[-1]  # Largest photo
            media_type = "image"
        elif message.sticker:
            media_file = message.sticker
            # Differentiate sticker types: animated (.tgs), video (.webm), static (.webp)
            if message.sticker.is_video:
                media_type = "sticker_video"
            elif message.sticker.is_animated:
                media_type = "sticker_animated"
            else:
                media_type = "sticker"
        elif message.voice:
            media_file = message.voice
            media_type = "voice"
        elif message.audio:
            media_file = message.audio
            media_type = "audio"
        elif message.document:
            media_file = message.document
            media_type = "file"
        
        # Download media if present
        if media_file and self._app:
            try:
                file = await self._app.bot.get_file(media_file.file_id)
                ext = self._get_extension(media_type, getattr(media_file, 'mime_type', None))
                
                # Save to workspace/media/
                from pathlib import Path
                media_dir = Path.home() / ".nanobot" / "media"
                media_dir.mkdir(parents=True, exist_ok=True)
                
                file_path = media_dir / f"{media_file.file_id[:16]}{ext}"
                await file.download_to_drive(str(file_path))
                
                media_paths.append(str(file_path))
                
                # Handle voice transcription
                if media_type == "voice" or media_type == "audio":
                    from nanobot.providers.transcription import GroqTranscriptionProvider
                    transcriber = GroqTranscriptionProvider(api_key=self.groq_api_key)
                    transcription = await transcriber.transcribe(file_path)
                    if transcription:
                        logger.info(f"Transcribed {media_type}: {transcription[:50]}...")
                        content_parts.append(f"[transcription: {transcription}]")
                    else:
                        content_parts.append(f"[{media_type}: {file_path}]")
                else:
                    content_parts.append(f"[{media_type}: {file_path}]")
                    
                logger.debug(f"Downloaded {media_type} to {file_path}")
            except Exception as e:
                logger.error(f"Failed to download media: {e}")
                content_parts.append(f"[{media_type}: download failed]")
        
        content = "\n".join(content_parts) if content_parts else "[empty message]"
        
        logger.debug(f"Telegram message from {sender_id}: {content[:50]}...")
        
        str_chat_id = str(chat_id)
        
        # Forward to the message bus (typing indicator is started in _handle_message
        # AFTER access check passes, to avoid leaking typing to denied senders)
        await self._handle_message(
            sender_id=sender_id,
            chat_id=str_chat_id,
            content=content,
            media=media_paths,
            metadata={
                "message_id": message.message_id,
                "user_id": user.id,
                "username": user.username,
                "first_name": user.first_name,
                "is_group": message.chat.type != "private"
            }
        )
    
    async def _start_typing_indicator(self, chat_id: str) -> None:
        """Start sending 'typing...' indicator for a chat.
        
        Overrides the base class method to provide Telegram-specific typing feedback.
        This is called from _handle_message AFTER access check passes.
        """
        self._start_typing(chat_id)
    
    def _start_typing(self, chat_id: str) -> None:
        """Start sending 'typing...' indicator for a chat."""
        # Cancel any existing typing task for this chat
        self._stop_typing(chat_id)
        self._typing_tasks[chat_id] = asyncio.create_task(self._typing_loop(chat_id))
    
    def _stop_typing(self, chat_id: str) -> None:
        """Stop the typing indicator for a chat."""
        task = self._typing_tasks.pop(chat_id, None)
        if task and not task.done():
            task.cancel()
    
    async def _typing_loop(self, chat_id: str) -> None:
        """Repeatedly send 'typing' action until cancelled."""
        try:
            while self._app:
                await self._app.bot.send_chat_action(chat_id=int(chat_id), action="typing")
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"Typing indicator stopped for {chat_id}: {e}")
    
    def _get_extension(self, media_type: str, mime_type: str | None) -> str:
        """Get file extension based on media type."""
        if mime_type:
            ext_map = {
                "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
                "audio/ogg": ".ogg", "audio/mpeg": ".mp3", "audio/mp4": ".m4a",
            }
            if mime_type in ext_map:
                return ext_map[mime_type]
        
        type_map = {
            "image": ".jpg",
            "voice": ".ogg",
            "audio": ".mp3",
            "sticker": ".webp",           # Static stickers
            "sticker_animated": ".tgs",   # Animated (Lottie) stickers
            "sticker_video": ".webm",     # Video stickers
            "file": ""
        }
        return type_map.get(media_type, "")
