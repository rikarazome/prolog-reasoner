"""Natural language to Prolog translation with self-correction."""

import re

from prolog_reasoner.config import Settings
from prolog_reasoner.errors import LLMError, TranslationError
from prolog_reasoner.executor import PrologExecutor
from prolog_reasoner.llm_client import LLMClient
from prolog_reasoner.logger import SecureLogger
from prolog_reasoner.models import TranslationResult

logger = SecureLogger(__name__)

_QUERY_COMMENT_PATTERN = re.compile(r"^%\s*Query:\s*(.+)$", re.MULTILINE)

# Matches a fenced code block like ```prolog ... ``` or ``` ... ``` that
# wraps the whole response. LLMs occasionally disobey the "no markdown" rule
# and the fences become lexer errors downstream, so we strip them defensively.
_CODE_FENCE_PATTERN = re.compile(
    r"^\s*```[a-zA-Z]*\s*\n(.*?)\n```\s*$", re.DOTALL
)


def _strip_code_fence(text: str) -> str:
    """Remove a surrounding markdown code fence if present."""
    m = _CODE_FENCE_PATTERN.match(text)
    return m.group(1) if m else text


class PrologTranslator:
    """Translates natural language to Prolog code with self-correction."""

    # Legacy prompt preserved for rollback. Was active through the v0.1.0
    # development; being replaced with a minimal prompt as a controlled
    # experiment to test whether fewer constraints yield better Prolog.
    _LEGACY_SYSTEM_PROMPT = """\
You are a Prolog code generator for SWI-Prolog.
Convert natural language facts and queries into valid Prolog code.

RULES:
- Output ONLY valid Prolog code, no markdown or explanations
- Use lowercase atoms mirroring entity names in the problem (e.g. knight, red); avoid numeric encodings of named entities
- Include a comment "% Query: <query>" indicating the suggested query
- The Query MUST be a single predicate call. If multiple goals are needed, define a wrapper predicate and call that — never put a comma-conjoined goal in the Query
- Place the answer variable (the value being asked for) as the LAST argument of the Query predicate; for yes/no questions use a ground (variable-free) goal
- For arithmetic, use is/2 (or #=/2 under CLP(FD)). Never leave bound variables as raw =/2 expressions — the result must be a numeric value, not an unevaluated term
- Use standard SWI-Prolog predicates; do NOT use write/format/print to wrap results in prose — let the query expose bindings directly
- Use CLP(FD) library (:- use_module(library(clpfd)).) only for INTEGER constraint problems; use permutation/member for non-integer domains"""

    SYSTEM_PROMPT = """\
You are a Prolog code generator for SWI-Prolog.
Convert natural language facts and queries into valid Prolog code.

RULES:
- Output ONLY valid Prolog code, no markdown or explanations
- Use lowercase atoms mirroring entity names in the problem (e.g. knight, red); avoid numeric encodings of named entities
- Include a comment "% Query: <query>" indicating the suggested query
- For arithmetic, use is/2 (or #=/2 under CLP(FD)); never leave bound variables as raw =/2 expressions
- Use standard SWI-Prolog predicates; do NOT use write/format/print to wrap results in prose — let the query expose bindings directly
- Use CLP(FD) library (:- use_module(library(clpfd)).) only for INTEGER constraint problems; use permutation/member for non-integer domains"""

    _CORRECTION_PROMPT_TEMPLATE = """\
The following Prolog code has a syntax error. Fix it.

CODE:
{code}

ERROR:
{error}

Output ONLY the corrected Prolog code, no markdown or explanations.
Keep the "% Query: <query>" comment."""

    def __init__(self, llm_client: LLMClient, settings: Settings):
        self._llm = llm_client
        self._temperature = settings.llm_temperature

    async def translate(
        self, query: str, context: str = ""
    ) -> tuple[str, str]:
        """Translate natural language to Prolog code.

        Args:
            query: Natural language question or facts.
            context: Additional premises (natural language).

        Returns:
            Tuple of (prolog_code, suggested_query).

        Raises:
            TranslationError: If LLM returns empty response (TRANSLATION_001).
            LLMError: On API communication failures.
        """
        user_prompt = query
        if context:
            user_prompt = f"Context: {context}\n\nQuestion: {query}"

        response = await self._llm.complete(
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=self._temperature,
        )

        prolog_code = _strip_code_fence(response.strip()).strip()
        if not prolog_code:
            raise TranslationError(
                "LLM returned empty response",
                error_code="TRANSLATION_001",
            )

        suggested_query = self._extract_query(prolog_code)
        return prolog_code, suggested_query

    async def translate_with_correction(
        self,
        query: str,
        context: str,
        executor: PrologExecutor,
        max_corrections: int,
    ) -> TranslationResult:
        """Translate with syntax validation and self-correction loop.

        1. Generate Prolog via translate()
        2. Validate syntax via executor.validate_syntax()
        3. On error, re-translate with error feedback
        4. Repeat up to max_corrections times

        Args:
            query: Natural language question.
            context: Additional premises.
            executor: PrologExecutor for syntax validation.
            max_corrections: Max correction attempts (0 disables validation).

        Returns:
            TranslationResult (always, never raises for business errors).

        Raises:
            LLMError: On API infrastructure failures.
        """
        import time

        start_time = time.monotonic()

        try:
            prolog_code, suggested_query = await self.translate(query, context)
        except TranslationError as exc:
            return TranslationResult(
                success=False,
                error=str(exc),
                metadata={
                    "error_code": exc.error_code,
                    "translation_time_ms": int(
                        (time.monotonic() - start_time) * 1000
                    ),
                },
            )

        if max_corrections == 0:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            return TranslationResult(
                success=True,
                prolog_code=prolog_code,
                suggested_query=suggested_query,
                metadata={
                    "correction_iterations": 0,
                    "translation_time_ms": elapsed_ms,
                },
            )

        for iteration in range(max_corrections):
            syntax_error = await executor.validate_syntax(prolog_code)
            if syntax_error is None:
                elapsed_ms = int((time.monotonic() - start_time) * 1000)
                return TranslationResult(
                    success=True,
                    prolog_code=prolog_code,
                    suggested_query=suggested_query,
                    metadata={
                        "correction_iterations": iteration,
                        "translation_time_ms": elapsed_ms,
                    },
                )

            logger.info(
                f"Syntax error on iteration {iteration + 1}/{max_corrections}: "
                f"{syntax_error[:100]}"
            )

            correction_prompt = self._CORRECTION_PROMPT_TEMPLATE.format(
                code=prolog_code, error=syntax_error
            )

            try:
                response = await self._llm.complete(
                    system_prompt=self.SYSTEM_PROMPT,
                    user_prompt=correction_prompt,
                    temperature=self._temperature,
                )
            except TranslationError:
                break

            corrected = _strip_code_fence(response.strip()).strip()
            if not corrected:
                break

            prolog_code = corrected
            suggested_query = self._extract_query(prolog_code)

        # Final validation after all corrections
        final_error = await executor.validate_syntax(prolog_code)
        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        if final_error is None:
            return TranslationResult(
                success=True,
                prolog_code=prolog_code,
                suggested_query=suggested_query,
                metadata={
                    "correction_iterations": max_corrections,
                    "translation_time_ms": elapsed_ms,
                },
            )

        return TranslationResult(
            success=False,
            prolog_code=prolog_code,
            suggested_query=suggested_query,
            error=f"Syntax errors remain after {max_corrections} corrections: {final_error}",
            metadata={
                "error_code": "TRANSLATION_002",
                "correction_iterations": max_corrections,
                "translation_time_ms": elapsed_ms,
            },
        )

    @staticmethod
    def _extract_query(prolog_code: str) -> str:
        """Extract suggested query from '% Query: <query>' comment.

        When multiple '% Query:' comments are present, the LAST one wins —
        LLMs sometimes emit a natural-language paraphrase of the question
        before the actual executable goal, and the final comment represents
        the LLM's committed query.

        Strips trailing period, question mark, and surrounding whitespace
        to prevent syntax errors when the query is embedded in the wrapper.
        """
        matches = _QUERY_COMMENT_PATTERN.findall(prolog_code)
        if not matches:
            return ""
        query = matches[-1].strip()
        # Strip trailing prose markers ("." or "?") that LLMs often append
        query = query.rstrip(".?").rstrip()
        return query
