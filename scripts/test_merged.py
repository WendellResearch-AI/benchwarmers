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
import time
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
RELEASE = threading.Event()     # "slow" answers once this is set
# The merger names its sources as told; the gateway puts the seats back.
MERGED = "answer from merger: Source 1 and source 2 agree; Sources 1 and 2 differ from Source 7 and Sources 1-2"


class Upstream(BaseHTTPRequestHandler):
    def do_GET(self):                            # noqa: N802
        self._send({"data": [{"id": i} for i in ("m1", "m2", "merger", "broken", "tiny", "slow")]})

    def do_POST(self):                           # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        ASKED.append(body)
        if body["model"] == "broken":            # a 400 is not retried
            return self._send({"error": {"message": "model is broken"}}, 400)
        if body["model"] == "slow":
            RELEASE.wait(20)
        said = MERGED if body["model"] == "merger" else f"answer from {body['model']}"
        self._send({"model": body["model"], "choices": [{"message": {"content": said}}]})

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
                                               ("broken", "broken"), ("tiny", "tiny"), ("slow", "slow"))))
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

    def last_line(panel: str) -> dict | None:
        """The panel's line in the call log, None until it is written."""
        calls = TMP / "runs" / "gateway" / "calls.jsonl"
        lines = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
        return next((r for r in map(json.loads, reversed(lines)) if r["panel"] == panel), None)

    try:
        # 1. The default is every answer, with the bench keys clients get today.
        collection()
        r, b, content = ask("bench/core")
        assert set(b) == {"panel", "collection", "shape", "shape_by", "seats", "verdict"}, b
        assert [s["alias"] for s in b["seats"]] == ["m1", "m2"] and all(s["call"] in content for s in b["seats"])
        assert all(s["status"] == "ok" for s in b["seats"]), b                 # every seat fast: none late
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
        # The merger sees sources, not seats or models; the reply names them again,
        # and a source number it made up, or a range, is left as written.
        assert "## Source 1\n" in said and "## Source 2\n" in said and "## member" not in said, said
        assert content.startswith("answer from merger: m1 (member) and m2 (member) agree; m1 (member) and"
                                  " m2 (member) differ from Source 7 and Sources 1-2"), content
        names = [f"n{i}" for i in range(1, 11)]
        assert gateway.unsource("Source 10, then Source 1", names) == "n10, then n1"
        assert gateway.unsource("source 2; Sources 1 and 3; Sources 1, 2, and 3", names[:3]) == "n2; n1 and n3; n1, n2, and n3"
        kept = "Source 4; Sources 1 and 4; Sources 1-3; Sources 1 to 3; Source 1a"
        assert gateway.unsource(kept, names[:3]) == kept
        # The dashboard finds the merger in the call log, so it is judged in the request's side pane.
        shown = serve.gateway_request(merger["call"].rsplit(".", 1)[0])
        assert [s["seat"] for s in shown["seats"]][-1] == "merger", shown

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
        # Streaming: the merged answer once the merge is done, the seats on the last chunk.
        chunks = [json.loads(line[6:]) for line in post({"model": "bench/core", "stream": True, "messages":
                  [{"role": "user", "content": "x"}]})[1].splitlines() if line.startswith("data: {")]
        texts = [c["choices"][0]["delta"].get("content") for c in chunks]
        assert texts[0] == "" and texts[1].startswith("answer from merger") and len(chunks) == 3, texts
        assert len(chunks[-1]["bench"]["seats"]) == 2

        # 6. /v1/models: each collection and its other reply modes.
        with urllib.request.urlopen(url + "/v1/models", timeout=10) as resp:
            listed = {m["id"]: m["reply"] for m in json.load(resp)["data"]}
        assert listed == {"bench/core": "merged", "bench/core:all": "all", "bench/core:route": "route"}, listed

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

        # 9. A retired model a collection still names (member, `with`, merger) is
        # not called: skipped with why, and the merge goes to a merger chosen by verdicts.
        write("retired.json", {"gone": {"served": "gone", "at": "2026-10-20T05:00:00+02:00", "why": "withdrawn"}})
        collection(members=["m1", "gone"], merger="gone")
        ASKED.clear()
        r, b, content = ask("bench/core:merged", **{"with": ["gone"]})
        assert [s["alias"] for s in b["seats"]] == ["m1"] and not any(q["model"] == "gone" for q in ASKED), b
        assert {(k["seat"], k["alias"]) for k in b["skipped"]} == {("member", "gone"), ("merger", "gone")}, b
        assert all("retired from the bench on 2026-10-20" in k["why"] for k in b["skipped"]), b["skipped"]
        assert b["reply"] == "merged" and b["merger"]["alias"] != "gone", b
        # Asked for every answer, the reply says who was not asked; `with` a retired model is skipped too.
        collection(members=["m1"])
        r, b, content = ask("bench/core", **{"with": ["gone"]})
        assert b["skipped"] == [{"seat": "with", "alias": "gone", "why": b["skipped"][0]["why"]}], b
        assert "Not asked: gone (retired from the bench" in content and not any(q["model"] == "gone" for q in ASKED)
        # Skipped before the max_calls cap (2 here), so the member after it still sits.
        collection(members=["gone", "m1", "m2"])
        r, b, content = ask("bench/core")
        assert [s["alias"] for s in b["seats"]] == ["m1", "m2"] and [k["alias"] for k in b["skipped"]] == ["gone"], b
        (TMP / "config" / "retired.json").unlink()

        # 10. QUORUM, THEN GRACE. Every seat fast: nothing waits, nothing late.
        write("gateway.json", {"max_calls": 3, "max_explorers": 0, "quorum": 2, "grace": 0.3})
        collection()
        r, b, content = ask("bench/core")
        assert [s["status"] for s in b["seats"]] == ["ok", "ok"] and "waited" not in last_line(b["panel"]), b
        # Two fast seats and a slow one: the reply goes after quorum and grace, the slow seat named late.
        collection(members=["m1", "m2", "slow"])
        r, b, content = ask("bench/core")
        late = b["seats"][2]
        assert [s["status"] for s in b["seats"]] == ["ok", "ok", "late"] and late["alias"] == "slow", b
        assert late["call"] == b["panel"] + ".member3" and late["call"] in content and gateway.LATE in content, content
        assert late["call"] not in logged() and last_line(b["panel"]) is None      # not in yet
        # Streamed: the late seat's section before the close; late on the last chunk.
        chunks = [json.loads(line[6:]) for line in post({"model": "bench/core", "stream": True, "messages":
                  [{"role": "user", "content": "x"}]})[1].splitlines() if line.startswith("data: {")]
        streamed = "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks if c.get("choices"))
        assert gateway.LATE in streamed and [s["status"] for s in chunks[-1]["bench"]["seats"]][2] == "late"
        # Merged: from the answers in at the grace's end, saying which seat was late.
        collection(members=["m1", "m2", "slow"], merger="mg")
        ASKED.clear()
        r, b, content = ask("bench/core:merged")
        said = next(q for q in ASKED if q["model"] == "merger")["messages"][1]["content"]
        assert "## Source 2\n" in said and "## Source 3" not in said and "slow late. A late seat" in content, content
        # When it is in, the late seat is recorded like any seat, under the call id the reply named,
        # owed a verdict, and the panel's call-log line has every answer (all three panels', before cleanup).
        RELEASE.set()
        panels = (late["call"].rsplit(".", 1)[0], chunks[-1]["bench"]["panel"], b["panel"])
        for _ in range(100):
            if all(last_line(p) for p in panels):
                break
            time.sleep(0.1)
        assert late["call"] in logged() and last_line(b["panel"])["seats"][-1]["seat"] == "merger"
        line = last_line(panels[0])
        assert line and line["seats"][2] == {**line["seats"][2], "call": late["call"], "status": "ok",
                                             "answer": "answer from slow", "late": True}, line
        assert line["waited"] < 10, line
        req = urllib.request.Request(url + "/bench/pending", json.dumps({"calls": [late["call"]]}).encode(),
                                     {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            assert json.load(resp)["pending"] == [late["call"]]
    finally:
        RELEASE.set()
        upstream.shutdown()
        gw.shutdown()
        shutil.rmtree(TMP, ignore_errors=True)
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
