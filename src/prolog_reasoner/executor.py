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

# Meta-interpreter prepended to user code when trace=True.
# Generates structured proof terms via clause/2 introspection. Uses '$pr_prove'
# as predicate name to minimize collision risk with user code.
# The defined-guard + cut on the clause/2 branch is critical: without it,
# Prolog falls through to the opaque branch after clause/2 exhaustion and
# every defined predicate's solutions are duplicated as opaque(...).
_META_INTERPRETER = """\
'$pr_prove'(true, true) :- !.
'$pr_prove'((A,B), (PA,PB)) :- !, '$pr_prove'(A, PA), '$pr_prove'(B, PB).
'$pr_prove'((A;B), P) :- !, ( '$pr_prove'(A, P) ; '$pr_prove'(B, P) ).
'$pr_prove'(\\+ G, negation_as_failure(G)) :- !, \\+ call(G).
'$pr_prove'(G, clpfd_constraint(G)) :-
    catch(predicate_property(G, imported_from(clpfd)), _, fail), !,
    call(G).
'$pr_prove'(G, builtin(G)) :- predicate_property(G, built_in), !, call(G).
'$pr_prove'(G, proof(G, Body)) :-
    predicate_property(G, defined), !,
    clause(G, B),
    '$pr_prove'(B, Body).
'$pr_prove'(G, opaque(G)) :- call(G)."""

# Trace-mode wrapper: emits one display line + one __PR_PROOF__: line per
# solution. Counter logic mirrors _QUERY_WRAPPER_TEMPLATE.
_TRACE_QUERY_WRAPPER_TEMPLATE = """\
:- nb_setval('__pr_count', 0).
:- ( '$pr_prove'((__QUERY__), __PR_PROOF__),
     nb_getval('__pr_count', __PR_N),
     ( __PR_N >= __MAX_RESULTS__
     -> (write('__TRUNCATED__'), nl, !)
     ;  (__PR_N1 is __PR_N + 1,
         nb_setval('__pr_count', __PR_N1),
         write_canonical((__QUERY__)), nl,
         write('__PR_PROOF__:'),
         write_canonical(__PR_PROOF__), nl,
         fail)
     )
   ; true
   ),
   nb_getval('__pr_count', __PR_Final),
   ( __PR_Final =:= 0 -> write(false), nl ; true ),
   halt(0).
:- halt(1)."""

_PROOF_PREFIX = "__PR_PROOF__:"

# clpfd operator prelude. `use_module(library(clpfd))` is *not* executed
# during parse-only validation (too broad a blast radius — arbitrary code
# would run). Instead we pre-declare the library's operator table so that
# code containing `X #< Y`, `X in 1..5`, etc. parses cleanly. Operator
# priorities/associativities mirror SWI-Prolog's library(clpfd) source; op/3
# is idempotent so re-declaration by user code is harmless.
_CLPFD_OPS_PRELUDE = """\
:- op(760, yfx, #<==>).
:- op(750, xfy, #==>).
:- op(750, yfx, #<==).
:- op(740, yfx, #\\/).
:- op(730, yfx, #\\).
:- op(720, yfx, #/\\).
:- op(710,  fy, #\\).
:- op(700, xfx, #>).
:- op(700, xfx, #<).
:- op(700, xfx, #>=).
:- op(700, xfx, #=<).
:- op(700, xfx, #=).
:- op(700, xfx, #\\=).
:- op(700, xfx, in).
:- op(700, xfx, ins).
:- op(450, xfx, ..).
"""

# Parse-only syntax validator script (v14). Reads each term from the user
# content file without executing any directive except op/3 (needed so that
# user-defined operators are visible to subsequent reads). See design §4.4.
# A clpfd operator prelude is applied up-front so that common constraint
# code parses even though :- use_module is not executed.
# The user file path is substituted in via str.replace on __USER_FILE__.
_PARSE_ONLY_SCRIPT_TEMPLATE = """\
:- set_prolog_flag(verbose, silent).

""" + _CLPFD_OPS_PRELUDE + """\

parse_file(Path) :-
    setup_call_cleanup(
        open(Path, read, S, [encoding(utf8)]),
        parse_terms(S),
        close(S)
    ).

parse_terms(S) :-
    read_term(S, T, []),
    ( T == end_of_file
    -> true
    ;  apply_safe_directive(T),
       parse_terms(S)
    ).

apply_safe_directive((:- op(P, A, Ops))) :- !, op(P, A, Ops).
apply_safe_directive(_).

:- catch(
     parse_file('__USER_FILE__'),
     Err,
     ( print_message(error, Err),
       halt(1)
     )
   ).
:- halt(0).
"""


