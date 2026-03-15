"""Tests for the ViewImageTool."""

import base64
from unittest.mock import MagicMock

import pytest

from pocketfox.agent.entries import ImageEntry
from pocketfox.agent.tools.view_image import MAX_IMAGE_SIZE, SUPPORTED_TYPES, ViewImageTool

# Minimal valid 1x1 pixel images for testing (with correct magic bytes)
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
TINY_GIF = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
)
TINY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2Q=="
)
TINY_WEBP = base64.b64decode(
    "UklGRiQAAABXRUJQVlA4IBgAAAAwAQCdASoBAAEAAUAlpAADcAD++5QAAA=="
)


class TestViewImageToolInit:
    """Tests for ViewImageTool initialization."""

    def test_init_default(self):
        """Test initialization with defaults."""
        tool = ViewImageTool()
        assert tool._allowed_dir is None

    def test_init_with_allowed_dir(self, tmp_path):
        """Test initialization with allowed directory."""
        tool = ViewImageTool(allowed_dir=tmp_path)
        assert tool._allowed_dir == tmp_path


class TestViewImageToolSchema:
    """Tests for ViewImageTool schema and metadata."""

    def test_name(self):
        tool = ViewImageTool()
        assert tool.name == "view_image"

    def test_description(self):
        tool = ViewImageTool()
        assert "image" in tool.description.lower()

    def test_required_params(self):
        tool = ViewImageTool()
        assert "path" in tool.parameters["required"]

    def test_optional_question_param(self):
        tool = ViewImageTool()
        assert "question" in tool.parameters["properties"]
        assert "question" not in tool.parameters["required"]

    def test_keep_param_in_schema(self):
        tool = ViewImageTool()
        assert "keep" in tool.parameters["properties"]
        assert tool.parameters["properties"]["keep"]["type"] == "boolean"
        assert "keep" not in tool.parameters["required"]


class TestViewImageToolExecute:
    """Tests for ViewImageTool.execute()."""

    @pytest.mark.asyncio
    async def test_view_png(self, tmp_path):
        """Test viewing a PNG image returns multimodal content blocks."""
        img = tmp_path / "test.png"
        img.write_bytes(TINY_PNG)

        tool = ViewImageTool(allowed_dir=tmp_path)
        result = await tool.execute(path=str(img))

        assert isinstance(result, list)
        assert len(result) == 2

        # Image block
        assert result[0]["type"] == "image"
        assert result[0]["source"]["media_type"] == "image/png"
        assert result[0]["source"]["type"] == "base64"
        assert len(result[0]["source"]["data"]) > 0

        # Text block
        assert result[1]["type"] == "text"
        assert "test.png" in result[1]["text"]

    @pytest.mark.asyncio
    async def test_view_gif(self, tmp_path):
        """Test viewing a GIF image."""
        img = tmp_path / "test.gif"
        img.write_bytes(TINY_GIF)

        tool = ViewImageTool(allowed_dir=tmp_path)
        result = await tool.execute(path=str(img))

        assert isinstance(result, list)
        assert result[0]["source"]["media_type"] == "image/gif"

    @pytest.mark.asyncio
    async def test_view_jpeg(self, tmp_path):
        """Test viewing a JPEG image."""
        img = tmp_path / "photo.jpg"
        img.write_bytes(TINY_JPEG)

        tool = ViewImageTool(allowed_dir=tmp_path)
        result = await tool.execute(path=str(img))

        assert isinstance(result, list)
        assert result[0]["source"]["media_type"] == "image/jpeg"

    @pytest.mark.asyncio
    async def test_view_webp(self, tmp_path):
        """Test viewing a WebP image."""
        img = tmp_path / "photo.webp"
        img.write_bytes(TINY_WEBP)

        tool = ViewImageTool(allowed_dir=tmp_path)
        result = await tool.execute(path=str(img))

        assert isinstance(result, list)
        assert result[0]["source"]["media_type"] == "image/webp"

    @pytest.mark.asyncio
    async def test_view_with_question(self, tmp_path):
        """Test that question is included in the text block."""
        img = tmp_path / "test.png"
        img.write_bytes(TINY_PNG)

        tool = ViewImageTool(allowed_dir=tmp_path)
        result = await tool.execute(path=str(img), question="What color is this?")

        text_block = result[1]
        assert "What color is this?" in text_block["text"]

    @pytest.mark.asyncio
    async def test_file_not_found(self, tmp_path):
        """Test error on missing file."""
        tool = ViewImageTool(allowed_dir=tmp_path)
        result = await tool.execute(path=str(tmp_path / "nonexistent.png"))

        assert isinstance(result, str)
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_not_a_file(self, tmp_path):
        """Test error when path is a directory."""
        tool = ViewImageTool(allowed_dir=tmp_path)
        result = await tool.execute(path=str(tmp_path))

        assert isinstance(result, str)
        assert "not a file" in result.lower()

    @pytest.mark.asyncio
    async def test_unsupported_type(self, tmp_path):
        """Test error on unsupported file type."""
        txt = tmp_path / "notes.txt"
        txt.write_text("not an image")

        tool = ViewImageTool(allowed_dir=tmp_path)
        result = await tool.execute(path=str(txt))

        assert isinstance(result, str)
        assert "unsupported" in result.lower()

    @pytest.mark.asyncio
    async def test_unsupported_image_type_svg(self, tmp_path):
        """Test error on SVG (not supported by Claude vision)."""
        svg = tmp_path / "icon.svg"
        svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')

        tool = ViewImageTool(allowed_dir=tmp_path)
        result = await tool.execute(path=str(svg))

        assert isinstance(result, str)
        assert "unsupported" in result.lower()

    @pytest.mark.asyncio
    async def test_file_too_large(self, tmp_path):
        """Test error on oversized file."""
        img = tmp_path / "huge.png"
        # PNG magic header + padding to exceed the size limit
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * MAX_IMAGE_SIZE)

        tool = ViewImageTool(allowed_dir=tmp_path)
        result = await tool.execute(path=str(img))

        assert isinstance(result, str)
        assert "too large" in result.lower()


