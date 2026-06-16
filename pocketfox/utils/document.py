"""Document text extraction utilities for LLM context.

The implementation now lives in :mod:`loom.media`; these names are re-exported
for backward compatibility with existing imports.
"""

from loom.media import (
    DOCUMENT_SUFFIXES,
    IMAGE_SUFFIXES,
    encode_document_block,
    encode_pdf_block,
    extract_document_text,
)

__all__ = [
    "DOCUMENT_SUFFIXES",
    "IMAGE_SUFFIXES",
    "encode_document_block",
    "encode_pdf_block",
    "extract_document_text",
]
