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


class KeptFileEntry(Entry):
    """A text file entry kept in context across turns.

    Re-reads the file from disk on every compile() so edits are reflected
    immediately (the resulting cache miss is intentional).  If the file is
    deleted, compile() returns a short notice instead of failing.
    """

    def __init__(
        self,
        path: Path,
        name: str | None = None,
    ):
        super().__init__(name or f"Kept: {path.name}")
        self._path = path
        self._resolved = str(path.resolve())

    def compile(self) -> str:
        try:
            content = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return f"[Kept file removed: {self._path.name}]"
        except OSError as e:
            return f"[Kept file unreadable: {self._path.name} ({e})]"
        return f"# {self._path.name}\n\n{content}"

    def identity(self) -> str:
        return f"file:{self._resolved}"

    def __repr__(self) -> str:
        return f"KeptFileEntry(path={self._path!r})"


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
        self._resolved = str(path.resolve())
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
        """Return OpenAI-format multimodal content blocks.

        Uses ``image_url`` with a data-URI so litellm can translate to
        the provider's native format (Anthropic, OpenAI, etc.).
        """
        data_uri = f"data:{self._mime_type};base64,{self._base64_data}"
        return [
            {
                "type": "image_url",
                "image_url": {"url": data_uri},
            },
            {"type": "text", "text": self.compile()},
        ]

    def identity(self) -> str:
        """Resolved path for deduplication."""
        return f"image:{self._resolved}"

    def __repr__(self) -> str:
        return f"ImageEntry(path={self._path!r})"
