"""The bench's own scheduler: the recurring checks, without Windows Task Scheduler.

    python metrics/scheduler.py              # mode, every job, when each is next due
    python metrics/scheduler.py --selftest

RELEASE-PLAN.md Phase 3. The checks used to exist only as Windows scheduled
tasks (scripts/schedule.ps1), which macOS, Linux and Docker do not have. serve.py
starts this in a background thread; the jobs below are the same seven, at the
same times, with the same arguments (schedule.ps1 explains each choice).

MODE, from config/scheduler.json:
  absent / "off"   nothing runs. A fresh install spends no model calls until
                   someone opts in.
  "log-only"       at each due time, write "would run X" to runs/scheduler.log
                   and run nothing. The switch-over week: it runs beside the
                   Windows tasks, which still do the work, so its timing can be
                   checked against theirs without doubling a single call.
  "run"            run the job (the repo's venv Python), one at a time per job,
                   with the Windows tasks switched off in the same step.
`"jobs": {"perf": false}` leaves a job out.

A run missed while the dashboard was down is caught up once when it next
starts (the Windows tasks' "start when available"). The last due time of each
job is kept in runs/scheduler-state.json; a lock beside it means only one
scheduler runs however many dashboards are started.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import subprocess
import sys
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
CONFIG = DATA / "config" / "scheduler.json"
LOG = DATA / "runs" / "scheduler.log"
STATE = DATA / "runs" / "scheduler-state.json"
TICK_S = 30
RUN_LIMIT_S = 2 * 3600                             # schedule.ps1's ExecutionTimeLimit
CATCH_UP_DAYS = 7                                  # older misses are not worth a burst of calls

# name -> (script and arguments, slots). A slot is (weekday or None, "HH:MM");
# weekday 0 is Monday. Mirrors scripts/schedule.ps1, which says why each runs when.
JOBS = {
    "availability": (["perf/run.py", "-n", "1", "--tiny-only", "--timeout", "90", "--client", "builtin"],
                     [(None, "05:00")]),
    "catalog": (["metrics/catalog.py"], [(None, "06:00")]),
    "suite-free": (["suite/run.py", "--next-free"], [(None, "06:00")]),
    "deprecations": (["scripts/check_deprecations.py", "--quiet"], [(None, "08:00")]),
    "perf": (["perf/run.py", "-n", "3", "--timeout", "90", "--client", "builtin"], [(0, "07:00")]),
    "capabilities": (["metrics/probe_caps.py", "--gap", "30"], [(6, "07:00")]),
    "suite": (["suite/run.py", "--next"], [(None, t) for t in ("03:00", "09:00", "15:00", "21:00")]),
}


def settings() -> dict:
    try:
        data = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"mode": "off", "jobs": {}}
    return {"mode": data.get("mode", "off"), "jobs": data.get("jobs") or {}}


def enabled(conf: dict) -> list[str]:
    return [n for n in JOBS if conf["jobs"].get(n, True)]


def due_between(slots, start: dt.datetime, end: dt.datetime) -> list[dt.datetime]:
    """Every slot time in (start, end], oldest first."""
    out = []
    day = start.date()
    while day <= end.date():
        for weekday, hhmm in slots:
            if weekday is not None and day.weekday() != weekday:
                continue
            h, m = map(int, hhmm.split(":"))
            when = dt.datetime.combine(day, dt.time(h, m))
            if start < when <= end:
                out.append(when)
        day += dt.timedelta(days=1)
    return sorted(out)


def next_due(slots, after: dt.datetime) -> dt.datetime:
    return due_between(slots, after, after + dt.timedelta(days=8))[0]


def _log(line: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {line}\n")


def _run(name: str, args: list[str], due: dt.datetime, running: dict) -> None:
    """One job, in its own thread, recorded in the log with its exit code."""
    sys.path.insert(0, str(ROOT / "suite"))
    from run import bench_python
    started = time.monotonic()
    try:
        done = subprocess.run([bench_python(), *args], cwd=ROOT, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=RUN_LIMIT_S)
        result = f"exit {done.returncode}"
    except subprocess.TimeoutExpired:
        result = f"stopped at the {RUN_LIMIT_S // 3600} h limit"
    except OSError as exc:
        result = f"could not start ({exc})"
    finally:
        running.pop(name, None)
    _log(f"ran {name} (due {due:%H:%M}): {result} in {time.monotonic() - started:.0f}s")


def tick(now: dt.datetime, state: dict, conf: dict, running: dict, start_job=None) -> list[str]:
    """Act on everything due since each job's last due time. Changes `state`
    in place; returns what it logged (for the self-check)."""
    logged = []
    for name in enabled(conf):
        args, slots = JOBS[name]
        last = dt.datetime.fromisoformat(state[name]) if name in state else now
        due = due_between(slots, max(last, now - dt.timedelta(days=CATCH_UP_DAYS)), now)
        if not due:
            state.setdefault(name, now.isoformat(timespec="seconds"))
            continue
        latest = due[-1]
        missed = " (catching up a run missed while stopped)" if now - latest > dt.timedelta(minutes=5) else ""
        state[name] = latest.isoformat(timespec="seconds")
        if conf["mode"] == "log-only":
            line = f"would run {name} (due {latest:%a %H:%M}){missed}: {' '.join(args)}"
        elif name in running:
            line = f"skipped {name} (due {latest:%H:%M}): the previous run is still going"
        else:
            line = f"starting {name} (due {latest:%H:%M}){missed}"
            running[name] = True
            (start_job or _start)(name, args, latest, running)
        _log(line)
        logged.append(line)
    return logged


def _start(name, args, due, running) -> None:
    threading.Thread(target=_run, args=(name, args, due, running), daemon=True).start()


_stop = threading.Event()


def _loop() -> None:
    import locked
    # Its own lock file, not the state file's: update() below locks that one
    # for each write, and holding it here as well would wait on itself.
    try:
        instance = locked.lock(DATA / "runs" / "scheduler", timeout=0)
        instance.__enter__()                            # one scheduler per machine
    except TimeoutError:
        return                                          # another dashboard already runs it
    try:
        running: dict = {}
        conf = settings()
        _log(f"scheduler started, mode {conf['mode']}, jobs {', '.join(enabled(conf))}")
        while True:
            try:
                conf = settings()                       # a mode change applies without a restart
                if conf["mode"] in ("log-only", "run"):
                    locked.update(STATE, lambda s: tick(dt.datetime.now().replace(microsecond=0),
                                                        s, conf, running))
            except Exception as exc:                    # noqa: BLE001 -- one bad tick must not end it
                _log(f"tick failed: {type(exc).__name__}: {exc}")
            if _stop.wait(TICK_S):
                return
    finally:
        instance.__exit__(None, None, None)


def start() -> threading.Thread:
    """Called once by serve.py. Does nothing visible when the mode is off."""
    _stop.clear()
    thread = threading.Thread(target=_loop, name="scheduler", daemon=True)
    thread.start()
    return thread


def _selftest() -> None:
    mon = dt.datetime(2026, 10, 19, 4, 0)               # a Monday
    slots = JOBS["suite"][1]
    assert [d.hour for d in due_between(slots, mon, mon + dt.timedelta(days=1))] == [9, 15, 21, 3]
    assert next_due(JOBS["perf"][1], mon) == dt.datetime(2026, 10, 19, 7, 0)
    assert next_due(JOBS["perf"][1], mon.replace(hour=8)) == dt.datetime(2026, 10, 26, 7, 0)
    assert next_due(JOBS["capabilities"][1], mon) == dt.datetime(2026, 10, 25, 7, 0)
    conf = {"mode": "log-only", "jobs": {n: n == "availability" for n in JOBS}}
    state: dict = {}
    global _log
    real_log, _log = _log, (lambda line: None)
    try:
        assert tick(mon, state, conf, {}) == [] and state == {"availability": mon.isoformat()}
        lines = tick(mon.replace(hour=5, minute=1), state, conf, {})
        assert len(lines) == 1 and lines[0].startswith("would run availability"), lines
        assert tick(mon.replace(hour=5, minute=2), state, conf, {}) == []      # once per due time
        # Stopped over two mornings: one catch-up, not two runs.
        lines = tick(mon.replace(hour=5) + dt.timedelta(days=2, hours=3), state, conf, {})
        assert len(lines) == 1 and "catching up" in lines[0], lines
        started = []
        run_conf = {"mode": "run", "jobs": conf["jobs"]}
        lines = tick(mon.replace(hour=5) + dt.timedelta(days=3, minutes=1), state, run_conf,
                     {}, start_job=lambda *a: started.append(a[0]))
        assert started == ["availability"] and lines[0].startswith("starting availability"), lines
        lines = tick(mon.replace(hour=5) + dt.timedelta(days=4, minutes=1), state, run_conf,
                     {"availability": True}, start_job=lambda *a: started.append(a[0]))
        assert started == ["availability"] and lines[0].startswith("skipped"), lines   # no overlap
    finally:
        _log = real_log
    # The loop itself, briefly, on temp files: it must write its state while
    # holding the one-per-machine lock (the first version waited on itself).
    import tempfile
    global DATA, CONFIG, LOG, STATE, TICK_S
    real = DATA, CONFIG, LOG, STATE, TICK_S
    with tempfile.TemporaryDirectory() as tmp:
        DATA = pathlib.Path(tmp)                        # the lock and state go here, not to the real data
        CONFIG, LOG, STATE, TICK_S = DATA / "s.json", DATA / "runs" / "s.log", DATA / "runs" / "s.json", 0.05
        CONFIG.write_text('{"mode": "log-only"}', encoding="utf-8")
        thread = start()
        try:
            for _ in range(100):
                if STATE.exists():
                    break
                time.sleep(0.05)
            assert STATE.exists() and set(json.loads(STATE.read_text())) == set(JOBS), "loop wrote no state"
        finally:
            _stop.set()                                 # stopped, and its lock let go, before
            thread.join(5)                              # the real paths come back
            assert not thread.is_alive(), "the loop did not stop"
            DATA, CONFIG, LOG, STATE, TICK_S = real
    print("ok")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if "--selftest" in sys.argv:
        _selftest()
        return
    conf, now = settings(), dt.datetime.now()
    print(f"mode: {conf['mode']}  (config/scheduler.json)")
    for name in JOBS:
        args, slots = JOBS[name]
        on = "on " if name in enabled(conf) else "off"
        print(f"  {on} {name:<13} next {next_due(slots, now):%a %d %b %H:%M}   {' '.join(args)}")


if __name__ == "__main__":
    main()
