# prolog-reasoner Specification v14

## Context

### Concept

LLMs are strong at natural language but weak at formal logical reasoning. Prolog is the opposite — rigorous at logical inference but unable to process natural language. This project exploits that complementarity, providing an MCP server + Python library that lets an LLM use Prolog as a "logic calculator."

### Core Thesis

**By materializing and directly executing an intermediate Prolog representation, we turn the black-box problem of AI logical reasoning into something "verifiable."** Instead of the LLM reasoning inside its head, the reasoning is written out as inspectable Prolog code and executed, leaving the inputs, rules, and derivation path as an auditable artifact.

### Standalone Utility of This Project

This project by itself is a general-purpose, stateless Prolog MCP server + Python library. It ships through both pip and MCP, and is usable on its own for:

- **Single-shot logical reasoning assistance for LLMs**: a "logic calculator" that Claude or any MCP client can invoke mid-task — for CLP(FD) constraint satisfaction, rule-based inference, search, and similar workloads (on the bundled 30-problem benchmark with Sonnet 4.6, accuracy rises from 73% → 90%, with +40–60pt gains concentrated in constraint satisfaction and multi-step reasoning).
- **Advantages of stateless design**: most Prolog MCP servers retain session state (consulted predicates, dynamically asserted clauses); this server makes **each call fully independent** — code, query, and rule-base names are received every call and discarded on exit. Consequences:
  - No state-pollution risk across sessions
  - The LLM can compose the problem from scratch each time (particularly strong for single-shot tasks)
  - Calls are purely functional, easy to parallelize and reproduce

### As a Foundation for Domain-Specialized Forks

At the same time, this project is designed as a **fork foundation for domain-specialized projects**. For domains with high error costs and regulatory pressure (contract-clause review, game-rule validation, tax scenarios, etc.), the expectation is that someone forks this project and bundles domain-specific rule bases into a **derivative project**. Such derivatives are independent projects and lie outside the scope of this specification.

The rule-base feature (v14) raises the project's standalone reusability and, simultaneously, serves as the substrate for domain assets in forked derivatives — the design allows accumulation of a Prolog knowledge base that users can fork, edit, and validate.

### Axes of Differentiation

- **Verifiability (core)**: externalization of reasoning as a Prolog intermediate representation, with `proof_trace` (v13, opt-in) for after-the-fact auditability
- **Statelessness (main-body axis)**: strong on single-shot tasks, no session pollution, easy to run in parallel
- **Fork-capable rule-base foundation (derivative axis)**: the extension path into domain-specialized projects is guaranteed by design. This moves the center of gravity from a general "verify AI reasoning" framing into derivative projects that **"help AI author auditable domain rules."**

### Relation to Prior Work

Academic prototypes exist (Logic-LM, ChatLogic, LoRP, etc.), but none ship as a `pip install`-ready implementation. This project focuses not on academic novelty but on **distributable lower-layer infrastructure + accumulation of domain assets**.

### Two Distribution Surfaces (MCP and Library)

From v13 onward, this project has two independent distribution surfaces:

| Surface | User | NL→Prolog translation | External API key required |
|---------|------|----------------------|--------------------------|
| **MCP server** | A connected LLM (Claude etc.) invokes it as a tool | **Done on the LLM side** (Claude writes Prolog itself) | None |
| **Python library** | Developers embedding an LLM in their own program | Done internally by the library's LLMClient | OpenAI / Anthropic |

Both share `PrologExecutor` (the Prolog execution engine). The library stacks `PrologTranslator` / `LLMClient` on top, providing NL→Prolog translation and a self-correction loop. **The MCP server has no translation feature** — the connected LLM uses its own reasoning to produce Prolog.

### Changelog

- v1 (2026-04-13): Initial version
- v2 (2026-04-13): Full revision reflecting architecture / security / consistency review
- v3 (2026-04-13): Security policy redesigned as "runaway prevention only." Excessive sandboxing removed
- v4 (2026-04-13): Output format switched to raw text. All review findings (consistency, naming, dependencies, etc.) applied
- v5 (2026-04-13): Clarified error-handling boundaries, Windows UTF-8 support, validate_syntax details, DI wiring, LLM timeout added
- v6 (2026-04-13): UTF-8 header moved to prepend (3-layer structure), translate_with_correction return type unified to TranslationResult, LLMClient.__init__ fix
- v7 (2026-04-14): LLMClient.complete() timeout setting applied, dead config removed, translate() error contract made explicit, main() definition added
- v8 (2026-04-14): executor.execute() timeout setting applied, newline separator between 3 layers specified, FastMCP instance defined, error-code comments corrected
- v9 (2026-04-14): reasoner.translate() delegation target clarified, validate_swipl() hardened, setup_logging() duplicate prevention, API-key pattern fix
- v10 (2026-04-14): Unreachable REDACT_PATTERNS entries removed; metadata.result_count computation defined
- v11 (2026-04-14): error_code added to ConfigurationError in validate_swipl(); result_count definition for the no-solution case clarified
- v12 (2026-04-14): trailing-period stripping for suggested_query documented (external review)
- v13 (2026-04-14): MCP / library responsibility split. `translate_to_prolog` removed from MCP, `llm_api_key` made optional, server.py switched to lazy init
- v14 (2026-04-16): Rule-base feature added. `rule_bases` parameter on `execute_prolog`, four new MCP tools (list/get/save/delete), `validate_syntax()` switched to parse-only (library(clpfd) operators pre-declared so syntax check still passes), `rule_bases` field added to both `ExecutionRequest` and `TranslationRequest`, `trace` field added to `ExecutionRequest`, `PrologReasoner.translate()` wired to forward `self.rule_base_store` to `translate_with_correction`, `rules_dir` / `bundled_rules_dir` / `max_rule_size` / `max_rule_prompt_bytes` added to Settings, `RULEBASE_001`–`005` error codes added

---

## 1. Architecture Overview

```
                   ┌─────────────────────┐
                   │  PrologExecutor     │
                   │  (SWI-Prolog exec)  │  ← shared component
                   └──────────▲──────────┘
                              │
          ┌───────────────────┴──────────────────┐
          │                                      │
┌─────────┴──────────┐              ┌────────────┴─────────────┐
│  server.py         │              │  reasoner.py             │
│  (MCP server)      │              │  (library API)           │
│                    │              │                          │
│  exposes only      │              │  translate() / execute() │
│  execute_prolog    │              │  + PrologTranslator      │
│                    │              │  + LLMClient             │
│  no API key        │              │  API key required        │
└─────────▲──────────┘              └────────────▲─────────────┘
          │ stdio                                │
┌─────────┴──────────┐              ┌────────────┴─────────────┐
│  connected LLM     │              │  user's Python app       │
│  (Claude etc.)     │              │                          │
│  writes Prolog     │              │  via OpenAI/Anthropic    │
│  itself            │              │                          │
└────────────────────┘              └──────────────────────────┘
```

**The core of the responsibility split:** the MCP server is nothing more than "a remote entrypoint into the Prolog execution engine." Because the connected LLM uses its own reasoning to generate Prolog, the MCP layer contains neither a translator nor an LLM client. The library, conversely, provides the full "NL → Prolog → execute" pipeline and targets developers embedding an LLM into their program. Both live in the same package (`prolog_reasoner`), but **import dependency is one-way** (server.py does not import translator or llm_client).

**Persisting Prolog intermediate representations to files is not this system's responsibility.** Saving the `prolog_code` that MCP's `execute_prolog` receives is done by the MCP client (the LLM) using its own tools (Write etc.). prolog-reasoner only deals with code as string input/output.

### Changes and Rationale

| Change | Rationale |
|--------|-----------|
| **4 layers → 3 layers** (executor + corrector merged) | Self-correction is part of translation; there's no value in making it an independent component |
| **engine.py → reasoner.py** (facade pattern) | This is the library's public API. translate and execute are independent operations, composed by the caller (the LLM) |
| **Abstract backend removed** (subprocess only) | Janus is unnecessary at the MVP stage. Prolog execution (1–100ms) is noise against LLM API calls (1–3s). YAGNI |
| **LiteLLM → direct API calls** | 100+ provider support is overkill for MVP. Use OpenAI/Anthropic SDKs directly; add abstraction later |
| **prompts.py folded into translator.py** | Prompts are an internal detail of the translator; separating them makes translation logic harder to understand |
| **Self-correction lives in Translator** | The correction loop is "improving translation quality" — a translator responsibility. Executor handles pure execution |

### Design Principles

1. **The MCP layer holds no business logic** — delegates to the Core layer
2. **The Core layer does not depend on MCP** — usable as a standalone Python library
3. **Never print to stdout** — it breaks the JSON-RPC protocol. Everything goes to stderr
4. **YAGNI** — don't build abstractions you don't need now; refactor when you do
5. **Runaway prevention is enabled by default** — timeout and result-count limits stop unintended runaways
6. **Don't over-restrict features in the name of security** — see §5 Security Policy

---

## 2. MCP Tool Design

### Design Decision: `execute_prolog` is primary; rule-base CRUD is auxiliary (v14)

v1 had three tools (`reason`, `execute_prolog`, `generate_prolog`); v2–v12 had two (`translate_to_prolog` + `execute_prolog`); v13 reduced this to **`execute_prolog` only** — a single tool. v14 adds four auxiliary tools for rule-base CRUD (five tools total).

**v14 rationale:**
- The primary feature is still `execute_prolog` (in practice almost every call is this one)
- Rule-base CRUD (create / read / update / delete) is a secondary operation and doesn't fit inside `execute_prolog`'s arguments
- The cognitive load of adding tools is minor (file CRUD is a concept LLMs handle routinely)

**Inherited from v13 — translation stays out of MCP:**
- An LLM connected to the MCP server (Claude etc.) can do NL→Prolog translation with its own reasoning. Calling a different LLM API server-side adds nothing
- Keeping `translate_to_prolog` would force the MCP server to require an external LLM API key, confusing users who wonder "why do I need a key?"
- The translation feature itself is not deleted — it remains in the library-side `PrologReasoner.translate()` (§4.2, §4.3)

