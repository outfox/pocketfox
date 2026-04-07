"""Tests for LOOM-based context building."""

import tempfile
from pathlib import Path

import pytest

from pocketfox.agent.context import ContextBuilder


@pytest.fixture
def workspace():
    """Create a temporary workspace with bootstrap files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir)
        
        # Create bootstrap files
        (ws / "AGENTS.md").write_text("# AGENTS.md\nYou are an agent.")
        (ws / "SOUL.md").write_text("# SOUL.md\nBe helpful.")
        (ws / "USER.md").write_text("# USER.md\nUser: Test User")
        
        # Create memory directory
        (ws / "memory").mkdir()
        (ws / "memory" / "MEMORY.md").write_text("# Long-term memory\nRemember this.")
        
        yield ws


def test_build_context_returns_loom_context(workspace):
    """ContextBuilder.build_context() returns a LOOM Context."""
    from loom import Context
    
    builder = ContextBuilder(workspace)
    ctx = builder.build_context()
    
    assert isinstance(ctx, Context)
    assert ctx.id is not None
    assert len(ctx.id) == 3  # Context IDs are 3 chars


def test_build_context_has_sections(workspace):
    """Context has populated sections."""
    builder = ContextBuilder(workspace)
    ctx = builder.build_context(channel="test", chat_id="123")
    
    # Foundation has identity + bootstrap files + memory
    assert len(ctx.foundation.entries) > 0
    
    # Topic has session info after build_context with channel/chat_id
    session_names = [e.name for e in ctx.topic.entries]
    assert "Current Session" in session_names
    
    # Attention has datetime
    assert len(ctx.attention.entries) > 0


def test_build_context_render(workspace):
    """Context renders to string."""
    builder = ContextBuilder(
        workspace,
        default_context_files=["AGENTS.md", "SOUL.md", "USER.md", "MEMORY.md"],
    )
    ctx = builder.build_context(
        channel="telegram", chat_id="42",
        context_files=("AGENTS.md", "SOUL.md", "USER.md", "MEMORY.md"),
    )

    rendered = ctx.render()

    assert "pocketfox" in rendered
    assert "AGENTS.md" in rendered
    assert "SOUL.md" in rendered
    assert "Channel: telegram" in rendered
    assert "Chat ID: 42" in rendered


def test_build_system_prompt_uses_loom(workspace):
    """build_system_prompt() uses LOOM under the hood."""
    builder = ContextBuilder(workspace)
    
    prompt = builder.build_system_prompt(channel="cli", chat_id="direct")
    
    assert "pocketfox" in prompt
    assert "Channel: cli" in prompt


def test_build_messages_uses_loom(workspace):
    """build_messages() uses LOOM context."""
    builder = ContextBuilder(workspace)
    
    messages = builder.build_messages(
        history=[],
        current_message="Hello!",
        channel="test",
        chat_id="456",
    )
    
    assert len(messages) >= 2  # system + user
    assert messages[0]["role"] == "system"
    assert messages[-1]["role"] == "user"
    # User message is block format: user text + volatile attention (datetime)
    content = messages[-1]["content"]
    assert isinstance(content, list)
    texts = [b["text"] for b in content if b.get("type") == "text"]
    assert any("Hello!" in t for t in texts)
    assert any("Current Time" in t for t in texts)


def test_context_is_persistent(workspace):
    """Context is persistent across calls."""
    builder = ContextBuilder(workspace)
    
    ctx1 = builder.build_context()
    ctx2 = builder.build_context()
    
    # Same context instance
    assert ctx1 is ctx2
    assert ctx1.id == ctx2.id


def test_to_messages_format(workspace):
    """Context.to_messages() returns OpenAI-compatible format."""
    builder = ContextBuilder(workspace)
    ctx = builder.build_context()
    
    messages = ctx.to_messages()
    
    assert isinstance(messages, list)
    assert len(messages) == 1
    assert messages[0]["role"] == "system"
    assert isinstance(messages[0]["content"], list)


def test_add_entry(workspace):
    """add_entry() adds an entry to the specified section."""
    builder = ContextBuilder(workspace)
    
    entry_id = builder.add_entry("topic", "Test content", name="Test Entry")
    
    assert isinstance(entry_id, str) and len(entry_id) > 0
    
    # Verify entry is in context
    entries = builder.list_entries("topic")
    names = [e["name"] for e in entries]
    assert "Test Entry" in names


def test_remove_entry(workspace):
    """remove_entry() removes an entry by ID."""
    builder = ContextBuilder(workspace)
    
    entry_id = builder.add_entry("topic", "To be removed", name="Temporary")
    
    # Verify it's there
    entries_before = builder.list_entries("topic")
    ids_before = [e["id"] for e in entries_before]
    assert entry_id in ids_before
    
    # Remove it
    result = builder.remove_entry(entry_id)
    assert result is True
    
    # Verify it's gone
    entries_after = builder.list_entries("topic")
    ids_after = [e["id"] for e in entries_after]
    assert entry_id not in ids_after


def test_remove_entry_not_found(workspace):
    """remove_entry() returns False for non-existent ID."""
    builder = ContextBuilder(workspace)
    
    result = builder.remove_entry("nonexistent_id")
    assert result is False


def test_list_entries(workspace):
    """list_entries() returns entry info."""
    builder = ContextBuilder(workspace)
    
    builder.add_entry("step", "First entry", name="Entry 1")
    builder.add_entry("step", "Second entry", name="Entry 2")
    
    entries = builder.list_entries("step")
    
    assert len(entries) == 2
    assert all("id" in e for e in entries)
    assert all("name" in e for e in entries)
    assert all("preview" in e for e in entries)


def test_add_entry_invalid_section(workspace):
    """add_entry() raises ValueError for invalid section."""
    builder = ContextBuilder(workspace)
    
    with pytest.raises(ValueError, match="Invalid section"):
        builder.add_entry("invalid_section", "content")


def test_list_entries_invalid_section(workspace):
    """list_entries() raises ValueError for invalid section."""
    builder = ContextBuilder(workspace)
    
    with pytest.raises(ValueError, match="Invalid section"):
        builder.list_entries("invalid_section")


def test_entry_persists_across_build_context_calls(workspace):
    """Entries added via add_entry() persist across build_context() calls."""
    builder = ContextBuilder(workspace)
    
    # Add entry
    entry_id = builder.add_entry("topic", "Persistent content", name="Persistent")
    
    # Build context multiple times
    ctx1 = builder.build_context(channel="a", chat_id="1")
    ctx2 = builder.build_context(channel="b", chat_id="2")
    
    # Entry should still be there
    entries = builder.list_entries("topic")
    ids = [e["id"] for e in entries]
    assert entry_id in ids
    
    # And rendered in both contexts
    rendered = ctx2.render()
    assert "Persistent content" in rendered


def test_prologue_appears_in_system_prompt(workspace):
    """A context prologue is included in the rendered system prompt."""
    builder = ContextBuilder(workspace)
    prologue_text = "This is a telegram group chat with close friends of the user."

    prompt = builder.build_system_prompt(
        context_name="friends",
        context_files=("AGENTS.md",),
        prologue=prologue_text,
    )

    assert prologue_text in prompt


def test_prologue_appears_in_build_messages(workspace):
    """Prologue is included when building LLM messages."""
    builder = ContextBuilder(workspace)
    prologue_text = "This context is a direct conversation with your super-user."

    messages = builder.build_messages(
        history=[],
        current_message="hi",
        context_name="superuser",
        context_files=("AGENTS.md",),
        prologue=prologue_text,
    )

    system_content = messages[0]["content"]
    # system content can be string or list of blocks
    if isinstance(system_content, list):
        full_text = " ".join(
            b.get("text", "") for b in system_content if isinstance(b, dict)
        )
    else:
        full_text = system_content
    assert prologue_text in full_text


def test_no_prologue_by_default(workspace):
    """Without prologue, no 'Context Prologue' entry appears."""
    builder = ContextBuilder(workspace)

    ctx = builder.build_context(context_name="plain", context_files=("AGENTS.md",))

    names = [e.name for e in ctx.foundation.entries]
    assert "Context Prologue" not in names
