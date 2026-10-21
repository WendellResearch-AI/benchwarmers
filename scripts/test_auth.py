"""Login, gateway keys and one port, through the real handlers. No real files.

    python scripts/test_auth.py

metrics/guard.py's self-check covers the pieces (the cookie, key hashing,
the startup refusal); this covers what the servers do with them, route by
route: the dashboard behind the login cookie, the gateway's routes behind a
gateway key on both ports, the open routes (health, login, Connect's
callback), no auth at all without an admin token, the access log, and both
servers refusing to listen beyond this machine without a token. Keys, the
change log and collections point at a temp folder.
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import serve  # noqa: E402
import gateway  # noqa: E402
import guard  # noqa: E402

TOKEN = "test-admin-token-0123456789"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def serve_on(handler) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def call(port: int, path: str, body: dict | None = None, **headers) -> tuple[int, dict, str]:
    """(status, headers, body text) for one request; HTTP errors are answers too."""
    headers = {"Host": f"127.0.0.1:{port}", **headers}
    data = None
    if body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data, headers)
    try:
        with OPENER.open(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode()


def refuses_to_start(script: str) -> str:
    """The startup message when `script` is told to listen beyond this machine without a token."""
    env = {k: v for k, v in os.environ.items() if k != "BENCHWARMERS_ADMIN_TOKEN"}
    env.update(BENCH_BIND="0.0.0.0", BENCH_PORT="8199", BENCH_GATEWAY_PORT="8198", PYTHONIOENCODING="utf-8")
    done = subprocess.run([sys.executable, script], cwd=ROOT, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)
    assert done.returncode != 0, f"{script} started on 0.0.0.0 without a token"
    return done.stderr


def main() -> int:
    for script in ("serve.py", "gateway.py"):
        assert "BENCHWARMERS_ADMIN_TOKEN" in refuses_to_start(script), script

    dash, gw = serve_on(serve.Handler), serve_on(gateway.Handler)
    d, g = dash.server_port, gw.server_port
    saved = (guard.KEYS, serve.CHANGES_PATH, serve.PORT, gateway.bench_collections.FILE,
             os.environ.pop("BENCHWARMERS_ADMIN_TOKEN", None), sys.stderr)
    with tempfile.TemporaryDirectory() as tmp:
        t = pathlib.Path(tmp)
        try:
            guard.KEYS, serve.CHANGES_PATH, serve.PORT = t / "gateway-keys.json", t / "changes.jsonl", d
            gateway.bench_collections.FILE = t / "collections.json"
            gateway.bench_collections.FILE.write_text(json.dumps({"core": {"members": [], "description": "x"}}),
                                                      encoding="utf-8")

            # NO TOKEN: everything as before -- no login, no key, any bearer ignored.
            assert call(d, "/api/stamp")[0] == 200
            assert call(d, "/login")[:2][0] == 303
            for port in (d, g):
                status, _, text = call(port, "/v1/models", Authorization="Bearer sk-anything")
                assert status == 200 and "bench/core" in text, (port, status, text)

            # TOKEN SET: the dashboard asks for the login cookie...
            os.environ["BENCHWARMERS_ADMIN_TOKEN"] = TOKEN
            status, headers, _ = call(d, "/")
            assert status == 303 and headers["Location"] == "/login", (status, headers)
            assert call(d, "/api/stamp")[0] == 401
            assert call(d, "/api/theme", {"theme": "nope"})[0] == 401             # writes too
            # Not even which routes exist: the Host, then the login, then the 404.
            assert call(d, "/api/nope", {}, Host="evil.example")[0] == 403
            assert call(d, "/api/nope", {})[0] == 401
            assert call(d, "/fonts/chivo-latin-400-normal.woff2")[0] == 303
            status, _, page = call(d, "/login")
            assert status == 200 and "Admin token" in page
            # ...which only the admin token gets, through the same guard as any write.
            assert call(d, "/login", {"token": "wrong"})[0] == 401
            assert call(d, "/login", {"token": TOKEN}, Origin="http://evil.example")[0] == 403
            status, headers, _ = call(d, "/login", {"token": TOKEN})
            cookie = headers.get("Set-Cookie", "")
            assert status == 200 and "HttpOnly" in cookie and "SameSite=Strict" in cookie, headers
            assert TOKEN not in cookie
            jar = {"Cookie": cookie.split(";", 1)[0]}
            assert call(d, "/api/stamp", **jar)[0] == 200
            assert call(d, "/api/theme", {"theme": "nope"}, **jar)[0] == 400      # in, then the theme rule
            assert call(d, "/api/nope", {}, **jar)[0] == 404

            # Open without either: health (any Host), and Connect's callback.
            assert call(d, "/health", Host="platform.internal:9")[:3:2] == (200, '{"ok": true}')
            status, _, page = call(d, "/connect/callback?state=nope&code=x")
            assert status == 200 and "unknown or has expired" in page, page
            # ...but not a path merely beginning like it.
            assert call(d, "/connect/callbackx")[0] == 303

            # Gateway keys, made from the dashboard and shown once.
            assert call(d, "/api/gateway-key", {"name": "chat-ui"})[0] == 401
            status, _, text = call(d, "/api/gateway-key", {"name": "chat-ui"}, **jar)
            key = json.loads(text)["key"]
            assert status == 200 and key not in guard.KEYS.read_text(encoding="utf-8")
            assert key not in serve.CHANGES_PATH.read_text(encoding="utf-8")
            assert key not in serve.settings_page() and "chat-ui" in serve.settings_page()
            bearer = {"Authorization": f"Bearer {key}"}

            # The gateway's routes, on both ports: the key, never the cookie.
            for port in (d, g):
                assert call(port, "/v1/models")[0] == 401, port
                assert call(port, "/v1/models", **jar)[0] == 401, port
                assert call(port, "/v1/models", Authorization="Bearer sk-dummy")[0] == 401, port
                status, _, text = call(port, "/v1/models", **bearer)
                assert status == 200 and json.loads(text)["data"][0]["id"] == "bench/core", (port, text)
                # One port: /bench/verdict reaches the gateway's own validation.
                status, _, text = call(port, "/bench/verdict", {"call": "x"}, **bearer)
                assert status == 400 and "useful" in json.loads(text)["error"]["message"], (port, text)
                assert call(port, "/v1/models", Host="evil.example", **bearer)[0] == 403, port   # Host still checked
            # A key never opens the dashboard.
            assert call(d, "/api/stamp", **bearer)[0] == 401
            assert call(d, "/", **bearer)[0] == 303

            # The access log: route, status, who; never a key, a query or a callback's code.
            log = io.StringIO()
            sys.stderr = log
            call(d, "/v1/models", **bearer)
            call(d, "/api/stamp", **jar)
            call(d, "/connect/callback?state=nope&code=secret-code")
            call(d, "/connect/callback/sid-in-path?code=secret-code")
            sys.stderr = saved[-1]
            lines = log.getvalue()
            assert "GET /v1/models 200 chat-ui" in lines and "GET /api/stamp 200 admin" in lines, lines
            assert key not in lines and "secret-code" not in lines and "sid-in-path" not in lines, lines

            # Revoked: refused at once.
            assert call(d, "/api/gateway-key/remove", {"name": "chat-ui"}, **jar)[0] == 200
            assert call(d, "/v1/models", **bearer)[0] == 401
            assert call(d, "/api/gateway-key/remove", {"name": "chat-ui"}, **jar)[0] == 400
        finally:
            (guard.KEYS, serve.CHANGES_PATH, serve.PORT, gateway.bench_collections.FILE) = saved[:4]
            os.environ.pop("BENCHWARMERS_ADMIN_TOKEN", None)
            if saved[4] is not None:
                os.environ["BENCHWARMERS_ADMIN_TOKEN"] = saved[4]
            sys.stderr = saved[5]
            dash.shutdown()
            gw.shutdown()
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
