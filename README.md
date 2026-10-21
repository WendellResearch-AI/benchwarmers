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
and Linux, or in Docker. An installer (`pipx install benchwarmers`) is on the
way to v0.1. Until then, expect rough edges and read the code where this page
is thin.

## Quick start (from source)

1. **Get the code** and a Python 3.11+ environment:

       git clone https://github.com/WendellResearch-AI/benchwarmers
       cd benchwarmers
       python -m venv .venv
       .venv/bin/pip install llm        # Windows: .venv\Scripts\pip install llm

   The [`llm`](https://llm.datasette.io/) CLI is the default model client today.
   The built-in client (`metrics/client.py`) replaces it with
   `BENCH_CLIENT=builtin` and will become the default.

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

4. **Onboard a few models** from it ("Onboard a model"): each is registered,
   probed and run through the suite.

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

## Docker

One container runs the dashboard, the gateway (on the same port, at `/v1`)
and the scheduler, with the data folder as a volume and no `llm` CLI (the
built-in client makes every call). Put the secrets in a `.env` file beside
`compose.yaml`:

    BENCHWARMERS_ADMIN_TOKEN=<16 or more characters>
    BENCHWARMERS_KEY_OPENROUTER=sk-or-...

then `docker compose up -d` and open http://127.0.0.1:8122. The compose file
publishes on this machine only; see its comments for a proxy.

## Security

Both servers listen on 127.0.0.1 by default. Every request is checked for the
server's own host name (against DNS rebinding), and every write must be a
same-origin JSON request, so a web page in your browser cannot drive either
server. On this machine with no admin token there is no login and the gateway
asks for no key, as before.

To listen anywhere else (`BENCH_BIND`, and `BENCH_PORT` / `BENCH_GATEWAY_PORT`
for the ports), set `BENCHWARMERS_ADMIN_TOKEN` first; without it both servers
refuse to start. With a token:

- the dashboard asks for it once, then keeps an `HttpOnly; SameSite=Strict`
  session cookie for a week (changing the token signs everyone out);
- the gateway's routes (`/v1/...`, `/bench/verdict`) take a **gateway key** as
  `Authorization: Bearer <key>`. Make named keys on Settings: each is shown
  once, kept only as a hash, revocable, and never opens the dashboard;
- `BENCHWARMERS_PUBLIC_URL` (e.g. `https://bench.example.com`) is the address
  browsers use behind a proxy or on a LAN address; its host and origin are
  accepted, and the cookie is `Secure` when it is https;
- every request is logged to stderr: route, status, and who (a key's name or
  admin), never a secret.

Endpoint keys come from `BENCHWARMERS_KEY_<NAME>` (the key name upper-cased,
`-` as `_`) first, then from the `llm` CLI's key store; they are never written
to the data folder's JSON or shown back.

## Licence

MIT, Copyright (c) 2026 Wendell Research ([LICENSE](LICENSE)). The starter
tasks and bundled fonts carry their own licences, listed in [NOTICE](NOTICE).
