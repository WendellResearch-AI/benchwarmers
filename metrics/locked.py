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
import threading
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
    _forget(path)
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


_seen = threading.local()


@contextlib.contextmanager
def snapshot():
    """Inside it, this thread reads each file from disk once and reuses the
    text: one dashboard render asked for the same dozen config files 11,000
    times, seconds of opens on Windows. Text, not the parsed object, so a
    caller changing what it parsed changes nothing for the next one. Other
    threads (the gateway, a POST) read the disk as before; a write from this
    thread drops its copy."""
    if getattr(_seen, "texts", None) is not None:      # already inside one
        yield
        return
    _seen.texts = {}
    try:
        yield
    finally:
        _seen.texts = None


def once(key, compute):
    """compute(); inside snapshot(), its first answer for `key` (an OSError as
    first raised). Only for answers no caller can change, such as a string."""
    texts = getattr(_seen, "texts", None)
    if texts is None:
        return compute()
    if key not in texts:
        try:
            texts[key] = compute()
        except OSError as exc:
            texts[key] = exc                           # missing stays missing, as the same error
    if isinstance(texts[key], OSError):
        raise texts[key]
    return texts[key]


def read_text(path) -> str:
    """The file's text (OSError if it cannot be read); inside snapshot(), as
    first read."""
    return once(str(path), lambda: pathlib.Path(path).read_text(encoding="utf-8"))


def appended(path, mark: dict) -> tuple[bool, list[str]]:
    """(start over?, lines) for a log that is only ever appended to: the
    whole lines added since `mark` last saw it. The first time, or when the
    file was replaced or rewritten rather than appended to (shorter, or the
    bytes before where it stopped differ), it is True and every line. A last
    line still missing its newline waits for the next call. `mark` is the
    caller's own dict, kept between calls; hold one lock around this call
    and whatever folds the lines in."""
    path = str(path)
    at, before = (mark.get("at", 0), mark.get("before", b"")) if mark.get("path") == path else (0, b"")
    try:
        with open(path, "rb") as fh:
            size = fh.seek(0, os.SEEK_END)
            if at:
                fh.seek(max(at - len(before), 0))
                if size < at or fh.read(len(before)) != before:
                    at, before = 0, b""
            fh.seek(at)
            data = fh.read()
    except OSError:
        mark.clear()
        return True, []
    end = data.rfind(b"\n") + 1
    mark.update(path=path, at=at + end, before=(before + data[:end])[-64:])
    return at == 0, data[:end].decode("utf-8", "replace").splitlines()


def _forget(path) -> None:
    if (texts := getattr(_seen, "texts", None)) is not None:
        texts.pop(str(path), None)


def read_json(path: pathlib.Path) -> dict:
    """The file's object, or {} when it is missing, unreadable or not an object."""
    try:
        data = json.loads(read_text(path))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def update(path: pathlib.Path, change, **dump):
    """Read `path`, call `change(data)` to change the dict in place, write it
    back if it changed. Returns what `change` returned. `dump` goes to
    json.dumps (indent defaults to 2)."""
    dump.setdefault("indent", 2)
    with lock(path):
        _forget(path)                                  # under the lock, the disk's copy
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
        # A snapshot reads each file once; its own writes, and leaving it, read the disk again.
        g = pathlib.Path(tmp) / "g.json"
        with snapshot():
            assert read_json(g) == {}                           # missing stays missing
            g.write_text('{"a": 1}', encoding="utf-8")
            assert read_json(g) == {}
            write_atomic(g, '{"a": 2}')
            assert read_json(g) == {"a": 2}
            g.write_text('{"a": 3}', encoding="utf-8")
            read_json(g)["a"] = 9                               # changing what it parsed changes nothing
            assert read_json(g) == {"a": 2}
        assert read_json(g) == {"a": 3}
        # A log read in pieces: only what was added, a half-written line waits,
        # and a rewrite starts over.
        log, mark = pathlib.Path(tmp) / "l.jsonl", {}
        assert appended(log, mark) == (True, [])
        log.write_bytes(b"a\nb\nc")
        assert appended(log, mark) == (True, ["a", "b"])
        with log.open("ab") as fh:
            fh.write(b"\nd\n")
        assert appended(log, mark) == (False, ["c", "d"]) and appended(log, mark) == (False, [])
        log.write_bytes(b"x\ny\nz\nw\n")                     # longer, but not what was there
        assert appended(log, mark) == (True, ["x", "y", "z", "w"])
    print("ok")


if __name__ == "__main__":
    _selftest()
