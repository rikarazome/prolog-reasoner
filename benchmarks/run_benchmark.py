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
import re
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


def _split_args(s: str) -> list[str]:
    """Split top-level comma-separated args, respecting nested brackets."""
    parts, depth, buf = [], 0, []
    for ch in s:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf).strip())
    return parts


def _split_comma_term(line: str) -> list[str] | None:
    """Flatten a ','(G1, G2, ...) prefix-functor term into a list of goals.

    SWI-Prolog's write_canonical/1 prints a top-level conjunction using the
    prefix functor notation ','/2. When a compound query like `a(X), b(Y)`
    succeeds, the output line becomes `,'(a(X),b(Y))`. This helper unpacks
    it so each goal can be inspected independently.

    Returns None if the line is not a recognisable comma term.
    """
    s = line.strip()
    if not (s.startswith("','(") or s.startswith(",(") or s.startswith("','(")):
        return None
    open_idx = s.index("(")
    if not s.endswith(")"):
        return None
    inner = s[open_idx + 1 : -1]
    args = _split_args(inner)
    if len(args) < 2:
        return None
    flat: list[str] = []
    for a in args:
        sub = _split_comma_term(a)
        if sub:
            flat.extend(sub)
        else:
            flat.append(a)
    return flat


def _query_var_positions(query: str) -> tuple[str, list[int]]:
    """Parse a simple Prolog goal and return (predicate, var_arg_indices).

    For a query like "descendant(X, tom)" returns ("descendant", [0]).
    For non-matching shapes (compound goals, no parens) returns ("", []).

    Used to disambiguate which argument of an output term carries the
    answer when the LLM places its variable at a non-trailing position.
    """
    q = query.strip().rstrip(".")
    m = re.match(r"^([a-z_]\w*)\s*\((.*)\)\s*$", q)
    if not m:
        return "", []
    name = m.group(1)
    args = _split_args(m.group(2))
    positions = [
        i for i, a in enumerate(args)
        if re.match(r"^[A-Z_]\w*$", a.strip())
    ]
    return name, positions


