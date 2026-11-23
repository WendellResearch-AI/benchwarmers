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
  <a href="https://skills.sh/wendellresearch-ai/benchwarmers/benchwarmers"><img src="https://img.shields.io/badge/skills.sh-111111" alt="On skills.sh"></a>
  <a href="https://glama.ai/mcp/servers/WendellResearch-AI/benchwarmers"><img src="https://glama.ai/mcp/servers/WendellResearch-AI/benchwarmers/badges/score.svg" alt="Benchwarmers MCP server, quality and maintenance score on Glama"></a>
  <a href="https://cursor.directory/plugins/benchwarmers"><img src="https://img.shields.io/badge/cursor.directory-111111?logo=cursor&logoColor=white" alt="On cursor.directory"></a>
  <a href="USAGE.md#from-claude-code-and-other-agents"><img src="https://img.shields.io/badge/Antigravity-plugin-3186FF?labelColor=111111&logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2CPHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjEyIDE3IDg2IDgwIj48cGF0aCBkPSJNODkuNyA5My43YzQuNjcgMy41IDExLjY3IDEuMTcgNS4yNS01LjI1Qzc1LjcgNjkuNzggNzkuNzggMTguNDUgNTUuODcgMTguNDVTMzYuMDMgNjkuNzggMTYuNzggODguNDVjLTcgNyAuNTggOC43NSA1LjI1IDUuMjVDNDAuMTIgODEuNDUgMzguOTUgNTkuODYgNTUuODcgNTkuODZTNzEuNjIgODEuNDUgODkuNyA5My43WiIgZmlsbD0iIzMxODZGRiIvPjxtYXNrIGlkPSJtIiBtYXNrVW5pdHM9InVzZXJTcGFjZU9uVXNlIiB4PSIxMyIgeT0iMTgiIHdpZHRoPSI4NSIgaGVpZ2h0PSI3OCI%2BPHBhdGggZD0iTTg5LjcgOTMuN2M0LjY3IDMuNSAxMS42NyAxLjE3IDUuMjUtNS4yNUM3NS43IDY5Ljc4IDc5Ljc4IDE4LjQ1IDU1Ljg3IDE4LjQ1UzM2LjAzIDY5Ljc4IDE2Ljc4IDg4LjQ1Yy03IDcgLjU4IDguNzUgNS4yNSA1LjI1QzQwLjEyIDgxLjQ1IDM4Ljk1IDU5Ljg2IDU1Ljg3IDU5Ljg2UzcxLjYyIDgxLjQ1IDg5LjcgOTMuN1oiIGZpbGw9IiNmZmYiLz48L21hc2s%2BPGcgbWFzaz0idXJsKCNtKSI%2BPGcgZmlsdGVyPSJ1cmwoI2EpIj48ZWxsaXBzZSBjeD0iOTYuNDkiIGN5PSIzNS4xMiIgcng9IjI5LjUiIHJ5PSIzMC4xNSIgdHJhbnNmb3JtPSJyb3RhdGUoNzYuOTIgOTYuNDkgMzUuMTIpIiBmaWxsPSIjRkM0MTNEIi8%2BPC9nPjxnIGZpbHRlcj0idXJsKCNiKSI%2BPGVsbGlwc2UgY3g9IjkuMDMiIGN5PSI0MS42NiIgcng9IjMwLjgzIiByeT0iMzkuOTQiIHRyYW5zZm9ybT0icm90YXRlKDc0LjEzIDkuMDMgNDEuNjYpIiBmaWxsPSIjMDBCOTVDIi8%2BPC9nPjxnIGZpbHRlcj0idXJsKCNjKSI%2BPGVsbGlwc2UgY3g9Ijc1Ljc1IiBjeT0iMTA0LjgyIiByeD0iMjkuMDIiIHJ5PSIyNy45NCIgdHJhbnNmb3JtPSJyb3RhdGUoNzYuOTIgNzUuNzUgMTA0LjgyKSIgZmlsbD0iIzMxODZGRiIvPjwvZz48ZyBmaWx0ZXI9InVybCgjZCkiPjxlbGxpcHNlIGN4PSIzMy41NyIgY3k9IjM1LjQiIHJ4PSIzMy41NyIgcnk9IjM1LjQiIHRyYW5zZm9ybT0ibWF0cml4KC0uNDEgLjkxMiAtLjkxMiAtLjQxIDEwMS4yNSAtMTUuMTcpIiBmaWxsPSIjRkJCQzA0Ii8%2BPC9nPjwvZz48ZGVmcz48ZmlsdGVyIGlkPSJhIiB4PSIyOCIgeT0iLTMzIiB3aWR0aD0iMTM3IiBoZWlnaHQ9IjEzNiIgZmlsdGVyVW5pdHM9InVzZXJTcGFjZU9uVXNlIj48ZmVHYXVzc2lhbkJsdXIgc3RkRGV2aWF0aW9uPSIxOC44Ii8%2BPC9maWx0ZXI%2BPGZpbHRlciBpZD0iYiIgeD0iLTYzIiB5PSItMjMiIHdpZHRoPSIxNDQiIGhlaWdodD0iMTI5IiBmaWx0ZXJVbml0cz0idXNlclNwYWNlT25Vc2UiPjxmZUdhdXNzaWFuQmx1ciBzdGREZXZpYXRpb249IjE2Ii8%2BPC9maWx0ZXI%2BPGZpbHRlciBpZD0iYyIgeD0iMTYiIHk9IjQ0IiB3aWR0aD0iMTE5IiBoZWlnaHQ9IjEyMSIgZmlsdGVyVW5pdHM9InVzZXJTcGFjZU9uVXNlIj48ZmVHYXVzc2lhbkJsdXIgc3RkRGV2aWF0aW9uPSIxNS4yIi8%2BPC9maWx0ZXI%2BPGZpbHRlciBpZD0iZCIgeD0iLTgiIHk9Ii02MSIgd2lkdGg9IjEyNyIgaGVpZ2h0PSIxMjQiIGZpbHRlclVuaXRzPSJ1c2VyU3BhY2VPblVzZSI%2BPGZlR2F1c3NpYW5CbHVyIHN0ZERldmlhdGlvbj0iMTMuOCIvPjwvZmlsdGVyPjwvZGVmcz48L3N2Zz4%3D" alt="Antigravity plugin"></a>
  <a href="#docker-and-hosting"><img src="https://img.shields.io/badge/Docker-compose-2496ED?logo=docker&logoColor=white" alt="Runs in Docker"></a>
  <a href="https://render.com/deploy?repo=https://github.com/WendellResearch-AI/benchwarmers"><img src="https://img.shields.io/badge/Render-deploy-8A05FF?logo=render&logoColor=white" alt="Deploy to Render in one click"></a>
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

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset=".github/bench-call-dark.svg">
    <img src=".github/bench-call.svg" width="900" alt="A real call, time compressed. A terminal types bench ask core “What happens when path doesn’t exist? Python 3.12” -f load.py, and the file shows briefly: a function that opens a file in a try block, sets data to None in except OSError as err, and afterwards logs a warning that uses err. Benchwarmers seats panel p20261120-222954-eb8d on bench/core, 5 members and 2 explorers, and all seven fan out at once. The answers land in the order they really finished, each labelled by model and seat: mercury-3 (member, 2.1 s) says err is cleared when the except block ends, so log.warning raises NameError; Beam-1.0-501B-A23B (member, 3.5 s) says it logs a warning and returns None; gemini-3.8-flash (7.5 s) and big-pickle (8.1 s) say UnboundLocalError; the explorers gemma-4-31b-it (11.9 s) and Qwen3.8-27B (12.5 s) say it logs a warning and returns None; and kimi-k3.1-pro (96.4 s) says UnboundLocalError, nothing logged. The verdicts: 4 useful and 3 confidently wrong, because Python deletes err when the except block ends; they go on the record, and the bench trusts them less next time.">
  </picture>
