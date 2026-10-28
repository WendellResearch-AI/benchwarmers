# Privacy policy

*Effective 23 October 2026.*

Benchwarmers runs on your own machine or server. It has no account, no
telemetry and no server of its own, and nothing you do with it reaches
Wendell Research.

## What leaves your machine

- **Your prompts**, only to the endpoints you add
  (a provider, an aggregator, a hub such as Open WebUI, or a local runtime).
  That includes what the gateway relays for your apps, and the calls the
  probes, the suite and onboarding make. Each endpoint's own privacy policy
  applies to what it receives; some free tiers keep prompts or train on them.
- **Catalog reads**, carrying nothing of yours: FreeLLMAPI (its feed and its
  site's sitemap) and models.dev (at most once a day) and OpenRouter's public
  model list.
- **Sign-ins**, only when you choose one: the provider's own sign-in page, in
  your browser, and the provider's token endpoint.
- **Charts**: the dashboard's page loads Observable Plot from jsDelivr, at a
  pinned version, in your browser.
- **Real-bug task seeding**, only when you run `suite/rebench.py`: dataset rows
  from Hugging Face and source files from GitHub.

Keys and sign-in tokens stay in your environment or llm's key file
(`keys.json` in llm's user folder, used whether or not the `llm` CLI is
installed). [SECURITY.md](SECURITY.md) has the full detail.

## What stays on your machine

Everything else: your endpoints, collections, verdicts, call logs, the usage
ledger (tokens per call, never the prompt), probe history and task answers
live in your data folder, which is yours to back up,
move or delete.

## Third parties

The endpoints you add, the catalogs above, jsDelivr, GitHub, Hugging Face and
any app you connect to the gateway each have their own privacy policies.

## Changes

Changes to this policy are made in this file, and its history is in the
repository's commits.

## Contact

Questions: [open an issue](https://github.com/WendellResearch-AI/benchwarmers/issues).
