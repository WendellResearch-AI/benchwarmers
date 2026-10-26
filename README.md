<h1 align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset=".github/logo-dark.svg">
    <img src=".github/logo.svg" width="480" alt="Benchwarmers, by Wendell Research: the name sitting on a crimson team bench">
  </picture>
</h1>

<p align="center">
  <a href="https://github.com/WendellResearch-AI/benchwarmers/releases/latest"><img src="https://img.shields.io/github/v/release/WendellResearch-AI/benchwarmers?logo=github&color=181717" alt="Latest release"></a>
  <a href="https://www.npmjs.com/package/@wendellresearch-ai/benchwarmers"><img src="https://img.shields.io/npm/v/@wendellresearch-ai/benchwarmers?color=cb3837&logo=npm" alt="npm"></a>
  <a href="https://pypi.org/project/benchwarmers/"><img src="https://img.shields.io/pypi/v/benchwarmers?color=3775a9&logo=pypi&logoColor=white" alt="PyPI"></a>
  <a href="https://github.com/WendellResearch-AI/benchwarmers/actions/workflows/check.yml"><img src="https://img.shields.io/github/actions/workflow/status/WendellResearch-AI/benchwarmers/check.yml?branch=main&logo=github&label=CI%20tests" alt="CI tests"></a>
  <a href="https://registry.modelcontextprotocol.io/?q=benchwarmers"><img src="https://img.shields.io/badge/MCP_Registry-111111?logo=modelcontextprotocol&logoColor=white" alt="In the official MCP Registry"></a>
  <a href="https://smithery.ai/skills/wendellresearch-ai/benchwarmers"><img src="https://img.shields.io/badge/Smithery-FF5601?logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2CPHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAxODggMjExIiBmaWxsPSIjZmZmIj48cGF0aCBkPSJNNDEuNzAxMSA3Ny40OEgwVjEwMC4yNjRDMCAxMTguMTcxIDE0LjUyNzUgMTMyLjY5OCAzMi40MzQyIDEzMi42OThINTUuMjE4M1Y5MC45OTcxQzU1LjIxODMgODMuNTQxOCA0OS4xNTY1IDc3LjQ4IDQxLjcwMTEgNzcuNDhaIi8%2BPHBhdGggZD0iTTY2LjA4NzkgOTAuOTk3MVYxMzIuNjk4SDEwNy43ODlDMTE1LjI0NCAxMzIuNjk4IDEyMS4zMDYgMTI2LjYzNiAxMjEuMzA2IDExOS4xODFWNzcuNDhINzkuNjA1MUM3Mi4xNDk3IDc3LjQ4IDY2LjA4NzkgODMuNTQxOCA2Ni4wODc5IDkwLjk5NzFaIi8%2BPHBhdGggZD0iTTE1NC45OTUgNzcuNDhIMTMyLjIxVjExOS4xODFDMTMyLjIxIDEyNi42MzYgMTM4LjI3MiAxMzIuNjk4IDE0NS43MjggMTMyLjY5OEgxODcuNDI5VjEwNi4xNTJDMTg3LjQyOSA4OC4yNDQ5IDE3Mi45MDEgNzcuNTE0OCAxNTQuOTk1IDc3LjUxNDhWNzcuNDhaIi8%2BPHBhdGggZD0iTTAgNDUuOTUxNFY2Ni42MTAzSDQxLjcwMTFDNDkuMTU2NSA2Ni42MTAzIDU1LjIxODMgNjAuNTQ4NSA1NS4yMTgzIDUzLjA5MzJWMEMyNS44NDk4IDAuMjA5MDI4IDAuMDM0ODM4IDE0LjY2NjggMCA0NS45NTE0WiIvPjxwYXRoIGQ9Ik02Ni4wODc5IDUzLjA5M0M2Ni4wODc5IDYwLjU0ODMgNzIuMTQ5NyA2Ni42MTAxIDc5LjYwNTEgNjYuNjEwMUgxMjEuMzA2VjExLjQ2MTVDMTAwLjkyNiA4LjM2MDkyIDkwLjMwMDMgMS43NDE2OSA2Ni4wODc5IDAuMjQzNjUyVjUzLjA1ODFWNTMuMDkzWiIvPjxwYXRoIGQ9Ik0xNDQuMDkgMTMuMDYzOUMxMzkuNzcgMTMuMDYzOSAxMzUuODM0IDEyLjkyNDYgMTMyLjIxIDEyLjY4MDdWNjYuNjFIMTU0Ljk5NUMxNzIuOTAxIDY2LjYxIDE4Ny40MjkgNTIuMDgyNSAxODcuNDI5IDM0LjE3NThWMC4xNzM4MjhDMTc3LjI5MSA4LjExNjkgMTYyLjkwMyAxMy4wNjM5IDE0NC4wOSAxMy4wNjM5WiIvPjxwYXRoIGQ9Ik0wIDE3Ni4wMDJWMjEwLjAwNEMxMC4xMzc5IDIwMi4wNjEgMjQuNTI2IDE5Ny4xMTQgNDMuMzM4NSAxOTcuMTE0QzQ3LjY1ODQgMTk3LjExNCA1MS41OTUxIDE5Ny4yNTMgNTUuMjE4MyAxOTcuNTMyVjE0My42MDNIMzIuNDM0MkMxNC41Mjc1IDE0My42MDMgMCAxNTguMTMgMCAxNzYuMDM3TDAgMTc2LjAwMloiLz48cGF0aCBkPSJNMTA3Ljc4OSAxNDMuNjAzSDY2LjA4NzlWMTk4LjcxNkM4Ni40NjgxIDIwMS44MTcgOTcuMDkzOCAyMDguNDM2IDEyMS4zMDYgMjA5LjkzNFYxNTcuMTJDMTIxLjMwNiAxNDkuNjY0IDExNS4yNDQgMTQzLjYwMyAxMDcuNzg5IDE0My42MDNaIi8%2BPHBhdGggZD0iTTEzMi4yMSAxNTcuMDg1VjIxMC4xNzhDMTYxLjU0NCAyMDkuOTY5IDE4Ny4zMjQgMTk1LjUxMSAxODcuNDI5IDE2NC4yOTZWMTQzLjU2OEgxNDUuNzI4QzEzOC4yNzIgMTQzLjU2OCAxMzIuMjEgMTQ5LjYzIDEzMi4yMSAxNTcuMDg1WiIvPjwvc3ZnPg%3D%3D" alt="The benchwarmers skill on Smithery"></a>
  <a href="#docker-and-hosting"><img src="https://img.shields.io/badge/Docker-compose-2496ED?logo=docker&logoColor=white" alt="Runs in Docker"></a>
  <br>
  <a href="GATEWAY.md#other-api-formats"><img src="https://img.shields.io/badge/API-OpenAI_%7C_Responses-111111" alt="OpenAI chat completions and Responses API"></a>
  <a href="GATEWAY.md#other-api-formats"><img src="https://img.shields.io/badge/API-Anthropic_Messages-111111" alt="Anthropic Messages API"></a>
  <a href="GATEWAY.md#other-api-formats"><img src="https://img.shields.io/badge/API-Gemini-111111" alt="Gemini API"></a>
  <a href="GATEWAY.md#other-api-formats"><img src="https://img.shields.io/badge/API-Ollama-111111" alt="Ollama API"></a>
  <a href="GATEWAY.md#route-mode-agent-clients"><img src="https://img.shields.io/badge/agents-tool_calling-111111" alt="Agent clients with tool calling, through route mode"></a>
  <br>
  <a href="#try-it"><img src="https://img.shields.io/badge/Python-3.11_%7C_3.12_%7C_3.13-3776AB?logo=python&logoColor=white" alt="Python 3.11 to 3.13"></a>
  <a href="#try-it"><img src="https://img.shields.io/badge/core-stdlib_only-2ea44f" alt="The core needs only the Python standard library"></a>
  <a href="#try-it"><img src="https://img.shields.io/badge/runs_on-Windows_%7C_macOS_%7C_Linux-555555" alt="Windows, macOS and Linux"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT license"></a>
