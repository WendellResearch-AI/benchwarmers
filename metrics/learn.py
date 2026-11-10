"""Learn a model's real limits from its own error messages.

    python metrics/learn.py              # what has been learned, and active cooldowns
    python metrics/learn.py --selftest   # canned error strings, no calls

THE CATALOG IS CURATED; THE ERRORS ARE THE TRUTH
------------------------------------------------
A provider that says "maximum context length is 131072 tokens" has told us its
real window, whatever a catalog claims; one that says "Limit 30, Used 30 ...
requests per minute" has told us its rate limit; one that says "try again in
17s" has told us when to come back. FreeLLMAPI's router (MIT, read 2026-10-17)
learns exactly these at runtime, and so does this: every failed call from the
perf probe, the suite (and so suite/pace.py) and the gateway passes its error
text through `record()`.

ONLY EVER TIGHTER, UNTIL IT EXPIRES
-----------------------------------
A learned context window or rate limit is kept only if it is LOWER than what is
known, and replaces an older learned value only when lower still. An error can
prove a ceiling exists; it cannot prove the ceiling is higher than a catalog
says. Values you entered in the model popup (config/model-overrides.json)
still win: you can always correct a bad parse.

A rate limit can be raised, though (a plan upgrade, a provider's change), and
no error ever says so. So a learned RATE limit that has not been seen again for
LEARNED_TTL_S (14 days) is left out wherever it is read: the bench goes back to
the higher rate, and the provider's next refusal teaches the limit again. The
same limit seen again refreshes its `confirmed_at`, so one that still holds
does not expire. A context window does not: a model's window does not grow.

A 429 THAT NAMES NO NUMBER
--------------------------
Cloudflare Workers AI says only that the day's free Neurons are used up. The
bench knows how many requests it sent on that connection in the window before
the refusal (metrics/ledger.py), and that count is the limit, near enough. It
is a connection's limit, not a model's: the key's allowance is shared. One
refusal is a candidate; a second at a count within 20% of it, a window or more
later (sooner is the same refusal, met by another call), is adopted (the lower
of the two) and enforced by metrics/pacing.py unless you set your own for that
connection. Seen again near the same count it is confirmed; a refusal at a
clearly lower count lowers it at once. Only rpm and rpd, and never from the
bench's own refusal.

    config/model-learned.json        alias -> {context_window, limits, capabilities, served_as}
                                     with the date and the evidence for each
    config/connection-learned.json   connection -> {rpm, rpd}: {value, at, confirmed_at, where,
                                     evidence}, or the candidates until one is adopted
    runs/cooldowns.json              alias -> {until, why}: rate-limited, come back later.
                                     Not config: it expires, and is nobody's decision.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import sys

import locked

ROOT = pathlib.Path(__file__).resolve().parent.parent
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
LEARNED = DATA / "config" / "model-learned.json"
CONN_LEARNED = DATA / "config" / "connection-learned.json"
COOLDOWNS = DATA / "runs" / "cooldowns.json"
DEFAULT_COOLDOWN_S = 1800          # a 429 with no stated wait
MAX_COOLDOWN_S = 24 * 3600
LEARNED_TTL_S = 14 * 86400         # a learned rate limit not seen again for this long is forgotten
CONN_AXES = {"rpm": 60, "rpd": 86400}     # what a connection's limit is learned for: its window, seconds
MIN_COUNT = 3                      # fewer requests in the window than this prove nothing

CONTEXT = [
    r"maximum context length is\s*([\d,]+)",
    r"context length(?: of| is)?\s*([\d,]+)",
    r"context window(?: of| is)?\s*([\d,]+)",
    r"max_model_len[^\d]{0,20}([\d,]+)",
    r"maximum (?:input|prompt) (?:length|tokens?)(?: is)?\s*([\d,]+)",
]
AXES = {"rpm": r"requests? per minute|\brpm\b",
        "rpd": r"requests? per day|\brpd\b",
        "tpm": r"tokens? per minute|\btpm\b",
        "tpd": r"tokens? per day|\btpd\b"}
RETRY = [
    r"retry-after[\"':\s]+(\d+(?:\.\d+)?)",
    r"\"retry_?delay\"\s*:\s*\"(\d+(?:\.\d+)?)s",
    r"\"retry_after\"\s*:\s*(\d+(?:\.\d+)?)",
    r"(?:try|retry) again in\s*(\d+(?:\.\d+)?)\s*(s|sec|seconds?|m|min|minutes?|h|hours?)\b",
    r"\bretry in\s*(\d+(?:\.\d+)?)\s*(s|m|h)",         # "please retry in 2s", UnoRouter's "retry in 60s"
]
RATE_LIMITED = re.compile(r"rate.?limit|too many requests|\b429\b|quota exceeded|resource_exhausted")
# The number and its window in one phrase: UnoRouter's free tier, "allows 5
# request(s) every 1 min per account on <model>:free". "every" only: the
# bench's own refusal says "allows 3 requests per minute" (pacing.py), and a
# limit the bench made up must never be learned back.
ALLOWS = re.compile(r"allows?\s+([\d,]+)\s+requests?(?:\(s\))?\s+every\s+(?:1\s+)?(min(?:ute)?|day)\b")
# new-api gateways (Agnes): "maximum 20 requests in 1 minutes" (its
# middleware/model-rate-limit.go); a window of 1 minute or 1,440 only.
MAXIMUM = re.compile(r"maximum\s+([\d,]+)\s+requests?\s+in\s+(1|1440)\s+minutes?\b")
# Which window a 429 with no number is about. A window, not a wait: "try
# again in 3 minutes" names no per-minute limit.
DAY = re.compile(r"\bday\b|daily|free allocation|neurons")
MINUTE = re.compile(r"per[- ]?min(?:ute)?\b|a minute|\brpm\b")


def _int(s: str) -> int:
    return int(s.replace(",", ""))


def parse(text: str) -> dict:
    """What an error message proves: {context_window, limits{axis: n},
    retry_after_s, rate_limited}. Absent keys were not stated."""
    low = (text or "").lower()
    out: dict = {}
    for pat in CONTEXT:
        if (m := re.search(pat, low)) and _int(m.group(1)) >= 512:
            out["context_window"] = _int(m.group(1))
            break
    # "Limit 30" (or "limit: 30") only counts when one axis is named, so a
    # number is never filed under the wrong limit.
    named = [axis for axis, pat in AXES.items() if re.search(pat, low)]
    if len(named) == 1 and (m := re.search(r"\blimit\b[\s:=]*([\d,]+)", low)):
        out["limits"] = {named[0]: _int(m.group(1))}
    elif m := ALLOWS.search(low):
        out["limits"] = {"rpd" if m.group(2) == "day" else "rpm": _int(m.group(1))}
    elif m := MAXIMUM.search(low):
        out["limits"] = {"rpd" if m.group(2) == "1440" else "rpm": _int(m.group(1))}
    for pat in RETRY:
        if m := re.search(pat, low):
            n = float(m.group(1))
            unit = (m.group(2) if m.lastindex and m.lastindex >= 2 else "s")[0]
            out["retry_after_s"] = min(n * {"s": 1, "m": 60, "h": 3600}[unit], MAX_COOLDOWN_S)
            break
    out["rate_limited"] = bool(RATE_LIMITED.search(low)) or "retry_after_s" in out
    return out


_read = locked.read_json


def _age(stamp, now: dt.datetime) -> float:
    """Seconds since an ISO stamp; forever when it is missing or unreadable."""
    try:
        return (now - dt.datetime.fromisoformat(stamp)).total_seconds()
    except (TypeError, ValueError):
        return float("inf")


def _live(ev: dict, now: dt.datetime) -> bool:
    """Seen within LEARNED_TTL_S: an older rate limit may have been raised since."""
    return _age(ev.get("confirmed_at") or ev.get("at"), now) <= LEARNED_TTL_S


def learned() -> dict:
    """alias -> what was learned, without the rate limits that have expired."""
    now = dt.datetime.now().astimezone()
    out = {}
    for alias, e in _read(LEARNED).items():
        if alias.startswith("_"):
            continue
        if isinstance(e, dict) and e.get("limits"):
            e = {**e, "limits": {k: ev for k, ev in e["limits"].items() if _live(ev, now)}}
        out[alias] = e
    return out


def connection_learned(pending: bool = False) -> dict:
    """connection -> {axis: {value, at, confirmed_at, where, evidence}}: the
    adopted limits that have not expired. pending=True: for the axes with none,
    the latest candidate instead ({n, at}), seen once and not enforced."""
    now = dt.datetime.now().astimezone()
    out: dict = {}
    for conn, axes in _read(CONN_LEARNED).items():
        if conn.startswith("_") or not isinstance(axes, dict):
            continue
        for axis, e in axes.items():
            if axis not in CONN_AXES or not isinstance(e, dict):
                continue
            adopted = bool(e.get("value")) and _live(e, now)
            waiting = [c for c in e.get("candidates") or [] if _live(c, now)]
            if adopted and not pending:
                out.setdefault(conn, {})[axis] = {k: v for k, v in e.items() if k != "candidates"}
            elif pending and not adopted and waiting:
                out.setdefault(conn, {})[axis] = waiting[-1]
    return out


def _known(alias: str, field: str, axis: str | None = None):
    """The value a learned figure has to beat: the catalog's, else the last
    learned; for a rate limit, its connection's too, yours and the learned."""
    import catalog
    auto = catalog.catalog().get(alias, {})
    mine = learned().get(alias, {})
    if axis:
        conn = catalog.connection_of(alias)
        vals = [(mine.get("limits") or {}).get(axis, {}).get("value"),
                catalog.connection_limits().get(conn, {}).get(axis),
                connection_learned().get(conn, {}).get(axis, {}).get("value")]
    else:
        vals = [(mine.get(field) or {}).get("value"), auto.get(field)]
    vals = [v for v in vals if v]
    return min(vals) if vals else None


