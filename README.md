# Benchwarmers

*Ask the whole bench. Keep every answer. Learn who to trust.* By Wendell Research.

Benchwarmers is a self-hosted **panel gateway** for LLMs. One prompt goes to a
named collection of models plus a couple of explorer seats; every answer comes
back, labelled by model and seat. You check the answers against the real work
and record a verdict, and those verdicts decide who sits on the next panel.

Around that loop: a dashboard, a catalog of model facts, health probes that
retire models which stop answering, rate limits learned from providers' own
error messages, and a review suite built from real bugs.

| Neighbour | What it does | What Benchwarmers does differently |
|---|---|---|
| Gateways and routers | One request to one model, with fallback | Fans one prompt out to a panel and returns every answer |
| Councils and ensembles | Several models merged into one answer | Keeps every answer, because who said what is the evidence |
| Side-by-side tools | Several answers shown together | Records verdicts and uses them to pick the next panel |
| Eval tools | Score models offline on fixed sets | Scores them on your real work and your own code |

## Status

**Early.** It runs from a checkout with Python 3.11 or later on Windows, macOS
and Linux. An installer (`pipx install benchwarmers`), dashboard login and a
Docker image are on the way to v0.1. Until then, expect rough edges and read
the code where this page is thin.

## Quick start (from source)

1. **Get the code** and a Python 3.11+ environment:

       git clone https://github.com/WendellResearch-AI/benchwarmers
       cd benchwarmers
       python -m venv .venv
       .venv/bin/pip install llm        # Windows: .venv\Scripts\pip install llm

   The [`llm`](https://llm.datasette.io/) CLI is the default model client today.
   The built-in client (`metrics/client.py`) replaces it with
   `BENCH_CLIENT=builtin` and will become the default.

2. **Choose a data folder.** Everything that is yours (endpoints, model
   registrations, collections, verdicts, probe history, task answers) lives
   there, never in the code. Set `BENCHWARMERS_HOME`, or write the folder's
   path on one line in a `.benchwarmers-home` file at the top of the checkout.
   Without either, the checkout itself is used.

3. **Add an endpoint**: any OpenAI-compatible API. In
   `<data folder>/config/endpoints.json`:

       {"openrouter": {"base": "https://openrouter.ai/api/v1", "key": "openrouter", "default": true}}

   and give the key to the `llm` CLI (the value never enters the data folder's
   JSON):

       llm keys set openrouter

   A hub such as Open WebUI or LiteLLM works the same way: its base URL and
   key.

4. **Start the dashboard** and onboard a few models from it ("Onboard a
   model"): each is registered, probed and run through the suite.

       python serve.py                  # http://localhost:8122

5. **Make a collection** on the Collections page (the models a panel always
   includes, and what it is for), then **start the gateway**:

       python gateway.py                # http://127.0.0.1:8123/v1

## Asking a panel

Any OpenAI-compatible client works: ask for the model `bench/<collection>`.

    curl http://127.0.0.1:8123/v1/chat/completions \
      -H "Content-Type: application/json" \
      -d '{"model": "bench/reviewers", "messages": [{"role": "user", "content": "Where can this function return a stale value? ..."}]}'

The reply carries every seat's answer in the message, and the same seats as
data in a `bench` field: model, seat, status, seconds and a call id. Record a
verdict per call once you have checked it:

    curl http://127.0.0.1:8123/bench/verdict -H "Content-Type: application/json" \
      -d '{"call": "<call id>", "useful": true, "note": "found the real bug", "real": ["..."], "false": ["..."]}'

or from the dashboard. Explorer seats rotate by how long a model has gone
unused, so the bench keeps learning about models a router would stop asking.
See [GATEWAY.md](GATEWAY.md) for the full contract.

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

## Checks

    python scripts/check.py             # every self-check and test (--quick skips the slow one)

## Security

Both servers listen on 127.0.0.1 only. Every request is checked for the
server's own host name (against DNS rebinding), and every write must be a
same-origin JSON request, so a web page in your browser cannot drive either
server. Keys are read from the `llm` CLI's key store and never written to the
data folder's JSON or shown back.

## Licence

MIT, Copyright (c) 2026 Wendell Research ([LICENSE](LICENSE)). The starter
tasks and bundled fonts carry their own licences, listed in [NOTICE](NOTICE).
