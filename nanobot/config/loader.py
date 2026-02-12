"""Configuration loading utilities."""

import json
import tomllib
from pathlib import Path
from typing import Any

import tomli_w

from nanobot.config.schema import Config


def get_config_path() -> Path:
    """Get the default configuration file path."""
    return Path.home() / ".nanobot" / "config.toml"


def get_data_dir() -> Path:
    """Get the nanobot data directory."""
    from nanobot.utils.helpers import get_data_path

    return get_data_path()


def load_config(config_path: Path | None = None) -> Config:
    """
    Load configuration from file or create default.

    Checks for config.toml first (snake_case keys, no conversion needed),
    then falls back to config.json (camelCase keys, converted to snake_case).

    Args:
        config_path: Optional path to config file. Uses default if not provided.

    Returns:
        Loaded configuration object.
    """
    if config_path:
        return _load_from_path(config_path)

    toml_path = Path.home() / ".nanobot" / "config.toml"
    json_path = Path.home() / ".nanobot" / "config.json"

    # TOML takes precedence
    if toml_path.exists():
        return _load_from_path(toml_path)

    # Fall back to legacy JSON
    if json_path.exists():
        return _load_from_path(json_path)

    return Config()


def _load_from_path(path: Path) -> Config:
    """Load config from a specific file path."""
    try:
        if path.suffix == ".toml":
            with open(path, "rb") as f:
                data = tomllib.load(f)
            data = _migrate_config(data)
            return Config.model_validate(data)
        else:
            with open(path) as f:
                data = json.load(f)
            data = _migrate_config(data)
            return Config.model_validate(convert_keys(data))
    except (tomllib.TOMLDecodeError, json.JSONDecodeError, ValueError) as e:
        print(f"Warning: Failed to load config from {path}: {e}")
        print("Using default configuration.")
        return Config()


def save_config(config: Config, config_path: Path | None = None) -> None:
    """
    Save configuration to TOML file.

    Args:
        config: Configuration to save.
        config_path: Optional path to save to. Uses default if not provided.
    """
    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = config.model_dump()
    data = _strip_none(data)

    with open(path, "wb") as f:
        tomli_w.dump(data, f)


def _migrate_config(data: dict) -> dict:
    """Migrate old config formats to current."""
    # Move tools.exec.restrictToWorkspace → tools.restrict_to_workspace
    tools = data.get("tools", {})
    exec_cfg = tools.get("exec", {})
    # Handle both camelCase (from JSON) and snake_case (from TOML)
    for key in ("restrictToWorkspace", "restrict_to_workspace"):
        if key in exec_cfg and "restrict_to_workspace" not in tools:
            tools["restrict_to_workspace"] = exec_cfg.pop(key)
            break
    return data


def _strip_none(data: Any) -> Any:
    """Recursively strip None values from nested dicts (TOML has no null type)."""
    if isinstance(data, dict):
        return {k: _strip_none(v) for k, v in data.items() if v is not None}
    if isinstance(data, list):
        return [_strip_none(item) for item in data]
    return data


def convert_keys(data: Any) -> Any:
    """Convert camelCase keys to snake_case for Pydantic (legacy JSON support)."""
    if isinstance(data, dict):
        return {camel_to_snake(k): convert_keys(v) for k, v in data.items()}
    if isinstance(data, list):
        return [convert_keys(item) for item in data]
    return data


def camel_to_snake(name: str) -> str:
    """Convert camelCase to snake_case."""
    result = []
    for i, char in enumerate(name):
        if char.isupper() and i > 0:
            result.append("_")
        result.append(char.lower())
    return "".join(result)
