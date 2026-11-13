"""python metrics/test_decide.py — the decision client and its log, the benchmark's
sets, measures and pass, who is asked, the error-shape cache and the role
suggestion. No network: urlopen is a stub. BENCHWARMERS_HOME is a temp folder,
set before anything is imported."""
import io
import json
import os
import pathlib
import shutil
import sys
import tempfile
import urllib.error

TMP = pathlib.Path(tempfile.mkdtemp(prefix="bench-decide-"))
for sub in ("config", "metrics", "perf", "runs/gateway", "llm"):
    (TMP / sub).mkdir(parents=True)
os.environ.update(BENCHWARMERS_HOME=str(TMP), LLM_USER_PATH=str(TMP / "llm"), BENCHWARMERS_KEY_HUB="sk-test")
HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "suite")]


def write(path: str, data) -> None:
    text = data if isinstance(data, str) else json.dumps(data)
    (TMP / path).write_text(text, encoding="utf-8")


def jsonl(path: str, rows: list[dict]) -> None:
    write(path, "".join(json.dumps(r) + "\n" for r in rows))


write("config/endpoints.json", {"hub": {"base": "http://127.0.0.1:9/api", "key": "hub", "default": True,
                                        "decisions": "http://127.0.0.1:9/openai/{path}"},
                                "cf": {"base": "http://127.0.0.1:9/ai/v1", "key": "hub",
                                       "decisions": "http://127.0.0.1:9/ai/run/{model}"},
                                "bare": {"base": "http://127.0.0.1:9/v1", "key": "hub", "kind": "provider"},
                                "barehub": {"base": "http://127.0.0.1:8/api", "key": "hub"}})
# dm2 first: before any result it is the one asked (the first that takes choice, as before),
# except per request, where its :free budget is spared while dm1 can answer.
MODELS = {
    "dm2": {"served": "vendor/d2:free", "via": "hub", "question_types": ["noul", "choice", "score"]},
    "dm1": {"served": "d1", "via": "hub", "path": "systemone", "question_types": ["noul", "choice", "score"]},
    "yesno": {"served": "y", "via": "hub", "question_types": ["noul"]},
    "dear": {"served": "dd", "via": "hub", "question_types": ["noul", "choice"]}}
write("config/decision-models.json", MODELS)
write("config/model-roles.json", {"models": {"m1": "general"}, "ad_hoc": ["dear"]})
write("config/collections.json", {
    "core": {"members": ["m1"], "description": "General second opinions.", "tags": ["general"], "shape": "general"},
    "fast": {"members": ["m1"], "description": "", "tags": ["fast"], "shape": "quick"},
    "vision": {"members": ["m1"], "description": "Photos with questions.", "tags": ["vision"]},
    "dear": {"members": ["m1"], "description": "Costly.", "ad_hoc": True}})
BUSY = "Error: Error code: 503 - The model is temporarily unavailable due to high demand. (Trace ID: 3fa2c9d81b)"
QUOTA = "Error: Error code: 429 - Rate limit exceeded: free-models-per-day. Add 10 credits"
ODD = "Error: the gremlins ate it"
jsonl("runs/gateway/calls.jsonl", [
    {"panel": "p1", "model": "bench/core", "shape": "general", "shape_by": "set on the collection", "system": "",
     "prompt": "Review this function for bugs.",
     "seats": [{"call": "p1.member1", "status": "ok", "answer": "It double-frees on line 3.", "error": ""},
               {"call": "p1.explorer", "status": "error", "answer": "", "error": BUSY},
               {"call": "p1.explorer2", "status": "empty", "answer": "", "error": ""}]},
    {"panel": "p2", "model": "bench/fast", "shape": "quick", "shape_by": "set on the collection", "system": "Be brief.",
     "prompt": "Spam or not: WIN A PRIZE", "seats": [{"call": "p2.member1", "status": "error", "answer": "", "error": QUOTA},
                                                     {"call": "p2.member2", "status": "error", "answer": "", "error": ODD}]},
    {"panel": "p3", "model": "bench/vision", "shape": "vision", "shape_by": "the request carries images",
     "prompt": "What is printed here? [image: data URL, 12 KB]",
     "seats": [{"call": "p3.member1", "status": "ok", "answer": "A cat.", "error": ""}]},
    # Chosen by a decision model: labels nothing.
    {"panel": "p4", "model": "bench/core", "shape": "general", "shape_by": "set on the collection",
     "prompt": "A request the gateway's pick sent here.", "picked": {"collection": "bench/core"}, "seats": []},
    {"panel": "p5", "model": "bench/dear", "shape": "general", "shape_by": "no explorer seats to fill",
     "prompt": "An ad hoc collection's request.", "seats": []},
    # The shape a decision model chose is not the collection's own.
    {"panel": "p6", "model": "bench/core", "shape": "review", "shape_by": "dm2, 0.99", "prompt": "Is this lock safe?",
     "seats": [{"call": "p6.member1", "status": "ok", "answer": "Yes.", "error": ""}]}])
