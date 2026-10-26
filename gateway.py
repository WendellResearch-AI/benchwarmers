"""The bench method as an OpenAI-compatible endpoint, at http://127.0.0.1:8123/v1.

    python gateway.py

Ask for a COLLECTION by name — `bench/core`, `bench/reviewers` — and the prompt
goes in parallel to every member of it plus explorer seats `route.py` fills by
staleness, so the roster keeps getting evidence even when the same collection
is called every day:

    explorers = min(max_explorers, max_calls - members)     # defaults 2 and 5

Collections and those limits live in `config/collections.json` and
`config/gateway.json` (see `metrics/bench_collections.py`), edited from the
dashboard. There is no default: a request that names no collection, or one
that does not exist, is refused with every collection's description and tags,
so the caller can choose — `GET /v1/models` carries the same.

The reply is every answer, each labelled by seat and model with a call id,
including seats that failed or came back empty: who answered is evidence about
the roster, and a summary naming only the good answer hides it.

Unless the reply is `merged` (a collection's `reply`, or per request a
`:merged` suffix on the model or `"bench": {"reply": "merged"}`; `:all` the
other way): then, after the panel, one more model writes a single answer
saying where the seats agreed, where they disagreed and who said what, for a
client that can show only one. It has its own call id, so a verdict can judge
the merge, and the `bench` field still carries every seat. If no merger has
the context for the merge, or it fails, the reply is every answer with a
one-line note: the panel the caller waited for is never lost.

A collection's `shape` (review, design, …) decides which models may fill the
explorer seats. Left empty, a decision model labels the prompt when one that
takes choice questions is onboarded (not ad hoc); otherwise, or on any doubt,
the shape is `general`. A request body may add `"with": ["<alias>"]` to seat a
model in an explorer chair, as `route.py --with` does.

A retired model (`config/retired.json`) is never called: retiring takes it out
of every collection, and one a collection still names — as a member, `with`
or its merger — is skipped and named in `bench.skipped`, its seat going to an
explorer and the merge to a merger chosen by verdicts.

THE LOOP
--------
Every seat is recorded in `metrics/offloads.jsonl` as an unjudged offload the
moment it answers, so the explorer seats rotate even when nobody judges. The
caller then checks the answers against the real code — agreement between seats
is not a verdict, they fail together — and records one per call id:

    python metrics/offload.py verdict <call id> --useful --note "..." \\
        --real "<a finding it got right>" --false "<a claim it made that is untrue>"
    POST /bench/verdict {"call": "<call id>", "useful": true, "note": "...",
                         "real": ["..."], "false": ["..."]}

The real/false claims are what `suite/mine.py` turns into suite tasks.

The verdict replaces the unjudged entry, and route.py's evidence — which
explorers are due, which models earn a place in a collection — reads it.

IMAGES
------
A request whose messages carry `image_url` parts (http(s) or data: URLs) goes
to each seat with its messages as sent, through the built-in client whatever
BENCH_CLIENT says (`llm -m` takes a prompt). Only models the catalog says take
images sit (`cannot_see`): a member that cannot, or that the catalog knows
nothing about, is skipped and named in `bench.skipped` with why; explorers
are drawn from models that can, under the shape `vision` unless the
collection sets one; with no seat that can see, the request is refused (400)
rather than answered blind. Each image counts IMAGE_TOKENS in the size check.
Wherever the request is written down (the call log, offloads, learned
limits, error text, the dashboard) an image is a placeholder, never its
bytes. The merger gets the request as text with those placeholders, plus
the seats' answers: its job is to reconcile what the seats said, not to look
again, so any model can merge and a disagreement about the picture is
reported rather than settled.

OTHER FORMATS, AND STREAMING
----------------------------
The same panel answers Anthropic Messages (/v1/messages), OpenAI Responses
(/v1/responses) and legacy completions (/v1/completions), Ollama (/api/chat,
/api/generate, /api/tags, /api/version, /api/show) and Gemini
(/v1beta/models/<collection>:generateContent and :streamGenerateContent):
metrics/formats.py turns each request into OpenAI messages and the answer
back into the format's reply. A panel refuses a request carrying tools,
tool calls or tool results (400): its text is not a tool call.

ROUTE MODE
----------
For agent clients (Claude Code, Codex, opencode, Cline...): `:route` on the
model, `"bench": {"reply": "route"}`, or a collection's `reply`. No panel:
one member gets the request as sent, tools, tool_choice, response_format,
images and all, and its answer is relayed in the request's format, streamed
or not (route_plan, route_turn, Handler._route). The member is picked by its
record with some rotation, kept for the whole conversation (fingerprint),
and replaced by the next if it fails before its first piece. Each turn is
in the ledger and runs/gateway/routes.jsonl; each conversation is one
unjudged offload per model that answered it, a verdict optional. GATEWAY.md
"Route mode" has the decisions and the limits.

Asked to stream, the reply's 200 goes as soon as the panel is seated (a
refusal before that keeps its status), keep-alives go while the seats
think, each seat's section goes as that seat finishes (a merged reply when
the merge is done), and an error after the 200 goes into the stream in the
format's own shape (Handler._stream).

WHAT IT IS NOT
--------------
A proxy to a model of the caller's choosing: route mode picks a collection's
member by its record, and a tool that wants one model by name calls that
model's endpoint (or Open WebUI) directly. Seats are called with `llm -m`, exactly
as offloads always were, or the built-in client (BENCH_CLIENT), so endpoints,
headers and per-model options all come from the same registry. Full prompts
and answers go to `runs/gateway/`, gitignored, with the name of the gateway
key that asked. Bound to 127.0.0.1, like the dashboard (BENCH_BIND and
BENCH_GATEWAY_PORT change that; beyond this machine it asks for a gateway key,
metrics/guard.py, which also covers keys on this machine, scopes, caps and
CORS). The dashboard answers these same routes on its own port.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import queue
import random
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "metrics"))
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
PORT = int(os.environ.get("BENCH_GATEWAY_PORT") or 8123)
LOG_DIR = DATA / "runs" / "gateway"
# The suite's own timeout. Above the slowest real answer seen (383 s); at 900
# one stuck explorer held a whole request for fifteen minutes.
SEAT_TIMEOUT = 420
sys.path.insert(0, str(ROOT / "metrics"))
sys.path.insert(0, str(ROOT / "suite"))
import bench_collections  # noqa: E402
import client  # noqa: E402
import decide  # noqa: E402
import formats  # noqa: E402
import guard  # noqa: E402
import mcp_server  # noqa: E402  -- its tools, answered at /mcp too
import model_roles  # noqa: E402
import offload  # noqa: E402
import route  # noqa: E402

# What the shape picker is asked to choose between. Each option carries a
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
CONFIDENT = 0.6
LARGE = 8000          # characters; route.py keeps models that fail big prompts off big tasks
# Bytes. A million-token prompt is about 4 MB, and a photo sent as a data URL
# is 4/3 of its file. 20 MB is the tightest whole-request limit among the big
# providers (Gemini's for inline data), so what passes here they take too.
MAX_BODY = 20_000_000
TOO_BIG = (" Send images as http(s) URLs, or smaller ones: models scale a photo down to about"
           " 1,500-2,000 pixels on its long side anyway.")
IMAGE_TOKENS = 2_500   # per image, in the size check (estimate_tokens says why)
IMAGE_URL = re.compile(r"(https?://[^\s/?#]+|data:image/[\w.+-]+;base64,)", re.I)
DATA_URLS = re.compile(r"data:[\w/.+-]*;base64,[A-Za-z0-9+/=_\\-]*", re.I)
URLS = re.compile(r"https?://[^\s\"'<>\\]+", re.I)
BASE64 = re.compile(r"[A-Za-z0-9+/]{100,}={0,2}")
HEADER_NAMES = re.compile(r"[A-Za-z0-9-]{1,64}(\s*,\s*[A-Za-z0-9-]{1,64}){0,31}")
SEAT_KEYS = ("call", "seat", "alias", "status", "seconds", "answer", "error")
QUERY_KEY = re.compile(r"([?&](?:key|api_key|access_token)=)[^&\s'\"]*", re.I)
# A streaming reply: how long a request may take to be seated before its 200
# goes anyway (a refusal before then keeps its status), and how often a
# keep-alive goes while the seats think. Idle timeouts in clients and proxies
# start around 30 seconds.
STREAM_GRACE = 1.0
KEEPALIVE = 10.0
# A merge that reads as consensus hides the dissent the bench runs on, so the
# merger is told to keep who said what.
MERGE_SYSTEM = ("Several models answered the same request. Write one answer to it from theirs: say where"
                " they agreed, where they disagreed, and who said what, naming each model as labelled."
                " Keep every dissent, and do not present a claim as settled because several made it.")


def flatten(messages: list[dict]) -> tuple[str, str]:
    """(system, prompt) for `llm`. A panel question is one prompt; a longer
    conversation is kept as labelled turns rather than silently truncated.
    An image is its placeholder: this text is what gets written down."""
    def text(m):
        c = m.get("content") or ""
        return c if isinstance(c, str) else "\n".join(
            placeholder(_url(p)) if p.get("type") == "image_url" else str(p.get("text") or "")
            for p in c if isinstance(p, dict))
    # Newer OpenAI SDKs send "developer" where older ones send "system".
    lead = ("system", "developer")
    system = "\n\n".join(text(m) for m in messages if m.get("role") in lead)
    turns = [m for m in messages if m.get("role") not in lead]
    if len(turns) == 1:
        return system, text(turns[0])
    return system, "\n\n".join(f"{m.get('role', 'user')}: {text(m)}" for m in turns)


def _url(part: dict) -> str:
    """An image part's URL: {"image_url": {"url": ...}} as OpenAI sends it, or a bare string."""
    u = part.get("image_url")
    u = u.get("url") if isinstance(u, dict) else u
    return u if isinstance(u, str) else ""


