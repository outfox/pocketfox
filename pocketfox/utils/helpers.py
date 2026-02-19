"""Utility functions for pocketfox."""

import os
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime


def ensure_dir(path: Path) -> Path:
    """Ensure a directory exists, creating it if necessary."""
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class PocketfoxPaths:
    """Single source of truth for all pocketfox paths.

    Config is shared across all agents (~/.config/pocketfox/).
    Agent-specific overrides live in ~/.config/pocketfox/agents/<name>/.
    Data is per-agent (~/.local/share/pocketfox/<name>/).
    Workspace is user-defined (PF_WORKSPACE or ~/workspace).
    """
    agent_name: str
    workspace: Path

    @classmethod
    def from_env(cls) -> "PocketfoxPaths":
        name = os.environ.get("PF_AGENT_NAME", "pocketfox")
        workspace = Path(os.environ.get("PF_WORKSPACE",
                         Path.home() / "workspace"))
        return cls(agent_name=name, workspace=workspace.expanduser())

    @property
    def data(self) -> Path:
        """Agent-specific data (~/.local/share/pocketfox/<name>/)."""
        return ensure_dir(
            Path.home() / ".local" / "share" / "pocketfox" / self.agent_name
        )

    @property
    def sessions(self) -> Path:
        return ensure_dir(self.data / "sessions")

    @property
    def config(self) -> Path:
        """Shared config (~/.config/pocketfox/)."""
        return ensure_dir(Path.home() / ".config" / "pocketfox")

    @property
    def config_overrides(self) -> Path:
        """Agent-specific config overrides."""
        return ensure_dir(self.config / "agents" / self.agent_name)

    @property
    def memory(self) -> Path:
        return ensure_dir(self.workspace / "memory")

    @property
    def skills(self) -> Path:
        return ensure_dir(self.workspace / "skills")

_paths: PocketfoxPaths | None = None

def get_paths() -> PocketfoxPaths:
    """Get the global PocketfoxPaths instance (lazy init from env)."""
    global _paths
    if _paths is None:
        _paths = PocketfoxPaths.from_env()
    return _paths


def today_date() -> str:
    return datetime.now().strftime("%Y-%m-%d")

def timestamp() -> str:
    return datetime.now().isoformat()

def truncate_string(s: str, max_len: int = 100, suffix: str = "...") -> str:
    if len(s) <= max_len:
        return s
    return s[: max_len - len(suffix)] + suffix

def safe_filename(name: str) -> str:
    unsafe = '<>:"/\\|?*'
    for char in unsafe:
        name = name.replace(char, "_")
    return name.strip()

def parse_session_key(key: str) -> tuple[str, str]:
    parts = key.split(":", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid session key: {key}")
    return parts[0], parts[1]

def redact_phone_number(phone: str) -> str:
    if not phone:
        return phone
    phone = phone.strip()
    if len(phone) <= 6:
        return "*" * (len(phone) - 4) + phone[-4:] if len(phone) > 4 else phone
    has_plus = phone.startswith("+")
    digits_only = phone.lstrip("+")
    if len(digits_only) <= 6:
        return "*" * (len(phone) - 4) + phone[-4:]
    prefix_len = 5 if has_plus else 4
    prefix = phone[:prefix_len]
    suffix = phone[-4:]
    return f"{prefix}***{suffix}"
