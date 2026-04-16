"""Public API for prolog-reasoner library."""

import time

from prolog_reasoner.errors import RuleBaseError
from prolog_reasoner.executor import PrologExecutor
from prolog_reasoner.models import (
    ExecutionRequest,
    ExecutionResult,
    TranslationRequest,
    TranslationResult,
)
from prolog_reasoner.rule_base import RuleBaseStore, dedup_names
from prolog_reasoner.translator import PrologTranslator


class PrologReasoner:
    """Public API for prolog-reasoner.

    Entry point for both MCP server (server.py) and standalone library usage.
    translate() and execute() are independent operations; the caller (LLM)
    composes them as needed.
    """

    def __init__(
        self,
        translator: PrologTranslator,
        executor: PrologExecutor,
        rule_base_store: RuleBaseStore | None = None,
    ):
        self.translator = translator
        self.executor = executor
        self.rule_base_store = rule_base_store

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        """Translate natural language to Prolog code.

        Delegates to self.translator.translate_with_correction().
        Passes self.executor for syntax validation.

        When ``request.rule_bases`` is non-empty, ``self.rule_base_store`` is
        forwarded so the translator can expose saved rule bases to the LLM
        prompt. Mirrors the DI contract of ``execute()``: misconfiguration
        raises ValueError.

        LLMError (infrastructure failure) is raised through to the caller.

        Raises:
            ValueError: ``request.rule_bases`` is non-empty but
                ``self.rule_base_store`` is None (DI misconfiguration).
        """
        if request.rule_bases and self.rule_base_store is None:
            raise ValueError(
                "rule_bases specified but PrologReasoner was constructed "
                "without a RuleBaseStore. Pass rule_base_store to "
                "PrologReasoner(...) or remove rule_bases from the request."
            )
        return await self.translator.translate_with_correction(
            query=request.query,
            context=request.context,
            executor=self.executor,
            max_corrections=request.max_corrections,
            rule_bases=request.rule_bases,
            rule_base_store=self.rule_base_store,
        )

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        """Execute Prolog code with a query.

        Resolves ``request.rule_bases`` via ``self.rule_base_store`` before
        delegating to ``self.executor.execute()``. Business-level rule base
        errors (RULEBASE_001/002) are converted to ``ExecutionResult(success=
        False)``; RULEBASE_004 and ``BackendError`` propagate.

        Raises:
            ValueError: ``request.rule_bases`` is non-empty but
                ``self.rule_base_store`` is None (DI misconfiguration).
        """
        resolved: list[tuple[str, str]] = []
        # Timed only when disk I/O actually happens; kept at None otherwise
        # so the executor omits the metadata field.
        rule_base_load_ms: int | None = None
        if request.rule_bases:
            if self.rule_base_store is None:
                raise ValueError(
                    "rule_bases specified but PrologReasoner was constructed "
                    "without a RuleBaseStore. Pass rule_base_store to "
                    "PrologReasoner(...) or remove rule_bases from the request."
                )
            deduped = dedup_names(request.rule_bases)
            load_start = time.monotonic()
            for name in deduped:
                try:
                    resolved.append((name, self.rule_base_store.get(name)))
                except RuleBaseError as exc:
                    if exc.error_code in ("RULEBASE_001", "RULEBASE_002"):
                        return ExecutionResult(
                            success=False,
                            output="",
                            query=request.query,
                            error=str(exc),
                            metadata={"error_code": exc.error_code},
                        )
                    raise  # RULEBASE_004 → infra, propagate
            rule_base_load_ms = int((time.monotonic() - load_start) * 1000)

        return await self.executor.execute(
            prolog_code=request.prolog_code,
            query=request.query,
            rule_base_contents=resolved,
            max_results=request.max_results,
            trace=request.trace,
            rule_base_load_ms=rule_base_load_ms,
        )
