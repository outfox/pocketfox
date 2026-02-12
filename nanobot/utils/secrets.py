"""Docker secrets support for sensitive configuration.

Docker secrets are mounted at /run/secrets/<secret_name> and provide
a secure way to pass sensitive data to containers without exposing
them in environment variables, command line arguments, or logs.

Usage:
    from nanobot.utils.secrets import get_secret, require_secret

    # Returns None if secret doesn't exist
    api_key = get_secret("my_api_key")

    # Raises SecretNotFoundError if secret doesn't exist
    passphrase = require_secret("keepassxc_passphrase")

Environment variable fallback:
    If a Docker secret is not found, the function will check for an
    environment variable with the same name (uppercased with NANOBOT_ prefix).
    E.g., "keepassxc_passphrase" -> NANOBOT_KEEPASSXC_PASSPHRASE
"""

import os
from pathlib import Path
from functools import lru_cache

from loguru import logger


# Standard Docker secrets path
SECRETS_PATH = Path("/run/secrets")


class SecretNotFoundError(Exception):
    """Raised when a required secret is not found."""

    def __init__(self, name: str):
        self.name = name
        super().__init__(
            f"Secret '{name}' not found. "
            f"Expected at {SECRETS_PATH / name} or env var NANOBOT_{name.upper()}"
        )


def get_secret(name: str, default: str | None = None) -> str | None:
    """
    Get a secret value from Docker secrets or environment variable.

    Lookup order:
    1. Docker secret at /run/secrets/<name>
    2. Environment variable NANOBOT_<NAME> (uppercased)
    3. Default value (if provided)

    Args:
        name: The secret name (e.g., "keepassxc_passphrase")
        default: Default value if secret is not found

    Returns:
        The secret value, or default if not found
    """
    # Try Docker secret first
    secret_path = SECRETS_PATH / name
    if secret_path.exists():
        try:
            value = secret_path.read_text().strip()
            logger.debug("Loaded secret '{}' from Docker secrets", name)
            return value
        except OSError as e:
            logger.warning("Failed to read secret '{}': {}", name, e)

    # Fall back to environment variable
    env_name = f"NANOBOT_{name.upper()}"
    env_value = os.environ.get(env_name)
    if env_value is not None:
        logger.debug("Loaded secret '{}' from env var {}", name, env_name)
        return env_value

    # Return default
    if default is not None:
        logger.debug("Using default value for secret '{}'", name)
    return default


def require_secret(name: str) -> str:
    """
    Get a required secret value.

    Like get_secret(), but raises SecretNotFoundError if the secret
    is not found instead of returning None.

    Args:
        name: The secret name

    Returns:
        The secret value

    Raises:
        SecretNotFoundError: If the secret is not found
    """
    value = get_secret(name)
    if value is None:
        raise SecretNotFoundError(name)
    return value


@lru_cache(maxsize=32)
def get_secret_cached(name: str) -> str | None:
    """
    Get a secret with caching.

    Use this for secrets that are read frequently and don't change
    during the lifetime of the process.

    Args:
        name: The secret name

    Returns:
        The secret value, or None if not found
    """
    return get_secret(name)


def list_secrets() -> list[str]:
    """
    List all available Docker secrets.

    Returns:
        List of secret names available in /run/secrets/
    """
    if not SECRETS_PATH.exists():
        return []
    return [f.name for f in SECRETS_PATH.iterdir() if f.is_file()]


def has_secret(name: str) -> bool:
    """
    Check if a secret exists.

    Args:
        name: The secret name

    Returns:
        True if the secret exists (as Docker secret or env var)
    """
    return get_secret(name) is not None
