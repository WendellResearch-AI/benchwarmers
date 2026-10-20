"""Run suite tasks across the roster slowly, one call at a time, in the background.

    python suite/pace.py                         # every seeded-hard task, every model, 120 s apart
    python suite/pace.py --prefix claim- --gap 300
    python suite/pace.py --dry-run               # list what would run, call nothing

Started detached it works through the queue on its own and stops when done:

    Start-Process .venv\\Scripts\\python.exe -ArgumentList suite\\pace.py -WindowStyle Hidden   # Windows
    nohup .venv/bin/python suite/pace.py > /dev/null 2>&1 &                                    # macOS, Linux

FREE TIERS LIMIT RATE, NOT TOTAL
--------------------------------
A full roster pass of ten seeded pairs is ~300 calls of 8-14K characters. Sent
back to back it trips every provider's per-minute limit and the run fills
with "rate limit" rows that say nothing about the models. Sent one at a time,
`--gap` seconds apart and ROUND-ROBIN — task by task across every model, so
one model is asked once per lap rather than twenty times in a row — each model
sees a call roughly every (models x gap) seconds, far under any limit.

A call that comes back rate-limited is deleted (it is not an answer) and its
model is rested for `--rest` seconds before being asked again later; the rest
of the queue carries on. Pairs that already have a saved answer are skipped,
so the run can be stopped and started again at any time and picks up where it
was. It logs to runs/pace-<date>.log and calls nothing that suite/run.py
would not.

Models on a retiring alert, ad hoc models and decision models are left out:
the first are failing their probes, the second cost money, the third cannot
answer a prompt.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "metrics"))
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
TASKS = DATA / "suite" / "tasks"
sys.path.insert(0, str(ROOT / "suite"))
sys.path.insert(0, str(ROOT / "metrics"))
import run  # noqa: E402

PY = run.bench_python()


def models() -> list[str]:
    try:
        import alerts
        retiring = {a.get("alias") for a in alerts.active().values() if a.get("kind") == "retiring"}
    except Exception:                                  # noqa: BLE001
        retiring = set()
    return [m for m in run.available_models() if m not in retiring and m not in run.ad_hoc_models()]


def answered(task: str, model: str) -> bool:
    """A saved file that is an answer. One that recorded a block (a rate
    limit, an error, a timeout) is not, so the pair is asked again."""
    out = TASKS / task / "output" / f"{model}.md"
    if not out.exists():
        return False
    spec = json.loads((TASKS / task / "task.json").read_text(encoding="utf-8"))
    return run.score(spec, out.read_text(encoding="utf-8", errors="replace"))["kind"] == "answered"


def queue(prefix: str, roster: list[str]) -> list[tuple[str, str]]:
    """(task, model) pairs without an answer yet, task by task across models."""
    tasks = sorted(p.parent.name for p in TASKS.glob(f"{prefix}*/task.json")
                   if not json.loads(p.read_text(encoding="utf-8")).get("draft"))
    return [(t, m) for t in tasks for m in roster if not answered(t, m)]


def rate_limited(task: str, model: str) -> bool:
    out = TASKS / task / "output" / f"{model}.md"
    if not out.exists():
        return False
    blocked = run.score(json.loads((TASKS / task / "task.json").read_text(encoding="utf-8")),
                        out.read_text(encoding="utf-8", errors="replace")).get("blocked") or ""
    return "rate limit" in blocked or "capacity" in blocked


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prefix", default="seeded-hard-", help="task directories starting with this")
    ap.add_argument("--gap", type=int, default=120, help="seconds between calls")
    ap.add_argument("--rest", type=int, default=1800, help="seconds a rate-limited model is rested")
    ap.add_argument("--timeout", type=int, default=420)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    roster = models()
    todo = queue(args.prefix, roster)
    log_path = DATA / "runs" / f"pace-{dt.date.today().isoformat()}.log"

    def log(msg: str) -> None:
        line = f"{dt.datetime.now():%H:%M:%S} {msg}"
        print(line, flush=True)
        if not args.dry_run:
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    eta = len(todo) * (args.gap + 60) / 3600
    log(f"{len(todo)} calls queued across {len(roster)} models, {args.gap}s apart"
        f" (about {eta:.1f} h): {', '.join(roster)}")
    if args.dry_run:
        for t, m in todo:
            print(f"  {t}  {m}")
        return
    rested: dict[str, float] = {}
    while todo:
        now = time.monotonic()
        pick = next(((t, m) for t, m in todo if rested.get(m, 0) <= now), None)
        if pick is None:
            # Only models with work left: a rest that already ended would make
            # this negative, and time.sleep raises on a negative wait.
            time.sleep(max(1, min(rested[m] for _, m in todo) - now + 1))
            continue
        todo.remove(pick)
        task, model = pick
        subprocess.run([str(PY), str(ROOT / "suite" / "run.py"), "--task", task, "--model", model,
                        "--timeout", str(args.timeout)], cwd=str(ROOT), capture_output=True,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        if rate_limited(task, model):
            (TASKS / task / "output" / f"{model}.md").unlink()
            rested[model] = time.monotonic() + args.rest
            todo.append(pick)
            log(f"rate-limited  {model} on {task}; resting it {args.rest}s, retried later")
        else:
            log(f"done          {model} on {task}  ({len(todo)} left)")
        if todo:
            time.sleep(args.gap)
    log("queue empty; finished")


if __name__ == "__main__":
    main()
