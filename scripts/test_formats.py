"""Other API formats on the gateway: the translators, and every route against fake seats.

    python scripts/test_formats.py

metrics/formats.py both ways, without a server: OpenAI messages to each
native request and back (text, a system text, an assistant turn, an inline
and a linked image), and an answer to each native reply and stream and back
to its text, so the outbound half the built-in client will use is the
inverse of the inbound half the gateway uses. Then the real gateway handler,
its seats stubbed (no model is called), on every route: Anthropic Messages
and count_tokens, Responses, legacy completions, Ollama's chat, generate,
tags, version and show, Gemini's generateContent, streamGenerateContent (SSE
and JSON array), models and countTokens. Streamed and not; seat by seat as
each finishes, a merged reply at the end, keep-alives while the seats think,
a refusal before the panel keeps its status and an error after the 200 goes
into the stream in the format's own shape; tools refused; images arrive as
image_url parts; keys by x-api-key and a Gemini route's ?key=, never logged.
Temp data folder; BENCHWARMERS_HOME is set before anything is imported.
"""

from __future__ import annotations

import http.client
import io
import json
import os
import pathlib
import shutil
import socket
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
TMP = pathlib.Path(tempfile.mkdtemp(prefix="bench-formats-"))
(TMP / "config").mkdir()
(TMP / "llm").mkdir()
os.environ.update(BENCHWARMERS_HOME=str(TMP), LLM_USER_PATH=str(TMP / "llm"), BENCH_CLIENT="builtin",
                  BENCHWARMERS_KEY_FAKE="sk-test")
for name in ("BENCHWARMERS_ADMIN_TOKEN", "BENCHWARMERS_REQUIRE_KEYS", "BENCH_ALLOWED_ORIGINS"):
    os.environ.pop(name, None)
sys.path[:0] = [str(ROOT), str(ROOT / "metrics"), str(ROOT / "suite")]

import formats as F  # noqa: E402

PIXEL = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
DATA_URL = "data:image/png;base64," + PIXEL
PHOTO = "https://photos.example/chair.png"
TALK = [{"role": "system", "content": "You price estate-sale items."},
        {"role": "user", "content": [F.text_part("What is this chair worth?"), F.image(DATA_URL)]},
        {"role": "assistant", "content": "About forty dollars."},
        {"role": "user", "content": "And with the cushion?"}]


def sse_events(raw: str) -> list[tuple[str, str]]:
    """(event name, data) per server-sent event; ("comment", line) for a comment."""
    out = []
    for block in raw.replace("\r\n", "\n").split("\n\n"):
        name, data = "", []
        for line in block.splitlines():
            if line.startswith(":"):
                out.append(("comment", line))
            elif line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data.append(line[6:])
        if data:
            out.append((name, "\n".join(data)))
    return out


def sse_json(raw: str) -> list[dict]:
    return [json.loads(d) for _, d in sse_events(raw) if d.startswith("{")]


def refused(fn, *args, said: str = "") -> str:
    try:
        fn(*args)
    except F.Unsupported as exc:
        assert said in str(exc), (said, str(exc))
        return str(exc)
    raise AssertionError(f"{fn.__name__} took {args!r}")


