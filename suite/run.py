"""Run the local task suite against the configured models.

Each task is something that actually happened, with an answer that was
verified against source or a live database before it went into the code. So a
score here is "would this model have found what we found", which is the only
question worth asking of a model we are about to hand work to.

Scoring is keyword-based on purpose. A rubric graded by a model is a rubric
that model can be wrong about; a string either appears in the output or it
does not. Crude, reproducible, and it cannot flatter anybody.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "metrics"))
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
TASKS = DATA / "suite" / "tasks"
RUNS = DATA / "runs"

# THE CODEBASES THIS BENCHMARK IS DRAWN FROM.
#
# Private repositories, which is the entire point: nobody trained on them, so
# a score here cannot be inflated by memorisation the way every public
# benchmark's can. That is a property no public suite can offer at any price,
# and it gets stronger as more real work goes in.
#
# Tasks name a repo with `"repo": "<key>"`, else DEFAULT_REPO. They point at
# REAL FILES rather than copies, so a fixture cannot quietly go stale while the
# thing it copied moves on — the cost being that a task must be re-verified
# when the code under it changes. Which folder each key means is the install's
# own: config/repos.json in the data folder, `{"<key>": "<path>"}`. A key it
# does not list falls back to the task's own fixtures/ copies, which is how
# the public starter pack runs with no repositories at all.
DEFAULT_REPO = "default"


def repos() -> dict[str, pathlib.Path]:
    try:
        data = json.loads((DATA / "config" / "repos.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: pathlib.Path(v) for k, v in data.items() if not k.startswith("_") and isinstance(v, str)}


REPOS = repos()


class MissingAttachment(Exception):
    """A task's file is in neither its repo nor its fixtures: skip that task."""

def registered_models() -> list[str]:
    """Every alias in the canonical yaml, newest registration last.

    Derived rather than listed. A hardcoded default went stale the moment
    models were added: it named five while twenty-two were registered, so a
    scheduled run would have quietly covered a quarter of them and reported
    nothing missing. A list that has to be edited alongside another list is a
    list that will disagree with it. Empty on a fresh install (no yaml yet).
    """
    import endpoints
    return list(endpoints.registrations())


