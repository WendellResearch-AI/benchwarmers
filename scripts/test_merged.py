"""Merged replies through the real gateway handler and client, against a fake upstream.

    python scripts/test_merged.py

RELEASE-PLAN Phase 5, "Merged replies": the panel runs once per mode against
a fake OpenAI-compatible server reached by the built-in client. With
`merged`, every seat is still in `bench` and in the offload log and the
merger has its own call id; when the merger fails, or no merger has the
context window for the merge, the reply falls back to every answer with a
note, and a failed merger is still logged. Also: the default stays `all`
with the same `bench` keys as before, the suffix and body field and which
wins, `/v1/models` variants, the merger chosen by verdict record, and the
editor's save. BENCHWARMERS_HOME and LLM_USER_PATH name temp folders before
anything is imported; the key comes from the environment.
"""

from __future__ import annotations

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
TMP = pathlib.Path(tempfile.mkdtemp(prefix="bench-merged-"))
(TMP / "config").mkdir()
(TMP / "llm").mkdir()
os.environ.update(BENCHWARMERS_HOME=str(TMP), LLM_USER_PATH=str(TMP / "llm"), BENCH_CLIENT="builtin",
                  BENCHWARMERS_KEY_FAKE="sk-test")
sys.path[:0] = [str(ROOT), str(ROOT / "metrics"), str(ROOT / "suite")]

ASKED: list[dict] = []          # every chat request the fake upstream received


