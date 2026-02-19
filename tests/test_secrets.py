"""Tests for Docker secrets support."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from pocketfox.utils.secrets import (
    SECRETS_PATH,
    SecretNotFoundError,
    get_secret,
    get_secret_cached,
    has_secret,
    list_secrets,
    require_secret,
)


class TestGetSecret:
    """Tests for get_secret function."""

    def test_docker_secret_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Test reading a Docker secret from file."""
        # Create a fake secrets directory
        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        secret_file = secrets_dir / "test_secret"
        secret_file.write_text("my-secret-value\n")

        # Patch the secrets path
        monkeypatch.setattr("pocketfox.utils.secrets.SECRETS_PATH", secrets_dir)

        result = get_secret("test_secret")
        assert result == "my-secret-value"

    def test_docker_secret_strips_whitespace(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Test that secret values are stripped of whitespace."""
        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        secret_file = secrets_dir / "test_secret"
        secret_file.write_text("  secret-with-spaces  \n\n")

        monkeypatch.setattr("pocketfox.utils.secrets.SECRETS_PATH", secrets_dir)

        result = get_secret("test_secret")
        assert result == "secret-with-spaces"

    def test_env_var_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Test fallback to environment variable."""
        # Empty secrets dir (no Docker secret)
        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        monkeypatch.setattr("pocketfox.utils.secrets.SECRETS_PATH", secrets_dir)

        # Set environment variable
        monkeypatch.setenv("POCKETFOX_MY_SECRET", "env-value")

        result = get_secret("my_secret")
        assert result == "env-value"

    def test_docker_secret_takes_precedence(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Test that Docker secret takes precedence over env var."""
        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        secret_file = secrets_dir / "my_secret"
        secret_file.write_text("docker-value")

        monkeypatch.setattr("pocketfox.utils.secrets.SECRETS_PATH", secrets_dir)
        monkeypatch.setenv("POCKETFOX_MY_SECRET", "env-value")

        result = get_secret("my_secret")
        assert result == "docker-value"

    def test_default_value(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Test default value when secret not found."""
        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        monkeypatch.setattr("pocketfox.utils.secrets.SECRETS_PATH", secrets_dir)

        result = get_secret("nonexistent", default="default-value")
        assert result == "default-value"

    def test_returns_none_when_not_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Test returns None when secret not found and no default."""
        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        monkeypatch.setattr("pocketfox.utils.secrets.SECRETS_PATH", secrets_dir)

        result = get_secret("nonexistent")
        assert result is None


class TestRequireSecret:
    """Tests for require_secret function."""

    def test_returns_value_when_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Test returns value when secret exists."""
        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        secret_file = secrets_dir / "required_secret"
        secret_file.write_text("required-value")

        monkeypatch.setattr("pocketfox.utils.secrets.SECRETS_PATH", secrets_dir)

        result = require_secret("required_secret")
        assert result == "required-value"

    def test_raises_when_not_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Test raises SecretNotFoundError when secret not found."""
        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        monkeypatch.setattr("pocketfox.utils.secrets.SECRETS_PATH", secrets_dir)

        with pytest.raises(SecretNotFoundError) as exc_info:
            require_secret("missing_secret")

        assert exc_info.value.name == "missing_secret"
        assert "missing_secret" in str(exc_info.value)


class TestListSecrets:
    """Tests for list_secrets function."""

    def test_lists_secrets(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Test listing available secrets."""
        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        (secrets_dir / "secret1").write_text("value1")
        (secrets_dir / "secret2").write_text("value2")

        monkeypatch.setattr("pocketfox.utils.secrets.SECRETS_PATH", secrets_dir)

        result = list_secrets()
        assert sorted(result) == ["secret1", "secret2"]

    def test_empty_when_no_secrets_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Test returns empty list when secrets dir doesn't exist."""
        monkeypatch.setattr("pocketfox.utils.secrets.SECRETS_PATH", tmp_path / "nonexistent")

        result = list_secrets()
        assert result == []


class TestHasSecret:
    """Tests for has_secret function."""

    def test_true_when_docker_secret_exists(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Test returns True when Docker secret exists."""
        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        (secrets_dir / "my_secret").write_text("value")

        monkeypatch.setattr("pocketfox.utils.secrets.SECRETS_PATH", secrets_dir)

        assert has_secret("my_secret") is True

    def test_true_when_env_var_exists(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Test returns True when env var exists."""
        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        monkeypatch.setattr("pocketfox.utils.secrets.SECRETS_PATH", secrets_dir)
        monkeypatch.setenv("POCKETFOX_MY_SECRET", "value")

        assert has_secret("my_secret") is True

    def test_false_when_not_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Test returns False when secret not found."""
        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        monkeypatch.setattr("pocketfox.utils.secrets.SECRETS_PATH", secrets_dir)

        assert has_secret("nonexistent") is False


class TestGetSecretCached:
    """Tests for get_secret_cached function."""

    def test_caches_result(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Test that results are cached."""
        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        secret_file = secrets_dir / "cached_secret"
        secret_file.write_text("original-value")

        monkeypatch.setattr("pocketfox.utils.secrets.SECRETS_PATH", secrets_dir)

        # Clear any existing cache
        get_secret_cached.cache_clear()

        # First call
        result1 = get_secret_cached("cached_secret")
        assert result1 == "original-value"

        # Modify the secret file
        secret_file.write_text("modified-value")

        # Second call should return cached value
        result2 = get_secret_cached("cached_secret")
        assert result2 == "original-value"  # Still the original!

        # Clear cache and try again
        get_secret_cached.cache_clear()
        result3 = get_secret_cached("cached_secret")
        assert result3 == "modified-value"
