"""List what Open WebUI actually serves, versus what the llm CLI can see.

The gap between those two is the thing this script exists for. A model can be
live in Open WebUI and completely invisible to the CLI, which reports it as
"not configured" — indistinguishable, from the outside, from not existing.

    python scripts/list_served.py             # everything, marked
    python scripts/list_served.py nemotron    # filter by substring

Output marks each served model:

    [registered]   in extra-openai-models.yaml, usable now
    [ MISSING  ]   served but unregistered — add it to the yaml

The API key is read from the llm CLI's own `keys.json` and is never printed.
"""

from __future__ import annotations

import json
import pathlib
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "metrics"))
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
import endpoints  # noqa: E402
LLM_DIR = endpoints.LLM_DIR
REPO_YAML = DATA / "config" / "extra-openai-models.yaml"
API_BASE = endpoints.base()   # the default endpoint; the picker covers the rest
TIMEOUT = 30


def registered_model_names() -> set[str]:
    """`model_name` values from the yaml — what the endpoint is asked for.

    Parsed by hand rather than with PyYAML so this script has no dependency;
    the file is a flat list of scalar keys and that is unlikely to change.
    """
    text = REPO_YAML.read_text(encoding="utf-8") if REPO_YAML.exists() else ""
    names = set()
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("model_name:"):
            names.add(line.split(":", 1)[1].strip().strip('"').strip("'"))
    return names


def served() -> list[str]:
    return sorted(endpoints.models(timeout=TIMEOUT))


def main() -> int:
    needle = sys.argv[1].lower() if len(sys.argv) > 1 else ""
    try:
        models = served()
    except Exception as exc:                       # noqa: BLE001
        print(f"could not reach {API_BASE}: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1

    known = registered_model_names()
    shown = [m for m in models if needle in m.lower()]
    missing = 0
    for name in shown:
        mark = "[registered]" if name in known else "[ MISSING  ]"
        missing += name not in known
        print(f"  {mark}  {name}")

    print(f"\n{len(shown)} shown of {len(models)} served; {missing} unregistered")
    if missing:
        print(f"Add the missing ones to {REPO_YAML.name}, then:"
              f"\n  python scripts/sync_config.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
