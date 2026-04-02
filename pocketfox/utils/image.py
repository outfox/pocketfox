"""Image encoding utilities for LLM API calls."""

import base64
import io
from pathlib import Path

from loguru import logger

# Anthropic's per-image limit (base64-decoded bytes)
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB


def encode_image_for_llm(
    raw: bytes,
    mime: str,
) -> tuple[str, str, bool] | None:
    """Encode image bytes for an LLM API call, re-encoding if too large.

    Args:
        raw: Raw image file bytes.
        mime: Original MIME type (e.g. "image/png").

    Returns:
        (base64_data, mime_type, was_reencoded) on success, or None if the
        image still exceeds the size limit after re-encoding.
    """
    if len(raw) <= MAX_IMAGE_BYTES:
        return base64.b64encode(raw).decode("ascii"), mime, False

    # Re-encode as JPEG at 95% quality
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(raw))
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        reencoded = buf.getvalue()
    except Exception as exc:
        logger.warning(f"Image re-encode failed: {exc}")
        return None

    if len(reencoded) > MAX_IMAGE_BYTES:
        logger.warning(
            f"Image still too large after re-encoding "
            f"({len(reencoded) / 1024 / 1024:.1f} MB > 5 MB)"
        )
        return None

    logger.info(
        f"Re-encoded image from {len(raw) / 1024 / 1024:.1f} MB to "
        f"{len(reencoded) / 1024 / 1024:.1f} MB JPEG"
    )
    return base64.b64encode(reencoded).decode("ascii"), "image/jpeg", True


def encode_image_file(path: Path) -> tuple[str, str, str, bool] | None:
    """Convenience: read a file and encode it for the LLM.

    Args:
        path: Path to the image file.

    Returns:
        (data_uri, base64_data, mime_type, was_reencoded) on success,
        or None if the image exceeds size limits.
    """
    import mimetypes as mt

    mime, _ = mt.guess_type(str(path))
    if not mime:
        # Fallback for platforms where mimetypes misses .webp etc.
        _fallback = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                     ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}
        mime = _fallback.get(path.suffix.lower())
    if not mime or not mime.startswith("image/"):
        return None
    raw = path.read_bytes()
    result = encode_image_for_llm(raw, mime)
    if result is None:
        return None
    b64, final_mime, reencoded = result
    data_uri = f"data:{final_mime};base64,{b64}"
    return data_uri, b64, final_mime, reencoded