def _parse_trace_output(stdout: str) -> tuple[str, list[str]]:
    """Split trace-mode stdout into (display_output, proof_traces).

    Lines prefixed with __PR_PROOF__: are extracted into the proof list
    (preserving order); everything else is preserved as user-visible output.
    """
    display_lines: list[str] = []
    proofs: list[str] = []
    for line in stdout.splitlines():
        if line.startswith(_PROOF_PREFIX):
            proofs.append(line[len(_PROOF_PREFIX):])
        else:
            display_lines.append(line)
    display = "\n".join(display_lines)
    if display_lines:
        display += "\n"
    return display, proofs


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


def _classify_error_with_trace(stderr_text: str) -> tuple[str, str]:
    """Classify trace-mode errors, preserving user-level categories.

    The meta-interpreter's call chain places `'$pr_prove'` in stderr for any
    error raised during user-code execution, so a naive substring check would
    misclassify every user error as a trace-mechanism bug. We only upgrade
    to trace_mechanism_error when the standard classifier returned 'unknown'.
    """
    category, explanation = _classify_error(stderr_text)
    if category == "unknown" and "$pr_prove" in stderr_text:
        return (
            "trace_mechanism_error",
            "An internal error occurred in the proof trace mechanism. "
            "This is likely a bug in prolog-reasoner. Please report it "
            "along with the Prolog code and query.",
        )
    return category, explanation


