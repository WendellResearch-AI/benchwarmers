"""bench: Benchwarmers from the command line, everything the dashboard does
that makes sense without one.

    bench status                                  what is running, and where
    bench ask core "review this" -f diff.txt      ask a collection, every seat's answer
    bench verdict <call id> --useful --real "..." record what a seat got right and wrong
    bench models | collections | endpoints | keys --help on any of them

A THIN CLIENT of a running Benchwarmers server, over HTTP, like the MCP
server (mcp_server.py): one process writes the data folder, and the same
commands work against a server on another machine.

    BENCHWARMERS_URL           the server, default http://127.0.0.1:8122
    BENCHWARMERS_ADMIN_TOKEN   for the dashboard's routes, where the server has one set
    BENCHWARMERS_KEY           a gateway key for ask/collections/verdict, where keys are required
    BENCHWARMERS_TIMEOUT       seconds to wait for a panel, default 900

--json on any command prints what the server answered, for scripts. Exit
codes: 0 done, 1 refused or failed, 2 bad usage, 3 no server. Standard
library only; nothing from metrics/, the data folder being the server's.
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PREFIX = "bench/"
GATEWAY = ("/v1/", "/bench/")
VERSION = (Path(__file__).resolve().parent / "VERSION").read_text(encoding="utf-8").strip()
OVERRIDES: dict[str, str] = {}                         # from flags (--url), over the environment


class Failed(Exception):
    """Print the message, exit with `code`."""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


# ---- talking to the server --------------------------------------------------

def env(name: str) -> str:
    """This client's settings: a flag, else the environment (one place, so a
    test can give the client different ones from the server in its process)."""
    return (OVERRIDES.get(name) or os.environ.get(name) or "").strip()


def too_old(path: str) -> Failed:
    """A route this CLI knows and the server does not: an older server."""
    return Failed(f"the server at {base_url()} has no {path}: it is older than this bench ({VERSION})."
                  " Upgrade it, or use its dashboard for this.")


def base_url() -> str:
    return (env("BENCHWARMERS_URL").rstrip("/") or "http://127.0.0.1:8122").removesuffix("/v1")


def http(method: str, path: str, body: dict | None = None) -> dict:
    """JSON in, JSON out. The gateway's routes take the gateway key, every
    other route the admin token (metrics/guard.py decides by route too)."""
    headers = {"Accept": "application/json"}
    if secret := env("BENCHWARMERS_KEY" if path.startswith(GATEWAY) else "BENCHWARMERS_ADMIN_TOKEN"):
        headers["Authorization"] = f"Bearer {secret}"
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    timeout = float(env("BENCHWARMERS_TIMEOUT") or 900)
    req = urllib.request.Request(base_url() + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            # An unknown GET is answered with the dashboard's page, not a 404.
            if path.startswith("/api/") and "text/html" in (r.headers.get("Content-Type") or ""):
                raise too_old(path)
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            reply = json.loads(raw)
        except ValueError:
            reply = None
        if e.code == 404 and isinstance(reply, dict) and reply.get("errors") == ["unknown endpoint"]:
            raise too_old(path) from None
        if isinstance(reply, dict) and reply.get("ok") is False and e.code != 401:
            return reply                       # the dashboard's own refusal: errors[] say why
        message = None
        if isinstance(reply, dict):
            message = "; ".join(reply.get("errors") or []) or (reply.get("error") or {}).get("message")
        if e.code == 401:
            need = "BENCHWARMERS_KEY (a gateway key)" if path.startswith(GATEWAY) else "BENCHWARMERS_ADMIN_TOKEN"
            message = f"{message or raw.strip()[:300]}; set {need}"
        raise Failed(f"{method} {path} refused ({e.code}): {message or raw.strip()[:300] or e.reason}") from None
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, TimeoutError) or isinstance(e, TimeoutError):
            raise Failed(f"no answer from {base_url()} in {timeout:.0f} s; set BENCHWARMERS_TIMEOUT"
                         " for longer panels") from None
        raise Failed(f"Benchwarmers is not running at {base_url()} ({reason}). Start it with"
                     " `bench serve` (or its service), or set BENCHWARMERS_URL.", 3) from None
    except ValueError:
        raise Failed(f"{base_url()}{path} did not answer JSON; is BENCHWARMERS_URL a Benchwarmers"
                     " server?") from None


def call(method: str, path: str, body: dict | None = None) -> dict:
    """`http`, failing on a dashboard refusal ({"ok": false, "errors": [...]})."""
    reply = http(method, path, body)
    if isinstance(reply, dict) and reply.get("ok") is False:
        raise Failed("; ".join(reply.get("errors") or ["refused"]))
    return reply


# ---- printing ---------------------------------------------------------------

def out(args, reply, human) -> None:
    """--json prints the reply as the server sent it; otherwise `human(reply)`."""
    if args.json:
        print(json.dumps(reply, indent=2, ensure_ascii=False))
    else:
        human(reply)


def table(rows: list[dict], cols: list[str], empty: str = "none") -> None:
    if not rows:
        print(empty)
        return
    cells = [[str(c) for c in cols]] + [["" if r.get(c) in (None, False) else
                                         "yes" if r.get(c) is True else
                                         ", ".join(map(str, r[c])) if isinstance(r.get(c), list) else str(r[c])
                                         for c in cols] for r in rows]
    width = [max(len(row[i]) for row in cells) for i in range(len(cols))]
    for n, row in enumerate(cells):
        print("  ".join(v.ljust(w) for v, w in zip(row, width)).rstrip())
        if n == 0:
            print("  ".join("-" * w for w in width))


def done(reply: dict, said: str = "done") -> None:
    print(reply.get("note") or said)
    for w in reply.get("warnings") or []:
        print(f"note: {w}", file=sys.stderr)


def csv(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def confirm(args, what: str) -> None:
    """Ask before something that cannot be taken back in one step: y in a
    terminal, --yes anywhere; never silently in a script."""
    if getattr(args, "yes", False):
        return
    if not sys.stdin.isatty():
        raise Failed(f"{what}: add --yes to do this without a terminal to ask in", 2)
    print(f"{what}? [y/N] ", end="", file=sys.stderr, flush=True)
    if sys.stdin.readline().strip().lower() not in ("y", "yes"):
        raise Failed("left as it was")


def secret_from(args, what: str) -> str:
    """A key from stdin (--key-stdin) or a hidden prompt: never an argument,
    which every other user of the machine can read in the process list."""
    if args.key_stdin:
        return sys.stdin.readline().strip()
    if sys.stdin.isatty():
        return getpass.getpass(f"{what} (hidden, Enter for none): ").strip()
    return ""


# ---- commands ---------------------------------------------------------------

def cmd_status(args) -> None:
    reply = call("GET", "/api/status")

    def human(r):
        print(f"Benchwarmers {r['version']} at {base_url()}")
        print(f"  gateway      {r['gateway']}")
        print(f"  data         {r['data']}")
        print(f"  models       {r['models']} chat, {r['decision_models']} decision, {r['retired']} retired")
        print(f"  collections  {r['collections']}")
        print(f"  endpoints    {r['endpoints']} (default {r['default_endpoint']})")
        print(f"  client       {r['client']}    scheduler {r['scheduler']}")
        print(f"  auth         {'admin token set' if r['auth'] else 'none (this machine only)'}"
              f"{', gateway keys required' if r['keys_required'] else ''}")
        q = r["onboarding"]
        if q["items"]:
            print(f"  onboarding   {'running' if q['running'] else 'finished'}, {q['items']} in the queue")
    out(args, reply, human)


def cmd_models(args) -> None:
    reply = call("GET", "/api/models")
    rows = [m for m in reply["models"] if args.all or not m["retired"]]
    if args.find:
        rows = [m for m in rows if args.find.lower() in (m["alias"] + " " + m["served"]).lower()]
    if args.role:
        rows = [m for m in rows if m["role"] == args.role]
    reply = {**reply, "models": rows}
    out(args, reply, lambda r: table(r["models"], ["alias", "role", "status", "connection", "endpoint", "served"]
                                     + (["retired"] if args.all else []), "no models"))


def cmd_model(args) -> None:
    a = args.action
    if a == "rename":
        reply = call("POST", "/api/model", {"alias": args.alias, "new_alias": args.new})
    elif a == "role":
        reply = call("POST", "/api/model", {"alias": args.alias, "new_role": args.role})
    elif a == "ad-hoc":
        reply = call("POST", "/api/model", {"alias": args.alias, "new_ad_hoc": args.state == "on"})
    elif a == "max-tokens":
        n = None if args.tokens in ("none", "") else int(args.tokens)
        reply = call("POST", "/api/model-options", {"alias": args.alias, "max_tokens": n})
    elif a == "retire":
        confirm(args, f"retire {args.alias} (it leaves every collection it is in; restore brings it back)")
        reply = call("POST", "/api/remove", {"alias": args.alias, "reason": args.reason or ""})
    elif a == "restore":
        reply = call("POST", "/api/restore", {"alias": args.alias})
    else:                                              # retry
        reply = call("POST", "/api/retry", {"alias": args.alias})
    out(args, reply, lambda r: done(r, f"{a}: {args.alias} done"))


def cmd_candidates(args) -> None:
    reply = call("GET", "/api/candidates")
    rows = reply.get("dismissed" if args.dismissed else "candidates") or []
    if args.endpoint:
        rows = [r for r in rows if r.get("endpoint") == args.endpoint]
    if args.find:
        rows = [r for r in rows if args.find.lower() in str(r.get("served", "")).lower()]
    if args.new:
        rows = [r for r in rows if r.get("new")]

    def human(_r):
        for e in reply.get("errors") or []:
            print(f"note: {e}", file=sys.stderr)
        table(rows, ["served", "endpoint", "group", "new"],
              "nothing dismissed" if args.dismissed else "nothing new to onboard")
    out(args, {**reply, "candidates": rows}, human)


def cmd_onboard(args) -> None:
    if args.served in (["status"], ["stop"], ["clear"]):
        verb = args.served[0]
        reply = call("GET", "/api/onboard-queue") if verb == "status" else \
            call("POST", f"/api/onboard-queue/{verb}", {})
    else:
        items = [{"served": s, "endpoint": args.endpoint or ""} for s in args.served]
        reply = call("POST", "/api/onboard-queue", {"items": items})
        if args.wait:
            while reply.get("running"):
                time.sleep(5)
                reply = call("GET", "/api/onboard-queue")
                now = [i for i in reply["items"] if i["state"] == "running"]
                if now and not args.json:
                    print(f"  {now[0]['served']}: step {now[0].get('step') or 0}/5", file=sys.stderr)

    def human(r):
        table([{**i, "as": i.get("alias")} for i in r.get("items") or []],
              ["served", "endpoint", "state", "as", "note"], "the onboarding queue is empty")
        if r.get("running"):
            print("running; `bench onboard status` to follow, `bench onboard stop` to stop after this one")
    out(args, reply, human)


def cmd_dismiss(args) -> None:
    # The picker's key: the bare served name on the default endpoint, served@endpoint elsewhere.
    listed = call("GET", "/api/candidates")
    known = {(r["served"], r["endpoint"]): r["key"]
             for r in (listed.get("candidates") or []) + (listed.get("dismissed") or [])}
    keys = []
    for s in args.served:
        hits = [k for (served, ep), k in known.items() if served == s and (not args.endpoint or ep == args.endpoint)]
        if len(hits) != 1:
            raise Failed(f"{s}: {'not among the candidates' if not hits else 'on several endpoints; add --endpoint'}")
        keys.append(hits[0])
    reply = call("POST", "/api/dismiss", {"served": keys, "undo": args.undo})
    out(args, reply, lambda r: done(r, ("brought back: " if args.undo else "dismissed: ") + ", ".join(keys)))


def cmd_collections(args) -> None:
    reply = call("GET", "/v1/models")
    rows = [{"name": m["id"].removeprefix(PREFIX), **m} for m in reply.get("data") or [] if ":" not in m["id"]]
    out(args, reply, lambda r: table(rows, ["name", "members", "explorers", "reply", "description"],
                                     "no collections; make one with `bench collection save`"))


def _collections_admin() -> dict:
    return {c["name"]: c for c in call("GET", "/api/collections").get("collections") or []}


def cmd_collection(args) -> None:
    name = args.name.removeprefix(PREFIX)
    if args.action == "show":
        have = _collections_admin()
        if name not in have:
            raise Failed(f"no collection {name!r}; have: {', '.join(sorted(have)) or 'none'}")
        return out(args, have[name], lambda c: print(json.dumps(c, indent=2, ensure_ascii=False)))
    if args.action == "rm":
        confirm(args, f"delete collection {name} (callers of bench/{name} start failing)")
        reply = call("POST", "/api/collection/remove", {"name": name})
        return out(args, reply, lambda r: done(r, f"deleted collection {name}"))
    # save: what is given replaces; what is not keeps the collection's own (a
    # new one starts empty), so one flag can be changed without retyping the rest.
    old = _collections_admin().get(name) or {}
    members = old.get("members") or []
    if args.members is not None:
        members = csv(args.members)
    members = [m for m in members if m not in csv(args.remove)] + \
              [m for m in csv(args.add) if m not in members]
    body = {"name": name, "members": members,
            "shape": args.shape if args.shape is not None else old.get("shape") or "",
            "description": args.description if args.description is not None else old.get("description") or "",
            "tags": csv(args.tags) if args.tags is not None else old.get("tags") or [],
            "reply": args.reply or old.get("reply") or "all",
            "merger": args.merger if args.merger is not None else old.get("merger") or ""}
    reply = call("POST", "/api/collection", body)
    out(args, reply, lambda r: done(r, f"saved collection {name}: {', '.join(members)}"))


def read_prompt(args) -> str:
    parts = [" ".join(args.prompt)] if args.prompt and args.prompt != ["-"] else []
    if args.prompt == ["-"] or (not args.prompt and not sys.stdin.isatty()):
        parts.append(sys.stdin.read())
    for f in args.file or []:
        p = Path(f)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            raise Failed(f"cannot read {f}: {e.strerror}") from None
        parts.append(f"`{p.name}`:\n```\n{text.rstrip()}\n```")
    prompt = "\n\n".join(x for x in parts if x.strip())
    if not prompt.strip():
        raise Failed("nothing to ask: give a prompt, -f FILE, or pipe one in", 2)
    return prompt


def cmd_ask(args) -> None:
    messages = [{"role": "user", "content": read_prompt(args)}]
    if args.system:
        messages.insert(0, {"role": "system", "content": args.system})
    name = args.collection if args.collection.startswith(PREFIX) else PREFIX + args.collection
    body = {"model": name, "messages": messages}
    if args.merged is not None:
        body["bench"] = {"reply": "merged" if args.merged else "all"}
    if args.with_:
        body["with"] = args.with_
    if not args.json and sys.stderr.isatty():
        print(f"asking {name}; a panel can take minutes...", file=sys.stderr)
    reply = http("POST", "/v1/chat/completions", body)

    def human(r):
        print(r["choices"][0]["message"]["content"])
        # The seats to stderr: `bench ask ... > review.md` keeps the answers only.
        seats = (r.get("bench") or {}).get("seats") or []
        if seats:
            with contextlib.redirect_stdout(sys.stderr):
                print("\nSeats (verdict each one you check: bench verdict <call> --useful|--not-useful):")
                table(seats, ["call", "seat", "alias", "status", "seconds"])
    out(args, reply, human)


def cmd_verdict(args) -> None:
    body = {"call": args.call, "useful": args.useful, "note": args.note or "",
            "real": args.real or [], "false": args.false or []}
    reply = call("POST", "/bench/verdict", body)
    out(args, reply, lambda r: done(r, f"recorded {args.call}: {'useful' if args.useful else 'not useful'},"
                                       f" {len(body['real'])} real, {len(body['false'])} false"))


def cmd_endpoints(args) -> None:
    reply = call("GET", "/api/endpoints")
    out(args, reply, lambda r: table(r["endpoints"], ["name", "base", "kind", "hub", "default", "key_set", "models"],
                                     "no endpoints; add one with `bench endpoint add`"))


def cmd_endpoint(args) -> None:
    a = args.action
    if a in ("add", "key", "test"):
        have = {e["name"]: e for e in call("GET", "/api/endpoints")["endpoints"]}
        base = args.base or (have.get(args.name) or {}).get("base") or ""
        if not base:
            raise Failed(f"give the base URL: bench endpoint {a} {args.name} --base https://...", 2)
        body = {"name": args.name, "base": base, "key": secret_from(args, f"API key for {args.name}"),
                "new": a == "add", "kind": args.kind or "", "hub": args.hub or "", "test": a == "test"}
        reply = call("POST", "/api/endpoint", body)
    elif a == "rm":
        confirm(args, f"remove endpoint {args.name}" + (" and retire every model on it" if args.with_models else ""))
        reply = call("POST", "/api/endpoint/remove", {"name": args.name, "with_models": args.with_models})
    else:                                              # default
        reply = call("POST", "/api/endpoint/default", {"name": args.name})
    out(args, reply, lambda r: done(r, f"{a}: {args.name} done"))


def cmd_keys(args) -> None:
    reply = call("GET", "/api/gateway-keys")

    def human(r):
        print("gateway keys are " + ("required" if r["required"] else "not required on this machine"))
        table(r["keys"], ["name", "hint", "collections", "rpm", "created", "last_used"], "no gateway keys")
    out(args, reply, human)


def cmd_key(args) -> None:
    if args.action == "revoke":
        confirm(args, f"revoke gateway key {args.name} (whatever uses it stops working)")
        reply = call("POST", "/api/gateway-key/remove", {"name": args.name})
        return out(args, reply, lambda r: done(r, f"revoked {args.name}"))
    reply = call("POST", "/api/gateway-key", {"name": args.name, "collections": csv(args.collections),
                                               "rpm": args.rpm})

    def human(r):
        print(r.get("key") or json.dumps(r))
        print(f"the key for {args.name}, shown this once: keep it now", file=sys.stderr)
    out(args, reply, human)


def cmd_settings(args) -> None:
    a = args.action
    if a == "gateway":
        reply = call("POST", "/api/gateway-settings", {"max_calls": args.max_calls,
                                                       "max_explorers": args.max_explorers})
    elif a == "client":
        reply = call("POST", "/api/client", {"client": args.client})
    else:                                              # scheduler
        status = call("GET", "/api/status")
        jobs = dict(status.get("scheduler_jobs") or {})
        for j in csv(args.on):
            jobs[j] = True
        for j in csv(args.off):
            jobs[j] = False
        reply = call("POST", "/api/scheduler", {"mode": args.mode or status["scheduler"], "jobs": jobs})
    out(args, reply, lambda r: done(r, f"saved {a} settings"))


# ---- the parser -------------------------------------------------------------

EXAMPLES = {
    "": """examples:
  bench status
  bench ask core "Where can this return a stale value?" -f cache.py
  git diff | bench ask core "Review this diff"
  bench verdict p20261022-101500-ab12.member1 --useful --real "the race in refresh()"
  bench models --find mercury
  bench --url http://bench.lan:8122 collections

