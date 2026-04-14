"""Unit tests for LLMClient.

Tests error classification, SDK creation, and provider validation.
Does NOT call actual LLM APIs — all network paths are mocked.
"""

from unittest.mock import AsyncMock, patch

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
    """Classification logic in complete(): auth / rate-limit / generic."""

    @pytest.mark.asyncio
    async def test_auth_error_classified_as_llm_002(self):
        client = LLMClient("openai", "key", "gpt-4o")
        client._client.chat.completions.create = AsyncMock(
            side_effect=Exception("Invalid API key provided")
        )
        with pytest.raises(LLMError) as exc_info:
            await client.complete("sys", "user")
        assert exc_info.value.error_code == "LLM_002"

    @pytest.mark.asyncio
    async def test_rate_limit_classified_as_llm_003_retryable(self):
        client = LLMClient("anthropic", "key", "claude-x")
        client._client.messages.create = AsyncMock(
            side_effect=Exception("Rate limit exceeded, retry later")
        )
        with pytest.raises(LLMError) as exc_info:
            await client.complete("sys", "user")
        assert exc_info.value.error_code == "LLM_003"
        assert exc_info.value.retryable is True

    @pytest.mark.asyncio
    async def test_generic_error_classified_as_llm_001(self):
        client = LLMClient("openai", "key", "gpt-4o")
        client._client.chat.completions.create = AsyncMock(
            side_effect=Exception("Internal server error")
        )
        with pytest.raises(LLMError) as exc_info:
            await client.complete("sys", "user")
        assert exc_info.value.error_code == "LLM_001"
        assert exc_info.value.retryable is True


class TestCompleteTimeout:
    @pytest.mark.asyncio
    async def test_timeout_override_is_applied(self):
        """timeout_seconds kwarg must be passed to asyncio.wait_for."""
        from types import SimpleNamespace

        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
        )

        async def fake_wait_for(coro, timeout):
            coro.close()  # prevent "coroutine was never awaited" warning
            fake_wait_for.captured_timeout = timeout
            return response

        client = LLMClient("openai", "key", "gpt-4o", timeout_seconds=30.0)
        client._client.chat.completions.create = AsyncMock(return_value=response)

        with patch("prolog_reasoner.llm_client.asyncio.wait_for", side_effect=fake_wait_for):
            result = await client.complete("sys", "user", timeout_seconds=5.0)

        assert result == "ok"
        assert fake_wait_for.captured_timeout == 5.0