class PrologExecutor:
    """Executes Prolog code via SWI-Prolog subprocess."""

    def __init__(self, settings: Settings):
        self._swipl_path = settings.swipl_path
        self._default_timeout = settings.execution_timeout_seconds

    async def execute(
        self,
        prolog_code: str,
        query: str,
        rule_base_contents: list[tuple[str, str]] | None = None,
        max_results: int = 100,
        timeout_seconds: float | None = None,
        trace: bool = False,
        rule_base_load_ms: int | None = None,
    ) -> ExecutionResult:
        """Execute Prolog code with a query and return results.

        Args:
            prolog_code: Prolog facts and rules.
            query: Prolog query to execute.
            rule_base_contents: Pre-resolved rule base contents as
                (name, prolog_text) pairs. Name resolution and dedup are
                the caller's responsibility; executor only concatenates
                the texts in order and records the names in metadata.
            max_results: Maximum number of results (runaway prevention).
            timeout_seconds: Override default timeout. None uses settings value.
            trace: When True, return structured proof trees for each solution
                in metadata["proof_trace"]. Adds meta-interpreter overhead.
            rule_base_load_ms: Caller-measured time spent resolving rule base
                names to content (disk I/O + any parsing). The executor never
                measures this itself because rule base contents arrive already
                resolved; only the caller (server.py / reasoner.py) sees the
                ``store.get()`` boundary. When ``None`` the metadata field is
                omitted entirely.

        Returns:
            ExecutionResult with output text and metadata.
            BackendError is raised only for infrastructure failures
            (SWI-Prolog cannot start).
        """
        timeout = timeout_seconds if timeout_seconds is not None else self._default_timeout

        wrapper_template = (
            _TRACE_QUERY_WRAPPER_TEMPLATE if trace else _QUERY_WRAPPER_TEMPLATE
        )
        wrapper = (
            wrapper_template
            .replace("__QUERY__", query)
            .replace("__MAX_RESULTS__", str(max_results))
        )

        rule_base_names: list[str] = []
        rule_base_blocks: list[str] = []
        for name, text in rule_base_contents or []:
            rule_base_names.append(name)
            rule_base_blocks.append(f"%% --- rule_base: {name} ---\n{text}")
        rule_base_section = (
            "\n".join(rule_base_blocks) + "\n" if rule_base_blocks else ""
        )

        if trace:
            prolog_input = (
                _UTF8_HEADER + "\n"
                + _META_INTERPRETER + "\n"
                + rule_base_section
                + prolog_code + "\n"
                + wrapper
            )
        else:
            prolog_input = (
                _UTF8_HEADER + "\n"
                + rule_base_section
                + prolog_code + "\n"
                + wrapper
            )

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

            classify = _classify_error_with_trace if trace else _classify_error

            if proc.returncode != 0:
                logger.error(
                    f"SWI-Prolog exited with code {proc.returncode}: "
                    f"{stderr_text[:200]}"
                )
                category, explanation = classify(stderr_text)
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
                category, explanation = classify(stderr_text)
                # Strip proof lines from user-visible output even on error.
                error_output = (
                    _parse_trace_output(stdout_text)[0] if trace else stdout_text
                )
                return ExecutionResult(
                    success=False,
                    output=error_output,
                    query=query,
                    error=stderr_text,
                    metadata={
                        "error_code": "EXEC_001",
                        "error_category": category,
                        "error_explanation": explanation,
                        "execution_time_ms": elapsed_ms,
                    },
                )

            if trace:
                display_text, proof_traces = _parse_trace_output(stdout_text)
            else:
                display_text = stdout_text
                proof_traces = None

            truncated = display_text.rstrip().endswith(_TRUNCATED_MARKER)
            result_count = self._count_results(display_text)

            warnings = [
                line for line in stderr_text.splitlines() if line.strip()
            ] if stderr_text.strip() else []

            metadata: dict = {
                "backend": "subprocess",
                "execution_time_ms": elapsed_ms,
                "result_count": result_count,
                "truncated": truncated,
                "rule_bases_used": rule_base_names,
            }
            if rule_base_load_ms is not None:
                metadata["rule_base_load_ms"] = rule_base_load_ms
            if warnings:
                metadata["prolog_warnings"] = warnings
            if trace:
                metadata["proof_trace"] = proof_traces

            return ExecutionResult(
                success=True,
                output=display_text,
                query=query,
                metadata=metadata,
            )
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    async def validate_syntax(self, prolog_code: str) -> str | None:
        """Parse-only Prolog syntax check (v14).

        Uses ``read_term/3`` to read every term from ``prolog_code`` without
        executing directives (``op/3`` is the only exception — needed so
        that user-defined operators defined *inside the same file* are
        visible to subsequent reads). See design §4.4.

        Contract changes from v13 (consult-based):

        * ``:- use_module(...)`` / ``:- initialization(...)`` / other
          directive *runtime* errors are no longer detected; only true
          parse errors surface here.
        * Operators imported from libraries are **not** available during
          parsing in general. As an exception, the validator pre-declares
          ``library(clpfd)`` operators (``#<``, ``#=``, ``in``, ``ins``,
          ``..``, etc.) so that typical constraint code parses correctly
          without ``:- use_module`` being executed. Other libraries
          (e.g. ``library(dif)`` with custom operators, third-party
          libraries) are still not covered; callers relying on validation
          for those must either (a) declare the operators explicitly via
          ``:- op(...).`` at the top of the file or (b) skip validation.

        Returns:
            Error message string if syntax errors found, None if valid.
        """
        user_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".pl", encoding="utf-8", delete=False,
        )
        script_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".pl", encoding="utf-8", delete=False,
        )
        try:
            try:
                user_file.write(prolog_code)
                # Terminator newline so a trailing comment without \n still
                # parses; harmless if already newline-terminated.
                if not prolog_code.endswith("\n"):
                    user_file.write("\n")
                user_file.close()

                # Use forward-slash path so SWI-Prolog's single-quoted
                # atom parses the same on Windows and POSIX.
                user_path_posix = user_file.name.replace("\\", "/")
                script = _PARSE_ONLY_SCRIPT_TEMPLATE.replace(
                    "__USER_FILE__", user_path_posix
                )
                script_file.write(script)
                script_file.close()

                proc = await self._start_swipl(script_file.name)
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
            if proc.returncode not in (0, None):
                return stderr_text or (
                    f"Parse-only validator exited with code {proc.returncode}"
                )
            return None
        finally:
            for f in (user_file, script_file):
                try:
                    os.unlink(f.name)
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
