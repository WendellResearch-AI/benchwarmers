"""Ask a decision model a typed question: choose, score, or yes/no.

    python metrics/decide.py mercury-decide-free "List every place serve.py writes a file." \\
        --choice review,design,enumeration,code
    python metrics/decide.py span-01-lite-free "Please refund the duplicate charge." \\
        --noul "Is this about billing?"

DECISION MODELS ARE NOT CHAT MODELS
-----------------------------------
Mercury Decide and Respan take a `state` and a map of typed `questions` and
return each answer with a probability. They refuse /chat/completions outright
("is a decisions model and cannot be used with the chat/completions endpoint"),
so `llm -m` cannot reach them, the availability probe and the suite cannot
test them, and they never go in the llm yaml. They live in
`config/decision-models.json` instead, written by `scripts/onboard.py` when a
model answers a typed question.

WHERE THE CALL GOES
-------------------
Each endpoint in `config/endpoints.json` may carry a `decisions` URL. Open
WebUI has one only through its `/openai/{path}` passthrough, which forwards to
`{connection base}/decisions` and answers only when the server runs with
ENABLE_OPENAI_API_PASSTHROUGH=True. That reaches Inception
(`api.inceptionlabs.ai/v1/decisions`) but never OpenRouter, whose Decisions API
is `/api/alpha/decisions`, outside its `/api/v1` base.

A model is called on the endpoint it was picked from when onboarding, recorded
as `via`: picked from Open WebUI, it goes through Open WebUI; picked from
`openrouter (direct)`, it goes direct. OpenRouter's /models omits its decision
models, so that endpoint names them in `unlisted_models` to make them pickable.

WHAT EACH MODEL CAN ANSWER
--------------------------
`question_types` lists the types a model accepted when onboarding probed it
with one question of each: Mercury Decide takes noul, choice and score;
Respan takes noul only, with plain-string instructions and criteria. `ask`
refuses a type the entry does not list, so a caller learns it from the bench
rather than from a 400. Both providers take the same body (`model`, `state`,
`questions`) and return the same `answers`, so one client covers them.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
FILE = DATA / "config" / "decision-models.json"
sys.path.insert(0, str(ROOT / "metrics"))
import endpoints  # noqa: E402

# One question of each type, plain strings throughout so Respan's rule holds.
PROBES = {
    "noul": {"type": "noul", "instructions": "Is this message about billing?",
             "criteria": {"true": "It is about charges, payments or refunds.",
                          "false": "It is about something else."}},
    "choice": {"type": "choice", "instructions": "Which team should handle this message?",
               "criteria": {"billing": "Charges, payments, refunds.",
                            "support": "Product problems.", "sales": "Buying new things."}},
    "score": {"type": "score", "instructions": "How urgent is this message?",
              "criteria": ["Can wait", "Needs attention soon", "Needs immediate attention"]},
}
PROBE_STATE = "Please refund the duplicate charge on my account today."
# Providers name the same API differently: Inception and OpenRouter
# `decisions`, Ollama `systemone`. An endpoint URL holding `{path}` (Open
# WebUI's passthrough, which appends it to the connection's base) is tried
# with each, and the one that answered is recorded on the model as `path`.
PATHS = ("decisions", "systemone")


def load() -> dict[str, dict]:
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def call(endpoint: str, served: str, state, questions: dict, timeout: int = 60,
         path: str = "decisions") -> dict:
    """POST to an endpoint's decisions URL. Returns the parsed body; raises on HTTP errors."""
    entry = endpoints.load()[endpoint]
    url = (entry.get("decisions") or "").replace("{path}", path)
    if not url:
        raise ValueError(f"{endpoint} has no decisions URL in config/endpoints.json")
    headers = {"Content-Type": "application/json", **(entry.get("headers") or {})}
    if (secret := endpoints.key(endpoint)):
        headers["Authorization"] = f"Bearer {secret}"
    body = json.dumps({"model": served, "state": state, "questions": questions}).encode()
    # Inception answers 429 to calls in quick succession (seen 2026-10-16:
    # the second of three onboarding probes), so a 429 waits and retries.
    for wait in (5, 15, 0):
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or not wait:
                raise
            time.sleep(wait)


