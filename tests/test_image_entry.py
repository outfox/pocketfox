"""Tests for ImageEntry."""

from pocketfox.agent.entries import ImageEntry

FAKE_B64 = "iVBORw0KGgoAAAANSUhEUg=="


class TestImageEntryCompile:
    """Tests for ImageEntry.compile() text fallback."""

    def test_compile_basic(self, tmp_path):
        entry = ImageEntry(
            path=tmp_path / "photo.png",
            base64_data=FAKE_B64,
            mime_type="image/png",
        )
        assert entry.compile() == "[Kept image: photo.png]"

    def test_compile_with_caption(self, tmp_path):
        entry = ImageEntry(
            path=tmp_path / "photo.png",
            base64_data=FAKE_B64,
            mime_type="image/png",
            caption="A sunset",
        )
        assert entry.compile() == "[Kept image: photo.png] (A sunset)"


class TestImageEntryCompileBlocks:
    """Tests for ImageEntry.compile_blocks() multimodal output."""

    def test_blocks_structure(self, tmp_path):
        entry = ImageEntry(
            path=tmp_path / "img.png",
            base64_data=FAKE_B64,
            mime_type="image/png",
        )
        blocks = entry.compile_blocks()
        assert len(blocks) == 2

        # Image block (OpenAI image_url format with data URI)
        assert blocks[0]["type"] == "image_url"
        url = blocks[0]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")
        assert url.endswith(FAKE_B64)

        # Text block
        assert blocks[1]["type"] == "text"
        assert "img.png" in blocks[1]["text"]

    def test_blocks_with_caption(self, tmp_path):
        entry = ImageEntry(
            path=tmp_path / "img.png",
            base64_data=FAKE_B64,
            mime_type="image/png",
            caption="Describe this",
        )
        blocks = entry.compile_blocks()
        assert "Describe this" in blocks[1]["text"]


class TestImageEntryIdentity:
    """Tests for ImageEntry.identity() and deduplication."""

    def test_identity_uses_resolved_path(self, tmp_path):
        p = tmp_path / "photo.png"
        entry = ImageEntry(path=p, base64_data=FAKE_B64, mime_type="image/png")
        assert entry.identity() == f"image:{p.resolve()}"

    def test_same_path_entries_are_equal(self, tmp_path):
        p = tmp_path / "photo.png"
        e1 = ImageEntry(path=p, base64_data=FAKE_B64, mime_type="image/png")
        e2 = ImageEntry(path=p, base64_data=FAKE_B64, mime_type="image/png", caption="x")
        assert e1 == e2
        assert hash(e1) == hash(e2)

    def test_different_path_entries_differ(self, tmp_path):
        e1 = ImageEntry(
            path=tmp_path / "a.png", base64_data=FAKE_B64, mime_type="image/png"
        )
        e2 = ImageEntry(
            path=tmp_path / "b.png", base64_data=FAKE_B64, mime_type="image/png"
        )
        assert e1 != e2
