"""Unit tests for SecureLogger and setup_logging."""

import logging

from prolog_reasoner.logger import SecureLogger, setup_logging


class TestSecureLogger:
    def test_redact_openai_and_anthropic_keys(self):
        """Both OpenAI (sk-...) and Anthropic (sk-ant-...) keys are redacted."""
        logger = SecureLogger("test")
        msg = (
            "openai=sk-abc123def456ghi789jkl012mno "
            "anthropic=sk-ant-api03-abcdefghijklmnopqrstuvwx"
        )
        redacted = logger._redact(msg)
        assert "sk-abc123" not in redacted
        assert "sk-ant-api03" not in redacted
        assert redacted.count("[REDACTED]") == 2

    def test_no_redact_normal_text(self):
        logger = SecureLogger("test")
        msg = "Prolog execution completed in 42ms"
        assert logger._redact(msg) == msg

    def test_short_sk_prefix_not_redacted(self):
        """sk- followed by < 20 chars should NOT be redacted."""
        logger = SecureLogger("test")
        msg = "token sk-short"
        assert logger._redact(msg) == msg


class TestSetupLogging:
    def test_setup_adds_handler(self):
        # Clear handlers to test fresh setup
        logging.root.handlers.clear()
        setup_logging("DEBUG")
        assert len(logging.root.handlers) >= 1
        assert logging.root.level == logging.DEBUG

    def test_duplicate_call_does_not_add_handler(self):
        """Multiple calls should not duplicate handlers."""
        logging.root.handlers.clear()
        setup_logging("INFO")
        count_after_first = len(logging.root.handlers)
        setup_logging("DEBUG")
        assert len(logging.root.handlers) == count_after_first
