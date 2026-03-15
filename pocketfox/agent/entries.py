"""Custom LOOM Entry subclasses for pocketfox."""

from datetime import datetime
from pathlib import Path
from typing import Any

from loom import Entry


class DateTimeEntry(Entry):
    """
    A volatile entry that renders the current date/time.

    Always returns a unique identity to prevent caching — this entry
    should be placed after all cache breakpoints to avoid invalidating
    the cached prefix.
    """

    def __init__(self, fmt: str = "%Y-%m-%d %H:%M (%A)", name: str | None = None):
        """
        Args:
            fmt: strftime format string.
            name: Entry name (default: "Current Time").
        """
        super().__init__(name or "Current Time")
        self._fmt = fmt

    def compile(self) -> str:
        """Compile to current timestamp."""
        return datetime.now().strftime(self._fmt)

    def identity(self) -> str:
        """Always unique — volatile entry, never deduplicated."""
        return f"datetime:{id(self)}"

    def __repr__(self) -> str:
        return f"DateTimeEntry(fmt={self._fmt!r})"


class ImageEntry(Entry):
    """An entry backed by an image, supporting multimodal compilation."""

    def __init__(
        self,
        path: Path,
        base64_data: str,
        mime_type: str,
        caption: str | None = None,
        name: str | None = None,
    ):
        """
        Args:
            path: Original file path of the image.
            base64_data: Pre-encoded base64 image data.
            mime_type: MIME type (e.g. "image/png").
            caption: Optional caption text.
            name: Entry name.
        """
        super().__init__(name or f"Image: {path.name}")
        self._path = path
        self._base64_data = base64_data
        self._mime_type = mime_type
        self._caption = caption

    def compile(self) -> str:
        """Text fallback for loom's text-only sections."""
        text = f"[Kept image: {self._path.name}]"
        if self._caption:
            text += f" ({self._caption})"
        return text

    def compile_blocks(self) -> list[dict[str, Any]]:
        """Return Anthropic multimodal content blocks."""
        blocks: list[dict[str, Any]] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": self._mime_type,
                    "data": self._base64_data,
                },
            },
        ]
        caption = f"[Kept image: {self._path.name}]"
        if self._caption:
            caption += f" ({self._caption})"
        blocks.append({"type": "text", "text": caption})
        return blocks

    def identity(self) -> str:
        """Resolved path for deduplication."""
        return f"image:{self._path.resolve()}"

    def __repr__(self) -> str:
        return f"ImageEntry(path={self._path!r})"
