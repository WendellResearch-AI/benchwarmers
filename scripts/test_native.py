"""The built-in client on native Anthropic Messages and Gemini APIs, against fake servers.

    python scripts/test_native.py

An endpoint whose `api` is `anthropic` or `gemini`: the request is built
by metrics/formats.py (system text apart, the model's own max_tokens, an
image as the API's own part), sent with the API's key header and streamed;
the reply is read into the client's usual result (text, reasoning, usage,
served model, finish) and its ledger line; an HTTP error and an error sent
mid-stream keep the error-text contract (`Error: Error code: 429 - ...`,
529 for Anthropic's overloaded_error), a stream that breaks off is
`client failed`. Each endpoint's model list in its own shape, the
dashboard's Add form saving the choice, and such a model always going
through the built-in client. Temp data folder; no model is called.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
TMP = pathlib.Path(tempfile.mkdtemp(prefix="bench-native-"))
(TMP / "config").mkdir()
(TMP / "llm").mkdir()
os.environ.update(BENCHWARMERS_HOME=str(TMP), LLM_USER_PATH=str(TMP / "llm"), BENCH_CLIENT="llm",
                  BENCHWARMERS_KEY_ANT="sk-ant-test", BENCHWARMERS_KEY_GEM="gm-test")
sys.path[:0] = [str(ROOT), str(ROOT / "metrics"), str(ROOT / "suite")]

PIXEL = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="


def sse(events, named=False) -> bytes:
    return b"".join(((f"event: {e['type']}\n" if named and "type" in e else "") + f"data: {json.dumps(e)}\n\n").encode()
                    for e in events)


class Native(BaseHTTPRequestHandler):
    """Anthropic under /a/v1, Gemini under /g/v1beta; the model name picks the behaviour."""
    seen: list[tuple[str, dict, dict]] = []

    def log_message(self, *a):
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):                                  # noqa: N802
        Native.seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}, {}))
        if self.path.startswith("/a/v1/models"):
            return self._send(200, json.dumps({"data": [{"id": "claude-x", "type": "model"}]}).encode())
        return self._send(200, json.dumps({"models": [
            {"name": "models/gemini-x", "supportedGenerationMethods": ["generateContent", "countTokens"]},
            {"name": "models/embed-x", "supportedGenerationMethods": ["embedContent"]}]}).encode())

    def do_POST(self):                                 # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Native.seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
        if self.path.startswith("/a/"):
            model = body["model"]
            if model == "claude-busy":
                return self._send(429, json.dumps({"type": "error", "error": {
                    "type": "rate_limit_error", "message": "slow down"}}).encode())
            events = [{"type": "message_start", "message": {"model": "claude-x-20261001", "usage": {"input_tokens": 12}}},
                      {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
                      {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "hmm"}},
                      {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
                      {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "rea"}},
                      {"type": "ping"},
                      {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "dy"}},
                      {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 5}},
                      {"type": "message_stop"}]
            if model == "claude-overloaded":
                events = events[:5] + [{"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}]
            if model == "claude-cut":
                events = events[:5]
            return self._send(200, sse(events, named=True), "text/event-stream")
        if "gemini-quota" in self.path:
            return self._send(429, json.dumps({"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}}).encode())
        chunks = [{"candidates": [{"content": {"role": "model", "parts": [{"text": "thinking", "thought": True}]}}]},
                  {"candidates": [{"content": {"role": "model", "parts": [{"text": "rea"}]}}]},
                  {"candidates": [{"content": {"role": "model", "parts": [{"text": "dy"}]}, "finishReason": "STOP"}],
                   "modelVersion": "gemini-x", "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 2,
                                                                      "thoughtsTokenCount": 3}}]
        return self._send(200, sse(chunks), "text/event-stream")


def write(name: str, data) -> None:
    (TMP / "config" / name).write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Native)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    write("endpoints.json", {"ant": {"base": f"{base}/a/v1", "key": "ant", "kind": "provider", "api": "anthropic",
                                     "default": True},
                             "gem": {"base": f"{base}/g/v1beta", "key": "gem", "kind": "provider", "api": "gemini"}})
    import endpoints
    write("extra-openai-models.yaml", "".join(endpoints.yaml_block(a, a, "ant") for a in
                                              ("claude-x", "claude-busy", "claude-overloaded", "claude-cut"))
          + "".join(endpoints.yaml_block(a, a, "gem") for a in ("gemini-x", "gemini-quota")))
    write("model-options.json", {"claude-x": {"max_tokens": 700, "temperature": 0.3}})
    import client
    import ledger
    try:
        # ANTHROPIC: the native request, its key header, the stream read back.
        heard = []
        res = client.chat("claude-x", "Say ready.", system="Be brief.", timeout=30,
                          on_delta=lambda kind, text: heard.append((kind, text)))
        path, headers, body = Native.seen[-1]
        assert path == "/a/v1/messages" and headers["x-api-key"] == "sk-ant-test" and "authorization" not in headers
        assert headers["anthropic-version"] == client.ANTHROPIC_VERSION
        assert body == {"model": "claude-x", "max_tokens": 700, "temperature": 0.3, "stream": True, "system": "Be brief.",
                        "messages": [{"role": "user", "content": "Say ready."}]}, body
        assert (res["text"], res["reasoning"], res["model"], res["finish"], res["error"]) == \
            ("ready", "hmm", "claude-x-20261001", "stop", ""), res
        assert res["usage"] == {"input_tokens": 12, "output_tokens": 5} and heard[0] == ("reasoning", "hmm"), (res, heard)
        row = list(ledger.rows(0))[-1]
        assert (row["alias"], row["status"], row["tokens_in"], row["tokens_out"]) == ("claude-x", "ok", 12, 5), row
        assert not row.get("swap")                                   # a dated name is the same model
        # An image goes as Anthropic's own base64 block.
        client.chat("claude-x", "", timeout=30, messages=[{"role": "user", "content": [
            {"type": "text", "text": "what?"}, {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{PIXEL}"}}]}])
        assert Native.seen[-1][2]["messages"][0]["content"][1] == {
            "type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PIXEL}}
        # Errors keep the contract: a status, an error sent mid-stream, a cut stream.
        busy = client.chat("claude-busy", "x", timeout=30, retries=0)
        assert busy["error"].startswith("Error: Error code: 429 - ") and "rate_limit_error" in busy["error"], busy
        over = client.chat("claude-overloaded", "x", timeout=30)
        assert over["error"].startswith("Error: Error code: 529 - ") and "Overloaded" in over["error"], over
        cut = client.chat("claude-cut", "x", timeout=30)
        assert cut["error"].startswith("Error: client failed: the stream broke off"), cut
        # Tools are not sent to a native API from here (route mode passes chat completions only).
        refused = client.chat("claude-x", "x", timeout=30, body={"tools": []})
        assert refused["error"].startswith("Error: client failed: ") and "chat completions only" in refused["error"]

        # GEMINI: the native request at its own path, its key header, the stream read back.
        res = client.chat("gemini-x", "Say ready.", system="Be brief.", timeout=30)
        path, headers, body = Native.seen[-1]
        assert path == "/g/v1beta/models/gemini-x:streamGenerateContent?alt=sse", path
        assert headers["x-goog-api-key"] == "gm-test" and "authorization" not in headers
        assert body == {"contents": [{"role": "user", "parts": [{"text": "Say ready."}]}],
                        "systemInstruction": {"parts": [{"text": "Be brief."}]}}, body
        assert (res["text"], res["reasoning"], res["model"], res["finish"]) == ("ready", "thinking", "gemini-x", "stop")
        assert res["usage"] == {"prompt_tokens": 7, "completion_tokens": 2,
                                "completion_tokens_details": {"reasoning_tokens": 3}}, res
        row = list(ledger.rows(0))[-1]
        assert (row["tokens_in"], row["tokens_out"], row["reasoning"]) == (7, 2, 3), row
        quota = client.chat("gemini-quota", "x", timeout=30, retries=0)
        assert quota["error"].startswith("Error: Error code: 429 - ") and "RESOURCE_EXHAUSTED" in quota["error"], quota

        # Each one's model list in its own shape, asked with its own key header.
        assert list(endpoints.models("ant")) == ["claude-x"] and Native.seen[-1][1]["x-api-key"] == "sk-ant-test"
        assert list(endpoints.models("gem")) == ["gemini-x"]                       # generateContent models only
        assert Native.seen[-1][0] == "/g/v1beta/models?pageSize=1000"
        # Neither goes through `llm -m`, whatever BENCH_CLIENT says.
        assert client.backend("claude-x") == client.backend("gemini-x") == "builtin" and client.backend() == "llm"

        # The dashboard's Add form: tested with the API's own key header, saved with the choice.
        import serve
        got = serve.save_endpoint("ant2", f"{base}/a/v1", "sk-ant-2", new=True, api="anthropic", test_only=True)
        # (seen[-1] is the form's second ask, without a key, to say whether the list proves it.)
        assert got["ok"] and got["served"] == 1 and Native.seen[-2][1]["x-api-key"] == "sk-ant-2", got
        assert serve.save_endpoint("ant2", f"{base}/a/v1", "sk-ant-2", new=True, api="anthropic")["ok"]
        assert endpoints.load()["ant2"]["api"] == "anthropic"
        assert not serve.save_endpoint("x2", f"{base}/a/v1", "k", new=True, api="responses")["ok"]   # Connect's only
        assert "<select id='e-api'>" in serve.editor()
    finally:
        srv.shutdown()
        shutil.rmtree(TMP, ignore_errors=True)
    print("native Anthropic and Gemini: requests, streams, usage, errors and the Add form hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
