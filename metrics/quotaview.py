"""The Quota page's tiles: per provider, what is left, when it resets, who rests.

    python metrics/quotaview.py              # every tile, as the page gets it
    python metrics/quotaview.py --selftest   # the parsers and the tile shapes; no calls

One tile per connection tag a bench model arrives through
(route.full_tag_of: `cli-proxy-api/codex`, `openrouter`, `nvidia-api`).
A tile takes the shape of what its provider says:

    groups  windows per model group, several each (CLI Proxy API's OAuth
            credentials: Gemini 5 hours and week), or per model from the
            rate-limit headers the built-in client saw (metrics/quota.py)
    pool    one allowance the models share: OpenRouter's free requests a
            day, a bar per key, counted from the usage ledger
    models  nothing stated: the provider's last day of calls, all together

and any of them its day and month caps (caps): yours, else a limit its replies
state with nothing left said, counted from the ledger. A model is named only
where a limit is its own. Nothing comes from a provider's docs: they change
unseen.

ONE REFRESH, THREE WAYS (how_refreshed). Three providers can be asked what is
left for free, and are:

    OpenRouter      GET /key on a direct OpenRouter endpoint: free tier or
                    not, and its credits (the free requests' limit comes
                    from its 429s, never its docs)
    xKiro           GET /v1/usage: free-model tokens left today (to midnight
                    UTC) and a plan's spend windows; the key is a direct
                    endpoint's, else its Open WebUI connection's (_key_for)
    CLI Proxy API   its management route `requests/api-call`, which calls the
                    credential's own quota endpoint with the credential's
                    token, as its console does (Google's retrieveUserQuotaSummary,
                    ChatGPT's wham/usage). Needs CLI Proxy API on the
                    Endpoints page as a hub with its management key
                    (endpoints.admin_key, as metrics/cliproxy.py); the proxy is
                    on the LAN and calls only providers the credential uses.

Where a provider has sent limit headers before (Groq; Routeway, through Open
WebUI too, since the bench streams), Refresh is `check`: a 16-token call to
one of its models, whose reply headers quota.py records. Everywhere else it
re-reads the bench's own records, since a call would say nothing.

Live reads are kept in runs/quota-live.json, newest per tag.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "metrics"), str(ROOT / "suite")]
import locked  # noqa: E402
from paths import DATA  # noqa: E402

LIVE = DATA / "runs" / "quota-live.json"
UTC = dt.timezone.utc
CPA_PREFIXES = ("/v8/management", "/v0/management")   # the console's, then the older one
# The headers CLI Proxy API's own console sends to each quota endpoint
# ($TOKEN$ is replaced by the proxy with the credential's token).
AG_URLS = ("https://daily-cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
           "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary")
# Google answers 403 without the Antigravity client's User-Agent.
AG_HEAD = {"Authorization": "Bearer $TOKEN$", "Content-Type": "application/json",
           "User-Agent": "antigravity/hub/2.23.0 darwin/arm64"}
CODEX_URL = "https://chatgpt.com/backend-api/wham/usage"
CODEX_HEAD = {"Authorization": "Bearer $TOKEN$", "Content-Type": "application/json",
              "User-Agent": "codex-tui/0.149.1"}
WINDOW_NAMES = {"5h": "5 hours", "weekly": "week", "daily": "day", "monthly": "month"}
# Free to ask: no tokens, outside the rate limits (docs.xkiro.com/api/usage, read 2026-10-29).
XKIRO_HOST = "api.xkiro.com"
XKIRO_USAGE = "https://api.xkiro.com/v1/usage"
# A day or month cap is drawn counted from the ledger, as pacing.py counts:
# yours from Endpoints (enforced), else one a provider states in its replies
# without saying what is left (Cohere's x-endpoint-monthly-call-limit; shown,
# never enforced). Never one from a provider's docs: they change unseen.
COUNTED = {"rpd": ("requests", "day"), "tpd": ("tokens", "day"), "rpmo": ("requests", "month")}


def _iso(t: float | None) -> str | None:
    return None if t is None else dt.datetime.fromtimestamp(t, UTC).isoformat(timespec="seconds")


def _ts(iso) -> float | None:
    try:
        return dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp() if iso else None
    except ValueError:
        return None


def _seconds_name(s) -> str:
    """A window's length as a word: 18000 -> "5 hours", 604800 -> "week"."""
    s = int(s or 0)
    return {3600: "hour", 18000: "5 hours", 86400: "day", 604800: "week", 2592000: "month"}.get(
        s, f"{round(s / 3600)} hours" if s else "window")