class TestViewImageToolSecurity:
    """Tests for path validation and security."""

    @pytest.mark.asyncio
    async def test_path_outside_allowed_dir(self, tmp_path):
        """Test that paths outside allowed_dir are rejected."""
        tool = ViewImageTool(allowed_dir=tmp_path / "safe")

        result = await tool.execute(path="/etc/passwd")

        assert isinstance(result, str)
        assert "outside allowed directory" in result.lower()

    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self, tmp_path):
        """Test that path traversal attempts are blocked."""
        safe_dir = tmp_path / "safe"
        safe_dir.mkdir()

        # Put an image outside the safe dir
        outside = tmp_path / "secret.png"
        outside.write_bytes(TINY_PNG)

        tool = ViewImageTool(allowed_dir=safe_dir)
        result = await tool.execute(path=str(safe_dir / ".." / "secret.png"))

        assert isinstance(result, str)
        assert "outside allowed directory" in result.lower()

    @pytest.mark.asyncio
    async def test_sibling_prefix_bypass_blocked(self, tmp_path):
        """Test that sibling-prefix paths are rejected (e.g., safe vs safe_evil)."""
        safe_dir = tmp_path / "safe"
        safe_dir.mkdir()
        sibling = tmp_path / "safe_evil"
        sibling.mkdir()
        outside = sibling / "secret.png"
        outside.write_bytes(TINY_PNG)

        tool = ViewImageTool(allowed_dir=safe_dir)
        result = await tool.execute(path=str(outside))

        assert isinstance(result, str)
        assert "outside allowed directory" in result.lower()

    @pytest.mark.asyncio
    async def test_no_allowed_dir_permits_all(self, tmp_path):
        """Test that without allowed_dir, any path is permitted."""
        img = tmp_path / "anywhere.png"
        img.write_bytes(TINY_PNG)

        tool = ViewImageTool()  # No allowed_dir
        result = await tool.execute(path=str(img))

        assert isinstance(result, list)  # Success

    @pytest.mark.asyncio
    async def test_resolve_path_expands_user(self, tmp_path, monkeypatch):
        """Test that _resolve_path expands ~ to home directory."""
        monkeypatch.setenv("HOME", str(tmp_path))
        tool = ViewImageTool()
        resolved = tool._resolve_path("~/test.png")
        assert resolved.is_absolute()
        assert str(resolved).startswith(str(tmp_path))


