# Benchmarks

The first part of this file says how to read the dashboard's **Benchmarks**
page; each report has the same under **How to read this**. The rest, from
"Standard benchmarks" on, is the record of why the bench scores models on its
own suite rather than on public benchmarks (2026-09), kept as written. The
`floor/` tier and the other files it names are not part of this repository.

## The Benchmarks page

- **Who to trust with a review.** Each dot is a model: further right finds
  more of the real bugs in your own code, higher makes fewer things up. Top
  right is where a reviewer should be.
- **Bug hunt.** Every task with an answer key: on the bug tasks, the changed
  line found, another line named, or no bug said; on the same files left
  clean, false alarms. Ordered by found share minus false-alarm share.
- **Real work.** Of the offloads judged, the share recorded useful, with how
  many were judged. Benchmarks never count here.
- **Confidently wrong.** Answers that asserted something false, of the answers
  that could have: a clean control called faulty, a wrong line, a false claim
  confirmed, a planted trap taken, and the false claims recorded in verdicts.
- **Our code.** Every task on your own code: the findings each model located,
  of the total, and anything it invented. A timeout, refusal or error is not
  a score.
- **Vision.** Fields read right on pictures with a known answer, then real
  work on images and how much of it was checked.
- **Trend**, **Uptime** and **Decision models**, below.
- **Performance.** What the serving path does, not what the models are worth:
  medians and 95th percentiles over time, each probe of the last 14 days by
  what came back, and speed against quality on the suite. Weight it least
  ([perf/README.md](perf/README.md)).