</p>

<p align="center"><strong><em>Put me in, coach.</em></strong><br>Every model on your bench gets its minutes.</p>

Benchwarmers is a self-hosted **panel gateway** for LLMs. Your app asks for one
of your **collections**, and the whole panel answers: the models you picked
for that kind of work, plus **explorer** seats that rotate in the models you
have used least. Every answer comes back side by side, labelled by model and
seat. You check them against the real work and record a verdict, and those
verdicts decide who sits on the next panel.

**What it is best at:**

- **Asking several models at once.** One request, in the OpenAI, Anthropic,
  Gemini or Ollama format, a panel of answers. Where they disagree is where
  to look.
- **Agents too.** Claude Code and other tool-calling clients use a collection
  through route mode: one member per turn, kept for the conversation, with
  failover, and every call on the record.
- **Reviewing.** Code review, claims about code, designs: different models
  catch different things, and two models agreeing is not proof.
- **Playing to each model's strengths.** A collection per kind of work
  (reviewers, designers, enumerators), each with the models that do it best.
- **Giving every model playing time.** Explorers cycle through the rest of
  the bench, so new and free models get tried beside the ones you trust,
  without you choosing them by hand.
- **Benchmarking on your own projects.** A review suite seeded from your own
  repositories, plus your verdicts on real work, instead of public leaderboards.
