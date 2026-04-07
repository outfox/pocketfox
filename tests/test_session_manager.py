"""Tests for SessionManager disk persistence (save/load round-trip)."""

from datetime import datetime
from pathlib import Path

import pytest

from pocketfox.session.manager import Session, SessionManager


@pytest.fixture
def manager(tmp_path: Path) -> SessionManager:
    """SessionManager that reads/writes into tmp_path."""
    sm = SessionManager(tmp_path)
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    sm.sessions_dir = sessions_dir
    return sm


def _fresh_manager(sessions_dir: Path) -> SessionManager:
    """New manager with empty cache pointing at the same sessions dir."""
    sm = SessionManager(sessions_dir.parent)
    sm.sessions_dir = sessions_dir
    return sm


# ---------------------------------------------------------------------------
# Basic round-trip
# ---------------------------------------------------------------------------


def test_save_and_load_basic_messages(manager: SessionManager):
    session = Session(key="telegram:123")
    session.add_message("user", "hi")
    session.add_message("assistant", "hello")
    manager.save(session)

    loaded = _fresh_manager(manager.sessions_dir).get_or_create("telegram:123")

    assert len(loaded.messages) == 2
    assert loaded.messages[0]["role"] == "user"
    assert loaded.messages[0]["content"] == "hi"
    assert loaded.messages[1]["role"] == "assistant"
    assert loaded.messages[1]["content"] == "hello"

    # JSONL file should exist
    path = manager._get_session_path("telegram:123")
    assert path.exists()


def test_message_timestamps_preserved(manager: SessionManager):
    session = Session(key="test:ts")
    session.add_message("user", "ping")
    original_ts = session.messages[0]["timestamp"]
    manager.save(session)

    loaded = _fresh_manager(manager.sessions_dir).get_or_create("test:ts")
    assert loaded.messages[0]["timestamp"] == original_ts


def test_metadata_roundtrip(manager: SessionManager):
    session = Session(key="test:meta")
    session.metadata = {"lang": "en", "tier": 2}
    session.add_message("user", "x")
    manager.save(session)

    loaded = _fresh_manager(manager.sessions_dir).get_or_create("test:meta")
    assert loaded.metadata == {"lang": "en", "tier": 2}


def test_created_at_roundtrip(manager: SessionManager):
    fixed = datetime(2025, 1, 15, 12, 0, 0)
    session = Session(key="test:created", created_at=fixed)
    session.add_message("user", "x")
    manager.save(session)

    loaded = _fresh_manager(manager.sessions_dir).get_or_create("test:created")
    assert loaded.created_at == fixed


def test_updated_at_roundtrip(manager: SessionManager):
    fixed = datetime(2025, 6, 1, 9, 30, 0)
    session = Session(key="test:updated", updated_at=fixed)
    session.add_message("user", "x")
    # Manually reset updated_at after add_message (which bumps it)
    session.updated_at = fixed
    manager.save(session)

    loaded = _fresh_manager(manager.sessions_dir).get_or_create("test:updated")
    assert loaded.updated_at == fixed


# ---------------------------------------------------------------------------
# Media attachments
# ---------------------------------------------------------------------------


def test_media_attachment_roundtrip(manager: SessionManager):
    session = Session(key="test:media")
    session.add_message("user", "look at this", media=["/path/to/image.png", "/path/to/doc.pdf"])
    manager.save(session)

    loaded = _fresh_manager(manager.sessions_dir).get_or_create("test:media")
    assert loaded.messages[0]["media"] == ["/path/to/image.png", "/path/to/doc.pdf"]

    # get_history should also surface media
    history = loaded.get_history()
    assert history[0]["media"] == ["/path/to/image.png", "/path/to/doc.pdf"]


# ---------------------------------------------------------------------------
# Cache vs disk behaviour
# ---------------------------------------------------------------------------


def test_get_or_create_cache_vs_disk(manager: SessionManager):
    session = Session(key="test:cache")
    session.add_message("user", "cached")
    manager.save(session)

    # Same manager returns cached instance
    cached = manager.get_or_create("test:cache")
    assert cached is session

    # Fresh manager loads from disk
    fresh = _fresh_manager(manager.sessions_dir)
    loaded = fresh.get_or_create("test:cache")
    assert loaded is not session
    assert loaded.messages[0]["content"] == "cached"

    # Missing key creates new empty session
    new = fresh.get_or_create("test:nonexistent")
    assert new.messages == []


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def test_delete_removes_file_and_cache(manager: SessionManager):
    session = Session(key="test:del")
    session.add_message("user", "bye")
    manager.save(session)

    path = manager._get_session_path("test:del")
    assert path.exists()
    assert "test:del" in manager._cache

    assert manager.delete("test:del") is True
    assert not path.exists()
    assert "test:del" not in manager._cache

    # Second delete returns False
    assert manager.delete("test:del") is False


# ---------------------------------------------------------------------------
# List sessions
# ---------------------------------------------------------------------------


def test_list_sessions(manager: SessionManager):
    for i, key in enumerate(["test:a", "test:b", "test:c"]):
        s = Session(key=key, updated_at=datetime(2025, 1, 1 + i))
        s.add_message("user", f"msg-{key}")
        s.updated_at = datetime(2025, 1, 1 + i)  # reset after add_message bumps it
        manager.save(s)

    listing = manager.list_sessions()
    assert len(listing) == 3
    # Sorted by updated_at descending
    assert listing[0]["updated_at"] >= listing[1]["updated_at"]
    assert listing[1]["updated_at"] >= listing[2]["updated_at"]
    for entry in listing:
        assert "key" in entry
        assert "created_at" in entry
        assert "updated_at" in entry
        assert "path" in entry


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_corrupted_jsonl_returns_none(manager: SessionManager):
    path = manager._get_session_path("bad:key")
    path.write_text("not valid json {{{{\n")

    result = manager._load("bad:key")
    assert result is None


# ---------------------------------------------------------------------------
# Edge case
# ---------------------------------------------------------------------------


def test_empty_session_roundtrip(manager: SessionManager):
    session = Session(key="test:empty", created_at=datetime(2025, 3, 1))
    manager.save(session)

    loaded = _fresh_manager(manager.sessions_dir).get_or_create("test:empty")
    assert loaded.messages == []
    assert loaded.metadata == {}
    assert loaded.created_at == datetime(2025, 3, 1)
