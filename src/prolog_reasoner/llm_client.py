"""LLM API client abstraction for OpenAI and Anthropic."""

import asyncio

from prolog_reasoner.errors import LLMError
from prolog_reasoner.logger import SecureLogger

logger = SecureLogger(__name__)


class LLMClient:
    """Thin abstraction over LLM provider APIs.

    Supports OpenAI and Anthropic via lazy-imported SDKs.
    """

    def __init__(
        self,
        provider: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
    ):
        self._provider = provider
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._client = self._create_client()

    def _create_client(self) -> object:
        """Lazy-import and instantiate the provider SDK client."""
        if self._provider == "openai":
            try:
                from openai import AsyncOpenAI
            except ImportError:
                raise LLMError(
                    "OpenAI SDK not installed. "
                    "Run: pip install prolog-reasoner[openai]",
                    error_code="LLM_002",
                )
            return AsyncOpenAI(api_key=self._api_key)

        elif self._provider == "anthropic":
            try:
                from anthropic import AsyncAnthropic
            except ImportError:
                raise LLMError(
                    "Anthropic SDK not installed. "
                    "Run: pip install prolog-reasoner[anthropic]",
                    error_code="LLM_002",
                )
            return AsyncAnthropic(api_key=self._api_key)

        else:
            raise LLMError(
                f"Unsupported LLM provider: '{self._provider}'. "
                "Supported: 'openai', 'anthropic'",
                error_code="LLM_002",
            )

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        timeout_seconds: float | None = None,
    ) -> str:
        """Execute a text completion request.

        Args:
            system_prompt: System instruction for the LLM.
            user_prompt: User message content.
            temperature: Sampling temperature (0.0 = deterministic).
            timeout_seconds: Override timeout. None uses constructor value.

        Returns:
            LLM response text.

        Raises:
            LLMError: On API communication failure, auth error, or rate limit.
        """
        timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else self._timeout_seconds
        )

        try:
            if self._provider == "openai":
                return await self._complete_openai(
                    system_prompt, user_prompt, temperature, timeout
                )
            else:
                return await self._complete_anthropic(
                    system_prompt, user_prompt, temperature, timeout
                )
        except LLMError:
            raise
        except Exception as exc:
            error_str = str(exc)
            logger.error(f"LLM API error ({self._provider}): {error_str}")

            if "auth" in error_str.lower() or "api key" in error_str.lower():
                raise LLMError(
                    f"Authentication failed for {self._provider}: {error_str}",
                    error_code="LLM_002",
                ) from exc

            if "rate" in error_str.lower() and "limit" in error_str.lower():
                raise LLMError(
                    f"Rate limit exceeded for {self._provider}: {error_str}",
                    error_code="LLM_003",
                    retryable=True,
                ) from exc

            raise LLMError(
                f"LLM API call failed ({self._provider}): {error_str}",
                error_code="LLM_001",
                retryable=True,
            ) from exc

    async def _complete_openai(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        timeout: float,
    ) -> str:
        response = await asyncio.wait_for(
            self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
            ),
            timeout=timeout,
        )
        return response.choices[0].message.content or ""

    async def _complete_anthropic(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        timeout: float,
    ) -> str:
        response = await asyncio.wait_for(
            self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
            ),
            timeout=timeout,
        )
        return response.content[0].text
