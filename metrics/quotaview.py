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
    models  nothing stated: each bench model's last day, answered or not

REFRESH NEVER SPENDS QUOTA. Two providers can be asked what is left for free,
and are, when Refresh is pressed:

    OpenRouter      GET /key on a direct OpenRouter endpoint: free tier or
                    not (50 or 1,000 free requests a day), and its credits
    CLI Proxy API   its management route `requests/api-call`, which calls the
                    credential's own quota endpoint with the credential's
                    token, as its console does (Google's retrieveUserQuotaSummary,
                    ChatGPT's wham/usage). Needs CLI Proxy API on the
                    Endpoints page as a hub with its management key
                    (endpoints.admin_key, as metrics/cliproxy.py); the proxy is
                    on the LAN and calls only providers the credential uses.

Every other provider's Refresh re-reads the bench's own records. `check`
is the one that asks: a 16-token call to one of its models, whose reply
headers quota.py records. Offered only where the provider has sent limit
headers before, since elsewhere the reply would say nothing.

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
AG_HEAD = {"Authorization": "Bearer $TOKEN$", "Content-Type": "application/json",
           "User-Agent": "antigravity/hub/2.23.0 darwin/arm64"}
CODEX_URL = "https://chatgpt.com/backend-api/wham/usage"
CODEX_HEAD = {"Authorization": "Bearer $TOKEN$", "Content-Type": "application/json",
              "User-Agent": "codex-tui/0.149.1"}
WINDOW_NAMES = {"5h": "5 hours", "weekly": "week", "daily": "day", "monthly": "month"}


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
    """Ask the provider when it can be asked for free, keep the answer;
    {"ok", "how": "live"|"records", "note"}."""
    if tag == "openrouter" and _openrouter_endpoint():
        got = read_openrouter()
    elif tag.split("/")[0] == "cli-proxy-api":
        vendors = {t.split("/", 1)[1] for t in _tags() if t.startswith("cli-proxy-api/")}
        got = read_cliproxy(tag, vendors)
    else:
        return {"ok": True, "how": "records",
                "note": "this provider has no free quota read: shown from the bench's own records"}
    got["at"] = _iso(time.time())
    locked.update(LIVE, lambda d: d.update({tag: got}), ensure_ascii=False)
    return {"ok": True, "how": "live", "note": "; ".join(got.get("notes") or [])}


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
    words = {"rpm": "requests a minute", "rpd": "requests a day", "tpm": "tokens a minute", "tpd": "tokens a day"}
    return ", ".join(f"{v:,} {words[k]}" for k, v in lim.items() if k in words and v)


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
    out = []
    for tag, aliases in tags.items():
        host = route.connection_of(aliases[0])
        mine = set(aliases)
        limits = {a: _limit_text({k: ev.get("value") for k, ev in ((learned.get(a) or {}).get("limits") or {}).items()})
                  for a in aliases}
        limits = {a: t for a, t in limits.items() if t}
        rows = [r for r in day_rows if r.get("alias") in mine]
        t = {"id": tag, "tag": tag, "host": host, "bench": len(aliases), "kind": "models", "groups": [],
             "pool": [], "notes": [], "conn_limit": _limit_text(entered.get(host) or {}),
             "models": _model_rows(aliases, rows, resting, limits),
             "refresh": "records", "check": False, "plan": "", "name": ""}
        got = live.get(tag) or {}
        if tag.split("/")[0] == "cli-proxy-api":
            t["refresh"] = "live"
            if got.get("groups"):
                t.update(kind="groups", groups=got["groups"], name=got.get("name", ""), plan=got.get("plan", ""))
        elif tag == "openrouter":
            t["refresh"] = "live" if _openrouter_endpoint() else "records"
            t["kind"] = "pool"
            t["pool"] = _openrouter_pool(aliases, utc_rows, regs, got, learned, now)
        if t["kind"] == "models":                       # the headers' windows, when any is in force
            recs = [r for r in quota.live(host, now=now) if r.get("alias") in mine
                    and r.get("window") != "retry-after" and r.get("limit")]
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
        t["check"] = t["refresh"] == "records" and _headers_seen(tag)
        last = max((r["t"] for r in rows if r.get("status") == "ok"), default=None)
        t["fresh"] = ({"how": "live", "at": got["at"]} if got.get("at") and got.get("how") != "seen"
                      else {"how": "seen", "at": got["at"]} if got.get("at")
                      else {"how": "answered", "at": _iso(last)} if last else {"how": "none", "at": None})
        t["live"] = sum(1 for a in aliases if a not in resting)
        resets = [_ts(w.get("reset_at")) for g in t["groups"] for w in g["windows"]] \
            + [_ts(p.get("reset_at")) for p in t["pool"]]
        resets = [r for r in resets if r and r > now]
        t["next_reset"] = _iso(min(resets)) if resets else None
        t["out"] = (t["live"] == 0 or any(w["left"] <= 0 for g in t["groups"] for w in g["windows"])
                    or any(p.get("left") == 0 for p in t["pool"]))
        t["notes"] = list(got.get("notes") or [])
        out.append(t)
    return ordered(out)


def _openrouter_pool(aliases, utc_rows, regs, got, learned, now) -> list[dict]:
    """A bar per OpenRouter key the bench uses: free-model requests since
    00:00 UTC, the bench's own count, against 50 or 1,000 a day."""
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
        # What OpenRouter stated wins (learned, else the last 429's, kept a day
        # past reset); the key's free tier only guesses, and was seen wrong.
        stated = [((learned.get(a) or {}).get("limits") or {}).get("rpd", {}).get("value") for a in eps]
        stated += [r.get("limit") for a in eps for r in (seen_429.get(a) or {}).values()
                   if isinstance(r, dict) and r.get("window") in ("day", "unknown")]
        limit = min([s for s in stated if s] or [0]) or None
        if not limit and got.get("endpoint") == ep:
            limit = 50 if got.get("free_tier") else 1000
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
    assert _seconds_name(18000) == "5 hours" and _seconds_name(7200) == "2 hours"
    assert _utc_midnight(1793306000) == dt.datetime(2026, 10, 29, tzinfo=UTC).timestamp()
    assert _limit_text({"rpm": 20, "tpd": 200000}) == "20 requests a minute, 200,000 tokens a day"
    print("quotaview selftest: ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        print(json.dumps(tiles(), indent=1))
