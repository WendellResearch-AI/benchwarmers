"""One place that knows an alias's current name.

`config/alias-history.json` maps an old alias to the name it became, because
aliases get renamed (`laguna-xs` became `laguna-xs-2.1` when a 2.2 was
coming) and every record here is keyed by alias: suite outputs are named
`<alias>.md`, offloads log `"model": "<alias>"`, the perf history has a
`model` column, the run notes quote the alias in a table. The notes are
narrative and are never retro-edited; the rest is data and is never rewritten
either — instead every reader maps through this, so a renamed model keeps
its whole record under its new name.

Chains are followed (a -> b -> c resolves a to c) and cycles are refused.
"""

from __future__ import annotations

import json
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
HISTORY = DATA / "config" / "alias-history.json"


_cache: tuple = (None, {}, 0.0)


def history() -> dict[str, str]:
    """The file, read again only when it changes, and looked at no more than
    once a second: one dashboard render asks tens of thousands of times, and
    even a stat each time was seconds on Windows. A rename written here
    (serve.rename) calls forget(), so it shows at once."""
    global _cache
    seen, now = _cache, time.monotonic()          # one read: forget() may swap it on another thread
    if seen[0] and seen[0][0] == str(HISTORY) and now - seen[2] < 1:
        return seen[1]
    try:
        st = HISTORY.stat()
        key = (str(HISTORY), st.st_mtime_ns, st.st_size)
        table = seen[1] if seen[0] == key else json.loads(HISTORY.read_text(encoding="utf-8"))
    except (OSError, ValueError):                 # none yet, or half-written: no renames, for a second
        key, table = (str(HISTORY), None, None), {}
    _cache = (key, table, now)
    return table


def forget() -> None:
    global _cache
    _cache = (None, {}, 0.0)


def current(alias: str, table: dict[str, str] | None = None) -> str:
    """The name this alias goes by today."""
    table = history() if table is None else table
    seen = {alias}
    while alias in table:
        alias = table[alias]
        if alias in seen:
            raise ValueError(f"alias history loops at {alias!r}")
        seen.add(alias)
    return alias
