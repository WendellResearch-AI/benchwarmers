"""launcher.py: what each command line runs, and that it really runs.

    python scripts/test_launcher.py

The launcher is what `benchwarmers` and `bench` are on every install (pip's
entry point, npm's bin/benchwarmers.js hands over to it, a checkout runs it
directly), so each route is checked: the version, the help, an unknown
command, the servers (with the first run where it belongs) and the client
(in the caller's folder, its own --help). Then two real runs: --version, and
mcp answering initialize with nothing else on stdout.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import launcher  # noqa: E402


def main() -> int:
    v = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert launcher.plan(["--version"]) == {"say": v, "code": 0}
    for argv in ([], ["serve"], ["--port-is-not-a-flag"]):            # no command: the dashboard
        p = launcher.plan(argv)
        assert p["script"] == "serve.py" and p["first_run"], (argv, p)
    assert launcher.plan(["serve", "--x"])["args"] == ["--x"]
    for argv in (["--help"], ["serve", "-h"], ["gateway", "--help"]):
        p = launcher.plan(argv)
        assert p["code"] == 0 and "ask <collection>" in p["say"], argv
    p = launcher.plan(["nope"])
    assert p["code"] == 2 and p.get("stderr") and 'no command "nope"' in p["say"], p
    p = launcher.plan(["init", "--help"])                              # init's own help
    assert p["script"].endswith("init.py") and p["args"] == ["--help"] and not p["first_run"], p
    p = launcher.plan(["mcp"])
    assert p["script"] == "mcp_server.py" and not p["first_run"], p      # stdout is the protocol's
    for argv in (["status"], ["ask", "core", "hi", "--help"], ["collection", "rm", "x", "--yes"]):
        p = launcher.plan(argv)
        assert p["script"] == "cli.py" and p["args"] == argv, (argv, p)
        assert p["cwd"] == os.getcwd() and not p["first_run"], p        # the caller's folder: -f is theirs
    assert launcher.plan(["gateway"])["cwd"] == str(ROOT)

    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    done = subprocess.run([sys.executable, str(ROOT / "launcher.py"), "--version"], capture_output=True,
                          text=True, env=env, timeout=60)
    assert done.returncode == 0 and done.stdout.strip() == v, done
    done = subprocess.run([sys.executable, str(ROOT / "launcher.py"), "nope"], capture_output=True,
                          text=True, env=env, timeout=60)
    assert done.returncode == 2 and not done.stdout and "nope" in done.stderr, done
    hello = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2025-06-18"}}) + "\n"
    done = subprocess.run([sys.executable, str(ROOT / "launcher.py"), "mcp"], input=hello, capture_output=True,
                          text=True, env=env, timeout=60)
    lines = done.stdout.splitlines()
    assert done.returncode == 0 and len(lines) == 1, done
    assert json.loads(lines[0])["result"]["serverInfo"]["version"] == v, lines
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
