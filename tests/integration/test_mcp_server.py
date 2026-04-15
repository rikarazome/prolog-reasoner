"""Integration tests for MCP server.

Tests tool registration and invocation. No LLM API key needed —
the MCP server only exposes execute_prolog.
"""

import pytest

import prolog_reasoner.server as server_module
from prolog_reasoner.config import Settings
from prolog_reasoner.executor import PrologExecutor


@pytest.fixture(autouse=True)
def _inject_executor():
    """Initialize server executor for tests, restore after."""
    settings = Settings()
    executor = PrologExecutor(settings)

    orig = server_module._executor
    server_module._executor = executor
    yield
    server_module._executor = orig


class TestMCPTools:
    @pytest.mark.asyncio
    async def test_execute_tool_registered(self):
        """Verify execute_prolog tool is registered."""
        tool = await server_module.mcp.get_tool("execute_prolog")
        assert tool is not None

    @pytest.mark.asyncio
    async def test_no_translate_tool(self):
        """translate_to_prolog is NOT an MCP tool (it's library-only)."""
        tool = await server_module.mcp.get_tool("translate_to_prolog")
        assert tool is None

    @pytest.mark.asyncio
    async def test_execute_simple_query(self):
        """execute_prolog runs Prolog and returns results."""
        result = await server_module.execute_prolog(
            prolog_code="human(socrates). mortal(X) :- human(X).",
            query="mortal(X)",
        )
        assert result["success"] is True
        assert "socrates" in result["output"]

    @pytest.mark.asyncio
    async def test_execute_clpfd(self):
        """CLP(FD) constraints work through MCP tool."""
        result = await server_module.execute_prolog(
            prolog_code=":- use_module(library(clpfd)).\nsolve(X) :- X in 1..5, X #> 3, label([X]).",
            query="solve(X)",
        )
        assert result["success"] is True
        assert "4" in result["output"]
        assert "5" in result["output"]

    @pytest.mark.asyncio
    async def test_execute_returns_error_on_bad_code(self):
        """Syntax errors are reported cleanly."""
        result = await server_module.execute_prolog(
            prolog_code="bad(code",
            query="bad(X)",
        )
        assert result["success"] is False
        assert result["error"] is not None

    @pytest.mark.asyncio
    async def test_no_api_key_required(self):
        """MCP server works without any LLM API key."""
        # Reset executor to force re-init from clean state
        server_module._executor = None
        result = await server_module.execute_prolog(
            prolog_code="fact(1). fact(2). fact(3).",
            query="fact(X)",
        )
        assert result["success"] is True
        assert result["metadata"]["result_count"] == 3

    @pytest.mark.asyncio
    async def test_execute_prolog_with_trace(self):
        """trace=True surfaces proof_trace in metadata via MCP tool."""
        result = await server_module.execute_prolog(
            prolog_code="human(socrates). mortal(X) :- human(X).",
            query="mortal(X)",
            trace=True,
        )
        assert result["success"] is True
        assert "proof_trace" in result["metadata"]
        proofs = result["metadata"]["proof_trace"]
        assert len(proofs) == 1
        assert "mortal(socrates)" in proofs[0]
        assert "human(socrates)" in proofs[0]

    @pytest.mark.asyncio
    async def test_execute_prolog_without_trace_default(self):
        """Default invocation must not include proof_trace."""
        result = await server_module.execute_prolog(
            prolog_code="human(socrates).",
            query="human(X)",
        )
        assert result["success"] is True
        assert "proof_trace" not in result["metadata"]