### Tool: `execute_prolog`

```python
@mcp.tool()
async def execute_prolog(
    prolog_code: str,
    query: str,
    rule_bases: list[str] | None = None,
    max_results: int = 100,
    trace: bool = False,
) -> dict:
    """
    Execute Prolog code and return inference results.
    Works on code the connected LLM just wrote, code produced by the
    library-side `PrologReasoner.translate()`, or hand-written code.

    Args:
        prolog_code: Prolog code to execute (facts and rule definitions)
        query: Prolog query to run (e.g. "mortal(X)")
        rule_bases: list of saved rule-base names to load. Prepended to
                    prolog_code in the given order (§4.4). None / [] means
                    "unspecified." A missing name yields RULEBASE_001;
                    a name-validation failure yields RULEBASE_002.
        max_results: maximum number of solutions to return (prevents
                     runaway loops)
        trace: if True, include a structured proof tree in
               `metadata.proof_trace`
    """
```

### Tools: `list_rule_bases` / `get_rule_base` / `save_rule_base` / `delete_rule_base`

Server-side CRUD for rule bases (named sets of Prolog code). See §4.10 for details.
Across all CRUD tools, RULEBASE_004 (I/O failure) propagates as an infrastructure fault — it doesn't appear in the return dict; FastMCP turns it into an error response.

**Shape of the error dict (note the difference between CRUD and `execute_prolog`):**
- The four CRUD tools (`list/get/save/delete_rule_base`) return business errors with a **top-level** `error_code`:
  `{"success": False, "error": str, "error_code": "RULEBASE_xxx"}`
- `execute_prolog` passes through `ExecutionResult`, so business errors arrive via `metadata.error_code`:
  `{"success": False, "output": "", "query": str, "error": str, "metadata": {"error_code": "RULEBASE_xxx"}}`
- Clients need to handle both paths (CRUD directly, `execute_prolog` via `ExecutionResult`).

```python
@mcp.tool()
async def list_rule_bases() -> dict:
    """Returns: {"rule_bases": [{"name": str, "description": str, "tags": list[str]}, ...]}
    Sorted ascending by name."""

@mcp.tool()
async def get_rule_base(name: str) -> dict:
    """Returns: {"success": True, "name": str, "content": str}
               or RULEBASE_001 (missing) / RULEBASE_002 (invalid name) error"""

@mcp.tool()
async def save_rule_base(name: str, content: str) -> dict:
    """Returns: {"success": True, "name": str, "created": bool}
               or RULEBASE_002 (invalid name) / RULEBASE_003 (syntax) / RULEBASE_005 (size) error"""

@mcp.tool()
async def delete_rule_base(name: str) -> dict:
    """Returns: {"success": True, "name": str}
               or RULEBASE_001 (missing) / RULEBASE_002 (invalid name) error"""
```

**Return value:**
```json
{
    "success": true,
    "output": "mortal(socrates)\n",
    "query": "mortal(X)",
    "metadata": {
        "backend": "subprocess",
        "execution_time_ms": 12,
        "result_count": 1,
        "truncated": false
    }
}
```

### Design Decisions: Clarifying Parameters

| v1 problem | v2+ solution | Rationale |
|-----------|--------------|-----------|
| `prolog_source` accepted both code and path | Restrict to `prolog_code` (code only) | Distinguishing code from path is ambiguous; file reading belongs on the MCP client side |
| `context` type inconsistent (str vs dict) | Unified to `str` | Easier for the LLM to build. If structure is needed, extend later |
| `query` was optional with auto-inference | Make `query` required in `execute_prolog` | The auto-inference algorithm was undefined and ambiguous; require explicit specification |
| `export_path` was a tool parameter | Removed. File saving is an MCP-client responsibility | prolog-reasoner only handles code strings |
| `explanation` in the return value | Removed. If needed, the LLM can interpret results itself | Extra LLM calls just to generate an explanation are excessive |
| `results` was a structured dict | Changed to `output` (raw text) | Eliminates parser complexity and bug risk; plenty for the LLM to interpret |

### Output format

`execute_prolog`'s `output` returns SWI-Prolog's output text verbatim.

Because the Prolog wrapper the executor auto-generates for the user's query (see §4.4) controls the output, the format is predictable:

```
% For mortal(X) — one line per solution, write_canonical form
mortal(socrates)
mortal(plato)

% For mortal(socrates) (no variables) — instantiated query term as-is
mortal(socrates)

% No solutions
false

% When max_results is exceeded — trailing truncation marker
num(1)
num(2)
num(3)
__TRUNCATED__
```

**Rules:**
- Each solution is one line, one term, via `write_canonical/1`; same format whether variables are bound or not
- No solutions = the single line `false`
- `max_results` exceeded = trailing `__TRUNCATED__` marker
- The LLM understands from context: "mortal(socrates) printed = that predicate holds"

**Design decision:** the LLM (the primary consumer) reads the text above naturally. If the Python-library path later needs structured data, add a `results` field alongside `output` (see §11 Future Extensions).

---

## 3. Project Layout

```
prolog-reasoner/
├── pyproject.toml
├── README.md
├── LICENSE (MIT)
├── .gitignore
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
├── src/
│   └── prolog_reasoner/
│       ├── __init__.py
│       ├── server.py              # FastMCP server + tool definitions
│       ├── reasoner.py            # public API (library entry point)
│       ├── translator.py          # NL→Prolog + self-correction loop
│       ├── executor.py            # Prolog execution (incl. query wrapper)
│       ├── rule_base.py           # rule-base CRUD (v14)
│       ├── llm_client.py          # thin LLM-API abstraction
│       ├── models.py              # Pydantic data models
│       ├── config.py              # configuration
│       ├── errors.py              # exception hierarchy
│       └── logger.py              # structured logging (stderr only)
├── tests/
│   ├── conftest.py                # shared fixtures
│   ├── unit/
│   │   ├── test_translator.py
│   │   ├── test_executor.py
│   │   ├── test_rule_base.py      # v14
│   │   └── test_models.py
│   ├── integration/
│   │   ├── test_reasoner.py
│   │   └── test_mcp_server.py
│   └── fixtures/
│       ├── prolog/                # test .pl files
│       ├── rule_bases/            # test rule bases (v14)
│       └── llm_responses/         # recorded LLM responses
└── examples/
    └── standalone_usage.py
```

### Changes and Rationale

| Change | Rationale |
|--------|-----------|
| `mcp/` subpackage dropped → `server.py` at root | A subpackage for a single-file tool definition is overkill |
| `prolog/backends/` dropped | Abstract backend removed (subprocess only) |
| `core/` subpackage dropped → flat layout | Few files; nesting is unnecessary |
| `llm/` subpackage dropped → `llm_client.py` | Subpackage around one file is overkill |
| `config/` dropped → `config.py` | Same |
| `sandbox.py` dropped | Security policy change; no sandbox needed (§5) |
| `tests/security/` dropped | Same. Runaway-prevention tests live in test_executor.py |
| `tests/fixtures/llm_responses/` added | Mitigates LLM non-determinism; record/replay testing |
| `pipeline.py` → `reasoner.py` | The real shape is a facade (API of two independent ops); "pipeline" was inaccurate |
| `logging.py` → `logger.py` | Avoids clashing with Python stdlib `logging` |
| `test_pipeline.py` → `test_reasoner.py` | Filename alignment |

---

## 4. Core Component Design

### 4.1 Data Models (models.py)

```python
from pydantic import BaseModel, Field

class TranslationRequest(BaseModel):
    """Input to PrologReasoner.translate() (library use)"""
    query: str = Field(min_length=1, description="Natural-language question")
    context: str = Field(default="", description="Additional premises")
    max_corrections: int = Field(default=3, ge=0, le=10)
    rule_bases: list[str] = Field(default_factory=list,
        description="Saved rule-base names to expose to the prompt. "
                    "An `Available rule bases:` section is appended to the "
                    "system prompt so the translator reuses existing predicates "
                    "instead of reinventing them (v14, §4.3)")

class ExecutionRequest(BaseModel):
    """Input to the execute_prolog tool"""
    prolog_code: str = Field(min_length=1, description="Prolog code")
    query: str = Field(min_length=1, description="Prolog query")
    rule_bases: list[str] = Field(default_factory=list,
        description="Saved rule-base names to load. Prepended to prolog_code "
                    "in the given order (v14)")
    max_results: int = Field(default=100, ge=1, le=10000)
    trace: bool = Field(default=False,
        description="Include a structured proof tree in metadata.proof_trace (v0.2.0)")

class TranslationResult(BaseModel):
    """Translation outcome"""
    success: bool
    prolog_code: str = ""
    suggested_query: str = ""
    error: str | None = None
    metadata: dict = Field(default_factory=dict)

class ExecutionResult(BaseModel):
    """Execution outcome"""
    success: bool
    output: str = ""
    query: str = ""
    error: str | None = None
    metadata: dict = Field(default_factory=dict)

class RuleBaseInfo(BaseModel):
    """Element of list_rule_bases (v14)"""
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
```

**Changes from v1:**
- The monolithic `ReasoningRequest` (shared across all modes) is gone — each tool has its own model
- `context` unified to `str` (type inconsistency resolved)
- `query` made required in `ExecutionRequest` (auto-inference ambiguity removed)
- `results: list[dict[str, str]]` changed to `output: str` (raw-text approach)
- `explanation` field removed (the LLM interprets results itself)

### 4.2 Public API (reasoner.py)

The library's entry point. Both server.py and standalone usage access the Core layer through this class.

