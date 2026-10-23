"""Quota records: what a provider says is left on a connection, and when it resets.

    python metrics/quota.py              # every record still in force
    python metrics/quota.py --selftest   # each reset format and provider profile; no calls

RELEASE-PLAN section 2, "Quotas". The built-in client (metrics/client.py)
hands every response's headers here, and every 429's body; what they state
becomes one record per connection, model, metric and window:

    {"connection": "groq", "alias": "...", "metric": "requests", "window": "day",
     "limit": 1000, "remaining": 998, "reset_at": "2026-10-24T00:00:12+00:00",
     "source": "headers (groq)", "observed_at": "2026-10-23T19:01:30+00:00"}

kept in runs/quotas.json (not config: it expires, and is nobody's decision),
by connection and then by model, since Groq, Cerebras and OpenAI meter each
model apart. Pacing (metrics/pacing.py) reads them back: calls sent since a
record was observed are taken off its `remaining`, and when nothing is left
the next call waits for `reset_at` or is refused. A record stops counting at
its reset, so a connection is never held for want of traffic to observe one.

ONE HEADER NAME, DIFFERENT WINDOWS
----------------------------------
`x-ratelimit-limit-requests` is per minute at OpenAI and per day at Groq;
Cerebras names its windows (`-requests-day`, `-tokens-minute`); OpenRouter
sends bare `X-RateLimit-*` only on a 429, inside the body's metadata as well,
and its message says which window ("free-models-per-day"); Anthropic's
`anthropic-ratelimit-*` are per minute; Gemini sends no headers but its 429
body names the exhausted quota and the retry delay. PROFILES holds what each
provider means, picked by the endpoint's host; any other host is read the
OpenAI way with the window left "unknown", which still enforces, since the
reset is absolute.

RESETS COME IN FIVE SPELLINGS
-----------------------------
Durations (`6m0s`, `2m59.56s`, `20ms`, `17s`), float seconds (Cerebras'
`33011.38`), epoch seconds or milliseconds (OpenRouter's `1761264000000`),
RFC 3339 (Anthropic) and HTTP dates (Retry-After). Each becomes an absolute
time the moment the response arrives: "in 6m0s" means nothing to whoever
reads the record later.

metrics/learn.py still learns from the error TEXT, for either backend: a
stated limit, a context window, a cooldown. A 429 body that states a limit
with a known window (Gemini's "50 a day") is passed on to it here, so the
limit is learned (and enforced) like one read from a message.
"""

from __future__ import annotations

import datetime as dt
import email.utils
import json
import pathlib
import re
import sys
import time
import urllib.parse

import locked

ROOT = pathlib.Path(__file__).resolve().parent.parent
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
FILE = DATA / "runs" / "quotas.json"
KEEP_S = 7 * 86400                     # a record with no reset is shown this long, never enforced

# host (or a parent domain of it) -> (profile, window of x-ratelimit-*-requests / -tokens
# when the header does not name one)
PROFILES = {
    "api.openai.com": ("openai", {"requests": "minute", "tokens": "minute"}),
    "api.groq.com": ("groq", {"requests": "day", "tokens": "minute"}),
    "api.cerebras.ai": ("cerebras", {}),
    "openrouter.ai": ("openrouter", {}),
    "api.anthropic.com": ("anthropic", {}),
    "api.sambanova.ai": ("sambanova", {"requests": "minute", "tokens": "minute"}),
    "generativelanguage.googleapis.com": ("gemini", {}),
}
WINDOWS = {"minute": 60, "hour": 3600, "day": 86400}
OPENAI_STYLE = re.compile(r"^x-ratelimit-(limit|remaining|reset)-(requests|tokens)(?:-(minute|hour|day))?$")
BARE = re.compile(r"^x-ratelimit-(limit|remaining|reset)$")
ANTHROPIC = re.compile(r"^anthropic-ratelimit-(requests|tokens|input-tokens|output-tokens)-(limit|remaining|reset)$")
DURATION = re.compile(r"^(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m(?!s))?(?:(\d+(?:\.\d+)?)s)?(?:(\d+(?:\.\d+)?)ms)?$")


def profile_for(base: str) -> tuple[str, dict]:
    """The profile for an endpoint's base URL; ("generic", {}) for any other host."""
    host = (urllib.parse.urlsplit(base or "").hostname or "").lower()
    for known, prof in PROFILES.items():
        if host == known or host.endswith("." + known):
            return prof
    return "generic", {}


