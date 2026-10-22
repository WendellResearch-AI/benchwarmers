"""The MCP server (mcp_server.py) as a client starts it: a subprocess on stdio, against a stub server.

    python scripts/test_mcp.py

initialize and its protocol version negotiation, ping, tools/list, a call of
each tool (what reaches the server, gateway key included, and what comes
back), a refusal passed through as a tool error, a ping answered while a
panel is still running, protocol errors, and the error when no Benchwarmers
is running. Nothing but protocol messages may reach stdout.
"""

from __future__ import annotations

import json
import os
import pathlib
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
SEEN: list[tuple[str, str, dict, str]] = []          # (method, path, body, Authorization)

MODELS = {"object": "list", "data": [
    {"id": "bench/core", "description": "second opinions", "tags": ["general"], "members": ["m1", "m2"],
     "explorers": 2, "shape": "", "reply": "all", "merger": "",
     "together": {"context_window": 128000, "capabilities": ["tools"], "connections": ["x"], "limits": {}}},
    {"id": "bench/core:merged", "description": "second opinions", "reply": "merged"}]}
SEATS = [{"call": "p1.member1", "seat": "member", "alias": "m1", "status": "ok", "seconds": 1.0,
          "answer": "answer one", "error": ""},
         {"call": "p1.explorer", "seat": "explorer", "alias": "x1", "status": "empty", "seconds": 2.0,
          "answer": "", "error": ""}]


class Stub(BaseHTTPRequestHandler):
    def do_GET(self):                                  # noqa: N802
        SEEN.append(("GET", self.path, {}, self.headers.get("Authorization", "")))
        self._send(200, MODELS)

    def do_POST(self):                                 # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append(("POST", self.path, body, self.headers.get("Authorization", "")))
        if self.path == "/bench/verdict":
            return self._send(200, {"ok": True, "recorded": {"call": body["call"]}})
        if body["model"] == "bench/nope":
            return self._send(404, {"error": {"message": "no collection nope; choose one of bench/core"}})
        time.sleep(1)                                  # a panel takes a while
        merged = (body.get("bench") or {}).get("reply") == "merged"
        self._send(200, {"choices": [{"message": {"role": "assistant", "content": "merged answer" if merged
                                                  else "## member — m1\nanswer one"}}],
                         "bench": {"panel": "p1", "collection": body["model"], "shape": "general",
                                   "seats": SEATS, "reply": "merged" if merged else "all"}})

    def _send(self, status, data):
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_a):
        pass


class Client:
    def __init__(self, url: str, key: str = ""):
        env = {**os.environ, "BENCHWARMERS_URL": url, "BENCHWARMERS_KEY": key, "BENCHWARMERS_TIMEOUT": "30"}
        self.p = subprocess.Popen([sys.executable, str(ROOT / "mcp_server.py")], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        self.next = 0

    def send(self, msg) -> None:
        self.p.stdin.write((msg if isinstance(msg, str) else json.dumps(msg)).encode() + b"\n")
        self.p.stdin.flush()

    def read(self) -> dict:
        line = self.p.stdout.readline()
        assert line, "the server closed stdout: " + self.p.stderr.read().decode()
        return json.loads(line)                        # every stdout line must be one JSON-RPC message

    def request(self, method: str, params: dict | None = None) -> dict:
        self.next += 1
        self.send({"jsonrpc": "2.0", "id": self.next, "method": method, **({"params": params} if params else {})})
        reply = self.read()
        assert reply["id"] == self.next and reply["jsonrpc"] == "2.0", reply
        return reply

    def tool(self, name: str, **arguments) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments})["result"]

    def close(self) -> None:
        self.p.stdin.close()
        self.p.wait(timeout=10)