jsonl("metrics/offloads.jsonl", [
    {"date": "2026-11-08", "model": "m1", "task": "t", "useful": True, "note": "right", "call": "p1.member1"},
    {"date": "2026-11-08", "model": "m1", "task": "t", "useful": False, "note": "a cat? no", "call": "p3.member1"},
    {"date": "2026-11-08", "model": "m1", "task": "t", "useful": False, "note": "it is not", "call": "p6.member1"},
    {"date": "2026-11-08", "model": "m1", "task": "t", "useful": True, "note": "ui test: in-place verdict",
     "call": "p1.explorer"}])
write("perf/history.csv", "timestamp,model,prompt,answered,error,detail\n2026-11-08T05:00:00,m1,tiny,0,1,<<TIMED OUT>>\n")

import decide  # noqa: E402

# ---- error text, and the type guard -----------------------------------------------


def http_error(body: bytes, code: int = 400) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x", code, "bad", {}, io.BytesIO(body))


# Every shape providers send must come back as text, never raise.
assert "decisions model" in decide.error_text(http_error(b'{"error": {"message": "is a decisions model"}}'))
assert "decisions model" in decide.error_text(http_error(b'{"error": "is a decisions model"}'))
assert "passthrough" in decide.error_text(http_error(b'{"detail": "Direct API passthrough is disabled"}'))
assert decide.error_text(http_error(b'{"error": null, "message": "down"}')).startswith("400")
assert decide.error_text(http_error(b"not json")) == "400"

# A chat refusal says "decision model" however the provider words it.
assert decide.names_decisions("400 is a decisions model and cannot be used with chat")             # OpenRouter
assert decide.names_decisions("400 {'errors': [{'message': \"AiError: Bad input: Error: required properties"
                              " at '/' are 'model,state,questions' (72ae)\", 'code': 5006}]}")     # Cloudflare
assert decide.names_decisions("400 Model execution failed (User Input Error): Required value missing: questions.")
assert not decide.names_decisions("400 messages: Required")       # Cloudflare's v1 refusing a decision body
assert not decide.names_decisions("429 Rate limit exceeded") and not decide.names_decisions("")

# ask() refuses a type the entry does not list, before any call is made (and logs nothing).
try:
    decide.ask("yesno", "s", {"q": {"type": "choice", "instructions": "?", "criteria": {"a": None}}})
except ValueError as exc:
    assert "does not take choice" in str(exc)
else:
    raise AssertionError("choice should have been refused")
assert not decide.LOG.exists()

# ---- the stub provider: dm1 answers the truth, dm2 is rate limited, the rest say yes / the first option --

SEEN: list[dict] = []
TRUTH: dict[str, object] = {}


def urlopen(request, timeout=0):
    body = json.loads(request.data)
    SEEN.append({"url": request.full_url, **body})
    if body["model"] == "vendor/d2:free":
        raise http_error(b'{"error": {"message": "Rate limit exceeded: free-models-per-min"}}', 429)
    name, q = next(iter(body["questions"].items()))
    truth = TRUTH.get(body["state"]) if body["model"] == "d1" else None
    if q["type"] == "noul":
        p = 0.9 if truth in (True, None) else 0.1
        answer = {"type": "noul", "noul": p}
    else:
        options = list(q["criteria"])
        pick = truth if truth in options else options[0]
        probs = {o: (0.9 if o == pick else 0.1 / (len(options) - 1)) for o in options}
        answer = {"type": "choice", "choice": pick, "probabilities": probs, "confidence": 0.85}
    return io.BytesIO(json.dumps({"answers": {name: answer}}).encode())




# Cloudflare: the model in the URL and not the body, the reply in a "result" wrapper.
def cf_urlopen(request, timeout=0):
    body = json.loads(request.data)
    assert request.full_url == "http://127.0.0.1:9/ai/run/@cf/cloudflare/clef-flash" and "model" not in body, body
    return io.BytesIO(json.dumps({"result": {"model": "clef-flash", "answers": {"probe": {"type": "noul", "noul": 0.98}}},
                                  "success": True, "errors": []}).encode())


