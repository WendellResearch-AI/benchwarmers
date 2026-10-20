"""Find out by asking: can each model call tools, and does it reason?

    python metrics/probe_caps.py                 # every chat model, 30 s between calls
    python metrics/probe_caps.py --model qwen3.8-27b
    python metrics/probe_caps.py --dry-run

WEEKLY, NOT DAILY: two calls per model, ~50 in all, spaced out. Registered as
WRBench-Capabilities by scripts/schedule.ps1.

WHAT COUNTS
-----------
- TOOLS: one trivial function offered with tool_choice "auto" and a prompt that
  needs it. Only a returned tool call counts (message.tool_calls, or
  finish_reason "tool_calls"); prose that describes calling it does not.
- REASONING: "What is 9*7?". Counts if the reply carries reasoning tokens
  (`reasoning_content` or `reasoning`) or a <think> block.
- SERVED AS: the response's `model` field, compared with the model asked for.
  A relay that quietly serves something else would make every score about the
  wrong model, so a mismatch is recorded and shown.

ONLY POSITIVES ARE WRITTEN (config/model-learned.json, via metrics/learn.py).
A probe that errors, times out or is rate-limited proves nothing about the
model, so it is never stored as "cannot"; a rate limit sets a cooldown instead.
max_tokens is generous because reasoning models spend it thinking, and an
empty reply from an exhausted budget is a budget problem, not an answer.

Calls go straight to each model's endpoint (the same base, key and headers the
llm registry uses), not through `llm`, because the CLI hides the response's
model field and tool calls.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "metrics"))
sys.path.insert(0, str(ROOT / "suite"))
import endpoints  # noqa: E402
import learn  # noqa: E402

TOOL = {"type": "function", "function": {
    "name": "get_time", "description": "Get the current local time in a city.",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}},
                   "required": ["city"]}}}


def call(endpoint: str, served: str, body: dict, timeout: int = 180) -> dict:
    entry = endpoints.load()[endpoint]
    headers = {"Content-Type": "application/json", **(entry.get("headers") or {})}
    if (secret := endpoints.key(endpoint)):
        headers["Authorization"] = f"Bearer {secret}"
    req = urllib.request.Request(endpoints.base(endpoint) + "/chat/completions",
                                 data=json.dumps({"model": served, **body}).encode(),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _short(name: str) -> str:
    """Comparable model name: no vendor prefix, connection prefix or :free."""
    name = name.lower().split("/")[-1].removesuffix(":free")
    return re.sub(r"^[a-z0-9]+\.", "", name)        # llm7.codestral -> codestral


def swapped(requested: str, served: str | None) -> bool:
    if not served:
        return False
    a, b = _short(requested), _short(served)
    return not (a in b or b in a)


def probe(alias: str, served: str, endpoint: str, gap: int) -> list[str]:
    notes = []
    tries = [
        ("tools", {"messages": [{"role": "user", "content":
                   "What time is it in Paris right now? Use the get_time tool."}],
                   "tools": [TOOL], "tool_choice": "auto", "max_tokens": 1024}),
        ("reasoning", {"messages": [{"role": "user", "content": "What is 9*7? Reply with the number."}],
                       "max_tokens": 2048}),
    ]
    for i, (cap, body) in enumerate(tries):
        if i:
            time.sleep(gap)
        try:
            resp = call(endpoint, served, body)
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")
            learn.record(alias, f"{exc.code} {text}", "capability probe")
            notes.append(f"{cap}: HTTP {exc.code} (unknown)")
            continue
        except Exception as exc:                       # noqa: BLE001
            notes.append(f"{cap}: {type(exc).__name__} (unknown)")
            continue
        choice = (resp.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        if swapped(served, resp.get("model")):
            learn.note_served_as(alias, served, resp["model"])
            notes.append(f"served as {resp['model']}")
        if cap == "tools" and (msg.get("tool_calls") or choice.get("finish_reason") == "tool_calls"):
            learn.note_capability(alias, "tools", json.dumps(msg.get("tool_calls") or {})[:200])
            notes.append("tools: yes")
        elif cap == "reasoning" and (msg.get("reasoning_content") or msg.get("reasoning")
                                     or "<think>" in (msg.get("content") or "")):
            learn.note_capability(alias, "reasoning",
                                  str(msg.get("reasoning_content") or msg.get("reasoning") or "<think>")[:200])
            notes.append("reasoning: yes")
        else:
            notes.append(f"{cap}: not seen")
    return notes


def roster(only: str | None) -> list[dict]:
    from run import ad_hoc_models
    held = ad_hoc_models()
    rows = [r for r in endpoints.registry() if r["endpoint"] in endpoints.load()]
    if only:
        return [r for r in rows if r["alias"] == only]
    cooling = set(learn.cooldowns())
    return [r for r in rows if r["alias"] not in held and r["alias"] not in cooling]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model")
    ap.add_argument("--gap", type=int, default=30, help="seconds between calls")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    rows = roster(args.model)
    print(f"{len(rows)} models, 2 calls each, {args.gap}s apart")
    for n, r in enumerate(rows):
        if args.dry_run:
            print(f"  {r['alias']}  ({r['served']} on {r['endpoint']})")
            continue
        if n:
            time.sleep(args.gap)
        print(f"  {r['alias']:<34} {'; '.join(probe(r['alias'], r['served'], r['endpoint'], args.gap))}",
              flush=True)


if __name__ == "__main__":
    main()
