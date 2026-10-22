"""Set up a data folder for a new install, and point this checkout at it.

    python scripts/init.py                     # the OS's per-user data folder
    python scripts/init.py ~/bench-data        # or one of your choosing
    python scripts/init.py --endpoint openrouter https://openrouter.ai/api/v1 --key

RELEASE-PLAN.md Phase 4, `benchwarmers init`, the small version until the
package exists (Phase 3b brings the command). It:

  1. copies `examples/` into the folder, never over a file already there, so
     running it again on a folder in use changes nothing it holds;
  2. makes the folder's layout (metrics/paths.py LAYOUT);
  3. points this checkout at it (`.benchwarmers-home`), unless
     BENCHWARMERS_HOME is set or the checkout already points elsewhere: then
     it says what to change rather than repoint an install that has data;
  4. with --endpoint NAME BASE_URL, adds a first endpoint by the dashboard's
     own rules (serve.save_endpoint: saved only once BASE_URL/models answers).
     NAME is also the key's name in the llm key store, and a key already
     stored under it is used. --key asks for one, unechoed, and stores it
     there; never a key on the command line, which lands in the shell's
     history. Asked only with --key: on Windows a redirected stdin still
     reads as a terminal, and getpass then waits on the console for ever.

The rest happens in the dashboard (`python serve.py`): sign in with a
provider or paste a key, onboard models, build a collection.
"""

from __future__ import annotations

import argparse
import getpass
import os
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
POINTER = ROOT / ".benchwarmers-home"              # metrics/paths.py reads it


def default_folder() -> pathlib.Path:
    """The OS's per-user data folder (RELEASE-PLAN section 2): never beside the
    code, which a package upgrade replaces."""
    if os.name == "nt":
        return pathlib.Path(os.environ.get("LOCALAPPDATA") or pathlib.Path.home()) / "benchwarmers"
    if sys.platform == "darwin":
        return pathlib.Path.home() / "Library" / "Application Support" / "benchwarmers"
    return pathlib.Path(os.environ.get("XDG_DATA_HOME") or pathlib.Path.home() / ".local" / "share") / "benchwarmers"


def point(folder: pathlib.Path, env: str) -> str:
    """Make this checkout use `folder`, or say why it was left alone."""
    if env:
        if pathlib.Path(env).expanduser().resolve() == folder:
            return "BENCHWARMERS_HOME already names it."
        return f"BENCHWARMERS_HOME is set to {env}, which wins: set it to {folder} to use this folder."
    named = POINTER.read_text(encoding="utf-8").strip() if POINTER.exists() else ""
    if named and pathlib.Path(named).expanduser().resolve() == folder:
        return f"This checkout already points at it ({POINTER.name})."
    if named:
        return (f"This checkout points at {named} ({POINTER.name}) and was left so: to use {folder},"
                f" put that path in {POINTER} or set BENCHWARMERS_HOME to it.")
    if folder == ROOT:
        return "This checkout is its own data folder."
    POINTER.write_text(f"{folder}\n", encoding="utf-8")
    return f"This checkout points at it now ({POINTER.name})."


def run(folder, endpoint: tuple[str, str] | None = None, secret: str = "") -> tuple[bool, list[str]]:
    """(ok, what to tell the user)."""
    folder = pathlib.Path(folder).expanduser().resolve()
    env = os.environ.get("BENCHWARMERS_HOME", "").strip()
    # Before metrics/ is imported: paths.py reads it once, and serve.save_endpoint
    # below must write into this folder, whatever the checkout points at.
    os.environ["BENCHWARMERS_HOME"] = str(folder)
    sys.path[:0] = [str(ROOT), str(ROOT / "metrics")]
    import paths
    copied: list[str] = []

    def copy_new(src, dst):
        if not os.path.exists(dst):
            shutil.copy2(src, dst)
            copied.append(dst)
    shutil.copytree(EXAMPLES, folder, dirs_exist_ok=True, copy_function=copy_new)
    paths.make(folder)
    said = [f"Data folder: {folder} ({len(copied)} starter file(s) copied, nothing overwritten).",
            point(folder, env)]
    ok = True
    if endpoint:
        import serve
        if serve.DATA.resolve() != folder:         # never into another install's data
            return False, said + [f"Endpoint not added: this process already uses {serve.DATA}."]
        name, base = endpoint
        r = serve.save_endpoint(name, base, secret, who="init", new=True)
        ok = r["ok"]
        said.append(f"Endpoint {name} added: it lists {r['served']} models. {r.get('note', '')}".strip() if ok else
                    "Endpoint not added: " + "; ".join(r["errors"]) + ". Add it from the dashboard instead.")
    port = os.environ.get("BENCH_PORT") or 8122
    # From npm (bin/benchwarmers.js) the dashboard starts next by itself.
    start = "the dashboard starts now: open" if os.environ.get("BENCHWARMERS_LAUNCHER") else "python serve.py, then open"
    said.append(f"Next: {start} http://localhost:{port} to "
                + ("onboard models from it." if endpoint and ok else
                   "add an endpoint (sign in with a provider, or paste a key) and onboard models."))
    return ok, said


def main() -> int:
    ap = argparse.ArgumentParser(description="Set up a data folder and point this checkout at it.")
    ap.add_argument("folder", nargs="?", help=f"default: {default_folder()}")
    ap.add_argument("--endpoint", nargs=2, metavar=("NAME", "BASE_URL"),
                    help="add a first OpenAI-compatible endpoint; NAME is also its key's name")
    ap.add_argument("--key", action="store_true", help="ask for the endpoint's API key (unechoed)")
    args = ap.parse_args()
    if args.key and not args.endpoint:
        ap.error("--key goes with --endpoint")
    secret = getpass.getpass(f"API key for {args.endpoint[0]}: ").strip() if args.key else ""
    ok, said = run(args.folder or default_folder(), args.endpoint, secret)
    print("\n".join(said))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
