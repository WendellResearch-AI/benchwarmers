"""An answer stays an answer when the `llm` CLI fails to write its own log.

    python scripts/test_llm_log.py

2026-10-22, panel p20261022-160401-6ca2: seat inkling-small-free printed
"pong" and was recorded as an error, because `llm` then raised
"sqlite3.OperationalError: database is locked" writing logs.db, which the
other seats' `llm` processes held. A fake `llm` here prints an answer and
fails that way, through the two places the bench runs `llm -m`: the gateway's
seat call and suite/run.py's ask_once (the perf probe, the suite, repoqa,
retries, auto-retire). The same lock before any answer, and a stream cut
mid-answer, stay errors. BENCHWARMERS_HOME and LLM_USER_PATH name temp
folders before anything is imported.
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
TMP = pathlib.Path(tempfile.mkdtemp(prefix="bench-llmlog-"))
(TMP / "config").mkdir()
(TMP / "llm").mkdir()
os.environ.update(BENCHWARMERS_HOME=str(TMP), LLM_USER_PATH=str(TMP / "llm"), BENCH_CLIENT="llm")
sys.path[:0] = [str(ROOT), str(ROOT / "metrics"), str(ROOT / "suite")]

FAKE = '''import os, sys
sys.stdin.read()
mode = os.environ["FAKE_LLM_MODE"]
if mode in ("locked", "cut"):
    sys.stdout.write("pong\\n" if mode == "locked" else "half an ans")
    sys.stdout.flush()
if mode == "cut":
    sys.stderr.write("Error: Connection error.\\n")
    sys.exit(1)
if mode != "ok":
    sys.stderr.write("Traceback (most recent call last):\\n"
                     "  File \\"llm/models.py\\", line 1, in log_to_db\\n"
                     "sqlite3.OperationalError: database is locked\\n")
    sys.exit(1)
sys.stdout.write("pong\\n")
'''


def fake_llm() -> str:
    """A path that runs like an `llm` executable on Windows, macOS and Linux."""
    script = TMP / "fake_llm.py"
    script.write_text(FAKE, encoding="utf-8")
    if os.name == "nt":
        shim = TMP / "llm.cmd"
        shim.write_text(f'@"{sys.executable}" "{script}" %*\r\n@exit /b %ERRORLEVEL%\r\n', encoding="utf-8")
    else:
        shim = TMP / "llm"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        shim.chmod(0o755)
    return str(shim)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    import gateway
    import run
    binary = fake_llm()
    for mode, answered in (("ok", True), ("locked", True), ("locked-early", False), ("cut", False)):
        os.environ["FAKE_LLM_MODE"] = mode
        answer, error = gateway._ask_llm([binary, "-m", "m1"], "ping")
        assert (answer, error == "") == (("pong", True) if answered else (answer, False)), (mode, answer, error)
        body, _ = run.ask_once(binary, "m1", "ping", 60)
        kind = (run.outcome(body, min_length=1) or ("answered", ""))[0]
        assert (kind == "answered") == answered and (body.strip() == "pong") == answered, (mode, kind, body)
        if mode == "locked-early":
            assert "database is locked" in error and "database is locked" in body, (error, body)
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
