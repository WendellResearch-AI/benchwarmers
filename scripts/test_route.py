"""Route mode on the gateway: one member, tools and all, against a fake upstream.

    python scripts/test_route.py

The real gateway handler and the built-in client, with a fake OpenAI-style
upstream standing in for the models (no model is called): a tool-call round
trip in every format, streamed and not (OpenAI chat completions relayed
chunk by chunk, Anthropic Messages, Responses with a custom tool, Gemini,
Ollama), parallel calls whose deltas interleave, a whole JSON reply, the
request's fields passed on intact and an output budget cut to the model's
maximum; failover before the first piece (a 429, a member that sends
nothing in time, an empty reply) and an error after it in the format's
stream shape; every member failing keeps a status; stickiness across turns
and a fresh pick for another conversation; who may be routed to (tools,
images, ad hoc, retired, another API); a panel still refusing tools; and
what is recorded: the ledger (purpose route), routes.jsonl, one unjudged
offload per conversation, and the Gateway card with nothing added to Owed.
Temp data folder; BENCHWARMERS_HOME is set before anything is imported.
"""

from __future__ import annotations

import http.client
import json
import os
import pathlib
import shutil
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
TMP = pathlib.Path(tempfile.mkdtemp(prefix="bench-route-"))
(TMP / "config").mkdir()
(TMP / "llm").mkdir()
os.environ.update(BENCHWARMERS_HOME=str(TMP), LLM_USER_PATH=str(TMP / "llm"), BENCH_CLIENT="builtin",
                  BENCHWARMERS_KEY_FAKE="sk-test")
for name in ("BENCHWARMERS_ADMIN_TOKEN", "BENCHWARMERS_REQUIRE_KEYS", "BENCH_ALLOWED_ORIGINS"):
    os.environ.pop(name, None)
sys.path[:0] = [str(ROOT), str(ROOT / "metrics"), str(ROOT / "suite")]

import formats as F  # noqa: E402

WEATHER = {"type": "function", "function": {"name": "get_weather", "description": "The weather in a city.",
                                            "parameters": {"type": "object", "properties": {"city": {"type": "string"}},
                                                           "required": ["city"]}}}
PIXEL = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="


def chunk(delta=None, finish=None, **extra) -> dict:
    return {"id": "chatcmpl-up", "object": "chat.completion.chunk", "created": 1, "model": "served-x",
            "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}], **extra}


def call_delta(index, cid=None, name=None, args="") -> dict:
    fn = {"arguments": args, **({"name": name} if name else {})}
    return {"index": index, **({"id": cid, "type": "function"} if cid else {}), "function": fn}


class Upstream(BaseHTTPRequestHandler):
    """The models, by the name each request asks for. Each request's body is
    kept (`seen`) to check what was passed on."""
    seen: list[dict] = []

    def log_message(self, *a):
        pass

    def _status(self, code: int, body: dict, **headers) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        for k, v in {"Content-Type": "application/json", "Content-Length": str(len(data)), **headers}.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):                                 # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Upstream.seen.append(body)
        model, last = body["model"], body["messages"][-1]
        if model == "busy":
            return self._status(429, {"error": {"message": "Rate limit exceeded", "code": 429}}, **{"Retry-After": "30"})
        if model.startswith("down"):
            return self._status(503, {"error": {"message": "overloaded"}})
        if model == "plain":                           # a whole JSON reply, not a stream
            return self._status(200, {"id": "x", "model": "plain-1", "usage": {"prompt_tokens": 4, "completion_tokens": 2},
                                      "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                                          "role": "assistant", "content": None, "tool_calls": [{
                                              "id": "call_p", "type": "function",
                                              "function": {"name": "get_weather", "arguments": "{\"city\": \"Oslo\"}"}}]}}]})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        if model == "slow":                            # the headers, then nothing in time
            time.sleep(2.5)
            return
        said = " ".join(F.text_of(m.get("content")) for m in body["messages"] if m.get("role") == "user")
        if model == "hollow":                          # reasons, then says nothing
            events = [chunk({"role": "assistant", "content": "", "reasoning_content": "Hmm."}), chunk({}, "stop")]
        elif last.get("role") == "tool":
            events = [chunk({"role": "assistant", "content": "It is 18C "}), chunk({"content": f"({last['content']})."}),
                      chunk({}, "stop"), chunk(None, None, usage={"prompt_tokens": 30, "completion_tokens": 6})]
        elif "both" in said:                           # two calls, their deltas interleaved
            events = [chunk({"role": "assistant", "content": None, "tool_calls": [call_delta(0, "call_a", "get_weather", "")]}),
                      chunk({"tool_calls": [call_delta(1, "call_b", "get_weather", "{\"city\": ")]}),
                      chunk({"tool_calls": [call_delta(0, None, None, "{\"city\": \"Paris\"}")]}),
                      chunk({"tool_calls": [call_delta(1, None, None, "\"Rome\"}")]}), chunk({}, "tool_calls")]
        else:
            events = [chunk({"role": "assistant", "content": ""}), chunk({"content": "Let me check."}),
                      chunk({"tool_calls": [call_delta(0, "call_1", "get_weather", "{\"ci")]}),
                      chunk({"tool_calls": [call_delta(0, None, None, "ty\": \"Paris\"}")]}),
                      chunk({}, "tool_calls"), chunk(None, None, usage={"prompt_tokens": 20, "completion_tokens": 9})]
            events[-1]["choices"] = []
        if model == "dies":                            # part of an answer, then the connection drops
            events = events[:2]
        for e in events:
            self.wfile.write(f"data: {json.dumps(e)}\n\n".encode())
            self.wfile.flush()
            time.sleep(0.02)
        if model != "dies":
            self.wfile.write(b"data: [DONE]\n\n")