- **Knowing who to bench.** Who is confidently wrong, who has stopped
  answering (retired automatically), and who is resting after a rate limit.
- **Keeping free and cheap models useful.** Health probes, rate limits taken
  from what providers say and enforced, and one endpoint for every app, over
  any OpenAI-compatible provider or hub, or Anthropic's and Gemini's own APIs.

| Related tools | Examples | What they do | What Benchwarmers does differently |
|---|---|---|---|
| Gateways and routers | LiteLLM, OpenRouter, Portkey | One request to one model, with fallback | Fans one prompt out to a panel and returns every answer; for agents, routes to one member by its record |
| Councils and ensembles | LLM Council, Mixture-of-Agents | Several models merged into one answer | Keeps every answer, because who said what is the evidence; one merged summary only if you ask |
| Side-by-side chat | Open WebUI, LMArena | Several answers shown together | Records a verdict per answer and uses them to pick the next panel |
| Evaluation harnesses | promptfoo, Inspect, SWE-bench | Score models offline on fixed test sets | Scores them on your real work and your own code, and keeps rotating new models in |

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset=".github/flow-dark.svg">
    <img src=".github/flow.svg" width="900" alt="Your app or agent asks the gateway for one of your collections. The gateway seats a panel: the collection's members, always seated, and an explorer that rotates in the model unused longest. Every answer comes back side by side, labelled by model and seat. You check them against the real work and record a verdict. Verdicts, daily health probes and the review suite make up the bench's record, which chooses who sits on the next panel.">
  </picture>
</p>

## Contents

