"""Bench-gateway collections: named groups of models a caller asks for by name.

    config/collections.json   {"core": {"members": [...], "shape": "",
                                         "description": "...", "tags": ["general"],
                                         "reply": "all", "merger": ""}}
    config/gateway.json       {"max_calls": 5, "max_explorers": 2,
                               "pick_collection": true, "pick_confidence": 0.6}

A request for `bench/core` runs every member, plus explorer seats that
`route.py` fills by staleness so the roster keeps getting evidence even when
the same collection is called every day:

    explorers = min(max_explorers, max_calls - members)

A collection may hold its own `max_calls` and `max_explorers`; what it leaves
out follows gateway.json, so changing that moves every collection without its
own. A member out of quota keeps its place and an explorer sits in for it
until the quota is back (gateway.py), unless max_explorers is 0: then only the
members ever run.

`shape` (review, design, …) decides which models may fill those explorer
seats; empty means a decision model labels each prompt, or `general` without
one. `reply` is what the caller reads: `all` (every answer, the default) or
`merged` (one answer a `merger` model writes from them; empty means chosen by
verdict record, gateway.py) or `route` (no panel: one member, picked by its
record, gets the request as sent, tools and all, for agent clients;
GATEWAY.md "Route mode"). A request may override it with a `:merged`,
`:all` or `:route` suffix on the model name, or `"bench": {"reply": ...}`
in the body. `ad_hoc` (false unless set) keeps a collection out of everything
that offers collections to choose from: it runs when a request names it, as an
ad hoc model does (suite/run.py), for the expensive ones a person decides on.
Named `bench_collections` because `collections` would shadow the standard
library for everything importing from metrics/.
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
BOUNDS = {"max_calls": (1, 10), "max_explorers": (0, 2)}   # route.py seats at most two explorers
NAME =re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")
PREFIX = "bench/"
REPLIES = ("all", "merged", "route")


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


def pick_settings() -> dict:
    """gateway.json's `pick_collection` (on unless false: a request that names
    no collection gets one a decision model chooses, gateway.pick_collection)
    and `pick_confidence` (above 0, at most 1; None means decide.CONFIDENT).
    Kept beside the limits, which save_gateway_settings leaves alone."""
    raw = _read(SETTINGS_FILE)
    on, at = raw.get("pick_collection"), raw.get("pick_confidence")
    return {"pick_collection": on is not False,
            "pick_confidence": at if isinstance(at, (int, float)) and not isinstance(at, bool) and 0 < at <= 1 else None}


def limit_ok(key: str, value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and BOUNDS[key][0] <= value <= BOUNDS[key][1]


def own_limits(c: dict) -> dict:
    """The limits a collection sets for itself; a hand-edited one out of bounds counts as unset."""
    return {k: c[k] for k in DEFAULTS if limit_ok(k, c.get(k))}


def limits(c: dict, s: dict | None = None) -> dict:
    return {**(s or settings()), **own_limits(c)}


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
           "explorers": explorers_for(len(members), limits(c, s)), **own_limits(c),
           "reply": c.get("reply") or "all", "merger": c.get("merger", ""), "ad_hoc": bool(c.get("ad_hoc"))}
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
    when it names nothing here. There is no default: a request with no model
    gets one a decision model chooses when it is sure (gateway.pick_collection,
    pick_settings), and is otherwise told what each collection is for and
    asked to choose, as is one naming a collection that does not exist."""
    name = (model or "").strip().removeprefix(PREFIX)   # "bench/" alone names nothing
    return name if name and name in load() else None


def split_reply(model: str) -> tuple[str, str]:
    """`bench/core:merged` -> ("bench/core", "merged"), for clients that can
    only pick a model name. No known suffix -> (model, ""): a collection name
    cannot hold ':', so anything else stays and is refused as unknown."""
    base, sep, mode = model.rpartition(":")
    return (base, mode) if sep and mode in REPLIES else (model, "")


def reply_errors(reply: str, merger: str, known) -> list[str]:
    """What is wrong with a collection's reply mode and merger. `known`: the
    chat models on the bench; the merger is any of them, member or not."""
    errors = []
    if reply not in REPLIES:
        errors.append(f"reply must be one of {', '.join(REPLIES)}")
    if merger and merger not in known:
        errors.append(f"merger: {merger!r} is not a chat model on the bench; leave it empty to choose by verdicts")
    return errors


def choose_message(model: str | None) -> str:
    """The refusal for a request that names no collection, or the wrong one:
    every collection on offer with its description and tags, so the caller can choose."""
    asked = f"{model!r} is not a collection here." if model else "No collection was named."
    rows = [f"- {PREFIX}{n}: {c.get('description') or 'no description'}"
            + (f" [{', '.join(c.get('tags', []))}]" if c.get("tags") else "")
            for n, c in sorted(load().items()) if not c.get("ad_hoc")]
    return (f"{asked} Set \"model\" to the collection that fits the task"
            " (GET /v1/models lists them with description and tags):\n"
            + ("\n".join(rows) or "- none yet: create one in the dashboard's Collections dialog"))


if __name__ == "__main__":
    # DEFAULTS, not settings(): the install's gateway.json must not decide the check.
    assert [explorers_for(n, DEFAULTS) for n in range(1, 7)] == [2, 2, 2, 1, 0, 0]
    assert explorers_for(3, {"max_calls": 4, "max_explorers": 2}) == 1
    assert limits({"max_calls": 4, "max_explorers": 0}, DEFAULTS) == {"max_calls": 4, "max_explorers": 0}
    assert limits({"max_calls": 99, "max_explorers": True}, DEFAULTS) == DEFAULTS and limits({}, DEFAULTS) == DEFAULTS
    assert explorers_for(4, limits({"max_calls": 4}, DEFAULTS)) == 0
    assert resolve("bench/") is None and resolve("bench/no-such") is None and resolve("") is None
    assert split_reply("bench/core:merged") == ("bench/core", "merged") and split_reply("core:all") == ("core", "all")
    assert split_reply("bench/core:route") == ("bench/core", "route")
    assert split_reply("bench/core") == ("bench/core", "") and split_reply("bench/core:x") == ("bench/core:x", "")
    import tempfile
    real = SETTINGS_FILE
    with tempfile.TemporaryDirectory() as tmp:
        SETTINGS_FILE = pathlib.Path(tmp) / "gateway.json"
        assert pick_settings() == {"pick_collection": True, "pick_confidence": None}           # on by default
        SETTINGS_FILE.write_text('{"pick_collection": false, "pick_confidence": 0.8}', encoding="utf-8")
        assert pick_settings() == {"pick_collection": False, "pick_confidence": 0.8}
        SETTINGS_FILE.write_text('{"pick_confidence": 2}', encoding="utf-8")                   # out of range: unset
        assert pick_settings() == {"pick_collection": True, "pick_confidence": None}
    SETTINGS_FILE = real
    assert not reply_errors("merged", "", []) and not reply_errors("all", "m", ["m"])
    assert len(reply_errors("", "m", [])) == 2
    print("ok")
