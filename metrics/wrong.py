"""Confidently wrong: how often a model asserts something that is not true.

    python metrics/wrong.py          # per model, worst first

Being wrong is expected; it is why the bench asks several models. Being wrong
WITH CONFIDENCE is what costs: a reviewer that names a bug that is not there
sends someone to fix working code, and three of them agreeing once asserted a
security hole that did not exist. Counted from evidence already on disk, one
opportunity per answer:

  control     a seeded or real-bug control (the clean file) answered FAULT
  wrong line  a seeded or real-bug task answered FAULT at a line the change did not touch
  false claim a claim task whose claim is false, answered VERDICT: TRUE
  trap        a hand-built task's planted falsehood asserted (its must_not_claim)
  review      a false claim recorded in a verdict (offload.py verdict --false)

A miss (NO FAULT on a bug, FALSE on a true claim) is not counted: saying
nothing is cheap to check, inventing is not. Drafts and non-answers (rate
limits, errors) are left out. Reviews are reported apart, as false claims
per judged offload, because one review can hold several claims.
"""

from __future__ import annotations

import collections
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
TASKS = DATA / "suite" / "tasks"
sys.path.insert(0, str(ROOT / "metrics"))
sys.path.insert(0, str(ROOT / "suite"))
KINDS = ("control", "wrong line", "false claim", "trap")
# What a seeded or real-bug answer did: on a bug task it found the line, named
# another, or missed it; on a control (the clean file) it said clean or raised
# a false alarm. The Bug hunt chart draws these.
HUNT = ("found", "wrong line", "missed", "clean", "false alarm")
FAULT = re.compile(r"^\W*fault:\s*line\s*(\d+)", re.I)


def outcome(spec: dict, text: str) -> str | None:
    """One of HUNT for a seeded or real-bug answer, None for any other task."""
    if spec.get("shape") not in ("seeded", "rebench"):
        return None
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    named = FAULT.match(first)
    if spec["id"].endswith("-control"):
        return "false alarm" if named else "clean"
    label = ((spec.get("must_find") or [{}])[0] or {}).get("label", "")
    keys = {int(n) for n in re.findall(r"\d+", label)}
    if not keys:                                      # no answer line to compare with
        return None
    if not named:
        return "missed"
    return "found" if int(named.group(1)) in keys else "wrong line"


def judge(spec: dict, text: str, invented: list) -> tuple[str | None, bool]:
    """(kind of opportunity, was it confidently wrong) for one answer, or
    (None, False) when the answer could not have been."""
    shape = spec.get("shape")
    if shape in ("seeded", "rebench"):
        o = outcome(spec, text)
        if o is None:
            return None, False
        return ("control", o == "false alarm") if o in ("clean", "false alarm") else ("wrong line", o == "wrong line")
    if shape == "claim":
        claim_false = any("verdict: false" in str(c).lower() for c in spec.get("must_find", []))
        return ("false claim", bool(invented)) if claim_false else (None, False)
    if spec.get("must_not_claim"):
        return "trap", bool(invented)
    return None, False


def table() -> dict[str, dict]:
    import offload
    import run
    from aliases import current
    out: dict[str, dict] = collections.defaultdict(
        lambda: {"answers": 0, "wrong": 0, "by": {k: [0, 0] for k in KINDS}, "reviews": 0, "review_false": 0,
                 "hunt": dict.fromkeys(HUNT, 0)})
    for path in sorted(TASKS.glob("*/task.json")):
        spec = json.loads(path.read_text(encoding="utf-8"))
        # Reading images has its own record (serve.vision_rows): a trap taken
        # there is a picture misread, not code.
        if spec.get("draft") or spec.get("shape") == "vision":
            continue
        for answer in sorted((path.parent / "output").glob("*.md")):
            text = answer.read_text(encoding="utf-8", errors="replace")
            s = run.score(spec, text)
            if s["kind"] != "answered":
                continue
            kind, wrong = judge(spec, text, s["invented"])
            if kind is None:
                continue
            row = out[current(answer.stem)]
            if (o := outcome(spec, text)):
                row["hunt"][o] += 1
            row["answers"] += 1
            row["by"][kind][1] += 1
            if wrong:
                row["wrong"] += 1
                row["by"][kind][0] += 1
    for e in offload.entries():
        if e.get("useful") is not None:
            row = out[e["model"]]
            row["reviews"] += 1
            row["review_false"] += len(e.get("false") or [])
    return dict(out)


def _selfcheck() -> None:
    control = {"id": "seeded-x-control", "shape": "seeded"}
    bug = {"id": "seeded-x-bug", "shape": "seeded", "must_find": [{"regex": "", "label": "fault: line 9 or 10"}]}
    assert judge(control, "FAULT: line 4\nbecause", []) == ("control", True)
    assert judge(control, "NO FAULT", []) == ("control", False)
    assert judge(bug, "FAULT: line 10", []) == ("wrong line", False)
    assert judge(bug, "FAULT: line 100", []) == ("wrong line", True)
    assert judge(bug, "NO FAULT", ["no fault"]) == ("wrong line", False)      # a miss, not invention
    false_claim = {"shape": "claim", "must_find": [{"any_of": ["verdict: false"]}]}
    true_claim = {"shape": "claim", "must_find": [{"any_of": ["verdict: true"]}]}
    assert judge(false_claim, "VERDICT: TRUE", ["verdict: true"]) == ("false claim", True)
    assert judge(true_claim, "VERDICT: FALSE", ["verdict: false"]) == (None, False)
    assert judge({"must_not_claim": ["INSERT"]}, "x", ["INSERT"]) == ("trap", True)
    assert judge({"must_find": []}, "x", []) == (None, False)
    assert judge({"id": "seeded-y-bug", "shape": "seeded"}, "FAULT: line 3", []) == (None, False)
    assert [outcome(bug, t) for t in ("FAULT: line 9", "FAULT: line 1", "NO FAULT")] == ["found", "wrong line", "missed"]
    assert [outcome(control, t) for t in ("NO FAULT", "FAULT: line 2")] == ["clean", "false alarm"]
    assert outcome({"shape": "claim"}, "VERDICT: TRUE") is None


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    _selfcheck()
    rows = table()
    for alias, r in sorted(rows.items(), key=lambda kv: -(kv[1]["wrong"] / kv[1]["answers"] if kv[1]["answers"] else 0)):
        rate = f"{r['wrong']}/{r['answers']}" if r["answers"] else "-"
        by = ", ".join(f"{k} {w}/{n}" for k, (w, n) in r["by"].items() if n)
        print(f"  {alias:<34} {rate:>7}  {by}" + (f"  · reviews: {r['review_false']} false in {r['reviews']}" if r["reviews"] else ""))


if __name__ == "__main__":
    main()
