# The local suite

Public benchmarks measure competitive programming. **We do not offload
competitive programming.** We offload three shapes: read an unfamiliar file
and describe it, draft something against a tight spec, and review a surface
adversarially. This suite measures those.

Every task here has a **verified correct answer**, because every task here
actually happened — the answers are recorded in [`../runs/`](../runs/) and
were confirmed against the source or a live database before being used. That
makes them regression tests for models rather than for code.

## What it scores

Two things, and the second is the one that matters:

| | |
| --- | --- |
| **Recall** | Did it find what a careful reviewer found? |
| **Invention** | Did it assert something that is not true? |

**Invention is scored, not just noted.** The failure mode that costs us is not
a model missing something — it is a model confidently naming a file, an API or
a bug that does not exist, which then takes longer to disprove than the task
would have taken to do. A model that finds three of four real findings and
invents nothing beats one that finds four and invents two.

## Running it

```bash
python suite/run.py                      # every task, every configured model
python suite/run.py --task lifecycle     # one task
python suite/run.py --model gemini-flash-3.8
python suite/run.py --dry-run            # print the prompts, call nothing
```

Results append to `runs/suite-<date>.md` and print a table. Nothing is
overwritten — a run is evidence, and evidence accumulates.

## Claim verification, and tasks mined from real work

