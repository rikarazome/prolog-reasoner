"""Pydantic data models for prolog-reasoner."""

from pydantic import BaseModel, Field


class TranslationRequest(BaseModel):
    """Input for translate_to_prolog tool."""

    query: str = Field(min_length=1, description="Natural language question")
    context: str = Field(default="", description="Additional premises or facts")
    max_corrections: int = Field(default=3, ge=0, le=10)
    rule_bases: list[str] = Field(
        default_factory=list,
        description=(
            "Names of saved rule bases to expose to the LLM in an "
            '"Available rule bases" section appended to the system prompt. '
            "Lets the translator reuse predicates defined in saved rule "
            "bases instead of reinventing them (v14)."
        ),
    )


class ExecutionRequest(BaseModel):
    """Input for execute_prolog tool."""

    prolog_code: str = Field(min_length=1, description="Prolog code")
    query: str = Field(min_length=1, description="Prolog query")
    rule_bases: list[str] = Field(
        default_factory=list,
        description=(
            "Names of saved rule bases to load. Concatenated before "
            "prolog_code in the given order (v14)."
        ),
    )
    max_results: int = Field(default=100, ge=1, le=10000)
    trace: bool = Field(
        default=False,
        description="Return structured proof trees in metadata.proof_trace",
    )


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


class RuleBaseInfo(BaseModel):
    """Element of list_rule_bases response (v14)."""

    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
