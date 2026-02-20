"""Tests for TOML config loading, saving, and round-trip."""

import tomllib
from pathlib import Path

import pytest
import tomli_w

from nanobot.config.loader import (
    _strip_none,
    load_config,
    save_config,
)
from nanobot.config.schema import Config


def test_load_toml(tmp_path: Path):
    """TOML file with snake_case keys loads correctly."""
    toml_path = tmp_path / "config.toml"
    data = {
        "providers": {"openrouter": {"api_key": "sk-test-123"}},
        "agents": {"defaults": {"model": "openrouter/claude-opus-4-5"}},
    }
    with open(toml_path, "wb") as f:
        tomli_w.dump(data, f)

    config = load_config(toml_path)
    assert config.providers.openrouter.api_key == "sk-test-123"
    assert config.agents.defaults.model == "openrouter/claude-opus-4-5"


def test_save_sets_permissions(tmp_path: Path):
    """save_config sets file permissions to 0o600."""
    toml_path = tmp_path / "config.toml"
    save_config(Config(), toml_path)

    mode = toml_path.stat().st_mode & 0o777
    assert mode == 0o600


def test_default_config_when_no_file(tmp_path: Path, monkeypatch):
    """Returns default Config when no config file exists."""
    nanobot_dir = tmp_path / ".nanobot"
    nanobot_dir.mkdir()

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    config = load_config()
    assert config == Config()


def test_save_produces_valid_toml(tmp_path: Path):
    """save_config writes a valid TOML file with snake_case keys."""
    toml_path = tmp_path / "config.toml"
    config = Config()
    config.agents.defaults.model = "test-model"

    save_config(config, toml_path)

    with open(toml_path, "rb") as f:
        data = tomllib.load(f)

    assert data["agents"]["defaults"]["model"] == "test-model"
    # Keys should be snake_case, not camelCase
    assert "restrict_to_workspace" in data.get("tools", {})


def test_round_trip(tmp_path: Path):
    """save → load produces equivalent config."""
    toml_path = tmp_path / "config.toml"

    original = Config()
    original.agents.defaults.model = "round-trip-model"
    original.providers.openrouter.api_key = "sk-round-trip"
    original.tools.restrict_to_workspace = True

    save_config(original, toml_path)
    loaded = load_config(toml_path)

    assert loaded.agents.defaults.model == original.agents.defaults.model
    assert loaded.providers.openrouter.api_key == original.providers.openrouter.api_key
    assert loaded.tools.restrict_to_workspace == original.tools.restrict_to_workspace


def test_strip_none():
    """_strip_none removes None values from nested structures."""
    data = {
        "a": 1,
        "b": None,
        "c": {"d": None, "e": "keep"},
        "f": [1, {"g": None, "h": 2}],
    }
    result = _strip_none(data)
    assert result == {
        "a": 1,
        "c": {"e": "keep"},
        "f": [1, {"h": 2}],
    }


def test_strip_none_empty_dict():
    """_strip_none handles empty dicts and all-None dicts."""
    assert _strip_none({}) == {}
    assert _strip_none({"a": None}) == {}


def test_invalid_toml_raises(tmp_path: Path):
    """Invalid TOML file raises an exception."""
    toml_path = tmp_path / "config.toml"
    toml_path.write_text("this is [not valid toml")

    with pytest.raises(tomllib.TOMLDecodeError):
        load_config(toml_path)
