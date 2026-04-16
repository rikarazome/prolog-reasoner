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
    RuleBaseError,
    TranslationError,
)
from prolog_reasoner.models import (
    ExecutionRequest,
    ExecutionResult,
    RuleBaseInfo,
    TranslationRequest,
    TranslationResult,
)
from prolog_reasoner.reasoner import PrologReasoner
from prolog_reasoner.rule_base import RuleBaseStore

__all__ = [
    "__version__",
    "PrologReasoner",
    "RuleBaseStore",
    "TranslationRequest",
    "TranslationResult",
    "ExecutionRequest",
    "ExecutionResult",
    "RuleBaseInfo",
    "PrologReasonerError",
    "TranslationError",
    "ExecutionError",
    "BackendError",
    "LLMError",
    "ConfigurationError",
    "RuleBaseError",
]
