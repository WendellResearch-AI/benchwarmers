"""The `benchwarmers` and `bench` commands, however Benchwarmers was installed.

    pipx install benchwarmers        # PyPI: this file is the entry point (pyproject.toml)
    npm install -g @wendellresearch-ai/benchwarmers    # npm: bin/benchwarmers.js finds Python and runs this
    python launcher.py [command]     # a checkout

It keeps the data in the user's profile (scripts/init.py's default_folder, or
BENCHWARMERS_HOME), sets that folder up on the first run, then runs the
command: a server (serve, gateway, mcp, init) or, for everything else, the
client (cli.py) in the caller's own folder. One launcher for every install, so
the commands, the help and the first run are written once.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent                 # the app: a checkout, the npm package or the wheel's folder
SERVERS = {"serve": "serve.py", "gateway": "gateway.py", "mcp": "mcp_server.py",
           "init": str(Path("scripts") / "init.py")}
# Everything but running a server is cli.py, a client of a running server.
CLIENT = ("status", "ask", "verdict", "models", "model", "candidates", "onboard", "dismiss",
          "collections", "collection", "endpoints", "endpoint", "keys", "key", "settings")


def version() -> str:
    return (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def default_home() -> Path:
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import init
        return init.default_folder()
    finally:
        sys.path.pop(0)


def prog() -> str:
    """`bench` or `benchwarmers`, as typed (npm passes it, being the one that saw it)."""
    named = os.environ.get("BENCHWARMERS_PROG") or Path(sys.argv[0]).stem
    return named if named in ("bench", "benchwarmers") else "benchwarmers"


def usage() -> str:
    return "\n".join([
        f"Usage: {prog()} [command]",
        "",
        "Running it:",
        "  serve        the dashboard, with the gateway at /v1: http://localhost:8122 (the default)",
        "  gateway      the gateway alone: http://127.0.0.1:8123/v1",
        "  init [...]   set up a data folder, optionally with a first endpoint (init --help)",
        "  mcp          an MCP server on stdio for agents",
        "",
        "Using it (a client of a running server, BENCHWARMERS_URL; --help on any):",
        "  status                          what is running, and where",
        "  ask <collection> [prompt]       ask a collection; -f FILE adds a file, a pipe reads stdin",
        "  verdict <call> --useful|--not-useful [--real ...] [--false ...]",
        "  models | model <action>         list, rename, role, retire, restore, max-tokens, ...",
        "  candidates | onboard | dismiss  what could be onboarded, and onboarding it",
        "  collections | collection <action>   list, show, save, rm",
        "  endpoints | endpoint <action>   list, add, test, key, rm, default",
        "  keys | key <action>             gateway keys: list, create, revoke",
        "  settings <gateway|client|scheduler>",
        "  --version    the version",
        "",
        f"Your data lives in BENCHWARMERS_HOME, else {default_home()}.",
        "BENCH_PORT changes the port. Docs: https://github.com/WendellResearch-AI/benchwarmers",
    ])


def plan(argv: list[str]) -> dict:
    """What a command line means: {"say", "code"} to print and stop, or
    {"script", "args", "cwd", "first_run"} to run. Pure, so it can be tested."""
    first = argv[0] if argv else ""
    command = first if first and not first.startswith("-") else "serve"
    client = command in CLIENT
    if first in ("--version", "-v"):
        return {"say": version(), "code": 0}
    # A client command's own --help is cli.py's, which knows its options; init has its own.
    if command != "init" and not client and ("--help" in argv or "-h" in argv):
        return {"say": usage(), "code": 0}
    if not client and command not in SERVERS:
        return {"say": f'{prog()}: no command "{command}"\n\n{usage()}', "code": 2, "stderr": True}
    if client:                                         # cli.py takes the command itself
        return {"script": "cli.py", "args": argv, "cwd": os.getcwd(), "first_run": False}
    # The first run makes the data folder; not for mcp, whose stdout carries protocol messages only.
    return {"script": SERVERS[command], "args": argv[1:] if command == first else argv, "cwd": str(ROOT),
            "first_run": command not in ("init", "mcp")}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    p = plan(argv)
    if "say" in p:
        print(p["say"], file=sys.stderr if p.get("stderr") else sys.stdout)
        return p["code"]
    home = Path(os.environ.get("BENCHWARMERS_HOME", "").strip() or default_home()).expanduser()
    # No bytecode beside the code: an installed folder may not be writable, and
    # an upgrade replaces it anyway.
    env = {**os.environ, "BENCHWARMERS_HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1",
           "BENCHWARMERS_LAUNCHER": os.environ.get("BENCHWARMERS_LAUNCHER") or "pip"}
    if p["first_run"] and not home.exists():
        print(f"First run: making your data folder at {home}")
        done = subprocess.run([sys.executable, str(ROOT / SERVERS["init"]), str(home)], env=env, cwd=ROOT)
        if done.returncode:
            return done.returncode
    child = subprocess.Popen([sys.executable, str(ROOT / p["script"]), *p["args"]], env=env, cwd=p["cwd"])
    while True:
        try:
            return child.wait()
        except KeyboardInterrupt:                      # the child has it too; let it finish stopping
            continue


if __name__ == "__main__":
    sys.exit(main())