decide.urllib.request.urlopen = cf_urlopen
assert decide.probe("cf", "@cf/cloudflare/clef-flash") == (None, "decisions")
assert decide.log_rows()[-1]["answers"]["probe"]["noul"] == 0.98
decide.LOG.unlink()


# An endpoint with no decisions URL is asked under its own base, each path in turn (AMD Token
# Lab answers only /v1/systemone); a hub without one is not asked: its relay is given by hand.
def bare_urlopen(request, timeout=0):
    if request.full_url != "http://127.0.0.1:9/v1/systemone":
        raise http_error(b'{"error": {"message": "Unknown endpoint"}}', 404)
    return io.BytesIO(json.dumps({"answers": {"probe": {"type": "noul", "noul": 0.99}}}).encode())


decide.urllib.request.urlopen = bare_urlopen
assert decide.probe("bare", "d3") == (None, "systemone")
assert decide.probe("barehub", "d3") == ("barehub has no decisions URL in config/endpoints.json", "")
decide.LOG.unlink()
decide.urllib.request.urlopen = urlopen

# ---- every call is logged, and an error's body survives the log -------------------------------

out = decide.ask("dm1", "hello", {"q": decide.PROBES["noul"]}, use="test")
assert out["answers"]["q"]["noul"] == 0.9 and SEEN[-1]["url"].endswith("/openai/systemone")
row = decide.log_rows()[-1]
assert (row["alias"], row["served"], row["use"], row["types"], row["chars"]) == ("dm1", "d1", "test", {"q": "noul"}, 5)
assert row["answers"]["q"]["noul"] == 0.9 and row["error"] == "" and isinstance(row["seconds"], float), row
before = len(SEEN)
try:
    decide.ask("dm2", "hello", {"q": decide.PROBES["noul"]}, use="test", retry=False)
except urllib.error.HTTPError as exc:
    assert "free-models-per-min" in decide.error_text(exc)          # read once by the log, kept for the caller
else:
    raise AssertionError("dm2 should have failed")
assert len(SEEN) == before + 1, "retry=False: a 429 is not retried"
row = decide.log_rows()[-1]
assert row["alias"] == "dm2" and row["answers"] is None and "429 Rate limit" in row["error"], row

# ---- shapes: nothing private, the status kept -------------------------------------------------

s = decide.shape("Error code: 429 - key sk-or-v1-abcdef0123456789 for a.b@example.com at"
                 " https://x.example/v1?key=secret, trace 3fa2c9d81b, retry in 37 s")
assert "429" in s and "<email>" in s and "<url>" in s and "retry in N s" in s, s
assert not any(w in s for w in ("secret", "example.com", "abcdef0123", "3fa2c9d81b")), s
assert decide.shape("word " * 400).count("…") == 1                    # start and end kept

# ---- the sets, from the data written above ----------------------------------------------------

sets = decide.build_sets()
labels = {k: sorted(str(i["label"]) for i in v["items"]) for k, v in sets.items()}
assert labels["errors"] == ["busy", "empty", "quota", "timeout"], labels           # the gremlins: unrecognised
assert labels["collection"] == ["core", "core", "fast", "vision"], labels           # not the pick's, not ad hoc
assert labels["useful"] == ["False", "True"], labels                                 # text only, no ui test
assert set(sets) == {"errors", "collection", "useful"}                               # no shape set
assert set(sets["collection"]["question"]["criteria"]) == {"core", "fast", "vision"}
assert sets["collection"]["question"]["criteria"]["fast"] == "No description. Tags: fast."
assert "Be brief.\n\nSpam or not" in next(i["state"] for i in sets["collection"]["items"] if i["label"] == "fast")
assert next(i for i in sets["useful"]["items"] if i["label"])["state"].startswith("THE REQUEST\n\nReview this")
assert not any("3fa2c9d81b" in i["state"] for i in sets["errors"]["items"])
for v in sets.values():
    for i in v["items"]:
        TRUTH[i["state"]] = i["label"]

# ---- who is asked, before any result: the first onboarded that takes the type, not ad hoc ------

assert decide.best("errors") == "dm2" and decide.best("useful") == "dm2" and decide.best("role") == "dm2"
# Per request (the gateway's picks, the notice check) the :free dm2 is spared while dm1 takes the question.
assert decide.best("shape") == "dm1" and decide.best("collection") == "dm1" and decide.best("notice") == "dm1"
who, why = decide.choose("shape")
assert who == "dm1" and "the default" in why and ":free models spared" in why and "collection set" in why, why
write("config/decision-models.json", {"dm2": MODELS["dm2"]})
assert decide.best("shape") == "dm2"                                  # the only one: the free budget it is
write("config/decision-models.json", {"dear": {"served": "dd", "via": "hub", "question_types": ["choice"]}})
assert decide.best("shape") is None                                   # ad hoc only: nobody
answer, by, why = decide.pick("shape", "x", decide.SHAPES, "test")
assert (answer, by, why) == ({}, "", "no decision model onboarded")
write("config/decision-models.json", MODELS)