def ad_hoc_models() -> set[str]:
    """Aliases that are only ever called BY NAME — never picked automatically.

    Declared in `config/model-roles.json` under `ad_hoc`. These are the
    expensive or tightly capped models: a daily sweep or a rotation that
    reached them would spend the cap on a benchmark and leave nothing for the
    moment someone actually wanted them. So every automatic selection skips
    them, and an explicit `--model`, `--with` or `llm -m` still works.
    """
    import json
    try:
        data = json.loads((DATA / "config" / "model-roles.json").read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return set(data.get("ad_hoc", []))


def available_models(cache: dict | None = None,
                     include_ad_hoc: bool = False) -> list[str]:
    """Registered, still served, and answering — the ones worth spending on.

    THERE IS NO CURATED DEFAULT LIST ANY MORE. There was one, of five models,
    written when five were registered; by the time twenty-two were it named a
    quarter of them and looked perfectly healthy doing it. A list that must be
    edited whenever another list changes is a list that will disagree with it.

    Three filters, each removing a different kind of waste:

    1. REGISTERED  — in the canonical yaml.
    2. STILL SERVED — present on the endpoint now. This is the deprecation
       check, applied automatically: `laguna-m1` was retired upstream and
       `gpt-oss-120b` never existed under that name, and both were being sent
       46,000-character prompts to earn a `0/11`.
    3. STILL ANSWERING — the last perf reading recorded at least one answer.
       `mistral-large` (subscription), `mistral-medium` and `mistral-small`
       (quota) are all SERVED and all refuse, so being on the endpoint is not
       enough on its own.

    And, unless `include_ad_hoc`, NOT AD HOC — see `ad_hoc_models`. Every
    automatic picker comes through here, so this is the one place that keeps
    them away from those models. A caller that is honouring an explicit request
    by name passes `include_ad_hoc=True`.

    **`perf` deliberately does NOT use this** — it runs everything registered
    and served, because a model that starts answering again is discovered by
    asking it, and a tier that skipped failing models would never notice a
    recovery.
    """
    import csv
    models = registered_models()
    if not include_ad_hoc:
        held = ad_hoc_models()
        models = [m for m in models if m not in held]
    served = _served_model_names(cache)
    if served is not None:
        names = _registered_names()
        models = [m for m in models if names.get(m) in served]

    history = DATA / "perf" / "history.csv"
    if history.exists():
        last: dict[str, bool] = {}
        with history.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                # Later rows win: a model that recovered is back in.
                last[row["model"]] = last.get(row["model"], False) or                     int(row["answered"] or 0) > 0
        # A model with NO perf history is kept: unmeasured is not the same as
        # failing, and excluding it would make it permanently invisible.
        models = [m for m in models if last.get(m, True)]
    return models


def images_only() -> set[str]:
    """Aliases whose role is vision or image (model_roles). No text shape
    would ever seat them, so the text and code tasks are not theirs: their
    answers only spent calls and put a reader of images on the code charts.
    Routing still sees them (available_models), where a vision model sits
    for images."""
    import model_roles
    return {a for a, r in model_roles.load()["models"].items() if r in ("vision", "image")}


def sees_images(alias: str) -> bool:
    """Whether the catalog says `alias` takes images, by the gateway's rule
    (gateway.cannot_see): its input types, the Vision / OCR override in its
    model popup, or the capability seen working. One the catalog knows
    nothing about is not asked: a model that cannot see answers anyway,
    describing a picture it never got, and that would score as reading badly."""
    import catalog
    import endpoints
    if endpoints.entry_of(alias).get("api") == "responses":    # the client sends it no images yet
        return False
    return bool(catalog.info(alias).get("vision"))


def applies(spec: dict, model: str, skip: set[str] | None = None, seeing: dict | None = None) -> bool:
    """Whether `spec` is a task for `model`: a vision task (one with images)
    for a model that takes them, any other for one that is not vision or
    image only. So a vision model's suite is the vision tasks, a general one
    that takes images gets both, and a text-only one never sees a picture.
    `skip` (images_only()) and `seeing` (alias -> sees_images) save asking
    again in a loop."""
    if spec.get("images"):
        if seeing is not None and model in seeing:
            return seeing[model]
        return sees_images(model)
    return model not in (images_only() if skip is None else skip)


def tasks_for(model: str, tasks: list[dict]) -> list[dict]:
    skip, seeing = images_only(), {}
    if any(t.get("images") for t in tasks):
        seeing[model] = sees_images(model)
    return [t for t in tasks if applies(t, model, skip, seeing)]


def _registered_names() -> dict[str, str]:
    """alias -> the model name the endpoint knows it by; {} with no yaml yet."""
    import endpoints
    return {alias: r.get("model_name", "") for alias, r in endpoints.registrations().items()}


def _served_model_names(cache: dict | None = None) -> set[str] | None:
    """What the endpoint offers right now, or None if it cannot be asked.

    None rather than an empty set on failure, and the caller treats that as
    "do not filter". An unreachable endpoint is not evidence that every model
    was retired.
    """
    if cache is not None and "served" in cache:
        return cache["served"]
    sys.path.insert(0, str(ROOT / "metrics"))
    import endpoints
    served: set[str] = set()
    for name in endpoints.load():
        try:
            served |= set(endpoints.models(name, timeout=20))
        except Exception:
            # The default endpoint down says nothing about any model: do not
            # filter. Another one down only drops its own models for today.
            if name == endpoints.default():
                return None
    if cache is not None:
        cache["served"] = served
    return served


def _note_benchmark(model: str, calls: int) -> None:
    """Report benchmark calls to the usage counter, never fatally."""
    try:
        sys.path.insert(0, str(ROOT / "metrics"))
        from cost import note_benchmark_calls
        note_benchmark_calls(model, calls)
    except Exception:                              # noqa: BLE001
        pass                                       # a counter is not the job


def llm_binary() -> str:
    found = shutil.which("llm")
    if found:
        return found
    fallback = pathlib.Path.home() / ".local" / "bin" / "llm.exe"
    if fallback.exists():
        return str(fallback)
    sys.exit("llm CLI not found. See the README.")


def bench_python() -> str:
    """The interpreter for child scripts: the repo's venv when there is one
    (Windows or POSIX layout), else the one running now. The dashboard may run
    on another Python, and onboarding's children want the venv's packages; a
    hardcoded `.venv\\Scripts\\python.exe` blocked onboarding on macOS, Linux
    and Docker."""
    for venv in (ROOT / ".venv" / "Scripts" / "python.exe", ROOT / ".venv" / "bin" / "python"):
        if venv.exists():
            return str(venv)
    return sys.executable


def free_models() -> list[str]:
    """Registered models on OpenRouter's shared free budget."""
    served = _registered_names()
    return [m for m in registered_models() if ":free" in served.get(m, "")]


def next_free(tasks_dir: pathlib.Path) -> str | None:
    """The `:free` model most in need of a suite score — ONE per run.

    `free-models-per-day` is 50 without credits and is shared across every
    `:free` model, so a full sweep of nine models over four tasks is 36 calls
    and crowds out everything else for the day. Rotating one model per run
    costs four, which fits alongside perf with room left.

    Never-scored first, then least-recently, so a newly registered free model
    is picked up on the next run rather than after a full cycle.
    """
    best, best_age = None, None
    held, specs = ad_hoc_models(), load_tasks(None)
    for model in free_models():
        if model in held or not tasks_for(model, specs):
            continue
        outputs = list(tasks_dir.glob(f"*/output/{model}.md"))
        if not outputs:
            return model
        age = max(o.stat().st_mtime for o in outputs)
        if best_age is None or age < best_age:
            best, best_age = model, age
    return best


def next_model(tasks_dir: pathlib.Path) -> str | None:
    """The PAID model most in need of a suite score — ONE per run.

    THIS TIER MATCHES HOW THE MODELS ARE ACTUALLY USED, which is why it holds
    the recurring slot and `funcbench/` no longer does. Every offload recorded
    in `metrics/offloads.jsonl` is a single-shot review: a large document goes
    in, prose comes back, and the operator verifies it. Nothing here passes a
    tool schema -- there is no `tools=` anywhere in this harness -- so a
    tool-calling benchmark was measuring a capability this bench never
    exercises. It ranked the roster close to backwards: `muse-glimmer-30b`
    scored nothing at all on it and gave the best answer of the panel on real
    work the same day.

    `:free` models are DELIBERATELY EXCLUDED and left to `--next-free`. They
    share one 50-calls-a-day budget, `--next-free` already rotates them at six
    calls a run, and letting this rotation pick them too would spend that
    budget twice over.

    Order of need, most urgent first:

      1. never scored -- a newly registered model, or one nothing has run
      2. INCOMPLETE, before merely old. Thirteen models sit at four of six
         tasks, frozen on 2026-09-04, missing `no-writes-here` and
         `observation-boundary` because both were written after that sweep.
         A gap is a worse blind spot than a stale number: an old score is
         still evidence, a missing one is none.
      3. oldest, so the set refreshes on its own

    Complete means every task that is the model's (tasks_for): a vision
    model is complete on the vision tasks alone.
    """
    specs = load_tasks(None)
    free = set(free_models())
    best, best_key = None, None
    for model in available_models():
        if model in free:
            continue
        mine = tasks_for(model, specs)
        if not mine:
            continue
        outputs = [p for p in (t["dir"] / "output" / f"{model}.md" for t in mine) if p.exists()]
        if not outputs:
            return model                      # never scored: go first
        key = (len(outputs) >= len(mine),     # False (incomplete) sorts first
               max(o.stat().st_mtime for o in outputs))
        if best_key is None or key < best_key:
            best, best_key = model, key
    return best


PER_RUN = 6        # tasks per scheduled run (--next, --next-free)


def pick_tasks(tasks: list[dict], model: str, n: int = PER_RUN) -> list[dict]:
    """What a scheduled run asks its one model: the tasks it has no saved
    answer for, else its oldest answers, at most `n`.

    The schedule was sized for six tasks a run; the suite has grown to over
    fifty, and running all of them each time redid every answer the model
    already had (2026-10-20: 52 calls on one free model at 6am, more than
    OpenRouter's whole free day, and a paid run stopped at its two-hour limit).
    Every result is saved, a failure included, so a gap is a task never asked,
    or one whose saved answer is a rate limit or quota (`capacity`: the
    afternoon, not the model, as pace.py treats it). A failure that is the
    model's own (a context too small, a timeout) stays: asking again would
    only repeat it. Once there are no gaps the oldest answers are refreshed,
    which keeps the scores current."""
    def answer(spec):
        return spec["dir"] / "output" / f"{model}.md"            # where main() saves it

    def gap(spec):
        saved = answer(spec)
        return (not saved.exists()
                or failure_class(saved.read_text(encoding="utf-8", errors="replace")) == "capacity")
    missing = [s for s in tasks if gap(s)]
    if missing:
        return missing[:n]
    return sorted(tasks, key=lambda s: answer(s).stat().st_mtime)[:n]


SHIPPED = HERE / "tasks"
SHIPPED_PREFIX = "vision-"


def seed_shipped() -> None:
    """Copy the vision tasks this checkout ships (suite/tasks/vision-*: made
    images with their answer keys, suite/vision_tasks.py) into the data
    folder, and keep their prompt, key and images current there. The saved
    answers (output/) are the install's and are never touched. Other tasks
    are the install's own and are not shipped this way."""
    if SHIPPED.resolve() == TASKS.resolve():
        return
    for task in SHIPPED.glob(f"{SHIPPED_PREFIX}*/task.json"):
        for src in [task, *sorted((task.parent / "fixtures").glob("*"))]:
            dst = TASKS / src.relative_to(SHIPPED)
            if not dst.exists() or dst.read_bytes() != src.read_bytes():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)


