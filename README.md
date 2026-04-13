# prolog-reasoner

MCP server + Python library that gives LLMs a "logic calculator" powered by SWI-Prolog.

LLMs excel at natural language but struggle with formal logic. Prolog excels at logical reasoning but can't process natural language. **prolog-reasoner** bridges this gap: it translates natural language into Prolog, validates and executes it, and returns verified reasoning results.

## Features

- **Two MCP tools**: `translate_to_prolog` (NL to Prolog) and `execute_prolog` (run Prolog code)
- **Self-correction loop**: Automatically fixes syntax errors via LLM feedback (up to N retries)
- **Transparent intermediate representation**: Inspect and modify generated Prolog before execution
- **CLP(FD) support**: Constraint logic programming for scheduling and optimization problems
- **Multiple LLM providers**: OpenAI and Anthropic via optional dependencies

## Requirements

- Python >= 3.10
- [SWI-Prolog](https://www.swi-prolog.org/download/stable) installed and on PATH
- API key for OpenAI or Anthropic

## Installation

```bash
# With OpenAI
pip install prolog-reasoner[openai]

# With Anthropic
pip install prolog-reasoner[anthropic]

# Both providers
pip install prolog-reasoner[all]
```

## MCP Server Setup

### Claude Desktop / Claude Code

Add to your MCP configuration:

```json
{
  "mcpServers": {
    "prolog-reasoner": {
      "command": "uvx",
      "args": ["prolog-reasoner[openai]"],
      "env": {
        "PROLOG_REASONER_LLM_API_KEY": "sk-...",
        "PROLOG_REASONER_LLM_PROVIDER": "openai"
      }
    }
  }
}
```

### Docker (SWI-Prolog included)

```bash
docker build -f docker/Dockerfile -t prolog-reasoner .
docker run -e PROLOG_REASONER_LLM_API_KEY=sk-... prolog-reasoner
```

## Configuration

All settings via environment variables (prefix `PROLOG_REASONER_`):

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `openai` | `openai` or `anthropic` |
| `LLM_API_KEY` | (required) | API key |
| `LLM_MODEL` | `gpt-4o` | Model name |
| `LLM_TEMPERATURE` | `0.0` | Sampling temperature |
| `LLM_TIMEOUT_SECONDS` | `30.0` | API timeout |
| `SWIPL_PATH` | `swipl` | Path to SWI-Prolog |
| `EXECUTION_TIMEOUT_SECONDS` | `10.0` | Prolog execution timeout |
| `LOG_LEVEL` | `INFO` | Logging level |

## Library Usage

```python
import asyncio
from prolog_reasoner import PrologReasoner, ExecutionRequest

# Direct Prolog execution (no LLM needed)
async def main():
    from prolog_reasoner.config import Settings
    from prolog_reasoner.executor import PrologExecutor

    settings = Settings(llm_api_key="dummy")
    executor = PrologExecutor(settings)

    result = await executor.execute(
        prolog_code="human(socrates). mortal(X) :- human(X).",
        query="mortal(X)",
    )
    print(result.output)  # mortal(socrates)

asyncio.run(main())
```

## Benchmark

Includes a benchmark suite with 10 logic problems across 5 categories (deduction, transitive, constraint, contradiction, multi-step) to compare LLM-only reasoning vs LLM+Prolog reasoning.

```bash
docker run --rm -e PROLOG_REASONER_LLM_API_KEY=sk-... \
    prolog-reasoner-dev python benchmarks/run_benchmark.py
```

Results are saved to `benchmarks/results.json`.

## Development

```bash
# Run tests (requires Docker)
docker build -f docker/Dockerfile -t prolog-reasoner-dev .
docker run --rm -e PROLOG_REASONER_LLM_API_KEY=dummy prolog-reasoner-dev

# Run tests with coverage
docker run --rm -e PROLOG_REASONER_LLM_API_KEY=dummy \
    prolog-reasoner-dev pytest tests/ -v --cov=prolog_reasoner

# Or via docker compose
docker compose -f docker/docker-compose.yml run --rm test
```

## License

MIT
