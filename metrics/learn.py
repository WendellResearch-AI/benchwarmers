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

ONLY EVER TIGHTER
-----------------
A learned context window or rate limit is kept only if it is LOWER than what is
known, and replaces an older learned value only when lower still. An error can
prove a ceiling exists; it cannot prove the ceiling is higher than a catalog
says. Values you entered in the model popup (config/model-overrides.json)
still win: you can always correct a bad parse.

    config/model-learned.json   alias -> {context_window, limits, capabilities, served_as}
                                with the date and the evidence for each
    runs/cooldowns.json         alias -> {until, why}: rate-limited, come back later.
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
COOLDOWNS = DATA / "runs" / "cooldowns.json"
DEFAULT_COOLDOWN_S = 1800          # a 429 with no stated wait
MAX_COOLDOWN_S = 24 * 3600

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
    r"please retry in\s*(\d+(?:\.\d+)?)\s*(s|m|h)",
]
RATE_LIMITED = re.compile(r"rate.?limit|too many requests|\b429\b|quota exceeded|resource_exhausted")


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
    for pat in RETRY:
        if m := re.search(pat, low):
            n = float(m.group(1))
            unit = (m.group(2) if m.lastindex and m.lastindex >= 2 else "s")[0]
            out["retry_after_s"] = min(n * {"s": 1, "m": 60, "h": 3600}[unit], MAX_COOLDOWN_S)
            break
    out["rate_limited"] = bool(RATE_LIMITED.search(low)) or "retry_after_s" in out
    return out


_read = locked.read_json


def learned() -> dict:
    return {k: v for k, v in _read(LEARNED).items() if not k.startswith("_")}


def _known(alias: str, field: str, axis: str | None = None):
    """The value a learned figure has to beat: the catalog's, else the last learned."""
    import catalog
    auto = catalog.catalog().get(alias, {})
    mine = learned().get(alias, {})
    if axis:
        vals = [(mine.get("limits") or {}).get(axis, {}).get("value"),
                catalog.connection_limits().get(catalog.connection_of(alias), {}).get(axis)]
    else:
        vals = [(mine.get(field) or {}).get("value"), auto.get(field)]
    vals = [v for v in vals if v]
    return min(vals) if vals else None


def record(alias: str, text: str, where: str) -> dict:
    """Learn from one failed call. Returns what was stored or set, for logging."""
    found = parse(text)
    now = dt.datetime.now().astimezone()
    stamp = now.isoformat(timespec="seconds")
    evidence = " ".join((text or "").split())[:200]
    stored: dict = {}

    def tighten(data: dict) -> None:
        # Compared inside the lock, so two seats learning at once both count.
        entry = data.get(alias, {})
        if (n := found.get("context_window")) and n < (_known(alias, "context_window") or 1 << 62):
            entry["context_window"] = {"value": n, "at": stamp, "where": where, "evidence": evidence}
            stored["context_window"] = n
        for axis, n in (found.get("limits") or {}).items():
            if n < (_known(alias, "", axis) or 1 << 62):
                entry.setdefault("limits", {})[axis] = {"value": n, "at": stamp, "where": where,
                                                        "evidence": evidence}
                stored[axis] = n
        if stored:
            data[alias] = entry
            data.setdefault("_why", "Learned from error messages by metrics/learn.py; only ever"
                                    " tightened. Your values in config/model-overrides.json win.")

    if alias and (found.get("context_window") or found.get("limits")):
        locked.update(LEARNED, tighten, ensure_ascii=False)
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
    assert parse("model not found") == {"rate_limited": False}
    assert parse("context length of 8k exceeded").get("context_window") is None   # "8" < 512
    print("ok")


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
    for alias, c in sorted(cooldowns().items()):
        print(f"  cooling  {alias:<26} until {c['until'][11:16]}  ({c['why'][:70]})")


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "metrics"))
    sys.path.insert(0, str(ROOT / "suite"))
    main()
