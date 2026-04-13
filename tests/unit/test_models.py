"""Unit tests for Pydantic data models."""

import pytest
from pydantic import ValidationError

from prolog_reasoner.models import (
    ExecutionRequest,
    ExecutionResult,
    TranslationRequest,
    TranslationResult,
)


class TestTranslationRequest:
    def test_valid_minimal(self):
        req = TranslationRequest(query="Who is mortal?")
        assert req.query == "Who is mortal?"
        assert req.context == ""
        assert req.max_corrections == 3

    def test_valid_full(self):
        req = TranslationRequest(
            query="Is Socrates mortal?",
            context="Socrates is a human. Humans are mortal.",
            max_corrections=5,
        )
        assert req.context == "Socrates is a human. Humans are mortal."
        assert req.max_corrections == 5

    def test_empty_query_rejected(self):
        with pytest.raises(ValidationError):
            TranslationRequest(query="")

    def test_max_corrections_bounds(self):
        with pytest.raises(ValidationError):
            TranslationRequest(query="test", max_corrections=-1)
        with pytest.raises(ValidationError):
            TranslationRequest(query="test", max_corrections=11)

    def test_max_corrections_zero_allowed(self):
        req = TranslationRequest(query="test", max_corrections=0)
        assert req.max_corrections == 0


class TestExecutionRequest:
    def test_valid_minimal(self):
        req = ExecutionRequest(prolog_code="human(socrates).", query="human(X)")
        assert req.max_results == 100

    def test_empty_code_rejected(self):
        with pytest.raises(ValidationError):
            ExecutionRequest(prolog_code="", query="test")

    def test_empty_query_rejected(self):
        with pytest.raises(ValidationError):
            ExecutionRequest(prolog_code="fact.", query="")

    def test_max_results_bounds(self):
        with pytest.raises(ValidationError):
            ExecutionRequest(prolog_code="f.", query="q", max_results=0)
        with pytest.raises(ValidationError):
            ExecutionRequest(prolog_code="f.", query="q", max_results=10001)


class TestTranslationResult:
    def test_success_result(self):
        result = TranslationResult(
            success=True,
            prolog_code="human(socrates).",
            suggested_query="human(X)",
        )
        assert result.success is True
        assert result.error is None
        assert result.metadata == {}

    def test_failure_result(self):
        result = TranslationResult(
            success=False,
            error="LLM returned empty response",
            metadata={"error_code": "TRANSLATION_001"},
        )
        assert result.success is False
        assert result.prolog_code == ""


class TestExecutionResult:
    def test_success_result(self):
        result = ExecutionResult(
            success=True,
            output="human(socrates)\n",
            query="human(X)",
            metadata={"result_count": 1},
        )
        assert result.success is True
        assert result.error is None

    def test_failure_result(self):
        result = ExecutionResult(
            success=False,
            query="bad(X)",
            error="Prolog execution timed out after 10s",
        )
        assert result.success is False
        assert result.output == ""
