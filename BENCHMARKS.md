# Standard benchmarks: which to adopt, and why

Two repositories were proposed. They are not the same kind of thing, and only
one of them is a thing you can run.

---

## `leobeeson/llm_benchmarks` — a reading list, not a harness

**What it is:** a single README cataloguing benchmarks by category, with
descriptions and links. Three commits. **No code, no datasets, no runner.**

**Verdict: read it, cite it, do not vendor it.**

There is nothing to pull in. Copying a catalogue into this repo creates a
second copy that goes stale while the original is one click away. Its value is
that it is a well-organised map of what exists — use it the way you would use
a bibliography: to decide what to go and get.

Where it *is* useful is deciding what a benchmark is even for. The categories
it lays out (knowledge, reasoning, coding, instruction-following, safety) are
a reasonable checklist against which to notice that a model rated well on
"coding" may be bad at the specific coding task you have.

---

## `LiveCodeBench/LiveCodeBench` — a real harness, worth running, scoped

**What it is:** the official harness for the *LiveCodeBench* paper. 1,055
problems from LeetCode, AtCoder and Codeforces spanning May 2023 – April 2025.
It evaluates code generation, self-repair, code execution and test-output
prediction. It supports closed API models with a `--multiprocess` flag for
rate-limit-adjustable parallelism, so an OpenAI-compatible endpoint —
which is what Open WebUI serves — can be evaluated.

**Verdict: SUPERSEDED — do not adopt. See `LIVECODEBENCH.md`.**

> **Amended after trying it.** The original verdict below was "adopt it, but
> scoped". That was wrong, and the fact that disproves it is *in the paragraph
> above*: the problems span **May 2023 – April 2025**, while our models carry a
> 2026 cutoff. The date filter — the entire reason to adopt it — therefore
> selects zero problems, and any score without the filter is contaminated.
> I recorded the disqualifying date here and then spent six dependency fixes
> before checking it against the models. The reasoning below is left intact
> because it is still correct about *why* the property matters; it is only
> wrong that this benchmark can supply it to us.

### Why it is worth having

**Contamination-free by construction, which is the whole point.** Every
problem carries a release date, so you can evaluate a model *only* on problems
published after its training cutoff. Nearly every other public benchmark has
leaked into training sets, which is why scores drift upward without models
getting better at anything. For a file whose entire purpose is tracking
whether a model is actually improving, that property is worth more than the
score itself.

**It is objective.** Notes like the ones in `MODELS.md` are honest but they
are *my* judgement of my own offloads, which is exactly the kind of evidence
that flatters itself. A pass/fail test suite does not care.

### The caveats, stated before anyone runs it

**Runtime is the binding constraint, not accuracy.** From the smoke tests in
`runs/2026-09-03`, latency through this Open WebUI path ranges from 6.6 s
(`north-mini-code`) to 134 s (`nemotron-lightning`) on a *one-word* prompt.
1,055 problems against the slow end is on the order of **40 hours for one
model**. A full sweep of six models is not a thing that happens.

→ **Run a dated subset.** The most recent 50–100 problems, which are also the
ones least likely to be contaminated. That is a couple of hours for the fast
models and an overnight run for the slow ones, and it answers the question.

**Python 3.11 and `uv`.** wendell-research is on 3.14. It needs its own
environment; do not try to share one.

**It measures competitive programming, and that is not what we offload.**
This is the important caveat. The actual tasks routed to these models are:
read an unfamiliar file and describe its structure; draft a component against
a written spec; review one small surface adversarially. LiveCodeBench measures
none of those. A model can be excellent at Codeforces problems and still
invent a filename that does not exist — which is precisely the failure
`gemini-flash-3.8` produced twice.

→ Treat its score as **one axis**, useful for ranking raw coding capability
and for noticing when a model has genuinely regressed. Not as a proxy for
"good at our work".

---

## The third thing, which matters more than either

**A local task suite built from our own offload shapes.**

Neither repository can tell us the thing we most want to know, because the
thing we most want to know is specific to this work. The suite that would:

