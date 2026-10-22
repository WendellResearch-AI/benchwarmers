# Contributing to Benchwarmers

Thank you for looking. Bug reports, fixes, new provider sign-ins and new hub
adapters are all welcome.

**Before you write code**, open an
[issue](https://github.com/WendellResearch-AI/benchwarmers/issues) for
anything bigger than a small fix, so we can talk it over first. A security
problem goes through [SECURITY.md](SECURITY.md), never a public issue.

## The rules the code keeps

- **The core is the Python standard library.** No new dependency for what a
  few lines can do. An optional extra is possible when it earns its place.
- **Code and data apart.** Nothing an install owns lives in the checkout: read
  and write it under the data folder (`metrics/paths.py`, `DATA`).
- **No secret in a file, a log or a reply.** Keys come from the environment or
  the `llm` CLI's key store; pages show whether one is set, never its value.
- **Calls cost.** Anything that calls models runs one at a time per provider
  or paces itself; a test never calls a real model.
- **Each piece leaves its check.** A change with logic in it adds the smallest
  check that fails if the logic breaks: a `_selftest()` the file already has,
  or a few lines in the matching `scripts/test_*.py`. `scripts/check.py` runs
  them all.
- **Plain words** in the dashboard and the docs: say what a thing does, not
  what it is called inside.

## Good first contributions

- **A provider's sign-in**: an entry in `metrics/connect.py`, whose docstring
  says how. Only providers that publish a sign-in for other apps.
- **A hub adapter**, beside `metrics/owui.py`, `metrics/litellm_hub.py` and
  `metrics/cliproxy.py`.
- **A theme**: a file in `ui/themes/` that passes `scripts/check_themes.py`.
- **Suite tasks** from public projects' real fixes (see
  [suite/REBENCH.md](suite/REBENCH.md)).

## Making the change

1. Fork, and branch from `dev`: `main` is what installs run.
2. Make the change with its check, and run everything:

       python scripts/check.py

3. Open a pull request against `dev`. The template asks what it adds and what
   it was checked with; CI runs the checks on Linux, Windows and macOS.

By contributing, you agree that your contribution is licensed under the
project's [MIT license](LICENSE).
