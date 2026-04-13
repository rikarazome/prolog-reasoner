"""Public API for prolog-reasoner library."""

from prolog_reasoner.executor import PrologExecutor
from prolog_reasoner.models import (
    ExecutionRequest,
    ExecutionResult,
    TranslationRequest,
    TranslationResult,
)
from prolog_reasoner.translator import PrologTranslator


class PrologReasoner:
    """Public API for prolog-reasoner.

    Entry point for both MCP server (server.py) and standalone library usage.
    translate() and execute() are independent operations; the caller (LLM)
    composes them as needed.
    """

    def __init__(self, translator: PrologTranslator, executor: PrologExecutor):
        self.translator = translator
        self.executor = executor

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        """Translate natural language to Prolog code.

        Delegates to self.translator.translate_with_correction().
        Passes self.executor for syntax validation.

        LLMError (infrastructure failure) is raised through to the caller.
        """
        return await self.translator.translate_with_correction(
            query=request.query,
            context=request.context,
            executor=self.executor,
            max_corrections=request.max_corrections,
        )

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        """Execute Prolog code with a query.

        Delegates to self.executor.execute().

        BackendError (infrastructure failure) is raised through to the caller.
        """
        return await self.executor.execute(
            prolog_code=request.prolog_code,
            query=request.query,
            max_results=request.max_results,
        )