```python
class PrologReasoner:
    """Public API of the prolog-reasoner library"""
    def __init__(
        self,
        translator: PrologTranslator,
        executor: PrologExecutor,
        rule_base_store: RuleBaseStore | None = None,  # v14: resolves rule_bases
    ):
        self.translator = translator
        self.executor = executor
        self.rule_base_store = rule_base_store

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        """
        Delegates to self.translator.translate_with_correction().
        Passes self.executor to the executor argument (for syntax validation).
        When `request.rule_bases` is non-empty, also forwards
        `self.rule_base_store` so rule-base content can be exposed in the
        translation prompt (v14, §4.3). Symmetric DI contract with `execute()`:
        if `rule_bases` is non-empty but `rule_base_store` is None, raises
        ValueError (misconfiguration).
        LLMError (infrastructure failure) is raised through.
        """

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        """
        Resolves request.rule_bases (list of names) into content via
        self.rule_base_store, then delegates to self.executor.execute().
        Resolution steps mirror server.py's `execute_prolog` (dedup → each
        name via store.get() → list of tuples).

        - If request.rule_bases is non-empty and self.rule_base_store is
          None, raises ValueError (DI misconfiguration)
        - RuleBaseError(RULEBASE_001/002) is caught as a business error and
          converted to ExecutionResult(success=False, metadata={"error_code": ...})
        - RuleBaseError(RULEBASE_004) and BackendError (infrastructure) are
          propagated
        """
```

**Rationale:** `translate` and `execute` are independent operations. They are not implicitly chained internally. The LLM (MCP client) composes them explicitly.

**Initialization (component wiring):**

server.py (MCP) and library usage take different init paths (v13).

**server.py (MCP) — Executor and RuleBaseStore lazy init (v14):**

```python
# server.py
_executor: PrologExecutor | None = None
_rule_base_store: RuleBaseStore | None = None

def _init() -> None:
    """Initialize settings, executor and rule base store on first use."""
    global _executor, _rule_base_store
    if _executor is not None:
        return
    settings = Settings()                # load from env (llm_api_key not required)
    settings.validate_swipl()            # check SWI-Prolog presence
    setup_logging(settings.log_level)
    _executor = PrologExecutor(settings)
    _rule_base_store = RuleBaseStore(settings, _executor)
    # copy-on-first-use: .pl files bundled by a forking package are copied
    # into rules_dir (§4.10).
    # If sync_bundled raises RuleBaseError(RULEBASE_004), promote it to
    # ConfigurationError(CONFIG_002) — treat it as a fatal startup error.
    try:
        _rule_base_store.sync_bundled(settings.bundled_rules_dir)
    except RuleBaseError as e:
        raise ConfigurationError(
            f"Failed to sync bundled rule bases from {settings.bundled_rules_dir}: {e}",
            error_code="CONFIG_002",
        ) from e

mcp = FastMCP("prolog-reasoner")

@mcp.tool()
async def execute_prolog(
    prolog_code: str,
    query: str,
    rule_bases: list[str] | None = None,   # ★ avoid mutable default
    max_results: int = 100,
    trace: bool = False,
) -> dict:
    _init()
    # rule_bases: None or [] means "no rule bases requested"
    # 1. Deduplicate preserving order (list(dict.fromkeys(rule_bases or [])))
    # 2. For each name, fetch content via _rule_base_store.get(name)
    # 3. Handling RuleBaseError:
    #    - RULEBASE_001 (missing): business error. Catch and return
    #      ExecutionResult(success=False, error=e.args[0],
    #                      metadata={"error_code":"RULEBASE_001"})
    #    - RULEBASE_002 (invalid name): business error. Catch and return
    #      ExecutionResult(success=False, error=e.args[0],
    #                      metadata={"error_code":"RULEBASE_002"})
    #    - RULEBASE_004 (I/O failure): infrastructure. Do NOT catch; let it
    #      propagate (FastMCP turns it into an MCP error response).
    # 4. Pass resolved [(name, content), ...] to _executor.execute()
    # ... delegate: _executor.execute(prolog_code, query,
    #                                 rule_base_contents=resolved,
    #                                 max_results=max_results, trace=trace)

@mcp.tool()
async def list_rule_bases() -> dict:
    _init()
    # Delegate to _rule_base_store.list().
    # RuleBaseError(RULEBASE_004) propagates as infrastructure fault.

@mcp.tool()
async def get_rule_base(name: str) -> dict:
    _init()
    # Delegate to _rule_base_store.get(name). RuleBaseError is converted as:
    #   RULEBASE_001 (missing)       → {"success":False, "error":..., "error_code":"RULEBASE_001"}
    #   RULEBASE_002 (invalid name)  → {"success":False, "error":..., "error_code":"RULEBASE_002"}
    #   RULEBASE_004 (I/O failure)   → propagate (infrastructure)

@mcp.tool()
async def save_rule_base(name: str, content: str) -> dict:
    _init()
    # Delegate to await _rule_base_store.save(name, content). RuleBaseError:
    #   RULEBASE_002 (invalid name)    → {"success":False, "error":..., "error_code":"RULEBASE_002"}
    #   RULEBASE_003 (syntax error)    → {"success":False, "error":..., "error_code":"RULEBASE_003"}
    #   RULEBASE_005 (too large)       → {"success":False, "error":..., "error_code":"RULEBASE_005"}
    #   RULEBASE_004 (I/O failure)     → propagate (infrastructure)
    # Success: {"success":True, "name":name, "created":bool}

@mcp.tool()
async def delete_rule_base(name: str) -> dict:
    _init()
    # Delegate to _rule_base_store.delete(name). RuleBaseError:
    #   RULEBASE_001 (missing)       → {"success":False, "error":..., "error_code":"RULEBASE_001"}
    #   RULEBASE_002 (invalid name)  → {"success":False, "error":..., "error_code":"RULEBASE_002"}
    #   RULEBASE_004 (I/O failure)   → propagate (infrastructure)

def main() -> None:
    """Startup function called from pyproject.toml [project.scripts]"""
    mcp.run()
```

**Why lazy init:**
- Avoid running the SWI-Prolog presence check on mere `from prolog_reasoner.server import mcp`
- Don't crash in test, docs-build, or other import-only scenarios
- Init happens once on the first tool call (`sync_bundled` copy-on-first-use runs at the same moment)

**Library usage (full pipeline) — assemble PrologReasoner yourself:**

```python
# user code or examples/standalone_usage.py
settings = Settings(llm_api_key="sk-...")
settings.validate_swipl()
setup_logging(settings.log_level)
llm_client = LLMClient(
    provider=settings.llm_provider,
    api_key=settings.llm_api_key,
    model=settings.llm_model,
    timeout_seconds=settings.llm_timeout_seconds,
)
executor = PrologExecutor(settings)
reasoner = PrologReasoner(
    translator=PrologTranslator(llm_client, settings),
    executor=executor,
    rule_base_store=RuleBaseStore(settings, executor),  # v14: required if using rule_bases
)
```

**Important:** the `prolog_reasoner` package exposes `PrologReasoner`, `PrologTranslator`, `PrologExecutor`, etc., but **does not provide a "full-auto assembly helper" like `create_reasoner()`**. It existed before v12, but v13 removed it (MCP no longer uses translation). Library users wire DI explicitly as above (prioritizing flexibility — tweaking Settings, injecting mock LLMs, etc.).

### 4.3 Translator (translator.py)

**Design decision: 3 phases → 1 phase + self-correction**

v1's Logic-LM 3-phase flow (semantic translation → syntax conversion → validation) is academically interesting but a single prompt suffices for the MVP. The self-correction loop catches syntax errors, so pre-stage conversion is unnecessary.

```python
class PrologTranslator:
    """Natural language → Prolog translation + self-correction"""

    SYSTEM_PROMPT = """You are a Prolog code generator for SWI-Prolog.
Convert natural language facts and queries into valid Prolog code.

RULES:
- Output ONLY valid Prolog code, no markdown or explanations
- Use lowercase for atoms, uppercase for variables
- Include a comment "% Query: <query>" indicating the suggested query
- Use standard SWI-Prolog predicates
- Use CLP(FD) library (:- use_module(library(clpfd)).) for constraint problems
"""

    async def translate(
        self, query: str, context: str = ""
    ) -> tuple[str, str]:
        """
        Returns: (prolog_code, suggested_query)
        Raises: TranslationError — LLM returned an empty response (TRANSLATION_001)
        """

    async def translate_with_correction(
        self, query: str, context: str, executor: PrologExecutor,
        max_corrections: int,
        rule_bases: list[str] | None = None,
        rule_base_store: RuleBaseStore | None = None,
    ) -> TranslationResult:
        """
        Translation + syntax-validation loop:
        1. Generate Prolog via translate() (when rule_bases is non-empty, embed
           each rule-base content into the system prompt as an
           `Available rule bases:` section. v14 §4.10)
        2. Run executor.validate_syntax() to check syntax
        3. If errors, re-translate with the error message appended
        4. Iterate up to max_corrections times
        5. Success → TranslationResult(success=True, prolog_code=..., suggested_query=...)
           Exceeded corrections → TranslationResult(success=False, error="...",
                                                    metadata={"error_code": "TRANSLATION_002"})

        If the total embedded rule-base size exceeds settings.max_rule_prompt_bytes,
        the overflow is truncated and metadata.rule_bases_truncated=True is set
        (protects the LLM's context window). The budget deducts both the
        inter-block `"\n".join` separators (N-1 bytes) and the truncation marker
        (`... [truncated]`) before slicing.

        Argument-pair contract:
        - If `rule_bases` is non-empty and `rule_base_store` is None → raise
          ValueError (caller misuse, treated as a programming error not a
          business error)
        - If `rule_bases` is None or empty, `rule_base_store` may be None

        RuleBaseError contract: when resolving names via rule_base_store.get(name),
        if RuleBaseError is raised, apply the layer rule from §4.8:
        - RULEBASE_001 (missing): business error. Catch and convert to
          TranslationResult(success=False,
                            error="rule base '{name}' not found[: did you mean '{suggestion}'?]",
                            metadata={"error_code": "RULEBASE_001"})
        - RULEBASE_002 (invalid name): business error. Catch and convert to
          TranslationResult(success=False, error=..., metadata={"error_code": "RULEBASE_002"})
        - RULEBASE_004 (I/O failure): infrastructure. Let it propagate.
        """
```