def extract_prolog_answer(
    output: str, answer_type: str, query: str
) -> object:
    """Extract structured answer from Prolog output, tolerating multiple
    output styles the LLM may produce (write_canonical, findall lists,
    Var = Value bindings, custom write messages)."""
    raw_lines = output.strip().splitlines()
    lines = [
        l.strip() for l in raw_lines
        if l.strip() and l.strip() != "__TRUNCATED__"
    ]
    # Lines after stripping bare "false" (a failed branch is not an answer)
    sig_lines = [l for l in lines if l != "false"]

    # Flatten any ','(G1, G2, ...) prefix-functor output — this happens when
    # the LLM's Query is a conjunction like "a(X), b(Y)" and write_canonical
    # prints the whole goal term. Unpacking lets downstream strategies reason
    # about each sub-goal independently.
    flat: list[str] = []
    for ln in sig_lines:
        parts = _split_comma_term(ln)
        flat.extend(parts) if parts else flat.append(ln)
    sig_lines = flat

    if answer_type == "boolean":
        # No significant output = query failed = false
        if not sig_lines:
            return False
        joined = " ".join(sig_lines).lower()
        # Explicit textual signals from custom write/format calls.
        # `contradict` is matched without word boundary because LLMs sometimes
        # emit repeated tokens like "contradictioncontradiction" with no
        # separator (e.g. via write/1 in a forall loop).
        if re.search(r"\b(false|no|inconsistent)\b|contradict", joined):
            return False
        if re.search(r"\b(true|yes|consistent)\b", joined):
            return True
        # Bare "false" anywhere with no other content already handled above
        return True

    if answer_type == "set":
        values: set[str] = set()
        # Strategy 1: list literal anywhere in output, e.g. [bob,ann,pat]
        for line in sig_lines:
            for m in re.finditer(r"\[([^\[\]]*)\]", line):
                inner = m.group(1).strip()
                if not inner:
                    continue
                for tok in _split_args(inner):
                    tok = tok.strip().strip("'\"").lower()
                    if tok and tok not in ("_", "[]"):
                        values.add(tok)
        if values:
            return values
        # Strategy 2: Var = value bindings (one or many on a line)
        for line in sig_lines:
            for m in re.finditer(r"([A-Z_]\w*)\s*=\s*([a-z_]\w*)", line):
                values.add(m.group(2).lower())
        if values:
            return values
        # Strategy 3: predicate(args...) — prefer the position(s) where the
        # query had unbound variables; fall back to last arg otherwise.
        qname, qvars = _query_var_positions(query)
        for line in sig_lines:
            if "(" in line and line.endswith(")"):
                line_pred = re.match(r"^([a-z_]\w*)\s*\(", line)
                inner = line[line.index("(") + 1 : line.rindex(")")]
                parts = _split_args(inner)
                if not parts:
                    continue
                use_qvars = (
                    qname
                    and line_pred is not None
                    and line_pred.group(1) == qname
                    and qvars
                    and all(p < len(parts) for p in qvars)
                )
                if use_qvars:
                    for p in qvars:
                        values.add(parts[p].strip().strip("'\"").lower())
                else:
                    values.add(parts[-1].strip().strip("'\"").lower())
        return values

    if answer_type == "count":
        # Strategy 1: a single line that's just an integer
        if len(sig_lines) == 1:
            m = re.fullmatch(r"-?\d+", sig_lines[0])
            if m:
                return int(m.group(0))
        # Strategy 2: predicate(N) where N is integer (e.g. count(6))
        if len(sig_lines) == 1 and "(" in sig_lines[0]:
            inner = sig_lines[0][sig_lines[0].index("(") + 1 : sig_lines[0].rindex(")")]
            parts = _split_args(inner)
            for p in parts:
                m = re.fullmatch(r"-?\d+", p.strip())
                if m:
                    return int(m.group(0))
        # Strategy 3: Var = N binding
        if len(sig_lines) == 1:
            m = re.search(r"=\s*(-?\d+)\b", sig_lines[0])
            if m:
                return int(m.group(1))
        # Strategy 4: explicit count phrase like "(6 total)" / "6 solutions"
        joined = " ".join(sig_lines)
        m = re.search(
            r"(\d+)\s*(?:total|solutions?|valid|distinct|colorings?|results?|ways?)",
            joined, re.IGNORECASE,
        )
        if m:
            return int(m.group(1))
        # Strategy 5: count lines that look like solution bindings (contain '=')
        binding_lines = [l for l in sig_lines if "=" in l and not l.endswith(":")]
        if binding_lines:
            return len(binding_lines)
        # Strategy 6: count enumerated solutions (one per line)
        return len(sig_lines)

    if answer_type == "number":
        # Strategy 1: SEND+MORE — bindings list of 8 digits
        for line in sig_lines:
            for m in re.finditer(r"\[([^\[\]]+)\]", line):
                parts = _split_args(m.group(1))
                if len(parts) == 8:
                    try:
                        d = [int(x.strip()) for x in parts]
                        # MONEY = M*10000 + O*1000 + N*100 + E*10 + Y
                        return d[4]*10000 + d[5]*1000 + d[2]*100 + d[1]*10 + d[7]
                    except ValueError:
                        pass
        # Strategy 2: explicit "MONEY = 10652" or any Var = number
        for line in sig_lines:
            m = re.search(r"\bMONEY\s*=\s*(-?\d+)", line, re.IGNORECASE)
            if m:
                return int(m.group(1))
        # Strategy 3: per-letter bindings — assemble MONEY
        bindings: dict[str, int] = {}
        for line in sig_lines:
            for m in re.finditer(r"\b([A-Z])\s*=\s*(\d)\b", line):
                bindings[m.group(1)] = int(m.group(2))
        if all(k in bindings for k in "MONEY"):
            return (bindings["M"]*10000 + bindings["O"]*1000
                    + bindings["N"]*100 + bindings["E"]*10 + bindings["Y"])
        # Strategy 4: a single integer line
        for line in sig_lines:
            m = re.fullmatch(r"-?\d+", line)
            if m:
                return int(m.group(0))
        # Strategy 5: predicate(a1, ..., aN) — prefer the LAST integer arg.
        # Symmetric to Plan Z's value extraction: when the LLM wraps bindings
        # plus the computed answer in one predicate (e.g. solve(S,E,N,D,M,O,
        # R,Y,MONEY) = solve(9,5,6,7,1,0,8,2,10652)), the answer is in the
        # final argument, not the first binding.
        for line in sig_lines:
            if "(" in line and line.endswith(")"):
                inner = line[line.index("(") + 1 : line.rindex(")")]
                parts = _split_args(inner)
                for p in reversed(parts):
                    m = re.fullmatch(r"-?\d+", p.strip())
                    if m:
                        return int(m.group(0))
        # Strategy 6: first integer anywhere (last resort)
        for line in sig_lines:
            m = re.search(r"-?\d+", line)
            if m:
                return int(m.group(0))
        return -1

    if answer_type == "value":
        if not sig_lines:
            return ""
        # Var = value — prefer any line that exposes a direct binding
        for line in sig_lines:
            m = re.search(r"=\s*([a-z_]\w*)", line)
            if m:
                return m.group(1).lower()
        # predicate(value) — prefer the LAST sub-goal. After comma-term
        # flattening, the answer variable is typically bound in the final
        # goal of a conjunction (e.g. `nth1(_, _, Pet)` after `solve(...)`).
        for line in reversed(sig_lines):
            if "(" in line and line.endswith(")"):
                inner = line[line.index("(") + 1 : line.rindex(")")]
                parts = _split_args(inner)
                if parts:
                    return parts[-1].strip().strip("'\"").lower()
        return sig_lines[-1].lower()

    if answer_type == "assignment":
        result: dict[str, str] = {}
        # Strategy 1: explicit A=knight, B=knave bindings anywhere
        for line in sig_lines:
            for m in re.finditer(r"\b([A-Z])\s*=\s*([a-z]\w*)", line):
                result[m.group(1).upper()] = m.group(2).lower()
        if "A" in result and "B" in result:
            return {"A": result["A"], "B": result["B"]}
        # Strategy 2: prose like "A is a knave" / "B is the knight"
        for line in sig_lines:
            for m in re.finditer(
                r"\b([A-Z])\b\s+is\s+(?:an?|the)?\s*([a-z]+)",
                line, re.IGNORECASE,
            ):
                result[m.group(1).upper()] = m.group(2).lower()
        if "A" in result and "B" in result:
            return {"A": result["A"], "B": result["B"]}
        # Strategy 3: predicate(a,b) — take first two args positionally,
        # but only if they look like meaningful atoms (not bare integers)
        for line in sig_lines:
            if "(" in line and line.endswith(")"):
                inner = line[line.index("(") + 1 : line.rindex(")")]
                parts = _split_args(inner)
                if len(parts) >= 2:
                    a, b = parts[0].strip().lower(), parts[1].strip().lower()
                    if not (a.lstrip("-").isdigit() and b.lstrip("-").isdigit()):
                        return {"A": a, "B": b}
        return result

    return sig_lines[0] if sig_lines else ""


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
) -> tuple[bool, str, float, dict]:
    """Run LLM+Prolog pipeline. Returns (correct, raw_output, time_ms, trace).

    trace dict captures intermediate artifacts for inspection/promo material:
    prolog_code, query, output.
    """
    start = time.monotonic()
    trace: dict = {"prolog_code": "", "query": "", "output": ""}
    try:
        tr = await reasoner.translate(
            TranslationRequest(query=problem["question"])
        )

        # Always capture intermediate artifacts, even on failure, for diagnosis
        trace["prolog_code"] = tr.prolog_code or ""
        trace["query"] = tr.suggested_query or ""

        if not tr.success:
            elapsed = (time.monotonic() - start) * 1000
            return False, f"TRANSLATION_FAILED: {tr.error}", elapsed, trace

        query = tr.suggested_query
        if not query:
            query = problem.get("prolog_hint", {}).get("query", "")
        trace["query"] = query

        if not query:
            elapsed = (time.monotonic() - start) * 1000
            return False, "NO_QUERY_EXTRACTED", elapsed, trace

        er = await executor.execute(
            prolog_code=tr.prolog_code,
            query=query,
        )
        elapsed = (time.monotonic() - start) * 1000
        trace["output"] = er.output

        if not er.success:
            return False, f"EXECUTION_FAILED: {er.error}", elapsed, trace

        answer = extract_prolog_answer(
            er.output, problem["answer_type"], query
        )
        correct = check_answer(answer, problem["expected_answer"], problem["answer_type"])
        return correct, er.output.strip(), elapsed, trace

    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return False, f"ERROR: {e}", elapsed, trace


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
        correct, raw, ms, trace = await run_prolog_pipeline(reasoner, executor, p)
        prolog_results.append({
            "id": p["id"],
            "correct": correct,
            "raw": raw,
            "ms": ms,
            "prolog_code": trace["prolog_code"],
            "query": trace["query"],
            "output": trace["output"],
        })
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
