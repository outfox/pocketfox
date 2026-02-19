"""Utility functions for pocketfox."""

from pathlib import Path
from datetime import datetime


def ensure_dir(path: Path) -> Path:
    """Ensure a directory exists, creating it if necessary."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_agent_name() -> str:
    """Get the agent name from PF_AGENT_NAME env var, defaulting to 'pocketfox'."""
    import os
    return os.environ.get("PF_AGENT_NAME", "pocketfox")


def get_data_path() -> Path:
    """Get the agent data directory (~/.config/pocketfox-<name>)."""
    name = get_agent_name()
    if name != "pocketfox":
        print(f"Using agent name '{name}' from PF_AGENT_NAME environment variable.")
        return ensure_dir(Path.home() / ".config" / f"pocketfox-{name}")
    else:
        return ensure_dir(Path.home() / ".config" / "pocketfox")


def get_workspace_path(workspace: str | None = None) -> Path:
    """
    Get the workspace path.

    Args:
        workspace: Optional workspace path. Defaults to ~/.config/pocketfox-<name>/workspace.

    Returns:
        Expanded and ensured workspace path.
    """
    if workspace:
        path = Path(workspace).expanduser()
    else:
        path = get_data_path() / "workspace"
    return ensure_dir(path)


def get_sessions_path() -> Path:
    """Get the sessions storage directory."""
    return ensure_dir(get_data_path() / "sessions")


def get_memory_path(workspace: Path | None = None) -> Path:
    """Get the memory directory within the workspace."""
    ws = workspace or get_workspace_path()
    return ensure_dir(ws / "memory")


def get_skills_path(workspace: Path | None = None) -> Path:
    """Get the skills directory within the workspace."""
    ws = workspace or get_workspace_path()
    return ensure_dir(ws / "skills")


def today_date() -> str:
    """Get today's date in YYYY-MM-DD format."""
    return datetime.now().strftime("%Y-%m-%d")


def timestamp() -> str:
    """Get current timestamp in ISO format."""
    return datetime.now().isoformat()


def truncate_string(s: str, max_len: int = 100, suffix: str = "...") -> str:
    """Truncate a string to max length, adding suffix if truncated."""
    if len(s) <= max_len:
        return s
    return s[: max_len - len(suffix)] + suffix


def safe_filename(name: str) -> str:
    """Convert a string to a safe filename."""
    # Replace unsafe characters
    unsafe = '<>:"/\\|?*'
    for char in unsafe:
        name = name.replace(char, "_")
    return name.strip()


def parse_session_key(key: str) -> tuple[str, str]:
    """
    Parse a session key into channel and chat_id.
    
    Args:
        key: Session key in format "channel:chat_id"
    
    Returns:
        Tuple of (channel, chat_id)
    """
    parts = key.split(":", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid session key: {key}")
    return parts[0], parts[1]


def redact_phone_number(phone: str) -> str:
    """
    Redact a phone number for privacy in logs.

    Preserves country code and last few digits for debugging,
    while hiding the middle portion.

    Examples:
        +49123456789012 -> +4912***9012
        +1234567890 -> +123***7890
        12345 -> *2345 (short numbers: just hide first digit)

    Args:
        phone: Phone number string (with or without + prefix)

    Returns:
        Redacted phone number string
    """
    if not phone:
        return phone

    # Strip any whitespace
    phone = phone.strip()

    # For very short strings, just mask the beginning
    if len(phone) <= 6:
        return "*" * (len(phone) - 4) + phone[-4:] if len(phone) > 4 else phone

    # Determine prefix (+ and country code) - keep first 3-5 chars
    # Standard format: +<country><area><number>
    # We want to show: +<country><partial area>***<last 4>
    has_plus = phone.startswith("+")
    digits_only = phone.lstrip("+")

    if len(digits_only) <= 6:
        # Short number - just show last 4 with some masking
        visible_end = 4
        masked_len = len(phone) - visible_end
        return "*" * masked_len + phone[-visible_end:]

    # For international numbers: show country code + 2-3 digits, then ***, then last 4
    # +49 176 243 84108 -> +49176***4108
    prefix_len = 5 if has_plus else 4  # +49176 or 49176
    suffix_len = 4

    prefix = phone[:prefix_len]
    suffix = phone[-suffix_len:]

    return f"{prefix}***{suffix}"
