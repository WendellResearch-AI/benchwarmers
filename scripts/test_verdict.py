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
    seat = b["seats"][0]["call"]
    for body in ({"evidence": ["a list"]}, {"caught": "yes"}):                 # POST /bench/verdict's new fields
        req = urllib.request.Request(url.replace("/v1/chat/completions", "/bench/verdict"),
                                     json.dumps({"call": seat, "useful": False, **body}).encode(),
                                     {"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req)
            raise AssertionError(f"{body} was accepted")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400, (body, exc.code)
    req = urllib.request.Request(url.replace("/v1/chat/completions", "/bench/verdict"),
                                 json.dumps({"call": seat, "useful": False, "false": ["f"], "evidence": "a.py:1",
                                             "caught": True}).encode(), {"Content-Type": "application/json"})
    e = json.loads(urllib.request.urlopen(req).read())["recorded"]
    assert (e["evidence"], e["caught"], offload.backed(e)) == ("a.py:1", True, True), e
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


def ranking() -> None:
    """offload.score, as the route order and the merger rank by it: few verdicts
    count for little, many let the rate speak, and a false claim counts against."""
    def judge(model: str, judged: int, useful: int, false: int = 0) -> None:
        for i in range(judged):                   # the false claims go on not-useful verdicts: worse than no use
            offload.add(model, "t (bench/core, review)", None, "member; ok", f"k.{model}.{i}")
            offload.verdict(f"k.{model}.{i}", i < useful, "", false=["untrue"] if judged - i <= false else None,
                            evidence="x.py:1 says otherwise" if judged - i <= false else None)
    for model, judged, useful, false in (("five", 5, 5, 0), ("thirty", 30, 10, 0), ("lucky", 1, 1, 0),
                                         ("steady", 10, 9, 0), ("honest", 5, 3, 0), ("liar", 5, 3, 1)):
        judge(model, judged, useful, false)
    gateway.ROTATE = 0
    assert gateway.route_order(["thirty", "five"]) == ["five", "thirty"]          # 5 of 5 over 10 of 30
    assert gateway.route_order(["lucky", "steady"]) == ["steady", "lucky"]        # one lucky verdict is not a record
    assert gateway.route_order(["liar", "honest"]) == ["honest", "liar"]          # a false claim counts against
    assert next(gateway.merger_candidates({}, ["liar", "honest"], "review", False)) == "honest"
    assert offload.score({}) == 0.5                                               # no verdicts: neutral
    # A useful verdict is worth the share of its claims that held: one false of ten costs a tenth.
    for model, useful, real, false in (("mostly", True, 9, 1), ("clean", True, 0, 0), ("nope", False, 0, 0)):
        offload.add(model, "t (bench/core, review)", None, "member; ok", f"k.{model}")
        offload.verdict(f"k.{model}", useful, "", [f"real {i}" for i in range(real)] or None, ["untrue"] * false or None,
                        evidence="x.py:1" if false else None)
    s = offload.summary()
    assert s["mostly"]["credit"] == 0.9 and abs(offload.score(s["mostly"]) - offload.score(s["clean"])) < 0.02, s["mostly"]
    assert gateway.route_order(["nope", "mostly"]) == ["mostly", "nope"]


def evidence() -> None:
    """A false claim counts only with what disproved it (or from before the field); caught is counted."""
    import mine
    for call, model in (("e1.member1", "proven"), ("e1.member2", "alleged"), ("e1.member3", "finder")):
        offload.add(model, "t (bench/core, review)", None, "member; ok", call)
    offload.verdict("e1.member1", False, "", false=["it leaks"], evidence="pool.py:12 closes it")
    offload.verdict("e1.member2", False, "", false=["it races"])
    offload.verdict("e1.member3", True, "", real=["the off-by-one"], caught=True)
    with offload.LOG.open("a", encoding="utf-8") as fh:                          # a verdict from before the field
        fh.write(json.dumps({"date": "2026-11-01", "model": "old", "task": "t", "useful": False, "note": "",
                             "call": "e0.member1", "false": ["old claim"]}) + "\n")
    s = offload.summary()
    assert (s["proven"]["wrong"], s["alleged"]["wrong"], s["old"]["wrong"]) == (1, 0, 1), s
    assert offload.score(s["alleged"]) > offload.score(s["proven"])              # an unproven word costs nothing extra
    assert s["finder"]["caught"] == 1 and s["proven"]["caught"] == 0
    assert next(e for e in offload.entries() if e.get("call") == "e1.member2")["evidence"] == ""
    assert {c[0] for c in mine.claims_for("e1")} == {"it leaks", "the off-by-one"}     # the alleged one is not mined
    assert {c[0] for c in mine.claims_for("e0")} == {"old claim"}
    # The CLI keeps evidence and caught when not given again; the dashboard form clears them.
    def got(call):
        return next(e for e in offload.entries() if e.get("call") == call)
    offload.verdict("e1.member3", True, "now with a note")
    assert got("e1.member3")["caught"] is True
    offload.verdict("e1.member1", False, "noted")
    assert got("e1.member1")["evidence"] == "pool.py:12 closes it"
    assert serve.record_verdict("e1.member3", True, "", ["the off-by-one"], [])["ok"]
    assert "caught" not in got("e1.member3")
    assert serve.record_verdict("e1.member3", True, "", [], [], "", True)["ok"] and got("e1.member3")["caught"]
    assert not serve.record_verdict("e1.member3", True, "", [], [], "", "yes")["ok"]
    assert not serve.record_verdict("e1.member3", True, "", [], [], ["a list"], False)["ok"]


if __name__ == "__main__":
    verdicts()
    bench_field()
    records()
    ranking()
    evidence()
    print("verdict, bench-field, record, ranking and evidence checks pass")
