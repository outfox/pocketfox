"""Tests for the VoiceTool."""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from pocketfox.agent.tools.voice import VoiceTool


class TestVoiceToolInit:
    """Tests for VoiceTool initialization."""
    
    def test_init_with_api_key(self):
        """Test initialization with explicit API key."""
        tool = VoiceTool(api_key="test-key")
        assert tool.api_key == "test-key"
        assert tool.default_voice_id == "5kN3CFEeRreSoAQlWcb9"  # Julia
        assert tool.default_stability == 0.0
    
    def test_init_with_custom_voice(self):
        """Test initialization with custom voice settings."""
        tool = VoiceTool(
            api_key="test-key",
            default_voice_id="custom-voice",
            default_stability=0.5,
        )
        assert tool.default_voice_id == "custom-voice"
        assert tool.default_stability == 0.5
    
    def test_init_without_api_key(self):
        """Test initialization without API key."""
        with patch.dict('os.environ', {}, clear=True):
            tool = VoiceTool()
            assert tool.api_key == ""


class TestVoiceToolExecute:
    """Tests for VoiceTool.execute()."""
    
    @pytest.mark.asyncio
    async def test_execute_no_api_key(self):
        """Test execute fails gracefully without API key."""
        tool = VoiceTool(api_key="")
        result = await tool.execute(text="Hello")
        assert "Error" in result
        assert "API key" in result
    
    @pytest.mark.asyncio
    async def test_execute_empty_text(self):
        """Test execute fails on empty text."""
        tool = VoiceTool(api_key="test-key")
        result = await tool.execute(text="   ")
        assert "Error" in result
        assert "empty" in result.lower()
    
    @pytest.mark.asyncio
    async def test_execute_no_sag(self):
        """Test execute fails gracefully when sag is not installed."""
        tool = VoiceTool(api_key="test-key")
        tool._sag_path = None  # Simulate sag not found
        result = await tool.execute(text="Hello")
        assert "Error" in result
        assert "sag" in result.lower()


class TestVoiceToolRedact:
    """Tests for VoiceTool.redact_params()."""
    
    def test_redact_long_text(self):
        """Test that long text is truncated in logs."""
        tool = VoiceTool(api_key="test-key")
        long_text = "A" * 200
        params = {"text": long_text, "voice_id": "abc123"}
        
        redacted = tool.redact_params(params)
        
        assert len(redacted["text"]) < len(long_text)
        assert redacted["text"].endswith("...")
        assert redacted["voice_id"] == "abc123"  # Unchanged
    
    def test_redact_short_text(self):
        """Test that short text is not truncated."""
        tool = VoiceTool(api_key="test-key")
        params = {"text": "Hello world"}
        
        redacted = tool.redact_params(params)
        
        assert redacted["text"] == "Hello world"


class TestVoiceToolParameters:
    """Tests for VoiceTool parameter schema."""
    
    def test_has_required_text_param(self):
        """Test that text is a required parameter."""
        tool = VoiceTool(api_key="test-key")
        assert "text" in tool.parameters["required"]
    
    def test_has_optional_params(self):
        """Test that optional parameters are defined."""
        tool = VoiceTool(api_key="test-key")
        props = tool.parameters["properties"]
        
        assert "output_path" in props
        assert "voice_id" in props
        assert "stability" in props
        assert "title" in props
        assert "artist" in props
    
    def test_stability_has_bounds(self):
        """Test that stability parameter has min/max bounds."""
        tool = VoiceTool(api_key="test-key")
        stability = tool.parameters["properties"]["stability"]
        
        assert stability["minimum"] == 0.0
        assert stability["maximum"] == 1.0
