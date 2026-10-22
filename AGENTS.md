# Setting up Benchwarmers with a person (for coding agents)

You are helping someone install Benchwarmers, a self-hosted panel gateway for
LLMs: one prompt goes to a named collection of models, every answer comes back
labelled, and the person's verdicts decide who sits on the next panel. Follow
these steps with them in order. Each says what to run and what to check.

## Ground rules

- **Never print, paste, log or commit an API key**, and never put one in a JSON
  file. Keys live in exactly one of two places (step 3).
- **Their data is not the code.** Endpoints, registrations, collections,
  verdicts and history live in a data folder outside the checkout. Never add
  anything from it to the repository.
- **Ask before spending model calls.** Onboarding probes a model (4 calls) and
  runs the review suite (more); free tiers run out. Say what a step costs.
- **Prefer the dashboard for anything involving a sign-in.** Signing in to a
  provider happens on the provider's own page in the person's browser; you
  cannot and must not do it for them.

## 1. Install

Python 3.11 or later; nothing else is required.

    git clone https://github.com/WendellResearch-AI/benchwarmers
    cd benchwarmers
    python -m venv .venv
    .venv/bin/pip install llm          # optional; Windows: .venv\Scripts\pip install llm

The `llm` CLI is today's default model client. Without it, set
`BENCH_CLIENT=builtin` to use the built-in client (`metrics/client.py`), which
needs nothing installed.

Check: `python scripts/check.py --quick` ends with `all checks pass`.

## 2. Make the data folder

    python scripts/init.py                 # or: python scripts/init.py <folder>

It creates the folder from `examples/` (by default in the OS's per-user data
folder) and points this checkout at it with a `.benchwarmers-home` file.
`BENCHWARMERS_HOME=<folder>` overrides the pointer.

Check: `python metrics/paths.py` prints the folder and why it was chosen.

## 3. Add a first endpoint, and its key

An endpoint is any OpenAI-compatible API: a provider (Groq, Mistral), an
aggregator (OpenRouter), a hub the person runs (Open WebUI, LiteLLM, CLI Proxy
API) or a local runtime (Ollama). Three ways, best first:

- **Sign in from the dashboard** (step 4 opens it on this): OpenRouter,
  NanoGPT, Hugging Face, Pollinations, a ChatGPT plan, GitHub Models, or a
  local Ollama. No key is copied by hand.
- **`init` with a key**, asked for without echo:

      python scripts/init.py --endpoint openrouter https://openrouter.ai/api/v1 --key

- **An environment variable**, for Docker and servers. The variable is
  `BENCHWARMERS_KEY_<NAME>`, where `<NAME>` is the endpoint's key name
  upper-cased with `-` written as `_`:

  | Endpoint key name | Variable |
  |---|---|
  | `openrouter` | `BENCHWARMERS_KEY_OPENROUTER` |
  | `openrouter-2` | `BENCHWARMERS_KEY_OPENROUTER_2` |
  | `my-hub` | `BENCHWARMERS_KEY_MY_HUB` |

  Without the variable, keys come from the `llm` CLI's key store
  (`llm keys set <name>` writes there). The variable wins when both exist.

One provider can be several endpoints (a pasted key and a sign-in, or two
accounts); each needs its own name, and Benchwarmers suggests a free one.

## 4. Start the dashboard

    python serve.py                        # http://localhost:8122

With no endpoint yet it opens on adding one. It listens on 127.0.0.1 only; to
reach it from elsewhere see "Beyond this machine" below.

Check: the page loads and Endpoints lists the endpoint from step 3.

## 5. Onboard a few models

In the dashboard: **Onboard a model**, pick a model the endpoint serves. Or:

    python scripts/onboard.py "vendor/model-id" --endpoint openrouter
    python scripts/onboard.py "vendor/model-id" --endpoint openrouter --skip-suite   # probe only

Each is registered, probed (4 calls) and run through the suite. Confirm the
cost with the person first. Three to five models make a useful first panel.

## 6. Make a collection, start the gateway

On the Collections page: a name, the members a panel always includes, and the
shape of work it is for (review, design, ...). Then:

    python gateway.py                      # http://127.0.0.1:8123/v1

Any OpenAI-compatible client asks for the model `bench/<collection>`:

    curl http://127.0.0.1:8123/v1/chat/completions -H "Content-Type: application/json" \
      -d '{"model": "bench/reviewers", "messages": [{"role": "user", "content": "..."}]}'

Every seat's answer comes back, plus a `bench` field with each seat's call id.
For a chat window that shows one answer, ask for `bench/<collection>:merged`
(or set the collection's reply to merged): a merger model writes one answer
saying where the seats agreed and disagreed, and every seat is still in
`bench`.
Photos go in as OpenAI `image_url` parts (an https URL, or a `data:` URL up to
20 MB a request); only models that take images sit, and the others are listed
in `bench.skipped`. One the catalog knows nothing about sits once its model
popup ticks Vision / OCR.
After the person checks an answer against the real work, record a verdict
(dashboard, or `POST /bench/verdict`); verdicts choose the next panels. See
GATEWAY.md for the whole contract.

## Beyond this machine

Both servers refuse to listen anywhere but 127.0.0.1 without an admin token:

    BENCHWARMERS_ADMIN_TOKEN=<16 or more characters>
    BENCH_BIND=0.0.0.0
    BENCHWARMERS_PUBLIC_URL=https://bench.example.com    # the address browsers use

The dashboard then asks for the token once (a session cookie for a week), and
the gateway routes need a gateway key made on Settings (`Authorization: Bearer
<key>`). Give each app its own key, limited to the collections it uses and a
rate; `BENCHWARMERS_REQUIRE_KEYS=1` asks for keys on this machine too. A
browser app on another origin also needs `BENCH_ALLOWED_ORIGINS`. In Docker: put the token and the `BENCHWARMERS_KEY_<NAME>` variables in
a `.env` beside `compose.yaml`, then `docker compose up -d`; the dashboard and
gateway share one port and the data folder is a volume.

## When something looks wrong

- **"no endpoints configured"** from a script: add one (step 3).
- **A model answers with an empty string**: usually a reasoning model that ran
  out of its output budget, not a dead model.
- **"sign in again"** on a model: the endpoint's key was refused (expired or
  revoked). Fix it on the Endpoints page (Sign in again, or Change key); it never
  retires the model.
- **A server will not start**: it says why; beyond 127.0.0.1 it needs
  `BENCHWARMERS_ADMIN_TOKEN`.