def load_tasks(only: str | None) -> list[dict]:
    seed_shipped()
    tasks = []
    for path in sorted(TASKS.glob("*/task.json")):
        spec = json.loads(path.read_text(encoding="utf-8"))
        spec["dir"] = path.parent
        spec.setdefault("id", path.parent.name)
        if only and spec["id"] != only:
            continue
        # A mined task is a draft until someone has read it (suite/mine.py).
        if spec.get("draft") and not only:
            continue
        tasks.append(spec)
    if only and not tasks:
        sys.exit(f"no task called {only!r}")
    return tasks


def build_prompt(spec: dict) -> str:
    parts = [spec["prompt"].strip(), ""]
    key = spec.get("repo", DEFAULT_REPO)
    root = REPOS.get(key)
    for rel in spec.get("attach", []):
        source = root / rel if root else None
        if not (source and source.exists()):
            source = spec["dir"] / "fixtures" / pathlib.Path(rel).name
        if not source.exists():
            raise MissingAttachment(
                f"{spec['id']}: attachment not found: {rel}"
                + ("" if root else f" (no repo {key!r} in config/repos.json, and no fixture)"))
        parts += [f"===== FILE: {rel} =====",
                  source.read_text(encoding="utf-8", errors="replace"), ""]
    return "\n".join(parts)


MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def image_urls(spec: dict) -> list[str]:
    """A vision task's pictures (`images`, files in its fixtures/) as data:
    URLs, the way the gateway passes a request's images on."""
    import base64
    urls = []
    for name in spec.get("images", []):
        path = spec["dir"] / "fixtures" / name
        if not path.exists():
            raise MissingAttachment(f"{spec['id']}: image not found: fixtures/{name}")
        mime = MIME.get(path.suffix.lower(), "image/png")
        urls.append(f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}")
    return urls


# A model that was never allowed to answer has not answered badly, and a
# service that fell over has not answered at all. Reporting either as 0/N is a
# lie about the model — and reporting them as the SAME thing is a second lie,
# because one is a fact about the model and the other is a fact about
# Tuesday afternoon.
#
# `nemotron-ultra-primary` returned "Service temporarily overloaded" in 11
# seconds and was filed beside a safety-filter block until somebody read it.
REFUSALS = (
    "blocked by gemini's filters",
    "i can't help with that",
    "i cannot help with that",
    "i'm not able to help",
    "against my guidelines",
    "unable to assist",
)
ERRORS = (
    "service temporarily overloaded",
    "rate limit",
    "internal server error",
    "bad gateway",
    "connection reset",
    "502",
    "503",
    # --- added 2026-09-04, after a 16-model run scored four of them 0/11 ----
    #
    # Every one of these came back in FOUR SECONDS against a 46,000-character
    # prompt and was recorded as a model that read the code and found nothing.
    # They are the gateway refusing before a model ever saw the question:
    #
    #   gpt-oss        "Model not found"        -- a stale registration
    #   mistral-large  "not available in your subscription tier"
    #   laguna-m1      "No endpoints found for poolside/laguna-m.1:free"
    #   qwen3.8-27b    "Request too large ... ITPM: Limit 7000"
    #
    # The `llm` CLI prefixes all of them with `Error: Error code: NNN`, which
    # is the reliable signal; the rest are spelled out so a changed prefix
    # cannot silently reopen this.
    "error: error code:",
    "model not found",
    "not available in your subscription",
    "no endpoints found",
    "request too large",
    "insufficient_quota",
    "quota exceeded",
    "context length",
    "too many requests",
    # --- added 2026-09-08, after a dead CLI scored as an answer -------------
    #
    # Every marker above is the PROVIDER failing. These two are the CLIENT
    # failing, and nothing here recognised that class at all. `llm` exiting
    # non-zero produced `Error: 'utf-8' codec can't encode character ...` --
    # 93 characters, over `min_length`, matching no marker -- so `outcome()`
    # returned None and the floor tier sanitised the error text into a
    # "solution" and graded it wrong.
    #
    # `Error: Error code:` was described above as "the reliable signal". It is
    # reliable for UPSTREAM errors only; a client-side crash also begins
    # `Error:` and goes no further. Ordered last so an upstream reason still
    # wins the `detail` when both are present.
    "error: llm exited",
    # The built-in client's counterpart (metrics/client.py, BENCH_CLIENT=builtin).
    "error: client failed",
    "codec can't encode",
    "request limit reached",
)