def main() -> int:
    stub = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{stub.server_port}"
    c = Client(url + "/v1", key="bwk_test")            # a pasted /v1 is fine

    init = c.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "test", "version": "0"}})["result"]
    assert init["protocolVersion"] == "2025-06-18", init
    assert init["capabilities"] == {"tools": {"listChanged": False}}, init
    assert init["serverInfo"]["name"] == "benchwarmers" and "verdict" in init["instructions"], init
    c.send({"jsonrpc": "2.0", "method": "notifications/initialized"})     # no answer to a notification
    assert c.request("ping")["result"] == {}

    tools = {t["name"]: t for t in c.request("tools/list")["result"]["tools"]}
    assert set(tools) == {"list_collections", "ask", "verdict"}, tools
    assert tools["ask"]["inputSchema"]["required"] == ["collection", "prompt"]

    r = c.tool("list_collections")
    assert not r["isError"], r
    rows = json.loads(r["content"][0]["text"])
    assert [x["name"] for x in rows] == ["bench/core"], rows        # the :merged twin is folded in
    assert rows[0]["context_window"] == 128000 and rows[0]["members"] == ["m1", "m2"], rows
    assert SEEN[-1][:2] == ("GET", "/v1/models") and SEEN[-1][3] == "Bearer bwk_test", SEEN[-1]

    # A ping sent while the panel runs is answered first.
    c.send({"jsonrpc": "2.0", "id": "a", "method": "tools/call",
            "params": {"name": "ask", "arguments": {"collection": "core", "prompt": "hi", "system": "be brief",
                                                    "with": ["x1"]}}})
    c.send({"jsonrpc": "2.0", "id": "p", "method": "ping"})
    first, second = c.read(), c.read()
    assert (first["id"], second["id"]) == ("p", "a"), (first, second)
    r = second["result"]
    assert not r["isError"] and "answer one" in r["content"][0]["text"], r
    seats = json.loads(r["content"][1]["text"].split("\n", 1)[1])["seats"]
    assert [s["call"] for s in seats] == ["p1.member1", "p1.explorer"] and "answer" not in seats[0], seats
    method, path, body, _ = SEEN[-1]
    assert (method, path) == ("POST", "/v1/chat/completions"), SEEN[-1]
    assert body["model"] == "bench/core" and body["with"] == ["x1"] and "bench" not in body, body
    assert body["messages"] == [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}]

    r = c.tool("ask", collection="bench/core", prompt="hi", merged=True)
    assert SEEN[-1][2]["bench"] == {"reply": "merged"}, SEEN[-1]
    assert r["content"][0]["text"] == "merged answer" and "answer one" in r["content"][1]["text"], r

    r = c.tool("ask", collection="nope", prompt="hi")              # the server's refusal, passed on
    assert r["isError"] and "(404)" in r["content"][0]["text"] and "choose one" in r["content"][0]["text"], r
    r = c.tool("ask", collection="core")
    assert r["isError"] and "prompt" in r["content"][0]["text"], r

    r = c.tool("verdict", call="p1.member1", useful=True, note="checked", real=["a true claim"], false=["x"])
    assert not r["isError"] and "1 real, 1 false" in r["content"][0]["text"], r
    assert SEEN[-1][1:3] == ("/bench/verdict", {"call": "p1.member1", "useful": True, "note": "checked",
                                                "real": ["a true claim"], "false": ["x"]}), SEEN[-1]
    assert c.tool("verdict", call="p1.member1")["isError"]          # useful is required

    assert c.request("tools/call", {"name": "nope"})["error"]["code"] == -32602
    assert c.request("resources/list")["error"]["code"] == -32601
    c.send("{not json")
    assert c.read()["error"]["code"] == -32700
    c.close()

    # An unknown protocol version gets the newest; no server running is a clear tool error.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        dead = s.getsockname()[1]
    c = Client(f"http://127.0.0.1:{dead}")
    init = c.request("initialize", {"protocolVersion": "1999-01-01", "capabilities": {}})["result"]
    assert init["protocolVersion"] == "2025-11-25", init
    r = c.tool("list_collections")
    assert r["isError"] and "not running" in r["content"][0]["text"], r
    c.close()
    stub.shutdown()
    print("mcp server ok: initialize, ping, tools/list, each tool, refusals, a ping mid-panel, not running")
    return 0


if __name__ == "__main__":
    sys.exit(main())
