"""Benchwarmers as an MCP server, so an agent (Claude Code, or any MCP client)
can ask a collection and send its verdicts.

    benchwarmers mcp                 # from npm: npx -y @wendellresearch-ai/benchwarmers mcp
    python mcp_server.py             # from a checkout

MCP over stdio: JSON-RPC 2.0, one message per line, nothing else on stdout.
A THIN CLIENT of a Benchwarmers server that is already running, over the same
HTTP routes any other caller uses (GATEWAY.md), so the panel, the call log and
the verdicts are that server's, whichever machine it is on:

    BENCHWARMERS_URL       the server, default http://127.0.0.1:8122 (a /v1 on the end is fine)
    BENCHWARMERS_KEY       a gateway key, sent as `Authorization: Bearer`; needed beyond
                           this machine, or where BENCHWARMERS_REQUIRE_KEYS is set (metrics/guard.py)
    BENCHWARMERS_TIMEOUT   seconds to wait for a panel, default 900

Tools: list_collections (GET /v1/models), ask (POST /v1/chat/completions),
verdict (POST /bench/verdict). A refusal from the server, or no server at
all, comes back as a tool error carrying its message. Standard library only,
and nothing from metrics/: the data folder is the server's, not this one's.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

# Newest first: the client's version when it is one of these, else the newest.
PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
PREFIX = "bench/"
VERSION = (Path(__file__).resolve().parent / "VERSION").read_text(encoding="utf-8").strip()

RULES = (
    "Benchwarmers asks a COLLECTION of models at once (every member plus explorer seats it rotates in)"
    " and keeps every answer. Rules: (1) ask a collection, not a favourite model; list_collections says"
    " what each is for. (2) Verify before acting on anything a seat says: seats fail together, so"
    " agreement is not evidence. (3) Send a verdict per seat you judged, with the claims it got right"
    " in `real` and the untrue ones in `false`. (4) Name every seat that answered, by model and seat,"
    " including errors and empty replies. An empty reply from a reasoning model is usually its token"
    " budget, not incapacity. Good uses: exhaustive enumeration over real files, adversarial review."
    " Keep the design and the decisions yourself.")

TOOLS = [
    {"name": "list_collections",
     "title": "List collections",
     "description": "The collections this Benchwarmers server offers: name, what each is for, tags, members,"
                    " explorer seats, reply mode, and what the members handle together (smallest context"
                    " window, shared capabilities, tightest rate limits). Pick by task before calling ask.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "annotations": {"readOnlyHint": True, "openWorldHint": False}},
    {"name": "ask",
     "title": "Ask a collection",
     "description": "Put a prompt to every seat of one collection at once and get every answer back, each"
                    " under its seat and model, plus the seats as data (alias, seat, status, call id). Can"
                    " take minutes. Ask a collection, not a favourite model. Give the seats the real text"
                    " (code, diff, file contents) in the prompt: they cannot read your files. Verify every"
                    " claim against the source before acting on it, name every seat that answered in what"
                    " you tell the user, then send a verdict per seat you judged.",
     "inputSchema": {"type": "object", "required": ["collection", "prompt"], "additionalProperties": False,
                     "properties": {
                         "collection": {"type": "string",
                                        "description": "A collection name, `core` or `bench/core` (list_collections)."},
                         "prompt": {"type": "string", "description": "The question, with everything it needs."},
                         "system": {"type": "string", "description": "A system message for every seat."},
                         "merged": {"type": "boolean",
                                    "description": "true: one reconciled answer, every seat still called and"
                                                   " listed; false: every answer. Omit for the collection's own."},
                         "with": {"type": "array", "items": {"type": "string"},
                                  "description": "Model aliases to seat in explorer chairs, this request only."}}},
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True}},
    {"name": "verdict",
     "title": "Record a verdict",
     "description": "Judge one seat's answer by its call id, once you have checked it against the source. One"
                    " per seat you judged. List the claims it got right in `real` and the claims that are"
                    " untrue in `false`, one sentence each: they feed routing and the 'confidently wrong'"
                    " record, so a note alone is not enough.",
     "inputSchema": {"type": "object", "required": ["call", "useful"], "additionalProperties": False,
                     "properties": {
                         "call": {"type": "string", "description": "The seat's call id, e.g. p20261022-101500-ab12.member1."},
                         "useful": {"type": "boolean"},
                         "note": {"type": "string", "description": "One or two sentences on why."},
                         "real": {"type": "array", "items": {"type": "string"},
                                  "description": "Claims it made that are true, one sentence each."},
                         "false": {"type": "array", "items": {"type": "string"},
                                   "description": "Claims it made that are untrue, one sentence each."}}},
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True,
                     "openWorldHint": False}},
]


class ToolError(Exception):
    """A tool call that failed: its message goes back as the tool's result, isError."""


