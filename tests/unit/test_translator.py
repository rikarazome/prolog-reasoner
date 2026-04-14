"""Unit tests for PrologTranslator.

Uses mock LLM client — no API key needed.
"""

from unittest.mock import AsyncMock

import pytest

from prolog_reasoner.config import Settings
from prolog_reasoner.errors import LLMError, TranslationError
from prolog_reasoner.executor import PrologExecutor
from prolog_reasoner.llm_client import LLMClient
from prolog_reasoner.translator import PrologTranslator


@pytest.fixture
def mock_llm():
    llm = AsyncMock(spec=LLMClient)
    return llm


@pytest.fixture
def settings():
    return Settings(
        llm_api_key="dummy",
        llm_temperature=0.0,
    )


@pytest.fixture
def translator(mock_llm, settings):
    return PrologTranslator(mock_llm, settings)


@pytest.fixture
def executor(settings):
    return PrologExecutor(settings)


class TestTranslate:
    @pytest.mark.asyncio
    async def test_basic_translation(self, translator, mock_llm):
        mock_llm.complete.return_value = (
            "human(socrates).\nmortal(X) :- human(X).\n% Query: mortal(X)"
        )
        code, query = await translator.translate("Is Socrates mortal?")
        assert "human(socrates)" in code
        assert query == "mortal(X)"
        mock_llm.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_with_context(self, translator, mock_llm):
        mock_llm.complete.return_value = "parent(tom, bob).\n% Query: parent(X, Y)"
        code, query = await translator.translate(
            "Who is Bob's parent?",
            context="Tom is Bob's parent."
        )
        assert "parent(tom, bob)" in code
        # Verify context was included in user prompt
        call_args = mock_llm.complete.call_args
        user_prompt = call_args.kwargs.get("user_prompt", "")
        assert "Context:" in user_prompt

    @pytest.mark.asyncio
    async def test_empty_response_raises(self, translator, mock_llm):
        mock_llm.complete.return_value = "   "
        with pytest.raises(TranslationError) as exc_info:
            await translator.translate("test")
        assert exc_info.value.error_code == "TRANSLATION_001"

    @pytest.mark.asyncio
    async def test_no_query_comment(self, translator, mock_llm):
        mock_llm.complete.return_value = "human(socrates)."
        code, query = await translator.translate("test")
        assert code == "human(socrates)."
        assert query == ""


class TestExtractQuery:
    def test_standard(self):
        code = "human(socrates).\n% Query: mortal(X)"
        assert PrologTranslator._extract_query(code) == "mortal(X)"

    def test_with_trailing_period(self):
        code = "% Query: mortal(socrates)."
        assert PrologTranslator._extract_query(code) == "mortal(socrates)"

    def test_with_spaces(self):
        code = "%  Query:   mortal(X)  "
        assert PrologTranslator._extract_query(code) == "mortal(X)"

    def test_no_match(self):
        code = "human(socrates)."
        assert PrologTranslator._extract_query(code) == ""

    def test_multiple_queries_picks_last(self):
        # LLMs sometimes emit an NL paraphrase first, then the real goal.
        # The last % Query: comment is the LLM's committed executable goal.
        code = (
            "% Query: Is it true that all birds can fly?\n"
            "all_birds_can_fly :- \\+ (bird(X), \\+ can_fly(X)).\n"
            "% Query: all_birds_can_fly"
        )
        assert PrologTranslator._extract_query(code) == "all_birds_can_fly"

    def test_strips_trailing_question_mark(self):
        code = "% Query: is_bigger(a, e)?"
        assert PrologTranslator._extract_query(code) == "is_bigger(a, e)"


class TestTranslateWithCorrection:
    @pytest.mark.asyncio
    async def test_first_attempt_valid(self, translator, mock_llm, executor):
        mock_llm.complete.return_value = (
            "human(socrates).\n% Query: human(X)"
        )
        result = await translator.translate_with_correction(
            query="test", context="", executor=executor, max_corrections=3
        )
        assert result.success is True
        assert result.prolog_code == "human(socrates).\n% Query: human(X)"
        assert result.suggested_query == "human(X)"
        assert result.metadata["correction_iterations"] == 0

    @pytest.mark.asyncio
    async def test_correction_fixes_syntax(self, translator, mock_llm, executor):
        # First call returns invalid, second returns valid
        mock_llm.complete.side_effect = [
            "human(socrates",           # invalid syntax
            "human(socrates).\n% Query: human(X)",  # corrected
        ]
        result = await translator.translate_with_correction(
            query="test", context="", executor=executor, max_corrections=3
        )
        assert result.success is True
        assert mock_llm.complete.call_count == 2

    @pytest.mark.asyncio
    async def test_max_corrections_exceeded(self, translator, mock_llm, executor):
        mock_llm.complete.return_value = "human(socrates"  # Always invalid
        result = await translator.translate_with_correction(
            query="test", context="", executor=executor, max_corrections=2
        )
        assert result.success is False
        assert "TRANSLATION_002" in result.metadata.get("error_code", "")

    @pytest.mark.asyncio
    async def test_skip_validation(self, translator, mock_llm, executor):
        mock_llm.complete.return_value = "human(socrates"  # Invalid, but skip
        result = await translator.translate_with_correction(
            query="test", context="", executor=executor, max_corrections=0
        )
        assert result.success is True  # No validation → success
        assert result.metadata["correction_iterations"] == 0

    @pytest.mark.asyncio
    async def test_empty_response_returns_failure(self, translator, mock_llm, executor):
        mock_llm.complete.return_value = ""
        result = await translator.translate_with_correction(
            query="test", context="", executor=executor, max_corrections=3
        )
        assert result.success is False
        assert "TRANSLATION_001" in result.metadata.get("error_code", "")

    @pytest.mark.asyncio
    async def test_llm_error_propagates(self, translator, mock_llm, executor):
        mock_llm.complete.side_effect = LLMError(
            "API failed", error_code="LLM_001", retryable=True
        )
        with pytest.raises(LLMError) as exc_info:
            await translator.translate_with_correction(
                query="test", context="", executor=executor, max_corrections=3
            )
        assert exc_info.value.error_code == "LLM_001"
        assert exc_info.value.retryable is True

    @pytest.mark.asyncio
    async def test_correction_loop_translation_error_breaks(
        self, translator, mock_llm, executor
    ):
        """TranslationError during correction loop should break and return failure."""
        mock_llm.complete.side_effect = [
            "human(socrates",               # initial: invalid syntax
            TranslationError("empty", error_code="TRANSLATION_001"),  # correction fails
        ]
        result = await translator.translate_with_correction(
            query="test", context="", executor=executor, max_corrections=3
        )
        # Should fail: initial code had syntax error, correction threw TranslationError
        assert result.success is False

    @pytest.mark.asyncio
    async def test_correction_empty_response_breaks(
        self, translator, mock_llm, executor
    ):
        """Empty correction response should break loop and return failure."""
        mock_llm.complete.side_effect = [
            "human(socrates",  # initial: invalid syntax
            "   ",             # correction returns empty
        ]
        result = await translator.translate_with_correction(
            query="test", context="", executor=executor, max_corrections=3
        )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_metadata_has_translation_time(self, translator, mock_llm, executor):
        """All results should include translation_time_ms in metadata."""
        mock_llm.complete.return_value = "human(socrates).\n% Query: human(X)"
        result = await translator.translate_with_correction(
            query="test", context="", executor=executor, max_corrections=3
        )
        assert "translation_time_ms" in result.metadata
        assert isinstance(result.metadata["translation_time_ms"], int)
        assert result.metadata["translation_time_ms"] >= 0
