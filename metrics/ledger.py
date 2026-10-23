"""The usage ledger: one JSON line for every call the built-in client makes.

    python metrics/ledger.py      # calls and tokens per connection: last hour, last day, this month

RELEASE-PLAN Phase 2b, "evidence by default". A line:

    {"t": 1761264000.123, "at": "2026-10-23T19:00:00-05:00", "alias": "...",
     "connection": "groq", "endpoint": "openwebui", "served": "llama-...",
     "status": "ok", "attempts": 1, "tokens_in": 812, "tokens_out": 96,
     "reasoning": 0, "seconds": 2.4, "caller": "gateway", "purpose": ""}

`t` is when the call STARTED (epoch seconds): that is the moment a provider's
per-minute window counts, and pacing counts the same way. `at` is the same
moment for people. `attempts` is how many HTTP requests the call sent,
retries included, and 0 for a call the bench refused to send, since requests
are what a provider's request limits count. `status` is ok, empty, error,
timeout or refused (not sent: pacing, or the free-tier guard). Tokens are the
provider's own figures from the reply's usage, absent when it gave none.
`caller` is the script that made the call (gateway, perf/run, suite/run, ...)
and `purpose` what the caller said it was for, when it said.

A FILE A MONTH
--------------
metrics/usage/YYYY-MM.jsonl, by UTC month. Pacing (metrics/pacing.py) counts
a window before every call on a limited connection, and its longest window
is a calendar month, so a count reads this month's file and at most the one
before, never the whole history.

Lines are appended under locked.lock, so the gateway's seat threads and the
suite's processes never interleave half-lines. A reader takes no lock and
skips a line it cannot parse (one still being written).

It replaces metrics/cost.py's read of the llm CLI's logs.db. Calls made
through the llm backend are still only in logs.db, so cost.py reads both;
nothing is in both.
"""

from __future__ import annotations

import calendar
import collections
import datetime as dt
import json
import pathlib
import sys
import time

import locked

ROOT = pathlib.Path(__file__).resolve().parent.parent
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
DIR = DATA / "metrics" / "usage"


def _file(t: float) -> pathlib.Path:
    return DIR / (time.strftime("%Y-%m", time.gmtime(t)) + ".jsonl")


def append(row: dict) -> None:
    """Add one call's line. `row["t"]` (its start) picks the month's file."""
    line = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
    path = _file(row["t"])
    with locked.lock(DIR / "usage"):
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(line)


def month_start(t: float) -> float:
    """The first moment of t's calendar month, UTC."""
    g = time.gmtime(t)
    return float(calendar.timegm((g.tm_year, g.tm_mon, 1, 0, 0, 0)))


def next_month(t: float) -> float:
    """The first moment of the calendar month after t's, UTC."""
    g = time.gmtime(t)
    y, m = (g.tm_year + 1, 1) if g.tm_mon == 12 else (g.tm_year, g.tm_mon + 1)
    return float(calendar.timegm((y, m, 1, 0, 0, 0)))


def rows(since: float = 0.0, until: float | None = None):
    """Every line that started at or after `since` (and before `until`), oldest
    file first. since=0 reads every month there is."""
    if since <= 0:
        files = sorted(DIR.glob("*.jsonl"))
    else:
        files, t = [], month_start(since)
        end = until or time.time()
        while t <= end:
            files.append(_file(t))
            t = next_month(t)
    for path in files:
        try:
            fh = open(path, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue                           # a line still being written
                if not isinstance(row, dict) or not isinstance(row.get("t"), (int, float)):
                    continue
                if row["t"] >= since and (until is None or row["t"] < until):
                    yield row


def tokens(usage: dict) -> dict:
    """{tokens_in, tokens_out, reasoning} from a reply's usage, in either
    spelling: chat completions' prompt/completion tokens, or the Responses
    API's input/output tokens. Absent keys were not reported."""
    if not isinstance(usage, dict):
        return {}
    out = {}
    for key, names in (("tokens_in", ("prompt_tokens", "input_tokens")),
                       ("tokens_out", ("completion_tokens", "output_tokens"))):
        val = next((usage[n] for n in names if isinstance(usage.get(n), int)), None)
        if val is not None:
            out[key] = val
    details = usage.get("completion_tokens_details") or usage.get("output_tokens_details") or {}
    if isinstance(details, dict) and isinstance(details.get("reasoning_tokens"), int):
        out["reasoning"] = details["reasoning_tokens"]
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    now = time.time()
    spans = (("hour", now - 3600), ("day", now - 86400), ("month", month_start(now)))
    agg: dict = collections.defaultdict(lambda: {s: [0, 0] for s, _ in spans})
    for row in rows(min(t for _, t in spans)):
        for span, since in spans:
            if row["t"] >= since:
                a = agg[row.get("connection") or "?"][span]
                a[0] += row.get("attempts") or 0
                a[1] += (row.get("tokens_in") or 0) + (row.get("tokens_out") or 0)
    if not agg:
        print(f"no calls in the ledger this month ({DIR})")
        return
    print(f"{'connection':<24}" + "".join(f"{s + ' (req/tok)':>22}" for s, _ in spans))
    for conn, a in sorted(agg.items()):
        print(f"{conn:<24}" + "".join(f"{a[s][0]:>10,} / {a[s][1]:>9,}" for s, _ in spans))
    print(f"\nmonth from {dt.datetime.fromtimestamp(month_start(now), dt.timezone.utc):%Y-%m-%d} UTC; {DIR}")


if __name__ == "__main__":
    main()