def _tighten(alias: str, found: dict, where: str, evidence: str, stamp: str) -> dict:
    """Store a context window or limits that are lower than what is known."""
    stored: dict = {}

    def tighten(data: dict) -> None:
        # Compared inside the lock, so two seats learning at once both count.
        entry = data.get(alias, {})
        if (n := found.get("context_window")) and n < (_known(alias, "context_window") or 1 << 62):
            entry["context_window"] = {"value": n, "at": stamp, "where": where, "evidence": evidence}
            stored["context_window"] = n
        for axis, n in (found.get("limits") or {}).items():
            had = (entry.get("limits") or {}).get(axis) or {}
            if n < (_known(alias, "", axis) or 1 << 62):   # an expired one is not known: a higher value may replace it
                entry.setdefault("limits", {})[axis] = {"value": n, "at": stamp, "where": where,
                                                        "evidence": evidence}
                stored[axis] = n
            elif had.get("value") == n:
                had["confirmed_at"] = stamp                # still true, so it does not expire
        if stored:
            data[alias] = entry
            data.setdefault("_why", "Learned from error messages by metrics/learn.py; only ever"
                                    " tightened. Your values in config/model-overrides.json win.")

    if alias and (found.get("context_window") or found.get("limits")):
        locked.update(LEARNED, tighten, ensure_ascii=False)
    return stored


