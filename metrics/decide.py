"""Ask a decision model a typed question: choose, score, or yes/no.

    python metrics/decide.py mercury-decide-free "List every place serve.py writes a file." \\
        --choice review,design,enumeration,code
    python metrics/decide.py span-01-lite-free "Please refund the duplicate charge." \\
        --noul "Is this about billing?"
    python metrics/decide.py bench [--per 1] [--gap 30] [--sets errors,shape] [--models a,b] [--dry-run]
    python metrics/decide.py results

DECISION MODELS ARE NOT CHAT MODELS
-----------------------------------
Mercury Decide and Respan take a `state` and a map of typed `questions` and
return each answer with a probability. They refuse /chat/completions outright
("is a decisions model and cannot be used with the chat/completions endpoint"),
so `llm -m` cannot reach them, the availability probe and the suite cannot
test them, and they never go in the llm yaml. They live in
`config/decision-models.json` instead, written by `scripts/onboard.py` when a
model answers a typed question.

WHERE THE CALL GOES
-------------------
Each endpoint in `config/endpoints.json` may carry a `decisions` URL. Open
WebUI has one only through its `/openai/{path}` passthrough, which forwards to
`{connection base}/decisions` and answers only when the server runs with
ENABLE_OPENAI_API_PASSTHROUGH=True. That reaches Inception
(`api.inceptionlabs.ai/v1/decisions`) but never OpenRouter, whose Decisions API
is `/api/alpha/decisions`, outside its `/api/v1` base.

A model is called on the endpoint it was picked from when onboarding, recorded
as `via`: picked from Open WebUI, it goes through Open WebUI; picked from
`openrouter (direct)`, it goes direct. Nothing here calls Open WebUI on its
own: the passthrough is reached only through an endpoint's own `decisions`
URL, which nothing writes (an Open WebUI hub is given one by hand), and the
onboarding hints about it show only for an Open WebUI hub (endpoints.hub). OpenRouter's /models omits its decision
models, so that endpoint names them in `unlisted_models` to make them pickable.

Cloudflare Workers AI answers decisions only on `/ai/run/<model>`, outside its
OpenAI-compatible `/ai/v1` base, so no hub can relay them: its endpoint's
`decisions` URL is `.../accounts/<id>/ai/run/{model}`. `{model}` puts the
served id in the URL and leaves it out of the body (Cloudflare refuses
`@cf/...` there), and its `{"result": {...}}` wrapper is taken off the reply.
Its /models answers 405, so that endpoint lists from `models_url`, the
catalog search (`.../ai/models/search`), read by endpoints.list_models.

WHAT EACH MODEL CAN ANSWER
--------------------------
`question_types` lists the types a model accepted when onboarding probed it
with one question of each: Mercury Decide takes noul, choice and score;
Respan takes noul only, with plain-string instructions and criteria. `ask`
refuses a type the entry does not list, so a caller learns it from the bench
rather than from a 400. Both providers take the same body (`model`, `state`,
`questions`) and return the same `answers`, so one client covers them:
noul `{"noul": p_yes}`; choice `{"choice", "probabilities": {option: p},
"confidence"}`; score `{"score", "legend", "probabilities", "confidence"}`.

EVERY CALL IS WRITTEN DOWN
--------------------------
`call` is the only way out, so every question asked (the gateway's picks,
onboarding's probes and role suggestion, the benchmark) is one line in
metrics/decisions.jsonl: when, the model, what it was for (`use`), the
question types, the answers with their probabilities, seconds, and the error
(redacted, clipped). The state is not kept, only its length. A benchmark
line adds its set, item and label. Not in the usage ledger: these are not
the built-in client's chat calls, and no pacing rule counts them.

THE DECISION BENCHMARK (`bench`)
--------------------------------
Labelled sets built from the bench's own data, no hand labelling:

  errors      choice: failed calls' error text as shapes (redacted; numbers,
              ids, emails and URLs as placeholders, 4xx/5xx kept), labelled by
              suite/run.uptime_class's rules alone (never a cached decision),
              the classes in ERROR_CLASSES
  collection  choice: gateway requests (runs/gateway/calls.jsonl), labelled
              with the collection the caller named; options are the
              collections not ad hoc, with their descriptions and tags
  useful      noul: text seat answers the owner judged (metrics/offloads.jsonl),
              with their request; USEFUL_EACH of each verdict, newest first

No shape set (dropped 2026-11-08): a collection's own shape says which
collection a caller used, not what the prompt is, so it measured the
collections' settings rather than the models. A request the gateway's own
pick sent to a collection (`picked`) labels nothing: that label would be a
decision model's. Each run asks every
onboarded model (not ad hoc) `--per` questions per set it can answer, the
ones it was asked longest ago first, ROUND-ROBIN across models `--gap`
seconds apart (suite/pace.py's lesson: one model is asked once a lap, never
in a burst), and stops a model at its first failure. The `:free` models
share OpenRouter's free daily budget, so the scheduled run is one question
per model per set. Per model and set, from each item's latest answer in the
log: accuracy (macro-F1 for choice), Brier score and log loss from the
probabilities, calibration (five bins by the probability given to the
answer, and their weighted gap, ECE), coverage and accuracy at CONFIDENT
(read from the answer's `confidence`, as the gateway reads it), median
seconds, and failures by uptime_class. Written to metrics/decide-bench.json.

WHO IS ASKED IN USE (`choose`, `best`)
--------------------------------------
Each use is ranked on one set (RANKED_BY): its own, or the nearest one of
its question type (the gateway's shape pick and a new model's role on the
collection set, both one described option of several for a text; the
provider-notice check on useful, the only yes/no set). Only models with
MIN_ITEMS or more items asked on that set are ranked, by correct answers
Laplace-smoothed ((correct + 1) / (asked + 2), failures counted as wrong), so
one lucky answer does not outrank a long record; ties go to config order.
With none ranked, the first onboarded model that takes the type, as before
the benchmark. A use made per request (PER_REQUEST: the gateway's picks, the
notice check) never goes to a `:free` model, whose calls come out of
OpenRouter's free daily budget that the suite and the probe share, while
another model takes the question; the daily benchmark may, paced.

WHAT THE ANSWERS ARE KEPT FOR (metrics/decided.json)
----------------------------------------------------
`roles`: a chat model's suggested role from onboarding (suggest_role),
shown, never set. `errors`: an unrecognised error shape's class
(classify_errors, a few new shapes a scheduled run), which uptime_class reads
through cached_error_class; never asked at render time. `notices`: whether a
short reply the phrase list missed is a provider's notice (notice), asked
from the gateway's seats only, read everywhere else.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import math
import pathlib
import re
import statistics
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
FILE = DATA / "config" / "decision-models.json"
LOG = DATA / "metrics" / "decisions.jsonl"
RESULTS = DATA / "metrics" / "decide-bench.json"
DECIDED = DATA / "metrics" / "decided.json"
sys.path.insert(0, str(ROOT / "metrics"))
sys.path.insert(0, str(ROOT / "suite"))
import endpoints  # noqa: E402

# One question of each type, plain strings throughout so Respan's rule holds.
PROBES = {
    "noul": {"type": "noul", "instructions": "Is this message about billing?",
             "criteria": {"true": "It is about charges, payments or refunds.",
                          "false": "It is about something else."}},
    "choice": {"type": "choice", "instructions": "Which team should handle this message?",
               "criteria": {"billing": "Charges, payments, refunds.",
                            "support": "Product problems.", "sales": "Buying new things."}},
    "score": {"type": "score", "instructions": "How urgent is this message?",
              "criteria": ["Can wait", "Needs attention soon", "Needs immediate attention"]},
}
PROBE_STATE = "Please refund the duplicate charge on my account today."
# Providers name the same API differently: Inception and OpenRouter
# `decisions`, Ollama `systemone`. An endpoint URL holding `{path}` (Open
# WebUI's passthrough, which appends it to the connection's base) is tried
# with each, and the one that answered is recorded on the model as `path`.
PATHS = ("decisions", "systemone")

# What the gateway's shape picker chooses between. Each option carries a
# description because bare names mislead it: Mercury Decide called "list every
# place serve.py writes a file" `code` at 0.93 when given names alone.
SHAPES = {
    "general": "A general question or task that fits none of the other kinds.",
    "design": "Architecture, process or approach decisions: weighing options, an ADR, a plan.",
    "prose": "Writing or editing documentation or other text.",
    "review": "Judging whether given code is correct: finding bugs, or where a guarantee breaks.",
    "code": "Writing new code or changing existing code.",
    "enumeration": "Listing every place something happens in given code or files.",
    "quick": "A short, bounded task: classify, extract or summarise a small input.",
}
# The uptime classes the error set asks about (the owner's list, 2026-11-08):
# rate, notsent, client and relay are left out, as is unrecognised.
ERROR_CLASSES = {
    "quota": "Our own long-window allowance is used up: a daily, monthly or hourly quota, credits, a usage limit.",
    "busy": "The provider is overloaded or failing: high demand, a shared pool at its limit, a server error"
            " (5xx), or a 429 that names no allowance of ours.",
    "timeout": "No answer in time: timed out, deadline exceeded, a 408, 504 or 524.",
    "unreachable": "The connection failed or broke: refused, reset, DNS, SSL, a broken or empty stream,"
                   " an HTML error page.",
    "setup": "Our setup or our request: the sign-in was refused, no such model, no access or paywalled,"
             " or a request the provider refused as malformed or too large.",
    "empty": "The call came back with no text and no error.",
}
# The question each use asks, the same in the benchmark and in use, so the
# benchmark measures what is used.
INSTRUCTIONS = {
    "errors": "What kind of failure does this error from a call to a model provider show?",
    "collection": "Which collection of models should answer this request?",
    "shape": "What kind of task does this prompt ask for?",
    "useful": "Is this answer useful for the request?",
    "role": "What is this chat model for, judging by its catalog facts and description?",
    "notice": "Is this reply a notice from the provider about the account instead of an answer?",
}
NOULS = {"useful": {"true": "The answer addresses the request, what it claims holds, and it is worth acting on.",
                    "false": "The answer is wrong, beside the point, or not worth acting on."},
         "notice": {"true": "It is the provider speaking about the account: credits, a quota, a plan, a"
                            " balance or a limit, not an answer to anything asked.",
                    "false": "It is an answer, however short, or anything else."}}
KINDS = {"errors": "choice", "collection": "choice", "shape": "choice", "useful": "noul", "role": "choice",
         "notice": "noul"}
SETS = ("errors", "collection", "useful")
# The set each use is ranked on (module docstring); a set ranks itself.
RANKED_BY = {"shape": "collection", "role": "collection", "notice": "useful"}
USES = ("shape", "collection", "notice", "errors", "role")          # as the Benchmarks card lists them
PER_REQUEST = ("shape", "collection", "notice")                     # asked while a caller waits
MIN_ITEMS = 8             # items asked on a set before a model is ranked on it
CONFIDENT = 0.6           # the gateway acts on a choice only at this confidence or more
MAX_STATE = 20_000        # characters of request sent, as pick_shape always cut it
PER_LABEL = 12            # items per label in a choice set, so one busy label cannot fill it
USEFUL_EACH = 20          # judged answers of each verdict in the useful set
EMPTY_STATE = "(the call came back with no text and no error)"


def load() -> dict[str, dict]:
    import locked
    try:
        return json.loads(locked.read_text(FILE))      # inside a render, from its snapshot
    except (OSError, ValueError):
        return {}


def call(endpoint: str, served: str, state, questions: dict, timeout: int = 60,
         path: str = "decisions", alias: str = "", use: str = "", meta: dict | None = None,
         retry: bool = True) -> dict:
    """POST to an endpoint's decisions URL. Returns the parsed body; raises on
    HTTP errors. Every call is logged (metrics/decisions.jsonl), answered or
    not; `alias` and `use` say who was asked and what for, `meta` adds the
    benchmark's set, item and label. `retry`: a 429 waits and tries again."""
    t, started, reply, error = time.time(), time.monotonic(), None, ""
    try:
        # Inside the try, so an endpoint gone from the config is logged too.
        entry = endpoints.load()[endpoint]
        url = (entry.get("decisions") or "").replace("{path}", path)
        if not url:
            raise ValueError(f"{endpoint} has no decisions URL in config/endpoints.json")
        headers = {"Content-Type": "application/json", **(entry.get("headers") or {})}
        if (secret := endpoints.key(endpoint)):
            headers["Authorization"] = f"Bearer {secret}"
        payload = {"model": served, "state": state, "questions": questions}
        if "{model}" in url:
            # Cloudflare names the model in the URL (/ai/run/@cf/cloudflare/clef)
            # and refuses its own id in the body ("clef", not "@cf/...", is valid there).
            url = url.replace("{model}", served)
            del payload["model"]
        body = json.dumps(payload).encode()
        # Inception answers 429 to calls in quick succession (seen 2026-10-16:
        # the second of three onboarding probes), so a 429 waits and retries.
        for wait in ((5, 15, 0) if retry else (0,)):
            request = urllib.request.Request(url, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    reply = json.load(response)
                    # Cloudflare wraps every reply: {"result": {"answers": ...}, "success": true}.
                    if isinstance(reply, dict) and "answers" not in reply and isinstance(reply.get("result"), dict):
                        reply = reply["result"]
                    return reply
            except urllib.error.HTTPError as exc:
                if exc.code != 429 or not wait:
                    raise
                time.sleep(wait)
    except Exception as exc:                           # noqa: BLE001 -- logged, then raised as it was
        error = error_text(exc)
        try:
            exc.decide_text = error                    # an HTTP error's body can be read only once
        except AttributeError:
            pass
        raise
    finally:
        _log({"t": round(t, 3), "at": dt.datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds"),
              "alias": alias or served, "served": served, "via": endpoint, "use": use,
              "types": ({k: q.get("type") if isinstance(q, dict) else None for k, q in questions.items()}
                        if isinstance(questions, dict) else None),
              "chars": len(state) if isinstance(state, str) else None,
              "answers": (reply or {}).get("answers") if isinstance(reply, dict) else None,
              "seconds": round(time.monotonic() - started, 2), "error": _clean(error), **(meta or {})})


def _clean(error: str) -> str:
    """An error as the log keeps it: redacted (images, URL queries, base64), clipped."""
    if not error:
        return ""
    import client
    return client.clip(client.redact(error))


def _log(row: dict) -> None:
    """One call's line. Never fails the call it records."""
    import locked
    try:
        with locked.lock(LOG):
            with LOG.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception:                                  # noqa: BLE001 -- a full disk must not lose the answer
        pass


def log_rows() -> list[dict]:
    """Every logged call, oldest first."""
    return _jsonl(LOG)


def _jsonl(path: pathlib.Path) -> list[dict]:
    """A log's objects, oldest first; a line still being written is skipped."""
    out = []
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    out.append(row)
    except OSError:
        pass
    return out


def error_text(exc: Exception) -> str:
    if (seen := getattr(exc, "decide_text", None)) is not None:
        return seen
    if isinstance(exc, urllib.error.HTTPError):
        try:
            detail = json.loads(exc.read() or b"{}")
        except ValueError:
            detail = ""
        # {"error": {"message": ...}}, {"error": "..."}, {"detail": ...}: providers differ.
        if isinstance(detail, dict):
            detail = detail.get("error") or detail
        if isinstance(detail, dict):
            detail = detail.get("message") or detail
        return f"{exc.code} {str(detail)[:160]}".strip()
    return f"{type(exc).__name__}: {exc}"


def probe(endpoint: str, served: str) -> tuple[str | None, str]:
    """(None, path) if `endpoint`'s decisions URL answers a yes/no question for
    `served`, else (why not, ""). Each of PATHS is tried where the URL has `{path}`."""
    url = endpoints.load()[endpoint].get("decisions")
    if not url:
        return f"{endpoint} has no decisions URL in config/endpoints.json", ""
    why = []
    for path in (PATHS if "{path}" in url else PATHS[:1]):
        try:
            answer = call(endpoint, served, PROBE_STATE, {"probe": PROBES["noul"]}, path=path, use="onboard probe")
        except Exception as exc:                       # noqa: BLE001
            why.append(f"{endpoint} /{path}: {error_text(exc)}")
            continue
        if "probe" in answer.get("answers", {}):
            return None, path
        why.append(f"{endpoint} /{path}: no answer in {str(answer)[:120]}")
    return "; ".join(why), ""


def refuses_chat(endpoint: str, served: str) -> bool:
    """True if a one-token chat call is refused because the model is a decision
    model ("is a decisions model and cannot be used with the chat/completions
    endpoint", as OpenRouter words it, relayed by Open WebUI)."""
    entry = endpoints.load()[endpoint]
    headers = {"Content-Type": "application/json", **(entry.get("headers") or {})}
    if (secret := endpoints.key(endpoint)):
        headers["Authorization"] = f"Bearer {secret}"
    body = json.dumps({"model": served, "max_tokens": 1,
                       "messages": [{"role": "user", "content": "ready?"}]}).encode()
    request = urllib.request.Request(endpoints.base(endpoint) + "/chat/completions",
                                     data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60):
            return False
    except urllib.error.HTTPError as exc:
        return names_decisions(error_text(exc))
    except Exception:                                  # noqa: BLE001
        return False


def names_decisions(text: str) -> bool:
    """A chat refusal that says the model is a decision model. OpenRouter names
    it ("is a decisions model"); Cloudflare only lists the fields it wanted
    ("required properties ... 'model,state,questions'", "Required value
    missing: questions"). Every Decisions API takes `state` and `questions`,
    so a refusal asking for both counts, whoever words it."""
    low = (text or "").lower()
    if any(w in low for w in ("decisions model", "decision model", "systemone", "/decisions")):
        return True
    return "questions" in low and ("state" in low or "required" in low)


def question_types(endpoint: str, served: str, path: str = "decisions") -> list[str]:
    """Which question types the model accepts, one probe call per type.

    noul is taken as proven: `probe` has just had one answered, and asking
    again only lets a transient 429 record `[]`, which `ask` would then read
    as "takes nothing"."""
    ok = ["noul"]
    for kind, question in PROBES.items():
        if kind in ok:
            continue
        try:
            if kind in call(endpoint, served, PROBE_STATE, {kind: question}, path=path,
                            use="onboard question types").get("answers", {}):
                ok.append(kind)
        except Exception:                              # noqa: BLE001
            pass
    return ok


def ask(alias: str, state, questions: dict, use: str = "", meta: dict | None = None, retry: bool = True,
        timeout: int = 60) -> dict:
    entry = load()[alias]
    wrong = sorted({q["type"] for q in questions.values()} - set(entry["question_types"]))
    if wrong:
        raise ValueError(f"{alias} does not take {', '.join(wrong)} questions;"
                         f" it takes {', '.join(entry['question_types'])}")
    return call(entry["via"], entry["served"], state, questions, timeout=timeout,
                path=entry.get("path", "decisions"), alias=alias, use=use, meta=meta, retry=retry)


# ---- in use: who is asked, and the shared question --------------------------

def question(name: str, options: dict | None = None) -> dict:
    """The question a set or use asks. Choice options are {name: description}."""
    if KINDS[name] == "noul":
        return {"type": "noul", "instructions": INSTRUCTIONS[name], "criteria": NOULS[name]}
    return {"type": "choice", "instructions": INSTRUCTIONS[name],
            "criteria": options if options is not None else {"errors": ERROR_CLASSES, "shape": SHAPES}[name]}


def results() -> dict:
    import locked
    return locked.read_json(RESULTS)


def shares_free(alias: str, models: dict | None = None) -> bool:
    """A `:free` model: its calls come out of OpenRouter's free daily budget,
    shared with the suite's free rotation and the free models' probes (the
    same mark suite/run.free_models reads)."""
    return ":free" in ((models if models is not None else load()).get(alias) or {}).get("served", "")


def choose(name: str, measured: dict | None = None) -> tuple[str | None, str]:
    """(the model to ask for `name`, why that one): a set, or a use in USES.
    Ranked on RANKED_BY's set among models with MIN_ITEMS or more items there,
    else the first onboarded that takes the question's type; never ad hoc, and
    per request never a `:free` model while another takes it (module
    docstring). `measured`: the results' models, read when not given."""
    import model_roles
    kind, held, models = KINDS[name], set(model_roles.load()["ad_hoc"]), load()
    ready = [a for a, d in models.items() if kind in (d.get("question_types") or []) and a not in held]
    spared = ""
    if name in PER_REQUEST and (paid := [a for a in ready if not shares_free(a, models)]):
        spared = "; :free models spared, as it is asked per request" if len(paid) < len(ready) else ""
        ready = paid
    if not ready:
        return None, f"no onboarded decision model takes {kind} questions"
    on = RANKED_BY.get(name, name)
    measured = (results().get("models") or {}) if measured is None else measured
    rows = {a: r for a in ready if isinstance(r := (measured.get(a) or {}).get(on), dict)
            and r.get("asked", 0) >= MIN_ITEMS}
    if not rows:
        return ready[0], (f"the default: the first onboarded that takes {kind} questions; none has"
                          f" {MIN_ITEMS} items on the {on} set yet{spared}")
    score = {a: (r.get("correct", 0) + 1) / (r["asked"] + 2) for a, r in rows.items()}
    top = max(rows, key=lambda a: score[a])                        # the first of a tie: config order
    r = rows[top]
    return top, (f"best of {len(rows)} ranked on the {on} set: {r.get('correct', 0)} right of {r['asked']}"
                 f" asked{spared}")


def best(name: str, measured: dict | None = None) -> str | None:
    """The model `choose` names for `name`."""
    return choose(name, measured)[0]


def pick(name: str, state: str, options: dict | None, use: str, timeout: int = 60) -> tuple[dict, str, str]:
    """(the answer, the model asked, why there is none): `name`'s question,
    with these options, to its model (choose). The answer is {} when there is
    no model or the call failed, and `why` says which."""
    try:
        alias = best(name)
    except Exception as exc:                           # noqa: BLE001 -- a hand-broken config: no decision, never a 500
        return {}, "", f"no decision model could be chosen ({type(exc).__name__}: {exc})"
    if not alias:
        return {}, "", "no decision model onboarded"
    try:
        return (ask(alias, state[:MAX_STATE], {name: question(name, options)}, use=use, timeout=timeout)
                ["answers"][name], alias, "")
    except Exception as exc:                           # noqa: BLE001
        return {}, alias, f"{alias} failed ({error_text(exc)[:80]})"


def collection_options(cols: dict) -> dict:
    """Each collection as a choice option: its description and tags."""
    return {n: (c.get("description") or c.get("note") or "No description.")
            + (f" Tags: {', '.join(c['tags'])}." if c.get("tags") else "") for n, c in cols.items()}


def suggest_role(alias: str, facts: str) -> dict:
    """A decision model's suggestion for a chat model's role (metrics/model_roles.py),
    from its catalog facts and description, kept in metrics/decided.json under
    `roles` with its confidence. A suggestion only: the role is never set from it."""
    import locked
    import model_roles
    roles = {r: d for r, d in model_roles.load()["roles"].items() if r != "decision"}
    answer, by, why = pick("role", facts, roles, f"role {alias}")
    if why or answer.get("choice") not in roles:
        return {"error": why or f"{by} answered {answer.get('choice')!r}, not a role"}
    rec = {"role": answer["choice"], "confidence": round(float(answer.get("confidence") or 0), 3),
           "probabilities": answer.get("probabilities") or {}, "by": by,
           "at": dt.datetime.now().astimezone().isoformat(timespec="seconds")}
    locked.update(DECIDED, lambda d: d.setdefault("roles", {}).__setitem__(alias, rec), ensure_ascii=False)
    return rec


# ---- error shapes -------------------------------------------------------------

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
ID = re.compile(r"\b(?=[A-Za-z_-]*\d)(?=[\d_-]*[A-Za-z])[A-Za-z0-9_-]{8,}\b|\b[A-Za-z0-9_-]{24,}\b")
NUMBER = re.compile(r"\d+(\.\d+)?")


def shape(text: str) -> str:
    """An error as a shape, the same failure the same text whichever call it
    came from, with nothing private in it: client.redact first, then URLs,
    emails, ids and keys (runs of 24 or more, or of 8 or more mixing letters
    and digits) as placeholders, numbers as N except a 4xx or 5xx status, whitespace
    collapsed, start and end kept. The benchmark's error state and the
    cache's key."""
    import client
    s = re.sub(r"https?://\S+", "<url>", client.redact(text))
    s = ID.sub("<id>", EMAIL.sub("<email>", s))
    s = NUMBER.sub(lambda m: m.group() if re.fullmatch(r"[45]\d\d", m.group()) else "N", s)
    return client.clip(" ".join(s.split()), 250, 250)


def _gateway_calls() -> list[dict]:
    return _jsonl(DATA / "runs" / "gateway" / "calls.jsonl")


def error_texts(calls: list[dict] | None = None) -> list[tuple[str, str]]:
    """(status, text) of every failed call the bench kept text for: gateway
    seats, the probe's detail (perf/history.csv) and the usage ledger."""
    import csv
    import ledger
    out = [(s["status"], s.get("error") or "") for r in (calls if calls is not None else _gateway_calls())
           for s in r.get("seats") or [] if s.get("status") in ("error", "empty")]
    try:
        with (DATA / "perf" / "history.csv").open(encoding="utf-8", newline="") as fh:
            out += [("error", r["detail"]) for r in csv.DictReader(fh) if r.get("detail")]
    except OSError:
        pass
    return out + [(r.get("status") or "error", r["error"]) for r in ledger.rows() if r.get("error")]


def classify_errors(limit: int = 3, gap: float = 30, sleep=time.sleep) -> int:
    """Ask the errors set's best model about up to `limit` error shapes that
    uptime_class does not recognise and nobody asked about yet, one call each,
    `gap` seconds apart, stopping at the first failure; each answer goes to
    metrics/decided.json `errors`. Returns the calls made."""
    import locked
    from run import uptime_class
    have = locked.read_json(DECIDED).get("errors") or {}
    new = list(dict.fromkeys(shape(t) for s, t in error_texts()
                             if t.strip() and uptime_class(t, s, decided=False)[0] == "unrecognised"))
    new = [k for k in new if k not in have][:limit]
    alias = best("errors")
    made = 0
    for key in new if alias else ():
        if made:
            sleep(gap)
        made += 1
        try:
            a = ask(alias, key, {"errors": question("errors")}, use="errors cache", retry=False)["answers"]["errors"]
        except Exception as exc:                       # noqa: BLE001
            print(f"  {alias} failed on an error shape: {error_text(exc)[:120]}; stopping")
            break
        rec = {"class": a.get("choice"), "confidence": round(float(a.get("confidence") or 0), 3), "by": alias,
               "at": dt.datetime.now().astimezone().isoformat(timespec="seconds")}
        locked.update(DECIDED, lambda d: d.setdefault("errors", {}).__setitem__(key, rec), ensure_ascii=False)
        print(f"  {alias}: {rec['class']} at {rec['confidence']:.2f}  {key[:90]}")
    return made


def cached_error_class(text: str) -> tuple[str, str] | None:
    """(class, "decided by <model> at <confidence>") a decision model gave
    this error's shape (classify_errors) at CONFIDENT or more, else None.
    Never `empty`: an error is not an empty answer, and empty counts as up.
    Reads only: never a call (uptime_class's fallback, at render time)."""
    import locked
    hit = (locked.read_json(DECIDED).get("errors") or {}).get(shape(text))
    if (isinstance(hit, dict) and hit.get("class") in ERROR_CLASSES and hit["class"] != "empty"
            and isinstance(hit.get("confidence"), (int, float)) and hit["confidence"] >= CONFIDENT):
        return hit["class"], f"decided by {hit.get('by')} at {hit['confidence']:.2f}"
    return None


def notice(text: str, ask_now: bool = False, timeout: int = 15) -> str | None:
    """"decided by <model> at <p>" when a decision model said a short reply
    is a provider's notice about the account (at CONFIDENT or more), else
    None. Its answer is kept by the reply's shape (decided.json `notices`)
    and read from there; it is asked only when `ask_now` and the shape was
    never asked (suite/run.provider_notice says when: a gateway seat's reply
    the phrase list missed that looks like one). A failed call is not kept,
    so the next such reply asks again."""
    import locked
    key = shape(text)
    hit = (locked.read_json(DECIDED).get("notices") or {}).get(key)
    if hit is None and ask_now:
        answer, by, why = pick("notice", text, None, "notice", timeout=timeout)
        if why or not isinstance(answer.get("noul"), (int, float)):
            return None
        hit = {"p": round(float(answer["noul"]), 3), "by": by,
               "at": dt.datetime.now().astimezone().isoformat(timespec="seconds")}
        locked.update(DECIDED, lambda d: d.setdefault("notices", {}).__setitem__(key, hit), ensure_ascii=False)
    if isinstance(hit, dict) and isinstance(hit.get("p"), (int, float)) and hit["p"] >= CONFIDENT:
        return f"decided by {hit.get('by')} at {hit['p']:.2f}"
    return None


# ---- the benchmark's sets -------------------------------------------------------

def _id(state: str) -> str:
    return hashlib.sha1(state.encode("utf-8", "replace")).hexdigest()[:12]


def _capped(by: dict[str, list[str]], cap: int) -> list[dict]:
    """Items, at most `cap` per label, in each label's own order."""
    return [{"id": _id(s), "state": s, "label": label} for label, states in by.items() for s in states[:cap]]


def request_text(record: dict) -> str:
    """A gateway request as the gateway puts it to a decision model (system, then prompt)."""
    return f"{record.get('system') or ''}\n\n{record.get('prompt') or ''}".strip()[:MAX_STATE]


def build_sets(only=None, calls: list[dict] | None = None) -> dict[str, dict]:
    """{set: {"question", "items": [{id, state, label}]}} from the bench's data."""
    import bench_collections
    calls = _gateway_calls() if calls is None else calls
    recent = [r for r in reversed(calls) if not r.get("picked")]      # newest first; a pick labels nothing
    out: dict[str, dict] = {}
    want = set(only or SETS)
    if "errors" in want:
        from run import uptime_class
        seen: dict[str, str] = {}                                     # shape -> its class
        count: collections.Counter = collections.Counter()
        for status, text in error_texts(calls):
            if not text.strip() and status != "empty":
                continue
            key = shape(text) if text.strip() else EMPTY_STATE
            count[key] += 1
            if key not in seen:
                seen[key] = uptime_class(text, status, decided=False)[0]     # the rules alone, never a decision
        by = collections.defaultdict(list)
        for key, _n in count.most_common():                           # the commonest shapes of each class
            if seen[key] in ERROR_CLASSES:
                by[seen[key]].append(key)
        out["errors"] = {"question": question("errors"), "items": _capped(by, PER_LABEL)}
    if "collection" in want:
        cols = {n: c for n, c in bench_collections.load().items() if not c.get("ad_hoc")}
        by, seen = collections.defaultdict(list), set()
        for r in recent:
            name, state = (r.get("model") or "").removeprefix(bench_collections.PREFIX), request_text(r)
            if name in cols and state and state not in seen:
                seen.add(state)
                by[name].append(state)
        out["collection"] = {"question": question("collection", collection_options(cols)),
                             "items": _capped(by, PER_LABEL)}
    if "useful" in want:
        import offload
        panels = {r.get("panel"): r for r in calls}
        pool: dict[bool, list[str]] = {True: [], False: []}
        for e in reversed(offload.entries()):
            if not isinstance(e.get("useful"), bool) or not e.get("call") or str(e.get("note") or "").startswith("ui test"):
                continue
            r = panels.get(e["call"].rpartition(".")[0]) or {}
            seat = next((s for s in r.get("seats") or [] if s.get("call") == e["call"]), None)
            # An image request is left out: a decision model reads its placeholder, not the picture.
            if not seat or seat.get("status") != "ok" or "[image:" in (r.get("prompt") or ""):
                continue
            pool[e["useful"]].append(f"THE REQUEST\n\n{request_text(r)[:8000]}\n\nTHE ANSWER\n\n{seat.get('answer') or ''}"
                                     [:MAX_STATE])
        out["useful"] = {"question": question("useful"),
                         "items": [{"id": _id(s), "state": s, "label": v} for v in (True, False)
                                   for s in list(dict.fromkeys(pool[v]))[:USEFUL_EACH]]}
    return out


# ---- the benchmark's measures ---------------------------------------------------

def _scored(row: dict) -> dict | None:
    """One answer against its label, or None when there is no usable answer:
    right, the probability given to the truth, the probability given to the
    answer it chose (`said`, for calibration), the answer's `confidence` (what
    the gateway's cutoff reads) and its Brier term (summed over the options,
    so 0 to 2 for a choice)."""
    a = (row.get("answers") or {}).get(row.get("set"))
    if row.get("error") or not isinstance(a, dict):
        return None
    label = row.get("label")
    try:
        if isinstance(label, bool):
            p = float(a["noul"])
            said = max(p, 1 - p)
            return {"pred": p >= 0.5, "right": (p >= 0.5) == label, "p_true": p if label else 1 - p,
                    "said": said, "conf": said, "brier": (p - label) ** 2}
        probs = {str(k): float(v) for k, v in (a.get("probabilities") or {}).items()}
        pred, conf = a.get("choice"), a.get("confidence")
        conf = float(conf) if isinstance(conf, (int, float)) else None          # absent or null alike
        said = probs.get(pred, conf or 0.0)
        return {"pred": pred, "right": pred == label, "p_true": probs.get(label, 0.0), "said": said,
                "conf": said if conf is None else conf,
                "brier": sum((probs.get(o, 0.0) - (o == label)) ** 2 for o in {*probs, label})}
    except (KeyError, TypeError, ValueError):
        return None


def _private(via: str | None) -> bool:
    """The call went to a hub on our own network, so a refused connection is
    that hub (uptime's relay), not the provider."""
    import ipaddress
    import urllib.parse
    host = urllib.parse.urlsplit((endpoints.load().get(via or "") or {}).get("base") or "").hostname or ""
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return host == "localhost"


def measure(rows: list[dict]) -> dict:
    """One model on one set, from each item's latest answer (module docstring)."""
    from run import uptime_class
    scored, fails = [], collections.Counter()
    for r in rows:
        if (s := _scored(r)) is None:
            fails[uptime_class(r["error"], "error", _private(r.get("via")))[0] if r.get("error") else "no answer"] += 1
        else:
            scored.append((r, s))
    secs = [r["seconds"] for r in rows if isinstance(r.get("seconds"), (int, float))]
    out = {"asked": len(rows), "answered": len(scored), "correct": sum(s["right"] for _, s in scored),
           "failures": dict(fails), "seconds": round(statistics.median(secs), 2) if secs else None,
           "last": max((r.get("at") or "" for r in rows), default="")}
    n = len(scored)
    if not n:
        return out
    mean = statistics.fmean
    out.update(accuracy=round(out["correct"] / n, 3), brier=round(mean(s["brier"] for _, s in scored), 4),
               log_loss=round(mean(-math.log(max(s["p_true"], 1e-6)) for _, s in scored), 4))
    bins: dict[int, list[dict]] = collections.defaultdict(list)
    for _, s in scored:
        bins[min(int(s["said"] * 5), 4)].append(s)
    out["calibration"] = [{"from": b / 5, "n": len(v), "said": round(mean(x["said"] for x in v), 3),
                           "right": round(mean(x["right"] for x in v), 3)} for b, v in sorted(bins.items())]
    out["ece"] = round(sum(len(v) / n * abs(mean(x["said"] for x in v) - mean(x["right"] for x in v))
                           for v in bins.values()), 4)
    sure = [s for _, s in scored if s["conf"] >= CONFIDENT]
    out["confident"] = {"cutoff": CONFIDENT, "coverage": round(len(sure) / n, 3),
                        "accuracy": round(mean(s["right"] for s in sure), 3) if sure else None}
    if not isinstance(scored[0][0].get("label"), bool):
        f1 = []
        for c in {r["label"] for r, _ in scored} | {s["pred"] for _, s in scored if s["pred"] is not None}:
            tp = sum(r["label"] == c and s["pred"] == c for r, s in scored)
            miss = sum((r["label"] == c) != (s["pred"] == c) for r, s in scored)
            f1.append(2 * tp / (2 * tp + miss) if tp + miss else 0.0)
        out["macro_f1"] = round(mean(f1), 3)
    return out


def summarise(sets: dict | None = None) -> dict:
    """metrics/decide-bench.json from the log: every model on every set it was asked."""
    latest: dict[tuple, dict] = {}
    for r in log_rows():
        if r.get("use") == "bench" and r.get("set") in SETS and r.get("item"):
            latest[(r.get("alias"), r["set"], r["item"])] = r          # the log is in time order
    per: dict[tuple, list] = collections.defaultdict(list)
    for (a, s, _), r in latest.items():
        per[(a, s)].append(r)
    models: dict[str, dict] = {}
    for (a, s), rows in sorted(per.items()):
        models.setdefault(a, {})[s] = measure(rows)
    old = {s: v for s, v in (results().get("sets") or {}).items() if s in SETS}
    info = {s: {"kind": KINDS[s], "size": len(v["items"]),
                "labels": dict(collections.Counter(str(i["label"]) for i in v["items"]))}
            for s, v in (sets or {}).items()}
    out = {"updated": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "confident": CONFIDENT,
           "min_items": MIN_ITEMS, "sets": {**old, **info}, "models": models}
    import locked
    locked.write_atomic(RESULTS, json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    return out


def bench(per: int = 1, gap: float = 30, only_sets=None, only_models=None, dry: bool = False,
          sleep=time.sleep, out=print) -> dict:
    """One benchmark pass (module docstring). Returns the summary, or the
    plan {alias: [(set, item id)]} on a dry run."""
    import model_roles
    sets = build_sets(only_sets)
    held = set(model_roles.load()["ad_hoc"])
    roster = {a: d for a, d in load().items() if a not in held and (not only_models or a in only_models)}
    last: dict[tuple, float] = {}
    for r in log_rows():
        if r.get("use") == "bench":
            last[(r.get("alias"), r.get("set"), r.get("item"))] = r.get("t") or 0
    queue: dict[str, list] = {}
    for a, d in roster.items():
        lanes = []
        for s, v in sets.items():
            if v["question"]["type"] in (d.get("question_types") or []) and v["items"]:
                # The items it was asked longest ago (never, first); the id breaks ties, a fixed shuffle.
                lanes.append([(s, i) for i in sorted(v["items"], key=lambda i: (last.get((a, s, i["id"]), 0), i["id"]))
                              ][:per])
        queue[a] = [lane[k] for k in range(per) for lane in lanes if k < len(lane)]     # a set at a time, in turn
    if dry:
        for a, picks in queue.items():
            out(f"  {a:<22} {len(picks)} questions: " + ", ".join(f"{s}:{i['id'][:6]}" for s, i in picks))
        return {a: [(s, i["id"]) for s, i in picks] for a, picks in queue.items()}
    stopped: dict[str, str] = {}
    made = 0
    for k in range(max((len(p) for p in queue.values()), default=0)):
        for a, picks in queue.items():
            if a in stopped or k >= len(picks):
                continue
            s, item = picks[k]
            if made:
                sleep(gap)
            made += 1
            try:
                answer = ask(a, item["state"], {s: sets[s]["question"]}, use="bench", retry=False,
                             meta={"set": s, "item": item["id"], "label": item["label"]})["answers"][s]
                got = answer.get("choice", answer.get("noul"))
                out(f"  {a:<22} {s:<10} {item['id'][:6]} said {got!s:<12} truth {item['label']}")
            except Exception as exc:                   # noqa: BLE001 -- one failure ends its run: a rate limit is not pressed
                stopped[a] = error_text(exc)
                out(f"  {a:<22} {s:<10} {item['id'][:6]} FAILED, stopped: {stopped[a][:120]}")
    out(f"{made} calls")
    return summarise(sets)


def report(res: dict, out=print) -> None:
    """The results, a line per model and set."""
    out(f"decision benchmark, {res.get('updated', 'never run')}")
    for u in USES:
        who, why = choose(u, res.get("models") or {})
        out(f"  {u:<11} -> {who}: {why}")
    for s in SETS:
        info = (res.get("sets") or {}).get(s, {})
        out(f"\n{s} ({info.get('size', '?')} items)")
        for a, m in sorted((res.get("models") or {}).items()):
            r = m.get(s)
            if not r:
                continue
            out(f"  {a:<22} {r['correct']}/{r['asked']} right, {r['answered']} answered"
                + (f", acc {r['accuracy']:.2f}, Brier {r['brier']:.3f}, log loss {r['log_loss']:.2f}" if r.get("answered") else "")
                + (f", F1 {r['macro_f1']:.2f}" if r.get("macro_f1") is not None else "")
                + (f", {r['seconds']}s" if r.get("seconds") is not None else "")
                + (f", failures {r['failures']}" if r.get("failures") else ""))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.argv[1:2] == ["bench"]:
        ap = argparse.ArgumentParser(prog="decide.py bench", description="One paced decision benchmark pass.")
        ap.add_argument("--per", type=int, default=1, help="questions per model per set")
        ap.add_argument("--gap", type=float, default=30, help="seconds between calls")
        ap.add_argument("--sets", default="", help=f"comma-separated, of {', '.join(SETS)}")
        ap.add_argument("--models", default="", help="comma-separated aliases; all onboarded by default")
        ap.add_argument("--new-errors", type=int, default=0, dest="new_errors",
                        help="first, ask about up to this many new unrecognised error shapes")
        ap.add_argument("--dry-run", action="store_true", dest="dry")
        args = ap.parse_args(sys.argv[2:])
        if args.new_errors and not args.dry and classify_errors(args.new_errors, args.gap):
            time.sleep(args.gap)
        res = bench(args.per, args.gap, [s for s in args.sets.split(",") if s] or None,
                    [m for m in args.models.split(",") if m] or None, args.dry)
        if not args.dry:
            report(res)
        return
    if sys.argv[1:2] == ["results"]:
        report(results())
        return
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("alias", help="a key of config/decision-models.json")
    ap.add_argument("state", help="the text being judged")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--noul", metavar="QUESTION", help="a yes/no question")
    group.add_argument("--choice", metavar="A,B,C", help="comma-separated options")
    group.add_argument("--score", metavar="L0,L1,L2", help="comma-separated levels, lowest first")
    ap.add_argument("--instructions", default="Answer the question about this text.")
    args = ap.parse_args()
    if args.alias not in load():
        sys.exit(f"{args.alias} is not in {FILE.name}; onboard it first: scripts/onboard.py <served id>")
    if args.noul:
        q = {"type": "noul", "instructions": args.noul,
             "criteria": {"true": "Yes.", "false": "No."}}
    elif args.choice:
        q = {"type": "choice", "instructions": args.instructions,
             "criteria": {o.strip(): None for o in args.choice.split(",")}}
    else:
        q = {"type": "score", "instructions": args.instructions,
             "criteria": [o.strip() for o in args.score.split(",")]}
    try:
        print(json.dumps(ask(args.alias, args.state, {"q": q}, use="cli")["answers"]["q"], indent=1))
    except ValueError as exc:
        sys.exit(str(exc))
    except Exception as exc:                           # noqa: BLE001
        sys.exit(error_text(exc))


if __name__ == "__main__":
    main()
