"""Natural language to Prolog translation with self-correction."""

import re

from prolog_reasoner.config import Settings
from prolog_reasoner.errors import LLMError, RuleBaseError, TranslationError
from prolog_reasoner.executor import PrologExecutor
from prolog_reasoner.llm_client import LLMClient
from prolog_reasoner.logger import SecureLogger
from prolog_reasoner.models import TranslationResult
from prolog_reasoner.rule_base import RuleBaseStore, dedup_names

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
        # Prompt budget is a separate setting from the per-file save cap:
        # the latter is ~1 MiB, which is too large for an LLM prompt.
        self._max_rule_prompt_bytes = settings.max_rule_prompt_bytes

    async def translate(
        self,
        query: str,
        context: str = "",
        rule_bases_section: str = "",
    ) -> tuple[str, str]:
        """Translate natural language to Prolog code.

        Args:
            query: Natural language question or facts.
            context: Additional premises (natural language).
            rule_bases_section: Optional ``Available rule bases:`` block to
                append to the system prompt (v14). Empty string disables.

        Returns:
            Tuple of (prolog_code, suggested_query).

        Raises:
            TranslationError: If LLM returns empty response (TRANSLATION_001).
            LLMError: On API communication failures.
        """
        user_prompt = query
        if context:
            user_prompt = f"Context: {context}\n\nQuestion: {query}"

        system_prompt = self.SYSTEM_PROMPT
        if rule_bases_section:
            system_prompt = f"{system_prompt}\n\n{rule_bases_section}"

        response = await self._llm.complete(
            system_prompt=system_prompt,
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
        rule_bases: list[str] | None = None,
        rule_base_store: RuleBaseStore | None = None,
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
            rule_bases: Names of rule bases to expose to the LLM via an
                ``Available rule bases:`` section appended to the system
                prompt (v14, design §4.3).
            rule_base_store: RuleBaseStore for resolving ``rule_bases``.
                Required when ``rule_bases`` is non-empty.

        Returns:
            TranslationResult (always, never raises for business errors).

        Raises:
            ValueError: ``rule_bases`` non-empty but ``rule_base_store`` is
                None (programming error).
            LLMError: On API infrastructure failures.
        """
        import time

        start_time = time.monotonic()

        requested_rule_bases = list(rule_bases or [])
        if requested_rule_bases and rule_base_store is None:
            raise ValueError(
                "rule_bases specified but rule_base_store is None. Pass "
                "rule_base_store=... to translate_with_correction."
            )

        rule_bases_section = ""
        rule_bases_truncated = False
        if requested_rule_bases:
            try:
                rule_bases_section, rule_bases_truncated = (
                    self._build_rule_bases_section(
                        requested_rule_bases, rule_base_store
                    )
                )
            except RuleBaseError as exc:
                if exc.error_code in ("RULEBASE_001", "RULEBASE_002"):
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
                raise  # RULEBASE_004 → infra, propagate

        def _metadata(extra: dict) -> dict:
            md: dict = {
                "translation_time_ms": int(
                    (time.monotonic() - start_time) * 1000
                ),
            }
            md.update(extra)
            if rule_bases_truncated:
                md["rule_bases_truncated"] = True
            return md

        try:
            prolog_code, suggested_query = await self.translate(
                query, context, rule_bases_section=rule_bases_section
            )
        except TranslationError as exc:
            return TranslationResult(
                success=False,
                error=str(exc),
                metadata=_metadata({"error_code": exc.error_code}),
            )

        if max_corrections == 0:
            return TranslationResult(
                success=True,
                prolog_code=prolog_code,
                suggested_query=suggested_query,
                metadata=_metadata({"correction_iterations": 0}),
            )

        correction_system_prompt = self.SYSTEM_PROMPT
        if rule_bases_section:
            correction_system_prompt = (
                f"{correction_system_prompt}\n\n{rule_bases_section}"
            )

        for iteration in range(max_corrections):
            syntax_error = await executor.validate_syntax(prolog_code)
            if syntax_error is None:
                return TranslationResult(
                    success=True,
                    prolog_code=prolog_code,
                    suggested_query=suggested_query,
                    metadata=_metadata({"correction_iterations": iteration}),
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
                    system_prompt=correction_system_prompt,
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

        final_error = await executor.validate_syntax(prolog_code)

        if final_error is None:
            return TranslationResult(
                success=True,
                prolog_code=prolog_code,
                suggested_query=suggested_query,
                metadata=_metadata({"correction_iterations": max_corrections}),
            )

        return TranslationResult(
            success=False,
            prolog_code=prolog_code,
            suggested_query=suggested_query,
            error=f"Syntax errors remain after {max_corrections} corrections: {final_error}",
            metadata=_metadata({
                "error_code": "TRANSLATION_002",
                "correction_iterations": max_corrections,
            }),
        )

    _TRUNCATION_MARKER = "\n... [truncated]\n"

    def _build_rule_bases_section(
        self, names: list[str], store: RuleBaseStore
    ) -> tuple[str, bool]:
        """Resolve rule base names into a prompt section. The rule-base
        body (concatenated blocks + join separators) is capped at
        ``self._max_rule_prompt_bytes`` bytes; a fixed header is appended
        unconditionally. Returns ``(section_text, truncated)``.

        The truncation marker and inter-block ``\\n`` separators are both
        reserved from the budget before slicing, so the final body never
        exceeds the cap (design §4.3).

        Raises:
            RuleBaseError: propagated from ``store.get()``.
        """
        deduped = dedup_names(names)
        budget = self._max_rule_prompt_bytes
        marker_bytes = len(self._TRUNCATION_MARKER.encode("utf-8"))
        blocks: list[str] = []
        total_bytes = 0
        truncated = False
        for name in deduped:
            text = store.get(name)
            block = f"### {name}\n{text.rstrip()}\n"
            block_bytes = len(block.encode("utf-8"))
            # "\n".join(blocks) adds one separator byte between blocks;
            # account for it against the budget from the second block on.
            separator_bytes = 1 if blocks else 0
            if total_bytes + separator_bytes + block_bytes > budget:
                # Reserve separator + marker bytes so the slice fits exactly.
                remaining = budget - total_bytes - separator_bytes - marker_bytes
                if remaining > 0:
                    encoded = block.encode("utf-8")[:remaining]
                    safe = encoded.decode("utf-8", errors="ignore")
                    blocks.append(safe + self._TRUNCATION_MARKER)
                    total_bytes += (
                        separator_bytes
                        + len(safe.encode("utf-8"))
                        + marker_bytes
                    )
                truncated = True
                break
            blocks.append(block)
            total_bytes += separator_bytes + block_bytes
        if not blocks:
            if truncated:
                # First rule base alone exceeds budget-minus-marker. Surface
                # this so operators can raise max_rule_prompt_bytes rather
                # than silently shipping an empty section.
                logger.warning(
                    "Rule bases section dropped entirely: first entry exceeds "
                    "max_rule_prompt_bytes=%d (including marker overhead).",
                    budget,
                )
            return "", truncated
        body = "\n".join(blocks)
        section = (
            "Available rule bases (predicates already defined — prefer reusing "
            "their names rather than inventing new ones):\n" + body
        )
        return section, truncated

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
