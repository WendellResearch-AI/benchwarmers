"""The built-in client against `llm`, on a fake OpenAI-compatible server.

    python scripts/test_client.py

No model is called and nothing of the owner's is read or written: `llm` runs
with LLM_USER_PATH pointed at a temp folder holding its own registry, key and
options, and metrics/client.py is pointed at the same files. Each scenario is
one model name the fake server answers in its own way (a reply, an empty
reply, a 404, a 429 that never clears, one that clears, an HTML 502, a reply
too slow for the deadline, a credit notice), plus an endpoint that refuses the
connection.

For every scenario both backends go through suite/run.py's ask_once and must
land in the same place: the same `outcome` and `failure_class`, at the suite's
length floor and the perf tier's, and the same text when the model answered.
That is what Phase 2a promises before the client may become the default. The
requests are compared too: model, messages, options, key and headers, and
that both ask for a stream (Phase 2b). Then the built-in client's own: the
usage from the stream's last chunk and its ledger line, and a call refused
by pacing, which must land where llm's real 429 does.

The data folder is a temp one too (BENCHWARMERS_HOME, set before any module
reads it): the built-in client now writes a ledger line and quota records
for every call.
"""

from __future__ import annotations

import json
import os
import pathlib
import socket
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DATA = pathlib.Path(tempfile.mkdtemp(prefix="bench-client-data-"))
os.environ["BENCHWARMERS_HOME"] = str(DATA)
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "metrics"), str(ROOT / "suite")]
import client  # noqa: E402
import endpoints  # noqa: E402
import ledger  # noqa: E402
import run  # noqa: E402

ANSWER = "The quick brown fox jumps over the lazy dog, and then does it twice."
seen: dict[tuple[str, str], list[dict]] = {}      # (backend, model) -> requests
OTHER = ""                                         # the second server's base, set in main()
leaked: list[str] = []                             # Authorization headers the other origin received


