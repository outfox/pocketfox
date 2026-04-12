"""View image tool: load images from the filesystem into the LLM context."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pocketfox.agent.tools.base import Tool
from pocketfox.utils.image import MAX_IMAGE_BYTES, encode_image_for_llm

if TYPE_CHECKING:
    from pocketfox.agent.context import ContextBuilder

# Supported MIME types for Claude vision
SUPPORTED_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}

# Fallback for platforms where mimetypes doesn't know .webp etc.
_EXT_TO_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


class ViewImageTool(Tool):
    """
    Tool to view images from the filesystem using the LLM's native vision.

    Reads an image file, base64-encodes it, and returns it as a multimodal
    tool result that Claude can see directly — no external API needed.
    """

    def __init__(
        self,
        allowed_dir: Path | None = None,
        context_builder: ContextBuilder | None = None,
    ):
        self._allowed_dir = allowed_dir
        self._context_builder = context_builder

    @property
    def name(self) -> str:
        return "fs_view_image"

    @property
    def description(self) -> str:
        return (
            "View an image file from the filesystem. The image will be loaded "
            "into your visual context so you can see and describe it. "
            "Supports JPEG, PNG, GIF, and WebP."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the image file",
                },
                "question": {
                    "type": "string",
                    "description": "Optional question or focus for viewing the image",
                },
                "keep": {
                    "type": "boolean",
                    "description": (
                        "If true, persist the image in the system context so it remains "
                        "visible across subsequent turns. Default: false."
                    ),
                },
            },
            "required": ["path"],
        }

    async def execute(
        self,
        path: str,
        question: str | None = None,
        keep: bool = False,
        **kwargs: Any,
    ) -> str | list[dict[str, Any]]:
        """
        Read an image and return it as a multimodal content block.

        Returns a list of content blocks (image + text) that the LLM provider
        will include in the tool result, allowing the model to see the image
        with its native vision capabilities.
        """
        try:
            file_path = self._resolve_path(path)

            if not file_path.exists():
                return f"Error: File not found: {path}"
            if not file_path.is_file():
                return f"Error: Not a file: {path}"

            # Check MIME type (mimetypes may miss .webp on some platforms)
            mime_type, _ = mimetypes.guess_type(str(file_path))
            if not mime_type:
                mime_type = _EXT_TO_MIME.get(file_path.suffix.lower())
            if not mime_type or mime_type not in SUPPORTED_TYPES:
                supported = ", ".join(sorted(SUPPORTED_TYPES))
                return f"Error: Unsupported image type '{mime_type}'. Supported: {supported}"

            # Read and encode (re-encodes as JPEG if >5 MB)
            raw = file_path.read_bytes()
            result = encode_image_for_llm(raw, mime_type)
            if result is None:
                actual_mb = len(raw) / (1024 * 1024)
                max_mb = MAX_IMAGE_BYTES / (1024 * 1024)
                return (
                    f"Error: Image too large ({actual_mb:.1f} MB). "
                    f"Maximum: {max_mb:.0f} MB (even after re-encoding)."
                )

            image_data, final_mime, reencoded = result
            data_uri = f"data:{final_mime};base64,{image_data}"
            content: list[dict[str, Any]] = [
                {
                    "type": "image_url",
                    "image_url": {"url": data_uri},
                },
            ]

            # Add context text
            caption = f"Image: {file_path.name}"
            if reencoded:
                caption += " (re-encoded to jpeg)"
            if question:
                caption += f"\nQuestion: {question}"

            # Persist image in context if requested
            if keep and self._context_builder:
                from pocketfox.agent.entries import ImageEntry

                entry = ImageEntry(
                    path=file_path,
                    base64_data=image_data,
                    mime_type=final_mime,
                    caption=question,
                )
                self._context_builder.add_entry("topic", entry)
                caption += "\n(keeping in context)"

            content.append({"type": "text", "text": caption})

            return content

        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error viewing image: {e}"

    def _resolve_path(self, path: str) -> Path:
        """Resolve and validate the file path."""
        resolved = Path(path).expanduser().resolve()
        if self._allowed_dir:
            allowed = self._allowed_dir.resolve()
            if not resolved.is_relative_to(allowed):
                raise PermissionError(
                    f"Path {path} is outside allowed directory {self._allowed_dir}"
                )
        return resolved
