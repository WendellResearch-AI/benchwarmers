"""Change a JSON file without losing another writer's change.

    python metrics/locked.py      # self-check: threads and processes racing on one file

    import locked
    locked.update(path, lambda data: data.update(x=1))

WHY
---
The gateway's seats run in threads, and the suite, the probes and the
dashboard run as separate processes. Each read the whole file, changed it and
wrote it back, so two writers at once kept only the last one's change: a
learned limit or an alert silently vanished. `update` holds an OS file lock
(beside the file, `<name>.lock`) for the whole read-change-write, so writers
take turns, across threads and processes alike.

Writes go to a uniquely named temp file and are moved into place, so a reader
never sees half a file and needs no lock. On Windows the move is retried
briefly: it fails while another process has the file open to read it.
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import tempfile
import time

if os.name == "nt":
    import msvcrt
else:
    import fcntl

LOCK_TIMEOUT_S = 30


@contextlib.contextmanager
def lock(path: pathlib.Path, timeout: float = LOCK_TIMEOUT_S):
    """Hold `<path>.lock` exclusively. Per open file, so threads of one process
    exclude each other as well as other processes."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    with open(path.with_name(path.name + ".lock"), "a+b") as fh:
        while True:
            try:
                if os.name == "nt":
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"{path.name} stayed locked for {timeout:.0f}s") from None
                time.sleep(0.02)
        try:
            yield
        finally:
            if os.name == "nt":
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fh, fcntl.LOCK_UN)


def write_atomic(path: pathlib.Path, text: str) -> None:
    """Replace `path` with `text` in one step. The temp name is unique, so two
    writers never share it (a fixed `.part` name let one writer move another's
    half-written file into place)."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        for attempt in range(50):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                # Windows: a reader has the target open. It closes in milliseconds.
                if attempt == 49:
                    raise
                time.sleep(0.02)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def read_json(path: pathlib.Path) -> dict:
    """The file's object, or {} when it is missing, unreadable or not an object."""
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def update(path: pathlib.Path, change, **dump):
    """Read `path`, call `change(data)` to change the dict in place, write it
    back if it changed. Returns what `change` returned. `dump` goes to
    json.dumps (indent defaults to 2)."""
    dump.setdefault("indent", 2)
    with lock(path):
        data = read_json(path)
        before = json.dumps(data, sort_keys=True)
        result = change(data)
        if json.dumps(data, sort_keys=True) != before:
            write_atomic(path, json.dumps(data, **dump) + "\n")
        return result


def _bump(path: str, key: str, n: int) -> None:
    for _ in range(n):
        update(pathlib.Path(path), lambda d: d.__setitem__(key, d.get(key, 0) + 1))


def _selftest() -> None:
    import multiprocessing
    import threading
    with tempfile.TemporaryDirectory() as tmp:
        f = pathlib.Path(tmp) / "x.json"
        threads = [threading.Thread(target=_bump, args=(str(f), "t", 50)) for _ in range(4)]
        procs = [multiprocessing.Process(target=_bump, args=(str(f), "p", 50)) for _ in range(3)]
        for w in threads + procs:
            w.start()
        for w in threads + procs:
            w.join()
        assert all(p.exitcode == 0 for p in procs), [p.exitcode for p in procs]
        data = read_json(f)
        # Without the lock, racing read-change-writes lose increments.
        assert data == {"t": 200, "p": 150}, data
        assert update(f, lambda d: "same") == "same"          # no change, no write, result passed back
        assert not [p for p in f.parent.iterdir() if p.suffix == ".part"], "temp file left behind"
    print("ok")


if __name__ == "__main__":
    _selftest()
