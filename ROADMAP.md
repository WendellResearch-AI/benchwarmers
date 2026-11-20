# Roadmap

What Benchwarmers will do, decided but not yet built. What it does today is in
the README, and the limits of what is built under its "Limits".

## Later

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
