"""Integration tests for MCP server.

Tests tool registration and invocation via FastMCP's in-memory client.
Uses mock LLM — no API key needed.
"""

from unittest.mock import AsyncMock, patch

import pytest

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


class TestMCPTools:
    @pytest.mark.asyncio
    async def test_tools_registered(self, mock_reasoner):
        """Verify both tools are registered on the MCP server."""
        with patch("prolog_reasoner.server.reasoner", mock_reasoner):
            from prolog_reasoner.server import mcp

            # FastMCP stores tools internally; verify via get_tool
            translate_tool = await mcp.get_tool("translate_to_prolog")
            execute_tool = await mcp.get_tool("execute_prolog")
            assert translate_tool is not None
            assert execute_tool is not None

    @pytest.mark.asyncio
    async def test_execute_tool_via_server(self, mock_reasoner):
        """Verify execute_prolog tool works end-to-end."""
        with patch("prolog_reasoner.server.reasoner", mock_reasoner):
            from prolog_reasoner.server import execute_prolog

            result = await execute_prolog(
                prolog_code="human(socrates). mortal(X) :- human(X).",
                query="mortal(X)",
            )
            assert result["success"] is True
            assert "socrates" in result["output"]

    @pytest.mark.asyncio
    async def test_translate_tool_via_server(self, mock_reasoner):
        """Verify translate_to_prolog tool works end-to-end."""
        with patch("prolog_reasoner.server.reasoner", mock_reasoner):
            from prolog_reasoner.server import translate_to_prolog

            result = await translate_to_prolog(
                query="Is Socrates mortal?",
            )
            assert result["success"] is True
            assert "human(socrates)" in result["prolog_code"]