</p>

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

**Not another gateway.** Gateways choose where a request goes by quota, cost
and speed; Benchwarmers finds out which of your models is *right*, and runs
happily on top of one. [COMPARISON.md](COMPARISON.md) has the full tables and
the set-up.

| Related tools | Examples | What they do | What Benchwarmers does differently |
|---|---|---|---|
| Gateways and routers | OmniRoute, freellmapi, LiteLLM, OpenRouter, Portkey | One request to one model, picked by quota, cost, speed and health, with fallback | Fans one prompt out to a panel and returns every answer; for agents, routes to one member by its verdicts. Runs on top of them |
| Councils and ensembles | OpenRouter Fusion, OmniRoute's fusion, LLM Council, Mixture-of-Agents | Several models, then a judge or chairman writes one answer | Keeps every answer, because who said what is the evidence; one merged summary only if you ask |
| Side-by-side chat | Open WebUI, LMArena | Several answers shown together, thumbs up or down for a leaderboard | Records a verdict per answer, with its false claims, from you or the calling agent, and routes by them |
| Evaluation harnesses | promptfoo, Inspect, SWE-bench | Score models offline on fixed test sets | Scores them on your real work and your own code, and keeps rotating new models in |

**The whole loop in one tool.** Each of those does one part well. Benchwarmers
asks, keeps every answer, takes your verdict and acts on it, in one place:

