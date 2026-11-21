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
- [From the command line](#from-the-command-line)
- [From Claude Code and other agents](#from-claude-code-and-other-agents)
- [Recording verdicts](#recording-verdicts)
- [Benchmarks, and sharing a report](#benchmarks-and-sharing-a-report)
- [Discover: finding models to add](#discover-finding-models-to-add)
- [Quota management](#quota-management)
- [The scheduler](#the-scheduler)
- [Your data folder](#your-data-folder)

## The dashboard

`python serve.py`, then http://localhost:8122. The sidebar groups its pages:

| Group | Page | What it is for |
| --- | --- | --- |
| Observe | **Benchmarks** | The home page. How models do on the suite and on real work: bugs found against false alarms, who is confidently wrong, each model against its own earlier scores, uptime, speed, and the decision models. Each report shares as an image or its table. |
| | **Action needed** | What will not fix itself, by the connection it comes from, with Retry and Retire on each model. |
| | **Quota management** | A tile per provider: what is left, when it resets, and which of its models are resting. Then the week's resets, and what the bench sent. |
| | **Logs** | Gateway calls (who sat and how it went, never the prompt), changes made from the dashboard, the scheduler, and onboarding runs. |
| Gateway | **Endpoints** | Where models are served from. Add an endpoint (a provider's sign-in, or any OpenAI-compatible API key), change a key, and see what each one serves. A model server running on this machine, at its usual port or any other its process is listening on (LM Studio, Jan, llama.cpp, vLLM, Open WebUI, gateways such as Bifrost, and the like), is offered under Found on this machine: Add opens the dialog with it filled in, to test and save. Also your own app registrations and rate limits per connection. |
| | **Models** | Every model on the bench, by the connection it comes from: what it holds, its reply time and its limits. One search above every table, each table folds, and Collapse all / Expand all. Click a name for its popup: role, alias, facts, options, retire. |
| | **Collections** | The named panels your apps ask for, and the gateway's settings. |
| | **Connect** | Each popular app's set-up (opencode, VS Code, Codex, Zed, Continue, Cline, Kilo Code, Claude Code, LangChain and the OpenAI Agents SDK, goose, chat apps), made from your choices (how the app uses Benchwarmers, which collection, its name, a key of its own), with Copy, Set it up for the apps whose files it can write, and Does it work? for the app's first call. [CLIENTS.md](CLIENTS.md) has the set-ups for a fresh install. |
| | **Discover** | Models worth adding, from public catalogs, with the ways this install can reach each one. |
| Control | **Settings** | Theme, gateway keys, the scheduler, the model client, providers whose free models work only in their own app, export and import, how the install is reached (shown only), and Restart. |
| | **Info** | The source repository, the version (linking its release), Python, data folder and counts. |

The page shown follows the data: while it is in view it checks every 10
seconds, and when something has changed it swaps in the new page in place,
keeping open sections, sorting and filters. It waits while you are at work on
it (a popup open, a form not saved or in use, text selected, a chart hovered),
and **New results · Show**, top right, offers the change instead.

**Action needed** lists what will not fix itself, such as a model about to be
retired, grouped by the connection it comes from, since one cause usually
reaches every model there. Each model is one line: its state, the provider's
own words for the error (whole on hover), and Retry (one call; an answer
clears the alert) and Retire. A group of two or more has Retry all and Retire
all; a connection held because every model on it fails says so above its
models, with any link the provider gave. Automatic retirements from the last
week are folded at the end, each with Restore. Its count sits beside it in the
sidebar. An alert whose models have all been retired goes at once. A model that
is out of quota is not counting down: days the bench held its calls back until
a stated reset, an account's shared free quota was spent, or the provider said
the model's free quota is used up, pause its clock, and while it is still out
of quota no countdown shows. One refused again after the short wait it named
is failing, and keeps counting.

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
Refresh, and a model already queued or onboarding is not offered again. A
hub's combos and routers (OmniRoute's combos, fusion and `auto` ones;
freellmapi's `auto`, `auto:<profile>`, `fusion` and Claude-named slots; a
one-api or new-api name whose channels send it upstream as different models)
are not offered either, and onboarding refuses one by name: each picks a
model per request, and a verdict is about one model. On one-api and new-api
the models are grouped by the channels that serve them, which the bench also
keeps as each model's connection; both need their access token as the
endpoint's admin key for this (one-api's system access token, new-api's
with the channel read scope), and without it every name is offered.

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
everything else well stays `general`. Where a decision model is onboarded,
onboarding asks it to suggest a new chat model's role; the popup shows the
suggestion, and nothing applies it.

## Collections

A collection is a named panel: the models it always includes, the shape of
work it is for (review, design, enumeration, ...), and how its reply comes
back. On the **Collections** page, **New collection**, then pick the members.
Each collection is drawn as its panel sits: a seat per member, the explorer
seats dashed, and the merger after an arrow.

In the popup, the panel's seats are on top, and the member list has one row
per model with its context, vision and tools, and price. A model served by
several providers is still one row, with a chip per provider: tick the chip
of the route you want. Ticking two is allowed, and the count says so. The
search matches a model, provider or served name, and the select beside it
shows one provider's models. The seats above the list always show who is
ticked, filtered out or not, and a seat's × takes that member off, as unticking
it does. The tags grey out the models that
do not fit them.

Each call also seats explorers, the models unused longest, up to the gateway's
limits (Gateway settings, on the same page: how many calls a request may make,
and how many of them explore). A collection can set its own in its popup; left
empty, it follows those settings. A member out of quota stays on the collection
and an explorer sits in for it, named `stand-in for <member>`, until the quota
is back. Max explorers 0 means only the members ever run, stand-ins included.

Gateway settings also says whether a request that names no collection is given
one by a decision model, and how sure that model must be (GATEWAY.md, "No
default collection").

## Connecting an app

The dashboard's **Connect** page has a ready set-up for each popular app,
filled in from this install (its address, collections and MCP server). For
each app you choose:

- **How it uses Benchwarmers**: a collection as its model (route mode), the
  panel as two tools beside its own model (MCP), or both; a chat app gets one
  merged reply or every seat's.
- **Collection**, and **where it runs** (in Docker, the address changes and
  the page says what to allow).
- **Its name.** With no key, the set-up sends the name as its key, and the
  app's calls are logged under it instead of `local`. It is a label, not a
  credential: lower-case letters and dashes, and not a name apps send as a
  dummy key (`ollama`, `test`), which the page refuses.
- **Key**: none, or one made there for that app, limited to its collection
  and, if you like, a rate. A key works the moment it is made, also where keys
  are not required. It is shown once and goes into the lines you copy (a shell
  line, a settings field), never into a file that may sync.

**Set it up** (opencode, VS Code, Zed, Codex, Continue, goose) writes the
app's own files on the machine Benchwarmers runs on. It lists them first, and
copies each file it changes to `<name>.bak-<time>` beside it. It merges into
plain JSON, into Codex's `config.toml` (only its own tables; the model goes in
a profile, `codex --profile benchwarmers`, so plain `codex` keeps yours) and
into goose's `config.yaml`; Continue's `config.yaml` is written only when
there is none. A file with comments in its JSON, or laid out in a way it
cannot edit safely, is left to you with the reason. Zed's and goose's
MCP header takes no variable, so with a key it holds `<your gateway key>` for
you to replace, and a key you typed there is kept by the next Set up. Or copy
each block into the file or screen it names.

**Does it work?** waits for the app's first call and says which model
answered. A set-up that sends neither name nor key (Codex, goose's provider,
Zed's and VS Code's models) calls as `local`: after Set it up, the next
`local` call is counted as probably that app's; set up by hand, look for
`local` under Connected in the page's side panel. What follows is the same by
hand.

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
panels. The Connect page has the other agents' set-ups; GATEWAY.md, "Route
mode", has the limits.

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

## From the command line

`bench` (or `benchwarmers`, the same program) does what the dashboard does,
without it: ask and judge, and run the bench itself. It is a client of a
running server, so the same commands work against one on another machine.

    bench status                                   what is running, and where
    bench restart                                  start it again on the code now on disk
    bench collections                              what you can ask
    bench ask core "Where can this return a stale value?" -f cache.py
    git diff | bench ask core "Review this diff"   a pipe is the prompt
    bench verdict <call id> --useful --real "the race in refresh()" --false "the SQL injection"

    bench models --find mercury                    the roster, with each model's status
    bench candidates --new                         what your endpoints serve that is not on the bench
    bench onboard <served name> --wait             onboard it (registered, probed, scored)
    bench model retire <alias> --reason "..."      and back: bench model restore <alias>
    bench collection save reviewers --members a,b,c --description "adversarial review"
    bench collection save reviewers --add d --remove a
    bench collection save heavy-hitters --ad-hoc --max-calls 4 --max-explorers 0
    bench endpoint add openrouter --base https://openrouter.ai/api/v1   (asks for the key, hidden)
    bench key create my-app --collections core --rpm 30
    bench settings scheduler run --off perf

`--help` on any command lists its options with examples, and `--json` prints
what the server answered, for scripts. `--url` points one command at another
server. `ask` prints every seat's answer on stdout and the table of call ids to
judge them by on stderr, so `bench ask core "..." > review.md` keeps only the
answers. Retiring a model, deleting a collection or an endpoint and revoking a
key ask first; `--yes` answers for a script. A key is never an argument (others
on the machine can read those): `endpoint add` prompts for it, or reads it
from stdin with `--key-stdin`.

Another program called `bench` (the Frappe framework's) may come first on your
PATH; type `benchwarmers` instead, which takes the same commands.

| Variable | Meaning |
| --- | --- |
| `BENCHWARMERS_URL` | the server, default `http://127.0.0.1:8122` |
| `BENCHWARMERS_ADMIN_TOKEN` | the server's admin token, where it has one; sent as `Authorization: Bearer` for every command but `ask`, `collections` and `verdict` |
| `BENCHWARMERS_KEY` | a gateway key, for `ask`, `collections` and `verdict`, where keys are required |
| `BENCHWARMERS_TIMEOUT` | seconds to wait for a panel, default 900 |

Exit codes: 0 done, 1 refused or failed (the reason on stderr), 2 bad usage, 3
no server at `BENCHWARMERS_URL`.

## From Claude Code and other agents

A running Benchwarmers is an MCP server at `/mcp` on its own port
(`http://127.0.0.1:8122/mcp`; `/v1/mcp` works too), over streamable HTTP,
with four tools: `list_collections`, `ask` (a collection, a prompt,
optionally `merged`, `with` and `system`), `verdict` and `pending_verdicts`
(the seats still unjudged). Nothing to start per
client: every agent session shares the one server, behind the same Host,
Origin and gateway-key checks as `/v1`. The tool descriptions carry the
rules: ask a collection, not a favourite model; verify before acting; name
every seat; send a verdict per seat you judged.

**Claude Code plugin.** The plugin connects to that `/mcp` and adds a
`/benchwarmers:ask <collection> <what to ask>` command and a `benchwarmers`
skill saying when a panel helps (adversarial review, exhaustive enumeration
over real files) and how to judge it:

    /plugin marketplace add WendellResearch-AI/benchwarmers
    /plugin install benchwarmers@benchwarmers

It finds the server through `BENCHWARMERS_URL` (default
`http://127.0.0.1:8122`; set it if you changed `BENCH_PORT`) and sends
`BENCHWARMERS_KEY` where keys are required. It also adds a `Stop` hook: at
the end of a turn it asks the server (the `pending_verdicts` tool, so nothing
extra runs on your machine) which seats of that session's panels have no
verdict yet, and reminds Claude of them once, as hook feedback. The server
finds the session's panels in its transcript, so the reminder needs a server
on the same machine; with one elsewhere the hook stays silent. A verdict sent
any way counts, the dashboard's included. With the plugin enabled and no
Benchwarmers running, the hook shows a hook-error notice at the end of each
turn, as `/mcp` shows the server as failed; start the server, or disable the
plugin for that session.

**Cursor and Antigravity plugins.** The same skill, with the stdio server
below (so they need Node and Python 3.11 or later): Cursor's is
[on cursor.directory](https://cursor.directory/plugins/benchwarmers), and
Antigravity's installs from the repository:

    agy plugin install https://github.com/WendellResearch-AI/benchwarmers

**Any other MCP client** that speaks HTTP:

```json
{"mcpServers": {"benchwarmers": {"type": "http", "url": "http://127.0.0.1:8122/mcp"}}}
```

with `"headers": {"Authorization": "Bearer <gateway key>"}` where keys are
required. **A client that only speaks stdio**: run `benchwarmers mcp` once
installed, `uvx benchwarmers mcp` or
`npx -y @wendellresearch-ai/benchwarmers mcp` without installing, or `python mcp_server.py` from a
checkout, a small process per client that calls the same server. For example:

```json
{"mcpServers": {"benchwarmers": {"command": "npx", "args": ["-y", "@wendellresearch-ai/benchwarmers", "mcp"],
                                 "env": {"BENCHWARMERS_URL": "http://127.0.0.1:8122"}}}}
```

On native Windows, a client that cannot start `npx` itself needs
`"command": "cmd", "args": ["/c", "npx", "-y", "@wendellresearch-ai/benchwarmers", "mcp"]`.

| Variable | Meaning |
| --- | --- |
| `BENCHWARMERS_URL` | the server, default `http://127.0.0.1:8122` (a `/v1` on the end is fine) |
| `BENCHWARMERS_KEY` | a gateway key from Settings, sent as `Authorization: Bearer`; needed for a server on another machine, or where keys are required |
| `BENCHWARMERS_TIMEOUT` | seconds to wait for a panel, default 900 |

**Behind an MCP aggregator** (1mcp, MetaMCP, mcp-proxy, Docker's MCP Gateway
and the like): add `http://127.0.0.1:8122/mcp` as an HTTP (streamable HTTP)
upstream, with the `Authorization` header where keys are required; an
aggregator that only runs stdio servers takes `benchwarmers mcp` as above.
Give it a request timeout of a few minutes or more, as a panel takes that
long; 1mcp's default of 60 seconds ends most of them. With 1mcp, for example:

    1mcp mcp add benchwarmers --type http --url http://127.0.0.1:8122/mcp --timeout 900000

The aggregator prefixes the tool names (1mcp's are
`benchwarmers_1mcp_ask` and so on). If you also use the Claude Code plugin,
disable the plugin's own `benchwarmers` server in `/mcp` so the tools are not
listed twice; the skill and `/benchwarmers:ask` keep working, though the
command then asks before each tool call, since its pre-approved tools carry
the plugin's names. The plugin's verdict hook calls the plugin's own server,
so it does not work through an aggregator: with that server disabled it only
shows a hook-error notice at the end of each turn.

**An agent running on a collection.** The MCP tools ask a panel for a
second opinion; an agent can also *be driven by* a collection, in **route
mode** (`bench/<collection>:route`): one member, picked by its record, gets
each turn with its tools, stays on for the whole conversation, and is
replaced by the next member if it fails before answering. Claude Code
points at it with `ANTHROPIC_BASE_URL=http://127.0.0.1:8122` and
`ANTHROPIC_MODEL=bench/core:route`; Codex, opencode, Cline and other
OpenAI-, Responses- or Ollama-speaking agents with the same base URL.
[GATEWAY.md](GATEWAY.md), "Route mode", has the set-up and the limits.

**Ready set-ups.** The dashboard's **Connect** page (Gateway) gives each
popular app's configuration, filled in from your install's address,
collections and MCP server, with a Copy button: opencode, VS Code chat, Codex
CLI, Zed, Continue, Cline and Kilo Code, Claude Code, your own Python with
LangChain or the OpenAI Agents SDK, goose, and chat apps
(LobeHub, AnythingLLM, Cherry Studio, Chatbox, LibreChat). You choose how the
app uses Benchwarmers (a collection as its model, the panel as tools, or
both), which collection, and its name; **Set it up** writes the files for
opencode, VS Code, Zed, Codex, Continue and goose itself, after showing them,
with a copy of each file it changes; and **Does it work?** shows the app's
first call. An app may have its own key, made there for its collection; it
goes into the lines you copy, never into a file that may sync.
The same set-ups are in [CLIENTS.md](CLIENTS.md), with a fresh install's
address and collections, for reading here or handing to an agent.

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

## Benchmarks, and sharing a report

**Benchmarks** is one report after another: who to trust with a review, bug
hunt, real work, confidently wrong, our code, vision, trend, uptime,
performance and the decision models, then links to public leaderboards. Most
have a chart, the table folded under it (with a filter, and **Show
retired**), and **How to read this**;
[BENCHMARKS.md](BENCHMARKS.md) has the same explanations in one place.
**Trend** (30 or 60 days) and **Uptime** (7 or 30) have a window to choose.

**Share**, top right of each report:

- **Copy image** / **Download image**: the report's charts, title and the line
  under it, with a band at the foot holding the logo, the project's link and
  the date, as a PNG at twice the size shown. The table, the key and the
  controls stay out; a window you chose shows as its words.
- **Copy table as Markdown** / **Download table as CSV**: the table as shown
  (its window, filter and Show retired), a model's connection in brackets.
- **Share…**: your system's share sheet, with the image.
- **Post on** X, Bluesky, LinkedIn or Reddit: the image goes on the clipboard
  (or is saved, where there is no clipboard), then the site opens with the
  report's name and the project's link, for you to paste the image into.

What your browser cannot do is not offered: copying needs a secure page
(`localhost` is one; a LAN address over plain http is not), and Share… needs
a share sheet.

## Discover: finding models to add

**Discover** leads with FreeLLMAPI's catalog of free tiers and fills in from
models.dev, which alone has prices, paid models and providers' base URLs.
The catalogs trail the providers by days, so it also reads each Open WebUI
connection's own model list (through Open WebUI, with the keys it already
has): a model a provider serves that neither catalog lists yet is a row of its
own, its price unknown where the provider's list does not say. All of them
are fetched daily; **Refresh lists** fetches them now. A provider is matched
to your Open WebUI connection by its API host, or by its name in the
connection's host (FreeLLMAPI's xKiro is `api.xkiro.com`). One
list: free models first, then by reach, and within each reach FreeLLMAPI's
smartest first (the **Rank** column), then what it does not rank, what is new
this week first and badged. A row opens its popup: what
the model takes and does, its limits and monthly budget, FreeLLMAPI's known
quirks for it, links to its own pages on freellmapi.co and models.dev, and every route
this install has to it, each with an **Add via this**. Adding through a hub (Open WebUI, LiteLLM, CLI Proxy
API, OmniRoute) puts it in that hub too; adding direct registers it on its own endpoint
only. freellmapi, one-api and new-api take no model through their API, so
they offer no route here: add the model there, then onboard it. Either way the onboarding joins the same queue, with the same
**Onboard as** and **Vision model** choice as the Onboard dialog, asked in the
confirm before anything is added.

Some providers' free models work only inside their own app; Discover leaves
those out and keeps their paid models. Settings, **Free only in their own
app**, switches a provider either way or adds one.

## Quota management

One tile per provider a bench model arrives through. Each draws the
provider's limits at the level they apply, and names a model only where a
limit is that model's own:
- **Windows**: the sign-ins in CLI Proxy API or OmniRoute have a 5-hour and a weekly limit per
  model group, a Codex sign-in one for the account. Other providers have the
  windows their replies' headers state; a window past its reset is shown full.
- **Pool**: OpenRouter's free requests a day, shared by its models, with a bar
  per key, against the limit OpenRouter stated in a rate-limit reply.
- **Caps**: a day or month cap, counted from the bench's own calls: one you set
  on Endpoints (Rate limits per connection), else a day cap the bench learned
  for the connection (below), else one the provider states in
  its replies without saying what is left (a Cohere trial key's 1,000 calls a
  month), shown but not enforced until you set it. Calls made elsewhere with
  the same key are not in the count.
- **Nothing stated**: how the provider's calls went in the last 24 hours, all
  its models together (green answered, red failed).

Nothing is taken from a provider's docs, which can change without notice; a
limit a provider states in a rate-limit error (UnoRouter's "allows 1
request(s) every 1 min" for a model) is learned and kept to. When the error
names no number (Cloudflare Workers AI's, once the day's free Neurons are
gone), the requests the bench had sent on that connection in that minute or
day are taken as the connection's limit: noted the first time, kept to once a
second refusal lands near the same count, unless you set your own for that
connection on Endpoints, where each learned limit is shown with a Forget
button. A learned rate limit not met again for 14 days is forgotten, so a
raised limit is found. One exception,
watched: NVIDIA states no limit per key, so its tile reads the file
build.nvidia.com's own badge reads (10,000 requests a day, up to 40 a minute,
the same for every account) daily and on Refresh, and counts the bench's
requests against it. If NVIDIA changes those figures, Action needed says so
(Got it dismisses it); if the file moves or changes shape, Action needed says
that too, and the tile keeps the figures it last read. Each meter leads
with the percentage left, then what is left of what in the provider's own unit
("94% left · 189 of 200 requests · day"), with its reset on the right. Under the name, one block says where the figures come
from (**From**: the provider's quota endpoint or its replies' headers, exact;
or a stated limit counted by the bench), your per-minute caps, and anything a
live read reported. **Models** names the connection and how it is reached
(each endpoint or Open WebUI connection, with its address), then lists the
provider's bench models (each opens its popup), whether each is free to call
now, and its last 24 hours of calls.

**Refresh** picks its own way, and hovering it says which:
- **A free read** on OpenRouter (it asks the key what is left), on xKiro (its
  usage endpoint: free-model tokens left today), on Requesty (its usage
  endpoint: the key's free-model requests today, by Requesty's count; its limit
  only once a rate-limit reply has stated it) and on the sign-ins in CLI Proxy
  API or OmniRoute (it asks the hub for each one's quota, as CLI Proxy API's
  own console does; this needs the hub on the Endpoints page, with its
  management key, or for OmniRoute a key with the manage scope). CLI Proxy
  API reads its Antigravity and Codex sign-ins; OmniRoute lists only its
  Claude and Codex ones there, and the bench reads Codex's (it has no reader
  for Claude's yet). A tile is a hub's when its tag names the hub's endpoint,
  its address, or the name `hosts` in `config/connections.json` gives that
  address. Through freellmapi (with its unified API key, not a client
  profile's): each provider pool's requests left and reset, and which
  providers are rate-limited or have failing keys. Through one-api or new-api:
  the key's balance in the hub's own unit (no reset: it is a balance), its
  expiry, and with the access token the channels the hub switched off after
  failures.
  xKiro's and Requesty's keys come from their endpoint, else their Open WebUI
  connection.
- **One 16-token request** where a provider states its limits only in its
  replies' headers, to read them (Groq, Routeway, a Cohere trial key). Every
  ordinary call reads them too, through Open WebUI as well, since the bench
  streams.
- **The bench's own records** again everywhere else.

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
deprecation check and auto-retire, suite passes, the decision models' daily
benchmark, the weekly speed and capability tests) run inside the dashboard.
Settings, **Scheduler**:

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

**Export and import**, on Settings, does the same through the dashboard, for
another machine or a copy to keep: one .zip of **Settings only**
(`config/`: endpoints, collections, roles, model options, the schedule,
gateway keys, the theme) or **Everything** (also every result, history, log
and saved answer). The endpoints' API keys go in only if you tick them; the
file then holds them, so keep it like a password and delete it once
imported. On the other install, choose the file: it says what is in it
(scope, release, date, files, how many it replaces, keys) before **Import**.
Its files replace the ones of the same name, after this install's own in that
scope are saved to `backups/before-import-<time>.zip`; files it does not have
are left. Restart afterwards (Settings, Server and access), so every part
reads them.
