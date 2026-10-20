# Real-bug tasks (rebench)

`python suite/rebench.py --count 5` writes draft review tasks from real bug
fixes in public Python projects, each paired with a control. Added 2026-10-17.

## Why

The suite had two kinds of review task: hand-built ones on private code, and
seeded bugs (one small change in a private file; `suite/seed.py`). Seeded bugs
have an answer key by construction but are synthetic. Real fixes sit between
the two: a bug a person actually wrote and someone else actually fixed, with
the fix as the answer key.

## Source

[nebius/SWE-rebench-V2](https://huggingface.co/datasets/nebius/SWE-rebench-V2)
(CC-BY-4.0): about 32,000 merged pull requests in 20 languages, 7,243 of them
Python, each with its fix as a diff, the commit it was made against, and the
issue text. Read through the Hugging Face datasets-server HTTP API with
`urllib`; no `datasets` package. Each task records the project, pull request
URL, creation date and the project's licence.

**Contamination.** The dataset ends 2025-10-31 (checked 2026-10-17), and the
companion `nebius/SWE-rebench-V2-PRs` ends 2025-07-23 with no language
column, so neither is newer than many current models' training data. The
default `--since 2025-06-01` takes the newest months, which are the least
likely to have been trained on, not guaranteed unseen. Treat a model that
does far better here than on the private seeded tasks as possibly
remembering, not reviewing. The private-code tasks remain the uncontaminated
measure.

## What is kept

A fix is kept only if it:
- changes exactly one file, a `.py` file outside tests;
- is one hunk whose changed lines are one contiguous block;
- removes at most five lines and adds at most five;
- changes code, not only comments or whitespace;
- applies exactly to the file fetched at its base commit from
  raw.githubusercontent.com (otherwise it is skipped, never forced).

## The two tasks

- **Bug** (`rebench-<project>-<pr>-bug`): the file before the fix, numbered as
  in the file, trimmed to 400 lines around the change when longer (the prompt
  says which lines). The answer is `FAULT: line <n>` for any line the fix
  removed; where the fix only added lines, the line just before or after
  where they belong.
- **Control** (`...-control`): the same region after the fix. The answer is
  `NO FAULT`.

The prompt says the file either has a bug a later commit fixed or is the fixed
version, and never hints at the kind of bug. Scoring is the seeded tasks'
exact first-line regex.

Both are `"shape": "rebench"` and `"draft": true`. `metrics/route.py` keeps
the shape out of routing recall, like claims and seeded tasks, and
`suite/run.py` and `suite/pace.py` skip drafts.

## Review before removing draft

Read each bug task's `why`: the lines the fix removed and added, and the issue
text. Drop a pair when:
- the "bug" is a refactor, a rename, a message or log text change, or a
  dependency bump;
- the bug cannot be seen from this file alone, because it needs another
  module's contract to recognise;
- the fixed version visibly has the same problem elsewhere, so `NO FAULT`
  would be wrong.

When you drop one, add its instance id and the reason to
`suite/rebench-rejected.json`, so later runs do not offer it again.

A model naming a different real bug in the control is scored as inventing
one. That is the known cost of using the fix as the only key.

**First batch (2026-10-17):** 158 Python fixes since 2025-06-01; 11 passed the
filter (116 touched more than one file, 17 had several hunks), and review kept
5. The kept ones:
- **quantecon:** an inverted assignment in a running minimum.
- **matchms:** a regex that misses an uppercase `E` exponent.
- **scrapy:** `close()` uses an attribute that may never have been set.
- **narwhals:** values reindexed twice, running out of bounds.
- **more-itertools:** `hasattr` where `__reversed__ = None` means not
  reversible.

The six dropped are in the rejected file with reasons.

## Options

| Flag | Default | Meaning |
|---|---|---|
| `--count` | 5 | pairs to write |
| `--since` | 2025-06-01 | only fixes created on or after this date |
| `--offset` | 0 | skip this many matching rows (newest first) |
| `--scan` | 400 | stop after reading this many rows |
| `--dry-run` | off | fetch and check everything, write nothing |

A fix that already has a task (same instance id) is never written again.
`python suite/test_rebench.py` checks the diff filter, answer keys and window
without the network.
