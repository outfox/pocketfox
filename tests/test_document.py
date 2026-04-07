"""Tests for document text extraction utilities."""

import json
import zipfile
from pathlib import Path

from pocketfox.utils.document import (
    DOCUMENT_SUFFIXES,
    encode_document_block,
    encode_pdf_block,
    extract_document_text,
)

# ---------------------------------------------------------------------------
# Plain text
# ---------------------------------------------------------------------------


def test_extract_txt(tmp_path: Path):
    f = tmp_path / "hello.txt"
    f.write_text("Hello, world!", encoding="utf-8")
    result = extract_document_text(f)
    assert result == "Hello, world!"


def test_extract_txt_latin1_fallback(tmp_path: Path):
    f = tmp_path / "latin.txt"
    f.write_bytes("caf\xe9".encode("latin-1"))
    result = extract_document_text(f)
    assert result is not None
    assert "caf" in result


def test_extract_txt_empty(tmp_path: Path):
    f = tmp_path / "empty.txt"
    f.write_text("   \n  \n  ")
    assert extract_document_text(f) is None


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


def test_extract_csv(tmp_path: Path):
    f = tmp_path / "data.csv"
    f.write_text("name,age\nAlice,30\nBob,25\n")
    result = extract_document_text(f)
    assert result is not None
    assert "Alice" in result
    assert "Bob" in result


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def test_extract_json(tmp_path: Path):
    f = tmp_path / "data.json"
    data = {"key": "value", "nested": [1, 2, 3]}
    f.write_text(json.dumps(data))
    result = extract_document_text(f)
    assert result is not None
    parsed = json.loads(result)
    assert parsed == data


def test_extract_json_invalid(tmp_path: Path):
    f = tmp_path / "bad.json"
    f.write_text("{not valid json")
    assert extract_document_text(f) is None


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------


def test_extract_html(tmp_path: Path):
    f = tmp_path / "page.html"
    f.write_text("<html><body><p>Hello from HTML</p></body></html>")
    result = extract_document_text(f)
    assert result is not None
    assert "Hello from HTML" in result


# ---------------------------------------------------------------------------
# ODT (stdlib zipfile + xml)
# ---------------------------------------------------------------------------


def _make_odt(path: Path, text: str) -> None:
    """Create a minimal ODT file with the given text."""
    content_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
        ' xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
        "<office:body><office:text>"
        f"<text:p>{text}</text:p>"
        "</office:text></office:body>"
        "</office:document-content>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("content.xml", content_xml)


def test_extract_odt(tmp_path: Path):
    f = tmp_path / "doc.odt"
    _make_odt(f, "Hello from ODT")
    result = extract_document_text(f)
    assert result is not None
    assert "Hello from ODT" in result


# ---------------------------------------------------------------------------
# EPUB (stdlib zipfile + html.parser)
# ---------------------------------------------------------------------------


def _make_epub(path: Path, text: str) -> None:
    """Create a minimal EPUB file with one chapter."""
    chapter = f"<html><body><p>{text}</p></body></html>"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("OEBPS/chapter1.xhtml", chapter)


def test_extract_epub(tmp_path: Path):
    f = tmp_path / "book.epub"
    _make_epub(f, "Chapter one content")
    result = extract_document_text(f)
    assert result is not None
    assert "Chapter one content" in result


# ---------------------------------------------------------------------------
# Size limit
# ---------------------------------------------------------------------------


def test_file_too_large(tmp_path: Path):
    f = tmp_path / "huge.txt"
    f.write_text("x" * 1000)
    # Set max_bytes to 500
    assert extract_document_text(f, max_bytes=500) is None


def test_file_within_limit(tmp_path: Path):
    f = tmp_path / "small.txt"
    f.write_text("small content")
    result = extract_document_text(f, max_bytes=1024 * 1024)
    assert result == "small content"


# ---------------------------------------------------------------------------
# Text truncation
# ---------------------------------------------------------------------------


