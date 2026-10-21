"""Every self-check and test file, in one command.

    python scripts/check.py           # all of them
    python scripts/check.py --quick   # without the llm-vs-built-in comparison (about two minutes)

RELEASE-PLAN.md Phase 6: one command a change is checked with, and what CI
will run. Each check is its own process, so one that crashes cannot take the
rest with it; the output of any that fail is shown. None of them call a model
or write the owner's data: they use temp copies, stubs and a fake server.
Some still READ the live config (the collections, the registry), which is
fine here and gets fixtures before CI runs them on a clean machine.

The export gate's full scan is not here: it fails by design until the public
tree is clean (`python scripts/export_check.py`). Its pattern self-check is.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent

# (script, arguments, slow)
CHECKS = [
    ("metrics/locked.py", [], False),
    ("metrics/guard.py", [], False),
    ("metrics/endpoints.py", ["--selftest"], False),
    ("metrics/connect.py", ["--selftest"], False),
    ("metrics/client.py", ["--selftest"], False),
    ("metrics/learn.py", ["--selftest"], False),
    ("metrics/discover.py", ["--selftest"], False),
    ("metrics/test_decide.py", [], False),
    ("metrics/wrong.py", [], False),
    ("metrics/scheduler.py", ["--selftest"], False),
    ("scripts/auto_retire.py", [], False),
    ("scripts/onboard.py", ["--selftest"], False),
    ("scripts/export_check.py", ["--selftest"], False),
    ("scripts/notice.py", ["--check"], False),
    ("scripts/check_themes.py", [], False),
    ("scripts/test_owui.py", [], False),
    ("scripts/test_verdict.py", [], False),
    ("scripts/test_serve_edit.py", [], False),
    ("scripts/test_connect.py", [], False),
    ("scripts/test_no_owui.py", [], False),
    ("suite/test_score.py", [], False),
    ("suite/test_mine.py", [], False),
    ("suite/test_seed.py", [], False),
    ("suite/test_rebench.py", [], False),
    ("suite/test_pick.py", [], False),
    ("scripts/test_client.py", [], True),          # runs the llm CLI against a fake server
]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    quick = "--quick" in sys.argv
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    failed = []
    for script, args, slow in CHECKS:
        if quick and slow:
            print(f"skip  {script}  (--quick)")
            continue
        if not (ROOT / script).exists():        # the export gate stays in the private repo
            print(f"skip  {script}  (not in this checkout)")
            continue
        started = time.monotonic()
        try:
            done = subprocess.run([sys.executable, script, *args], cwd=ROOT, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=900, env=env)
            ok, out = done.returncode == 0, (done.stdout + done.stderr).strip()
        except subprocess.TimeoutExpired:
            ok, out = False, "no result in 900 s"
        print(f"{'ok   ' if ok else 'FAIL '} {script}  ({time.monotonic() - started:.0f}s)")
        if not ok:
            failed.append(script)
            print("\n".join("      " + line for line in out.splitlines()[-15:]))
    print(f"\n{len(failed)} failed: {', '.join(failed)}" if failed else "\nall checks pass")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
