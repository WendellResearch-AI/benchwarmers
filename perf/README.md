# The performance tier

What the **serving path** does, tracked over time. Not what the models are
worth — that is `suite/`.

Latency through Open WebUI to whichever provider is behind it depends on
congestion, rate limits, routing and the hour of the day. None of those are
properties of a model, and all of them decide whether an offload is worth
making right now.

## One reading is weather; the history is climate

Every run appends to `history.csv` rather than overwriting a report. A single
number here is close to meaningless — the first run recorded a p95 of 44.5 s
against a p50 of 11.6 s on the *same* prompt, entirely from one cold first
call. A p50 that doubles over a month is a real finding. A p50 that is 40%
higher this afternoon is a Tuesday.

The runner prints a `(+N% vs <date>)` marker against the previous reading, and
says `(steady)` inside ±15%, so the comparison is made for you rather than left
in a spreadsheet.

## What is recorded, and why each

| Column | Why |
| --- | --- |
| `p50_s`, `p95_s` | **Not the mean.** One timeout drags a mean into fiction. p95 is where rate limiting shows itself, and rate limiting is what makes a model unusable while looking fine on average |
| `chars_per_s` | Throughput, from the **large prompt only** |
| `answered` / `refused` / `error` / `timeout` | Four columns, never one. A safety filter, an overloaded service and a slow model are three different facts, and collapsing them is the mistake this repo was built around |
| `timestamp` | The whole point is the trend |

**Two prompt sizes, because they measure different things.** The tiny prompt
asks for one word and measures the FLOOR — connection, queueing, cold start.
The large one is 18 KB and measures THROUGHPUT. The first run made the case
better than the design did: the 18 KB prompt came back in 9.0 s and the
one-word prompt took 11.6 s. Latency to these models is overhead, not
generation.

**Throughput is not recorded for the tiny prompt.** Six characters over ten
seconds is 0.5 ch/s, which reads as a catastrophically slow model and is
really connection overhead with a one-word answer attached. A number that
invites the wrong reading is worse than no number.

**Characters, not tokens.** The `llm` CLI does not report token counts for
these endpoints. Chars/s is a proxy, it is affected by how verbose a model is,
and saying so is better than publishing something that looks like tokens/s and
is not.

## Running it

```bash
# All default models, both prompt sizes, 5 samples each.
.venv/Scripts/python.exe perf/run.py

# One model, quick availability check.
.venv/Scripts/python.exe perf/run.py --model gemini-flash-3.8 -n 3 --tiny-only
```

Worth running on a schedule — weekly is enough to see drift — and again
whenever a model starts feeling slow, so the feeling can be checked against a
number.

## One caveat: not every model takes the same path

`inkling-free` and `inkling-small-free` are called **directly at OpenRouter**,
because Open WebUI cannot relay them (see `runs/2026-09-04-registry-second-pass.md`).
Everything else goes through Open WebUI first.

That is one fewer hop, and it shows: `inkling-small-free` measured **245 ch/s**
against 88.7 for the next fastest. **Part of that gap is the path, not the
model** — which is exactly what this tier is named for. Do not read it as a
threefold quality difference; read it as the relay costing something.

The right comparison for these two is against each other, or against their own
history over time.

## How much this should count

**Least of the three tiers.** `suite/` decides which model to use, `floor/`
says whether one is broken, and this is a tiebreaker plus a veto for the
extreme cases. Differences of seconds are a wash — waiting is cheap and a
confident wrong answer is not. Speed only overrules capability when a model is
slow enough to change how you work.

## What this cannot tell you

Whether a model is any good. The fastest model here can still invent an API
that does not exist. That is what `suite/` is for, and the two should never be
read as one ranking.
