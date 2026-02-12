"""Voice tool: Text-to-speech generation using ElevenLabs Python SDK."""

import asyncio
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, ClassVar

from loguru import logger

from nanobot.agent.tools.base import Tool


class VoiceTool(Tool):
    """Generate voice audio from text using ElevenLabs TTS.
    
    Uses the ElevenLabs Python SDK directly for secure API key handling
    and better control over TTS generation.
    """
    
    name = "voice"
    description = (
        "Generate voice audio from text using ElevenLabs TTS. "
        "Supports v3 direction tags like [excited], [whispers], [pause], etc. "
        "Returns the path to the generated audio file."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Text to convert to speech. Can include ElevenLabs v3 direction tags."
            },
            "output_path": {
                "type": "string",
                "description": "Optional output path for the audio file. If not provided, generates a unique file in workspace."
            },
            "voice_id": {
                "type": "string",
                "description": "ElevenLabs voice ID. Defaults to configured voice."
            },
            "stability": {
                "type": "number",
                "description": "Voice stability (0.0=creative, 0.5=natural, 1.0=robust). Default: 0.0",
                "minimum": 0.0,
                "maximum": 1.0
            },
            "speed": {
                "type": "number",
                "description": "Speech speed multiplier (0.5=slow, 1.0=normal, 2.0=fast). Default: 1.0",
                "minimum": 0.5,
                "maximum": 2.0
            },
            "title": {
                "type": "string",
                "description": "Optional title for audio metadata."
            },
            "artist": {
                "type": "string",
                "description": "Optional artist for audio metadata. Default: 'Blue Duval'"
            }
        },
        "required": ["text"]
    }
    
    def __init__(
        self,
        api_key: str | None = None,
        default_voice_id: str | None = None,
        default_stability: float = 0.0,
        workspace: Path | None = None,
    ):
        """Initialize the voice tool.
        
        Args:
            api_key: ElevenLabs API key from config.
            default_voice_id: Default voice ID to use.
            default_stability: Default stability setting (0.0-1.0).
            workspace: Workspace path for output files.
        """
        self.api_key = api_key or ""
        self.default_voice_id = default_voice_id or "JBFqnCBsd6RMkjVDRZzb"  # George (neutral English)
        self.default_stability = default_stability
        self.workspace = workspace or Path.home() / ".nanobot" / "workspace"
        
        # Check if ffmpeg is available for metadata
        self._ffmpeg_path = shutil.which("ffmpeg")
        
        # Lazy-load ElevenLabs client
        self._client = None
    
    def _get_client(self):
        """Get or create the ElevenLabs client."""
        if self._client is None:
            try:
                from elevenlabs.client import ElevenLabs
                self._client = ElevenLabs(api_key=self.api_key)
            except ImportError:
                raise RuntimeError("elevenlabs package not installed. Run: pip install elevenlabs")
        return self._client
    
    async def execute(
        self,
        text: str,
        output_path: str | None = None,
        voice_id: str | None = None,
        stability: float | None = None,
        speed: float | None = None,
        title: str | None = None,
        artist: str | None = None,
        **_kwargs: Any,  # Accept extra params for Tool interface compatibility
    ) -> str:
        """Generate voice audio from text.
        
        Args:
            text: Text to convert to speech.
            output_path: Optional output path for the audio file.
            voice_id: ElevenLabs voice ID.
            stability: Voice stability setting (0.0-1.0).
            speed: Speech speed multiplier (0.5-2.0).
            title: Optional title for metadata.
            artist: Optional artist for metadata.
        
        Returns:
            Path to the generated audio file, or error message.
        """
        if not self.api_key:
            return "Error: ElevenLabs API key not configured. Set tools.voice.apiKey in config."
        
        if not text.strip():
            return "Error: Text cannot be empty."
        
        # Apply defaults
        voice_id = voice_id or self.default_voice_id
        stability = stability if stability is not None else self.default_stability
        artist = artist or "Blue Duval"
        
        # Determine output path
        if output_path:
            final_path = Path(output_path)
        else:
            voice_dir = self.workspace / "media" / "voice"
            voice_dir.mkdir(parents=True, exist_ok=True)
            # Use milliseconds for better uniqueness
            timestamp = int(time.time() * 1000)
            final_path = voice_dir / f"voice_{timestamp}.mp3"
        
        final_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            # Generate audio using ElevenLabs Python SDK
            audio_bytes = await self._generate_audio(
                text=text,
                voice_id=voice_id,
                stability=stability,
                speed=speed,
            )
            
            # Write to temp file first
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                tmp.write(audio_bytes)
                raw_path = tmp.name
            
            # Add metadata with ffmpeg if available
            if self._ffmpeg_path and (title or artist):
                metadata_success = await self._add_metadata(
                    raw_path=raw_path,
                    final_path=final_path,
                    title=title,
                    artist=artist,
                )
                if not metadata_success:
                    # Fallback: move raw file without metadata
                    shutil.move(raw_path, final_path)
            else:
                shutil.move(raw_path, final_path)
            
            logger.info(f"Voice generated: {final_path}")
            return str(final_path)
            
        except (OSError, asyncio.SubprocessError) as e:
            logger.exception("Voice generation failed")
            return f"Error: {e}"
        except Exception as e:
            logger.exception("Voice generation failed unexpectedly")
            return f"Error: {e}"
    
    async def _generate_audio(
        self,
        text: str,
        voice_id: str,
        stability: float,
        speed: float | None,
    ) -> bytes:
        """Generate audio using ElevenLabs SDK.
        
        Runs the synchronous SDK call in a thread pool to avoid blocking.
        
        Args:
            text: Text to convert.
            voice_id: Voice ID to use.
            stability: Stability setting.
            speed: Speed multiplier (optional).
            
        Returns:
            Audio data as bytes.
        """
        from elevenlabs.types import VoiceSettings
        
        client = self._get_client()
        
        # Build voice settings
        voice_settings = VoiceSettings(
            stability=stability,
            similarity_boost=0.75,  # Good default for natural sound
            speed=speed,  # None means use default (1.0)
        )
        
        # Run synchronous API call in thread pool
        loop = asyncio.get_event_loop()
        audio_iterator = await loop.run_in_executor(
            None,
            lambda: client.text_to_speech.convert(
                voice_id=voice_id,
                text=text,
                model_id="eleven_v3",
                output_format="mp3_44100_128",
                voice_settings=voice_settings,
            )
        )
        
        # Collect all chunks from the iterator
        audio_chunks = []
        for chunk in audio_iterator:
            audio_chunks.append(chunk)
        
        return b"".join(audio_chunks)
    
    async def _add_metadata(
        self,
        raw_path: str,
        final_path: Path,
        title: str | None,
        artist: str | None,
    ) -> bool:
        """Add ID3 metadata to audio file using ffmpeg.
        
        Args:
            raw_path: Path to raw audio file.
            final_path: Path for output file with metadata.
            title: Optional title tag.
            artist: Optional artist tag.
            
        Returns:
            True if successful, False if ffmpeg failed.
        """
        metadata_args = [
            self._ffmpeg_path,
            "-y",  # Overwrite output
            "-i", raw_path,
            "-c:a", "copy",
            "-id3v2_version", "3",
        ]
        
        if title:
            metadata_args.extend(["-metadata", f"title={title}"])
        if artist:
            metadata_args.extend(["-metadata", f"artist={artist}"])
        
        metadata_args.extend(["-metadata", "album=Voice Notes"])
        metadata_args.append(str(final_path))
        
        proc = await asyncio.create_subprocess_exec(
            *metadata_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        _stdout, stderr = await proc.communicate()
        
        if proc.returncode != 0:
            error_msg = stderr.decode() if stderr else "unknown error"
            logger.warning(f"ffmpeg metadata failed (returncode={proc.returncode}): {error_msg}")
            return False
        
        # Clean up raw file only on success
        try:
            os.unlink(raw_path)
        except OSError:
            pass
        
        return True
    
    def redact_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Redact sensitive content from logs."""
        redacted = params.copy()
        # Truncate long text for logging
        if "text" in redacted and len(redacted["text"]) > 100:
            redacted["text"] = redacted["text"][:100] + "..."
        return redacted
