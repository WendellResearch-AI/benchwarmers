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
JUDGED: set[str] = set()                             # call ids the stub has a verdict for

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
            JUDGED.add(body["call"])
            return self._send(200, {"ok": True, "recorded": {"call": body["call"]}})
        if self.path == "/bench/pending":
            return self._send(200, {"pending": [c for c in body["calls"] if c not in JUDGED]})
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
    assert set(tools) == {"list_collections", "ask", "verdict", "pending_verdicts"}, tools
    assert c.tool("pending_verdicts", active="false")["content"][0]["text"] == ""     # no panel yet: nothing
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

    # The Stop hook's tool: the call ids in the session's transcript (each once), the server says which
    # are unjudged, and those come back as feedback for Claude, once; nothing while the hook is already
    # continuing a turn, nor for a transcript it cannot read.
    import tempfile
    a, b = "p20261025-120000-abcd.member1", "p20261025-120000-abcd.explorer2"
    with tempfile.TemporaryDirectory() as tmp:
        transcript = pathlib.Path(tmp) / "session.jsonl"
        transcript.write_text(f'{{"tool_result": "`{a}` ok"}}\n{{"tool_result": "`{b}` ok, `{a}` again"}}\n',
                              encoding="utf-8")
        hook = {"session": "s", "transcript": str(transcript)}
        assert not c.tool("verdict", call=a, useful=True)["isError"]
        assert c.tool("pending_verdicts", active="true", **hook)["content"][0]["text"] == ""
        out = json.loads(c.tool("pending_verdicts", active="false", **hook)["content"][0]["text"])
        assert SEEN[-1][1:3] == ("/bench/pending", {"calls": [a, b]}), SEEN[-1]
        ctx = out["hookSpecificOutput"]
        assert ctx["hookEventName"] == "Stop" and "1 seat " in ctx["additionalContext"], ctx
        assert b in ctx["additionalContext"] and a not in ctx["additionalContext"], ctx
        assert c.tool("pending_verdicts", active="false", **hook)["content"][0]["text"] == ""        # reminded
        assert c.tool("pending_verdicts", session="t", transcript=str(transcript) + ".missing.jsonl",
                      active="false")["content"][0]["text"] == ""
        assert c.tool("pending_verdicts", session="t", transcript=str(pathlib.Path(tmp) / "x.txt"),
                      active="false")["content"][0]["text"] == ""                                 # not a transcript

    assert c.request("tools/call", {"name": "nope"})["error"]["code"] == -32602
    assert c.request("tools/call", {"name": ["x"]})["error"]["code"] == -32602         # unhashable: answered
    assert c.request("resources/list")["error"]["code"] == -32601
    c.send("{not json")
    assert c.read()["error"]["code"] == -32700
    c.send("[" * 100_000)                                          # deeper than json parses
    assert c.read()["error"]["code"] == -32700
    for method in ("initialize", "tools/call"):                    # params not an object: answered, never raised
        c.send({"jsonrpc": "2.0", "id": method, "method": method, "params": [1]})
        assert c.read() == {"jsonrpc": "2.0", "id": method,
                            "error": {"code": -32602, "message": "params must be an object"}}
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
    over_http()
    print("mcp server ok: initialize, ping, tools/list, each tool, refusals, a ping mid-panel, not running;"
          " /mcp on both ports, its paths, the key and its scope through the loopback, the guard;"
          " pending_verdicts and /bench/pending for the Stop hook")
    return 0


