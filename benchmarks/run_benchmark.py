"""Benchmark: LLM-only vs LLM+Prolog reasoning accuracy comparison.

Runs a set of logical reasoning problems through two pipelines:
1. LLM-only: Ask the LLM to answer directly
2. LLM+Prolog: Translate to Prolog, execute, extract answer

Requires: PROLOG_REASONER_LLM_API_KEY environment variable.

Usage:
    docker run --rm -e PROLOG_REASONER_LLM_API_KEY=sk-... \
        prolog-reasoner-dev python benchmarks/run_benchmark.py
"""

import asyncio
import json
import sys
import time
from pathlib import Path

from prolog_reasoner.config import Settings
from prolog_reasoner.executor import PrologExecutor
from prolog_reasoner.llm_client import LLMClient
from prolog_reasoner.models import TranslationRequest
from prolog_reasoner.reasoner import PrologReasoner
from prolog_reasoner.translator import PrologTranslator

PROBLEMS_PATH = Path(__file__).parent / "problems.json"

# Prompt for LLM-only mode
LLM_ONLY_SYSTEM = """\
You are a precise logical reasoning assistant.
Answer the question based ONLY on the given premises.
Your response must be EXACTLY one of these formats:
- For boolean: "true" or "false"
- For a set of values: comma-separated lowercase values like "bob,ann,pat"
- For a count: just the number like "6"
- For a single value: just the value like "fish"
- For assignments: "A=knight,B=knave"
No explanations. Just the answer."""


def load_problems() -> list[dict]:
    with open(PROBLEMS_PATH) as f:
        return json.load(f)


def normalize_answer(raw: str, answer_type: str) -> object:
    """Normalize LLM response to comparable format."""
    raw = raw.strip().lower().strip('"').strip("'").strip(".")

    if answer_type == "boolean":
        return raw in ("true", "yes", "1")

    if answer_type == "set":
        items = [x.strip() for x in raw.replace(" ", "").split(",")]
        return set(items)

    if answer_type == "count":
        # Extract first number from response
        for token in raw.split():
            try:
                return int(token)
            except ValueError:
                continue
        return -1

    if answer_type == "number":
        for token in raw.split():
            try:
                return int(token)
            except ValueError:
                continue
        return -1

    if answer_type == "value":
        return raw.split(",")[0].split("=")[-1].strip()

    if answer_type == "assignment":
        # Parse "A=knight,B=knave" format
        result = {}
        for pair in raw.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                result[k.strip().upper()] = v.strip().lower()
        return result

    return raw


def check_answer(got: object, expected: object, answer_type: str) -> bool:
    """Compare normalized answer against expected."""
    if answer_type == "boolean":
        return got == expected

    if answer_type == "set":
        if isinstance(expected, list):
            expected = set(expected)
        return got == expected

    if answer_type in ("count", "number"):
        return got == expected

    if answer_type == "value":
        return str(got).lower() == str(expected).lower()

    if answer_type == "assignment":
        if isinstance(got, dict) and isinstance(expected, dict):
            return got == {k.upper(): v.lower() for k, v in expected.items()}
        return False

    return str(got) == str(expected)


def extract_prolog_answer(
    output: str, answer_type: str, query: str
) -> object:
    """Extract structured answer from Prolog output."""
    lines = [
        l.strip() for l in output.strip().splitlines()
        if l.strip() and l.strip() != "false" and l.strip() != "__TRUNCATED__"
    ]

    if answer_type == "boolean":
        return len(lines) > 0  # Any result = true

    if answer_type == "set":
        # Extract variable bindings from write_canonical output
        values = set()
        for line in lines:
            # Parse e.g. "ancestor(tom,bob)" → extract last arg
            if "(" in line:
                inner = line[line.index("(") + 1 : line.rindex(")")]
                parts = inner.split(",")
                values.add(parts[-1].strip())
        return values

    if answer_type == "count":
        return len(lines)

    if answer_type == "number":
        # For SEND+MORE=MONEY, extract the values
        if lines:
            line = lines[0]
            # Extract digits from solve([S,E,N,D,M,O,R,Y])
            if "[" in line:
                nums = line[line.index("[") + 1 : line.index("]")]
                digits = [int(x.strip()) for x in nums.split(",")]
                if len(digits) == 8:
                    # M*10000 + O*1000 + N*100 + E*10 + Y
                    return digits[4]*10000 + digits[5]*1000 + digits[2]*100 + digits[1]*10 + digits[7]
        return -1

    if answer_type == "value":
        if lines:
            line = lines[0]
            if "(" in line:
                inner = line[line.index("(") + 1 : line.rindex(")")]
                return inner.strip().lower()
            return line.lower()
        return ""

    if answer_type == "assignment":
        result = {}
        if lines:
            line = lines[0]
            if "(" in line:
                inner = line[line.index("(") + 1 : line.rindex(")")]
                parts = inner.split(",")
                if len(parts) >= 2:
                    result["A"] = parts[0].strip().lower()
                    result["B"] = parts[1].strip().lower()
        return result

    return lines[0] if lines else ""