✅ yes · 🟡 partly or differently · ❌ no

| | **Benchwarmers** | Gateways and routers | Councils | Side-by-side chat | Evaluation harnesses |
|---|---|---|---|---|---|
| One prompt, every answer back, labelled by model | ✅ | 🟡 a judge writes one answer | 🟡 one final answer | ✅ | ✅ in a report |
| Agents with tools, with failover | ✅ route mode | ✅ | ❌ | ❌ | ❌ |
| New models rotate in on their own | ✅ explorer seats | 🟡 some explore a share of requests | ❌ | 🟡 from a pool you set | ❌ |
| A verdict on each answer, with its false claims | ✅ | ❌ | 🟡 models rank each other | 🟡 thumbs up or down | 🟡 assertions you write |
| Verdicts decide who answers next | ✅ | ❌ | ❌ | 🟡 a leaderboard | ❌ |
| Agents judge answers too, over MCP | ✅ | ❌ | ❌ | ❌ | ❌ |
| Tests from your own code | ✅ bugs seeded in copies of your code | 🟡 suites you write | ❌ | ❌ | ✅ tests you write |
| Health probes, dead models retired, free-tier limits kept | ✅ | ✅ | ❌ | ❌ | ❌ |
| Hundreds of providers, accounts pooled | 🟡 any OpenAI-compatible API or hub; runs on top of a gateway | ✅ | ❌ | ❌ | ❌ |

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset=".github/flow-dark.svg">
    <img src=".github/flow.svg" width="900" alt="Your app or agent (opencode, VS Code, Zed, Codex, Continue, goose, or any OpenAI, Anthropic, Gemini or Ollama-format client, set up from the Connect page) asks the gateway for one of your collections. In a panel, bench/&lt;collection&gt;, the collection's members, always seated, and explorer seats that rotate in the model unused longest all answer, and every answer comes back side by side, labelled by model and seat, once two have answered and the rest have had 60 seconds more (a late seat is still recorded), or as one summary with :merged, whose merger sees Source 1, Source 2 rather than model names. In route mode, bench/&lt;collection&gt;:route, for agents with tool calls, one member takes each turn, ranked by verdict score and kept for the conversation, the next member takes over on a 429, an error or silence, and an optional shadow seat answers the same turn in the background, for evaluation only. You or the calling agent check the answers against the real work and record a verdict per call: useful or not, with its real and false claims. The bench's record of calls and verdicts, daily health probes and the review suite chooses who sits on the next panel and ranks the members for route mode.">
  </picture>
</p>

## Contents

