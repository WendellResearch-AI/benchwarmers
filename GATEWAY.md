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
Each image adds 2,500 tokens to the estimate (see Images).

## Images

A request whose messages carry OpenAI `image_url` parts, with an `https://`
URL or a `data:image/...;base64,` URL, goes to every seat with its messages
exactly as sent:

```json
{"model": "bench/vision", "messages": [{"role": "user", "content": [
  {"type": "text", "text": "What is this chair, and what might it fetch?"},
  {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}]}]}
```

- **Through the built-in client**, whatever `BENCH_CLIENT` says: the `llm`
  CLI takes a prompt, not inline images. A text-only request takes the same
  path as before.
- **Only seats that can see.** A model sits only if the catalog says it takes
  images: its input types from its endpoint's listing, models.dev or
  OpenRouter, or **Vision / OCR** ticked in its model popup. A member that
  cannot, or that the catalog knows nothing about, is not asked and is listed
  in `bench.skipped` with why (`{"seat", "alias", "why"}`; a grey chip on the
  Gateway card); so is a `with` nomination. Explorer seats go only to models
  that take images. With no seat that can see, the request is refused (400)
  rather than answered blind. Unknown counts as cannot because a model that
  cannot see answers anyway, describing a photo it never got, and the popup
  fixes it in one click. A model reached through the Responses API (a ChatGPT
  plan) gets no images yet.
- **A vision collection** is members that take images; `GET /v1/models` lists
  `vision` in its `together.capabilities` when every member does. An image
  request on a collection with no shape takes the shape `vision` (general and
  light models), so verdicts on image work are counted apart.
