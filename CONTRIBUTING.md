# Contributing to prolog-reasoner

Thanks for your interest. This project is small and pragmatic — the fastest path
to getting a change merged is to keep PRs focused and include tests.

## Development environment

All development and testing happens in Docker so that SWI-Prolog and Python are
pinned. You do not need SWI-Prolog installed on your host.

```bash
docker build -f docker/Dockerfile -t prolog-reasoner-dev .
docker run --rm prolog-reasoner-dev                              # run tests
docker run --rm prolog-reasoner-dev pytest tests/ -v --cov=prolog_reasoner
```

CI mirrors this setup on Python 3.10 – 3.13 (see `.github/workflows/test.yml`).

## Submitting changes

1. Open an issue first if you're proposing a non-trivial change.
2. Fork, branch, and keep the PR scoped to one topic.
3. Run the test suite locally and make sure it still passes.
4. If you add behavior, add a test. If you fix a bug, add a regression test.
5. Update `CHANGELOG.md` under `## [Unreleased]` if user-visible.

## Reporting bugs

Please include:

- SWI-Prolog version (`swipl --version`)
- Python version
- Minimal reproduction: the Prolog code, the query, and the observed output
- Whether you hit it via the MCP server or the Python library

## What's in scope

This project is a thin bridge between LLMs and SWI-Prolog. Changes that expand
the Prolog surface (CLP(FD), new backends, better error messages) are welcome.
Changes that duplicate functionality already available in SWI-Prolog itself, or
that add heavy new dependencies, are usually not.
