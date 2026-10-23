"""The PERFORMANCE tier: what the serving path does, tracked over time.

This measures the PATH, not the model. Latency through Open WebUI to whichever
provider is behind it depends on congestion, rate limits, routing and the time
of day — none of which are properties of the model, and all of which decide
whether an offload is worth making right now.

That makes it the opposite of `suite/` and `floor/`, which are built to hold
still. A single perf number is nearly meaningless. **The history is the
measurement**, which is why every run appends to `perf/history.csv` rather than
overwriting a report: a p50 that doubles over a month is a real finding, and a
p50 that is 40% higher this afternoon is a Tuesday.

WHAT IS RECORDED, AND WHY EACH
------------------------------
- **p50 and p95, not the mean.** One 300-second timeout drags a mean into
  fiction. p95 is where rate limiting shows up, and rate limiting is the thing
  most likely to make a model unusable while looking fine on average.
- **Two prompt sizes.** A one-word prompt measures the FLOOR — connection,
  queueing, cold start. A large prompt measures THROUGHPUT. A model can be
  fine on one and hopeless on the other, and `nemotron-lightning` (134 s on a
  one-word prompt) is why the distinction earns its place.
- **Characters per second, and tokens per second where they are real.** The
  `llm` CLI does not report token counts for these endpoints, so chars/s is
  kept as the proxy every row has; it is affected by how verbose a model is.
  The built-in client reads the provider's own usage from the stream (Phase
  2b), and with it `tokens_per_s` is the real thing: output tokens (reasoning
  included, which is generated like any other) over the time from the first
  token to the last, so queueing and connection time are not in it. Blank
  where the provider gave no usage, never estimated.
- **Outcome counts, split four ways.** answered / refused / error / timeout.
  Collapsing these is the mistake this repo was built around: a safety filter,
  an overloaded service and a slow model are three different facts.
- **A timestamp on every row**, because the whole point is the trend.

WHAT THIS CANNOT TELL YOU
-------------------------
Whether a model is any good. That is `suite/`. A model can be the fastest here
and still invent an API that does not exist.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import pathlib
import statistics
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "metrics"))
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
HISTORY = DATA / "perf" / "history.csv"

sys.path.insert(0, str(ROOT / "suite"))
from run import (_learn, _registered_names, ad_hoc_models, ask_once, FAILURE_CLASSES,
                 failure_class, llm_binary, outcome,
                 registered_models)  # noqa: E402

# A one-word prompt measures overhead; a large one measures throughput. The
# large prompt is generated rather than stored so this file stays readable and
# the size is exact.
TINY = "Reply with exactly one word: ready"
LARGE = (
    "Summarise the following configuration in at most three sentences. "
    "Do not quote it back.\n\n"
    + "\n".join(f"setting_{i} = value_{i}  # option {i} of many" for i in range(400))
)

FIELDS = ["timestamp", "model", "prompt", "n", "p50_s", "p95_s", "min_s",
          "max_s", "chars_per_s", "answered", "refused", "error", "timeout",
          # WHY it failed, not just that it did. `structural` means the model
          # is not there and waiting will not help; `capacity` means it is
          # there and busy. The deprecation check reads this to tell a
          # retirement apart from a rate limit.
          "failure",
          # The provider's own words for the worst failure, first 300 chars.
          # Without them `glm-5-3` sat as an unexplained `other` for a month
          # when the message was a plain "404 page not found".
          "detail",
          # Real output tokens/s from the provider's usage (built-in client only).
          "tokens_per_s"]


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile.

    `statistics.quantiles` interpolates, which invents a value that was never
    measured. With n=5 that is misleading, so this returns a real observation.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(round(pct * (len(ordered) - 1)))))
    return ordered[k]


def _ask(binary: str, model: str, prompt: str, timeout: int) -> tuple[str, float, float | None]:
    """ask_once's (text, seconds), plus output tokens/s when the built-in
    client made the call and the provider reported usage."""
    import client
    if client.backend(model) != "builtin":
        return (*ask_once(binary, model, prompt, timeout), None)
    res = client.chat(model, prompt, timeout=timeout, purpose="perf")
    out = (res["usage"] or {}).get("completion_tokens") or (res["usage"] or {}).get("output_tokens")
    # From the first token on, so connection and queueing time are left out.
    span = res["seconds"] - (res.get("first_token_s") or 0)
    rate = out / span if isinstance(out, int) and out > 0 and span > 0 and not res["error"] else None
    return client.output(res, timeout), res["seconds"], rate


def measure(binary: str, model: str, label: str, prompt: str, n: int,
            timeout: int) -> dict:
    latencies, rates, token_rates = [], [], []
    counts = {"answered": 0, "refused": 0, "error": 0, "timeout": 0}
    failures: list[str] = []
    details: dict[str, str] = {}
    for i in range(1, n + 1):
        body, seconds, tokens_per_s = _ask(binary, model, prompt, timeout)
        # A one-word reply is the CORRECT answer to the tiny prompt, so
        # only a genuinely empty one counts as a failure here.
        kind = (outcome(body, min_length=1) or ("answered", ""))[0]
        counts[kind] += 1
        # ONLY classify an actual non-answer. `failure_class` uses the
        # default minimum reply length, so a correct one-word answer to the
        # tiny prompt looked like an empty response and was filed as a
        # failure -- on models that answered 3 of 3.
        if kind != "answered" and (why := failure_class(body)) is not None:
            failures.append(why)
            details.setdefault(why, " ".join(body.split())[:300])
            _learn(model, body, "perf")     # a stated context window, rate limit or wait
        # Only a real answer times anything meaningful. A refusal is fast
        # because it is short, and averaging that in would flatter the model.
        if kind == "answered":
            latencies.append(seconds)
            # Throughput ONLY from the large prompt. Six characters over ten
            # seconds is 0.5 ch/s, which reads as a catastrophically slow
            # model and is really just connection overhead with a one-word
            # answer attached. A number that invites the wrong reading is
            # worse than no number.
            if seconds > 0 and label == "large":
                rates.append(len(body) / seconds)
                if tokens_per_s:
                    token_rates.append(tokens_per_s)
        print(f"    {model} {label} [{i}/{n}] {kind} {seconds:.1f}s", flush=True)
    worst = next((c for c in FAILURE_CLASSES if c in failures), "")
    return {
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "model": model, "prompt": label, "n": n,
        "p50_s": round(percentile(latencies, 0.50), 2),
        "p95_s": round(percentile(latencies, 0.95), 2),
        "min_s": round(min(latencies), 2) if latencies else 0.0,
        "max_s": round(max(latencies), 2) if latencies else 0.0,
        "chars_per_s": round(statistics.median(rates), 1) if rates else 0.0,
        # The worst class seen: structural beats capacity beats timeout, so a
        # model that failed structurally even once is not filed as merely busy.
        # ORDER COMES FROM `suite/run.py`, not from a copy kept here -- a copy
        # is what silently dropped `paywalled` and wrote a blank failure.
        "failure": worst,
        "detail": details.get(worst, ""),
        "tokens_per_s": round(statistics.median(token_rates), 1) if token_rates else "",
        **counts,
    }


def append(rows: list[dict]) -> None:
    """Append, MIGRATING the file first if its header is out of date.

    A column was added to FIELDS while a run was in flight. The running process
    kept the old header, and the next append would have written fourteen values
    under thirteen headings — every row silently shifted by one, which is worse
    than a crash because the file still parses. History is only worth keeping
    if it cannot quietly rot.
    """
    if HISTORY.exists():
        with HISTORY.open(encoding="utf-8", newline="") as fh:
            existing = list(csv.DictReader(fh))
            header = (csv.reader(HISTORY.open(encoding="utf-8"))
                      .__next__() if existing else FIELDS)
        if header != FIELDS:
            with HISTORY.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=FIELDS)
                writer.writeheader()
                writer.writerows({k: r.get(k, "") for k in FIELDS}
                                 for r in existing)

    new = not HISTORY.exists()
    with HISTORY.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        if new:
            writer.writeheader()
        writer.writerows(rows)


def trend(model: str, label: str) -> str:
    """Compare against this model's previous reading, if there is one."""
    if not HISTORY.exists():
        return ""
    prior = [r for r in csv.DictReader(HISTORY.open(encoding="utf-8"))
             if r["model"] == model and r["prompt"] == label]
    if len(prior) < 2:
        return ""
    now, before = float(prior[-1]["p50_s"]), float(prior[-2]["p50_s"])
    if before == 0:
        return ""
    change = (now - before) / before * 100
    if abs(change) < 15:
        return "  (steady)"
    return f"  ({change:+.0f}% vs {prior[-2]['timestamp'][:10]})"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", action="append", dest="models")
    ap.add_argument("-n", type=int, default=5,
                    help="samples per prompt size; more is better and slower")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--tiny-only", action="store_true",
                    help="skip the large prompt (a quick availability check)")
    ap.add_argument("--client", choices=("llm", "builtin"),
                    help="which model client makes the calls (metrics/client.py);"
                         " default: BENCH_CLIENT, else llm")
    args = ap.parse_args()
    if args.client:
        # ask_once reads it per call; set here because a scheduled task's
        # action can pass arguments but not environment variables.
        os.environ["BENCH_CLIENT"] = args.client

    # Every registered model, derived from the yaml — a perf run that
    # skipped a model would hide exactly the outage it exists to find.
    # Except the AD HOC ones: they are capped or expensive, and a daily
    # probe would spend the cap on a latency number. Measured only when
    # named with --model.
    held = ad_hoc_models()
    models = args.models or [m for m in registered_models() if m not in held]
    if not args.models and held:
        print(f"held back (ad hoc — name with --model to measure): "
              f"{', '.join(sorted(held))}")
    prompts = [("tiny", TINY)] if args.tiny_only else [("tiny", TINY),
                                                       ("large", LARGE)]
    binary = llm_binary()
    print(f"{len(models)} models x {len(prompts)} prompts x {args.n} samples")
    print(f"large prompt is {len(LARGE):,} chars\n")

    # OpenRouter's `:free` models share ONE daily budget — 50 requests without
    # credits — so a full weekly sweep at n=3 spends 54 on them alone and the
    # last few get throttled. That is recorded honestly as `capacity` rather
    # than as a fault, but it still means their numbers are measuring the
    # quota. One sample each keeps the whole free tier inside the budget with
    # room to spare, at the cost of no percentile spread for those models.
    served = _registered_names()
    rows = []
    for model in models:
        n = 1 if ":free" in served.get(model, "") else args.n
        for label, prompt in prompts:
            rows.append(measure(binary, model, label, prompt, n,
                                args.timeout))
    try:
        sys.path.insert(0, str(ROOT / "metrics"))
        from cost import note_benchmark_calls
        for row in rows:
            note_benchmark_calls(row["model"], row["n"])
    except Exception:                              # noqa: BLE001
        pass

    append(rows)

    print(f"\n{'model':<24} {'prompt':<7} {'p50':>7} {'p95':>7} {'ch/s':>8}"
          f"  outcomes")
    for r in rows:
        oc = (f"{r['answered']}a/{r['refused']}r/{r['error']}e/{r['timeout']}t")
        print(f"{r['model']:<24} {r['prompt']:<7} {r['p50_s']:>7.1f} "
              f"{r['p95_s']:>7.1f} {r['chars_per_s']:>8.1f}  {oc}"
              + trend(r["model"], r["prompt"]))
    print(f"\nappended {len(rows)} rows to {HISTORY}")
    print("These numbers measure the SERVING PATH, not the models. "
          "One reading is weather; the history is climate.")


if __name__ == "__main__":
    main()
