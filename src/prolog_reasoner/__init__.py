"""prolog-reasoner: LLM-powered logical reasoning with Prolog."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("prolog-reasoner")
except PackageNotFoundError:
    __version__ = "unknown"

from prolog_reasoner.errors import (
    BackendError,
    ConfigurationError,
    ExecutionError,
    LLMError,
    PrologReasonerError,
    TranslationError,
)
from prolog_reasoner.models import (
    ExecutionRequest,
    ExecutionResult,
    TranslationRequest,
    TranslationResult,
)
from prolog_reasoner.reasoner import PrologReasoner

__all__ = [
    "__version__",
    "PrologReasoner",
    "TranslationRequest",
    "TranslationResult",
    "ExecutionRequest",
    "ExecutionResult",
    "PrologReasonerError",
    "TranslationError",
    "ExecutionError",
    "BackendError",
    "LLMError",
    "ConfigurationError",
]
