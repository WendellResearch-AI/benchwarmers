# Bench gateway — design

Status: **built** (2026-10-16): `python gateway.py`, then point an OpenAI client
at `http://127.0.0.1:8123/v1` with a collection as the model, e.g. `bench/core`.
Diagram: `.archify/architecture-bench-gateway-20261016-182500/bench-gateway.html`.

## The idea in one paragraph

The bench gets its own OpenAI-compatible endpoint whose only job is **the bench
method**: one prompt, several different models, every answer back, every
answer logged. You define **collections** — named groups of models such as
`core` or `reviewers` — on the bench. A caller asks for `bench/<name>` and the
prompt goes to every member of that collection plus one or two **explorer**
seats the bench fills by staleness, so the roster keeps getting evidence even
when the same collection is called every day. Each answer comes back labelled
by model and seat with a call id; the caller checks the answers and records a
verdict by call id, which lands in the evidence `route.py` reads. Using the
bench and measuring it stay the same act, without anyone retyping an offload.

Single-model calls are **not** the bench's job: chat and coding agents keep
calling the upstream relay directly. The relay stays the engine; the bench
gateway sits beside it for the multi-model work.

## Collections and explorer seats

```
explorers = min(max_explorers, max_calls - members)      defaults: 2 and 5
```

| Members | Explorers | Calls |
|---|---|---|
| 1 | 2 | 3 |
| 2 | 2 | 4 |
| 3 | 2 | 5 |
| 4 | 1 | 5 |
| 5 | 0 | 5 |

Two to four members is recommended. Explorers are never a member or a member's
model family. Collections live in `config/collections.json`, the limits in
`config/gateway.json`; both are edited in the dashboard's
**Collections** tab and every change is logged in `config/changes.jsonl`.

**Naming:** `bench/<name>`, lower-case letters, digits and `-`. The name says
what the collection is *for* (`reviewers`, `design`), not who is in it, because
members change. The `bench/` prefix keeps collections apart from real model ids
in clients that merge model lists.

**Choosing a collection by task:** each collection has a `description` (what
it is for, up to 300 characters) and `tags` (up to 8 lower-case words).
`GET /v1/models` returns them with the members, shape and explorer count, so an
orchestrator can list the collections and pick the one that fits before it
calls. Write the description for that reader.

**No default collection.** A request that names no collection, or one that
does not exist, is refused (400 / 404) with every collection's description and
tags in the message, so a caller that could not choose learns how to. A
`bench/auto` that guessed for it was considered and rejected: the caller knows
the task, and a guess would run the wrong models silently.

## Prompt size

The gateway estimates the prompt (system text included) at characters / 4 plus
10%, which errs high. If that is larger than any **member's** context window,
the request is refused with **413** naming each member that is too small and
its window, so the caller can trim the prompt or pick another collection;
`GET /v1/models` gives each collection's `together.context_window` (the smallest
known among its members) to check first. Explorer seats skip models too small
for the prompt, as well as retiring and rate-limited ones.

Context windows come from `metrics/catalog.py`: your overrides in the model
popup, then windows learned from real "maximum context length" errors (only
ever lower), then the daily refresh from each model's own endpoint listing,
models.dev and FreeLLMAPI. A model with no known window is never refused.

## The reply

`choices[0].message.content` is markdown: one section per seat with its call
id, status and time. Beside it, a `bench` field carries the same seats as data,
so a caller need not parse the markdown (OpenAI clients ignore unknown fields;
a streaming client gets it on the first chunk):

```json
"bench": {"panel": "p20261017-...", "collection": "bench/core", "shape": "review",
          "shape_by": "set on the collection",
          "seats": [{"call": "p20261017-....member1", "seat": "member", "alias": "…",
                     "status": "ok|empty|error", "seconds": 12.3, "answer": "…", "error": ""}],
          "verdict": "POST http://127.0.0.1:8123/bench/verdict {…}"}
```

A verdict is `POST /bench/verdict` with `{"call", "useful": true|false, "note",
"real": [...], "false": [...]}`, `offload.py verdict <call id>`, or the verdict
dialog in the dashboard's Collections tab, under Gateway (click a seat). Sending the same verdict
twice writes nothing.

## The decision model's job (optional)

