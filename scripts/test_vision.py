"""Images through the real gateway handler and client, against a fake upstream.

    python scripts/test_vision.py

A request carrying image parts reaches every seat that can see with its
messages intact, through the built-in client even with BENCH_CLIENT=llm; a
text request still takes the llm path. Members that cannot see, or that the
catalog knows nothing about, are skipped and named; explorers are drawn only
from models that take images; with no seat that can see, 400; images count in
the context check, and a body over the cap is a 413 that says what to do.
No image bytes reach any file in the data folder, the access log, an error
or the merger, which gets the request as text with placeholders. Temp data
folder and llm user folder; the `llm` CLI is stubbed, never run.
"""

from __future__ import annotations

import http.client
import io
import json
import os
import pathlib
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
TMP = pathlib.Path(tempfile.mkdtemp(prefix="bench-vision-"))
(TMP / "config").mkdir()
(TMP / "llm").mkdir()
os.environ.update(BENCHWARMERS_HOME=str(TMP), LLM_USER_PATH=str(TMP / "llm"), BENCH_CLIENT="llm",
                  BENCHWARMERS_KEY_FAKE="sk-test")
os.environ.pop("BENCHWARMERS_ADMIN_TOKEN", None)
os.environ.pop("BENCHWARMERS_REQUIRE_KEYS", None)
sys.path[:0] = [str(ROOT), str(ROOT / "metrics"), str(ROOT / "suite")]

# A 1x1 PNG, generated: the bytes that must never be written down.
PIXEL = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
DATA_URL = "data:image/png;base64," + PIXEL
PHOTO = "https://user:pw@photos.example/chair.jpg?sig=SECRET123"
ASKED: list[dict] = []          # every chat request the fake upstream received
LLM_CALLS: list[tuple] = []     # every seat that took the llm path (stubbed)


class Upstream(BaseHTTPRequestHandler):
    def do_GET(self):                            # noqa: N802
        self._send({"data": [{"id": i} for i in ("eye", "eye2", "blind", "unknown", "echo", "tiny", "merger")]})

    def do_POST(self):                           # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        ASKED.append(body)
        if body["model"] == "echo":              # an upstream that echoes the request in its error
            return self._send({"error": {"message": f"bad request: {json.dumps(body['messages'])}"}}, 400)
        saw = any(isinstance(m.get("content"), list) and any(p.get("type") == "image_url" for p in m["content"])
                  for m in body["messages"])
        self._send({"model": body["model"], "choices": [{"message": {
            "content": f"answer from {body['model']}" + (" (saw the images)" if saw else "")}}]})

    def _send(self, data, status=200):
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_a):
        pass