- [Try it](#try-it)
- [How it works](#how-it-works)
- [Use it from your apps](#use-it-from-your-apps)
- [Where to read more](#where-to-read-more)
- [The suite](#the-suite)
- [Docker and hosting](#docker-and-hosting)
- [What leaves your machine](#what-leaves-your-machine)
- [Limits](#limits)
- [Security](#security)
- [Development](#development)
- [Licence](#licence)

## Try it

**Version 1.2.** It runs with Python 3.11 or later on Windows, macOS and
Linux. The app is the Python standard library: nothing else to install.

    pipx install benchwarmers        # or: uv tool install benchwarmers
    benchwarmers                     # or try it first, without installing: uvx benchwarmers

or `npx @wendellresearch-ai/benchwarmers`, with Node 18+ as well. Either way
you get the same command, `benchwarmers`, and its short name `bench`. It keeps
your data in your user profile (`BENCHWARMERS_HOME` to choose another folder)
and opens the dashboard on http://localhost:8122. Then:

1. **Add an endpoint.** Sign in with a provider on its own page (OpenRouter,
   NanoGPT, Hugging Face, Pollinations, your ChatGPT plan, GitHub Models, a
   local Ollama), paste a key for any OpenAI-compatible API or hub (Anthropic
   and Gemini through their own APIs too), or pick a model server already
   running on this machine under "Found on this machine".
2. **Onboard a few models.** Tick one or several: each is registered, probed
   and run through the suite, one at a time.
3. **Make a collection** on the Collections page: the models a panel always
   includes, and what it is for.
4. **Connect an app** on the Connect page, or ask the gateway yourself
   ([below](#use-it-from-your-apps)).

From a checkout it is `git clone`, `python scripts/init.py` for a data folder,
and `python serve.py`; [AGENTS.md](AGENTS.md) has every step and option, and
Docker is [below](#docker-and-hosting).

## How it works

- **Collections and seats.** A collection names the models a panel always
  includes and the kind of work it is for. Each call adds explorer seats,
  chosen by how long a model has gone unused, so the bench keeps learning
  about models a router would stop asking. A member out of quota keeps its
  place while an explorer stands in.
- **Two ways to ask.** A **panel** returns every seat's answer, without
  waiting for the slowest: once two have answered, the rest get 60 seconds. In
  **route mode** an agent's turns go to one member at a time, ranked by its
  verdicts, with failover.
- **Verdicts.** Each answer carries a call id. Once you have checked it,
  record whether it was useful and which claims were real or false. Verdicts,
  not vibes, rank the models; one that is often confidently wrong shows as
  that.
- **Health and limits.** A daily one-word probe, a weekly speed test, and a
  model that stops answering retired automatically, evidence kept, restored in
  one click. Rate limits come from what providers say and from what you set,
  and are enforced before a call goes out; every call lands in a usage ledger.
- **Benchmarks.** Bugs found against false alarms, who is confidently wrong,
  real work that helped, each model against its own earlier scores, uptime and
  speed, on one page ([BENCHMARKS.md](BENCHMARKS.md)).
- **Discover.** Free and paid models worth adding, from FreeLLMAPI's catalog,
  models.dev and your hubs' own lists, smartest first.

## Use it from your apps

- **Any OpenAI-compatible client:** ask for the model `bench/<collection>` at
  `http://127.0.0.1:8122/v1`. The reply carries every seat's answer, and each
  seat's call id in its `bench` field; `bench/<collection>:merged` returns one
  summary instead. The same panel answers in the Anthropic, Gemini, Responses
  and Ollama formats.

      curl http://127.0.0.1:8122/v1/chat/completions -H "Content-Type: application/json" \
        -d '{"model": "bench/<collection>", "messages": [{"role": "user", "content": "Where can this return a stale value? ..."}]}'

- **Agents with tools** (Claude Code, Codex, opencode, Cline and the like):
  `bench/<collection>:route` gives each turn to one member, ranked by its
  verdicts, kept for the whole conversation and replaced by the next if it
  fails.
- **Ready set-ups:** the dashboard's **Connect** page fills in each popular
  app's configuration from your install, and **Quick setup** writes the files for
  Claude Code, opencode, VS Code, Zed, Codex, Continue and goose after showing them, with a
  copy of each file it changes. [CLIENTS.md](CLIENTS.md) has the same set-ups.
- **As MCP tools:** a running server is an MCP server at `/mcp`, with
  `list_collections`, `ask`, `verdict` and `pending_verdicts`. The Claude Code
  plugin adds `/benchwarmers:ask`, a skill saying when a panel helps, and a
  reminder of the seats still unjudged:

      /plugin marketplace add WendellResearch-AI/benchwarmers
      /plugin install benchwarmers@benchwarmers

- **From the command line:** `bench ask core "Review this diff" -f app.py`,
  `bench verdict <call id> --useful`, and the rest of what the dashboard does.

Once you have checked an answer against the real work, record a verdict for its
call: from the dashboard, `bench verdict`, the MCP `verdict` tool or
`POST /bench/verdict`.

## Where to read more

| For | Read |
|---|---|
| The dashboard, onboarding, collections, Connect, the CLI, MCP clients and plugins | [USAGE.md](USAGE.md) |
| The gateway's contract: API formats, route mode, the reply, who may call | [GATEWAY.md](GATEWAY.md) |
| Each app's set-up, ready to copy or hand to an agent | [CLIENTS.md](CLIENTS.md) |
| A coding agent setting Benchwarmers up for you | [AGENTS.md](AGENTS.md) |
| Reading the reports | [BENCHMARKS.md](BENCHMARKS.md) |
| The suite, and tasks from your own code | [suite/README.md](suite/README.md) |
| Servers and hosting platforms | [HOSTING.md](HOSTING.md) |
| How it compares with gateways, councils and arenas | [COMPARISON.md](COMPARISON.md) |
| What is planned | [ROADMAP.md](ROADMAP.md) |
| Security and privacy | [SECURITY.md](SECURITY.md), [PRIVACY.md](PRIVACY.md) |

<!-- The MCP Registry's proof that the PyPI package is this server (server.json):
mcp-name: io.github.WendellResearch-AI/benchwarmers -->

## The suite

Models are scored on review work: find the faulty line, or say whether a claim
about some code is true. A starter pack ships with real bugs from public
projects and claims about this project's own code; `python suite/seed.py
--repo <name>` copies functions from your own code into test tasks, each with
one realistic bug, which no model has trained on. Your files are only read,
never changed. See [suite/README.md](suite/README.md).

## Docker and hosting

One container runs the dashboard, the gateway and the scheduler, with the data
folder as a volume: put `BENCHWARMERS_ADMIN_TOKEN` and your keys in a `.env`
beside `compose.yaml`, then `docker compose up -d`. [HOSTING.md](HOSTING.md)
compares Fly.io, Railway, Render and a VPS, with a template for each under
[deploy/](deploy/).

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/WendellResearch-AI/benchwarmers)

## What leaves your machine

Benchwarmers has no account, no telemetry and no server of its own; nothing
reaches Wendell Research. What it sends, and where:

- **Your prompts**, only to the endpoints you add: a provider, an aggregator,
  a hub such as Open WebUI, or a local runtime. That includes the probes, the
  suite and onboarding, which spend calls on your keys, and the decision
  models' daily benchmark, which puts past requests, judged answers and
  redacted error text to them again. Add only endpoints you are content to
  send that work to.
- **Catalog reads**, no key and nothing of yours: FreeLLMAPI (its feed and
  its site's sitemap) and models.dev once a day, and OpenRouter's public
  model list. With an NVIDIA connection, the public file of rate limits
  build.nvidia.com states, once a day and on Refresh.
- **Model lists**, with no prompt: each Open WebUI connection's list of the
  models its provider serves, asked through Open WebUI with the key it
  already holds, once a day and on Discover's Refresh lists.
- **Sign-ins**, only when you choose one: the provider's own sign-in page.
- **Quota reads**, only when you press Refresh on Quota management, with no
  prompt: OpenRouter's key status, and xKiro's and Requesty's usage, each with
  your key for it (from its Open WebUI connection when no endpoint has one). CLI Proxy
  API or OmniRoute asks each sign-in's own provider for its quota. freellmapi,
  one-api and new-api are asked themselves, on your machine or LAN; one-api
  and new-api also when the Onboard dialog opens, for their channels.
- **Charts**: the dashboard's browser page loads Observable Plot from jsDelivr
  at a pinned version.
- **Posts**, only when you click Post on X, Bluesky, LinkedIn or Reddit in a
  report's Share menu: that site opens in your browser with the report's name
  and the project's link. The image goes only to your clipboard, a file, or
  the app you pick in your system's share sheet.
- **Seeding real-bug tasks**, only when you run `suite/rebench.py`: the
  SWE-rebench-V2 rows from Hugging Face and the files from GitHub.

Keys stay in the environment or llm's key file (`keys.json` in its user
folder), not in the data folder. An export (Settings, Export and import)
carries them only if you tick them, and an import that carries keys first
backs up the ones it replaces, in the data folder's `backups/`.
[PRIVACY.md](PRIVACY.md) and [SECURITY.md](SECURITY.md) have the detail.

## Limits

- **Tools go through route mode only.** A panel answers with text; a request
  with tools is refused there, not trimmed. Route mode passes turns only to
  members on chat-completions endpoints, keeps its sticky conversations in
  memory (a restart starts each on a fresh pick), and has been run end to end
  with Claude Code and opencode 2; the other set-ups on the Connect page are
  written from each app's docs, not run (Codex's profile among them). Shadow seats (another member answering an agent's turn
  afterwards, for evaluation) are opt-in (`shadow_rate` in `config/gateway.json`) and
  compare the shape of the answers (text or tool calls, which tools, arguments that
  parse), not whether either was right.
- **A merged reply arrives in one piece** once the merger has finished; the
  seats before it stream as they finish.
- **A late seat's answer is not in the reply.** A seat still answering a
  minute after two others did is named `late`; its answer reaches the
  dashboard and its call id `pending_verdicts`, not the caller. `quorum`
  and `grace` in `config/gateway.json` change that (GATEWAY.md, "The reply").
- **Pacing and the ledger cover the built-in client.** Calls made through the
  `llm` CLI, when you choose it, are neither paced nor counted.
- **Calls cost.** Probes, onboarding, the suite and the decision benchmark
  run on your keys and free tiers. Onboarding runs one model at a time for
  that reason (Quick spreads the suite over the scheduled passes); a suite
  pass is paced, not burst.
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
