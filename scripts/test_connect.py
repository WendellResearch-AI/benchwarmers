"""Connect, end to end through the dashboard, against a fake provider. No real files.

    python scripts/test_connect.py

metrics/connect.py --selftest covers the flows themselves; this covers what
serve.py does with them: the callback route on the real handler, saving the
endpoint by save_endpoint's rules (an owner's headers kept), the refresh token
beside the key, a device sign-in finished by polling, Ollama's local add,
models on a renewing endpoint routed to the built-in client, and model servers
found on this machine and what they say of their models for the catalog
(local_servers). Endpoints, keys, the change log and connect.json all point at
a temp folder.
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


SHAPES: dict[int, dict] = {}                     # port -> path -> (status, answer): what the server there says


class Server(BaseHTTPRequestHandler):
    """A local model server, answering as SHAPES says the one on its port does."""
    def do_GET(self):                            # noqa: N802
        status, body = SHAPES[self.server.server_port].get(self.path, (404, {"error": "not found"}))
        raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html" if isinstance(body, str) else "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):                           # noqa: N802 -- Ollama's /api/show: one answer whatever is asked
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.do_GET()

    def log_message(self, *_a):
        pass


def local_servers() -> None:
    """Found on this machine (connect.detect): each server told by its answers,
    llama.cpp and LocalAI apart on one port, impostors and a hung port ignored,
    this install's ports and servers already added skipped; then on any port,
    by the process listening there. Fake servers on free ports stand in for the
    real ones, a table of their own for this machine's processes (none at
    first: the usual ports alone); endpoints.json is main()'s temp one."""
    import os
    import socket
    import time

    def listing(*owners):
        return 200, {"object": "list", "data": [{"id": f"m{i}", "object": "model", **({"owned_by": o} if o else {})}
                                                for i, o in enumerate(owners)]}
    refused, version = (401, {"error": "Missing API key"}), (200, {"version": "1.0"})
    shapes = {
        "ollama": {"/v1/models": listing("library"), "/api/version": version, "/api/show": (200, {
            "capabilities": ["completion", "tools"], "model_info": {
                "general.architecture": "qwen35", "qwen35.context_length": 262144,
                "qwen35.rope.scaling.original_context_length": 16384}})},
        "lmstudio": {"/v1/models": listing(None, None, None), "/api/v1/models": (200, {"models": [
            {"key": "pub/m0", "max_context_length": 32768, "variants": ["pub/m0@q4_k_m"],
             "capabilities": {"vision": True, "trained_for_tool_use": False},
             "loaded_instances": [{"id": "pub/m0:2", "config": {"context_length": 4096}}]},
            {"key": "m1", "max_context_length": 8192, "loaded_instances": [],
             "capabilities": {"vision": False, "trained_for_tool_use": True, "reasoning": {"default": "on"}}}]})},
        "jan": {"/v1/models": refused},
        "llamacpp": {"/v1/models": listing("llamacpp")},
        "localai": {"/v1/models": listing(None, None), "/version": version},
        "vllm": {"/v1/models": listing("vllm"), "/version": version},
        "litellm": {"/v1/models": refused, "/health/liveliness": (200, "I'm alive!")},
        "cliproxy": {"/v1/models": refused, "/": (200, {"message": "CLI Proxy API Server", "endpoints": []})},
        "omniroute": {"/v1/models": listing("a", "b")},
    }
    servers = {sid: serve_on(Server) for sid in shapes if sid != "localai"}
    port = {sid: s.server_port for sid, s in servers.items()}
    port["localai"] = shared = port["llamacpp"]  # as 8080 is both
    for sid, shape in shapes.items():
        if sid != "localai":
            SHAPES[port[sid]] = shape
    hung = socket.socket()                       # takes the connection, never answers
    hung.bind(("127.0.0.1", 0))
    hung.listen()
    saved = (dict(connect.LOCAL), connect.listeners, os.environ.pop("OLLAMA_HOST", None))
    connect.listeners = lambda: []
    try:
        for sid in shapes:
            connect.LOCAL[sid] = {**saved[0][sid], "ports": (port[sid],)}
        connect.LOCAL["hung"] = {"label": "Hung", "ports": (hung.getsockname()[1],), "kind": "local", "docs": "https://x"}

        def found(**kw) -> dict:
            return {f["id"]: f for f in connect.detect(**kw)}

        start = time.time()
        got = found()
        # 0.5 s per probe: a hung port that held it up would take far longer; CI runners are slow.
        assert time.time() - start < 5, "a hung port held detection up"
        assert set(got) == set(shapes) - {"localai"}, got           # 8080 answering as llama.cpp
        assert got["llamacpp"]["port"] == shared and got["lmstudio"]["models"] == 3, got
        for sid in ("jan", "litellm", "cliproxy"):
            assert got[sid]["key"] and got[sid]["models"] is None, got[sid]
        assert (got["litellm"]["kind"], got["litellm"]["hub"]) == ("hub", "litellm")
        assert (got["cliproxy"]["kind"], got["cliproxy"]["hub"]) == ("hub", "cliproxy")
        assert got["omniroute"]["kind"] == "aggregator" and got["vllm"]["kind"] == "local"
        assert got["vllm"]["base"] == f"http://127.0.0.1:{port['vllm']}/v1"
        assert got["ollama"]["provider"] == "ollama" and got["ollama"]["base"] == connect.PROVIDERS["ollama"]["base"]
        SHAPES[shared] = shapes["localai"]                          # the same port, answering as LocalAI
        assert "localai" in found() and "llamacpp" not in found()
        # Something else on a server's port: a web page, an API without the server's sign.
        SHAPES[shared] = {"/v1/models": (200, "<html>a web app</html>")}
        SHAPES[port["vllm"]] = {"/v1/models": listing(None)}
        assert not {"localai", "llamacpp", "vllm"} & set(found())
        # This install's own port, and servers already endpoints (by localhost, or by this machine's name).
        assert "omniroute" not in found(skip={port["omniroute"]})
        connect.locked.update(connect.endpoints.FILE, lambda d: d.update(
            mine={"base": f"http://localhost:{port['lmstudio']}/v1", "key": "mine"},
            lan={"base": f"http://{socket.gethostname()}:{port['jan']}/v1", "key": "lan"}))
        listed = {f["id"]: f for f in serve.connect_local()["found"]}
        assert not {"lmstudio", "jan"} & set(listed) and "ollama" in listed, listed
        assert listed["ollama"]["name"] == "ollama" and listed["litellm"]["name"] == "litellm", listed
        # What each says of its own models, for the catalog: LM Studio's listing by every id a
        # request can name, Ollama's /api/show a model at a time, and nothing from anything else.
        import catalog
        connect.locked.update(connect.endpoints.FILE, lambda d: d.update(
            olla={"base": f"http://127.0.0.1:{port['ollama']}/v1", "key": "olla"},
            vl={"base": f"http://127.0.0.1:{port['vllm']}/v1/", "key": "vl"}))
        said, facts = catalog._native("mine", ["ignored"])
        seen = {"context_window": 4096, "vision": True, "tools": False, "reasoning": False}
        assert said == "lmstudio /api/v1/models" and facts["pub/m0"] == facts["pub/m0@q4_k_m"] == \
            facts["pub/m0:2"] == seen, facts
        assert facts["m1"] == {"context_window": 8192, "vision": False, "tools": True, "reasoning": True}, facts
        assert catalog._native("olla", ["q:2b"]) == ("ollama /api/show", {"q:2b": {
            "context_window": 262144, "vision": False, "tools": True, "reasoning": False}})
        assert catalog._native("vl", ["m0"]) == ("", {})
        # On any port, by the process listening there: LM Studio by its name, vLLM by its command
        # line under Python, Ollama by OLLAMA_HOST (added at its real port, so not by its own
        # flow, which knows only 11434); a port whose process is none of them is never asked.
        extra = {sid: serve_on(Server) for sid in ("lmstudio", "vllm", "ollama", "other")}
        servers.update({sid + "-moved": s for sid, s in extra.items()})
        at = {sid: s.server_port for sid, s in extra.items()}
        for sid in at:
            SHAPES[at[sid]] = shapes["vllm" if sid == "other" else sid]     # "other" answers as vLLM too
        connect.listeners = lambda: [(1, at["lmstudio"], "LM Studio.exe", ""),
                                     (2, at["vllm"], "python3", "/opt/venv/bin/python3 /opt/venv/bin/vllm serve m"),
                                     (3, at["other"], "notepad.exe", "notepad.exe a.txt")]
        os.environ["OLLAMA_HOST"] = f"0.0.0.0:{at['ollama']}"
        moved = {(f["id"], f["port"]): f for f in connect.detect()}
        assert {("lmstudio", at["lmstudio"]), ("vllm", at["vllm"]), ("ollama", at["ollama"])} <= set(moved), moved
        assert at["other"] not in {p for _, p in moved}, moved
        ollama = moved[("ollama", at["ollama"])]
        assert not ollama["provider"] and ollama["base"] == f"http://127.0.0.1:{at['ollama']}/v1", ollama
    finally:
        connect.LOCAL.clear()
        connect.LOCAL.update(saved[0])
        connect.listeners = saved[1]
        os.environ.pop("OLLAMA_HOST", None)
        if saved[2] is not None:
            os.environ["OLLAMA_HOST"] = saved[2]
        hung.close()
        for s in servers.values():
            s.shutdown()


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

            local_servers()
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
