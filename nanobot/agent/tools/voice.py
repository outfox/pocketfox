"""Voice tool: Text-to-speech generation using ElevenLabs."""

import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool


class VoiceTool(Tool):
    """Generate voice audio from text using ElevenLabs TTS."""
    
    name = "voice"
    description = (
        "Generate voice audio from text using ElevenLabs TTS. "
        "Supports v3 direction tags like [excited], [whispers], [pause], etc. "
        "Returns the path to the generated audio file."
    )
    parameters = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Text to convert to speech. Can include ElevenLabs v3 direction tags."
            },
            "output_path": {
                "type": "string",
                "description": "Optional output path for the audio file. If not provided, generates a temp file."
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
        """
        Initialize the voice tool.
        
        Args:
            api_key: ElevenLabs API key. Falls back to ELEVENLABS_API_KEY env var.
            default_voice_id: Default voice ID to use.
            default_stability: Default stability setting (0.0-1.0).
            workspace: Workspace path for output files.
        """
        self.api_key = api_key or os.environ.get("ELEVENLABS_API_KEY", "")
        self.default_voice_id = default_voice_id or "5kN3CFEeRreSoAQlWcb9"  # Julia
        self.default_stability = default_stability
        self.workspace = workspace or Path.home() / ".nanobot" / "workspace"
        
        # Check if sag is available
        self._sag_path = shutil.which("sag")
        self._ffmpeg_path = shutil.which("ffmpeg")
    
    async def execute(
        self,
        text: str,
        output_path: str | None = None,
        voice_id: str | None = None,
        stability: float | None = None,
        title: str | None = None,
        artist: str | None = None,
        **kwargs: Any
    ) -> str:
        """
        Generate voice audio from text.
        
        Args:
            text: Text to convert to speech.
            output_path: Optional output path for the audio file.
            voice_id: ElevenLabs voice ID.
            stability: Voice stability setting.
            title: Optional title for metadata.
            artist: Optional artist for metadata.
        
        Returns:
            Path to the generated audio file, or error message.
        """
        if not self.api_key:
            return "Error: ElevenLabs API key not configured. Set voice.apiKey in config or ELEVENLABS_API_KEY env var."
        
        if not self._sag_path:
            return "Error: 'sag' CLI tool not found. Please install it first."
        
        if not text.strip():
            return "Error: Text cannot be empty."
        
        # Use defaults
        voice_id = voice_id or self.default_voice_id
        stability = stability if stability is not None else self.default_stability
        artist = artist or "Blue Duval"
        
        # Generate output path if not provided
        if output_path:
            final_path = Path(output_path)
        else:
            # Generate in workspace/media/voice/
            voice_dir = self.workspace / "media" / "voice"
            voice_dir.mkdir(parents=True, exist_ok=True)
            
            # Create unique filename
            import time
            timestamp = int(time.time())
            final_path = voice_dir / f"voice_{timestamp}.mp3"
        
        # Ensure parent directory exists
        final_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            # Step 1: Generate raw audio with sag
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                raw_path = tmp.name
            
            sag_cmd = [
                self._sag_path,
                "--api-key", self.api_key,
                "--voice-id", voice_id,
                "--stability", str(stability),
                "-o", raw_path,
                text
            ]
            
            logger.debug(f"Running sag: voice_id={voice_id}, stability={stability}")
            
            proc = await asyncio.create_subprocess_exec(
                *sag_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            
            if proc.returncode != 0:
                error = stderr.decode() if stderr else "Unknown error"
                return f"Error generating voice: {error}"
            
            # Step 2: Add metadata with ffmpeg (if available)
            if self._ffmpeg_path and (title or artist):
                metadata_args = [
                    self._ffmpeg_path,
                    "-y",  # Overwrite
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
                await proc.communicate()
                
                # Clean up raw file
                try:
                    os.unlink(raw_path)
                except OSError:
                    pass
            else:
                # No ffmpeg or no metadata, just move the file
                shutil.move(raw_path, final_path)
            
            logger.info(f"Voice generated: {final_path}")
            return str(final_path)
            
        except Exception as e:
            logger.error(f"Voice generation failed: {e}")
            return f"Error: {e}"
    
    def redact_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Redact API key from logs (though it's not in params, just being safe)."""
        # Text content could be sensitive, truncate for logging
        redacted = params.copy()
        if "text" in redacted and len(redacted["text"]) > 100:
            redacted["text"] = redacted["text"][:100] + "..."
        return redacted
