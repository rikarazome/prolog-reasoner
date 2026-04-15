# prolog-reasoner

[![PyPI version](https://img.shields.io/pypi/v/prolog-reasoner.svg)](https://pypi.org/project/prolog-reasoner/)
[![Python versions](https://img.shields.io/pypi/pyversions/prolog-reasoner.svg)](https://pypi.org/project/prolog-reasoner/)
[![CI](https://github.com/rikarazome/prolog-reasoner/actions/workflows/test.yml/badge.svg)](https://github.com/rikarazome/prolog-reasoner/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

SWI-Prolog as a "logic calculator" for LLMs — available as an MCP server and a Python library. Eliminate the black box from LLM logical reasoning.

LLMs excel at natural language but struggle with formal logic. Prolog excels at logical reasoning but can't process natural language. **prolog-reasoner** bridges this gap by exposing SWI-Prolog execution to LLMs through two complementary surfaces:

- **MCP server** — the connected LLM (e.g. Claude) writes Prolog and executes it via the server. No LLM API key needed on the server side.
- **Python library** — a full NL→Prolog pipeline with self-correction, for programs that don't have an LLM in the loop. Requires an OpenAI or Anthropic API key.

Both surfaces share the same Prolog executor; the library adds an LLM-based translator on top. In either mode, the Prolog code is the reasoning — you can see what was inferred, how, and why.

## Features

- **MCP tool** (`execute_prolog`): run arbitrary SWI-Prolog code with a query
- **Transparent intermediate representation**: the Prolog code is the audit trail — inspect, modify, or verify before execution
- **CLP(FD) support**: constraint logic programming for scheduling and optimization
- **Negation-as-failure, recursion, all standard SWI-Prolog features**
- **Library mode**: NL→Prolog translation with self-correction loop (OpenAI / Anthropic)

## Requirements

- Python ≥ 3.10
- [SWI-Prolog](https://www.swi-prolog.org/download/stable) installed and on PATH (≥ 9.0)
- API key for OpenAI or Anthropic — **only for library mode**, not for the MCP server

## Installation

```bash
# MCP server only (no LLM dependencies)
pip install prolog-reasoner

# Library with OpenAI
pip install prolog-reasoner[openai]

# Library with Anthropic
pip install prolog-reasoner[anthropic]

# Both providers
pip install prolog-reasoner[all]
```

## MCP Server Setup

The MCP server exposes a single tool, `execute_prolog`, that runs Prolog code written by the connected LLM. It does **not** call any external LLM API, so no API key is required.

### Claude Desktop / Claude Code

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

Or, if `prolog-reasoner` is installed directly:

```json
{
  "mcpServers": {
    "prolog-reasoner": {
      "command": "prolog-reasoner"
    }
  }
}
```

### Docker (SWI-Prolog bundled)

Use Docker if you don't want to install SWI-Prolog locally:

```bash
docker build -f docker/Dockerfile -t prolog-reasoner .
```

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

### Tool reference

**`execute_prolog(prolog_code, query, max_results=100, trace=False)`**
- `prolog_code` — Prolog facts and rules (string)
- `query` — Prolog query to run, e.g. `"mortal(X)"` (string)
- `max_results` — cap the number of solutions returned (default 100)
- `trace` — when `True`, attach a structured proof tree per solution to `metadata.proof_trace`. Opt-in sub-feature; has performance overhead and does not support CLP(FD), higher-order predicates, or assert/retract.

Returns a JSON object with `success`, `output`, `query`, `error`, and `metadata`.

On success, `metadata` includes `execution_time_ms`, `result_count`, and `truncated`. On failure, `metadata` also includes `error_category` (one of `syntax_error`, `undefined_predicate`, `unbound_variable`, `type_error`, `domain_error`, `evaluation_error`, `permission_error`, `timeout`, `trace_mechanism_error`, `unknown`) and `error_explanation` — a natural-language hint for the connected LLM (or human) to decide how to fix the Prolog code.

## Library Usage

The library exposes `PrologExecutor` (Prolog-only, no LLM) and `PrologReasoner` (NL→Prolog pipeline, needs an LLM API key).

### Execute Prolog directly (no LLM)

```python
import asyncio
from prolog_reasoner.config import Settings
from prolog_reasoner.executor import PrologExecutor

async def main():
    settings = Settings()  # no API key needed
    executor = PrologExecutor(settings)
    result = await executor.execute(
        prolog_code="human(socrates). mortal(X) :- human(X).",
        query="mortal(X)",
    )
    print(result.output)  # mortal(socrates)

asyncio.run(main())
```

### Full NL→Prolog pipeline (requires LLM API key)

```python
import asyncio
from prolog_reasoner import PrologReasoner, TranslationRequest, ExecutionRequest
from prolog_reasoner.config import Settings
from prolog_reasoner.executor import PrologExecutor
from prolog_reasoner.translator import PrologTranslator
from prolog_reasoner.llm_client import LLMClient

async def main():
    settings = Settings(llm_api_key="sk-...")  # from env or explicit
    llm = LLMClient(
        provider=settings.llm_provider,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )
    reasoner = PrologReasoner(
        translator=PrologTranslator(llm, settings),
        executor=PrologExecutor(settings),
    )
    translation = await reasoner.translate(
        TranslationRequest(query="Socrates is human. All humans are mortal. Is Socrates mortal?")
    )
    print(translation.prolog_code)
    result = await reasoner.execute(
        ExecutionRequest(prolog_code=translation.prolog_code, query=translation.suggested_query)
    )
    print(result.output)

asyncio.run(main())
```

## Configuration

All settings via environment variables (prefix `PROLOG_REASONER_`):

| Variable | Default | Required for |
|----------|---------|--------------|
| `LLM_PROVIDER` | `openai` | library (`openai` or `anthropic`) |
| `LLM_API_KEY` | `""` | library only — leave unset for MCP |
| `LLM_MODEL` | `gpt-5.4-mini` | library |
| `LLM_TEMPERATURE` | `0.0` | library |
| `LLM_TIMEOUT_SECONDS` | `30.0` | library |
| `SWIPL_PATH` | `swipl` | both |
| `EXECUTION_TIMEOUT_SECONDS` | `10.0` | both |
| `LOG_LEVEL` | `INFO` | both |

## Benchmark

`benchmarks/` contains 30 logic problems across 5 categories (deduction, transitive, constraint, contradiction, multi-step) to compare LLM-only reasoning vs LLM+Prolog reasoning. The benchmark exercises the **library** path (translator + executor), since it requires the NL→Prolog step.

### Results

Measured on `anthropic/claude-sonnet-4-6`, single run over 30 problems:

| Pipeline | Accuracy | Avg latency |
|----------|----------|-------------|
| LLM-only | 22/30 (73.3%) | 1.7s |
| **LLM + Prolog** | **27/30 (90.0%)** | 3.8s |

Per-category breakdown:

| Category | LLM-only | LLM + Prolog |
|----------|----------|--------------|
| deduction | 6/6 | 6/6 |
| transitive | 6/6 | 5/6 |
| constraint | 3/7 | **6/7** |
| contradiction | 4/4 | 3/4 |
| multi-step | 3/7 | **7/7** |

The gap is concentrated in **constraint** (SEND+MORE, 6-queens, knapsack, K4 coloring, Einstein-lite) and **multi-step** (Nim game theory, 3-person knights-and-knaves, TSP-4, zebra puzzle) — exactly the combinatorial/search-heavy territory where symbolic solvers outperform pattern completion. On purely deductive or transitive questions the LLM is already strong and Prolog adds latency without accuracy gains.

All 3 LLM+Prolog failures were Prolog execution errors from malformed LLM-generated code (missing predicate definitions, unbound CLP(FD) variables) rather than reasoning errors — addressable via prompt tuning. Notably, every failure is inspectable: you can see the exact Prolog that failed and why, rather than a wrong natural-language answer with no explanation.

### Running it yourself

```bash
docker run --rm -e PROLOG_REASONER_LLM_API_KEY=sk-... \
    prolog-reasoner-dev python benchmarks/run_benchmark.py
```

Results are saved to `benchmarks/results.json`.

## Comparison with other Prolog MCPs

Several Prolog MCP servers exist, each with different design choices. **prolog-reasoner** is intentionally stateless and spot-use — Prolog is a calculator you call when logic matters, not the backbone of your agent's memory.

| | prolog-reasoner | Stateful Prolog MCPs |
|---|---|---|
| Prolog's role | Per-call reasoning tool | Project-wide knowledge base |
| State | Stateless (each call independent) | Persistent sessions / layered KBs |
| Reproducibility | Same input → same output, always | Depends on accumulated state |
| Integration effort | Use where logic matters, skip where it doesn't | Architectural commitment |
| A/B testable vs LLM-only | Yes (each call is a controlled experiment) | Structurally not comparable |

This is also why accuracy benchmarks are published here and not elsewhere: statelessness is what makes a side-by-side comparison possible.

If you need persistent agent memory, hallucination-safeguarded fact storage, or a full neuro-symbolic substrate, other projects may fit better:

- [adamrybinski/prolog-mcp](https://github.com/adamrybinski/prolog-mcp) — Trealla WASM with save/load sessions
- [umuro/prolog-mcp](https://github.com/umuro/prolog-mcp) — layered KB with file-backed persistence
- [vpursuit/model-context-lab](https://github.com/vpursuit/model-context-lab) — SWI-Prolog with security sandboxing
- [dr3d/prolog-reasoning](https://github.com/dr3d/prolog-reasoning) — neuro-symbolic memory with write-path safety

We're the spot-use option.

## Development

```bash
# Build dev image
docker build -f docker/Dockerfile -t prolog-reasoner-dev .

# Run tests (no API key needed — LLM calls are mocked)
docker run --rm prolog-reasoner-dev

# With coverage
docker run --rm prolog-reasoner-dev pytest tests/ -v --cov=prolog_reasoner

# Or via docker compose
docker compose -f docker/docker-compose.yml run --rm test
```

## License

MIT