def note_limits(alias: str, limits: dict, where: str, evidence: str) -> dict:
    """Limits a provider stated outside its message text: a 429 body's quota
    (metrics/quota.py, Gemini's "50 a day"). Kept the same way, only tighter."""
    stamp = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    return _tighten(alias, {"limits": {k: v for k, v in limits.items() if k in AXES}}, where, evidence, stamp)


def _axis(text: str, found: dict) -> str | None:
    """The window a 429 that names no number is about, or None: nothing to
    learn. Never the bench's own refusal ("not sent", pacing.py): a limit the
    bench enforced must not be learned back from itself. Nor a token limit: the
    count is of requests."""
    low = (text or "").lower()
    if not found.get("rate_limited") or found.get("limits") or "not sent" in low \
            or re.search(AXES["tpm"] + "|" + AXES["tpd"], low):
        return None
    wait = found.get("retry_after_s")
    if DAY.search(low) or (wait or 0) >= 3600:
        return "rpd"
    if MINUTE.search(low) or (wait is not None and wait <= 120):
        return "rpm"
    return None


def _sent(connection: str, since: float) -> int:
    """Requests the provider took on a connection since then: the ledger's
    attempts, without the calls that failed, whose own retries would count the
    refusal as use."""
    import ledger
    return sum(r.get("attempts") or 0 for r in ledger.rows(since)
               if r.get("connection") == connection and r.get("status") != "error")


