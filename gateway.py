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

A collection's `shape` (review, design, …) decides which models may fill the
explorer seats. Left empty, a decision model labels the prompt when one that
takes choice questions is onboarded (not ad hoc); otherwise, or on any doubt,
the shape is `general`. A request body may add `"with": ["<alias>"]` to seat a
model in an explorer chair, as `route.py --with` does.

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

WHAT IT IS NOT
--------------
A single-model proxy. Tools such as Roo Code call Open WebUI directly; this
exists only for the multi-model work. Seats are called with `llm -m`, exactly
as offloads always were, so endpoints, headers and per-model options all come
from the same registry. Full prompts and answers go to `runs/gateway/`,
gitignored. Bound to 127.0.0.1, like the dashboard (BENCH_BIND and
BENCH_GATEWAY_PORT change that; beyond this machine it asks for a gateway key,
metrics/guard.py). The dashboard answers these same routes on its own port.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import secrets
import subprocess
import sys
import time
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
MAX_BODY = 16_000_000  # bytes; a million-token prompt is about 4 MB


def flatten(messages: list[dict]) -> tuple[str, str]:
    """(system, prompt) for `llm`. A panel question is one prompt; a longer
    conversation is kept as labelled turns rather than silently truncated."""
    def text(m):
        c = m.get("content") or ""
        return c if isinstance(c, str) else "\n".join(
            str(p.get("text") or "") for p in c if isinstance(p, dict))
    # Newer OpenAI SDKs send "developer" where older ones send "system".
    lead = ("system", "developer")
    system = "\n\n".join(text(m) for m in messages if m.get("role") in lead)
    turns = [m for m in messages if m.get("role") not in lead]
    if len(turns) == 1:
        return system, text(turns[0])
    return system, "\n\n".join(f"{m.get('role', 'user')}: {text(m)}" for m in turns)


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


def ask_seat(alias: str, system: str, prompt: str) -> dict:
    cmd = ["llm", "-m", alias] + (["-s", system] if system else [])
    started = time.monotonic()
    if client.backend(alias) == "builtin":
        # No subprocess, and no shared llm log for parallel seats to lock.
        res = client.chat(alias, prompt, system, SEAT_TIMEOUT)
        answer = res["text"]
        error = res["error"] or (f"no answer in {SEAT_TIMEOUT}s" if res["timed_out"] else "")
    else:
        answer, error = _ask_llm(cmd, prompt)
    # A quota or credit notice delivered as the reply's text, with a success
    # exit, is not an answer (suite/run.py PROVIDER_NOTICES).
    from run import provider_notice
    if not error and (notice := provider_notice(answer)):
        error, answer = f"provider notice in a success reply ({notice}): {answer[:200]}", ""
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
        # A non-zero exit is an error even when stderr is silent.
        error = (done.stderr.strip() or f"llm exited {done.returncode}") if done.returncode else ""
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
    length' errors (metrics/learn.py)."""
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