def base_url() -> str:
    url = (os.environ.get("BENCHWARMERS_URL") or "").strip().rstrip("/") or "http://127.0.0.1:8122"
    return url.removesuffix("/v1")


def env_conn() -> tuple[str, dict]:
    """(server, headers) for the stdio server: BENCHWARMERS_URL and BENCHWARMERS_KEY. Served
    over HTTP (gateway.py's /mcp), the server's own address and the caller's credential instead."""
    key = (os.environ.get("BENCHWARMERS_KEY") or "").strip()
    return base_url(), {"Authorization": f"Bearer {key}"} if key else {}


def http(conn: tuple[str, dict], method: str, path: str, body: dict | None = None) -> dict:
    base, extra = conn
    url = base + path
    headers = {"Accept": "application/json", **extra}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    timeout = float(os.environ.get("BENCHWARMERS_TIMEOUT") or 900)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            message = json.loads(raw)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            message = raw.strip()[:500] or e.reason
        raise ToolError(f"Benchwarmers refused {method} {path} ({e.code}): {message}") from None
    except (urllib.error.URLError, ConnectionError) as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, TimeoutError):
            raise ToolError(f"no answer from Benchwarmers at {base} in {timeout:.0f} s;"
                            " set BENCHWARMERS_TIMEOUT for longer panels") from None
        raise ToolError(f"Benchwarmers is not running at {base} ({reason}). Start it (`benchwarmers`,"
                        " or its service), or point BENCHWARMERS_URL at the server you use.") from None
    except TimeoutError:
        raise ToolError(f"no answer from Benchwarmers at {base} in {timeout:.0f} s;"
                        " set BENCHWARMERS_TIMEOUT for longer panels") from None
    except ValueError:
        raise ToolError(f"{base} answered {method} {path} with something that is not JSON;"
                        " is BENCHWARMERS_URL a Benchwarmers server?") from None


def list_collections(_args: dict, conn: tuple[str, dict]) -> list[str]:
    rows = []
    for m in http(conn, "GET", "/v1/models").get("data") or []:
        if ":" in m.get("id", ""):                     # the same collection in its other reply mode
            continue
        together = m.get("together") or {}
        rows.append({"name": m["id"], **{k: m.get(k) for k in ("description", "tags", "members", "explorers",
                                                              "shape", "reply", "merger")},
                     **{k: together.get(k) for k in ("context_window", "capabilities", "limits")}})
    if not rows:
        return ["No collections on this server (or none this gateway key may call). Make one on the"
                " dashboard's Collections page."]
    return [json.dumps(rows, indent=1, ensure_ascii=False)]


def ask(args: dict, conn: tuple[str, dict]) -> list[str]:
    collection, prompt = args.get("collection"), args.get("prompt")
    if not (isinstance(collection, str) and collection.strip()):
        raise ToolError("`collection` is required: a name from list_collections, e.g. core")
    if not (isinstance(prompt, str) and prompt.strip()):
        raise ToolError("`prompt` is required")
    collection = collection.strip()
    messages = [{"role": "user", "content": prompt}]
    if isinstance(args.get("system"), str) and args["system"].strip():
        messages.insert(0, {"role": "system", "content": args["system"]})
    body = {"model": collection if collection.startswith(PREFIX) else PREFIX + collection, "messages": messages}
    if isinstance(args.get("merged"), bool):
        body["bench"] = {"reply": "merged" if args["merged"] else "all"}
    if args.get("with"):
        body["with"] = args["with"]
    reply = http(conn, "POST", "/v1/chat/completions", body)
    content = reply["choices"][0]["message"]["content"]
    bench = reply.get("bench") or {}
    merged = bench.get("reply") == "merged"
    # Each answer is already in the content unless it is the merge, so the
    # seat data stays short: who sat, how it went, the id to judge it by.
    keep = ("call", "seat", "alias", "status", "seconds", "error") + (("answer",) if merged else ())
    seats = [{k: s[k] for k in keep if s.get(k) not in (None, "")} for s in bench.get("seats") or []]
    for s in seats:
        if "error" in s:
            s["error"] = s["error"][-300:]             # a traceback's last lines say what went wrong
    data = {"panel": bench.get("panel"), "collection": bench.get("collection"), "shape": bench.get("shape"),
            "seats": seats, **{k: bench[k] for k in ("merger", "skipped", "note") if bench.get(k)}}
    return [content, "Seats (name each one; send a verdict per seat you judged, by call id):\n"
            + json.dumps(data, indent=1, ensure_ascii=False)]