A third shape, added 2026-10-16: **is this claim about the code true?** The
step that stops a false finding being acted on is checking it, and models are
asked to do that constantly — so it is measured. A claim task attaches the code
exactly as it was when the claim was made, states the claim as a neutral
hypothesis (no model's name, no giveaway line number), and requires
`VERDICT: TRUE` or `VERDICT: FALSE` on the first line; `score_in: "first_line"`
scores only that line. True and false claims are kept roughly balanced, because
a model that always says FALSE should score half, not well.

Claim tasks have `"shape": "claim"` and stay **out of the routing recall**:
a true/false verdict scores half by guessing, and mixing it into finding-recall
would blur the number that picks the primary. They are reported per task.

The first eight (`claim-*`) are claims panels made about the bench's own code
on 2026-10-16, four true and four false — including a streaming hang that two
models agreed on and that does not exist.

**More come from use.** A verdict on a bench-gateway call can record verified
claims (`offload.py verdict <id> --real "..." --false "..."`), and
`python suite/mine.py <call id>` turns them into **draft** tasks, with the
files frozen from the gateway's log of what the models saw. Drafts are skipped
by normal runs until someone has read them and removed `"draft": true`.

## Seeded bugs

A fourth shape, added 2026-10-17: `python suite/seed.py --count N` takes real
functions from a private repository, changes ONE token in each (a comparison,
`and`/`or`, a returned True/False, a removed `not`, found with `tokenize` so
strings and comments are never touched and the author's formatting survives),
and writes the bug task plus a **control**: the same function untouched, where
the right answer is `NO FAULT`. Lines are numbered in the prompt; the first
line must be `FAULT: line <n>` or `NO FAULT`, scored by exact regex. The answer
key exists by construction, so variants are unlimited, and the control turns
"always names a line" into an invention score rather than a good one.

`--hard` gives the whole file (100-400 lines) and no hint at the kind of
change. Since 2026-10-17 there are ten kinds of change (`--kinds` picks; the
kind is drawn first, so rare kinds are not crowded out by comparisons):
the four token swaps above, plus a small number moved by one, two adjacent
arguments swapped, an off-by-one in `range()` or a slice, `break` <->
`continue`, a call statement deleted, and `+=` <-> `-=`. These six are found
with `ast` (ideas from mutmut's and cosmic-ray's operator lists) and still edit
one line in place, or delete one, so formatting gives nothing away. A deleted
line accepts the line before or after the gap. The bug task records its
`"kind"`. Function mode keeps the four token kinds unless told otherwise,
because its prompt names them; the prompt never varies per task, so it cannot
give a task's kind away.

Changes that rarely alter behaviour are not offered: code under
`if __name__ == "__main__":` or after a return/raise/break/continue,
keyword-argument and default values, `round()`'s digits, calls to
print/logging/sleep, and argument swaps for `max`/`min`. `python
suite/test_seed.py` checks every kind on a small inline source.

Generated as drafts, because a change can still leave behaviour unchanged;
each bug task's `why` shows the kind and the line before and after. Of the
first six new-kind drafts, two were dropped in review: `round(hi, 2)` ->
`round(hi, 3)` (display precision, not a bug) and `v[:1 - 1]` (a real bug, but
nobody writes `1 - 1`); the generator now avoids both. `"shape": "seeded"`
keeps them out of the routing recall for now, like claims.

A clause may be `{"regex": "...", "label": "..."}` where a substring cannot be
exact (`fault: line 1` is a substring of `fault: line 12`).

The scorer ignores a `must_not_claim` phrase where it is negated in its own
sentence ("there are no INSERT statements"), so ruling something out is never
counted as claiming it.

## Vision tasks

A fifth shape, added 2026-10-27: **reading images**. A task with `"shape":
"vision"` and `"images"` (files in its `fixtures/`) is sent with its pictures
as image parts, through the built-in client, to every model the catalog says
takes images; a vision model (role `vision`) gets only these, and a model that
cannot see never gets one (`run.applies`). The answer is `key: value` lines,
`not shown` for what the picture does not show, scored by
`suite/vision_keys.py`: a value for a field the picture cannot answer is an
invention, like a trap. They stay out of the routing recall and the code
reports; the Benchmarks page's Vision panel shows them field by field.

Three sources:

- **Made pictures** (`suite/vision_tasks.py`, needs Pillow to regenerate): a
  receipt, a grading label, a bar chart, a table, a settings screen, shapes
  and an invoice turned sideways, each key built from the data that drew it.
  Shipped in `suite/tasks/vision-*` and copied into the data folder on the
  next run (`run.seed_shipped`). Never seen in training; clean renders.
- **Public datasets** (`python suite/vision_sample.py --count 5`): photographed
  receipts (CORD v2), app screens with an unanswerable question (RICO-ScreenQA),
  tables (TableVQA-Bench) and absent-object questions (POPE), with the
  datasets' human answers. Written into the data folder only, so each install
  samples its own and nothing is redistributed.
- **Your own**: any task folder you write into the data folder's
  `suite/tasks/vision-*` in the same form, from photos whose answers you have
  checked. Real pictures of the real job, never in anyone's training data:
  the strongest of the three.

To hold them back for a while (a hub being changed, say), put the day they may
start in the data folder's `config/suite.json`: `{"vision_from": "2026-10-30"}`.
Until then no run picks a vision task; `--task <id>` still runs one.

## Adding a task

A task is one directory under `suite/tasks/` containing:

- `task.json` — the prompt, which fixture files to attach, and the scoring
- `fixtures/` — optional; usually the task points at real repo files instead

Scoring is keyword-based and deliberately crude. A rubric a model grades is a
rubric that model can be wrong about; a string that must appear either appears
or does not. Where a finding can be phrased several ways, `any_of` accepts
alternatives.

**Point the task at real files wherever possible.** A fixture is a copy, and
a copy goes stale while the thing it copied moves on.

### Designing a task that actually tests something

Our codebase is heavily commented on purpose, and that fights this suite. A
task like "what is subtle about `push_campaign`?" is answerable by quoting the
comment above it — that tests reading, not analysis, and every model passes.

Two shapes survive it:

- **Exhaustive enumeration.** "List every place X happens." Comments do not
  contain the list, so the model has to build it. `edit-points` and
  `satellite-writes` are this shape, and they are the most discriminating
  tasks here.
- **Adversarial hole-finding.** "Where does this guarantee break?" Comments
  assert that it holds, so quoting them is the wrong answer. `lifecycle` and
  `public-route` are this shape.

**Build the answer key by extraction, not from memory.** `satellite-writes`
was built by pulling every INSERT/UPDATE/DELETE out of the module with a
script and then reading each in context — which immediately caught two
regex false positives that would otherwise have become a wrong answer key:
one prose match inside a docstring, and two real SQL statements that write to
the application's *own* SQLite rather than to the satellite.

**A good distractor is a real one.** That SQLite/Postgres split became the
task's `must_not_claim`: the module genuinely talks to two databases, and a
model skimming for SQL will list writes from both. The prompt excludes them in
as many words, so naming one is an invention rather than a difference of
opinion — which is the distinction the scoring exists to draw.