class TestViewImageToolContentBlocks:
    """Tests for the structure of returned content blocks."""

    @pytest.mark.asyncio
    async def test_image_block_structure(self, tmp_path):
        """Test that image block has correct Anthropic structure."""
        img = tmp_path / "test.png"
        img.write_bytes(TINY_PNG)

        tool = ViewImageTool(allowed_dir=tmp_path)
        result = await tool.execute(path=str(img))

        image_block = result[0]
        assert image_block["type"] == "image"
        assert "source" in image_block
        assert image_block["source"]["type"] == "base64"
        assert image_block["source"]["media_type"] in SUPPORTED_TYPES
        assert isinstance(image_block["source"]["data"], str)

        # Verify base64 is valid
        decoded = base64.b64decode(image_block["source"]["data"])
        assert decoded == TINY_PNG

    @pytest.mark.asyncio
    async def test_text_block_structure(self, tmp_path):
        """Test that text block has correct structure."""
        img = tmp_path / "photo.png"
        img.write_bytes(TINY_PNG)

        tool = ViewImageTool(allowed_dir=tmp_path)
        result = await tool.execute(path=str(img))

        text_block = result[1]
        assert text_block["type"] == "text"
        assert "photo.png" in text_block["text"]


class TestViewImageToolKeep:
    """Tests for the keep parameter."""

    def _make_mock_context_builder(self):
        """Create a mock ContextBuilder that captures add_entry calls."""
        builder = MagicMock()
        builder._added_entries = []

        def add_entry(section, content, name=None):
            builder._added_entries.append((section, content))
            return f"entry_{len(builder._added_entries)}"

        builder.add_entry = add_entry
        return builder

    @pytest.mark.asyncio
    async def test_keep_false_does_not_add_entry(self, tmp_path):
        """Default keep=False should not add entries to context."""
        img = tmp_path / "test.png"
        img.write_bytes(TINY_PNG)

        builder = self._make_mock_context_builder()
        tool = ViewImageTool(allowed_dir=tmp_path, context_builder=builder)
        await tool.execute(path=str(img), keep=False)

        assert len(builder._added_entries) == 0

    @pytest.mark.asyncio
    async def test_keep_true_adds_image_entry(self, tmp_path):
        """keep=True should add an ImageEntry via add_entry."""
        img = tmp_path / "test.png"
        img.write_bytes(TINY_PNG)

        builder = self._make_mock_context_builder()
        tool = ViewImageTool(allowed_dir=tmp_path, context_builder=builder)
        result = await tool.execute(path=str(img), keep=True)

        assert len(builder._added_entries) == 1
        section, entry = builder._added_entries[0]
        assert section == "topic"
        assert isinstance(entry, ImageEntry)

        # Caption should mention "keeping in context"
        text_block = result[1]
        assert "keeping in context" in text_block["text"]

    @pytest.mark.asyncio
    async def test_keep_true_without_context_builder(self, tmp_path):
        """keep=True without context_builder should work normally."""
        img = tmp_path / "test.png"
        img.write_bytes(TINY_PNG)

        tool = ViewImageTool(allowed_dir=tmp_path)  # No context_builder
        result = await tool.execute(path=str(img), keep=True)

        # Should still succeed, just no entry added
        assert isinstance(result, list)
        assert "keeping in context" not in result[1]["text"]

    @pytest.mark.asyncio
    async def test_keep_true_with_question(self, tmp_path):
        """keep=True should store the question as caption."""
        img = tmp_path / "test.png"
        img.write_bytes(TINY_PNG)

        builder = self._make_mock_context_builder()
        tool = ViewImageTool(allowed_dir=tmp_path, context_builder=builder)
        await tool.execute(path=str(img), question="What is this?", keep=True)

        _, entry = builder._added_entries[0]
        assert entry._caption == "What is this?"
