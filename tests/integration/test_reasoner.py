"""Integration tests for PrologReasoner.

Uses recorded LLM responses — no API key needed.
Tests the full translate → validate → execute pipeline.
"""

from unittest.mock import AsyncMock

import pytest

from prolog_reasoner.config import Settings
from prolog_reasoner.errors import RuleBaseError
from prolog_reasoner.executor import PrologExecutor
from prolog_reasoner.llm_client import LLMClient
from prolog_reasoner.models import ExecutionRequest, TranslationRequest
from prolog_reasoner.reasoner import PrologReasoner
from prolog_reasoner.rule_base import RuleBaseStore
from prolog_reasoner.translator import PrologTranslator


class TestSocratesSyllogism:
    """Classic deductive reasoning: human(socrates) + mortal(X):-human(X)."""

    @pytest.mark.asyncio
    async def test_translate(self, recorded_reasoner):
        reasoner = recorded_reasoner(["socrates"])
        request = TranslationRequest(
            query="Is Socrates mortal? Socrates is a human. All humans are mortal."
        )
        result = await reasoner.translate(request)
        assert result.success is True
        assert "human(socrates)" in result.prolog_code
        assert "mortal" in result.prolog_code

    @pytest.mark.asyncio
    async def test_execute(self, executor):
        request = ExecutionRequest(
            prolog_code="human(socrates). mortal(X) :- human(X).",
            query="mortal(socrates)",
        )
        result = await executor.execute(
            prolog_code=request.prolog_code,
            query=request.query,
        )
        assert result.success is True
        assert "socrates" in result.output
        assert result.metadata["result_count"] == 1

    @pytest.mark.asyncio
    async def test_full_pipeline(self, recorded_reasoner, executor):
        reasoner = recorded_reasoner(["socrates"])
        # Translate
        tr = await reasoner.translate(
            TranslationRequest(
                query="Is Socrates mortal? Socrates is a human. All humans are mortal."
            )
        )
        assert tr.success is True
        # Execute
        er = await executor.execute(
            prolog_code=tr.prolog_code,
            query=tr.suggested_query or "mortal(socrates)",
        )
        assert er.success is True
        assert "socrates" in er.output


class TestFamilyRelations:
    """Transitive multi-hop reasoning: ancestor via parent chain."""

    @pytest.mark.asyncio
    async def test_translate(self, recorded_reasoner):
        reasoner = recorded_reasoner(["family"])
        request = TranslationRequest(
            query="Who are Tom's descendants?",
            context="Tom is Bob's parent. Bob is Ann's parent. Bob is Pat's parent.",
        )
        result = await reasoner.translate(request)
        assert result.success is True
        assert "ancestor" in result.prolog_code

    @pytest.mark.asyncio
    async def test_execute_ancestors(self, executor):
        code = (
            "parent(tom, bob). parent(bob, ann). parent(bob, pat). "
            "ancestor(X, Y) :- parent(X, Y). "
            "ancestor(X, Y) :- parent(X, Z), ancestor(Z, Y)."
        )
        result = await executor.execute(code, "ancestor(tom, X)")
        assert result.success is True
        assert "bob" in result.output
        assert "ann" in result.output
        assert "pat" in result.output
        assert result.metadata["result_count"] == 3


class TestConstraintSatisfaction:
    """CLP(FD) constraint solving: scheduling with all_different."""

    @pytest.mark.asyncio
    async def test_translate(self, recorded_reasoner):
        reasoner = recorded_reasoner(["constraint"])
        request = TranslationRequest(
            query="Schedule 3 tasks in 3 different time slots where task A is before task B"
        )
        result = await reasoner.translate(request)
        assert result.success is True
        assert "clpfd" in result.prolog_code

    @pytest.mark.asyncio
    async def test_execute_constraint(self, executor):
        code = (
            ":- use_module(library(clpfd)).\n"
            "schedule(A, B, C) :- "
            "[A, B, C] ins 1..3, all_different([A, B, C]), A #< B, label([A, B, C])."
        )
        result = await executor.execute(code, "schedule(A, B, C)")
        assert result.success is True
        # A < B, all different in 1..3
        assert result.metadata["result_count"] >= 1