def images(messages: list[dict]) -> list[str]:
    """Every image part's URL, in order. ValueError for one no seat could be
    sent: the gateway never fetches an image, the upstream does, so only
    http(s) URLs and inline base64 images pass; and an image in another API's
    shape is refused rather than dropped, which would have a seat answer blind."""
    urls = []
    for m in messages:
        parts = m.get("content")
        for p in parts if isinstance(parts, list) else ():
            kind = p.get("type") if isinstance(p, dict) else None
            if kind in ("image", "input_image"):
                raise ValueError(f'a "{kind}" part is another API\'s shape; send each image as'
                                 ' {"type": "image_url", "image_url": {"url": "<http(s) or data: URL>"}}')
            if kind == "image_url":
                url = _url(p)
                if not IMAGE_URL.match(url):
                    raise ValueError("an image_url must be an http(s) URL or a data:image/...;base64, URL,"
                                     f" not {placeholder(url) if url else 'empty'}")
                urls.append(url)
    return urls


def placeholder(url: str) -> str:
    """How an image is written down (the call log, offloads, the dashboard,
    error text, the merger's copy of the request), never its bytes: a data
    URL by its size, an http URL without its query (a signed URL's signature
    is a credential) or a user:password."""
    if url[:5].lower() == "data:":
        return f"[image: data URL, {(len(url) * 3 // 4 + 999) // 1000:,} KB]"
    u = urllib.parse.urlsplit(url)
    return f"[image: {u.scheme}://{u.netloc.rpartition('@')[2]}{u.path[:200]}{'?...' if u.query else ''}]"


def redact(text: str) -> str:
    """`text` with each inline image as its placeholder, every http(s) URL
    without its query or user:password, and any other long base64 run as its
    size: an upstream may echo the request in an error, in whatever form, and
    errors are logged, shown and learned from."""
    def bare(m):
        try:
            u = urllib.parse.urlsplit(m.group())
        except ValueError:                             # an unparseable netloc, such as "[::1"
            return "[url]"
        return f"{u.scheme}://{u.netloc.rpartition('@')[2]}{u.path}{'?...' if u.query else ''}"
    text = URLS.sub(bare, DATA_URLS.sub(lambda m: placeholder(m.group()), text))
    return BASE64.sub(lambda m: f"[base64, {(len(m.group()) * 3 // 4 + 999) // 1000:,} KB]", text)


def cannot_see(alias: str) -> str:
    """Why an image cannot be put to `alias`, or "" when it can. The catalog
    decides (metrics/catalog.py: the model's input types, your Vision / OCR
    override in its model popup, a capability seen working). A model the
    catalog knows nothing about does not sit either: one that cannot see
    answers anyway, describing a photo it never got, and the override is one
    click."""
    import catalog
    import endpoints
    if endpoints.entry_of(alias).get("api") == "responses":
        return "reached through the Responses API, which the gateway sends no images to yet"
    rec = catalog.info(alias)
    if rec.get("vision"):
        return ""
    if rec.get("vision") is None:
        return "the catalog does not say whether it takes images; tick Vision / OCR in its model popup if it does"
    return f"does not take images (says {rec['sources'].get('vision', 'the catalog')})"


def pick_shape(prompt: str) -> tuple[str, str]:
    """(shape, how it was chosen). Falls back to general on any doubt."""
    try:
        held = set(json.loads((DATA / "config" / "model-roles.json").read_text(encoding="utf-8"))
                   .get("ad_hoc", []))
    except (OSError, ValueError, AttributeError):
        return "general", "config/model-roles.json unreadable"
    picker = next((a for a, d in decide.load().items()
                   if "choice" in d["question_types"] and a not in held), None)
    if not picker:
        return "general", "no decision model onboarded"
    question = {"shape": {"type": "choice",
                          "instructions": "What kind of task does this prompt ask for?",
                          "criteria": SHAPES}}
    try:
        answer = decide.ask(picker, prompt[:20000], question)["answers"]["shape"]
    except Exception as exc:                           # noqa: BLE001
        return "general", f"{picker} failed ({decide.error_text(exc)[:80]})"
    conf = answer.get("confidence", 0)
    if answer.get("choice") not in SHAPES or conf < CONFIDENT:
        return "general", f"{picker} unsure ({answer.get('choice')} at {conf:.2f})"
    return answer["choice"], f"{picker}, {conf:.2f}"


