<h1 align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset=".github/logo-dark.svg">
    <img src=".github/logo.svg" width="480" alt="Benchwarmers, by Wendell Research: the name sitting on a crimson team bench">
  </picture>
</h1>

<p align="center">
  <a href="https://github.com/WendellResearch-AI/benchwarmers/releases"><img src="https://img.shields.io/github/v/release/WendellResearch-AI/benchwarmers?color=990000" alt="Latest release"></a>
  <a href="https://www.npmjs.com/package/@wendellresearch-ai/benchwarmers"><img src="https://img.shields.io/npm/v/@wendellresearch-ai/benchwarmers?color=cb3837&logo=npm" alt="npm"></a>
  <a href="https://pypi.org/project/benchwarmers/"><img src="https://img.shields.io/pypi/v/benchwarmers?color=3775a9&logo=pypi&logoColor=white" alt="PyPI"></a>
  <a href="https://github.com/WendellResearch-AI/benchwarmers/actions/workflows/check.yml"><img src="https://github.com/WendellResearch-AI/benchwarmers/actions/workflows/check.yml/badge.svg" alt="Checks"></a>
  <a href="#docker"><img src="https://img.shields.io/badge/Docker-compose-2496ED?logo=docker&logoColor=white" alt="Runs in Docker"></a>
  <a href="GATEWAY.md"><img src="https://img.shields.io/badge/OpenAI--compatible-gateway-111111" alt="OpenAI-compatible gateway"></a>
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

- **Asking several models at once.** One OpenAI-compatible request, a panel of
  answers. Where they disagree is where to look.
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
- **Keeping free and cheap models useful.** Health probes, learned rate limits
  and one endpoint for every app, over any OpenAI-compatible provider or hub.

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
- [Docker](#docker)
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

**v0.2, early.** It runs with Python 3.11 or later on Windows, macOS and
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
       python -m venv .venv
       .venv/bin/pip install llm        # Windows: .venv\Scripts\pip install llm

   The [`llm`](https://llm.datasette.io/) CLI is the default model client today.
   The built-in client (`metrics/client.py`) replaces it, chosen on Settings
   or with `BENCH_CLIENT=builtin` (which wins), and will become the default.

2. **Make a data folder.** Everything that is yours (endpoints, model
   registrations, collections, verdicts, probe history, task answers) lives
   there, never in the code:

       python scripts/init.py           # or: python scripts/init.py <folder>

   It starts the folder from `examples/` (in your OS's per-user data folder
   unless you name one) and points the checkout at it with a
   `.benchwarmers-home` file; `BENCHWARMERS_HOME` overrides that. With
   `--endpoint NAME BASE_URL --key` it also adds a first endpoint and asks for
   its key, which goes to the `llm` CLI's key store (`llm keys set NAME` does
   the same), never into the data folder.

3. **Start the dashboard.** With no endpoint yet it opens on adding one: sign
   in with a provider on its own page (OpenRouter, NanoGPT, Hugging Face,
   Pollinations, your ChatGPT plan, GitHub Models, or a local Ollama), or paste
   an API key for any OpenAI-compatible API, a hub such as Open WebUI or
   LiteLLM included. Only providers that publish a sign-in for other apps are
   offered; adding one is an entry in `metrics/connect.py` (its docstring says
   how).

       python serve.py                  # http://localhost:8122

4. **Onboard a few models** from it ("Onboard a model"): tick one or several;
   each is registered, probed and run through the suite, one at a time, with
   the progress beside the button.

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
  call, with the evidence kept, and can be restored in one click. Rate limits
  are learned from providers' own error messages and kept per connection,
  since models behind one key share them. The built-in scheduler runs all of
  this once Settings switches it on; a fresh install spends no calls until then.
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
arrives as that seat finishes. Agent and tool use is not supported yet: a
request carrying tools is refused with a 400 rather than answered without
them. GATEWAY.md, "Other API formats", has a curl for each and what is
refused or ignored.

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

`benchwarmers mcp` is an MCP server on stdio with three tools:
`list_collections`, `ask` (a collection, a prompt, optionally `merged`,
`with` and `system`) and `verdict`. It is a client of a Benchwarmers that is
already running, over the routes above, so start the dashboard (or the
service) first. The tool descriptions carry the rules: ask a collection, not
a favourite model; verify before acting; name every seat; send a verdict per
seat you judged.

**Claude Code plugin.** The plugin adds the MCP server, a `/benchwarmers:ask
<collection> <what to ask>` command and a `benchwarmers` skill saying when a
panel helps (adversarial review, exhaustive enumeration over real files) and
how to judge it:

    /plugin marketplace add WendellResearch-AI/benchwarmers
    /plugin install benchwarmers@benchwarmers

It needs Node 18 or later and Python 3.11 or later on PATH.

**Any other MCP client** (or Claude Code without the plugin): run
`benchwarmers mcp` once installed, `uvx benchwarmers mcp` or
`npx -y @wendellresearch-ai/benchwarmers mcp` without installing, or `python mcp_server.py` from a
checkout. For example:

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

## Docker

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

Keys stay in the environment or the `llm` CLI's key store, never in the data
folder. [PRIVACY.md](PRIVACY.md) and [SECURITY.md](SECURITY.md) have the
detail.

## Limits

- **No tools yet.** A panel answers with text, not a tool call, so agents
  that need tool use (Claude Code, Codex, opencode as callers) wait for a
  route mode; requests with tools are refused, not trimmed.
- **Early.** v0.1: no installer yet, merged replies stream in one piece, and the LiteLLM and
  CLI Proxy API hub adapters are built from those projects' source, not yet
  run against a real hub.
- **Calls cost.** Probes, onboarding and the suite run on your keys and free
  tiers. Onboarding runs one model at a time for that reason; a suite pass is
  paced, not burst.
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
