"""Who to ask, and who to ask as well — a panel, not a winner.

    python metrics/route.py                       # today's panel (shape: general)
    python metrics/route.py --shape review        # code review: code models eligible too
    python metrics/route.py --shape design        # ADR / process / prose: general models only

A MODEL HAS A ROLE, A TASK HAS A SHAPE
--------------------------------------
Not every model is the same kind of thing. `codestral` and `north-mini-code`
are code-completion models: sharp on "list every place X happens" and "is
this function wrong", and they answer a design or process question with
boilerplate — one gave six generic findings on a packet of ADR text and none
survived. `inkling` and `kimi` are reasoning models that can do all of it.
Seating them identically wastes the explorer's call and, worse, records a
miss against a model that was never the right tool.

So `config/model-roles.json` gives each alias a ROLE (general, code, image)
and each task SHAPE a list of roles it accepts. `--shape` picks the shape;
with none given the task is `general`, which admits general models only.
Pass `--shape review`, `code` or `enumeration` and the code models are back
in the pool, explorers included. A role is not a score and does not exile
anyone: a code model is still explored, on code-shaped work. `--with` seats
anything regardless, with a note, because an explicit request outranks a
heuristic.

CONNECTIONS ARE NOT EVENLY STOCKED
----------------------------------
Six carry registered models and the distribution is lopsided: nvidia-api and
openrouter hold 17 of the 23 available between them, while groq has one and
mistral one usable of four. So only the PRIMARY and SECOND are required to sit
on different connections — that pair is the backup relationship, and a backup
behind the same rate limit is not one. The explorers may share, because
forcing a fourth distinct connection would push both probes onto the thin ones
and under-explore exactly where most models live.

`inkling-free` is tagged `openrouter` and that is correct for this purpose. It
is registered on the direct `openrouter` endpoint (config/endpoints.json), so it
is independent in TRANSPORT — an Open WebUI outage would not touch it — but it
uses the same OpenRouter key and shares `free-models-per-day` with every other
`:free` model. Attribution opens the agentic gate and does not lift a rate
limit, which was tested. Shared quota is what matters here, so it counts as
openrouter.

WHY A PANEL AND NOT A PICK
--------------------------
These models are free or flat-rate, so asking three costs about what asking
one costs. The binding constraint is not money, it is that **a second opinion
from a model that fails the same way is not a second opinion.** So the panel is
chosen for INDEPENDENCE: the second seat goes to a different connection from
the first, because models behind one proxy share a rate limit, an outage and
often a vendor.

THE FEEDBACK LOOP THIS EXISTS TO BREAK
--------------------------------------
A router that only picks proven models is self-reinforcing and quietly wrong: a
model that stops being asked stops producing evidence, so its record never
improves and it is never asked again. It gets phased out by SILENCE rather than
by being bad. Nothing in the data would show this happening — the unused model
simply has no rows.

So the last TWO seats are EXPLORERS, chosen by staleness rather than by
score: whichever available models have gone longest without real work. They
are structural slots, not good intentions, and they cost two extra free calls.

Two rather than one because coverage is the constraint — 17 of 23 available
models had never been given real work. And an explorer is not a wasted seat:
it answers the same question as everyone else, so it is a fourth opinion as
well as a probe.

WHAT DISQUALIFIES A MODEL
-------------------------
Availability, and fit. `structural` (it is not there) and `entitlement` (this
account may not call it) remove a model; a prompt it cannot read removes it
for that prompt; a role the task's shape does not accept removes it for that
task. A poor score never does. A model that did badly once on one task shape
has earned less confidence, not exile.

Cost, separately. A model listed under `ad_hoc` in config/model-roles.json is
expensive or tightly capped and is never seated automatically: `--with` is the
only way onto this router's panel. Staleness would otherwise pick it first,
since it is exactly the model nobody uses casually. A collection that lists it
as a member (or merger) still calls it on every request; that is a choice made
by name, like `--with`.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
sys.path.insert(0, str(ROOT / "suite"))
sys.path.insert(0, str(ROOT / "metrics"))

from aliases import current as current_alias  # noqa: E402


def _json(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def connection_of(alias: str) -> str:
    """The connection a model is reached through, trimmed to the host (what
    shares a rate limit): see full_tag_of."""
    return full_tag_of(alias).split("/")[0]


# Connections already PAID for, so a spare call there costs nothing extra:
# `preferred` in config/connections.json. Used as a TIEBREAK only — never to
# inflate a score, because the evidence is the evidence. When two models are
# within noise of each other, the one already paid for wins.
def preferred_connections() -> set[str]:
    import endpoints
    return set(endpoints.connections().get("preferred") or ())


def full_tag_of(alias: str) -> str:
    """The connection tag INCLUDING the vendor suffix.

    `connection_of` trims to the host because that is what shares a rate
    limit; this keeps `<proxy>/<vendor>` whole, because that is what a
    subscription is bought for.

    Its Open WebUI tag (scripts/provider_tags.py), else the endpoint it is
    registered on: a model on its own endpoint, or any model on an install
    with no Open WebUI to tag anything, needs no tags file. Never a hub's
    name, else "?": a hub fronts many connections, and an untagged model
    behind it (Open WebUI stopped listing it) is not on a connection called
    `openwebui` with every other untagged one.
    """
    from run import _registered_names
    import endpoints
    # Tags are keyed by the served name. A decision model is not in the llm
    # yaml, so its served name comes from config/decision-models.json.
    served = (_registered_names().get(alias)
              or (_json(DATA / "config" / "decision-models.json").get(alias) or {}).get("served")
              or alias)
    tag = _json(DATA / "config" / "provider-tags.json").get(served)
    if not tag:
        name = endpoints.endpoint_name(endpoints.registrations().get(alias) or {})
        tag = name if name and not endpoints.hub(name) else None
    return tag or "?"


def family_of(alias: str) -> str:
    """The underlying MODEL, ignoring which connection serves it.

    The same model can arrive on two connections — `Inkling` on llm7-api is
    `thinkingmachines/inkling:free` on openrouter — and everything else in
    this repo treats them as separate, correctly: they have different rate
    limits, different quotas and can behave differently (quantisation, token
    caps, injected prompts), so scoring and availability must stay apart.

    Routing is the exception. Seating one model in two chairs looks like a
    second opinion and is not one: it will agree with itself for reasons that
    have nothing to do with being right. A panel's whole value is independent
    failure.

    Normalised by dropping the vendor prefix, the `:free` suffix, case and
    separators — so `thinkingmachines/inkling:free` and `Inkling` collide,
    while `glm-5.2` and `glm-5.3` do not, because those really are different
    models.
    """
    from run import _registered_names
    served = _registered_names().get(alias, alias)
    base = served.split("/")[-1].split(":")[0]
    return base.lower().replace("-", "").replace("_", "").replace(".", "")


def _roles() -> dict:
    import model_roles                             # roles and shapes shipped, the install's on top
    return model_roles.load()


def role_of(alias: str) -> str:
    """What the model is for: general, code or image. Unlisted means general."""
    return _roles().get("models", {}).get(alias, "general")


def roles_for_shape(shape: str | None) -> list[str]:
    """The roles a task shape admits. Unknown shapes are refused rather than
    silently treated as general — a typo would otherwise widen or narrow the
    panel with no sign that it had. ValueError, not SystemExit: the gateway
    calls this from a request thread, which SystemExit killed without a
    reply; it answers a ValueError with a 400."""
    shapes = _roles().get("shapes", {"general": ["general"]})
    shape = shape or "general"
    if shape not in shapes:
        raise ValueError(f"unknown shape {shape!r}; known: {', '.join(sorted(shapes))}")
    return list(shapes[shape])


def size_capability() -> dict[str, bool]:
    """alias -> can it take a LARGE prompt, judged from the perf history.

    Derived, not declared. The perf tier already measures two prompt sizes, so
    a model that answers "ready" and fails an 18 KB summary has said what its
    limit is without anyone writing it down: `qwen3.8-27b` refuses at 7,000
    tokens a minute and `laguna-s-2.1-free` fails the same way.

    That matters because most of our real tasks are large — `satellite-writes`
    is 46 KB — so recommending a model that cannot read the prompt wastes a
    seat on the panel.
    """
    path = DATA / "perf" / "history.csv"
    if not path.exists():
        return {}
    import csv as _csv
    latest: dict[tuple[str, str], dict] = {}
    with path.open(encoding="utf-8", newline="") as fh:
        for row in _csv.DictReader(fh):
            latest[(current_alias(row["model"]), row["prompt"])] = row
    out: dict[str, bool] = {}
    for (model, prompt), row in latest.items():
        if prompt != "large":
            continue
        out[model] = int(row.get("answered") or 0) > 0
    return out


def suite_recall() -> dict[str, tuple[int, int, int]]:
    """alias -> (found, of, tasks), scored from the SAVED OUTPUTS.

    Not from the run notes. Parsing those inherits every misclassification ever
    written down: `gpt-oss-120b-cloud` was recorded 0/5, 0/3, 0/2 and 0/11 in a
    run where it was misregistered and returning "Model not found" in four
    seconds, and this router read those as a capability score — the exact
    conflation the rest of this repo exists to prevent.

    The outputs are the data and the notes are the narrative. Re-scoring the
    outputs applies today's classifier to the original answers, so a fix to the
    scorer retroactively corrects the routing rather than leaving a wrong
    number fossilised in a markdown table.
    """
    import json as _json
    from run import registered_models, score

    live = set(registered_models())
    out: dict[str, list[int]] = {}
    for task_dir in sorted((DATA / "suite" / "tasks").glob("*/task.json")):
        spec = _json.loads(task_dir.read_text(encoding="utf-8"))
        # Claim-verification tasks are a true/false verdict, where a guess
        # scores half: folding them into finding-recall would blur the one
        # number that picks the primary. They are reported on their own, as
        # are seeded and real-bug (rebench) FAULT/NO FAULT tasks.
        if spec.get("shape") in ("claim", "seeded", "rebench") or spec.get("draft"):
            continue
        for answer in sorted((task_dir.parent / "output").glob("*.md")):
            alias = current_alias(answer.stem)      # a renamed model keeps its record
            if alias not in live:
                continue
            result = score(spec, answer.read_text(encoding="utf-8",
                                                  errors="replace"))
            # A model that never answered has no score, in either direction.
            if result["kind"] != "answered":
                continue
            acc = out.setdefault(alias, [0, 0, 0])
            acc[0] += result["found"]
            acc[1] += result["of"]
            acc[2] += 1
    return {m: tuple(v) for m, v in out.items()}


def panel(shape: str | None = None, large: bool = True,
          nominated: list[str] | None = None,
          members: list[str] | None = None, explorers: int = 2,
          avoid: set[str] | None = None) -> dict:
    """`large=True` means the task is a big one — most of ours are.

    `members` is a bench-gateway COLLECTION: models the caller always runs. With
    it, the primary and second seats are not chosen (the collection is the
    exploitation), and only `explorers` (0-2) seats are filled by staleness,
    never with a member or a member's family. `avoid` keeps models out of
    those automatic picks (the gateway passes the ones on a retiring alert, so
    a seat is not spent on a model already known to be failing); a nomination
    still seats one.

    `nominated` names models the operator wants on the panel today, and they
    take EXPLORER seats — never the primary or the second.

    That placement is the whole design. The first two seats are the
    independence pair: the primary is exploitation and the second is the
    backup that must not fail the same way, so a hand-picked model displacing
    either would quietly remove the guarantee those seats exist to give. An
    explorer seat is already "a model we have little evidence about, seated to
    get some" — which is exactly what wanting to try something new IS. So a
    nomination is not a new kind of seat, it is the same seat filled by
    judgement instead of by staleness, and no fifth chair is needed.

    Only ONE of the two explorer seats is taken per nomination, and the
    remainder still go to the stalest available models. That matters: if
    nominating switched off automatic exploration entirely, a habit of
    nominating a favourite would rebuild the exact feedback loop the explorer
    slots were built to break, and it would do it invisibly.
    """
    from run import ad_hoc_models, available_models
    from offload import summary

    # AD HOC models get a record but no automatic seat — not primary, second,
    # explorer or reserve. They are the expensive or capped ones, and an
    # explorer seat chosen by staleness would pick exactly them, since nobody
    # uses them casually. `--with` is the only way onto the panel.
    avail = available_models(include_ad_hoc=True)
    held_ad_hoc = sorted(ad_hoc_models() & set(avail))
    recall = suite_recall()
    used = summary()
    sizes = size_capability()
    today = dt.date.today()

    # The record for EVERY available model, built before any exclusion, so a
    # model seated by nomination over an exclusion still shows its real
    # evidence rather than a blank.
    record: dict[str, dict] = {}
    for alias in avail:
        found, of, tasks = recall.get(alias, (0, 0, 0))
        u = used.get(alias, {})
        last = u.get("last_date")
        stale = ((today - dt.date.fromisoformat(last)).days
                 if last else 9999)          # never used: maximally stale
        record[alias] = {
            "model": alias,
            "connection": connection_of(alias),
            "full_tag": full_tag_of(alias),
            "family": family_of(alias),
            "found": found, "of": of, "tasks": tasks,
            "recall": found / of if of else None,
            "offloads": u.get("offloads", 0),
            "helped": u.get("useful", 0), "judged": u.get("judged", 0),
            "stale_days": stale,
        }
    avail = [m for m in avail if m not in held_ad_hoc]

    # A MODEL THAT CANNOT READ THE PROMPT IS A WASTED SEAT. Judged from the
    # perf tier's two prompt sizes rather than from a hardcoded table.
    excluded_for_size = []
    if large:
        excluded_for_size = [m for m in avail if sizes.get(m) is False]
        avail = [m for m in avail if sizes.get(m) is not False]

    # A MODEL THAT IS NOT FOR THIS KIND OF TASK IS A WASTED SEAT TOO — and a
    # miss recorded against it teaches the roster nothing true. Declared in
    # config/model-roles.json, chosen by --shape; general is the default.
    admitted = roles_for_shape(shape)
    excluded_for_role = [m for m in avail if role_of(m) not in admitted]
    avail = [m for m in avail if role_of(m) in admitted]

    rows = [record[alias] for alias in avail]

    # PAID connections break ties and nothing more. The evidence is the
    # evidence; a subscription only decides who wins when two models are level.
    preferred = preferred_connections()

    def paid(r):
        return 1 if r["full_tag"] in preferred else 0

    # PRIMARY: most verified findings on our own code. Absolute rather than a
    # ratio, so a perfect score on one task does not outrank near-perfect on
    # five.
    scored = [r for r in rows if r["of"]]
    primary = max(scored, key=lambda r: (r["found"], r["recall"] or 0, paid(r)),
                  default=None)

    # SECOND OPINION: rotated among CONTENDERS, not simply the runner-up.
    # Taking the runner-up had the same feedback loop as taking the leader, one
    # rank down — one model would hold the seat for ever while its evidence
    # went stale. Only the PRIMARY is fixed; that seat is exploitation.
    #
    # NO CONNECTION CONSTRAINT ANY MORE. Requiring a different connection was
    # a preventive proxy for "do not both fail at once", and it distorted the
    # choice: nvidia-api and openrouter hold 17 of 23 available models, and
    # most new models arrive on nvidia, so excluding a whole connection to
    # satisfy a rule meant systematically under-exercising the busiest one.
    # The RESERVE below handles the real failure reactively instead.
    contenders = [
        r for r in scored
        if primary and r["model"] != primary["model"]
        and r["family"] != primary["family"]     # not the same model twice
        and r["tasks"] >= 2                      # one task is not a record
        and (r["recall"] or 0) >= 0.85 * (primary["recall"] or 0)
    ]
    second = max(contenders,
                 key=lambda r: (r["stale_days"], paid(r), r["found"]),
                 default=None)
    if second is None:
        second = max((r for r in scored
                      if primary and r["model"] != primary["model"]),
                     key=lambda r: (r["found"], paid(r)), default=None)

    # TWO EXPLORERS: longest since real work, score ignored. The slot that
    # stops the router eating its own tail. Two because coverage is the
    # constraint, and an explorer answers the same question as everyone else —
    # a fourth opinion as well as a probe.
    if members is not None:
        primary = second = None
    chosen = {r["model"] for r in (primary, second) if r} | set(members or [])
    # Families already seated. The same model on a second connection is not a
    # second opinion, however different its alias looks.
    seated_families = ({r["family"] for r in (primary, second) if r}
                       | {family_of(m) for m in members or []})

    def stalest():
        pool = [r for r in rows if r["model"] not in chosen
                and r["family"] not in seated_families and r["model"] not in (avoid or ())]
        return max(pool, key=lambda r: (r["stale_days"], paid(r),
                                        -(r["recall"] or 0)), default=None)

    # Nominations are seated FIRST, so staleness fills whatever explorer seats
    # are left rather than being overwritten after the fact.
    by_alias = {r["model"]: r for r in rows}
    seats: list[dict | None] = []
    notes: list[str] = []
    explorers = max(0, min(2, explorers))
    for alias in (nominated or [])[:explorers]:
        row = by_alias.get(alias)
        if row is None and alias in held_ad_hoc:
            # Exactly what ad hoc is for: named, so seated. Said anyway,
            # because the call spends a cap the rest of the panel does not.
            notes.append(f"{alias} is ad hoc (expensive or capped) — seated "
                         "because you asked; one call, not a retry loop")
            row = record[alias]
        elif row is None:
            if alias in excluded_for_size or alias in excluded_for_role:
                # Honoured anyway: an explicit request outranks a heuristic,
                # and the operator is the one who knows how big today's prompt
                # is and what it is really about. Said out loud, because a
                # seat that cannot read the prompt, or is the wrong kind of
                # model for it, is a wasted seat.
                if alias in excluded_for_size:
                    notes.append(
                        f"{alias} is recorded as failing on LARGE prompts — seated"
                        " because you asked; pass --small if this one is short")
                else:
                    notes.append(
                        f"{alias} is a {role_of(alias)} model and this is a"
                        f" {shape or 'general'} task — seated because you asked;"
                        " judge its answer with that in mind")
                row = record[alias]
            else:
                notes.append(f"{alias} is not available — check the alias "
                             f"against `llm models list`; seat left to staleness")
                continue
        if row["model"] in chosen:
            notes.append(f"{alias} is already seated as primary or second "
                         f"opinion; no explorer seat used")
            continue
        row = dict(row, nominated=True)
        seats.append(row)
        chosen.add(row["model"])
        seated_families.add(row["family"])

    while len(seats) < explorers:
        pick = stalest()
        if pick is None:
            seats.append(None)
            continue
        chosen.add(pick["model"])
        seated_families.add(pick["family"])
        seats.append(pick)
    seats += [None] * (2 - len(seats))

    explorer, explorer2 = seats[0], seats[1]

    # RESERVE: the fallback when the panel meets a rate limit.
    #
    # This replaces the hard rule that seats must sit on different
    # connections. That rule was preventive and blunt — it distorted every
    # pick to guard against something that might not happen. A reserve is
    # reactive and cheap: one named substitute on a connection NOTHING on the
    # panel is using, so whatever limit takes out a seat cannot take out its
    # replacement too.
    used_connections = {r["connection"] for r in
                        (primary, second, explorer, explorer2) if r}
    reserve = max(
        (r for r in scored
         if r["model"] not in chosen and r["family"] not in seated_families
         and r["connection"] not in used_connections),
        key=lambda r: (r["found"], paid(r)), default=None)
    if reserve is None:                            # every connection in use
        reserve = max((r for r in scored if r["model"] not in chosen),
                      key=lambda r: (r["found"], paid(r)), default=None)

    return {"primary": primary, "second": second, "explorer": explorer,
            "explorer2": explorer2, "reserve": reserve,
            "considered": len(rows),
            "untried": sum(1 for r in rows if not r["offloads"]),
            "contenders": len(contenders),
            "excluded_for_size": excluded_for_size,
            "excluded_for_role": excluded_for_role,
            "held_ad_hoc": held_ad_hoc,
            "shape": shape or "general",
            "notes": notes}


def describe(r: dict | None, role: str) -> str:
    if r is None:
        return f"  {role:<14} —"
    ev = (f"{r['found']}/{r['of']} over {r['tasks']} task"
          f"{'s' if r['tasks'] != 1 else ''}" if r["of"] else "no suite score")
    work = (f"{r['offloads']} offload{'s' if r['offloads'] != 1 else ''}"
            if r["offloads"] else "never used for real work")
    # A nominated seat is labelled, because the record has to distinguish
    # evidence the router went looking for from evidence the operator asked
    # for. They are not the same claim about a model.
    if r.get("nominated"):
        role = f"{role} (asked)"
    return (f"  {role:<14} {r['model']:<28} {r['connection']:<14} {ev}; {work}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shape", default=None,
                    help="what today's task is: general (default), design, prose,"
                         " review, code, enumeration, image. Decides which model"
                         " ROLES are eligible — see config/model-roles.json")
    ap.add_argument("--small", action="store_true",
                    help="the task is a SHORT prompt, so models that fail on "
                         "large ones are eligible again")
    ap.add_argument("--with", dest="nominated", action="append", metavar="ALIAS",
                    help="seat this model today; takes an EXPLORER seat, never "
                         "the primary or second. Repeatable up to twice, but "
                         "leaving one for staleness is the point.")
    args = ap.parse_args()

    try:
        p = panel(args.shape, large=not args.small, nominated=args.nominated)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    print(f"Panel of four, from {p['considered']} available models"
          f" (task shape: {p['shape']}):\n")
    print(describe(p["primary"], "primary"))
    print(describe(p["second"], "second opinion"))
    if p.get("contenders"):
        print(f"                 (rotating among {p['contenders']} contender"
              f"{'s' if p['contenders'] != 1 else ''} — only the primary is fixed)")
    print(describe(p["explorer"], "explorer"))
    print(describe(p["explorer2"], "explorer 2"))
    print(describe(p["reserve"], "reserve"))
    for note in p.get("notes", []):
        print(f"\n  note: {note}")
    if p["excluded_for_size"]:
        print(f"\n  excluded (fail on large prompts): "
              f"{', '.join(sorted(p['excluded_for_size']))}")
        print("  — pass --small if this task is a short one.")
    if p["excluded_for_role"]:
        by_role: dict[str, list[str]] = {}
        for m in p["excluded_for_role"]:
            by_role.setdefault(role_of(m), []).append(m)
        for role, models in sorted(by_role.items()):
            print(f"\n  not seated ({role} models, task is {p['shape']}): "
                  f"{', '.join(sorted(models))}")
        if "code" in by_role:
            print("  — pass --shape review, code or enumeration to make them"
                  " eligible; --with seats one anyway.")
    if p["held_ad_hoc"]:
        print(f"\n  held back (ad hoc — never seated automatically): "
              f"{', '.join(p['held_ad_hoc'])}")
        print("  — --with <alias> seats one in an explorer chair.")
    print(f"\n{p['untried']} of {p['considered']} available models have never "
          f"been given real work.")
    print("\nThe second seat is on a different connection on purpose — models "
          "behind one proxy\nshare a rate limit and an outage, so a backup "
          "there is not a backup.")
    print("The third is chosen by STALENESS, not score. A router that only "
          "asks proven\nmodels stops learning about the rest, and they vanish "
          "by silence rather than merit.")
    print("\nROTATION DEPENDS ON RECORDING. The explorers move only when an "
          "offload is written\ndown (metrics/offload.py add). Skip that and "
          "this recommends the same two for ever,\nand the exploring quietly "
          "stops — which is the failure it exists to prevent.")


if __name__ == "__main__":
    main()
