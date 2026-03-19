"""Tests for ContextRouter — matching, output resolution, and cron listing."""

import pytest

from pocketfox.agent.router import ContextRouter
from pocketfox.config.schema import ContextConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_router(contexts: dict[str, ContextConfig]) -> ContextRouter:
    return ContextRouter(contexts)


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


class TestMatch:
    """Tests for ContextRouter.match()."""

    def test_wildcard_match(self):
        router = _make_router({
            "main": ContextConfig(inputs=["telegram:*"]),
        })
        assert router.match("telegram", "123") == ["main"]

    def test_exact_match(self):
        router = _make_router({
            "friends": ContextConfig(inputs=["telegram:-100123"]),
        })
        assert router.match("telegram", "-100123") == ["friends"]

    def test_no_match(self):
        router = _make_router({
            "main": ContextConfig(inputs=["telegram:*"]),
        })
        assert router.match("discord", "456") == []

    def test_fan_out_multiple_contexts(self):
        router = _make_router({
            "main": ContextConfig(inputs=["telegram:*"]),
            "logging": ContextConfig(inputs=["telegram:*"]),
        })
        matched = router.match("telegram", "123")
        assert len(matched) == 2
        assert "main" in matched
        assert "logging" in matched

    def test_exact_and_wildcard_no_duplicates(self):
        router = _make_router({
            "main": ContextConfig(inputs=["telegram:*", "telegram:123"]),
        })
        matched = router.match("telegram", "123")
        assert matched == ["main"]  # No duplicate

    def test_mixed_exact_and_wildcard(self):
        router = _make_router({
            "specific": ContextConfig(inputs=["telegram:123"]),
            "general": ContextConfig(inputs=["telegram:*"]),
        })
        matched = router.match("telegram", "123")
        assert "specific" in matched
        assert "general" in matched

    def test_empty_inputs(self):
        router = _make_router({
            "cron_only": ContextConfig(inputs=[]),
        })
        assert router.match("telegram", "123") == []


# ---------------------------------------------------------------------------
# Output resolution
# ---------------------------------------------------------------------------


class TestGetOutputs:
    """Tests for ContextRouter.get_outputs()."""

    def test_responsive_wildcard_resolves_to_trigger(self):
        router = _make_router({
            "main": ContextConfig(outputs_responsive=["telegram:*"]),
        })
        outputs = router.get_outputs("main", "telegram", "123")
        assert outputs == [("telegram", "123")]

    def test_always_outputs(self):
        router = _make_router({
            "main": ContextConfig(outputs_always=["telegram:789"]),
        })
        outputs = router.get_outputs("main", None, None)
        assert outputs == [("telegram", "789")]

    def test_deduplication(self):
        router = _make_router({
            "main": ContextConfig(
                outputs_always=["telegram:123"],
                outputs_responsive=["telegram:123"],
            ),
        })
        outputs = router.get_outputs("main", "telegram", "123")
        assert outputs == [("telegram", "123")]  # Deduplicated

    def test_no_responsive_without_trigger(self):
        router = _make_router({
            "main": ContextConfig(
                outputs_always=["telegram:789"],
                outputs_responsive=["telegram:*"],
            ),
        })
        outputs = router.get_outputs("main", None, None)
        assert outputs == [("telegram", "789")]  # Only always

    def test_responsive_different_channel_wildcard(self):
        """Wildcard only resolves when channel matches."""
        router = _make_router({
            "main": ContextConfig(outputs_responsive=["telegram:*"]),
        })
        outputs = router.get_outputs("main", "discord", "456")
        assert outputs == []  # telegram:* doesn't match discord trigger

    def test_responsive_literal(self):
        router = _make_router({
            "main": ContextConfig(outputs_responsive=["telegram:999"]),
        })
        outputs = router.get_outputs("main", "telegram", "123")
        assert outputs == [("telegram", "999")]

    def test_always_plus_responsive(self):
        router = _make_router({
            "main": ContextConfig(
                outputs_always=["telegram:789"],
                outputs_responsive=["telegram:*"],
            ),
        })
        outputs = router.get_outputs("main", "telegram", "123")
        assert ("telegram", "789") in outputs
        assert ("telegram", "123") in outputs
        assert len(outputs) == 2


# ---------------------------------------------------------------------------
# Cron contexts
# ---------------------------------------------------------------------------


class TestCronContexts:
    """Tests for ContextRouter.get_cron_contexts()."""

    def test_lists_cron_contexts(self):
        router = _make_router({
            "main": ContextConfig(cron="*/30 * * * *", cron_files=["HEARTBEAT.md"]),
            "other": ContextConfig(),
        })
        cron_ctxs = router.get_cron_contexts()
        assert len(cron_ctxs) == 1
        assert cron_ctxs[0][0] == "main"
        assert cron_ctxs[0][1].cron == "*/30 * * * *"

    def test_no_cron_contexts(self):
        router = _make_router({
            "main": ContextConfig(),
        })
        assert router.get_cron_contexts() == []


# ---------------------------------------------------------------------------
# Context files
# ---------------------------------------------------------------------------


class TestContextFiles:
    """Tests for context file resolution."""

    def test_get_context_files(self):
        router = _make_router({
            "main": ContextConfig(context_files=["A.md", "B.md"]),
        })
        assert router.get_context_files("main") == ["A.md", "B.md"]

    def test_get_cron_context_files(self):
        router = _make_router({
            "main": ContextConfig(
                context_files=["A.md"],
                cron_files=["HEARTBEAT.md"],
            ),
        })
        assert router.get_cron_context_files("main") == ["A.md", "HEARTBEAT.md"]