def _utc_midnight(now: float) -> float:
    d = dt.datetime.fromtimestamp(now, UTC)
    return dt.datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp()


# ---- parsers: a provider's reply -> groups of windows -----------------------

def antigravity_groups(body: dict) -> list[dict]:
    """retrieveUserQuotaSummary -> [{label, windows: [{label, left, reset_at}]}],
    the shorter window first."""
    out = []
    for g in body.get("groups") or []:
        wins = [{"label": WINDOW_NAMES.get(b.get("window"), str(b.get("window") or "window")),
                 "left": round(float(b.get("remainingFraction", 0)) * 100),
                 "reset_at": b.get("resetTime")}
                for b in g.get("buckets") or [] if isinstance(b, dict)]
        wins.sort(key=lambda w: ["5 hours", "day", "week", "month"].index(w["label"])
                  if w["label"] in ("5 hours", "day", "week", "month") else 9)
        if wins:
            out.append({"label": str(g.get("displayName") or ""), "windows": wins})
    return out


def codex_groups(body: dict) -> list[dict]:
    """wham/usage -> one group with its primary and secondary windows."""
    rl = body.get("rate_limit") or {}
    wins = [{"label": _seconds_name(w.get("limit_window_seconds")),
             "left": max(0, 100 - round(float(w.get("used_percent") or 0))),
             "reset_at": _iso(w["reset_at"]) if w.get("reset_at") else None}
            for w in (rl.get("primary_window"), rl.get("secondary_window")) if isinstance(w, dict)]
    return [{"label": "", "windows": wins}] if wins else []


def xkiro_groups(body: dict, now: float) -> list[dict]:
    """/v1/usage -> the free models' tokens today (to midnight UTC), then the
    plan's spend windows (USD, as strings), where there is a plan."""
    out, ft = [], body.get("free_tokens") or {}
    if ft.get("limit_per_day"):
        lim, rem = int(ft["limit_per_day"]), int(ft.get("remaining") or 0)
        out.append({"label": "free models", "windows": [
            {"label": "tokens · day", "left": round(100 * rem / lim), "remaining": rem, "of": lim,
             "reset_at": _iso(_utc_midnight(now) + 86400)}]})
    wins = [{"label": _seconds_name(w.get("window_sec")) + " · spend",
             "left": round(100 * float(w.get("remaining_usd") or 0) / float(w["cap_usd"])),
             "reset_at": _iso(now + float(w.get("resets_in_sec") or 0))}
            for w in body.get("windows") or [] if isinstance(w, dict) and float(w.get("cap_usd") or 0) > 0]
    if wins:
        out.append({"label": "plan", "windows": wins})
    return out


# ---- live reads --------------------------------------------------------------

