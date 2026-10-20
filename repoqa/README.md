# `repoqa/` — the long-context tier

**Can this model find one described function inside 16k tokens of real code?**

```
python repoqa/run.py --model kimi-k3                 # 20 calls, python
python repoqa/run.py --model kimi-k3 --repos 10      # 100, a full language
python repoqa/run.py --model kimi-k3 --languages python,rust
```

[RepoQA](https://github.com/evalplus/repoqa) — same authors as EvalPlus, which
`floor/` already uses. Adopted 2026-09-09, **on demand, not on a timer**.

## Why this tier exists

It measures the one thing nothing else here could see: whether a model
actually **reads** a long document or loses the middle of it.

| Tier | Long prompt? | Checks the answer? |
| --- | --- | --- |
| `perf/` | yes | **no** — it only times it |
| `suite/` | yes | yes, but six tasks on our own repos |
| `floor/` | no — HumanEval problems are tiny | yes |
| **`repoqa/`** | **yes, ~16k tokens** | **yes** |

The real offloads run **7.9k to 60k characters**, and one was a 41KB
pre-commit review. That is the binding constraint on this work and it was
unmeasured: `route.py` already excludes four models for "failing on large
prompts", but that is about erroring out, not about comprehension.

## How to read it — DOWNWARD, like floor

RepoQA is 2024 and built from public repositories, so it fails the date check
in [`BENCHMARKS.md`](../BENCHMARKS.md). Treat it exactly as `floor/` was
treated:

> A **low** score is real evidence — the model could not find a function it
> was handed and given a description of. A **high** score proves less.

**It is a better floor than `floor/` was**, for one reason. HumanEval asks for
a small isolated function, and memorisation answers that outright. Here the
answer is *in the prompt*, and the question is whether the model can find it.
Contamination helps less with retrieval than with recall.

Scoring is **objective and needs no judge**: an answer counts when it is the
best match to the ground-truth function and clears a similarity threshold.
That matters — an LLM judge would have added both a cost and a new bias.

## Two things that are not obvious

**Generation does not use `repoqa.search_needle_function`.** Its OpenAI
provider calls `signal.alarm`, which does not exist on Windows — the same wall
`evalplus.codegen` presented, from the same authors. Generation runs through
the `llm` CLI path that `suite/` and `floor/` already use, and RepoQA does the
part carrying the value: building the contexts and scoring the answers. That
is better than patching theirs, for the reason `floor/` gives — our path tells
a refusal apart from an upstream error apart from a timeout, so a model that
was rate-limited is not recorded as one that could not find the function.

**Repositories are trimmable; needles are not.** RepoQA plants the i-th needle
at `(i + 0.5) / len(needles)` through the context, so a repository's ten
needles sweep the document front to back. Keeping fewer would sample only the
front — and *where* the needle sits is precisely what this tier measures. One
repository is ten needles is one full position sweep. `--repos` takes the
slice instead.

A full sweep is 500 tasks (5 languages × 10 repos × 10 needles) at 16k tokens
each, which is three floor runs. The default 2 repos is 20 calls, which is the
difference between a check somebody runs and one nobody does.

## Install

```
pip install repoqa
```

The dataset (~2026-04 release) downloads itself on first use and is cached
outside the repository. `repoqa/results/` is gitignored, like `floor/results/`.