A collection's members are fixed; only the explorer seats are chosen per
request, and they should go to models suited to the task — no code-completion
model on a design question. A collection may set its **shape** (review, design,
enumeration, …), and then that decides. Left on *automatic*, a decision model
labels the prompt, when one that takes choice questions is onboarded and not ad
hoc; otherwise, or on failure or confidence under 0.6, the shape is `general`.
With no explorer seats to fill, nothing is asked.

## Components

| Part | Job |
|---|---|
| Gateway | `gateway.py`, stdlib server on 127.0.0.1. `GET /v1/models` lists the collections with description, tags and make-up; `POST /v1/chat/completions` runs one. A streaming client gets the finished answer as one chunk. |
| Collections | `metrics/bench_collections.py` reads the two config files and computes explorer seats. |
| Seat router | `route.panel(members=…, explorers=n)` fills explorer seats by staleness for the shape. |
| Shape picker | Optional decision model (above). |
| Call log | `runs/gateway/calls.jsonl`, gitignored: every seat's answer, status, timing and call id. |
| Evidence | Each seat is recorded in `metrics/offloads.jsonl` as an unjudged offload when it answers; `offload.py verdict <call id>` or `POST /bench/verdict` replaces it. |
| Collections tab | The collections and their form, then the gateway's recent requests: every seat's status and verdict, and how many verdicts are owed. Clicking a seat opens its answer and the verdict form. |
| Catalog | `metrics/catalog.py`: context window, output limit, rate limits and capabilities per model, refreshed daily (scheduled task WRBench-Catalog); a free model whose price goes above zero raises a "went paid" alert. |
| Discover tab | `metrics/discover.py`: models.dev and FreeLLMAPI merged into one list of models worth adding, free first, marked by whether you can already reach the provider and whether the model is on the bench (on another provider counts as "also on bench"). |
| Council (not built) | Would return one merged answer instead of every answer; see Decisions. |

## Rules

- **The bench method only.** No single-model routing.
- **The caller judges.** Only the caller can check an answer against the real
  code. Models fail together, so agreement between seats is not a verdict.
- **Every seat is named** in the response, including ones that failed or came
  back empty, because who answered is itself evidence about the roster.
- **Free models only**, so rate limits are the constraint; a failed seat is
  reported and logged, not retried into the limit.
- **Privacy:** prompts go only to providers the bench already uses.
- **Bind to 127.0.0.1**, as the dashboard does.

## What is and isn't new

A git-for-me search (2026-10-16) found gateways (mozilla-ai/otari, Gozar,
NadirClaw), councils (karpathy/llm-council, togethercomputer/MoA) and
observability platforms (opik). None serves a curated multi-model collection as
an OpenAI model name with exploration seats, and none closes the loop from a
caller's verdict back into who gets seated next. LiteLLM was rejected: a second
copy of the alias table and a plugin to write bench evidence.

## Decisions (2026-10-16)

- **No council route, for now.** A council would have the members review each
  other and one model merge the result into a single answer. That suits a
  caller that cannot read several answers; every caller so far can, and the
  orchestrator verifies against the code better than a merging model. A merge
  also hides who said what, which is the evidence the bench runs on. Revisit
  if a single-answer client needs the bench.
- **Collections replace `bench/panel`.** Every request names a collection;
  the bench adds the explorers.
- **No single-model routes**, which also dropped the shadow worker and judge
  model from the first draft: they learned from single-model traffic that no
  longer passes through the bench.
- **Decision models** are onboarded into `config/decision-models.json` with
  the question types they accept, and called through the endpoint they were
  picked from. Option descriptions must be given, because bare names mislead
  them (Mercury Decide called an enumeration `code` at 0.93).
- **Provider paths differ:** Inception and OpenRouter call the API
  `decisions`, Ollama `systemone`. Open WebUI's passthrough appends the path to
  the connection's base, so its URL carries `{path}` and onboarding records the
  one that answered.

## Reviews so far

- `bench/panel` reviewed `gateway.py`: Mercury Decide picked `review` at 0.99;
  two seats found real defects, two found nothing, two agreed on a streaming
  hang that was false.
- `bench/core` reviewed the collections change: 3 members + 2 explorers, shape
  `review` at 0.97; four seats found real defects (fixed), one came back empty,
  and three asserted a duplicate-seat bug the code already prevents.