def _near(n: int, value: int) -> bool:
    return 0.8 * value <= n <= 1.2 * value


def _learn_connection(connection: str, axis: str, n: int, where: str, evidence: str, now: dt.datetime) -> dict:
    """A connection refused after `n` requests in the axis's window. Returns
    {axis: value} when that adopted a limit or lowered one."""
    stamp, span = now.isoformat(timespec="seconds"), CONN_AXES[axis]
    stored: dict = {}

    def change(data: dict) -> None:
        axes = data.setdefault(connection, {})
        e = axes.get(axis) or {}
        fresh = {"value": n, "at": stamp, "confirmed_at": stamp, "where": where, "evidence": evidence}
        waiting = [c for c in e.get("candidates") or [] if _live(c, now)]
        if e.get("value") and _live(e, now):
            # One lesson a window: while a provider keeps refusing, the count
            # of what it took only falls, and would drag the limit down with it.
            if _age(e.get("confirmed_at") or e.get("at"), now) < span:
                return
            if n < 0.8 * e["value"]:                   # refused well below it: that is proof
                axes[axis] = fresh
                stored[axis] = n
            elif _near(n, e["value"]):
                e["confirmed_at"] = stamp
        elif waiting and _age(waiting[-1]["at"], now) < span:
            return                                     # the same refusal, met by another call
        elif agree := [c["n"] for c in waiting if _near(n, c["n"])]:
            axes[axis] = fresh | {"value": min(agree + [n])}
            stored[axis] = axes[axis]["value"]
        else:
            axes[axis] = {"candidates": (waiting + [{"n": n, "at": stamp}])[-5:]}
        data.setdefault("_why", "Learned by metrics/learn.py from 429s that named no number: the requests"
                                " the bench had sent in the window. Enforced unless you set your own"
                                " (config/connection-limits.json); forgotten 14 days after last seen.")

    locked.update(CONN_LEARNED, change, ensure_ascii=False)
    return stored


def record(alias: str, text: str, where: str) -> dict:
    """Learn from one failed call. Returns what was stored or set, for logging."""
    found = parse(text)
    now = dt.datetime.now().astimezone()
    stamp = now.isoformat(timespec="seconds")
    evidence = " ".join((text or "").split())[:200]
    stored = _tighten(alias, found, where, evidence, stamp)
    if alias and (axis := _axis(text, found)):
        try:
            import catalog
            conn = catalog.connection_of(alias)
            if conn and (n := _sent(conn, now.timestamp() - CONN_AXES[axis])) >= MIN_COUNT:
                stored.update(_learn_connection(conn, axis, n, where, evidence, now))
        except Exception:                              # noqa: BLE001  -- learning never breaks a call
            pass
    if alias and found.get("rate_limited"):
        wait = found.get("retry_after_s") or DEFAULT_COOLDOWN_S
        cool = {"until": (now + dt.timedelta(seconds=wait)).isoformat(timespec="seconds"),
                "why": evidence, "where": where, "stated": "retry_after_s" in found}
        locked.update(COOLDOWNS, lambda cd: cd.__setitem__(alias, cool), ensure_ascii=False)
        stored["cooldown_s"] = round(wait)
    return stored


