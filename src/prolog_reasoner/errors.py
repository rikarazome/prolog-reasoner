"""Exception hierarchy for prolog-reasoner."""


class PrologReasonerError(Exception):
    """Base exception. All prolog-reasoner errors inherit from this."""

    def __init__(self, message: str, error_code: str, retryable: bool = False):
        self.error_code = error_code
        self.retryable = retryable
        super().__init__(message)


class TranslationError(PrologReasonerError):
    """NL to Prolog translation failure.

    Internal use. Public API returns TranslationResult(success=False) instead.
    error_code: TRANSLATION_001 (empty LLM response)
    """


class ExecutionError(PrologReasonerError):
    """Prolog execution error.

    Internal use. Public API returns ExecutionResult(success=False) instead.
    error_code: EXEC_001 (syntax error), EXEC_002 (timeout), EXEC_003 (process crash)
    """


class BackendError(PrologReasonerError):
    """SWI-Prolog unavailable at runtime.

    error_code: BACKEND_001
    """


class LLMError(PrologReasonerError):
    """LLM API call failure.

    error_code: LLM_001 (network), LLM_002 (auth), LLM_003 (rate limit)
    retryable: True for LLM_001, LLM_003
    """


class ConfigurationError(PrologReasonerError):
    """Invalid configuration.

    error_code: CONFIG_001
    """
