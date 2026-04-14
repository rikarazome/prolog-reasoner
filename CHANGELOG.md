# Changelog

All notable changes to prolog-reasoner will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-04-14

### Added
- MCP server exposing a single tool, `execute_prolog`, for use by connected LLMs (no API key required on the server side)
- Python library with full NL→Prolog pipeline: `PrologReasoner.translate()` + `PrologReasoner.execute()` with self-correction loop (requires OpenAI or Anthropic API key)
- SWI-Prolog subprocess execution backend with CLP(FD) support, shared by both MCP and library
- OpenAI and Anthropic LLM provider support via optional dependencies (library only)
- Docker environment (Python 3.12 + SWI-Prolog 9.x)
- Benchmark suite: 10 logic problems comparing LLM-only vs LLM+Prolog reasoning
- Configuration via environment variables (`PROLOG_REASONER_*`)
- Lazy initialization in `server.py` so `from prolog_reasoner.server import mcp` succeeds even without SWI-Prolog at import time
- Comprehensive test suite (81 tests)

### Security
- API key redaction in all log output
- Input validation via Pydantic models
- Execution timeout protection against infinite loops
