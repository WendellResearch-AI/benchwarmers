"""The bench method as an OpenAI-compatible endpoint, at http://127.0.0.1:8123/v1.

    python gateway.py

Ask for a COLLECTION by name — `bench/core`, `bench/reviewers` — and the prompt
goes in parallel to every member of it plus explorer seats `route.py` fills by
staleness, so the roster keeps getting evidence even when the same collection
is called every day:

    explorers = min(max_explorers, max_calls - members)     # defaults 2 and 5

Collections and those limits live in `config/collections.json` and
`config/gateway.json` (see `metrics/bench_collections.py`), edited from the
dashboard. A request with no model runs the default collection; a model name
that is not a collection is refused with the list, so a typo never runs one.

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

    python metrics/offload.py verdict <call id> --useful --note "..."
    POST /bench/verdict {"call": "<call id>", "useful": true, "note": "..."}

The verdict replaces the unjudged entry, and route.py's evidence — which
explorers are due, which models earn a place in a collection — reads it.

WHAT IT IS NOT
--------------
A single-model proxy. Tools such as Roo Code call Open WebUI directly; this
exists only for the multi-model work. Seats are called with `llm -m`, exactly
as offloads always were, so endpoints, headers and per-model options all come
from the same registry. Full prompts and answers go to `runs/gateway/`,
gitignored. Bound to 127.0.0.1, like the dashboard.
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
PORT = 8123
LOG_DIR = ROOT / "runs" / "gateway"
SEAT_TIMEOUT = 900
sys.path.insert(0, str(ROOT / "metrics"))
sys.path.insert(0, str(ROOT / "suite"))
import bench_collections  # noqa: E402
import decide  # noqa: E402
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


def flatten(messages: list[dict]) -> tuple[str, str]:
    """(system, prompt) for `llm`. A panel question is one prompt; a longer
    conversation is kept as labelled turns rather than silently truncated."""
    def text(m):
        c = m.get("content") or ""
        return c if isinstance(c, str) else "\n".join(
            p.get("text", "") for p in c if isinstance(p, dict))
    # Newer OpenAI SDKs send "developer" where older ones send "system".
    lead = ("system", "developer")
    system = "\n\n".join(text(m) for m in messages if m.get("role") in lead)
    turns = [m for m in messages if m.get("role") not in lead]
    if len(turns) == 1:
        return system, text(turns[0])
    return system, "\n\n".join(f"{m.get('role', 'user')}: {text(m)}" for m in turns)


def pick_shape(prompt: str) -> tuple[str, str]:
    """(shape, how it was chosen). Falls back to general on any doubt."""
    held = set(json.loads((ROOT / "config" / "model-roles.json").read_text(encoding="utf-8"))
               .get("ad_hoc", []))
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


def ask_seat(alias: str, system: str, prompt: str) -> dict:
    cmd = ["llm", "-m", alias] + (["-s", system] if system else [])
    started = time.monotonic()
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
    status = "error" if error else ("empty" if not answer else "ok")
    return {"status": status, "answer": answer, "error": error[-500:],
            "seconds": round(time.monotonic() - started, 1)}


def run_collection(name: str, messages: list[dict], nominated: list[str] | None = None) -> str:
    system, prompt = flatten(messages)
    if not prompt.strip():
        raise ValueError("no user message to put to the collection")
    collection = bench_collections.load()[name]
    members = list(collection.get("members") or [])
    n_explorers = bench_collections.explorers_for(len(members))
    # The system text goes to every seat too, so it counts for shape and size.
    whole = f"{system}\n\n{prompt}".strip()
    if not n_explorers:
        shape, how = collection.get("shape") or "general", "no explorer seats to fill"
    elif collection.get("shape"):
        shape, how = collection["shape"], "set on the collection"
    else:
        shape, how = pick_shape(whole)
    seats = [(f"member{i}", "member", alias) for i, alias in enumerate(members, 1)]
    if n_explorers:
        # `with` in the request body is route.py's --with: an explorer seat.
        p = route.panel(shape, large=len(whole) > LARGE, nominated=nominated or None,
                        members=members, explorers=n_explorers)
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
    return "\n".join(out)


def models() -> list[str]:
    return [bench_collections.PREFIX + n for n in bench_collections.load()]


class Handler(BaseHTTPRequestHandler):
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
        if self.path.split("?", 1)[0].rstrip("/") in ("/v1/models", "/models"):
            self._json(200, {"object": "list", "data": [
                {"id": m, "object": "model", "owned_by": "model-bench"} for m in models()]})
        else:
            self._error(404, "GET /v1/models is the only GET here")

    def do_POST(self):                                 # noqa: N802
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            return self._error(400, "body is not JSON, or Content-Length is not a number")
        if not isinstance(body, dict):
            return self._error(400, "body must be a JSON object")
        path = self.path.split("?", 1)[0].rstrip("/")
        if path == "/bench/verdict":
            if not isinstance(body.get("useful"), bool):
                return self._error(400, '"useful" must be true or false')
            try:
                e = offload.verdict(str(body.get("call", "")), body["useful"], str(body.get("note", "")))
            except KeyError as exc:
                return self._error(404, exc.args[0])
            return self._json(200, {"ok": True, "recorded": e})
        if path not in ("/v1/chat/completions", "/chat/completions"):
            return self._error(404, "POST /v1/chat/completions or /bench/verdict")
        model = body.get("model") or ""
        name = bench_collections.resolve(model if isinstance(model, str) else "")
        if name is None:
            asked = f"{model!r} is not a collection here" if model else \
                "no model given and no default collection is set"
            return self._error(404, f"{asked}; collections: {', '.join(models()) or 'none yet'}"
                                    " (created in the dashboard's Collections dialog)")
        model = bench_collections.PREFIX + name
        messages, nominated = body.get("messages"), body.get("with") or []
        if not (isinstance(messages, list) and all(isinstance(m, dict) for m in messages)):
            return self._error(400, '"messages" must be a list of message objects')
        if not (isinstance(nominated, list) and all(isinstance(a, str) for a in nominated)):
            return self._error(400, '"with" must be a list of model aliases')
        try:
            content = run_collection(name, messages, nominated)
        except ValueError as exc:
            return self._error(400, str(exc))
        except LookupError as exc:
            return self._error(503, str(exc))
        reply_id, created = f"chatcmpl-{secrets.token_hex(8)}", int(time.time())
        if not body.get("stream"):
            return self._json(200, {
                "id": reply_id, "object": "chat.completion", "created": created, "model": model,
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": content}}]})
        # A panel finishes all at once, so a streaming client gets it as one chunk.
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for delta, finish in (({"role": "assistant", "content": content}, None), ({}, "stop")):
            chunk = {"id": reply_id, "object": "chat.completion.chunk", "created": created,
                     "model": model, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, fmt, *args):                 # quieter than the default
        sys.stderr.write(f"{self.log_date_time_string()} {fmt % args}\n")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(f"bench gateway on http://127.0.0.1:{PORT}/v1 — models: {', '.join(models())}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
