"""Tests for pocketfox.utils.helpers."""

import pytest

from pocketfox.utils.helpers import redact_phone_number


class TestRedactPhoneNumber:
    """Tests for phone number redaction."""

    def test_international_german_number(self):
        """Standard German mobile number."""
        assert redact_phone_number("+49123456789012") == "+4912***9012"

    def test_international_us_number(self):
        """US number format."""
        assert redact_phone_number("+12025551234") == "+1202***1234"

    def test_number_without_plus(self):
        """Number without + prefix (4 chars shown instead of 5)."""
        assert redact_phone_number("49123456789012") == "4912***9012"

    def test_short_number(self):
        """Short numbers should still be partially masked."""
        assert redact_phone_number("12345") == "*2345"
        assert redact_phone_number("123456") == "**3456"

    def test_very_short_number(self):
        """Very short numbers returned as-is."""
        assert redact_phone_number("1234") == "1234"
        assert redact_phone_number("123") == "123"

    def test_empty_string(self):
        """Empty string returns empty."""
        assert redact_phone_number("") == ""

    def test_whitespace_handling(self):
        """Whitespace should be stripped."""
        assert redact_phone_number("  +49123456789012  ") == "+4912***9012"

    def test_preserves_prefix_and_suffix(self):
        """Verify the redaction preserves useful debugging info."""
        result = redact_phone_number("+4916090722506")
        # Should start with country code area
        assert result.startswith("+4916")
        # Should end with last 4 digits
        assert result.endswith("2506")
        # Should have *** in the middle
        assert "***" in result
