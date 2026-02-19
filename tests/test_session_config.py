"""Tests for session configuration and input parsing."""

import pytest
from pocketfox.config.inputs import parse_input, parse_inputs, ParsedInput
from pocketfox.config.schema import SessionConfig, SessionsConfig


class TestInputParsing:
    """Tests for input string parsing."""
    
    def test_telegram_basic(self):
        """Parse basic telegram input."""
        result = parse_input("telegram|123456789:ABCdefGHI")
        assert result.channel_type == "telegram"
        assert result.config["token"] == "123456789:ABCdefGHI"
        assert "proxy" not in result.config
    
    def test_telegram_with_proxy(self):
        """Parse telegram input with proxy."""
        result = parse_input("telegram|123456789:ABCdefGHI|http://proxy:8080")
        assert result.channel_type == "telegram"
        assert result.config["token"] == "123456789:ABCdefGHI"
        assert result.config["proxy"] == "http://proxy:8080"
    
    def test_discord_basic(self):
        """Parse basic discord input."""
        result = parse_input("discord|MTIzNDU2Nzg5.abcdef")
        assert result.channel_type == "discord"
        assert result.config["token"] == "MTIzNDU2Nzg5.abcdef"
    
    def test_signal_basic(self):
        """Parse signal input."""
        result = parse_input("signal|http://signal:8080|+1234567890")
        assert result.channel_type == "signal"
        assert result.config["api_url"] == "http://signal:8080"
        assert result.config["phone_number"] == "+1234567890"
    
    def test_signal_missing_phone(self):
        """Signal requires phone number."""
        with pytest.raises(ValueError, match="phone number"):
            parse_input("signal|http://signal:8080")
    
    def test_whatsapp_basic(self):
        """Parse whatsapp input."""
        result = parse_input("whatsapp|ws://localhost:3001")
        assert result.channel_type == "whatsapp"
        assert result.config["bridge_url"] == "ws://localhost:3001"
    
    def test_feishu_basic(self):
        """Parse feishu input."""
        result = parse_input("feishu|app123|secret456")
        assert result.channel_type == "feishu"
        assert result.config["app_id"] == "app123"
        assert result.config["app_secret"] == "secret456"
    
    def test_feishu_with_encrypt(self):
        """Parse feishu input with encryption keys."""
        result = parse_input("feishu|app123|secret456|enckey|verifytoken")
        assert result.channel_type == "feishu"
        assert result.config["app_id"] == "app123"
        assert result.config["app_secret"] == "secret456"
        assert result.config["encrypt_key"] == "enckey"
        assert result.config["verification_token"] == "verifytoken"
    
    def test_dingtalk_basic(self):
        """Parse dingtalk input."""
        result = parse_input("dingtalk|client123|secret456")
        assert result.channel_type == "dingtalk"
        assert result.config["client_id"] == "client123"
        assert result.config["client_secret"] == "secret456"
    
    def test_unknown_channel(self):
        """Unknown channel type raises error."""
        with pytest.raises(ValueError, match="Unknown channel type"):
            parse_input("slack|token123")
    
    def test_missing_separator(self):
        """Missing | separator raises error."""
        with pytest.raises(ValueError, match="Invalid input format"):
            parse_input("telegram123456")
    
    def test_case_insensitive(self):
        """Channel type is case insensitive."""
        result = parse_input("TELEGRAM|token123")
        assert result.channel_type == "telegram"
        
        result = parse_input("Discord|token123")
        assert result.channel_type == "discord"
    
    def test_parse_multiple(self):
        """Parse multiple inputs."""
        inputs = [
            "telegram|token1",
            "discord|token2",
        ]
        results = parse_inputs(inputs)
        assert len(results) == 2
        assert results[0].channel_type == "telegram"
        assert results[1].channel_type == "discord"


class TestSessionConfig:
    """Tests for SessionConfig."""
    
    def test_defaults(self):
        """SessionConfig has sensible defaults."""
        config = SessionConfig()
        assert config.enabled is True
        assert config.inputs == []
        assert config.owners == []
        assert config.allow_from == []
        assert config.sandbox is None
        assert config.readonly == []
    
    def test_full_config(self):
        """SessionConfig with all fields."""
        config = SessionConfig(
            enabled=True,
            inputs=["telegram|token123"],
            owners=["user1"],
            allow_from=["user1", "user2"],
            sandbox="/home/user/workspace",
            readonly=["/shared/ref1", "/shared/ref2"],
        )
        assert config.enabled is True
        assert config.inputs == ["telegram|token123"]
        assert config.owners == ["user1"]
        assert config.allow_from == ["user1", "user2"]
        assert config.sandbox == "/home/user/workspace"
        assert config.readonly == ["/shared/ref1", "/shared/ref2"]


class TestSessionsConfig:
    """Tests for SessionsConfig container."""
    
    def test_empty(self):
        """Empty sessions config."""
        config = SessionsConfig()
        assert config.all_sessions() == {}
    
    def test_get_session_from_dict(self):
        """Get session from dict data (as loaded from TOML)."""
        # Simulate how Pydantic loads extra fields from TOML
        config = SessionsConfig.model_validate({
            "main": {
                "enabled": True,
                "inputs": ["telegram|token123"],
                "sandbox": "/home/user/workspace",
            }
        })
        
        session = config.get_session("main")
        assert session is not None
        assert session.enabled is True
        assert session.inputs == ["telegram|token123"]
        assert session.sandbox == "/home/user/workspace"
    
    def test_all_sessions(self):
        """Get all sessions."""
        config = SessionsConfig.model_validate({
            "main": {
                "enabled": True,
                "inputs": ["telegram|token1"],
            },
            "group": {
                "enabled": False,
                "inputs": ["discord|token2"],
            }
        })
        
        sessions = config.all_sessions()
        assert len(sessions) == 2
        assert "main" in sessions
        assert "group" in sessions
        assert sessions["main"].enabled is True
        assert sessions["group"].enabled is False
    
    def test_get_nonexistent_session(self):
        """Get nonexistent session returns None."""
        config = SessionsConfig()
        assert config.get_session("nonexistent") is None
