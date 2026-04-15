"""Prolog execution engine using SWI-Prolog subprocess."""

import asyncio
import os
import re
import tempfile
import time

from prolog_reasoner.config import Settings
from prolog_reasoner.errors import BackendError
from prolog_reasoner.logger import SecureLogger
from prolog_reasoner.models import ExecutionResult

logger = SecureLogger(__name__)

# Prepended to all Prolog input: force UTF-8 streams, suppress banners.
_UTF8_HEADER = """\
:- set_stream(user_input, encoding(utf8)).
:- set_stream(user_output, encoding(utf8)).
:- set_prolog_flag(verbose, silent)."""

# Template for the query execution wrapper.
# Placeholders __QUERY__ and __MAX_RESULTS__ are replaced via str.replace().
_QUERY_WRAPPER_TEMPLATE = """\
:- nb_setval('__pr_count', 0).
:- ( __QUERY__,
     nb_getval('__pr_count', __PR_N),
     ( __PR_N >= __MAX_RESULTS__
     -> (write('__TRUNCATED__'), nl, !)
     ;  (__PR_N1 is __PR_N + 1,
         nb_setval('__pr_count', __PR_N1),
         write_canonical((__QUERY__)), nl,
         fail)
     )
   ; true
   ),
   nb_getval('__pr_count', __PR_Final),
   ( __PR_Final =:= 0 -> write(false), nl ; true ),
   halt(0).
:- halt(1)."""

_TRUNCATED_MARKER = "__TRUNCATED__"

# Error classification patterns. Each entry: (regex, category, explanation template).
# The first capture group (if any) is substituted into `{match}` in the template.
# Order matters: check syntax errors first since they can cascade into other errors.
_ERROR_CLASSIFIERS: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(r"[Ss]yntax error"),
        "syntax_error",
        "The Prolog code has a syntax error. Common causes: missing period at "
        "end of a clause, unbalanced parentheses, misused operator, or invalid "
        "characters. Check the referenced line in the raw error message.",
    ),
    (
        re.compile(r"Unknown procedure:\s*(\S+)"),
        "undefined_predicate",
        "The predicate `{match}` is not defined. Check for typos in the "
        "predicate name, a missing rule definition, or a required module "
        "import (e.g. `:- use_module(library(clpfd)).`).",
    ),
    (
        re.compile(r"existence_error\(procedure,\s*([^)]+)\)"),
        "undefined_predicate",
        "The predicate `{match}` is not defined. Check for typos, missing "
        "rule definitions, or required module imports.",
    ),
    (
        re.compile(r"Arguments are not sufficiently instantiated"),
        "unbound_variable",
        "A variable used by a built-in (arithmetic, comparison, or CLP(FD)) "
        "is still unbound. For CLP(FD), add `label([Vars])` to enumerate "
        "solutions; for arithmetic, ensure operands are ground before `is/2`.",
    ),
    (
        re.compile(r"instantiation_error"),
        "unbound_variable",
        "A required argument is an unbound variable. Ensure all variables "
        "are bound before the operation.",
    ),
    (
        re.compile(r"type_error\(([^,)]+)"),
        "type_error",
        "An argument had the wrong type (expected `{match}`). Verify "
        "argument types match the predicate's requirements.",
    ),
    (
        re.compile(r"domain_error\(([^,)]+)"),
        "domain_error",
        "An argument was the correct type but outside the valid domain "
        "(`{match}`). Check allowed value ranges for this predicate.",
    ),
    (
        re.compile(r"evaluation_error\(zero_divisor\)"),
        "evaluation_error",
        "Division by zero during arithmetic evaluation.",
    ),
    (
        re.compile(r"evaluation_error"),
        "evaluation_error",
        "Arithmetic evaluation failed. Possible causes: division by zero, "
        "invalid operand, or numeric overflow.",
    ),
    (
        re.compile(r"permission_error"),
        "permission_error",
        "An operation was attempted that is not permitted (e.g. modifying "
        "a static predicate or accessing a protected resource).",
    ),
]

_UNKNOWN_EXPLANATION = (
    "Prolog raised an error that did not match any known category. "
    "See the `error` field for the raw message."
)


def _classify_error(error_text: str) -> tuple[str, str]:
    """Classify a SWI-Prolog error message.

    Returns:
        (category, explanation). Falls back to ("unknown", default_message)
        when no pattern matches.
    """
    for pattern, category, template in _ERROR_CLASSIFIERS:
        match = pattern.search(error_text)
        if match:
            captured = match.group(1) if match.groups() else ""
            return category, template.format(match=captured)
    return "unknown", _UNKNOWN_EXPLANATION