def _get_json(url: str, headers: dict, data: bytes | None = None, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, data=data, headers={"Accept": "application/json", **headers},
                                 method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def _openrouter_endpoint() -> str | None:
    """A direct OpenRouter endpoint with a key: the one /key can be asked on."""
    import endpoints
    for name, entry in endpoints.load().items():
        host = urllib.parse.urlsplit(entry.get("base", "")).hostname or ""
        if host.endswith("openrouter.ai") and not endpoints.hub(name) and endpoints.key(name):
            return name
    return None


def read_openrouter() -> dict:
    import endpoints
    name = _openrouter_endpoint()
    if not name:
        raise ValueError("no direct OpenRouter endpoint with a key to ask")
    data = _get_json(endpoints.base(name) + "/key", {"Authorization": f"Bearer {endpoints.key(name)}"}).get("data") or {}
    return {"endpoint": name, "free_tier": bool(data.get("is_free_tier")),
            "limit": data.get("limit"), "remaining": data.get("limit_remaining"), "usage": data.get("usage")}


def _key_for(host: str) -> str:
    """A key for `host`: a direct endpoint's, else the Open WebUI connection's
    (read with the hub's admin key, as owui.py reads its connections)."""
    import endpoints
    for name, entry in endpoints.load().items():
        if (urllib.parse.urlsplit(entry.get("base", "")).hostname or "") == host and not endpoints.hub(name):
            if endpoints.key(name):
                return endpoints.key(name)
    import owui
    cfg = owui._call("/openai/config")
    for url, key in zip(cfg.get("OPENAI_API_BASE_URLS") or [], cfg.get("OPENAI_API_KEYS") or []):
        if (urllib.parse.urlsplit(url).hostname or "") == host and key:
            return key
    raise ValueError(f"no key for {host}: neither an endpoint nor an Open WebUI connection has one")


def read_xkiro() -> dict:
    """Only the figures are kept: the reply also names the account's owner."""
    try:                                               # Cloudflare refuses Python's own User-Agent (1010)
        body = _get_json(XKIRO_USAGE, {"Authorization": f"Bearer {_key_for(XKIRO_HOST)}", "User-Agent": "benchwarmers"})
    except urllib.error.HTTPError as exc:
        raise ValueError(f"xKiro answered {exc.code} on /v1/usage") from None
    return {"groups": xkiro_groups(body, time.time()), "plan": str(body.get("plan") or ""), "notes": []}


def _cpa_hub() -> tuple[str, str]:
    """(management base, key) of the CLI Proxy API hub; ValueError when there is none."""
    import endpoints
    name = endpoints.find_hub("cliproxy")
    if not name:
        raise ValueError("add CLI Proxy API on the Endpoints page (kind hub, CLI Proxy API) with its"
                         " management key to read these quotas live")
    secret = endpoints.admin_key(name, fallback=False)
    if not secret:
        raise ValueError(f"CLI Proxy API's management key is not stored: Change key on {name}")
    return endpoints.base(name).removesuffix("/v1"), secret


def _cpa(base: str, secret: str, path: str, body: dict | None = None) -> dict:
    """One management call; the console's prefix first, the older on a 404.
    A 401 is never retried: the proxy bans an address after 5 refusals."""
    head = {"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}
    for prefix in CPA_PREFIXES:
        try:
            return _get_json(base + prefix + path, head, None if body is None else json.dumps(body).encode())
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and prefix != CPA_PREFIXES[-1]:
                continue
            if exc.code == 401:
                raise ValueError("CLI Proxy API refused the management key; check it before trying again"
                                 " (5 refusals ban this address for 30 minutes)") from None
            raise ValueError(f"CLI Proxy API answered {exc.code} on {path}") from None
    raise ValueError("CLI Proxy API's management API is off (every route 404s without a secret key)")


def _api_call(base, secret, auth_index, method, url, header, data=None) -> dict:
    """The credential's own request, made by the proxy; the reply's JSON body."""
    reply = _cpa(base, secret, "/requests/api-call",
                 {"auth_index": auth_index, "method": method, "url": url, "header": header,
                  **({"data": data} if data is not None else {})})
    if not 200 <= int(reply.get("status_code") or 0) < 300:
        raise ValueError(f"{urllib.parse.urlsplit(url).hostname} answered {reply.get('status_code')}")
    body = reply.get("body")
    return json.loads(body) if isinstance(body, str) and body.strip() else (body or {})


def read_cliproxy(tag: str, sibling_vendors: set[str]) -> dict:
    """Every enabled credential behind `tag`: `cli-proxy-api/codex` is
    the codex ones, bare `cli-proxy-api` those no sibling tag names."""
    base, secret = _cpa_hub()
    vendor = tag.split("/", 1)[1] if "/" in tag else ""
    creds = [c for c in _cpa(base, secret, "/credentials").get("files") or []
             if not c.get("disabled") and (c.get("provider") == vendor if vendor
                                           else c.get("provider") not in sibling_vendors)]
    groups, names, plan, notes = [], [], "", []
    for c in creds:
        prov, idx = c.get("provider", ""), c.get("auth_index")
        try:
            if prov == "antigravity":
                body = None
                for url in AG_URLS:
                    try:
                        body = _api_call(base, secret, idx, "POST", url, AG_HEAD,
                                         json.dumps({"project": c.get("project_id") or ""}))
                        break
                    except ValueError:
                        continue
                got = antigravity_groups(body or {})
            elif prov == "codex":
                head = dict(CODEX_HEAD)
                acct = (c.get("id_token") or {}).get("chatgpt_account_id")
                if acct:
                    head["Chatgpt-Account-Id"] = acct
                body = _api_call(base, secret, idx, "GET", CODEX_URL, head)
                got = codex_groups(body)
                plan = str(body.get("plan_type") or "")
            else:
                notes.append(f"no quota reader for {prov}")
                continue
        except ValueError as exc:
            notes.append(f"{prov}: {exc}")
            continue
        if len(creds) > 1:
            for g in got:
                g["label"] = f"{prov} · {g['label']}".rstrip(" ·")
        groups += got
        names.append(prov)
    if not groups and notes:
        raise ValueError("; ".join(notes))
    return {"groups": groups, "name": ", ".join(dict.fromkeys(names)), "plan": plan, "notes": notes}


def refresh(tag: str) -> dict:
    """The tile's one Refresh, which picks its own way (how_refreshed): ask the
    provider when it can be asked for free; else, where it states limits in its
    replies, one 16-token call (check); else the bench's own records.
    {"ok", "how": "live"|"check"|"records", "note"}."""
    how = how_refreshed(tag)
    if how == "check":
        r = check(tag)
        if r.get("ok"):
            return dict(r, how="check")
        return {"ok": True, "how": "records", "note": "; ".join(r.get("errors") or []) + "; shown from the bench's own records"}
    if how == "records":
        return {"ok": True, "how": "records",
                "note": "this provider states no limits to ask about: shown from the bench's own records"}
    if tag == "openrouter":
        got = read_openrouter()
    elif _host(tag) == XKIRO_HOST:
        got = read_xkiro()
    else:
        vendors = {t.split("/", 1)[1] for t in _tags() if t.startswith("cli-proxy-api/")}
        got = read_cliproxy(tag, vendors)
    got["at"] = _iso(time.time())
    locked.update(LIVE, lambda d: d.update({tag: got}), ensure_ascii=False)
    return {"ok": True, "how": "live", "note": "; ".join(got.get("notes") or [])}


def about(tag: str) -> dict:
    """How a tile's provider is reached, for its Models popup: {connection,
    routes: [{via, url}]}, a direct endpoint by that name and each Open WebUI
    connection named so (scripts/provider_tags.py's names). URLs only:
    /openai/config returns Open WebUI's keys too, never read here."""
    import endpoints
    host, routes = tag.split("/")[0], []
    if host in endpoints.load() and not endpoints.hub(host):
        routes.append({"via": f"its own endpoint, {host}", "url": endpoints.base(host)})
    hub = endpoints.find_hub("openwebui")
    if hub:
        sys.path.insert(0, str(ROOT / "scripts"))
        import provider_tags
        urls = provider_tags.get(hub, "/openai/config").get("OPENAI_API_BASE_URLS", [])
        routes += [{"via": f"an Open WebUI connection on {hub}", "url": url}
                   for name, url in zip(provider_tags.connection_names(hub), urls) if name == host]
    return {"connection": tag, "routes": routes}


def _host(tag: str) -> str:
    """The connection a tile's models are called through (route.connection_of)."""
    import route
    aliases = _tags().get(tag) or []
    return route.connection_of(aliases[0]) if aliases else tag


def how_refreshed(tag: str) -> str:
    """"live" (a free read), "check" (one short call whose headers say what is
    left) or "records" (nothing to ask: the bench's own records)."""
    if (tag.split("/")[0] == "cli-proxy-api" or tag == "openrouter" and _openrouter_endpoint()
            or _host(tag) == XKIRO_HOST):
        return "live"
    return "check" if _headers_seen(tag) else "records"


def check(tag: str) -> dict:
    """One 16-token call to a model on `tag` that is free to call; the
    built-in client records what its reply headers say (quota.observe)."""
    import client
    import learn
    resting = learn.cooldowns()
    aliases = [a for a in _tags().get(tag, []) if a not in resting]
    if not aliases:
        return {"ok": False, "errors": ["every model on it is resting; nothing was sent"]}
    if not _headers_seen(tag):
        return {"ok": False, "errors": ["this provider has never sent limit headers, so a call would"
                                        " tell nothing; nothing was sent"]}
    res = client.chat(aliases[0], "Hi", timeout=60, body={"max_tokens": 16}, purpose="quota check", retries=0)
    if res.get("error"):
        return {"ok": False, "errors": [f"{aliases[0]}: {res['error'][:200]}"]}
    return {"ok": True, "note": f"asked {aliases[0]}: one request"}


# ---- tiles -------------------------------------------------------------------

def _bench() -> list[str]:
    from run import _registered_names
    gone = locked.read_json(DATA / "config" / "retired.json")
    return [a for a in _registered_names() if a not in gone]


def _tags() -> dict[str, list[str]]:
    import route
    out: dict[str, list[str]] = {}
    for a in _bench():
        tag = route.full_tag_of(a)
        if tag != "?":
            out.setdefault(tag, []).append(a)
    return out


def _headers_seen(tag: str) -> bool:
    """Has this tag's provider ever stated a limit in its headers (kept a day past reset)?"""
    import quota
    import route
    aliases = set(_tags().get(tag, []))
    host = route.connection_of(next(iter(aliases), "")) if aliases else tag
    recs = locked.read_json(quota.FILE).get(host) or {}
    return any(str(r.get("source", "")).startswith("headers") and r.get("limit")
               for a, by in recs.items() if a in aliases and isinstance(by, dict)
               for r in by.values() if isinstance(r, dict))


def _model_rows(aliases, rows, resting, limits) -> list[dict]:
    out = []
    for a in sorted(aliases):
        mine = [r for r in rows if r.get("alias") == a]
        ok = [r for r in mine if r.get("status") == "ok"]
        out.append({"alias": a, "ok": len(ok), "failed": len(mine) - len(ok),
                    "last_ok": _iso(max(r["t"] for r in ok)) if ok else None,
                    "last": _iso(max(r["t"] for r in mine)) if mine else None,
                    "until": (resting.get(a) or {}).get("until"),
                    "limit": limits.get(a, "")})
    return out


def _limit_text(lim: dict) -> str:
    words = {"rpm": "requests a minute", "rpd": "requests a day", "tpm": "tokens a minute", "tpd": "tokens a day",
             "rpmo": "requests a calendar month"}
    return ", ".join(f"{v:,} {words[k]}" for k, v in lim.items() if k in words and v)


def caps(host: str, entered: dict, stated: dict, day_rows: list, month_rows, now: float) -> list[dict]:
    """The connection's day and month caps as groups of windows, each counted
    from the bench's ledger since midnight UTC or the month's start (what a
    provider's calendar counts): yours, else `stated` ({axis: limit}, what its
    replies say with nothing left given). `month_rows` is called only when a
    month cap needs it."""
    mine = {k: (v, "your cap", "Set on Endpoints, Rate limits per connection; the bench keeps to it.")
            for k, v in (entered.get(host) or {}).items() if k in COUNTED and v}
    for axis, n in stated.items():
        mine.setdefault(axis, (n, "its replies", "The provider's replies state this limit but not what is"
                               " left. Shown, not enforced: set it on Endpoints to have the bench keep to it."))
    by: dict[str, list] = {}
    about: dict[str, str] = {}
    for axis, (n, source, detail) in mine.items():
        about[source] = detail
        metric, window = COUNTED[axis]
        start, end = ((_utc_midnight(now), _utc_midnight(now) + 86400) if window == "day"
                      else (__import__("ledger").month_start(now), __import__("ledger").next_month(now)))
        rows = [r for r in (day_rows if window == "day" else month_rows())
                if r.get("connection") == host and r["t"] >= start]
        used = sum((r.get("attempts") or 0) if metric == "requests"
                   else (r.get("tokens_in") or 0) + (r.get("tokens_out") or 0) for r in rows)
        left = max(0, n - used)
        by.setdefault(source, []).append({"label": f"{metric} · {window}", "left": round(100 * left / n),
                                          "remaining": left, "of": n, "reset_at": _iso(end)})
    return [{"label": f"{source} · counted by the bench", "title": about[source] + " Counted from the bench's"
             " own calls: calls made elsewhere with the same key are not in it.", "windows": w}
            for source, w in by.items()]


def tiles(now: float | None = None) -> list[dict]:
    """Every tile, in the saved order (out first, then soonest reset, for new ones)."""
    import catalog
    import learn
    import ledger
    import quota
    import route
    now = time.time() if now is None else now
    tags, live = _tags(), locked.read_json(LIVE)
    resting, learned = learn.cooldowns(), learn.learned()
    day_rows = list(ledger.rows(now - 86400))
    utc_rows = [r for r in day_rows if r["t"] >= _utc_midnight(now)]
    entered = catalog.connection_limits()
    regs = __import__("endpoints").registrations()
    month: list = []                                   # this month's ledger, read once if a month cap asks

    def month_rows() -> list:
        if not month:
            month.append(list(ledger.rows(ledger.month_start(now))))
        return month[0]
    out = []
    for tag, aliases in tags.items():
        host = route.connection_of(aliases[0])
        mine = set(aliases)
        limits = {a: _limit_text({k: ev.get("value") for k, ev in ((learned.get(a) or {}).get("limits") or {}).items()})
                  for a in aliases}
        limits = {a: t for a, t in limits.items() if t}
        rows = [r for r in day_rows if r.get("alias") in mine]
        # What the replies' headers said, kept a day past each reset (quota.py).
        # A limit with nothing left said (Cohere's month) is a cap to count.
        seen_recs = [r for a, by in (locked.read_json(quota.FILE).get(host) or {}).items()
                     if a in mine and isinstance(by, dict) for r in by.values()
                     if isinstance(r, dict) and r.get("limit") and r.get("window") != "retry-after"]
        stated: dict[str, int] = {}
        for r in seen_recs:
            if r.get("remaining") is None and r["metric"] == "requests" and r.get("window") in ("day", "month"):
                axis = "rpd" if r["window"] == "day" else "rpmo"
                stated[axis] = max(stated.get(axis, 0), r["limit"])
        t = {"id": tag, "tag": tag, "host": host, "bench": len(aliases), "kind": "models", "groups": [],
             "pool": [], "notes": [],          # a day or month cap is a meter (caps), a minute one is said
             "conn_limit": _limit_text({k: v for k, v in (entered.get(host) or {}).items() if k in ("rpm", "tpm")}),
             "caps": caps(host, entered, stated, day_rows, month_rows, now),
             "models": _model_rows(aliases, rows, resting, limits),
             "refresh": how_refreshed(tag), "plan": "", "name": ""}
        got = live.get(tag) or {}
        if tag.split("/")[0] == "cli-proxy-api" or host == XKIRO_HOST:
            if got.get("groups"):
                t.update(kind="groups", groups=got["groups"], name=got.get("name", ""), plan=got.get("plan", ""))
        elif tag == "openrouter":
            t["kind"] = "pool"
            t["pool"] = _openrouter_pool(aliases, utc_rows, regs, got, learned, now)
        if t["kind"] == "models":                       # the headers' windows, as last seen
            # A window past its reset has filled again: its limit is known, all
            # of it left, the next reset not.
            recs = [r if (_ts(r.get("reset_at")) or 0) > now else dict(r, remaining=r["limit"], reset_at=None)
                    for r in seen_recs if r.get("remaining") is not None]
            by: dict[str, list] = {}
            for r in recs:
                by.setdefault(r["alias"], []).append(
                    {"label": r["metric"] + ("" if r["window"] == "unknown" else " · " + r["window"]),
                     "left": round(100 * (r.get("remaining") or 0) / r["limit"]),
                     "remaining": r.get("remaining"), "of": r["limit"], "reset_at": r.get("reset_at")})
            if by:
                t["kind"] = "groups"
                t["groups"] = [{"label": a if len(by) > 1 else "", "windows": w} for a, w in sorted(by.items())]
            seen = [_ts(r.get("observed_at")) or 0 for r in recs]
            if seen:
                got = {"at": _iso(max(seen)), "how": "seen"}
        # Where the tile's figures come from, said on it: exact (an endpoint,
        # the replies' headers) or a stated limit counted by the bench.
        t["source"] = ((["its reply headers" if got.get("how") == "seen" else "its quota endpoint"] if t["groups"] else [])
                       + (["its stated daily limit, counted by the bench"] if t["pool"] else [])
                       + [g["label"] for g in t["caps"]]) or ["nothing stated"]
        last = max((r["t"] for r in rows if r.get("status") == "ok"), default=None)
        t["fresh"] = ({"how": "live", "at": got["at"]} if got.get("at") and got.get("how") != "seen"
                      else {"how": "seen", "at": got["at"]} if got.get("at")
                      else {"how": "answered", "at": _iso(last)} if last else {"how": "none", "at": None})
        t["live"] = sum(1 for a in aliases if a not in resting)
        windows = [w for g in t["groups"] + t["caps"] for w in g["windows"]]
        resets = [_ts(w.get("reset_at")) for w in windows] + [_ts(p.get("reset_at")) for p in t["pool"]]
        resets = [r for r in resets if r and r > now]
        t["next_reset"] = _iso(min(resets)) if resets else None
        t["out"] = (t["live"] == 0 or any(w["left"] <= 0 for w in windows)
                    or any(p.get("left") == 0 for p in t["pool"]))
        t["notes"] = list(got.get("notes") or [])
        out.append(t)
    return ordered(out)


def _openrouter_pool(aliases, utc_rows, regs, got, learned, now) -> list[dict]:
    """A bar per OpenRouter key the bench uses: free-model requests since
    00:00 UTC, the bench's own count, against the day's limit OpenRouter
    stated (a 429's), else none."""
    import endpoints
    import quota
    seen_429 = locked.read_json(quota.FILE).get("openrouter") or {}
    by_ep: dict[str, list[str]] = {}
    for a in aliases:
        by_ep.setdefault(endpoints.endpoint_name(regs.get(a) or {}) or "?", []).append(a)
    bars = []
    for ep, eps in sorted(by_ep.items()):
        used = sum(r.get("attempts") or 1 for r in utc_rows
                   if r.get("alias") in eps and str(r.get("served", "")).endswith(":free"))
        # Only what OpenRouter stated (learned, else the last 429's, kept a day
        # past reset): its docs' 50-or-1,000 by tier changes unseen, and the
        # tier was seen wrong.
        stated = [((learned.get(a) or {}).get("limits") or {}).get("rpd", {}).get("value") for a in eps]
        stated += [r.get("limit") for a in eps for r in (seen_429.get(a) or {}).values()
                   if isinstance(r, dict) and r.get("window") in ("day", "unknown")]
        limit = min([s for s in stated if s] or [0]) or None
        via = "direct key" if not endpoints.hub(ep) else f"via {ep}"
        bar = {"label": f"free requests today · {via}", "used": used, "of": limit,
               "left": max(0, round(100 * (limit - used) / limit)) if limit else None,
               "reset_at": _iso(_utc_midnight(now) + 86400), "models": len(eps)}
        bars.append(bar)
    if got.get("limit"):
        lim, rem = float(got["limit"]), float(got.get("remaining") or 0)
        bars.append({"label": "credits", "used": round(lim - rem, 2), "of": lim,
                     "left": round(100 * rem / lim) if lim else 0, "reset_at": None, "models": 0})
    return bars


def ordered(tiles_: list[dict]) -> list[dict]:
    """The saved order first (config/ui.json quota_order), then the rest: out
    first, then the soonest reset, then by tag."""
    saved = locked.read_json(DATA / "config" / "ui.json").get("quota_order") or []
    pos = {t: i for i, t in enumerate(saved)}
    return sorted(tiles_, key=lambda t: (pos.get(t["id"], len(pos)), not t["out"],
                                         t["next_reset"] or "9999", t["id"]))


def save_order(order) -> dict:
    if order is not None and not (isinstance(order, list) and all(isinstance(t, str) for t in order)):
        return {"ok": False, "errors": ["order must be a list of tags"]}
    locked.update(DATA / "config" / "ui.json", lambda d: d.update(quota_order=order or []))
    return {"ok": True, "errors": []}


def _selftest() -> None:
    ag = {"groups": [{"displayName": "Gemini Models", "buckets": [
        {"window": "weekly", "resetTime": "2026-10-30T23:01:51Z", "remainingFraction": 0.620195},
        {"window": "5h", "resetTime": "2026-10-29T23:11:05Z", "remainingFraction": 0.9362293}]},
        {"displayName": "Gemini 4 Argon", "buckets": [
            {"window": "weekly", "resetTime": "2026-10-31T01:20:30Z", "remainingFraction": 0.40175587}]}]}
    g = antigravity_groups(ag)
    assert [w["label"] for w in g[0]["windows"]] == ["5 hours", "week"], g
    assert [w["left"] for w in g[0]["windows"]] == [94, 62] and g[1]["windows"][0]["left"] == 40, g
    cx = {"plan_type": "free", "rate_limit": {"primary_window": {
        "used_percent": 100, "limit_window_seconds": 2592000, "reset_at": 1795707512}, "secondary_window": None}}
    c = codex_groups(cx)
    assert c == [{"label": "", "windows": [{"label": "month", "left": 0,
                                            "reset_at": "2026-11-26T15:38:32+00:00"}]}], c
    assert codex_groups({}) == [] and antigravity_groups({}) == []
    now = 1793306000.0                                   # 2026-10-29 ~19:13 UTC
    xk = xkiro_groups({"free_tokens": {"used_today": 125000, "limit_per_day": 500000, "remaining": 375000},
                       "windows": [{"kind": "short", "window_sec": 18000, "spent_usd": "1.500000",
                                    "cap_usd": "6.000000", "remaining_usd": "4.500000", "resets_in_sec": 600}]}, now)
    assert xk[0]["windows"][0] == {"label": "tokens · day", "left": 75, "remaining": 375000, "of": 500000,
                                   "reset_at": "2026-10-30T00:00:00+00:00"}, xk
    assert xk[1]["windows"][0]["label"] == "5 hours · spend" and xk[1]["windows"][0]["left"] == 75, xk
    assert xkiro_groups({"free_tokens": {"limit_per_day": None}, "windows": []}, now) == []
    # Caps: yours beat a stated one on the same axis; counted since midnight UTC or the month's start.
    mid = _utc_midnight(now)
    day = [{"connection": "router.requesty.ai", "t": mid + 60, "attempts": 2},
           {"connection": "router.requesty.ai", "t": mid - 60, "attempts": 5},      # yesterday
           {"connection": "api.cohere.ai", "t": mid + 60, "attempts": 1, "tokens_in": 10, "tokens_out": 5},
           {"connection": "router.requesty.ai", "t": mid + 90, "attempts": 0}]       # refused: not sent
    g = caps("router.requesty.ai", {}, {"rpd": 200}, day, lambda: [], now)
    assert len(g) == 1 and g[0]["windows"][0] == {"label": "requests · day", "left": 99, "remaining": 198,
                                                  "of": 200, "reset_at": _iso(mid + 86400)}, g
    assert g[0]["label"] == "its replies · counted by the bench" and "not enforced" in g[0]["title"], g
    g = caps("router.requesty.ai", {"router.requesty.ai": {"rpd": 10, "rpm": 5}}, {"rpd": 200}, day, lambda: [], now)
    assert g[0]["label"] == "your cap · counted by the bench" and g[0]["windows"] == [
        {"label": "requests · day", "left": 80, "remaining": 8, "of": 10, "reset_at": _iso(mid + 86400)}], g
    asked = []
    g = caps("api.cohere.ai", {}, {"rpmo": 1000}, day, lambda: asked.append(1) or day, now)
    assert asked and g[0]["windows"][0]["remaining"] == 999 and g[0]["windows"][0]["label"] == "requests · month", g
    assert caps("nvidia-api", {}, {}, day, lambda: 1 / 0, now) == []                   # no cap: no ledger read
    assert _seconds_name(18000) == "5 hours" and _seconds_name(7200) == "2 hours"
    assert _utc_midnight(1793306000) == dt.datetime(2026, 10, 29, tzinfo=UTC).timestamp()
    assert _limit_text({"rpm": 20, "tpd": 200000}) == "20 requests a minute, 200,000 tokens a day"
    print("quotaview selftest: ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        print(json.dumps(tiles(), indent=1))
