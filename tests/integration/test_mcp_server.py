"""Integration tests for MCP server.

Tests tool registration and invocation. No LLM API key needed —
the MCP server only exposes execute_prolog and the rule-base CRUD tools.
"""

import pytest

import prolog_reasoner.server as server_module
from prolog_reasoner.config import Settings
from prolog_reasoner.executor import PrologExecutor
from prolog_reasoner.rule_base import RuleBaseStore


@pytest.fixture(autouse=True)
def _inject_executor(tmp_path):
    """Initialize server executor + rule base store for tests.

    The store writes to an isolated ``tmp_path`` so CRUD tests never
    touch the user's real ``~/.prolog-reasoner/rules``.
    """
    settings = Settings(rules_dir=tmp_path / "rules")
    executor = PrologExecutor(settings)
    store = RuleBaseStore(settings, executor)

    orig_executor = server_module._executor
    orig_store = server_module._rule_base_store
    server_module._executor = executor
    server_module._rule_base_store = store
    yield
    server_module._executor = orig_executor
    server_module._rule_base_store = orig_store


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


class TestRuleBaseTools:
    """v14: 4 CRUD tools + execute_prolog with rule_bases."""

    @pytest.mark.asyncio
    async def test_all_tools_registered(self):
        for name in (
            "list_rule_bases",
            "get_rule_base",
            "save_rule_base",
            "delete_rule_base",
        ):
            tool = await server_module.mcp.get_tool(name)
            assert tool is not None, f"MCP tool {name!r} not registered"

    @pytest.mark.asyncio
    async def test_save_then_list(self):
        res = await server_module.save_rule_base(
            name="chess",
            content=(
                "% description: Chess piece movement\n"
                "% tags: chess, game\n"
                "piece(king).\n"
            ),
        )
        assert res == {"success": True, "name": "chess", "created": True}

        listing = await server_module.list_rule_bases()
        assert listing == {
            "rule_bases": [
                {
                    "name": "chess",
                    "description": "Chess piece movement",
                    "tags": ["chess", "game"],
                }
            ]
        }

    @pytest.mark.asyncio
    async def test_save_overwrite_reports_created_false(self):
        await server_module.save_rule_base("chess", "piece(king).\n")
        res = await server_module.save_rule_base("chess", "piece(queen).\n")
        assert res == {"success": True, "name": "chess", "created": False}

        got = await server_module.get_rule_base("chess")
        assert "piece(queen)" in got["content"]

    @pytest.mark.asyncio
    async def test_get_returns_content(self):
        await server_module.save_rule_base("chess", "piece(king).\n")
        res = await server_module.get_rule_base("chess")
        assert res == {
            "success": True,
            "name": "chess",
            "content": "piece(king).\n",
        }

    @pytest.mark.asyncio
    async def test_get_missing_returns_001_dict(self):
        res = await server_module.get_rule_base("nonexistent")
        assert res["success"] is False
        assert res["error_code"] == "RULEBASE_001"

    @pytest.mark.asyncio
    async def test_get_invalid_name_returns_002_dict(self):
        res = await server_module.get_rule_base("bad name")
        assert res["success"] is False
        assert res["error_code"] == "RULEBASE_002"

    @pytest.mark.asyncio
    async def test_save_invalid_name_returns_002_dict(self):
        res = await server_module.save_rule_base("bad/name", "x.\n")
        assert res["success"] is False
        assert res["error_code"] == "RULEBASE_002"

    @pytest.mark.asyncio
    async def test_save_syntax_error_returns_003_dict(self):
        res = await server_module.save_rule_base("chess", "piece(king")
        assert res["success"] is False
        assert res["error_code"] == "RULEBASE_003"

    @pytest.mark.asyncio
    async def test_save_oversize_returns_005_dict(self, tmp_path, monkeypatch):
        """Temporarily shrink max_rule_size via a fresh store."""
        settings = Settings(
            rules_dir=tmp_path / "rules",
            max_rule_size=32,
        )
        executor = PrologExecutor(settings)
        store = RuleBaseStore(settings, executor)
        monkeypatch.setattr(server_module, "_rule_base_store", store)
        monkeypatch.setattr(server_module, "_executor", executor)

        big = "fact(" + ("x" * 200) + ")."
        res = await server_module.save_rule_base("big", big)
        assert res["success"] is False
        assert res["error_code"] == "RULEBASE_005"

    @pytest.mark.asyncio
    async def test_delete_removes_rule_base(self):
        await server_module.save_rule_base("chess", "piece(king).\n")
        res = await server_module.delete_rule_base("chess")
        assert res == {"success": True, "name": "chess"}

        listing = await server_module.list_rule_bases()
        assert listing == {"rule_bases": []}

    @pytest.mark.asyncio
    async def test_delete_missing_returns_001_dict(self):
        res = await server_module.delete_rule_base("nope")
        assert res["success"] is False
        assert res["error_code"] == "RULEBASE_001"

    @pytest.mark.asyncio
    async def test_execute_prolog_uses_saved_rule_base(self):
        await server_module.save_rule_base(
            "chess", "piece(king). piece(queen).\n",
        )
        res = await server_module.execute_prolog(
            prolog_code="royal(X) :- piece(X).",
            query="royal(X)",
            rule_bases=["chess"],
        )
        assert res["success"] is True
        assert "king" in res["output"]
        assert "queen" in res["output"]
        assert res["metadata"]["rule_bases_used"] == ["chess"]

    @pytest.mark.asyncio
    async def test_execute_prolog_dedups_rule_bases(self):
        """Repeat names must not load the same file twice (§4.10)."""
        await server_module.save_rule_base("chess", "piece(king).\n")
        res = await server_module.execute_prolog(
            prolog_code="% empty",
            query="piece(X)",
            rule_bases=["chess", "chess", "chess"],
        )
        assert res["success"] is True
        # Dedup preserves first occurrence.
        assert res["metadata"]["rule_bases_used"] == ["chess"]

    @pytest.mark.asyncio
    async def test_execute_prolog_missing_rule_base_returns_001(self):
        """RULEBASE_001 is converted to ExecutionResult(success=False)."""
        res = await server_module.execute_prolog(
            prolog_code="fact(1).",
            query="fact(X)",
            rule_bases=["does_not_exist"],
        )
        assert res["success"] is False
        assert res["metadata"]["error_code"] == "RULEBASE_001"

    @pytest.mark.asyncio
    async def test_execute_prolog_invalid_name_returns_002(self):
        res = await server_module.execute_prolog(
            prolog_code="fact(1).",
            query="fact(X)",
            rule_bases=["bad name"],
        )
        assert res["success"] is False
        assert res["metadata"]["error_code"] == "RULEBASE_002"

    @pytest.mark.asyncio
    async def test_execute_prolog_missing_suggests_similar_name(self):
        """§5.3: not-found error includes a 'did you mean?' suggestion."""
        await server_module.save_rule_base("piece_moves", "piece(king).\n")
        res = await server_module.execute_prolog(
            prolog_code="% empty",
            query="piece(X)",
            rule_bases=["piece_move"],  # typo
        )
        assert res["success"] is False
        assert "piece_moves" in res["error"]

    @pytest.mark.asyncio
    async def test_execute_prolog_rule_bases_order_preserved(self):
        """Insertion order (with dedup) is what's reported back."""
        await server_module.save_rule_base("a", "fact(1).\n")
        await server_module.save_rule_base("b", "fact(2).\n")
        res = await server_module.execute_prolog(
            prolog_code="% empty",
            query="fact(X)",
            rule_bases=["b", "a", "b"],
        )
        assert res["success"] is True
        assert res["metadata"]["rule_bases_used"] == ["b", "a"]

    @pytest.mark.asyncio
    async def test_execute_prolog_none_rule_bases_defaults_to_empty(self):
        """Unset rule_bases behaves as empty list (rule_bases_used == [])."""
        res = await server_module.execute_prolog(
            prolog_code="fact(1).",
            query="fact(X)",
        )
        assert res["success"] is True
        assert res["metadata"]["rule_bases_used"] == []
