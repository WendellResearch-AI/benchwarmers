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


def call(port: int, path: str, body: dict | None = None, method: str | None = None,
         **headers) -> tuple[int, dict, str]:
    """(status, headers, body text) for one request; HTTP errors are answers too."""
    headers = {"Host": f"127.0.0.1:{port}", **headers}
    data = None
    if body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data, headers, method=method)
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
             os.environ.pop("BENCHWARMERS_ADMIN_TOKEN", None), sys.stderr, gateway.run_collection,
             os.environ.pop("BENCHWARMERS_REQUIRE_KEYS", None), os.environ.pop("BENCH_ALLOWED_ORIGINS", None))
    with tempfile.TemporaryDirectory() as tmp:
        t = pathlib.Path(tmp)
        try:
            guard.KEYS, serve.CHANGES_PATH, serve.PORT = t / "gateway-keys.json", t / "changes.jsonl", d
            gateway.bench_collections.FILE = t / "collections.json"
            gateway.bench_collections.FILE.write_text(json.dumps({"core": {"members": [], "description": "x"},
                                                                  "vision": {"members": [], "description": "x"}}),
                                                      encoding="utf-8")
            # The panel is test_merged's and test_vision's; here only who asked, and whether it ran.
            ran = []
            gateway.run_collection = lambda name, messages, nominated, reply, who: (
                ran.append((name, who)) or "ok", {"seats": []})

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
                status, headers, _ = call(port, "/v1/models")
                assert status == 401 and headers["WWW-Authenticate"].startswith("Bearer"), (port, headers)
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
            sys.stderr = saved[5]
            lines = log.getvalue()
            assert "GET /v1/models 200 chat-ui" in lines and "GET /api/stamp 200 admin" in lines, lines
            assert key not in lines and "secret-code" not in lines and "sid-in-path" not in lines, lines

            # A key limited to collections and a rate, made on Settings: it sees and calls only
            # those, its name rides on the call, and past its cap it waits.
            status, _, text = call(d, "/api/gateway-key", {"name": "estate", "collections": ["vision"], "rpm": "1"},
                                   **jar)
            assert status == 200, text
            estate = {"Authorization": f"Bearer {json.loads(text)['key']}"}
            assert call(d, "/api/gateway-key", {"name": "other", "collections": ["nope"]}, **jar)[0] == 400
            assert call(d, "/api/gateway-key", {"name": "other", "rpm": "lots"}, **jar)[0] == 400
            status, _, text = call(g, "/v1/models", **estate)
            assert [m["id"] for m in json.loads(text)["data"]] == ["bench/vision", "bench/vision:merged",
                                                                   "bench/vision:route"], text
            ask = {"messages": [{"role": "user", "content": "hi"}]}
            status, _, text = call(g, "/v1/chat/completions", {"model": "bench/core", **ask}, **estate)
            assert status == 403 and "may call bench/vision" in text, text
            assert call(g, "/v1/chat/completions", {"model": "bench/vision", **ask}, **estate)[0] == 200
            assert call(g, "/v1/chat/completions", {"model": "bench/core", **ask}, **bearer)[0] == 200
            assert ran == [("vision", "estate"), ("core", "chat-ui")], ran
            status, headers, _ = call(g, "/v1/chat/completions", {"model": "bench/vision", **ask}, **estate)
            assert status == 429 and int(headers["Retry-After"]) >= 1, (status, headers)
            page = serve.settings_page()
            assert "estate" in page and "bwk_…" in page and ">vision<" in page, page

            # CORS for a browser app on an allowed origin, on the gateway's routes only.
            os.environ["BENCH_ALLOWED_ORIGINS"] = "https://estate.example"
            app, evil = {"Origin": "https://estate.example"}, {"Origin": "https://evil.example"}
            asks = {"Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "authorization, content-type, x-stainless-os"}
            for port in (d, g):
                status, headers, _ = call(port, "/v1/chat/completions", method="OPTIONS", **app, **asks)
                assert status == 204 and headers["Access-Control-Allow-Origin"] == "https://estate.example", headers
                assert "Authorization" in headers["Access-Control-Allow-Headers"] and headers["Vary"] == "Origin"
                assert "x-stainless-os" in headers["Access-Control-Allow-Headers"]      # the OpenAI JS SDK's
                assert "Access-Control-Allow-Credentials" not in headers
                status, headers, _ = call(port, "/v1/models", method="OPTIONS", **app,
                                          **{"Access-Control-Request-Headers": "x-ok; not a name"})
                assert status == 204 and headers["Access-Control-Allow-Headers"] == "Authorization, Content-Type"
                status, headers, _ = call(port, "/v1/chat/completions", method="OPTIONS", **evil, **asks)
                assert status == 403 and "Access-Control-Allow-Origin" not in headers, (port, headers)
                status, headers, _ = call(port, "/v1/models", **bearer, **app)
                assert status == 200 and headers["Access-Control-Allow-Origin"] == "https://estate.example"
                assert "Access-Control-Allow-Origin" not in call(port, "/v1/models", **bearer, **evil)[1]
                # The app reads a refusal too; cross-site is fine for it, for nobody else.
                assert call(port, "/v1/models", **app)[1]["Access-Control-Allow-Origin"] == "https://estate.example"
                status, _, text = call(port, "/bench/verdict", {"call": "x"}, **bearer, **app,
                                       **{"Sec-Fetch-Site": "cross-site"})
                assert status == 400 and "useful" in text, (port, text)
                assert call(port, "/bench/verdict", {"call": "x"}, **bearer, **evil,
                            **{"Sec-Fetch-Site": "cross-site"})[0] == 403
            # The dashboard: no preflight, no CORS, same-origin as before.
            assert call(d, "/api/stamp", method="OPTIONS", **app, **asks)[0] == 501
            status, headers, _ = call(d, "/api/theme", {"theme": "nope"}, **jar, **app, **{"Sec-Fetch-Site": "cross-site"})
            assert status == 403 and "Access-Control-Allow-Origin" not in headers, headers
            assert "Access-Control-Allow-Origin" not in call(d, "/api/stamp", **jar, **app)[1]
            del os.environ["BENCH_ALLOWED_ORIGINS"]

            # Revoked: refused at once.
            assert call(d, "/api/gateway-key/remove", {"name": "chat-ui"}, **jar)[0] == 200
            assert call(d, "/v1/models", **bearer)[0] == 401
            assert call(d, "/api/gateway-key/remove", {"name": "chat-ui"}, **jar)[0] == 400

            # No admin token, keys required on this machine: the gateway asks, the dashboard does not.
            del os.environ["BENCHWARMERS_ADMIN_TOKEN"]
            os.environ["BENCHWARMERS_REQUIRE_KEYS"] = "1"
            for port in (d, g):
                assert call(port, "/v1/models", Authorization="Bearer sk-anything")[0] == 401, port
                assert call(port, "/v1/models", **estate)[0] == 200, port
            assert call(d, "/api/stamp")[0] == 200
        finally:
            (guard.KEYS, serve.CHANGES_PATH, serve.PORT, gateway.bench_collections.FILE) = saved[:4]
            gateway.run_collection = saved[6]
            for name, value in (("BENCHWARMERS_ADMIN_TOKEN", saved[4]), ("BENCHWARMERS_REQUIRE_KEYS", saved[7]),
                                ("BENCH_ALLOWED_ORIGINS", saved[8])):
                os.environ.pop(name, None)
                if value is not None:
                    os.environ[name] = value
            sys.stderr = saved[5]
            dash.shutdown()
            gw.shutdown()
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
