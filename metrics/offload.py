"""A record of REAL work handed to a model — not benchmark calls.

    python metrics/offload.py list
    python metrics/offload.py add kimi-k3 "audit the sync seam" --useful \\
        --note "found fetch_assignments was never called"
    python metrics/offload.py verdict p20261016-171200-a1b2.primary --useful --note "..."

WHY THIS IS WRITTEN DOWN RATHER THAN DERIVED
--------------------------------------------
The first attempt counted offloads as `logged calls − benchmark calls`, and it
was wrong in both directions. The benchmark ledger only started when it was
built, so every earlier suite run counted as work: `kimi-k3` showed 26
"offloads" and had never been given a real task. Meanwhile a model with a
164-call floor run looked like the most-used thing here.

Subtracting one imperfect number from another gives a third number nobody can
check. An offload is a deliberate act, so it is recorded deliberately.

**And a count alone answers the wrong question.** "Which models am I using
most" matters far less than what for, and whether it helped. Each entry keeps
the task and the verdict, so the routing table can be argued from evidence
rather than from a tally.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
LOG = DATA / "metrics" / "offloads.jsonl"


def entries() -> list[dict]:
    """Every offload, oldest first. An entry with a `call` id (written by the
    bench gateway) is superseded by a later one with the same id: the gateway
    records each seat unjudged as it answers, and the verdict comes after."""
    if not LOG.exists():
        return []
    out, by_call = [], {}
    from aliases import current
    for line in LOG.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            e["model"] = current(e.get("model", ""))   # a renamed model keeps its record
            if e.get("call") in by_call:
                out[by_call[e["call"]]] = e
                continue
            if e.get("call"):
                by_call[e["call"]] = len(out)
            out.append(e)
    return out


def add(model: str, task: str, useful: bool | None, note: str, call: str | None = None,
        real: list[str] | None = None, false: list[str] | None = None) -> dict:
    entry = {"date": dt.date.today().isoformat(), "model": model,
             "task": task, "useful": useful, "note": note}
    if call:
        entry["call"] = call
    # Verified claims, one sentence each: what this answer got right, and what
    # it asserted that is not true. suite/mine.py turns them into tasks.
    if real:
        entry["real"] = real
    if false:
        entry["false"] = false
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    return entry


def verdict(call: str, useful: bool | None, note: str,
            real: list[str] | None = None, false: list[str] | None = None,
            replace: bool = False) -> dict:
    """Judge one seat of a bench-gateway panel by its call id. A blank note or
    claim list keeps the earlier one, unless `replace` (the dashboard's form,
    which shows them for editing, so blank means cleared)."""
    found = next((e for e in reversed(entries()) if e.get("call") == call), None)
    if found is None:
        raise KeyError(f"no offload with call id {call!r}; the gateway prints them under each answer")
    if replace:
        real, false = real or None, false or None
    else:
        note, real, false = note or found.get("note", ""), real or found.get("real"), false or found.get("false")
    if (useful, note, real, false) == (found.get("useful"), found.get("note", ""), found.get("real"), found.get("false")):
        return found                                   # the same verdict again writes nothing
    return add(found["model"], found["task"], useful, note, call, real, false)


def summary() -> dict[str, dict]:
    """Per model: how often, how often it helped, and the last thing it did."""
    agg: dict[str, dict] = collections.defaultdict(
        lambda: {"offloads": 0, "useful": 0, "judged": 0,
                 "last_task": "", "last_date": ""})
    for e in entries():
        a = agg[e["model"]]
        a["offloads"] += 1
        if e.get("useful") is not None:
            a["judged"] += 1
            a["useful"] += 1 if e["useful"] else 0
        if e["date"] >= a["last_date"]:
            a["last_date"], a["last_task"] = e["date"], e["task"]
    return dict(agg)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    a = sub.add_parser("add")
    a.add_argument("model")
    a.add_argument("task", help="what it was asked to do, in a few words")
    a.add_argument("--useful", dest="useful", action="store_true", default=None)
    a.add_argument("--not-useful", dest="useful", action="store_false")
    a.add_argument("--note", default="")
    # Structured, so metrics/wrong.py can count who asserts false things.
    a.add_argument("--real", action="append", metavar="CLAIM",
                   help="a finding this answer got right, verified; repeatable")
    a.add_argument("--false", action="append", dest="false_claims", metavar="CLAIM",
                   help="a claim this answer made that is not true, verified; repeatable")
    v = sub.add_parser("verdict", help="judge one seat of a bench-gateway panel by call id")
    v.add_argument("call")
    judged = v.add_mutually_exclusive_group(required=True)   # a verdict without one erases nothing
    judged.add_argument("--useful", dest="useful", action="store_true", default=None)
    judged.add_argument("--not-useful", dest="useful", action="store_false")
    v.add_argument("--note", default="")
    v.add_argument("--real", action="append", metavar="CLAIM",
                   help="a finding this answer got right, verified; repeatable")
    v.add_argument("--false", action="append", dest="false_claims", metavar="CLAIM",
                   help="a claim this answer made that is not true, verified; repeatable")
    args = ap.parse_args()

    if args.cmd == "add":
        e = add(args.model, args.task, args.useful, args.note, real=args.real, false=args.false_claims)
        print(f"recorded: {e['model']} — {e['task']}")
        return
    if args.cmd == "verdict":
        try:
            e = verdict(args.call, args.useful, args.note, args.real, args.false_claims)
        except KeyError as exc:
            raise SystemExit(exc.args[0])
        print(f"recorded: {e['model']} — {e['task']} — useful={e['useful']}")
        return

    data = summary()
    if not data:
        print("No offloads recorded yet.")
        print("This is empty because it counts REAL WORK only — a benchmark "
              "run is not an offload.")
        return
    print(f"{'model':<26} {'used':>5} {'helped':>7}  last")
    for model, a in sorted(data.items(), key=lambda kv: -kv[1]["offloads"]):
        helped = (f"{a['useful']}/{a['judged']}" if a["judged"] else "—")
        print(f"{model:<26} {a['offloads']:>5} {helped:>7}  {a['last_task'][:44]}")


if __name__ == "__main__":
    main()