def over_http() -> None:
    """POST /mcp on the real handlers, both ports: the tools reach the server's own routes with the caller's key."""
    import tempfile
    import urllib.error
    import urllib.request
    sys.path.insert(0, str(ROOT))
    import serve
    import gateway
    import guard

    def post(port, path, msg, **headers):
        headers = {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json", **headers}
        data = msg.encode() if isinstance(msg, str) else json.dumps(msg).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data, headers,
                                     method="POST" if msg is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return r.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    def tool(port, name, path="/mcp", **headers_and_args):
        headers = {k: headers_and_args.pop(k) for k in ("Authorization", "Origin") if k in headers_and_args}
        status, reply = post(port, path, {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                                          "params": {"name": name, "arguments": headers_and_args}}, **headers)
        assert status == 200 and reply["id"] == 7, (status, reply)
        return reply["result"]

    servers = [ThreadingHTTPServer(("127.0.0.1", 0), h) for h in (serve.Handler, gateway.Handler)]
    for s in servers:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    saved = (guard.KEYS, gateway.bench_collections.FILE, gateway.run_collection, sys.stderr,
             os.environ.pop("BENCHWARMERS_ADMIN_TOKEN", None), os.environ.pop("BENCHWARMERS_REQUIRE_KEYS", None),
             gateway.offload.LOG)
    ran = []
    with tempfile.TemporaryDirectory() as tmp:
        try:
            t = pathlib.Path(tmp)
            guard.KEYS, gateway.bench_collections.FILE = t / "gateway-keys.json", t / "collections.json"
            gateway.bench_collections.FILE.write_text(json.dumps({"core": {"members": [], "description": "x"},
                                                                  "vision": {"members": [], "description": "y"}}),
                                                      encoding="utf-8")
            gateway.offload.LOG = t / "offloads.jsonl"
            gateway.run_collection = lambda name, messages, nominated, reply, who: (
                ran.append((name, who)) or f"answer from {name}",
                {"seats": [{"call": f"{name}.member1", "seat": "member", "alias": "m1", "status": "ok"},
                           {"call": f"{name}.explorer", "seat": "explorer", "alias": "x1", "status": "error"}]})
            sys.stderr = open(os.devnull, "w")         # the access log
            for port in (s.server_port for s in servers):
                init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2025-06-18", "capabilities": {}}}
                # A BENCHWARMERS_URL with /v1 or a trailing slash, joined to /mcp, is the same route.
                for path in ("/mcp", "/mcp/", "//mcp", "/v1/mcp", "/v1//mcp"):
                    status, reply = post(port, path, init)
                    assert status == 200 and reply["result"]["serverInfo"]["name"] == "benchwarmers", (path, reply)
                assert not guard.mcp_route("/mcpx") and not guard.mcp_route("/v1/mcp/x")
                assert post(port, "/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"}) == (202, None)
                assert post(port, "/mcp", None)[0] == 405                     # no server stream
                assert post(port, "/mcp", "{not json") == (400, {"jsonrpc": "2.0", "id": None,
                                                                  "error": {"code": -32700, "message": "parse error"}})
                status, reply = post(port, "/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
                assert {x["name"] for x in reply["result"]["tools"]} == {"list_collections", "ask", "verdict",
                                                                          "pending_verdicts"}
                r = tool(port, "list_collections", path="//mcp")
                assert not r["isError"] and [x["name"] for x in json.loads(r["content"][0]["text"])] == \
                    ["bench/core", "bench/vision"], r
                r = tool(port, "ask", collection="core", prompt="hi")
                assert not r["isError"] and "answer from core" in r["content"][0]["text"], r
                # A client that takes SSE (every streamable-HTTP client must) gets a tool call as a stream,
                # keep-alives while it runs and then the reply, so its idle timeouts never fire mid-panel.
                saved_keepalive, gateway.KEEPALIVE = gateway.KEEPALIVE, 0.2
                slow, gateway.run_collection = gateway.run_collection, lambda *a, **k: (time.sleep(0.7), slow(*a, **k))[1]
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/mcp", json.dumps({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                                                                "params": {"name": "ask", "arguments": {
                                                                    "collection": "core", "prompt": "hi"}}}).encode(),
                    {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json",
                     "Accept": "application/json, text/event-stream"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    assert resp.headers["Content-Type"] == "text/event-stream", resp.headers
                    events = resp.read().decode()
                assert events.startswith(": keep-alive\n\n") and events.endswith("\n\n"), events
                reply = json.loads(events.rsplit("data: ", 1)[1])
                assert reply["id"] == 9 and "answer from core" in reply["result"]["content"][0]["text"], reply
                # With a progressToken, progress notifications instead: what resets a client's idle timeout.
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/mcp", json.dumps({"jsonrpc": "2.0", "id": 11, "method": "tools/call",
                                                                "params": {"name": "ask", "_meta": {"progressToken": "t1"},
                                                                           "arguments": {"collection": "core",
                                                                                         "prompt": "hi"}}}).encode(),
                    {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json",
                     "Accept": "application/json, text/event-stream"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    messages = [json.loads(line[6:]) for line in resp.read().decode().splitlines()
                                if line.startswith("data: ")]
                gateway.KEEPALIVE, gateway.run_collection = saved_keepalive, slow
                assert ": keep-alive" not in json.dumps(messages) and len(messages) >= 2, messages
                assert all(m["method"] == "notifications/progress" and m["params"]["progressToken"] == "t1"
                           for m in messages[:-1]), messages
                assert messages[-1]["id"] == 11 and not messages[-1]["result"]["isError"], messages[-1]
                # A tool call that cannot be answered still ends its stream (it once kept it open forever).
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/mcp", json.dumps({"jsonrpc": "2.0", "id": 10, "method": "tools/call",
                                                                "params": [1]}).encode(),
                    {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json",
                     "Accept": "application/json, text/event-stream"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    reply = json.loads(resp.read().decode().rsplit("data: ", 1)[1])
                assert reply["id"] == 10 and reply["error"]["code"] == -32602, reply
                assert post(port, "/mcp", "[" * 100_000)[1]["error"]["code"] == -32700
                # The guard on /mcp itself: Host and Origin, as on /v1.
                assert post(port, "/mcp", init, Host="evil.example")[0] == 403
                assert post(port, "/mcp", init, Origin="http://evil.example")[0] == 403
            assert ran == [("core", "local")] * 6, ran

            # The Stop hook over HTTP: the call ids in the transcript, of which the call log says which are
            # seats that answered (ok or empty, not errored) and have no verdict; reminded of once; a
            # verdict sent any way clears one; an id the log does not know is not a seat.
            g = servers[1].server_port
            ok, empty, failed, judged, unknown = (f"p20261025-130000-beef.{s}" for s in
                                                  ("member1", "member2", "explorer", "explorer2", "member9"))
            for call, note in ((ok, "member; ok in 1.0s"), (empty, "member; empty in 2.0s"),
                               (failed, "explorer; error in 0.4s: Error: 429"), (judged, "explorer 2; ok in 1.0s")):
                gateway.offload.add("m", "t", None, note, call)
            gateway.offload.add("m", "t", True, "checked", judged)
            status, reply = post(g, "/bench/pending", {"calls": [ok, empty, failed, judged, unknown]})
            assert (status, reply) == (200, {"pending": [ok, empty]}), reply
            assert post(g, "/bench/pending", {"calls": "x"})[0] == 400
            transcript = t / "claude-1.jsonl"
            transcript.write_text(" ".join((ok, empty, failed, judged, unknown, ok)), encoding="utf-8")

            def pending(session="claude-1"):
                return tool(g, "pending_verdicts", session=session, transcript=str(transcript),
                            active="false")["content"][0]["text"]
            ctx = json.loads(pending())["hookSpecificOutput"]["additionalContext"]
            assert "2 seats " in ctx and ok in ctx and empty in ctx and failed not in ctx and judged not in ctx, ctx
            assert pending() == ""                                         # once per session
            assert ok in pending("claude-2")                               # another session: its own reminder
            assert not tool(g, "verdict", call=ok, useful=True)["isError"]
            assert post(g, "/bench/pending", {"calls": [ok, empty]})[1] == {"pending": [empty]}

            # Keys required: /mcp asks for one, and the loopback carries it, scope and all.
            os.environ["BENCHWARMERS_REQUIRE_KEYS"] = "1"
            vision = {"Authorization": "Bearer " + guard.make_key("vision-only", ["vision"])}
            ran.clear()
            for port in (s.server_port for s in servers):
                assert post(port, "/mcp", init)[0] == 401
                assert post(port, "/mcp", init, Authorization="Bearer ")[0] == 401     # the plugin with no key set
                r = tool(port, "list_collections", **vision)
                assert [x["name"] for x in json.loads(r["content"][0]["text"])] == ["bench/vision"], r
                r = tool(port, "ask", collection="core", prompt="hi", **vision)
                assert r["isError"] and "may call bench/vision" in r["content"][0]["text"], r
                assert not tool(port, "ask", collection="vision", prompt="hi", **vision)["isError"]
            assert ran == [("vision", "vision-only")] * 2, ran
        finally:
            sys.stderr.close()
            guard.KEYS, gateway.bench_collections.FILE, gateway.run_collection, sys.stderr = saved[:4]
            gateway.offload.LOG = saved[6]
            for name, value in (("BENCHWARMERS_ADMIN_TOKEN", saved[4]), ("BENCHWARMERS_REQUIRE_KEYS", saved[5])):
                os.environ.pop(name, None)
                if value is not None:
                    os.environ[name] = value
            for s in servers:
                s.shutdown()


if __name__ == "__main__":
    sys.exit(main())