def serve_on(handler) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def write(name: str, data) -> None:
    (TMP / "config" / name).write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def main() -> int:
    upstream = serve_on(Upstream)
    base = f"http://127.0.0.1:{upstream.server_port}"
    write("endpoints.json", {"fake": {"base": base + "/v1", "key": "fake", "default": True, "kind": "provider"},
                             "plan": {"base": base + "/plan/v1", "key": "fake", "kind": "provider",
                                      "api": "responses"}})
    import endpoints
    write("extra-openai-models.yaml", "".join(endpoints.yaml_block(a, s) for a, s in
                                              (("eye", "eye"), ("eye2", "eye2"), ("blind", "blind"),
                                               ("unknown", "unknown"), ("echo", "echo"), ("tiny", "tiny"),
                                               ("mg", "merger")))
               + endpoints.yaml_block("planned", "planned", "plan"))
    write("model-overrides.json", {"eye": {"vision": True}, "eye2": {"vision": True}, "echo": {"vision": True},
                                   "blind": {"vision": False}, "planned": {"vision": True},
                                   "tiny": {"vision": True, "context_window": 3000}})
    write("model-roles.json", {"models": {"eye2": "vision"}})     # a specialist: sits only for pictures
    write("gateway.json", {"max_calls": 5, "max_explorers": 2})
    write("collections.json", {
        "vision": {"members": ["eye", "blind", "unknown", "echo"], "description": "photos"},
        "merged": {"members": ["eye", "eye2"], "reply": "merged", "merger": "mg", "description": "x"},
        "blindonly": {"members": ["blind", "unknown", "planned"], "description": "x"},
        "small": {"members": ["tiny"], "description": "x"},
        "text": {"members": ["eye", "eye2"], "description": "x"}})

    import client
    import gateway
    import serve
    gateway._ask_llm = lambda cmd, prompt: (LLM_CALLS.append((cmd[2], prompt)) or f"via llm: {cmd[2]}", "")
    gw = serve_on(gateway.Handler)
    url = f"http://127.0.0.1:{gw.server_port}"

    def post(body: dict) -> tuple[int, dict]:
        req = urllib.request.Request(url + "/v1/chat/completions", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    question = [{"role": "system", "content": "You price estate-sale items."},
                {"role": "user", "content": [{"type": "text", "text": "What is this chair worth?"},
                                             {"type": "image_url", "image_url": {"url": DATA_URL, "detail": "low"}},
                                             {"type": "image_url", "image_url": {"url": PHOTO}}]}]
    saved_err = sys.stderr
    try:
        # 1. Images reach every seat that can see, intact, past BENCH_CLIENT=llm.
        sys.stderr = log = io.StringIO()
        status, r = post({"model": "bench/vision", "messages": question, "with": ["mg"]})
        sys.stderr = saved_err
        assert status == 200, (status, r)
        b, content = r["bench"], r["choices"][0]["message"]["content"]
        sat = {s["alias"] for s in b["seats"]}
        assert {"eye", "echo"} <= sat and not sat & {"blind", "unknown", "mg"}, sat
        explorers = [s["alias"] for s in b["seats"] if s["seat"].startswith("explorer")]
        assert len(explorers) == 1 and explorers[0] in ("eye2", "tiny"), explorers   # only models that see
        assert (b["shape"], b["shape_by"]) == ("vision", "the request carries images"), b
        assert {(k["seat"], k["alias"]) for k in b["skipped"]} == {("member", "blind"), ("member", "unknown"),
                                                                   ("with", "mg")}, b["skipped"]
        why = {k["alias"]: k["why"] for k in b["skipped"]}
        assert "does not take images" in why["blind"] and "Vision / OCR" in why["unknown"], why
        assert not LLM_CALLS, LLM_CALLS
        assert all(q["messages"] == question for q in ASKED), "a seat got other messages than were sent"
        assert {q["model"] for q in ASKED} == sat
        assert next(s for s in b["seats"] if s["alias"] == "eye")["answer"].endswith("(saw the images)")
        echo = next(s for s in b["seats"] if s["alias"] == "echo")
        assert echo["status"] == "error" and "[image: data URL, 1 KB]" in echo["error"], echo
        assert "blind (does not take images" in content, content

        # Never the bytes, nor the signed URL's signature or password: not in the reply,
        # the access log, any file in the data folder, or the dashboard's card.
        record = json.loads((TMP / "runs" / "gateway" / "calls.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        assert "[image: data URL, 1 KB]" in record["prompt"], record["prompt"]
        assert "[image: https://photos.example/chair.jpg?...]" in record["prompt"], record["prompt"]
        assert record["key"] == "local" and record["skipped"] == b["skipped"]
        card = serve.gateway_card()
        assert "blind –" in card and "not asked: does not take images" in card
        shown = [json.dumps(r), log.getvalue(), card] + [p.read_text(encoding="utf-8", errors="replace")
                                                         for p in TMP.rglob("*") if p.is_file()]
        for secret in (PIXEL[30:60], "SECRET123", "user:pw"):
            where = [text[max(0, text.find(secret) - 300):text.find(secret) + 50] for text in shown if secret in text]
            assert not where, f"{secret!r} was written down: {where}"

        # 2. A text request takes the llm path, as before, with the same bench keys;
        # the vision specialist among the members is not asked, and is named.
        write("gateway.json", {"max_calls": 5, "max_explorers": 0})          # members only from here
        ASKED.clear()
        status, r = post({"model": "bench/text", "messages": [{"role": "user", "content": "say hi"}]})
        assert status == 200 and LLM_CALLS == [("eye", "say hi")] and not ASKED, (status, LLM_CALLS, ASKED)
        assert set(r["bench"]) == {"panel", "collection", "shape", "shape_by", "seats", "verdict", "skipped"}, r["bench"]
        assert [(k["alias"], "vision specialist" in k["why"]) for k in r["bench"]["skipped"]] == [("eye2", True)]

        # 3. Merged: the seats see the images; the merger gets text, placeholders and answers.
        LLM_CALLS.clear()
        status, r = post({"model": "bench/merged", "messages": question})
        assert status == 200 and r["bench"]["reply"] == "merged", r
        [(merger, said)] = LLM_CALLS
        assert merger == "mg" and "[image: data URL, 1 KB]" in said and "(saw the images)" in said, said
        assert PIXEL[30:60] not in said and "SECRET123" not in said

        # 4. No seat can see and no explorer seat to fill: refused, nobody asked.
        ASKED.clear()
        status, r = post({"model": "bench/blindonly", "messages": question})
        message = r["error"]["message"]
        assert status == 400 and "can see images" in message and "blind" in message and "unknown" in message, r
        assert "Responses API" in message and not ASKED, message      # a plan model gets no images yet
        assert "Responses API" in client.chat("planned", "", messages=question)["error"]

        # 5. Images count in the context check: two of them overflow a 3,000-token window.
        status, r = post({"model": "bench/small", "messages": question})
        assert status == 413 and "each image counted" in r["error"]["message"], r

        # 6. Images no seat could be sent are refused, never dropped.
        for part in ({"type": "image_url", "image_url": {"url": "file:///etc/passwd"}},
                     {"type": "image_url", "image_url": {"url": "data:text/plain;base64,aGk="}},
                     {"type": "image_url"}, {"type": "input_image", "image_url": DATA_URL}):
            status, r = post({"model": "bench/text", "messages": [{"role": "user", "content": [part]}]})
            assert status == 400, (part, status, r)

        # 7. Over the body cap: a 413 that says what to do, before any of it is read.
        conn = http.client.HTTPConnection("127.0.0.1", gw.server_port, timeout=10)
        conn.putrequest("POST", "/v1/chat/completions")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(gateway.MAX_BODY + 1))
        conn.endheaders()
        resp = conn.getresponse()
        assert resp.status == 413 and "http(s) URLs" in json.loads(resp.read())["error"]["message"]
        conn.close()

        # 8. The redaction itself: an inline image, a bare base64 run, a URL's secrets in any form.
        assert gateway.redact(f"x {DATA_URL} y") == "x [image: data URL, 1 KB] y"
        assert gateway.redact("cannot fetch 'https://photos.example/chair.jpg?sig=SECRET123'") \
            == "cannot fetch 'https://photos.example/chair.jpg?...'"
        assert gateway.redact("bad http://[::1?x") == "bad [url]"
        assert gateway.redact("A" * 150) == "[base64, 1 KB]" and gateway.redact("short AAAA") == "short AAAA"
        assert gateway.placeholder(PHOTO) == "[image: https://photos.example/chair.jpg?...]"
    finally:
        sys.stderr = saved_err
        upstream.shutdown()
        gw.shutdown()
        shutil.rmtree(TMP, ignore_errors=True)
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