**Where the self-correction loop lives:** inside the Translator. Correction is "improving translation quality" — a translator responsibility. The Executor only exposes `validate_syntax()`, which the translator calls.

**Prompt design rationale:** keep LLM instructions minimal so it can use SWI-Prolog features freely. Library use (CLP(FD), etc.) is not restricted.

**Extracting `suggested_query`:** parse the `% Query: <query>` comment from the LLM output. After extraction, strip a trailing period (`.`) and surrounding whitespace (LLMs sometimes emit `% Query: mortal(socrates).` with a period; embedding that in the wrapper would cause a syntax error). If the LLM omitted the comment, return `suggested_query = ""` (the LLM or user can specify it later).

### 4.4 Executor (executor.py)

```python
class PrologExecutor:
    """SWI-Prolog subprocess execution"""

    async def execute(
        self, prolog_code: str, query: str,
        rule_base_contents: list[tuple[str, str]] | None = None,
        max_results: int = 100,
        trace: bool = False,
        timeout_seconds: float | None = None,
    ) -> ExecutionResult:
        """
        timeout_seconds: defaults to Settings.execution_timeout_seconds
        rule_base_contents: defaults to empty list. Each element is
            (name, prolog_text). Name→content resolution is done upstream
            (server.py / reasoner.py) via RuleBaseStore.get(); deduplication
            is also the caller's responsibility. The name is used only for
            metadata/trace source display; the executor does NOT raise
            RULEBASE_001 (that is concentrated in §4.10).

        1. Prepend the UTF-8 header to prolog_code (joined with \n)
        2. Insert each rule-base prolog_text between the header and user
           code (all joined with \n). Record load time in
           metadata.rule_base_load_ms; record used names in
           metadata.rule_bases_used.
        3. Append the query wrapper to prolog_code (joined with \n)
        4. Spawn SWI-Prolog as a subprocess
        5. Send the combined code over stdin
           (header + \n + rb[0].text + \n + ... + \n + user code + \n + wrapper)
        6. Run under timeout supervision (kill the process on timeout)
        7. Return stdout as output
        """

    async def validate_syntax(self, prolog_code: str) -> str | None:
        """
        Parse-only syntax check (v14).

        Implementation:
        - Use read_term/3 (with a stream) to parse each clause.
          **Do not execute** directives inside the user code (exception:
          :- op(P,A,Ops), which must be executed so subsequent parses see
          the operator table).
        - Pre-declare the operators of library(clpfd) in the validation
          script (#<, #=, #\\=, in, ins, .., #<==>, #==>, #<==, #\\/, #/\\,
          #\\ etc.). Since :- use_module is not executed, this is how
          typical constraint programs still pass syntax validation;
          use_module itself is simply ignored.
        - Prepend the UTF-8 stream setup to the code (same as execute(),
          joined with \n)
        - Apply the same timeout as execute() (execution_timeout_seconds)
        - If read_term throws, stringify the position and the error kind
        - On timeout, proc.kill() + proc.wait() to reliably reap the process

        Public-API contract change vs pre-v13:
        - Runtime errors of :- use_module/initialization/consult are
          **no longer detected** (only pure syntax errors)
        - Side-effecting directives (:- halt. etc.) are safely ignored
        - Operators defined by libraries other than clpfd are not
          pre-declared, so code using them still fails syntax check.
          Workarounds: write :- op(...). explicitly at the top of user
          code, or skip validation (max_corrections=0).

        Returns: error message, or None (no error)
        """
```

**Starting SWI-Prolog:**
```python
proc = await asyncio.create_subprocess_exec(
    self.swipl_path,
    '-f', 'none',              # skip the user's init file (reproducibility)
    '-q',                      # suppress banner / help messages
    stdin=asyncio.subprocess.PIPE,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
    env={**os.environ, 'LANG': 'C.UTF-8'},  # Linux/macOS fallback
                                             # (the primary UTF-8 fix is set_stream in the wrapper)
)
```

**Three-layer Prolog input structure:**

The executor sandwiches the user's Prolog code between a header and a wrapper. Layers are joined with `\n` (so the `.` that terminates the user's final clause is still recognized even if the user's code didn't end with a newline). `<QUERY>` and `<MAX_RESULTS>` are injected via string replacement (`.replace()`) — not `.format()`, because Prolog uses `{}` (e.g. DCG) which would clash.

```python
prolog_input = HEADER + "\n" + prolog_code + "\n" + WRAPPER
```

```
[1. UTF-8 header]    ← prepended to user code
[    \n separator ]
[2. user code    ]   ← prolog_code verbatim
[    \n separator ]
[3. query wrapper]   ← appended after user code
```

**1. UTF-8 header (prepend):**
```prolog
:- set_stream(user_input, encoding(utf8)).
:- set_stream(user_output, encoding(utf8)).
:- set_prolog_flag(verbose, silent).
```

Runs before the user code, putting subsequent stdin reads into UTF-8 mode. `:- set_stream(user_input, encoding(utf8)).` is itself ASCII, so it parses correctly under any encoding. This lets non-ASCII atoms (Japanese etc.) in user code parse correctly.

**2. Query wrapper (append):**
```prolog
:- nb_setval('__pr_count', 0).
:- ( <QUERY>,
     nb_getval('__pr_count', N),
     ( N >= <MAX_RESULTS>
     -> (write('__TRUNCATED__'), nl, !)
     ;  (N1 is N + 1,
         nb_setval('__pr_count', N1),
         write_canonical(<QUERY>), nl,
         fail)
     )
   ; true
   ),
   nb_getval('__pr_count', Final),
   ( Final =:= 0 -> write(false), nl ; true ),
   halt(0).
:- halt(1).
```

**Wrapper rationale:**
- `write_canonical/1` prints each solution as one line, one term (Prolog standard form; LLM-readable)
- `fail` drives backtracking explicitly, seeking the next solution
- `nb_setval` / `nb_getval` manage the counter (`findall` is avoided — it accumulates all solutions in memory and risks OOM)
- When the counter hits `max_results`, print `__TRUNCATED__` and cut (`!`) to stop searching
- `; true` captures the post-enumeration state
- On zero solutions, print `false`
- For variable-free queries (e.g. `mortal(socrates)`), `write_canonical` still prints the instantiated term (uniform format across all queries)
- `halt(1)` is the fallback for a wrapper-level syntax error
- `forall/2` is not used (it uses double negation internally, so `!` can't stop the outer search)
- `set_stream/2` runs in the UTF-8 header before user code (fixes Windows CP932 in Prolog; the `LANG` env var is the Linux/macOS fallback)
- The counter variable is `'__pr_count'` (quoted atom) to avoid collision with user code
- `metadata.truncated`: the executor sets this by detecting a trailing `__TRUNCATED__\n` in output
- `metadata.result_count`: the executor computes this from the number of non-empty output lines (excluding `__TRUNCATED__` lines and `false` lines; 0 when there are no solutions)

**Success / failure determination:**

| Condition | `success` | `error` | `output` |
|-----------|-----------|---------|----------|
| exit code = 0, no Prolog error in stderr | `True` | `None` | stdout contents |
| exit code = 0, Prolog error in stderr | `False` | stderr contents | stdout contents (partial results if any) |
| exit code ≠ 0 | `False` | stderr contents | `""` |
| Timeout | `False` | `"Prolog execution timed out..."` | `""` |

**Note:** "no solution" from Prolog is `success=True, output="false\n"`. That's a valid inference result, not a failure. `success=False` is reserved for actual faults — syntax errors, timeouts, abnormal process termination.

**stderr handling:**
- `validate_syntax()`: parses stderr and returns an error string
- `execute()`: if stderr contains a Prolog error, put it in `error`; normal-path stderr (warnings) goes into `metadata.prolog_warnings`

**Process cleanup on timeout:**
```python
try:
    stdout, stderr = await asyncio.wait_for(
        proc.communicate(input=prolog_input.encode('utf-8')),
        timeout=timeout_seconds
    )
except asyncio.TimeoutError:
    proc.kill()               # SIGKILL
    await proc.wait()         # reap (no zombies)
    return ExecutionResult(
        success=False,
        output="",
        error=f"Prolog execution timed out after {timeout_seconds}s",
        metadata={"error_code": "EXEC_002"}
    )
```

**Decoding stdout / stderr:** the bytes returned by `proc.communicate()` are decoded via `stdout.decode('utf-8')`. The UTF-8 header guarantees the output is UTF-8, so decoding is fixed.

**Subprocess invocation rules (code quality):**
- Don't use `shell=True` → bug prevention (no argument-escaping mistakes)
- Pass Prolog code via stdin (not command-line args; avoids arg-length limits and escaping issues)
- One independent process per request → prevents state mixing

**Concurrency model:**
- Each request spawns its own SWI-Prolog process
- Processes share no state
- This eliminates assert/retract race conditions at the root
- MCP's stdio is single-client, so concurrent execution is naturally bounded. For future HTTP multiplexing, gate process count with `asyncio.Semaphore`.

### 4.5 Runaway Prevention

Three mechanisms together:

**1. Timeout (in executor.py)**
- `asyncio.wait_for` enforces a timeout on the whole subprocess
- On timeout, `proc.kill()` reliably terminates the process

**2. Result-count cap (in the query wrapper)**
- Counter-based control via `nb_setval` / `nb_getval` (not `findall`)
- `findall` accumulates all solutions, risking OOM on large solution sets
- The counter approach emits one at a time and stops immediately at the cap

**3. LLM self-correction cap**
- Controlled by `max_corrections` (default 3, max 10)

With these in place, the full feature set of SWI-Prolog — libraries, modules, file I/O, CLP, etc. — is available without restriction.

### 4.6 LLM Client (llm_client.py)

```python
class LLMClient:
    """Thin abstraction over the LLM API"""

    def __init__(self, provider: str, api_key: str, model: str, timeout_seconds: float = 30.0):
        """
        provider: "openai" | "anthropic"
        timeout_seconds: default timeout for complete() (overridable per call)
        """

    async def complete(
        self, system_prompt: str, user_prompt: str,
        temperature: float = 0.0,
        timeout_seconds: float | None = None
    ) -> str:
        """Text completion. Raises LLMError on timeout. Returns the LLM's response text.
        timeout_seconds: omitted → use the constructor's value (self.timeout_seconds)."""
```

**Design decision: LiteLLM → direct API**

v1 used LiteLLM (100+ providers), but:
- 95% of MVP users are on OpenAI or Anthropic
- LiteLLM pulls in a large dependency tree
- Direct implementations of two providers are under 100 lines
- Switching to LiteLLM later is just swapping this thin abstraction

**API-key handling:**
- Read from environment variables (the standard Pydantic Settings approach)
- Auto-mask API keys in log output (implemented in logger.py)

**Lazy import of provider SDKs:**
- `openai` / `anthropic` packages are optional extras (§6)
- `LLMClient.__init__` imports the provider's SDK on demand and raises a clear error if not installed

### 4.7 Configuration (config.py)

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PROLOG_REASONER_",
        env_file=".env",
    )

    # LLM (library only; MCP server does not use these)
    llm_provider: str = "openai"           # "openai" | "anthropic"
    llm_api_key: str = ""                  # optional from v13. Empty means LLMClient unusable
    llm_model: str = "gpt-5.4-mini"
    llm_temperature: float = 0.0
    llm_timeout_seconds: float = 30.0      # LLM API call timeout

    # Prolog
    swipl_path: str = "swipl"              # path to the SWI-Prolog binary
    execution_timeout_seconds: float = 10.0

    # Rule bases (v14)
    rules_dir: Path = Path.home() / ".prolog-reasoner" / "rules"  # CRUD target
    bundled_rules_dir: Path | None = None                          # fork sets this to its bundled rules
    max_rule_size: int = 1_048_576                                 # 1 MiB. save_rule_base limit

    # Logging
    log_level: str = "INFO"
