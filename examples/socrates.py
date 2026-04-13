"""Example: Direct Prolog execution without LLM.

Demonstrates the classic Socrates syllogism:
  All humans are mortal. Socrates is human. Therefore, Socrates is mortal.

Usage:
    docker run --rm -e PROLOG_REASONER_LLM_API_KEY=dummy prolog-reasoner-dev \
        python examples/socrates.py
"""

import asyncio

from prolog_reasoner.config import Settings
from prolog_reasoner.executor import PrologExecutor


async def main():
    settings = Settings(llm_api_key="dummy")
    executor = PrologExecutor(settings)

    prolog_code = """
human(socrates).
human(plato).
human(aristotle).
mortal(X) :- human(X).
"""
    # Query: Who is mortal?
    result = await executor.execute(prolog_code, "mortal(X)")

    print(f"Success: {result.success}")
    print(f"Output:\n{result.output}")
    print(f"Result count: {result.metadata.get('result_count', 'N/A')}")


if __name__ == "__main__":
    asyncio.run(main())