def test_text_truncated_at_limit(tmp_path: Path):
    f = tmp_path / "long.txt"
    # Write more than _MAX_TEXT_CHARS (200,000)
    f.write_text("A" * 250_000)
    result = extract_document_text(f, max_bytes=300_000)
    assert result is not None
    assert len(result) < 250_000
    assert "[truncated" in result


# ---------------------------------------------------------------------------
# Native document content blocks (MIME-annotated base64)
# ---------------------------------------------------------------------------


def test_encode_document_block_pdf(tmp_path: Path):
    f = tmp_path / "test.pdf"
    f.write_bytes(b"%PDF-1.4 fake content")
    block = encode_document_block(f)
    assert block is not None
    assert block["type"] == "document"
    assert block["source"]["type"] == "base64"
    assert block["source"]["media_type"] == "application/pdf"


def test_encode_document_block_txt(tmp_path: Path):
    f = tmp_path / "readme.txt"
    f.write_text("hello")
    block = encode_document_block(f)
    assert block is not None
    assert block["source"]["media_type"] == "text/plain"


def test_encode_document_block_csv(tmp_path: Path):
    f = tmp_path / "data.csv"
    f.write_text("a,b\n1,2\n")
    block = encode_document_block(f)
    assert block is not None
    assert block["source"]["media_type"] == "text/csv"


def test_encode_document_block_html(tmp_path: Path):
    f = tmp_path / "page.html"
    f.write_text("<html><body>hi</body></html>")
    block = encode_document_block(f)
    assert block is not None
    assert block["source"]["media_type"] == "text/html"


def test_encode_document_block_docx(tmp_path: Path):
    f = tmp_path / "doc.docx"
    f.write_bytes(b"PK\x03\x04 fake docx")
    block = encode_document_block(f)
    assert block is not None
    assert "wordprocessingml" in block["source"]["media_type"]


def test_encode_document_block_xlsx(tmp_path: Path):
    f = tmp_path / "sheet.xlsx"
    f.write_bytes(b"PK\x03\x04 fake xlsx")
    block = encode_document_block(f)
    assert block is not None
    assert "spreadsheetml" in block["source"]["media_type"]


def test_encode_document_block_json(tmp_path: Path):
    f = tmp_path / "data.json"
    f.write_text('{"key": "value"}')
    block = encode_document_block(f)
    assert block is not None
    assert block["source"]["media_type"] == "application/json"


def test_encode_document_block_too_large(tmp_path: Path):
    f = tmp_path / "big.pdf"
    f.write_bytes(b"x" * 1000)
    assert encode_document_block(f, max_bytes=500) is None


def test_encode_document_block_missing_file(tmp_path: Path):
    f = tmp_path / "missing.pdf"
    assert encode_document_block(f) is None


def test_encode_document_block_unsupported_ext(tmp_path: Path):
    f = tmp_path / "data.xyz"
    f.write_text("unknown")
    assert encode_document_block(f) is None


def test_encode_pdf_block_backward_compat(tmp_path: Path):
    """encode_pdf_block is an alias for encode_document_block."""
    f = tmp_path / "test.pdf"
    f.write_bytes(b"%PDF-1.4 fake")
    assert encode_pdf_block(f) is not None


# ---------------------------------------------------------------------------
# Unsupported / missing file
# ---------------------------------------------------------------------------


def test_unsupported_extension(tmp_path: Path):
    f = tmp_path / "data.xyz"
    f.write_text("unknown format")
    assert extract_document_text(f) is None


def test_missing_file(tmp_path: Path):
    f = tmp_path / "nope.txt"
    assert extract_document_text(f) is None


# ---------------------------------------------------------------------------
# DOCUMENT_SUFFIXES constant
# ---------------------------------------------------------------------------


def test_document_suffixes_completeness():
    expected = {".pdf", ".docx", ".csv", ".txt", ".html", ".htm", ".odt", ".rtf", ".epub", ".json", ".xlsx"}
    assert DOCUMENT_SUFFIXES == expected