# ---- the measures, by hand ----------------------------------------------------------------------

choice = [
    {"set": "s", "label": "a", "seconds": 1.0, "at": "1",
     "answers": {"s": {"choice": "a", "probabilities": {"a": 0.9, "b": 0.1}, "confidence": 0.8}}},
    {"set": "s", "label": "a", "seconds": 3.0, "at": "2",
     "answers": {"s": {"choice": "b", "probabilities": {"a": 0.3, "b": 0.7}, "confidence": 0.4}}},
    {"set": "s", "label": "a", "seconds": 2.0, "at": "3", "answers": None, "error": QUOTA}]
m = decide.measure(choice)
assert (m["asked"], m["answered"], m["correct"], m["accuracy"]) == (3, 2, 1, 0.5), m
assert (m["brier"], m["log_loss"], m["ece"], m["macro_f1"], m["seconds"]) == (0.5, 0.6547, 0.4, 0.333, 2.0), m
assert m["confident"] == {"cutoff": 0.6, "coverage": 0.5, "accuracy": 1.0} and m["failures"] == {"quota": 1}, m
assert [(b["from"], b["n"], b["right"]) for b in m["calibration"]] == [(0.6, 1, 0.0), (0.8, 1, 1.0)], m
noul = [{"set": "u", "label": True, "answers": {"u": {"noul": 0.8}}},
        {"set": "u", "label": False, "answers": {"u": {"noul": 0.7}}}]
m = decide.measure(noul)
assert (m["accuracy"], m["brier"], "macro_f1" in m) == (0.5, 0.265, False), m
assert decide.measure([{"set": "u", "label": True, "answers": {"u": {"noul": "?"}}}])["failures"] == {"no answer": 1}
# A null confidence is no confidence: the answer still counts, at the probability it gave its choice.
m = decide.measure([{"set": "s", "label": "a", "answers": {"s": {"choice": "a", "probabilities": {"a": 0.9, "b": 0.1},
                                                                 "confidence": None}}}])
assert m["answered"] == 1 and m["confident"]["coverage"] == 1.0, m
# An endpoint gone from the config is a failed call, logged like any other.
try:
    decide.call("gone", "x", "s", {"q": decide.PROBES["noul"]}, use="test")
except KeyError:
    pass
assert decide.log_rows()[-1]["alias"] == "x" and "KeyError" in decide.log_rows()[-1]["error"]

# ---- a dry run asks nobody; a pass is round-robin, paced, and stops a model at its first failure --

plan = decide.bench(per=1, dry=True, out=lambda *_: None)
assert set(plan) == {"dm2", "dm1", "yesno"}, plan                     # never the ad hoc one
assert [s for s, _ in plan["yesno"]] == ["useful"] and [s for s, _ in plan["dm1"]] == list(decide.SETS), plan
SEEN.clear()
slept: list[float] = []
res = decide.bench(per=1, gap=7, sleep=slept.append, out=lambda *_: None)
assert [q["model"] for q in SEEN] == ["vendor/d2:free", "d1", "y", "d1", "d1"], [q["model"] for q in SEEN]
assert slept == [7] * 4, slept
assert set(res["models"]["dm1"]) == set(decide.SETS) and all(r["correct"] == 1 for r in res["models"]["dm1"].values())
assert res["models"]["dm2"]["errors"]["failures"] == {"rate": 1}, res["models"]["dm2"]
assert res["sets"]["errors"]["size"] == 4 and res["min_items"] == decide.MIN_ITEMS, res
assert json.loads(decide.RESULTS.read_text(encoding="utf-8"))["models"]["yesno"]["useful"]["correct"] == 1
# One item each is under MIN_ITEMS: nobody is ranked, the defaults stand.
assert decide.best("errors") == "dm2" and "none has 8 items on the errors set" in decide.choose("errors")[1]
decide.MIN_ITEMS = 1
assert decide.best("errors") == "dm1" and decide.best("role") == "dm1"           # 1 of 1 beats 0 of 1
assert decide.choose("errors")[1] == "best of 2 ranked on the errors set: 1 right of 1 asked"
assert decide.best("shape") == "dm1" and decide.best("useful") == "dm1"           # a tie with yesno: config order
decide.MIN_ITEMS = 8
bench_rows = [r for r in decide.log_rows() if r.get("use") == "bench"]
assert len(bench_rows) == 5 and all(r["item"] and "label" in r for r in bench_rows), bench_rows
# A second pass asks each model the items it was asked longest ago: another collection item now.
plan2 = decide.bench(per=1, dry=True, out=lambda *_: None)
assert dict(plan2["dm1"])["collection"] != dict(plan["dm1"])["collection"], (plan, plan2)

