"""Pacing: the stored rate limits enforced before a call, not only shown.

    python metrics/pacing.py <alias>     # the limits that apply, and how much of each is used

RELEASE-PLAN Phase 2b ("limits enforced, not just shown"). Until now the
limits in config/connection-limits.json (the Endpoints dialog), a model's own
`limits` in config/model-overrides.json (its popup) and the limits
metrics/learn.py read out of providers' errors were display only. The
built-in client now asks `admit` before every call. When a window is used
up it waits, if the wait ends before the call's deadline, and otherwise
refuses with an error that names the limit, so nothing is sent that the
provider would refuse, and nothing is spent of a cap that does not come back
(the motivating case: a trial key at 20 a minute and 1,000 a month).

WHAT IS COUNTED
---------------
- rpm, tpm: the last 60 s.  rpd, tpd: the last 24 h, which never allows more
  than a provider's calendar day would, however it counts its day.
  rpmo (connection limits only): the calendar month in UTC, how trial keys
  are reset.
- Requests are the ledger's `attempts` (metrics/ledger.py), retries included:
  what the provider saw. Tokens are its in plus out.
- Calls in flight count too. A panel fires its seats at once, long before
  any of them is in the ledger, so each admitted call is written to
  runs/inflight.json until its ledger line exists. The check and the write
  happen under one lock (runs/pacing.lock), which the gateway's seat threads
  and the suite's processes share; an entry whose call outlived its own
  deadline is dropped, so a killed process cannot hold a slot.
- A connection's limits count every model on it (a key's limits are the
  key's); a model's own or learned limits count that model alone, its own
  beating the learned (as everywhere: your values win).
- Quota records (metrics/quota.py): what a provider last said was left, less
  the calls sent since, until its reset.

The catalog refresh's figures (FreeLLMAPI's listing) are not enforced: they
describe a typical free tier, not this key's.

THE REFUSAL IS A RATE LIMIT
---------------------------
`Error: client failed: rate limit (not sent; ...): ...; try again in 41s`.
"client failed" and "rate limit" are markers (suite/run.py ERRORS and
CAPACITY), so it counts as a retryable capacity failure, never as the model
answering badly or going away; and learn.py reads "try again in 41s" as the
cooldown, so the gateway's explorer seats pass the model by meanwhile. The
text names no "limit N" figure, which learn.py would file as a new limit.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import sys
import time
import uuid

import ledger
import locked

ROOT = pathlib.Path(__file__).resolve().parent.parent
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
LOCK = DATA / "runs" / "pacing"
INFLIGHT = DATA / "runs" / "inflight.json"
# axis -> (what it counts, the window in seconds or "month", how it reads)
AXES = {"rpm": ("requests", 60, "requests per minute"), "rpd": ("requests", 86400, "requests per day"),
        "tpm": ("tokens", 60, "tokens per minute"), "tpd": ("tokens", 86400, "tokens per day"),
        "rpmo": ("requests", "month", "requests a calendar month (UTC)")}
MARGIN_S = 2.0                         # a wait must leave this long for the call itself
POLL_S = 5.0                           # re-check at least this often while waiting


def rules(alias: str, connection: str) -> list[dict]:
    """The limits that apply: {scope, axis, limit, source}."""
    import catalog
    import learn
    conn = catalog.connection_limits().get(connection) or {}
    mine = (catalog.overrides().get(alias) or {}).get("limits") or {}
    seen = (learn.learned().get(alias) or {}).get("limits") or {}
    out = []

    def ok(n) -> bool:
        return isinstance(n, int) and not isinstance(n, bool) and n > 0
    for axis in AXES:
        if ok(n := conn.get(axis)):
            out.append({"scope": "connection", "axis": axis, "limit": n,
                        "source": f"you, for {connection} (config/connection-limits.json)"})
        if axis == "rpmo":
            continue
        if ok(n := mine.get(axis)):
            out.append({"scope": "alias", "axis": axis, "limit": n,
                        "source": "you, for this model (config/model-overrides.json)"})
        elif ok(n := (seen.get(axis) or {}).get("value")):
            out.append({"scope": "alias", "axis": axis, "limit": n,
                        "source": f"learned {str(seen[axis].get('at', ''))[:10]} from the provider"})
    return out


def _wait_text(s: float) -> str:
    """learn.py's spelling (s, m or h), so it reads the cooldown back."""
    return f"{s:.0f}s" if s < 120 else f"{s / 60:.0f}m" if s < 7200 else f"{s / 3600:.0f}h"


def _inflight(now: float) -> dict:
    return {k: v for k, v in locked.read_json(INFLIGHT).items()
            if isinstance(v, dict) and (v.get("until") or 0) > now}


