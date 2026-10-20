"""Things that went wrong and are still wrong, where they will be seen.

    python metrics/alerts.py            # what is currently wrong

The scheduled checks already find problems. Until now they announced them by
exiting non-zero, which Windows records and nobody reads — a model could be
retired for a fortnight and the only trace would be a number in Task
Scheduler. Work that reports only to a log nobody opens has not reported.

KEYED AND SELF-CLEARING, NOT AN APPEND LOG
------------------------------------------
An alert has a stable `key`, so a daily check that keeps finding the same
retired model updates one row rather than writing thirty. The first sighting is
kept, because "missing since Tuesday" is the useful part — a model absent for a
day may be an outage and one absent for a week is gone.

**A check that finds nothing CLEARS its alerts.** Otherwise the file only ever
grows and the dashboard turns into a wall of stale red that everyone learns to
ignore, which is worse than no alerting at all. `sweep()` exists so a caller
can say "these are all the problems I found" and have the rest retired
automatically.

WHAT IS NOT AN ALERT
--------------------
Anything transient. A rate limit, a timeout, one failed call — those are
`capacity` and they resolve themselves. An alert is for a state that will not
fix itself and that somebody has to decide about: a model retired upstream, a
registration pointing at a name that does not exist, a subscription that no
longer covers what it did.
"""

from __future__ import annotations

import datetime as dt
import pathlib

import locked

ROOT = pathlib.Path(__file__).resolve().parent.parent
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
FILE = DATA / "metrics" / "alerts.json"


def _load() -> dict:
    return locked.read_json(FILE)


def _update(change):
    """Read-change-write under a lock: the gateway's seat threads, the
    scheduled checks and the dashboard all raise and clear alerts."""
    return locked.update(FILE, change, sort_keys=True)


def raise_alert(key: str, kind: str, message: str, action: str = "",
                **extra) -> None:
    """Record a problem, keeping the date it was first seen.

    `extra` carries what the dashboard needs to ACT on it rather than just
    show it: `alias` for a one-model action, `aliases` for retire-all.
    """
    today = dt.date.today().isoformat()

    def put(data: dict) -> None:
        data[key] = {
            "kind": kind,
            "message": message,
            "action": action,
            "first_seen": data.get(key, {}).get("first_seen", today),
            "last_seen": today,
            **extra,
        }
    _update(put)


def clear(key: str) -> bool:
    return _update(lambda data: data.pop(key, None) is not None)


def sweep(prefix: str, still_wrong: set[str]) -> list[str]:
    """Retire every alert under `prefix` that the caller did not re-raise.

    This is what stops the file growing for ever. A check reports the complete
    set of problems it found, and anything it used to find but no longer does
    is resolved — a model that came back should not leave a red row behind it.
    """
    def retire(data: dict) -> list[str]:
        gone = [k for k in data
                if k.startswith(prefix) and k not in still_wrong]
        for key in gone:
            del data[key]
        return gone
    return _update(retire)


def active() -> dict:
    return _load()


def main() -> None:
    data = active()
    if not data:
        print("Nothing wrong.")
        return
    today = dt.date.today()
    print(f"{len(data)} open:\n")
    for key, a in sorted(data.items(),
                         key=lambda kv: kv[1]["first_seen"]):
        days = (today - dt.date.fromisoformat(a["first_seen"])).days
        age = "today" if days == 0 else f"{days}d"
        print(f"  [{a['kind']}] {a['message']}")
        print(f"      first seen {a['first_seen']} ({age})"
              + (f" — {a['action']}" if a["action"] else ""))


if __name__ == "__main__":
    main()
