"""Image encoding utilities for LLM API calls.

The implementation now lives in :mod:`loom.media`; these names are re-exported
for backward compatibility with existing imports.
"""

from loom.media import (
    MAX_IMAGE_BYTES,
    encode_image_file,
    encode_image_for_llm,
)

__all__ = ["MAX_IMAGE_BYTES", "encode_image_file", "encode_image_for_llm"]