def run_collection(name: str, messages: list[dict], nominated: list[str] | None = None) -> tuple[str, dict]:
    """The panel's reply as markdown, and the same seats as data for the
    response's `bench` field."""
    system, prompt = flatten(messages)
    if not prompt.strip():
        raise ValueError("no user message to put to the collection")
    collection = bench_collections.load()[name]
    s = bench_collections.settings()
    raw = collection.get("members") or []
    # The editor dedupes and caps; a hand-edited file may not have.
    members = list(dict.fromkeys(m for m in raw if isinstance(m, str) and m))[:s["max_calls"]]
    n_explorers = bench_collections.explorers_for(len(members), s)
    # A member named in `with` is already seated; it must not use up the slot.
    nominated = [a for a in dict.fromkeys(nominated or []) if a not in members]
    # The system text goes to every seat too, so it counts for shape and size.
    whole = f"{system}\n\n{prompt}".strip()
    # REFUSE WHAT A MEMBER CANNOT READ, so the caller can trim the prompt or
    # pick another collection instead of collecting a seat's context error.
    needed = estimate_tokens(whole)
    small = too_small(needed, members)
    if small:
        windows = ", ".join(f"{a} {cw:,}" for a, cw in sorted(small.items(), key=lambda kv: kv[1]))
        raise TooLarge(f"prompt is about {needed:,} tokens; too large for {windows} (context"
                       f" windows) in bench/{name}. Trim it under {min(small.values()):,} tokens,"
                       " or choose a collection whose members have larger windows"
                       " (GET /v1/models lists each collection's context_window).")
    if not n_explorers:
        shape, how = collection.get("shape") or "general", "no explorer seats to fill"
    elif collection.get("shape"):
        shape, how = collection["shape"], "set on the collection"
    else:
        shape, how = pick_shape(whole)
    seats = [(f"member{i}", "member", alias) for i, alias in enumerate(members, 1)]
    if n_explorers:
        # `with` in the request body is route.py's --with: an explorer seat.
        from run import _registered_names
        avoid = retiring() | set(too_small(needed, _registered_names()))   # explorers must fit too
        p = route.panel(shape, large=len(whole) > LARGE, nominated=nominated or None,
                        members=members, explorers=n_explorers, avoid=avoid)
        seats += [(key, label, p[key]["model"]) for key, label in
                  (("explorer", "explorer"), ("explorer2", "explorer 2")) if p.get(key)]
    if not seats:
        raise LookupError(f"collection {name!r} has no members and no explorer could be seated")
    panel_id = f"p{dt.datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
    model = bench_collections.PREFIX + name
    with ThreadPoolExecutor(max_workers=len(seats)) as pool:
        results = list(pool.map(lambda s: ask_seat(s[2], system, prompt), seats))

    task = " ".join(prompt.split())[:90] + f" ({model}, {shape})"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    record = {"panel": panel_id, "at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
              "model": model, "shape": shape, "shape_by": how, "system": system,
              "prompt": prompt, "seats": []}
    out = [f"Bench {model} {panel_id} · {len(members)} members + {len(seats) - len(members)}"
           f" explorers · explorer shape {shape} ({how})\n"]
    for (key, label, alias), r in zip(seats, results):
        call = f"{panel_id}.{key}"
        offload.add(alias, task, None, f"{label}; {r['status']} in {r['seconds']}s"
                    + (f": {r['error'][:120]}" if r["error"] else ""), call)
        record["seats"].append({"call": call, "seat": label, "alias": alias, **r})
        body = r["answer"] or (f"_error: {r['error'][:300]}_" if r["error"] else
                               "_empty reply — usually a token budget, not incapacity_")
        out.append(f"## {label} — {alias}\n`{call}` · {r['status']} · {r['seconds']}s\n\n{body}\n")
    with (LOG_DIR / "calls.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    out.append("---\nRecord a verdict per seat once checked against the code:\n"
               "`python metrics/offload.py verdict <call id> --useful|--not-useful --note \"...\"`")
    # Beyond the OpenAI fields, so a caller reads seats without parsing the
    # markdown. Clients that do not know it ignore it.
    bench = {"panel": panel_id, "collection": model, "shape": shape, "shape_by": how,
             "seats": [{k: s[k] for k in ("call", "seat", "alias", "status", "seconds", "answer", "error")}
                       for s in record["seats"]],
             "verdict": f"POST http://127.0.0.1:{PORT}/bench/verdict"
                        ' {"call", "useful": true|false, "note", "real": [...], "false": [...]}'}
    return "\n".join(out), bench


def models() -> list[str]:
    return [bench_collections.PREFIX + n for n in bench_collections.load()]


class Handler(BaseHTTPRequestHandler):
    """The gateway's routes. serve.py's Handler subclasses this to answer them
    on the dashboard's port too, so the checks use the port actually served."""
    who = "-"

    def _json(self, code: int, data: dict) -> None:
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code: int, message: str) -> None:
        self._json(code, {"error": {"message": message, "type": "bench_gateway", "code": code}})

    def do_GET(self):                                  # noqa: N802
        try:
            guard.check(self.headers, self.server.server_port, post=False, max_body=0)
            self.who = guard.gateway_caller(self.headers)
        except guard.Refused as r:
            return self._error(r.status, str(r))
        if self.path.split("?", 1)[0].rstrip("/") in ("/v1/models", "/models"):
            # Beyond the OpenAI fields: what each collection is for, so an
            # orchestrator can choose one by task. Clients ignore extra keys.
            s, data = bench_collections.settings(), bench_collections.load()
            self._json(200, {"object": "list", "data": [
                {"id": bench_collections.PREFIX + n, "object": "model", "owned_by": "benchwarmers",
                 **bench_collections.described(n, c, s)} for n, c in data.items()]})
        else:
            self._error(404, "GET /v1/models is the only GET here")

    def do_POST(self):                                 # noqa: N802
        # Without this any web page could spend model calls, seat ad hoc
        # models through `with`, or write verdicts (metrics/guard.py).
        try:
            length = guard.check(self.headers, self.server.server_port, post=True, max_body=MAX_BODY)
            self.who = guard.gateway_caller(self.headers)
        except guard.Refused as r:
            return self._error(r.status, str(r))
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
        name = bench_collections.resolve(model)
        if name is None:
            return self._error(400 if not model else 404, bench_collections.choose_message(model))
        model = bench_collections.PREFIX + name
        messages, nominated = body.get("messages"), body.get("with") or []
        if not (isinstance(messages, list) and all(isinstance(m, dict) for m in messages)):
            return self._error(400, '"messages" must be a list of message objects')
        if not (isinstance(nominated, list) and all(isinstance(a, str) for a in nominated)):
            return self._error(400, '"with" must be a list of model aliases')
        try:
            content, bench = run_collection(name, messages, nominated)
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
        # A panel finishes all at once, so a streaming client gets it as one chunk.
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