```

**Purpose of v14 additions:**
- `rules_dir`: writable working directory. Target of all CRUD. Default `~/.prolog-reasoner/rules/`
- `bundled_rules_dir`: read-only bundled directory shipped by a forking package. Copy-on-first-use into `rules_dir` at startup (see §4.10 Rule-Base Module)
- `max_rule_size`: `save_rule_base` size ceiling. Over → `RULEBASE_005`

**Anti-pattern for forks:**
A forking package that ships bundled rules should set `PROLOG_REASONER_BUNDLED_RULES_DIR` to its bundle location (typically a read-only path inside site-packages). **Do not set `PROLOG_REASONER_RULES_DIR` to a site-packages path** — `rules_dir` is the write target of every CRUD op, so pointing it at a read-only location makes `save_rule_base` / `delete_rule_base` fail with `RULEBASE_004`. Because copy-on-first-use from bundled → rules_dir runs automatically at startup, the fork author only needs to set the bundled side.

**Behavior when SWI-Prolog is absent:**
```python
def validate_swipl(self) -> None:
    """Verify SWI-Prolog presence and basic functionality at startup"""
    try:
        result = subprocess.run(
            [self.swipl_path, '--version'],
            capture_output=True, timeout=5
        )
        if result.returncode != 0:
            raise ConfigurationError(
                f"SWI-Prolog returned exit code {result.returncode}.\n"
                f"stderr: {result.stderr.decode(errors='replace')}\n"
                f"Path: {self.swipl_path}",
                error_code="CONFIG_001"
            )
    except (FileNotFoundError, PermissionError):
        raise ConfigurationError(
            "SWI-Prolog not found. Install from: https://www.swi-prolog.org/download/stable\n"
            f"Searched path: {self.swipl_path}\n"
            "Or set PROLOG_REASONER_SWIPL_PATH to the correct location.",
            error_code="CONFIG_001"
        )
    except subprocess.TimeoutExpired:
        raise ConfigurationError(
            f"SWI-Prolog did not respond within 5 seconds.\n"
            f"Path: {self.swipl_path}",
            error_code="CONFIG_001"
        )
```

### 4.8 Error Handling (errors.py)

```python
class PrologReasonerError(Exception):
    """Base exception. All errors inherit from this."""
    def __init__(self, message: str, error_code: str, retryable: bool = False):
        self.error_code = error_code
        self.retryable = retryable
        super().__init__(message)

class TranslationError(PrologReasonerError):
    """NL→Prolog translation failure (internal use; the public API returns
    TranslationResult(success=False) instead)"""
    # error_code: "TRANSLATION_001" (empty response)
    # Note: TRANSLATION_002 (corrections exhausted) is returned directly
    # as TranslationResult.metadata["error_code"]

class ExecutionError(PrologReasonerError):
    """Prolog runtime failure (internal use; the public API returns
    ExecutionResult(success=False) instead)"""
    # error_code: "EXEC_001" (syntax), "EXEC_002" (timeout), "EXEC_003" (abnormal termination)
    # Note: caught inside the executor and returned as
    # ExecutionResult.metadata["error_code"]

class BackendError(PrologReasonerError):
    """SWI-Prolog unavailable"""
    # error_code: "BACKEND_001"

class LLMError(PrologReasonerError):
    """LLM API call failure"""
    # error_code: "LLM_001" (API communication), "LLM_002" (auth),
    #             "LLM_003" (rate limit)
    # retryable: True for LLM_001, LLM_003

class ConfigurationError(PrologReasonerError):
    """Misconfiguration"""
    # error_code:
    #   "CONFIG_001" — SWI-Prolog missing/broken (validate_swipl failure)
    #   "CONFIG_002" — copy failure from bundled_rules_dir (fatal startup error; v14)

class RuleBaseError(PrologReasonerError):
    """Rule-base CRUD failure (v14)"""
    # error_code:
    #   "RULEBASE_001" (rule_base_not_found)      — requested rule base absent
    #   "RULEBASE_002" (rule_base_invalid_name)   — name validation violation
    #   "RULEBASE_003" (rule_base_syntax_error)   — parse-only syntax error on save
    #   "RULEBASE_004" (rule_base_io_error)       — file I/O failure (permissions, etc.)
    #   "RULEBASE_005" (rule_base_too_large)      — exceeds max_rule_size
    # Note: caught inside rule_base.py and returned as each MCP tool's
    # error_code / error_category in its response
```

**Error-usage rules (public-API boundary):**

| Layer | Success | Business error (expected failure) | Infrastructure error (unexpected) |
|-------|---------|-----------------------------------|-----------------------------------|
| **executor** | `ExecutionResult(success=True)` | `ExecutionResult(success=False)` — timeout, syntax error, abnormal termination | `BackendError` raise — SWI-Prolog unlaunchable |
| **translator** | `TranslationResult(success=True)` | `TranslationResult(success=False)` — corrections exhausted, untranslatable input | `LLMError` raise — API communication fault, auth error |
| **server.py** | convert result → dict | convert result → dict | catch exception → MCP error response |

- **result.success=True, output="false\n"**: Prolog found no solution. Not an error — a valid result.
- **Business-error details**: message in `result.error`, programmatic discriminator in `result.metadata["error_code"]` (e.g. `"EXEC_002"`).
- **Exceptions = infrastructure only**: executor/translator don't raise business errors. They always return a result object.
- **API-key masking**: when server.py converts exceptions to MCP errors, logger.py masks keys automatically.

### 4.9 Logging (logger.py)

```python
import sys
import logging

def setup_logging(level: str = "INFO") -> None:
    """stderr-only structured logging. Safe to call multiple times
    (handler duplication guarded)."""
    if logging.root.handlers:
        logging.root.setLevel(level)
        return
    handler = logging.StreamHandler(sys.stderr)  # never stdout
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    )
    handler.setFormatter(formatter)
    logging.root.addHandler(handler)
    logging.root.setLevel(level)

class SecureLogger:
    """Logger wrapper that auto-redacts secrets like API keys"""
    REDACT_PATTERNS = [
        re.compile(r'sk-[a-zA-Z0-9_-]{20,}'),     # OpenAI / Anthropic shared (matches both sk-proj-... and sk-ant-...)
    ]

    def info(self, msg: str, **kwargs): ...
    def error(self, msg: str, **kwargs): ...
```

### 4.10 Rule-Base Management (rule_base.py) — v14

A new module providing CRUD on saved rule bases.

```python
class RuleBaseStore:
    """Filesystem-backed rule-base CRUD"""

    def __init__(self, settings: Settings, executor: PrologExecutor):
        self.rules_dir = settings.rules_dir
        self.max_size = settings.max_rule_size
        self._executor = executor  # for validate_syntax

    def sync_bundled(self, bundled_dir: Path | None) -> None:
        """Copy-on-first-use called at startup. Only `.pl` files inside
        bundled_dir are copied into rules_dir (other extensions are
        ignored). Existing files are not overwritten. Self-copy protection
        via resolved path comparison.
        If bundled_dir is None or doesn't exist, no-op return.
        I/O failure → raise RuleBaseError(RULEBASE_004)."""

    def list(self) -> list[RuleBaseInfo]:
        """Build RuleBaseInfo from every `.pl` file in rules_dir.
        Extract `% description:` / `% tags:` from the file's leading
        contiguous `%` comments (terminated by a blank line, directive,
        or clause; last value wins for duplicate keys).
        Return sorted ascending by `name` (stable via `sorted()`).
        I/O failure → raise RuleBaseError(RULEBASE_004)."""

    def get(self, name: str) -> str:
        """UTF-8 load (BOM fallback).
        Raises:
            RuleBaseError(RULEBASE_002): name validation failed
            RuleBaseError(RULEBASE_001): file missing (message includes close-
                match suggestions; see the key points at the end of §4.10)
            RuleBaseError(RULEBASE_004): any other I/O failure"""

    async def save(self, name: str, content: str) -> bool:
        """Returns: True=newly created, False=overwrite.
        Validation order: name (RULEBASE_002) → size (RULEBASE_005) →
        parse-only syntax (RULEBASE_003) → atomic write (tmpfile + os.replace).
        Any failure → raise RuleBaseError with the corresponding error_code."""

    def delete(self, name: str) -> None:
        """Raises:
            RuleBaseError(RULEBASE_002): name validation failed
            RuleBaseError(RULEBASE_001): file missing
            RuleBaseError(RULEBASE_004): I/O failure"""
