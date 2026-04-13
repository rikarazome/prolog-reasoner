"""MCP server for prolog-reasoner.

Exposes SWI-Prolog execution as an MCP tool. No LLM API key required —
the connected LLM (e.g. Claude) writes Prolog, this server executes it.
"""

from fastmcp import FastMCP

from prolog_reasoner.config import Settings
from prolog_reasoner.executor import PrologExecutor
from prolog_reasoner.logger import SecureLogger, setup_logging
from prolog_reasoner.models import ExecutionRequest

logger = SecureLogger(__name__)

_executor: PrologExecutor | None = None


def _init() -> None:
    """Initialize settings and executor on first use."""
    global _executor
    if _executor is not None:
        return
    settings = Settings()
    settings.validate_swipl()
    setup_logging(settings.log_level)
    _executor = PrologExecutor(settings)


mcp = FastMCP("prolog-reasoner")


@mcp.tool()
async def execute_prolog(
    prolog_code: str,
    query: str,
    max_results: int = 100,
) -> dict:
    """Execute Prolog code and return reasoning results.

    Write Prolog facts and rules, then run a query against them.
    Supports CLP(FD) constraints, negation-as-failure, and all
    standard SWI-Prolog features.

    Args:
        prolog_code: Prolog code (facts and rules).
        query: Prolog query to execute (e.g. "mortal(X)").
        max_results: Maximum number of results (prevents infinite loops).
    """
    _init()
    request = ExecutionRequest(
        prolog_code=prolog_code,
        query=query,
        max_results=max_results,
    )
    result = await _executor.execute(
        prolog_code=request.prolog_code,
        query=request.query,
        max_results=request.max_results,
    )
    return result.model_dump()


def main() -> None:
    """Entry point called from pyproject.toml [project.scripts]."""
    mcp.run()