Each report's **Share** menu copies or saves it as an image, or its table as
Markdown or CSV, or opens a post (USAGE.md, "Benchmarks, and sharing a
report").

### Trend

Who scores lower, or higher, than it used to: each model against its own
earlier scores on the same tasks, over the last 30 days (or 60).

- **A bar** is how far a model's scores in the window sit from its own scores
  before it, on the tasks scored in both. Each task counts as a share of its
  findings, so a one-finding claim and an eleven-finding file weigh the same,
  then the tasks are averaged. Left is lower, right is higher; the shaded band
  is 10 points either side. Hover for each task's move.
- **Lower** (or **higher**) when the scores moved 10 points or more on
  average, on at least two tasks and twice as many as moved the other way, in
  at least two separate runs. **Steady** otherwise: a steady bar past the band
  moved on average, but not on enough tasks or runs. **Too little to say**, with
  no bar, when fewer than three tasks can be compared, or there are fewer than
  two runs in the window or before it. One bad run, one task, or one earlier
  run to compare against is never a trend. Biggest move first in each group.
- **Beside the bar**, the evidence: the tasks compared and how many went down
  or up, then the runs in the window against the runs before it. A model with
  no task scored both in the window and before it is named under the chart.
- **Runs** are days a model was scored; a task asked twice in a day counts
  once, as the mean of its scores.
- **Why 30 days.** A paid model is scored about once a week and a free one
  about every 15 days, six tasks a run, so 7 days would hold one run at most.
  30 days holds two to four. A longer window holds more runs but leaves less
  before it to compare with.
- **What is counted**: the scores in the run notes (`runs/suite-*.md`), under
  each model's current name. Timeouts, errors and refusals are not scores and
  are left out; Uptime counts them. A task whose number of findings changed
  starts again. The answers behind the scores are kept from 2026-11-08 in
  `runs/answers/<task>/<model>/`; older ones were replaced by the next run.

### Uptime

Whether the provider was there when the bench called it, from the calls the
bench makes anyway: one cell a day, by provider and then by model, over the
last 7 days (or 30). At a glance has each model's 7 days too.

- **Down**: the provider was busy or overloaded (a 5xx, high demand, a shared
  pool's 429), unreachable (a broken stream, no reply), timed out (the
  gateway's 420 s included), or failed in a way no rule recognises yet. Those
  are listed under the chart, so a rule can be added (`uptime_class` in
  `suite/run.py`). The scheduler's `decisions` job asks a decision model about
  a few new shapes a day; one it was sure of (0.6 or more) counts as it said,
  marked "decided by" with the model and how sure.
- **Left out**, neither up nor down, since it says nothing about the provider:
  your own quota or allowance used up (a day, a month, credits), your own
  per-minute or concurrency limit, a call the bench's pacing never sent, the
  bench's own client failing, your LAN hub refusing the connection, and setup
  problems (sign-in, no such model, no access, a bad request of the bench's).
- **Up**: it answered. An empty reply still counts as an answer.
- **A cell** is one day: answered of the calls counted that day, coloured all
  answered, some down (80% or more answered), or under 80%; hollow when none
  were counted. Hover for the day, the count and the main reasons.
- **The %** is answered of counted over the days shown, weighted by calls, so
  a provider's row is every call to any of its models.
- **What is counted**: the daily probe, every gateway seat, the suite's own
  calls (from 2026-11-08, when the client's ledger began keeping their error
  text) and every decision model call, each on the connection behind its
  served name. Each call is classified from its text when the report is drawn,
  so a better rule changes the past too. It is shown only: nothing is routed
  or retired by it.

### Decision models

Models that answer typed questions (a choice, a yes or no) with a
probability, such as Mercury Decide, rather than chat. Onboarding records them
in `config/decision-models.json` with the question types they take. The bench
asks one for:

| Use | When |
| --- | --- |
| The shape of a request | per request, when its collection leaves the shape automatic and there are explorer seats to fill |
| A collection for a request that names none | per request (GATEWAY.md, "No default collection") |
| Whether a short reply is a provider's notice about the account | per gateway seat, when a short reply mentions credit, quota or a limit and no known phrase matched; once per shape of reply |
| The class of an error shape no uptime rule knows | the daily job, a few new shapes a day; cached |
| A new chat model's role | at onboarding; the model popup shows it, nothing applies it |

They are measured on three sets of questions with known answers, built from
the bench's own data with no hand labelling:

- **Error classes**: failed calls' error text, redacted (no keys, ids or
  emails), and the class the uptime rules give it; a choice of six.
- **Collections**: gateway requests and the collection the caller named (never
  one a decision model chose); a choice among the collections.
- **Useful answers**: seat answers you judged, with their request; yes or no.

Per model and set:

- **Right of asked**: each item's latest answer; a failed call counts as asked
  and not right. **Accuracy** is of the answers given; **F1** is the mean over
  the options of how well each was picked, so always saying the commonest
  option scores low.
- **Brier**: how far its probabilities sat from the truth. 0 is perfect; for
  a choice it is summed over the options, so up to 2. Lower is better, and a
  sure wrong answer costs most.
- **Calibration**: the chart groups answers by the probability the model gave
  (across) against how often they were right (up). On the diagonal, its 80%
  answers come true 80% of the time; under it, it is surer than it should be.
  The gap is the average distance from the diagonal, weighted by answers.
- **At 0.6**: the gateway acts on a choice only when the model is this sure;
  how many answers were, and how many of those were right.
- **Rank**: among models with 8 or more items asked on the set, by right
  answers smoothed for sample size, (right + 1) / (asked + 2). Below 8 a model
  is shown but not ranked.
- **Failures**: calls that came back with no answer, by the uptime classes.
  A pass stops a model at its first failure.

**Who each use asks** is listed on the report, with why: the best-ranked model
on its set (the shape of a request and a new model's role on Collections, the
notice check on Useful answers, the others on their own), or, until one is
ranked, the first onboarded model that takes the question. A use asked per
request never goes to an OpenRouter `:free` model while another can answer:
those calls come out of OpenRouter's free daily budget, which the suite and the
probe share.

The scheduler's `decisions` job asks each model one question per set a day,
the ones asked longest ago first, 30 seconds apart; `python metrics/decide.py
bench` runs a pass by hand. Every decision call, the gateway's included, is a
line in `metrics/decisions.jsonl` (the model, the use, the answers and their
probabilities, seconds, any error redacted), never the question's text.

---

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
