# Bench gateway — design

Status: **built** (2026-10-16): `python gateway.py`, then point an OpenAI client
at `http://127.0.0.1:8123/v1` with a collection as the model, e.g. `bench/core`.
Since 2026-10-23 it also speaks Anthropic Messages, OpenAI Responses, Ollama,
Gemini and OpenAI's legacy completions, and streams each seat as it finishes
(Other API formats, Streaming).
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
  request on a collection with no shape takes the shape `vision` (general,
  light and vision models), so verdicts on image work are counted apart.
- **Vision specialists.** A model whose role is `vision` (set in its popup) is
  good at reading images and weak at text-only work, such as an OCR or
  document model. Only the `vision` shape admits it, and as a collection
  member it sits only when the request carries images; on a text request it
  is listed in `bench.skipped` and its seat goes to the next member or an
  explorer. Named in `with`, it sits anyway. A model that reads images and
  does everything else well stays `general`.
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
a streaming client gets it on the last chunk, the one with the finish_reason):

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
one-line note (`bench.note`). A streaming client gets the merged answer in one
piece once the merge is done (Streaming).

A verdict is `POST /bench/verdict` with `{"call", "useful": true|false, "note",
"real": [...], "false": [...]}`, `offload.py verdict <call id>`, or the verdict
dialog in the dashboard's Collections tab, under Gateway (click a seat). Sending the same verdict
twice writes nothing.

A client that weighs its seats by how they have done reads the verdicts back:
`GET /bench/record?collection=bench/vision&task=thicken slab faces` answers
`{"collection", "task", "aliases": {"<alias>": {"useful", "notUseful",
"judged", "lastAt"}}}`, counting each model's judged seats on that collection.
`task` (optional) keeps the verdicts whose note holds it. Counts only: never a
prompt, an answer or a note. thicken breaks ties between its vision seats with
it.

## Streaming