class TestSelfCorrection:
    """Verify the self-correction loop fixes syntax errors."""

    @pytest.mark.asyncio
    async def test_correction_repairs_syntax(self, recorded_reasoner):
        """First LLM response has syntax error, second is correct."""
        settings = Settings(llm_api_key="dummy")
        mock_llm = AsyncMock(spec=LLMClient)
        mock_llm.complete.side_effect = [
            "human(socrates.\n% Query: human(X)",          # broken
            "human(socrates).\n% Query: human(X)",         # fixed
        ]
        translator = PrologTranslator(mock_llm, settings)
        executor = PrologExecutor(settings)
        reasoner = PrologReasoner(translator, executor)

        result = await reasoner.translate(
            TranslationRequest(query="test", max_corrections=3)
        )
        assert result.success is True
        assert "human(socrates)." in result.prolog_code
        assert mock_llm.complete.call_count == 2


class TestRuleBasesExecute:
    """v14: PrologReasoner.execute() resolves rule_bases via the store."""

    def _build_reasoner(
        self, tmp_path, *, with_store: bool = True,
    ) -> PrologReasoner:
        settings = Settings(
            llm_api_key="dummy",
            rules_dir=tmp_path / "rules",
        )
        executor = PrologExecutor(settings)
        # Translator is unused by execute(); mock it.
        translator = AsyncMock(spec=PrologTranslator)
        store = RuleBaseStore(settings, executor) if with_store else None
        return PrologReasoner(translator, executor, rule_base_store=store)

    @pytest.mark.asyncio
    async def test_no_store_no_rule_bases_ok(self, tmp_path):
        """Backwards-compatible path: omitting store AND rule_bases works."""
        reasoner = self._build_reasoner(tmp_path, with_store=False)
        request = ExecutionRequest(
            prolog_code="human(socrates).",
            query="human(X)",
        )
        result = await reasoner.execute(request)
        assert result.success is True
        assert "socrates" in result.output

    @pytest.mark.asyncio
    async def test_no_store_with_rule_bases_raises_value_error(self, tmp_path):
        """DI misconfiguration: rule_bases without a store must hard-error."""
        reasoner = self._build_reasoner(tmp_path, with_store=False)
        request = ExecutionRequest(
            prolog_code="fact(1).",
            query="fact(X)",
            rule_bases=["chess"],
        )
        with pytest.raises(ValueError, match="rule_bases"):
            await reasoner.execute(request)

    @pytest.mark.asyncio
    async def test_store_present_normal_flow(self, tmp_path):
        reasoner = self._build_reasoner(tmp_path)
        await reasoner.rule_base_store.save("chess", "piece(king).\n")

        request = ExecutionRequest(
            prolog_code="royal(X) :- piece(X).",
            query="royal(X)",
            rule_bases=["chess"],
        )
        result = await reasoner.execute(request)
        assert result.success is True
        assert "king" in result.output
        assert result.metadata["rule_bases_used"] == ["chess"]

    @pytest.mark.asyncio
    async def test_store_dedups_rule_bases(self, tmp_path):
        reasoner = self._build_reasoner(tmp_path)
        await reasoner.rule_base_store.save("chess", "piece(king).\n")

        request = ExecutionRequest(
            prolog_code="% empty",
            query="piece(X)",
            rule_bases=["chess", "chess"],
        )
        result = await reasoner.execute(request)
        assert result.success is True
        assert result.metadata["rule_bases_used"] == ["chess"]

    @pytest.mark.asyncio
    async def test_missing_rule_base_returns_failed_result(self, tmp_path):
        """RULEBASE_001 converts to ExecutionResult(success=False)."""
        reasoner = self._build_reasoner(tmp_path)
        request = ExecutionRequest(
            prolog_code="fact(1).",
            query="fact(X)",
            rule_bases=["does_not_exist"],
        )
        result = await reasoner.execute(request)
        assert result.success is False
        assert result.metadata["error_code"] == "RULEBASE_001"
        assert result.output == ""

    @pytest.mark.asyncio
    async def test_invalid_name_returns_failed_result(self, tmp_path):
        """RULEBASE_002 converts to ExecutionResult(success=False)."""
        reasoner = self._build_reasoner(tmp_path)
        request = ExecutionRequest(
            prolog_code="fact(1).",
            query="fact(X)",
            rule_bases=["bad name"],
        )
        result = await reasoner.execute(request)
        assert result.success is False
        assert result.metadata["error_code"] == "RULEBASE_002"

    @pytest.mark.asyncio
    async def test_io_error_propagates(self, tmp_path, monkeypatch):
        """RULEBASE_004 (infra) must NOT be converted — it propagates."""
        reasoner = self._build_reasoner(tmp_path)

        def _raise_io(name: str) -> str:
            raise RuleBaseError("disk broke", error_code="RULEBASE_004")

        monkeypatch.setattr(reasoner.rule_base_store, "get", _raise_io)

        request = ExecutionRequest(
            prolog_code="fact(1).",
            query="fact(X)",
            rule_bases=["chess"],
        )
        with pytest.raises(RuleBaseError) as excinfo:
            await reasoner.execute(request)
        assert excinfo.value.error_code == "RULEBASE_004"

    @pytest.mark.asyncio
    async def test_empty_rule_bases_does_not_require_store(self, tmp_path):
        """An empty list is a no-op; store may be None."""
        reasoner = self._build_reasoner(tmp_path, with_store=False)
        request = ExecutionRequest(
            prolog_code="fact(1).",
            query="fact(X)",
            rule_bases=[],
        )
        result = await reasoner.execute(request)
        assert result.success is True


