"""Retire a model when the evidence says it is gone, without waiting for a person.

Called by `check_deprecations.py` every morning, after the 5am availability
probe has written its rows. Until 2026-10-15 the check only raised alerts, and
six "no longer served" alerts sat for eight days with nobody acting on them --
detection worked, removal was the bottleneck.

THE RULES (decided 2026-10-15)
----------------------------
- IMMEDIATE, the same day: the latest probe failed `structural` (no such
  model), `paywalled` (free tier gone) or `entitlement` (payment required, no
  credit, not authorised). Confirmed first by ONE fresh call that must fail
  the same way, so a single blip cannot retire a working model.
- AFTER 3 DAYS: missing from its endpoint's /models (the daily served
  check's snapshot: Open WebUI's whole list, or the registered models on any
  other endpoint), or failing for a reason nothing recognises (`other`) on 3
  separate days with no answer since.
- BACKSTOP, AFTER 7 DAYS: no answer of any kind for 7 days, whatever the
  reason -- a rate limit that never lifts is a quota or a dead model.
  `mistral-medium-3-5` answered 429 to 11 calls out of 11.
- NEVER on a rate limit or timeout alone inside those 7 days.

"No answer" means from ANY source: the perf probe, a useful offload, or a
suite output that was a real answer. A model that answers in the suite resets
the clock even if the 5am probe was throttled.

THE CONNECTION GUARD
--------------------
An expired key, an empty balance or a renamed Open WebUI prefix makes EVERY
model on that connection fail at once, and each failure looks like a
retirement. So when every model on a connection (two or more, with evidence)
is down, nothing on it is retired: one alert names the connection and offers
"retire all" for when it really is gone. A one-model connection cannot be told
apart from its model, which is what Restore is for.

    python scripts/auto_retire.py            # self-check of the rules
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "metrics"))
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
# Once, at import: evidence() runs on every dashboard render, and used to
# insert on each call.
for _d in (ROOT, ROOT / "metrics", ROOT / "suite"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))
import locked  # noqa: E402
IMMEDIATE = {"structural", "paywalled", "entitlement"}
MISSING_DAYS, OTHER_DAYS, BACKSTOP_DAYS = 3, 3, 7
PROBE = "Reply with exactly one word: ready"
# see account_quota(); the second is the weekly cap a ChatGPT plan sets per app
ACCOUNT_QUOTAS = ("free-models-per-day", "usage_limit_exceeded")


def account_quota(detail: str) -> bool:
    """A failure that says nothing about the model: OpenRouter's 50 free calls
    a day are shared by every free model on the account, so the model that
    meets the 429 is whichever was called after the others spent them (the
    suite's own runs, 2026-10-19). Not counted toward retirement."""
    return any(marker in detail for marker in ACCOUNT_QUOTAS)


def plan(today: dt.date, models: dict[str, str], live: set[str],
         first_missing: dict[str, str], rows: list[dict],
         answers: dict[str, dt.date], restored: dict[str, dt.date],
         tags: dict[str, str], owui: set[str] = frozenset()) -> dict:
    """Decide, from evidence alone. No calls, no writes.

    models   alias -> served name, every registered model
    rows     perf rows: {alias, date, answered, failure, detail}
    answers  alias -> last date it answered outside the perf tier
    tags     served name -> its connection (route.connection_of)
    owui     aliases served through an Open WebUI hub, the only place a
             connection prefix can rename a model
    Returns {"retire": [...], "confirm": [...], "pending": [...],
             "held": {connection: [...]}}; each item is (alias, why).
    """
    out = {"retire": [], "confirm": [], "pending": [], "held": {}}
    down: dict[str, bool] = {}
    candidates: list[tuple[str, str, str]] = []      # (alias, bucket, why)
    for alias, served in models.items():
        mine = [r for r in rows if r["alias"] == alias]
        last_ok = max([r["date"] for r in mine if r["answered"]]
                      + ([answers[alias]] if alias in answers else []),
                      default=None)
        floor = max(d for d in (last_ok, restored.get(alias), dt.date.min) if d)
        # A refused key (`signin`) is the endpoint's, fixed by signing in again.
        fails = sorted((r for r in mine if not r["answered"] and r["failure"] and r["failure"] != "signin"
                        and r["date"] > floor and not account_quota(r["detail"])),
                       key=lambda r: r["date"])
        missing_since = None
        if served not in live and served in first_missing:
            missing_since = max(dt.date.fromisoformat(first_missing[served]),
                                restored.get(alias, dt.date.min))
        if mine or missing_since:
            down[alias] = bool(missing_since) or not mine[-1]["answered"]

        latest = fails[-1] if fails else None
        why = (f"{latest['failure']}: {latest['detail']}".rstrip(": ")
               if latest else "")
        other_days = {r["date"] for r in fails if r["failure"] == "other"}
        span = (today - fails[0]["date"]).days if fails else 0
        fail_days = {r["date"] for r in fails}

        # A CONNECTION PREFIX ADDED IN OPEN WEBUI renames every model on it
        # (`codestral-latest` -> `llm7.codestral-latest`), and the old names
        # vanish. That is an address change, not a retirement, and
        # `reprefix.py` repoints the registry with all history intact.
        renamed = next((s for s in live if s.endswith("." + served)), None) \
            if missing_since and alias in owui else None
        if renamed:
            out["pending"].append((alias, f"1|served as {renamed} now — run "
                                          f"scripts/reprefix.py, not a retirement"))
            continue
        where = "Open WebUI" if alias in owui else "its endpoint"
        if missing_since and (today - missing_since).days >= MISSING_DAYS:
            # Not guarded: three days of absence already outlasts a blip, and
            # providers DO retire in batches (three gpt-* on 10-08, three
            # mistral-*-latest on 10-07), which a guard would hold for ever.
            out["retire"].append((alias,
                                  f"not served by {where} since {missing_since}"))
        elif latest and latest["failure"] in IMMEDIATE \
                and (today - latest["date"]).days <= 1:
            candidates.append((alias, "confirm", why))
        elif latest and latest["failure"] == "other" \
                and len(other_days) >= OTHER_DAYS:
            candidates.append((alias, "retire",
                               f"unrecognised failure on {len(other_days)} days — {why}"))
        elif fails and span >= BACKSTOP_DAYS and len(fail_days) >= 3:
            candidates.append((alias, "retire",
                               f"no answer for {span} days — {why}"))
        else:
            left = []
            if missing_since:
                left.append((MISSING_DAYS - (today - missing_since).days,
                             f"not served by {where}"))
            if latest and latest["failure"] == "other":
                left.append((OTHER_DAYS - len(other_days), why))
            if fails:
                left.append((BACKSTOP_DAYS - span, why))
            if left:
                days, reason = min(left)
                out["pending"].append((alias, f"{max(days, 1)}|{reason}"))

    conn_of = {a: (tags.get(s) or "?").split("/")[0] for a, s in models.items()}
    for conn in set(conn_of.values()):
        evidenced = [a for a in models if conn_of[a] == conn and a in down]
        mine = [c for c in candidates if conn_of[c[0]] == conn]
        # "?" is not a connection, it is "no tag": a model drops out of
        # provider-tags.json when Open WebUI stops listing it, so grouping the
        # untagged together would guard strangers against each other.
        if mine and conn != "?" and len(evidenced) >= 2 \
                and all(down[a] for a in evidenced):
            out["held"][conn] = [(a, why) for a, _b, why in mine]
        else:
            for alias, bucket, why in mine:
                out[bucket].append((alias, why))
    return out


# ---- evidence from the repository ------------------------------------------

def _current(alias: str, history: dict[str, str]) -> str:
    seen = set()
    while alias in history and alias not in seen:
        seen.add(alias)
        alias = history[alias]
    return alias


def evidence() -> tuple[list[dict], dict[str, dt.date], dict[str, dt.date]]:
    """(perf rows, last non-perf answer per alias, restore date per alias)."""
    history = locked.read_json(DATA / "config" / "alias-history.json")     # {} until a rename
    rows = []
    path = DATA / "perf" / "history.csv"
    if path.exists():
        with path.open(encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                rows.append({"alias": _current(r["model"], history),
                             "date": dt.date.fromisoformat(r["timestamp"][:10]),
                             "answered": int(r.get("answered") or 0) > 0,
                             "failure": r.get("failure") or "",
                             "detail": r.get("detail") or ""})
    rows.sort(key=lambda r: r["date"])

    answers: dict[str, dt.date] = {}

    def saw(alias: str, day: dt.date) -> None:
        alias = _current(alias, history)
        answers[alias] = max(answers.get(alias, day), day)

    offloads = DATA / "metrics" / "offloads.jsonl"
    for line in (offloads.read_text(encoding="utf-8").splitlines()
                 if offloads.exists() else []):
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if o.get("useful"):
            saw(o["model"], dt.date.fromisoformat(o["date"]))

    from run import outcome
    for out in DATA.glob("suite/tasks/*/output/*.md"):    # the saved answers are the install's data
        if outcome(out.read_text(encoding="utf-8", errors="replace")) is None:
            saw(out.stem, dt.date.fromtimestamp(out.stat().st_mtime))

    restored: dict[str, dt.date] = {}
    changes = DATA / "config" / "changes.jsonl"
    for line in (changes.read_text(encoding="utf-8").splitlines()
                 if changes.exists() else []):
        try:
            c = json.loads(line)
        except ValueError:
            continue
        if c.get("field") == "restored":
            restored[c["alias"]] = dt.date.fromisoformat(c["at"][:10])
    return rows, answers, restored


def connections(models: dict[str, str]) -> tuple[dict[str, str], set[str]]:
    """(snapshot key -> connection, aliases on an Open WebUI hub), for plan.

    The connection is route.connection_of's, the one answer every grouping
    uses: the Open WebUI tag, else the endpoint the model is registered on.
    Until 2026-10-21 this read provider-tags.json directly and named only
    `served@endpoint` models by their endpoint, so on an install without Open
    WebUI every model on the default was "?" and the guard never held one."""
    import endpoints
    import route
    data = endpoints.load()
    owui = {r["alias"] for r in endpoints.registry()
            if r["endpoint"] in data and endpoints.hub(r["endpoint"], data) == "openwebui"}
    return {key: route.connection_of(alias) for alias, key in models.items()}, owui


def confirm(alias: str) -> tuple[bool, str]:
    """One fresh call. Retire only if it fails the same way again."""
    from run import ask_once, failure_class, llm_binary
    body, _ = ask_once(llm_binary(), alias, PROBE, 90)
    cls = failure_class(body, min_length=1)
    return cls in IMMEDIATE, f"{cls}: {' '.join(body.split())[:200]}"


def run(models: dict[str, str], live: set[str], first_missing: dict[str, str],
        dry_run: bool = False) -> tuple[list[str], set[str]]:
    """Apply the plan. Returns (report lines, alert keys raised)."""
    rows, answers, restored = evidence()
    tags, owui = connections(models)
    p = plan(dt.date.today(), models, live, first_missing, rows, answers,
             restored, tags, owui)

    retire = list(p["retire"])
    for alias, why in p["confirm"]:
        if dry_run:
            retire.append((alias, f"{why} (would confirm with one call)"))
            continue
        gone, fresh = confirm(alias)
        if gone:
            retire.append((alias, f"{why} — confirmed {dt.date.today()}: {fresh}"))
        else:
            p["pending"].append((alias, f"1|failed the 5am probe, answered the "
                                        f"confirmation call ({fresh})"))

    lines, keys = [], set()
    import alerts
    for alias, why in retire:
        if dry_run:
            lines.append(f"  would retire {alias}: {why}")
            continue
        import serve
        result = serve.remove_model(alias, f"auto: {why}"[:400], who="auto")
        lines.append(f"  retired {alias}: {why}" if result["ok"]
                     else f"  could not retire {alias}: {'; '.join(result['errors'])}")
    for alias, item in p["pending"]:
        days, why = item.split("|", 1)
        lines.append(f"  {alias} retires in {days}d unless it answers — {why}")
        if not dry_run:
            key = f"model:pending:{models[alias]}"
            alerts.raise_alert(key, "retiring",
                               f"{alias} retires in {days} day{'s' * (days != '1')}"
                               f" unless it answers", why[:300], alias=alias)
            keys.add(key)
    for conn, items in p["held"].items():
        names = ", ".join(a for a, _ in items)
        lines.append(f"  HELD on {conn}: every model there is down, so this"
                     f" looks like the connection — not retiring {names}")
        if not dry_run:
            key = f"connection:held:{conn}"
            alerts.raise_alert(key, "connection",
                               f"every model on {conn} is failing — likely the"
                               f" connection (key, credit, prefix), so nothing"
                               f" was retired", items[0][1][:300],
                               aliases=[a for a, _ in items])
            keys.add(key)
    if not dry_run:
        alerts.sweep("connection:", keys)
    return lines, keys


def _selfcheck() -> None:
    d = dt.date(2026, 10, 15)
    day = lambda n: d - dt.timedelta(days=n)            # noqa: E731
    row = lambda a, n, f="", ok=False: {"alias": a, "date": day(n),  # noqa: E731
                                        "answered": ok, "failure": f, "detail": "x"}
    models = {"dead": "v/dead", "paid": "v/paid", "slow": "v/slow",
              "quota": "v/quota", "gone": "v/gone", "fine": "v/fine",
              "k1": "k/1", "k2": "k/2", "back": "v/back"}
    tags = {"v/dead": "v", "v/paid": "v", "v/slow": "v", "v/quota": "v",
            "v/gone": "v", "v/fine": "v", "k/1": "k", "k/2": "k", "v/back": "v"}
    live = set(models.values()) - {"v/gone"}
    rows = ([row("dead", n, "other") for n in (2, 1, 0)]
            + [row("paid", 0, "paywalled"), row("slow", 2, "timeout"),
               row("slow", 0, "capacity"), row("fine", 0, ok=True)]
            + [row("quota", n, "capacity") for n in (8, 5, 0)]
            + [row("k1", 0, "entitlement"), row("k2", 0, "entitlement")]
            + [row("back", n, "other") for n in (3, 2, 1)])
    p = plan(d, models, live, {"v/gone": str(day(3))}, rows, {},
             {"back": day(2)}, tags)
    # untagged models missing together are retired, not held as one connection
    p2 = plan(d, {"a": "x/a", "b": "x/b"}, set(),
              {"x/a": str(day(4)), "x/b": str(day(4))}, [], {}, {}, {})
    assert {a for a, _ in p2["retire"]} == {"a", "b"} and not p2["held"], p2
    # a prefix added in Open WebUI is a rename, never a retirement
    p3 = plan(d, {"c": "codestral"}, {"llm7.codestral"},
              {"codestral": str(day(9))}, [], {}, {}, {}, {"c"})
    assert not p3["retire"] and "reprefix" in dict(p3["pending"])["c"], p3
    # ...and only Open WebUI prefixes: elsewhere a model gone for 9 days is gone
    p3 = plan(d, {"c": "codestral"}, {"llm7.codestral"},
              {"codestral": str(day(9))}, [], {}, {}, {})
    assert dict(p3["retire"])["c"].startswith("not served by its endpoint"), p3
    retire = {a for a, _ in p["retire"]}
    assert retire == {"dead", "quota", "gone"}, retire
    assert {a for a, _ in p["confirm"]} == {"paid"}
    assert set(p["held"]) == {"k"}, p["held"]        # whole connection down
    pending = dict(p["pending"])
    assert pending["slow"].startswith("5|"), pending  # 7-day backstop counting
    assert "back" in pending                          # restored: old rows ignored
    # one answer in the suite resets the clock
    p = plan(d, models, live, {}, rows, {"quota": day(1)}, {}, tags)
    assert "quota" not in {a for a, _ in p["retire"]}
    # OpenRouter's account-wide free quota is not the model failing
    shared = [dict(row("quota", n, "capacity"),
                   detail="429 Rate limit exceeded: free-models-per-day") for n in (8, 5, 0)]
    p = plan(d, {"quota": "v/quota"}, {"v/quota"}, {}, shared, {}, {}, {"v/quota": "v"})
    assert not p["retire"] and not p["pending"], p
    # A refused key is the endpoint's, never the model's (suite/run.py SIGNIN)
    from run import failure_class
    assert failure_class("Error: Error code: 401 - {'error': 'invalid key'}") == "signin"
    assert failure_class("Error: Error code: 403 - payment required") == "entitlement"
    assert failure_class("Error: Error code: subscription_sharing_usage_unavailable - {}") == "signin"
    assert account_quota("Error: Error code: subscription_sharing_usage_limit_exceeded - {}")
    refused = [row("pk", n, "signin") for n in (9, 5, 0)]
    p = plan(d, {"pk": "v/pk"}, {"v/pk"}, {}, refused, {}, {}, {"v/pk": "v"})
    assert not p["retire"] and not p["confirm"] and not p["pending"], p
    print("auto_retire: rules hold")


if __name__ == "__main__":
    _selfcheck()
