"""Notice when a model goes away, instead of finding out when it stops working.

Providers retire models quietly. The first symptom is usually a task failing
for what looks like a transient reason, which is the worst possible way to
learn it — the failure is indistinguishable from a rate limit, and the model
stays in the routing table being recommended for work it can no longer do.

This compares three lists and reports the differences:

    what the endpoints SERVE   (each live /models list; see served())
    what the CLI has REGISTERED (config/extra-openai-models.yaml)
    what was served LAST TIME (config/served-snapshot.json)

    python scripts/check_deprecations.py
    python scripts/check_deprecations.py --quiet   # only problems

**A model that vanishes is reported with the date it was last seen**, so
"gone" can be told apart from "the endpoint hiccuped this morning". The
snapshot is what makes that possible, and it is committed on purpose: the
history of what was available when is part of the record this repo keeps.

Exit status is 1 if anything REGISTERED has gone missing, so this can be run
on a schedule and only speak up when it matters.

SINCE 2026-10-15 IT ALSO ACTS: `scripts/auto_retire.py` retires models that
are clearly gone before anything is reported (rules in its docstring), and
`--dry-run` says what it would retire without retiring or calling anything.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sys
import urllib.error
import urllib.request

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "metrics"))
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
YAML = DATA / "config" / "extra-openai-models.yaml"
SNAPSHOT = DATA / "config" / "served-snapshot.json"
sys.path.insert(0, str(REPO / "metrics"))
import endpoints  # noqa: E402
API_BASE = endpoints.base()
TIMEOUT = 30

# How long a model may be absent before it is called gone rather than a blip.
# One day is enough to survive a restart and short enough to be useful.
GRACE_DAYS = 1


def served() -> set[str]:
    """What every endpoint serves, as snapshot keys (`endpoints.snapshot_key`).

    The default endpoint is recorded whole when it is a hub or local (the
    owner's Open WebUI, as it always was): a list the user chose.
    Anything else is recorded only for the models REGISTERED on it --
    OpenRouter alone lists 465, and the snapshot is not a mirror of its
    catalogue (`endpoints.recorded_whole`), even as a stranger's only and
    default endpoint. An endpoint that cannot be asked contributes its
    registered models as if present: its outage is not evidence that any of
    them went. Only the default being unreachable raises, and the caller
    writes nothing.
    """
    live: set[str] = set()
    on = {}
    for r in endpoints.registry():
        on.setdefault(r["endpoint"], set()).add(r["served"])
    for name in endpoints.load():
        try:
            listed = set(endpoints.models(name, TIMEOUT))
        except Exception as exc:                   # noqa: BLE001
            if name == endpoints.default():
                raise
            print(f"cannot reach {name} ({type(exc).__name__}); its models are "
                  f"left as they were")
            listed = on.get(name, set())
        if endpoints.recorded_whole(name):
            live |= listed
        else:
            live |= {endpoints.snapshot_key(s, name) for s in on.get(name, set()) & listed}
    return live


def registered() -> dict[str, str]:
    """snapshot key -> alias, for every registration on every endpoint."""
    return {endpoints.snapshot_key(r["served"], r["endpoint"]): r["alias"]
            for r in endpoints.registry()}


def load_snapshot() -> dict:
    if not SNAPSHOT.exists():
        return {"last_seen": {}, "first_missing": {}}
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quiet", action="store_true",
                    help="print only problems")
    ap.add_argument("--dry-run", action="store_true",
                    help="say what auto-retire would do; retire nothing, call nothing")
    args = ap.parse_args()

    today = dt.date.today().isoformat()
    try:
        live = served()
    except (urllib.error.URLError, OSError, KeyError) as exc:
        # An endpoint that cannot be reached says NOTHING about any model, and
        # writing a snapshot now would record every model as missing and
        # generate a page of false deprecations tomorrow.
        print(f"cannot reach {API_BASE}: {exc}")
        print("No snapshot written — an unreachable endpoint is not evidence "
              "that anything was retired.")
        return 2

    known = registered()
    snap = load_snapshot()
    last_seen, first_missing = snap["last_seen"], snap["first_missing"]

    for name in live:
        last_seen[name] = today
        first_missing.pop(name, None)

    gone_registered, gone_other, returned = [], [], []
    for name in list(last_seen):
        if name in live:
            continue
        first_missing.setdefault(name, today)
        days = (dt.date.fromisoformat(today)
                - dt.date.fromisoformat(first_missing[name])).days
        row = (name, known.get(name), last_seen[name], days)
        (gone_registered if name in known else gone_other).append(row)

    # REGISTERED AND NEVER SEEN AT ALL. The loop above can only report a model
    # that was in the snapshot, so a registration that was wrong from the
    # start — or predates the first snapshot — was invisible here.
    #
    # `gpt-oss-120b` was exactly that: registered, never served under that
    # name, and discovered only when a 16-model suite run recorded it as
    # scoring 0/11 on every task. It was returning "Model not found" in four
    # seconds. A checker whose whole job is to notice absent models should not
    # need a benchmark to find one.
    never_seen = sorted(n for n in known if n not in live
                        and n not in last_seen)
    new = sorted(n for n in live if n not in snap["last_seen"])
    unregistered = sorted(n for n in live if n not in known)

    # A dry run writes nothing: not the snapshot (it starts the countdowns)
    # and not alerts (they put red rows on the dashboard).
    if not args.dry_run:
        SNAPSHOT.write_text(json.dumps(
            {"updated": today, "last_seen": last_seen,
             "first_missing": first_missing}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")

    # ACT, THEN REPORT WHAT IS LEFT. Everything below this describes the
    # registry as auto-retire leaves it, so a model retired this morning does
    # not also appear as a problem for someone to go and fix.
    sys.path.insert(0, str(REPO / "scripts"))
    import auto_retire
    retire_lines, pending_keys = auto_retire.run(
        {alias: name for name, alias in known.items()}, live, first_missing,
        dry_run=args.dry_run)
    if retire_lines:
        print("\nAUTO-RETIRE" + (" (dry run)" if args.dry_run else ""))
        print("\n".join(retire_lines))
    known = registered()
    gone_registered = [g for g in gone_registered if g[0] in known]
    never_seen = [n for n in never_seen if n in known]
    # One row per model on the dashboard: a model counting down to retirement
    # already says why, so its older per-kind alert is not raised as well.
    counting_down = {k.split(":", 2)[2] for k in pending_keys
                     if k.startswith("model:pending:")}

    # LISTED, BUT FAILING STRUCTURALLY. The catalogue is the primary signal
    # and it is a good one — a throttled or slow model still appears in it, so
    # rate limits and timeouts can never be mistaken for a retirement.
    #
    # The gap is the other direction: a model the provider has retired can go
    # on being LISTED until the endpoint's own list catches up. `laguna-m1`
    # did exactly that, answering "No endpoints found" while still appearing.
    #
    # So the second signal is the KIND of failure, recorded by the perf tier.
    # `structural` means the model is not there whatever the list says;
    # `capacity` and `timeout` are explicitly NOT evidence of anything and are
    # never reported here.
    structural, entitlement, paywalled, unclassified = [], [], [], []
    history = DATA / "perf" / "history.csv"
    if history.exists():
        import csv
        latest: dict[str, str] = {}
        with history.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                if row.get("failure"):
                    latest[row["model"]] = row["failure"]
                elif int(row.get("answered") or 0) > 0:
                    latest.pop(row["model"], None)   # it answered: cleared
        alias_of = {name: alias for name, alias in known.items()}
        for name, alias in alias_of.items():
            if name in live and latest.get(alias) == "structural":
                structural.append((name, alias))
            if name in live and latest.get(alias) == "entitlement":
                entitlement.append((name, alias))
            # A `:free` slug that retired behind a paid one. Counted as a
            # RETIREMENT here, not a plan question: this bench does not buy
            # paid tiers, so a model that costs money is as gone as one that
            # was deleted, and it should stop occupying a registration.
            if name in live and latest.get(alias) == "paywalled":
                paywalled.append((name, alias))
            # FAILING FOR A REASON NOTHING HERE RECOGNISES. `other` means
            # `failure_class` found no marker, and until now that was silently
            # dropped -- which is how `minimax-m3-free` went unreported for a
            # day and `glm-5.2-free` for longer. Both were a wording nobody had
            # seen before, and the only way to learn that is to be told.
            # Surfaced, not counted: an unrecognised message is a prompt to go
            # and look, not proof of a retirement, and the alert sweeps itself
            # away as soon as the model answers again.
            if name in live and latest.get(alias) == "other":
                unclassified.append((name, alias))

    # RAISE WHAT IS STILL WRONG, AND RETIRE WHAT IS NOT. Every problem found
    # here is a state somebody has to decide about, and until now it was
    # announced only by a non-zero exit code that Windows recorded and nobody
    # read. `sweep` clears anything no longer found, so a model that comes back
    # does not leave a red row behind it.
    try:
        sys.path.insert(0, str(REPO / "metrics"))
        import alerts as _alerts
        raise_alert = (lambda *a, **k: None) if args.dry_run else _alerts.raise_alert
        raised = set(pending_keys)
        for name, alias in never_seen and [(n, known[n]) for n in never_seen] or []:
            key = f"model:never-served:{name}"
            raise_alert(
                key, "registration",
                f"{name} is registered but the endpoint has never served it",
                f"fix or drop the entry for `{alias}` in "
                f"config/extra-openai-models.yaml")
            raised.add(key)
        data = endpoints.load()
        for name, alias, seen, days in gone_registered:
            key = f"model:gone:{name}"
            served_as, _, ep = name.rpartition("@")       # bare: on the default (a model may be named like an endpoint)
            hub = endpoints.hub(ep if served_as and ep in data else endpoints.default(), data)
            raise_alert(
                key, "retired",
                f"{name} is no longer served (last seen {seen}, {days}d ago)",
                f"remove it from {'Open WebUI and from ' if hub == 'openwebui' else ''}the yaml")
            raised.add(key)
        for name, alias in structural:
            key = f"model:structural:{name}"
            raise_alert(
                key, "retired",
                f"{name} is listed but every call fails structurally",
                "verify, then remove — the catalogue has not caught up")
            raised.add(key)
        for name, alias in entitlement:
            key = f"model:entitlement:{name}"
            raise_alert(
                key, "plan",
                f"{name} is served but this account may not call it",
                "keep registered and watch — a pricing change would clear this")
            raised.add(key)
        for name, alias in unclassified:
            key = f"model:unclassified:{name}"
            raise_alert(
                key, "registration",
                f"{name} is failing for a reason this check does not recognise",
                f"call it once and read the message — `llm -m {alias} hi` — "
                f"then add a marker to the right class in suite/run.py")
            raised.add(key)
        for name, alias in paywalled:
            key = f"model:paywalled:{name}"
            raise_alert(
                key, "retired",
                f"{name} has retired its free tier behind a paid slug",
                "re-point the yaml at the paid slug and pay for it, or drop "
                "the entry — waiting will not bring the free one back")
            raised.add(key)
        raised = {k for k in raised if k.startswith("model:pending:")
                  or k.split(":", 2)[2] not in counting_down}
        if not args.dry_run:
            _alerts.sweep("model:", raised)
    except Exception:                              # noqa: BLE001
        pass                                       # alerting is not the job

    problems = 0
    if structural:
        print("\nLISTED BUT NOT ACTUALLY THERE")
        print("  Still in the endpoint's catalogue, but every recent call"
              " failed structurally\n  (no such model, no provider, or not"
              " permitted) rather than being throttled.\n  A retirement the"
              " list has not caught up with — verify before removing.")
        for name, alias in sorted(structural):
            print(f"  - {name}  [llm id: {alias}]")
        # COUNTED, since 2026-09-09. The exit code is how the scheduled task
        # surfaces a problem at all -- nobody reads the task's stdout -- and a
        # model that is listed while every call fails structurally is a
        # retirement the catalogue has not caught up with. Leaving it out of
        # the count meant the one signal that reaches anyone stayed green.
        problems += len(structural)

    if paywalled:
        print("\nFREE TIER RETIRED BEHIND A PAID SLUG")
        print("  Still in the catalogue and still reachable — but only for"
              " money, and this bench\n  does not buy paid tiers for this"
              " work. That makes it a RETIREMENT for our purposes:\n"
              "  waiting will not bring the free endpoint back, so re-point"
              " the registration at\n  the paid slug and pay for it, or drop"
              " the entry.")
        for name, alias in sorted(paywalled):
            print(f"  - {name}  [llm id: {alias}]")
        problems += len(paywalled)

    if unclassified and not args.quiet:
        print("\nFAILING FOR A REASON THIS CHECK DOES NOT RECOGNISE")
        print("  Every recent call failed, but the message matched no marker"
              " in suite/run.py, so\n  the failure could not be filed as"
              " retired, paywalled, throttled or a plan\n  problem. NOT"
              " counted as a retirement — it is a prompt to go and read the"
              " message.\n  Call one by hand, then add a marker so the next"
              " model with that wording is caught.")
        for name, alias in sorted(unclassified):
            print(f"  - {name}  [llm id: {alias}]")

    if entitlement and not args.quiet:
        print("\nSERVED, BUT THIS ACCOUNT MAY NOT CALL THEM")
        print("  Present and working — the plan does not include them. Not a"
              " retirement,\n  and not something to remove: keep them"
              " registered to notice if that changes.")
        for name, alias in sorted(entitlement):
            print(f"  - {name}  [llm id: {alias}]")

    if never_seen:
        print("\nREGISTERED BUT NEVER SERVED")
        print("  The endpoint has no model by this name — the registration is"
              " wrong, not the model.\n  Fix the name in"
              " config/extra-openai-models.yaml or drop the entry.")
        for name in never_seen:
            print(f"  - {name}  [llm id: {known[name]}]")
        problems += len(never_seen)

    if gone_registered:
        print("\nREGISTERED MODELS NO LONGER SERVED")
        print("  These are in the routing table and the endpoint no longer "
              "offers them.\n  Auto-retire removes them after 3 days missing;"
              " the dashboard can retire them sooner.")
        for name, alias, seen, days in sorted(gone_registered):
            hedge = "" if days >= GRACE_DAYS else "   (first miss today — "\
                "could still be a blip)"
            print(f"  - {name}  [llm id: {alias}]"
                  f"\n      last seen {seen}, missing {days}d{hedge}")
        problems += len(gone_registered)

    if gone_other and not args.quiet:
        print("\nUnregistered models that disappeared (informational)")
        for name, _alias, seen, days in sorted(gone_other):
            print(f"  - {name}  last seen {seen}, missing {days}d")

    if new and not args.quiet:
        print("\nNEW since the last check")
        for name in new:
            print(f"  + {name}")

    if not args.quiet:
        print(f"\n{len(live)} served, {len(known)} registered, "
              f"{len(unregistered)} served-but-unregistered")
    if not problems and not args.quiet:
        print("No registered model has gone missing.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
