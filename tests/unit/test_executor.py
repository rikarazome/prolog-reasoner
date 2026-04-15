"""Unit tests for PrologExecutor.

Requires SWI-Prolog installed (run in Docker).
"""

import pytest

from prolog_reasoner.config import Settings
from prolog_reasoner.errors import BackendError
from prolog_reasoner.executor import PrologExecutor, _classify_error


@pytest.fixture
def executor():
    settings = Settings(
        llm_api_key="dummy",
        swipl_path="swipl",
        execution_timeout_seconds=5.0,
    )
    return PrologExecutor(settings)


class TestExecute:
    @pytest.mark.asyncio
    async def test_simple_query(self, executor):
        code = "human(socrates). human(plato)."
        result = await executor.execute(code, "human(X)")
        assert result.success is True
        assert "socrates" in result.output
        assert "plato" in result.output
        assert result.metadata["result_count"] == 2
        assert result.metadata["truncated"] is False

    @pytest.mark.asyncio
    async def test_rule_and_query(self, executor):
        code = "human(socrates). mortal(X) :- human(X)."
        result = await executor.execute(code, "mortal(X)")
        assert result.success is True
        assert "socrates" in result.output
        assert result.metadata["result_count"] == 1

    @pytest.mark.asyncio
    async def test_no_results(self, executor):
        code = "human(socrates)."
        result = await executor.execute(code, "human(plato)")
        assert result.success is True
        assert "false" in result.output
        assert result.metadata["result_count"] == 0

    @pytest.mark.asyncio
    async def test_syntax_error(self, executor):
        code = "human(socrates"  # Missing period and paren
        result = await executor.execute(code, "human(X)")
        assert result.success is False
        assert result.error is not None
        assert "ERROR" in result.error  # Verify actual Prolog error message
        assert result.metadata.get("error_code") in ("EXEC_001", "EXEC_003")

    @pytest.mark.asyncio
    async def test_timeout(self, executor):
        code = "loop :- loop."
        result = await executor.execute(code, "loop", timeout_seconds=1.0)
        assert result.success is False
        assert "timed out" in result.error

    @pytest.mark.asyncio
    async def test_max_results_truncation(self, executor):
        code = "num(1). num(2). num(3). num(4). num(5)."
        result = await executor.execute(code, "num(X)", max_results=3)
        assert result.success is True
        assert result.metadata["truncated"] is True
        assert result.metadata["result_count"] == 3

    @pytest.mark.asyncio
    async def test_clpfd(self, executor):
        code = ":- use_module(library(clpfd)).\nsolve(X) :- X in 1..5, X #> 3, label([X])."
        result = await executor.execute(code, "solve(X)")
        assert result.success is True
        assert "4" in result.output
        assert "5" in result.output

    @pytest.mark.asyncio
    async def test_unicode(self, executor):
        code = 'greeting("こんにちは").'
        result = await executor.execute(code, 'greeting(X)')
        assert result.success is True
        assert "こんにちは" in result.output

    @pytest.mark.asyncio
    async def test_compound_goal_query(self, executor):
        """Query containing top-level comma must not be parsed as write_canonical/2.

        Regression: when __QUERY__ substitutes a goal like `human(X), mortal(X)`
        into write_canonical(__QUERY__), SWI-Prolog reads it as a 2-arg call
        expecting a stream alias as the first argument and raises a domain
        error. The wrapper must wrap the goal in parens to preserve it as a
        single comma-term argument.
        """
        code = "human(socrates). human(plato). mortal(X) :- human(X)."
        result = await executor.execute(code, "human(X), mortal(X)")
        assert result.success is True
        assert "socrates" in result.output
        assert "plato" in result.output
        assert result.metadata["result_count"] == 2


class TestValidateSyntax:
    @pytest.mark.asyncio
    async def test_valid_code(self, executor):
        code = "human(socrates). mortal(X) :- human(X)."
        error = await executor.validate_syntax(code)
        assert error is None

    @pytest.mark.asyncio
    async def test_invalid_code(self, executor):
        code = "human(socrates"
        error = await executor.validate_syntax(code)
        assert error is not None
        assert "ERROR" in error

    @pytest.mark.asyncio
    async def test_directive_executed(self, executor):
        code = ":- use_module(library(clpfd)).\nsolve(X) :- X in 1..5."
        error = await executor.validate_syntax(code)
        assert error is None


