# Using Benchwarmers

Day-to-day use, once it runs: the dashboard's pages, onboarding models,
collections, connecting your apps and recording verdicts. Setting it up is in
the README's [Try it](README.md#try-it) (or [AGENTS.md](AGENTS.md), for a
coding agent doing it with you); the gateway's whole contract is in
[GATEWAY.md](GATEWAY.md).

## Contents

- [The dashboard](#the-dashboard)
- [Onboarding models](#onboarding-models)
- [Collections](#collections)
- [Connecting an app](#connecting-an-app)
- [Recording verdicts](#recording-verdicts)
- [Discover: finding models to add](#discover-finding-models-to-add)
- [The scheduler](#the-scheduler)
- [Your data folder](#your-data-folder)

## The dashboard

`python serve.py`, then http://localhost:8122. The sidebar groups its pages:

| Group | Page | What it is for |
| --- | --- | --- |
| Observe | **At a glance** | Every model on the bench: whether it works right now, its role, and its connection. Click a name for its popup: role, alias, facts, options, retire. |
| | **Benchmarks** | How models do on the suite and on real work: bugs found against false alarms, who is confidently wrong, and speed. |
| | **Quota management** | What limits each connection, and which models are resting after hitting one. |
| | **Logs** | Gateway calls (who sat and how it went, never the prompt), changes made from the dashboard, the scheduler, and onboarding runs. |
| Gateway | **Endpoints** | Where models are served from. Add an endpoint (a provider's sign-in, or any OpenAI-compatible API key), change a key, and see what each one serves. Also your own app registrations and rate limits per connection. |
| | **Models** | Every model on the bench, by the connection it comes from. |
| | **Collections** | The named panels your apps ask for, and the gateway's settings. |
| | **Discover** | Models worth adding, from public catalogs, with the ways this install can reach each one. |
| Control | **Settings** | Theme, gateway keys, the scheduler, the model client, providers whose free models work only in their own app, and how the install is reached (shown only). |
| | **Info** | Version, Python, data folder and counts. |

"Action needed", above the pages, lists what will not fix itself, such as a
model about to be retired, with Retry, Open and Retire now beside it. Hide
folds it away until something new turns up.

## Onboarding models

**Onboard a model**, top right, lists everything your endpoints serve that is
not on the bench yet. Search, and tick one or several: the ticks survive a new
search, so you can gather models from different connections in one go.
Dismiss hides one you never want offered (an image model, say); the dismissed
list brings it back.

Each model is registered, probed with four short calls, and, if it answers,
run through the suite. That takes minutes and spends calls, so they run **one
at a time**, in a queue the dashboard keeps:

- One model with nothing ahead of it is followed step by step in the popup.
- Otherwise the popup shows the queue, and a chip beside the button shows the
  progress ("Onboarding 2 of 5: ..."), filling as models finish. It turns green
  when every model is on the bench, and red when one failed or did not answer.
- Closing the popup or reloading the page stops nothing. **Details** reopens
  the queue: each model's state, its alias, why it stopped, and its log.
- **Stop** lets the model running now finish and skips the rest. **Clear**
  forgets a finished batch's results; the logs stay in `runs/`.

A model that registered but did not answer stays on the bench as
unavailable, so the daily probe keeps watching it.

## Collections

A collection is a named panel: the models it always includes, the shape of
work it is for (review, design, enumeration, ...), and how its reply comes
back. On the **Collections** page, **New collection**, then pick the members.
Each call also seats explorers, the models unused longest, up to the gateway's
limits (Gateway settings, on the same page: how many calls a request may make,
and how many of them explore).

## Connecting an app

Any OpenAI-compatible client works. Point it at the dashboard's port and ask
for the model `bench/<collection>`:

| Setting | Value |
| --- | --- |
| Base URL | `http://127.0.0.1:8122/v1` (or `gateway.py` alone: `http://127.0.0.1:8123/v1`) |
| API key | any text on this machine; a **gateway key** from Settings when keys are required |
| Model | `bench/<collection>`, or `bench/<collection>:merged` for one merged answer |

With the OpenAI Python SDK:

```python
from openai import OpenAI

bench = OpenAI(base_url="http://127.0.0.1:8122/v1", api_key="local")
reply = bench.chat.completions.create(
    model="bench/<collection>",
    messages=[{"role": "user", "content": "Where can this function return a stale value? ..."}],
)
print(reply.choices[0].message.content)   # every seat's answer, labelled
```

In a chat app such as Open WebUI, add the base URL as an OpenAI connection;
the collections appear as models. For a chat window, the `:merged` form reads
best: a merger model writes one answer saying where the seats agreed and
disagreed, and every seat is still in the reply's `bench` field.

Images go in as OpenAI `image_url` parts; only models that take images sit,
and the rest are listed in `bench.skipped`. [GATEWAY.md](GATEWAY.md) has the
limits and the reply's full shape.

## Recording verdicts

A panel's answers are worth something only once someone has checked them
against the real work. Every seat has a call id; record a verdict per call on
the Collections page (the Gateway card lists recent requests: click a seat),
or:

    curl http://127.0.0.1:8122/bench/verdict -H "Content-Type: application/json" \
      -d '{"call": "<call id>", "useful": true, "note": "found the real bug", "real": ["..."], "false": ["..."]}'

`real` and `false` list the specific claims you verified either way. False
claims feed **Confidently wrong** on Benchmarks, and verdicts together decide
who sits on the next panel. Agreement between models is not evidence: they
are often wrong together.

## Discover: finding models to add

**Discover** merges models.dev and FreeLLMAPI into one list, free models
first, with what is new this week badged. A row opens its popup: what the
model takes and does, its price, and every route this install has to it, each
with an **Add via this**. Adding through a hub (Open WebUI, LiteLLM, CLI Proxy
API) puts it in that hub too; adding direct registers it on its own endpoint
only. Either way the onboarding joins the same queue.

Some providers' free models work only inside their own app; Discover leaves
those out and keeps their paid models. Settings, **Free only in their own
app**, switches a provider either way or adds one.

## The scheduler

The recurring jobs (the morning availability probe, the catalog refresh, the
deprecation check and auto-retire, suite passes, the weekly speed and
capability tests) run inside the dashboard. Settings, **Scheduler**:

- **Off**: nothing runs; a fresh install spends no calls until you opt in.
- **Log only**: writes "would run" to `runs/scheduler.log` at each due time,
  to check the times before anything spends calls.
- **Run**: runs each job at its time, one at a time per job.

If something else already runs these jobs (cron, Windows Task Scheduler),
turn it off in the same step, or every job runs twice.

## Your data folder

Everything that is yours (endpoints, registrations, collections, verdicts,
call logs, probe history, task answers) lives in the data folder: the one
`BENCHWARMERS_HOME` names, else the one in `.benchwarmers-home` at the top of
the checkout. Back it up by copying it; move it by copying it and pointing one
of those two at the new place. Info shows which folder is in use.
