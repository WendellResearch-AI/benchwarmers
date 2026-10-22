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

WHAT IT IS NOT
--------------
A single-model proxy. Tools such as Roo Code call Open WebUI directly; this
exists only for the multi-model work. Seats are called with `llm -m`, exactly
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
import json
import os
import pathlib
import re
import secrets
import subprocess
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
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
import guard  # noqa: E402
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
    return int(len(text) / 4 * 1.1)


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
    rotation counts it even if nobody judges; returns the seat as logged."""
    offload.add(alias, task, None, f"{label}; {r['status']} in {r['seconds']}s"
                + (f": {r['error'][:120]}" if r["error"] else ""), call)
    return {"call": call, "seat": label, "alias": alias, **r}


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


def run_collection(name: str, messages: list[dict], nominated: list[str] | None = None,
                   reply: str = "", who: str = "local") -> tuple[str, dict]:
    """The panel's reply as markdown, and the same seats as data for the
    response's `bench` field. `reply`: the request's mode, "" for the
    collection's own. `who`: the gateway key that asked, for the call log."""
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
    with ThreadPoolExecutor(max_workers=len(seats)) as pool:
        results = list(pool.map(lambda s: ask_seat(s[2], system, prompt, messages if pictures else None), seats))

    task = " ".join(prompt.split())[:90] + f" ({model}, {shape})"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    record = {"panel": panel_id, "at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
              "model": model, "shape": shape, "shape_by": how, "key": who, "system": system,
              "prompt": prompt, "seats": [], **({"skipped": skipped} if skipped else {})}
    out = [f"Bench {model} {panel_id} · {len(sitting)} members + {len(seats) - len(sitting)}"
           f" explorers · explorer shape {shape} ({how})\n"]
    if skipped:
        out.append("Not asked: " + "; ".join(f"{k['alias']} ({k['why']})" for k in skipped) + "\n")
    for (key, label, alias), r in zip(seats, results):
        call = f"{panel_id}.{key}"
        record["seats"].append(record_seat(task, call, label, alias, r))
        body = r["answer"] or (f"_error: {r['error'][:300]}_" if r["error"] else
                               "_empty reply — usually a token budget, not incapacity_")
        out.append(f"## {label} — {alias}\n`{call}` · {r['status']} · {r['seconds']}s\n\n{body}\n")
    out.append("---\nRecord a verdict per seat once checked against the code:\n"
               "`POST /bench/verdict` on this same server (see `bench.verdict`), or"
               " `python metrics/offload.py verdict <call id> --useful|--not-useful --note \"...\"`")
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

    def _error(self, code: int, message: str, headers: dict | None = None) -> None:
        self._json(code, {"error": {"message": message, "type": "bench_gateway", "code": code}}, headers=headers)

    def end_headers(self):
        # CORS for a browser app on an allowed origin, on the gateway's routes
        # only (metrics/guard.py); Vary, so a cache never hands one origin's
        # answer to another. A request line too broken to parse has no headers.
        headers = getattr(self, "headers", None)
        if headers is not None and guard.gateway_route((getattr(self, "path", "") or "").split("?", 1)[0]) \
                and (origin := guard.cors_origin(headers, self.server.server_port)):
            self.send_header("Access-Control-Allow-Origin", origin)
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
        try:
            guard.check(self.headers, self.server.server_port, post=False, max_body=0)
            self.who = guard.gateway_caller(self.headers)
        except guard.Refused as r:
            return self._error(r.status, str(r), r.headers)
        if self.path.split("?", 1)[0].rstrip("/") in ("/v1/models", "/models"):
            # Beyond the OpenAI fields: what each collection is for, so an
            # orchestrator can choose one by task. Clients ignore extra keys.
            # Each collection twice: as set, and in the other reply mode, so a
            # client that offers only listed models can still pick either.
            s, data = bench_collections.settings(), bench_collections.load()
            rows = []
            for n, c in data.items():
                if not guard.may_call(self.who, n):    # a key limited to some collections sees those
                    continue
                d = bench_collections.described(n, c, s)
                other = "all" if d["reply"] == "merged" else "merged"
                rows += [{"id": bench_collections.PREFIX + n, "object": "model", "owned_by": "benchwarmers", **d},
                         {"id": f"{bench_collections.PREFIX}{n}:{other}", "object": "model",
                          "owned_by": "benchwarmers", **d, "reply": other}]
            self._json(200, {"object": "list", "data": rows})
        else:
            self._error(404, "GET /v1/models is the only GET here")

    def do_POST(self):                                 # noqa: N802
        # Without this any web page could spend model calls, seat ad hoc
        # models through `with`, or write verdicts (metrics/guard.py).
        try:
            length = guard.check(self.headers, self.server.server_port, post=True, max_body=MAX_BODY, cors=True)
            self.who = guard.gateway_caller(self.headers)
        except guard.Refused as r:
            return self._error(r.status, str(r) + (TOO_BIG if r.status == 413 else ""), r.headers)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._error(400, "body is not JSON")
        if not isinstance(body, dict):
            return self._error(400, "body must be a JSON object")
        path = self.path.split("?", 1)[0].rstrip("/")
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
        if path not in ("/v1/chat/completions", "/chat/completions"):
            return self._error(404, "POST /v1/chat/completions or /bench/verdict")
        model = body.get("model") or ""
        if not isinstance(model, str):
            return self._error(400, '"model" must be a collection name such as bench/core')
        model, suffix = bench_collections.split_reply(model)
        name = bench_collections.resolve(model)
        if name is None:
            return self._error(400 if not model else 404, bench_collections.choose_message(model))
        model = bench_collections.PREFIX + name
        messages, nominated, opts = body.get("messages"), body.get("with") or [], body.get("bench") or {}
        if not (isinstance(messages, list) and all(isinstance(m, dict) for m in messages)):
            return self._error(400, '"messages" must be a list of message objects')
        if not (isinstance(nominated, list) and all(isinstance(a, str) for a in nominated)):
            return self._error(400, '"with" must be a list of model aliases')
        if not isinstance(opts, dict) or opts.get("reply", "all") not in bench_collections.REPLIES:
            return self._error(400, '"bench" must be an object whose "reply" is "all" or "merged"')
        if not guard.may_call(self.who, name):
            allowed = ", ".join(bench_collections.PREFIX + c for c in guard.scope(self.who) or []) or "none"
            return self._error(403, f"the gateway key {self.who} may not call {model}; it may call {allowed}")
        try:
            guard.spend(self.who)                      # counted once it names a collection it may call
        except guard.Refused as r:
            return self._error(r.status, str(r), r.headers)
        try:
            # The body is the most specific, then the suffix, then the collection.
            content, bench = run_collection(name, messages, nominated, opts.get("reply") or suffix, self.who)
        except TooLarge as exc:
            return self._error(413, str(exc))
        except ValueError as exc:
            return self._error(400, str(exc))
        except LookupError as exc:
            return self._error(503, str(exc))
        reply_id, created = f"chatcmpl-{secrets.token_hex(8)}", int(time.time())
        if not body.get("stream"):
            return self._json(200, {
                "id": reply_id, "object": "chat.completion", "created": created, "model": model,
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": content}}], "bench": bench})
        # A panel finishes all at once, so a streaming client gets it as one
        # chunk, merged or not. ponytail: nothing is sent until the panel and
        # the merge are done, so a client or proxy with a shorter idle timeout
        # drops it. Keep-alives mean sending the 200 before the panel runs, so
        # the 413/503 refusals move into the stream; streaming the merge as it
        # is written needs a streaming client.chat (RELEASE-PLAN acceptance 11).
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for delta, finish in (({"role": "assistant", "content": content}, None), ({}, "stop")):
            chunk = {"id": reply_id, "object": "chat.completion.chunk", "created": created,
                     "model": model, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                     **({"bench": bench} if finish is None else {})}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def log_request(self, code="-", size="-"):         # route, status, who; never a query or a key
        guard.access(self, code)

    def log_message(self, fmt, *args):                 # quieter than the default
        sys.stderr.write(f"{self.log_date_time_string()} {fmt % args}\n")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    address = guard.bind()
    guard.startup(address)
    print(f"bench gateway on http://{address}:{PORT}/v1 — models: {', '.join(models())}")
    ThreadingHTTPServer((address, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
