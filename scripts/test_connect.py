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
             serve.endpoints.FILE, serve.endpoints.YAML, serve.endpoints.LLM_DIR, serve.DATA, serve.DISMISSED_PATH)
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
                    "token": fake + "/token", "client_id": "cid", "register": "an app at the fake"},
                fl={**base, "label": "Local", "flow": "local"},
                fc={**base, "label": "Code", "flow": "oauth", "authorize": fake + "/auth", "token": fake + "/token",
                    "device": fake + "/device", "client_id": "cid"})

            def callback(pid: str, endpoint: str = "") -> str:
                r = serve.connect_start(pid, endpoint)
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

            # A provider can be several endpoints: "fk" is taken (the owner's, by
            # key), so a sign-in adds "fk-2" and leaves "fk" exactly as it was.
            before = serve.endpoints.load()["fk"]
            page = callback("fk")
            assert "Key is connected as fk-2" in page and "lists 2 models" in page, page
            data, keys = serve.endpoints.load(), serve.endpoints.keys()
            assert data["fk"] == before and "fk" not in keys, data["fk"]
            assert data["fk-2"]["headers"] == {"X-Title": "Benchwarmers"}, data["fk-2"]
            assert keys["fk-2"] == "sk-fake" and data["fk-2"]["connect"]["provider"] == "fk", (keys, data)
            assert "sk-fake" not in page and "sk-fake" not in serve.CHANGES_PATH.read_text(encoding="utf-8")
            # Signing that one in again keeps its name and its own headers.
            connect.locked.update(serve.endpoints.FILE, lambda d: d["fk-2"].update(headers={"X-Title": "mine"}))
            assert "connected as fk-2" in callback("fk", "fk-2")
            assert serve.endpoints.load()["fk-2"]["headers"] == {"X-Title": "mine"}
            assert not serve.connect_start("fk", "fk")["ok"]                   # never signed in: not its button
            # Add refuses a name in use and offers a free one; Change key edits it.
            refused = serve.save_endpoint("fk", fake + "/v1", "k", new=True)
            assert not refused["ok"] and "fk-3" in refused["errors"][0], refused
            # A sign-in can be given its own name instead of the next number...
            assert "connected as work-or" in callback("fk", "work-or")
            # ...but not one in use, one that is not a name, or a key the store already holds.
            serve.endpoints.set_key("someones", "theirs")
            for name in ("hub", "Bad Name", "someones"):
                assert not serve.connect_start("fk", name)["ok"], name
            assert serve.endpoints.keys()["someones"] == "theirs"
            assert {p["id"]: p for p in serve.connect_list()["providers"]}["fk"]["suggest"] == "fk-3"

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
            assert serve.connect_list_suggest("fl") == "fl"                    # adding it again refreshes it

            # Use your own app: this install's client id instead of the shipped one, and back.
            assert serve.connect_app("fd", "Iv1.own")["ok"]
            row = {p["id"]: p for p in serve.connect_list()["providers"]}["fd"]
            assert row["own_id"] == "Iv1.own" and row["shipped"] and connect.client_id("fd") == "Iv1.own", row
            assert serve.connect_app("fd", "")["ok"] and connect.client_id("fd") == "cid"
            assert not serve.connect_app("fk", "x-id")["ok"]                  # nothing to register for it

            # A key typed over a sign-in replaces it, or the stale refresh would overwrite it.
            assert serve.save_endpoint("fo", fake + "/v1", "pasted")["ok"]
            assert "connect" not in serve.endpoints.load()["fo"] and serve.endpoints.keys()["fo"] == "pasted"
            assert client.backend("fo-m1") == "builtin"                    # still the Responses API
            assert not serve.connect_start("fo", "fo")["ok"]                # no longer a sign-in to renew
            callback("fo")                                                 # a new sign-in: its own endpoint
            assert serve.endpoints.load()["fo-2"]["connect"]["provider"] == "fo"

            listed = {p["id"]: p for p in serve.connect_list()["providers"]}
            assert not listed["fk"]["missing"] and listed["fk"]["flow"] == "key", listed["fk"]
            rows = {r["name"]: r for r in serve.endpoint_list()["endpoints"]}
            assert rows["fo-2"]["connect"]["label"] == "OAuth" and rows["fo-2"]["connect"]["flow"] == "oauth"
            assert not rows["fo-2"]["connect"]["expired"] and rows["hub"]["connect"] is None

            # From a browser on another machine: by code where the provider has a device URL...
            c = serve.connect_start("fc", "", True)
            assert c["ok"] and c["code"] == "WXYZ-0000" and "url" not in c, c
            assert serve.connect_poll(c["id"])["endpoint"] == "fc"
            # ...and refused where it signs in only through a page on this machine.
            assert not serve.connect_start("fk", "", True)["ok"]
            assert {p["id"]: p for p in serve.connect_list()["providers"]}["fc"]["code"]

            # Make default: moved, the default can't be removed, and records keyed by
            # endpoint are renamed with it (bare on the default, served@endpoint elsewhere).
            cfg = t / "config"
            cfg.mkdir()
            serve.DATA, serve.DISMISSED_PATH = t, cfg / "dismissed.json"
            (cfg / "served-snapshot.json").write_text(json.dumps({"last_seen": {
                "m1": "d", "m1@fk-2": "d", "a@b": "d"}, "first_missing": {"m9": "d"}}), encoding="utf-8")
            serve.DISMISSED_PATH.write_text(json.dumps({"m2": {}, "m3@fk-2": {}}), encoding="utf-8")
            assert serve.set_default("fk-2")["ok"] and serve.endpoints.default() == "fk-2"
            snap = json.loads((cfg / "served-snapshot.json").read_text(encoding="utf-8"))
            assert set(snap["last_seen"]) == {"m1@hub", "m1", "a@b@hub"}, snap   # "@b" is not an endpoint
            assert set(snap["first_missing"]) == {"m9@hub"}, snap
            assert set(json.loads(serve.DISMISSED_PATH.read_text(encoding="utf-8"))) == {"m2@hub", "m3"}
            assert not serve.remove_endpoint("fk-2")["ok"] and not serve.set_default("nope")["ok"]
            assert sum(1 for e in serve.endpoints.load().values() if e.get("default")) == 1
        finally:
            connect.PROVIDERS.clear()
            connect.PROVIDERS.update(saved[0])
            (connect.CONFIG, serve.PORT, serve.CHANGES_PATH, serve.endpoints.FILE, serve.endpoints.YAML,
             serve.endpoints.LLM_DIR, serve.DATA, serve.DISMISSED_PATH) = saved[1:]
            provider.shutdown()
            dash.shutdown()
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
