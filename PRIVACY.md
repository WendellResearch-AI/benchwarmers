# Privacy policy

*Effective 8 November 2026.*

Benchwarmers runs on your own machine or server. It has no account, no
telemetry and no server of its own, and nothing you do with it reaches
Wendell Research.

## What leaves your machine

- **Your prompts**, only to the endpoints you add
  (a provider, an aggregator, a hub such as Open WebUI, or a local runtime).
  That includes what the gateway relays for your apps, and the calls the
  probes, the suite and onboarding make. A decision model on one of those
  endpoints is also sent a gateway request, to label its shape or, when it
  names no collection, to pick one; a seat's short reply that may be a
  provider's notice; and, by the daily decision benchmark, past gateway
  requests, answers you judged with their request, and redacted error text.
  Each endpoint's own privacy policy applies to what it receives; some free
  tiers keep prompts or train on them.
- **Catalog reads**, carrying nothing of yours: FreeLLMAPI (its feed and its
  site's sitemap) and models.dev (at most once a day) and OpenRouter's public
  model list. With an NVIDIA connection on the bench, the public file of rate
  limits build.nvidia.com states, once a day and when you press Refresh on its
  quota tile.
- **Model lists**, carrying no prompt: each Open WebUI connection's list of the
  models its provider serves, asked through Open WebUI with the key it already
  holds for that provider, once a day and when you press Refresh lists on
  Discover. Only the models' ids, names, context sizes, prices and abilities
  are kept.
- **Sign-ins**, only when you choose one: the provider's own sign-in page, in
  your browser, and the provider's token endpoint.
- **Quota reads**, only when you press Refresh on Quota management, carrying
  no prompt:
  - OpenRouter's key status, asked with your key.
  - xKiro's usage, asked with your key for it (from its Open WebUI connection
    when no endpoint has one). Its reply names the account's owner; only the
    quota figures are kept.
  - Requesty's usage for the key and its public model list, asked the same
    way; only the count of free-model requests is kept.
  - For CLI Proxy API's sign-ins, through the proxy, the quota endpoint of the
    provider each sign-in belongs to (Google's, ChatGPT's).
- **Charts**: the dashboard's page loads Observable Plot from jsDelivr, at a
  pinned version, in your browser.
- **Posts**, only when you click Post on X, Bluesky, LinkedIn or Reddit in a
  Benchmarks report's Share menu: that site opens in your browser with the
  report's name and the project's link (LinkedIn's, the link alone), and
  nothing else. The report's image
  goes only to your clipboard, a file, or the app you pick in your system's
  share sheet (Share…); you choose whether to attach it. Copying a table puts
  it on your clipboard and sends nothing.
- **Real-bug task seeding**, only when you run `suite/rebench.py`: dataset rows
  from Hugging Face and source files from GitHub.

Keys and sign-in tokens stay in your environment or llm's key file
(`keys.json` in llm's user folder, used whether or not the `llm` CLI is
installed). [SECURITY.md](SECURITY.md) has the full detail.

## What stays on your machine

Everything else: your endpoints, collections, verdicts, call logs, the usage
ledger (tokens per call, never the prompt), the decision models' log (their
answers, never the question), probe history and task answers live in your
data folder, which is yours to back up, move or delete. An export (Settings,
Export and import) is that folder, or its settings, as a .zip your browser
downloads; it holds your endpoints' keys only if you tick them.

## Third parties

The endpoints you add, the catalogs above, jsDelivr, GitHub, Hugging Face and
any app you connect to the gateway each have their own privacy policies.

## Changes

Changes to this policy are made in this file, and its history is in the
repository's commits.

## Contact

Questions: [open an issue](https://github.com/WendellResearch-AI/benchwarmers/issues).
