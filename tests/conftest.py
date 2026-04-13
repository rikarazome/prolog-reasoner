"""Shared test fixtures for prolog-reasoner."""

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from prolog_reasoner.config import Settings
from prolog_reasoner.executor import PrologExecutor
from prolog_reasoner.llm_client import LLMClient
from prolog_reasoner.reasoner import PrologReasoner
from prolog_reasoner.translator import PrologTranslator

FIXTURES_DIR = Path(__file__).parent / "fixtures"
LLM_RESPONSES_DIR = FIXTURES_DIR / "llm_responses"
PROLOG_FIXTURES_DIR = FIXTURES_DIR / "prolog"


@pytest.fixture
def settings():
    return Settings(llm_api_key="dummy")


@pytest.fixture
def executor(settings):
    return PrologExecutor(settings)


def load_recorded_response(name: str) -> str:
    """Load a recorded LLM response from fixtures."""
    path = LLM_RESPONSES_DIR / f"{name}.json"
    with open(path) as f:
        data = json.load(f)
    return data["response"]


def create_recorded_llm(responses: list[str]) -> AsyncMock:
    """Create a mock LLM client that replays recorded responses."""
    mock = AsyncMock(spec=LLMClient)
    mock.complete.side_effect = responses
    return mock


@pytest.fixture
def recorded_reasoner(settings, executor):
    """Factory fixture: create a PrologReasoner with recorded LLM responses.

    Usage:
        def test_something(recorded_reasoner):
            reasoner = recorded_reasoner(["socrates"])
    """
    def _factory(recording_names: list[str]) -> PrologReasoner:
        responses = [load_recorded_response(name) for name in recording_names]
        mock_llm = create_recorded_llm(responses)
        translator = PrologTranslator(mock_llm, settings)
        return PrologReasoner(translator, executor)

    return _factory