def translators() -> None:
    # OpenAI messages -> each native request -> back: the same conversation.
    assert F.anthropic_ask(F.anthropic_request(TALK, "bench/core"))["messages"] == TALK
    assert F.gemini_ask(F.gemini_request(TALK), "bench/core")["messages"] == TALK
    assert F.responses_ask(F.responses_request(TALK, "bench/core"))["messages"] == TALK
    assert F.ollama_chat_ask(F.ollama_request(TALK, "bench/core"))["messages"] == TALK
    linked = [{"role": "user", "content": [F.text_part("This one?"), F.image(PHOTO)]}]
    for there_and_back in (lambda: F.anthropic_ask(F.anthropic_request(linked, "m")),
                           lambda: F.gemini_ask(F.gemini_request(linked), "m"),
                           lambda: F.responses_ask(F.responses_request(linked, "m"))):
        assert there_and_back()["messages"] == linked
    refused(F.ollama_request, linked, "m", said="base64 only")          # Ollama takes no URLs
    # The native shapes, as their APIs document them.
    a = F.anthropic_request(TALK, "claude-x", {"max_tokens": 50, "temperature": 0.2, "stop": ["END"]}, stream=True)
    assert a["system"] == "You price estate-sale items." and a["max_tokens"] == 50 and a["stream"] is True
    assert a["stop_sequences"] == ["END"] and a["messages"][0]["content"][1] == {
        "type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PIXEL}}, a
    assert F.anthropic_request(TALK, "m")["max_tokens"] == F.ANTHROPIC_MAX_TOKENS
    g = F.gemini_request(TALK, {"max_tokens": 9})
    assert g["systemInstruction"] == {"parts": [{"text": "You price estate-sale items."}]}
    assert [c["role"] for c in g["contents"]] == ["user", "model", "user"] and g["generationConfig"] == {"maxOutputTokens": 9}
    assert g["contents"][0]["parts"][1] == {"inlineData": {"mimeType": "image/png", "data": PIXEL}}
    assert F.gemini_request(linked)["contents"][0]["parts"][1] == {"fileData": {"mimeType": "image/png", "fileUri": PHOTO}}
    assert F.gemini_path("gemini-x") == "/v1beta/models/gemini-x:generateContent"
    assert F.gemini_path("gemini-x", True).endswith(":streamGenerateContent?alt=sse")
    r = F.responses_request(TALK, "gpt-x", {"max_tokens": 7})
    assert r["store"] is False and r["instructions"] and r["max_output_tokens"] == 7
    assert r["input"][1]["content"] == [{"type": "output_text", "text": "About forty dollars."}]
    o = F.ollama_request(TALK, "llama", {"max_tokens": 5})
    assert o["messages"][1] == {"role": "user", "content": "What is this chair worth?", "images": [PIXEL]}
    assert o["options"] == {"num_predict": 5} and o["stream"] is False

    # An answer -> each native reply, and each stream -> back to its text.
    text = "Seats agree: ## heading, `code`, ünïcode."
    pieces = ["Seats agree: ", "## heading, ", "`code`, ünïcode."]
    for reply, read in ((F.anthropic_reply, F.anthropic_text), (F.gemini_reply, F.gemini_text),
                        (F.responses_reply, F.responses_text), (F.ollama_reply, F.ollama_text),
                        (F.ollama_generate_reply, F.ollama_text)):
        assert read(json.loads(json.dumps(reply(text, "bench/core", {"panel": "p"}, (3, 4), 1.5)))) == text, reply
    for stream, delta in ((F.AnthropicStream, F.anthropic_delta), (F.ResponsesStream, F.responses_delta),
                          (F.GeminiStream, F.gemini_delta)):
        s = stream("bench/core", 12)
        raw = (s.start() + s.keepalive() + b"".join(s.text(p) for p in pieces) + s.keepalive() + s.end({"panel": "p"}))
        assert "".join(delta(e) for e in sse_json(raw.decode())) == text, stream
    s = F.GeminiStream("bench/core", 12, sse=False)                     # without ?alt=sse: one JSON array
    raw = s.start() + s.keepalive() + b"".join(s.text(p) for p in pieces) + s.end()
    chunks = json.loads(raw)
    assert "".join(F.gemini_delta(c) for c in chunks) == text and chunks[-1]["candidates"][0]["finishReason"] == "STOP"
    assert chunks[-1]["usageMetadata"]["promptTokenCount"] == 12
    s = F.OllamaStream("bench/core", 3)
    lines = (s.start() + s.keepalive() + b"".join(s.text(p) for p in pieces) + s.end({"panel": "p"})).decode().splitlines()
    rows = [json.loads(line) for line in lines]
    assert "".join(F.ollama_delta(r) for r in rows) == text and rows[-1]["done"] and rows[-1]["bench"] == {"panel": "p"}
    assert rows[-1]["eval_duration"] > 0 and rows[-1]["prompt_eval_count"] == 3
    for s in (F.ChatStream("bench/core"), F.CompletionStream("bench/core")):
        raw = (s.start() + b"".join(s.text(p) for p in pieces) + s.end({"panel": "p"})).decode()
        got = sse_json(raw)
        said = "".join(c["choices"][0].get("text") or c["choices"][0].get("delta", {}).get("content") or "" for c in got)
        assert said == text and got[-1]["bench"] == {"panel": "p"} and raw.endswith("data: [DONE]\n\n"), raw
    # An error in a stream, in each shape, is raised by the outbound reader.
    for stream, delta in ((F.AnthropicStream, F.anthropic_delta), (F.ResponsesStream, F.responses_delta),
                          (F.GeminiStream, F.gemini_delta), (F.OllamaStream, F.ollama_delta)):
        raw = stream("m").error(503, "no seat answered").decode()
        events = sse_json(raw) if stream is not F.OllamaStream else [json.loads(raw)]
        try:
            "".join(delta(e) for e in events)
            raise AssertionError(f"{stream.__name__}'s error read as text")
        except F.UpstreamError as exc:
            assert "no seat answered" in str(exc), exc
    assert sse_json(F.ChatStream("m").error(500, "boom").decode())[0]["error"]["message"] == "boom"

    # Inbound: each format's own request shapes.
    a = F.anthropic_ask({"model": "bench/core", "max_tokens": 9, "stream": True, "system": [{"type": "text", "text": "S"}],
                         "messages": [{"role": "user", "content": [
                             {"type": "text", "text": "look", "cache_control": {"type": "ephemeral"}},
                             {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "/9j/AA"}},
                             {"type": "image", "source": {"type": "url", "url": PHOTO}}]},
                             {"role": "assistant", "content": [{"type": "thinking", "thinking": "hm"},
                                                               {"type": "text", "text": "yes"}]}],
                         "bench": {"reply": "merged"}, "with": ["x"]})
    assert a["messages"] == [{"role": "system", "content": "S"},
                             {"role": "user", "content": [F.text_part("look"), F.image("data:image/jpeg;base64,/9j/AA"),
                                                          F.image(PHOTO)]},
                             {"role": "assistant", "content": "yes"}], a
    assert (a["stream"], a["reply"], a["with"], a["options"]) == (True, "merged", ["x"], {"max_tokens": 9})
    g = F.gemini_ask({"system_instruction": {"parts": [{"text": "S"}]}, "contents": [
        {"role": "user", "parts": [{"text": "look"}, {"inline_data": {"mime_type": "image/png", "data": PIXEL}}]},
        {"role": "model", "parts": [{"text": "thinking...", "thought": True}, {"text": "yes"}]}],
        "generationConfig": {"maxOutputTokens": 9, "stopSequences": ["X"]}}, "bench/core:merged", True)
    assert g["messages"] == [{"role": "system", "content": "S"}, {"role": "user", "content": [F.text_part("look"),
                             F.image(DATA_URL)]}, {"role": "assistant", "content": "yes"}], g
    assert (g["model"], g["stream"], g["options"]) == ("bench/core:merged", True, {"max_tokens": 9, "stop": ["X"]})
    r = F.responses_ask({"model": "bench/core", "instructions": "S", "input": [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "look"},
                                                        {"type": "input_image", "image_url": DATA_URL, "detail": "low"}]},
        {"type": "reasoning", "id": "rs_1", "summary": []},
        {"role": "assistant", "content": "yes"}], "store": True})
    assert r["messages"] == [{"role": "system", "content": "S"}, {"role": "user", "content": [F.text_part("look"),
                             F.image(DATA_URL)]}, {"role": "assistant", "content": "yes"}], r
    assert F.responses_ask({"model": "m", "input": "hi"})["messages"] == [{"role": "user", "content": "hi"}]
    o = F.ollama_chat_ask({"model": "bench/core:latest", "messages": [{"role": "user", "content": "look", "images": [PIXEL]}],
                           "options": {"num_predict": 4}})
    assert o["model"] == "bench/core" and o["stream"] is True, o           # Ollama streams unless told not to
    assert o["messages"] == [{"role": "user", "content": [F.text_part("look"), F.image(DATA_URL)]}]
    assert F.ollama_model("bench/core:merged") == "bench/core:merged"
    o = F.ollama_generate_ask({"model": "bench/core", "prompt": "hi", "system": "S", "stream": False})
    assert o["messages"] == [{"role": "system", "content": "S"}, {"role": "user", "content": "hi"}] and not o["stream"]
    c = F.completion_ask({"model": "bench/core", "prompt": ["hi"], "stop": "END"})
    assert c["messages"] == [{"role": "user", "content": "hi"}] and c["options"] == {"stop": ["END"]}
    assert F.sniff("/9j/4AAQ") == "image/jpeg" and F.sniff(PIXEL) == "image/png" and F.sniff("R0lGODlh") == "image/gif"
    assert F.gemini_route("/v1beta/models/bench/core:merged:streamGenerateContent") == \
        ("bench/core:merged", "streamGenerateContent")
    assert F.gemini_route("/v1beta/models/bench%2Fcore:generateContent") == ("bench/core", "generateContent")
    assert F.gemini_route("/v1beta/models") is None and F.gemini_route("/v1/models") is None
    assert [F.format_of(p) for p in ("/v1/messages", "/api/tags", "/v1beta/models", "/v1/models/x:generateContent",
                                     "/v1/models", "/v1/responses", "/v1/completions")] == \
        ["anthropic", "ollama_chat", "gemini", "gemini", "chat", "responses", "completion"]

    # Refused, not dropped: tools, tool turns, a stored response, what no seat could read.
    tool = [{"name": "f", "input_schema": {}}]
    user = [{"role": "user", "content": "hi"}]
    for fn, body in ((F.chat_ask, {"messages": user, "tools": [{"type": "function"}]}),
                     (F.chat_ask, {"messages": user + [{"role": "tool", "content": "x"}]}),
                     (F.anthropic_ask, {"messages": user, "tools": tool}),
                     (F.anthropic_ask, {"messages": [{"role": "user", "content": [{"type": "tool_result"}]}]}),
                     (F.responses_ask, {"input": "hi", "tools": [{"type": "web_search"}]}),
                     (F.responses_ask, {"input": [{"type": "function_call_output", "output": "x"}]}),
                     (F.ollama_chat_ask, {"messages": user, "tools": [{}]}),
                     (F.ollama_chat_ask, {"messages": [{"role": "assistant", "tool_calls": [{}]}]})):
        refused(fn, body, said="route mode")
    refused(F.gemini_ask, {"contents": [], "tools": [{"functionDeclarations": []}]}, "m", said="route mode")
    refused(F.gemini_ask, {"contents": [{"parts": [{"functionCall": {}}]}]}, "m", said="route mode")
    refused(F.responses_ask, {"input": "hi", "previous_response_id": "resp_1"}, said="never stored")
    refused(F.gemini_ask, {"contents": [{"parts": [{"inlineData": {"mimeType": "application/pdf", "data": "x"}}]}]},
            "m", said="only images")
    refused(F.gemini_ask, {"contents": [{"parts": [{"fileData": {"mimeType": "image/png",
                                                                  "fileUri": "files/abc"}}]}]}, "m", said="Files API")
    refused(F.anthropic_ask, {"messages": [{"role": "user", "content": [{"type": "document"}]}]}, said="'document'")
    refused(F.anthropic_ask, {"messages": [{"role": "system", "content": "x"}]}, said="role")
    refused(F.ollama_generate_ask, {"prompt": "hi", "context": [1, 2]}, said="/api/chat")
    refused(F.completion_ask, {"prompt": ["a", "b"]}, said="one string")
    refused(F.chat_ask, {"messages": user, "with": "x"}, said='"with"')
    refused(F.chat_ask, {"messages": user, "bench": {"reply": "both"}}, said='"bench"')
    refused(F.chat_ask, {"messages": user, "model": 5}, said='"model"')
    refused(F.chat_ask, {"messages": "hi"}, said='"messages"')
    assert F.chat_ask({"messages": user, "tools": []})["messages"] == user                 # an empty list is no tools


