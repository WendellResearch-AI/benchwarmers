"""Verdicts and the gateway's `bench` field, against a throwaway log. No model or
endpoint is called and the real offloads.jsonl and runs/gateway/ are never
written. The collection comes from the data folder (in CI, tests/fixtures/data).

    python scripts/test_verdict.py
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import threading
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "metrics"), str(ROOT / "suite")]
import gateway  # noqa: E402
import offload  # noqa: E402
import run  # noqa: E402
import serve  # noqa: E402

tmp = pathlib.Path(tempfile.mkdtemp(prefix="bench-verdict-"))
offload.LOG = tmp / "offloads.jsonl"
gateway.LOG_DIR = tmp


def verdicts() -> None:
    offload.add("m1", "task", None, "member; ok in 3s", "pX.member1")
    assert not serve.record_verdict("pX.member1", None, "", [], [])["ok"]        # no pick
    assert not serve.record_verdict("pX.member1", True, "", "x", [])["ok"]       # claims not a list
    assert not serve.record_verdict("nope.member1", True, "", [], [])["ok"]      # unknown call
    assert serve.record_verdict("pX.member1", True, "  found   it ", ["real one", " "], ["wrong one"])["ok"]
    e = offload.entries()[-1]
    assert (e["useful"], e["note"], e["real"], e["false"]) == (True, "found it", ["real one"], ["wrong one"]), e
    n = len(offload.LOG.read_text(encoding="utf-8").splitlines())
    assert serve.record_verdict("pX.member1", True, "found it", ["real one"], ["wrong one"])["ok"]
    assert len(offload.LOG.read_text(encoding="utf-8").splitlines()) == n, "the same verdict again wrote a line"
    # The dashboard form shows note and claims for editing: blank means cleared.
    assert serve.record_verdict("pX.member1", False, "", [], [])["ok"]
    e = offload.entries()[-1]
    assert e["useful"] is False and e["note"] == "" and "real" not in e and "false" not in e, e
    # The CLI: a blank note or claim list keeps the earlier one.
    offload.verdict("pX.member1", False, "", ["cli claim"])
    offload.verdict("pX.member1", True, "")
    assert offload.entries()[-1]["real"] == ["cli claim"], offload.entries()[-1]
    assert len(offload.entries()) == 1                                           # superseded by call id


def bench_field() -> None:
    gateway.pick_shape = lambda text: ("general", "test")
    gateway.ask_seat = lambda alias, system, prompt, messages=None: {
        "status": "ok", "answer": f"answer from {alias}", "error": "", "seconds": 0.1}
    # The explorer seats ask every endpoint's /models; None is its own "cannot
    # be asked, do not filter", so no request leaves the machine.
    run._served_model_names = lambda cache=None: None
    srv = ThreadingHTTPServer(("127.0.0.1", 0), gateway.Handler)
    gateway.PORT = srv.server_address[1]          # the Host check expects the port it runs on
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/v1/chat/completions"

    def post(body, **headers):
        req = urllib.request.Request(url, json.dumps(body).encode(),
                                     {"Content-Type": "application/json", **headers})
        with urllib.request.urlopen(req) as r:
            return r.read().decode()

    # metrics/guard.py, end to end: a rebinding Host, a page's text/plain
    # form and a foreign Origin are refused before any seat is called.
    for headers, code in (({"Host": "evil.example"}, 403), ({"Content-Type": "text/plain"}, 415),
                          ({"Origin": "http://evil.example"}, 403)):
        try:
            post({}, **headers)
            raise AssertionError(f"{headers} was served")
        except urllib.error.HTTPError as exc:
            assert exc.code == code, (headers, exc.code)

    name = next(iter(gateway.bench_collections.load()))
    msg = {"model": f"bench/{name}", "messages": [{"role": "user", "content": "say hi"}]}
    r = json.loads(post(msg))
    b = r["bench"]
    assert b["collection"] == f"bench/{name}" and b["seats"], b
    assert all(s["answer"] == f"answer from {s['alias']}" and s["call"].startswith(b["panel"] + ".")
               for s in b["seats"])
    assert all(s["call"] in r["choices"][0]["message"]["content"] for s in b["seats"])
    assert "prompt" not in b and "system" not in b
    chunks = [json.loads(line[6:]) for line in post({**msg, "stream": True}).splitlines()
              if line.startswith("data: {")]
    # Seat by seat as each finishes; the bench rides on the last chunk, with the finish_reason.
    assert "bench" in chunks[-1] and chunks[-1]["choices"][0]["finish_reason"] == "stop", chunks
    assert not any("bench" in c for c in chunks[:-1]), chunks
    said = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
    assert all(s["call"] in said for s in chunks[-1]["bench"]["seats"]), said
    srv.shutdown()


def records() -> None:
    """GET /bench/record's counts: one collection's judged seats, a task's by its notes, never an unjudged one."""
    for call, model, coll, useful, note in [("r1.m1", "m1", "bench/vision", True, "thicken slab faces: agreed"),
                                            ("r1.m2", "m2", "bench/vision", False, "thicken slab faces: no answer"),
                                            ("r2.m1", "m1", "bench/vision", True, "thicken what it is: agreed"),
                                            ("r3.m1", "m1", "bench/core", False, "a review")]:
        offload.add(model, f"look at this ({coll}, vision)", None, "member; ok", call)
        offload.verdict(call, useful, note)
    offload.add("m3", "look at this (bench/vision, vision)", None, "member; ok", "r4.m3")   # never judged
    assert gateway.record("bench/vision", "thicken slab faces") == {
        "m1": {"useful": 1, "notUseful": 0, "judged": 1, "lastAt": offload.entries()[-1]["date"]},
        "m2": {"useful": 0, "notUseful": 1, "judged": 1, "lastAt": offload.entries()[-1]["date"]}}
    assert gateway.record("bench/vision")["m1"]["judged"] == 2 and "m3" not in gateway.record("bench/vision")
    assert gateway.record("bench/core")["m1"]["notUseful"] == 1 and gateway.record("bench/none") == {}


if __name__ == "__main__":
    verdicts()
    bench_field()
    records()
    print("verdict, bench-field and record checks pass")