async def run_llm_only(
    llm: LLMClient, problem: dict
) -> tuple[bool, str, float]:
    """Run LLM-only pipeline. Returns (correct, raw_answer, time_ms)."""
    start = time.monotonic()
    try:
        response = await llm.complete(
            system_prompt=LLM_ONLY_SYSTEM,
            user_prompt=problem["question"],
            temperature=0.0,
        )
        elapsed = (time.monotonic() - start) * 1000

        answer = normalize_answer(response, problem["answer_type"])
        correct = check_answer(answer, problem["expected_answer"], problem["answer_type"])
        return correct, response.strip(), elapsed

    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return False, f"ERROR: {e}", elapsed


async def run_prolog_pipeline(
    reasoner: PrologReasoner,
    executor: PrologExecutor,
    problem: dict,
) -> tuple[bool, str, float]:
    """Run LLM+Prolog pipeline. Returns (correct, raw_output, time_ms)."""
    start = time.monotonic()
    try:
        # Step 1: Translate to Prolog
        tr = await reasoner.translate(
            TranslationRequest(query=problem["question"])
        )

        if not tr.success:
            elapsed = (time.monotonic() - start) * 1000
            return False, f"TRANSLATION_FAILED: {tr.error}", elapsed

        # Step 2: Execute
        query = tr.suggested_query
        if not query:
            # Fallback to hint query if translation didn't produce one
            query = problem.get("prolog_hint", {}).get("query", "")

        if not query:
            elapsed = (time.monotonic() - start) * 1000
            return False, "NO_QUERY_EXTRACTED", elapsed

        er = await executor.execute(
            prolog_code=tr.prolog_code,
            query=query,
        )
        elapsed = (time.monotonic() - start) * 1000

        if not er.success:
            return False, f"EXECUTION_FAILED: {er.error}", elapsed

        # Step 3: Extract answer
        answer = extract_prolog_answer(
            er.output, problem["answer_type"], query
        )
        correct = check_answer(answer, problem["expected_answer"], problem["answer_type"])
        return correct, er.output.strip(), elapsed

    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return False, f"ERROR: {e}", elapsed


async def run_prolog_direct(
    executor: PrologExecutor, problem: dict
) -> tuple[bool, str, float]:
    """Run Prolog directly with hint code (oracle baseline)."""
    hint = problem.get("prolog_hint", {})
    if not hint:
        return False, "NO_HINT", 0.0

    start = time.monotonic()
    er = await executor.execute(
        prolog_code=hint["code"],
        query=hint["query"],
    )
    elapsed = (time.monotonic() - start) * 1000

    if not er.success:
        return False, f"EXECUTION_FAILED: {er.error}", elapsed

    answer = extract_prolog_answer(
        er.output, problem["answer_type"], hint["query"]
    )
    correct = check_answer(answer, problem["expected_answer"], problem["answer_type"])
    return correct, er.output.strip(), elapsed