def _blocked(rs: list[dict], records: list[dict], alias: str, connection: str, est: int,
             now: float, rows: list[dict], flying: dict) -> tuple[float, str] | None:
    """(when a slot frees, why) for the window that frees last, or None when
    every window has room for this call."""
    worst = None

    def mine(e: dict, scope: str) -> bool:
        return e.get("connection") == connection if scope == "connection" else e.get("alias") == alias
    for r in rs:
        what, span, reads = AXES[r["axis"]]
        since = ledger.month_start(now) if span == "month" else now - span
        events = sorted(
            [(e["t"], (e.get("attempts") or 0) if what == "requests"
              else (e.get("tokens_in") or 0) + (e.get("tokens_out") or 0))
             for e in rows if e["t"] >= since and mine(e, r["scope"])]
            + [(f["t"], 1 if what == "requests" else f.get("tokens") or 0)
               for f in flying.values() if mine(f, r["scope"])])
        used = sum(w for _, w in events)
        # A prompt larger than the whole window waits for an empty one, then goes:
        # the estimate is rough, and the provider has the last word.
        need = 1 if what == "requests" else min(max(est, 1), r["limit"])
        if used + need <= r["limit"]:
            continue
        if span == "month":
            frees = ledger.next_month(now)
        else:
            frees, gone = now, 0
            for t, w in events:                        # oldest first, until enough have aged out
                gone += w
                frees = t + span
                if used - gone + need <= r["limit"]:
                    break
        whose = connection if r["scope"] == "connection" else alias
        why = (f"{whose} allows {r['limit']:,} {reads} ({r['source']}) and {used:,} "
               f"{'were' if what == 'requests' else 'tokens were'} used"
               f"{' this month' if span == 'month' else ''}")
        if worst is None or frees > worst[0]:
            worst = (frees, why)
    for q in records:
        what = "tokens" if "tokens" in q.get("metric", "") else "requests"
        if not isinstance(q.get("remaining"), int):
            continue
        try:
            seen = _ts(q["observed_at"])
            reset = _ts(q["reset_at"])
        except (KeyError, TypeError, ValueError):
            continue
        used = sum((e.get("attempts") or 0) if what == "requests"
                   else (e.get("tokens_in") or 0) + (e.get("tokens_out") or 0)
                   for e in rows if e["t"] >= seen and e.get("alias") == alias)
        used += sum(1 if what == "requests" else f.get("tokens") or 0
                    for f in flying.values() if f.get("alias") == alias)
        if q["remaining"] - used > 0:
            continue
        limit = f" of {q['limit']:,}" if isinstance(q.get("limit"), int) else ""
        window = "" if q.get("window") in ("unknown", "retry-after") else f" a {q['window']}"
        why = (f"{connection} said {max(q['remaining'], 0):,}{limit} {q['metric']}{window} were left for {alias}"
               f" ({q.get('source')}, {_ago(now - seen)})")
        if worst is None or reset > worst[0]:
            worst = (reset, why)
    return worst


def _ts(iso: str) -> float:
    return dt.datetime.fromisoformat(iso).timestamp()


def _ago(s: float) -> str:
    return "just now" if s < 60 else f"{s / 60:.0f} min ago" if s < 7200 else f"{s / 3600:.0f} h ago"


def admit(alias: str, connection: str, est_tokens: int, left: float, sleep=time.sleep) -> tuple[str | None, str]:
    """Wait for room, or refuse. Returns (ticket, refusal): a ticket to pass
    to `release` once the call's ledger line is written (None when no limit
    applies, so there is nothing to release), or the refusal text."""
    import quota
    rs = rules(alias, connection)
    end = time.time() + left
    while True:
        records = quota.live(connection, alias)
        if not rs and not records:
            return None, ""
        spans = [AXES[r["axis"]][1] for r in rs]
        since = min([ledger.month_start(time.time()) if s == "month" else time.time() - s for s in spans]
                    + [_ts(q["observed_at"]) for q in records if q.get("observed_at")])
        with locked.lock(LOCK):
            now = time.time()
            rows = list(ledger.rows(since))
            flying = _inflight(now)
            block = _blocked(rs, records, alias, connection, est_tokens, now, rows, flying)
            if block is None:
                ticket = uuid.uuid4().hex
                flying[ticket] = {"alias": alias, "connection": connection, "t": now, "tokens": est_tokens,
                                  "until": end + 60, "pid": os.getpid()}
                locked.write_atomic(INFLIGHT, json.dumps(flying, indent=1) + "\n")
                return ticket, ""
        frees, why = block
        wait = max(frees - time.time(), 0.05)
        if time.time() + wait > end - MARGIN_S:
            return None, (f"Error: client failed: rate limit (not sent; paced by the bench): {why};"
                          f" try again in {_wait_text(wait)}")
        sleep(min(wait, POLL_S))


def release(ticket: str | None) -> None:
    """The call's ledger line is written: it no longer counts as in flight."""
    if not ticket:
        return
    with locked.lock(LOCK):
        flying = _inflight(time.time())               # stale entries go too
        flying.pop(ticket, None)
        locked.write_atomic(INFLIGHT, json.dumps(flying, indent=1) + "\n")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) != 2:
        print(__doc__.split("\n\n")[0])
        return
    sys.path.insert(0, str(ROOT / "suite"))
    import route
    alias = sys.argv[1]
    conn = route.connection_of(alias)
    now = time.time()
    rows = list(ledger.rows(ledger.month_start(now) - 86400))
    rs = rules(alias, conn)
    if not rs:
        print(f"{alias} ({conn}): no limit is set or learned, so nothing paces it")
    for r in rs:
        what, span, reads = AXES[r["axis"]]
        since = ledger.month_start(now) if span == "month" else now - span
        used = sum((e.get("attempts") or 0) if what == "requests" else
                   (e.get("tokens_in") or 0) + (e.get("tokens_out") or 0) for e in rows if e["t"] >= since and
                   (e.get("connection") == conn if r["scope"] == "connection" else e.get("alias") == alias))
        print(f"  {r['axis']:<5} {used:>9,} of {r['limit']:<9,} {reads}  ({r['source']})")


if __name__ == "__main__":
    main()