```

**Design key points:**
- Name validation: `^[a-zA-Z0-9_-]{1,64}$`. Rejects path-traversal, extensions, and dots.
- The `rule_bases` parameter on `execute_prolog` is **resolved upstream (server.py / reasoner.py) via `RuleBaseStore.get()` into content**, then passed to `PrologExecutor.execute(rule_base_contents=[(name, text), ...])`. Duplicates are deduplicated upstream via `rule_base.dedup_names()` (a thin wrapper over `list(dict.fromkeys(...))`) preserving order. The Executor performs neither name resolution nor dedup (avoids a circular dependency).
- Business errors raised by `RuleBaseStore.get()` (`RULEBASE_001` missing, `RULEBASE_002` invalid name) are caught by server.py / reasoner.py and converted to `ExecutionResult(success=False, metadata={"error_code": <code>})` or `TranslationResult(success=False, ...)`. `RULEBASE_004` (I/O failure) is propagated as an infrastructure fault.
- Atomic write: tmpfile in the same directory → `os.replace()`.
- Error messages include close-match suggestions via `difflib.get_close_matches` (helps LLM self-recovery).

---

## 5. Security Policy

### 5.1 Threat model: no attacker exists

This tool is a library / MCP server that runs in a local environment.

```
User        = the developer themselves (trusted)
Environment = the user's own PC
Transport   = MCP stdio (not network-exposed)
External    = HTTPS to LLM APIs (OpenAI / Anthropic) only
```

Because there is no external access path, classical attacks (injection, path traversal, etc.) are not a threat. The user is simply running Prolog on their own PC with their own privileges, so there is no reason to block predicates like `shell/1`.

**Therefore, no Prolog feature restrictions (sandboxing, whitelists) are imposed.**

### 5.2 Real risks and countermeasures

Risks that remain even under a no-attacker assumption:

| Risk | Cause | Impact | Countermeasure |
|------|-------|--------|----------------|
| **Unintended runaway** | LLM generates an infinite loop or exponentially-branching Prolog | CPU/memory hogging, unresponsive PC | Timeout + result-count limits |
| **Over-trusting inferences** | Semantic errors in NL→Prolog translation | Taking a wrong conclusion as "verified" | **Exposing the Prolog intermediate representation** (core feature) |
| **API key in logs** | Debug logs contain the key | Unintended exposure | API key masking in logs |
| **LLM API cost runaway** | Excessive self-correction looping | Unexpected billing | `max_corrections` cap (default 3, max 10) |

### 5.3 Runaway prevention (the only security mechanism)

| Protection | Limit | Changeable | Setting |
|------------|-------|------------|---------|
| Prolog execution timeout | 10 s | Yes | `execution_timeout_seconds` |
| Result-count cap | 100 | Yes | `max_results` parameter |
| LLM self-correction count | 3 (default), 10 (max) | Yes | `max_corrections` parameter |

All limits are user-configurable. Deliberate user operations are not restricted.

### 5.4 Rationale for this policy

**Why no sandbox:**
- The user is in a position to run arbitrary code on their own PC; restrictions at the library level are meaningless
- Whitelists rely on incomplete regex parsing and produce false positives (blocking legitimate code)
- Unrestricted access to SWI-Prolog libraries (CLP(FD), DCG, file handling, etc.) directly expands reasoning capability
- Excessive security degrades developer experience and inhibits adoption

**Why only runaway prevention:**
- LLM-generated code is unpredictable and can realistically fall into unintended infinite loops
- Timeouts are a UX concern (keeping the PC responsive), not a security one
- These limits do not strip user capability (they can be extended if needed)

**If embedded in a web service in the future:**
- The service layer can add Docker/container isolation, etc.
- The library is not responsible for that concern

### 5.5 Subprocess invocation rules

Not for security, but as **code quality hygiene**, observe the following:

- Never use `shell=True` → prevents bugs (avoids argument-escaping omissions)
- Feed Prolog code via stdin → avoids argument-length limits
- A separate process per request → prevents state contamination (bug prevention, not race-condition defense)

---

## 6. Dependencies

### Required

| Package | Purpose | Version |
|---------|---------|---------|
| `fastmcp` | MCP server | `^3.0` |
| `pydantic` | Data validation | `^2.0` |
| `pydantic-settings` | Settings management | `^2.0` |

### Optional extras (LLM providers)

```toml
[project.optional-dependencies]
openai = ["openai>=1.0"]
anthropic = ["anthropic>=0.40"]
all = ["openai>=1.0", "anthropic>=0.40"]
```

```bash
pip install prolog-reasoner[openai]      # OpenAI only
pip install prolog-reasoner[anthropic]   # Anthropic only
pip install prolog-reasoner[all]         # both
```

**Rationale:** no need to install SDKs for providers you don't use. `llm_client.py` imports lazily and raises a clear error if the required SDK is missing.

### Runtime requirements

| Requirement | Version | Note |
|-------------|---------|------|
| **Python** | 3.10+ | `str \| None` syntax (PEP 604), asyncio stability |
| **SWI-Prolog** | 9.0+ | Installed separately by the user (not a pip dependency) |

- SWI-Prolog: when using Docker, pin the version in the Dockerfile (e.g. 9.2.7)

---

## 7. Testing Strategy

### 7.1 Unit tests (no LLM required)

| Test | Target | Method |
|------|--------|--------|
| `test_models.py` | Validation | Confirm rejection of invalid input |
| `test_executor.py` | Prolog execution + runaway prevention | Confirm outputs of fixed code, timeout, process kill |
| `test_translator.py` | Translation | Mock LLM (recording / playback) |

### 7.2 Integration tests (LLM API required)

| Test | Scenario |
|------|----------|
| Socrates problem | Basic deductive reasoning |
| Family-relation inference | Multi-step reasoning over transitive relations |
| Constraint satisfaction | Scheduling problem |
| Self-correction | Deliberately hard input to exercise the correction loop |
| MCP server | Tool invocation via an in-memory client |

### 7.3 Handling LLM non-determinism

LLM responses are non-deterministic, making test reproducibility a concern.

**Countermeasure:** record LLM responses under `tests/fixtures/llm_responses/` and replay them in normal test runs. Periodically (e.g. monthly) refresh the recordings against the real LLM API to detect model drift.

```python
# conftest.py
@pytest.fixture
def mock_llm(request):
    """Fixture that replays recorded LLM responses."""
    recording_path = f"tests/fixtures/llm_responses/{request.node.name}.json"
    if os.path.exists(recording_path):
        return RecordedLLMClient(recording_path)
    else:
        # If no recording exists, call the real API and save the recording
        return RecordingLLMClient(real_client, recording_path)
```

---

## 8. Distribution Strategy

### `pyproject.toml` entry point

```toml
[project.scripts]
prolog-reasoner = "prolog_reasoner.server:main"
```

`main()` is the FastMCP server launch function. This makes the `prolog-reasoner` command available after `pip install`.

### PyPI
```bash
pip install prolog-reasoner[openai]   # as a library (with OpenAI)
prolog-reasoner                       # start as an MCP server
uvx prolog-reasoner                   # run immediately without installation
```

### Docker (SWI-Prolog bundled)

```dockerfile
FROM python:3.12-slim

