"""Example: Constraint satisfaction with CLP(FD).

Demonstrates scheduling 3 tasks into 3 time slots with constraints:
  - All tasks in different slots
  - Task A before Task B

Usage:
    docker run --rm -e PROLOG_REASONER_LLM_API_KEY=dummy prolog-reasoner-dev \
        python examples/constraint.py
"""

import asyncio

from prolog_reasoner.config import Settings
from prolog_reasoner.executor import PrologExecutor


async def main():
    settings = Settings(llm_api_key="dummy")
    executor = PrologExecutor(settings)

    prolog_code = """
:- use_module(library(clpfd)).

schedule(A, B, C) :-
    [A, B, C] ins 1..3,
    all_different([A, B, C]),
    A #< B,
    label([A, B, C]).
"""
    result = await executor.execute(prolog_code, "schedule(A, B, C)")

    print("Valid schedules (A < B, all different):")
    print(result.output)
    print(f"Count: {result.metadata.get('result_count', 'N/A')}")


if __name__ == "__main__":
    asyncio.run(main())
