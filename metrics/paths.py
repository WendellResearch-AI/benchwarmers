"""Where this install keeps its data: one answer for every module.

    python metrics/paths.py      # the data folder in use, and why

RELEASE-PLAN.md Phase 1, code and data apart. Everything that is an install's
own state (config/, metrics/*.json*, perf/history.csv, runs/, suite/tasks/ with
the saved answers) is read and written under DATA, never beside the code, so
the same code can serve the public repository and the owner's instance.

DATA is, in order:
  1. BENCHWARMERS_HOME, when set;
  2. the folder named in `.benchwarmers-home` at the top of the checkout (one
     line; gitignored). The owner's instance points at C:\\benchwarmers-data
     this way, which every process started from this checkout sees (the dashboard,
     the scheduled tasks, the suite), without an environment variable;
  3. the checkout itself, the layout before Phase 1.
     ponytail: (3) keeps a fresh checkout working until `benchwarmers init`
     exists (Phase 4); then the default becomes the OS's per-user data folder.

Inside DATA the layout is the checkout's own (`config/endpoints.json`,
`metrics/offloads.jsonl`, `suite/tasks/<id>/output/`), so moving an install's
data is a copy, not a rewrite.
"""

from __future__ import annotations

import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
POINTER = ROOT / ".benchwarmers-home"


def home() -> tuple[pathlib.Path, str]:
    """(the data folder, how it was chosen)."""
    if env := os.environ.get("BENCHWARMERS_HOME", "").strip():
        return pathlib.Path(env).expanduser(), "BENCHWARMERS_HOME"
    try:
        named = POINTER.read_text(encoding="utf-8").strip()
    except OSError:
        named = ""
    if named:
        return pathlib.Path(named).expanduser(), f"{POINTER.name}"
    return ROOT, "the checkout (no BENCHWARMERS_HOME or .benchwarmers-home)"


DATA, WHY = home()
LAYOUT = ("config", "metrics", "perf", "runs", "suite/tasks")


def make(folder: pathlib.Path = DATA) -> None:
    """The layout, empty. Writers that append (the change log, onboarding's log
    and notes, the perf history) expect their folder to exist, which on a fresh
    install it does not; serve.py and scripts/init.py make it once instead."""
    for sub in LAYOUT:
        (folder / sub).mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    print(f"{DATA}  ({WHY})")