class PrologExecutor:
    """Executes Prolog code via SWI-Prolog subprocess."""

    def __init__(self, settings: Settings):
        self._swipl_path = settings.swipl_path
        self._default_timeout = settings.execution_timeout_seconds

    async def execute(
        self,
        prolog_code: str,
        query: str,
        max_results: int = 100,
        timeout_seconds: float | None = None,
    ) -> ExecutionResult:
        """Execute Prolog code with a query and return results.

        Args:
            prolog_code: Prolog facts and rules.
            query: Prolog query to execute.
            max_results: Maximum number of results (runaway prevention).
            timeout_seconds: Override default timeout. None uses settings value.

        Returns:
            ExecutionResult with output text and metadata.
            BackendError is raised only for infrastructure failures
            (SWI-Prolog cannot start).
        """
        timeout = timeout_seconds if timeout_seconds is not None else self._default_timeout

        wrapper = (
            _QUERY_WRAPPER_TEMPLATE
            .replace("__QUERY__", query)
            .replace("__MAX_RESULTS__", str(max_results))
        )
        prolog_input = _UTF8_HEADER + "\n" + prolog_code + "\n" + wrapper

        start_time = time.monotonic()

        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".pl", encoding="utf-8", delete=False,
        )
        try:
            try:
                tmp.write(prolog_input)
                tmp.close()
                proc = await self._start_swipl(tmp.name)
            except Exception as exc:
                raise BackendError(
                    f"Failed to start SWI-Prolog: {exc}",
                    error_code="BACKEND_001",
                ) from exc

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                elapsed = time.monotonic() - start_time
                logger.warning(f"Prolog execution timed out after {elapsed:.1f}s")
                return ExecutionResult(
                    success=False,
                    output="",
                    query=query,
                    error=f"Prolog execution timed out after {timeout}s",
                    metadata={
                        "error_code": "EXEC_002",
                        "error_category": "timeout",
                        "error_explanation": (
                            f"Execution exceeded the {timeout}s time limit. "
                            "This usually indicates an infinite recursion, a "
                            "missing base case, or a search space too large "
                            "for the timeout. Check for left-recursive rules "
                            "and ensure termination."
                        ),
                    },
                )

            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            stdout_text = stdout.decode("utf-8")
            stderr_text = stderr.decode("utf-8")

            has_prolog_error = any(
                "ERROR:" in line for line in stderr_text.splitlines()
            )

            if proc.returncode != 0:
                logger.error(
                    f"SWI-Prolog exited with code {proc.returncode}: "
                    f"{stderr_text[:200]}"
                )
                category, explanation = _classify_error(stderr_text)
                return ExecutionResult(
                    success=False,
                    output="",
                    query=query,
                    error=stderr_text or f"SWI-Prolog exited with code {proc.returncode}",
                    metadata={
                        "error_code": "EXEC_003",
                        "error_category": category,
                        "error_explanation": explanation,
                        "execution_time_ms": elapsed_ms,
                    },
                )

            if has_prolog_error:
                category, explanation = _classify_error(stderr_text)
                return ExecutionResult(
                    success=False,
                    output=stdout_text,
                    query=query,
                    error=stderr_text,
                    metadata={
                        "error_code": "EXEC_001",
                        "error_category": category,
                        "error_explanation": explanation,
                        "execution_time_ms": elapsed_ms,
                    },
                )

            truncated = stdout_text.rstrip().endswith(_TRUNCATED_MARKER)
            result_count = self._count_results(stdout_text)

            warnings = [
                line for line in stderr_text.splitlines() if line.strip()
            ] if stderr_text.strip() else []

            metadata: dict = {
                "backend": "subprocess",
                "execution_time_ms": elapsed_ms,
                "result_count": result_count,
                "truncated": truncated,
            }
            if warnings:
                metadata["prolog_warnings"] = warnings

            return ExecutionResult(
                success=True,
                output=stdout_text,
                query=query,
                metadata=metadata,
            )
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    async def validate_syntax(self, prolog_code: str) -> str | None:
        """Check Prolog code for syntax errors via SWI-Prolog consult.

        Directives (e.g. :- use_module(...)) ARE executed as side effects.
        This is acceptable for a local tool (see §5.1).

        Returns:
            Error message string if syntax errors found, None if valid.
        """
        code = _UTF8_HEADER + "\n" + prolog_code + "\n:- halt(0).\n"

        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".pl", encoding="utf-8", delete=False,
        )
        try:
            try:
                tmp.write(code)
                tmp.close()
                proc = await self._start_swipl(tmp.name)
            except Exception as exc:
                raise BackendError(
                    f"Failed to start SWI-Prolog: {exc}",
                    error_code="BACKEND_001",
                ) from exc

            try:
                _, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=self._default_timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return f"Syntax check timed out after {self._default_timeout}s"

            stderr_text = stderr.decode("utf-8")
            error_lines = [
                line for line in stderr_text.splitlines() if "ERROR:" in line
            ]
            if error_lines:
                return "\n".join(error_lines)

            return None
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    async def _start_swipl(self, script_path: str) -> asyncio.subprocess.Process:
        """Start a SWI-Prolog subprocess loading a script file."""
        return await asyncio.create_subprocess_exec(
            self._swipl_path,
            "-f", "none",
            "-q",
            "-l", script_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "LANG": "C.UTF-8"},
        )

    @staticmethod
    def _count_results(stdout_text: str) -> int:
        """Count result lines, excluding __TRUNCATED__ and bare 'false'."""
        count = 0
        for line in stdout_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped == _TRUNCATED_MARKER:
                continue
            if stripped == "false":
                continue
            count += 1
        return count