Starting the server: bench serve | gateway | init | mcp (benchwarmers --help).
Where another program is called bench (the Frappe framework's), type benchwarmers.""",
    "ask": """examples:
  bench ask core "Is this regex safe on untrusted input?" -f validate.py
  git diff main | bench ask reviewers "Where does this change break callers?"
  bench ask core --merged "Summarise the disagreements" < notes.md
  bench ask core --with mercury-3 "..."      seat a model you want to try

The answers go to stdout and the seats' table to stderr, so
  bench ask core "..." > review.md
keeps only the answers. Then judge each seat you checked:
  bench verdict <call> --useful|--not-useful""",
    "verdict": """examples:
  bench verdict p20261022-101500-ab12.member1 --useful --note "found the real bug" \\
      --real "refresh() races with evict()" --false "the query is injectable\"""",
    "model": """examples:
  bench model role mercury-3 code
  bench model rename deepseek-v4.1-flash deepseek-flash
  bench model max-tokens inkling-free 8000        (none: the endpoint's own)
  bench model retire old-model --reason "deprecated by its provider"
  bench model restore old-model""",
    "onboard": """examples:
  bench candidates --new
  bench onboard minimax/minimax-m3.1-flash:free --wait
  bench onboard a/one:free b/two:free --endpoint openrouter
  bench onboard status | stop | clear""",
    "collection": """examples:
  bench collection save reviewers --members a,b,c --description "adversarial review" --tags review
  bench collection save reviewers --add d --remove a      (the rest stays as it was)
  bench collection save reviewers --reply merged --merger a
  bench collection show reviewers
  bench collection rm reviewers --yes""",
    "endpoint": """examples:
  bench endpoint add openrouter --base https://openrouter.ai/api/v1      (asks for the key, hidden)
  printf %s "$KEY" | bench endpoint key openrouter --key-stdin
  bench endpoint test groq --base https://api.groq.com/openai/v1
  bench endpoint default openwebui
  bench endpoint rm old --with-models --yes""",
    "key": """examples:
  bench key create open-webui --collections core,fast --rpm 30      (printed once: keep it)
  bench key revoke open-webui --yes""",
    "settings": """examples:
  bench settings gateway --max-calls 6 --max-explorers 2
  bench settings client builtin
  bench settings scheduler run --off perf,suite""",
}