- **Size.** The body cap is 20 MB: a photo as a data URL is 4/3 of its file,
  and 20 MB is the tightest whole-request limit among the big providers
  (Gemini's for inline data). Above it the gateway answers 413 and says to
  send http(s) URLs or smaller images; models scale a photo down to about
  1,500-2,000 pixels on its long side anyway. Each image counts 2,500 tokens
  in the context check, which errs high: OpenAI's, Anthropic's and Gemini's
  models spend up to about 1,600 at their usual detail.
- **Never written down.** In `runs/gateway/calls.jsonl`, the offload log,
  learned limits, error text (an upstream may echo the request) and the
  dashboard, an image is a placeholder: `[image: data URL, 412 KB]`, or an
  http URL without its query string or user:password
  (`[image: https://photos.example/chair.jpg?...]`). The gateway never
  fetches an image URL; the upstream does.
- **Merged replies.** The merger gets the request as text with those
  placeholders, plus the seats' answers. Its job is to reconcile what the
  seats said, not to look again, so any model can merge and a disagreement
  about the picture is reported, not settled.

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

**Merged replies (opt-in).** A collection's `reply` is `all` (the default) or
`merged`; a request overrides it with `"bench": {"reply": "merged"}` in the
body, or a `:merged` / `:all` suffix on the model name for clients that can
only pick a name (`bench/reviewers:merged`), and `GET /v1/models` lists each
collection in its other mode too. Merged adds one call after the panel: a
merger writes a single answer saying where the seats agreed, where they
disagreed and who said what, and that becomes `content`. The merger is the
collection's `merger`, or when empty the member with the most useful verdicts
on the request's shape, else the router's primary, else the first member that
fits. It gets its own call id (`<panel>.merger`, in `bench.merger`, the
offload log and the Gateway card) so a verdict can judge the merge; `bench`
still carries every seat. If no merger has the context window for the
request plus every answer, or it fails, the reply is every answer with a
one-line note (`bench.note`). A streaming client gets it as one chunk, like
any reply.

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

## Who may call

| Where it runs | Set | The gateway asks for |
|---|---|---|
| This machine, as installed | nothing | no key: every caller is `local`, any bearer is ignored |
| This machine, one key per app | `BENCHWARMERS_REQUIRE_KEYS=1` | a gateway key; the dashboard stays open |
| LAN, Docker or cloud | `BENCHWARMERS_ADMIN_TOKEN`, `BENCH_BIND`, `BENCHWARMERS_PUBLIC_URL` | a gateway key on every `/v1/...` and `/bench/...`; the dashboard asks for the token |
| A browser app on another origin | `BENCH_ALLOWED_ORIGINS=https://app.example` (comma-separated), plus one of the rows above | as that row says |

**Gateway keys** are made on Settings, sent as `Authorization: Bearer <key>`,
shown once and kept as a SHA-256. Each has a name, written on every call it
makes (`key` in `calls.jsonl`, "Asked by" on the Gateway card), and a "last
used" on Settings. Optional, set when it is made: the **collections** it may
call (others are refused with 403, and `GET /v1/models` lists only its own)
and **requests a minute** (429 with `Retry-After` past it; counted per
process). Keys start `bwk_`, so a leaked one is recognisable, and Settings
shows each key's last four characters to tell which. A missing, malformed or
unknown key is refused with 401, a message saying which, and
`WWW-Authenticate: Bearer`.

**CORS** is answered on the gateway's routes only, for an allowed origin
(`BENCH_ALLOWED_ORIGINS`, and `BENCHWARMERS_PUBLIC_URL`'s): the preflight
allows `Authorization`, `Content-Type` and any other plain header names it
asks for (the OpenAI JS SDK sends `X-Stainless-*`), and every reply echoes
the origin with `Vary: Origin`. Never `*`, never credentials: the key travels
in a header, not a cookie. Such an origin may be cross-site; every other
browser request stays same-origin, and the dashboard answers no preflight. A
key in a page's code is visible to whoever opens it, so give a browser app a
key limited to its collection and a rate, or keep the key on the app's server.

## Components

| Part | Job |
|---|---|
| Gateway | `gateway.py`, stdlib server on 127.0.0.1. `GET /v1/models` lists the collections with description, tags and make-up; `POST /v1/chat/completions` runs one, images included. A streaming client gets the finished answer as one chunk. |
| Collections | `metrics/bench_collections.py` reads the two config files and computes explorer seats. |
| Seat router | `route.panel(members=…, explorers=n)` fills explorer seats by staleness for the shape. |
| Shape picker | Optional decision model (above). |
| Call log | `runs/gateway/calls.jsonl`, gitignored: every seat's answer, status, timing and call id. |
| Evidence | Each seat is recorded in `metrics/offloads.jsonl` as an unjudged offload when it answers; `offload.py verdict <call id>` or `POST /bench/verdict` replaces it. |
| Collections tab | The collections and their form, then the gateway's recent requests: every seat's status and verdict, and how many verdicts are owed. Clicking a seat opens its answer and the verdict form. |
| Catalog | `metrics/catalog.py`: context window, output limit, rate limits and capabilities per model, refreshed daily (scheduled task WRBench-Catalog); a free model whose price goes above zero raises a "went paid" alert. |
| Discover tab | `metrics/discover.py`: models.dev and FreeLLMAPI merged into one list of models worth adding, free first, marked by whether you can already reach the provider and whether the model is on the bench (on another provider counts as "also on bench"). |
| Merged replies | Opt-in: one merger answer as `content`, every seat still in `bench` (The reply). |

## Rules

- **The bench method only.** No single-model routing.
- **The caller judges.** Only the caller can check an answer against the real
  code. Models fail together, so agreement between seats is not a verdict.
- **Every seat is named** in the response, including ones that failed or came
  back empty, because who answered is itself evidence about the roster.
- **Free models only**, so rate limits are the constraint; a failed seat is
  reported and logged, not retried into the limit.
- **Privacy:** prompts go only to providers the bench already uses.
- **Bind to 127.0.0.1**, as the dashboard does. Elsewhere only with an admin
  token set, and then every call carries a gateway key made on the
  dashboard's Settings page (`Authorization: Bearer <key>`); see "Who may
  call". The dashboard answers the same routes on its own port, so one port
  can serve both (README, "Security").

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
  if a single-answer client needs the bench. (Revisited 2026-10-19 for chat
  UIs: merged replies, opt-in, with every seat still called, recorded and
  returned in `bench`, and the merge told to keep who said what.)
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
