"""The Docker image's entry point: a hosting platform's conventions, then serve.py.

    python deploy/container.py               # what the image runs (Dockerfile CMD)
    python deploy/container.py --selftest

serve.py knows nothing about hosting platforms. This does three things for it,
and only in the image (HOSTING.md has the platforms):

- PORT. Railway and Render tell an app which port to listen on in PORT. When
  it is set, it becomes BENCH_PORT. Not in serve.py itself: a PORT left in a
  developer's shell would move a local dashboard off 8122 without a word.
- The public address. Each platform says where it serves the app (Fly:
  FLY_APP_NAME; Railway: RAILWAY_PUBLIC_DOMAIN; Render: RENDER_EXTERNAL_URL).
  When BENCHWARMERS_PUBLIC_URL is not set it is taken from these, so the
  platform's own host name and origin are accepted and the login cookie is
  Secure. A custom domain still needs BENCHWARMERS_PUBLIC_URL.
- The data folder's owner. A platform's volume is mounted owned by root,
  which the app's user (uid 1000) cannot write to. Started as root, this hands
  /data to the app's user and drops to that user before serve.py starts, so
  no request is ever served as root. Started as the app's user already
  (`docker run --user 1000`), it changes nothing.
"""

from __future__ import annotations

import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
USER = "bench"


def platform_env(env: dict) -> dict:
    """The variables to set, from what the platform put in `env`. Never
    overrides one the operator set, except BENCH_PORT, where the platform's
    PORT is the port its proxy sends to."""
    out = {}
    if re.fullmatch(r"[0-9]{1,5}", env.get("PORT", "").strip()):
        out["BENCH_PORT"] = env["PORT"].strip()
    if not env.get("BENCHWARMERS_PUBLIC_URL", "").strip():
        if url := env.get("RENDER_EXTERNAL_URL", "").strip():
            out["BENCHWARMERS_PUBLIC_URL"] = url
        elif domain := env.get("RAILWAY_PUBLIC_DOMAIN", "").strip():
            out["BENCHWARMERS_PUBLIC_URL"] = f"https://{domain}"
        elif app := env.get("FLY_APP_NAME", "").strip():
            out["BENCHWARMERS_PUBLIC_URL"] = f"https://{app}.fly.dev"
    return out


def hand_over(data: pathlib.Path) -> None:
    """As root: give `data` to the app's user, then become that user."""
    import pwd                                         # Unix only, and only the image gets here
    user = pwd.getpwnam(USER)
    for top, dirs, files in os.walk(data):
        dirs[:] = [d for d in dirs if d != "lost+found"]   # the disk's own, root's
        for p in [top, *(os.path.join(top, f) for f in files)]:
            if os.lstat(p).st_uid != user.pw_uid:
                os.lchown(p, user.pw_uid, user.pw_gid)
    os.setgroups([])
    os.setgid(user.pw_gid)
    os.setuid(user.pw_uid)
    os.environ["HOME"] = user.pw_dir


def main() -> None:
    changes = platform_env(dict(os.environ))
    for name, value in changes.items():
        print(f"container: {name}={value} (from the platform)", flush=True)
    os.environ.update(changes)
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        hand_over(pathlib.Path(os.environ.get("BENCHWARMERS_HOME") or "/data"))
    serve = str(ROOT / "serve.py")
    os.execv(sys.executable, [sys.executable, serve])


def selftest() -> None:
    assert platform_env({}) == {}
    assert platform_env({"PORT": "10000"}) == {"BENCH_PORT": "10000"}
    assert platform_env({"PORT": "tcp://10.0.0.2:80"}) == {}          # an old docker --link variable
    assert platform_env({"FLY_APP_NAME": "my-bench"}) == {"BENCHWARMERS_PUBLIC_URL": "https://my-bench.fly.dev"}
    assert platform_env({"RAILWAY_PUBLIC_DOMAIN": "b.up.railway.app", "PORT": "8080"}) == \
        {"BENCH_PORT": "8080", "BENCHWARMERS_PUBLIC_URL": "https://b.up.railway.app"}
    assert platform_env({"RENDER_EXTERNAL_URL": "https://b.onrender.com"}) == \
        {"BENCHWARMERS_PUBLIC_URL": "https://b.onrender.com"}
    # the operator's own address always wins
    assert platform_env({"FLY_APP_NAME": "x", "BENCHWARMERS_PUBLIC_URL": "https://bench.example.com"}) == {}
    print("container: ok")


if __name__ == "__main__":
    selftest() if "--selftest" in sys.argv else main()