def parser() -> argparse.ArgumentParser:
    # On every command as well as before it (bench --json status, bench status --json).
    # SUPPRESS on the copies: a sub-command's default must not overwrite one given first.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="print the server's reply as JSON")
    common.add_argument("--url", default=argparse.SUPPRESS, metavar="URL",
                        help="the server (default: BENCHWARMERS_URL, else http://127.0.0.1:8122)")
    fmt = argparse.RawDescriptionHelpFormatter
    p = argparse.ArgumentParser(prog="bench", formatter_class=fmt, epilog=EXAMPLES[""],
                                description="Benchwarmers from the command line: a client of a running server."
                                " Tokens come from BENCHWARMERS_ADMIN_TOKEN and BENCHWARMERS_KEY, never"
                                " an argument.")
    p.add_argument("--json", action="store_true", default=False, help="print the server's reply as JSON")
    p.add_argument("--url", default=None, metavar="URL",
                   help="the server (default: BENCHWARMERS_URL, else http://127.0.0.1:8122)")
    p.add_argument("--version", action="version", version=f"bench {VERSION}")
    sub = p.add_subparsers(dest="command", required=True, metavar="command")

    def cmd(name, fn, help_):
        s = sub.add_parser(name, help=help_, description=help_, parents=[common], formatter_class=fmt,
                           epilog=EXAMPLES.get(name))
        s.set_defaults(fn=fn)
        return s

    def actions(s, fn):
        a = s.add_subparsers(dest="action", required=True, metavar="action")

        def act(name, help_, destructive=False):
            x = a.add_parser(name, help=help_, description=help_, parents=[common])
            x.set_defaults(fn=fn)
            if destructive:
                x.add_argument("-y", "--yes", action="store_true", help="do it without asking")
            return x
        return act

    cmd("status", cmd_status, "what is running, and where")

    s = cmd("ask", cmd_ask, "ask a collection: every seat answers")
    s.add_argument("collection", help="a collection, e.g. core (bench collections)")
    s.add_argument("prompt", nargs="*", help="the question; - or a pipe reads stdin")
    s.add_argument("-f", "--file", action="append", help="add a file's text to the prompt (repeatable)")
    s.add_argument("--system", help="a system message for every seat")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--merged", dest="merged", action="store_true", default=None, help="one reconciled answer")
    g.add_argument("--all", dest="merged", action="store_false", help="every answer (the usual)")
    s.add_argument("--with", dest="with_", action="append", metavar="ALIAS", help="seat a model as an explorer")

    s = cmd("verdict", cmd_verdict, "judge one seat's answer by its call id")
    s.add_argument("call")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--useful", dest="useful", action="store_true")
    g.add_argument("--not-useful", dest="useful", action="store_false")
    s.add_argument("--note")
    s.add_argument("--real", action="append", metavar="CLAIM", help="a claim it got right (repeatable)")
    s.add_argument("--false", action="append", metavar="CLAIM", help="a claim that is untrue (repeatable)")

    s = cmd("models", cmd_models, "the models on the bench")
    s.add_argument("--all", action="store_true", help="retired ones too")
    s.add_argument("--find", help="alias or served name contains this")
    s.add_argument("--role", help="only this role")

    act = actions(cmd("model", cmd_model, "change one model"), cmd_model)
    m = act("rename", "give it a new alias")
    m.add_argument("alias"), m.add_argument("new")
    m = act("role", "what it is for (general, code, review, ...)")
    m.add_argument("alias"), m.add_argument("role")
    m = act("ad-hoc", "only when asked for by name")
    m.add_argument("alias"), m.add_argument("state", choices=["on", "off"])
    m = act("max-tokens", "the most an answer may use; none for the endpoint's")
    m.add_argument("alias"), m.add_argument("tokens")
    m = act("retire", "take it off the bench and out of its collections (restore undoes it)", destructive=True)
    m.add_argument("alias"), m.add_argument("--reason")
    act("restore", "undo a retire").add_argument("alias")
    act("retry", "one fresh call to a model on a retiring alert").add_argument("alias")

    s = cmd("candidates", cmd_candidates, "what the endpoints serve that could be onboarded")
    s.add_argument("--endpoint"), s.add_argument("--find")
    s.add_argument("--new", action="store_true", help="only ones new since the daily check")
    s.add_argument("--dismissed", action="store_true", help="the dismissed ones instead")

    s = cmd("onboard", cmd_onboard, "onboard models (queued, one at a time), or: onboard status|stop|clear")
    s.add_argument("served", nargs="+", help="served names, as `bench candidates` lists them")
    s.add_argument("--endpoint", help="the endpoint they are on; the default if omitted")
    s.add_argument("--wait", action="store_true", help="stay until the queue finishes")

    s = cmd("dismiss", cmd_dismiss, "stop offering served models as candidates")
    s.add_argument("served", nargs="+"), s.add_argument("--endpoint"), s.add_argument("--undo", action="store_true")

    cmd("collections", cmd_collections, "the collections a caller can ask")

    act = actions(cmd("collection", cmd_collection, "show, save or delete one collection"), cmd_collection)
    act("show", "everything about one").add_argument("name")
    act("rm", "delete one", destructive=True).add_argument("name")
    c = act("save", "create one, or change what is given")
    c.add_argument("name")
    c.add_argument("--members", help="comma-separated aliases: the whole list")
    c.add_argument("--add", help="comma-separated aliases to add")
    c.add_argument("--remove", help="comma-separated aliases to take out")
    c.add_argument("--shape", help="the task shape explorers are picked for; empty for automatic")
    c.add_argument("--description", help="what it is for: callers read it to choose")
    c.add_argument("--tags", help="comma-separated")
    c.add_argument("--reply", choices=["all", "merged"])
    c.add_argument("--merger", help="the alias that merges, for --reply merged")

    cmd("endpoints", cmd_endpoints, "where models are served from")

    act = actions(cmd("endpoint", cmd_endpoint, "add, test, re-key, remove or make default an endpoint"),
                  cmd_endpoint)
    for name, help_ in (("add", "add one (tested first)"), ("test", "test without saving"),
                        ("key", "change one's key")):
        e = act(name, help_)
        e.add_argument("name"), e.add_argument("--base", help="its base URL, e.g. https://openrouter.ai/api/v1")
        e.add_argument("--kind"), e.add_argument("--hub")
        e.add_argument("--key-stdin", action="store_true", help="read the key from stdin; else a hidden prompt")
    e = act("rm", "remove one", destructive=True)
    e.add_argument("name"), e.add_argument("--with-models", action="store_true", help="retire its models too")
    act("default", "make one the default").add_argument("name")

    cmd("keys", cmd_keys, "the gateway keys (names and hints, never a key)")

    act = actions(cmd("key", cmd_key, "make or revoke a gateway key"), cmd_key)
    k = act("create", "a new key, printed once")
    k.add_argument("name"), k.add_argument("--collections", help="comma-separated; all if omitted")
    k.add_argument("--rpm", type=int, help="chat completions a minute; no cap if omitted")
    act("revoke", "revoke one by name: whatever uses it stops working", destructive=True).add_argument("name")

    act = actions(cmd("settings", cmd_settings, "gateway limits, the model client, the scheduler"), cmd_settings)
    t = act("gateway", "how many calls a panel may make")
    t.add_argument("--max-calls", type=int, required=True), t.add_argument("--max-explorers", type=int, required=True)
    act("client", "llm or builtin").add_argument("client")
    t = act("scheduler", "its mode, and jobs on or off")
    t.add_argument("mode", nargs="?", choices=["off", "log-only", "run"])
    t.add_argument("--on", help="comma-separated jobs to run"), t.add_argument("--off", help="comma-separated jobs to skip")
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    args = parser().parse_args(argv)
    OVERRIDES.clear()
    if args.url:
        OVERRIDES["BENCHWARMERS_URL"] = args.url
    try:
        args.fn(args)
    except Failed as e:
        print(f"bench: {e}", file=sys.stderr)
        return e.code
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