class Other(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _any(self):
        leaked.append(self.headers.get("Authorization") or "")
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = do_POST = _any


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):                                 # noqa: N802
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        who = "llm" if "OpenAI" in (self.headers.get("User-Agent") or "") else "builtin"
        calls = seen.setdefault((who, req["model"]), [])
        calls.append({"body": req, "headers": {k.lower(): v for k, v in self.headers.items()}})
        model = req["model"]
        try:
            if model == "redirect":                    # to another origin: the key must not follow
                self.send_response(302)
                self.send_header("Location", f"{OTHER}/chat/completions")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if model == "trickle":                     # headers at once, then a byte a while
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "1000")
                self.end_headers()
                for _ in range(40):
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    time.sleep(0.25)
                return
            if model == "cutoff":                      # an error whose body breaks off
                self.send_response(502)
                self.send_header("Content-Length", "100")
                self.end_headers()
                self.wfile.write(b"<html>")
                self.wfile.flush()
                self.connection.shutdown(socket.SHUT_RDWR)
                return
            if model == "nochoices":
                return self._send(200, b"{}")
            if model == "notfound":
                return self._send(404, b'{"error": {"message": "Model not found", "code": 404}}')
            if model == "ratelimit":
                return self._send(429, json.dumps({"error": {
                    "message": "Rate limit exceeded: free-models-per-day", "code": 429}}).encode(),
                    extra={"Retry-After": "1"})
            if model == "flaky" and len(calls) == 1:
                return self._send(429, b'{"error": {"message": "slow down", "code": 429}}',
                                  extra={"Retry-After": "1"})
            if model == "html502":
                return self._send(502, b"<html><head><title>502 Bad Gateway</title></head></html>",
                                  "text/html")
            if model == "slow":
                time.sleep(6)
            content = {"empty": None, "short": "ready",
                       "notice": "You have run out of credits. Please add credits."}.get(model, ANSWER)
            message = {"role": "assistant", "content": content}
            if model == "empty":
                message["reasoning_content"] = "thinking, and out of budget"
            if req.get("stream"):
                head = {"id": "c1", "object": "chat.completion.chunk", "created": 0, "model": model}
                chunks = [{**head, "choices": [{"index": 0, "delta": message, "finish_reason": None}]},
                          {**head, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                          {**head, "choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 5,
                                                            "total_tokens": 8}}]
                body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
                return self._send(200, body.encode(), "text/event-stream")
            return self._send(200, json.dumps({
                "id": "c1", "object": "chat.completion", "created": 0, "model": model,
                "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8}}).encode())
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass                                       # the caller gave up on "slow"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    global OTHER
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    other = ThreadingHTTPServer(("127.0.0.1", 0), Other)
    for s in (srv, other):
        s.daemon_threads = True
        threading.Thread(target=s.serve_forever, daemon=True).start()
    live, dead = f"http://127.0.0.1:{srv.server_address[1]}/v1", f"http://127.0.0.1:{free_port()}/v1"
    OTHER = f"http://localhost:{other.server_address[1]}/v1"

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="bench-client-"))
    scenarios = ["ok", "short", "empty", "notfound", "ratelimit", "flaky", "html502", "slow", "notice",
                 "refused", "nokey"]
    own = ["redirect", "trickle", "cutoff", "nochoices", "paced"]      # the built-in client only
    # The endpoint the live scenarios are on, so they share a connection ("fake")
    # and pacing has one to limit: one call a minute for the paced model.
    (DATA / "config").mkdir(parents=True, exist_ok=True)
    (DATA / "config" / "endpoints.json").write_text(json.dumps(
        {"fake": {"base": live, "key": "fake", "kind": "provider", "default": True}}), encoding="utf-8")
    (DATA / "config" / "model-overrides.json").write_text('{"t-paced": {"limits": {"rpm": 1}}}',
                                                          encoding="utf-8")
    yaml = []
    for s in scenarios + own:
        yaml += [f"- model_id: t-{s}", f"  model_name: {'ok' if s in ('refused', 'nokey') else s}",
                 f'  api_base: "{dead if s == "refused" else live}"',
                 f"  api_key_name: {'unset' if s == 'nokey' else 'fake'}"]
        if s == "ok":
            yaml += ["  headers:", '    X-Title: "bench test"']
    (tmp / "extra-openai-models.yaml").write_text("\n".join(yaml) + "\n", encoding="utf-8")
    (tmp / "keys.json").write_text('{"fake": "sk-test-not-a-key"}', encoding="utf-8")
    (tmp / "model_options.json").write_text('{"t-ok": {"max_tokens": "123"}}', encoding="utf-8")
    (tmp / "options.json").write_text('{"t-ok": {"max_tokens": 123}}', encoding="utf-8")
    os.environ["LLM_USER_PATH"] = str(tmp)            # llm reads only the temp folder
    endpoints.YAML, endpoints.LLM_DIR, client.OPTIONS = (tmp / "extra-openai-models.yaml", tmp,
                                                         tmp / "options.json")

    binary, failures, prompt = run.llm_binary(), 0, "Say something."

    def kinds(x):
        return ((run.outcome(x) or ("answered",))[0], (run.outcome(x, 1) or ("answered",))[0],
                run.failure_class(x), run.failure_class(x, 1))
    bodies_429 = {}
    for s in scenarios:
        bodies = {}
        for backend in ("llm", "builtin"):
            os.environ["BENCH_CLIENT"] = backend
            # On Windows a refused connection takes about 2 s and the SDK retries:
            # llm needed 19-41 s for "refused" there, past a 30 s deadline.
            bodies[backend], _ = run.ask_once(binary, f"t-{s}", prompt, {"slow": 4, "refused": 90}.get(s, 30))
        a, b = bodies["llm"], bodies["builtin"]
        if s == "ratelimit":
            bodies_429 = bodies
        same = kinds(a) == kinds(b) and (a == b or kinds(a)[1] != "answered")
        print(("ok   " if same else "FAIL ") + f"{s:<10} {kinds(b)}")
        if not same:
            print(f"       llm:     {a[:150]!r}\n       builtin: {b[:150]!r}")
        failures += not same

    # The request itself: same model, messages, options, key and headers.
    sent = {who: seen[(who, "ok")][-1] for who in ("llm", "builtin")}
    want = {k: sent["llm"]["body"].get(k) for k in ("model", "messages", "max_tokens")}
    got = {k: sent["builtin"]["body"].get(k) for k in ("model", "messages", "max_tokens")}
    hdr = [(sent[w]["headers"].get("authorization"), sent[w]["headers"].get("x-title")) for w in sent]
    ok = want == got and want["max_tokens"] == 123 and hdr[0] == hdr[1] == ("Bearer sk-test-not-a-key",
                                                                             "bench test")
    print(("ok   " if ok else "FAIL ") + f"request matches: {got}, {hdr[1]}"
          + ("" if ok else f"\n       llm sent: {want}, {hdr[0]}"))
    failures += not ok
    # Both stream (2b), as llm always has; the built-in client asks for usage in the stream.
    streams = [sent[w]["body"].get("stream") for w in ("llm", "builtin")]
    ok = streams == [True, True] and sent["builtin"]["body"].get("stream_options") == {"include_usage": True}
    print(("ok   " if ok else "FAIL ") + f"both ask for a stream (llm {streams[0]}, built-in {streams[1]},"
          f" usage {sent['builtin']['body'].get('stream_options')})")
    failures += not ok
    # Retries as the SDK does them: three tries at a 429 that never clears.
    tries = [len(seen.get((w, "ratelimit"), [])) for w in ("llm", "builtin")]
    ok = tries == [3, 3]
    print(("ok   " if ok else "FAIL ") + f"429 tried {tries[1]} times (llm {tries[0]})")
    failures += not ok

    # Usage from the stream's last chunk, in the result and in the ledger.
    r = client.chat("t-ok", prompt, timeout=30)
    line = [x for x in ledger.rows() if x["alias"] == "t-ok"][-1]
    ok = (r["usage"].get("completion_tokens") == 5 and line["tokens_in"] == 3 and line["tokens_out"] == 5
          and line["status"] == "ok" and line["connection"] == "fake")
    print(("ok   " if ok else "FAIL ") + f"usage from the stream and its ledger line: {r['usage']}, {line}")
    failures += not ok
    # Pacing: the model's own rpm 1; the second call in the minute is not sent, and
    # lands where llm's real 429 lands ("ratelimit" above), at both length floors.
    first = client.chat("t-paced", prompt, timeout=30)
    body = run.ask_once(binary, "t-paced", prompt, 30)[0]
    want = kinds(bodies_429["llm"])
    ok = not first["error"] and "not sent" in body and kinds(body) == want \
        and len(seen.get(("builtin", "paced"), [])) == 1
    print(("ok   " if ok else "FAIL ") + f"a paced refusal lands where a real 429 does: {kinds(body)}"
          + ("" if ok else f"\n       llm's 429: {want}; refusal: {body[:150]!r}"))
    failures += not ok

    # Where the built-in client is stricter than urllib's defaults would be.
    r = client.chat("t-redirect", prompt, timeout=10)
    ok = r["status"] == 302 and "not followed" in r["error"] and not any(leaked)
    print(("ok   " if ok else "FAIL ") + f"redirect refused, key kept: {r['error'][:60]!r}")
    failures += not ok
    t = time.monotonic()
    r = client.chat("t-trickle", prompt, timeout=3)
    ok = r["timed_out"] and time.monotonic() - t < 5
    print(("ok   " if ok else "FAIL ") + f"trickling reply cut at the deadline ({time.monotonic() - t:.1f}s)")
    failures += not ok
    r = client.chat("t-cutoff", prompt, timeout=20)
    ok = r["error"].startswith("Error: Error code: 502") and run.failure_class(client.output(r, 20)) == "capacity"
    print(("ok   " if ok else "FAIL ") + f"broken-off error body, no crash: {r['error'][:40]!r}")
    failures += not ok
    r = client.chat("t-nochoices", prompt, timeout=10)
    ok = "no choices" in r["error"] and run.failure_class(client.output(r, 10)) == "other"
    print(("ok   " if ok else "FAIL ") + "a 200 with no choice is an error, not an empty answer")
    failures += not ok

    srv.shutdown()
    print("\nthe built-in client lands where llm does" if not failures else f"\n{failures} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