class TestBackendError:
    @pytest.mark.asyncio
    async def test_invalid_swipl_path_execute(self):
        """BackendError raised when swipl binary doesn't exist."""
        settings = Settings(
            llm_api_key="dummy",
            swipl_path="/nonexistent/swipl",
        )
        executor = PrologExecutor(settings)
        with pytest.raises(BackendError) as exc_info:
            await executor.execute("human(X).", "human(X)")
        assert exc_info.value.error_code == "BACKEND_001"

    @pytest.mark.asyncio
    async def test_invalid_swipl_path_validate(self):
        """BackendError raised in validate_syntax with bad path."""
        settings = Settings(
            llm_api_key="dummy",
            swipl_path="/nonexistent/swipl",
        )
        executor = PrologExecutor(settings)
        with pytest.raises(BackendError) as exc_info:
            await executor.validate_syntax("human(socrates).")
        assert exc_info.value.error_code == "BACKEND_001"


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_max_results_one(self, executor):
        """Boundary: max_results=1 truncates after first result."""
        code = "num(1). num(2). num(3)."
        result = await executor.execute(code, "num(X)", max_results=1)
        assert result.success is True
        assert result.metadata["result_count"] == 1
        assert result.metadata["truncated"] is True

    @pytest.mark.asyncio
    async def test_empty_code_with_query(self, executor):
        """No matching facts — query for undefined predicate returns error."""
        result = await executor.execute("% empty", "human(X)")
        assert result.success is False
        assert "Unknown procedure" in result.error

    @pytest.mark.asyncio
    async def test_multiple_queries_same_executor(self, executor):
        """Sequential queries on same executor don't interfere."""
        r1 = await executor.execute("a(1).", "a(X)")
        r2 = await executor.execute("b(2).", "b(X)")
        assert r1.success is True
        assert r2.success is True
        assert "1" in r1.output
        assert "2" in r2.output
        # Ensure no cross-contamination
        assert "2" not in r1.output
        assert "1" not in r2.output


class TestClassifyError:
    """Unit tests for the pattern-based error classifier."""

    def test_undefined_predicate_unknown_procedure(self):
        text = "ERROR: /tmp/foo.pl:14: animal/1: Unknown procedure: cat/1"
        category, explanation = _classify_error(text)
        assert category == "undefined_predicate"
        assert "cat/1" in explanation

    def test_undefined_predicate_existence_error(self):
        text = "ERROR: existence_error(procedure, user:solve/1)"
        category, explanation = _classify_error(text)
        assert category == "undefined_predicate"
        assert "user:solve/1" in explanation

    def test_unbound_variable_instantiation_phrase(self):
        text = "ERROR: /tmp/foo.pl:10: >/2: Arguments are not sufficiently instantiated"
        category, _ = _classify_error(text)
        assert category == "unbound_variable"

    def test_unbound_variable_instantiation_error_tag(self):
        text = "ERROR: instantiation_error"
        category, _ = _classify_error(text)
        assert category == "unbound_variable"

    def test_syntax_error(self):
        text = "ERROR: /tmp/foo.pl:1:14: Syntax error: Unexpected end of file"
        category, _ = _classify_error(text)
        assert category == "syntax_error"

    def test_type_error_captures_expected_type(self):
        text = "ERROR: type_error(integer, foo)"
        category, explanation = _classify_error(text)
        assert category == "type_error"
        assert "integer" in explanation

    def test_domain_error(self):
        text = "ERROR: domain_error(not_less_than_zero, -1)"
        category, explanation = _classify_error(text)
        assert category == "domain_error"
        assert "not_less_than_zero" in explanation

    def test_evaluation_error_zero_divisor_specific(self):
        text = "ERROR: evaluation_error(zero_divisor)"
        category, explanation = _classify_error(text)
        assert category == "evaluation_error"
        assert "zero" in explanation.lower()

    def test_permission_error(self):
        text = "ERROR: permission_error(modify, static_procedure, foo/1)"
        category, _ = _classify_error(text)
        assert category == "permission_error"

    def test_unknown_fallback(self):
        text = "Something completely unexpected"
        category, explanation = _classify_error(text)
        assert category == "unknown"
        assert "raw message" in explanation

    def test_syntax_checked_before_cascading_errors(self):
        """A syntax error may also trigger unknown-procedure errors; we want
        the syntax classification to win."""
        text = (
            "ERROR: /tmp/foo.pl:1:14: Syntax error: Unexpected end of file\n"
            "ERROR: Unknown procedure: foo/1"
        )
        category, _ = _classify_error(text)
        assert category == "syntax_error"


class TestErrorClassificationIntegration:
    """Integration tests: classification embedded in ExecutionResult."""

    @pytest.mark.asyncio
    async def test_undefined_predicate_from_real_prolog(self, executor):
        code = "human(socrates)."
        result = await executor.execute(code, "mortal(X)")
        assert result.success is False
        assert result.metadata["error_category"] == "undefined_predicate"
        assert "not defined" in result.metadata["error_explanation"]

    @pytest.mark.asyncio
    async def test_unbound_variable_from_real_prolog(self, executor):
        code = "check(X, Y) :- X > Y."
        result = await executor.execute(code, "check(X, 5)")
        assert result.success is False
        assert result.metadata["error_category"] == "unbound_variable"

    @pytest.mark.asyncio
    async def test_syntax_error_from_real_prolog(self, executor):
        code = "human(socrates"  # Missing paren and period
        result = await executor.execute(code, "human(X)")
        assert result.success is False
        assert result.metadata["error_category"] == "syntax_error"

    @pytest.mark.asyncio
    async def test_timeout_classified_as_timeout(self, executor):
        code = "loop :- loop."
        result = await executor.execute(code, "loop", timeout_seconds=1.0)
        assert result.success is False
        assert result.metadata["error_category"] == "timeout"
        assert "time limit" in result.metadata["error_explanation"]

    @pytest.mark.asyncio
    async def test_success_has_no_error_fields(self, executor):
        """Successful executions must not carry error_category/error_explanation."""
        code = "human(socrates)."
        result = await executor.execute(code, "human(X)")
        assert result.success is True
        assert "error_category" not in result.metadata
        assert "error_explanation" not in result.metadata