def reset_time(value, now: float) -> float | None:
    """An absolute epoch time from any of the five spellings, or None."""
    raw = str(value or "").strip()
    if not raw:
        return None
    if (m := DURATION.match(raw)) and any(m.groups()):
        h, mi, s, ms = (float(g) if g else 0.0 for g in m.groups())
        return now + h * 3600 + mi * 60 + s + ms / 1000
    if re.match(r"^\d{4}-\d{2}-\d{2}T", raw):
        try:
            return dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    try:
        n = float(raw)
    except ValueError:
        try:                                           # an HTTP date
            return email.utils.parsedate_to_datetime(raw).timestamp()
        except (TypeError, ValueError):
            return None
    if n >= 1e12:                                      # epoch milliseconds
        return n / 1000
    if n >= 1e9:                                       # epoch seconds
        return n
    return now + n                                     # seconds from now


def _iso(t: float | None) -> str | None:
    return None if t is None else dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat(timespec="seconds")


def _int(v) -> int | None:
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return None


def _window_hint(text: str) -> str:
    """OpenRouter says which window ran out in its message, not its headers."""
    low = (text or "").lower()
    for word, window in (("per-day", "day"), ("per day", "day"), ("per-min", "minute"), ("per min", "minute")):
        if word in low:
            return window
    return "unknown"


def from_headers(headers, profile: tuple[str, dict], now: float, status: int = 200,
                 hint: str = "", source: str = "headers") -> list[dict]:
    """The records a response's headers state. `hint`: the error message, which
    names OpenRouter's window."""
    name, defaults = profile
    h = {str(k).lower(): v for k, v in (headers.items() if hasattr(headers, "items") else [])}
    fields: dict[tuple[str, str], dict] = {}
    for key, value in h.items():
        if m := OPENAI_STYLE.match(key):
            what, metric, window = m.group(1), m.group(2), m.group(3) or defaults.get(m.group(2), "unknown")
        elif m := BARE.match(key):
            what, metric, window = m.group(1), "requests", _window_hint(hint)
        elif m := ANTHROPIC.match(key):
            metric, what, window = m.group(1), m.group(2), "minute"
        else:
            continue
        fields.setdefault((metric, window), {})[what] = value
    out = []
    for (metric, window), f in fields.items():
        rec = {"metric": metric, "window": window, "limit": _int(f.get("limit")),
               "remaining": _int(f.get("remaining")), "reset_at": _iso(reset_time(f.get("reset"), now)),
               "source": f"{source} ({name})", "observed_at": _iso(now)}
        if rec["limit"] is not None or rec["remaining"] is not None:
            out.append(rec)
    if status == 429:
        wait = None
        for key, scale in (("retry-after-ms", 0.001), ("retry-after", 1.0)):
            if h.get(key):
                raw = str(h[key]).strip()
                try:
                    wait = float(raw) * scale
                except ValueError:
                    t = reset_time(raw, now)
                    wait = None if t is None else t - now
                break
        if wait and wait > 0:
            out.append({"metric": "requests", "window": "retry-after", "limit": None, "remaining": 0,
                        "reset_at": _iso(now + wait), "source": f"retry-after ({name})",
                        "observed_at": _iso(now)})
    return out


def _error_objects(body):
    """Each `error` dict in a 429 body: an object, or Gemini's one-item list."""
    for item in (body if isinstance(body, list) else [body]):
        if isinstance(item, dict) and isinstance(item.get("error"), dict):
            yield item["error"]


def from_body(body, profile: tuple[str, dict], now: float) -> list[dict]:
    """The records a 429 body states: Gemini's QuotaFailure and RetryInfo, and
    OpenRouter's copy of its rate-limit headers in `metadata.headers`."""
    out = []
    for err in _error_objects(body):
        meta = err.get("metadata") if isinstance(err.get("metadata"), dict) else {}
        if isinstance(meta.get("headers"), dict):
            out += from_headers(meta["headers"], profile, now, hint=str(err.get("message") or ""),
                                source="429 body")
        details = [d for d in err.get("details") or [] if isinstance(d, dict)]
        delay = next((reset_time(d.get("retryDelay"), now) for d in details if d.get("retryDelay")), None)
        for d in details:
            for v in d.get("violations") or []:
                if not isinstance(v, dict) or not v.get("quotaId"):
                    continue
                qid = str(v["quotaId"])
                window = next((w for w in ("day", "hour", "minute") if f"per{w}" in qid.lower()), "unknown")
                metric = "tokens" if "token" in (qid + str(v.get("quotaMetric") or "")).lower() else "requests"
                # No retry delay on a per-minute quota: the minute is the wait.
                reset = delay if delay else (now + WINDOWS[window] if window == "minute" else None)
                out.append({"metric": metric, "window": window, "limit": _int(v.get("quotaValue")),
                            "remaining": 0, "reset_at": _iso(reset), "quota": qid,
                            "source": f"429 body ({profile[0]})", "observed_at": _iso(now)})
    return out


def _ts(iso: str | None) -> float | None:
    try:
        return dt.datetime.fromisoformat(iso).timestamp() if iso else None
    except (TypeError, ValueError):
        return None