# WHY A FAILURE HAPPENED decides whether it is evidence about the MODEL or
# about the afternoon, and the two look identical from the outside.
#
# STRUCTURAL   the model is not there. No such name, no provider behind it.
#              Waiting changes nothing and the registration should go.
# ENTITLEMENT  it exists; this account may not call it. A plan decision, not a
#              fault — worth keeping registered to notice if it changes.
# CAPACITY     it is there and busy, throttled, or the prompt is too big for
#              its window. Temporary by nature -- worth retrying, and worth
#              retrying tomorrow.
# TIMEOUT      no answer inside the budget. Says NOTHING about existence: a
#              slow model and a queued request are indistinguishable here.
#
# The distinction matters because a model absent from the endpoint's CATALOGUE
# is deprecated, while one that is listed and failing might be either. A
# listed model failing STRUCTURALLY every time is retired upstream and the
# catalogue has not caught up -- which is exactly what `laguna-m1` did.
# SIGNIN       the key itself was refused: 401, or a sign-in metrics/connect.py
#              could not renew. Fixed on the Endpoints page, never by retiring.
SIGNIN = (
    "error code: 401",
    "sign in again",
    # ChatGPT's plan cannot share usage with apps: the account's, every model at once.
    "sharing_usage_unavailable",
)
STRUCTURAL = (
    "model not found",
    "no endpoints found",
    "does not exist",
    "unknown model",
    "invalid model",
    "model_not_found",
)
# ENTITLEMENT is its own class, and the difference decides what to DO.
# `structural` means the model is not there and the registration should go.
# This means it IS there and this account may not call it — a pricing or plan
# decision. `mistral-large` is exactly this, and the operator is keeping it
# registered on purpose to notice if the tier ever changes. Reporting it as a
# retirement would advise deleting the very thing being watched.
ENTITLEMENT = (
    "not available in your subscription",
    "not authorized",
    "unauthorized",
    "permission denied",
    "no access to model",
    # Credit exhausted or never funded. The model is there and the account
    # cannot call it, which is an entitlement question rather than a rate
    # limit — waiting does not clear it, adding balance does.
    "insufficient balance",
    "insufficient credit",
    "payment required",
    # OpenRouter gates some free endpoints to apps on its own published list.
    # The model exists and works; this account's CLIENT is not eligible.
    "only available on agentic harnesses",
    "only available on",
)
# PAYWALLED is A FORM OF DEPRECATION HERE, which is why it is not folded into
# ENTITLEMENT. The two look alike -- the model exists and this account cannot
# call it -- but the ACTION is opposite, and the action is the whole point of
# separating failure kinds in this file.
#
#   entitlement  keep it registered and watch. `mistral-large` sits behind a
#                subscription tier that could change, and noticing that change
#                is worth a registration doing nothing in the meantime.
#   paywalled    a `:free` slug retired behind a paid one. THIS BENCH DOES NOT
#                BUY PAID TIERS FOR THIS, so the model is gone for our
#                purposes as surely as if it had been deleted. Waiting does
#                not fix it: the free endpoint is not coming back. Re-point
#                the registration at the paid slug and pay, or drop it.
#
# Two models did this within days of each other, both on OpenRouter, both
# still listed in the catalogue and both returning 400 on every call:
#
#   minimax-m3-free  -> use this slug instead: minimax/minimax-m3
#   glm-5.2-free     -> use this slug instead: z-ai/glm-5.2
#
# Filed as `other` and reported by nothing until 2026-09-09.
PAYWALLED = (
    "unavailable for free",
    "use this slug instead",
    "the paid version is available",
    # OpenRouter's 404 when a launch-only `:free` ends (minimax-m3.1-flash-free,
    # 2026-10-22): "The free launch period ... ended ... variant has been retired".
    "free launch period",
    "variant has been retired",
)
CAPACITY = (
    "rate limit",
    "quota exceeded",
    "insufficient_quota",
    # ChatGPT's plan: the weekly cap the user set for this app, or the plan's own.
    "usage_limit_exceeded",
    "too many requests",
    "request too large",
    "context length",
    "service temporarily overloaded",
    "internal server error",
    # OpenRouter's own wording when the upstream provider behind a `:free`
    # model fails. Says nothing about the model — transient, and retryable.
    "provider returned error",
    "bad gateway",
    "502",
    "503",
    # NVIDIA's shared worker being full: "ResourceExhausted: Worker local
    # total request limit reached (33/32)". Other people's load, not ours, and
    # not the model going away -- `laguna-xs-2.1` failed 21 of 44 probes this
    # way, filed as `other`, which the 3-day auto-retire rule would act on.
    "request limit reached",
    "resourceexhausted",
)


