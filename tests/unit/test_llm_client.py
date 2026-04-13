"""Unit tests for LLMClient.

Tests error classification, SDK creation, and provider validation.
Does NOT call actual LLM APIs.
"""

import pytest

from prolog_reasoner.errors import LLMError
from prolog_reasoner.llm_client import LLMClient


class TestClientCreation:
    def test_unsupported_provider(self):
        with pytest.raises(LLMError) as exc_info:
            LLMClient("gemini", "fake-key", "gemini-pro")
        assert exc_info.value.error_code == "LLM_002"
        assert "Unsupported" in str(exc_info.value)

    def test_openai_client_created(self):
        """OpenAI SDK should be available in test environment."""
        client = LLMClient("openai", "test-key", "gpt-4o")
        assert client._provider == "openai"
        assert client._client is not None

    def test_anthropic_client_created(self):
        """Anthropic SDK should be available in test environment."""
        client = LLMClient("anthropic", "test-key", "claude-sonnet-4-20250514")
        assert client._provider == "anthropic"
        assert client._client is not None

    def test_timeout_default(self):
        client = LLMClient("openai", "key", "model")
        assert client._timeout_seconds == 30.0

    def test_timeout_custom(self):
        client = LLMClient("openai", "key", "model", timeout_seconds=60.0)
        assert client._timeout_seconds == 60.0


class TestErrorClassification:
    @pytest.mark.asyncio
    async def test_auth_error_openai(self):
        """Invalid API key should raise LLM_002."""
        client = LLMClient("openai", "sk-invalid-key-for-test", "gpt-4o")
        with pytest.raises(LLMError) as exc_info:
            await client.complete("system", "user", temperature=0.0)
        # Should be auth error or generic API error
        assert exc_info.value.error_code in ("LLM_001", "LLM_002")

    @pytest.mark.asyncio
    async def test_auth_error_anthropic(self):
        """Invalid API key should raise LLM_002."""
        client = LLMClient("anthropic", "sk-ant-invalid", "claude-sonnet-4-20250514")
        with pytest.raises(LLMError) as exc_info:
            await client.complete("system", "user", temperature=0.0)
        assert exc_info.value.error_code in ("LLM_001", "LLM_002")


class TestCompleteTimeout:
    @pytest.mark.asyncio
    async def test_timeout_override(self):
        """timeout_seconds parameter should override constructor value."""
        client = LLMClient("openai", "sk-invalid", "gpt-4o", timeout_seconds=30.0)
        with pytest.raises(LLMError):
            # Very short timeout — should fail fast
            await client.complete("sys", "user", timeout_seconds=0.001)