class Upstream(BaseHTTPRequestHandler):
    def do_GET(self):                            # noqa: N802
        self._send({"data": [{"id": i} for i in ("m1", "m2", "merger", "broken", "tiny")]})

    def do_POST(self):                           # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        ASKED.append(body)
        if body["model"] == "broken":            # a 400 is not retried
            return self._send({"error": {"message": "model is broken"}}, 400)
        self._send({"model": body["model"], "choices": [{"message": {"content": f"answer from {body['model']}"}}]})

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
    write("endpoints.json", {"fake": {"base": f"http://127.0.0.1:{upstream.server_port}/v1", "key": "fake",
                                      "default": True, "kind": "provider"}})
    import endpoints
    write("extra-openai-models.yaml", "".join(endpoints.yaml_block(a, s) for a, s in
                                              (("m1", "m1"), ("m2", "m2"), ("mg", "merger"),
                                               ("broken", "broken"), ("tiny", "tiny"))))
    write("model-overrides.json", {"tiny": {"context_window": 50}})
    write("gateway.json", {"max_calls": 2, "max_explorers": 0})       # no explorers: no router, no shape picker

    def collection(**kw):
        write("collections.json", {"core": {"members": ["m1", "m2"], "description": "x", **kw}})

    import gateway
    import offload
    import serve
    gw = serve_on(gateway.Handler)
    url = f"http://127.0.0.1:{gw.server_port}"

    def post(body: dict) -> tuple[int, dict | str]:
        req = urllib.request.Request(url + "/v1/chat/completions", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                text = r.read().decode()
                return r.status, (text if body.get("stream") else json.loads(text))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def ask(model: str, **extra) -> tuple[dict, dict, str]:
        status, r = post({"model": model, "messages": [{"role": "user", "content": "say hi"}], **extra})
        assert status == 200, (status, r)
        return r, r["bench"], r["choices"][0]["message"]["content"]

    def logged() -> set[str]:
        return {e["call"] for e in offload.entries() if e.get("call")}

    try:
        # 1. The default is every answer, with the bench keys clients get today.
        collection()
        r, b, content = ask("bench/core")
        assert set(b) == {"panel", "collection", "shape", "shape_by", "seats", "verdict"}, b
        assert [s["alias"] for s in b["seats"]] == ["m1", "m2"] and all(s["call"] in content for s in b["seats"])
        assert all(s["call"] in logged() for s in b["seats"])
        assert not any(q["messages"][0]["content"] == gateway.MERGE_SYSTEM for q in ASKED), "merged by default"

        # 2. Merged by suffix: one answer, its own call id, every seat kept and logged.
        collection(merger="mg")
        ASKED.clear()
        r, b, content = ask("bench/core:merged")
        merger = b["merger"]
        assert b["reply"] == "merged" and merger["call"] == b["panel"] + ".merger" and merger["alias"] == "mg", b
        assert content.startswith("answer from merger") and merger["call"] in content, content
        assert [s["alias"] for s in b["seats"]] == ["m1", "m2"] and all(s["answer"] for s in b["seats"])
        assert {s["call"] for s in b["seats"]} | {merger["call"]} <= logged()
        merge_ask = next(q for q in ASKED if q["model"] == "merger")
        said = merge_ask["messages"][1]["content"]
        assert merge_ask["messages"][0]["content"] == gateway.MERGE_SYSTEM
        assert "say hi" in said and "answer from m1" in said and "answer from m2" in said, said
        # The dashboard finds the merger in the call log, so its verdict dialog opens.
        assert serve.gateway_seat(merger["call"])["seat"] == "merger"

        # 3. The merger fails (body field this time): every answer, a note, the failed merger logged.
        collection(merger="broken")
        r, b, content = ask("bench/core", bench={"reply": "merged"})
        assert b["reply"] == "all" and "broken" in b["note"] and b["merger"]["status"] == "error", b
        assert content.startswith("_Merged reply unavailable") and all(s["call"] in content for s in b["seats"])
        assert b["merger"]["call"] in logged() and all(s["call"] in logged() for s in b["seats"])

        # 4. No merger fits the merge: every answer, a note, and no merge call spent.
        collection(merger="tiny")
        ASKED.clear()
        r, b, content = ask("bench/core:merged")
        assert b["reply"] == "all" and "context window" in b["note"] and "merger" not in b, b
        assert content.startswith("_Merged reply unavailable") and not any(q["model"] == "tiny" for q in ASKED)
        assert all(s["call"] in logged() for s in b["seats"])

        # 5. Most specific wins: a merged collection, asked for :all, and the body over the suffix.
        collection(reply="merged", merger="mg")
        assert "merger" not in ask("bench/core:all")[1]
        assert "merger" not in ask("bench/core:merged", bench={"reply": "all"})[1]
        assert ask("bench/core")[1]["reply"] == "merged"
        assert post({"model": "bench/core", "messages": [{"role": "user", "content": "x"}],
                     "bench": {"reply": "both"}})[0] == 400
        assert post({"model": "bench/core:both", "messages": [{"role": "user", "content": "x"}]})[0] == 404
        # Streaming: the merged answer as the one chunk, the seats beside it.
        chunks = [json.loads(line[6:]) for line in post({"model": "bench/core", "stream": True, "messages":
                  [{"role": "user", "content": "x"}]})[1].splitlines() if line.startswith("data: {")]
        assert chunks[0]["choices"][0]["delta"]["content"].startswith("answer from merger")
        assert len(chunks[0]["bench"]["seats"]) == 2

        # 6. /v1/models: each collection and its other reply mode.
        with urllib.request.urlopen(url + "/v1/models", timeout=10) as resp:
            listed = {m["id"]: m["reply"] for m in json.load(resp)["data"]}
        assert listed == {"bench/core": "merged", "bench/core:all": "all"}, listed

        # 7. An empty merger: the member with the most useful verdicts on the shape,
        # else (no verdicts, and no suite scores for a router's primary) the first member.
        assert next(gateway.merger_candidates({}, ["m1", "m2"], "review", False)) == "m1"
        offload.add("m2", "a task (bench/core, review)", True, "", "pZ.member2")
        offload.add("m1", "a task (bench/core, design)", True, "", "pZ.member1")
        assert next(gateway.merger_candidates({}, ["m1", "m2"], "review", False)) == "m2"

        # 8. The editor: reply and merger saved, and checked.
        for reply, merger_alias in (("both", ""), ("merged", "nope")):
            assert not serve.save_collection("chat", ["m1"], "", "", [], reply, merger_alias)["ok"]
        assert serve.save_collection("chat", ["m1"], "", "", [], "merged", "mg")["ok"]
        saved = json.loads((TMP / "config" / "collections.json").read_text(encoding="utf-8"))["chat"]
        assert (saved["reply"], saved["merger"]) == ("merged", "mg"), saved
    finally:
        upstream.shutdown()
        gw.shutdown()
        shutil.rmtree(TMP, ignore_errors=True)
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