def store(connection: str, alias: str, records: list[dict], now: float | None = None) -> None:
    """Keep the newest record per metric and window, and drop what has expired."""
    now = time.time() if now is None else now

    def change(data: dict) -> None:
        mine = data.setdefault(connection, {}).setdefault(alias, {})
        for r in records:
            mine[f"{r['metric']}/{r['window']}"] = {"connection": connection, "alias": alias, **r}
        for conn in list(data):
            if conn.startswith("_") or not isinstance(data[conn], dict):
                continue
            for a in list(data[conn]):
                recs = data[conn][a]
                for k in list(recs):
                    reset, seen = _ts(recs[k].get("reset_at")), _ts(recs[k].get("observed_at")) or 0
                    if (reset is not None and reset < now - 86400) or (reset is None and seen < now - KEEP_S):
                        del recs[k]
                if not recs:
                    del data[conn][a]
            if not data[conn]:
                del data[conn]
        data["_why"] = ("What providers' rate-limit headers and 429 bodies said was left, per connection"
                        " and model (metrics/quota.py). Expires; nobody's decision.")
    locked.update(FILE, change, ensure_ascii=False)


def observe(alias: str, connection: str, base: str, status: int, headers, body=None) -> list[dict]:
    """Record what one response states; returns the records. Called by the
    built-in client on every reply and every HTTP error."""
    now, profile = time.time(), profile_for(base)
    hint = " ".join(str(e.get("message") or "") for e in _error_objects(body)) if status == 429 else ""
    records = from_headers(headers, profile, now, status, hint)
    if status == 429 and body is not None:
        records += from_body(body, profile, now)
    if records:
        store(connection or "?", alias, records, now)
        if status == 429:
            _learn_limits(alias, records)
    return records


def _learn_limits(alias: str, records: list[dict]) -> None:
    """A 429 that states a per-minute or per-day limit: learned, as learn.py
    learns one stated in an error message (only ever tighter)."""
    axes = {("requests", "minute"): "rpm", ("requests", "day"): "rpd",
            ("tokens", "minute"): "tpm", ("tokens", "day"): "tpd"}
    found = {axes[(r["metric"], r["window"])]: r["limit"] for r in records
             if (r["metric"], r["window"]) in axes and isinstance(r.get("limit"), int) and r["limit"] > 0}
    if found:
        try:
            import learn
            learn.note_limits(alias, found, "quota", "; ".join(r["source"] for r in records)[:200])
        except Exception:                              # noqa: BLE001  -- learning never breaks a call
            pass


def live(connection: str | None = None, alias: str | None = None, now: float | None = None) -> list[dict]:
    """Records whose reset is still ahead, for one connection and model, or all."""
    now = time.time() if now is None else now
    out = []
    for conn, by_alias in locked.read_json(FILE).items():
        if conn.startswith("_") or not isinstance(by_alias, dict) or (connection and conn != connection):
            continue
        for a, recs in by_alias.items():
            if (alias and a != alias) or not isinstance(recs, dict):
                continue
            out += [r for r in recs.values() if isinstance(r, dict) and (_ts(r.get("reset_at")) or 0) > now]
    return out


