"""MCP server for prolog-reasoner.

Exposes SWI-Prolog execution as an MCP tool. No LLM API key required —
the connected LLM (e.g. Claude) writes Prolog, this server executes it.
"""

import time

from fastmcp import FastMCP

from prolog_reasoner.config import Settings
from prolog_reasoner.errors import ConfigurationError, RuleBaseError
from prolog_reasoner.executor import PrologExecutor
from prolog_reasoner.logger import SecureLogger, setup_logging
from prolog_reasoner.models import ExecutionRequest
from prolog_reasoner.rule_base import RuleBaseStore, dedup_names

logger = SecureLogger(__name__)

_executor: PrologExecutor | None = None
_rule_base_store: RuleBaseStore | None = None


def _init() -> None:
    """Initialize settings, executor and rule base store on first use."""
    global _executor, _rule_base_store
    if _executor is not None:
        return
    settings = Settings()
    settings.validate_swipl()
    setup_logging(settings.log_level)
    _executor = PrologExecutor(settings)
    _rule_base_store = RuleBaseStore(settings, _executor)
    try:
        _rule_base_store.sync_bundled(settings.bundled_rules_dir)
    except RuleBaseError as exc:
        raise ConfigurationError(
            (
                f"Failed to sync bundled rule bases from "
                f"{settings.bundled_rules_dir}: {exc}"
            ),
            error_code="CONFIG_002",
        ) from exc


mcp = FastMCP("prolog-reasoner")


_BUSINESS_CODES = {"RULEBASE_001", "RULEBASE_002", "RULEBASE_003", "RULEBASE_005"}


def _rule_base_error_dict(exc: RuleBaseError, **extra: object) -> dict:
    """Convert a business-level RuleBaseError into a tool response dict.

    RULEBASE_004 (I/O) is an infrastructure error and MUST be re-raised by
    the caller; this helper asserts that precondition so callers cannot
    accidentally swallow it.
    """
    assert exc.error_code in _BUSINESS_CODES, (
        f"RuleBaseError({exc.error_code}) is not a business error; "
        "let it propagate."
    )
    payload: dict = {
        "success": False,
        "error": str(exc),
        "error_code": exc.error_code,
    }
    payload.update(extra)
    return payload


@mcp.tool()
async def execute_prolog(
    prolog_code: str,
    query: str,
    rule_bases: list[str] | None = None,
    max_results: int = 100,
    trace: bool = False,
) -> dict:
    """Execute Prolog code and return reasoning results.

    Write Prolog facts and rules, then run a query against them.
    Supports CLP(FD) constraints, negation-as-failure, and all
    standard SWI-Prolog features.

    Args:
        prolog_code: Prolog code (facts and rules).
        query: Prolog query to execute (e.g. "mortal(X)").
        rule_bases: Names of previously saved rule bases to include. Rules
            are prepended to ``prolog_code`` in the specified order. Use
            this for domain-specific rules (e.g. game mechanics, legal
            rules) that should be reused across queries.
        max_results: Maximum number of results (prevents infinite loops).
        trace: When True, include structured proof trees per solution in
            metadata.proof_trace. Adds meta-interpreter overhead; opt-in.
    """
    _init()
    request = ExecutionRequest(
        prolog_code=prolog_code,
        query=query,
        rule_bases=list(rule_bases or []),
        max_results=max_results,
        trace=trace,
    )

    # Dedup in order (§4.10) before resolving so repeat names don't hit disk.
    deduped = dedup_names(request.rule_bases)
    resolved: list[tuple[str, str]] = []
    # Time the actual disk I/O + read path (§4.10). Left at None when no
    # rule bases were requested so the metadata field stays absent.
    rule_base_load_ms: int | None = None
    if deduped:
        load_start = time.monotonic()
        for name in deduped:
            try:
                resolved.append((name, _rule_base_store.get(name)))
            except RuleBaseError as exc:
                if exc.error_code in ("RULEBASE_001", "RULEBASE_002"):
                    return {
                        "success": False,
                        "output": "",
                        "query": request.query,
                        "error": str(exc),
                        "metadata": {"error_code": exc.error_code},
                    }
                raise  # RULEBASE_004 → infra, propagate
        rule_base_load_ms = int((time.monotonic() - load_start) * 1000)

    result = await _executor.execute(
        prolog_code=request.prolog_code,
        query=request.query,
        rule_base_contents=resolved,
        max_results=request.max_results,
        trace=request.trace,
        rule_base_load_ms=rule_base_load_ms,
    )
    return result.model_dump()


@mcp.tool()
async def list_rule_bases() -> dict:
    """List all saved rule bases with description and tags.

    Returns ``{"rule_bases": [{"name": str, "description": str,
    "tags": list[str]}, ...]}`` sorted by name. Metadata is extracted
    from the leading ``% description:`` / ``% tags:`` comments of each
    rule base file (see §4.10).
    """
    _init()
    infos = _rule_base_store.list()
    return {"rule_bases": [info.model_dump() for info in infos]}


@mcp.tool()
async def get_rule_base(name: str) -> dict:
    """Retrieve the Prolog source of a saved rule base."""
    _init()
    try:
        content = _rule_base_store.get(name)
    except RuleBaseError as exc:
        if exc.error_code in ("RULEBASE_001", "RULEBASE_002"):
            return _rule_base_error_dict(exc)
        raise
    return {"success": True, "name": name, "content": content}


@mcp.tool()
async def save_rule_base(name: str, content: str) -> dict:
    """Save a named rule base containing Prolog rules that can be reused
    across ``execute_prolog`` calls.

    Use this for stable, reusable knowledge (e.g. ``piece_moves`` for
    chess piece movement rules). For one-time facts, include them
    directly in ``prolog_code`` instead.
    """
    _init()
    try:
        created = await _rule_base_store.save(name, content)
    except RuleBaseError as exc:
        if exc.error_code in ("RULEBASE_002", "RULEBASE_003", "RULEBASE_005"):
            return _rule_base_error_dict(exc)
        raise
    return {"success": True, "name": name, "created": created}


@mcp.tool()
async def delete_rule_base(name: str) -> dict:
    """Delete a saved rule base by name."""
    _init()
    try:
        _rule_base_store.delete(name)
    except RuleBaseError as exc:
        if exc.error_code in ("RULEBASE_001", "RULEBASE_002"):
            return _rule_base_error_dict(exc)
        raise
    return {"success": True, "name": name}


def main() -> None:
    """Entry point called from pyproject.toml [project.scripts]."""
    mcp.run()