def verdict(args: dict, conn: tuple[str, dict]) -> list[str]:
    if not (isinstance(args.get("call"), str) and args["call"].strip()):
        raise ToolError("`call` is required: the seat's call id from ask")
    if not isinstance(args.get("useful"), bool):
        raise ToolError("`useful` must be true or false")
    body = {"call": args["call"].strip(), "useful": args["useful"], "note": args.get("note") or "",
            "real": args.get("real") or [], "false": args.get("false") or []}
    http(conn, "POST", "/bench/verdict", body)
    return [f"Recorded {body['call']}: {'useful' if body['useful'] else 'not useful'},"
            f" {len(body['real'])} real, {len(body['false'])} false."]


HANDLERS = {"list_collections": list_collections, "ask": ask, "verdict": verdict}
OUT = threading.Lock()


def send(message: dict) -> None:
    line = json.dumps(message, ensure_ascii=False) + "\n"
    with OUT:
        sys.stdout.buffer.write(line.encode("utf-8"))
        sys.stdout.buffer.flush()


def call_tool(rid, params: dict, conn: tuple[str, dict]) -> dict:
    name, args = params.get("name"), params.get("arguments") or {}
    if not isinstance(name, str) or name not in HANDLERS:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": f"unknown tool {name!r}"}}
    try:
        if not isinstance(args, dict):
            raise ToolError("arguments must be an object")
        texts, failed = HANDLERS[name](args, conn), False
    except ToolError as e:
        texts, failed = [str(e)], True
    except Exception as e:                             # noqa: BLE001 -- a bad reply must not kill the server
        texts, failed = [f"{type(e).__name__}: {e}"], True
    return {"jsonrpc": "2.0", "id": rid,
            "result": {"content": [{"type": "text", "text": t} for t in texts], "isError": failed}}


def answer(msg, conn: tuple[str, dict]) -> dict | None:
    """The reply to one JSON-RPC message, or None for a notification. Both
    transports: the stdio loop below and the HTTP route (gateway.py, /mcp).
    Never raises: a request left unanswered would hang its client."""
    try:
        return _answer(msg, conn)
    except Exception as e:                             # noqa: BLE001
        rid = msg.get("id") if isinstance(msg, dict) else None
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32603, "message": f"{type(e).__name__}: {e}"}}


def _answer(msg, conn: tuple[str, dict]) -> dict | None:
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        rid = msg.get("id") if isinstance(msg, dict) else None
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32600, "message": "invalid request"}}
    if "id" not in msg:                                # a notification (initialized, cancelled): nothing to answer
        return None
    rid, method, params = msg["id"], msg["method"], msg.get("params") or {}
    if not isinstance(params, dict):
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": "params must be an object"}}
    if method == "initialize":
        asked = params.get("protocolVersion")
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "benchwarmers", "title": "Benchwarmers", "version": VERSION},
            "instructions": RULES}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        return call_tool(rid, params, conn)
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"no method {method!r}"}}


def reply_to(msg) -> None:
    if (reply := answer(msg, env_conn())) is not None:
        send(reply)


def handle(msg) -> None:
    if isinstance(msg, dict) and msg.get("method") == "tools/call":
        # A panel takes minutes: on its own thread, so a ping or a second call is answered meanwhile.
        # Not a daemon: when stdin closes, a call already sent (a verdict) still finishes.
        threading.Thread(target=reply_to, args=(msg,)).start()
    else:
        reply_to(msg)


def main() -> None:
    for raw in sys.stdin.buffer:
        if not raw.strip():
            continue
        try:
            msg = json.loads(raw)
        except (ValueError, RecursionError):           # RecursionError: nesting deeper than json can parse
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            continue
        handle(msg)


if __name__ == "__main__":
    main()
