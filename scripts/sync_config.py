"""Move `extra-openai-models.yaml` between this repo and the llm CLI.

The CLI reads its model registrations from one fixed path in its own per-user
folder (endpoints.LLM_DIR), which is not somewhere anyone thinks to look and
not somewhere version control reaches. So the data folder holds the canonical
copy and this moves it into place.

    python scripts/sync_config.py          # data folder -> llm  (the usual way)
    python scripts/sync_config.py --pull   # llm -> data folder  (live has drifted)
    python scripts/sync_config.py --check  # report differences, change nothing

A backup of whatever is being overwritten is written beside the target first,
because the live file is the one thing here that is not reproducible if the
repo copy turns out to be older.
"""

from __future__ import annotations

import argparse
import datetime as dt
import filecmp
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "metrics"))
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
import endpoints  # noqa: E402
REPO = DATA / "config" / "extra-openai-models.yaml"
LIVE = endpoints.LLM_DIR / "extra-openai-models.yaml"


def _backup(path: pathlib.Path) -> pathlib.Path | None:
    if not path.exists():
        return None
    stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    dest = path.with_suffix(f".{stamp}.bak")
    shutil.copy2(path, dest)
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pull", action="store_true",
                    help="copy the live file back into the repo")
    ap.add_argument("--check", action="store_true",
                    help="report whether they differ; change nothing")
    args = ap.parse_args()

    if not REPO.exists() and not args.pull:
        print(f"repo copy missing: {REPO}", file=sys.stderr)
        return 1

    if args.check:
        if not LIVE.exists():
            print(f"live file does not exist: {LIVE}")
            return 1
        same = REPO.exists() and filecmp.cmp(REPO, LIVE, shallow=False)
        print("identical" if same else "DIFFERENT")
        if not same:
            print(f"  repo: {REPO}")
            print(f"  live: {LIVE}")
        return 0 if same else 1

    src, dst = (LIVE, REPO) if args.pull else (REPO, LIVE)
    if not src.exists():
        print(f"source missing: {src}", file=sys.stderr)
        return 1

    dst.parent.mkdir(parents=True, exist_ok=True)
    backup = _backup(dst)
    shutil.copy2(src, dst)
    print(f"{src}\n  -> {dst}")
    if backup:
        print(f"  (previous version kept at {backup.name})")
    if not args.pull:
        apply_options()
        print("\nConfirm with:  llm models list")
    return 0


def apply_options() -> None:
    """`config/model-options.json` -> `llm models options set`, per alias.

    Default options live in the llm CLI's own store, which no repo reaches;
    this file is the canonical copy, as the yaml is for registrations."""
    import json
    import subprocess
    path = REPO.parent / "model-options.json"
    if not path.exists():
        return
    for alias, options in json.loads(path.read_text(encoding="utf-8")).items():
        if alias.startswith("_"):
            continue
        for name, value in options.items():
            done = subprocess.run(["llm", "models", "options", "set", alias, name, str(value)],
                                  capture_output=True, text=True)
            if done.returncode:
                print(f"  could not set {alias} {name}={value}: {done.stderr.strip()[:200]}")


if __name__ == "__main__":
    raise SystemExit(main())