class Seats:
    """Stands in for gateway.ask_seat: an answer per alias, after a delay;
    "boom" raises, as a seat failing outside its own error handling would."""

    def __init__(self):
        self.asked: list[tuple] = []
        self.delay = {"m1": 0.2}

    def __call__(self, alias, system, prompt, messages=None):
        self.asked.append((alias, system, prompt, messages))
        time.sleep(self.delay.get(alias, 0.0))
        if alias == "boom":
            raise RuntimeError("the seat's process died")
        answer = "the merged answer" if alias == "mg" else f"answer from {alias}"
        return {"status": "ok", "answer": answer, "error": "", "seconds": 0.0}


def write(name: str, data) -> None:
    (TMP / "config" / name).write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def gateway_routes() -> None:
    write("endpoints.json", {"fake": {"base": "http://127.0.0.1:9/v1", "key": "fake", "default": True,
                                      "kind": "provider"}})
    import endpoints
    write("extra-openai-models.yaml", "".join(endpoints.yaml_block(a, a) for a in ("m1", "m2", "mg", "boom", "tiny")))
    write("model-overrides.json", {"m1": {"vision": True}, "m2": {"vision": True},
                                   "tiny": {"vision": True, "context_window": 50}})
    write("gateway.json", {"max_calls": 3, "max_explorers": 0})          # no explorers: no router, no shape picker
    write("collections.json", {"core": {"members": ["m1", "m2"], "description": "general"},
                               "merge": {"members": ["m1", "m2"], "reply": "merged", "merger": "mg", "description": "x"},
                               "broken": {"members": ["m1", "boom"], "description": "x"},
                               "small": {"members": ["tiny"], "description": "x"},
                               "other": {"members": ["m2"], "description": "x"}})
    import gateway
    import guard
    import serve
    seats = Seats()
    gateway.ask_seat = seats
    gateway.STREAM_GRACE, gateway.KEEPALIVE = 0.1, 0.15
    gw = ThreadingHTTPServer(("127.0.0.1", 0), gateway.Handler)
    dash = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
    serve.PORT = dash.server_port                      # the dashboard's own Host check uses it
    for s in (gw, dash):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    port = gw.server_port

    def call(path, body=None, method=None, on=port, **headers):
        """(status, headers, text, seconds to the status line)."""
        conn = http.client.HTTPConnection("127.0.0.1", on, timeout=30)
        data = json.dumps(body).encode() if body is not None else None
        sent = {"Host": f"127.0.0.1:{on}", **({"Content-Type": "application/json"} if data else {}), **headers}
        started = time.monotonic()
        conn.request(method or ("POST" if data is not None else "GET"), path, data, sent)
        r = conn.getresponse()
        first = time.monotonic() - started
        text = r.read().decode()
        conn.close()
        return r.status, dict(r.getheaders()), text, first

    def ok(path, body=None, **headers):
        status, h, text, _ = call(path, body, **headers)
        assert status == 200, (path, status, text)
        return text, h

    user = "say hi"
    saved_err = sys.stderr
    try:
        # NOT STREAMED: each format's reply carries every seat in seat order; the bench where it fits.
        text, _ = ok("/v1/chat/completions", {"model": "bench/core", "messages": [{"role": "user", "content": user}]})
        chat = json.loads(text)
        content, bench = chat["choices"][0]["message"]["content"], chat["bench"]
        assert set(chat) == {"id", "object", "created", "model", "choices", "bench"} and chat["model"] == "bench/core"
        assert content.index("answer from m1") < content.index("answer from m2"), content     # seat order
        header, footer = content.split("\n## ")[0], "---\n" + content.split("\n---\n", 1)[1]
        assert content == "\n".join([header] + [gateway.section(s) for s in bench["seats"]] + [footer]), content
        for path, body, read in (
                ("/v1/messages", {"model": "bench/core", "max_tokens": 64, "messages": [{"role": "user", "content": user}]},
                 F.anthropic_text),
                ("/v1/responses", {"model": "bench/core", "input": user}, F.responses_text),
                ("/v1/completions", {"model": "bench/core", "prompt": user}, lambda r: r["choices"][0]["text"]),
                ("/api/chat", {"model": "bench/core:latest", "stream": False,
                               "messages": [{"role": "user", "content": user}]}, F.ollama_text),
                ("/api/generate", {"model": "bench/core", "prompt": user, "stream": False}, F.ollama_text),
                ("/v1beta/models/bench/core:generateContent", {"contents": [{"parts": [{"text": user}]}]},
                 F.gemini_text)):
            text, headers = ok(path, body)
            reply = json.loads(text)
            said = read(reply)
            assert "answer from m1" in said and "answer from m2" in said, (path, said)
            if path.startswith("/v1beta"):
                # Gemini's body has no room for the bench: the call ids are in headers.
                assert "bench" not in reply and headers["X-Bench-Panel"].startswith("p"), headers
                calls = json.loads(headers["X-Bench-Seats"])
                assert [s["alias"] for s in calls] == ["m1", "m2"] and all(s["call"] in said for s in calls), calls
            else:
                assert [s["alias"] for s in reply["bench"]["seats"]] == ["m1", "m2"], (path, reply)
        reply = json.loads(ok("/v1/messages", {"model": "bench/core", "max_tokens": 8,
                                               "messages": [{"role": "user", "content": user}]})[0])
        assert reply["type"] == "message" and reply["usage"]["input_tokens"] > 0 and reply["usage"]["output_tokens"] > 0
        reply = json.loads(ok("/api/chat", {"model": "bench/core", "stream": False, "messages": [{"role": "user", "content": user}]})[0])
        assert reply["done"] and reply["total_duration"] >= 2e8 and reply["model"] == "bench/core", reply  # m1 took 0.2 s

        # Images in each format's shape reach the seats as OpenAI image_url parts.
        seats.asked.clear()
        ok("/v1/messages", {"model": "bench/core", "max_tokens": 8, "messages": [{"role": "user", "content": [
            {"type": "text", "text": "what is it"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PIXEL}}]}]})
        ok("/api/chat", {"model": "bench/core", "stream": False,
                         "messages": [{"role": "user", "content": "what is it", "images": [PIXEL]}]})
        ok("/v1beta/models/bench/core:generateContent", {"contents": [{"parts": [
            {"text": "what is it"}, {"inlineData": {"mimeType": "image/png", "data": PIXEL}}]}]})
        ok("/v1/responses", {"model": "bench/core", "input": [{"role": "user", "content": [
            {"type": "input_text", "text": "what is it"}, {"type": "input_image", "image_url": DATA_URL}]}]})
        sent = [m for _, _, _, m in seats.asked]
        assert len(sent) == 8 and all(m == [{"role": "user", "content": [F.text_part("what is it"), F.image(DATA_URL)]}]
                                      for m in sent), sent
        # The merged reply, by tag and by mode.
        for path, body in (("/api/chat", {"model": "bench/core:merged", "stream": False,
                                          "messages": [{"role": "user", "content": user}]}),
                           ("/v1/messages", {"model": "bench/merge", "max_tokens": 8,
                                             "messages": [{"role": "user", "content": user}]})):
            reply = json.loads(ok(path, body)[0])
            text = F.ollama_text(reply) if path == "/api/chat" else F.anthropic_text(reply)
            merger = reply["bench"]["merger"]                                  # bench/core has none set: m1
            assert reply["bench"]["reply"] == "merged" and text.startswith(merger["answer"]), reply
            assert merger["call"] in text and merger["alias"] == ("mg" if body["model"] == "bench/merge" else "m1"), merger

        seats.delay["m1"] = 0.8
        # STREAMED: the 200 at once, every seat as it finishes (m2 before the slower m1), the bench at the end.
        for path, body, parse in (
                ("/v1/chat/completions", {"model": "bench/core", "stream": True,
                                          "messages": [{"role": "user", "content": user}]},
                 lambda raw: [c["choices"][0]["delta"].get("content", "") for c in sse_json(raw)]),
                ("/v1/completions", {"model": "bench/core", "stream": True, "prompt": user},
                 lambda raw: [c["choices"][0]["text"] for c in sse_json(raw)]),
                ("/v1/messages", {"model": "bench/core", "stream": True, "max_tokens": 8,
                                  "messages": [{"role": "user", "content": user}]},
                 lambda raw: [F.anthropic_delta(e) for e in sse_json(raw)]),
                ("/v1/responses", {"model": "bench/core", "stream": True, "input": user},
                 lambda raw: [F.responses_delta(e) for e in sse_json(raw)]),
                ("/api/chat", {"model": "bench/core", "messages": [{"role": "user", "content": user}]},
                 lambda raw: [F.ollama_delta(json.loads(line)) for line in raw.splitlines()]),
                ("/api/generate", {"model": "bench/core", "prompt": user},
                 lambda raw: [F.ollama_delta(json.loads(line)) for line in raw.splitlines()]),
                ("/v1beta/models/bench/core:streamGenerateContent?alt=sse", {"contents": [{"parts": [{"text": user}]}]},
                 lambda raw: [F.gemini_delta(e) for e in sse_json(raw)]),
                ("/v1beta/models/bench/core:streamGenerateContent", {"contents": [{"parts": [{"text": user}]}]},
                 lambda raw: [F.gemini_delta(c) for c in json.loads(raw)])):
            status, headers, raw, first = call(path, body)
            assert status == 200 and first < 0.6, (path, status, first, raw)          # before m1 has answered
            said = "".join(parse(raw))
            assert said.index("answer from m2") < said.index("answer from m1") and said.startswith("Bench bench/core"), (path, said)
            assert "Record a verdict" in said, (path, said)
            if "ndjson" in headers["Content-Type"]:
                rows = [json.loads(line) for line in raw.splitlines()]
                assert any(not r["done"] and not F.ollama_delta(r) for r in rows), raw   # an empty line kept it alive
                assert rows[-1]["done"] and rows[-1]["bench"]["seats"], raw
            elif path.startswith("/v1beta"):
                assert headers["Content-Type"] == ("text/event-stream" if "alt=sse" in path else "application/json")
            else:
                assert headers["Content-Type"] == "text/event-stream" and ("comment" in {e for e, _ in sse_events(raw)}
                                                                           or "event: ping" in raw), (path, raw)
            if path == "/v1/messages":
                kinds = [e["type"] for e in sse_json(raw)]
                assert kinds[:2] == ["message_start", "content_block_start"] and kinds[-1] == "message_stop", kinds
                assert "ping" in kinds and next(e for e in sse_json(raw) if e["type"] == "message_delta")["bench"]["seats"]
            if path == "/v1/responses":
                done = sse_json(raw)[-1]
                assert done["type"] == "response.completed" and done["response"]["bench"]["seats"], done
                assert F.responses_text(done["response"]) == said
                assert [e["sequence_number"] for e in sse_json(raw)] == list(range(len(sse_json(raw))))
        # Merged and streamed: keep-alives, then the merged answer once, then the end.
        status, _, raw, first = call("/v1/messages", {"model": "bench/merge", "stream": True, "max_tokens": 8,
                                                      "messages": [{"role": "user", "content": user}]})
        texts = [t for t in (F.anthropic_delta(e) for e in sse_json(raw)) if t]
        assert status == 200 and first < 0.6 and len(texts) == 1 and texts[0].startswith("the merged answer"), raw
        assert sse_json(raw)[1]["type"] == "content_block_start" and "event: ping" in raw

        seats.delay["m1"] = 0.2
        # A refusal before the panel is seated keeps its status, in the format's shape, streamed or not.
        for path, body, shape in (
                ("/v1/messages", {"model": "bench/small", "stream": True, "max_tokens": 8,
                                  "messages": [{"role": "user", "content": "x" * 400}]}, lambda e: e["error"]["type"]),
                ("/api/chat", {"model": "bench/small", "messages": [{"role": "user", "content": "x" * 400}]},
                 lambda e: e["error"]),
                ("/v1beta/models/bench/small:streamGenerateContent?alt=sse",
                 {"contents": [{"parts": [{"text": "x" * 400}]}]}, lambda e: e["error"]["status"]),
                ("/v1/responses", {"model": "bench/small", "stream": True, "input": "x" * 400},
                 lambda e: e["error"]["message"])):
            status, _, text, _ = call(path, body)
            assert status == 413 and shape(json.loads(text)), (path, status, text)
        # An error after the 200 goes into the stream, in the format's error shape: a seat that
        # raises once the heading is out (bench/broken seats m1 and "boom").
        seats.delay["boom"] = 0.3
        for path, body, find in (
                ("/v1/chat/completions", {"model": "bench/broken", "stream": True,
                                          "messages": [{"role": "user", "content": user}]},
                 lambda raw: sse_json(raw)[-1]["error"]["message"]),
                ("/v1/messages", {"model": "bench/broken", "stream": True, "max_tokens": 8,
                                  "messages": [{"role": "user", "content": user}]},
                 lambda raw: sse_json(raw)[-1]["error"]["message"]),
                ("/v1/responses", {"model": "bench/broken", "stream": True, "input": user},
                 lambda raw: sse_json(raw)[-1]["response"]["error"]["message"]),
                ("/api/chat", {"model": "bench/broken", "messages": [{"role": "user", "content": user}]},
                 lambda raw: json.loads(raw.splitlines()[-1])["error"]),
                ("/v1beta/models/bench/broken:streamGenerateContent", {"contents": [{"parts": [{"text": user}]}]},
                 lambda raw: json.loads(raw)[-1]["error"]["message"])):
            status, _, raw, _ = call(path, body)
            assert status == 200 and "the seat's process died" in find(raw), (path, raw)

        # Refused before any seat is asked: tools, an unknown collection, in each format's shape.
        seats.asked.clear()
        for path, body, msg in (
                ("/v1/chat/completions", {"model": "bench/core", "messages": [{"role": "user", "content": user}],
                                          "tools": [{"type": "function", "function": {"name": "f"}}]},
                 lambda e: e["error"]["message"]),
                ("/v1/messages", {"model": "bench/core", "max_tokens": 8, "tools": [{"name": "f"}],
                                  "messages": [{"role": "user", "content": user}]}, lambda e: e["error"]["message"]),
                ("/v1/responses", {"model": "bench/core", "input": user, "tools": [{"type": "function"}]},
                 lambda e: e["error"]["message"]),
                ("/api/chat", {"model": "bench/core", "tools": [{}], "messages": [{"role": "user", "content": user}]},
                 lambda e: e["error"]),
                ("/v1beta/models/bench/core:generateContent", {"contents": [{"parts": [{"text": user}]}],
                                                               "tools": [{"functionDeclarations": [{}]}]},
                 lambda e: e["error"]["message"])):
            status, _, text, _ = call(path, body)
            assert status == 400 and "route mode" in msg(json.loads(text)), (path, status, text)
        status, _, text, _ = call("/v1/messages", {"model": "bench/nope", "max_tokens": 8,
                                                   "messages": [{"role": "user", "content": user}]})
        assert status == 404 and json.loads(text)["error"]["type"] == "not_found_error", text
        assert "bench/core" in json.loads(text)["error"]["message"]             # the collections, to choose from
        status, _, text, _ = call("/v1beta/models/bench/nope:generateContent", {"contents": [{"parts": [{"text": "x"}]}]})
        assert status == 404 and json.loads(text)["error"]["status"] == "NOT_FOUND", text
        status, _, text, _ = call("/v1beta/models/bench/core:embedContent", {"content": {}})
        assert status == 404 and "generateContent" in text, text
        assert not seats.asked, seats.asked

        # Listing, in each format's shape: every collection, and each in its other reply mode.
        tags = json.loads(ok("/api/tags")[0])["models"]
        assert {"bench/core", "bench/core:merged", "bench/merge", "bench/merge:all"} <= {m["name"] for m in tags}, tags
        assert json.loads(ok("/api/version")[0]) == {"version": F.OLLAMA_VERSION}
        shown = json.loads(ok("/api/show", {"model": "bench/core"})[0])
        assert "vision" in shown["capabilities"] and shown["details"]["family"] == "benchwarmers", shown
        assert json.loads(ok("/api/show", {"name": "core"})[0])["capabilities"]
        status, _, text, _ = call("/api/show", {"model": "bench/nope"})
        assert status == 404 and "error" in json.loads(text), text
        names = [m["name"] for m in json.loads(ok("/v1beta/models")[0])["models"]]
        assert "models/bench/core" in names and "models/bench/core:merged" in names, names
        one = json.loads(ok("/v1beta/models/bench/core")[0])
        assert one["name"] == "models/bench/core" and "generateContent" in one["supportedGenerationMethods"]
        assert call("/v1beta/models/bench/nope")[0] == 404
        # Token counts are estimates, without a model call; an image counts IMAGE_TOKENS.
        counted = {"model": "bench/core", "messages": [
            {"role": "user", "content": [{"type": "text", "text": "x" * 400},
                                         {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                                      "data": PIXEL}}]}]}
        n = json.loads(ok("/v1/messages/count_tokens", counted)[0])["input_tokens"]
        assert n == gateway.prompt_tokens(F.anthropic_ask(counted)["messages"]), n
        assert F.estimate("x" * 400) + gateway.IMAGE_TOKENS <= n < F.estimate("x" * 400) + gateway.IMAGE_TOKENS + 20, n
        n = json.loads(ok("/v1beta/models/bench/core:countTokens", {"contents": [{"parts": [{"text": "x" * 400}]}]})[0])
        assert n == {"totalTokens": F.estimate("x" * 400)}, n
        assert not seats.asked

        # The dashboard's port answers Ollama's /api/ routes as the gateway's, beside its own.
        text, _ = ok("/api/tags", on=dash.server_port)
        assert "bench/core" in text
        assert call("/api/stamp", on=dash.server_port)[0] == 200

        # KEYS: x-api-key and x-goog-api-key anywhere, ?key= on Gemini routes only; never logged.
        os.environ["BENCHWARMERS_REQUIRE_KEYS"] = "1"
        key = guard.make_key("sdk")
        sys.stderr = log = io.StringIO()
        ask = {"model": "bench/core", "max_tokens": 8, "messages": [{"role": "user", "content": user}]}
        status, h, text, _ = call("/v1/messages", ask)
        assert status == 401 and json.loads(text)["error"]["type"] == "authentication_error", text
        assert h["WWW-Authenticate"].startswith("Bearer")
        assert call("/v1/messages", ask, **{"x-api-key": key})[0] == 200
        assert call("/v1/messages", ask, **{"x-api-key": "sk-ant-dummy"})[0] == 401
        assert call("/api/tags", Authorization=f"Bearer {key}")[0] == 200
        assert call(f"/v1beta/models?key={key}")[0] == 200
        assert call("/v1beta/models", **{"x-goog-api-key": key})[0] == 200
        assert call(f"/v1beta/models/bench/core:generateContent?alt=sse&key={key}",
                    {"contents": [{"parts": [{"text": user}]}]})[0] == 200
        status, _, text, _ = call(f"/v1/models?key={key}")                    # not in an OpenAI route's URL
        assert status == 401, text
        # A request line too broken to parse is quoted by http.server's own error log: the key is not.
        with socket.create_connection(("127.0.0.1", port), timeout=10) as raw:
            raw.sendall(f"GET /v1beta/models?alt=sse&key={key} HTTP/1.1 extra\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
            raw.recv(4096)
        time.sleep(0.2)
        sys.stderr = saved_err
        lines = log.getvalue()
        assert "POST /v1/messages 200 sdk" in lines and "GET /v1beta/models 200 sdk" in lines, lines
        assert "Bad request" in lines and key not in lines and key[4:12] not in lines, lines
        os.environ.pop("BENCHWARMERS_REQUIRE_KEYS")
    finally:
        sys.stderr = saved_err
        os.environ.pop("BENCHWARMERS_REQUIRE_KEYS", None)
        gw.shutdown()
        dash.shutdown()


def main() -> int:
    try:
        translators()
        gateway_routes()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
