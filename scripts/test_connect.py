"""Connect, end to end through the dashboard, against a fake provider. No real files.

    python scripts/test_connect.py

metrics/connect.py --selftest covers the flows themselves; this covers what
serve.py does with them: the callback route on the real handler, saving the
endpoint by save_endpoint's rules (an owner's headers kept), the refresh token
beside the key, a device sign-in finished by polling, Ollama's local add, and
models on a renewing endpoint routed to the built-in client. Endpoints, keys,
the change log and connect.json all point at a temp folder.
"""

from __future__ import annotations

import base64
import hashlib
import json
import pathlib
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import serve  # noqa: E402
import client  # noqa: E402
import connect  # noqa: E402

challenges: dict[str, str] = {}


class Provider(BaseHTTPRequestHandler):
    def do_GET(self):                            # noqa: N802
        self._send(200, {"data": [{"id": "m1"}, {"id": "m2"}]} if self.path == "/v1/models" else {})

    def do_POST(self):                           # noqa: N802
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        f = json.loads(raw) if "json" in self.headers.get("Content-Type", "") else dict(urllib.parse.parse_qsl(raw))
        if self.path == "/device":
            return self._send(200, {"device_code": "dc", "user_code": "WXYZ-0000",
                                    "verification_uri": "https://fake/device", "interval": 0})
        if f.get("grant_type", "").endswith("device_code"):
            return self._send(200, {"access_token": "dev-token", "expires_in": 28800})
        verifier = f.get("code_verifier", "")
        if challenges.get(f.get("code", "")) != base64.urlsafe_b64encode(
                hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode():
            return self._send(400, {"error": "invalid_grant"})
        if self.path == "/keys":
            return self._send(200, {"key": "sk-fake"})
        self._send(200, {"access_token": "at", "refresh_token": "rt", "expires_in": 3600})

    def _send(self, status: int, data: dict) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a):
        pass


def serve_on(handler) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main() -> int:
    provider, dash = serve_on(Provider), serve_on(serve.Handler)
    fake = f"http://127.0.0.1:{provider.server_port}"
    saved = (dict(connect.PROVIDERS), connect.CONFIG, serve.PORT, serve.CHANGES_PATH,
             serve.endpoints.FILE, serve.endpoints.YAML, serve.endpoints.LLM_DIR)
    with tempfile.TemporaryDirectory() as tmp:
        t = pathlib.Path(tmp)
        try:
            serve.PORT = dash.server_port
            connect.CONFIG, serve.CHANGES_PATH = t / "connect.json", t / "changes.jsonl"
            serve.endpoints.FILE, serve.endpoints.YAML, serve.endpoints.LLM_DIR = (
                t / "endpoints.json", t / "models.yaml", t)
            # An owner's endpoint with its own attribution headers, which a sign-in must keep.
            serve.endpoints.FILE.write_text(json.dumps({
                "hub": {"base": fake + "/v1", "key": "hub", "default": True},
                "fk": {"base": fake + "/v1", "key": "fk", "headers": {"X-Title": "owner"}}}), encoding="utf-8")
            base = {"base": fake + "/v1", "docs": "https://x"}
            connect.PROVIDERS.update(
                fk={**base, "label": "Key", "flow": "key", "authorize": fake + "/auth", "token": fake + "/keys",
                    "entry": {"headers": {"X-Title": "Benchwarmers"}}},
                fo={**base, "label": "OAuth", "flow": "oauth", "authorize": fake + "/auth", "token": fake + "/token",
                    "client_id": "cid", "entry": {"api": "responses"}},
                fd={**base, "label": "Device", "flow": "device", "device": fake + "/device",
                    "token": fake + "/token", "client_id": "cid"},
                fl={**base, "label": "Local", "flow": "local"})

            def callback(pid: str) -> str:
                r = serve.connect_start(pid)
                assert r["ok"], r
                q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(r["url"]).query))
                back = q.get("callback_url") or q.get("redirect_uri")
                assert f":{dash.server_port}/connect/callback" in back, q
                challenges["code-" + pid] = q["code_challenge"]
                # The key flow is sent back WITHOUT state, as OpenRouter may do:
                # the id in the callback's path is what finds the sign-in.
                url = (back + "?code=code-" + pid) if "callback_url" in q else \
                    back + "?" + urllib.parse.urlencode({"state": q["state"], "code": "code-" + pid})
                url = url.replace("localhost", "127.0.0.1")
                page = urllib.request.urlopen(url, timeout=10).read().decode()
                assert serve.connect_poll(r["id"])["ok"], pid                  # the dashboard tab hears it
                return page

            page = callback("fk")
            assert "Key is connected" in page and "lists 2 models" in page, page
            data, keys = serve.endpoints.load(), serve.endpoints.keys()
            assert data["fk"]["headers"] == {"X-Title": "owner"}, data["fk"]       # not rewritten
            assert keys["fk"] == "sk-fake" and data["fk"]["connect"]["provider"] == "fk", (keys, data)
            assert "sk-fake" not in page and "sk-fake" not in serve.CHANGES_PATH.read_text(encoding="utf-8")

            page = callback("fo")
            data, keys = serve.endpoints.load(), serve.endpoints.keys()
            assert keys["fo"] == "at" and keys["fo.refresh"] == "rt", keys
            assert data["fo"]["connect"]["refreshes"] and data["fo"]["api"] == "responses", data["fo"]
            serve.endpoints.YAML.write_text(serve.endpoints.yaml_block("fo-m1", "m1", "fo")
                                            + serve.endpoints.yaml_block("fk-m1", "m1", "fk"), encoding="utf-8")
            assert client.backend("fo-m1") == "builtin", "a renewing endpoint must not go through llm"
            assert client.backend("fk-m1") == client.backend()                  # a plain key: as configured

            bad = urllib.request.urlopen(f"http://127.0.0.1:{dash.server_port}/connect/callback?state=nope&code=x",
                                         timeout=10).read().decode()
            assert "Not connected" in bad and "unknown or has expired" in bad, bad

            d = serve.connect_start("fd")
            assert d["ok"] and d["code"] == "WXYZ-0000", d
            done = serve.connect_poll(d["id"])
            assert done["ok"] and done["endpoint"] == "fd" and serve.endpoints.keys()["fd"] == "dev-token", done

            local = serve.connect_start("fl")
            assert local["ok"] and serve.endpoints.load()["fl"]["base"] == fake + "/v1", local

            # A key typed over a sign-in replaces it, or the stale refresh would overwrite it.
            assert serve.save_endpoint("fo", fake + "/v1", "pasted")["ok"]
            assert "connect" not in serve.endpoints.load()["fo"] and serve.endpoints.keys()["fo"] == "pasted"
            assert client.backend("fo-m1") == "builtin"                    # still the Responses API
            callback("fo")                                                 # and signed in again

            listed = {p["id"]: p for p in serve.connect_list()["providers"]}
            assert listed["fk"]["endpoint"] == "fk" and not listed["fk"]["missing"], listed["fk"]
            rows = {r["name"]: r for r in serve.endpoint_list()["endpoints"]}
            assert rows["fo"]["connect"]["label"] == "OAuth" and not rows["fo"]["connect"]["expired"], rows["fo"]
            assert rows["hub"]["connect"] is None
        finally:
            connect.PROVIDERS.clear()
            connect.PROVIDERS.update(saved[0])
            (connect.CONFIG, serve.PORT, serve.CHANGES_PATH,
             serve.endpoints.FILE, serve.endpoints.YAML, serve.endpoints.LLM_DIR) = saved[1:]
            provider.shutdown()
            dash.shutdown()
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