def error_text(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        try:
            detail = json.loads(exc.read() or b"{}")
        except ValueError:
            detail = ""
        # {"error": {"message": ...}}, {"error": "..."}, {"detail": ...}: providers differ.
        if isinstance(detail, dict):
            detail = detail.get("error") or detail
        if isinstance(detail, dict):
            detail = detail.get("message") or detail
        return f"{exc.code} {str(detail)[:160]}".strip()
    return f"{type(exc).__name__}: {exc}"


def probe(endpoint: str, served: str) -> tuple[str | None, str]:
    """(None, path) if `endpoint`'s decisions URL answers a yes/no question for
    `served`, else (why not, ""). Each of PATHS is tried where the URL has `{path}`."""
    url = endpoints.load()[endpoint].get("decisions")
    if not url:
        return f"{endpoint} has no decisions URL in config/endpoints.json", ""
    why = []
    for path in (PATHS if "{path}" in url else PATHS[:1]):
        try:
            answer = call(endpoint, served, PROBE_STATE, {"probe": PROBES["noul"]}, path=path)
        except Exception as exc:                       # noqa: BLE001
            why.append(f"{endpoint} /{path}: {error_text(exc)}")
            continue
        if "probe" in answer.get("answers", {}):
            return None, path
        why.append(f"{endpoint} /{path}: no answer in {str(answer)[:120]}")
    return "; ".join(why), ""


def refuses_chat(endpoint: str, served: str) -> bool:
    """True if a one-token chat call is refused because the model is a decision
    model ("is a decisions model and cannot be used with the chat/completions
    endpoint", as OpenRouter words it, relayed by Open WebUI)."""
    entry = endpoints.load()[endpoint]
    headers = {"Content-Type": "application/json", **(entry.get("headers") or {})}
    if (secret := endpoints.key(endpoint)):
        headers["Authorization"] = f"Bearer {secret}"
    body = json.dumps({"model": served, "max_tokens": 1,
                       "messages": [{"role": "user", "content": "ready?"}]}).encode()
    request = urllib.request.Request(endpoints.base(endpoint) + "/chat/completions",
                                     data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60):
            return False
    except urllib.error.HTTPError as exc:
        # OpenRouter says "is a decisions model"; other providers word it their
        # own way, so any refusal that names decisions or systemone counts.
        text = error_text(exc).lower()
        return any(w in text for w in ("decisions model", "decision model", "systemone", "/decisions"))
    except Exception:                                  # noqa: BLE001
        return False


def question_types(endpoint: str, served: str, path: str = "decisions") -> list[str]:
    """Which question types the model accepts, one probe call per type.

    noul is taken as proven: `probe` has just had one answered, and asking
    again only lets a transient 429 record `[]`, which `ask` would then read
    as "takes nothing"."""
    ok = ["noul"]
    for kind, question in PROBES.items():
        if kind in ok:
            continue
        try:
            if kind in call(endpoint, served, PROBE_STATE, {kind: question}, path=path).get("answers", {}):
                ok.append(kind)
        except Exception:                              # noqa: BLE001
            pass
    return ok


def ask(alias: str, state, questions: dict) -> dict:
    entry = load()[alias]
    wrong = sorted({q["type"] for q in questions.values()} - set(entry["question_types"]))
    if wrong:
        raise ValueError(f"{alias} does not take {', '.join(wrong)} questions;"
                         f" it takes {', '.join(entry['question_types'])}")
    return call(entry["via"], entry["served"], state, questions, path=entry.get("path", "decisions"))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("alias", help="a key of config/decision-models.json")
    ap.add_argument("state", help="the text being judged")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--noul", metavar="QUESTION", help="a yes/no question")
    group.add_argument("--choice", metavar="A,B,C", help="comma-separated options")
    group.add_argument("--score", metavar="L0,L1,L2", help="comma-separated levels, lowest first")
    ap.add_argument("--instructions", default="Answer the question about this text.")
    args = ap.parse_args()
    if args.alias not in load():
        sys.exit(f"{args.alias} is not in {FILE.name}; onboard it first: scripts/onboard.py <served id>")
    if args.noul:
        q = {"type": "noul", "instructions": args.noul,
             "criteria": {"true": "Yes.", "false": "No."}}
    elif args.choice:
        q = {"type": "choice", "instructions": args.instructions,
             "criteria": {o.strip(): None for o in args.choice.split(",")}}
    else:
        q = {"type": "score", "instructions": args.instructions,
             "criteria": [o.strip() for o in args.score.split(",")]}
    try:
        print(json.dumps(ask(args.alias, args.state, {"q": q})["answers"]["q"], indent=1))
    except ValueError as exc:
        sys.exit(str(exc))
    except Exception as exc:                           # noqa: BLE001
        sys.exit(error_text(exc))


if __name__ == "__main__":
    main()