# EVERY CLASS `failure_class` CAN RETURN, WORST FIRST, IN ONE PLACE.
#
# `perf/run.py` kept its own copy of this tuple to pick the worst class a
# model showed across samples, and when `paywalled` was added the copy was not
# updated -- so `failure_class` correctly returned "paywalled", the lookup
# found no match, and the row was written with an EMPTY failure. That is worse
# than a wrong class: `check_deprecations` skips blank failures and keeps
# whatever it saw last, so two retired models silently kept their old
# classification. Imported rather than repeated, so the next class added
# cannot reintroduce this.
FAILURE_CLASSES = ("structural", "paywalled", "entitlement", "capacity",
                   "timeout", "empty", "other")


def failure_class(output: str, min_length: int = 40) -> str | None:
    """`signin`, `structural`, `paywalled`, `entitlement`, `capacity`,
    `timeout`, or None if the model answered.

    `min_length` carries the same meaning as in `outcome`, and for the same
    reason: without it a correct one-word reply falls through to the final
    check and is classified `other`. Any caller asking about a short-answer
    prompt must pass it.
    """
    head = output.strip().lower()[:400]
    if "<<timed out" in head:
        return "timeout"
    # FIRST, because a refused key says nothing about the model: an expired
    # sign-in (Pollinations keys last days) or a revoked key fails every model
    # on the endpoint at once, and `entitlement` retired them the same day.
    for marker in SIGNIN:
        if marker in head:
            return "signin"
    for marker in STRUCTURAL:
        if marker in head:
            return "structural"
    # BEFORE entitlement, because a paywall message can read like one and the
    # two carry opposite advice -- act on it, versus keep it and wait.
    for marker in PAYWALLED:
        if marker in head:
            return "paywalled"
    for marker in ENTITLEMENT:
        if marker in head:
            return "entitlement"
    # A credit/quota notice delivered as a success reply: the account, not the
    # model -- and `other` would count it toward auto-retirement.
    if provider_notice(output):
        return "entitlement"
    for marker in CAPACITY:
        if marker in head:
            return "capacity"
    # NOTHING CAME BACK AT ALL. Usually a reasoning model that spent its whole
    # output budget thinking (the inkling pair at max_tokens 8000), which is a
    # budget problem, not a missing model -- so it is kept out of `other`,
    # which the deprecation check counts toward auto-retirement.
    if not head:
        return "empty"
    return None if outcome(output, min_length) is None else "other"


# A 200 THAT IS NOT AN ANSWER. Some relays return a quota or credit notice as
# the reply's TEXT with a success status, so nothing upstream flags it and it
# would be scored as a model that answered badly. Only short replies are read
# this way: a real answer that discusses quotas is long. (Learned from
# FreeLLMAPI's openai-compat provider, 2026-10-17.)
PROVIDER_NOTICES = (
    "run out of credits", "out of credits", "insufficient credits", "credit balance",
    "upgrade your plan", "upgrade to continue", "upgrade your account",
    "exceeded your current quota", "quota has been exhausted", "daily limit reached",
    "you have reached your", "free tier limit", "please add credits", "top up your",
)


def provider_notice(output: str) -> str | None:
    """The notice phrase, if a short reply is a provider's quota/credit notice."""
    text = output.strip().lower()
    if len(text) > 400:
        return None
    return next((m for m in PROVIDER_NOTICES if m in text), None)


def outcome(output: str, min_length: int = 40) -> tuple[str, str] | None:
    """(kind, detail) for a non-answer, or None if the model actually spoke.

    `min_length` is the shortest reply that could be a real answer. The default
    suits this suite, whose tasks all demand tables or prose — but it is a
    property of the QUESTION, not of answering. The perf tier asks for a single
    word, and the default scored a correct `ready` as an empty response, which
    made a healthy endpoint look like it was failing every call.
    """
    lowered = output.strip().lower()
    head = lowered[:400]
    if "<<timed out" in head:
        return ("timeout", "no answer within the budget")
    for marker in ERRORS:
        if marker in head:
            # Retryable, and NOT a fact about the model.
            return ("error", marker)
    if notice := provider_notice(output):
        return ("error", f"provider notice in a success reply: {notice}")
    for marker in REFUSALS:
        if marker in head:
            return ("refused", marker)
    if len(lowered) < min_length:
        return ("error", "empty response")
    return None


def format_check(spec: dict, output: str) -> tuple[bool, str]:
    """Did the answer arrive in the SHAPE the prompt demanded?

    Separate from correctness on purpose, because the two failures look
    identical in a recall score and are not the same problem.
    `codestral-latest` returned 13,000 characters on `satellite-writes` and
    scored 0/11 — not because it did not know, but because it never named a
    function, so nothing could be matched. `gemini-3.8-flash-high` returned 150
    characters on `edit-points`: a table header and nothing else.

    A model that is right but unparseable is useless in a pipeline and fine in
    a chat. That is a routing decision, and it needs its own number.

    Declared per task under `format`, since only the prompt knows what it
    asked for.
    """
    want = spec.get("format") or {}
    if not want:
        return True, ""
    text = output.strip()
    rows = [ln for ln in text.splitlines() if ln.lstrip().startswith("|")]
    if want.get("table") and len(rows) < want.get("min_rows", 2):
        return False, f"expected a markdown table, found {len(rows)} row(s)"
    if len(text) < want.get("min_chars", 0):
        return False, (f"{len(text)} chars, under the {want['min_chars']} a "
                       f"real answer to this needs")
    for banned in want.get("must_not_contain", []):
        if banned.lower() in text.lower():
            return False, f"contains {banned!r}, which the prompt excluded"
    return True, ""


