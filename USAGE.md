# Using Benchwarmers

Day-to-day use, once it runs: the dashboard's pages, onboarding models,
collections, connecting your apps and recording verdicts. Setting it up is in
the README's [Try it](README.md#try-it) (or [AGENTS.md](AGENTS.md), for a
coding agent doing it with you); the gateway's whole contract is in
[GATEWAY.md](GATEWAY.md).

## Contents

- [The dashboard](#the-dashboard)
- [Onboarding models](#onboarding-models)
- [Collections](#collections)
- [Connecting an app](#connecting-an-app)
- [Recording verdicts](#recording-verdicts)
- [Discover: finding models to add](#discover-finding-models-to-add)
- [Quota management](#quota-management)
- [The scheduler](#the-scheduler)
- [Your data folder](#your-data-folder)

## The dashboard

`python serve.py`, then http://localhost:8122. The sidebar groups its pages:

| Group | Page | What it is for |
| --- | --- | --- |
| Observe | **At a glance** | Every model on the bench: whether it works right now, its role, and its connection. Click a name for its popup: role, alias, facts, options, retire. |
| | **Benchmarks** | How models do on the suite and on real work: bugs found against false alarms, who is confidently wrong, and speed. |
| | **Quota management** | A tile per provider: what is left, when it resets, and which of its models are resting. Then the week's resets, and what the bench sent. |
| | **Logs** | Gateway calls (who sat and how it went, never the prompt), changes made from the dashboard, the scheduler, and onboarding runs. |
| Gateway | **Endpoints** | Where models are served from. Add an endpoint (a provider's sign-in, or any OpenAI-compatible API key), change a key, and see what each one serves. Also your own app registrations and rate limits per connection. |
| | **Models** | Every model on the bench, by the connection it comes from. |
| | **Collections** | The named panels your apps ask for, and the gateway's settings. |
| | **Discover** | Models worth adding, from public catalogs, with the ways this install can reach each one. |
| Control | **Settings** | Theme, gateway keys, the scheduler, the model client, providers whose free models work only in their own app, how the install is reached (shown only), and Restart. |
| | **Info** | Version, Python, data folder and counts. |

"Action needed", above the pages, lists what will not fix itself, such as a
model about to be retired, naming each model and where it comes from, with
Retry, Open and Retire now beside it. Hide folds it away until something new
turns up; an alert whose models have all been retired goes at once.

**Restart** (Settings, Server and access), or `bench restart`, starts the
dashboard again on the code now on disk, after an update. It waits for
onboarding, a scheduled job and gateway calls in progress (`--force` cuts them
off), and Settings says when newer code is on disk. It needs no service
manager: `serve.py` restarts its own dashboard.

## Onboarding models

**Onboard a model**, top right, lists everything your endpoints serve that is
not on the bench yet. Search, and tick one or several: the ticks survive a new
search, so you can gather models from different connections in one go.
Dismiss hides one you never want offered (an image model, say); the dismissed
list brings it back. Each endpoint's list is kept an hour, with its age and a
Refresh, and a model already queued or onboarding is not offered again.

Each model is registered, probed with four short calls, and, if it answers,
run through the suite. **Onboard as**, under the list, chooses how much that
spends:

- **Full**: the probe, then the suite now.
- **Full + ad hoc**: the probe and the suite now, once, so routing has scores
  for it; after that the bench never calls the model on its own (no explorer
  seat, no scheduled runs). A collection that lists it still calls it.
- **Quick**: the probe now; the scheduled suite passes fill the suite in, six
  tasks a run. The same calls, spread out.
- **Quick + ad hoc**: the probe only, and then ad hoc as above. The one that
  spends least, for a metered, capped or trial key; its suite stays empty,
  since the scheduled passes leave ad hoc models alone.

**Vision model**, the box beside it, gives the model role **vision** as it is
registered: it sits only for requests that carry images, and its suite is the
vision tasks (photos of documents, charts, labels and screens with an answer
key) rather than the text and code ones. Full runs them now, Quick through the
scheduled passes. For a model that reads images and little else; a model that
does everything and also takes images stays general and gets both.

`bench onboard <served> [--quick] [--ad-hoc] [--vision]` does the same from a terminal.
Onboarding takes minutes and spends calls, so models run **one at a time**,
in a queue the dashboard keeps:

- One model with nothing ahead of it is followed step by step in the popup.
- Otherwise the popup shows the queue, and a chip beside the button shows the
  progress ("Onboarding 2 of 5: ..."), filling as models finish. It turns green
  when every model is on the bench, and red when one failed or did not answer.
- Closing the popup or reloading the page stops nothing. **Details** reopens
  the queue: each model's state, its alias, why it stopped, and its log.
- **Stop** lets the model running now finish and skips the rest. **Clear**
  forgets a finished batch's results; the logs stay in `runs/`.

A model that registered but did not answer stays on the bench as
unavailable, so the daily probe keeps watching it.

**Roles.** A model's popup sets what it is for: `general` (the default),
`code`, `light` (small and fast), `vision` (reads images well, weak at text:
OCR and document models; seated only when a request carries images) or
`image` (makes images; never seated). A model that reads images and does
everything else well stays `general`.

## Collections

A collection is a named panel: the models it always includes, the shape of
work it is for (review, design, enumeration, ...), and how its reply comes
back. On the **Collections** page, **New collection**, then pick the members.
Each collection is drawn as its panel sits: a seat per member, the explorer
seats dashed, and the merger after an arrow.

In the popup, the panel's seats are on top, and the member list has one row
per model with its context, vision and tools, and price. A model served by
several providers is still one row, with a chip per provider: tick the chip
of the route you want. Ticking two is allowed, and the count says so. The tags
grey out the models that do not fit them, as before.

Each call also seats explorers, the models unused longest, up to the gateway's
limits (Gateway settings, on the same page: how many calls a request may make,
and how many of them explore).

## Connecting an app

Any OpenAI-compatible client works. Point it at the dashboard's port and ask
for the model `bench/<collection>`:

| Setting | Value |
| --- | --- |
| Base URL | `http://127.0.0.1:8122/v1` (or `gateway.py` alone: `http://127.0.0.1:8123/v1`) |
| API key | any text on this machine; a **gateway key** from Settings when keys are required |
| Model | `bench/<collection>`, or `bench/<collection>:merged` for one merged answer |

With the OpenAI Python SDK:

```python
from openai import OpenAI

bench = OpenAI(base_url="http://127.0.0.1:8122/v1", api_key="local")
reply = bench.chat.completions.create(
    model="bench/<collection>",
    messages=[{"role": "user", "content": "Where can this function return a stale value? ..."}],
)
print(reply.choices[0].message.content)   # every seat's answer, labelled
```

In a chat app such as Open WebUI, add the base URL as an OpenAI connection;
the collections appear as models. For a chat window, the `:merged` form reads
best: a merger model writes one answer saying where the seats agreed and
disagreed, and every seat is still in the reply's `bench` field.

Images go in as OpenAI `image_url` parts; only models that take images sit,
and the rest are listed in `bench.skipped`. [GATEWAY.md](GATEWAY.md) has the
limits and the reply's full shape.

**A client that speaks another API** works too, with the server's address as
its base URL and a collection as its model:

| Client | Base URL | Notes |
| --- | --- | --- |
| Open WebUI, as an **Ollama** connection | `http://127.0.0.1:8122` | collections listed from `/api/tags`; `bench/<collection>:merged` reads best in a chat window |
| **Anthropic** SDK (`anthropic.Anthropic(base_url=...)`) | `http://127.0.0.1:8122` | `/v1/messages`; a gateway key goes in `api_key` |
| **Google GenAI** SDK (`http_options=HttpOptions(base_url=...)`) | `http://127.0.0.1:8122` | `generate_content(model="bench/<collection>")`; a key in `api_key` |
| OpenAI **Responses** clients | `http://127.0.0.1:8122/v1` | `responses.create`; nothing is stored, so send the whole conversation |
| Older tools on `/v1/completions` | `http://127.0.0.1:8122/v1` | one `prompt` |

Asked to stream, each answers at once and sends each seat's answer as it
finishes. GATEWAY.md, "Other API formats", has a curl for each.

**An agent that calls tools** (Claude Code, Codex, opencode, Cline...) uses
a collection's **route mode**: model `bench/<collection>:route`. No panel:
one member, picked by its record, gets the request with its tools and
streams its answer back in the agent's own format; the conversation stays
on that model, and a member that fails before answering is replaced by the
next. A panel refuses tools with a 400 saying so. For Claude Code:

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:8122 ANTHROPIC_API_KEY=local
export ANTHROPIC_MODEL=bench/core:route ANTHROPIC_DEFAULT_HAIKU_MODEL=bench/fast:route
claude
```

Each conversation is one offload per model that answered it; a verdict is
optional, and the Gateway card lists route conversations apart from the
panels. GATEWAY.md, "Route mode", has the other clients and the limits.

**A coding agent** connects over MCP instead: the Claude Code plugin
(`/plugin marketplace add WendellResearch-AI/benchwarmers`, then
`/plugin install benchwarmers@benchwarmers`), or `benchwarmers mcp` in any MCP
client, with `BENCHWARMERS_URL` and, where keys are required, a gateway key in
`BENCHWARMERS_KEY`. Its `ask` tool returns every seat with its call id and its
`verdict` tool records the verdicts; README, "From Claude Code and other
agents", has the set-up.

**From a terminal or a script**, `bench ask <collection> "..."` (a pipe or
`-f FILE` adds the text to review) prints every seat's answer and a table of
call ids, and `bench verdict <call id> --useful|--not-useful` judges one. The
rest of this guide's pages have commands too (`bench models`, `bench onboard`,
`bench collection save`, `bench endpoint add`, `bench key create`, `bench
settings`); README, "From the command line", lists them.

## Recording verdicts

A panel's answers are worth something only once someone has checked them
against the real work. Every seat has a call id; record a verdict per call on
the Collections page (the Gateway card lists recent requests: click a seat),
or:

    curl http://127.0.0.1:8122/bench/verdict -H "Content-Type: application/json" \
      -d '{"call": "<call id>", "useful": true, "note": "found the real bug", "real": ["..."], "false": ["..."]}'

`real` and `false` list the specific claims you verified either way. False
claims feed **Confidently wrong** on Benchmarks, and verdicts together decide
who sits on the next panel. Agreement between models is not evidence: they
are often wrong together.

## Discover: finding models to add

**Discover** leads with FreeLLMAPI's catalog of free tiers and fills in from
models.dev, which alone has prices, paid models and providers' base URLs. One
list: free models first, then by reach, and within each reach FreeLLMAPI's
smartest first (the **Rank** column), then what it does not rank, what is new
this week first and badged. A row opens its popup: what
the model takes and does, its limits and monthly budget, FreeLLMAPI's known
quirks for it, links to its freellmapi.co page and models.dev, and every route
this install has to it, each with an **Add via this**. Adding through a hub (Open WebUI, LiteLLM, CLI Proxy
API) puts it in that hub too; adding direct registers it on its own endpoint
only. Either way the onboarding joins the same queue, with the same
**Onboard as** and **Vision model** choice as the Onboard dialog, asked in the
confirm before anything is added.

Some providers' free models work only inside their own app; Discover leaves
those out and keeps their paid models. Settings, **Free only in their own
app**, switches a provider either way or adds one.

## Quota management

One tile per provider a bench model arrives through, shaped by how that
provider limits:
- **Windows**: CLI Proxy API's sign-ins have a 5-hour and a weekly limit per
  model group. Other providers have the windows their rate-limit headers state.
- **Pool**: OpenRouter's free requests a day, shared by its models, with a bar
  per key.
- **Each model's last day**: shown where the provider states nothing.

Each tile shows when its limits reset and which of its models are resting
after a rate limit. **Models** lists the provider's bench models, whether each
is free to call now, and its last 24 hours of calls.

**Refresh never spends quota.** On OpenRouter it asks the key what is left. On
CLI Proxy API's sign-ins it asks the proxy for each one's quota, as its own
console does; this needs CLI Proxy API on the Endpoints page as a hub, with
its management key. Every other tile re-reads what the provider last said in
its replies to the bench. **Check now** sends one 16-token request to read the
reply's limit headers. It is offered only where the provider has sent them
before.

Tiles sort with the ones that are out first, then the soonest reset. Drag a
tile, or move it with the arrow keys, and the order is kept; **Sort** brings
the default order back.

Under the tiles are:
- the next 7 days' resets, and when each resting model is back
- **What the bench sent**: requests per provider this month and in the last 24
  hours, as a chart with its table
- the known limits, folded away

## The scheduler

The recurring jobs (the morning availability probe, the catalog refresh, the
deprecation check and auto-retire, suite passes, the weekly speed and
capability tests) run inside the dashboard. Settings, **Scheduler**:

- **Off**: nothing runs; a fresh install spends no calls until you opt in.
- **Log only**: writes "would run" to `runs/scheduler.log` at each due time,
  to check the times before anything spends calls.
- **Run**: runs each job at its time, one at a time per job.

If something else already runs these jobs (cron, Windows Task Scheduler),
turn it off in the same step, or every job runs twice.

## Your data folder

Everything that is yours (endpoints, registrations, collections, verdicts,
call logs, probe history, task answers) lives in the data folder: the one
`BENCHWARMERS_HOME` names, else the one in `.benchwarmers-home` at the top of
the checkout. Back it up by copying it; move it by copying it and pointing one
of those two at the new place. Info shows which folder is in use.