class TestRuleBasesTranslate:
    """v15: PrologReasoner.translate() forwards rule_bases + rule_base_store
    to the translator so the LLM can see already-defined predicates."""

    def _build(
        self, tmp_path, *, with_store: bool = True,
    ) -> tuple[PrologReasoner, AsyncMock]:
        settings = Settings(llm_api_key="dummy", rules_dir=tmp_path / "rules")
        executor = PrologExecutor(settings)
        mock_llm = AsyncMock(spec=LLMClient)
        translator = PrologTranslator(mock_llm, settings)
        store = RuleBaseStore(settings, executor) if with_store else None
        reasoner = PrologReasoner(translator, executor, rule_base_store=store)
        return reasoner, mock_llm

    @pytest.mark.asyncio
    async def test_rule_bases_forwarded_to_translator_prompt(self, tmp_path):
        reasoner, mock_llm = self._build(tmp_path)
        await reasoner.rule_base_store.save(
            "chess", "% description: Chess\npiece(king).\n"
        )
        mock_llm.complete.return_value = "piece(X).\n% Query: piece(X)"
        result = await reasoner.translate(
            TranslationRequest(
                query="list pieces",
                max_corrections=0,
                rule_bases=["chess"],
            )
        )
        assert result.success is True
        system_prompt = mock_llm.complete.call_args.kwargs["system_prompt"]
        assert "Available rule bases" in system_prompt
        assert "piece(king)" in system_prompt

    @pytest.mark.asyncio
    async def test_rule_bases_without_store_raises(self, tmp_path):
        reasoner, _ = self._build(tmp_path, with_store=False)
        with pytest.raises(ValueError, match="rule_base_store|RuleBaseStore"):
            await reasoner.translate(
                TranslationRequest(
                    query="x", max_corrections=0, rule_bases=["chess"],
                )
            )

    @pytest.mark.asyncio
    async def test_empty_rule_bases_no_store_ok(self, tmp_path):
        """Backwards-compat: no rule_bases, no store → still works."""
        reasoner, mock_llm = self._build(tmp_path, with_store=False)
        mock_llm.complete.return_value = "ok.\n% Query: ok"
        result = await reasoner.translate(
            TranslationRequest(query="x", max_corrections=0)
        )
        assert result.success is True
        system_prompt = mock_llm.complete.call_args.kwargs["system_prompt"]
        assert "Available rule bases" not in system_prompt

    @pytest.mark.asyncio
    async def test_unknown_rule_base_surfaces_failure(self, tmp_path):
        reasoner, mock_llm = self._build(tmp_path)
        result = await reasoner.translate(
            TranslationRequest(
                query="x", max_corrections=0, rule_bases=["missing"],
            )
        )
        assert result.success is False
        assert result.metadata.get("error_code") == "RULEBASE_001"
        mock_llm.complete.assert_not_called()
