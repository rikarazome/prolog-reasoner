# Changelog

All notable changes to prolog-reasoner will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-04-17

### Added
- Rule bases: named, reusable Prolog modules stored as `.pl` files under `PROLOG_REASONER_RULES_DIR` (default `~/.prolog-reasoner/rules/`). Four new MCP tools manage them — `save_rule_base`, `list_rule_bases`, `get_rule_base`, `delete_rule_base` — and `execute_prolog` accepts a `rule_bases: list[str]` parameter that prepends the named modules (in order, deduped) to the user-supplied Prolog code. Library equivalents: `ExecutionRequest.rule_bases` and `TranslationRequest.rule_bases`; the translator exposes the saved bodies to the LLM via an "Available rule bases" prompt section so it can reuse existing predicate names. Metadata exposes `rule_bases_used` on every execution and `rule_base_load_ms` when disk I/O occurred. Rules are name-validated (`[a-zA-Z0-9_-]{1,64}`), syntax-validated on save via a parse-only `read_term` loop (with CLP(FD) operator prelude), size-capped at `PROLOG_REASONER_MAX_RULE_SIZE` (default 1 MiB), and written atomically via `mkstemp` + `os.replace`. Bundled rule bases shipped by a fork via `PROLOG_REASONER_BUNDLED_RULES_DIR` are copied into `RULES_DIR` once on startup (copy-on-first-use; user edits are preserved). Prompt injection is capped separately at `PROLOG_REASONER_MAX_RULE_PROMPT_BYTES` (default 64 KiB) with a truncation marker. New error taxonomy: `RuleBaseError` with codes `RULEBASE_001` (not found, includes difflib suggestion) / `RULEBASE_002` (invalid name) / `RULEBASE_003` (syntax error on save) / `RULEBASE_004` (I/O failure, propagated as infrastructure error) / `RULEBASE_005` (oversize); `ConfigurationError` code `CONFIG_002` for bundled-sync failures at server startup. Leading `% description:` / `% tags:` comments in rule-base files surface as metadata from `list_rule_bases`.
- Structured error classification on `ExecutionResult.metadata`: `error_category` (syntax_error / undefined_predicate / unbound_variable / type_error / domain_error / evaluation_error / permission_error / timeout / unknown) and `error_explanation` (natural-language hint). The raw `error` field is preserved for callers that need the original SWI-Prolog output.
- Opt-in proof trace via `trace: bool = False` parameter on `execute_prolog` (MCP) and `PrologExecutor.execute()` (library). When enabled, `metadata.proof_trace` returns a list of structured Prolog term strings — one per solution — built by a meta-interpreter (`'$pr_prove'/2`) that introspects rule application via `clause/2`. Constructors: `proof(Goal, Body)`, `builtin(Goal)`, `negation_as_failure(Goal)`, `opaque(Goal)`, conjunction tuples. Includes a new `trace_mechanism_error` category for self-detected meta-interpreter bugs. Default behavior (trace=False) is unchanged. Known limitation: CLP(FD)-bearing code is not supported with trace=True (use trace=False).

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

### Reliability
- Guaranteed tempfile cleanup via `try/finally` in `PrologExecutor.execute()` and `validate_syntax()` — no leak on unexpected exceptions

### Security
- API key redaction in all log output
- Input validation via Pydantic models
- Execution timeout protection against infinite loops