def score(spec: dict, output: str) -> dict:
    """Recall against the verified findings, and invention against reality."""
    # THE SHORTEST VALID ANSWER IS A PROPERTY OF THE QUESTION. `outcome`
    # already said so in its own docstring and this call ignored it, so the
    # calibration task — whose correct answer is the 18 characters
    # "NO DATABASE WRITES" — scored four models as having returned an empty
    # response when all four were exactly right. Third time this class of bug
    # has appeared here; it is declared per task now rather than assumed.
    non_answer = outcome(output, spec.get("min_answer_chars", 40))
    if non_answer:
        kind, detail = non_answer
        return {"found": 0, "of": len(spec.get("must_find", [])),
                "missed": [], "invented": [],
                "blocked": f"{kind}: {detail}", "kind": kind,
                "format_ok": None, "format_why": ""}
    lowered = output.lower()
    # `score_in: "first_line"`: a claim-verification task asks for its verdict
    # on the first line, so only that line is scored. Reasoning that wanders
    # towards TRUE and ends FALSE is judged on what it committed to first.
    if spec.get("score_in") == "first_line":
        lowered = next((ln.strip() for ln in lowered.splitlines() if ln.strip()), "")

    def present(clause) -> bool:
        # A clause is a string, {"any_of": [...]} where any spelling of the
        # same finding counts, or {"regex": "..."} where a substring cannot be
        # exact ("fault: line 1" is a substring of "fault: line 12").
        if isinstance(clause, dict) and "regex" in clause:
            return re.search(clause["regex"], lowered) is not None
        if isinstance(clause, dict):
            return any(alt.lower() in lowered for alt in clause["any_of"])
        return clause.lower() in lowered

    findings = spec.get("must_find", [])
    found = [c for c in findings if present(c)]
    missed = [c for c in findings if not present(c)]

    # INVENTION. Anything asserted here is not true of the code, so saying it
    # is a fabrication rather than a miss. This is the score that matters:
    # a confident wrong answer costs more than a silent one.
    #
    # But WHERE it is said decides whether it was asserted at all. On
    # `satellite-writes`, kimi-k3 wrote "writes in wave_contact_rows ... are
    # out of scope and not listed" -- demonstrating precisely the
    # understanding the trap tests -- and a whole-output substring match
    # scored it as a fabrication. That is the scorer inventing, not the model.
    #
    # `must_not_claim_in: "table"` confines the check to markdown table rows,
    # where a task asks for its answer. Naming the item in a row is the claim;
    # naming it in prose to rule it out is the opposite of one.
    claim_text = lowered
    if spec.get("must_not_claim_in") == "table":
        claim_text = "\n".join(ln for ln in lowered.splitlines()
                               if ln.lstrip().startswith("|"))

    # A NEGATED MENTION IS NOT A CLAIM. "NO DATABASE WRITES: there are no
    # INSERT or DELETE statements" is the right answer to `no-writes-here`, and
    # a bare substring match scored it as inventing an INSERT. (Latent when
    # found on 2026-10-16: every stored answer was terse enough to escape it.)
    def claimed(clause) -> bool:
        if isinstance(clause, dict) and "regex" in clause:
            return re.search(clause["regex"], claim_text) is not None
        alts = clause["any_of"] if isinstance(clause, dict) else [clause]
        return any(asserted(claim_text, alt.lower()) for alt in alts)

    invented = [c for c in spec.get("must_not_claim", []) if claimed(c)]

    shape_ok, shape_why = format_check(spec, output)
    return {
        "found": len(found), "of": len(findings),
        "missed": [_label(c) for c in missed],
        "invented": [_label(c) for c in invented],
        "blocked": None, "kind": "answered",
        # Shape, judged apart from substance. A low recall WITH a format
        # failure means "could not be read"; a low recall with a clean format
        # means "did not know".
        "format_ok": shape_ok, "format_why": shape_why,
    }


NEGATION = re.compile(r"\b(no|not|never|none|without|zero|cannot|can't|isn't|aren't|"
                      r"doesn't|don't|didn't|false|disproved|neither|nor)\b")


def asserted(text: str, phrase: str) -> bool:
    """True if `phrase` occurs in `text` at least once WITHOUT a negation
    earlier in the same sentence (within 60 characters)."""
    start = 0
    while (i := text.find(phrase, start)) != -1:
        before = text[max(0, i - 60):i]
        before = re.split(r"[.!?\n]", before)[-1]      # this sentence only
        if not NEGATION.search(before):
            return True
        start = i + 1
    return False


def _label(clause) -> str:
    if isinstance(clause, dict):
        return clause.get("label") or clause.get("regex") or clause["any_of"][0]
    return clause


# THE DIRECT PATH IS GONE, since 2026-10-15. The inkling pair used to bypass
# Open WebUI here, with OpenRouter's key read live out of Open WebUI's config.
# They are now ordinary registrations on the `openrouter` endpoint
# (config/endpoints.json), with the attribution headers in their yaml entry and
# max_tokens in config/model-options.json, so `llm -m` reaches them like any
# other model.


