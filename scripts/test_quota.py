"""The built-in client's Phase 2b, on a fake server: streaming, the usage
ledger, quota records, pacing and the free-tier guard.

    python scripts/test_quota.py

No model is called and nothing of the install's is read or written:
BENCHWARMERS_HOME and LLM_USER_PATH name temp folders before any module
reads them. Each scenario is one model name the fake server answers in its
own way; the pacing ones sit on endpoints of their own, so their counts do
not mix. Covered:

- a streamed answer: text and reasoning apart, each piece heard by on_delta,
  usage from the final chunk, the first token's time; and its ledger line;
- an upstream that answers plain JSON to a stream request, and one that
  refuses stream_options (asked again without);
- a reply naming another model: the call succeeds, the swap is noted;
- an empty answer cut off by its budget: tried once more with twice the budget;
- a stream that breaks off, and STOP between chunks;
- quota records from rate-limit headers, the next call waiting out the reset;
  a Gemini 429 body's daily quota learned as a limit;
- an rpm limit across six threads at once and across two processes (calls in
  flight count), a monthly cap (rpmo), and both refusals classed as capacity;
- the free-tier guard holding a model that went paid, until it is allowed;
- metrics/cost.py reading the ledger.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import pathlib
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# A child process (the cross-process check) re-runs this module: it must find the parent's folder.
TMP = pathlib.Path(os.environ.get("BENCH_QUOTA_TMP") or tempfile.mkdtemp(prefix="bench-quota-"))
os.environ.update(BENCHWARMERS_HOME=str(TMP), LLM_USER_PATH=str(TMP / "llm"), BENCH_CLIENT="builtin",
                  BENCH_QUOTA_TMP=str(TMP))
os.environ.pop("BENCH_ALLOW_PAID", None)
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "metrics"), str(ROOT / "suite")]

import alerts  # noqa: E402
import client  # noqa: E402
import cost  # noqa: E402
import learn  # noqa: E402
import ledger  # noqa: E402
import quota  # noqa: E402
import run  # noqa: E402

seen: dict[str, list[dict]] = {}
lock = threading.Lock()


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

    def _stream(self, model: str, deltas: list[dict], finish: str = "stop", usage=None, served=None,
                extra=None, gap: float = 0.0, close_early: bool = False):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        head = {"id": "c1", "object": "chat.completion.chunk", "created": 0, "model": served or model}
        for d in deltas:
            self.wfile.write(f"data: {json.dumps({**head, 'choices': [{'index': 0, 'delta': d}]})}\n\n".encode())
            self.wfile.flush()
            time.sleep(gap)
        if close_early:
            return                                     # HTTP/1.0: the connection closes, no finish, no [DONE]
        tail = [{**head, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
                {**head, "choices": [], "usage": usage or {"prompt_tokens": 7, "completion_tokens": 4,
                                                          "total_tokens": 11}}]
        self.wfile.write(("".join(f"data: {json.dumps(c)}\n\n" for c in tail) + "data: [DONE]\n\n").encode())

    def do_POST(self):                                 # noqa: N802
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        model = req["model"]
        with lock:
            calls = seen.setdefault(model, [])
            calls.append(req)
            n = len(calls)
        try:
            if model == "plain" or (model == "nooptions" and "stream_options" in req):
                if model == "nooptions":
                    return self._send(400, b'{"error": {"message": "Unrecognized request argument supplied:'
                                           b' stream_options"}}')
                return self._send(200, json.dumps({"model": model, "choices": [{"message": {
                    "content": "plain answer"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 2}}).encode())
            if model == "budget" and (req.get("max_tokens") or 0) < 100:
                return self._stream(model, [{"reasoning_content": "thinking and thinking"}], finish="length",
                                    usage={"prompt_tokens": 5, "completion_tokens": req.get("max_tokens")})
            if model == "ratelimited" and n <= 3:
                return self._send(429, b'{"error": {"message": "slow down", "code": 429}}', extra={
                    "Retry-After": "0.1", "x-ratelimit-limit-requests": "3",
                    "x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "2s"})
            if model == "gemini":
                return self._send(429, json.dumps([{"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                    "message": "Quota exceeded", "details": [
                        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
                            {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                             "quotaValue": "50"}]},
                        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "0.1s"}]}}]).encode())
            if model == "paced":
                time.sleep(0.4)                        # long enough that the seats overlap
            if model == "dribble":
                return self._stream(model, [{"content": "x"}] * 25, gap=0.2)
            if model == "breakoff":
                return self._stream(model, [{"content": "half an answer"}], close_early=True)
            return self._stream(model, [{"reasoning_content": "let me "}, {"reasoning_content": "see"},
                                        {"content": "The answer "}, {"content": "is ready."}],
                                served="someone-else-7b" if model == "swapped" else None,
                                extra={"x-ratelimit-remaining-requests": "99", "x-ratelimit-limit-requests": "100",
                                       "x-ratelimit-reset-requests": "6m0s"} if model == "ok" else None)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


def setup(port: int) -> None:
    base = f"http://127.0.0.1:{port}"
    eps = {"fakeprov": f"{base}/v1", "pacedprov": f"{base}/p/v1", "procprov": f"{base}/q/v1",
           "monthprov": f"{base}/m/v1"}
    (TMP / "config").mkdir(parents=True, exist_ok=True)
    (TMP / "llm").mkdir(parents=True, exist_ok=True)
    (TMP / "config" / "endpoints.json").write_text(json.dumps(
        {n: {"base": b, "key": "fake", "kind": "provider", **({"default": True} if n == "fakeprov" else {})}
         for n, b in eps.items()}), encoding="utf-8")
    where = {"paced": "pacedprov", "proc": "procprov", "month": "monthprov"}
    yaml = []
    for m in ("ok", "plain", "nooptions", "swapped", "budget", "ratelimited", "gemini", "dribble", "breakoff",
              "paced", "proc", "month", "paid"):
        yaml += [f"- model_id: t-{m}", f"  model_name: {'paced' if m in ('proc', 'month') else m}",
                 f'  api_base: "{eps[where.get(m, "fakeprov")]}"', "  api_key_name: fake"]
    (TMP / "config" / "extra-openai-models.yaml").write_text("\n".join(yaml) + "\n", encoding="utf-8")
    (TMP / "config" / "model-options.json").write_text('{"t-budget": {"max_tokens": 64}}', encoding="utf-8")
    (TMP / "config" / "connection-limits.json").write_text(json.dumps(
        {"pacedprov": {"rpm": 3}, "procprov": {"rpm": 4}, "monthprov": {"rpmo": 3}}), encoding="utf-8")
    (TMP / "llm" / "keys.json").write_text('{"fake": "sk-test-not-a-key"}', encoding="utf-8")


def _child(n: int) -> None:
    """One process's calls, for the cross-process pacing check."""
    for _ in range(n):
        client.chat("t-proc", "hi", timeout=5)