def cooldowns() -> dict:
    """Active cooldowns only: alias -> {until, why}."""
    now = dt.datetime.now().astimezone()
    out = {}
    for alias, c in _read(COOLDOWNS).items():
        try:
            if dt.datetime.fromisoformat(c["until"]) > now:
                out[alias] = c
        except (KeyError, ValueError, TypeError):
            continue
    return out


def note_capability(alias: str, cap: str, evidence: str) -> None:
    """A capability SEEN working (a tool call returned, reasoning tokens
    present). Only positives are stored: a probe that fails proves nothing."""
    seen = {"value": True, "at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "evidence": evidence[:200]}
    locked.update(LEARNED, lambda data: data.setdefault(alias, {}).setdefault("capabilities", {})
                  .__setitem__(cap, seen), ensure_ascii=False)


def note_served_as(alias: str, requested: str, served: str) -> None:
    """The response named a different model than the one asked for."""
    swap = {"requested": requested, "served": served,
            "at": dt.datetime.now().astimezone().isoformat(timespec="seconds")}
    locked.update(LEARNED, lambda data: data.setdefault(alias, {}).__setitem__("served_as", swap),
                  ensure_ascii=False)


def _selftest() -> None:
    p = parse("Error code: 400 - This model's maximum context length is 131,072 tokens. However,"
              " you requested 140000 tokens")
    assert p["context_window"] == 131072 and not p["rate_limited"], p
    p = parse("429 Too Many Requests: Rate limit reached for model in organization: Limit 30,"
              " Used 30, Requested 1. Please try again in 2.5s. (requests per minute)")
    assert p["limits"] == {"rpm": 30} and p["retry_after_s"] == 2.5 and p["rate_limited"], p
    p = parse('{"error":{"code":429,"status":"RESOURCE_EXHAUSTED","details":[{"retryDelay":"17s"}]}}')
    assert p["retry_after_s"] == 17 and p["rate_limited"], p
    p = parse("Rate limit exceeded: free-models-per-day. Try again in 3 minutes")
    assert p["retry_after_s"] == 180 and "limits" not in p, p      # no number under a named axis
    # Two axes named: the number is not filed under either.
    assert "limits" not in parse("limit 100 (tokens per minute) and requests per minute")
    # UnoRouter's free tier says the number and its window together.
    p = parse("Error code: 429 - {'error': {'message': 'Too many requests. The free tier allows 5 request(s) every"
              " 1 min per account on llama-3.2-11b-vision:free - nothing is used up, retry in 60s.")
    assert p["limits"] == {"rpm": 5} and p["retry_after_s"] == 60 and p["rate_limited"], p
    assert parse("The free tier allows 200 requests every day")["limits"] == {"rpd": 200}
    assert "limits" not in parse("allows 3 requests every 5 min")             # a window no axis holds
    assert "limits" not in parse("rate limit (not sent; paced by the bench): x allows 3 requests per minute")
    # A new-api gateway's 429 (Agnes).
    p = parse("Error code: 429 - {'error': {'message': 'You have reached the request limit: maximum 20 requests"
              " in 1 minutes', 'type': 'AgnesAI_error'}}")
    assert p["limits"] == {"rpm": 20} and p["rate_limited"], p
    assert "limits" not in parse("maximum 100 requests in 5 minutes")         # a window no axis holds
    assert parse("model not found") == {"rate_limited": False}
    assert parse("context length of 8k exceeded").get("context_window") is None   # "8" < 512

    # Which window a 429 with no number is about.
    def axis(text: str):
        return _axis(text, parse(text))
    neurons = "Error code: 429 - you have used up your daily free allocation of 10,000 neurons"
    assert axis(neurons) == "rpd" and "limits" not in parse(neurons)
    assert axis("Rate limit exceeded: free-models-per-day. Try again in 3 minutes") == "rpd"
    assert axis("429 Too Many Requests, retry-after: 7200") == "rpd"
    assert axis("429 Too Many Requests, retry-after: 30") == "rpm"
    assert axis("429: too many requests per minute") == "rpm"
    assert axis("429 Too Many Requests") is None                         # no window named
    assert axis("Rate limit exceeded. Try again in 3 minutes") is None    # a wait, not a window
    assert axis("429: rate limit on tokens per minute") is None          # requests are what is counted
    assert axis("Limit 30, Used 30 (requests per minute) 429") is None   # it gave the number
    assert axis("model not found") is None
    # The bench's own refusal is never learned from.
    refusal = ("Error: client failed: rate limit (not sent; paced by the bench): x allows 50 requests"
               " per day (learned) and 50 were used; try again in 2h")
    assert axis(refusal) is None and axis(refusal.replace("not sent; ", "")) == "rpd"
    _selftest_stores(neurons, refusal)
    print("ok")


def _selftest_stores(neurons: str, refusal: str) -> None:
    """The connection store and the expiry, in a temp folder with a stub
    catalog: nothing of the install's is read or written."""
    import tempfile

    import catalog
    import ledger
    with tempfile.TemporaryDirectory() as folder:
        tmp = pathlib.Path(folder)
        globals().update(LEARNED=tmp / "model-learned.json", CONN_LEARNED=tmp / "connection-learned.json",
                         COOLDOWNS=tmp / "cooldowns.json")
        ledger.DIR = tmp / "usage"
        catalog.catalog = catalog.connection_limits = lambda: {}
        catalog.connection_of = lambda alias: "conn-" + alias
        now = dt.datetime.now().astimezone()

        def ago(**kw) -> dt.datetime:
            return now - dt.timedelta(**kw)

        def refused(n: int, when: dt.datetime, conn: str = "prov", ax: str = "rpd") -> dict:
            return _learn_connection(conn, ax, n, "test", "429", when)
        # One refusal is a candidate; the same refusal met again changes nothing.
        assert refused(100, ago(days=5)) == {} and connection_learned() == {}
        assert connection_learned(pending=True)["prov"]["rpd"]["n"] == 100
        assert refused(104, ago(days=5, hours=-1)) == {} and connection_learned() == {}
        # A second count far from the first does not adopt; one within 20% does, the lower of the two.
        assert refused(300, ago(days=4)) == {} and connection_learned() == {}
        assert refused(110, ago(days=3)) == {"rpd": 100}
        got = connection_learned()["prov"]["rpd"]
        assert got["value"] == 100 and "candidates" not in got and connection_learned(pending=True) == {}, got
        # Seen again near it: confirmed. Clearly lower: lowered at once. Higher: left alone.
        assert refused(95, ago(days=2)) == {}
        got = connection_learned()["prov"]["rpd"]
        assert got["value"] == 100 and got["confirmed_at"] == ago(days=2).isoformat(timespec="seconds") != got["at"]
        assert refused(40, ago(days=2, hours=-1)) == {}                      # within the window: the same refusal
        assert refused(500, ago(days=1)) == {} and connection_learned()["prov"]["rpd"]["value"] == 100
        assert refused(60, now) == {"rpd": 60} and connection_learned()["prov"]["rpd"]["value"] == 60
        # Not seen for 15 days: forgotten on read, and a higher limit can be learned.
        assert refused(20, ago(days=16), "old", "rpm") == {} and refused(20, ago(days=15), "old", "rpm") == {"rpm": 20}
        assert "old" not in connection_learned()
        assert refused(50, ago(minutes=5), "old", "rpm") == {} and refused(50, now, "old", "rpm") == {"rpm": 50}

        # A model's own learned limit: the same expiry, and the same limit seen again is confirmed.
        old = {"value": 5, "at": ago(days=15).isoformat(timespec="seconds"), "where": "test", "evidence": ""}
        LEARNED.write_text(json.dumps({"m": {"limits": {"rpm": dict(old), "rpd": dict(old)},
                                             "context_window": dict(old, value=8192)}}), encoding="utf-8")
        assert learned()["m"]["limits"] == {} and learned()["m"]["context_window"]["value"] == 8192
        stamp = now.isoformat(timespec="seconds")
        assert _tighten("m", {"limits": {"rpm": 20}}, "test", "", stamp) == {"rpm": 20}
        assert _tighten("m", {"limits": {"rpm": 30}}, "test", "", stamp) == {}        # live: only ever tighter
        assert "confirmed_at" not in learned()["m"]["limits"]["rpm"]
        again = ago(hours=-1).isoformat(timespec="seconds")
        assert _tighten("m", {"limits": {"rpm": 20}}, "test", "", again) == {}
        got = learned()["m"]["limits"]["rpm"]
        assert got["value"] == 20 and got["at"] == stamp and got["confirmed_at"] == again, got
        # A model's limit must beat its connection's learned one too.
        assert refused(10, ago(minutes=5), "conn-m2", "rpm") == {} and refused(10, now, "conn-m2", "rpm") == {"rpm": 10}
        assert _tighten("m2", {"limits": {"rpm": 60}}, "test", "", stamp) == {}
        assert _tighten("m2", {"limits": {"rpm": 6}}, "test", "", stamp) == {"rpm": 6}

        # record(): the count is the ledger's, of what the provider took on that connection.
        def sent(conn: str, n: int, status: str = "ok", attempts: int = 1) -> None:
            for i in range(n):
                ledger.append({"t": now.timestamp() - 30 - i, "alias": "x", "connection": conn, "status": status,
                               "attempts": attempts})
        sent("conn-r", 4), sent("conn-r", 1, "error", 3), sent("elsewhere", 9), sent("conn-p", 4), sent("conn-few", 2)
        assert record("r", neurons, "test") == {"cooldown_s": DEFAULT_COOLDOWN_S}     # a candidate: nothing stored yet
        assert connection_learned(pending=True)["conn-r"]["rpd"]["n"] == 4
        assert record("few", neurons, "test") and "conn-few" not in connection_learned(pending=True)   # too few to say
        # The bench's own refusal cools the model as before, and teaches nothing.
        assert record("p", refusal, "test") == {"cooldown_s": 7200} and "p" in cooldowns()
        assert "conn-p" not in connection_learned(pending=True)



def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if "--selftest" in sys.argv:
        _selftest()
        return
    for alias, e in sorted(learned().items()):
        bits = []
        if e.get("context_window"):
            bits.append(f"context {e['context_window']['value']:,}")
        bits += [f"{k} {v['value']}" for k, v in (e.get("limits") or {}).items()]
        bits += [f"can {k}" for k in (e.get("capabilities") or {})]
        if e.get("served_as"):
            bits.append(f"served as {e['served_as']['served']}")
        print(f"  {alias:<34} {', '.join(bits)}")
    for conn, axes in sorted(connection_learned().items()):
        bits = [f"{k} {v['value']}" for k, v in axes.items()]
        print(f"  {conn:<34} {', '.join(bits)}  (the connection's, from 429s with no number)")
    for alias, c in sorted(cooldowns().items()):
        print(f"  cooling  {alias:<26} until {c['until'][11:16]}  ({c['why'][:70]})")


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "metrics"))
    sys.path.insert(0, str(ROOT / "suite"))
    main()
