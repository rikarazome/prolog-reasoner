"""Unit tests for Settings and validate_swipl()."""

import pytest

from prolog_reasoner.config import Settings
from prolog_reasoner.errors import ConfigurationError


class TestSettings:
    def test_defaults(self):
        s = Settings(llm_api_key="test-key")
        assert s.llm_provider == "openai"
        assert s.llm_model == "gpt-5.4-mini"
        assert s.llm_temperature == 0.0
        assert s.swipl_path == "swipl"
        assert s.execution_timeout_seconds == 10.0
        assert s.log_level == "INFO"

    def test_custom_values(self):
        s = Settings(
            llm_api_key="key",
            llm_provider="anthropic",
            llm_model="claude-sonnet-4-20250514",
            swipl_path="/usr/bin/swipl",
        )
        assert s.llm_provider == "anthropic"
        assert s.llm_model == "claude-sonnet-4-20250514"


class TestValidateSwipl:
    def test_valid_swipl(self):
        """swipl is available in Docker — should pass."""
        s = Settings(llm_api_key="dummy")
        s.validate_swipl()  # Should not raise

    def test_not_found(self):
        """Non-existent path should raise CONFIG_001."""
        s = Settings(llm_api_key="dummy", swipl_path="/nonexistent/swipl")
        with pytest.raises(ConfigurationError) as exc_info:
            s.validate_swipl()
        assert exc_info.value.error_code == "CONFIG_001"
        assert "not found" in str(exc_info.value).lower()

    def test_not_executable(self, tmp_path):
        """File exists but is not a valid executable."""
        fake = tmp_path / "fake_swipl"
        fake.write_text("not a binary")
        # Don't make it executable — should fail
        s = Settings(llm_api_key="dummy", swipl_path=str(fake))
        with pytest.raises(ConfigurationError) as exc_info:
            s.validate_swipl()
        assert exc_info.value.error_code == "CONFIG_001"