def main() -> int:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    setup(srv.server_address[1])
    failures = 0

    def check(ok: bool, what: str, detail="") -> None:
        nonlocal failures
        print(("ok   " if ok else "FAIL ") + what + ("" if ok else f"\n       {detail}"))
        failures += not ok

    def last(alias: str) -> dict:
        return [r for r in ledger.rows() if r["alias"] == alias][-1]

    # Streaming: text and reasoning apart, every piece heard, usage from the last chunk.
    heard = []
    r = client.chat("t-ok", "Say something.", on_delta=lambda k, t: heard.append((k, t)), purpose="test")
    req = seen["ok"][-1]
    check(r["text"] == "The answer is ready." and r["reasoning"] == "let me see" and r["finish"] == "stop",
          "a streamed answer, its reasoning kept apart", r)
    check(req.get("stream") is True and req.get("stream_options") == {"include_usage": True},
          "asked for a stream with usage", req)
    check(heard == [("reasoning", "let me "), ("reasoning", "see"), ("text", "The answer "), ("text", "is ready.")],
          "on_delta heard each piece in order", heard)
    check(r["usage"].get("completion_tokens") == 4 and r["first_token_s"] is not None and r["attempts"] == 1,
          "usage from the final chunk, first token timed", r)
    row = last("t-ok")
    check(row["status"] == "ok" and row["tokens_in"] == 7 and row["tokens_out"] == 4 and row["attempts"] == 1
          and row["connection"] == "fakeprov" and row["caller"] == "scripts/test_quota" and row["purpose"] == "test",
          "a ledger line with tokens, connection and caller", row)
    recs = quota.live("fakeprov", "t-ok")
    check(len(recs) == 1 and recs[0]["remaining"] == 99 and recs[0]["limit"] == 100,
          "the reply's rate-limit headers kept as a quota record", recs)

    # Upstreams that do not stream.
    r = client.chat("t-plain", "hi")
    check(r["text"] == "plain answer" and not r["error"], "plain JSON to a stream request is read as before", r)
    r = client.chat("t-nooptions", "hi")
    again = seen["nooptions"][-1]
    check(r["text"] == "The answer is ready." and r["attempts"] == 2 and "stream_options" not in again
          and again.get("stream") is True, "stream_options refused: asked once more without, still streamed", r)

    # A silent swap: the call succeeds and is noted.
    r = client.chat("t-swapped", "hi")
    swap = (learn.learned().get("t-swapped") or {}).get("served_as") or {}
    check(r["text"] and r["model"] == "someone-else-7b" and swap.get("served") == "someone-else-7b"
          and last("t-swapped").get("swap") is True, "a swapped model is noted, the answer kept", (r, swap))

    # An empty answer that ran out of budget: once more with twice the budget.
    r = client.chat("t-budget", "hi")
    tried = [c.get("max_tokens") for c in seen["budget"]]
    check(r["text"] == "The answer is ready." and tried == [64, 128]
          and r.get("budget_retry") == {"max_tokens": [64, 128], "answered": True}
          and last("t-budget")["attempts"] == 2, "an empty reply cut by its budget is retried once, larger",
          (tried, r))

    # A stream that breaks off, and STOP between chunks.
    r = client.chat("t-breakoff", "hi", timeout=10)
    check("broke off before it finished" in r["error"] and run.failure_class(client.output(r, 10)) == "other",
          "a stream that breaks off is an error, not a short answer", r["error"])
    threading.Timer(0.6, client.STOP.set).start()
    t = time.monotonic()
    r = client.chat("t-dribble", "hi", timeout=30)
    client.STOP.clear()
    check(r["timed_out"] and time.monotonic() - t < 3, f"STOP ends a stream between chunks ({time.monotonic() - t:.1f}s)", r)

    # Quota records: a 429's headers, then the next call waits for the reset.
    r = client.chat("t-ratelimited", "hi", timeout=20)
    # Read whole: the Retry-After record (0.1 s) has passed already, so live() leaves it out.
    recs = (quota.locked.read_json(quota.FILE).get("fakeprov") or {}).get("t-ratelimited") or {}
    check(r["status"] == 429 and r["attempts"] == 3 and recs.get("requests/unknown", {}).get("remaining") == 0
          and "requests/retry-after" in recs, "a 429's rate-limit headers kept as quota records", (r, recs))
    t = time.monotonic()
    r = client.chat("t-ratelimited", "hi", timeout=20)
    waited = time.monotonic() - t
    check(r["text"] and r["attempts"] == 1 and 1.0 <= waited < 6,
          f"the next call waited out the stated reset, then answered ({waited:.1f}s)", r)
    r = client.chat("t-gemini", "hi", timeout=20)
    rpd = ((learn.learned().get("t-gemini") or {}).get("limits") or {}).get("rpd") or {}
    check(r["status"] == 429 and rpd.get("value") == 50 and rpd.get("where") == "quota",
          "a Gemini 429 body's daily quota is learned as the model's rpd", rpd)

    # Pacing across threads: rpm 3, six seats at once, the server slow enough that they overlap.
    out: list[dict] = []
    seats = [threading.Thread(target=lambda: out.append(client.chat("t-paced", "hi", timeout=5)))
             for _ in range(6)]
    for s in seats:
        s.start()
    for s in seats:
        s.join()
    answered = [x for x in out if x["text"]]
    refused = [x for x in out if "rate limit (not sent" in x["error"]]
    check(len(answered) == 3 and len(refused) == 3 and len(seen["paced"]) == 3,
          f"rpm 3 across six threads: {len(answered)} sent, {len(refused)} refused", [x["error"][:80] for x in out])
    if refused:
        body = client.output(refused[0], 5)
        check(run.failure_class(body) == "capacity" and run.outcome(body)[0] == "error"
              and "try again in" in body and learn.parse(body).get("limits") is None,
              "the refusal is a capacity failure, with a wait and no new learned limit", body)
        check(last("t-paced")["status"] in ("refused", "ok") and
              sum(1 for x in ledger.rows() if x["alias"] == "t-paced" and x["status"] == "refused") == 3,
              "refused calls are in the ledger, sending nothing")

    # Across processes: rpm 4 on procprov, two processes of three calls.
    procs = [multiprocessing.Process(target=_child, args=(3,)) for _ in range(2)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    rows = [x for x in ledger.rows() if x["connection"] == "procprov"]
    sent = sum(1 for x in rows if x["status"] == "ok")
    check(all(p.exitcode == 0 for p in procs) and sent == 4 and len(rows) == 6,
          f"rpm 4 across two processes: {sent} of {len(rows)} sent", rows)

    # A monthly cap: three this month already, so the next waits for next month, which no deadline fits.
    now = time.time()
    for _ in range(3):
        ledger.append({"t": now - 1, "alias": "t-month", "connection": "monthprov", "status": "ok", "attempts": 1})
    r = client.chat("t-month", "hi", timeout=30)
    check("rate limit (not sent" in r["error"] and "a calendar month" in r["error"] and "this month" in r["error"],
          "rpmo 3: the fourth call this month is refused", r["error"])
    check(client.chat("t-ok", "hi")["text"] != "", "another connection is not held by that cap")

    # The free-tier guard: a model that went paid is held until allowed.
    before = len(seen.get("paid", []))
    alerts.raise_alert("model:paid:paid", "went paid", "t-paid is listed with a price now (models.dev)",
                       "check the provider", alias="t-paid")
    r = client.chat("t-paid", "hi")
    body = client.output(r, 30)
    check("free-tier guard" in r["error"] and len(seen.get("paid", [])) == before
          and run.failure_class(body) == "entitlement" and last("t-paid")["status"] == "refused",
          "the free-tier guard holds a model that went paid, as entitlement", body)
    (TMP / "config" / "connections.json").write_text('{"allow_paid": ["t-paid"]}', encoding="utf-8")
    check(client.chat("t-paid", "hi")["text"] != "", "allow_paid lets it through")

    # cost.py reads the ledger.
    data = cost.collect()
    ok = data.get("t-ok", {})
    check(ok.get("calls", 0) >= 2 and ok.get("input", 0) >= 14 and ok.get("output", 0) >= 8,
          "cost.py counts the ledger's calls and tokens", ok)

    # The dashboard: the Endpoints dialog's limits take rpmo; the Quotas page shows records and the ledger.
    sys.path.insert(0, str(ROOT))
    import serve
    saved = serve.save_connection_limits("monthprov", {"rpm": 20, "rpmo": 1000})
    view = {c["name"]: c for c in serve.connections_view()["connections"]}
    check(saved["ok"] and "rpmo" in serve.connections_view()["keys"]
          and view.get("monthprov", {}).get("limits") == {"rpm": 20, "rpmo": 1000},
          "the Endpoints dialog saves a monthly cap (rpmo)", (saved, view.get("monthprov")))
    bad = serve.save_connection_limits("monthprov", {"rpmo": -1})
    check(not bad["ok"] and serve.save_model_facts("t-ok", {"limits": {"rpmo": 5}}).get("ok") is not None
          and "rpmo" not in ((serve.catalog.overrides().get("t-ok") or {}).get("limits") or {}),
          "a bad rpmo is refused; a model's own limits have none", bad)
    page = serve.quotas_page()
    check("What providers said is left" in page and "fakeprov" in page and "What the bench sent" in page,
          "the Quotas page shows the records and the ledger's counts")

    srv.shutdown()
    print("\nthe ledger, quotas and pacing hold" if not failures else f"\n{failures} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
