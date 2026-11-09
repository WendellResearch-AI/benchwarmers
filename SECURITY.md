# Security

## Supported versions

Fixes ship on `main` and in the next release. Use the latest of either.

| Version | Supported |
| --- | --- |
| latest release, `main` | ✅ |
| older releases | ❌ |

## Reporting a vulnerability

Report it privately through
[GitHub Security Advisories](https://github.com/WendellResearch-AI/benchwarmers/security/advisories/new).
Please don't open a public issue for a vulnerability.

Include what you can: the version or commit, how it is run (from source or
Docker, bound to 127.0.0.1 or wider, with or without an admin token), steps to
reproduce, and what an attacker gains.

## The model

**On this machine by default.** The dashboard (`serve.py`, port 8122) and the
gateway (on the same port at `/v1`, or alone with `gateway.py` on 8123) listen
on 127.0.0.1. Every request is checked for the server's own host name (against
DNS rebinding), and every write must be a same-origin request with a JSON
body (a zip, for an import), a type no plain form can send, so a web page
open in your browser cannot drive either server. With no admin token
there is no login and the gateway asks for no key.

**Anywhere else, only with a login.** To listen beyond this machine
(`BENCH_BIND`, and `BENCH_PORT` / `BENCH_GATEWAY_PORT` for the ports), set
`BENCHWARMERS_ADMIN_TOKEN` (16 or more characters) first; without it both
servers refuse to start. With a token:

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

`BENCHWARMERS_REQUIRE_KEYS=1` asks for gateway keys on this machine too, with
the dashboard left open, so each local app has its own. A key can be limited
to some collections and to a number of requests a minute, and its name is on
every call it makes. A browser app on another origin is let in by
`BENCH_ALLOWED_ORIGINS` (CORS on the gateway's routes only; never `*`).

Settings, under Server and access, shows how all of this stands right now
(whether a secret is set, never its value). It is changed only in the
environment, then a restart: a page that could change its own login could
open itself to anyone.

## What Benchwarmers touches

- **Keys.** Endpoint keys come from `BENCHWARMERS_KEY_<NAME>` (the key name
  upper-cased, `-` as `_`) first, then from llm's key file (`keys.json` in
  its user folder, used whether or not the `llm` CLI is installed). They are
  never shown back and never put in a log. Sign-ins through a provider's page
  store their token the same way.
- **Exports.** Settings, Export and import, puts the endpoints' stored keys
  (key, admin key, refresh token; never one from the environment) in an
  export only when you tick them, in plain text in its `keys.json`: keep that
  file like a password and delete it once imported. Importing one puts its
  keys in the key store, and first backs up this install's own, with its files,
  to the data folder's `backups/before-import-<time>.zip`. An import is
  refused whole if any file in it would land outside its scope's folders.
- **The network.** Your prompts go only to the endpoints you add; the probes,
  the suite, onboarding and the decision benchmark spend calls there too.
  Otherwise: public catalog reads (models.dev, FreeLLMAPI, OpenRouter's model
  list), each Open WebUI connection's model list (through Open WebUI, with its
  key), a provider's sign-in page when you choose one, Observable Plot from
  jsDelivr in the dashboard's page, the site a report's Share menu posts to
  when you choose one (opened with the report's name and the project's link
  only), and Hugging Face and GitHub only when you run `suite/rebench.py`.
  [PRIVACY.md](PRIVACY.md) lists them.
- **Files.** It writes inside its data folder (`BENCHWARMERS_HOME`, or the
  folder `.benchwarmers-home` names) and, in llm's user folder, the key file
  (when you paste a key or sign in) and the model list (when it registers a
  model). Images sent to the gateway are never written to a log.
- **Processes.** Onboarding, the probes and the suite run this repository's
  own scripts with the same Python. Model ids that reach a command line are
  checked against a strict pattern first.

## Endpoints you add

An endpoint receives everything sent to the models on it, including any code
or document in a prompt. Benchwarmers cannot vouch for a provider; the
Endpoints page says the same. Some providers' free tiers keep prompts or train
on them: check each one's terms before you send it private work.
