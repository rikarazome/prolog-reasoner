"""Integration tests for MCP server.

Tests tool registration and invocation via FastMCP's in-memory client.
Uses mock LLM — no API key needed.
"""

from unittest.mock import AsyncMock

import pytest

import prolog_reasoner.server as server_module
from prolog_reasoner.config import Settings
from prolog_reasoner.executor import PrologExecutor
from prolog_reasoner.llm_client import LLMClient
from prolog_reasoner.reasoner import PrologReasoner
from prolog_reasoner.translator import PrologTranslator


@pytest.fixture
def mock_reasoner():
    settings = Settings(llm_api_key="dummy")
    mock_llm = AsyncMock(spec=LLMClient)
    mock_llm.complete.return_value = (
        "human(socrates).\nmortal(X) :- human(X).\n% Query: mortal(X)"
    )
    translator = PrologTranslator(mock_llm, settings)
    executor = PrologExecutor(settings)
    return PrologReasoner(translator, executor)


@pytest.fixture(autouse=True)
def _inject_reasoner(mock_reasoner):
    """Inject mock reasoner into server module, restore after test."""
    original = server_module._reasoner
    server_module._reasoner = mock_reasoner
    yield
    server_module._reasoner = original


class TestMCPTools:
    @pytest.mark.asyncio
    async def test_tools_registered(self):
        """Verify both tools are registered on the MCP server."""
        translate_tool = await server_module.mcp.get_tool("translate_to_prolog")
        execute_tool = await server_module.mcp.get_tool("execute_prolog")
        assert translate_tool is not None
        assert execute_tool is not None

    @pytest.mark.asyncio
    async def test_execute_tool_via_server(self):
        """Verify execute_prolog tool works end-to-end."""
        result = await server_module.execute_prolog(
            prolog_code="human(socrates). mortal(X) :- human(X).",
            query="mortal(X)",
        )
        assert result["success"] is True
        assert "socrates" in result["output"]

    @pytest.mark.asyncio
    async def test_translate_tool_via_server(self):
        """Verify translate_to_prolog tool works end-to-end."""
        result = await server_module.translate_to_prolog(
            query="Is Socrates mortal?",
        )
        assert result["success"] is True
        assert "human(socrates)" in result["prolog_code"]
