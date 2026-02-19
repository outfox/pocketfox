"""Configuration loading utilities."""

import os
import tomllib
from pathlib import Path
from typing import Any

import tomli_w
from loguru import logger

from pocketfox.config.schema import Config
from pocketfox.utils.helpers import get_paths


def get_config_path() -> Path:
    """Get the default configuration file path."""
    return get_paths().config / "config.toml"


def load_config(config_path: Path | None = None) -> Config:
    """Load configuration with layered merging.

    Layer order (last wins):
        1. Schema defaults (from Config model)
        2. Shared config:  <config_dir>/config.toml
        3. Agent overrides: <config_overrides_dir>/config.toml
        4. Explicit path (if provided, replaces layers 2+3)
    """
    if config_path:
        data = _load_toml_or_raise(config_path)
        return Config.model_validate(data)

    paths = get_paths()
    shared_path = paths.config / "config.toml"
    override_path = paths.config_overrides / "config.toml"

    if not shared_path.exists():
        logger.info("No config at {}; using defaults", shared_path)
        return Config()

    data = _load_toml_or_raise(shared_path)
    logger.debug("Loaded shared config from {}", shared_path)

    if override_path.exists():
        overrides = _load_toml(override_path)
        logger.debug("Applying agent overrides from {}", override_path)
        data = _deep_merge(data, overrides)

    return Config.model_validate(data)


def save_config(config: Config, config_path: Path | None = None) -> None:
    """Save configuration to TOML file."""
    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = _strip_none(config.model_dump())

    with open(path, "wb") as f:
        tomli_w.dump(data, f)
    os.chmod(path, 0o600)


# --- internals ---


def _load_toml(path: Path) -> dict:
    """Load a TOML file, returning empty dict if not found."""
    if not path.exists():
        return {}
    return _load_toml_or_raise(path)


def _load_toml_or_raise(path: Path) -> dict:
    """Load a TOML file, raising on missing or malformed."""
    with open(path, "rb") as f:
        try:
            return tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise tomllib.TOMLDecodeError(ValueError(f"Failed to parse {path}: {e}")) from e


def _deep_merge(base: dict, overrides: dict) -> dict:
    """Recursively merge overrides into base."""
    result = base.copy()
    for key, value in overrides.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result



def _strip_none(data: Any) -> Any:
    """Recursively strip None values from nested dicts (TOML has no null type)."""
    if isinstance(data, dict):
        return {k: _strip_none(v) for k, v in data.items() if v is not None}
    if isinstance(data, list):
        return [_strip_none(item) for item in data]
    return data