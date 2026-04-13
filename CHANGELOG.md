# Changelog

All notable changes to prolog-reasoner will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-04-14

### Added
- MCP server with two tools: `translate_to_prolog` and `execute_prolog`
- LLM-to-Prolog translation with self-correction loop
- SWI-Prolog subprocess execution backend with CLP(FD) support
- OpenAI and Anthropic LLM provider support via optional dependencies
- Docker environment (Python 3.12 + SWI-Prolog 9.x)
- Benchmark suite: 10 logic problems comparing LLM-only vs LLM+Prolog reasoning
- Configuration via environment variables (`PROLOG_REASONER_*`)
- Library API for standalone use without MCP
- Comprehensive test suite (78 tests)

### Security
- API key redaction in all log output
- Input validation via Pydantic models
- Execution timeout protection against infinite loops