# Install SWI-Prolog (pinned)
RUN apt-get update && \
    apt-get install -y swi-prolog=9.2.* && \
    rm -rf /var/lib/apt/lists/*

# Non-root user
RUN useradd -m -u 1000 reasoner
USER reasoner

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir ".[all]"

ENTRYPOINT ["prolog-reasoner"]
```

### MCP configuration example (v13)

Because the MCP server does not call any LLM API, **API key configuration is unnecessary**:

```json
{
  "mcpServers": {
    "prolog-reasoner": {
      "command": "uvx",
      "args": ["prolog-reasoner"]
    }
  }
}
```

Via Docker (for environments without SWI-Prolog installed):

```json
{
  "mcpServers": {
    "prolog-reasoner": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "prolog-reasoner"]
    }
  }
}
```

---

## 9. Implementation Order

### Phase 1: working prototype
1. `pyproject.toml` + project layout + `.gitignore`
2. `models.py` — data models (including Pydantic validation)
3. `config.py` — settings management + SWI-Prolog presence check
4. `errors.py` — exception hierarchy
5. `logger.py` — stderr-only logging
6. `executor.py` — SWI-Prolog subprocess execution (query wrapper + timeout + result-count cap)
7. `llm_client.py` — direct OpenAI / Anthropic invocation (lazy import)
8. `translator.py` — single-phase translation + self-correction loop
9. `reasoner.py` — public API
10. `server.py` — MCP server (2 tools)
11. Basic tests (execution + translation + runaway prevention)

### Phase 2: quality hardening
- Integration tests (real LLM API)
- LLM response recording harness
- Strengthened error handling
- Unicode / multilingual tests

### Phase 3: distribution prep
- Docker support
- README
- PyPI release
- Sample code

---

## 10. Verification Methods

### Basic operation check (MCP)

```bash
# 1. MCP server starts (no error, no API key required)
prolog-reasoner

# 2. execute_prolog (the connected LLM writes Prolog and invokes the tool)
# Input: prolog_code="human(socrates). mortal(X) :- human(X)." + query="mortal(socrates)"
# Expect: output="mortal(socrates)\n"

# 3. execute_prolog (query with a variable)
# Input: same prolog_code + query="mortal(X)"
# Expect: output="mortal(socrates)\n"

# 4. Confirm rule-change effect
# Intentionally modify prolog_code → execute_prolog → verify output changes
```

### Library pipeline check (LLM API key required)

```python
# 1. translate: NL→Prolog (LLM API call + self-correction internally)
# Input: "Socrates is human. Humans are mortal. Is Socrates mortal?"
# Expect: success=true, prolog_code contains human(socrates) and a mortal/1 rule

# 2. execute: prolog_code + suggested_query from step 1
# Expect: output="mortal(socrates)\n"
```

### Runaway-prevention verification

```bash
# Timeout: stopping an infinite loop
execute_prolog(prolog_code="loop :- loop. :- loop.", query="true")
# → error: "Prolog execution timed out after 10.0s"

# Resource cap: truncating massive result sets
execute_prolog(prolog_code="num(X) :- between(1,999999,X).", query="num(X)", max_results=10)
# → output: 10 result lines + "__TRUNCATED__\n", metadata.truncated=true
```

---

## 11. Future Extensions

Features to consider post-MVP. Not implemented at this time.

| Extension | Summary | Trigger condition |
|-----------|---------|-------------------|
| **Structured output parser** | Utility that parses `output` text into `results: list[dict[str, str]]` | When programmatic use as a Python library is confirmed in demand |
| **Janus backend** | The official SWI-Prolog Python bridge. No subprocess, ~1 μs | When performance requirements arise. Add an abstract backend layer |
| **LiteLLM integration** | 100+ providers | When support for 3+ providers is requested |
| **Additional LLM providers** | Google Gemini, Ollama, etc. | As user demand warrants |
| **MCP resources** | Listing / reuse of generated Prolog code | Once the MCP Resources spec matures |
| **Web service deployment** | Docker + API gateway + auth | To be considered during the domain-specialization phase (Phase C) |

---

## Appendix A: Full Changelog

### v1 → v2

| Item | v1 | v2 | Rationale |
|------|----|----|-----------|
| MCP tool count | 3 (reason, execute, generate) | 2 (translate, execute) | Unix philosophy — small, composable tools |
| Architecture | 4 layers + abstract backend | 3 layers + direct subprocess | YAGNI — drop abstractions the MVP doesn't need |
| Orchestration | `engine.py` (imperative) | `pipeline.py` (pipeline) | Extensibility — easier to add/remove steps |
| LLM integration | LiteLLM (100+ providers) | Direct API (OpenAI / Anthropic) | Fewer deps; covers 95% of use cases |
| Translation method | 3-phase (Logic-LM) | 1-phase + self-correction | Simpler for an MVP; self-correction absorbs syntax errors |
| `context` type | `str` (tool) vs `dict` (model) | unified `str` | Remove type mismatch |
| `prolog_source` | Dual-use (code and path) | `prolog_code` only (code only) | Path-traversal prevention |
| `query` (execute) | Optional (auto-inferred) | Required | Remove ambiguity; the auto-inference algorithm was undefined |
| `explanation` | In the return value | Removed | The LLM itself can interpret results; an extra LLM call is overkill |
| `results` format | Undefined | Clearly defined (dict of variable bindings) | Remove ambiguity |
| Concurrency | Undefined | Independent process per request | Eliminate race conditions at the root |

### v2 → v3

| Item | v2 | v3 | Rationale |
|------|----|----|-----------|
| Security policy | Whitelist-style sandbox | Runaway prevention only (see §5) | There is no attacker for a local tool |
| `sandbox.py` | `PrologSandbox` (regex whitelist) | Removed | The sandbox is unnecessary |
| `SandboxViolationError` | In the exception hierarchy | Removed | No corresponding mechanism exists |
| Security constraints in the prompt | Listed in `SYSTEM_PROMPT` | No constraints | Don't limit LLM generation capability |

### v3 → v4

| Item | v3 | v4 | Rationale |
|------|----|----|-----------|
| Execution-result shape | `results: list[dict[str, str]]` | `output: str` (raw text) | Removes parser complexity and bug risk; enough for the LLM to interpret; structuring can be added later |
| Query execution | Undefined | Auto-generated query wrapper (§4.4) | Clarifies the output formatting and `max_results` control mechanism |
| Query wrapper impl | `forall/2` + cut | fail-driven loop + `nb_setval` counter | `forall/2` uses double negation internally, so cut doesn't escape; fail-loop makes cut effective at the top level |
| Result-count limit | `findall + length` (ambiguous) | `nb_setval` counter approach | `findall` risks OOM; the counter processes one at a time and stops at the cap |
| Timeout handling | `asyncio.wait_for` only | + `proc.kill()` + `proc.wait()` | Prevents zombie processes after timeout |
| Self-correction location | Inside Executor (per architecture diagram) | Inside Translator (diagram and implementation unified) | Correction is about translation quality and belongs to Translator |
| Tool description | "Does not execute" | "Does not run inferential query execution" | Accurately states that syntax validation uses SWI-Prolog |
| `pipeline.py` | Pipeline pattern | `reasoner.py` (façade) | Actually a façade; name matches reality |
| `logging.py` | Same name as stdlib | `logger.py` | Avoid import-time name collision |
| LLM SDK deps | `Yes (mutually exclusive)` | Optional extras | No need to install providers you don't use |
| `error_code` / `retryable` | Appendix said "simplified" | Retained (appendix wording fixed) | Useful for programmatic error handling |
| File I/O | "Managed separately in the MCP layer" (undefined) | Clearly the MCP client's responsibility | prolog-reasoner only handles code-string I/O |
| Concurrent execution | Unmentioned | Single stdio client + a design note for a future Semaphore | Spells out the realistic constraint and the future direction |
| `validate_syntax` | "Syntax check only; does not execute" | Clarified as the consult approach (including directive execution) | Side effects are fine for a local tool |
| SWI-Prolog startup args | Undefined | `-f none -q` + `LANG=C.UTF-8` | `-f none` disables init files; `-q` suppresses the banner; UTF-8 for multilingual support |
| Success / failure decision | Undefined | Four-pattern decision table based on exit code + stderr content | Exhaustively defines the previously ambiguous criteria |
| stderr handling | Undefined | Log at WARNING+; Prolog errors → `success=False` | Clearly separates the roles of stdout and stderr |
| `suggested_query` | Undefined | Extracted from the `% Query:` comment + empty-string fallback | A safe default when the LLM omits the comment |
| Handling of "no solution" | Included in `success=False` | `success=True`, `output="false\n"` | No solution is a normal inference result, not an error |

### v4 → v5

| Item | v4 | v5 | Rationale |
|------|----|----|-----------|
| Error-handling boundary | `raise ExecutionError` on timeout | `return ExecutionResult(success=False)` | Exceptions only for infrastructure failures; business errors go in the result. §4.8's rule contradicted §4.4's code |
| UTF-8 support | `LANG=C.UTF-8` only | + `set_stream(user_input/output, encoding(utf8))` | `LANG` has no effect on Windows; set stream encoding explicitly in Prolog |
| `validate_syntax` details | "Let it consult" only | Appended `:- halt(0).`, timeout applied, `ERROR:` detection | Process exit, timeout, and error-detection criteria were undefined |
| Component wiring | Undefined | `create_reasoner()` spells out the DI wiring | How `server.py` connects to `PrologReasoner` was unclear |
| LLM call timeout | Undefined | `llm_timeout_seconds: float = 30.0` | A network failure would block `translate` indefinitely |
| Python version | Not documented | 3.10+ | Uses language features like `str \| None` |
| `pyproject.toml` entry point | Undefined | `[project.scripts]` documented | It was unclear how the `prolog-reasoner` command gets registered |
| `nb_setval` variable name | `result_count` | `'__pr_count'` | Avoid collision with user Prolog variables |
| `metadata.truncated` detection | Undefined | Detect `__TRUNCATED__\n` at the end of output | The truncated-flag mechanism was unspecified |

### v5 → v6

| Item | v5 | v6 | Rationale |
|------|----|----|-----------|
| Prolog-input structure | Wrapper appended after user code (including `set_stream` inside the wrapper) | 3-layer: UTF-8 header (prepend) + user code + query wrapper (append) | If `set_stream` runs after user code, it's too late to parse non-ASCII atoms |
| `translate_with_correction` return | `tuple[str, str]` | `TranslationResult` | A tuple can't express failure (correction-cap exceeded); conflicted with §4.8's "business errors go in the result" rule |
| `LLMClient.__init__` | `(provider, api_key, model)` | + `timeout_seconds: float = 30.0` | `create_reasoner()` passes `timeout_seconds`, but the constructor had no place to receive it |
| `TranslationError` / `ExecutionError` docstrings | `error_code: "EXEC_001"` etc. | `metadata["error_code"]: "EXEC_001"` etc. + "for internal use; surfaced in the public API as a result" | v5 moved to exception→result, but class docstrings still reflected the old design |
| String embedding | "Python string formatting" | "String replacement (`.replace()`)" | `.format()` collides with Prolog's `{}` (DCG notation, etc.) |

### v6 → v7

| Item | v6 | v7 | Rationale |
|------|----|----|-----------|
| `LLMClient.complete()` timeout | `timeout_seconds: float = 30.0` | `timeout_seconds: float \| None = None` (falls back to `self.timeout_seconds`) | A parameter default of 30.0 overrode the constructor-configured value, making the `llm_timeout_seconds` env var ineffective |
| `Settings.max_results_default` | `max_results_default: int = 100` | Removed | Triple-defined across the `ExecutionRequest` model (`default=100`), the tool definition (`max_results=100`), and `Settings`. The Settings value was unreferenced dead config |
| `Settings.max_results_limit` | `max_results_limit: int = 10000` | Removed | Duplicated with the `ExecutionRequest` model (`le=10000`). Validation consolidated onto the model |
| `translate()` error contract | Return-value docstring only | + `Raises: TranslationError` | The error-propagation path for an empty LLM response (TRANSLATION_001) was undocumented; it was unclear how `translate_with_correction()` catches it and converts to `TranslationResult(success=False)` |
| `server.py` `main()` | Undefined (only referenced from `pyproject.toml`) | Added function definition: `mcp.run()` | The entry-point implementation was unspecified |
| stdout / stderr decoding | Unspecified | `stdout.decode('utf-8')` | Mirrors the UTF-8 header; the bytes→str conversion was undefined |

### v7 → v8

| Item | v7 | v8 | Rationale |
|------|----|----|-----------|
| `executor.execute()` timeout | `timeout_seconds: float = 10.0` | `timeout_seconds: float \| None = None` (falls back to `Settings.execution_timeout_seconds`) | v7 fixed `LLMClient.complete()`, but the same pattern (parameter default overriding the setting) lingered in `executor.execute()` |
| 3-layer Prolog concatenation | Join method unspecified | `HEADER + "\n" + prolog_code + "\n" + WRAPPER` | Without a trailing newline on user code, Prolog's term-terminating `.` gets concatenated with the next directive, causing a syntax error |
| `validate_syntax` concatenation | Join method unspecified | Clarified `\n`-joining | The same newline-separator issue as `execute()` also existed in `validate_syntax()` |
| FastMCP instance | Undefined (only `@mcp.tool()` was shown) | `mcp = FastMCP("prolog-reasoner")` documented | The origin of the `mcp` object was unclear |
| `TranslationError` docstring | `metadata["error_code"]: "TRANSLATION_001", "TRANSLATION_002"` | `error_code: "TRANSLATION_001"` only; note that TRANSLATION_002 is returned directly in the result metadata | The exception-class attribute is `self.error_code`, not `metadata`. TRANSLATION_002 isn't raised — it goes in the result |
| `ExecutionError` docstring | `metadata["error_code"]: "EXEC_001"`–`"EXEC_003"` | `error_code: "EXEC_001"`–`"EXEC_003"` + note that executor catches it internally and returns the code in the result metadata | Same inaccuracy as `TranslationError` |

### v8 → v9

| Item | v8 | v9 | Rationale |
|------|----|----|-----------|
| `reasoner.translate()` docstring | "LLM NL→Prolog + syntax check" | Delegates to `self.translator.translate_with_correction()`, passing `self.executor` as the executor argument | Implementers had to guess how reasoner and translator connect |
| `reasoner.execute()` docstring | "Prolog execution + output text" | Delegates to `self.executor.execute()` (documented) | Same delegation clarification as `translate()` |
| `validate_swipl()` exceptions | Only `FileNotFoundError` caught | + `PermissionError`, `subprocess.TimeoutExpired`, `returncode != 0` checks | When the path exists but is broken, permission denied, or hanging, non-`ConfigurationError` exceptions propagated |
| `setup_logging()` dedup | No guard (adds a handler per call) | Uses `logging.root.handlers` check to prevent duplication | Repeated calls in tests produced duplicated log output |
| `SecureLogger.REDACT_PATTERNS` | `sk-[a-zA-Z0-9]{20,}` | `sk-[a-zA-Z0-9_-]{20,}` | Didn't match OpenAI `sk-proj-...` keys because they contain hyphens |

### v9 → v10

| Item | v9 | v10 | Rationale |
|------|----|-----|-----------|
| `SecureLogger.REDACT_PATTERNS` | 2 patterns (`sk-` generic + `sk-ant-` Anthropic-specific) | 1 pattern (`sk-` generic only) | Pattern 1 also matches `sk-ant-` first, making pattern 2 unreachable |
| `metadata.result_count` | Appears in return examples but no derivation rule | Computed from non-blank lines of `output` (excluding `__TRUNCATED__` lines); documented | Implementers had to guess how `result_count` was derived |

### v10 → v11

| Item | v10 | v11 | Rationale |
|------|-----|-----|-----------|
| `validate_swipl()` `ConfigurationError` | Missing `error_code` argument (3 sites) | Added `error_code="CONFIG_001"` at all sites | `PrologReasonerError.__init__`'s required `error_code` was missing — implementing it as written would raise `TypeError` immediately |
| `metadata.result_count` definition | "Non-blank lines (excluding `__TRUNCATED__` lines)" | "Non-blank lines (excluding `__TRUNCATED__` and `false` lines; 0 when there is no solution)" | With `output="false\n"`, `result_count=1` contradicted the name's implicit "count of solutions" |

### v11 → v12

| Item | v11 | v12 | Rationale |
|------|-----|-----|-----------|
| `suggested_query` extraction | Parses the `% Query: <query>` comment | + Strips a trailing period and surrounding whitespace | If the LLM emits `% Query: mortal(socrates).` with a period, the wrapper's `<QUERY>` substitution produces a syntax error |

### v12 → v13

v13 is a major release that **realigns architectural responsibilities**. LLM translation, previously shared between the MCP server and the library, is now library-only.

| Item | v12 | v13 | Rationale |
|------|-----|-----|-----------|
| MCP tool count | 2 (`translate_to_prolog` + `execute_prolog`) | 1 (`execute_prolog`) | The LLM connected over MCP (e.g. Claude) can generate Prolog itself. Having the server call another LLM API is redundant. General-purpose MCP servers don't require an API key |
| `Settings.llm_api_key` | `str` (required, no default) | `str = ""` (empty-string default) | When used as an MCP server, no LLM API is called, so the server must start without the key being set |
| `server.py` initialization | Generates the full `PrologReasoner` at module load via `create_reasoner()` | `_init()` lazily creates only `PrologExecutor` | MCP doesn't use Translator / LLMClient, so don't hold them. Lazy init prevents import-only failures |
| `create_reasoner()` helper | Public API (used by both `server.py` and the library) | Removed | MCP uses Executor only; the library hand-wires DI per use case. The shared helper lost its role |
| `env.PROLOG_REASONER_LLM_API_KEY` in MCP config | Required | Not required | The MCP server doesn't call an LLM API |
| Architecture diagram | Single stack (MCP → Reasoner → Translator + Executor) | Split stacks (MCP → Executor; Library → Reasoner → Translator + Executor) | Reflects the responsibility split in the diagram too |
| `translate_to_prolog` tool tests | Present in `tests/integration/test_mcp_server.py` | Removed + added `test_no_translate_tool` (asserts the tool is not exposed) | Regression guard against accidental re-exposure |
| The translation feature itself | Shared between MCP and library | Library-only (retained as `PrologReasoner.translate()`) | The feature itself isn't gone — only the distribution surface changed |

### v13 → v14

v14 adds the **rule-base feature** — a major change. Named, reusable Prolog modules (e.g. chess piece move rules, legal axioms, tax scenarios) can be saved / listed / read / deleted by LLM callers, and referenced in subsequent `execute_prolog` calls via `rule_bases=[...]`. This is the substrate for Phase C (domain-specialized forks).

| Item | v13 | v14 | Rationale |
|------|----|----|-----------|
| MCP tool count | 1 (`execute_prolog`) | 5 (`execute_prolog` + `list_rule_bases` / `get_rule_base` / `save_rule_base` / `delete_rule_base`) | CRUD can't be overloaded onto `execute_prolog` parameters; split into auxiliary tools |
| `execute_prolog` signature | `(prolog_code, query, max_results=100)` | + `rule_bases: list[str] \| None = None`, + `trace: bool = False` | Adds rule-base-by-name references and opt-in proof trace |
| `ExecutionRequest` fields | `prolog_code` / `query` / `max_results` | + `rule_bases: list[str] = []` / `trace: bool = False` | Request model stays in sync with the MCP surface |
| `TranslationRequest` fields | `query` / `context` / `max_corrections` | + `rule_bases: list[str] = []` | Exposes saved rule bases to the LLM on the translation path too |
| `PrologReasoner.translate()` wiring | Calls `translate_with_correction(self.executor)` | + Forwards `rule_base_store=self.rule_base_store` | Makes the `rule_bases` parameter on `translate_with_correction` actually reachable from the public API |
| `translate_with_correction` | Translate + self-correction loop | + `rule_bases` / `rule_base_store` arguments; injects an "Available rule bases:" section into the system prompt; budget-managed by `max_rule_prompt_bytes` with a truncation marker when exceeded | Lets the LLM reuse predicates from saved rule bases instead of reinventing them |
| `executor.execute()` | `(prolog_code, query, max_results, trace)` | + `rule_base_contents: list[tuple[str, str]]` argument; `metadata` gains `rule_bases_used` / `rule_base_load_ms` | Name resolution is the caller's responsibility; the executor only consumes content |
| `validate_syntax()` implementation | `consult`-based (directives like `:- initialization(...)` execute as side effects) | Parse-only (`read_term`-based; only `op/3` is executed; CLP(FD) operators pre-declared so the syntax check still passes) | Prevents `:- halt.` and similar from firing when saving a rule base. See §11.4 |
| New `Settings` fields | — | `rules_dir` (`~/.prolog-reasoner/rules`) / `bundled_rules_dir` / `max_rule_size` (1 MiB) / `max_rule_prompt_bytes` (64 KiB) | Storage location, fork-bundled rules, size cap, prompt budget |
| Error codes | `CONFIG_001` / `EXEC_001`–`003` / `TRANSLATION_001`–`002`, etc. | + `RULEBASE_001`–`005`, `CONFIG_002` (bundled_rules_dir copy failure) | Adds CRUD business errors (not-found, invalid-name, syntax, I/O, oversize) |
| `server.py` initialization | Lazily creates `PrologExecutor` in `_init()` | + Lazily creates `RuleBaseStore` too; calls `sync_bundled()` to copy `bundled_rules_dir` into `rules_dir` on first use (copy-on-first-use) | Lets MCP servers ship with fork-bundled rules that auto-populate on startup |
| New modules / types | — | `src/prolog_reasoner/rule_base.py` (CRUD), `RuleBaseError` exception, `RuleBaseInfo` model | Core implementation of the rule-base feature |
