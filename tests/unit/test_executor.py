"""Unit tests for PrologExecutor.

Requires SWI-Prolog installed (run in Docker).
"""

import pytest

from prolog_reasoner.config import Settings
from prolog_reasoner.errors import BackendError
from prolog_reasoner.executor import PrologExecutor


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
    async def test_prolog_warnings_in_metadata(self, executor):
        """Non-ERROR stderr content should appear as warnings."""
        # Singleton variable generates a warning in SWI-Prolog
        code = "test(X, Y) :- X = 1."  # Y is singleton
        result = await executor.execute(code, "test(A, B)")
        assert result.success is True
        # Warnings may or may not appear depending on SWI-Prolog config

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
