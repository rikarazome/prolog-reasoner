"""Pydantic data models for prolog-reasoner."""

from pydantic import BaseModel, Field


class TranslationRequest(BaseModel):
    """Input for translate_to_prolog tool."""

    query: str = Field(min_length=1, description="Natural language question")
    context: str = Field(default="", description="Additional premises or facts")
    max_corrections: int = Field(default=3, ge=0, le=10)


class ExecutionRequest(BaseModel):
    """Input for execute_prolog tool."""

    prolog_code: str = Field(min_length=1, description="Prolog code")
    query: str = Field(min_length=1, description="Prolog query")
    max_results: int = Field(default=100, ge=1, le=10000)


class TranslationResult(BaseModel):
    """Result of NL to Prolog translation."""

    success: bool
    prolog_code: str = ""
    suggested_query: str = ""
    error: str | None = None
    metadata: dict = Field(default_factory=dict)


class ExecutionResult(BaseModel):
    """Result of Prolog execution."""

    success: bool
    output: str = ""
    query: str = ""
    error: str | None = None
    metadata: dict = Field(default_factory=dict)
