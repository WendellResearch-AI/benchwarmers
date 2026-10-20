"""Write NOTICE from what the repository actually ships.

    python scripts/notice.py           # rewrite NOTICE
    python scripts/notice.py --check   # fail if NOTICE is out of date (scripts/check.py runs this)

RELEASE-PLAN.md Phase 4: the credits are generated, not kept by hand. Each
public real-bug task records the project it came from, its licence and the
pull request (`upstream`, `license`, `source_url` in task.json), so adding a
task adds its credit and dropping one removes it. The fonts and the dataset
are listed from what is in the tree.

Still to do before release (Phase 7): the full licence texts the projects'
licences require (Apache-2.0, BSD-3-Clause with its copyright lines) in a
LICENSES/ folder.
"""

from __future__ import annotations

import collections
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
NOTICE = ROOT / "NOTICE"
DATASET = "nebius/SWE-rebench-V2"                  # suite/rebench.py DATASET


def render() -> str:
    projects: dict[tuple[str, str], list[str]] = collections.defaultdict(list)
    for path in sorted((ROOT / "suite" / "tasks").glob("rebench-*/task.json")):
        spec = json.loads(path.read_text(encoding="utf-8"))
        key = (spec.get("upstream") or "unknown", spec.get("license") or "unknown licence")
        if spec.get("source_url") and spec["source_url"] not in projects[key]:
            projects[key].append(spec["source_url"])
    lines = [
        "Benchwarmers, by Wendell Research",
        "Copyright (c) 2026 Wendell Research. Licensed under the MIT License (LICENSE).",
        "",
        "This repository also contains material from others, under their own licences:",
        "",
    ]
    if projects:
        lines += [f"Real-bug tasks (suite/tasks/rebench-*) are drawn from the {DATASET} dataset",
                  "on Hugging Face (CC-BY-4.0). Each task holds code from the project it names,",
                  "under that project's licence:", ""]
        for (upstream, licence), urls in sorted(projects.items(), key=lambda kv: kv[0][0].lower()):
            lines.append(f"  {upstream} ({licence})")
            lines += [f"    {u}" for u in urls]
        lines.append("")
    if sorted((ROOT / "ui" / "fonts").glob("chivo*.woff2")):
        lines += ["Fonts (ui/fonts): Chivo and Chivo Mono, Copyright The Chivo Project Authors",
                  "(https://github.com/Omnibus-Type/Chivo), under the SIL Open Font License 1.1",
                  "(ui/fonts/OFL-*.txt).", ""]
    return "\n".join(lines)


def main() -> int:
    text = render()
    if "--check" in sys.argv:
        current = NOTICE.read_text(encoding="utf-8") if NOTICE.exists() else ""
        if current.replace("\r\n", "\n") != text:
            print("NOTICE is out of date: run python scripts/notice.py")
            return 1
        print("NOTICE is current")
        return 0
    NOTICE.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {NOTICE.name}: {text.count(chr(10))} lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
