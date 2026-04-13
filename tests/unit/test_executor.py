"""Unit tests for PrologExecutor.

Requires SWI-Prolog installed (run in Docker).
"""

import pytest

from prolog_reasoner.config import Settings
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
