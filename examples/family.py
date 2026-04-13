"""Example: Family relationship reasoning with transitive rules.

Demonstrates multi-hop ancestor reasoning:
  parent(tom, bob), parent(bob, ann) => ancestor(tom, ann)

Usage:
    docker run --rm -e PROLOG_REASONER_LLM_API_KEY=dummy prolog-reasoner-dev \
        python examples/family.py
"""

import asyncio

from prolog_reasoner.config import Settings
from prolog_reasoner.executor import PrologExecutor


async def main():
    settings = Settings(llm_api_key="dummy")
    executor = PrologExecutor(settings)

    prolog_code = """
parent(tom, bob).
parent(tom, liz).
parent(bob, ann).
parent(bob, pat).
parent(pat, jim).

ancestor(X, Y) :- parent(X, Y).
ancestor(X, Y) :- parent(X, Z), ancestor(Z, Y).
"""
    # Query: Who are Tom's descendants?
    result = await executor.execute(prolog_code, "ancestor(tom, X)")

    print(f"Tom's descendants:")
    print(result.output)
    print(f"Count: {result.metadata.get('result_count', 'N/A')}")


if __name__ == "__main__":
    asyncio.run(main())
