"""Structured logging for prolog-reasoner. All output goes to stderr."""

import logging
import re
import sys


def setup_logging(level: str = "INFO") -> None:
    """Configure stderr-only structured logging.

    Safe to call multiple times (prevents handler duplication).
    """
    if logging.root.handlers:
        logging.root.setLevel(level)
        return

    handler = logging.StreamHandler(sys.stderr)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    handler.setFormatter(formatter)
    logging.root.addHandler(handler)
    logging.root.setLevel(level)


class SecureLogger:
    """Logger wrapper that auto-redacts API keys and sensitive data."""

    REDACT_PATTERNS = [
        re.compile(r"sk-[a-zA-Z0-9_-]{20,}"),  # OpenAI / Anthropic
    ]

    def __init__(self, name: str):
        self._logger = logging.getLogger(name)

    def _redact(self, msg: str) -> str:
        for pattern in self.REDACT_PATTERNS:
            msg = pattern.sub("[REDACTED]", msg)
        return msg

    def debug(self, msg: str, **kwargs: object) -> None:
        self._logger.debug(self._redact(msg), **kwargs)

    def info(self, msg: str, **kwargs: object) -> None:
        self._logger.info(self._redact(msg), **kwargs)

    def warning(self, msg: str, **kwargs: object) -> None:
        self._logger.warning(self._redact(msg), **kwargs)

    def error(self, msg: str, **kwargs: object) -> None:
        self._logger.error(self._redact(msg), **kwargs)