| Task | Shape | Scored against |
| --- | --- | --- |
| Edit-point audit | Pipe 3 unfamiliar React files, ask for every user-editable value | An answer verified by hand against the files |
| Component draft | A tight spec (exact DOM, exact class names, framework versions) | Does it parse? Does it invent APIs? |
| Adversarial review | "This route is public. What can an authenticated admin do to make it serve something it should not?" | Did it find the avatar-through-brand-logo case? |
| Structured extraction | 40 test files → every substring assertion where a line match is meant | A known-good list |

Each has **a recorded correct answer already**, because each was done and
verified. That makes them regression tests for models rather than for code.
Cheap to run, directly predictive, and they measure the failure mode that
actually costs us — confident invention — which no public benchmark scores.

**ADOPTED 2026-09-04: EvalPlus (HumanEval+/MBPP+), as a floor tier only.**
See [`floor/README.md`](floor/README.md). It **fails** the date check below —
HumanEval and MBPP are 2021, our models are 2026 — and was adopted anyway,
knowingly, because a contaminated benchmark is still valid *downward*:
memorisation can only inflate a score, so a low one is real evidence and a high
one is worthless. It supplies the two things our own suite cannot — an absolute
scale, and a way to baseline a brand-new model against published figures. It
may never rank our models. That is the difference between adopting it and
adopting LiveCodeBench, which was adopted for a job it could not do.

**Recommended order (amended):** the local suite is the primary
benchmark, not the first of two. LiveCodeBench is dropped — its data ends
before our models' training cutoffs. The catalogue stays a bibliography.

**Before adopting any benchmark from it, check one thing first:** what is the
newest question, and is it after the cutoff of the model you want to score?
Every option in this file except our own suite fails that check today. Our
suite passes it for a reason no public benchmark can copy: its tasks are
built on private codebases, so they are in nobody's training set.

---

# Second review — 2026-09-04

Five more candidates, assessed against three criteria the operator set: **more
than Python**, **newer**, and **contamination-free if possible**. Each was
checked rather than recalled, because the LiveCodeBench detour came from
trusting a recollection.

| Candidate | Beyond Python | Contamination | Verdict |
| --- | --- | --- | --- |
| **FuncBenchGen** | Function calling, not code-gen | **Free by construction** | **Adopt** |
| **MultiPL-E** | **18 languages** | Inherits HumanEval/MBPP (2021) | **Adopt into Floor** |
| Ag-LiveCodeBench-X | Yes (Lua, Julia, …) | Inherits LCB's 2025-04 ceiling | Wait |
| LiveCodeBench | Python | Data ends 2025-04-06 | Still blocked |
| HELM | Very broad | Public datasets | Framework, not a fit |
| BIG-bench | Broad | 2021–22, absorbed | Skip |

## FuncBenchGen — the one that is genuinely uncontaminated

It **generates** tasks by traversing a hidden function-dependency DAG with
controllable depth and distractors, rather than publishing a fixed set. That
makes it contamination-free *by construction* rather than by date — the only
durable form, because anything published eventually gets scraped. ICLR 2026,
and it interfaces through LiteLLM so an OpenAI-compatible endpoint works.

It measures **multi-step tool use**, which is a different axis from writing a
function and one nothing else here covers.

## MultiPL-E — the answer to "not just Python"

Translates HumanEval and MBPP into **18 further languages**. 1,129 commits, and
it ships a container with every toolchain.

**It is the same 2021 problems, so it is equally contaminated in principle** —
which is why it belongs in `floor/` under the same downward-only rule, not
beside the suite. One real nuance: a translation into a low-resource language
(Lua, Racket, OCaml) is *less* likely to have been memorised verbatim than the
Python original, simply because less of that code exists to train on. Weaker
contamination, not absent.

**The integration gap:** its runners target HuggingFace and vLLM models, not
API endpoints. That is the same wall `evalplus.codegen` presented, and the same
answer applies — take the dataset from the Hub and generate through our own
`llm` path, using their container only for execution.