def _selftest() -> None:
    now = 1_761_264_000.0                              # 2025-10-24T00:00:00Z
    # Each reset spelling.
    assert reset_time("6m0s", now) == now + 360
    assert abs(reset_time("2m59.56s", now) - (now + 179.56)) < 1e-6     # Groq
    assert reset_time("20ms", now) == now + 0.02                        # OpenAI
    assert reset_time("1h2m3s", now) == now + 3723
    assert reset_time("17s", now) == now + 17                           # Gemini's retryDelay
    assert abs(reset_time("33011.382867", now) - (now + 33011.382867)) < 1e-6   # Cerebras
    assert reset_time("1761350400000", now) == 1761350400.0             # OpenRouter, epoch ms
    assert reset_time("1761350400", now) == 1761350400.0                # epoch s
    assert reset_time("2025-10-24T00:01:00Z", now) == now + 60          # Anthropic, RFC 3339
    assert reset_time("Fri, 24 Oct 2025 00:00:30 GMT", now) == now + 30  # an HTTP date
    assert reset_time("", now) is None and reset_time("soon", now) is None

    def one(recs, metric, window):
        got = [r for r in recs if r["metric"] == metric and r["window"] == window]
        assert len(got) == 1, (metric, window, recs)
        return got[0]
    # OpenAI: unsuffixed means per minute.
    r = from_headers({"x-ratelimit-limit-requests": "500", "x-ratelimit-remaining-requests": "499",
                      "x-ratelimit-reset-requests": "120ms", "x-ratelimit-limit-tokens": "30000",
                      "x-ratelimit-remaining-tokens": "29000", "x-ratelimit-reset-tokens": "2s"},
                     profile_for("https://api.openai.com/v1"), now)
    assert one(r, "requests", "minute")["remaining"] == 499 and one(r, "tokens", "minute")["limit"] == 30000
    # Groq: the same names, but requests are per day.
    r = from_headers({"X-Ratelimit-Limit-Requests": "1000", "X-Ratelimit-Remaining-Requests": "0",
                      "X-Ratelimit-Reset-Requests": "2m59.56s"}, profile_for("https://api.groq.com/openai/v1"), now)
    g = one(r, "requests", "day")
    assert g["remaining"] == 0 and g["reset_at"] == _iso(now + 179.56) and g["source"] == "headers (groq)", g
    # Cerebras names its windows.
    r = from_headers({"x-ratelimit-limit-requests-day": "14400", "x-ratelimit-remaining-requests-day": "14399",
                      "x-ratelimit-reset-requests-day": "33011.38", "x-ratelimit-limit-tokens-minute": "60000",
                      "x-ratelimit-remaining-tokens-minute": "59000", "x-ratelimit-reset-tokens-minute": "11.4"},
                     profile_for("https://api.cerebras.ai/v1"), now)
    assert one(r, "requests", "day")["limit"] == 14400 and one(r, "tokens", "minute")["remaining"] == 59000
    # Anthropic: RFC 3339 resets, per minute.
    r = from_headers({"anthropic-ratelimit-requests-limit": "50", "anthropic-ratelimit-requests-remaining": "0",
                      "anthropic-ratelimit-requests-reset": "2025-10-24T00:00:45Z"},
                     profile_for("https://api.anthropic.com/v1"), now)
    assert one(r, "requests", "minute")["reset_at"] == _iso(now + 45)
    # OpenRouter: bare headers, epoch ms, window from the message; and retry-after on a 429.
    r = from_headers({"X-RateLimit-Limit": "50", "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1761350400000",
                      "Retry-After": "30"}, profile_for("https://openrouter.ai/api/v1"), now, 429,
                     hint="Rate limit exceeded: free-models-per-day")
    assert one(r, "requests", "day")["reset_at"] == _iso(1761350400.0)
    assert one(r, "requests", "retry-after")["reset_at"] == _iso(now + 30)
    # Retry-After is only a quota on a 429.
    assert from_headers({"Retry-After": "30"}, ("generic", {}), now, 503) == []
    # Any other host: read the OpenAI way, window unknown.
    r = from_headers({"x-ratelimit-remaining-requests": "3", "x-ratelimit-reset-requests": "10s"},
                     profile_for("http://127.0.0.1:9/v1"), now)
    assert one(r, "requests", "unknown")["source"] == "headers (generic)"
    assert from_headers({"content-type": "application/json"}, ("generic", {}), now) == []
    # Gemini's 429 body (a one-item list through the OpenAI-compatible endpoint).
    gem = [{"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
            {"quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
             "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier", "quotaValue": "50"}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "17s"}]}}]
    g = one(from_body(gem, profile_for("https://generativelanguage.googleapis.com/v1beta/openai"), now),
            "requests", "day")
    assert g["limit"] == 50 and g["remaining"] == 0 and g["reset_at"] == _iso(now + 17), g
    tok = {"error": {"details": [{"violations": [{"quotaId": "GenerateContentInputTokensPerModelPerMinute-FreeTier",
                                                  "quotaValue": "250000"}]}]}}
    g = one(from_body(tok, ("gemini", {}), now), "tokens", "minute")
    assert g["reset_at"] == _iso(now + 60), g                          # no delay: the minute is the wait
    # OpenRouter's 429 body carries its headers in metadata.
    orb = {"error": {"message": "Rate limit exceeded: free-models-per-min.", "code": 429,
                     "metadata": {"headers": {"X-RateLimit-Limit": "20", "X-RateLimit-Remaining": "0",
                                              "X-RateLimit-Reset": "1761264060000"}}}}
    g = one(from_body(orb, profile_for("https://openrouter.ai/api/v1"), now), "requests", "minute")
    assert g["limit"] == 20 and g["source"] == "429 body (openrouter)", g
    assert from_body("<html>busy</html>", ("generic", {}), now) == []
    print("ok")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if "--selftest" in sys.argv:
        _selftest()
        return
    recs = live()
    if not recs:
        print(f"no quota record in force ({FILE})")
        return
    for r in sorted(recs, key=lambda r: (r["connection"], r["alias"], r["metric"], r["window"])):
        print(f"  {r['connection']:<16} {r['alias']:<30} {r['metric']:<8} {r['window']:<11}"
              f" {r.get('remaining')!s:>7} of {r.get('limit')!s:<7} until {r.get('reset_at')}  ({r['source']})")


if __name__ == "__main__":
    main()
