"""Tests for per-context tool whitelist (glob-based filtering)."""

from typing import Any

from pocketfox.agent.tools.base import Tool
from pocketfox.agent.tools.registry import ToolRegistry


class _StubTool(Tool):
    """Minimal Tool implementation for registry tests."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"stub {self._name}"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        return "ok"


def _populated_registry() -> ToolRegistry:
    reg = ToolRegistry()
    for name in [
        "fs_read",
        "fs_write",
        "fs_edit",
        "fs_list",
        "fs_view_image",
        "shell_exec",
        "web_search",
        "web_fetch",
        "message_send",
        "cron_schedule",
        "agent_spawn",
        "voice_speak",
    ]:
        reg.register(_StubTool(name))
    return reg


def _names(defs: list[dict[str, Any]]) -> set[str]:
    return {d["function"]["name"] for d in defs}


class TestGetDefinitionsFiltering:
    def test_none_returns_all_tools(self) -> None:
        reg = _populated_registry()
        defs = reg.get_definitions(None)
        assert len(defs) == 12

    def test_empty_list_returns_all_tools(self) -> None:
        reg = _populated_registry()
        defs = reg.get_definitions([])
        assert len(defs) == 12

    def test_literal_name_match(self) -> None:
        reg = _populated_registry()
        defs = reg.get_definitions(["fs_read"])
        assert _names(defs) == {"fs_read"}

    def test_glob_star_match(self) -> None:
        reg = _populated_registry()
        defs = reg.get_definitions(["fs_*"])
        assert _names(defs) == {
            "fs_read",
            "fs_write",
            "fs_edit",
            "fs_list",
            "fs_view_image",
        }

    def test_multiple_patterns_union(self) -> None:
        reg = _populated_registry()
        defs = reg.get_definitions(["fs_*", "message_send"])
        assert _names(defs) == {
            "fs_read",
            "fs_write",
            "fs_edit",
            "fs_list",
            "fs_view_image",
            "message_send",
        }

    def test_no_matches_returns_empty(self) -> None:
        reg = _populated_registry()
        defs = reg.get_definitions(["nonexistent_*"])
        assert defs == []

    def test_user_requested_example_message_glob(self) -> None:
        """The user's example: ['message_*'] should pick up messaging tools."""
        reg = _populated_registry()
        defs = reg.get_definitions(["message_*"])
        assert _names(defs) == {"message_send"}

    def test_case_sensitive_matching(self) -> None:
        """fnmatchcase is case-sensitive — uppercase should not match lowercase."""
        reg = _populated_registry()
        defs = reg.get_definitions(["FS_*"])
        assert defs == []

    def test_question_mark_wildcard(self) -> None:
        """fnmatch ? matches a single character."""
        reg = ToolRegistry()
        reg.register(_StubTool("fs_a"))
        reg.register(_StubTool("fs_ab"))
        defs = reg.get_definitions(["fs_?"])
        assert _names(defs) == {"fs_a"}

    def test_cross_category_glob(self) -> None:
        reg = _populated_registry()
        defs = reg.get_definitions(["*_read", "*_search"])
        assert _names(defs) == {"fs_read", "web_search"}


class TestIsAllowed:
    def test_none_allows_everything(self) -> None:
        reg = _populated_registry()
        assert reg.is_allowed("fs_read", None) is True
        assert reg.is_allowed("shell_exec", None) is True

    def test_empty_list_allows_everything(self) -> None:
        reg = _populated_registry()
        assert reg.is_allowed("fs_read", []) is True

    def test_literal_match(self) -> None:
        reg = _populated_registry()
        assert reg.is_allowed("fs_read", ["fs_read"]) is True
        assert reg.is_allowed("fs_write", ["fs_read"]) is False

    def test_glob_match(self) -> None:
        reg = _populated_registry()
        assert reg.is_allowed("fs_write", ["fs_*"]) is True
        assert reg.is_allowed("shell_exec", ["fs_*"]) is False

    def test_multiple_patterns(self) -> None:
        reg = _populated_registry()
        patterns = ["fs_read", "web_*"]
        assert reg.is_allowed("fs_read", patterns) is True
        assert reg.is_allowed("web_search", patterns) is True
        assert reg.is_allowed("web_fetch", patterns) is True
        assert reg.is_allowed("fs_write", patterns) is False
        assert reg.is_allowed("shell_exec", patterns) is False

    def test_tuple_patterns_work(self) -> None:
        """Patterns can be passed as a tuple (how loop.py stores them in meta)."""
        reg = _populated_registry()
        assert reg.is_allowed("fs_read", ("fs_*",)) is True
        assert reg.is_allowed("shell_exec", ("fs_*",)) is False


class TestContextConfigField:
    def test_allowed_tools_default_is_empty_list(self) -> None:
        from pocketfox.config.schema import ContextConfig

        cfg = ContextConfig()
        assert cfg.allowed_tools == []

    def test_allowed_tools_accepts_glob_patterns(self) -> None:
        from pocketfox.config.schema import ContextConfig

        cfg = ContextConfig(allowed_tools=["fs_*", "message_send", "web_*"])
        assert cfg.allowed_tools == ["fs_*", "message_send", "web_*"]