With `"stream": true` (or the format's own way of asking, below):

- **The 200 goes at once**: as soon as the panel is seated, and at the
  latest after a second (the shape picker can take longer). A refusal raised
  before then (too large for a member, an image no seat can see, no seat
  free) keeps its status and the format's error body, as without streaming.
- **Keep-alives** go every 10 seconds while the seats think, in the format's
  own form: an SSE comment for the OpenAI formats, Anthropic's `ping`, a
  chunk with no text for Ollama and Gemini (their readers have no comments).
  A client or proxy with an idle timeout keeps the connection.
- **Every answer** (`reply` all): the heading, then each seat's section the
  moment that seat finishes, then the verdict lines. The pieces are the same
  as the non-streamed reply's, with the seats in the order they finished
  rather than seat order.
- **Merged**: the merged answer in one piece when the merge is done (or
  every answer with its note, if the merge fails). Token by token waits for
  the built-in client's streaming.
- **An error after the 200** goes into the stream in the format's own error
  shape (the OpenAI SDKs' `{"error": ...}` chunk, Anthropic's `error` event,
  Responses' `error` and `response.failed`, Ollama's `{"error": ...}` line,
  Gemini's error object), so a client raises rather than hangs.
- **The `bench` field** rides on the last event where the format has room
  (the table below). It is in the JSON on the wire; an SDK that parses into
  fixed types may drop it (the Anthropic SDK's accumulated final message and
  the `ollama` library's replies do; read the raw event, or the call ids,
  which every reply's text carries). A caller that hangs up does not stop
  the panel: every seat still answers and is recorded.

## Other API formats

Each is a thin adapter around the same panel (`metrics/formats.py`): the
request becomes OpenAI-style messages, the panel runs exactly as on
`/v1/chat/completions` (collections, explorers, size check, images, keys,
scope, rate caps, CORS, the call log), and the reply goes back in the
format's own shape. The model field is the collection: `bench/core`, or
`bench/core:merged` for one merged answer. Clients that pick from a list
(`/api/tags`, `/v1beta/models`, `/v1/models`) see every collection in all three
reply modes.

| Format | Routes | Streams with | `bench` field | Known clients |
|---|---|---|---|---|
| OpenAI chat completions | `POST /v1/chat/completions`, `GET /v1/models` | `"stream": true` (SSE) | in the reply; the last chunk | OpenAI SDKs, Open WebUI's OpenAI connection, most chat apps |
| OpenAI Responses | `POST /v1/responses` | `"stream": true` (typed events) | in the response; `response.completed` | OpenAI SDKs' `responses.create`, Responses-only clients |
| OpenAI legacy completions | `POST /v1/completions` | `"stream": true` (SSE) | in the reply; the last chunk | older tools that send a `prompt` |
| Anthropic Messages | `POST /v1/messages`, `POST /v1/messages/count_tokens` | `"stream": true` (SSE events) | in the message; `message_delta` | Anthropic SDKs with `base_url`, tools built on them |
| Ollama | `POST /api/chat`, `POST /api/generate`, `GET /api/tags`, `GET /api/version`, `POST /api/show` | on unless `"stream": false` (NDJSON) | in the reply; the `done` line | Open WebUI's Ollama connection, the `ollama` libraries, Ollama-only apps |
| Gemini | `POST /v1beta/models/<collection>:generateContent`, `:streamGenerateContent` (`?alt=sse`, else a JSON array), `:countTokens`, `GET /v1beta/models` | the stream route | **headers**: `X-Bench-Panel`, `X-Bench-Seats` (call, seat, alias, status); none when streamed | Google GenAI SDKs with `base_url`, Gemini REST |

The base URL is the server itself (`http://127.0.0.1:8122` on the dashboard's
port, `:8123` for `gateway.py`): the Anthropic SDK and Google's add their own
`/v1/...` and `/v1beta/...`, Ollama clients their `/api/...`; OpenAI clients
take `.../v1`.

```bash
curl http://127.0.0.1:8122/v1/messages -H "Content-Type: application/json" \
  -d '{"model": "bench/core", "max_tokens": 1024, "messages": [{"role": "user", "content": "Where can this return a stale value? ..."}]}'
curl http://127.0.0.1:8122/v1/responses -H "Content-Type: application/json" \
  -d '{"model": "bench/core", "input": "Where can this return a stale value? ..."}'
curl http://127.0.0.1:8122/v1/completions -H "Content-Type: application/json" \
  -d '{"model": "bench/core", "prompt": "Where can this return a stale value? ..."}'
curl http://127.0.0.1:8122/api/chat -H "Content-Type: application/json" \
  -d '{"model": "bench/core", "stream": false, "messages": [{"role": "user", "content": "Where can this return a stale value? ..."}]}'
curl "http://127.0.0.1:8122/v1beta/models/bench/core:streamGenerateContent?alt=sse" -H "Content-Type: application/json" \
  -d '{"contents": [{"parts": [{"text": "Where can this return a stale value? ..."}]}]}'
```

Every POST needs `Content-Type: application/json`, as on chat completions
(it is what keeps a web page's plain form out).

```python
import anthropic
bench = anthropic.Anthropic(base_url="http://127.0.0.1:8122", api_key="local")   # or a gateway key
with bench.messages.stream(model="bench/core", max_tokens=1024,
                           messages=[{"role": "user", "content": "..."}]) as s:
    for text in s.text_stream:
        print(text, end="")              # each seat's section as that seat finishes

from google import genai
from google.genai import types
gem = genai.Client(api_key="local", http_options=types.HttpOptions(base_url="http://127.0.0.1:8122"))
print(gem.models.generate_content(model="bench/core:merged", contents="...").text)
```

In **Open WebUI**, add `http://127.0.0.1:8122` as an Ollama connection (or
`.../v1` as an OpenAI one): the collections appear as models.

**Keys.** Where keys are required, a gateway key goes as `Authorization:
Bearer`, or as `x-api-key` (the Anthropic SDK's `api_key`), `x-goog-api-key`
(the Google SDKs' `api_key`), or `?key=` on a Gemini route only (Google's REST
examples). The first one sent is judged. The access log never records a
query string or a key; neither does http.server's own error line.

**Images** in each format's shape become `image_url` parts, so only seats that
can see sit (Images): Anthropic `image` blocks (base64 or url), Responses
`input_image`, Ollama's `images` (base64, its type read from its first
bytes), Gemini `inlineData` and `fileData` at an http(s) URL.

**Refused, not dropped** (400, with why), because a client waiting for what
it asked would hang or loop on a panel's text:

- **Tools, in a panel.** A request carrying tools, or a conversation
  holding tool calls or tool results, in any format, is refused by a panel
  (`all` or `merged`) with a message pointing at route mode (next section),
  which takes them. An empty `tools` list is no tools.
- a tool only its own vendor runs (Anthropic's server tools such as
  `web_search_20250305` and their result blocks, OpenAI's hosted tools such
  as `web_search`, Gemini's Google Search, URL context and code execution),
  in route mode too: no other model can run them;
- the legacy `functions` / `function_call` fields: send `tools`;
- a PDF, audio or other non-image part; an image as an uploaded file id
  (Anthropic `file`, Responses `file_id`, Gemini's Files API);
- `previous_response_id` or a stored `conversation` on Responses: nothing is
  stored (`store` is answered `false`), so send the whole conversation;
- Ollama's `context` (a model's own token state): use `/api/chat`;
- more than one prompt on legacy completions.

**Ignored by a panel**: sampling options (`max_tokens`, `temperature`,
`top_p`, `stop` and their native names) are read but each seat keeps its own
call options; structured-output settings (Ollama `format`, Gemini
`responseSchema`, Responses `text.format`), since a panel answers in
markdown. Route mode passes both on. **Ignored everywhere**: a model's own
reasoning from an earlier turn (Anthropic thinking blocks, Responses
reasoning items, Gemini thought parts), and native parameters with no
OpenAI counterpart (Anthropic's `top_k`, `thinking`, `metadata`).

## Route mode (agent clients)

A panel answers with several models' text; an agent needs one model that
calls its tools. **Route mode** is that: no panel, one member of the
collection gets the request as sent — messages, tool calls and results,
`tools`, `tool_choice`, `parallel_tool_calls`, `response_format`,
temperature and the rest, images — and its answer is relayed in the
request's own format, streamed or not. Ask for it with `:route` on the
model (`bench/core:route`), `"bench": {"reply": "route"}`, or set a
collection's reply to *route* in the Collections dialog. `GET /v1/models`
(and `/api/tags`, `/v1beta/models`) lists every collection in all three
modes, so an agent's model picker can choose `bench/core:route`.

**Who answers.** Members only, no explorers: an agent's working session is
not the place to try an unknown model. Excluded, and named in
`bench.skipped` with why, as a panel excludes them: retired and ad hoc
members, members whose endpoint speaks another API than chat completions,
members the catalog does not know to call tools (when the request has
tools; the model popup's *Tool calling* is the override, like *Vision /
OCR*; the catalog's `tools` comes from OpenRouter's listed parameters,
models.dev, the endpoint's own listing or a capability probe that saw a
call), that cannot see (when it has images) or whose context window is
under the request (tools and tool calls counted). No member left: 400
(413 when size is why). Of those left, the best record goes first: useful
verdicts counted, then verified suite findings, then a connection already
paid for; members cooling down after a rate limit go last. One new
conversation in four goes instead to the member whose record is oldest, so
the record does not narrow to one model.

**Sticky.** A conversation stays on its model across turns: switching
mid-task breaks the agent's coherence and the provider's prompt cache. The
key is a hash of the collection, the gateway key, the system text and the
first user message, which an agent resends unchanged every turn; it lives
in memory and lapses after an hour idle (or a restart). A side request with
its own system prompt (Claude Code's command checks, a subagent, a title) is
its own conversation. If the conversation's model cannot take a turn (an
image arrives), the turn is re-ranked; the model that answers becomes the
conversation's.

**Failover.** If the member fails before the first piece of its answer
reaches the caller — a 429, a 5xx, a pacing refusal, nothing at all in 90
seconds, an empty reply (even after reasoning) — the next member in order
gets the turn, transparently, up to three; the caller sees only the answer,
and `bench.failed_over` names who failed and why. Reasoning streamed before
the answer is held back with it. A streamed turn's 200 waits up to 15
seconds for that first piece, so a turn that fails everywhere quickly keeps
its status (429 when every member was rate limited, else 502), then
keep-alives cover the wait. After the first piece, an error goes into the
stream in the format's error shape: no other model can continue a
half-written answer.

**Streams.** OpenAI chat completions relays the member's chunks as they
came, tool-call deltas exactly, under the collection's name, then a closing
chunk with no choice that carries `bench`. The other formats stream the
text as it comes and each tool call **whole when the turn ends** (an
Anthropic `tool_use` block with one `input_json_delta`, a Responses
`function_call` item, a Gemini `functionCall` part, an Ollama `tool_calls`
line), so calls whose deltas interleave still arrive intact. Usage is the
member's own count. An output budget over the model's known maximum is cut
to it (Claude Code asks every model for 32,000). Headers name the
conversation (`X-Bench-Conversation`) and, when known before the 200, the
model (`X-Bench-Model`).

**Translations.** Anthropic `tool_use` / `tool_result` blocks, Responses
`function_call` / `function_call_output` items, Gemini `functionCall` /
`functionResponse` parts and Ollama `tool_calls` / `tool` messages become
OpenAI tool calls and tool messages and back; tool definitions and
`tool_choice` likewise (Gemini's upper-case schema types lowered). Tool call
ids pass through both ways; Gemini and Ollama usually send none, so they
are numbered per request and a result matched to the oldest open call of
its name. A Responses **custom tool** (Codex's free-form `apply_patch`)
becomes a function taking one string, `input`, its grammar in the
description, and the call goes back as a `custom_tool_call`. An image in an
Anthropic tool result goes in a user message after it (a tool message
carries text only); `is_error` prefixes the result with `Error: `.

**Recording (the decision).** Every call is in the usage ledger (purpose
`route`), and every turn a line in `runs/gateway/routes.jsonl`: model,
status, seconds, tool calls, tokens, who failed over — never the prompt,
which an agent resends whole every turn. For the router, a conversation is
**one unjudged offload per model that answered it** (call id
`<conversation>.1`, `.2` after a failover to another model), not one per
turn: an agent session is dozens of turns, and a seat per turn would bury
the panels' verdicts. A verdict on a conversation is optional
(`POST /bench/verdict` with that call id); the offload alone already counts
toward rotation. The Gateway card lists route conversations in their own
table (when, who asked, the first message, who answered with ✓/✗, turns,
tool calls, failovers) and none of them adds to **Owed**.

**Shadows (later, opt-in).** A shadow seat would answer the same turn in the
background for evaluation only, after the caller has its answer. The place
is marked in `route_turn` ("SHADOWS GO HERE"): each would be its own client
call with purpose `shadow`, recorded on the conversation's call id; nothing
the caller sees would wait for or change because of them. Not built.

**Limits, honestly.**

- Members reached through native Anthropic, Gemini or Responses endpoints
  are not routed to: route mode speaks chat completions to the member.
- The member is a free or flat-rate model, not the model the client was
  built for. Claude Code and Codex are tuned for their own vendors' models;
  smaller models make more tool-call mistakes, and some providers reject a
  tool schema another accepts (that request fails over).
- Prompt caching is the provider's own: stickiness keeps the prefix on one
  provider, but `cache_control` marks are not passed on.
- Thinking is not shown to Anthropic, Responses, Gemini or Ollama clients
  (a model's reasoning is relayed raw only in chat completions).
- Tool calls arrive whole, not as deltas, outside chat completions.
- A caller that hangs up does not stop the member; its answer runs out and
  is recorded.
- Stickiness is per gateway process, in memory.

### Pointing agents at a collection

The base URL is the server: `http://127.0.0.1:8122` (the dashboard's port)
or `:8123` (`gateway.py`). On this machine any key text works; elsewhere,
or where keys are required, use a gateway key from Settings.

**Claude Code** (Anthropic Messages). It sends the model named by
`ANTHROPIC_MODEL` (or `--model`) for its main loop and a smaller "haiku"
model for side tasks (command checks, titles), so set both; a name it does
not know prints a harmless `unrecognized_model` note:

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:8122
export ANTHROPIC_API_KEY=<a gateway key, or any text on this machine>
export ANTHROPIC_MODEL=bench/core:route
export ANTHROPIC_DEFAULT_HAIKU_MODEL=bench/fast:route
export ANTHROPIC_DEFAULT_SONNET_MODEL=bench/core:route ANTHROPIC_DEFAULT_OPUS_MODEL=bench/core:route
export CLAUDE_CODE_SUBAGENT_MODEL=bench/core:route
claude
```

Checked end to end (2026-10-23): Claude Code 2.1.299 ran Read, Write and
Bash tool loops through `bench/core:route` and `bench/fast:route`, failing
over past a member's 429 on the first turn and staying on one model after.

**Codex and other Responses clients**: a provider with `base_url =
"http://127.0.0.1:8122/v1"`, `wire_api = "responses"`, the model
`bench/core:route`, and the gateway key as its API key. Function and custom
tools work; hosted tools (`web_search`, `local_shell`) are refused, so turn
them off. Nothing is stored: `previous_response_id` is refused, so the
client must send the whole conversation every turn.
Not checked against a real Codex yet.

**opencode, Cline, Continue, Roo, Aider and other OpenAI-compatible agents**:
an OpenAI-compatible provider at `http://127.0.0.1:8122/v1`, model
`bench/core:route` (it is in `/v1/models`). Agents that speak Ollama
(`http://127.0.0.1:8122`, model `bench/core:route`) work the same way. A
real `curl` with tools streamed a model's tool-call deltas through
unchanged; these clients themselves were not run.

**Counts and durations.** `usage` and the token-count routes
(`/v1/messages/count_tokens`, Gemini `:countTokens`) are the size check's
estimate, characters / 4 plus 10% and 2,500 a picture, with no model called.
Ollama's durations are real. `/api/version` reports the Ollama API version
whose shapes these follow (0.12.0), so clients send what it reads.

**On the dashboard's port**, Ollama's five `/api/` routes are the gateway's
(a gateway key, never the login cookie); the dashboard's own `/api/...`
routes never use those names.

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
| Gateway | `gateway.py`, stdlib server on 127.0.0.1. `GET /v1/models` lists the collections with description, tags and make-up; `POST /v1/chat/completions` runs one, images included, streamed seat by seat when asked. |
| Formats | `metrics/formats.py`: Anthropic Messages, Responses, legacy completions, Ollama and Gemini, each translated to and from OpenAI messages (Other API formats); the outbound half is for the built-in client to call those APIs natively, not wired yet. |
| Collections | `metrics/bench_collections.py` reads the two config files and computes explorer seats. |
| Seat router | `route.panel(members=…, explorers=n)` fills explorer seats by staleness for the shape. |
| Shape picker | Optional decision model (above). |
| Call log | `runs/gateway/calls.jsonl`, gitignored: every seat's answer, status, timing and call id. |
| Evidence | Each seat is recorded in `metrics/offloads.jsonl` as an unjudged offload when it answers; `offload.py verdict <call id>` or `POST /bench/verdict` replaces it. |
| Collections tab | The collections and their form, then the gateway's recent requests: every seat's status and verdict, and how many verdicts are owed. Clicking a seat opens its answer and the verdict form. |
| Catalog | `metrics/catalog.py`: context window, output limit, rate limits and capabilities per model, refreshed daily (scheduled task WRBench-Catalog); a free model whose price goes above zero raises a "went paid" alert. |
| Discover tab | `metrics/discover.py`: FreeLLMAPI (leading) and models.dev merged into one list of models worth adding, free first, FreeLLMAPI's smartest first, marked by whether you can already reach the provider and whether the model is on the bench (on another provider counts as "also on bench"). |
| Merged replies | Opt-in: one merger answer as `content`, every seat still in `bench` (The reply). |
| MCP server | `mcp_server.py`: the same three routes as MCP tools for agents, `list_collections`, `ask` and `verdict`. Two transports for one `answer()`: `POST /mcp` (or `/v1/mcp`) on either port, streamable HTTP with a JSON reply per request, its tools calling the server's own routes over loopback with the caller's credential and Host; and stdio (`benchwarmers mcp`), a caller like any other over HTTP (`BENCHWARMERS_URL`, `BENCHWARMERS_KEY`). The Claude Code plugin in `plugin/` connects to `/mcp` and adds a command, a skill and a `Stop` hook (`plugin/hooks/hooks.json`, an `mcp_tool` hook) that calls `pending_verdicts` with the session's `transcript_path`: the call ids in the transcript (Claude Code keeps no `Mcp-Session-Id`, so the transcript is the record of whose panels they were), of which the call log says which are answered seats with no verdict (`POST /bench/pending`), returned once as `additionalContext`. A server on another machine cannot read the transcript, so there the hook says nothing. A tool call answers as SSE with progress notifications, which reset Claude Code's 300-second idle limit. |

## Rules

- **The bench method only.** No single-model routing.
- **The caller judges.** Only the caller can check an answer against the real
  code. Models fail together, so agreement between seats is not a verdict.
- **Every seat is named** in the response, including ones that failed or came
  back empty, because who answered is itself evidence about the roster.
- **Free models only**, so rate limits are the constraint; a failed seat is
  reported and logged, not retried into the limit. A model the bench already
  knows has no room (a limit or quota it has counted, `metrics/pacing.py`) is
  not seated at all: an explorer seat goes to a model with room now, a member
  is named as not asked when its wait would outlast the seat, and a request
  with no seat left gets a 429.
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