def write(name: str, data) -> None:
    (TMP / "config" / name).write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def sse_json(raw: str) -> list[dict]:
    return [json.loads(line[6:]) for line in raw.splitlines() if line.startswith("data: {")]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    up = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    write("endpoints.json", {"fake": {"base": f"http://127.0.0.1:{up.server_port}/v1", "key": "fake", "default": True,
                                      "kind": "provider"},
                             "native": {"base": f"http://127.0.0.1:{up.server_port}/native/v1", "key": "fake",
                                        "kind": "provider", "api": "anthropic"}})
    import endpoints
    agents = ("busy", "tooler", "slow", "hollow", "dies", "plain", "down", "down2", "prose", "pricey", "blind")
    write("extra-openai-models.yaml", "".join(endpoints.yaml_block(a, a, "fake") for a in agents)
          + endpoints.yaml_block("claude-native", "claude-native", "native"))
    write("model-overrides.json", {**{a: {"tools": True, "vision": True, "max_output": 8192} for a in agents},
                                   "prose": {"tools": False}, "blind": {"tools": True, "vision": False},
                                   "claude-native": {"tools": True}})
    write("model-roles.json", {"ad_hoc": ["pricey"]})
    write("retired.json", {"gone": {"at": "2026-10-01"}})
    write("collections.json", {
        "agents": {"members": ["busy", "tooler"], "reply": "route", "description": "x"},
        "slowpoke": {"members": ["slow", "tooler"], "description": "x"},
        "hollow": {"members": ["hollow", "tooler"], "description": "x"},
        "dies": {"members": ["dies", "tooler"], "description": "x"},
        "plain": {"members": ["plain"], "description": "x"},
        "broken": {"members": ["down", "down2"], "description": "x"},
        "picky": {"members": ["gone", "pricey", "prose", "claude-native", "blind", "tooler"], "description": "x"},
        "proseonly": {"members": ["prose"], "description": "x"},
        "panel": {"members": ["tooler"], "description": "x"}})
    import gateway
    import serve
    gateway.ROTATE, gateway.FIRST_TOKEN, gateway.ROUTE_GRACE, gateway.KEEPALIVE = 0, 1, 5.0, 0.3
    gw = ThreadingHTTPServer(("127.0.0.1", 0), gateway.Handler)
    threading.Thread(target=gw.serve_forever, daemon=True).start()

    def post(path, body, **headers):
        conn = http.client.HTTPConnection("127.0.0.1", gw.server_port, timeout=30)
        conn.request("POST", path, json.dumps(body).encode(),
                     {"Host": f"127.0.0.1:{gw.server_port}", "Content-Type": "application/json", **headers})
        r = conn.getresponse()
        text = r.read().decode()
        conn.close()
        return r.status, dict(r.getheaders()), text

    def ok(path, body):
        status, headers, text = post(path, body)
        assert status == 200, (path, status, text)
        return headers, text

    q = [{"role": "user", "content": "What is the weather in Paris?"}]
    saved_err = sys.stderr
    sys.stderr = open(os.devnull, "w", encoding="utf-8")   # the access log
    try:
        # 1. OPENAI CHAT, STREAMED: the member's chunks relayed as they came, after a
        # failover past a 429 that the caller never sees; the request's fields intact.
        Upstream.seen.clear()
        headers, raw = ok("/v1/chat/completions", {
            "model": "bench/agents", "stream": True, "messages": q, "tools": [WEATHER], "tool_choice": "auto",
            "parallel_tool_calls": False, "temperature": 0.1, "response_format": {"type": "text"},
            "max_tokens": 50000, "seed": 7})
        assert [b["model"] for b in Upstream.seen] == ["busy", "tooler"], Upstream.seen
        sent = Upstream.seen[-1]
        assert sent["tools"] == [WEATHER] and sent["tool_choice"] == "auto" and sent["parallel_tool_calls"] is False
        assert sent["temperature"] == 0.1 and sent["seed"] == 7 and sent["response_format"] == {"type": "text"}
        assert sent["max_tokens"] == 8192, sent                       # cut to the model's known maximum
        assert sent["messages"] == q and sent["stream"] is True
        got = sse_json(raw)
        deltas = [c["choices"][0]["delta"].get("tool_calls") for c in got if c["choices"]]
        assert [d for d in deltas if d] == [[call_delta(0, "call_1", "get_weather", "{\"ci")],
                                            [call_delta(0, None, None, "ty\": \"Paris\"}")]], deltas
        assert all(c["model"] == "bench/agents" for c in got) and raw.rstrip().endswith("data: [DONE]")
        bench = got[-1]["bench"]
        assert got[-1]["choices"] == [] and bench["alias"] == "tooler" and bench["reply"] == "route", bench
        assert bench["failed_over"][0]["alias"] == "busy" and "429" in bench["failed_over"][0]["error"], bench
        assert headers["X-Bench-Model"] == "tooler" and headers["X-Bench-Conversation"] == bench["conversation"]
        conv = bench["conversation"]

        # 2. STICKY: the next turn of that conversation goes straight to tooler
        # (busy is cooling down anyway), and it is the same conversation.
        Upstream.seen.clear()
        turn2 = q + [{"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": "{\"city\": \"Paris\"}"}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "sunny"}]
        _, raw = ok("/v1/chat/completions", {"model": "bench/agents:route", "messages": turn2, "tools": [WEATHER]})
        reply = json.loads(raw)
        assert [b["model"] for b in Upstream.seen] == ["tooler"] and Upstream.seen[0]["messages"] == turn2
        assert reply["choices"][0]["message"]["content"] == "It is 18C (sunny)." and reply["bench"]["conversation"] == conv
        assert reply["choices"][0]["finish_reason"] == "stop" and reply["usage"]["completion_tokens"] == 6
        assert reply["bench"]["turn"] == 2 and reply["bench"]["call"] == f"{conv}.1"
        # Another conversation (another first message) is its own, picked afresh.
        _, raw = ok("/v1/chat/completions", {"model": "bench/agents", "messages": [{"role": "user", "content": "Rome?"}],
                                             "tools": [WEATHER]})
        assert json.loads(raw)["bench"]["conversation"] != conv

        # 3. OPENAI CHAT, WHOLE: the tool call assembled from its deltas.
        _, raw = ok("/v1/chat/completions", {"model": "bench/slowpoke:route", "messages": q, "tools": [WEATHER]})
        msg = json.loads(raw)["choices"][0]
        assert msg["finish_reason"] == "tool_calls" and msg["message"]["content"] == "Let me check."
        assert msg["message"]["tool_calls"] == [{"id": "call_1", "type": "function", "function": {
            "name": "get_weather", "arguments": "{\"city\": \"Paris\"}"}}], msg
        bench = json.loads(raw)["bench"]               # slow sent nothing within FIRST_TOKEN: failed over
        assert bench["alias"] == "tooler" and "nothing in 1s" in bench["failed_over"][0]["error"], bench

        # 4. ANTHROPIC MESSAGES, as Claude Code sends them: tool_use out, tool_result back in.
        claude = {"model": "bench/hollow:route", "max_tokens": 32000, "stream": True,
                  "system": [{"type": "text", "text": "You are an agent.", "cache_control": {"type": "ephemeral"}}],
                  "tools": [{"name": "get_weather", "description": "The weather in a city.",
                             "input_schema": WEATHER["function"]["parameters"]}],
                  "tool_choice": {"type": "auto"}, "messages": [{"role": "user", "content": q[0]["content"]}]}
        Upstream.seen.clear()
        _, raw = ok("/v1/messages", claude)
        assert [b["model"] for b in Upstream.seen] == ["hollow", "tooler"]          # an empty reply fails over too
        assert Upstream.seen[-1]["tools"] == [WEATHER] and Upstream.seen[-1]["messages"][0] == {
            "role": "system", "content": "You are an agent."}
        events = sse_json(raw)
        kinds = [e["type"] for e in events]
        assert kinds[0] == "message_start" and kinds[-1] == "message_stop", kinds
        blocks = [e["content_block"] for e in events if e["type"] == "content_block_start"]
        assert blocks == [{"type": "text", "text": ""},
                          {"type": "tool_use", "id": "call_1", "name": "get_weather", "input": {}}], blocks
        assert json.loads(next(e["delta"]["partial_json"] for e in events if e["type"] == "content_block_delta"
                               and e["delta"]["type"] == "input_json_delta")) == {"city": "Paris"}
        stop = next(e for e in events if e["type"] == "message_delta")
        assert stop["delta"]["stop_reason"] == "tool_use" and stop["usage"] == {"input_tokens": 20, "output_tokens": 9}
        assert stop["bench"]["alias"] == "tooler"
        claude["messages"] += [{"role": "assistant", "content": [
                                   {"type": "text", "text": "Let me check."},
                                   {"type": "tool_use", "id": "call_1", "name": "get_weather", "input": {"city": "Paris"}}]},
                               {"role": "user", "content": [
                                   {"type": "tool_result", "tool_use_id": "call_1", "content": [{"type": "text", "text": "rain"}]}]}]
        Upstream.seen.clear()
        _, raw = ok("/v1/messages", {**claude, "stream": False})
        assert [b["model"] for b in Upstream.seen] == ["tooler"]                   # sticky, past hollow
        assert Upstream.seen[0]["messages"][-2:] == [
            {"role": "assistant", "content": "Let me check.", "tool_calls": [{"id": "call_1", "type": "function", "function": {
                "name": "get_weather", "arguments": "{\"city\": \"Paris\"}"}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "rain"}], Upstream.seen[0]["messages"]
        reply = json.loads(raw)
        assert reply["content"] == [{"type": "text", "text": "It is 18C (rain)."}] and reply["stop_reason"] == "end_turn"
        _, raw = ok("/v1/messages", {**claude, "stream": False, "messages": claude["messages"][:1]})
        reply = json.loads(raw)
        assert reply["stop_reason"] == "tool_use" and reply["content"][1] == {
            "type": "tool_use", "id": "call_1", "name": "get_weather", "input": {"city": "Paris"}}, reply

        # 5. PARALLEL CALLS, deltas interleaved: each arrives whole outside OpenAI's format.
        both = [{"role": "user", "content": "both cities"}]
        _, raw = ok("/v1/messages", {**claude, "messages": both})
        partial = [json.loads(e["delta"]["partial_json"]) for e in sse_json(raw)
                   if e.get("delta", {}).get("type") == "input_json_delta"]
        assert partial == [{"city": "Paris"}, {"city": "Rome"}], partial
        _, raw = ok("/v1/chat/completions", {"model": "bench/agents:route", "messages": both, "tools": [WEATHER]})
        calls = json.loads(raw)["choices"][0]["message"]["tool_calls"]
        assert [(c["id"], json.loads(c["function"]["arguments"])) for c in calls] == [
            ("call_a", {"city": "Paris"}), ("call_b", {"city": "Rome"})], calls

        # 6. RESPONSES (Codex): a function call out and its output back; a custom tool.
        codex = {"model": "bench/agents:route", "instructions": "Be brief.", "stream": True, "input": q[0]["content"],
                 "tools": [{"type": "function", "name": "get_weather", "parameters": WEATHER["function"]["parameters"]},
                           {"type": "custom", "name": "apply_patch", "description": "Patch files.",
                            "format": {"type": "grammar", "syntax": "lark", "definition": "start: /.+/"}}]}
        Upstream.seen.clear()
        _, raw = ok("/v1/responses", codex)
        tools = Upstream.seen[-1]["tools"]
        assert tools[0] == WEATHER | {"function": {**WEATHER["function"], "description": ""}}, tools
        assert tools[1]["function"]["parameters"]["required"] == ["input"] and "start: /.+/" in tools[1]["function"]["description"]
        events = sse_json(raw)
        done = next(e for e in events if e["type"] == "response.completed")["response"]
        assert [i["type"] for i in done["output"]] == ["message", "function_call"], done["output"]
        assert done["output"][1]["call_id"] == "call_1" and json.loads(done["output"][1]["arguments"]) == {"city": "Paris"}
        assert any(e["type"] == "response.function_call_arguments.done" for e in events)
        back = {**codex, "stream": False, "input": [
            {"role": "user", "content": q[0]["content"]},
            {"type": "function_call", "call_id": "call_1", "name": "get_weather", "arguments": "{\"city\": \"Paris\"}"},
            {"type": "function_call_output", "call_id": "call_1", "output": "windy"}]}
        _, raw = ok("/v1/responses", back)
        assert Upstream.seen[-1]["messages"][-1] == {"role": "tool", "tool_call_id": "call_1", "content": "windy"}
        assert F.responses_text(json.loads(raw)) == "It is 18C (windy)."
        ask = F.responses_ask({"input": [{"type": "custom_tool_call", "call_id": "c9", "name": "apply_patch", "input": "*** x"}]})
        assert ask["messages"][0]["tool_calls"][0]["function"]["arguments"] == "{\"input\": \"*** x\"}"
        turn = F.Turn()
        turn.feed(chunk({"tool_calls": [call_delta(0, "c9", "apply_patch", "{\"input\": \"*** y\"}")]}))
        item = F.route_reply("responses", turn, "m", {}, 1, 0.1, custom=["apply_patch"])["output"][0]
        assert item["type"] == "custom_tool_call" and item["input"] == "*** y" and item["call_id"] == "c9", item
        # Turn's keying: an id that comes after the call's first piece is that call's; a new id on
        # a reused index (as some compatibility layers send parallel calls) is a new call.
        turn = F.Turn()
        for d in ([call_delta(0, None, "get_weather", "")], [call_delta(0, "call_late", None, "{\"city\": ")],
                  [call_delta(0, None, None, "\"Oslo\"}")], [call_delta(0, "call_2", "get_weather", "{}")]):
            turn.feed(chunk({"tool_calls": d}))
        assert [(c["id"], c["name"], c["arguments"]) for c in turn.calls] == [
            ("call_late", "get_weather", "{\"city\": \"Oslo\"}"), ("call_2", "get_weather", "{}")], turn.calls

        # 7. GEMINI: a functionCall part out (whole, in the last chunk), its functionResponse back.
        gem = {"contents": [{"role": "user", "parts": [{"text": q[0]["content"]}]}],
               "tools": [{"functionDeclarations": [{"name": "get_weather", "parameters": {
                   "type": "OBJECT", "properties": {"city": {"type": "STRING"}}}}]}],
               "toolConfig": {"functionCallingConfig": {"mode": "ANY"}}}
        Upstream.seen.clear()
        _, raw = ok("/v1beta/models/bench/agents:route:streamGenerateContent?alt=sse", gem)
        assert Upstream.seen[-1]["tools"][0]["function"]["parameters"] == {
            "type": "object", "properties": {"city": {"type": "string"}}} and Upstream.seen[-1]["tool_choice"] == "required"
        last = sse_json(raw)[-1]["candidates"][0]
        assert last["content"]["parts"] == [{"functionCall": {"id": "call_1", "name": "get_weather", "args": {"city": "Paris"}}}]
        assert "".join(F.gemini_delta(c) for c in sse_json(raw)) == "Let me check."
        gem["contents"] += [{"role": "model", "parts": [{"functionCall": {"name": "get_weather", "args": {"city": "Paris"}}}]},
                            {"role": "user", "parts": [{"functionResponse": {"name": "get_weather", "response": {"sky": "grey"}}}]}]
        _, raw = ok("/v1beta/models/bench/agents:route:generateContent", gem)
        sent = Upstream.seen[-1]["messages"]
        assert sent[-2]["tool_calls"][0]["id"] == sent[-1]["tool_call_id"] == "call_0", sent      # numbered, matched
        assert F.gemini_text(json.loads(raw)) == "It is 18C ({\"sky\": \"grey\"})."

        # 8. OLLAMA: tool_calls with object arguments, a tool message back.
        olla = {"model": "bench/agents:route", "messages": q, "tools": [WEATHER], "stream": False}
        _, raw = ok("/api/chat", olla)
        assert json.loads(raw)["message"]["tool_calls"] == [{"function": {"name": "get_weather", "arguments": {"city": "Paris"}}}]
        _, raw = ok("/api/chat", {**olla, "stream": True})
        lines = [json.loads(line) for line in raw.splitlines()]
        assert any(line.get("message", {}).get("tool_calls") for line in lines) and lines[-1]["done"]
        _, raw = ok("/api/chat", {**olla, "messages": q + [
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "get_weather", "arguments": {"city": "Paris"}}}]},
            {"role": "tool", "tool_name": "get_weather", "content": "hail"}]})
        assert json.loads(raw)["message"]["content"] == "It is 18C (hail)."

        # 9. A WHOLE JSON REPLY upstream becomes the one chunk it would have streamed.
        _, raw = ok("/v1/messages", {**claude, "model": "bench/plain:route", "messages": both})
        assert [json.loads(e["delta"]["partial_json"]) for e in sse_json(raw)
                if e.get("delta", {}).get("type") == "input_json_delta"] == [{"city": "Oslo"}]

        # 10. AFTER THE FIRST PIECE an error goes into the stream, in the format's shape.
        _, raw = ok("/v1/messages", {**claude, "model": "bench/dies:route", "messages": [{"role": "user", "content": "x1"}]})
        events = sse_json(raw)
        assert events[-1]["type"] == "error" and "dies failed part way" in events[-1]["error"]["message"], events[-1]
        _, raw = ok("/v1/chat/completions", {"model": "bench/dies:route", "stream": True,
                                             "messages": [{"role": "user", "content": "x2"}]})
        assert "dies failed part way" in sse_json(raw)[-1]["error"]["message"]

        # 11. EVERY MEMBER FAILING keeps a status, streamed or not.
        for stream in (False, True):
            status, _, text = post("/v1/messages", {**claude, "model": "bench/broken:route", "stream": stream})
            assert status == 502 and "down: Error: Error code: 503" in json.loads(text)["error"]["message"], text
        status, _, text = post("/v1/chat/completions", {"model": "bench/agents:route", "messages": [
            {"role": "user", "content": "x3"}]})                       # busy cooling, tooler fine: still answers
        assert status == 200, text

        # 12. WHO MAY BE ROUTED TO: tools, images, ad hoc, retired, another API.
        Upstream.seen.clear()
        _, raw = ok("/v1/chat/completions", {"model": "bench/picky:route", "messages": [{"role": "user", "content": [
            {"type": "text", "text": "what is this?"}, {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{PIXEL}"}}]}],
            "tools": [WEATHER]})
        skipped = {k["alias"]: k["why"] for k in json.loads(raw)["bench"]["skipped"]}
        assert [b["model"] for b in Upstream.seen] == ["tooler"], Upstream.seen
        assert "retired" in skipped["gone"] and "ad hoc" in skipped["pricey"] and "Tool calling" not in skipped["prose"]
        assert "does not call tools" in skipped["prose"] and "anthropic API" in skipped["claude-native"]
        assert "does not take images" in skipped["blind"], skipped
        status, _, text = post("/v1/chat/completions", {"model": "bench/proseonly:route", "messages": q, "tools": [WEATHER]})
        assert status == 400 and "does not call tools" in json.loads(text)["error"]["message"], text
        status, _, text = post("/v1/chat/completions", {"model": "bench/proseonly:route", "messages": q})
        assert status == 200, text                                     # no tools asked for: any member will do

        # 13. A PANEL STILL REFUSES TOOLS, and says how to ask for route mode.
        status, _, text = post("/v1/chat/completions", {"model": "bench/panel", "messages": q, "tools": [WEATHER]})
        assert status == 400 and ":route" in json.loads(text)["error"]["message"], text
        status, _, text = post("/v1/messages", {**claude, "model": "bench/panel:all"})
        assert status == 400 and json.loads(text)["error"]["type"] == "invalid_request_error", text

        # 14. RECORDED: the ledger, routes.jsonl per turn, one offload per conversation.
        import ledger
        import offload
        rows = list(ledger.rows(0))
        assert rows and all(r["purpose"] == "route" for r in rows), rows[:2]
        assert any(r["alias"] == "busy" and r["status"] == "error" for r in rows)
        assert any(r["alias"] == "tooler" and r["status"] == "ok" for r in rows)      # a tool-call turn is an answer
        lines = [json.loads(line) for line in (TMP / "runs" / "gateway" / "routes.jsonl").read_text(encoding="utf-8").splitlines()]
        # (Gemini's and Ollama's turns above, with the same first message and
        # no system text, joined the same conversation: the key is not the format.)
        mine = [line for line in lines if line["conversation"] == conv]
        assert [line["turn"] for line in mine[:2]] == [1, 2] and mine[0]["tool_calls"] == 1, mine
        assert mine[0]["tried"][0]["alias"] == "busy", mine
        assert mine[0]["tokens_out"] == 9 and "prompt" not in mine[0] and "messages" not in mine[0]
        assert any(line["status"] == "error" and line["alias"] == "" for line in lines)   # every member failed
        entries = [e for e in offload.entries() if e.get("call", "").startswith(conv)]
        assert len(entries) == 1 and entries[0]["call"] == f"{conv}.1" and entries[0]["useful"] is None, entries
        assert entries[0]["task"] == "What is the weather in Paris? (bench/agents:route)", entries[0]
        assert not (TMP / "runs" / "gateway" / "calls.jsonl").exists()           # no panel rows: nothing Owed
        card = serve.gateway_card()
        assert "0 verdicts owed" in card and "Route conversations" in card and conv in card, card[-2000:]
        offload.verdict(f"{conv}.1", True, "fixed the bug")
        assert "tooler ✓" in serve.gateway_card()
    finally:
        sys.stderr = saved_err
        gw.shutdown()
        up.shutdown()
        shutil.rmtree(TMP, ignore_errors=True)
    print("route mode: tool round trips in every format, failover, stickiness and recording hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
