"""Golden-snapshot characterization of ContextBuilder.build_messages output.

This freezes the *current* (pre-refactor) wire shape that pocketfox sends to
litellm, so the loom-owned ``openai`` serializer can be proven a drop-in
replacement.  Output is normalized for volatile bits (workspace path, runtime
line, datetime) and compared byte-for-byte against a saved fixture.

On first run (fixture missing) the snapshot is captured; commit the fixture and
subsequent runs assert equality.  Set ``POCKETFOX_REGOLD=1`` to recapture.
"""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

from pocketfox.agent.context import ContextBuilder

GOLDEN = Path(__file__).parent / "golden" / "build_messages.json"

# A 1x1 transparent PNG (fixed bytes -> deterministic base64).
_PNG_1x1 = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def _make_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "AGENTS.md").write_text("# AGENTS\nYou are an agent.", encoding="utf-8")
    (ws / "TOOLS.md").write_text("# TOOLS\nUse tools wisely.", encoding="utf-8")
    (ws / "memory").mkdir()
    (ws / "memory" / "MEMORY.md").write_text("# Memory\nRemember stuff.", encoding="utf-8")
    return ws


def _normalize(messages: list[dict[str, Any]], ws: Path) -> list[dict[str, Any]]:
    """Strip volatile pieces so the snapshot is reproducible across runs/machines."""
    raw = json.dumps(messages, ensure_ascii=False, sort_keys=True)
    raw = raw.replace(str(ws.expanduser().resolve()).replace("\\", "\\\\"), "<WS>")
    raw = raw.replace(str(ws.expanduser().resolve()), "<WS>")
    # Runtime line: "macOS arm64, Python 3.12.1" style
    raw = re.sub(r"(macOS|Linux|Windows|Darwin)[^\\\"]*?Python \d+\.\d+\.\d+", "<RUNTIME>", raw)
    # DateTimeEntry: "2026-06-16 14:33 (Tuesday)"
    raw = re.sub(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} \(\w+\)", "<DT>", raw)
    return json.loads(raw)


def _cases(ws: Path) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}

    files = ("AGENTS.md", "TOOLS.md", "MEMORY.md")

    b = ContextBuilder(ws, default_context_files=list(files))
    out["text_only"] = b.build_messages(
        history=[],
        current_message="Hello!",
        channel="cli",
        chat_id="1",
        context_files=files,
    )

    b = ContextBuilder(ws, default_context_files=list(files))
    out["history_cache"] = b.build_messages(
        history=[
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "reply one"},
            {"role": "user", "content": "second"},
            {"role": "assistant", "content": "reply two"},
        ],
        current_message="now",
        channel="telegram",
        chat_id="42",
        cache_ttl=3600,
        context_files=files,
    )

    img = ws / "pic.png"
    img.write_bytes(_PNG_1x1)
    b = ContextBuilder(ws, default_context_files=list(files))
    out["with_image"] = b.build_messages(
        history=[],
        current_message="what is this?",
        media=[str(img)],
        channel="cli",
        chat_id="1",
        context_files=files,
    )

    # Kept-image injection: an ImageEntry in topic becomes a cached user/assistant
    # pair injected just before the final user message.
    from pocketfox.agent.entries import ImageEntry

    b = ContextBuilder(ws, default_context_files=list(files))
    b.add_entry(
        "topic",
        ImageEntry(
            path=ws / "kept.png",
            base64_data=base64.b64encode(_PNG_1x1).decode("ascii"),
            mime_type="image/png",
            caption="a kept pic",
        ),
    )
    out["kept_image"] = b.build_messages(
        history=[{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "ok"}],
        current_message="and now?",
        channel="cli",
        chat_id="1",
        context_files=files,
    )

    return out


def test_build_messages_golden(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    captured = {name: _normalize(msgs, ws) for name, msgs in _cases(ws).items()}

    if os.environ.get("POCKETFOX_REGOLD") or not GOLDEN.exists():
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(captured, indent=2, ensure_ascii=False), encoding="utf-8")
        pytest.skip(f"captured golden snapshot at {GOLDEN}")

    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert captured == expected


def test_sender_renders_to_openai_name_field(tmp_path: Path) -> None:
    """A message's sender flows through to the OpenAI ``name`` field end-to-end."""
    ws = _make_workspace(tmp_path)
    files = ("AGENTS.md", "TOOLS.md", "MEMORY.md")
    b = ContextBuilder(ws, default_context_files=list(files))

    messages = b.build_messages(
        history=[
            {"role": "user", "content": "earlier", "name": "alice"},
            {"role": "assistant", "content": "ok"},
        ],
        current_message="hello!",
        sender="thygrrr",
        channel="telegram",
        chat_id="42",
        context_files=files,
    )

    # History keeps each user's name; the assistant has none.
    history_user = next(m for m in messages if m["role"] == "user" and m.get("name") == "alice")
    assert history_user["content"] == "earlier"

    # The current (last) user message is attributed to its sender.
    last_user = messages[-1]
    assert last_user["role"] == "user"
    assert last_user["name"] == "thygrrr"
