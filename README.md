# prolog-reasoner

SWI-Prolog as a "logic calculator" for LLMs — available as an MCP server and a Python library.

LLMs excel at natural language but struggle with formal logic. Prolog excels at logical reasoning but can't process natural language. **prolog-reasoner** bridges this gap by exposing SWI-Prolog execution to LLMs through two complementary surfaces:

- **MCP server** — the connected LLM (e.g. Claude) writes Prolog and executes it via the server. No LLM API key needed on the server side.
- **Python library** — a full NL→Prolog pipeline with self-correction, for programs that don't have an LLM in the loop. Requires an OpenAI or Anthropic API key.

Both surfaces share the same Prolog executor; the library adds an LLM-based translator on top.

## Features

- **MCP tool** (`execute_prolog`): run arbitrary SWI-Prolog code with a query
- **CLP(FD) support**: constraint logic programming for scheduling and optimization
- **Negation-as-failure, recursion, all standard SWI-Prolog features**
- **Transparent intermediate representation**: inspect / modify Prolog before execution
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

**`execute_prolog(prolog_code, query, max_results=100)`**
- `prolog_code` — Prolog facts and rules (string)
- `query` — Prolog query to run, e.g. `"mortal(X)"` (string)
- `max_results` — cap the number of solutions returned (default 100)

Returns a JSON object with `success`, `output`, `query`, `error`, and `metadata` (execution time, result count, truncated flag).

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
| `LLM_MODEL` | `gpt-4o` | library |
| `LLM_TEMPERATURE` | `0.0` | library |
| `LLM_TIMEOUT_SECONDS` | `30.0` | library |
| `SWIPL_PATH` | `swipl` | both |
| `EXECUTION_TIMEOUT_SECONDS` | `10.0` | both |
| `LOG_LEVEL` | `INFO` | both |

## Benchmark

`benchmarks/` contains 10 logic problems across 5 categories (deduction, transitive, constraint, contradiction, multi-step) to compare LLM-only reasoning vs LLM+Prolog reasoning. The benchmark exercises the **library** path (translator + executor), since it requires the NL→Prolog step.

```bash
docker run --rm -e PROLOG_REASONER_LLM_API_KEY=sk-... \
    prolog-reasoner-dev python benchmarks/run_benchmark.py
```

Results are saved to `benchmarks/results.json`.

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