## Why Ag-LiveCodeBench-X is a wait, not a no

Multi-language and agentic, from the same lab as MultiPL-E. But it is a variant
**of** LiveCodeBench, so it inherits that problem set and therefore the same
date ceiling that disqualified it: the newest problem predates our models'
training cutoffs. One commit on main. Revisit when it has its own problems or a
newer LCB release lands.

## HELM and BIG-bench

**HELM** is a framework rather than a benchmark — broad, actively maintained,
genuinely useful for holistic evaluation. Its scenarios are public datasets, so
it inherits their contamination, and it is heavy. If breadth beyond code is
ever wanted it is the right tool; it is not the right tool for "is this model
good at our work".

**BIG-bench** is 2021–22, superseded in practice by BIG-Bench Hard, and
thoroughly absorbed into training sets. Skip.

## The rule this review confirms

**Only generated benchmarks stay uncontaminated, and only private code stays
private.** Everything published is on a clock. That is why the tiers are
shaped as they are: `suite/` (our own repositories) and FuncBenchGen
(synthesised per run) are the two that can be trusted upward; everything else
is a floor.

---

# What already existed, and what this should have reused (2026-09-05)

Asked late whether an off-the-shelf tool would have done this. **Largely yes,
for one of the four tiers**, and the honest answer is that it should have been
checked at the start rather than after the code was written.

| What we built | What already does it |
| --- | --- |
| `suite/run.py` — run N models over N prompts, assert on output, compare | **promptfoo** — a CLI and library for exactly this, with a viewer |
| `metrics/cost.py` — token and cost accounting | **Langfuse**, **Helicone** — LLM observability with cost tracking |
| `floor/` — public code benchmarks | **EvalPlus** (which we did use), `lm-evaluation-harness` |
| `metrics/route.py` — pick a model per task | **RouteLLM**, **Not Diamond**, OpenRouter's own auto-router |

**`promptfoo` is the real overlap.** It would have supplied the runner, the
assertion mechanism, multi-model comparison and a results viewer — a good
share of `suite/run.py` and `serve.py`.

## What would not have come off a shelf

- **The tasks.** Drawn from private repositories with answers verified before
  any model saw them. No tool can supply those; that is the whole point of the
  tier and the reason it can rank when the public ones cannot.
- **The failure taxonomy.** `structural` / `entitlement` / `capacity` /
  `timeout`, each tied to a different ACTION — fix the registration, keep and
  watch, retry, wait. Generic harnesses record pass/fail, and every hard-won
  correction in this repo came from refusing to collapse those.
- **The benchmark-versus-offload distinction.** A general tool counts calls. It
  cannot know that 164 of them were a floor run rather than work, which is the
  difference between "most used" and "most measured".
- **Explore/exploit routing on staleness.** Commercial routers optimise cost
  and quality per call. None of them is trying to stop a local roster of models
  going stale through disuse.

## Should it be migrated

**Not now, and the reason is where the value sits.** It is in the tasks, the
verified answers and the recorded reasoning — not in the runner, which is the
part promptfoo would replace. Swapping it would re-implement the taxonomy and
the ledger inside somebody else's abstraction for no gain today.

**Revisit if the suite gets large — but as a COMPONENT, not a replacement.**
That is the sharper framing, and it is the one to act on. This repository is
not an eval runner; it is an integration of four things that are usually
separate products: evaluation, cost accounting, availability monitoring and
routing. The glue between them is where its value is, and no single tool sells
that glue.

So at forty or fifty tasks the move is not "migrate to promptfoo". It is
"let promptfoo be the runner inside `suite/`", keeping the failure taxonomy,
the offload ledger and the router above it. That swaps ~400 lines we maintain
for something better maintained, and gives up nothing that is actually ours.

The tasks are portable, which is what makes that possible whenever it becomes
worth doing.

**The lesson is the process, not the verdict.** The same check that now
precedes adopting a benchmark — *does this already exist, and does it fit* —
should have preceded building one.