- [Try it](#try-it)
- [How it works](#how-it-works)
- [Asking a panel](#asking-a-panel)
- [From the command line](#from-the-command-line)
- [From Claude Code and other agents](#from-claude-code-and-other-agents)
- [The suite](#the-suite)
- [Docker and hosting](#docker-and-hosting)
- [What leaves your machine](#what-leaves-your-machine)
- [Limits](#limits)
- [Security](#security)
- [Development](#development)
- [Licence](#licence)

Day-to-day use (the dashboard's pages, connecting apps, onboarding in bulk) is
in [USAGE.md](USAGE.md). Running it on a server or a hosting platform is in
[HOSTING.md](HOSTING.md). A coding agent setting it up for someone should start
with [AGENTS.md](AGENTS.md).

## Try it

**v0.3, early.** It runs with Python 3.11 or later on Windows, macOS and
Linux, from PyPI, npm, a checkout or Docker. Expect rough edges, and read the
code where these pages are thin.

**Quickest**, with Python 3.11+ installed, from PyPI:

    pipx install benchwarmers        # or: uv tool install benchwarmers
    benchwarmers                     # or try it first, without installing: uvx benchwarmers

or from npm, with Node 18+ as well:

    npx @wendellresearch-ai/benchwarmers

Either way you get the same two commands, `benchwarmers` and its short name
`bench`. It keeps your data in your user profile (`BENCHWARMERS_HOME` to
choose another folder), sets it up on the first run, and opens the dashboard
on http://localhost:8122, where steps 3 to 5 below continue. The built-in
model client makes every call, so nothing else needs installing. No
dependencies either way: the app is the Python standard library.

**From source:**

1. **Get the code** and a Python 3.11+ environment:

       git clone https://github.com/WendellResearch-AI/benchwarmers
       cd benchwarmers
       python -m venv .venv             # nothing to pip install: the standard library is enough

   The built-in model client (`metrics/client.py`) makes every call. The
   [`llm`](https://llm.datasette.io/) CLI is an alternative for its plugins:
   install it in the venv and choose it on Settings (or `BENCH_CLIENT=llm`,
   which wins).

2. **Make a data folder.** Everything that is yours (endpoints, model
   registrations, collections, verdicts, probe history, task answers) lives
   there, never in the code:

       python scripts/init.py           # or: python scripts/init.py <folder>

   It starts the folder from `examples/` (in your OS's per-user data folder
   unless you name one) and points the checkout at it with a
   `.benchwarmers-home` file; `BENCHWARMERS_HOME` overrides that. With
   `--endpoint NAME BASE_URL --key` it also adds a first endpoint and asks for
   its key, which goes to the key file in llm's user folder (`keys.json`,
   shared with the `llm` CLI if you have it, not needed otherwise), never into
   the data folder. `BENCHWARMERS_KEY_<NAME>` in the environment works too.

3. **Start the dashboard.** With no endpoint yet it opens on adding one: sign
   in with a provider on its own page (OpenRouter, NanoGPT, Hugging Face,
   Pollinations, your ChatGPT plan, GitHub Models, or a local Ollama), or paste
   an API key for any OpenAI-compatible API, a hub such as Open WebUI or
   LiteLLM included. Anthropic and Gemini keys can also be called through
   those providers' own APIs (the API choice in the same dialog). Only providers that publish a sign-in for other apps are
   offered; adding one is an entry in `metrics/connect.py` (its docstring says
   how).

       python serve.py                  # http://localhost:8122

4. **Onboard a few models** from it ("Onboard a model"): tick one or several;
   each is registered, probed and run through the suite, one at a time, with
   the progress beside the button. **Quick** probes now and leaves the suite
   to the scheduled passes; **Quick + ad hoc** also keeps the bench from ever
   calling the model on its own, for a metered or trial key.

5. **Make a collection** on the Collections page (the models a panel always
   includes, and what it is for). The dashboard answers the gateway's routes on
   its own port (`http://127.0.0.1:8122/v1`); `python gateway.py` runs the
   gateway alone on `http://127.0.0.1:8123/v1`.

## How it works

- **Collections and seats.** A collection names the models a panel always
  includes and the shape of work it is for (review, design, enumeration...).
  Each call adds explorer seats chosen by how long a model has gone unused, so
  the bench keeps learning about models a router would stop asking.
- **Verdicts.** Each seat's answer carries a call id. Once you have checked an
  answer against the real work, record whether it was useful, and which of its
  claims were real or false. Verdicts, not vibes, decide the next panel; a
  model that is often confidently wrong shows as that.
- **The catalog.** Context window, output limit, tools, vision and price come
  from models.dev for the provider that actually serves a model, then
  OpenRouter's public catalog and FreeLLMAPI, and anything you set by hand.
- **Health.** Every morning each model gets a one-word prompt; once a week, a
  short and a long one for speed. A model that stops answering, or that its
  endpoint no longer serves, is retired automatically after one confirming
  call, with the evidence kept, and can be restored in one click. The
  built-in scheduler runs all of this once Settings switches it on; a fresh
  install spends no calls until then.
- **Rate limits and usage.** Limits come from providers' rate-limit headers
  and error messages, and from what you set per connection or model
  (requests a minute, day or month; tokens a minute or day). They are kept per
  connection, since models behind one key share them, and enforced: a call
  waits when the wait fits, or is refused before it goes out. Every call lands
  in a monthly usage ledger with its tokens, and the Quotas page shows what is
  left and when it resets.
- **Discover.** models.dev and FreeLLMAPI, merged into one list of models
  worth adding, each with the routes this install has to it.

## Asking a panel

Any OpenAI-compatible client works: ask for the model `bench/<collection>`.

    curl http://127.0.0.1:8122/v1/chat/completions \
      -H "Content-Type: application/json" \
      -d '{"model": "bench/<collection>", "messages": [{"role": "user", "content": "Where can this function return a stale value? ..."}]}'

The reply carries every seat's answer in the message, and the same seats as
data in a `bench` field: model, seat, status, seconds and a call id. For a chat
window that shows one answer, ask for `bench/<collection>:merged`: a merger
model writes one answer saying where the seats agreed and disagreed, and every
seat is still in `bench`. Record a verdict per call once you have checked it:

    curl http://127.0.0.1:8122/bench/verdict -H "Content-Type: application/json" \
      -d '{"call": "<call id>", "useful": true, "note": "found the real bug", "real": ["..."], "false": ["..."]}'

or from the dashboard. See [GATEWAY.md](GATEWAY.md) for the full contract:
images, prompt sizes, who may call, and the reply's shape.

**Other clients.** The same panel answers in other APIs' shapes, so a client
that speaks one of them works with the server's address as its base URL:

| Format | Route | For |
|---|---|---|
| OpenAI chat completions | `POST /v1/chat/completions` | OpenAI SDKs, most chat apps |
| OpenAI Responses | `POST /v1/responses` | `responses.create` clients (nothing is stored) |
| OpenAI legacy completions | `POST /v1/completions` | tools that send a `prompt` |
| Anthropic Messages | `POST /v1/messages` | Anthropic SDKs with `base_url` |
| Ollama | `POST /api/chat`, `/api/generate`, `GET /api/tags` | Open WebUI's Ollama connection, Ollama apps |
| Gemini | `POST /v1beta/models/bench/<collection>:generateContent` | Google GenAI SDKs with `base_url` |

    curl http://127.0.0.1:8122/v1/messages -H "Content-Type: application/json" \
      -d '{"model": "bench/<collection>", "max_tokens": 1024, "messages": [{"role": "user", "content": "..."}]}'

Each streams when asked: the reply starts at once, and each seat's answer
arrives as that seat finishes. A panel answers with text, so a request
carrying tools is refused with a 400 that points to **route mode**
(`bench/<collection>:route`, one member with its tools; see
[below](#from-claude-code-and-other-agents)). GATEWAY.md, "Other API formats",
has a curl for each and what is refused or ignored.

## From the command line

`bench` (or `benchwarmers`, the same program) does what the dashboard does,
without it: ask and judge, and run the bench itself. It is a client of a
running server, so the same commands work against one on another machine.

    bench status                                   what is running, and where
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
with three tools: `list_collections`, `ask` (a collection, a prompt,
optionally `merged`, `with` and `system`) and `verdict`. Nothing to start per
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

<!-- The MCP Registry's proof that the PyPI package is this server (server.json):
mcp-name: io.github.WendellResearch-AI/benchwarmers -->

## The suite

Models are scored on review work: find the faulty line, or say whether a
claim about some code is true. The repository ships a starter pack:

- **Real bugs** (`suite/tasks/rebench-*`): recent fixes from public projects,
  drawn from the SWE-rebench-V2 dataset, each paired with a control.
- **Claims** (`suite/tasks/claim-*`): true and false statements about this
  project's own code, with the code attached.

Add your own from your repositories (`config/repos.json` in the data folder
maps a name to a folder): `python suite/seed.py --repo <name>` plants
realistic bugs in your real functions, each with a clean control. Private
code makes the best tasks, because no model has trained on it. See
[suite/README.md](suite/README.md), [suite/REBENCH.md](suite/REBENCH.md) and
[BENCHMARKS.md](BENCHMARKS.md).

## Docker and hosting

One container runs the dashboard, the gateway (on the same port, at `/v1`)
and the scheduler, with the data folder as a volume and no `llm` CLI (the
built-in client makes every call). Put the secrets in a `.env` file beside
`compose.yaml`:

    BENCHWARMERS_ADMIN_TOKEN=<16 or more characters>
    BENCHWARMERS_KEY_OPENROUTER=sk-or-...

then `docker compose up -d` and open http://127.0.0.1:8122. The compose file
publishes on this machine only; see its comments for a proxy.

**Hosted, always on:** [HOSTING.md](HOSTING.md) compares Fly.io, Railway,
Render and a VPS with Caddy (cost, sleep, disks, HTTPS), with a template for
each under [deploy/](deploy/).

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/WendellResearch-AI/benchwarmers)

Render is one click: a paid instance with a 1 GB disk and a generated admin
token. Fly.io is four commands from a checkout (`cp deploy/fly.toml .`,
`fly launch --copy-config --no-deploy`, `fly secrets set
BENCHWARMERS_ADMIN_TOKEN=...`, `fly deploy`). Railway is a few steps in its CLI
or browser, in [HOSTING.md](HOSTING.md#railway).

## What leaves your machine

Benchwarmers has no account, no telemetry and no server of its own; nothing
reaches Wendell Research. What it sends, and where:

- **Your prompts**, only to the endpoints you add: a provider, an aggregator,
  a hub such as Open WebUI, or a local runtime. That includes the probes, the
  suite and onboarding, which spend calls on your keys. Add only endpoints you
  are content to send that work to.
- **Catalog reads**, no key and nothing of yours: models.dev and FreeLLMAPI
  once a day, and OpenRouter's public model list.
- **Sign-ins**, only when you choose one: the provider's own sign-in page.
- **Charts**: the dashboard's browser page loads Observable Plot from jsDelivr
  at a pinned version.
- **Seeding real-bug tasks**, only when you run `suite/rebench.py`: the
  SWE-rebench-V2 rows from Hugging Face and the files from GitHub.

Keys stay in the environment or llm's key file (`keys.json` in its user
folder), never in the data folder. [PRIVACY.md](PRIVACY.md) and [SECURITY.md](SECURITY.md) have the
detail.

## Limits

- **Tools go through route mode only.** A panel answers with text; a request
  with tools is refused there, not trimmed. Route mode passes turns only to
  members on chat-completions endpoints, keeps its sticky conversations in
  memory (a restart starts each on a fresh pick), and has been run end to end
  with Claude Code; the Codex, opencode and Cline set-ups are written from
  their docs. Shadow seats (other members answering an agent's turn for
  evaluation) are planned, not built.
- **Some parts are checked against fakes only.** The native Anthropic and
  Gemini endpoints, and the LiteLLM and CLI Proxy API hub adapters, are built
  from those APIs' documentation and source and tested against fake servers,
  not yet against the real services.
- **A merged reply arrives in one piece** once the merger has finished; the
  seats before it stream as they finish.
- **Pacing and the ledger cover the built-in client.** Calls made through the
  `llm` CLI, when you choose it, are neither paced nor counted.
- **Calls cost.** Probes, onboarding and the suite run on your keys and free
  tiers. Onboarding runs one model at a time for that reason (Quick spreads
  the suite over the scheduled passes); a suite pass is paced, not burst.
- **The scheduler lives in the server.** Its jobs run while the dashboard (or
  the container, or the service) is up; a missed run is made up once, within
  seven days, when it comes back.
- **One install, one person** at heart. Logins and gateway keys exist for a
  LAN or a server, but there are no per-user views or roles.
- **The onboarding queue lives in the dashboard's memory.** Restarting it
  drops the rest of a batch; each started model's log stays in `runs/`.
- **Free tiers change without notice.** Discover's prices come from public
  catalogs, and some providers' free models work only in their own app
  (switched per provider on Settings).

## Security

Both servers listen on 127.0.0.1 by default and accept only same-origin writes
for their own host name, so a web page in your browser cannot drive them. To
listen anywhere else, set `BENCHWARMERS_ADMIN_TOKEN` first; without it they
refuse to start. With it, the dashboard asks for a login and the gateway takes
named, revocable **gateway keys**. Settings, under Server and access, shows how
this stands (whether a secret is set, never its value).

[SECURITY.md](SECURITY.md) has the whole model and how to report a
vulnerability.

## Development

    python scripts/check.py             # every self-check and test (--quick skips the slow one)

The core is the Python standard library only. CI runs the checks on Linux and
Windows for Python 3.11 to 3.13, and on macOS.

Benchwarmers isn't taking outside contributions yet; that opens once it is
more established. A security problem goes through [SECURITY.md](SECURITY.md).

## Licence

MIT, Copyright (c) 2026 Wendell Research ([LICENSE](LICENSE)). The starter
tasks and bundled fonts carry their own licences, listed in [NOTICE](NOTICE).
Use is also covered by [TERMS.md](TERMS.md).