def retired() -> dict[str, dict]:
    """config/retired.json: alias -> its record (when, why). Retiring a model
    takes it out of every collection (serve.remove_model); this is for a
    collection that still names one -- a hand edit, or one saved before that
    -- so the seat is skipped with a reason instead of failing on the call."""
    try:
        data = json.loads((DATA / "config" / "retired.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def retired_why(entry: dict) -> str:
    when = str(entry.get("at", ""))[:10] if isinstance(entry, dict) else ""
    return (f"retired from the bench{' on ' + when if when else ''}; take it out of the collection"
            " (Restore in its model popup brings it back)")


def retiring() -> set[str]:
    """Aliases not to spend an explorer seat on: those on a retiring alert
    (failing their probes; 2026-10-16 both explorers of a bench/core call were
    and both failed), and those cooling down after a rate limit, until the
    wait the provider stated, or 30 minutes (metrics/learn.py)."""
    out: set[str] = set()
    try:
        import alerts
        out |= {a.get("alias", "") for a in alerts.active().values() if a.get("kind") == "retiring"}
    except Exception:                                  # noqa: BLE001
        pass
    try:
        import learn
        out |= set(learn.cooldowns())
    except Exception:                                  # noqa: BLE001
        pass
    return out


def ask_seat(alias: str, system: str, prompt: str, messages: list[dict] | None = None) -> dict:
    """One seat's answer. `messages`: the request as sent, when it carries
    images; it goes through the built-in client whatever BENCH_CLIENT says,
    since `llm -m` takes a prompt, not inline images."""
    cmd = ["llm", "-m", alias] + (["-s", system] if system else [])
    started = time.monotonic()
    if messages is not None or client.backend(alias) == "builtin":
        # No subprocess, and no shared llm log for parallel seats to lock.
        res = client.chat(alias, prompt, system, SEAT_TIMEOUT, messages=messages)
        answer = res["text"]
        error = res["error"] or (f"no answer in {SEAT_TIMEOUT}s" if res["timed_out"] else "")
    else:
        answer, error = _ask_llm(cmd, prompt)
    # A quota or credit notice delivered as the reply's text, with a success
    # exit, is not an answer (suite/run.py PROVIDER_NOTICES).
    from run import provider_notice
    if not error and (notice := provider_notice(answer)):
        error, answer = f"provider notice in a success reply ({notice}): {answer[:200]}", ""
    if messages is not None:
        # Before the error is cut, logged or learned from. ponytail: the answer
        # is not redacted; a seat gets an image as pixels, not as its URL or bytes.
        error = redact(error)
    if error:
        # A stated context window, rate limit or wait is kept (metrics/learn.py);
        # a rate limit also cools the model, so explorer seats skip it.
        try:
            import learn
            learn.record(alias, error, "gateway")
        except Exception:                              # noqa: BLE001
            pass
    status = "error" if error else ("empty" if not answer else "ok")
    return {"status": status, "answer": answer, "error": error[-500:],
            "seconds": round(time.monotonic() - started, 1)}


def _ask_llm(cmd: list[str], prompt: str) -> tuple[str, str]:
    """(answer, error) from the `llm` CLI."""
    try:
        done = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=SEAT_TIMEOUT,
                              env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        answer = done.stdout.strip()
        # A non-zero exit is an error even when stderr is silent, unless it is
        # only llm failing to log an answer it printed (client.py).
        failed = done.returncode and not client.llm_log_failed_only(done.stdout, done.stderr)
        error = (done.stderr.strip() or f"llm exited {done.returncode}") if failed else ""
    except subprocess.TimeoutExpired:
        answer, error = "", f"no answer in {SEAT_TIMEOUT}s"
    except Exception as exc:                           # noqa: BLE001
        # One seat failing to start must not lose the other seats' records.
        answer, error = "", f"{type(exc).__name__}: {exc}"
    return answer, error


class TooLarge(ValueError):
    """The prompt does not fit a member's context window (HTTP 413)."""


def estimate_tokens(text: str) -> int:
    """Characters / 4 plus 10%: tokenizers differ by family, so this errs high.
    The windows it is compared with are corrected by learned 'maximum context
    length' errors (metrics/learn.py). An image adds IMAGE_TOKENS whatever its
    size, which errs high too: OpenAI's, Anthropic's and Gemini's models spend
    up to about 1,600 on one at their usual detail. Qwen and Llama vision
    models can spend more on a very large image; a learned error corrects that."""
    return formats.estimate(text)


def prompt_tokens(messages: list[dict]) -> int:
    """The estimate for a whole request, images counted: what a reply's usage
    and a count_tokens route say."""
    system, prompt = flatten(messages)
    try:
        pictures = len(images(messages))
    except ValueError:                                 # refused by run_collection, which says why
        pictures = 0
    return estimate_tokens(f"{system}\n\n{prompt}".strip()) + IMAGE_TOKENS * pictures


def too_small(needed: int, aliases) -> dict[str, int]:
    """Aliases whose KNOWN context window is under `needed`, with the window."""
    import catalog
    out = {}
    for a in aliases:
        cw = catalog.info(a).get("context_window")
        if cw and cw < needed:
            out[a] = cw
    return out


def record_seat(task: str, call: str, label: str, alias: str, r: dict) -> dict:
    """Log one answer as an unjudged offload, the moment it is in, so explorer
    rotation counts it even if nobody judges; returns the seat as logged. The
    note's "<seat>; <status> in" is what /bench/pending reads the status from."""
    offload.add(alias, task, None, f"{label}; {r['status']} in {r['seconds']}s"
                + (f": {r['error'][:120]}" if r["error"] else ""), call)
    return {"call": call, "seat": label, "alias": alias, **r}


def record(collection: str, task: str = "") -> dict[str, dict]:
    """Each model's verdicts on one collection's seats, for a client that weighs
    its seats by how they have done (thicken breaks ties with it): useful, not
    useful and judged, counted, and the last one's date. `task` keeps only the
    verdicts whose note holds it ("thicken slab faces"). Counts only: never a
    prompt, an answer or a note."""
    out: dict[str, dict] = {}
    for e in offload.entries():
        # The collection ends each seat's task as run_collection writes it: "(bench/<name>, <shape>)".
        if e.get("useful") is None or f"({collection}," not in e.get("task", ""):
            continue
        if task and task not in (e.get("note") or ""):
            continue
        r = out.setdefault(e["model"], {"useful": 0, "notUseful": 0, "judged": 0, "lastAt": ""})
        r["useful" if e["useful"] else "notUseful"] += 1
        r["judged"] += 1
        r["lastAt"] = max(r["lastAt"], e.get("date", ""))
    return out


def merger_candidates(collection: dict, members: list[str], shape: str, large: bool):
    """Who may merge, best first. The collection's `merger` alone when set;
    else the members with verdicts on this shape, most useful first, then the
    router's primary, then the other members (a fresh install has neither
    verdicts nor suite scores). A generator, so the router is asked only if
    no member with a record fits."""
    if collection.get("merger"):
        yield collection["merger"]
        return
    record: dict[str, list[int]] = {}
    for e in offload.entries():
        # The shape ends each seat's task as run_collection writes it: "(bench/<name>, <shape>)".
        if e["model"] in members and e.get("useful") is not None and e.get("task", "").endswith(f", {shape})"):
            r = record.setdefault(e["model"], [0, 0])
            r[0], r[1] = r[0] + bool(e["useful"]), r[1] + 1
    # Useful verdicts counted, not a ratio, as route.py counts findings: one
    # lucky verdict must not outrank a long good record.
    yield from sorted(record, key=lambda a: (-record[a][0], -record[a][0] / record[a][1]))
    try:
        primary = route.panel(shape, large=large)["primary"]
    except Exception:                                  # noqa: BLE001 -- the members can still merge
        primary = None
    if primary:
        yield primary["model"]
    yield from members


def merge(collection: dict, members: list[str], shape: str, whole: str, seats: list[dict],
          task: str, panel_id: str) -> tuple[dict | None, str]:
    """The merger's seat, and why the reply falls back to every answer ("" if
    it does not). The merger reads the request and every answer, so it gets
    the members' size check; a failed merger is still logged like a seat."""
    answered = [s for s in seats if s["status"] == "ok"]
    if not answered:
        return None, "no seat answered"
    text = f"THE REQUEST\n\n{whole}\n\nTHE ANSWERS\n\n" + "\n\n".join(
        f"## {s['seat']} — {s['alias']}\n\n{s['answer']}" for s in answered)
    needed = estimate_tokens(f"{MERGE_SYSTEM}\n\n{text}")
    alias = next((a for a in merger_candidates(collection, members, shape, len(text) > LARGE)
                  if not too_small(needed, [a])), None)
    if alias is None:
        return None, f"no merger has the context window for the merge (about {needed:,} tokens)"
    seat = record_seat(task, f"{panel_id}.merger", "merger", alias, ask_seat(alias, MERGE_SYSTEM, text))
    if seat["status"] != "ok":
        return seat, f"the merger {alias} came back {seat['status']}" + (f": {seat['error'][:120]}" if seat["error"] else "")
    return seat, ""


def section(seat: dict) -> str:
    """One seat's part of an every-answer reply: its heading, call id, status and answer."""
    body = seat["answer"] or (f"_error: {seat['error'][:300]}_" if seat["error"] else
                              "_empty reply — usually a token budget, not incapacity_")
    return f"## {seat['seat']} — {seat['alias']}\n`{seat['call']}` · {seat['status']} · {seat['seconds']}s\n\n{body}\n"


def run_collection(name: str, messages: list[dict], nominated: list[str] | None = None,
                   reply: str = "", who: str = "local", on_part=None) -> tuple[str, dict]:
    """The panel's reply as markdown, and the same seats as data for the
    response's `bench` field. `reply`: the request's mode, "" for the
    collection's own. `who`: the gateway key that asked, for the call log.

    `on_part`, for a streaming caller: called with each piece of an
    every-answer reply as it is ready, the heading once the panel is seated,
    each seat's section as that seat finishes, then the closing lines. The
    pieces add up to the content, with the seats in the order they finished
    rather than seat order. Not called for a merged reply: the merge is the
    reply, and it comes at the end (or every answer, if it fails)."""
    pictures = images(messages)
    system, prompt = flatten(messages)
    if not prompt.strip():
        raise ValueError("no user message to put to the collection")
    collection = bench_collections.load()[name]
    s = bench_collections.settings()
    raw = collection.get("members") or []
    # The editor dedupes and caps; a hand-edited file may not have.
    members = list(dict.fromkeys(m for m in raw if isinstance(m, str) and m))
    # A RETIRED MODEL IS NOT CALLED: it is no longer registered, so the call
    # could only fail ("Unknown model"). Skipped, and named, before the cap so
    # it frees its seat for the next member or an explorer. Retiring takes it
    # out of the collection, so this is a collection that still names one.
    gone = retired()
    skipped = [{"seat": "member", "alias": a, "why": retired_why(gone[a])} for a in members if a in gone]
    members = [a for a in members if a not in gone][:s["max_calls"]]
    # A member named in `with` is already seated; it must not use up the slot.
    nominated = [a for a in dict.fromkeys(nominated or []) if a not in members]
    skipped += [{"seat": "with", "alias": a, "why": retired_why(gone[a])} for a in nominated
                if a in gone and a not in {k["alias"] for k in skipped}]
    nominated = [a for a in nominated if a not in gone]
    merged = (reply or collection.get("reply") or "all") == "merged"
    if collection.get("merger") in gone:
        if merged:
            skipped.append({"seat": "merger", "alias": collection["merger"],
                            "why": retired_why(gone[collection["merger"]]) + "; the merger is chosen by verdicts"})
        collection = {**collection, "merger": ""}
    n_explorers = bench_collections.explorers_for(len(members), s)
    # AN IMAGE GOES ONLY TO A MODEL THAT CAN SEE IT: one that cannot would
    # answer anyway, about a picture it never got. Skipped, and named.
    skipped += [{"seat": seat, "alias": a, "why": why} for seat, group in (("member", members), ("with", nominated))
                for a in group if pictures and (why := cannot_see(a))]
    # A VISION SPECIALIST SITS ONLY FOR PICTURES: its role says it reads
    # images, not that it answers text, so on a text request its seat goes to
    # the next member or an explorer. Named in `with`, it sits anyway.
    if not pictures:
        roles = model_roles.load()["models"]
        skipped += [{"seat": "member", "alias": a, "why": "a vision specialist: sits only when the request"
                     " carries images"} for a in members if roles.get(a) == "vision"]
    unseen = {k["alias"] for k in skipped}
    sitting = [a for a in members if a not in unseen]
    nominated = [a for a in nominated if a not in unseen]
    # The system text goes to every seat too, so it counts for shape and size.
    whole = f"{system}\n\n{prompt}".strip()
    # REFUSE WHAT A MEMBER CANNOT READ, so the caller can trim the prompt or
    # pick another collection instead of collecting a seat's context error.
    needed = estimate_tokens(whole) + IMAGE_TOKENS * len(pictures)
    small = too_small(needed, sitting)
    if small:
        windows = ", ".join(f"{a} {cw:,}" for a, cw in sorted(small.items(), key=lambda kv: kv[1]))
        counted = f" (each image counted as {IMAGE_TOKENS:,})" if pictures else ""
        raise TooLarge(f"prompt is about {needed:,} tokens{counted}; too large for {windows} (context"
                       f" windows) in bench/{name}. Trim it under {min(small.values()):,} tokens,"
                       " or choose a collection whose members have larger windows"
                       " (GET /v1/models lists each collection's context_window).")
    if pictures and not collection.get("shape"):
        shape, how = "vision", "the request carries images"
    elif not n_explorers:
        shape, how = collection.get("shape") or "general", "no explorer seats to fill"
    elif collection.get("shape"):
        shape, how = collection["shape"], "set on the collection"
    else:
        shape, how = pick_shape(whole)
    seats = [(f"member{i}", "member", alias) for i, alias in enumerate(sitting, 1)]
    if n_explorers:
        # `with` in the request body is route.py's --with: an explorer seat.
        from run import _registered_names
        avoid = retiring() | set(too_small(needed, _registered_names()))   # explorers must fit too
        if pictures:
            avoid |= {a for a in _registered_names() if cannot_see(a)}    # and see
        p = route.panel(shape, large=len(whole) > LARGE, nominated=nominated or None,
                        members=sitting, explorers=n_explorers, avoid=avoid)
        seats += [(key, label, p[key]["model"]) for key, label in
                  (("explorer", "explorer"), ("explorer2", "explorer 2")) if p.get(key)]
    if not seats and pictures:
        raise ValueError(f"no seat in bench/{name} can see images, so none is asked rather than answer"
                         " blind: " + ("; ".join(f"{k['alias']} {k['why']}" for k in skipped) or "no members")
                         + ", and no model that takes images was free for an explorer seat. Add one to the"
                         " collection, or call one whose together.capabilities on GET /v1/models lists vision.")
    if not seats:
        raise LookupError(f"collection {name!r} has no members and no explorer could be seated"
                          + "".join(f"; {k['alias']} not asked: {k['why']}" for k in skipped))
    panel_id = f"p{dt.datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
    model = bench_collections.PREFIX + name
    task = " ".join(prompt.split())[:90] + f" ({model}, {shape})"
    out = [f"Bench {model} {panel_id} · {len(sitting)} members + {len(seats) - len(sitting)}"
           f" explorers · explorer shape {shape} ({how})\n"]
    if skipped:
        out.append("Not asked: " + "; ".join(f"{k['alias']} ({k['why']})" for k in skipped) + "\n")
    emit = None if merged else on_part
    if emit:
        emit("\n".join(out))
    done: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=len(seats)) as pool:
        asked = {pool.submit(ask_seat, alias, system, prompt, messages if pictures else None): i
                 for i, (_key, _label, alias) in enumerate(seats)}
        # Each seat is logged the moment it answers, so explorer rotation
        # counts it even if a slower seat (or the caller) never finishes.
        for future in as_completed(asked):
            i = asked[future]
            key, label, alias = seats[i]
            done[i] = record_seat(task, f"{panel_id}.{key}", label, alias, future.result())
            if emit:
                emit("\n" + section(done[i]))

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    record = {"panel": panel_id, "at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
              "model": model, "shape": shape, "shape_by": how, "key": who, "system": system,
              "prompt": prompt, "seats": [done[i] for i in range(len(seats))],
              **({"skipped": skipped} if skipped else {})}
    out += [section(s) for s in record["seats"]]
    footer = ("---\nRecord a verdict per seat once checked against the code:\n"
              "`POST /bench/verdict` on this same server (see `bench.verdict`), or"
              " `python metrics/offload.py verdict <call id> --useful|--not-useful --note \"...\"`")
    out.append(footer)
    if emit:
        emit("\n" + footer)
    content = "\n".join(out)
    # Beyond the OpenAI fields, so a caller reads seats without parsing the
    # markdown. Clients that do not know it ignore it.
    bench = {"panel": panel_id, "collection": model, "shape": shape, "shape_by": how,
             "seats": [{k: s[k] for k in SEAT_KEYS} for s in record["seats"]],
             # A path, not a URL: the same server answered, whichever port or
             # proxy the caller came through (the dashboard serves these routes too).
             "verdict": 'POST /bench/verdict {"call", "useful": true|false, "note", "real": [...], "false": [...]}',
             **({"skipped": skipped} if skipped else {})}
    if merged:
        try:
            merger, note = merge(collection, members, shape, whole, record["seats"], task, panel_id)
        except Exception as exc:                       # noqa: BLE001 -- never lose the panel to the merge
            merger, note = None, f"the merge failed ({type(exc).__name__}: {exc})"
        if merger:
            bench["merger"] = {k: merger[k] for k in SEAT_KEYS}
            record["seats"].append(merger)             # the dashboard shows it, verdict and all
        if note:
            bench.update(reply="all", note=note)
            content = f"_Merged reply unavailable: {note}. Every answer follows._\n\n{content}"
        else:
            who = ", ".join(f"{s['alias']} {s['status']}" for s in bench["seats"])
            bench["reply"] = "merged"
            content = (f"{merger['answer']}\n\n---\nMerged by {merger['alias']} (`{merger['call']}`) from:"
                       f" {who}. Every seat's answer and call id is in the response's `bench` field.")
    with (LOG_DIR / "calls.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return content, bench


def models() -> list[str]:
    return [bench_collections.PREFIX + n for n in bench_collections.load()]


def listed(who: str) -> list[tuple[str, dict]]:
    """(model name, what it is for) for every collection key `who` may call,
    each three times: as set, and in each other reply mode, so a client that
    offers only listed models (an agent's model picker above all, for
    `:route`) can still pick any. What GET /v1/models, /api/tags and
    /v1beta/models list, each in its own shape."""
    s, rows = bench_collections.settings(), []
    for n, c in bench_collections.load().items():
        if not guard.may_call(who, n):                 # a key limited to some collections sees those
            continue
        d = bench_collections.described(n, c, s)
        rows.append((bench_collections.PREFIX + n, d))
        rows += [(f"{bench_collections.PREFIX}{n}:{other}", {**d, "reply": other})
                 for other in bench_collections.REPLIES if other != d["reply"]]
    return rows


def bench_headers(bench: dict) -> dict:
    """The panel's call ids as headers, for a format with no room for the
    bench field in its body (Gemini): X-Bench-Panel, and X-Bench-Seats as
    JSON, without the answers (they are in the reply)."""
    seats = bench.get("seats", []) + ([bench["merger"]] if bench.get("merger") else [])
    return {"X-Bench-Panel": str(bench.get("panel", "")),
            "X-Bench-Seats": json.dumps([{k: s.get(k) for k in ("call", "seat", "alias", "status")} for s in seats])}


def refusal(exc: Exception) -> tuple[int, str]:
    """The status and message for a panel that could not run."""
    if isinstance(exc, TooLarge):
        return 413, str(exc)
    if isinstance(exc, ValueError):
        return 400, str(exc)
    if isinstance(exc, LookupError):
        return 503, str(exc)
    return 500, f"the panel failed ({type(exc).__name__}: {exc})"


# ---- route mode: one member, tools and all (GATEWAY.md "Route mode") --------

# A whole agent turn; a file written as one tool call can stream for minutes.
ROUTE_TIMEOUT = 600
# How long a member may take to its first piece (text, reasoning or a tool
# call) before the next is tried. Reasoning streams count, so only a model
# that sends nothing at all waits this long.
FIRST_TOKEN = 90
ROUTE_TRIES = 3                # members tried for one turn before it fails
# A streamed turn's 200 waits this long for the first piece, so a turn that
# fails everywhere quickly keeps its status (a client retries a 429 or 503 it
# can see, not an error inside a 200); after it, keep-alives.
ROUTE_GRACE = 15.0
STICKY_TTL = 3600              # seconds idle before a conversation is picked afresh
ROTATE = 0.25                  # share of new conversations given to the member with the oldest record
_STICKY: dict[str, dict] = {}  # ponytail: in memory, so a restart re-picks; a file if that ever matters
_STICKY_LOCK = threading.Lock()


def cannot_call_tools(alias: str) -> str:
    """Why a request with tools cannot be routed to `alias`, or "" when it
    can. As cannot_see, the catalog decides (the model's listed parameters,
    your Tool calling override in its model popup, a probe that saw a call),
    and a model it knows nothing about is not routed to: one that cannot call
    tools answers in prose, and the agent waits for a call that never comes."""
    import catalog
    rec = catalog.info(alias)
    if rec.get("tools"):
        return ""
    if rec.get("tools") is None:
        return "the catalog does not say whether it calls tools; tick Tool calling in its model popup if it does"
    return f"does not call tools (says {rec['sources'].get('tools', 'the catalog')})"


def route_tokens(ask: dict) -> int:
    """The size check's estimate for a routed request: the messages as a
    panel counts them, plus the tool calls in them and the tool definitions,
    which an agent's requests are full of."""
    extra = [[m["tool_calls"] for m in ask["messages"] if m.get("tool_calls")], ask["upstream"].get("tools") or []]
    return prompt_tokens(ask["messages"]) + (estimate_tokens(json.dumps(extra)) if any(extra) else 0)


def fingerprint(name: str, who: str, messages: list[dict]) -> str:
    """What keeps an agent's conversation on one model from turn to turn:
    the collection, the gateway key, the system text and the first user
    message, which every turn sends again unchanged. A side task with its
    own system prompt (titling the chat, a subagent) is its own conversation."""
    system = "\n".join(formats.text_of(m.get("content")) for m in messages if m.get("role") in ("system", "developer"))
    first = next((formats.text_of(m.get("content")) for m in messages if m.get("role") == "user"), "")
    return hashlib.sha256("\0".join((name, who, system, first)).encode()).hexdigest()[:24]


def route_order(aliases: list[str]) -> list[str]:
    """Best record first: useful verdicts counted, as the merger is chosen
    (one lucky verdict must not outrank a long good record), then verified
    suite findings, then a connection already paid for. Members cooling down
    after a rate limit or failing their probes go last. Now and then
    (ROTATE) the member whose record is oldest goes first instead, so one
    that stops being picked still gets turns, and with them a record."""
    try:
        used = offload.summary()
    except Exception:                                  # noqa: BLE001
        used = {}
    try:
        found = route.suite_recall()
    except Exception:                                  # noqa: BLE001
        found = {}
    try:
        paid = route.preferred_connections()
        tags = {a: route.full_tag_of(a) for a in aliases}
    except Exception:                                  # noqa: BLE001
        paid, tags = set(), {}
    ranked = sorted(aliases, reverse=True, key=lambda a: (used.get(a, {}).get("useful", 0),
                                                          found.get(a, (0, 0, 0))[0], tags.get(a) in paid))
    cooling = retiring()
    fresh = [a for a in ranked if a not in cooling]
    if len(fresh) > 1 and random.random() < ROTATE:
        stalest = min(fresh, key=lambda a: used.get(a, {}).get("last_date") or "")
        fresh.remove(stalest)
        fresh.insert(0, stalest)
    return fresh + [a for a in ranked if a in cooling]


def route_plan(name: str, ask: dict, who: str) -> dict:
    """The members that may take this turn, in the order to try them, and
    the conversation it belongs to. A conversation under way keeps its model
    first; a new one is ranked by route_order. Excluded, and named with why,
    as a panel excludes them: retired and ad hoc members, vision specialists
    on a request without images, members reached
    through another API than chat completions, and those that cannot call
    tools (when the request has tools), see (when it has images) or read it.
    Raises as run_collection does: TooLarge, ValueError, LookupError."""
    import endpoints
    from run import ad_hoc_models
    members = list(dict.fromkeys(m for m in bench_collections.load()[name].get("members") or []
                                 if isinstance(m, str) and m))
    if not members:
        raise LookupError(f"collection {name!r} has no members to route to")
    pictures, tools = images(ask["messages"]), bool(ask["upstream"].get("tools"))
    needed = route_tokens(ask) + IMAGE_TOKENS * len(pictures)
    gone, held, small = retired(), ad_hoc_models(), too_small(needed, members)
    roles = model_roles.load()["models"]
    skipped, fit = [], []
    for a in members:
        api = endpoints.entry_of(a).get("api") or "chat"
        why = (retired_why(gone[a]) if a in gone else
               "ad hoc (expensive or capped), so never routed to automatically" if a in held else
               "a vision specialist: routed to only when the request carries images"
               if roles.get(a) == "vision" and not pictures else
               f"reached through the {api} API; route mode passes requests on to chat completions only" if api != "chat"
               else (tools and cannot_call_tools(a)) or (pictures and cannot_see(a)) or
               (f"its context window ({small[a]:,} tokens) is under this request (about {needed:,})" if a in small else ""))
        (skipped.append({"alias": a, "why": why}) if why else fit.append(a))
    if not fit:
        detail = "; ".join(f"{k['alias']} {k['why']}" for k in skipped)
        if all(k["alias"] in small for k in skipped):
            raise TooLarge(f"no member of bench/{name} can read this request: {detail}. Trim it, or choose a"
                           " collection whose members have larger windows (GET /v1/models lists each one's).")
        raise ValueError(f"no member of bench/{name} can take this request: {detail}")
    key, now = fingerprint(name, who, ask["messages"]), time.monotonic()
    with _STICKY_LOCK:
        for k in [k for k, c in _STICKY.items() if now - c["at"] > STICKY_TTL]:
            del _STICKY[k]
        conv = _STICKY.get(key)
    if conv is None or conv["alias"] not in fit:      # new, or its model cannot take this turn (an image, say)
        ranked = route_order(fit)
        first = next((formats.text_of(m.get("content")) for m in ask["messages"] if m.get("role") == "user"), "")
        fresh = {"conv": f"r{dt.datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}", "turns": 0, "models": [],
                 "task": " ".join(first.split())[:90], "alias": ranked[0], "order": ranked, "at": now}
        with _STICKY_LOCK:
            # Changed in place, or the first of two simultaneous new turns
            # kept: every turn of one conversation shares one record.
            if conv is None:
                conv = _STICKY.setdefault(key, fresh)
            else:
                conv.update(alias=ranked[0], order=ranked, at=now)
    order = [conv["alias"]] + [a for a in conv["order"] if a != conv["alias"] and a in fit]
    return {"conv": conv, "order": order + [a for a in fit if a not in order], "skipped": skipped,
            "images": bool(pictures)}


def _fit(upstream: dict, alias: str) -> dict:
    """The request's fields for `alias`: an output budget over the model's
    known maximum is cut to it rather than refused (Claude Code asks every
    model for 32,000)."""
    import catalog
    cap = catalog.info(alias).get("max_output")
    return {k: min(v, cap) if k in ("max_tokens", "max_completion_tokens") and isinstance(v, int)
            and isinstance(cap, int) and cap > 0 else v for k, v in upstream.items()}


def route_turn(name: str, plan: dict, ask: dict, who: str, emit) -> None:
    """One agent turn: the plan's members in order until one answers.

    The answering member's chunks go to emit("chunk", chunk), held back
    until the first piece of its answer (text or a tool call; reasoning
    before it is held with it), so a member that fails before then (a 429, a
    5xx, a pacing refusal, nothing in FIRST_TOKEN seconds, an empty reply
    even after reasoning) is replaced by the next without the caller seeing
    a thing; keep-alives cover the wait. emit("live",
    alias) says who it is. Then emit("done", bench), or emit("error",
    (status, message)) when every member tried failed, or the one under way
    failed part way, which no other member can take up.

    Every call reaches the usage ledger (purpose "route"). Every turn is a
    line in runs/gateway/routes.jsonl, without the prompt (an agent resends
    its whole conversation each turn). A conversation is one unjudged
    offload per model that answered it, not one per turn (GATEWAY.md says why)."""
    conv, order, tried = plan["conv"], plan["order"][:ROUTE_TRIES], []
    for n, alias in enumerate(order):
        held, live = [], [False]
        last = n == len(order) - 1

        def on_chunk(chunk, held=held, live=live, turn=formats.Turn(), alias=alias):
            _, anything = turn.feed(chunk)
            if live[0]:
                return emit("chunk", chunk)
            held.append(chunk)
            if anything:
                live[0] = True
                emit("live", alias)
                for c in held:
                    emit("chunk", c)
        res = client.chat(alias, "", timeout=ROUTE_TIMEOUT, messages=ask["messages"], body=_fit(ask["upstream"], alias),
                          on_chunk=on_chunk, first_token=FIRST_TOKEN, retries=None if last else 0, purpose="route")
        error = res["error"] or ("" if not res["timed_out"] else "timed out part way" if live[0]
                                 else f"nothing in {FIRST_TOKEN}s")
        if plan["images"]:
            error = redact(error)                      # an upstream may echo an image back
        if error:
            try:
                import learn
                learn.record(alias, error, "gateway")  # a rate limit cools the model for the next pick
            except Exception:                          # noqa: BLE001
                pass
        if not error and not live[0] and not last:
            error = "an empty reply"                   # nothing was sent: another member may do better
        if error and not live[0]:
            tried.append({"alias": alias, "error": error[-300:]})
            continue
        if not live[0]:                                # the last member's empty reply is its answer
            emit("live", alias)
            for c in held:
                emit("chunk", c)
        with _STICKY_LOCK:
            fresh = alias not in conv["models"]
            if fresh:
                conv["models"].append(alias)
            conv.update(alias=alias, at=time.monotonic(), turns=conv["turns"] + 1)
            call, turn_no = f"{conv['conv']}.{conv['models'].index(alias) + 1}", conv["turns"]
        status = "error" if error else "ok" if res["text"] or res["tool_calls"] else "empty"
        model = bench_collections.PREFIX + name
        if fresh:
            offload.add(alias, f"{conv['task']} ({model}:route)", None,
                        f"route conversation; first turn {status} in {res['seconds']}s", call)
        _log_route(conv, turn_no, call, model, who, alias, status, res, tried, error)
        if error:
            return emit("error", (res["status"] if res["status"] >= 400 else 502,
                                  f"{alias} failed part way through the turn: {error[:300]}"))
        # SHADOWS GO HERE, when they come: an opt-in that sends this same turn
        # (ask, minus the caller's stream) to other members in the background,
        # for evaluation only, after the caller has its answer. Each would be
        # its own client.chat (purpose "shadow"), recorded as an unjudged
        # offload on this conversation's call id with a suffix; nothing the
        # caller sees waits for them or changes.
        return emit("done", {"collection": model, "reply": "route", "conversation": conv["conv"], "call": call,
                             "alias": alias, "turn": turn_no, **({"failed_over": tried} if tried else {}),
                             **({"skipped": plan["skipped"]} if plan["skipped"] else {}),
                             "verdict": 'optional, once per conversation: POST /bench/verdict {"call", "useful", "note"}'})
    _log_route(conv, conv["turns"], "", bench_collections.PREFIX + name, who, "", "error", None, tried,
               "every member failed")
    # Every member rate limited (or held back by pacing): 429, so the caller
    # backs off and tries again, as it would for one provider.
    limited = all("Error code: 429" in t["error"] or "rate limit" in t["error"].lower() for t in tried)
    emit("error", (429 if tried and limited else 502,
                   f"no member of bench/{name} answered: " + "; ".join(f"{t['alias']}: {t['error'][:200]}" for t in tried)))


def _log_route(conv: dict, turn: int, call: str, model: str, who: str, alias: str, status: str, res: dict | None,
               tried: list[dict], error: str) -> None:
    """One turn's line in runs/gateway/routes.jsonl, which the dashboard's
    Gateway card reads for its route conversations."""
    import ledger
    res = res or {}
    line = {"conversation": conv["conv"], "call": call, "at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "model": model, "key": who, "alias": alias, "turn": turn, "task": conv["task"], "status": status,
            "seconds": res.get("seconds"), "first_token_s": res.get("first_token_s"),
            "tool_calls": res.get("tool_calls", 0), "finish": res.get("finish", ""), **ledger.tokens(res.get("usage") or {}),
            **({"tried": tried} if tried else {}), **({"error": error[-300:]} if error else {})}
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with (LOG_DIR / "routes.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")


class Handler(BaseHTTPRequestHandler):
    """The gateway's routes. serve.py's Handler subclasses this to answer them
    on the dashboard's port too, so the checks use the port actually served."""
    who = "-"

    def _json(self, code: int, data: dict, headers: dict | None = None) -> None:
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code: int, message: str, headers: dict | None = None, fmt: str = "chat") -> None:
        """A refusal, in the shape the route's clients read (metrics/formats.py)."""
        self._json(code, formats.INBOUND[fmt].error(code, message), headers=headers)

    def _query_key(self, fmt: str) -> str | None:
        """A Gemini route's ?key=, as Google's REST examples send it; no other
        route takes a key in its URL, where proxies and histories keep it."""
        if fmt != "gemini":
            return None
        return urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("key", [None])[0]

    def end_headers(self):
        # CORS for a browser app on an allowed origin, on the gateway's routes
        # only (metrics/guard.py); Vary, so a cache never hands one origin's
        # answer to another. A request line too broken to parse has no headers.
        headers = getattr(self, "headers", None)
        if headers is not None and guard.gateway_route((getattr(self, "path", "") or "").split("?", 1)[0]) \
                and (origin := guard.cors_origin(headers, self.server.server_port)):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Expose-Headers",
                             "X-Bench-Panel, X-Bench-Seats, X-Bench-Conversation, X-Bench-Model")
            self.send_header("Vary", "Origin")
        super().end_headers()

    def do_OPTIONS(self):                              # noqa: N802
        """A browser's CORS preflight: answered on the gateway's routes, for an
        allowed origin only. The dashboard's routes answer none, as before."""
        if not guard.gateway_route(self.path.split("?", 1)[0]):
            return self.send_error(501, f"Unsupported method ({self.command!r})")
        try:
            guard.check(self.headers, self.server.server_port, post=False, max_body=0)
        except guard.Refused as r:
            return self._error(r.status, str(r))
        if not guard.cors_origin(self.headers, self.server.server_port):
            return self._error(403, f"Origin {self.headers.get('Origin') or '(none)'!r} is not allowed;"
                                    " set BENCH_ALLOWED_ORIGINS or BENCHWARMERS_PUBLIC_URL to add one")
        # The headers it asks for as well, when they are plain names: the
        # OpenAI JS SDK sends X-Stainless-* on every call, and a preflight that
        # refused them would block it. The origin is the boundary, not these.
        asked = (self.headers.get("Access-Control-Request-Headers") or "").strip()
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type"
                         + (f", {asked}" if HEADER_NAMES.fullmatch(asked) else ""))
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def do_GET(self):                                  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/")
        fmt = formats.format_of(path)
        try:
            guard.check(self.headers, self.server.server_port, post=False, max_body=0)
            self.who = guard.gateway_caller(self.headers, self._query_key(fmt))
        except guard.Refused as r:
            return self._error(r.status, str(r), r.headers, fmt)
        if path in ("/v1/models", "/models"):
            # Beyond the OpenAI fields: what each collection is for, so an
            # orchestrator can choose one by task. Clients ignore extra keys.
            return self._json(200, {"object": "list", "data": [
                {"id": n, "object": "model", "owned_by": "benchwarmers", **d} for n, d in listed(self.who)]})
        if path == "/bench/record":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            collection, task = (query.get(k, [""])[0] for k in ("collection", "task"))
            if not collection:
                return self._error(400, "GET /bench/record?collection=bench/<name>[&task=<words in the verdicts' notes>]")
            return self._json(200, {"collection": collection, "task": task, "aliases": record(collection, task)})
        if path == "/api/tags":
            return self._json(200, formats.ollama_tags(listed(self.who)))
        if path == "/api/version":
            return self._json(200, {"version": formats.OLLAMA_VERSION})
        if path == "/v1beta/models":
            return self._json(200, formats.gemini_models(listed(self.who)))
        if path.startswith("/v1beta/models/"):
            name = urllib.parse.unquote(path[len("/v1beta/models/"):])
            row = dict(listed(self.who)).get(name)
            return self._json(200, formats.gemini_model(name, row)) if row else \
                self._error(404, f"{name!r} is not a collection here; GET /v1beta/models lists them", fmt=fmt)
        if guard.mcp_route(path):                      # no stream from the server: every reply is a POST's
            return self._json(405, {"error": {"message": "POST /mcp, one JSON-RPC message per request"}},
                              {"Allow": "POST"})
        self._error(404, "GET /v1/models, /bench/record, /api/tags, /api/version or /v1beta/models", fmt=fmt)

    def do_POST(self):                                 # noqa: N802
        # Without this any web page could spend model calls, seat ad hoc
        # models through `with`, or write verdicts (metrics/guard.py).
        path = self.path.split("?", 1)[0].rstrip("/")
        fmt = formats.format_of(path)
        try:
            length = guard.check(self.headers, self.server.server_port, post=True, max_body=MAX_BODY, cors=True)
            self.who = guard.gateway_caller(self.headers, self._query_key(fmt))
        except guard.Refused as r:
            return self._error(r.status, str(r) + (TOO_BIG if r.status == 413 else ""), r.headers, fmt)
        if guard.mcp_route(path):
            return self._mcp(length)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._error(400, "body is not JSON", fmt=fmt)
        if not isinstance(body, dict):
            return self._error(400, "body must be a JSON object", fmt=fmt)
        if path == "/bench/verdict":
            if not isinstance(body.get("useful"), bool):
                return self._error(400, '"useful" must be true or false')
            lists = {k: body.get(k) or [] for k in ("real", "false")}
            if not all(isinstance(v, list) and all(isinstance(s, str) for s in v) for v in lists.values()):
                return self._error(400, '"real" and "false" must be lists of one-sentence claims')
            try:
                e = offload.verdict(str(body.get("call", "")), body["useful"], str(body.get("note") or ""),
                                    lists["real"], lists["false"])
            except KeyError as exc:
                return self._error(404, exc.args[0])
            return self._json(200, {"ok": True, "recorded": e})
        if path == "/bench/pending":
            # Which of these call ids are seats that answered (ok or empty, not errored) and have
            # no verdict yet, however one is sent (mcp_server.py's pending_verdicts, the plugin's
            # Stop hook). An id the log does not know is not a seat. A seat's first entry is
            # record_seat's, its note "<seat>; <status> in <s>s".
            calls = body.get("calls")
            if not (isinstance(calls, list) and all(isinstance(c, str) for c in calls)):
                return self._error(400, '"calls" must be a list of call ids')
            first, judged = {}, set()
            for e in offload.entries():
                first.setdefault(e.get("call"), e)
                if e.get("useful") is not None:
                    judged.add(e.get("call"))
            return self._json(200, {"pending": [
                c for c in calls if c in first and c not in judged
                and "; error in " not in str(first[c].get("note", "")).split(":", 1)[0]]})
        # Each format's request becomes one Ask (metrics/formats.py); a part
        # that would be lost on the way, tools above all, is refused with why.
        gemini = formats.gemini_route(path)
        try:
            if gemini and gemini[1] == "countTokens":
                ask = formats.gemini_ask(body.get("generateContentRequest") or body, gemini[0])
                return self._json(200, {"totalTokens": prompt_tokens(ask["messages"])})
            if gemini:
                if gemini[1] not in ("generateContent", "streamGenerateContent"):
                    return self._error(404, f"POST models/<collection>:generateContent or :streamGenerateContent,"
                                            f" not :{gemini[1]}", fmt=fmt)
                ask = formats.gemini_ask(body, gemini[0], gemini[1] == "streamGenerateContent")
            elif path == "/v1/messages/count_tokens":
                # An estimate, as the size check makes, without calling a model.
                return self._json(200, {"input_tokens": prompt_tokens(formats.anthropic_ask(body)["messages"])})
            elif path == "/api/show":
                return self._show(body)
            elif path in formats.POST_ROUTES:
                ask = formats.INBOUND[fmt].ask(body)
            else:
                return self._error(404, "POST /v1/chat/completions, /v1/messages, /v1/responses, /v1/completions,"
                                        " /api/chat, /api/generate, /v1beta/models/<collection>:generateContent"
                                        " or /bench/verdict", fmt=fmt)
        except formats.Unsupported as exc:
            return self._error(400, str(exc), fmt=fmt)
        alt = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("alt", [""])[0]
        self._panel(fmt, ask, sse=alt == "sse")

    def _mcp(self, length: int) -> None:
        """MCP over streamable HTTP, a reply to each POST (JSON, or SSE for a
        tool call from a client that takes it): mcp_server.py's
        tools on this port, so a client starts no process of its own. They call
        this server's own routes over loopback with the caller's credential and
        Host (both already checked above), so a key's collections and rate
        limit hold exactly as on /v1."""
        try:
            msg = json.loads(self.rfile.read(length))
        except (ValueError, RecursionError):           # RecursionError: nesting deeper than json can parse
            return self._json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
        host, port = self.server.server_address[:2]
        host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)
        sent = {h: self.headers[h] for h in ("Authorization", "Host", *guard.KEY_HEADERS) if self.headers.get(h)}
        conn = (f"http://{'[' + host + ']' if ':' in host else host}:{port}", sent)
        if isinstance(msg, dict) and msg.get("method") == "tools/call" and "id" in msg \
                and "text/event-stream" in (self.headers.get("Accept") or ""):
            return self._mcp_stream(msg, conn)
        reply = mcp_server.answer(msg, conn)
        if reply is None:                              # a notification: accepted, nothing to say
            self.send_response(202)
            self.send_header("Content-Length", "0")
            return self.end_headers()
        self._json(200, reply)

    def _mcp_stream(self, msg: dict, conn: mcp_server.Conn) -> None:
        """A tool call as an SSE stream: the 200 at once, then every KEEPALIVE
        seconds while a panel runs (minutes) a progress notification when the
        client gave a progressToken, else a keep-alive comment, then the reply.
        A comment keeps proxies' idle timeouts away; only progress resets a
        client's own (Claude Code aborts a tool silent for 300 seconds)."""
        done: queue.Queue = queue.Queue()
        threading.Thread(target=lambda: done.put(mcp_server.answer(msg, conn)), daemon=True).start()
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        token = (params.get("_meta") or {}).get("progressToken") if isinstance(params.get("_meta"), dict) else None
        started = time.monotonic()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            while True:
                try:
                    reply = done.get(timeout=KEEPALIVE)
                except queue.Empty:
                    if token is None:
                        self.wfile.write(b": keep-alive\n\n")
                    else:
                        seconds = round(time.monotonic() - started)
                        self.wfile.write(b"event: message\ndata: " + json.dumps(
                            {"jsonrpc": "2.0", "method": "notifications/progress",
                             "params": {"progressToken": token, "progress": seconds,
                                        "message": f"the panel is running, {seconds} s"}}).encode() + b"\n\n")
                    self.wfile.flush()
                    continue
                self.wfile.write(b"event: message\ndata: " + json.dumps(reply).encode() + b"\n\n")
                return
        except OSError:
            return                                     # the caller left; the panel still finishes, every seat recorded

    def _show(self, body: dict) -> None:
        """POST /api/show: Ollama's model card, for a collection the key may call."""
        name = formats.ollama_model(body.get("model") or body.get("name") or "")
        rows = dict(listed(self.who))
        row = rows.get(name) or rows.get(bench_collections.PREFIX + str(name))
        if not row:
            return self._error(404, f"model {name!r} not found; GET /api/tags lists the collections",
                               fmt="ollama_chat")
        self._json(200, formats.ollama_show(name, row))

    def _panel(self, fmt: str, ask: dict, sse: bool = True) -> None:
        """One panel, for a request in any format: the collection it names,
        the key's scope and cap, then the panel, answered in that format."""
        model, suffix = bench_collections.split_reply(ask["model"])
        name = bench_collections.resolve(model)
        if name is None:
            return self._error(400 if not model else 404, bench_collections.choose_message(model), fmt=fmt)
        model = bench_collections.PREFIX + name
        if not guard.may_call(self.who, name):
            allowed = ", ".join(bench_collections.PREFIX + c for c in guard.scope(self.who) or []) or "none"
            return self._error(403, f"the gateway key {self.who} may not call {model}; it may call {allowed}",
                               fmt=fmt)
        try:
            guard.spend(self.who)                      # counted once it names a collection it may call
        except guard.Refused as r:
            return self._error(r.status, str(r), r.headers, fmt)
        reply = ask["reply"] or suffix                 # the body is the most specific, then the suffix, then the collection
        # Chat completions have always answered with the collection's name;
        # the other formats echo the name as asked, which their clients match.
        shown = model if fmt == "chat" else ask["model"]
        if (reply or bench_collections.load()[name].get("reply")) == "route":
            return self._route(fmt, name, ask, shown, sse)
        if ask["agentic"]:
            return self._error(400, formats.TOOLS, fmt=fmt)
        if ask["stream"]:
            out = formats.INBOUND[fmt].stream(shown, prompt_tokens(ask["messages"]), sse=sse)
            return self._stream(fmt, name, ask, reply, out)
        started = time.monotonic()
        try:
            content, bench = run_collection(name, ask["messages"], ask["with"], reply, self.who)
        except (ValueError, LookupError) as exc:       # TooLarge is a ValueError
            return self._error(*refusal(exc), fmt=fmt)
        usage = (prompt_tokens(ask["messages"]), estimate_tokens(content))
        self._json(200, formats.INBOUND[fmt].reply(content, shown, bench, usage, time.monotonic() - started),
                   bench_headers(bench) if fmt == "gemini" else None)

    def _stream(self, fmt: str, name: str, ask: dict, reply: str, out: formats.Stream) -> None:
        """The panel as it comes, in the format's stream shape: every answer
        seat by seat as each finishes, or a merged reply when the merge is
        done. The panel runs in its own thread and this one writes, so a
        keep-alive goes out every KEEPALIVE seconds while the seats think. A
        refusal raised before anything is ready (the size check, an image no
        seat can see: milliseconds) still gets its status; the 200 goes once
        the panel is seated or after STREAM_GRACE, whichever is first, and
        from then on an error goes into the stream in the format's shape."""
        events: queue.Queue = queue.Queue()

        def work():
            try:
                events.put(("done", run_collection(name, ask["messages"], ask["with"], reply, self.who,
                                                   on_part=lambda text: events.put(("part", text)))))
            except Exception as exc:                   # noqa: BLE001 -- answered below, as a status or in the stream
                events.put(("error", exc))
        threading.Thread(target=work, daemon=True).start()
        started = streamed = False
        while True:
            try:
                kind, data = events.get(timeout=KEEPALIVE if started else STREAM_GRACE)
            except queue.Empty:
                kind, data = "idle", None
            if kind == "error" and not started:
                return self._error(*refusal(data), fmt=fmt)
            try:
                if not started:
                    self.send_response(200)
                    self.send_header("Content-Type", out.content_type)
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    self.wfile.write(out.start())
                    started = True
                if kind == "idle":
                    self.wfile.write(out.keepalive())
                elif kind == "part":
                    self.wfile.write(out.text(data))
                    streamed = True
                elif kind == "done":
                    content, bench = data
                    self.wfile.write((b"" if streamed else out.text(content)) + out.end(bench))
                    return
                else:
                    self.wfile.write(out.error(*refusal(data)))
                    return
            except OSError:
                return                                 # the caller left; the panel still finishes, every seat recorded

    def _route(self, fmt: str, name: str, ask: dict, shown: str, sse: bool = True) -> None:
        """Route mode: one member's answer, in the request's format. The
        turn runs in its own thread (route_turn) and this one writes: a
        whole reply once it is done, or a stream whose 200 waits for the
        member's first piece, or ROUTE_GRACE, whichever is first, then
        relays it, with a keep-alive whenever nothing went for KEEPALIVE."""
        try:
            plan = route_plan(name, ask, self.who)
        except (ValueError, LookupError) as exc:       # TooLarge is a ValueError
            return self._error(*refusal(exc), fmt=fmt)
        events: queue.Queue = queue.Queue()

        def work():
            try:
                route_turn(name, plan, ask, self.who, lambda kind, data=None: events.put((kind, data)))
            except Exception as exc:                   # noqa: BLE001 -- answered below, as a status or in the stream
                events.put(("error", (500, f"route mode failed ({type(exc).__name__}: {exc})")))
        threading.Thread(target=work, daemon=True).start()
        tokens, began = route_tokens(ask), time.monotonic()
        headers = {"X-Bench-Conversation": plan["conv"]["conv"]}
        if not ask["stream"]:
            turn = formats.Turn()
            while True:
                kind, data = events.get()
                if kind == "chunk":
                    turn.feed(data)
                elif kind == "done":
                    return self._json(200, formats.route_reply(fmt, turn, shown, data, tokens, time.monotonic() - began,
                                                               ask.get("custom")), {**headers, "X-Bench-Model": data["alias"]})
                elif kind == "error":
                    return self._error(*data, fmt=fmt)
        out = formats.ROUTE[fmt](shown, tokens, sse=sse, custom=ask.get("custom"))
        started, wrote = False, time.monotonic()
        while True:
            try:
                kind, data = events.get(timeout=KEEPALIVE if started else max(0.0, ROUTE_GRACE - (time.monotonic() - began)))
            except queue.Empty:
                kind, data = "idle", None
            if kind == "error" and not started:
                return self._error(*data, fmt=fmt)
            if kind == "live":
                headers["X-Bench-Model"] = data
                continue
            try:
                if not started:
                    self.send_response(200)
                    for k, v in {"Content-Type": out.content_type, "Cache-Control": "no-cache", **headers}.items():
                        self.send_header(k, v)
                    self.end_headers()
                    self.wfile.write(out.start())
                    started = True
                piece = (out.chunk(data) if kind == "chunk" else out.end(data) if kind == "done" else
                         out.error(*data) if kind == "error" else b"")
                if not piece and time.monotonic() - wrote >= KEEPALIVE:
                    piece = out.keepalive()
                if piece:
                    self.wfile.write(piece)
                    wrote = time.monotonic()
                if kind in ("done", "error"):
                    return
            except OSError:
                # The caller left. ponytail: the member's answer still runs to
                # its end and is recorded; stopping it needs a per-call STOP.
                return

    def log_request(self, code="-", size="-"):         # route, status, who; never a query or a key
        guard.access(self, code)

    def log_message(self, fmt, *args):                 # quieter than the default
        # http.server's own errors quote the request line, ?key= and all
        # ("Bad request syntax ('GET /v1beta/models?key=...')").
        line = QUERY_KEY.sub(r"\1...", fmt % args)
        sys.stderr.write(f"{self.log_date_time_string()} {line}\n")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    address = guard.bind()
    guard.startup(address)
    print(f"bench gateway on http://{address}:{PORT}/v1 — models: {', '.join(models())}")
    ThreadingHTTPServer((address, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
