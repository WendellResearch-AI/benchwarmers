"""Bench-gateway collections: named groups of models a caller asks for by name.

    config/collections.json   {"core": {"members": [...], "shape": "",
                                         "description": "...", "tags": ["general"]}}
    config/gateway.json       {"max_calls": 5, "max_explorers": 2}

A request for `bench/core` runs every member, plus explorer seats that
`route.py` fills by staleness so the roster keeps getting evidence even when
the same collection is called every day:

    explorers = min(max_explorers, max_calls - members)

`shape` (review, design, …) decides which models may fill those explorer
seats; empty means a decision model labels each prompt, or `general` without
one. Named `bench_collections` because `collections` would shadow the
standard library for everything importing from metrics/.
"""

from __future__ import annotations

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
FILE = DATA / "config" / "collections.json"
SETTINGS_FILE = DATA / "config" / "gateway.json"
DEFAULTS = {"max_calls": 5, "max_explorers": 2}
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")
PREFIX = "bench/"


def _read(path: pathlib.Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def unreadable(path: pathlib.Path) -> bool:
    """True if the file exists but is not a JSON object. A writer must refuse
    then: `_read` would hand it {} and the save would wipe the file."""
    if not path.exists():
        return False
    try:
        return not isinstance(json.loads(path.read_text(encoding="utf-8")), dict)
    except (OSError, ValueError):
        return True


def load() -> dict[str, dict]:
    return _read(FILE)


def settings() -> dict:
    return {**DEFAULTS, **{k: v for k, v in _read(SETTINGS_FILE).items() if k in DEFAULTS}}


def explorers_for(members: int, s: dict | None = None) -> int:
    s = s or settings()
    return max(0, min(s["max_explorers"], s["max_calls"] - members))


TAG = re.compile(r"^[a-z0-9][a-z0-9-]{0,23}$")


def described(name: str, c: dict, s: dict | None = None) -> dict:
    """What a caller needs to choose a collection: purpose, tags and make-up.
    Served on GET /v1/models, so an orchestrator can pick by task."""
    members = c.get("members", [])
    out = {"description": c.get("description") or c.get("note", ""),
           "tags": c.get("tags", []), "members": members, "shape": c.get("shape", ""),
           "explorers": explorers_for(len(members), s)}
    # What the members can handle together (metrics/catalog.py): the smallest
    # context window is what the gateway refuses above.
    try:
        import catalog
        out["together"] = catalog.consolidate(members)
    except Exception:                                  # noqa: BLE001
        out["together"] = None
    return out


def resolve(model: str | None) -> str | None:
    """The collection a request's `model` names: `bench/core` or `core`; None
    when it names nothing here. There is no default: a caller that did not
    choose is told what each collection is for and asked to choose."""
    name = (model or "").strip().removeprefix(PREFIX)   # "bench/" alone names nothing
    return name if name and name in load() else None


def choose_message(model: str | None) -> str:
    """The refusal for a request that names no collection, or the wrong one:
    every collection with its description and tags, so the caller can choose."""
    asked = f"{model!r} is not a collection here." if model else "No collection was named."
    rows = [f"- {PREFIX}{n}: {c.get('description') or 'no description'}"
            + (f" [{', '.join(c.get('tags', []))}]" if c.get("tags") else "")
            for n, c in sorted(load().items())]
    return (f"{asked} Set \"model\" to the collection that fits the task"
            " (GET /v1/models lists them with description and tags):\n"
            + ("\n".join(rows) or "- none yet: create one in the dashboard's Collections dialog"))


if __name__ == "__main__":
    assert explorers_for(1) == 2 and explorers_for(2) == 2 and explorers_for(3) == 2
    assert explorers_for(4) == 1 and explorers_for(5) == 0 and explorers_for(6) == 0
    assert explorers_for(3, {"max_calls": 4, "max_explorers": 2}) == 1
    assert resolve("bench/") is None and resolve("bench/no-such") is None and resolve("") is None
    print("ok")