# ---- the pick asks the best, and logs the use ----------------------------------------------------

answer, by, why = decide.pick("shape", "Review this function for bugs.", decide.SHAPES, "pick_shape")
assert by == "dm1" and not why and answer["choice"] in decide.SHAPES
assert decide.log_rows()[-1]["use"] == "pick_shape"

# ---- unrecognised error shapes: asked once each, cached, read without a call ----------------------

import run  # noqa: E402

decide.MIN_ITEMS = 1                                                  # dm1 ranked first on errors from here
SEEN.clear()
TRUTH[decide.shape(ODD)] = "unreachable"                              # what the stub's dm1 will say
assert run.uptime_class(ODD) == ("unrecognised", "nothing matched")
assert decide.classify_errors(limit=3, sleep=slept.append) == 1 and len(SEEN) == 1   # only the gremlins
assert SEEN[0]["state"] == decide.shape(ODD) and SEEN[0]["model"] == "d1"
assert decide.cached_error_class(ODD) == ("unreachable", "decided by dm1 at 0.85"), decide.cached_error_class(ODD)
assert decide.classify_errors(limit=3) == 0                           # asked once per shape
assert decide.cached_error_class("something else entirely") is None
# uptime_class reads the decision (no call), the benchmark's labels never do.
assert run.uptime_class(ODD) == ("unreachable", "decided by dm1 at 0.85") and len(SEEN) == 1
assert run.uptime_class(ODD, decided=False) == ("unrecognised", "nothing matched")
assert decide.build_sets(["errors"])["errors"]["items"] == sets["errors"]["items"]
# A decided `empty` is never used: an error is not an answer, and empty counts as up.
import locked  # noqa: E402
locked.update(decide.DECIDED, lambda d: d["errors"][decide.shape(ODD)].__setitem__("class", "empty"))
assert run.uptime_class(ODD)[0] == "unrecognised"
locked.update(decide.DECIDED, lambda d: d["errors"][decide.shape(ODD)].__setitem__("class", "unreachable"))

# ---- the provider-notice fallback: asked only for a seat's short reply that looks like one ----------

SEEN.clear()
NOTE = "Your plan's balance is low; top up soon to keep going."
assert run.provider_notice("ready") is None and run.provider_notice("ready", ask=True) is None
assert run.provider_notice("The limit here is " + "x " * 200, ask=True) is None      # long: an answer
assert run.provider_notice(NOTE) is None and not SEEN                 # a page or a score never asks
assert run.provider_notice("you have run out of credits") == "run out of credits" and not SEEN   # the phrases first
assert run.provider_notice(NOTE, ask=True) == "decided by dm1 at 0.90" and len(SEEN) == 1
assert SEEN[0]["model"] == "d1" and SEEN[0]["questions"]["notice"]["type"] == "noul"   # per request: not :free
assert decide.log_rows()[-1]["use"] == "notice"
assert run.provider_notice(NOTE, ask=True) == "decided by dm1 at 0.90" and len(SEEN) == 1   # kept: asked once
assert run.provider_notice(NOTE) == "decided by dm1 at 0.90"                         # and read anywhere
assert run.failure_class(NOTE) == "entitlement"

# ---- a role suggestion is kept, never set --------------------------------------------------------

hint = decide.suggest_role("m2", "model: vendor/m2\ncontext window: 8192\ndescription: A small coder.")
assert (hint["role"], hint["confidence"], hint["by"]) == ("general", 0.85, "dm1"), hint   # the stub's first option
assert "decision" not in SEEN[-1]["questions"]["role"]["criteria"], SEEN[-1]
kept = json.loads(decide.DECIDED.read_text(encoding="utf-8"))
assert kept["roles"]["m2"]["role"] == hint["role"] and "unreachable" == kept["errors"][decide.shape(ODD)]["class"]
assert json.loads((TMP / "config" / "model-roles.json").read_text(encoding="utf-8"))["models"] == {"m1": "general"}

shutil.rmtree(TMP, ignore_errors=True)
print("ok")
