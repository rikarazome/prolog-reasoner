# Security Policy

## Supported versions

Only the latest `0.x` release receives security fixes while the project is in
alpha.

## Reporting a vulnerability

Please report security issues privately via GitHub's security advisories:

https://github.com/rikarazome/prolog-reasoner/security/advisories/new

Do **not** open a public issue for anything you believe has security impact.

We aim to acknowledge reports within a few days and to ship a fix or mitigation
as quickly as practical given the scope of the project.

## Threat model

`prolog-reasoner` executes SWI-Prolog code in a subprocess. The MCP server does
not run untrusted code beyond what the connected LLM sends it, and the Python
library only runs Prolog that the calling application passes in. Execution is
bounded by a timeout (`PROLOG_REASONER_EXECUTION_TIMEOUT_SECONDS`, default 10s)
to contain runaway queries, but the Prolog process has the same filesystem and
network access as the user running it.

If you are exposing this to untrusted input, run it inside a sandbox (container,
VM, or SWI-Prolog's own safety facilities) appropriate to your environment.
