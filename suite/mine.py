"""Turn judged bench-gateway calls into DRAFT claim-verification tasks.

    python suite/mine.py                  # judged calls with claims not yet mined
    python suite/mine.py p20261016-...    # draft tasks from one call

THE SUITE GROWS FROM REAL WORK
------------------------------
Every bench-gateway call logs its prompt — including the files, exactly as the
models saw them — and a verdict can record verified claims:

    python metrics/offload.py verdict <call id> --useful \\
        --real "<a finding it got right>" --false "<a claim it made that is untrue>"

Each claim becomes one task: the files as fixtures, the claim as a neutral
hypothesis, `VERDICT: TRUE` or `VERDICT: FALSE` on the first line. A --real
claim is true, a --false one is not, so both kinds accumulate. The code is the
version that was reviewed, frozen: a claim task tests verification, not
whether today's code still has the bug.

DRAFTS, NOT TASKS
-----------------
A mined task is written with "draft": true, and suite/run.py skips drafts
unless asked for by --task. Read it before removing the flag: is the claim
stated so the files alone decide it, with no model's name and no line number
that gives the answer away? Is the fixture small enough for free-tier models?
A claim the attached files cannot settle is a bad task, however true.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "metrics"))
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
TASKS = DATA / "suite" / "tasks"
CALLS = DATA / "runs" / "gateway" / "calls.jsonl"
sys.path.insert(0, str(ROOT / "metrics"))
import offload  # noqa: E402

PROMPT = ("Hypothesis about the attached code:\n\n{claim}\n\n"
          "Decide whether the hypothesis is TRUE or FALSE for the code exactly as attached, using"
          " standard Python and standard-library behaviour. Your first line must be exactly"
          " `VERDICT: TRUE` or `VERDICT: FALSE`. Then quote the line or lines that decide it and"
          " explain in at most five sentences. Plain ASCII.")
HEADER = re.compile(r"^===== (?:FILE: )?(.+?)(?: =====)?$", re.M)
BIG = 40_000      # characters of fixtures; past this, free models start failing


def calls() -> dict[str, dict]:
    out = {}
    for line in CALLS.read_text(encoding="utf-8").splitlines() if CALLS.exists() else []:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        out[r["panel"]] = r
    return out


def files_in(prompt: str) -> dict[str, str]:
    """`===== path` sections of a logged prompt -> {path: text}. A section
    whose name is not a file (a git diff, say) is kept under a safe name."""
    marks = list(HEADER.finditer(prompt))
    out = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(prompt)
        name = re.sub(r"[^\w.-]+", "-", m.group(1).strip()).strip("-") or f"part{i}"
        out[name if "." in name else name + ".txt"] = prompt[m.end():end].strip("\n") + "\n"
    return out


def claims_for(panel: str) -> list[tuple[str, bool, str]]:
    """(claim, is_true, seat call id) from every judged seat of the call."""
    seen, out = set(), []
    for e in offload.entries():
        if not str(e.get("call", "")).startswith(panel + "."):
            continue
        for text, truth in [(c, True) for c in e.get("real", [])] + [(c, False) for c in e.get("false", [])]:
            if text.lower() not in seen:
                seen.add(text.lower())
                out.append((text, truth, e["call"]))
    return out


def mined() -> set[str]:
    return {json.loads(p.read_text(encoding="utf-8")).get("mined_from", "")
            for p in TASKS.glob("*/task.json")}


def draft(panel: str) -> list[str]:
    record = calls().get(panel)
    if record is None:
        sys.exit(f"no gateway call {panel!r} in {CALLS.relative_to(ROOT)}")
    files = files_in(record["prompt"])
    if not files:
        sys.exit(f"{panel}: its prompt attached no `===== file` sections, so nothing can be frozen")
    found = claims_for(panel)
    if not found:
        sys.exit(f"{panel}: no verdict records --real or --false claims for it yet")
    size = sum(len(t) for t in files.values())
    written = []
    for n, (claim, truth, call) in enumerate(found, 1):
        task_id = f"mined-{panel.removeprefix('p')}-{n}"
        d = TASKS / task_id
        (d / "fixtures").mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            (d / "fixtures" / name).write_text(text, encoding="utf-8")
        yes, no = ("verdict: true", "verdict: false") if truth else ("verdict: false", "verdict: true")
        spec = {"id": task_id, "draft": True, "shape": "claim", "mined_from": call,
                "title": f"Claim verification ({'true' if truth else 'false'} claim), mined",
                "why": f"{'TRUE' if truth else 'FALSE'}, verified by the caller in the verdict on"
                       f" {call} (model {record.get('model')}). Review before removing draft.",
                # Its files are the snapshot copied above: a repo key no install
                # maps, so build_prompt always reads the fixtures.
                "prompt": PROMPT.format(claim=claim), "repo": "fixtures",
                "attach": [f"snapshot/{name}" for name in files],
                "must_find": [{"any_of": [yes]}], "must_not_claim": [no],
                "score_in": "first_line", "min_answer_chars": 10}
        (d / "task.json").write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n",
                                     encoding="utf-8")
        written.append(task_id)
    print(f"{len(written)} draft task(s) from {panel}: {', '.join(written)}")
    if size > BIG:
        print(f"  note: {size:,} characters of fixtures; trim them to what the claims need"
              " before promoting, or free models will fail on size rather than judgement")
    print("  review each task.json, then remove \"draft\": true to put it in the suite")
    return written


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("panel", nargs="?", help="a gateway call id, e.g. p20261016-165636-afd6")
    args = ap.parse_args()
    if args.panel:
        draft(args.panel)
        return
    done = {c.rsplit(".", 1)[0] for c in mined()}
    rows = [(p, len(claims_for(p))) for p in calls()]
    rows = [(p, n) for p, n in rows if n and p not in done]
    if not rows:
        print("Nothing to mine: no judged gateway call carries --real/--false claims that are"
              " not already tasks.")
    for p, n in rows:
        print(f"  {p}: {n} claim(s)   python suite/mine.py {p}")


if __name__ == "__main__":
    main()
