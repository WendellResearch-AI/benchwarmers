"""What each model actually costs us, from the `llm` CLI's own log.

    python metrics/cost.py

**No new instrumentation.** The `llm` CLI has been logging every call all
along — 382 turns with token counts were already on disk before this file
existed. This reads them.

WHY REASONING TOKENS MATTER SEPARATELY
--------------------------------------
They are billed like output and are invisible in the answer. One
`gemini-3.8-flash-high` call spent **6,524** of them on a prompt whose reply
was a few lines; a trivial one spent 123 of 134 total tokens thinking. A model
that answers correctly at four times the token cost of another is a different
proposition for bulk work, and nothing else in this repo could see that.

WHAT THIS IS NOT
----------------
Money. Most of what we call is free or flat-rate, and inventing a price per
model would be a number nobody could check. Tokens are the honest unit: they
are what we spend, they are what a provider meters, and a rate can be applied
later by whoever knows it.
"""

from __future__ import annotations

import collections
import json
import pathlib
import sqlite3

ROOT = pathlib.Path(__file__).resolve().parent.parent
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
from endpoints import LLM_DIR  # noqa: E402
LOGS = LLM_DIR / "logs.db"
OUT = DATA / "metrics" / "cost.json"


def alias_history() -> dict[str, str]:
    path = DATA / "config" / "alias-history.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


LEDGER = DATA / "metrics" / "benchmark-calls.json"


def note_benchmark_calls(model: str, calls: int) -> None:
    """Record that N of a model's logged calls were a BENCHMARK, not work.

    Without this the call counter measures how much a model was BENCHMARKED
    rather than how much it was relied on, and says the opposite of what it
    appears to. `deepseek-v4-pro-0813` shows 214 calls, of which roughly 200
    are floor runs that failed — it looks like the most-used model here and it
    is nothing of the sort.

    Each runner reports its own total, because each runner is the only thing
    that knows. Offload calls are then the remainder.
    """
    try:
        data = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {}
    except ValueError:
        data = {}
    data[model] = data.get(model, 0) + calls
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")


def benchmark_calls() -> dict[str, int]:
    if not LEDGER.exists():
        return {}
    try:
        return json.loads(LEDGER.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def collect() -> dict:
    """Per-model totals, with old aliases folded into their current name."""
    if not LOGS.exists():
        return {}
    rename = alias_history()
    db = sqlite3.connect(f"file:{LOGS}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row

    agg: dict[str, dict] = collections.defaultdict(
        lambda: {"calls": 0, "input": 0, "output": 0, "reasoning": 0,
                 "duration_ms": 0, "with_reasoning_data": 0})
    for row in db.execute(
            "SELECT model, input_tokens, output_tokens, token_details,"
            " duration_ms FROM turns"):
        # Turns predating the versioned rename carry the old alias.
        model = rename.get(row["model"], row["model"])
        a = agg[model]
        a["calls"] += 1
        a["input"] += row["input_tokens"] or 0
        a["output"] += row["output_tokens"] or 0
        a["duration_ms"] += row["duration_ms"] or 0
        try:
            details = json.loads(row["token_details"] or "{}") or {}
        except ValueError:
            details = {}
        reasoning = ((details.get("completion_tokens_details") or {})
                     .get("reasoning_tokens"))
        if reasoning is not None:
            a["reasoning"] += reasoning
            a["with_reasoning_data"] += 1

    bench = benchmark_calls()
    out = {}
    for model, a in agg.items():
        billed = a["output"] + a["reasoning"]
        out[model] = {
            **a,
            # The number that decides bulk work: how much of what we pay for
            # never appears in the answer.
            "reasoning_share": (round(a["reasoning"] / billed, 3)
                                if billed else None),
            "tokens_per_call": (round((a["input"] + billed) / a["calls"])
                                if a["calls"] else 0),
            # Benchmark calls ONLY. There was an `offload_calls` here too,
            # derived as calls minus benchmarks, and it was wrong in both
            # directions: the ledger began the day it was written, so every
            # earlier suite run counted as work. Real offloads are recorded
            # deliberately in metrics/offload.py, because subtracting one
            # imperfect number from another gives a third nobody can check.
            "benchmark_calls": bench.get(model, 0),
        }
    return out


def main() -> None:
    data = collect()
    OUT.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    rows = sorted(data.items(), key=lambda kv: -kv[1]["tokens_per_call"])
    print(f"{'model':<30} {'calls':>5} {'tok/call':>9} {'reasoning share':>16}")
    for model, a in rows:
        share = ("—" if a["reasoning_share"] is None
                 else f"{a['reasoning_share']:.1%}")
        note = "" if a["with_reasoning_data"] else "  (no reasoning data)"
        print(f"{model:<30} {a['calls']:>5} {a['tokens_per_call']:>9,} "
              f"{share:>16}{note}")
    print(f"\nwrote {OUT}")
    print("Tokens, not money: most of what we call is free or flat-rate, and "
          "a made-up price is a number nobody can check.")


if __name__ == "__main__":
    main()
