# Roadmap

What Benchwarmers will do, decided but not yet built. What it does today is in
the README, and the limits of what is built under its "Limits".

## Later

- **A judge benchmark, before any model's verdict counts.** Callers judge
  about four seats in five; route mode and chat UIs judge none. A judge model
  could fill that gap, but without the code it grades how plausible an answer
  sounds, which is the trap "agreement is not evidence" names. So first
  measure: replay panels callers already judged, have each candidate judge
  grade them blind (no model names), and score its agreement with the caller's
  verdicts, above all on false claims backed by evidence. Like the decision
  models (BENCHMARKS.md). Only a judge that passes may then:
  - flag a caller's verdict it disagrees with, for a person to look at (a
    check on a biased caller), and
  - fill an unjudged panel, as its own source, lower weight, replaced by any
    caller verdict, never from the same family as a seat, and never in route
    mode, whose turns are mostly tool calls.

- **A built-in provider in opencode, through models.dev.** opencode reads its
  providers from [models.dev](https://github.com/anomalyco/models.dev), the
  open catalog its team keeps, and a provider is added there by pull request.
  We will, but not yet: first Benchwarmers needs a true model of its own to
  show, one every install serves and worth listing. The pull request adds
  `providers/benchwarmers/`: a `provider.toml` (`npm =
  "@ai-sdk/openai-compatible"`, `api = "http://127.0.0.1:8122/v1"`, `env =
  ["BENCHWARMERS_KEY"]`, a `doc` link), a `logo.svg` drawn in `currentColor`,
  and one schema-2 file per model, checked with `bun validate` and `bun run
  v1:check`. A local server can be listed: LM Studio is, at 127.0.0.1:1234.
  Before it:
  - a model ID without a colon in the gateway, since the file name is the ID
    and `bench/core:route.toml` cannot exist on Windows;
  - only models every install has go in the listing, as it is a fixed list.
    Each install's own collections keep coming from the opencode plugin
    (`clients/opencode`), which publishing to npm would make one
    `opencode plugin add` away.