async def main():
    problems = load_problems()
    settings = Settings()
    llm = LLMClient(
        provider=settings.llm_provider,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )
    executor = PrologExecutor(settings)
    translator = PrologTranslator(llm, settings)
    reasoner = PrologReasoner(translator, executor)

    print("=" * 70)
    print(f"BENCHMARK: LLM-only vs LLM+Prolog Reasoning")
    print(f"Model: {settings.llm_provider}/{settings.llm_model}")
    print(f"Problems: {len(problems)}")
    print("=" * 70)

    # First verify Prolog hint solutions work (oracle)
    print("\n--- Prolog Oracle (verifying problem correctness) ---")
    oracle_pass = 0
    for p in problems:
        correct, output, ms = await run_prolog_direct(executor, p)
        status = "PASS" if correct else "FAIL"
        if not correct:
            print(f"  [{status}] {p['id']}: {output[:80]}")
        else:
            oracle_pass += 1
    print(f"  Oracle: {oracle_pass}/{len(problems)} problems verified\n")

    if oracle_pass < len(problems):
        print("WARNING: Some oracle problems failed. Fix before trusting results.\n")

    # Run LLM-only
    print("--- LLM-only ---")
    llm_results = []
    for p in problems:
        correct, raw, ms = await run_llm_only(llm, p)
        llm_results.append({"id": p["id"], "correct": correct, "raw": raw, "ms": ms})
        status = "PASS" if correct else "FAIL"
        print(f"  [{status}] {p['id']:20s} ({ms:6.0f}ms) {raw[:50]}")

    # Run LLM+Prolog
    print("\n--- LLM+Prolog ---")
    prolog_results = []
    for p in problems:
        correct, raw, ms = await run_prolog_pipeline(reasoner, executor, p)
        prolog_results.append({"id": p["id"], "correct": correct, "raw": raw, "ms": ms})
        status = "PASS" if correct else "FAIL"
        print(f"  [{status}] {p['id']:20s} ({ms:6.0f}ms) {raw[:50]}")

    # Summary
    llm_correct = sum(1 for r in llm_results if r["correct"])
    prolog_correct = sum(1 for r in prolog_results if r["correct"])
    llm_avg_ms = sum(r["ms"] for r in llm_results) / len(llm_results)
    prolog_avg_ms = sum(r["ms"] for r in prolog_results) / len(prolog_results)

    print("\n" + "=" * 70)
    print("RESULTS SUMMARY")
    print("=" * 70)
    print(f"{'':20s} {'LLM-only':>12s} {'LLM+Prolog':>12s}")
    print(f"{'Correct':20s} {llm_correct:>8d}/{len(problems):<3d} {prolog_correct:>8d}/{len(problems):<3d}")
    print(f"{'Accuracy':20s} {llm_correct/len(problems)*100:>11.1f}% {prolog_correct/len(problems)*100:>11.1f}%")
    print(f"{'Avg latency':20s} {llm_avg_ms:>10.0f}ms {prolog_avg_ms:>10.0f}ms")
    print()

    # By category
    categories = sorted(set(p["category"] for p in problems))
    print(f"{'Category':15s} {'LLM-only':>10s} {'LLM+Prolog':>12s}")
    print("-" * 40)
    for cat in categories:
        cat_problems = [p for p in problems if p["category"] == cat]
        cat_ids = {p["id"] for p in cat_problems}
        llm_cat = sum(1 for r in llm_results if r["id"] in cat_ids and r["correct"])
        prolog_cat = sum(1 for r in prolog_results if r["id"] in cat_ids and r["correct"])
        n = len(cat_problems)
        print(f"{cat:15s} {llm_cat:>5d}/{n:<4d} {prolog_cat:>7d}/{n:<4d}")

    print()

    # Export results
    results = {
        "model": f"{settings.llm_provider}/{settings.llm_model}",
        "problems_count": len(problems),
        "llm_only": {
            "correct": llm_correct,
            "accuracy": llm_correct / len(problems),
            "avg_latency_ms": llm_avg_ms,
            "details": llm_results,
        },
        "llm_prolog": {
            "correct": prolog_correct,
            "accuracy": prolog_correct / len(problems),
            "avg_latency_ms": prolog_avg_ms,
            "details": prolog_results,
        },
    }

    output_path = Path(__file__).parent / "results.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"Detailed results saved to {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
