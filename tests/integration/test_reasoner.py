"""Integration tests for PrologReasoner.

Uses recorded LLM responses — no API key needed.
Tests the full translate → validate → execute pipeline.
"""

import pytest

from prolog_reasoner.models import ExecutionRequest, TranslationRequest


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
        from unittest.mock import AsyncMock

        from prolog_reasoner.config import Settings
        from prolog_reasoner.executor import PrologExecutor
        from prolog_reasoner.llm_client import LLMClient
        from prolog_reasoner.reasoner import PrologReasoner
        from prolog_reasoner.translator import PrologTranslator

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