def _child_env() -> dict:
    """The environment the `llm` CLI must run in, or its output is corrupted.

    WE READ UTF-8; THE CHILD WROTE CP1252. When `llm`'s stdout is a pipe --
    which it always is under `capture_output=True` -- Windows gives the child
    `locale.getpreferredencoding()`, i.e. cp1252, while the `encoding="utf-8"`
    below decodes what comes back as UTF-8. The two disagreed, and that broke
    in two directions:

      CRASH      an em dash, curly quote or non-breaking hyphen has no cp1252
                 encoding, so the CLI died mid-write. `nemotron-lightning`
                 returned 378 bytes of a real answer and then
                 `charmap codec can't encode '\\u2011'`.
      CORRUPTION anything cp1252 COULD encode (accented letters) arrived as
                 bytes this end then decoded as UTF-8, and `errors="replace"`
                 quietly turned them into replacement characters.

    The crash was the worse of the two, because `outcome()` does not recognise
    a CLIENT-side failure: the message is over `min_length` and matches no
    marker in `ERRORS`, so a dead CLI scored as `answered` and the truncated
    text was graded as the model's work. An infrastructure failure recorded as
    model incompetence is exactly the confusion this repo exists to prevent.

    Forcing UTF-8 on the child makes both ends agree. `PYTHONUTF8` covers
    interpreters that ignore `PYTHONIOENCODING`.
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def ask_once(binary: str, model: str, prompt: str,
             timeout: int, images: list[str] | None = None) -> tuple[str, float]:
    # BENCH_CLIENT=builtin: the same call without the `llm` subprocess, giving
    # the same text for the same failure (metrics/client.py).
    if str(ROOT / "metrics") not in sys.path:
        sys.path.insert(0, str(ROOT / "metrics"))
    import client
    if images:
        # Pictures go as the API's own image parts, through the built-in
        # client whichever is chosen: `llm -m` reads a prompt, not parts.
        import formats
        content = [formats.text_part(prompt), *(formats.image(u) for u in images)]
        res = client.chat(model, "", timeout=timeout, messages=[{"role": "user", "content": content}],
                          purpose="vision task")
        return client.output(res, timeout), res["seconds"]
    if client.backend(model) == "builtin":
        res = client.chat(model, prompt, timeout=timeout)
        return client.output(res, timeout), res["seconds"]
    started = time.monotonic()
    try:
        out = subprocess.run(
            [binary, "-m", model], input=prompt, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout,
            env=_child_env())
        body = out.stdout or out.stderr
        # A NON-ZERO EXIT IS FREE EVIDENCE AND WAS BEING THROWN AWAY. On a
        # mid-stream crash `out.stdout` holds real but TRUNCATED text, so
        # `stdout or stderr` prefers it and the failure becomes invisible --
        # it reads as a model that simply stopped early. The exit code is the
        # only signal that separates those, so it decides, not the text --
        # except when it is only llm failing to log an answer it printed in
        # full, its logs.db locked by another llm (metrics/client.py).
        if out.returncode != 0 and not client.llm_log_failed_only(out.stdout, out.stderr):
            detail = (out.stderr or body or "").strip().replace("\n", " ")
            body = f"Error: llm exited {out.returncode}: {detail[:300]}"
    except subprocess.TimeoutExpired:
        body = f"<<TIMED OUT after {timeout}s>>"
    return body, time.monotonic() - started


def ask(binary: str, model: str, prompt: str, timeout: int,
        attempts: int = 3, images: list[str] | None = None) -> tuple[str, float, int]:
    """Ask, retrying UPSTREAM FAILURES only.

    A rate limit or an overloaded service is a fact about the afternoon, not
    about the model, and retrying is the only way to tell one from the other.
    A refusal is not retried — a filter will refuse again — and neither is a
    timeout, because retrying a five-minute wait turns one slow run into
    fifteen minutes of the same answer.
    """
    total = 0.0
    for attempt in range(1, attempts + 1):
        body, seconds = ask_once(binary, model, prompt, timeout, images)
        total += seconds
        kind = (outcome(body) or ("answered", ""))[0]
        if kind != "error" or attempt == attempts:
            return body, total, attempt
        # Back off before trying again: hammering a service that just said it
        # was overloaded is how one failure becomes three.
        time.sleep(5 * attempt)
    return body, total, attempts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--task")
    ap.add_argument("--model", action="append", dest="models")
    ap.add_argument("--timeout", type=int, default=420)
    ap.add_argument("--repeat", type=int, default=1,
                    help="ask each model the same task N times and report the "
                         "SPREAD. Every score in this suite has been n=1, so a "
                         "model that scores 10/11 once and 6/11 the next time "
                         "reads identically to one that scores 8/11 twice — "
                         "and they are not interchangeable")
    ap.add_argument("--next-free", action="store_true", dest="next_free",
                    help="score ONE OpenRouter :free model — the one most in "
                         "need. For a daily timer: the free tier is 50 calls "
                         "a day shared across all of them, so a full sweep "
                         "would spend most of the budget in one go")
    ap.add_argument("--next", action="store_true", dest="pick_next",
                    help="score ONE paid model — the one most in need. For a "
                         "timer: never-scored first, then INCOMPLETE, then "
                         "oldest. This is the tier that matches how these "
                         "models are actually used, so it is the one worth "
                         "automating. `:free` models are left to --next-free")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    tasks = load_tasks(args.task)
    if args.next_free:
        chosen = next_free(TASKS)
        if chosen is None:
            sys.exit("no :free model registered")
        args.models = [chosen]
        tasks = pick_tasks(tasks_for(chosen, tasks), chosen)
        print(f"--next-free chose {chosen}: {len(tasks)} tasks "
              f"(one model per run; the free tier is 50 calls a day, shared)")

    if args.pick_next:
        chosen = next_model(TASKS)
        if chosen is None:
            sys.exit("no paid model registered")
        args.models = [chosen]
        mine = tasks_for(chosen, tasks)
        have = sum((t["dir"] / "output" / f"{chosen}.md").exists() for t in mine)
        tasks = pick_tasks(mine, chosen)
        print(f"--next chose {chosen} ({have}/{len(mine)} tasks scored; "
              f"{len(tasks)} this run)")

    models = args.models or [m for m in available_models()
                             if m not in free_models() or args.next_free]
    binary = None if args.dry_run else llm_binary()

    # A task whose files cannot be found is skipped, with a line saying why;
    # it used to end the whole run.
    ready = []
    for spec in tasks:
        try:
            ready.append((spec, build_prompt(spec), image_urls(spec)))
        except MissingAttachment as exc:
            print(f"skipped {exc}")
    tasks, prompts = [s for s, _, _ in ready], {s["id"]: p for s, p, _ in ready}
    pictures = {s["id"]: u for s, _, u in ready}
    # Each pair only where the task is the model's (applies): pictures to a
    # model that takes them, text and code to one that is not images only.
    skip = images_only()
    seeing = {m: sees_images(m) for m in models} if any(pictures.values()) else {}
    pairs = {(s["id"], m) for s in tasks for m in models if applies(s, m, skip, seeing)}
    left = len(tasks) * len(models) - len(pairs)
    if left:
        print(f"{left} task/model pair(s) left out: vision tasks go to models that take images,"
              " text and code tasks to models that are not vision or image only")

    if args.dry_run:
        for spec in tasks:
            prompt = prompts[spec["id"]]
            print(f"=== {spec['id']} ({len(prompt):,} chars) ===")
            print(prompt[:600])
            print("...\n")
        return 0

    RUNS.mkdir(exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d")
    report = RUNS / f"suite-{stamp}.md"
    rows = []

    for spec in tasks:
        prompt = prompts[spec["id"]]
        if not any((spec["id"], m) in pairs for m in models):
            continue
        print(f"\n## {spec['id']} — {spec['title']}")
        print(f"   {len(prompt):,} chars, {len(spec.get('must_find', []))} findings\n")
        for model in models:
            if (spec["id"], model) not in pairs:
                continue
            # N attempts at the SAME question. The spread is the point: a
            # single sample is a measurement of one afternoon, and this suite
            # has been reporting those as though they were properties.
            takes = []
            for _ in range(max(1, args.repeat)):
                body, seconds, attempts = ask(binary, model, prompt,
                                              args.timeout, images=pictures[spec["id"]])
                takes.append((score(spec, body), seconds, attempts, body))
                if args.repeat > 1:
                    time.sleep(2)
            result, seconds, attempts, body = takes[0]
            scored = [t[0]["found"] for t in takes
                      if t[0]["kind"] == "answered"]
            row = {"task": spec["id"], "model": model,
                   "seconds": round(seconds, 1), "attempts": attempts,
                   **result}
            if args.repeat > 1:
                row["takes"] = len(takes)
                row["found_min"] = min(scored) if scored else None
                row["found_max"] = max(scored) if scored else None
                # The number that decides whether a single score can be
                # trusted at all.
                row["spread"] = ((max(scored) - min(scored))
                                 if len(scored) > 1 else 0)
            rows.append(row)
            # Tell the counter these were MEASUREMENT, not work. Without it a
            # model's call count says how much it was benchmarked and reads as
            # how much it was relied on — the opposite of the truth.
            _note_benchmark(model, len(takes))
            # A courtesy gap. The first baseline fired fifteen calls back to
            # back, which is itself a plausible way to trip a shared limit.
            time.sleep(2)
            tries = f" x{attempts}" if attempts > 1 else ""
            if args.repeat > 1 and row.get("found_min") is not None:
                tries += (f"  [{row['found_min']}-{row['found_max']} over "
                          f"{row['takes']}]")
            if result["blocked"]:
                _learn(model, body, "suite")
                print(f"   {model:24} {result['kind'].upper():8} {seconds:6.1f}s{tries}"
                      f"  ({result['blocked'].split(': ', 1)[1]})")
            else:
                flag = ("  INVENTED " + ", ".join(result["invented"])
                        if result["invented"] else "")
                print(f"   {model:24} {result['found']}/{result['of']}"
                      f"  {seconds:6.1f}s{tries}{flag}")
            (spec["dir"] / "output").mkdir(exist_ok=True)
            (spec["dir"] / "output" / f"{model}.md").write_text(body, encoding="utf-8")

    _write_report(report, stamp, rows)
    print(f"\nwritten: {report}")
    return 0


def _learn(model: str, body: str, where: str) -> None:
    """Pass a failed call's text to metrics/learn.py: a context window or rate
    limit the error states, and a cooldown if it was rate-limited. Never lets
    a learning failure break a run."""
    try:
        sys.path.insert(0, str(ROOT / "metrics"))
        import learn
        learn.record(model, body, where)
    except Exception:                                  # noqa: BLE001
        pass


def _write_report(path: pathlib.Path, stamp: str, rows: list[dict]) -> None:
    lines = [f"\n## Suite run {stamp}\n",
             "| Task | Model | Found | Invented | Seconds |",
             "| --- | --- | --- | --- | --- |"]
    for r in rows:
        invented = ", ".join(r["invented"]) if r["invented"] else "—"
        found = (r.get("kind") if r.get("blocked")
                 else f"{r['found']}/{r['of']}")
        tries = f" (x{r.get('attempts', 1)})" if r.get("attempts", 1) > 1 else ""
        lines.append(f"| {r['task']} | `{r['model']}` | {found}"
                     f" | {invented} | {r['seconds']}{tries} |")
    non_answers = [r for r in rows if r.get("blocked")]
    if non_answers:
        lines += ["", "**Not a score — the model did not answer:**"]
        for r in non_answers:
            lines.append(f"- `{r['model']}` on {r['task']}: {r['blocked']}")
        lines += ["",
                  "`error` is retryable and says nothing about the model — it "
                  "was already retried up to three times before appearing "
                  "here.",
                  "",
                  "**A `timeout` is not clean evidence either.** From the "
                  "client there is no difference between a model that is slow "
                  "and a request that queued behind a rate limit. Treat "
                  "timeouts as \"did not answer through this path today\", "
                  "not as a property of the model."]
    misses = [r for r in rows if r["missed"]]
    if misses:
        lines += ["", "**Missed:**"]
        for r in misses:
            lines.append(f"- `{r['model']}` on {r['task']}: {', '.join(r['missed'])}")
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(existing + "\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
