"""A window on the bench, at http://localhost:8122.

    python serve.py

Stdlib only — no framework, no build step, no dependencies beyond what the
bench already needs. It reads the repository's own files and renders them; it
runs nothing and calls no model. Refreshing after a run is how it updates,
which is the whole design: the files are the record and this is a view of
them.

It writes three kinds of thing, each behind a popup and each logged to
`config/changes.jsonl`. A model's ROLE or ALIAS (click its name): rules from
the bench itself — a role the config defines; an alias in the standing form,
unused, never a retired name — written atomically, the rename recorded in
`config/alias-history.json`, the `llm` registry re-synced while it is the
client. A model's REMOVAL
(same popup, confirmed, reason optional): deregistered, taken
out of every collection, and recorded in `config/retired.json`; no evidence
is deleted. And ONBOARDING
(the sidebar's button): `scripts/onboard.py` started in the background with its log
tailed here. The upstream model id is never editable. Nothing else can write.

Every table carries a KEY beside it saying what each column measures. A
benchmark nobody can read is a benchmark nobody uses, and these numbers each
mean something narrower than their name suggests — an efficiency below 1.0 in
particular means the model arrived while wandering, which a success rate hides.

It binds to 127.0.0.1 on purpose. Nothing here is secret, but a benchmark
dashboard is not a thing to put on a network by accident. BENCH_BIND puts it
on one deliberately, and then it refuses to start without
BENCHWARMERS_ADMIN_TOKEN, which turns on the login (metrics/guard.py). It also
answers the gateway's routes, so one port serves both.
"""

from __future__ import annotations

import csv
import datetime
import functools
import html
import json
import math
import os
import pathlib
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "metrics"))
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
# BENCH_PORT: a second checkout (a dev worktree) beside the live one.
PORT = int(os.environ.get("BENCH_PORT") or 8122)

# Once, at import: roster() used to insert two entries on every page render.
for _d in ("metrics", "suite", "scripts"):
    sys.path.insert(0, str(ROOT / _d))
import aliases  # noqa: E402
from aliases import current as current_alias  # noqa: E402
import connect  # noqa: E402
import gateway  # noqa: E402  -- its routes are answered on this port too (one port)
import guard  # noqa: E402
import locked  # noqa: E402


def gateway_url() -> str:
    """Where a caller reaches the gateway: this dashboard's own address (it
    answers the gateway's routes), or BENCHWARMERS_PUBLIC_URL behind a proxy."""
    if public := guard.public_url():
        return public + "/v1"
    address = guard.bind()
    host = "127.0.0.1" if address in ("127.0.0.1", "0.0.0.0", "::") else address
    return f"http://{'[' + host + ']' if ':' in host else host}:{PORT}/v1"


UI = ROOT / "ui"

# THE THEME IS A FILE IN THIS REPO. Until 2026-10-19 the colours and faces were
# read live from the AI site's stylesheet in a sibling repository, which a
# public install does not have; the Wendell Research palette is now
# ui/themes/*.css, and the Chivo faces (OFL, licence beside them) in ui/fonts.
THEMES = UI / "themes"
BASE_THEME = "rainforest"      # sets every token (faces, measure, radius); the others lay theirs over it
DEFAULT_THEME = "hardwood"     # what an install shows until someone picks a theme
# The names and the order on Settings; a theme file not named here follows, by its own name.
THEME_LABELS = {"rainforest": "Light", "dark": "Dark", "hardwood": "Hardwood"}   # the files' names stay
# The rail's theme pill: Light and Dark as a sun and a moon, the rest by name.
_SVG = ("<svg viewBox='0 0 16 16' width='15' height='15' fill='none' stroke='currentColor' stroke-width='1.5'"
        " stroke-linecap='round' aria-hidden='true'>")
THEME_ICONS = {"rainforest": _SVG + "<circle cx='8' cy='8' r='3'/><path d='M8 1v1.5M8 13.5V15M1 8h1.5M13.5 8H15"
                                    "M3 3l1.1 1.1M11.9 11.9L13 13M3 13l1.1-1.1M11.9 4.1L13 3'/></svg>",
               "dark": _SVG + "<path d='M13.5 9.8A5.8 5.8 0 0 1 6.2 2.5a5.8 5.8 0 1 0 7.3 7.3z'/></svg>"}
FONTS = {f"{family}-latin-{weight}-normal.woff2": family
         for family, weights in (("chivo", (400, 500, 700, 800)),
                                 ("chivo-mono", (400, 500)))
         for weight in weights}


def themes() -> list[str]:
    found = {p.stem for p in THEMES.glob("*.css")}
    return [n for n in THEME_LABELS if n in found] + sorted(found - set(THEME_LABELS))


def _tokens(name: str) -> dict[str, str]:
    block = re.search(r":root\s*\{(.*?)\}", read(THEMES / f"{name}.css"), re.S)
    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", block.group(1))) if block else {}


def brand_css(fonts: bool = True) -> str:
    """Every theme's tokens, and the @font-face rules (not on the sign-in page:
    the fonts are behind the login). The default theme supplies every token on
    `:root` (faces, measure, radius); each other theme lays its own over them
    under `html[data-theme=<name>]`, so the page holds them all and switching
    is one attribute (bench.js), with no reload. scripts/check_themes.py checks
    each one's contrast."""
    def block(name: str) -> str:
        return "".join(f"{k}:{v.strip()};" for k, v in _tokens(name).items())
    scoped = "".join(f"html[data-theme='{name}']{{{block(name)}}}" for name in themes() if name != BASE_THEME)
    faces = "".join(
        f"@font-face{{font-family:'{'Chivo Mono' if fam == 'chivo-mono' else 'Chivo'}';"
        f"font-weight:{name.split('-')[-2]};font-style:normal;font-display:swap;"
        f"src:url('/fonts/{name}') format('woff2');}}"
        for name, fam in FONTS.items()) if fonts else ""
    return f":root{{{block(BASE_THEME)}}}{scoped}{faces}"


def font_file(name: str) -> pathlib.Path | None:
    path = (UI / "fonts" / name) if name in FONTS else None
    return path if path and path.exists() else None


# Images a theme may draw (Hardwood's centre court): named here, served from
# ui/ with a fixed type, so the route cannot be walked to other files.
ASSETS = {"mascot.png": "image/png"}
# The dashboard's own script and stylesheet, at an address that changes with
# the file (versioned), so a browser keeps each for good and a page carries
# neither (they were 270 KB of every page).
CODE = {"bench.js": "text/javascript; charset=utf-8", "bench.css": "text/css; charset=utf-8"}


def versioned(name: str) -> str:
    """/ui/<name>?v=<its modification time>: a new address whenever it changes."""
    return f"/ui/{name}?v={(UI / name).stat().st_mtime_ns:x}"


# The logo, header and byline in one, for a shared report's image (bench.js
# "share"): the repository's own, at /ui/<name>; light and dark grounds.
LOGOS = {"logo.svg": ROOT / ".github" / "logo.svg", "logo-dark.svg": ROOT / ".github" / "logo-dark.svg"}


def read(path: pathlib.Path) -> str:
    try:
        return locked.read_text(path)          # inside a render, from its snapshot
    except OSError:
        return ""


def load_json(path: pathlib.Path) -> dict:
    return json.loads(read(path) or "{}")


def data_stamp() -> float:
    """Newest change to anything the page renders from. The page polls this
    and, when it moves, fetches the page shown again (bench.js, "live")."""
    paths = [*DATA.glob("config/*.json*"), *DATA.glob("config/*.yaml"),
             *DATA.glob("metrics/*.json*"), *DATA.glob("metrics/usage/*.jsonl"),
             DATA / "perf" / "history.csv", *DATA.glob("runs/*.md"),
             *DATA.glob("runs/gateway/*.jsonl"), DATA / "runs" / "scheduler.log"]
    paths = [p for p in paths if p.name != UI_CONFIG.name]     # a theme switch is already on the page
    def at(p: pathlib.Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:                        # not there, or gone since it was listed (a writer's temporary file)
            return 0.0
    return max(map(at, paths), default=0.0)


# ---- kept between requests -------------------------------------------------
# A page that took a second to work out is kept while nothing it reads has
# changed: data_stamp() has not moved, the dashboard itself has written nothing
# (_writes, bumped after every POST and Connect's callback, which also covers a
# file the stamp does not watch), and for KEEP_FOR seconds at most, so what
# reads the clock (a reset, "resting until", today's column) is never older.
KEEP_FOR = 30
_kept: dict = {}                       # name -> (key, made at, value)
_kept_locks: dict = {}
_writes = [0]
_writes_lock = threading.Lock()        # += from two POSTs at once could step the count back to one a page was kept under


def data_key() -> tuple:
    """What the data is as this request starts, once a render: taken before it
    reads a file, so nothing kept is worked out from older text than its key
    says. render() asks first (its rail reads files before the page); the
    routes that keep something reach kept() before reading anything."""
    return locked.once(("data key",), lambda: (str(DATA), data_stamp(), _writes[0]))


def kept(name: str, make):
    """make(), kept between requests while data_key() stays the same, for at
    most KEEP_FOR seconds. A request asking while another works it out waits
    for that answer instead of working it out again."""
    key = data_key()
    with _kept_locks.setdefault(name, threading.Lock()):
        hit = _kept.get(name)
        if hit and hit[0] == key and time.monotonic() - hit[1] < KEEP_FOR:
            return hit[2]
        made = time.monotonic()
        value = make()
        _kept[name] = (key, made, value)
        return value


def wrote() -> None:
    """The dashboard changed something: nothing kept from before is answered again."""
    with _writes_lock:
        _writes[0] += 1


def _warm() -> None:
    """The home page worked out once as the dashboard starts (the run notes
    parsed, the answers scored, the logs read, kept), so the first visitor,
    most often the page coming back after a Restart, waits for what is left of
    it (kept) rather than all of it."""
    try:
        render()
    except Exception:                          # noqa: BLE001 -- the page asked for later says what went wrong
        pass


def alias_history() -> dict[str, str]:
    """Old alias -> current alias.

    Aliases were renamed to carry version numbers (`laguna-xs` became
    `laguna-xs-2.1`), because there will be a 2.2. Run notes are NARRATIVE and
    this repo does not retro-edit them, so instead the old names are mapped
    here — a historical run still lines up with the model it was about, and
    the record of what was actually written stays intact.
    """
    return load_json(DATA / "config" / "alias-history.json")


def suite_scores() -> dict:
    """Latest score per (task, model), from the dated run notes.

    The notes are the record, so they are also the source here rather than a
    parallel database that could disagree with them. Later files win, which is
    what makes a re-run show up.

    Not a vision or image model's (suite/run.images_only): the suite is text
    and code, which it is never seated for, and the answers it gave before it
    had that role stay in the notes, uncounted. Real work, perf and reviews
    still count it. Nor the vision tasks (vision-*): the Vision panel reads
    those (vision_rows).

    Read once a render (four panes ask), each caller given its own copy.
    """
    return {k: dict(v) for k, v in locked.once(("suite_scores",), _suite_scores).items()}


def _suite_scores() -> dict:
    from run import images_only
    scores: dict[tuple[str, str], dict] = {}
    skip = images_only()
    for _day, _run, task, model, found, invented, seconds in note_rows():
        model = current_alias(model)
        if model in skip or task.startswith("vision-"):
            continue
        scores[(task, model)] = {
            "found": found, "invented": invented.strip(" —-"),
            "seconds": float(seconds)}
    return scores


# A row of a run note's table (suite/run._write_report). The seconds may carry
# the call's attempts, "3.2 (x3)": such rows were skipped before 2026-11-08.
_NOTE_ROW = re.compile(r"^\|\s*([\w-]+)\s*\|\s*`([^`]+)`\s*\|\s*([^|]+?)\s*\|"
                       r"\s*([^|]*?)\s*\|\s*([\d.]+)(?:\s*\(x\d+\))?\s*\|")
_notes: dict = {}                                   # path -> ((mtime_ns, size), rows)
_notes_all: tuple = (None, [])                      # (the listing's stats, every row)


def note_rows() -> list[tuple]:
    """Every table row of the run notes (runs/suite-*.md), in order, as
    written: (day, run, task, alias, found, invented, seconds). `run` counts
    the note's "## Suite run" sections (one per run, in start order); `day` is
    "" for a note not named by its date. Shared: read it, never change it."""
    return _note_table()[1]


def _note_table() -> tuple:
    """(the notes' stats, note_rows()). A note is parsed again only when its
    listing's stat changes: only today's does."""
    global _notes_all
    listing = []
    try:
        for e in os.scandir(DATA / "runs"):
            if e.name.startswith("suite-") and e.name.endswith(".md"):
                try:
                    listing.append((e.path, e.name, e.stat()))
                except OSError:                         # gone since the listing: the rest still count
                    pass
    except OSError:
        pass
    listing.sort(key=lambda e: e[1])
    key = tuple((path, st.st_mtime_ns, st.st_size) for path, _name, st in listing)
    seen = _notes_all
    if seen[0] == key:
        return seen
    out: list[tuple] = []
    for path, name, st in listing:
        hit = _notes.get(path)
        if hit is None or hit[0] != (st.st_mtime_ns, st.st_size):
            day = name[6:-3] if re.fullmatch(r"\d{4}-\d\d-\d\d", name[6:-3]) else ""
            # From disk, not the render's copy (read): a note appended since this
            # render first read it would be kept as it was, under its new stat.
            try:
                text = pathlib.Path(path).read_text(encoding="utf-8")
            except OSError:                             # this time only: kept under no key, read again next time
                key = None
                continue
            rows, run = [], 0
            for line in text.splitlines():
                if line.startswith("## Suite run"):
                    run += 1
                elif (m := _NOTE_ROW.match(line.strip())):
                    rows.append((day, run, *m.groups()))
            hit = _notes[path] = ((st.st_mtime_ns, st.st_size), rows)
        out += hit[1]
    _notes_all = (key, out)
    return _notes_all


def perf_rows() -> list[dict]:
    """perf/history.csv, read once a render (Benchmarks asks three times),
    each caller given its own copies."""
    return [dict(r) for r in locked.once(("perf_rows",), _perf_rows)]


def _perf_rows() -> list[dict]:
    path = DATA / "perf" / "history.csv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    for row in rows:
        row["model"] = current_alias(row.get("model", ""))
    return rows


def registered() -> dict[str, str]:
    """model_name -> llm id. Parsed once a render (asked a hundred times)."""
    return dict(locked.derived(YAML_PATH, _registered))


def _registered(text: str) -> dict[str, str]:
    out, current = {}, None
    for line in text.splitlines():
        if (m := re.match(r"\s*-?\s*model_id:\s*(.+)", line)):
            current = m.group(1).strip().strip("\"'")
        elif (m := re.match(r"\s*model_name:\s*(.+)", line)):
            out[m.group(1).strip().strip("\"'")] = current or "?"
    return out


def tag_for(llm_id: str) -> str:
    """The connection a model arrives through, by its llm alias (route.full_tag_of:
    its Open WebUI tag, else its endpoint); "" when unknown."""
    import route
    tag = route.full_tag_of(llm_id)
    return "" if tag == "?" else tag


def provider_label(tag: str) -> str:
    """A connection tag as a short provider name for a chip: a proxy's vendor
    (cli-proxy-api/codex: codex), else the host without its api./
    router. prefix, -api suffix and TLD (router.requesty.ai: requesty)."""
    if "/" in tag:
        return tag.split("/", 1)[1]
    host = re.sub(r"^(api|apihub|router)\.", "", tag)
    host = host.rsplit(".", 1)[0] if "." in host else host
    return host.removesuffix("-api") or tag


def retired() -> dict[str, dict]:
    """Aliases removed from the bench by hand, with when and why. Parsed once
    a render (Benchmarks asked 1,200 times), each caller given its own dict."""
    return dict(locked.derived(RETIRED_PATH, _object))


def _object(text: str) -> dict:
    """A JSON file's text as load_json reads it ({} when there is none)."""
    return json.loads(text or "{}")


def _ad_hoc(text: str) -> frozenset:
    """config/model-roles.json's ad hoc aliases."""
    return frozenset(json.loads(text or "{}").get("ad_hoc", []))


def current() -> set[str]:
    """Aliases the tables show: still registered, or removed HERE and faded.

    The dashboard answers "what should I route to now", so a model that has
    silently gone from the registry does not belong in its tables — `laguna-m1`
    was retired by OpenRouter and removed, and went on appearing with the
    `error` rows from the run that discovered it. The run NOTES keep that
    history, which is where history belongs.

    A model removed from the popup is different: it was here yesterday, its
    evidence still is, and a row that simply vanishes reads as a bug. Those
    stay in the tables, greyed and labelled, so the change is visible rather
    than inferred from an absence.
    """
    return set(registered().values()) | set(retired())


def esc(value) -> str:
    return html.escape(str(value))


def named(model: str) -> str:
    """`kimi-k3 (lan-proxy/moonshot)` — who is actually serving this.

    The name is a link: clicking it opens the edit popup for that alias.
    """
    gone = retired().get(model)
    if gone:
        sub = f"retired {gone.get('at', '')[:10]}"
    else:
        d = locked.once(("decide.load",), decide.load).get(model)
        sub = " · ".join(filter(None, [
            f"{d['served']} · via {d['via']}" if d else tag_for(model),
            "ad hoc" if model in locked.derived(ROLES_PATH, _ad_hoc) else ""]))
    return (f"<a href='#' class='model' data-alias='{esc(model)}'>"
            f"<span class='alias'>{esc(model)}</span></a>"
            + (f"<span class='conn'>{esc(sub)}</span>" if sub else ""))


def tr(model: str, attrs: str = "") -> str:
    """A table row's opening tag; retired rows hide behind the toggle."""
    return f"<tr class='retired'{attrs}>" if model in retired() else f"<tr{attrs}>"


# ---- editing a model: role and alias, under the bench's own rules ----------

ROLES_PATH = DATA / "config" / "model-roles.json"
YAML_PATH = DATA / "config" / "extra-openai-models.yaml"
HISTORY_PATH = DATA / "config" / "alias-history.json"
OPTIONS_PATH = DATA / "config" / "model-options.json"   # keyed by alias, follows it
import endpoints  # noqa: E402
import decide  # noqa: E402
import bench_collections  # noqa: E402
import model_roles  # noqa: E402
import catalog  # noqa: E402
CHANGES_PATH = DATA / "config" / "changes.jsonl"

# The standing alias form: what onboard.py derives — vendor prefix dropped,
# `:` became `-`. Letters either case (existing aliases carry some), digits,
# dot, dash, underscore; starts with a letter or digit; no spaces.
ALIAS_FORM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$")


def roles_config() -> dict:
    return load_json(ROLES_PATH)


def model_directory() -> dict[str, dict]:
    """alias -> {served, tag, role, ad_hoc, endpoint} for every registered model."""
    config = roles_config()
    roles = config.get("models", {})
    ad_hoc = set(config.get("ad_hoc", []))
    on = {r["alias"]: r["endpoint"] for r in endpoints.registry()}
    out = {}
    for served, alias in registered().items():
        out[alias] = {"served": served, "tag": tag_for(alias),
                      "role": roles.get(alias, "general"),
                      "ad_hoc": alias in ad_hoc,
                      "endpoint": on.get(alias, endpoints.default())}
    # Decision models live in config/decision-models.json, not the llm yaml;
    # `kind` sends their rename, removal and restore there instead.
    for alias, d in decide.load().items():
        out[alias] = {"served": d["served"], "role": "decision", "kind": "decision",
                      "question_types": d["question_types"],
                      "tag": f"via {d['via']} · takes {', '.join(d['question_types'])}",
                      "ad_hoc": alias in ad_hoc, "endpoint": d["endpoint"]}
    return out


def _write_atomic(path: pathlib.Path, text: str) -> None:
    locked.write_atomic(path, text)                # makes config/ on a fresh install's first save


def _dump(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def change_model(alias: str, new_alias: str | None, new_role: str | None,
                 who: str = "ui", new_ad_hoc: bool | None = None,
                 new_question_types: list | None = None) -> dict:
    """Apply a role, ad-hoc and/or alias change, or refuse with reasons.

    `new_ad_hoc` None leaves the flag alone; True/False sets it. Ad hoc is
    kept apart from role on purpose: role says what a model is for, ad hoc
    says it is too expensive to be picked by anything but a person.

    Returns {"ok": bool, "errors": [...], "changed": [...], "sync": str}.
    Every rule is checked before anything is written, so a refusal changes
    nothing; the writes are atomic per file and logged.
    """
    directory = model_directory()
    roles = roles_config()
    known_roles = set(model_roles.load()["roles"])
    errors: list[str] = []

    if alias not in directory:
        return {"ok": False, "errors": [f"{alias!r} is not a registered alias"],
                "changed": [], "sync": ""}
    new_alias = (new_alias or "").strip()
    new_role = (new_role or "").strip()
    rename = bool(new_alias) and new_alias != alias
    rerole = bool(new_role) and new_role != directory[alias]["role"]
    if new_ad_hoc is not None and not isinstance(new_ad_hoc, bool):
        errors.append(f"ad_hoc must be true or false; got {new_ad_hoc!r}")
        new_ad_hoc = None
    reflag = new_ad_hoc is not None and new_ad_hoc != directory[alias]["ad_hoc"]

    is_decision = directory[alias].get("kind") == "decision"
    # A decision model's question types: removing one only stops the bench
    # asking; adding one is proven by a test question before it is written.
    retype = False
    if new_question_types is not None and is_decision:
        wanted = [t for t in decide.PROBES if t in set(new_question_types)]
        have = directory[alias]["question_types"]
        unknown = set(new_question_types) - set(decide.PROBES)
        if unknown or not wanted:
            errors.append(f"question types must be one or more of {', '.join(decide.PROBES)}")
        elif wanted != have:
            retype = True
            entry = decide.load()[alias]
            for kind in set(wanted) - set(have):
                try:
                    decide.call(entry["via"], entry["served"], decide.PROBE_STATE,
                                {kind: decide.PROBES[kind]}, path=entry.get("path", "decisions"))
                except Exception as exc:                   # noqa: BLE001
                    errors.append(f"{alias} did not answer a {kind} question:"
                                  f" {decide.error_text(exc)}; not added")
    if rerole and (is_decision or new_role == "decision"):
        errors.append("the decision role is fixed: a decision model cannot answer chat, and a"
                      " chat model cannot answer typed questions")
    elif rerole and new_role not in known_roles:
        errors.append(f"role must be one of {', '.join(sorted(known_roles))};"
                      f" got {new_role!r}. New roles are added in"
                      " config/model-roles.json, not here")
    if rename:
        history = load_json(HISTORY_PATH)
        if not ALIAS_FORM.match(new_alias):
            errors.append("alias must be letters, digits, '.', '-' or '_', start"
                          " with a letter or digit, 2-64 characters, no spaces")
        if new_alias in directory:
            errors.append(f"{new_alias!r} is already the alias of"
                          f" {directory[new_alias]['served']}")
        if new_alias in history:
            errors.append(f"{new_alias!r} is a retired alias (it became"
                          f" {history[new_alias]!r}); a retired name is never"
                          " reused, because the run notes still say it")
        if new_alias in load_json(RETIRED_PATH):
            errors.append(f"{new_alias!r} was removed from the bench; a retired"
                          " name is never reused, because the run notes still say it")
        if new_alias.lower() != alias.lower() and any(
                a.lower() == new_alias.lower() for a in directory):
            errors.append(f"{new_alias!r} differs from an existing alias only by"
                          " case; the CLI is case-sensitive, people are not")
    if not rename and not rerole and not reflag and not retype and not errors:
        errors.append("nothing to change")
    if errors:
        return {"ok": False, "errors": errors, "changed": [], "sync": ""}

    changed: list[str] = []
    stamp = datetime.datetime.now().astimezone().isoformat(timespec="seconds")

    if rerole:
        models = dict(roles.get("models", {}))
        models[alias] = new_role
        roles["models"] = models
        _write_atomic(ROLES_PATH, _dump(roles))
        changed.append("config/model-roles.json")
        _log_change(stamp, who, alias, "role", directory[alias]["role"], new_role)

    if reflag:
        roles = roles_config()
        held = [a for a in roles.get("ad_hoc", []) if a != alias]
        if new_ad_hoc:
            held.append(alias)
        roles["ad_hoc"] = sorted(held)
        _write_atomic(ROLES_PATH, _dump(roles))
        if "config/model-roles.json" not in changed:
            changed.append("config/model-roles.json")
        _log_change(stamp, who, alias, "ad_hoc", directory[alias]["ad_hoc"], new_ad_hoc)

    if retype:
        data = decide.load()
        data[alias]["question_types"] = wanted
        _write_atomic(decide.FILE, _dump(data))
        changed.append("config/decision-models.json")
        _log_change(stamp, who, alias, "question_types", have, wanted)

    sync_report = ""
    if rename:
        if is_decision:
            # 1. a decision model's registry is its key in decision-models.json
            data = decide.load()
            data[new_alias] = data.pop(alias)
            _write_atomic(decide.FILE, _dump(data))
            changed.append("config/decision-models.json")
        else:
            # 1. the registry the llm CLI reads — the one line that names the alias
            text = read(YAML_PATH)
            pattern = re.compile(rf"^(\s*-?\s*model_id:\s*){re.escape(alias)}\s*$", re.M)
            text, n = pattern.subn(rf"\g<1>{new_alias}", text)
            if n != 1:
                return {"ok": False, "changed": changed, "sync": "",
                        "errors": [f"expected exactly one 'model_id: {alias}' line in"
                                   f" extra-openai-models.yaml, found {n}; nothing renamed"]}
            _write_atomic(YAML_PATH, text)
            changed.append("config/extra-openai-models.yaml")
        # 2. the history, so every alias-keyed record follows the rename
        history = load_json(HISTORY_PATH)
        history[alias] = new_alias
        _write_atomic(HISTORY_PATH, _dump(history))
        aliases.forget()                       # its one-second cache, so the next read has the rename
        changed.append("config/alias-history.json")
        # 3. configs keyed by alias
        roles = roles_config()
        models = dict(roles.get("models", {}))
        held = list(roles.get("ad_hoc", []))
        if alias in models or alias in held:
            if alias in models:
                models[new_alias] = models.pop(alias)
            roles["models"] = models
            if alias in held:
                roles["ad_hoc"] = sorted(new_alias if a == alias else a for a in held)
            _write_atomic(ROLES_PATH, _dump(roles))
            if "config/model-roles.json" not in changed:
                changed.append("config/model-roles.json")
        options = load_json(OPTIONS_PATH)
        if alias in options:
            options[new_alias] = options.pop(alias)
            _write_atomic(OPTIONS_PATH, _dump(options))
            changed.append("config/model-options.json")
        _log_change(stamp, who, alias, "alias", alias, new_alias)
        if is_decision:
            return {"ok": True, "errors": [], "changed": changed, "sync": ""}
        # 4. push the registry to where the CLI reads it
        sync_report = _sync_cli()

    return {"ok": True, "errors": [], "changed": changed, "sync": sync_report}


def _log_change(stamp: str, who: str, alias: str, field: str, old, new) -> None:
    with CHANGES_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": stamp, "by": who, "alias": alias,
                             "field": field, "old": old, "new": new}) + "\n")


RETIRED_PATH = DATA / "config" / "retired.json"


def _drop_from_collections(alias: str, stamp: str, who: str) -> dict[str, list[str]]:
    """Take a retired alias out of every collection: off `members`, and off
    `merger` (empty then, which means chosen by verdicts). Otherwise each call
    to that collection seats a model that is no longer registered. Returns
    {collection: [the fields it was in]} for retired.json, so Restore can put
    it back. An unreadable collections.json is left alone (a save would wipe
    it); the gateway skips a retired member anyway."""
    if bench_collections.unreadable(bench_collections.FILE):
        return {}
    data = bench_collections.load()
    was: dict[str, list[str]] = {}
    for name, c in data.items():
        if not isinstance(c, dict):
            continue
        fields = []
        if alias in (c.get("members") or []):
            fields.append("members")
        if c.get("merger") == alias:
            fields.append("merger")
        if not fields:
            continue
        old = json.loads(json.dumps(c))
        if "members" in fields:
            c["members"] = [m for m in c["members"] if m != alias]
        if "merger" in fields:
            c["merger"] = ""
        was[name] = fields
        _log_change(stamp, who, alias, f"collection {name}", old, c)
    if was:
        _write_atomic(bench_collections.FILE, _dump(data))
    return was


def _return_to_collections(alias: str, was: dict, stamp: str, who: str) -> list[str]:
    """Undo _drop_from_collections for the collections that still exist; a
    collection deleted since, or one now full (max_calls), is left as it is.
    Returns the names it went back into."""
    if not was or bench_collections.unreadable(bench_collections.FILE):
        return []
    data, s = bench_collections.load(), bench_collections.settings()
    back = []
    for name, fields in was.items():
        c = data.get(name)
        if not isinstance(c, dict):
            continue
        old = json.loads(json.dumps(c))
        members = c.get("members") or []
        if "members" in fields and alias not in members and len(members) < bench_collections.limits(c, s)["max_calls"]:
            c["members"] = members + [alias]
        if "merger" in fields and not c.get("merger"):
            c["merger"] = alias
        if c != old:
            back.append(name)
            _log_change(stamp, who, alias, f"collection {name}", old, c)
    if back:
        _write_atomic(bench_collections.FILE, _dump(data))
    return back


# The llm CLI keeps its own copy of the registry and the options, which only
# it reads: the built-in client reads the data folder's. So the copy is kept
# in step only while the llm CLI is the client (it cost 5-6 s a save), and
# save_client syncs it whole when it becomes the client.
# ponytail: BENCH_CLIENT=llm set at a restart is not a switch anyone sees here;
# run scripts/sync_config.py once then, or sync at startup if that becomes usual.
NOT_SYNCED = "not synced: the built-in client is in use; synced when you switch to the llm CLI"


def _sync_cli() -> str:
    import client as model_client
    if model_client.chosen()[0] != "llm":
        return NOT_SYNCED
    try:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "sync_config.py")],
            cwd=str(ROOT), capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as exc:
        return f"could not run scripts/sync_config.py ({exc}); run it by hand"
    if result.returncode == 0:
        return "synced to the llm CLI"
    return (f"sync_config.py exited {result.returncode}:"
            f" {(result.stderr or result.stdout).strip()[:300]} — run it by hand")


def remove_model(alias: str, reason: str = "", who: str = "ui") -> dict:
    """Retire a model from the bench by hand.

    The daily check (`scripts/check_deprecations.py`, via `auto_retire.py`)
    calls this too, with `who="auto"`, when a model is clearly gone; this is
    also the button for a person who knows sooner. It deregisters the alias from the llm registry (and
    re-syncs the llm CLI while it is the client),
    drops it from the role and direct-endpoint configs and from every
    collection (members and merger), records it in
    `config/retired.json` — so the alias is never reused, and onboarding the
    same served model again is a deliberate act — clears that model's alerts,
    and logs the change. It deletes NO evidence: suite outputs, offloads,
    perf history and run notes stay exactly as they are.
    """
    directory = model_directory()
    if alias not in directory:
        return {"ok": False, "errors": [f"{alias!r} is not a registered alias"], "changed": [], "sync": ""}
    # The page's confirm popup is the safeguard; a reason is recorded when given.
    reason = (reason or "").strip() or "retired by hand"

    served = directory[alias]["served"]
    stamp = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    changed = []

    if directory[alias].get("kind") == "decision":
        # Its whole entry goes into retired.json, so Restore puts back the
        # same endpoint, route and question types without a new probe.
        data = decide.load()
        entry = data.pop(alias)
        _write_atomic(decide.FILE, _dump(data))
        # A decision model is never saved into a collection; a hand edit may have.
        was = _drop_from_collections(alias, stamp, who)
        retired = load_json(RETIRED_PATH)
        retired[alias] = {"served": served, "tag": directory[alias]["tag"], "role": "decision",
                          "at": stamp, "by": who, "why": reason.strip(),
                          "endpoint": entry["endpoint"], "decision": entry,
                          **({"ad_hoc": True} if directory[alias]["ad_hoc"] else {}),
                          **({"collections": was} if was else {})}
        _write_atomic(RETIRED_PATH, _dump(retired))
        roles = roles_config()
        roles.get("models", {}).pop(alias, None)
        roles["ad_hoc"] = [a for a in roles.get("ad_hoc", []) if a != alias]
        _write_atomic(ROLES_PATH, _dump(roles))
        _log_change(stamp, who, alias, "removed", served, reason.strip())
        return {"ok": True, "errors": [], "sync": "",
                "changed": ["config/decision-models.json", "config/retired.json",
                            "config/model-roles.json"]
                           + (["config/collections.json"] if was else [])}

    # 1. the registry block — model_id line plus its indented lines
    text = read(YAML_PATH)
    block = re.compile(rf"(?:^|\n)\n?-\s*model_id:\s*{re.escape(alias)}\s*\n(?:[ \t]+.*\n?)*")
    text, n = block.subn("\n", text)
    if n != 1:
        return {"ok": False, "changed": [], "sync": "",
                "errors": [f"expected one registry block for {alias!r}, found {n}; nothing removed"]}
    _write_atomic(YAML_PATH, text.rstrip("\n") + "\n")
    changed.append("config/extra-openai-models.yaml")
    # 2. the collections it sat in, so no call seats a model that is gone
    was = _drop_from_collections(alias, stamp, who)
    if was:
        changed.append("config/collections.json")
    # 3. the memory of it -- enough to restore it exactly: endpoint, options,
    # collections too
    retired = load_json(RETIRED_PATH)
    options = load_json(OPTIONS_PATH)
    retired[alias] = {"served": served, "tag": directory[alias]["tag"],
                      "role": directory[alias]["role"], "at": stamp, "by": who,
                      "why": reason.strip(), "endpoint": directory[alias]["endpoint"],
                      **({"ad_hoc": True} if directory[alias]["ad_hoc"] else {}),
                      **({"options": options[alias]} if alias in options else {}),
                      **({"collections": was} if was else {})}
    _write_atomic(RETIRED_PATH, _dump(retired))
    changed.append("config/retired.json")
    # 4. configs keyed by alias
    roles = roles_config()
    if alias in roles.get("models", {}) or alias in roles.get("ad_hoc", []):
        roles.get("models", {}).pop(alias, None)
        if alias in roles.get("ad_hoc", []):
            roles["ad_hoc"] = [a for a in roles["ad_hoc"] if a != alias]
        _write_atomic(ROLES_PATH, _dump(roles))
        changed.append("config/model-roles.json")
    if alias in options:
        options.pop(alias)
        _write_atomic(OPTIONS_PATH, _dump(options))
        changed.append("config/model-options.json")
    # 5. its alerts — the action they asked for has been taken
    try:
        import alerts as _alerts
        for key in list(_alerts.active()):
            if key.startswith("model:") and key.endswith(f":{served}"):
                _alerts.clear(key)
                changed.append(f"alert {key}")
    except Exception:                                  # noqa: BLE001
        pass
    _log_change(stamp, who, alias, "removed", served, reason.strip())
    return {"ok": True, "errors": [], "changed": changed, "sync": _sync_cli()}


def dismiss_alert(key: str) -> dict:
    """Got it on a notice (an alert raised with dismiss=True, such as NVIDIA
    changing its stated limits). A state that is still wrong is not dismissed:
    its check clears it when it is put right."""
    import alerts as _alerts
    if not (_alerts.active().get(key) or {}).get("dismiss"):
        return {"ok": False, "errors": [f"{key!r} is not a notice that can be dismissed"]}
    _alerts.clear(key)
    return {"ok": True, "errors": []}


def retry_model(alias: str, who: str = "ui") -> dict:
    """One fresh call to a model on Action needed. An answer is written to
    perf/history.csv as evidence (scripts/auto_retire.py reads it, so the
    clock resets) and its alerts are cleared now rather than at the next 5am
    check (alerts.answered). A failure writes nothing: the daily probe keeps
    the record.

    One request, never the client's retries: a 429's stated wait (UnoRouter's
    60 s) then a second request spent a minute and another call of a 1-a-minute
    free tier, and Retry all did that for each model in turn. A person can
    click again."""
    names = registered()
    if alias not in names.values():
        return {"ok": False, "errors": [f"{alias!r} is not registered"]}
    from run import ask_once, failure_class, llm_binary
    body, seconds = ask_once(llm_binary(), alias, "Reply with exactly one word: ready", 90, retries=0)
    cls = failure_class(body, min_length=1)
    if cls is not None:
        try:                                           # a stated quota or wait still teaches a cooldown
            import learn
            learn.record(alias, body, "retry")
        except Exception:                              # noqa: BLE001
            pass
        return {"ok": False, "errors": [f"{alias} still fails ({cls}): {' '.join(body.split())[:200]}"]}
    with (DATA / "perf" / "history.csv").open("a", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerow([datetime.datetime.now().isoformat(timespec="seconds"), alias, "retry", 1,
                                 f"{seconds:.2f}", f"{seconds:.2f}", f"{seconds:.2f}", f"{seconds:.2f}",
                                 0.0, 1, 0, 0, 0, "", "retry from the dashboard", ""])
    import alerts as _alerts
    # Every alert the answer disproves, not only a retiring one: 2026-11-02,
    # big-pickle answered and stayed on Action needed as "healthy", its
    # unrecognised-failure alert still open.
    cleared = _alerts.answered(alias, {a: s for s, a in names.items()}.get(alias, ""))
    _log_change(datetime.datetime.now().astimezone().isoformat(timespec="seconds"), who, alias,
                "retried", None, f"answered in {seconds:.1f}s")
    return {"ok": True, "errors": [], "sync": "",
            "changed": [f"{alias} answered in {seconds:.1f}s"] + [f"cleared {k}" for k in cleared]}


def restore_model(alias: str, who: str = "ui") -> dict:
    """Undo a removal: the same alias, role, ad-hoc flag and direct endpoint.

    The daily check retires models on its own now, so a wrong call has to be
    cheap to reverse. Logged as `restored`, and the deprecation check ignores
    failures recorded before that moment -- otherwise yesterday's evidence
    would retire it again tomorrow morning.
    """
    retired = load_json(RETIRED_PATH)
    entry = retired.get(alias)
    if not entry:
        return {"ok": False, "errors": [f"{alias!r} is not a retired alias"], "changed": [], "sync": ""}
    gone = entry.get("endpoint")
    if gone and gone != "?" and gone not in endpoints.load():
        # Retired with its endpoint: putting it on the default would send its
        # prompts to another provider under its old name.
        return {"ok": False, "changed": [], "sync": "",
                "errors": [f"its endpoint {gone} was removed; add an endpoint named {gone} again, then restore"]}
    if entry.get("decision"):
        data = decide.load()
        if alias in data or any(d["served"] == entry["served"] and d["endpoint"] == entry["endpoint"]
                                for d in data.values()):
            return {"ok": False, "changed": [], "sync": "",
                    "errors": [f"{entry['served']} is on the bench again; nothing to restore"]}
        data[alias] = entry["decision"]
        _write_atomic(decide.FILE, _dump(data))
        roles = roles_config()
        roles.setdefault("models", {})[alias] = "decision"
        if entry.get("ad_hoc"):
            roles["ad_hoc"] = sorted(set(roles.get("ad_hoc", [])) | {alias})
        _write_atomic(ROLES_PATH, _dump(roles))
        stamp = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        back = _return_to_collections(alias, entry.get("collections") or {}, stamp, who)
        del retired[alias]
        _write_atomic(RETIRED_PATH, _dump(retired))
        _log_change(stamp, who, alias, "restored", None, entry["served"])
        return {"ok": True, "errors": [], "sync": "",
                "changed": ["config/decision-models.json", "config/model-roles.json",
                            "config/retired.json"]
                           + (["config/collections.json"] if back else [])}
    if entry["served"] in registered():
        return {"ok": False, "changed": [], "sync": "",
                "errors": [f"{entry['served']} is registered again as"
                           f" {registered()[entry['served']]!r}; nothing to restore"]}
    endpoint = entry.get("endpoint") if entry.get("endpoint") in endpoints.load() \
        else endpoints.default()
    # Written here rather than through onboard.register, which has its own
    # fixed path -- YAML_PATH is what every other write in this file uses.
    _write_atomic(YAML_PATH, read(YAML_PATH).rstrip("\n") + "\n"
                  + endpoints.yaml_block(alias, entry["served"], endpoint))
    changed = ["config/extra-openai-models.yaml"]
    roles = roles_config()
    if entry.get("role", "general") != "general" or entry.get("ad_hoc"):
        roles.setdefault("models", {})[alias] = entry.get("role", "general")
        if entry.get("ad_hoc"):
            roles["ad_hoc"] = sorted(set(roles.get("ad_hoc", [])) | {alias})
        _write_atomic(ROLES_PATH, _dump(roles))
        changed.append("config/model-roles.json")
    if entry.get("options"):
        options = load_json(OPTIONS_PATH)
        options[alias] = entry["options"]
        _write_atomic(OPTIONS_PATH, _dump(options))
        changed.append("config/model-options.json")
    stamp = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    if _return_to_collections(alias, entry.get("collections") or {}, stamp, who):
        changed.append("config/collections.json")
    del retired[alias]
    _write_atomic(RETIRED_PATH, _dump(retired))
    changed.append("config/retired.json")
    _log_change(stamp, who, alias, "restored", None, entry["served"])
    return {"ok": True, "errors": [], "changed": changed, "sync": _sync_cli()}


DISMISSED_PATH = DATA / "config" / "dismissed.json"


_modalities_index: tuple = (None, {})


def _modelsdev_modalities() -> dict[str, dict]:
    """models.dev's cached modalities by comparable model name
    (sources.norm_model), not once per row, and built again only when the
    daily fetch rewrites its copy (a second of every picker opening)."""
    global _modalities_index
    import sources
    try:
        st = (sources.CACHE / "modelsdev.json").stat()
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        key = None
    if key and _modalities_index[0] == key:
        return _modalities_index[1]
    out = {}
    for p in sources.cached("modelsdev").values():
        for k, m in ((p or {}).get("models") or {}).items():
            if (m.get("modalities") or {}).get("input"):
                out.setdefault(sources.norm_model(k), m["modalities"])
    _modalities_index = (key, out)
    return out


def _modalities(entry: dict, served: str, index: dict) -> dict:
    """{inputs, outputs} for a picker row: the endpoint's own listing when it
    says (OpenRouter's architecture), else models.dev's same model; empty
    lists when neither knows."""
    import sources
    arch = entry.get("architecture") or {}
    if arch.get("input_modalities"):
        return {"inputs": arch["input_modalities"], "outputs": arch.get("output_modalities") or []}
    m = index.get(sources.norm_model(served)) or {}
    return {"inputs": m.get("input") or [], "outputs": m.get("output") or []}


# What each endpoint answered, for LISTED_FOR seconds: name -> (when, its
# config, live, conns). Only the asking is kept; what is on the bench, retired
# or dismissed is taken off fresh on every opening.
LISTED_FOR = 3600                  # an hour: lists rarely change sooner, and Refresh is one click
_listed: dict[str, tuple] = {}


def onboard_candidates(fresh: bool = False) -> dict:
    """What every endpoint serves that the bench could onboard, for the picker.

    Served, minus registered, minus retired, minus queued or onboarding now,
    minus dismissed (those come back separately so a dismissal can be undone),
    each endpoint asked separately
    so one being down only empties its own group. The live calls this page
    makes happen only when the picker opens: a dashboard that needs the
    network to render would be down exactly when you want to look at it.

    An Open WebUI model's group is its connection, named live from its
    `urlIdx` the way provider_tags.py names them (base URLs only, never the
    keys): provider-tags.json is written by that script on its schedule, so a
    model added to Open WebUI since was "unknown connection" until it ran.
    The file is the fallback when the hub's connections cannot be read.

    An endpoint's answer is reused for LISTED_FOR seconds, unless `fresh` (the
    picker's Refresh) or its entry in endpoints.json changed since; a failure
    is never kept. Open WebUI alone can take ten seconds to list.
    """
    config = endpoints.load()
    names = list(config)
    if not names:
        return {"ok": False, "errors": ["no endpoint yet: add one first (Endpoints, then Manage, then"
                                        " Add an endpoint), and what it serves is listed here"]}
    taken = ({endpoints.snapshot_key(r["served"], r["endpoint"]) for r in endpoints.registry()}
             | {endpoints.snapshot_key(r.get("served", ""), r.get("endpoint") or endpoints.default())
                for r in retired().values()}
             | {endpoints.snapshot_key(d["served"], d["endpoint"]) for d in decide.load().values()})
    with _OQ_LOCK:                                  # on its way: Start would refuse it anyway
        taken |= {endpoints.snapshot_key(i["served"], i["endpoint"]) for i in _OQ["items"]
                  if i["state"] in ("queued", "running")}
    tags = load_json(DATA / "config" / "provider-tags.json")
    dismissed = load_json(DISMISSED_PATH)
    first_seen = load_json(DATA / "config" / "served-snapshot.json").get("last_seen", {})
    open_, gone, errors = [], [], []
    from concurrent.futures import ThreadPoolExecutor

    def ask(name: str):
        kept = _listed.get(name)
        if not fresh and kept and kept[1] == config[name] and time.time() - kept[0] < LISTED_FOR:
            return kept[2], kept[3], kept[0]
        try:
            live = endpoints.models(name)
        except Exception as exc:                       # noqa: BLE001
            return exc, [], 0
        conns = []
        if endpoints.hub(name) == "openwebui":
            import provider_tags
            try:
                conns = provider_tags.connection_names(name)
            except Exception:                          # noqa: BLE001 -- the tags file answers instead
                pass
        _listed[name] = (time.time(), config[name], live, conns)
        return live, conns, _listed[name][0]
    with ThreadPoolExecutor(max_workers=len(names)) as pool:   # all at once, not in turn
        answers = dict(zip(names, pool.map(ask, names)))
    # The oldest answer shown, so the picker can say how old its list is.
    listed = min((at for _, _, at in answers.values() if at), default=time.time())
    mods = _modelsdev_modalities()
    for name, (live, conns, _) in answers.items():
        if isinstance(live, Exception):
            errors.append(f"cannot reach {name}: {live}")
            continue
        owui, whole = endpoints.hub(name) == "openwebui", endpoints.recorded_whole(name)
        for served, entry in sorted(live.items()):
            key = endpoints.snapshot_key(served, name)
            if key in taken:
                continue
            # On Open WebUI the group is the connection behind it; any other
            # endpoint is its own group.
            at = entry.get("urlIdx")
            if not owui:
                group = f"{name} (direct)"
            elif entry.get("connection_type") == "local":
                group = "local"
            elif isinstance(at, int) and 0 <= at < len(conns):
                group = conns[at]
            else:
                group = tags.get(served, "").strip("?").split("/")[0] or "unknown connection"
            # New since the daily check, where it records the whole list; on an
            # aggregator that records only registered models, everything would be.
            row = {"served": served, "endpoint": name, "key": key, "group": group,
                   "new": whole and served not in first_seen, **_modalities(entry, served, mods)}
            (gone if key in dismissed else open_).append(row)
    if not open_ and not gone and errors:
        return {"ok": False, "errors": errors}
    return {"ok": True, "errors": errors, "candidates": open_, "dismissed": gone,
            "age": round(time.time() - listed)}


def dismiss(served, undo: bool = False, who: str = "ui") -> dict:
    """Stop offering served models in the picker (image models, arena, …), or
    bring them back. `served` is one picker key (served@endpoint) or a list of
    them, the picker's ticked rows: one locked write, a change-log line each."""
    keys = [served] if isinstance(served, str) else served if isinstance(served, list) else []
    keys = [k.strip() for k in keys if isinstance(k, str) and k.strip()]
    if not keys:
        return {"ok": False, "errors": ["which model?"]}
    stamp = datetime.datetime.now().astimezone().isoformat(timespec="seconds")

    def change(data: dict) -> None:
        for k in keys:
            if undo:
                data.pop(k, None)
            else:
                data[k] = {"at": stamp, "by": who}
    locked.update(DISMISSED_PATH, change, ensure_ascii=False)
    for k in keys:
        _log_change(stamp, who, "", "undismissed" if undo else "dismissed", None, k)
    return {"ok": True, "errors": []}


def endpoint_list() -> dict:
    """Every endpoint, and whether its key is set -- never the key itself."""
    counts: dict[str, int] = {}
    for d in model_directory().values():               # decision models too: removing takes them as well
        counts[d["endpoint"]] = counts.get(d["endpoint"], 0) + 1
    data = endpoints.load()
    rows = [{"name": n, "base": e["base"], "default": n == endpoints.default(),
             "kind": endpoints.kind(n, data), "hub": endpoints.hub(n, data),
             "key_set": bool(endpoints.key(n)), "models": counts.get(n, 0),
             "connect": connect.standing(e.get("connect"))}
            for n, e in data.items()]
    return {"ok": True, "errors": [], "endpoints": rows}


# ---- read-only JSON for the bench CLI (cli.py) -----------------------------
# What the dashboard shows as HTML, as data, so everything it does can be done
# headless. Each says no more than the page does: no key, no key hash.

def models_view() -> dict:
    """Every model on the bench, and every retired one, with its status."""
    directory, gone, status = model_directory(), retired(), model_status()
    rows = []
    for alias in sorted(set(directory) | set(gone)):
        d = directory.get(alias) or gone.get(alias) or {}
        label, _css, why = status.get(alias, ("", "", ""))
        rows.append({"alias": alias, "served": d.get("served", ""), "role": d.get("role", ""),
                     "kind": d.get("kind") or "chat", "endpoint": d.get("endpoint", ""),
                     "connection": catalog.connection_of(alias) if alias in directory else "",
                     "status": label, "why": why, "retired": alias not in directory,
                     **({"retired_why": gone[alias].get("why", "")} if alias in gone else {})})
    return {"ok": True, "errors": [], "models": rows}


def gateway_keys_view() -> dict:
    keep = ("hint", "collections", "rpm", "created", "last_used")
    return {"ok": True, "errors": [], "required": guard.keys_required(),
            "keys": [{"name": n, **{k: e.get(k) for k in keep}}
                     for n, e in sorted(guard.gateway_keys().items()) if isinstance(e, dict)]}


def status_view() -> dict:
    import client as model_client
    import scheduler
    queue, sched = onboard_queue(), scheduler.settings()
    try:
        default = endpoints.default()
    except RuntimeError:                               # a fresh install: no endpoint yet
        default = None
    return {"ok": True, "errors": [], "version": RELEASE, "gateway": gateway_url(), "data": str(DATA),
            "models": len(_chat_aliases()), "decision_models": len(decide.load()),
            "retired": len(retired()), "collections": len(bench_collections.load()),
            "endpoints": len(endpoints.load()), "default_endpoint": default,
            "client": model_client.chosen()[0], "scheduler": sched["mode"],
            # Every job and whether it runs: save_scheduler turns on any job it is not told is off.
            "scheduler_jobs": {n: sched["jobs"].get(n, True) for n in scheduler.JOBS},
            "auth": guard.auth_on(), "keys_required": guard.keys_required(),
            "onboarding": {"running": bool(queue.get("running")), "items": len(queue.get("items") or [])}}


def free_name(stem: str) -> str:
    """The first of stem, stem-2, stem-3... that is neither an endpoint nor a key
    already in the store: one provider can be several endpoints (a pasted key
    and a sign-in, or two accounts), each under its own name and key."""
    have, keys = endpoints.load(), endpoints.keys()
    return next(n for n in [stem] + [f"{stem}-{i}" for i in range(2, 1000)] if n not in have and n not in keys)


def save_endpoint(name: str, base: str, secret: str, who: str = "ui", extra: dict | None = None,
                  new: bool = False, kind: str = "", hub: str = "", admin: str = "",
                  test_only: bool = False, api: str = "") -> dict:
    """Add an endpoint, or change one's key. Tested before anything is saved;
    `test_only` (the form's Test button) stops there and saves nothing.

    The key goes where `llm keys set` puts it (the CLI's own keys.json), never
    into this repository, the change log or a response. It is sent to the base
    URL typed here, which is the point: that is the endpoint being added.
    `extra` is fields from Connect (metrics/connect.py): its `connect` record,
    its `kind`, `api`, `models_url`, and headers, which only a new endpoint
    takes. `new` (the Add form) refuses a name in use: Change key is how one
    is edited. `kind` (endpoints.KINDS), and `hub` (endpoints.HUBS) for a hub,
    say what it is; a new endpoint is written with a kind (a provider unless
    told), so nothing added here is left to endpoints.kind's inference. Left
    out, an existing endpoint's kind stays as it was. `admin`, a hub's admin
    key where it is not the API key (LiteLLM's master key, CLI Proxy API's
    management key), is kept beside it under endpoints.admin_name; nothing
    tests it here, since a refused CLI Proxy API key counts toward a ban.
    `api` (endpoints.APIS), on a new endpoint: what it speaks, when not chat
    completions; Anthropic's and Gemini's native APIs take their own key
    header, which the test sends too.
    """
    name, base, secret = (name or "").strip().lower(), (base or "").strip().rstrip("/"), (secret or "").strip()
    kind, hub, admin, api = (kind or "").strip(), (hub or "").strip(), (admin or "").strip(), (api or "").strip()
    data = endpoints.load()
    errors = []
    if api and (api not in endpoints.APIS or api == "responses"):    # responses: Connect sets it, with its sign-in
        errors.append(f"api: one of {', '.join(a for a in endpoints.APIS if a != 'responses')}")
    if kind and kind not in endpoints.KINDS:
        errors.append(f"kind: one of {', '.join(endpoints.KINDS)}")
    elif kind == "hub" and hub not in endpoints.HUBS:
        errors.append(f"hub: which hub it is, one of {', '.join(endpoints.HUBS)}")
    if not endpoints.NAME.match(name):
        errors.append("name: 2-32 lower-case letters, digits or '-', e.g. openrouter")
    elif new and name in data:
        errors.append(f"there is already an endpoint named {name}: give this one its own name, e.g."
                      f" {free_name(name)}, or use Change key on {name}")
    if not re.fullmatch(r"https?://[^\s\"'<>]+", base):
        errors.append("base URL must start http:// or https://, e.g. https://openrouter.ai/api/v1")
    on_it = [r["alias"] for r in endpoints.registry() if r["endpoint"] == name]
    if name in data and base != data[name]["base"].rstrip("/") and on_it:
        errors.append(f"{len(on_it)} model(s) are registered on {name} at its current URL;"
                      " a new URL would strand them. Add it under a new name instead")
    if errors:
        return {"ok": False, "errors": errors}
    entry = dict(data.get(name) or {"key": name, "kind": "provider"})
    extra = dict(extra or {})
    if entry.get("headers"):
        extra.pop("headers", None)                 # never rewrite an endpoint's own attribution headers
    if secret and "connect" not in extra:
        # A key typed over a sign-in replaces it: the old record would renew
        # with its stale refresh token and overwrite the typed key.
        entry.pop("connect", None)
    entry.update(extra, base=base)
    if api and new:
        entry.pop("api", None) if api == "chat" else entry.update(api=api)
    if kind:
        entry.pop("hub", None)
        entry.update(kind=kind, **({"hub": hub} if kind == "hub" else {}))
    secret = secret or endpoints.keys().get(entry["key"], "")
    listing = entry.get("models_url") or base + "/models"
    import urllib.error
    import urllib.request
    try:
        count = len(endpoints.list_models(entry, secret))
    except urllib.error.HTTPError as exc:
        return {"ok": False, "errors": [f"{listing} answered {exc.code}"
                                        + (" — check the key" if exc.code in (401, 403) else "")
                                        + "; nothing saved"]}
    except Exception as exc:                           # noqa: BLE001
        return {"ok": False, "errors": [f"could not list {listing} ({type(exc).__name__}:"
                                        f" {exc}); nothing saved"]}
    # A LISTING PROVES THE URL, NOT ALWAYS THE KEY. OpenRouter lists its
    # models to anyone, so a wrong key sails through the test above. Asked
    # once without a key: if that works too, say plainly the key is unproven.
    note = ""
    if secret:
        try:
            with urllib.request.urlopen(listing, timeout=15):
                note = (" It lists models without a key, so the key itself is only"
                        " checked by the first call to one of its models.")
        except Exception:                              # noqa: BLE001
            note = " The key was checked: the list needs it."
    if test_only:
        return {"ok": True, "errors": [], "served": count, "note": note.strip()}
    if secret and secret != endpoints.keys().get(entry["key"]):
        endpoints.set_key(entry["key"], secret)
    if admin and endpoints.kind(name, {name: entry}) == "hub":
        endpoints.set_key(endpoints.admin_name(entry), admin)
        note += f" The admin key is kept as {endpoints.admin_name(entry)}."
    data = dict(data)
    data[name] = entry
    _write_atomic(endpoints.FILE, _dump(data))
    _log_change(datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                who, "", "endpoint", None, f"{name} {base}")
    return {"ok": True, "errors": [], "served": count, "note": note.strip()}


def remove_endpoint(name: str, who: str = "ui", with_models: bool = False) -> dict:
    """Remove an endpoint. Its models go with it only when asked (`with_models`,
    once the dashboard has said how many): each is retired as by hand, so no
    evidence is lost and Restore brings it back once an endpoint of that name
    exists again. The default cannot be removed: make another the default first."""
    data = endpoints.load()
    on_it = sorted(a for a, d in model_directory().items() if d["endpoint"] == name)
    errors = ([f"no endpoint {name!r}"] if name not in data else
              ["the default endpoint cannot be removed: make another one the default first"]
              if name == endpoints.default() else
              [f"{len(on_it)} model(s) are on it ({', '.join(on_it)}): remove it with them, or retire them first"]
              if on_it and not with_models else [])
    if errors:
        return {"ok": False, "errors": errors}
    for i, alias in enumerate(on_it):
        r = remove_model(alias, f"its endpoint {name} was removed", who)
        if not r["ok"]:
            # Stop with the endpoint still there: the rest stay registered on it.
            return {"ok": False, "errors": [f"stopped at {alias} after retiring {i} of {len(on_it)};"
                                            f" {name} is still there"] + r["errors"]}
    data = {n: e for n, e in endpoints.load().items() if n != name}
    _write_atomic(endpoints.FILE, _dump(data))
    _log_change(datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                who, "", "endpoint removed", name, f"with {len(on_it)} model(s)" if on_it else None)
    gone = f"{len(on_it)} model(s) retired with it, restorable once an endpoint named {name} exists again; " \
        if on_it else ""
    return {"ok": True, "errors": [], "retired": on_it,
            "note": f"{gone}its key stays in the llm key store; `llm keys` lists it"}


def set_default(name: str, who: str = "ui") -> dict:
    """Make `name` the default endpoint: the one a model is taken to be on when
    nothing says otherwise, and the only one that cannot be removed.

    Models on the default are recorded by their bare served name, elsewhere as
    served@endpoint (endpoints.snapshot_key). So the records kept that way (the
    daily served check's snapshot, onboarding dismissals) are renamed in the
    same step: otherwise the next check would report every model on the old
    default as gone, and what was dismissed there would come back."""
    data = endpoints.load()
    old = endpoints.default()
    if name not in data:
        return {"ok": False, "errors": [f"no endpoint {name!r}"]}
    if name == old:
        return {"ok": True, "errors": [], "note": f"{name} is already the default"}

    def rekey(keys: dict) -> dict:
        out = {}
        for k, v in keys.items():
            served, _, ep = k.rpartition("@")
            if not served or ep not in data:       # bare (an "@" in a model id is not an endpoint)
                out[f"{k}@{old}"] = v
            else:
                out[served if ep == name else k] = v
        return out

    def snapshot(d: dict) -> None:
        for field in ("last_seen", "first_missing"):
            if isinstance(d.get(field), dict):
                d[field] = rekey(d[field])

    def dismissed(d: dict) -> None:
        new = rekey(d)
        d.clear()
        d.update(new)

    def move(d: dict) -> None:
        for e in d.values():
            e.pop("default", None)
        d[name]["default"] = True

    locked.update(DATA / "config" / "served-snapshot.json", snapshot)
    locked.update(DISMISSED_PATH, dismissed)
    locked.update(endpoints.FILE, move)                # last: the records already match it
    _log_change(datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                who, "", "default endpoint", old, name)
    return {"ok": True, "errors": [], "note": f"{name} is the default endpoint now (was {old})"}


# ---- Connect: sign in on a provider's page (metrics/connect.py) -------------

def connect_list() -> dict:
    """Every provider Connect knows: whether it can be used here, the name a
    new endpoint would get, and for those needing an app registration, the
    recipe and this install's own client id (Use your own app)."""
    own = connect.own_ids()
    rows = []
    for pid, p in connect.PROVIDERS.items():
        row = {"id": pid, "label": p["label"], "flow": p["flow"], "missing": connect.ready(pid),
               "suggest": connect_list_suggest(pid),
               # Signs in with a code, so from a browser on another machine too.
               "code": p["flow"] in ("device", "local") or bool(p.get("device"))}
        if p.get("register"):
            row.update(register=p["register"], own_id=own.get(pid, ""), shipped=bool(p.get("client_id")))
        rows.append(row)
    return {"ok": True, "errors": [], "providers": rows}


def _signed_in_as(name: str, provider: str) -> bool:
    return ((endpoints.load().get(name) or {}).get("connect") or {}).get("provider") == provider


def connect_list_suggest(provider: str) -> str:
    """The name a new sign-in gets unless the user types one. A local Ollama
    already added is offered again (adding it again refreshes it)."""
    local = connect.PROVIDERS[provider]["flow"] == "local"
    return provider if local and _signed_in_as(provider, provider) else free_name(provider)


def connect_local() -> dict:
    """Model servers running on this machine, at their usual ports or wherever
    their processes listen, that are not endpoints yet (connect.detect), each
    with the name it would be added under. Asked when the Endpoints page is
    shown, never in its render: a look takes a second or two. Never this
    install's ports, nor another one's defaults."""
    found = connect.detect(skip={PORT, gateway.PORT, 8122, 8123})
    for f in found:
        f["name"] = connect_list_suggest(f["provider"]) if f["provider"] else free_name(f["id"])
    return {"ok": True, "errors": [], "found": found}


def connect_app(provider: str, client_id: str) -> dict:
    """Use your own app: this install's client id for a provider that needs a
    registration, instead of the one Benchwarmers ships. Empty goes back to it."""
    try:
        connect.set_own_id(provider, client_id)
    except connect.ConnectError as exc:
        return {"ok": False, "errors": [str(exc)]}
    _log_change(datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "ui", "",
                "connect app", None, f"{provider} {'own' if client_id.strip() else 'shipped'}")
    return {"ok": True, "errors": []}


def connect_start(provider: str, endpoint: str = "", by_code: bool = False) -> dict:
    """A sign-in for an endpoint named `endpoint`: an existing one signed in to
    this provider is signed in again; a free name is a new endpoint (a provider
    can be several: a pasted key and a sign-in, two accounts). No name: the
    next free one, provider, provider-2... `by_code`: the browser is not on
    this machine, so no callback to it can arrive (metrics/connect.py)."""
    p = connect.PROVIDERS.get(provider)
    if not p:
        return {"ok": False, "errors": [f"no provider {provider!r}"]}
    endpoint = (endpoint or "").strip().lower() or connect_list_suggest(provider)
    if endpoint in endpoints.load():
        if not _signed_in_as(endpoint, provider):
            return {"ok": False, "errors": [f"{endpoint} is already an endpoint: give this one another name,"
                                            f" e.g. {free_name(endpoint)}"]}
    elif not endpoints.NAME.match(endpoint):
        return {"ok": False, "errors": ["name: 2-32 lower-case letters, digits or '-', e.g. " + free_name(provider)]}
    elif endpoint in endpoints.keys():
        # Never write over a key this install did not make for an endpoint.
        return {"ok": False, "errors": [f"a key named {endpoint} is already in the llm key store:"
                                        f" give this endpoint another name, e.g. {free_name(endpoint)}"]}
    if p["flow"] == "local":
        # Ollama ignores the key, but llm sends one, so the endpoint gets a stand-in.
        return connect_save({"provider": provider, "endpoint": endpoint, "secret": provider})
    try:
        return {"ok": True, "errors": [], **connect.start(provider, PORT, endpoint, by_code)}
    except connect.ConnectError as exc:
        return {"ok": False, "errors": [str(exc)]}


def connect_poll(sid: str) -> dict:
    res = connect.poll(sid)
    if res["state"] == "pending":
        return {"ok": True, "errors": [], "state": "pending"}
    if res["state"] == "error" and "error" in res:
        return {"ok": False, "errors": [res["error"]], "state": "error"}
    return connect_save(res) if "secret" in res else res    # device tokens, or a callback's outcome


def connect_save(tokens: dict) -> dict:
    """Save a sign-in as the endpoint it was started for, by save_endpoint's rules."""
    pid = tokens["provider"]
    name = tokens.get("endpoint") or pid
    p = connect.PROVIDERS[pid]
    key = (endpoints.load().get(name) or {}).get("key", name)
    with connect.renewal_lock(key):              # a renewal in flight finishes first, then this wins
        out = save_endpoint(name, p["base"], tokens["secret"], who="connect",
                            extra={**p.get("entry", {}), "connect": connect.entry_field(tokens)})
        if out["ok"] and tokens.get("refresh"):
            endpoints.set_key(key + ".refresh", tokens["refresh"])
    if not out["ok"] and p.get("hint"):
        out["errors"].append(p["hint"])
    if p["flow"] == "local":
        out.pop("note", None)                      # "the key is unproven" means nothing where none is needed
    return {**out, "state": "done" if out["ok"] else "error", "endpoint": name, "label": p["label"]}


def connect_page(out: dict) -> str:
    """What the sign-in tab shows when the provider sends it back here."""
    home = (guard.public_url() or f"http://localhost:{PORT}") + "/"
    if out.get("ok"):
        head, text = (f"{out['label']} is connected as {out['endpoint']}",
                      f"It lists {out.get('served', 0):,} models. Back in Benchwarmers, onboard the ones you want.")
    else:
        head, text = "Not connected", " ".join(out.get("errors") or ["the sign-in did not finish"])
    return (f"<!doctype html><meta charset='utf-8'><title>Benchwarmers</title>"
            f"<body style='font:16px system-ui;max-width:36rem;margin:4rem auto;padding:0 1rem'>"
            f"<h1>{html.escape(head)}</h1><p>{html.escape(text)}</p>"
            f"<p><a href='{html.escape(home)}'>Back to Benchwarmers</a>. You can close this tab.</p>")


def _clean_limits(raw, keys=catalog.LIMIT_KEYS) -> tuple[dict, list[str]]:
    """rpm/rpd/tpm/tpd (and a connection's rpmo) as positive whole numbers;
    blank clears one."""
    out, errors = {}, []
    for k in keys:
        v = (raw or {}).get(k)
        if v in (None, ""):
            continue
        if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
            errors.append(f"{k} must be a positive whole number or blank")
        else:
            out[k] = v
    return out, errors


def save_model_facts(alias: str, facts: dict, who: str = "ui") -> dict:
    """Your values for one model; each beats the catalog refresh. A blank (or
    null) clears your value so the refresh shows through again."""
    if alias not in model_directory() or model_directory()[alias].get("kind") == "decision":
        return {"ok": False, "errors": [f"{alias!r} is not a chat model on the bench"]}
    facts = facts if isinstance(facts, dict) else {}
    mine, errors = {}, []
    for k in ("context_window", "max_output"):
        v = facts.get(k)
        if v in (None, ""):
            continue
        if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
            errors.append(f"{k} must be a positive whole number of tokens, or blank")
        else:
            mine[k] = v
    for k in catalog.CAPS:
        if isinstance(facts.get(k), bool):
            mine[k] = facts[k]
    for k, cap in (("knowledge_cutoff", 20), ("description", 300)):
        v = " ".join(str(facts.get(k) or "").split())
        if len(v) > cap:
            errors.append(f"{k}: at most {cap} characters")
        elif v:
            mine[k] = v
    limits, bad = _clean_limits(facts.get("limits"))
    errors += bad
    if limits:
        mine["limits"] = limits
    if errors:
        return {"ok": False, "errors": errors}
    data = catalog._read(catalog.OVERRIDES)
    old = data.get(alias)
    if mine:
        data[alias] = mine
    else:
        data.pop(alias, None)
    _write_atomic(catalog.OVERRIDES, _dump(data))
    _log_change(datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                who, alias, "facts", old, mine or None)
    return {"ok": True, "errors": [], "changed": ["config/model-overrides.json"]}


def _llm_option(alias: str, name: str, value) -> str:
    """The llm CLI keeps its own copy of a model's options (sync_config.py
    sets them from the file), so a change here is made there too while it is
    the client (NOT_SYNCED); None clears it. The built-in client reads the
    file itself."""
    import shutil
    import client as model_client
    if model_client.chosen()[0] != "llm":
        return NOT_SYNCED
    if not shutil.which("llm"):            # found the way every llm call here finds it
        return "no llm CLI on this machine's PATH, so only the built-in client uses it"
    # `--`: whatever follows is an argument, never a flag, whatever the alias.
    args = ["set", "--", alias, name, str(value)] if value is not None else ["clear", "--", alias, name]
    try:
        done = subprocess.run(["llm", "models", "options", *args], capture_output=True, text=True,
                              timeout=30, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as exc:
        return f"the llm CLI was not told ({exc}): run scripts/sync_config.py"
    return ("the llm CLI has it too" if done.returncode == 0
            else f"llm models options exited {done.returncode}: {(done.stderr or done.stdout).strip()[:200]}")


def save_model_options(alias: str, max_tokens, who: str = "ui") -> dict:
    """How the bench calls one model (config/model-options.json): for now
    max_tokens, the most an answer may use. A reasoning model thinks out of it
    and answers with an empty string when it runs out. Blank hands the choice
    back to the endpoint."""
    if alias not in model_directory() or model_directory()[alias].get("kind") == "decision":
        return {"ok": False, "errors": [f"{alias!r} is not a chat model on the bench"]}
    if max_tokens in (None, ""):
        value = None
    elif isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= 1_000_000:
        return {"ok": False, "errors": ["max tokens: a whole number from 1 to 1,000,000, or blank"]}
    else:
        value, ceiling = max_tokens, catalog.info(alias).get("max_output")
        if isinstance(ceiling, int) and value > ceiling:
            return {"ok": False, "errors": [f"max tokens: at most this model's max output, {ceiling:,}"]}
    options = load_json(OPTIONS_PATH)
    old = dict(options.get(alias) or {})
    mine = {k: v for k, v in old.items() if k != "max_tokens"} | ({"max_tokens": value} if value else {})
    if mine == old:
        return {"ok": True, "errors": [], "note": "no change"}
    if mine:
        options[alias] = mine
    else:
        options.pop(alias, None)
    _write_atomic(OPTIONS_PATH, _dump(options))
    _log_change(datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                who, alias, "options", old or None, mine or None)
    return {"ok": True, "errors": [], "changed": ["config/model-options.json"],
            "note": _llm_option(alias, "max_tokens", value)}


def save_connection_limits(connection: str, limits: dict, who: str = "ui") -> dict:
    connection = (connection or "").strip()
    known = {catalog.connection_of(a) for a in _chat_aliases()}
    if connection not in known:
        return {"ok": False, "errors": [f"{connection!r} is not a connection any model uses;"
                                        f" known: {', '.join(sorted(known))}"]}
    clean, errors = _clean_limits(limits, catalog.CONNECTION_LIMIT_KEYS)
    if errors:
        return {"ok": False, "errors": errors}
    data = catalog._read(catalog.LIMITS)
    old = data.get(connection)
    if clean:
        data[connection] = clean
    else:
        data.pop(connection, None)
    _write_atomic(catalog.LIMITS, _dump(data))
    _log_change(datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                who, "", f"limits {connection}", old, clean or None)
    return {"ok": True, "errors": [], "changed": ["config/connection-limits.json"]}


def forget_connection_learned(connection: str, axis: str, who: str = "ui") -> dict:
    """Drop what the bench learned for one connection's axis (metrics/learn.py):
    its limit and its candidates, so the next 429 starts over."""
    import learn

    def drop(data: dict):
        axes = data.get(connection)
        old = axes.pop(axis, None) if isinstance(axes, dict) else None
        if axes == {}:
            del data[connection]
        return old
    old = locked.update(learn.CONN_LEARNED, drop, ensure_ascii=False)
    if old is None:
        return {"ok": False, "errors": [f"nothing is learned for {axis or '?'} on {connection or '?'}"]}
    _log_change(datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                who, "", f"learned limits {connection}", {axis: old}, None)
    return {"ok": True, "errors": [], "changed": ["config/connection-learned.json"]}


def connections_view() -> dict:
    """Each connection the chat models use, its models, its limits, and what
    the bench learned from its 429s (`learned`: enforced where you set none;
    `pending`: met once, not enforced). Without the 429's text: it can carry
    an account id."""
    import learn
    by = {}
    for a in _chat_aliases():
        by.setdefault(catalog.connection_of(a), []).append(a)
    lim = catalog.connection_limits()
    met, once = learn.connection_learned(), learn.connection_learned(pending=True)
    return {"ok": True, "errors": [], "keys": list(catalog.CONNECTION_LIMIT_KEYS),
            "labels": {k: catalog.LIMIT_LABELS[k] for k in catalog.CONNECTION_LIMIT_KEYS},
            "connections": [{"name": c, "models": sorted(m), "limits": lim.get(c, {}),
                             "learned": {k: {f: ev.get(f) for f in ("value", "at", "confirmed_at")}
                                         for k, ev in met.get(c, {}).items()},
                             "pending": once.get(c, {})}
                            for c, m in sorted(by.items())]}


def _chat_aliases() -> list[str]:
    return sorted(a for a, d in model_directory().items() if d.get("kind") != "decision")


def collections_view() -> dict:
    """Collections, gateway settings, and what a collection may hold. Kept
    between requests (kept): the page draws its list from it, then asks for
    it again for the editor. Shared: read it, never change it."""
    return kept("collections", _collections_view)


def _collections_view() -> dict:
    s = bench_collections.settings()
    rows = [{"name": n, **bench_collections.described(n, c, s)}
            for n, c in sorted(bench_collections.load().items())]
    shapes = [k for k in model_roles.load()["shapes"] if k != "image"]
    # Tags are read only by callers (GET /v1/models, and the refusal when a
    # request names no collection), to choose a collection for a task. The
    # suggestions: the bench's task shapes, a few purposes, and every tag a
    # collection already uses, so the same idea is spelled the same way.
    used = [t for r in rows for t in r.get("tags") or []]
    suggestions = list(dict.fromkeys(shapes + list(TAG_PURPOSES) + used))
    # The served name beside each alias: the member search matches either.
    directory = model_directory()
    served = {a: directory[a].get("served", "") for a in _chat_aliases()}
    # What the tags check members against (bench.js tagBlocks): capabilities
    # (None: the catalog does not say), context, price and role. A shape's
    # tag admits the roles that shape admits, as route.py does for seats.
    # The member picker draws one row per model, its providers as chips:
    # `family` is route.family_of, the match that keeps one model out of two
    # seats, and `provider` the chip's name for its connection.
    import route
    roles = model_roles.load()
    facts = {}
    for a in _chat_aliases():
        f = catalog.info(a)
        tag = directory[a].get("tag") or directory[a].get("endpoint") or ""
        facts[a] = {"caps": {c: f.get(c) for c in catalog.CAPS}, "context": f.get("context_window"),
                    "price": price_of(a, served[a], f), "role": (roles.get("models") or {}).get(a, "general"),
                    "family": route.family_of(a), "provider": provider_label(tag), "connection": tag}
    return {"ok": True, "errors": [], "collections": rows, "settings": s,
            "pick": bench_collections.pick_settings(), "confident": decide.CONFIDENT, "shapes": shapes, "models": _chat_aliases(), "served": served, "tag_suggestions": suggestions,
            "facts": facts, "shape_roles": {k: v for k, v in roles["shapes"].items() if k in shapes},
            "long_context": LONG_CONTEXT}


TAG_PURPOSES = ("second-opinion", "security", "long-context", "fast", "free", "tools", "json")
LONG_CONTEXT = 128_000          # the long-context tag: members known to hold at least this many tokens


def save_collection(name: str, members, shape: str, description: str, tags=None,
                    reply: str = "all", merger: str = "", ad_hoc: bool = False, who: str = "ui",
                    max_calls=None, max_explorers=None) -> dict:
    """Create or replace a collection. Every member must be a chat model on the
    bench (a decision model cannot answer a prompt), no more than max_calls.
    The description and tags are what an orchestrator reads on /v1/models to
    choose a collection, so they say what it is for. `ad_hoc` takes it off
    /v1/models: it runs only when a request names it. `max_calls` and
    `max_explorers`: its own limits; None follows the gateway settings."""
    # The name is what follows "bench/"; one typed with the prefix is taken as meant.
    name, shape = (name or "").strip().removeprefix(bench_collections.PREFIX), (shape or "").strip()
    description = " ".join((description or "").split())
    if isinstance(tags, str):
        tags = tags.split(",")
    tags = list(dict.fromkeys(t.strip().lower() for t in (tags or []) if isinstance(t, str) and t.strip()))
    own = {k: v for k, v in (("max_calls", max_calls), ("max_explorers", max_explorers)) if v is not None}
    s = bench_collections.limits(own)
    errors = [f"{k.replace('_', ' ')} must be a whole number from {bench_collections.BOUNDS[k][0]} to"
              f" {bench_collections.BOUNDS[k][1]}, or empty for the gateway setting"
              for k, v in own.items() if not bench_collections.limit_ok(k, v)]
    if not bench_collections.NAME.match(name):
        errors.append("name: 2-32 lower-case letters, digits or '-', saying what it is for,"
                      " e.g. reviewers; callers ask for bench/<name>")
    if not isinstance(members, list) or not all(isinstance(m, str) for m in members):
        members = []
    members = list(dict.fromkeys(m.strip() for m in members if m.strip()))
    known = set(_chat_aliases())
    if not members:
        errors.append("a collection needs at least one member")
    if len(members) > s["max_calls"]:
        errors.append(f"at most {s['max_calls']} members (max calls per request)")
    unknown = [m for m in members if m not in known]
    if unknown:
        errors.append(f"not chat models on the bench: {', '.join(unknown)}")
    shapes = [k for k in model_roles.load()["shapes"] if k != "image"]
    if shape and shape not in shapes:
        errors.append(f"shape must be one of {', '.join(shapes)}, or empty for automatic")
    if len(description) > 300:
        errors.append("description: at most 300 characters; say what the collection is for")
    bad = [t for t in tags if not bench_collections.TAG.match(t)]
    if bad:
        errors.append(f"tags are lower-case words joined by '-', up to 24 characters: {', '.join(bad)}")
    if len(tags) > 8:
        errors.append("at most 8 tags")
    reply, merger = (reply or "all").strip(), (merger or "").strip()
    errors += bench_collections.reply_errors(reply, merger, known)
    if bench_collections.unreadable(bench_collections.FILE):
        errors.append("config/collections.json is not valid JSON; fix it by hand first,"
                      " or this save would replace every collection in it")
    if errors:
        return {"ok": False, "errors": errors}
    data = bench_collections.load()
    old = data.get(name)
    data[name] = {"members": members, "shape": shape, "description": description, "tags": tags,
                  "reply": reply, "merger": merger, **({"ad_hoc": True} if ad_hoc else {}), **own}
    _write_atomic(bench_collections.FILE, _dump(data))
    _log_change(datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                who, "", f"collection {name}", old, data[name])
    return {"ok": True, "errors": [], "changed": ["config/collections.json"]}


def delete_collection(name: str, who: str = "ui") -> dict:
    if bench_collections.unreadable(bench_collections.FILE):
        return {"ok": False, "errors": ["config/collections.json is not valid JSON; fix it by hand first"]}
    data = bench_collections.load()
    if name not in data:
        return {"ok": False, "errors": [f"no collection {name!r}"]}
    old = data.pop(name)
    _write_atomic(bench_collections.FILE, _dump(data))
    _log_change(datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                who, "", f"collection {name}", old, None)
    return {"ok": True, "errors": [], "changed": ["config/collections.json"]}


KEEP = object()                                      # a setting the request did not send: left as it is


def save_gateway_settings(max_calls, max_explorers, who: str = "ui", pick_collection=KEEP,
                          pick_confidence=KEEP) -> dict:
    """The gateway's limits, and the collection pick for a request that names
    none (bench_collections.pick_settings): `pick_collection` true or false,
    `pick_confidence` above 0 and at most 1, or None for the default."""
    errors = []
    if not isinstance(max_calls, int) or isinstance(max_calls, bool) or not 1 <= max_calls <= 10:
        errors.append("max calls must be a whole number from 1 to 10")
    if not isinstance(max_explorers, int) or isinstance(max_explorers, bool) or not 0 <= max_explorers <= 2:
        errors.append("max explorers must be 0, 1 or 2 (route.py seats at most two)")
    if pick_collection is not KEEP and not isinstance(pick_collection, bool):
        errors.append("choosing a collection must be on or off")
    if pick_confidence is not KEEP and pick_confidence is not None and not (
            isinstance(pick_confidence, (int, float)) and not isinstance(pick_confidence, bool)
            and 0 < pick_confidence <= 1):
        errors.append(f"the confidence must be above 0 and at most 1, or empty for {decide.CONFIDENT}")
    too_big = [n for n, c in bench_collections.load().items()
               if isinstance(max_calls, int) and len(c.get("members", [])) > max_calls
               and "max_calls" not in bench_collections.own_limits(c)]
    if too_big:
        errors.append(f"these collections have more members than {max_calls}: {', '.join(too_big)}")
    if bench_collections.unreadable(bench_collections.SETTINGS_FILE):
        errors.append("config/gateway.json is not valid JSON; fix it by hand first")
    if errors:
        return {"ok": False, "errors": errors}
    raw = bench_collections._read(bench_collections.SETTINGS_FILE)
    old = {**bench_collections.settings(), **bench_collections.pick_settings()}
    raw.pop("default_collection", None)
    raw.update({"max_calls": max_calls, "max_explorers": max_explorers})
    if pick_collection is not KEEP:
        raw["pick_collection"] = pick_collection
    if pick_confidence is None:
        raw.pop("pick_confidence", None)                # empty: the default, decide.CONFIDENT
    elif pick_confidence is not KEEP:
        raw["pick_confidence"] = pick_confidence
    _write_atomic(bench_collections.SETTINGS_FILE, _dump(raw))
    _log_change(datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                who, "", "gateway settings", old, {**bench_collections.settings(), **bench_collections.pick_settings()})
    return {"ok": True, "errors": [], "changed": ["config/gateway.json"]}


# How much an onboarding spends. Full: the 4-call probe, then the suite now.
# Quick: the probe only; the scheduled `suite --next` passes fill the suite in,
# six tasks a run, so the same calls are spread out, not saved. Ad hoc: the
# bench never calls it on its own afterwards, for a metered, capped or trial
# key; Full + ad hoc spends the suite once now so routing has scores for it,
# Quick + ad hoc only the probe, and no scheduled pass ever fills its suite in
# (suite/pace.py leaves ad hoc models out). Apart from the mode,
# `vision` (the picker's Vision model box) gives it role vision as it is
# registered: a seat only for requests that carry images, and its suite is the
# vision tasks alone (suite/run.applies), now or through the scheduled passes.
ONBOARD_MODES = {"full": [], "full-adhoc": ["--ad-hoc"], "quick": ["--skip-suite"],
                 "quick-adhoc": ["--skip-suite", "--ad-hoc"],
                 "rerun": ["--rerun"]}             # one on the bench: its probe and whole suite again (a model's Run suite)


def onboard_choice(prefix: str) -> str:
    """The mode and the Vision box, as the Onboard dialog (o-) and Discover's Add confirm (ask-) show them."""
    return (f"<label class='check'>Onboard as <select id='{prefix}-mode'>"
            "<option value='full'>Full: probe, then the suite now</option>"
            "<option value='full-adhoc'>Full + ad hoc: probe and the suite now; then the bench never calls it"
            " on its own</option>"
            "<option value='quick'>Quick: probe now, the suite through the scheduled passes</option>"
            "<option value='quick-adhoc'>Quick + ad hoc: probe only; the bench never calls it on its own"
            " (metered, capped or trial keys)</option></select></label>"
            f"<label class='check'><input type='checkbox' id='{prefix}-vision'> Vision model: a seat only for"
            " questions about images, and its suite is the vision tasks</label>")


def start_onboard(served: str, endpoint: str = "", who: str = "ui", mode: str = "full",
                  vision: bool = False) -> dict:
    """Kick off `scripts/onboard.py` for a served model, detached, logging to runs/.

    The script does its own checks in the right order (is it served, derive
    the alias, register, probe, suite) and refuses at the first one that
    fails; this only refuses what can be known without a call — a name that is
    empty, has spaces, is already registered, or was retired from here — and
    hands the rest to the script. It runs for minutes and spends calls, so it
    runs in the background and the page tails its log.
    """
    return _spawn_onboard(served, endpoint, who, mode, vision)[0]


def _onboard_refusal(served: str, endpoint: str, mode: str = "full") -> list[str]:
    """What start_onboard refuses without a call (its docstring)."""
    errors = [] if mode in ONBOARD_MODES else [f"mode must be one of {', '.join(ONBOARD_MODES)}; got {mode!r}"]
    # It goes into a shell command line below, so only the characters model
    # ids actually use -- not merely "no spaces".
    if not re.fullmatch(r"[\w./:@+-]{1,200}", served):
        errors.append("the served name is the id the endpoint lists, e.g. vendor/model:free"
                      " — letters, digits and . / : @ + - only")
    if endpoint not in endpoints.load():
        errors.append(f"{endpoint!r} is not an endpoint in config/endpoints.json")
    on_it = {r["served"]: r["alias"] for r in endpoints.registry() if r["endpoint"] == endpoint}
    if mode == "rerun":                                # the opposite: it must be on the bench, as a chat model
        return errors + ([] if served in on_it else [f"{served} is not on the bench from {endpoint}"])
    on_it.update({d["served"]: a for a, d in decide.load().items() if d["endpoint"] == endpoint})
    if served in on_it or (endpoint == endpoints.default() and served in registered()):
        errors.append(f"{served} is already registered as"
                      f" {on_it.get(served) or registered()[served]!r}")
    retired = load_json(RETIRED_PATH)
    hit = next((a for a, r in retired.items() if r.get("served") == served
                and (r.get("endpoint") or endpoints.default()) == endpoint), None)
    if hit:
        errors.append(f"{served} was retired from the bench as {hit!r} on {retired[hit]['at'][:10]}"
                      f" ({retired[hit]['why']}); remove that entry from config/retired.json"
                      " first if you mean it")
    return errors


def _spawn_onboard(served: str, endpoint: str = "", who: str = "ui",
                   mode: str = "full", vision: bool = False) -> tuple[dict, subprocess.Popen | None]:
    """start_onboard's result, and the process (None when refused) for a caller that waits on it."""
    served = (served or "").strip()
    endpoint = (endpoint or "").strip() or endpoints.default()
    errors = _onboard_refusal(served, endpoint, mode)
    if errors:
        return {"ok": False, "errors": errors}, None
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", served)
    log = DATA / "runs" / f"onboard-{stamp}-{safe}.log"
    from run import bench_python
    cmd = [bench_python(), str(ROOT / "scripts" / "onboard.py"), served, "--endpoint", endpoint,
           *ONBOARD_MODES[mode], *(["--vision"] if vision else [])]
    # The log handle goes straight to the child, unbuffered, so the page sees
    # each step as it prints. The old `cmd /c ... > log & echo` wrapper with
    # DETACHED_PROCESS sent the output to a console window instead, so every
    # log held only the sentinel and the modal never moved past step 1.
    out = open(log, "w", encoding="utf-8")
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    # start_new_session: the POSIX counterpart of a new process group, so the
    # child is not tied to this server's terminal.
    proc = subprocess.Popen(cmd, cwd=str(ROOT), creationflags=flags, stdin=subprocess.DEVNULL,
                            stdout=out, stderr=subprocess.STDOUT, start_new_session=os.name != "nt",
                            env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
                                 "BENCH_ONBOARD_LOG": log.name})     # named in onboard-issues.json

    def sentinel() -> None:
        # The process outlives this request and nothing else reports its exit.
        # ponytail: lost if serve.py restarts mid-run; the log still has the steps.
        code = proc.wait()
        out.write(f"__ONBOARD_EXIT__ {code}\n")
        out.close()

    threading.Thread(target=sentinel, daemon=True).start()
    _log_change(datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                who, "", "onboard" + ("" if mode == "full" else f" ({mode})"), None, served)
    return {"ok": True, "errors": [], "log": log.name}, proc


# ---- the onboarding queue ---------------------------------------------------
# The picker's models, and Discover's Add, run ONE AT A TIME, each a 4-call
# probe and a suite: a burst on one free tier gets its key throttled. A thread
# of this server runs them, so closing the popup or reloading the page stops
# nothing; the sidebar shows progress from GET /api/onboard-queue. Only
# POST /api/onboard (start_onboard) still starts one at once, for scripts.
# ponytail: in memory; restarting serve.py drops the rest of a batch (each
# started model's log stays in runs/). Persist it if restarts mid-batch matter.
OQ_MAX = 50
_OQ_LOCK = threading.Lock()
_OQ: dict = {"items": [], "stop": False, "worker": None}


def onboard_queue() -> dict:
    """The batch: each model's state (queued, running, done, no answer,
    failed, skipped) and whether it is still running. The running model also
    has `step`, the last of onboard.py's five steps its log has reached (0
    before the first), so the sidebar's chip can fill by fifths of a model."""
    with _OQ_LOCK:
        running = _OQ["worker"] is not None
        out = {"ok": True, "errors": [], "running": running, "stopping": running and _OQ["stop"],
               "items": [dict(i) for i in _OQ["items"]]}
    for i in out["items"]:
        if i["state"] == "running":
            text = read(DATA / "runs" / i["log"]) if i["log"] else ""
            i["step"] = max((int(s) for s in re.findall(r"\[([1-5])/5\]", text)), default=0)
            at = re.findall(r"^\s+suite (\d+)/(\d+|\?): (.*)$", text, re.M)    # step 5: the task it is on
            i["suite"] = list(at[-1]) if at else None
    return out


def queue_onboard(items, who: str = "ui") -> dict:
    """Add models to the queue, refused as a whole if any would be refused
    alone (_onboard_refusal) or is already waiting. A new batch replaces a
    finished one's results; a running one is added to."""
    if not isinstance(items, list) or not 1 <= len(items) <= OQ_MAX:
        return {"ok": False, "errors": [f"pick between 1 and {OQ_MAX} models"]}
    picked, errors = [], []
    for it in items:
        it = it if isinstance(it, dict) else {}
        served = str(it.get("served") or "").strip()
        endpoint = str(it.get("endpoint") or "").strip() or endpoints.default()
        mode = str(it.get("mode") or "full")
        errors += _onboard_refusal(served, endpoint, mode)
        picked.append((served, endpoint, mode, it.get("vision") is True))
    with _OQ_LOCK:
        waiting = {(i["served"], i["endpoint"]) for i in _OQ["items"] if i["state"] in ("queued", "running")}
        errors += [f"{s} from {e} is already in the queue" for s, e, *_ in picked if (s, e) in waiting]
        if len({(s, e) for s, e, *_ in picked}) < len(picked):
            errors.append("a model is picked twice")
        if errors:
            return {"ok": False, "errors": errors}
        if _OQ["worker"] is None:
            _OQ["items"] = []
        _OQ["stop"] = False                              # added after a Stop: these run
        _OQ["items"] += [{"served": s, "endpoint": e, "mode": m, "vision": v, "state": "queued", "alias": None,
                          "log": None, "exit": None, "note": ""} for s, e, m, v in picked]
        if _OQ["worker"] is None:
            _OQ["worker"] = threading.Thread(target=_onboard_worker, args=(who,), daemon=True)
            _OQ["worker"].start()
    return onboard_queue()


def stop_onboard_queue() -> dict:
    """The model running now finishes; the ones still queued are skipped, now,
    so a model picked after the Stop still runs."""
    with _OQ_LOCK:
        if _OQ["worker"] is None:
            return {"ok": False, "errors": ["no onboarding is running"]}
        for i in _OQ["items"]:
            if i["state"] == "queued":
                i.update(state="skipped", note="stopped before its turn")
        _OQ["stop"] = True                               # shown as "Stopping after …" until it ends
    return onboard_queue()


def clear_onboard_queue() -> dict:
    """Forget a finished batch's results (the logs stay in runs/)."""
    with _OQ_LOCK:
        if _OQ["worker"] is not None:
            return {"ok": False, "errors": ["still running: Stop it first, or wait"]}
        _OQ["items"] = []
    return onboard_queue()


def _onboard_outcome(code: int, text: str) -> tuple[str, str]:
    """(state, note) from onboard.py's exit and log."""
    if code == 0 and "STOPPING" not in text:
        return "done", ""
    body = text.rsplit("__ONBOARD_EXIT__", 1)[0].strip()
    at = body.find("STOPPING")                     # why it stopped, else the last lines it printed
    why = body[at:].splitlines()[:2] if at != -1 else body.splitlines()[-3:]
    # The probe's own reason (onboard.py prints "tiny: capacity — ...") says more than STOPPING.
    reason = re.search(r"^\s+(?:tiny|large): (.+)$", body, re.M)
    if code == 0 and reason:
        why = [reason.group(1)]
    return ("no answer" if code == 0 else "failed"), " ".join(x.strip() for x in why)[:300]


def _onboard_worker(who: str) -> None:
    while True:
        with _OQ_LOCK:
            nxt = next((i for i in _OQ["items"] if i["state"] == "queued"), None)
            if nxt is None:
                _OQ.update(worker=None, stop=False)      # under the lock: a batch added now starts its own
                return
            nxt["state"] = "running"
        try:
            res, proc = _spawn_onboard(nxt["served"], nxt["endpoint"], who, nxt.get("mode", "full"),
                                       nxt.get("vision", False))
            if proc is None:                             # refused now: the bench changed since it was queued
                state, note, code = "failed", "; ".join(res["errors"]), None
            else:
                with _OQ_LOCK:
                    nxt["log"] = res["log"]
                code = proc.wait()
                text = read(DATA / "runs" / res["log"])
                alias = re.search(r"(?:->\s+alias|already on the bench as)\s+(\S+)", text)
                with _OQ_LOCK:
                    nxt["alias"] = alias.group(1) if alias else None
                state, note = _onboard_outcome(code, text)
        except Exception as exc:                         # noqa: BLE001 -- one model must not end the batch
            state, note, code = "failed", f"{type(exc).__name__}: {exc}", None
        with _OQ_LOCK:
            nxt.update(state=state, note=note, exit=code)


def discover_add(connection: str, model: str, who: str = "ui", endpoint: str = "",
                 mode: str = "full", vision: bool = False) -> dict:
    """From the Discover tab: put a model into a hub as it is otherwise done by
    hand, through the adapter of the hub `endpoint` is (endpoints.adapter:
    Open WebUI's connection Model IDs and a public, tagged record with a logo;
    a LiteLLM deployment; a CLI Proxy API provider's models list), then the
    normal onboarding, in the mode and with the Vision box the confirm chose
    (ONBOARD_MODES). Only for a connection the hub already has. No endpoint
    means Open WebUI's, as before there were other hubs."""
    if mode not in ONBOARD_MODES or mode == "rerun":   # before the hub has it, not after
        return {"ok": False, "errors": [f"mode must be one of {', '.join(ONBOARD_MODES)}; got {mode!r}"]}
    if not re.fullmatch(r"[\w./:@+-]{1,200}", model or ""):
        return {"ok": False, "errors": ["the model id is the provider's id, e.g. vendor/model:free"]}
    if not re.fullmatch(r"[\w.-]{1,80}", connection or ""):
        return {"ok": False, "errors": ["choose a connection"]}
    if endpoint and endpoint not in endpoints.load():
        return {"ok": False, "errors": [f"no endpoint {endpoint!r}"]}
    which = endpoints.hub(endpoint) if endpoint else "openwebui"
    if not which:
        return {"ok": False, "errors": [f"{endpoint} is not a hub; add the model on it from the Onboard dialog"]}
    hub, label = endpoints.adapter(which), endpoints.HUBS[which]
    try:
        served, steps = hub.add(connection, model)
    except ValueError as exc:
        return {"ok": False, "errors": [str(exc)]}
    except Exception as exc:                           # noqa: BLE001
        return {"ok": False, "errors": [f"{label} refused: {type(exc).__name__}: {exc}"]}
    _log_change(datetime.datetime.now().astimezone().isoformat(timespec="seconds"), who, "",
                f"{which}-add", None, f"{served} on {connection}")
    started = queue_onboard([{"served": served, "endpoint": hub.endpoint(), "mode": mode, "vision": vision}], who)
    return {**started, "served": served, "endpoint": hub.endpoint(), "steps": steps,
            "errors": started["errors"] + ([] if started["ok"] else
                                          [f"{label} now has {served}; onboard it from the Onboard dialog"])}


def onboard_status(log_name: str) -> dict:
    """The tail of an onboarding log, and whether it has finished."""
    if not re.match(r"^onboard-\d{8}T\d{6}-[A-Za-z0-9._-]+\.log$", log_name or ""):
        return {"ok": False, "errors": ["not an onboarding log name"]}
    text = read(DATA / "runs" / log_name)
    done = re.search(r"__ONBOARD_EXIT__ (\d+)\s*$", text)
    tail = text.rsplit("__ONBOARD_EXIT__", 1)[0][-6000:]
    return {"ok": True, "done": bool(done), "exit": int(done.group(1)) if done else None,
            "tail": tail}


def card(title: str, subtitle: str, body: str, key: str, facets: str = "") -> str:
    """A tab's panel: filter and retired toggle above, the key folded below.

    The key used to sit open beside every table. It is still one click away,
    because these numbers each mean something narrower than their names."""
    return (f"<div class='panel'><h2>{title}</h2><p class='sub'>{subtitle}</p>"
            "<div class='toolbar'><input type='search' data-filter"
            " placeholder='Filter by model or connection' aria-label='Filter rows'>"
            f"{facets}<label><input type='checkbox' data-retired> Show retired</label></div>"
            f"<div class='scroll'>{body}</div>"
            f"<details class='key'><summary>How to read this</summary>{key}</details></div>")


def th(label: str, num: bool = False) -> str:
    return f"<th data-sortable tabindex='0'{' class=num' if num else ''}>{label}</th>"


def ext_link(url: str, text: str) -> str:
    return f"<a href='{esc(url)}' target='_blank' rel='noopener'>{esc(text)}</a>"


def meter(share: float, text: str, low: bool) -> str:
    """A share as a bar beside its figure (Uptime, Confidently wrong), so how
    much reads at a glance and not by colour alone."""
    return (f"<span class='helped'><span class='meter'><span{' class=low' if low else ''}"
            f" style='width:{max(4, min(100, round(share * 100)))}%'></span></span>"
            f"<span class='mono'>{text}</span></span>")


STATUS = {
    # failure class -> (chip label, chip class, plain reading)
    "capacity": ("throttled", "warn", "rate limit or quota"),
    "timeout": ("slow", "warn", "no reply within the budget"),
    "empty": ("empty reply", "warn", "answered nothing; usually an output budget spent thinking"),
    "structural": ("not found", "err", "no such model, or no provider behind it"),
    "paywalled": ("no longer free", "err", "the free tier moved behind a paid slug"),
    "entitlement": ("no access", "err", "payment, credit or permission refused"),
    "signin": ("sign in again", "warn", "the key was refused: expired or revoked; fix it on the Endpoints page"),
    "other": ("failing", "err", "an error nothing here recognises"),
}


def model_status() -> dict[str, tuple[str, str, str]]:
    """alias -> (label, chip class, why), for every registered or retired model.

    One vocabulary for the whole page, so the roster, the problems and the
    editor never describe the same model two ways. Worked out once a render
    (four panes ask), each caller given its own copy (the values are tuples)."""
    return dict(locked.once(("model_status",), _model_status))


def _model_status() -> dict[str, tuple[str, str, str]]:
    alerts = load_json(DATA / "metrics" / "alerts.json")
    pending = {a.get("alias"): a for k, a in alerts.items()
               if k.startswith("model:pending:")}
    missing = load_json(DATA / "config" / "served-snapshot.json").get("first_missing", {})
    latest: dict[str, dict] = {}
    for row in perf_rows():
        latest[row["model"]] = row
    out = {alias: ("retired", "", r.get("why", "")) for alias, r in retired().items()}
    # A bare snapshot key is a model on the default endpoint (none on a fresh install).
    default = endpoints.default() if endpoints.load() else ""
    where = "Open WebUI" if default and endpoints.hub(default) == "openwebui" else default
    for served, alias in registered().items():
        row = latest.get(alias)
        if alias in pending:
            days = re.search(r"in (\d+) day", pending[alias].get("message", ""))
            out[alias] = (f"retiring in {days.group(1)}d" if days else "retiring",
                          "err", pending[alias].get("action", ""))
        elif served in missing:
            out[alias] = ("not served", "err", f"missing from {where} since {missing[served]}")
        elif row is None:
            out[alias] = ("untested", "", "no probe yet")
        elif int(row.get("answered") or 0) > 0:
            out[alias] = ("healthy", "ok", "answered its last probe")
        else:
            cls = row.get("failure") or ("timeout" if int(row.get("timeout") or 0) else "other")
            label, css, reading = STATUS.get(cls, STATUS["other"])
            out[alias] = (label, css, row.get("detail") or reading)
    return out


ONBOARD_ISSUES_PATH = DATA / "metrics" / "onboard-issues.json"


def onboard_issues(status: dict | None = None) -> dict[str, dict]:
    """alias -> how its onboarding stopped (scripts/onboard.py writes these:
    at, step, failure, detail, log), for each model still registered that has
    not answered a probe since. One that answers later drops out by itself;
    the file keeps the record."""
    status = status if status is not None else model_status()
    names = set(registered().values())
    out = {}
    for alias, issue in load_json(ONBOARD_ISSUES_PATH).items():
        alias = current_alias(alias)                 # renamed since: it follows
        if alias in names and status.get(alias, ("", "", ""))[1] != "ok" and isinstance(issue, dict):
            label, css, reading = STATUS.get(issue.get("failure") or "other", STATUS["other"])
            out[alias] = {**issue, "label": label, "css": css, "reading": reading}
    return out


def recently_onboarded(days: int = 7) -> set[str]:
    cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    names = registered()
    out = set()
    for line in read(CHANGES_PATH).splitlines():
        try:
            c = json.loads(line)
        except ValueError:
            continue
        if c.get("field") == "onboard" and c.get("at", "") >= cutoff and c.get("new") in names:
            out.add(names[c["new"]])
    return out


def chip(label: str, css: str, why: str = "") -> str:
    return (f"<span class='chip {css}'" + (f" title='{esc(why)}'" if why else "")
            + f">{esc(label)}</span>")


# The empty bench (ui/UI.md, Empty state): three seats on a bench, empty; pointing at
# the action beside it takes one (bench.css, motion 4). A tick, drawn (bench.js TICK).
BENCH_ART = "<div class='bench-art' aria-hidden='true'><i></i><i></i><i></i></div>"
TICK = ("<svg class='tick' viewBox='0 0 20 20' aria-hidden='true'><circle cx='10' cy='10' r='8'/>"
        "<path d='M5.8 10.4l2.9 2.8 5.6-6'/></svg>")
# First run's steps (RELEASE-PLAN Phase 4: init's steps, in the dashboard), and what each is.
FIRST_STEPS = (("endpoint", "An endpoint", "where models are served from: a provider, a hub or a local runtime"),
               ("onboard", "A model on the bench", "picked from what an endpoint serves, probed and run through"
                                                   " the suite"),
               ("collection", "A collection", "the panel of models an app asks for, bench/core to begin"),
               ("app", "An app", "pointed at the gateway: <a href='/clients' data-go='clients'>Clients</a> has"
                                 " each one's set-up"))


def first_step() -> tuple[str, str, str, str] | None:
    """A fresh install's next step (its key, heading, button and what it does),
    until it has an endpoint, a model on the bench and a collection; None then.
    Each button opens what every install uses (bench.js firstStep)."""
    if not endpoints.load():
        return ("endpoint", "Add your first endpoint", "Add an endpoint",
                "Where models are served from: sign in with a provider, or paste an API key for any"
                " OpenAI-compatible API. Then onboard a model from it.")
    if not registered() and not decide.load():
        return ("onboard", "Onboard your first model", "Onboard a model",
                "Pick a model your endpoints serve. Onboarding registers it, probes it (4 calls) and runs"
                " the internal suite, so it takes minutes and spends calls.")
    if not bench_collections.load():
        return ("collection", "Make your first collection", "Make bench/core",
                "The models a panel always asks, and what it is for. It opens filled in as bench/core, the"
                " name the set-ups on Clients use, with the models you onboarded ticked: change anything,"
                f" then save. Callers ask for it through the gateway at {esc(gateway_url())}.")
    return None


def step_button() -> str:
    """First run's next step as a button, the action of a page that is empty until it is done."""
    s = first_step()
    return f"<button class='btn primary' type='button' data-first='{s[0]}'>{s[2]}</button>" if s else ""


def empty_state(say: str, action: str = "", more: str = "") -> str:
    """An empty page (ui/UI.md): the empty bench, one sentence, the action that fills it, what that does."""
    return (f"<div class='empty-bench'>{BENCH_ART}<p class='say'>{say}</p>{action}"
            + (f"<p class='sub'>{more}</p>" if more else "") + "</div>")


def first_run() -> str:
    """A fresh install's home page, in Benchmarks' place until the install is
    set up: what Benchwarmers is, the next step and the one button that takes
    it, and the steps as a list that ticks off. "" once it is set up."""
    step = first_step()
    if not step:
        return ""
    key, head, label, text = step
    at = [k for k, _, _ in FIRST_STEPS].index(key)
    steps = "".join(f"<li class='{'done' if i < at else 'now' if i == at else ''}'>{TICK if i < at else ''}"
                    f"{name}<small>{what}</small></li>" for i, (_, name, what) in enumerate(FIRST_STEPS))
    return (f"<section class='first' id='first-run' data-step='{key}' aria-labelledby='first-head'>"
            f"<div class='empty-bench'>{BENCH_ART}<p class='say'>Benchwarmers is a self-hosted panel gateway: an"
            " app asks one of your collections, and every model on it answers.</p>"
            f"<h2 id='first-head'>{head}</h2><p class='sub'>{text}</p>"
            f"<button class='btn primary' type='button' id='first-go' data-first='{key}'>{label}</button></div>"
            f"<ol class='steps' aria-label='Setting up'>{steps}</ol></section>")


def said(detail: str) -> tuple[str, str]:
    """An error as one short line, and the link the provider put in it: the
    status code and the provider's own words, without the JSON around them or
    a lead-in such as "Too many requests." that says less than the sentence
    after it, ending where the link it gives begins (the link follows it on
    the page). Cut to a line by the page, not here: a phone shows it whole.
    The whole detail stays in the row's title."""
    code = re.search(r"Error code: (\w+)", detail)
    quoted = re.search(r"""['"](?:message|detail)['"]: ['"](.+?)(?:['"]\s*[,}]|$)""", detail)
    text = (quoted.group(1) if quoted else re.sub(r"^.*?Error: ", "", detail) if "Error: " in detail
            else re.sub(r"^\w+: ", "", detail))
    text = " ".join(text.replace("<<", "").replace(">>", "").split())
    first, _, rest = text.partition(". ")
    if rest and len(first) < 25:
        text = rest
    url = re.search(r"https?://[^\s;,'\"()]+", quoted.group(1) if quoted else "")
    if url and url.group(0) in text:
        text = text[:text.index(url.group(0))].rstrip()
    text = (f"{code.group(1)} · " if code else "") + text
    return (text if len(text) <= 400 else text[:399].rstrip() + "…"), (url.group(0) if url else "")


def breakable(name: str) -> str:
    """An escaped name that may wrap at a domain's dots (`…-api.unorouter.com`),
    never inside a version number (`llama-3.2`)."""
    return re.sub(r"\.(?=[a-z])", ".<wbr>", name)


def open_actions() -> tuple[dict[str, dict], list[tuple[str, dict]]]:
    """What needs a person, by the connection it comes from ({connection:
    {"held": [...], "rows": [...]}}, the soonest to retire first), and the
    last week's automatic retirements, newest first.

    Rate limits and timeouts are not listed: they resolve themselves, or count
    toward the 7-day backstop and appear here as `retiring` when that nears.
    Automatic retirements ARE listed, with Restore, because a decision taken
    without a person should be easy for one to undo.

    Once a render (the rail's count and Benchmarks' facts ask): read it, never change it."""
    return locked.once(("open_actions",), _open_actions)


def _open_actions() -> tuple[dict[str, dict], list[tuple[str, dict]]]:
    alerts = load_json(DATA / "metrics" / "alerts.json")
    names = registered()
    live = set(names.values())
    status = model_status()
    groups: dict[str, dict] = {}

    def group(conn: str) -> dict:
        return groups.setdefault(conn or "?", {"held": [], "rows": {}})

    def row(alias: str, key: str, a: dict) -> dict:
        # The roster's words for its state, so the two pages agree; the
        # alert's own evidence for a countdown, the latest probe otherwise.
        label, css, latest = status.get(alias, (a.get("kind", "?"), "warn", ""))
        days = re.search(r"in (\d+)d", label)
        detail = a.get("action", "") if a.get("kind") == "retiring" else latest or a.get("message", "")
        text, url = said(detail)
        return {"alias": alias, "key": key, "label": label, "css": "warn" if days and int(days.group(1)) > 2 else css,
                "days": int(days.group(1)) if days else 99, "said": text, "url": url, "title": detail,
                "why": a.get("message", "")}

    for key, a in alerts.items():
        served = key.split(":", 2)[2] if key.count(":") >= 2 else ""
        alias = a.get("alias") or names.get(served, "")
        # The alerts file is rewritten by the daily check; until then an alert
        # whose models have all left the bench (Retire all, or one by one) has
        # nothing left to act on.
        if (a.get("aliases") and not live & set(a["aliases"])) or (alias and alias not in live):
            continue
        if a.get("kind") == "connection":
            g = group(served)
            text, url = said(a.get("action", ""))
            g["held"].append({"said": text, "url": url, "title": a.get("action", "")})
            for held in a.get("aliases", []):
                if held in live:
                    g["rows"].setdefault(held, row(held, key, a))
        elif alias:                                    # its own alert says more than a held connection's list
            group((tag_for(alias) or a.get("endpoint") or "?").split("/")[0])["rows"][alias] = row(alias, key, a)
        else:                                          # a notice about no one model (NVIDIA's limits)
            group("Notices")["rows"][key] = {"alias": "", "key": key, "label": a.get("kind", "?"), "css": "warn",
                                             "days": 99, "said": a.get("message", ""), "url": "",
                                             "title": a.get("action", ""), "dismiss": a.get("dismiss")}
    for g in groups.values():
        g["rows"] = sorted(g["rows"].values(), key=lambda r: (r["days"], r["alias"]))
    order = sorted(groups.items(), key=lambda kv: (kv[0] == "Notices", min([r["days"] for r in kv[1]["rows"]] or [99]),
                                                   -len(kv[1]["rows"]), kv[0]))
    cutoff = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
    auto = [(alias, r) for alias, r in sorted(retired().items(), key=lambda kv: kv[1].get("at", ""), reverse=True)
            if r.get("by") == "auto" and r.get("at", "") >= cutoff]
    return dict(order), auto


def open_count(groups: dict) -> int:
    """Models and connections waiting on a person: the sidebar's count."""
    return sum(len(g["rows"]) + len(g["held"]) for g in groups.values())


FOLD_AT = 8                    # Action needed: a group's items past this many fold away


def actions_page(groups: dict, auto: list) -> str:
    """Action needed, its own page since 2026-11-02: it sat above every page
    and grew to a screen and more of red. Grouped by what to do (open_actions
    has it by connection): a connection whose every model fails, which is its
    key, credit or prefix, with its models under it; a key to sign in again;
    models to retire or retry; models to retry; notices. Each item its name,
    its state, its actions at the right and its reason, in the provider's own
    words, under them; each counted in the rail (open_count). The week's
    automatic retirements are folded, each with Restore."""
    def link(url: str) -> str:
        if not url:
            return ""
        shown = re.sub(r"^https?://", "", url).split("?")[0]
        return " " + ext_link(url, shown + " ↗")

    def item(what: str, acts: str, why: str, title: str = "", more: str = "", counted: bool = True) -> str:
        return (f"<li data-row{' data-item' * counted}><span class='what'>{what}</span><span class='acts'>{acts}</span>"
                f"<p class='why'" + (f" title='{esc(title)}'" if title else "") + f">{why}</p>{more}</li>")

    def name(alias: str) -> str:
        return f"<a href='#' class='model' data-alias='{esc(alias)}'><span class='alias'>{breakable(esc(alias))}</span></a>"

    def model(r: dict) -> str:
        if not r["alias"]:                             # a notice: what it says, and Got it if it goes once seen
            return item(f"{esc(r['said'])} {chip(r['label'], r['css'])}",
                        f"<button class='linkish' data-act='dismiss' data-key='{esc(r['key'])}'>Got it</button>"
                        if r.get("dismiss") else "", esc(r["title"]))
        alias = esc(r["alias"])
        signin = (f"<a href='/endpoints' data-go='endpoints'>Sign in again</a>"
                  if r["label"] == STATUS["signin"][0] else "")
        return item(f"{name(r['alias'])} {chip(r['label'], r['css'])}",
                    signin + f"<button class='linkish' data-act='retry' data-alias='{alias}'"
                    " title='One call now; an answer clears the alert'>Retry</button>"
                    + ("" if signin else f"<button class='linkish danger' data-act='retire' data-alias='{alias}'"
                                         f" data-why='{esc(r['why'] or r['label'])}'>Retire</button>"),
                    esc(r["said"]) + link(r["url"]), r["title"])

    def both(aliases: list, why: str, retire: bool = True) -> str:
        """Retry all and Retire all, over two or more models."""
        if len(aliases) < 2:
            return ""
        return (f"<button class='btn small' data-act='retry' data-aliases='{esc(' '.join(aliases))}'"
                f" title='One call each'>Retry all {len(aliases)}</button>"
                + (f"<button class='btn small danger' data-act='retire' data-aliases='{esc(' '.join(aliases))}'"
                   f" data-why='{esc(why)}'>Retire all {len(aliases)}</button>" if retire else ""))

    def group(title: str, sub: str, items: list, acts: str = "", count: str = "") -> str:
        rest = items[FOLD_AT:]
        return (f"<section class='panel'><header data-row><h2>{title} · {count or len(items)}</h2><span class='why'></span>"
                f"<span class='acts'>{acts}</span></header><p class='sub'>{sub}</p>"
                f"<ul class='items'>{''.join(items[:FOLD_AT])}</ul>"
                + (f"<details class='more'><summary>{len(rest)} more</summary><ul class='items'>{''.join(rest)}</ul>"
                   "</details>" if rest else "") + "</section>")

    todo: dict[str, list] = {"held": [], "signin": [], "retire": [], "retry": [], "notices": []}
    held = [0, 0]                                      # the rail counts a held connection and each of its models
    for conn, g in groups.items():
        if g["held"]:                                  # one cause for every model on it: the connection, its models under it
            held[0] += len(g["held"])
            held[1] += len(g["rows"])
            aliases = [r["alias"] for r in g["rows"] if r["alias"]]
            models = (f"<details class='more'><summary>Its {len(g['rows'])} model{'s' * (len(g['rows']) != 1)}"
                      f"</summary><ul class='items'>{''.join(model(r) for r in g['rows'])}</ul></details>")
            todo["held"] += [item(f"<b>{esc(conn)}</b> {chip('held', '')}", both(aliases, f"every model on {conn} needed action"),
                                  f"Latest: {esc(h['said'])}{link(h['url'])}", h["title"], models if i == 0 else "")
                             for i, h in enumerate(g["held"])]
            continue
        for r in g["rows"]:
            kind = ("notices" if not r["alias"] else "signin" if r["label"] == STATUS["signin"][0]
                    else "retire" if r["label"].startswith("retiring") or r["css"] == "err" else "retry")
            todo[kind].append((r, model(r)))
    every = {k: [r["alias"] for r, _ in v] for k, v in todo.items() if k != "held"}
    body = "".join(g for g in (
        todo["held"] and group("Check the connection", "Every model on it is failing, so the cause is likely its key,"
                               " credit or model prefix, and nothing was retired. Retry spends one call a model.",
                               todo["held"], count=f"{held[0]} connection{'s' * (held[0] != 1)}, "
                                                   f"{held[1]} model{'s' * (held[1] != 1)}"),
        todo["signin"] and group("Sign in again", "The provider refused the key: it expired or was revoked. Sign in"
                                 " again on Endpoints, then Retry.", [h for _, h in todo["signin"]],
                                 both(every["signin"], "", False)),
        todo["retire"] and group("Retire, or retry", "Gone from the provider, no longer free, or failing for days:"
                                 " retire it, or Retry once (one call), as an answer clears it.",
                                 [h for _, h in todo["retire"]], both(every["retire"], "it needed action")),
        todo["retry"] and group("Retry", "Failing for now, often a limit or a timeout. Retry spends one call; an"
                                " answer clears it.", [h for _, h in todo["retry"]],
                                both(every["retry"], "it needed action")),
        todo["notices"] and group("Notices", "What a daily check found that is not one model's.",
                                  [h for _, h in todo["notices"]])) if g)
    tags = load_json(DATA / "config" / "provider-tags.json")      # a retired model's connection, by served name
    gone = "".join(item(f"{name(alias)} <span class='muted'>{esc(r['at'][:10])}</span>",
                        f"<button class='linkish' data-act='restore' data-alias='{esc(alias)}'>Restore</button>",
                        esc(f"{tags.get(r.get('served', ''), '').strip('?') or r.get('endpoint') or endpoints.default()}"
                            f" · {said(r.get('why', '').removeprefix('auto: '))[0]}"), r.get("why", ""), counted=False)
                   for alias, r in auto)
    # Folded: a week of automatic retirements is a record to check, not a list of things to do.
    folded = (f"<section class='panel'><details class='more'><summary><h2>Retired automatically this week ·"
              f" {len(auto)}</h2></summary><p class='sub'>Each can be restored.</p><ul class='items'>{gone}</ul>"
              "</details></section>" if auto else "")
    return ("<p class='sub'>What will not fix itself, by what to do about it. A name opens the model; a reason's"
            " whole error is on hover.</p><div class='cols flow'>"
            + (body or "<section class='panel'><h2>Nothing needs doing</h2><p class='sub'>No model is failing in a"
                       " way that will not fix itself, and no notice is waiting.</p></section>")
            + folded + "</div>")


CAP_LABELS = {"tools": "tools", "vision": "vision", "structured_output": "json", "reasoning": "reasons"}


def price_of(alias: str, served: str, f: dict) -> str:
    """free, paid or unknown, for the model browser's filter.

    The bench keeps no single record of this, so it is read off what is
    known, surest first: a connection paid by subscription; a `:free` slug, a
    local model or a list price of 0; a free-tier listing (FreeLLMAPI limits,
    which beat models.dev's list price: Groq and NVIDIA have free tiers that
    models.dev prices); else the list price.
    ponytail: a heuristic; a per-model "free" fact in the catalog replaces it."""
    import sources
    conn = catalog.connection_of(alias)
    if conn in (endpoints.connections().get("paid") or ()):
        return "paid"
    if ":free" in served or conn == "local" or sources.is_free(f.get("cost")):
        return "free"
    if any(k.startswith("limits") and str(v).startswith("freellmapi") for k, v in (f.get("sources") or {}).items()):
        return "free"
    return "paid" if sources.is_free(f.get("cost")) is False else "unknown"


def on_bench() -> set[str]:
    """Aliases registered now: what the charts draw."""
    return set(registered().values())


# Every report with a chart shares the same way (bench.js "share"): its charts as
# an image, its table as Markdown or CSV, or a post with the image on the clipboard.
SHARE = ("<details class='share'><summary>Share</summary><div class='share-menu'>"
         + "".join(f"<button type='button' class='linkish' data-share='{k}'>{label}</button>" for k, label in (
             ("copy", "Copy image"), ("png", "Download image"), ("md", "Copy table as Markdown"),
             ("csv", "Download table as CSV"), ("native", "Share…")))
         + "<span class='share-to'>Post on " + ", ".join(
             f"<button type='button' class='linkish' data-share='{k}'>{label}</button>" for k, label in (
                 ("x", "X"), ("bsky", "Bluesky"), ("linkedin", "LinkedIn"), ("reddit", "Reddit")))
         + "</span></div></details>")


def report_head(title: str, tools: str = "") -> str:
    """A report's heading line: its name, its own controls (a window, a view),
    and Share at the right."""
    return f"<header><h2>{title}</h2>{tools}{SHARE}</header>"


def table_fold(table: str, retired: bool = True) -> str:
    """A report's table, folded under its chart, with its filter."""
    return ("<details class='key'><summary>Table</summary><div class='toolbar'><input type='search'"
            " data-filter placeholder='Filter by model or connection' aria-label='Filter rows'>"
            + ("<label><input type='checkbox' data-retired> Show retired</label>" if retired else "") + "</div>"
            f"<div class='scroll'>{table}</div></details>")


def chart_card(title: str, sub: str, chart_id: str, table: str, key: str = "", extra: str = "",
               retired: bool = True, after: str = "", tools: str = "") -> str:
    """A Benchmarks panel: its heading line (`tools` on it), one sentence, the
    chart (drawn by bench.js with Observable Plot), then the table and how it
    is measured, each folded. `extra` goes above the chart, `after` right under it."""
    return (f"<div class='panel'>{report_head(title, tools)}<p class='sub'>{sub}</p>{extra}"
            f"<div class='chart' id='{chart_id}'></div>{after}{table_fold(table, retired)}"
            + (f"<details class='key'><summary>How this is measured</summary>{key}</details>" if key else "") + "</div>")


def _family(task: str) -> str:
    for prefix, name in (("claim-", "claims"), ("seeded-hard-", "seeded, whole file"),
                         ("seeded-", "seeded, function"), ("rebench-", "real bugs")):
        if task.startswith(prefix):
            return name
    return "hand-built"


_wrong: tuple = (None, {})                          # (key, wrong.scores())
_wrong_table: tuple = (None, {})                    # (key, wrong.table())


def _tasks_key(tasks: pathlib.Path) -> tuple:
    """(path, mtime, size) of every file in each task's folder and its output
    folder: what scoring reads. The listings carry the stats on Windows."""
    key = []
    for task in (os.scandir(tasks) if tasks.is_dir() else ()):
        for folder in (task.path, os.path.join(task.path, "output")):
            try:
                key += [(e.path, e.stat().st_mtime_ns, e.stat().st_size) for e in os.scandir(folder) if e.is_file()]
            except OSError:                                  # not a folder, or no output yet
                pass
    return tuple(key)


def wrong_table() -> dict:
    """wrong.table(). The suite answers are scored again only when an answer,
    a task, a rename or a model's role changes (it reads them all: asked three
    times a render, then kept 5 seconds, it was 0.7 s of each reload); the
    reviews are counted again when the offloads change."""
    global _wrong, _wrong_table
    import aliases
    import offload
    import wrong
    from run import images_only
    vision = images_only()
    key = (locked.once(("tasks key",), lambda: _tasks_key(wrong.TASKS)), aliases.history(), vision)
    scored = _wrong
    if scored[0] != key:
        s = wrong.scores()
        # A vision or image model keeps its reviews (real work, judged) but not
        # its code-task answers: the suite is not what it is for (suite_scores).
        for alias in vision & set(s):
            s[alias].update(answers=0, wrong=0, by={k: [0, 0] for k in s[alias]["by"]},
                            hunt=dict.fromkeys(s[alias]["hunt"], 0))
        scored = _wrong = (key, s)                   # keyed before scoring: a change meanwhile scores again
    try:
        st = offload.LOG.stat()
        log = st.st_mtime_ns, st.st_size
    except OSError:
        log = None
    seen = _wrong_table
    if seen[0] != (key, log):
        seen = _wrong_table = ((key, log), wrong.table(scored[1]))
    return seen[1]


NEW_DAYS = 7                   # a model first scored this recently is marked new on Who to trust and Performance


def new_since() -> set[str]:
    """The models whose first suite score is within NEW_DAYS (today counted): new to the reports.
    First from the run notes and the kept answers (runs/answers/<task>/<model>/<run start>.md):
    a suite stopped at its hour writes no note, but its answers are kept and scored."""
    return locked.once(("new_since",), _new_since)


def _new_since() -> set[str]:
    since, first = (datetime.date.today() - datetime.timedelta(days=NEW_DAYS - 1)).isoformat(), {}

    def saw(alias: str, day: str) -> None:
        a = current_alias(alias)
        first[a] = min(first.get(a, day), day)
    for day, _run, _task, alias, *_ in _note_table()[1]:
        if day:
            saw(alias, day)
    try:
        for task in os.scandir(DATA / "runs" / "answers"):
            for model in (os.scandir(task.path) if task.is_dir() else ()):
                days = [e.name[:10] for e in os.scandir(model.path)] if model.is_dir() else []
                if days:
                    saw(model.name, min(days))
    except OSError:                                    # no archive yet, or a folder gone mid-read: the notes still count
        pass
    return {a for a, d in first.items() if d >= since}


def overview_rows() -> list[dict]:
    """Who to trust, a row per registered model with both suite scores and answers:
    the share of real findings located, and the share of answers confidently wrong.
    Worked out once a render (the card and the chart ask), each caller given its own rows."""
    return [dict(r) for r in locked.once(("overview_rows",), _overview_rows)]


def _overview_rows() -> list[dict]:
    import route
    recall, wr, new = route.suite_recall(), wrong_table(), new_since()
    return [{"model": m, "recall": recall[m][0] / recall[m][1], "wrong": wr[m]["wrong"] / wr[m]["answers"],
             "found": f"{recall[m][0]}/{recall[m][1]}", "wrongN": f"{wr[m]['wrong']}/{wr[m]['answers']}",
             "new": m in new}
            for m in sorted(on_bench()) if recall.get(m) and recall[m][1] and wr.get(m, {}).get("answers")]


def charts_data() -> dict:
    """Everything the Benchmarks charts draw, for bench.js. Registered models
    only: the tables keep retired ones behind "Show retired", a chart has no toggle."""
    import wrong
    live = on_bench()
    wr = wrong_table()
    heat = []
    for (task, m), v in sorted(suite_scores().items()):
        if m in live:
            hit = re.match(r"(\d+)/(\d+)", v["found"])
            heat.append({"model": m, "task": task, "family": _family(task), "label": v["found"],
                         "rate": int(hit[1]) / max(1, int(hit[2])) if hit else None, "invented": bool(v["invented"])})
    bars = [{"model": m, "kind": k, "rate": w / r["answers"], "n": f"{w}/{n}"}
            for m, r in sorted(wr.items()) if m in live and r["answers"] for k, (w, n) in r["by"].items() if w]
    totals = {m: {"rate": r["wrong"] / r["answers"], "answers": r["answers"], "wrong": r["wrong"]}
              for m, r in wr.items() if m in live and r["answers"]}
    hunt = [{"model": m, **r["hunt"]} for m, r in sorted(wr.items()) if m in live and any(r["hunt"].values())]
    return {"overview": overview_rows(), "heat": heat, "families": ["hand-built", "claims", "seeded, function",
                                                             "seeded, whole file", "real bugs"],
            "wrong": bars, "wrongTotals": totals, "kinds": list(wrong.KINDS), "hunt": hunt,
            "work": work_rows(), "vision": vision_rows(), "uptime": uptime_data(), "trend": trend_data(),
            "decide": decision_data(),
            **perf_charts()}


def work_rows() -> list[dict]:
    """Real work handed out (metrics/offloads.jsonl): how often it helped when
    judged, and the verified claims recorded since 2026-10-17. Once a render
    (the card and the chart ask), each caller given its own rows."""
    return [dict(r) for r in locked.once(("work_rows",), _work_rows)]


def _work_rows() -> list[dict]:
    from offload import entries, summary
    live, claims = on_bench(), {}
    for e in entries():
        c = claims.setdefault(e["model"], [0, 0])
        c[0] += len(e.get("real") or [])
        c[1] += len(e.get("false") or [])
    return [{"model": m, "offloads": s["offloads"], "judged": s["judged"], "useful": s["useful"],
             "real": claims.get(m, [0, 0])[0], "false": claims.get(m, [0, 0])[1]}
            for m, s in sorted(summary().items()) if m in live and s["judged"]]


VISION_STATES = ("right", "misread", "left out", "trap taken", "no answer")
VISION_SEATS = ("checked, helped", "checked, did not help", "useful, no claims", "no use, no claims",
                "not judged", "no answer")
_vision_work: tuple = (None, {})


def vision_rows() -> dict:
    """The Vision panel's evidence, in two kinds kept apart.

    TASKS: the vision tasks' saved answers (suite/tasks/vision-*), scored by
    the suite's own scorer and then field by field: right, misread (the line
    is there with another value), left out, trap taken (a value for something
    the picture does not show, or a switch that is off called on), no answer.
    The keys exist by construction (suite/vision_tasks.py), so this is the
    controlled comparison.

    REAL WORK: every seat of the gateway's vision panels (runs/gateway/
    calls.jsonl, shape vision) and how it was judged (metrics/offloads.jsonl).
    A verdict that lists claims was checked claim by claim against the
    picture; one without claims says only useful or not, which for thicken's
    face corners means it agreed with another seat. Agreement is not a check
    (the bench's own rule: seats fail together), so the two are never added.

    Worked out once a render (the card and the chart ask), and shared: read it,
    never change it.
    """
    return locked.once(("vision_rows",), _vision_rows)


def _vision_rows() -> dict:
    import run
    live = on_bench()
    heat, tasks = [], {}
    for path in sorted((DATA / "suite" / "tasks").glob("vision-*/task.json")):
        spec = json.loads(read(path))
        if spec.get("draft"):
            continue
        names = list(dict.fromkeys(c["label"].split(":")[0] for c in spec["must_find"]))
        traps = {c["label"].split(":")[0] for c in spec.get("must_not_claim", [])}
        tasks[spec["id"]] = {"title": spec["title"], "fields": names, "family": _vision_family(spec)}
        for answer in sorted((path.parent / "output").glob("*.md")):
            alias = current_alias(answer.stem)
            if alias not in live:
                continue
            text = read(answer)
            s = run.score(spec, text)
            missed = {lbl.split(":")[0] for lbl in s["missed"]}
            invented = {lbl.split(":")[0] for lbl in s["invented"]}
            for name in names:
                there = re.search(rf"(?m)^[^\w\n]*{re.escape(name).replace('_', '[ _]')}[^\w\n]*:", text.lower())
                state = ("no answer" if s["kind"] != "answered" else "trap taken" if name in invented
                         else "right" if name not in missed else "misread" if there else "left out")
                heat.append({"model": alias, "task": spec["id"], "field": name, "key": f"{spec['id']}·{name}",
                             "state": state, "trap": name in traps})
    models: dict[str, dict] = {}
    for h in heat:
        m = models.setdefault(h["model"], {"asked": set(), "answered": set(), "fields": 0, "right": 0,
                                           "traps": 0, "taken": 0})
        m["asked"].add(h["task"])
        if h["state"] != "no answer":
            m["answered"].add(h["task"])
            m["fields"] += 1
            m["right"] += h["state"] == "right"
            m["traps"] += h["trap"]
            m["taken"] += h["state"] == "trap taken"
    summary = {a: {"asked": len(m["asked"]), "answered": len(m["answered"]), "fields": m["fields"],
                   "right": m["right"], "traps": m["traps"], "taken": m["taken"]} for a, m in models.items()}
    return {"tasks": tasks, "heat": heat, "summary": summary, "states": list(VISION_STATES),
            "seatKinds": list(VISION_SEATS), "work": _vision_work_rows(live), "held": run.vision_held()}


VISION_FAMILIES = (("vision-cord-", "receipts"), ("vision-screenqa-", "screens"), ("vision-tablevqa-", "tables"),
                   ("vision-pope-", "objects"))


def _vision_family(spec: dict) -> str:
    """The grid's group: a public source (suite/vision_sample.py), the made
    pictures (suite/vision_tasks.py), or the install's own (written by hand)."""
    if spec.get("made_by", "").startswith("suite/vision_tasks.py"):
        return "made"
    return next((name for prefix, name in VISION_FAMILIES if spec["id"].startswith(prefix)), "own")


_gateway_seen: dict = {"mark": {}, "seats": [], "read": 0}
_gateway_lock = threading.Lock()


def _gateway_fold(calls: pathlib.Path) -> list[tuple]:
    """Every gateway seat, oldest first: (panel shape, panel at, alias, call,
    status, answered, seconds, error). Only the lines added since the last call
    are parsed: the log runs to 50 MB, and the gateway adds to it on every
    call. Vision and Uptime both read it, so it is parsed once. Hold
    _gateway_lock; `read` counts the times it started over."""
    fresh, lines = locked.appended(calls, _gateway_seen["mark"])
    if fresh:
        _gateway_seen["seats"] = []
        _gateway_seen["read"] += 1
    for raw in lines:
        try:
            panel = json.loads(raw)
        except ValueError:
            continue
        shape, at = panel.get("shape"), panel.get("at") or ""
        _gateway_seen["seats"] += [(shape, at, s.get("alias", ""), s.get("call"), s.get("status"),
                                    s.get("status") == "ok" and bool((s.get("answer") or "").strip()),
                                    s.get("seconds"), s.get("error") or "") for s in panel.get("seats", [])]
    return _gateway_seen["seats"]


def _gateway_seats(calls: pathlib.Path) -> list[tuple]:
    with _gateway_lock:
        return list(_gateway_fold(calls))


_seat_days: dict = {"read": None, "since": "", "n": 0, "counts": {}}


def _seat_counts(since: str) -> dict:
    """(day, alias as logged, status, error) -> (seats, answered seconds, last
    at) for the gateway's seats from `since` on, the new seats folded in: a
    render then resolves a few thousand keys, not every seat."""
    with _gateway_lock:
        seats, s = _gateway_fold(DATA / "runs" / "gateway" / "calls.jsonl"), _seat_days
        if (s["read"], s["since"]) != (_gateway_seen["read"], since):
            s.update(read=_gateway_seen["read"], since=since, n=0, counts={})
        counts = s["counts"]
        for _shape, at, alias, _call, status, _answered, seconds, error in seats[s["n"]:]:
            if at[:10] < since:
                continue
            c = counts.get(k := (at[:10], alias, status or "error", error))
            if c is None:
                c = counts[k] = [0, [], ""]
            c[0] += 1
            if seconds and status == "ok":
                c[1].append(float(seconds))
            c[2] = max(c[2], at)
        s["n"] = len(seats)
        return {k: (n, list(secs), last) for k, (n, secs, last) in counts.items()}


def _vision_seat_list(calls: pathlib.Path) -> list[tuple]:
    """Every seat of the gateway's vision panels, oldest first: (alias, call,
    answered, seconds)."""
    return [(alias, call, answered, seconds)
            for shape, _, alias, call, _, answered, seconds, _ in _gateway_seats(calls) if shape == "vision"]


def _vision_work_rows(live: set[str]) -> dict:
    """alias -> {seat kind: count, claims right, claims wrong, seconds}; read
    again only when the gateway's log or the verdicts change (5 MB a render)."""
    global _vision_work
    calls, verdicts = DATA / "runs" / "gateway" / "calls.jsonl", DATA / "metrics" / "offloads.jsonl"
    try:
        key = tuple((p.stat().st_mtime_ns, p.stat().st_size) for p in (calls, verdicts)) + (frozenset(live),)
    except OSError:
        return {}
    if _vision_work[0] == key:
        return _vision_work[1]
    from offload import entries
    judged = {e["call"]: e for e in entries() if e.get("call") and e.get("useful") is not None}
    out: dict[str, dict] = {}
    for alias, call, answered, seconds in _vision_seat_list(calls):
        alias = current_alias(alias)
        if alias not in live:
            continue
        r = out.setdefault(alias, {**dict.fromkeys(VISION_SEATS, 0), "real": 0, "false": 0, "seconds": []})
        v = judged.get(call)
        if not answered:
            kind = "no answer"
        elif v is None:
            kind = "not judged"
        elif v.get("real") or v.get("false"):
            kind = "checked, helped" if v["useful"] else "checked, did not help"
            r["real"] += len(v.get("real") or [])
            r["false"] += len(v.get("false") or [])
        else:
            kind = "useful, no claims" if v["useful"] else "no use, no claims"
        r[kind] += 1
        if kind != "no answer" and seconds:
            r["seconds"].append(float(seconds))
    for r in out.values():
        secs = sorted(r.pop("seconds"))
        r["seconds"] = secs[len(secs) // 2] if secs else None
    _vision_work = (key, out)
    return out


def vision_card() -> str:
    """Reading images: the vision tasks, then real work on images."""
    v = vision_rows()
    s, w = v["summary"], v["work"]
    body = ["<table><thead><tr>", th("Model"), th("Tasks answered", True), th("Fields read right", True),
            th("Traps taken", True), th("Image seats", True), th("Checked claims right", True),
            th("Checked claims wrong", True), th("Useful, no claims", True), th("Not judged", True),
            th("Median seconds", True), "</tr></thead><tbody>"]

    def cell(n, d):
        return (f"<td class='num' data-sort='{n / d:.3f}'>{n}/{d}</td>" if d
                else "<td class='num muted' data-sort='-1'>—</td>")
    for m in sorted(set(s) | set(w)):
        t, r = s.get(m, {}), w.get(m, {})
        seats = sum(r.get(k, 0) for k in VISION_SEATS)
        claims = r.get("real", 0) + r.get("false", 0)
        body.append(f"{tr(m)}<td>{named(m)}</td>" + cell(t.get("answered", 0), t.get("asked", 0))
                    + cell(t.get("right", 0), t.get("fields", 0)) + cell(t.get("taken", 0), t.get("traps", 0))
                    + f"<td class='num'>{seats or '—'}</td>" + cell(r.get("real", 0), claims)
                    + cell(r.get("false", 0), claims) + f"<td class='num'>{r.get('useful, no claims', 0) or '—'}</td>"
                    + f"<td class='num'>{r.get('not judged', 0) or '—'}</td>"
                    + (f"<td class='num'>{r['seconds']:.1f}</td>" if r.get("seconds") else "<td class='num muted'>—</td>")
                    + "</tr>")
    body.append("</tbody></table>")
    held = (f"<p class='hint'>The vision tasks wait until {esc(v['held'])}: no run picks them before then"
            " (<code>vision_from</code> in <code>config/suite.json</code>; remove it to start them now).</p>"
            if v["held"] else "")
    how = ("<p>The tasks are pictures with a known answer (receipts, screens, tables and objects from public"
           " sets, pictures the bench makes, and any of the install's own), scored field by field: right, misread (the line is there with another value), left out,"
           " trap taken (a value for something the picture does not show), or no answer.</p><p>Real work is every"
           " seat of the gateway's image panels and how it was judged: checked claim by claim against the"
           " picture, or only said useful or not, which can mean it agreed with another seat. Agreement is not a"
           " check, so the two are never added. A seat never judged, or never answered, gets no dot; the table has"
           " every seat.</p>")
    return (f"<div class='panel'>{report_head('Vision')}<p class='sub'>Reading images: fields read right on pictures"
            " with a known answer, then real work on images.</p>" + held +
            "<h3 class='chart-head'>On the vision tasks, field by field</h3><div class='chart' id='chart-vision-fields'></div>"
            "<h3 class='chart-head'>On real work with images: judged useful, and how sure that is</h3>"
            "<div class='chart' id='chart-vision-work'></div>" + table_fold("".join(body))
            + f"<details class='key'><summary>How this is measured</summary>{how}</details></div>")


def perf_charts() -> dict:
    """Speed on real tasks against how much a model finds, for the Performance
    panel of Benchmarks. (Whether probes answered is Uptime's.)"""
    import route
    live = on_bench()
    seconds: dict[str, list[float]] = {}
    for (_task, m), v in suite_scores().items():
        if m in live and re.match(r"\d+/\d+", v["found"]):
            seconds.setdefault(m, []).append(v["seconds"])
    recall, new = route.suite_recall(), new_since()
    speed = [{"model": m, "seconds": sorted(s)[len(s) // 2], "recall": recall[m][0] / recall[m][1], "tasks": len(s),
              "new": m in new}
             for m, s in sorted(seconds.items()) if recall.get(m) and recall[m][1]]
    return {"speed": speed}


# ---- Trend: does a model score lower, or higher, than it used to? ----------
#
# From the run notes, a model against itself, task by task: a run asks up to
# six tasks and tasks differ in size, so run totals are never compared. A score
# is a share of the task's findings; a task whose count of findings changed
# (its answer key did) starts again. Not a score (timeout, error, refused): left
# out, as uptime counts those. Shown only: it routes and retires nothing.

TREND_DAYS = (30, 60)          # the window's choices, the first by default
TREND_MOVE = 0.10              # a move of 10 points of a task's findings, on average
TREND_STATES = ("lower", "higher", "steady", "too little")


_trend_seen: tuple = (None, {})                     # (notes' stats, renames, today), _trend_rows()


def trend_rows() -> dict:
    """The trend, worked out again only when a note, a rename or the day
    changes. Shared: read it, never change it."""
    global _trend_seen
    stats, rows = _note_table()
    key, seen = (stats, aliases.history(), datetime.date.today()), _trend_seen
    if stats is None:                                   # a note could not be read: not kept
        return _trend_rows(rows, key[2])
    if seen[0] != key:
        seen = _trend_seen = (key, _trend_rows(rows, key[2]))
    return seen[1]


def _trend_rows(rows: list[tuple], today: datetime.date) -> dict:
    """{"first": the first day with a score, "models": alias -> {"last": day,
    "compared": a task scored on two days or more, "windows": {days:
    summary}}}. A window's summary is the scores in the window against those
    before it. A task asked twice in a day counts once that day, as the mean
    of its scores."""
    by: dict[str, dict[tuple, dict]] = {}               # alias -> (task, of) -> day -> [found], oldest first
    first = ""
    for day, _run, task, alias, found, _inv, _s in rows:
        hit = re.fullmatch(r"(\d+)/(\d+)", found)
        if not day or not hit or not int(hit[2]):
            continue
        first = min(first or day, day)
        by.setdefault(current_alias(alias), {}).setdefault((task, int(hit[2])), {}).setdefault(day, []).append(int(hit[1]))

    def mean(xs):
        return sum(xs) / len(xs)
    models = {}
    for alias, found_by_task in by.items():
        tasks = {(task, of): [(d, mean(f) / of) for d, f in days.items()]     # [(day, share)], oldest first
                 for (task, of), days in found_by_task.items()}
        windows = {}
        for w in TREND_DAYS:
            since = (today - datetime.timedelta(days=w - 1)).isoformat()
            moves, recent, earlier, per = [], {}, set(), []     # per task; day -> [moves]; days before; [task, move]
            for (task, _of), scores in tasks.items():
                old = [s for d, s in scores if d < since]
                new = [(d, s) for d, s in scores if d >= since]
                if not old or not new:
                    continue
                base = mean(old)
                moves.append(mean([s for _d, s in new]) - base)
                per.append([task, moves[-1]])
                earlier |= {d for d, _s in scores if d < since}
                for d, s in new:
                    recent.setdefault(d, []).append(s - base)
            down, up = sum(m < -1e-9 for m in moves), sum(m > 1e-9 for m in moves)
            change = mean(moves) if moves else 0.0
            runs_down = sum(mean(v) < -1e-9 for v in recent.values())
            runs_up = sum(mean(v) > 1e-9 for v in recent.values())
            if len(moves) < 3 or len(recent) < 2 or len(earlier) < 2:     # one run on either side is not a trend
                state = "too little"
            elif change <= -TREND_MOVE + 1e-9 and down >= 2 and down >= 2 * up and runs_down >= 2:
                state = "lower"
            elif change >= TREND_MOVE - 1e-9 and up >= 2 and up >= 2 * down and runs_up >= 2:
                state = "higher"
            else:
                state = "steady"
            windows[w] = {"state": state, "change": change, "tasks": len(moves), "down": down, "up": up,
                          "runs": len(recent), "runsDown": runs_down, "runsUp": runs_up, "before": len(earlier),
                          "per": sorted(per, key=lambda p: p[1])}
        models[alias] = {"last": max(d for s in tasks.values() for d, _s in s),
                         "compared": any(len(s) > 1 for s in tasks.values()), "windows": windows}
    return {"first": first, "models": models}


def _trend_says(s: dict) -> str:
    """A window's summary in words, as the chart and the table say it."""
    if not s["tasks"]:
        return "no task scored both in the window and before it"
    head = {"too little": "too little to say", "steady": "steady"}.get(s["state"], s["state"])
    return (f"{head}: {s['change'] * 100:+.0f} pts on {s['tasks']} task{'s' * (s['tasks'] != 1)}"
            f" ({s['down']} down, {s['up']} up), {s['runs']} run{'s' * (s['runs'] != 1)}"
            f" against {s['before']} before")


def _trend_order(m: str, s: dict) -> tuple:
    """The chart's and the table's order: lower, higher, steady, too little;
    the biggest move first in each (too little: by name, its move means nothing)."""
    return TREND_STATES.index(s["state"]), 0 if s["state"] == "too little" else -abs(s["change"]), m


def trend_data() -> dict:
    """For bench.js: registered models with a run to compare, each window's
    flag, its evidence and each task's move."""
    t, live = trend_rows(), on_bench()
    return {"choices": list(TREND_DAYS), "move": TREND_MOVE,
            "models": [{"name": m, "last": v["last"],
                        "w": {str(w): {**s, "says": _trend_says(s)} for w, s in v["windows"].items()}}
                       for m, v in sorted(t["models"].items()) if m in live and v["compared"]]}


def trend_card() -> str:
    """Each model against its own history: a bar for how far its scores in the
    window moved from its scores before it, grouped by the flag, the evidence beside it."""
    t, live, first, names = trend_rows(), current(), TREND_DAYS[0], {}

    def name(model: str) -> str:                       # each window's table shows it: worked out once
        if model not in names:
            names[model] = f"{tr(model)}<td>{named(model)}</td>"
        return names[model]

    css = {"lower": "err", "higher": "ok", "steady": "", "too little": ""}

    def window(w: int) -> str:
        rows = {m: v["windows"][w] for m, v in t["models"].items() if m in live and v["windows"][w]["tasks"]}
        body = ["<table><thead><tr>", th("Model"), th("Trend"), th("Change", True), th("Tasks compared", True),
                th("Down", True), th("Up", True), th("Runs in window", True), th("Runs before", True),
                th("Last scored"), "</tr></thead><tbody>"]
        for m in sorted(rows, key=lambda m: _trend_order(m, rows[m])):
            s = rows[m]
            body.append(
                name(m) + f"<td data-sort='{TREND_STATES.index(s['state'])}'>"
                + chip(s["state"], css[s["state"]], _trend_says(s)) + "</td>"
                f"<td class='num' data-sort='{s['change']:.4f}'>{s['change'] * 100:+.0f} pts</td>"
                f"<td class='num'>{s['tasks']}</td><td class='num'>{s['down']}</td><td class='num'>{s['up']}</td>"
                f"<td class='num' data-sort='{s['runs']}'>{s['runs']}"
                f" <span class='muted'>({s['runsDown']} below, {s['runsUp']} above)</span></td>"
                f"<td class='num'>{s['before']}</td><td class='mono'>{esc(t['models'][m]['last'])}</td></tr>")
        if not rows:
            body.append(f"<tr><td colspan='9' class='muted'>No model was scored on the same task both in the last"
                        f" {w} days and before them.</td></tr>")
        body.append("</tbody></table>")
        return f"<div data-trend-window='{w}'{'' if w == first else ' hidden'}>" + "".join(body) + "</div>"

    legend = ("<div class='q-legend'><span><i style='background:var(--err)'></i>lower than it used to be</span>"
              "<span><i style='background:var(--ok)'></i>higher</span><span><i style='background:var(--muted);opacity:.6'></i>"
              "steady</span><span><i style='background:var(--line)'></i>within 10 pts of its earlier scores</span>"
              "<span>no bar: too little to say</span></div>")
    pick = ("<label>Window <select data-trend-days>"
            + "".join(f"<option value='{w}'>last {w} days</option>" for w in TREND_DAYS) + "</select></label>")
    how = ("<p>A model against itself, task by task: its scores on a task in the window against its scores on the"
           " same task before it. A score is the share of the task's findings located; a task whose answer key"
           " changed starts again, and a timeout, an error or a refusal is not a score.</p><p>Lower or higher is a"
           f" move of {TREND_MOVE * 100:.0f} points or more on average, with at least two tasks and twice as many"
           " moving that way as the other, in two runs or more. Fewer than three tasks compared, or one run on"
           " either side, is too little to say. Shown only: it routes and retires nothing.</p>")
    return chart_card("Trend", "Who scores lower, or higher, than it used to on the same tasks.",
                      "chart-trend", "".join(window(w) for w in TREND_DAYS), how, extra=legend, tools=pick)


# ---- Uptime: was the provider there when the bench called it? --------------
#
# Only calls the bench already makes, each counted once: the daily probe
# (perf/history.csv), every gateway seat (runs/gateway/calls.jsonl), and the
# calls only the client's ledger has (the suite's, onboarding's). The ledger
# also lists the probe's and the gateway's calls, which are counted from their
# own logs because those keep the error text. Each call is classified from its
# text when the report is drawn (suite/run.uptime_class). Shown only: uptime
# changes no routing and retires nothing.

UPTIME_DAYS = (7, 30)                                # the window's choices, the first by default
LEDGER_OWN = ("perf/run", "serve", "gateway")        # callers whose calls their own logs count
# A probe row from before 2026-10-16 keeps no text, only its class then.
_OLD_CLASS = {"empty": "empty", "capacity": "busy", "structural": "setup",
              "paywalled": "setup", "entitlement": "setup", "signin": "setup"}
_OUT_LABEL = {"quota": "our quota", "rate": "our rate limit", "notsent": "not sent by the bench",
              "client": "our client", "relay": "our LAN hub"}
_ledger_seen: dict = {"marks": {}, "rows": {}, "mute": {}, "conn": {}, "endpoint": {}}
_ledger_lock = threading.Lock()
_LEDGER_HEAD = re.compile(r'"t":([\d.]+),"at":"([^"]*)","alias":"([^"]*)","connection":"([^"]*)","endpoint":"([^"]*)"')


def _ledger_calls(since_month: str) -> tuple[list, dict, dict, float]:
    """(calls only the ledger has, alias -> connection, alias -> endpoint, the
    start of the last failed call it kept no text for) from the month files
    from `since_month` (YYYY-MM) on. A call is (t, at, alias, connection,
    status, error, seconds). New lines only, as the gateway's seats are read."""
    import ledger
    with _ledger_lock:
        seen = _ledger_seen
        for path in sorted(ledger.DIR.glob("*.jsonl")):
            stem = path.stem
            if stem < since_month:
                continue
            fresh, lines = locked.appended(path, seen["marks"].setdefault(stem, {}))
            if fresh:
                seen["rows"][stem], seen["mute"][stem] = [], 0.0
            rows = seen["rows"].setdefault(stem, [])
            for raw in lines:
                # Most lines are the probe's or the gateway's, wanted only for
                # where the model was: read by a pattern, not parsed (ledger.append
                # writes the keys in this order).
                if (m := _LEDGER_HEAD.search(raw)) and any(f'"caller":"{c}"' in raw for c in LEDGER_OWN):
                    t, at, alias, conn, endpoint = float(m[1]), m[2], m[3], m[4], m[5]
                    status, mute = None, '"status":"error"' in raw and '"error":' not in raw
                else:
                    try:
                        r = json.loads(raw)
                    except ValueError:
                        continue
                    if not isinstance(r, dict) or not r.get("alias") or not isinstance(r.get("t"), (int, float)):
                        continue
                    t, at, alias, conn, endpoint = r["t"], r.get("at") or "", r["alias"], r.get("connection"), r.get("endpoint")
                    status = (r.get("status") or "") if r.get("caller") not in LEDGER_OWN else None
                    mute = r.get("status") == "error" and "error" not in r
                if conn not in (None, "", "?"):
                    seen["conn"][alias] = conn
                if endpoint:
                    seen["endpoint"][alias] = endpoint
                if mute:
                    seen["mute"][stem] = max(seen["mute"].get(stem, 0.0), t)
                if status is not None:
                    rows.append((t, at, alias, conn or "", status, r.get("error") or "", r.get("seconds")))
        stems = sorted(s for s in seen["rows"] if s >= since_month)
        # Until the ledger kept error text (2026-11-08) its failed calls cannot be
        # classified, and counting only its successes would flatter everyone: its
        # own calls count from after the last failure it kept no text for.
        mute = max((seen["mute"].get(s, 0.0) for s in stems), default=0.0)
        return ([c for s in stems for c in seen["rows"][s]], dict(seen["conn"]), dict(seen["endpoint"]), mute)


@functools.lru_cache(maxsize=8192)
def _uptime_class(text: str, status: str, private: bool, decided: tuple = ()) -> tuple[str, str]:
    """`decided`: metrics/decided.json's stat, so a new decision about a
    shape (decide.classify_errors) is read on the next render, not the next start."""
    from run import uptime_class
    return uptime_class(text, status, private)


def _down(kind: str) -> bool:
    """A kind of failure counted down: its class, before any "(decided by ...)"."""
    from run import UPTIME_DOWN
    return kind.split(" (decided by ")[0] in UPTIME_DOWN


_decide_seen: dict = {"mark": {}, "rows": []}
_decide_lock = threading.Lock()


def _decision_calls() -> list[tuple]:
    """Every decision call (metrics/decisions.jsonl, decide.call): (at, alias,
    served, via, status, error, seconds). New lines only, as the ledger is read."""
    with _decide_lock:
        fresh, lines = locked.appended(decide.LOG, _decide_seen["mark"])
        if fresh:
            _decide_seen["rows"] = []
        for raw in lines:
            try:
                r = json.loads(raw)
            except ValueError:
                continue
            if isinstance(r, dict) and r.get("at") and r.get("alias"):
                # Answered unless it failed: a reply without the asked answer is still the provider there.
                status = "error" if r.get("error") else "ok"
                _decide_seen["rows"].append((r["at"], r["alias"], r.get("served") or "", r.get("via") or "",
                                             status, r.get("error") or "", r.get("seconds")))
        return list(_decide_seen["rows"])


def _shape(text: str) -> str:
    """An error as a shape, so the same failure counts once in a list: URLs,
    ids and numbers stand in for what changes between calls."""
    s = re.sub(r"https?://\S+", "<url>", text)
    s = re.sub(r"\b(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{14,}\b", "<id>", s)
    return " ".join(re.sub(r"\d+(\.\d+)?", "N", s).split())[:130]


def uptime_rows() -> dict:
    """The Uptime report, once a render (the card and the charts both ask)."""
    return locked.once(("uptime",), _uptime_rows)


def _uptime_rows() -> dict:
    """{"days": the last 30 dates, "providers"/"models": name -> one bucket a
    day (None, or up, down, out, {class: n}, [answered seconds], last down),
    "conn": model -> connection, "odd": unrecognised shapes [day index, at,
    connection, shape]}. A provider's uptime is weighted by calls: every call
    to any of its models counts once."""
    import ipaddress
    import urllib.parse
    from run import UPTIME_DOWN, UPTIME_UP
    today = datetime.date.today()
    days = [(today - datetime.timedelta(days=i)).isoformat() for i in range(UPTIME_DAYS[-1] - 1, -1, -1)]
    since, index = days[0], {d: i for i, d in enumerate(days)}
    named_now: dict[str, str] = {}

    def now(alias: str) -> str:
        if alias not in named_now:
            named_now[alias] = current_alias(alias)
        return named_now[alias]
    # The month before too: a file is a UTC month, a day here is local.
    calls, conn_of, endpoint_of, mute = _ledger_calls((today - datetime.timedelta(days=UPTIME_DAYS[-1])).isoformat()[:7])
    conn_of = {now(a): c for a, c in conn_of.items()}           # a renamed model keeps where it was
    endpoint_of = {now(a): e for a, e in endpoint_of.items()}
    data = endpoints.load()
    regs = endpoints.registrations()
    gone = retired()
    conns: dict[str, str] = {}
    private: dict[str, bool] = {}

    def conn(alias: str) -> str:
        if alias not in conns:
            c = conn_of.get(alias) or catalog.connection_of(alias)
            if c in ("", "?"):
                c = (gone.get(alias, {}).get("tag") or "").split("/")[0] or "unknown"
            conns[alias] = c
        return conns[alias]

    def lan(alias: str) -> bool:
        """The call went to a hub on our own network (a private or loopback address)."""
        if alias not in private:
            base = (regs.get(alias, {}).get("api_base")
                    or data.get(endpoint_of.get(alias, ""), {}).get("base") or "")
            host = urllib.parse.urlsplit(base).hostname or ""
            try:
                private[alias] = ipaddress.ip_address(host).is_private
            except ValueError:
                private[alias] = host == "localhost" or host.endswith((".local", ".lan", ".localhost"))
        return private[alias]

    agg: dict[tuple, list] = {}           # (day, alias, connection, class, why) -> [calls, answered seconds, last at]
    odd: list[list] = []
    try:
        st = decide.DECIDED.stat()
        stamp = (st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = ()

    def classify(text: str, status: str, private: bool) -> tuple[str, str]:
        return _uptime_class(text, status, private, stamp)

    def count(at: str, alias: str, c: str, cls: str, why: str, n: int = 1, seconds=(), text: str = "") -> None:
        day = index.get(at[:10])
        if day is None or n <= 0:
            return
        a = agg.get(k := (day, alias, c, cls, why))
        if a is None:
            a = agg[k] = [0, [], ""]
        a[0] += n
        if cls == "ok":
            a[1] += seconds
        a[2] = max(a[2], at)
        # No rule knows it: listed, with what a decision model said of it when one did.
        if cls == "unrecognised" or why.startswith("decided by"):
            odd.append([day, at, c, n, _shape(text) if text else f"({why})",
                        f"{cls}, {why}" if why.startswith("decided by") else ""])

    for r in perf_rows():                                  # the probe: counts per row, one text per row
        at, alias = r.get("timestamp") or "", r["model"]
        # Not the dashboard's Retry: it writes a row only when the model answered.
        if at[:10] < since or r.get("prompt") == "retry":
            continue
        c, num = conn(alias), (lambda k: int(r.get(k) or 0))
        p50 = float(r.get("p50_s") or 0)                    # of the answers; a refusal is not timed
        count(at, alias, c, "ok", "", num("answered") + num("refused"), [p50] * num("answered") if p50 > 0 else [])
        count(at, alias, c, "timeout", "timed out", num("timeout"))
        if num("error"):
            old = r.get("failure") or ""
            if old == "timeout":
                # The row's one text is its timeout's; its errors were worse-than-nothing
                # classes after it (empty, other) that kept no text.
                cls, why = "unrecognised", "no text kept; the probe's text was its timeout's"
            elif r.get("detail"):
                cls, why = classify(r["detail"], "error", lan(alias))
            else:
                cls, why = _OLD_CLASS.get(old, "unrecognised"), f"no text kept; the probe said {old or 'nothing'}"
            count(at, alias, c, cls, why, num("error"), text=r.get("detail") if old != "timeout" else "")
    for (_day, alias, status, error), (n, seconds, last) in _seat_counts(since).items():
        alias = now(alias)
        cls, why = classify(error, status, lan(alias))
        count(last, alias, conn(alias), cls, why, n, seconds, error)
    for t, at, alias, c, status, error, seconds in calls:   # the suite's and the rest, once text is kept
        if t <= mute or at[:10] < since:
            continue
        alias = now(alias)
        cls, why = classify(error, status, lan(alias))
        count(at, alias, c or conn(alias), cls, why, 1, [float(seconds)] if seconds else [], error)
    # Decision models: every call decide.call logged, on the connection behind its served name
    # (provider tags: OpenRouter for the :free ones), its hub's address deciding the relay.
    tags, hubs = load_json(DATA / "config" / "provider-tags.json"), {}
    for at, alias, served, via, status, error, seconds in _decision_calls():
        if at[:10] < since:
            continue
        alias = now(alias)                             # a renamed decision model keeps its calls
        if via not in hubs:
            hubs[via] = decide._private(via)
        cls, why = classify(error, status, hubs[via])
        c = str(tags.get(served) or "").split("/")[0] or via or "unknown"
        conns.setdefault(alias, c)
        count(at, alias, c, cls, why, 1, [float(seconds)] if seconds and cls == "ok" else [], error)
    providers: dict[str, list] = {}
    models: dict[str, list] = {}
    for (day, alias, c, cls, why), (n, seconds, last) in agg.items():
        kind = f"setup: {why}" if cls == "setup" else _OUT_LABEL.get(cls, cls)
        if why.startswith("decided by") and cls != "setup":
            kind += f" ({why})"                        # the hover says a model, not a rule, said so
        for table, key in ((providers, c), (models, alias)):
            cells = table.setdefault(key, [None] * len(days))
            b = cells[day] = cells[day] or [0, 0, 0, {}, [], ""]
            if cls in UPTIME_UP:
                b[0] += n
                b[4] += seconds
                continue
            b[3][kind] = b[3].get(kind, 0) + n
            if cls in UPTIME_DOWN:
                b[1] += n
                b[5] = max(b[5], last)
            else:
                b[2] += n
    return {"days": days, "providers": providers, "models": models,
            "conn": {m: conn(m) for m in models}, "odd": odd}


def _uptime_window(cells: list, days: int) -> dict:
    """One row's totals over the last `days`."""
    up = down = out = 0
    kinds: dict[str, int] = {}
    seconds: list[float] = []
    last = ""
    for b in cells[-days:]:
        if b:
            up, down, out, last = up + b[0], down + b[1], out + b[2], max(last, b[5])
            seconds += b[4]
            for k, n in b[3].items():
                kinds[k] = kinds.get(k, 0) + n
    seconds.sort()
    return {"up": up, "down": down, "out": out, "kinds": kinds, "last": last,
            "pct": up / (up + down) if up + down else None,
            "median": seconds[len(seconds) // 2] if seconds else None}


def _why(kinds: dict, which) -> str:
    return ", ".join(f"{k} {n}" for k, n in sorted(kinds.items(), key=lambda kv: -kv[1]) if which(k))


def uptime_data() -> dict:
    """For bench.js: each row's 30 days, a day as [up, down, left out, why] or 0.
    Models on the bench now, decision models included."""
    u = uptime_rows()
    live = on_bench() | set(decide.load())

    def row(name: str, cells: list) -> dict:
        out = []
        for b in cells:
            if not b:
                out.append(0)
                continue
            down = _why(b[3], _down)
            left = _why(b[3], lambda k: not _down(k))
            out.append([b[0], b[1], b[2], "; ".join(filter(None, [down and "down: " + down,
                                                                  left and "left out: " + left]))])
        return {"name": name, "cells": out}
    return {"days": u["days"], "choices": list(UPTIME_DAYS),
            "providers": [row(c, cells) for c, cells in sorted(u["providers"].items())],
            "models": [row(m, cells) for m, cells in sorted(u["models"].items()) if m in live]}


def uptime_card() -> str:
    """Was the provider there when the bench called it: a cell a day, by
    provider or by model, as its By says; 7 days, or 30 on choice (bench.js swaps them)."""
    u = uptime_rows()
    first, gone, names = UPTIME_DAYS[0], retired(), {}

    def name(model: str) -> str:                       # each window's table shows it: worked out once
        if model not in names:
            names[model] = (("<tr class='retired'>" if model in gone else "<tr>")
                            + f"<td>{named(model)}</td><td>{esc(u['conn'].get(model, ''))}</td>")
        return names[model]

    def window(w: int) -> str:
        parts = []
        for title, rows, models in (("Providers", u["providers"], False), ("Models", u["models"], True)):
            totals = {k: _uptime_window(cells, w) for k, cells in rows.items()}
            totals = {k: t for k, t in totals.items() if t["up"] + t["down"] + t["out"]}
            body = ["<table><thead><tr>", th("Model" if models else "Provider"), th("Connection") if models else "",
                    th("Uptime"), th("Calls counted", True), th("Down"), th("Left out"), th("Reply time", True),
                    th("Last down"), "</tr></thead><tbody>"]
            for k in sorted(totals, key=lambda k: (2 if totals[k]["pct"] is None else totals[k]["pct"], k)):
                t, pct = totals[k], totals[k]["pct"]
                bar = (meter(pct, f"{pct * 100:.1f}%", pct < .8) if pct is not None
                       else "<span class='muted'>no counted calls</span>")
                down = _why(t["kinds"], _down)
                left = _why(t["kinds"], lambda kind: not _down(kind))
                body.append(
                    (name(k) if models else f"<tr><td>{esc(k)}</td>")
                    + f"<td data-sort='{-1 if pct is None else round(pct, 4)}'>{bar}</td>"
                    f"<td class='num'>{t['up'] + t['down']}</td>"
                    f"<td class='why' data-sort='{t['down']}'>{t['down']}"
                    + (f" <span class='muted'>· {esc(down)}</span>" if down else "") + "</td>"
                    f"<td class='why' data-sort='{t['out']}'>{t['out']}"
                    + (f" <span class='muted'>· {esc(left)}</span>" if left else "") + "</td>"
                    + (f"<td class='num' data-sort='{t['median']}'>{t['median']:.1f}s</td>" if t["median"]
                       else "<td class='num muted' data-sort='99999'>—</td>")
                    + (f"<td class='mono' data-sort='{t['last']}'>{esc(t['last'][5:16].replace('T', ' '))}</td>"
                       if t["last"] else "<td class='muted' data-sort=''>—</td>") + "</tr>")
            body.append("</tbody></table>")
            parts.append(f"<h3 class='chart-head'>{title}</h3>" + "".join(body))
        return (f"<div data-uptime-window='{w}'{'' if w == first else ' hidden'}>" + "".join(parts) + "</div>")

    def odd(w: int) -> str:
        shapes: dict[str, list] = {}
        for day, at, conn, n, shape, said in u["odd"]:
            if day >= len(u["days"]) - w:
                # By shape and what was decided of it: two texts this shape joins may not share a decision.
                s = shapes.setdefault((shape, said), [0, set(), at, at, said])
                s[0] += n
                s[1].add(conn)
                s[2], s[3] = min(s[2], at), max(s[3], at)
        if not shapes:
            return ""
        items = "".join(
            f"<li><span class='mono'>{n}×</span> {esc(shape)} <span class='muted'>· {esc(', '.join(sorted(conns)))},"
            f" {esc(a[5:10])}{'' if a[:10] == b[:10] else ' to ' + esc(b[5:10])}</span>"
            + (" {} <span class='muted'>{}</span>".format(
                chip(said.partition(", ")[0], "warn", "no rule knows it; counted as " + said),
                esc(said.partition(", ")[2])) if said else "") + "</li>"
            for (shape, _), (n, conns, a, b, said) in sorted(shapes.items(), key=lambda kv: -kv[1][0])[:12])
        decided, total = sum(s[0] for s in shapes.values() if s[4]), sum(s[0] for s in shapes.values())
        return (f"<div data-uptime-window='{w}'{'' if w == first else ' hidden'}>"
                f"<h3 class='chart-head'>Not recognised by a rule: {total} call{'s' * (total != 1)}"
                + (f", {decided} of them classed by a decision model" if decided else "") + "</h3>"
                f"<ul class='links'>{items}</ul></div>")

    legend = ("<div class='q-legend'><span><i style='background:var(--ok)'></i>all answered</span>"
              "<span><i style='background:var(--signal)'></i>some down, 80% or more answered</span>"
              "<span><i style='background:var(--err)'></i>under 80% answered</span>"
              "<span><i style='background:none;border:1px solid var(--line)'></i>no counted calls</span></div>")
    # The window and one view at a time: by provider (shorter, the default) or by model (bench.js).
    pick = ("<label>Days <select data-uptime-days>"
            + "".join(f"<option value='{w}'>last {w}</option>" for w in UPTIME_DAYS)
            + "</select></label><label>By <select data-uptime-by><option value='providers'>provider</option>"
            "<option value='models'>model</option></select></label>")
    how = ("<p>Only calls the bench makes anyway, each counted once: the daily probe, every gateway seat, and"
           " the suite's and onboarding's calls. A call that failed is down when the provider was not there, and"
           " left out when the bench caused it (its own quota or rate limit, its client, its LAN hub, a call it"
           " did not send). Shown only: uptime changes no routing and retires nothing.</p>"
           "<p>An error no rule in <code>suite/run.py</code> (<code>uptime_class</code>) knows is counted as down,"
           " unless a decision model was asked about its shape (the scheduler's <code>decisions</code> job, a few"
           " new shapes a day): then it is counted as the model said, and marked so here and in the hovers.</p>")
    return chart_card("Uptime", "Whether the provider was there when the bench called it, one cell a day.",
                      "chart-uptime-models", "".join(window(w) for w in UPTIME_DAYS),
                      how + "".join(odd(w) for w in UPTIME_DAYS),
                      extra=legend + "<div class='chart' id='chart-uptime-providers'></div>", tools=pick)


# ---- Decision models: how well each answers the bench's typed questions -----
#
# metrics/decide.py's benchmark (the scheduler's `decisions` job) writes
# metrics/decide-bench.json; this shows it, and which model each use goes to
# now (decide.choose), never asking one.

DECIDE_USES = {"shape": "The shape of a request (gateway)", "collection": "A collection for a request that names none",
               "notice": "A short reply that may be a provider's notice (gateway seats)",
               "errors": "Error shapes no uptime rule knows (daily job)", "role": "A new chat model's role (onboarding)"}
DECIDE_SETS = {"errors": "Error classes", "collection": "Collections", "useful": "Useful answers"}
DECIDE_KIND = {"errors": "choice of 6", "collection": "choice of the collections", "useful": "yes or no"}


def decision_data() -> dict:
    """For bench.js: each model's calibration bins per set, [said, right, n]."""
    res = decide.results()
    models = sorted((res.get("models") or {}).items())
    return {"sets": [[s, DECIDE_SETS[s]] for s in decide.SETS], "low": decide.MIN_ITEMS,
            # Right of asked per model and set: the bars, as the tables count it.
            "scores": [{"model": a, "set": s, "correct": r.get("correct", 0), "asked": r["asked"],
                        "answered": r.get("answered", 0), "brier": r.get("brier")}
                       for a, by in models for s, r in by.items()
                       if s in decide.SETS and isinstance(r, dict) and r.get("asked")],
            "points": [{"model": a, "set": s, "said": b["said"], "right": b["right"], "n": b["n"]}
                       for a, by in models for s, r in by.items()
                       if s in decide.SETS for b in r.get("calibration") or []]}


def _pct(x) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def decision_card() -> str:
    """Per decision model and set: right of asked, Brier, calibration, the
    cutoff's coverage and accuracy, seconds, failures; and who each use asks."""
    res = decide.results()
    measured, onboarded, low = res.get("models") or {}, decide.load(), decide.MIN_ITEMS
    uses = []
    for u in decide.USES:
        who, why = decide.choose(u, measured)
        uses.append(f"<li>{esc(DECIDE_USES[u])}"
                    + (" <span class='muted'>· per request</span>" if u in decide.PER_REQUEST else "") + ": "
                    + (f"<b>{esc(who)}</b>" if who else "<span class='muted'>nobody</span>")
                    + f" <span class='muted'>· {esc(why)}</span></li>")
    tables = []
    for s, title in DECIDE_SETS.items():
        info = (res.get("sets") or {}).get(s) or {}
        rows = {a: by[s] for a, by in measured.items() if isinstance(by.get(s), dict)}
        ranked = sorted((a for a, r in rows.items() if r["asked"] >= low and a in onboarded),
                        key=lambda a: -(rows[a]["correct"] + 1) / (rows[a]["asked"] + 2))
        body = ["<table><thead><tr>", th("Model"), th("Rank"), th("Right of asked", True), th("Accuracy", True),
                th("Brier", True), th("Calibration gap", True), th(f"At {decide.CONFIDENT}"), th("Seconds", True),
                th("Failures"), th("Last asked"), "</tr></thead><tbody>"]
        for a in sorted(rows, key=lambda a: (a not in ranked, ranked.index(a) if a in ranked else 0, a)):
            r, n = rows[a], rows[a].get("answered", 0)
            rank = (chip(f"{ranked.index(a) + 1} of {len(ranked)}", "ok" if ranked.index(a) == 0 else "",
                         f"ranked on {r['asked']} items asked") if a in ranked
                    else chip(f"below {low} items", "", f"{r['asked']} asked; a model is ranked from {low}")
                    if a in onboarded else chip("not onboarded", "", "no longer in config/decision-models.json"))
            acc = (f"{_pct(r.get('accuracy'))} <span class='muted'>of {n}</span>"
                   + (f" <span class='muted'>· F1 {r['macro_f1']:.2f}</span>" if r.get("macro_f1") is not None else "")
                   if n else "<span class='muted'>no answer</span>")
            sure = r.get("confident") or {}
            cut = (f"{_pct(sure.get('coverage'))} <span class='muted'>of {n} sure</span>, {_pct(sure.get('accuracy'))}"
                   f" <span class='muted'>of {round(sure['coverage'] * n)} right</span>"
                   if n and sure.get("coverage") is not None else "—")
            fails = sum((r.get("failures") or {}).values())
            body.append(
                f"<tr><td>{named(a)}</td><td>{rank}</td>"
                f"<td class='num' data-sort='{r['correct']}'>{r['correct']}/{r['asked']}</td>"
                f"<td class='num' data-sort='{r.get('accuracy') if n else -1}'>{acc}</td>"
                + (f"<td class='num' data-sort='{r['brier']}'>{r['brier']:.3f}</td>"
                   f"<td class='num' data-sort='{r['ece']}'>{r['ece']:.2f}</td>" if n else
                   "<td class='num muted' data-sort='9'>—</td><td class='num muted' data-sort='9'>—</td>")
                + f"<td class='why'>{cut}</td>"
                + (f"<td class='num'>{r['seconds']:.2f}</td>" if r.get("seconds") is not None
                   else "<td class='num muted'>—</td>")
                + f"<td class='why' data-sort='{fails}'>{fails}"
                + (f" <span class='muted'>· {esc(_why(r['failures'], lambda k: True))}</span>" if fails else "")
                + f"</td><td class='mono'>{esc((r.get('last') or '')[5:16].replace('T', ' '))}</td></tr>")
        if not rows:
            body.append("<tr><td colspan='10' class='muted'>No model asked yet.</td></tr>")
        body.append("</tbody></table>")
        labels = ", ".join(f"{k} {v}" for k, v in (info.get("labels") or {}).items())
        tables.append(f"<h3 class='chart-head'>{title}: {info.get('size', '?')} items"
                      f" <span class='muted'>({esc(DECIDE_KIND[s])}{'; ' + esc(labels) if labels else ''})</span></h3>"
                      + "".join(body))
    when = res.get("updated", "")
    how = (f"<p>{'Measured up to ' + esc(when[:16].replace('T', ' ')) + '.' if when else 'Not measured yet.'}"
           " The scheduler's <code>decisions</code> job asks each model one question a set a day;"
           " <code>python metrics/decide.py bench</code> runs a pass by hand. A model is ranked on a set once it"
           f" has been asked {low} of its items; until then its bar is faint.</p>")
    return chart_card("Decision", "How often the models that answer typed questions (a choice, a yes or no)"
                      " are right, and how sure.", "chart-decide", "".join(tables), how, retired=False,
                      after=f"<details class='key'><summary>Who each use asks now</summary>"
                            f"<ul class='links'>{''.join(uses)}</ul></details>")


def bughunt_card() -> str:
    """Seeded and real-bug tasks against their answer keys."""
    rows = {m: r["hunt"] for m, r in wrong_table().items() if m in current() and any(r["hunt"].values())}
    body = ["<table><thead><tr>", th("Model"), th("Found", True), th("Wrong line", True), th("Missed", True),
            th("Clean said clean", True), th("False alarm", True), "</tr></thead><tbody>"]
    for m in sorted(rows, key=lambda m: -rows[m]["found"]):
        h = rows[m]
        bugs, clean = h["found"] + h["wrong line"] + h["missed"], h["clean"] + h["false alarm"]
        body.append(f"<tr><td>{named(m)}</td>"
                    + "".join(f"<td class='num' data-sort='{h[k]}'>{h[k]}/{bugs}</td>" for k in ("found", "wrong line", "missed"))
                    + "".join(f"<td class='num' data-sort='{h[k]}'>{h[k]}/{clean}</td>" for k in ("clean", "false alarm"))
                    + "</tr>")
    body.append("</tbody></table>")
    how = ("<p>Seeded bugs and real ones, scored against their answer keys. Right of zero, the share of the bugs"
           " found, found on the wrong line, and missed; left of it, the share of the same files left clean on"
           " which the model raised a false alarm.</p><p>Ranked on what is proven: the low end of the found"
           " share's 95% range less the high end of the false alarms', so a few tasks cannot top the chart."
           " The number beside a name is its tasks; under 10, its bar is faint.</p>")
    return chart_card("Bug hunt", "Bugs found against false alarms on the same files left clean.",
                      "chart-hunt", "".join(body), how)


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """The 95% Wilson interval of k successes in n, as bench.js wilson() draws it."""
    if not n:
        return 0.0, 1.0
    p, z2 = k / n, z * z
    mid, half = p + z2 / (2 * n), z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return max(0.0, (mid - half) / (1 + z2 / n)), min(1.0, (mid + half) / (1 + z2 / n))


def work_card() -> str:
    """How often real offloads helped."""
    rows = work_rows()
    body = ["<table><thead><tr>", th("Model"), th("Offloads", True), th("Judged", True), th("Helped", True),
            th("Likely between", True), th("Verified findings", True), th("False claims", True), "</tr></thead><tbody>"]
    for r in sorted(rows, key=lambda r: (-_wilson(r["useful"], r["judged"])[0], -r["judged"])):
        lo, hi = _wilson(r["useful"], r["judged"])
        body.append(f"<tr><td>{named(r['model'])}</td><td class='num'>{r['offloads']}</td><td class='num'>{r['judged']}</td>"
                    f"<td class='num' data-sort='{r['useful'] / r['judged']:.3f}'>{r['useful']}/{r['judged']}</td>"
                    f"<td class='num' data-sort='{lo:.3f}'>{lo * 100:.0f}–{hi * 100:.0f}%</td>"
                    f"<td class='num'>{r['real']}</td><td class='num'>{r['false']}</td></tr>")
    body.append("</tbody></table>")
    how = ("<p>Work handed to a model through the bench and judged afterwards. The dot is the share that helped,"
           " sized by how many were judged; the line is where the true share likely sits (a 95% Wilson interval:"
           " wide for 3 of 3, narrow for 40 of 50). Ranked by the low end of that line, so a few lucky answers do"
           " not outrank many good ones. The dashed line is every model's work together.</p>")
    return chart_card("Real work", "How often a model helped when given real work that was checked afterwards.",
                      "chart-work", "".join(body), how)


def new_legend(rows: list[dict]) -> str:
    """Under Who to trust and Performance, while a model on them is new: what its ring means."""
    return (f"<p class='new-key' title='First scored in the last {NEW_DAYS} days'><i></i>new</p>"
            if any(r.get("new") for r in rows) else "")


def overview_card() -> str:
    rows = sorted(overview_rows(), key=lambda d: (d["wrong"], -d["recall"]))
    body = ["<table><thead><tr>", th("Model"), th("Finds real bugs", True), th("Makes things up", True),
            "</tr></thead><tbody>"]
    body += [f"<tr><td>{named(d['model'])}</td>"
             f"<td class='num' data-sort='{d['recall']:.3f}'>{d['found']} ({d['recall'] * 100:.0f}%)</td>"
             f"<td class='num' data-sort='{d['wrong']:.3f}'>{d['wrongN']} ({d['wrong'] * 100:.0f}%)</td></tr>"
             for d in rows]
    body.append("</tbody></table>")
    how = ("<p>A dot per model with both scores: the share of the real findings in our own code tasks it located"
           " (right), and the share of its answers that were confidently wrong (up is fewer). The shaded zone, 90%"
           " or more found and 15% or less made up, is where to pick a reviewer from. The models in or near it,"
           " and the three that make the most up, are named; hover a dot for any other. A model first scored in"
           f" the last {NEW_DAYS} days is new: its dot is ringed and named.</p>")
    return chart_card("Who to trust with a review", "Top right is the reviewer to pick: finds the real bugs"
                      " and makes little up.", "chart-overview", "".join(body), how, retired=False,
                      extra=new_legend(rows))


def suite_card() -> str:
    scores = suite_scores()
    live = current()
    scores = {(t, m): v for (t, m), v in scores.items() if m in live}
    tasks = sorted({t for t, _ in scores})
    models = sorted({m for _, m in scores})
    if not scores:
        body = "<p class='hint'>No suite runs recorded yet.</p>"
    else:
        # A cell per task and model, about 5,000: each as short as it can be (bench.css
        # table.grid sets the figures and the dash of a task not run).
        body = ["<table class='grid'><thead><tr>" + th("Model")
                + "".join(th(esc(t), True) for t in tasks)
                + th("Invented") + "</tr></thead><tbody>"]
        for model in models:
            cells, invented = [], []
            for task in tasks:
                hit = scores.get((task, model))
                if not hit:
                    cells.append("<td data-sort=-2></td>")
                    continue
                found = hit["found"]
                bad = found in ("timeout", "refused", "error")
                m = re.match(r"(\d+)/(\d+)", found)
                rank = f"{int(m.group(1)) / max(1, int(m.group(2))):.3g}" if m else "-1"
                cells.append(f"<td{' class=err' if bad else ''} data-sort={rank}>{esc(found)}</td>")
                if hit["invented"]:
                    invented.append(f"{task}: {hit['invented']}")
            flag = ("<td class='err'>" + esc("; ".join(invented)) + "</td>"
                    if invented else "<td class='muted'>none</td>")
            body.append(f"{tr(model)}<td>{named(model)}</td>"
                        + "".join(cells) + flag + "</tr>")
        body.append("</tbody></table>")
        body = "".join(body)
    how = ("<p>A square per task and model, coloured by the share of the task's findings it located: green all,"
           " red none, pale no score (a timeout, an error or a refusal). A dot marks an answer that invented"
           " something. The tasks are grouped by kind (hand-built, claims, seeded in a function or a whole file,"
           " real bugs), and the models ordered by all they found.</p>")
    return chart_card("Our code", "Every task on our own code, one square per task and model: green found"
                      " everything, red nothing.", "chart-code", body, how)


EXTERNAL = (("LiveBench", "https://livebench.ai/", "contamination-limited questions, refreshed monthly"),
            ("FreeLLMAPI", "https://freellmapi.co/models",
             "every free tier: limits, ranks and quirks per model (Discover leads with it)"),
            ("models.dev", "https://models.dev/", "specs and prices per provider (the catalog reads it daily)"),
            ("BullshitBench", "https://petergpt.github.io/bullshit-benchmark/viewer/index.next.html",
             "pushing back on nonsense; the idea behind confidently wrong"))


def external_card() -> str:
    """Links, not scores: the External tab showed FuncBench numbers nobody
    refreshed and was dropped (2026-10-16). Public leaderboards are read there."""
    rows = "".join(f"<li>{ext_link(u, n)} <span class='muted'>— {esc(why)}</span></li>" for n, u, why in EXTERNAL)
    return ("<div class='panel'><h2>External</h2><p class='sub'>Public leaderboards, for comparison;"
            " routing uses the bench's own tests.</p>"
            f"<ul class='links'>{rows}</ul></div>")


def wrong_card() -> str:
    """metrics/wrong.py: how often each model asserts something false."""
    import wrong
    from run import images_only
    rows = wrong_table()
    live, images = current(), images_only()
    body = ["<table><thead><tr>", th("Model"), th("Confidently wrong", True),
            *[th(k.capitalize(), True) for k in wrong.KINDS], th("Reviews", True), "</tr></thead><tbody>"]

    def cell(w, n):
        return (f"<td class='num' data-sort='{w / n:.3f}'>{w}/{n}</td>" if n
                else "<td class='num muted' data-sort='-1'>—</td>")
    order = sorted((a for a in rows if a in live and (rows[a]["answers"] or rows[a]["reviews"])),
                   key=lambda a: -(rows[a]["wrong"] / rows[a]["answers"] if rows[a]["answers"] else -1))
    for alias in order:
        r = rows[alias]
        if r["answers"]:
            rate = r["wrong"] / r["answers"]
            bar = meter(rate, f"{r['wrong']}/{r['answers']}", rate > .25)
        else:
            rate, bar = -1, ("<span class='muted'>reads images: no code tasks</span>" if alias in images
                             else "<span class='muted'>no tasks yet</span>")
        body.append(f"<tr><td>{named(alias)}</td><td data-sort='{rate:.3f}'>{bar}</td>"
                    + "".join(cell(*r["by"][k]) for k in wrong.KINDS)
                    + (f"<td class='num' data-sort='{r['review_false']}'>{r['review_false']} in {r['reviews']}</td>"
                       if r["reviews"] else "<td class='num muted' data-sort='-1'>—</td>") + "</tr>")
    body.append("</tbody></table>")
    how = ("<p>One chance per answer to our own code tasks: a clean control called faulty, a fault named on a line"
           " the change did not touch, a false claim called true, a planted falsehood asserted. A miss is not"
           " counted: saying nothing is cheap to check, inventing is not.</p><p>The thin line over each bar is"
           " where the true share likely sits (95% Wilson), so 0 of 2 shows how bad it could be, not a clean"
           " record. Worst proven first; the number beside a name is its answers, and under 10 its bar is faint."
           " The table's Reviews are apart: false claims recorded in its judged real work.</p>")
    return chart_card("Confidently wrong", "Who asserts things that are not true; shorter is better.",
                      "chart-wrong", "".join(body), how)


def benchmarks_tab() -> str:
    first = first_run()                        # a fresh install: its next step, in place of reports of nothing
    if first:
        return first
    data = json.dumps(charts_data(), ensure_ascii=False).replace("</", "<\\/")
    # Who to trust, then what moves day to day (Uptime, Trend, Performance), then Real work,
    # then the rest in the order they had, in as many columns as fit (bench.js columns).
    # Each report gets an id; beside them, a link to each, the one in view marked (bench.js
    # jumpSpy), and what the bench holds now.
    reports = (("trust", "Who to trust", overview_card), ("uptime", "Uptime", uptime_card),
               ("trend", "Trend", trend_card), ("performance", "Performance", perf_card),
               ("work", "Real work", work_card), ("hunt", "Bug hunt", bughunt_card),
               ("wrong", "Confidently wrong", wrong_card), ("code", "Our code", suite_card),
               ("vision", "Vision", vision_card), ("decisions", "Decision", decision_card),
               ("external", "External", external_card))
    links = "".join(f"<li><a href='#report-{key}'>{esc(label)}</a></li>" for key, label, _ in reports)
    cards = "".join(card().replace("<div class='panel'", f"<div class='panel report' id='report-{key}'", 1)
                    for key, _, card in reports)
    return (f"<div class='cols'>{cards}</div>"
            "<aside class='facts' id='facts-suite' aria-labelledby='facts-suite-h'>"
            "<div class='bar'><h2 id='facts-suite-h'>On this page</h2>"
            "<button type='button' class='linkish facts-close'>Close</button></div><div class='facts-body'>"
            f"<nav class='jump' aria-labelledby='facts-suite-h'><ol>{links}</ol></nav>"
            f"<h3>The bench now</h3><dl class='rows'>{bench_now()}</dl></div></aside>"
            f"<script type='application/json' id='charts-data'>{data}</script>")


def bench_now() -> str:
    """Benchmarks' facts beside its reports: counts of what the dashboard reads anyway."""
    directory = model_directory()
    chat = [a for a, d in directory.items() if d.get("kind") != "decision"]
    rows = (("Models on the bench", len(chat)), ("Decision models", len(directory) - len(chat)),
            ("Connections", len({catalog.connection_of(a) for a in directory})),
            ("Endpoints", len(endpoints.load())), ("Collections", len(bench_collections.load())),
            ("Ad hoc only", sum(1 for d in directory.values() if d.get("ad_hoc"))),
            ("Retired", len(retired())), ("Action needed", f"{open_count(open_actions()[0])} open"))
    return "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows)


def perf_card() -> str:
    perf = perf_rows()
    if not perf:
        body = "<p class='hint'>No perf runs recorded yet.</p>"
    else:
        live = current()
        latest = {(r["model"], r["prompt"]): r for r in perf
                  if r["model"] in live}
        widest = max((float(r["p50_s"]) for r in latest.values()), default=1) or 1
        body = ["<table><thead><tr>" + th("Model") + th("Prompt") + th("<abbr title='median seconds'>p50</abbr>", True)
                + th("<abbr title='95th percentile, seconds'>p95</abbr>", True)
                + th("<abbr title='characters a second'>ch/s</abbr>", True)
                + th("<abbr title='tokens a second'>tok/s</abbr>", True)
                + th("<abbr title='answered / refused / error / timeout'>Outcomes</abbr>")
                + "<th>p50, relative</th></tr></thead><tbody>"]
        for (model, prompt), row in sorted(latest.items()):
            p50 = float(row["p50_s"])
            width = max(2, round(p50 / widest * 120))
            oc = (f"{row['answered']}a / {row['refused']}r / "
                  f"{row['error']}e / {row['timeout']}t")
            bad = int(row["timeout"]) or int(row["error"])
            body.append(
                f"{tr(model)}<td>{named(model)}</td><td>{esc(prompt)}</td>"
                f"<td class='num'>{p50:.1f}s</td>"
                f"<td class='num'>{float(row['p95_s']):.1f}s</td>"
                f"<td class='num'>{esc(row['chars_per_s'])}</td>"
                f"<td class='num'>{esc(row.get('tokens_per_s') or '—')}</td>"
                f"<td class='{'err' if bad else ''}'>{esc(oc)}</td>"
                f"<td><span class='meter' style='width:{width}px' "
                f"title='p50 {p50:.1f}s'><span style='width:100%'></span></span></td></tr>")
        body.append("</tbody></table>")
        body = "".join(body)
    how = ("<p>A dot per model: its median seconds an answer on our own code tasks (on a log scale) against the"
           " share of their findings it located. The zone, 90% or more found in 40 seconds or less, is the quick"
           " and thorough corner; the models in or near it are named, hover a dot for the rest. The table is the"
           " daily probe instead: each model's latest timings per prompt.</p>")
    return chart_card("Performance", "How fast each model answers our own tasks, against how much it finds.",
                      "chart-speed", body, how, extra=new_legend(perf_charts()["speed"]))


def favicon(theme: str) -> str:
    """The tab icon: the sidebar's bench, its seat in the theme's brand colour
    (read from the theme's css; a tab cannot read the page's variables)."""
    m = re.search(r"--brand:\s*(#[0-9a-fA-F]{3,8})\b", read(UI / "themes" / f"{theme}.css"))
    seat = m.group(1) if m else "#990000"
    svg = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
           "<rect x='6' y='15' width='3.5' height='14' rx='.8' fill='#3a3530'/>"
           "<rect x='22.5' y='15' width='3.5' height='14' rx='.8' fill='#3a3530'/>"
           "<rect x='6' y='23' width='20' height='2.4' fill='#3a3530'/>"
           f"<rect x='1.5' y='9' width='29' height='7.5' rx='1.6' fill='{seat}'/></svg>")
    from urllib.parse import quote
    return f"<link rel='icon' type='image/svg+xml' href='data:image/svg+xml,{quote(svg)}'>"


def render(theme: str | None = None, shown: str = "suite") -> str:
    with locked.snapshot():                    # each file read once a render (metrics/locked.py)
        data_key()                             # before any file is read (kept)
        return _page(theme, shown)


def _page(theme: str | None, shown: str = "suite") -> str:
    theme = theme if theme in themes() else saved_theme()      # a ?theme preview, else the saved one
    shown = page_for(shown)
    name = next(label for key, label, _ in TABS if key == shown)
    return (
        f"<!doctype html><html lang='en' data-theme='{esc(theme)}'><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{'' if shown == 'suite' else esc(name) + ' · '}Benchwarmers</title>" + favicon(theme) +
        f"<style>{brand_css()}</style><link rel='stylesheet' href='{versioned('bench.css')}'>"
        f"{tabs(shown, theme)}<script>window.BENCH={directory_json()};</script>"
        # The charts' library (d3, Observable Plot) is asked by bench.js when a page with
        # charts is first shown, not by every page (bench.js needPlot).
        f"<script src='{versioned('bench.js')}'></script></html>")


def gateway_card(limit: int = 30) -> str:
    """Recent bench-gateway requests: who sat, how each seat went, and which
    verdicts are still owed. A verdict owed is evidence the router is waiting
    for; this is where it shows."""
    records = []
    for line in _log_rows(DATA / "runs" / "gateway" / "calls.jsonl"):     # newest first, from its end
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
        if len(records) >= limit:
            break
    try:
        from offload import entries
        judged = {e["call"]: e.get("useful") for e in entries() if e.get("call")}
    except Exception:                                  # noqa: BLE001
        judged = {}
    owed = seats_total = 0
    body = ["<table><thead><tr>", th("When"), th("Collection"), th("Asked by"), th("Explorer shape"),
            th("Seats"), th("Time", True), "</tr></thead><tbody>"]
    for r in records:
        chips, row_owed = [], 0
        for seat in r.get("seats", []):
            seats_total += 1
            verdict = judged.get(seat.get("call"))
            if verdict is None:
                row_owed += 1
            mark = {True: " ✓", False: " ✗", None: ""}[verdict]
            css = {"ok": "ok", "empty": "warn", "error": "err"}.get(seat.get("status"), "")
            why = (f"{seat.get('seat')} · {seat.get('status')} in {seat.get('seconds')}s · "
                   + ({True: "useful", False: "not useful", None: "verdict owed"}[verdict])
                   + f" · {seat.get('call')}")
            # A button: it opens the answer with the verdict form (bench.js).
            chips.append(f"<button type='button' class='chip {css} seat' data-call='{esc(seat.get('call', ''))}'"
                         f" title='{esc(why)}'>{esc(seat.get('alias', ''))}{mark}</button>")
        # Members an image could not be sent to: not asked, so no call and no verdict.
        chips += [f"<span class='chip muted' title='{esc('not asked: ' + str(k.get('why', '')))}'>"
                  f"{esc(k.get('alias', ''))} –</span>" for k in r.get("skipped", []) if isinstance(k, dict)]
        owed += row_owed
        # The request's own time: its seats ran at once, so the slowest of
        # them, then the merge (a merger seat answers after the rest).
        secs = [(s.get("seat"), float(s.get("seconds") or 0)) for s in r.get("seats", [])]
        panel = max((t for seat, t in secs if seat != "merger"), default=0.0)
        merge = sum(t for seat, t in secs if seat == "merger")
        took = (f"<td class='num' data-sort='{panel + merge:.1f}' title='slowest seat {panel:.1f}s"
                f"{f' + merge {merge:.1f}s' if merge else ''}'>{panel + merge:.0f}s</td>" if secs
                else "<td class='num muted' data-sort='-1'>—</td>")
        body.append(
            f"<tr><td class='mono' data-sort='{esc(r.get('at', ''))}'>{esc(r.get('at', '')[:16].replace('T', ' '))}</td>"
            f"<td class='mono'>{esc(r.get('model', ''))}</td>"
            f"<td class='mono'>{esc(r.get('key') or '—')}</td>"
            f"<td title='{esc(r.get('shape_by', ''))}'>{esc(r.get('shape', ''))}</td>"
            f"<td>{' '.join(chips)}</td>{took}</tr>")
    if not records:
        body.append("<tr><td colspan='6' class='muted'>No gateway requests yet. Call a collection"
                    f" at <code>{esc(gateway_url())}</code>.</td></tr>")
    body.append("</tbody></table>")
    key = """<dl>
      <dt>Seats</dt><dd>Every model that answered the request, members first, then
        explorers, then the merger if the reply was merged. Green answered, amber came back empty, red failed. ✓ judged
        useful, ✗ judged not useful; no mark means the verdict is still owed.
        Hover a chip for its seat, timing and call id; click it to read the answer
        and record a verdict. A grey chip with a dash is a member not asked: the request
        carried images it cannot take, or it is a vision specialist and the request
        carried none. Hover it for why.</dd>
      <dt>Asked by</dt><dd>The gateway key that made the request, or <code>local</code>
        when no key is asked for.</dd>
      <dt>Time</dt><dd>How long the caller waited: the seats run at once, so the
        slowest of them, plus the merge when the reply was merged. Hover for the two
        parts; a chip's own time is on the chip.</dd>
      <dt>Verdicts owed</dt><dd>A chip with no mark. Each is recorded as an unjudged
        offload, so rotation still counts it; a verdict tells the router whether
        it helped. The orchestrator that called can also record one:
        <code>python metrics/offload.py verdict &lt;call id&gt; --useful</code>.</dd>
      <dt>Explorer shape</dt><dd>Which models could fill explorer seats; hover for
        how it was chosen (set on the collection, or a decision model's label).</dd>
    </dl>"""
    return card("Gateway", f"The last {len(records)} bench-gateway requests:"
                f" {seats_total} seats, <span id='g-owed' data-n='{owed}'>{owed} verdict{'s' * (owed != 1)} owed</span>.",
                "".join(body) + route_table(judged), key)


def route_table(judged: dict, limit: int = 30) -> str:
    """Route-mode conversations (runs/gateway/routes.jsonl, a line a turn),
    one row each, apart from the panels: an agent turn is not a seat, and a
    conversation's verdict is optional, so none of this counts as Owed.
    ponytail: reads the whole log; read its tail once it grows large."""
    convs: dict[str, dict] = {}
    for line in read(DATA / "runs" / "gateway" / "routes.jsonl").splitlines():
        try:
            t = json.loads(line)
        except ValueError:
            continue
        c = convs.setdefault(str(t.get("conversation", "")), {
            "at": t.get("at", ""), "model": t.get("model", ""), "key": t.get("key", ""), "task": t.get("task", ""),
            "turns": 0, "tools": 0, "failovers": 0, "models": {}})
        c["turns"] += 1
        c["tools"] += t.get("tool_calls") or 0
        c["failovers"] += len(t.get("tried") or [])
        c.update(last=t.get("at", ""), status=t.get("status", ""), error=t.get("error", ""))
        if t.get("alias"):
            c["models"].setdefault(t["alias"], t.get("call", ""))
    if not convs:
        return ""
    rows = ["<h3>Route conversations</h3><table><thead><tr>", th("When"), th("Collection"), th("Asked by"),
            th("First message"), th("Answered by"), th("Turns", True), th("Tool calls", True),
            th("Failovers", True), th("Last turn"), "</tr></thead><tbody>"]
    for conv, c in sorted(convs.items(), key=lambda kv: kv[1]["last"], reverse=True)[:limit]:
        chips = " ".join(f"<span class='chip' title='{esc(call + ' · ' + {True: 'useful', False: 'not useful', None: 'no verdict (optional)'}[judged.get(call)])}'>"
                         f"{esc(alias)}{ {True: ' ✓', False: ' ✗', None: ''}[judged.get(call)]}</span>"
                         for alias, call in c["models"].items())
        css = {"ok": "ok", "empty": "warn", "error": "err"}.get(c["status"], "")
        rows.append(
            f"<tr><td class='mono' data-sort='{esc(c['at'])}' title='{esc(conv)}'>{esc(c['at'][:16].replace('T', ' '))}</td>"
            f"<td class='mono'>{esc(c['model'])}</td><td class='mono'>{esc(c['key'] or '—')}</td>"
            f"<td>{esc(c['task'][:60])}</td><td>{chips or '—'}</td><td class='num'>{c['turns']}</td>"
            f"<td class='num'>{c['tools']}</td><td class='num{' muted' if not c['failovers'] else ''}'>{c['failovers']}</td>"
            f"<td><span class='chip {css}' title='{esc(c['error'])}'>{esc(c['status'])}</span></td></tr>")
    rows.append("</tbody></table><p class='muted'>Each conversation is one unjudged offload per model that"
                " answered it, under the call id on its chip; a verdict is optional:"
                " <code>python metrics/offload.py verdict &lt;call id&gt; --useful</code>.</p>")
    return "".join(rows)


def gateway_seat(call: str) -> dict:
    """One seat of a logged gateway request, with its latest verdict, for the
    verdict dialog. Searched from the log's end, where a recent seat is: the
    log runs to 50 MB, read whole each time the dialog opened."""
    panel = call.rsplit(".", 1)[0]
    for line in _from_end(DATA / "runs" / "gateway" / "calls.jsonl"):
        if not panel or f'"{panel}"' not in line:           # skip without parsing
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        seat = next((s for s in r.get("seats", []) if s.get("call") == call), None)
        if seat is None:
            continue
        from offload import entries
        last = next((e for e in reversed(entries()) if e.get("call") == call), {})
        return {"ok": True, "call": call, "model": r.get("model", ""), "at": r.get("at", ""),
                "prompt": r.get("prompt", "")[:20000], "seat": seat.get("seat", ""),
                "alias": seat.get("alias", ""), "status": seat.get("status", ""),
                "seconds": seat.get("seconds"), "answer": seat.get("answer", ""),
                "error": seat.get("error", ""), "useful": last.get("useful"),
                "note": last.get("note", ""),
                "real": last.get("real") or [], "false": last.get("false") or []}
    return {"ok": False, "errors": [f"no gateway seat with call id {call!r}"]}


def record_verdict(call: str, useful, note: str, real, false) -> dict:
    """offload.verdict from the dashboard: same log, same rules."""
    errors = []
    if not isinstance(useful, bool):
        errors.append("choose useful or not useful")
    claims = {}
    for key, value in (("real", real), ("false", false)):
        if not isinstance(value or [], list) or not all(isinstance(x, str) for x in value or []):
            errors.append(f"{key} claims must be a list of sentences")
            continue
        claims[key] = [" ".join(x.split())[:400] for x in value or [] if x.strip()][:20]
    if errors:
        return {"ok": False, "errors": errors}
    import offload
    try:
        e = offload.verdict(call, useful, " ".join(str(note or "").split())[:500],
                            claims["real"], claims["false"], replace=True)
    except KeyError as exc:
        return {"ok": False, "errors": [str(exc.args[0])]}
    return {"ok": True, "changed": [f"{e['model']} {'useful' if useful else 'not useful'}"], "sync": ""}


_DISCOVER: dict = {}


def _discover_rows() -> list[dict]:
    """metrics/discover.py's rows, built once per change to what they read:
    the daily feeds, the registry and endpoints (an onboarding moves a model
    to "on bench" and a new endpoint adds routes), and connections.json
    (Settings' free-only-in-their-own-app switches)."""
    import discover
    import sources
    paths = [sources.CACHE / f"{n}.json" for n in [*sources.FEEDS, discover.LISTS]] + [
        endpoints.YAML, endpoints.FILE, endpoints.CONNECTIONS]
    stamp = tuple(p.stat().st_mtime if p.exists() else 0 for p in paths)
    if _DISCOVER.get("stamp") != stamp:
        _DISCOVER.update(stamp=stamp, rows=discover.build())
    return _DISCOVER["rows"]


def discover_model(provider: str, model: str) -> dict:
    """One Discover row for its popup, with every route this install has to
    the same model: through each provider that offers it (`norm`, as
    discover.py matches them), via Open WebUI and via an endpoint of its own,
    and through any LiteLLM or CLI Proxy API hub, which is asked here."""
    rows = _discover_rows()
    row = next((r for r in rows if r["provider"] == provider and r["model"] == model), None)
    if row is None:
        return {"ok": False, "errors": [f"Discover has no {model!r} from {provider!r}; reload the page"]}
    same = [row] + [r for r in rows if r["norm"] == row["norm"] and r is not row]
    import discover
    hubs, errors = discover.hub_routes(same)
    routes = [{**x, "provider": r["provider_name"], "model": r["model"], "free": r["free"],
               "cost": r.get("cost"), "limits": r.get("limits")}
              for r, more in zip(same, hubs) for x in r["routes"] + more]
    return {"ok": True, "errors": errors, "model": row, "routes": routes}


def discover_refresh(who: str = "ui") -> dict:
    """Discover's Refresh: every list fetched now (discover.refresh), for a
    model too new to have waited for the daily fetch. A list that would not
    come keeps its last copy and is said; the rest are still used."""
    import discover
    said = discover.refresh(force=True)
    _log_change(datetime.datetime.now().astimezone().isoformat(timespec="seconds"), who, "", "discover-refresh",
                None, "; ".join(said) or "every list fetched")
    return {"ok": True, "errors": said}


# Discover's columns (bench.js dRow draws them): key, heading, class, what it means. An
# `opt` column leaves a narrow list (the panel has it), a `wide` one joins a wide list; `dim` is muted.
DISCOVER_COLS = (
    ("name", "Model", "", ""), ("rank", "Rank", "num opt", "FreeLLMAPI's smartest-first rank; hover one for more"),
    ("provider", "Provider", "", ""), ("reach", "Reach", "", "How near it is: on the bench, connected, easy to add, other"),
    ("price", "Price", "", "Free, or dollars per million tokens, input / output"),
    ("context", "Context", "num", "Context window, in thousands of tokens"),
    ("io", "In → out", "opt dim", "What it takes in and gives out"),
    ("caps", "Can", "", "Calls tools, reads images (vision), reasons before answering"),
    ("released", "Released", "opt dim", ""),
    ("limits", "Limits", "opt dim", "The free tier's: requests or tokens a minute (rpm, tpm) or a day (rpd, tpd)"),
    ("output", "Max output", "num wide", "The longest answer, in tokens"),
    ("knowledge", "Knowledge", "wide dim", "Knowledge cutoff"), ("weights", "Weights", "wide dim", "Open or closed weights"),
    ("listed", "Listed by", "wide dim", ""), ("seen", "First seen", "wide dim", "A dash: before the lists began"))
REACH_CSS = {"on bench": "ok", "connected": "ok", "easy to add": "warn", "other": ""}


def discover_data(rows: list[dict]) -> str:
    """Discover's rows as data for bench.js (dApply filters, counts and sorts
    them and draws the rows shown): their HTML was 2 MB, and every row in
    the page was restyled on each key typed. Only what the table shows, with
    the display text worked out here; each row a list in the order of
    `fields`, an empty value 0 (bench.js dRead makes them objects again)."""
    fields, out = None, []
    for r in rows:
        caps = [c for c, on in (("tools", r["tools"]), ("vision", r["vision"]), ("reasons", r["reasoning"])) if on]
        cost = r.get("cost") or {}
        lim = r.get("limits") or {}
        on = ([f"on bench as {r['on_bench_as']}"] if r.get("on_bench_as") else []) + (
            [f"also on bench as {', '.join(r['also_on'])}"] if r.get("also_on") else [])
        row = {"provider": r["provider"], "model": r["model"], "name": r["name"], "new": r["new"],
               "seen": "" if r["first_seen"] == "baseline" else r["first_seen"], "on": " · ".join(on),
               "rank": r.get("rank"),
               "rank_title": f"FreeLLMAPI: smartest #{r['rank']}" + (f", fastest #{r['speed']}" if r.get("speed") else "")
               + (f", {r['size']}" if r.get("size") else "") if r.get("rank") is not None else "",
               "reach": r["reach"],
               "price": "free" if r["free"] else "unknown" if r["free"] is None else
               f"{cost.get('input', '?')} / {cost.get('output', '?')}" if cost else "paid",
               "context": r.get("context"), "io": f"{', '.join(r['in_mods'])} → {', '.join(r['out_mods'])}",
               "caps": " ".join(caps), "released": r.get("released"),
               "limits": " · ".join([f"{v:,} {k}" for k, v in lim.items()]
                                    + ([f"{r['budget']} tokens a month"] if r.get("budget") else [])),
               "add": any(not x["on_bench_as"] for x in r["routes"]),
               "output": r.get("output"), "knowledge": r.get("knowledge"),
               "weights": {True: "open", False: "closed"}.get(r.get("open_weights")),
               "listed": ", ".join({"freellmapi": "FreeLLMAPI", "provider": "own list"}.get(s, s) for s in r["sources"])}
        fields = fields or list(row)
        values = [v or 0 for v in row.values()]
        while values and values[-1] == 0:
            values.pop()
        out.append(values)
    # Each provider's name, and its OpenAI-compatible base URL where one is known (its Reach chip's title).
    return json.dumps({"fields": fields, "providers": {r["provider"]: r["provider_name"] for r in rows},
                       "apis": {r["provider"]: r["api"] for r in rows if r.get("api")}, "rows": out},
                      ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def discover_card() -> str:
    """Models worth adding, from FreeLLMAPI and models.dev (metrics/discover.py),
    deduped. Read from the daily cache, so the page never waits on the network.
    The rows arrive as data (discover_data); the facets beside them, a fold
    on a narrow window. A row opens beside the list (bench.js openDiscover),
    which asks /api/discover-model for the rest: its routes are where Add is.
    With none open, the panel says what the catalog is."""
    all_rows = _discover_rows()
    rows = [r for r in all_rows if r["free"] or r["reach"] in ("connected", "on bench")]
    # New models first, the newest at the top; the rest keep discover.py's order (a stable sort).
    rows.sort(key=lambda r: r["first_seen"] if r["new"] else "", reverse=True)
    hidden = len(all_rows) - len(rows)
    free = sum(1 for r in rows if r["free"])
    if _DISCOVER.get("data_of") is not all_rows:          # once per new list of rows: 40 ms
        _DISCOVER.update(data_of=all_rows, data=discover_data(rows))
    import discover
    import sources
    ages = [a for a in map(sources.age_s, [*sources.FEEDS, discover.LISTS]) if a is not None]
    # A checkbox per value of each facet, matched against the rows by bench.js (dApply),
    # which also fills in the counts and pages the list.
    def group(title: str, facet: str, values) -> str:
        return (f"<fieldset><legend>{esc(title)}</legend>" + "".join(
            f"<label><input type='checkbox' data-df='{facet}' value='{esc(v)}'> {esc(label)}"
            "<span class='n'></span></label>" for v, label in values) + "</fieldset>")
    provs = {r["provider"]: r["provider_name"] for r in rows}
    rail = ("<details class='facets' id='d-facets'><summary>Filters</summary>"
            + group("Show", "new", [("1", "New")])
            + group("Price", "price", [("free", "Free"), ("paid", "Paid and unpriced")])
            + group("Reach", "reach", [(v, v.capitalize()) for v in REACH_CSS])
            + group("Can", "caps", [(v, v.capitalize()) for v in ("tools", "vision", "reasons")])
            + group(f"Provider · {len(provs)}", "provider",
                    sorted(provs.items(), key=lambda kv: kv[1].lower()))
            + "</details>")
    heads = "".join(f"<th data-sortable tabindex='0' data-k='{k}'" + (f" class='{css}'" if css else "")
                    + (f" title='{esc(why)}'" if why else "") + f">{esc(label)}</th>" for k, label, css, why in DISCOVER_COLS)
    age = ("never fetched" if not ages else f"{max(ages) / 60:.0f} minutes old" if max(ages) < 5400
           else f"{max(ages) / 3600:.0f} hours old")
    facts = (("Listed", f"{len(rows):,} models, {free:,} free"),
             ("Left out", f"{hidden:,} paid, with no route to them"),
             ("Sources", "FreeLLMAPI, models.dev and your Open WebUI connections' own lists, deduped"),
             ("Oldest list", f"<span id='d-refreshed'>{age}</span>; all are fetched daily"),
             ("New", "first seen in the last 7 days"))
    key = """<dl>
      <dt>Rank</dt><dd>FreeLLMAPI's smartest-first order (1 is the smartest it lists); within
        each reach the list follows it. Hover for its fastest-first rank and size. A dash where
        only models.dev lists the model.</dd>
      <dt>Provider</dt><dd>Who serves the model: a row per provider, so the same model
        from OpenRouter and from NVIDIA is two rows. Hover for its id.</dd>
      <dt>Reach</dt><dd><em>on bench</em> already registered; <em>connected</em> this
        install has a route to the provider (an Open WebUI connection, or an endpoint of its
        own); <em>easy to add</em> an OpenAI-compatible API, so one new connection away (hover
        for its base URL); <em>other</em> a native API or an unvetted reseller.</dd>
      <dt>Price</dt><dd>Free if FreeLLMAPI lists it (a catalog of free tiers) or models.dev
        prices it at 0: models.dev shows the PAID price even where a free tier exists. Otherwise
        input / output per million tokens. <em>Unknown</em> where only the provider's own list has
        the model and it says nothing of price; those are under <em>Paid and unpriced</em>.</dd>
      <dt>Limits</dt><dd>The free tier's, from FreeLLMAPI: requests and tokens per minute (rpm,
        tpm) and per day (rpd, tpd), and roughly how many tokens a month that allows.</dd>
      <dt>New</dt><dd>First seen in either list in the last 7 days.</dd>
      <dt>Add</dt><dd>Open a row: the panel beside the list has everything known about the model
        and every route this install has to it, each with <em>Add via this</em>. <em>Via Open WebUI</em>
        adds the model to that connection's Model IDs (when it lists them), creates its model
        record public, tagged like the connection's other models and with their logo, then runs
        the bench's onboarding as the confirm chooses (Onboard as, Vision model), so it is in
        the chats too.
        <em>Via LiteLLM</em> or <em>CLI Proxy API</em> adds it to that hub's connection (one more
        deployment, or the provider's models list); those hubs are asked when the panel opens.
        <em>Direct</em> onboards it on the provider's own endpoint: bench only. A new provider
        needs a connection or an endpoint first; hover its <em>easy to add</em> chip for the
        base URL.</dd>
      <dt>Sources</dt><dd>FreeLLMAPI's free catalog first: where it lists a model, its name,
        provider, context, tools, limits, ranks and quirks lead, and the panel links its
        freellmapi.co page. models.dev fills the rest and alone has the paid models, prices,
        base URLs and modalities; the panel links its page for the model. A model one provider
        offers in both is shown once. Then each Open WebUI connection's own model list, for
        what the provider serves that neither catalog has yet. All are fetched daily;
        <em>Refresh lists</em> fetches them now.</dd>
    </dl>"""
    return (f"<div class='discover'>{rail}<div class='list'><table id='d-table'><thead><tr>{heads}<th class='opt'></th></tr>"
            "</thead><tbody></tbody></table>"
            "<button class='btn small' type='button' id='d-more' hidden>Show more</button></div></div>"
            f"<script type='application/json' id='d-data'>{_DISCOVER['data']}</script>"
            # One model, docked beside the list (bench.js openDiscover fills it): what is known, the routes to add it by.
            "<dialog class='dock' id='discover-dialog' aria-labelledby='d-title'>"
            "<div class='bar'><p>In the catalog</p></div><div class='dock-body'>"
            "<h2 class='dock-head' id='d-title' tabindex='-1'></h2><dl class='rows' id='d-profile'></dl>"
            "<h3>Routes on this install</h3><p class='sub'>Add via this puts it on the bench that way, and into the hub"
            " first where it goes through one.</p>"
            "<div id='d-routes'></div><div class='errors' id='d-errors' role='alert'></div></div>"
            "<div class='dock-foot'><button class='btn' type='button' id='d-close'>Close</button></div></dialog>"
            # With none open: what the catalog is (bench.css aside.facts).
            "<aside class='facts' id='facts-discover' aria-labelledby='facts-discover-h'>"
            "<div class='bar'><h2 id='facts-discover-h'>About the catalog</h2>"
            "<button type='button' class='linkish facts-close'>Close</button></div><div class='facts-body'>"
            "<dl class='rows'>" + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in facts) + "</dl>"
            "<p>Open a model for everything known about it and the routes to add it by.</p>"
            f"<details class='key'><summary>How to read this</summary>{key}</details></div></aside>")


_CAN = {"reasoning": "reason", "vision": "take images", "tools": "call tools",
        "structured_output": "give structured output"}


def _ctx(n: int) -> str:
    """512k, 1M (2^20), 1.9M: a context window as bench.js tokens() writes it."""
    return (str(math.floor(n / 104857.6 + 0.5) / 10).removesuffix(".0") + "M" if n >= 1_000_000
            else f"{int(n // 1000)}k")


def collection_list(rows: list[dict]) -> str:
    """The collections, a row each (a click opens one beside the list): its
    name, what it is for and its tags; its panel as it sits (a seat per member,
    one per explorer, then the merger or the route after an arrow); and what
    its members can do together. bench.js fetches the page again after a change."""
    out = []
    for row in rows:
        name = esc(row["name"])
        seats = ("".join(f"<span class='seat'>{esc(m)}</span>" for m in row["members"])
                 + (f"<span class='seat explorer'>explorer{' · ' + esc(row['shape']) if row['shape'] else ''}</span>"
                    * row["explorers"])
                 + ("<span class='merge'><span aria-hidden='true'>→</span><span class='seat merger'>"
                    f"<span class='k'>merger</span>{esc(row['merger'] or 'chosen by verdicts')}</span></span>"
                    if row["reply"] == "merged" else
                    "<span class='merge'><span aria-hidden='true'>→</span><span class='seat merger'>"
                    "<span class='k'>route</span>one member, by its record</span></span>"
                    if row["reply"] == "route" else ""))
        t = row.get("together") or {"context_window": None, "context_unknown": [], "capabilities": [],
                                    "limits": {}, "connections": []}
        lim = [f"{v:,}".removesuffix(".0") + f" {k}" for k, v in (t.get("limits") or {}).items() if v]
        can = [_CAN.get(k, k) for k in t["capabilities"]]
        n = len(t["connections"])
        together = (
            (f"<span>Up to <span class='mono'>{_ctx(t['context_window'])}</span> tokens</span>" if t["context_window"]
             else "<span class='muted'>Context window unknown</span>")
            + (f"<span class='muted'>not known for {esc(', '.join(t['context_unknown']))}</span>"
               if t["context_unknown"] else "")
            + (f"<span>All can {esc(', '.join(can))}</span>" if can
               else "<span class='muted'>Nothing known they all can do</span>")
            + (f"<span>At most {esc(', '.join(lim))}</span>" if lim else "<span class='muted'>No limits known</span>")
            + f"<span class='muted'>across {n} connection{'' if n == 1 else 's'}</span>")
        out.append(
            f"<tr class='c-row' data-name='{name}'><td><button type='button' class='c-open alias'"
            f" aria-label='Open bench/{name}'>bench/{name}</button>"
            + ("<span class='chip'>ad hoc: only when called by name</span>" if row["ad_hoc"] else "")
            + (f"<p class='desc'>{esc(row['description'])}</p>" if row["description"] else "")
            + ("<p class='tags'>" + "".join(f"<span class='chip'>{esc(x)}</span>" for x in row["tags"]) + "</p>"
               if row["tags"] else "")
            + f"</td><td><div class='seats'>{seats}</div></td><td class='together'>{together}</td></tr>")
    if not out:
        return empty_state("No collection yet.", step_button(), "A collection is a name callers ask for, and two to"
                           " four models that answer together; New collection makes one.")
    return ("<table class='c-table'><thead><tr>" + th("Collection") + "<th>Panel</th>"
            "<th title='What every member takes and does, as the catalog says: the longest prompt, what they"
            " all can do, and the lowest rate limits among their connections (requests or tokens a minute"
            " or a day: rpm, rpd, tpm, tpd)'>Together</th></tr></thead><tbody>" + "".join(out) + "</tbody></table>")


def collections_tab() -> str:
    """Every collection, a row each; one clicked, or New collection (in the
    bar, BAR_TOOLS), opens beside the list in the docked panel (bench.js
    fillForm). With none open, the panel holds what applies to every
    collection: its facts and the gateway settings. The list arrives with the
    page, and the page is fetched again after a change."""
    view = collections_view()
    s, pick, conf = view["settings"], view["pick"], view["pick"].get("pick_confidence")
    rows = view["collections"]
    ad_hoc = sum(1 for r in rows if r["ad_hoc"])
    sure = round((view["confident"] if conf is None else conf) * 100)
    facts = (("Collections", f"{len(rows)}" + (f", {ad_hoc} ad hoc" if ad_hoc else "")),
             ("Gateway", f"<span class='mono'>{esc(gateway_url())}</span>"),
             ("A request naming none", f"A decision model picks one when it is at least {sure}% sure;"
              " otherwise it is refused" if pick.get("pick_collection")
              else "Refused, with each collection's description and tags"))
    return (
        "<div class='panel c-panel'><p class='sub'>Ask the gateway for <code>bench/&lt;name&gt;</code>"
        " and every seat in its panel answers.</p>"
        f"<div id='c-list'>{collection_list(rows)}</div></div>"
        # A collection opens docked beside the list, as a model does (bench.js fillForm):
        # the one clicked, or a new one. Its Save at a fixed foot.
        "<dialog class='dock' id='c-form' aria-labelledby='c-form-head'>"
        "<div class='bar'><p>Collection</p></div><div class='dock-body'>"
        "<h2 id='c-form-head' tabindex='-1'>New collection</h2>"
        "<label class='field' for='c-name'>Name</label>"
        # The prefix is fixed text beside the field, so it cannot be deleted
        # or typed twice; save_collection also drops one typed by habit.
        "<div class='prefixed'><span class='prefix' aria-hidden='true'>bench/</span>"
        "<input id='c-name' spellcheck='false' autocomplete='off' placeholder='reviewers'"
        " aria-describedby='c-name-rules'></div>"
        "<p class='rules' id='c-name-rules'>Callers ask for <code>bench/&lt;name&gt;</code>. Say what"
        " it is for, not who is in it: members change. Lower-case letters, digits and '-'.</p>"
        # Before the members: some tags say what every member must be able to
        # do, and the list greys out the models that cannot (bench.js tagBlocks).
        "<label class='field' for='c-tags'>Tags</label>"
        "<input id='c-tags' spellcheck='false' autocomplete='off' placeholder='e.g. review, code, security'>"
        "<div class='tag-picks' id='c-tag-picks' aria-label='Suggested tags'></div>"
        "<p class='rules'>Callers read these: an agent asking <code>GET /v1/models</code> sees"
        " each collection's description and tags and picks one for its task. Some also narrow the"
        " members: <code>vision</code>, <code>tools</code> and <code>json</code> grey out the models the"
        " catalog does not say can; <code>long-context</code> those under 128k tokens; a task shape such"
        " as <code>code</code> or <code>prose</code> the roles it does not admit; <code>free</code> hides"
        " the paid ones. Click a suggestion or type your own: comma-separated lower-case words, up to 8.</p>"
        # Not data-filter: that binds to the whole panel. The same match (any
        # text, case-insensitive), on the member list only; the seats show who is ticked.
        "<div class='members-head'><label class='field' for='c-member-filter'>Members</label>"
        # Model details as they are now (a fact set in a model's popup, a refresh of the
        # catalog), the ticks and the search kept; also done when a model popup opened from here closes.
        "<button class='btn small' type='button' id='c-refresh' title='Fetch the models and their details again;"
        " what is ticked stays ticked'>Refresh model details</button>"
        "<span class='done' id='c-refresh-out' role='status'></span></div>"
        # The panel as it will sit, drawn as the Collections list draws it
        # (bench.js recount): a seat per ticked member, explorer seats dashed.
        "<div id='c-seats' class='seats' aria-live='polite'></div>"
        "<div class='member-find'><input type='search' id='c-member-filter' class='member-filter' spellcheck='false'"
        " autocomplete='off' placeholder='Filter by model, provider or served name' aria-controls='c-members'"
        " aria-describedby='c-count'><select id='c-member-show' aria-label='Show' aria-controls='c-members'>"
        "<option value=''>Every model</option><option value='ticked'>Ticked</option>"
        "<option value='fits'>Fits the tags</option></select>"
        "<select id='c-member-provider' aria-label='Provider' aria-controls='c-members'>"
        "<option value=''>Every provider</option></select></div>"
        "<div id='c-members' class='members' role='group' aria-label='Members'></div>"
        "<p class='rules' id='c-count'></p>"
        "<div class='settings'>"
        "<div><label class='field' for='c-max-calls'>Max calls per request</label>"
        "<input id='c-max-calls' type='number' min='1' max='10'></div>"
        "<div><label class='field' for='c-max-explorers'>Max explorers</label>"
        "<input id='c-max-explorers' type='number' min='0' max='2'></div>"
        "</div>"
        "<p class='rules'>Empty follows the Gateway settings, shown greyed. A member out of quota stays"
        " on the collection and an explorer sits in for it until the quota is back. Max explorers 0"
        " means only the members ever run, stand-ins included.</p>"
        "<label class='field' for='c-shape'>Explorer shape</label><select id='c-shape'></select>"
        "<p class='rules'>Which models may fill the explorer seats. Automatic asks a decision"
        " model to label each prompt when one is onboarded, else general.</p>"
        "<label class='field' for='c-reply'>Reply</label><select id='c-reply'>"
        "<option value='all'>every answer</option><option value='merged'>merged</option>"
        "<option value='route'>route (one member, for agents)</option></select>"
        "<p class='rules'>Merged: after the panel, one model writes a single answer saying where the"
        " seats agreed and who said what, for a chat UI that shows one answer; every seat is still"
        " called and recorded. Route: no panel; one member, picked by its record, gets the request"
        " with its tools, for an agent such as Claude Code or Codex. A caller can ask for any with"
        " <code>bench/&lt;name&gt;:merged</code>, <code>:all</code> or <code>:route</code>.</p>"
        # Only a merged reply has a merger; the choice is kept while hidden.
        "<div id='c-merger-row' hidden><label class='field' for='c-merger'>Merger</label>"
        "<select id='c-merger'></select>"
        "<p class='rules'>Automatic picks the member with the best verdicts on the task's shape.</p></div>"
        "<label class='field' for='c-desc'>Description</label>"
        "<textarea id='c-desc' rows='2' maxlength='300'"
        " placeholder='e.g. Adversarial code review: finding bugs and broken guarantees in a diff'></textarea>"
        "<label class='check'><input type='checkbox' id='c-ad-hoc'> Ad hoc: only when called by name</label>"
        "<p class='rules'>Left off <code>GET /v1/models</code> and the list agents choose from, so an"
        " agent picking for itself never spends it. For the expensive ones you decide to run: say"
        " <code>bench/&lt;name&gt;</code> and it runs. Not a lock: anyone who knows the name can call it.</p>"
        "</div><div class='dock-foot'>"
        "<div class='errors' id='c-errors' role='alert'></div><div class='done' id='c-done'></div>"
        "<button class='btn primary' id='c-save'>Save collection</button>"
        "<button class='btn' id='c-close'>Close</button>"
        "<button class='linkish danger' id='c-delete' hidden>Delete collection</button></div>"
        "</dialog>"
        # What applies to every collection, beside the list (bench.css aside.facts).
        "<aside class='facts' id='facts-collections' aria-labelledby='facts-collections-h'>"
        "<div class='bar'><h2 id='facts-collections-h'>All collections</h2>"
        "<button type='button' class='linkish facts-close'>Close</button></div><div class='facts-body'>"
        "<dl class='rows'>" + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in facts) + "</dl>"
        "<h3>Gateway settings</h3><div class='form-panel'>"
        "<div class='settings'>"
        "<div><label class='field' for='g-max-calls'>Max calls per request</label>"
        f"<input id='g-max-calls' type='number' min='1' max='10' value='{esc(s['max_calls'])}'></div>"
        "<div><label class='field' for='g-max-explorers'>Max explorers</label>"
        f"<input id='g-max-explorers' type='number' min='0' max='2' value='{esc(s['max_explorers'])}'></div>"
        "</div>"
        "<p class='rules'>Explorers = min(max explorers, max calls − members): with 5 and 2, a"
        " 2- or 3-model collection gets 2, a 4-model one 1, a 5-model one none. These apply to"
        " every collection that does not set its own.</p>"
        f"<label class='check'><input type='checkbox' id='g-pick'{' checked' if pick.get('pick_collection') else ''}>"
        " Choose a collection for a request that names none</label>"
        "<div class='settings'><div><label class='field' for='g-pick-conf'>Only when the model is at least"
        "</label><input id='g-pick-conf' type='number' min='0.05' max='1' step='0.05'"
        f" placeholder='{esc(view['confident'])}' value='{'' if conf is None else esc(conf)}'"
        f"{'' if pick.get('pick_collection') else ' disabled'}></div></div>"
        "<p class='rules'>A decision model (the one Benchmarks › Decision says the collection pick"
        " asks) chooses from the collections' descriptions and tags, and the reply says which and why"
        " (<code>bench.picked</code>, <code>X-Bench-Picked</code>). Less sure than this, or switched off,"
        " the request is refused with each collection's description and tags, so the caller can choose."
        " A name that is not a collection is always refused.</p>"
        "<div class='errors' id='g-errors' role='alert'></div><div class='done' id='g-done'></div>"
        "<div class='actions'>"
        "<button class='btn primary' id='g-save'>Save settings</button></div></div>"
        "<details class='key'><summary>How a collection runs</summary><p>A collection is a named panel of"
        " models the gateway runs together. Every member answers, plus explorer seats the bench fills with"
        " other models, the least recently tried first, so it keeps learning about the rest of the bench."
        " Two to four members is the sweet spot. Open a row to change it or delete it.</p></details>"
        "</div></aside>")


# A grouped sidebar since 2026-10-19 (RELEASE-PLAN "The dashboard"): eight
# pages were too many for a row of tabs. Collections holds the gateway's
# requests under the collections they ran; Benchmarks holds Performance.
# At a glance (every model's state and uptime) was retired 2026-11-10: Benchmarks,
# Uptime and Models said it all again. Benchmarks is the home page.
SECTIONS = (("Observe", (("suite", "Benchmarks", "benchmarks_tab"),
                         ("needs", "Action needed", "actions_page"),
                         ("quotas", "Quota management", "quotas_page"),
                         ("logs", "Logs", "logs_page"))),
            ("Gateway", (("endpoints", "Endpoints", "endpoints_page"),
                         ("models", "Models benched", "models_page"),
                         ("collections", "Collections", "collections_and_gateway"),
                         ("discover", "Discover new models", "discover_card"),
                         ("clients", "Clients setup", "clients_page"))),
            ("Control", (("settings", "Settings", "settings_page"),
                         ("info", "Info", "info_page"))))
TABS = tuple(page for _, pages in SECTIONS for page in pages)
UI_CONFIG = DATA / "config" / "ui.json"
# A page's own controls in its bar (ui/UI.md): its filter, then its actions at the
# right. Fixed HTML, so they are there before the page arrives and a page fetched
# again never replaces them: a filter keeps what is typed in it.
BAR_TOOLS = {
    # data-none: what a filter that matches nothing says under the table's head (bench.js applyFilter).
    "endpoints": ("<input type='search' data-filter='page' placeholder='Find an endpoint or a model it serves'"
                  " aria-label='Find an endpoint or a model it serves' data-none='No endpoint or model matches'>"
                  "<span class='spring'></span>"
                  "<span class='done' id='el-done' role='status'></span><span class='errors' id='el-errors' role='alert'></span>"
                  "<button class='btn primary' type='button' id='e-add'>Add an endpoint</button>"),
    "quotas": ("<span class='spring'></span><span class='done' id='q-done' role='status'></span>"
               "<span class='errors' id='q-errors' role='alert'></span><button class='btn' type='button' id='q-all'"
               " title='Asks each connection that tells for free what is left'>Refresh all live</button>"
               "<button class='btn' type='button' id='q-reorder' title='Forgets the order the tiles were moved into'>"
               "Sort: out first, then soonest reset</button>"),
    # Collapse all while every connection is open, else Expand all (bench.js foldButtons).
    "models": ("<input type='search' data-filter='page' placeholder='Find a model or connection'"
               " aria-label='Find a model or connection' data-none='No model matches'><span class='spring'></span>"
               "<button class='fold-all' type='button' data-fold-all='close' aria-expanded='true'>"
               "<span class='fold-chev' aria-hidden='true'></span>Collapse all</button>"
               "<button class='btn primary' type='button' id='models-onboard'>Onboard a model</button>"),
    "collections":("<span class='spring'></span><span class='done' id='c-status' role='status'></span>"
                    "<button class='btn primary' type='button' id='c-new'>New collection</button>"),
    "logs": ("<input type='search' data-filter='page' placeholder='Filter every log' aria-label='Filter every log'>"
             "<span class='spring'></span><button class='btn' type='button' id='logs-export'>Export…</button>"),
    "discover": ("<input type='search' id='d-q' placeholder='Search the catalog' aria-label='Search the catalog'>"
                 "<span class='muted' id='d-count' role='status'></span><span class='spring'></span>"
                 "<button class='btn' type='button' id='d-refresh' title='Fetch FreeLLMAPI, models.dev and every"
                 " Open WebUI connection&#39;s own model list now, instead of waiting for the daily fetch'>"
                 "Refresh lists</button>"),
}
# The pages with their own facts (aside.facts), and the bar's button that opens them under 1280px.
FACTS_OPEN = {"suite": "On this page", "collections": "Gateway settings", "endpoints": "All endpoints",
              "models": "All models", "clients": "This install", "discover": "About the catalog",
              "settings": "On this page", "quotas": "All connections"}


def collections_and_gateway() -> str:
    return collections_tab() + gateway_card()


# ---- Clients: each app's set-up, filled in from this install ---------------
# Each format follows the client's own docs as read on CLIENTS_READ (linked on
# the page); only Claude Code was run against the gateway (GATEWAY.md, "Route
# mode"). A key made on the page never goes into a file, which may sync: a file
# names an environment variable, or the app asks, or (an MCP header in Zed,
# Cline or goose, which take no variable) KEY_HOLDER stays for the reader.
# Only blocks marked `key` (shell lines, fields typed into an app) get the key
# made on the page, swapped in for KEY_HOLDER by bench.js. CLIENTS.md is written
# from client_setups (scripts/clients_doc.py): after a change here, run it, as
# check.py fails while the file differs.
KEY_HOLDER = "<your gateway key>"
KEY_ENV = "BENCHWARMERS_KEY"         # the plugin's and the CLI's name for it too
CLIENTS_READ = "2026-11-08"
AGENT_OUT = 16_384                   # the output budget an agent is told; route mode cuts it to the model's own
UNKNOWN_CONTEXT = 128_000            # for a collection whose members' windows the catalog does not know
DEFAULT_SERVER = "http://127.0.0.1:8122"


def _snip(label: str, lang: str, text: str, key: bool = False) -> dict:
    return {"label": label, "lang": lang, "text": text, "key": key}


def _env_snips(name: str) -> list[dict]:
    return [_snip("macOS, Linux: in your shell profile", "sh", f"export {name}='{KEY_HOLDER}'", True),
            _snip("Windows: once, then start the app again", "bat", f'setx {name} "{KEY_HOLDER}"', True)]


def _fields(pairs: list) -> str:
    width = max(len(k) for k, _ in pairs)
    return "\n".join(f"{k.ljust(width)}  {v}" for k, v in pairs)


def client_setups(root: str, cols: list, keys: bool) -> list[dict]:
    """Every client's panel: what it gets, its snippets (_snip), where they
    go, the one thing to watch for and the docs they follow. `root`: the
    server, without /v1; `cols`: (name, context window or None, sees images)
    for each collection offered, the first being the one a single-model field
    names; `keys`: whether every call needs a gateway key."""
    base, mcp, env = root + "/v1", root + "/mcp", KEY_ENV
    fast = next((n for n, _, _ in cols if n == "fast"), cols[0][0])
    # (model, collection, context window, output budget, sees images)
    rows = [(f"bench/{n}:route", n, c or UNKNOWN_CONTEXT, min(AGENT_OUT, (c or UNKNOWN_CONTEXT) // 4), bool(eyes))
            for n, c, eyes in cols]
    model, ctx, out = rows[0][0], rows[0][2], rows[0][3]
    q = json.dumps                       # a quoted string, read the same by JSON, TOML and YAML

    def js(data) -> str:
        return json.dumps(data, indent=2)

    def lines(*parts) -> str:
        return "\n".join(p for p in parts if p is not None)

    def when(text: str):
        return text if keys else None

    opencode = {"$schema": "https://opencode.ai/config.json",
                "provider": {"benchwarmers": {
                    "npm": "@ai-sdk/openai-compatible", "name": "Benchwarmers",
                    "options": {"baseURL": base, **({"apiKey": f"{{env:{env}}}"} if keys else {})},
                    "models": {m: {"name": f"{n} (route)", "limit": {"context": c, "output": o}}
                               for m, n, c, o, _ in rows}}},
                "mcp": {"benchwarmers": {
                    "type": "remote", "url": mcp, "enabled": True, "oauth": False,
                    **({"headers": {"Authorization": f"Bearer {{env:{env}}}"}} if keys else {}),
                    "timeout": 900_000}}}
    vscode = [{"name": "Benchwarmers", "vendor": "customendpoint", "apiKey": "${input:benchwarmersKey}",
               "apiType": "chat-completions",
               "models": [{"id": m, "name": f"{n} (Benchwarmers)", "url": base + "/chat/completions",
                           "toolCalling": True, "vision": eyes, "maxInputTokens": c - o, "maxOutputTokens": o}
                          for m, n, c, o, eyes in rows]}]
    vscode_mcp = {**({"inputs": [{"type": "promptString", "id": "benchwarmers-key",
                                  "description": "Benchwarmers gateway key", "password": True}]} if keys else {}),
                  "servers": {"benchwarmers": {"type": "http", "url": mcp, **(
                      {"headers": {"Authorization": "Bearer ${input:benchwarmers-key}"}} if keys else {})}}}
    codex = lines(f"model = {q(model)}", 'model_provider = "benchwarmers"', f"model_context_window = {ctx}",
                  'web_search = "disabled"', "",
                  "[model_providers.benchwarmers]", 'name = "Benchwarmers"', f"base_url = {q(base)}",
                  when(f'env_key = "{env}"'), 'wire_api = "responses"', "",
                  "[mcp_servers.benchwarmers]", f"url = {q(mcp)}", when(f'bearer_token_env_var = "{env}"'),
                  "tool_timeout_sec = 900")
    zed = {"language_models": {"openai_compatible": {"benchwarmers": {
               "api_url": base,
               "available_models": [{"name": m, "display_name": f"{n} (Benchwarmers)", "max_tokens": c,
                                     "max_output_tokens": o,
                                     "capabilities": {"tools": True, "images": eyes, "parallel_tool_calls": False,
                                                      "prompt_cache_key": False}}
                                    for m, n, c, o, eyes in rows]}}},
           "context_servers": {"benchwarmers": {"url": mcp, **(
               {"headers": {"Authorization": f"Bearer {KEY_HOLDER}"}} if keys else {})}}}
    secret = f"${{{{ secrets.{env} }}}}"
    continue_ = lines("name: Benchwarmers", "version: 0.0.1", "schema: v1", "models:",
                      *(lines(f"  - name: {q(n + ' (Benchwarmers)')}", "    provider: openai", f"    model: {q(m)}",
                              f"    apiBase: {q(base)}", when(f"    apiKey: {secret}"),
                              "    roles: [chat, edit, apply]",
                              f"    capabilities: [tool_use{', image_input' if eyes else ''}]",
                              "    defaultCompletionOptions:", f"      contextLength: {c}", f"      maxTokens: {o}")
                        for m, n, c, o, eyes in rows),
                      "mcpServers:", "  - name: benchwarmers", "    type: streamable-http", f"    url: {q(mcp)}",
                      when("    requestOptions:"), when("      headers:"),
                      when(f"        Authorization: Bearer {secret}"))
    cline_mcp = {"mcpServers": {"benchwarmers": {"type": "streamableHttp", "url": mcp, **(
        {"headers": {"Authorization": f"Bearer {KEY_HOLDER}"}} if keys else {}), "timeout": 900}}}
    librechat = lines("endpoints:", "  custom:", '    - name: "Benchwarmers"',
                      f'      apiKey: "{"${" + env + "}" if keys else "local"}"', f"      baseURL: {q(base)}",
                      "      models:", f"        default: [{', '.join(q(f'bench/{n}:merged') for n, _, _ in cols)}]",
                      "        fetch: true", "      titleConvo: false", '      modelDisplayLabel: "Benchwarmers"')
    goose = {"name": "benchwarmers", "engine": "openai", "display_name": "Benchwarmers",
             "description": "Benchwarmers collections, one model per conversation (route mode)",
             "api_key_env": env, "base_url": base + "/chat/completions",
             "models": [{"name": m, "context_limit": c} for m, _, c, _, _ in rows],
             "supports_streaming": True, "requires_auth": keys}
    goose_ext = lines("extensions:", "  benchwarmers:", "    type: streamable_http", "    name: benchwarmers",
                      "    enabled: true", f"    uri: {q(mcp)}",
                      f"    headers: {{Authorization: {q('Bearer ' + KEY_HOLDER)}}}" if keys else "    headers: {}",
                      "    timeout: 900")
    # Your own code (Python): the key from the environment, as every file here; "local" where none is asked.
    key_py = f"os.environ[{q(env)}]" if keys else q("local")
    auth_py = f'"headers": {{"Authorization": "Bearer " + {key_py}}}' if keys else ""
    imp_os = "import os\n" if keys else ""
    llm_py = f"llm = ChatOpenAI(base_url={q(base)}, api_key={key_py},\n                 model={q(model)})"
    ask = q("How many words are in 'the bench is deep'?")
    lc_agent = (f"# pip install langchain langchain-openai\n{imp_os}"
                "from langchain.agents import create_agent\nfrom langchain_openai import ChatOpenAI\n\n"
                f"{llm_py}\n\n\n"
                "def word_count(text: str) -> int:\n    \"\"\"Count the words in a text.\"\"\"\n"
                "    return len(text.split())\n\n\n"
                "agent = create_agent(llm, tools=[word_count])\n"
                f"reply = agent.invoke({{\"messages\": [{{\"role\": \"user\", \"content\": {ask}}}]}})\n"
                "print(reply[\"messages\"][-1].content)")
    lc_panel = (f"# pip install langchain-openai\n{imp_os}from langchain_openai import ChatOpenAI\n\n"
                f"panel = ChatOpenAI(base_url={q(base)}, api_key={key_py},\n"
                f"                   model={q(f'bench/{cols[0][0]}:merged')}, timeout=900, max_retries=0)\n"
                "print(panel.invoke(\"Where can a cache keyed by user id return another user's data?\").content)")
    lc_mcp = ("# pip install langchain langchain-openai langchain-mcp-adapters\nimport asyncio\n" + imp_os
              + "from langchain.agents import create_agent\n"
                "from langchain_mcp_adapters.client import MultiServerMCPClient\n"
                "from langchain_openai import ChatOpenAI\n\n"
              + f"{llm_py}\nbench = MultiServerMCPClient({{\"benchwarmers\": {{\n"
              + f"    \"transport\": \"streamable_http\", \"url\": {q(mcp)}, \"sse_read_timeout\": 900"
              + (f",\n    {auth_py}" if keys else "") + "}})\n\n\n"
                "async def main():\n    agent = create_agent(llm, tools=await bench.get_tools())\n"
                "    reply = await agent.ainvoke({\"messages\": [{\"role\": \"user\", \"content\":\n"
                "        \"Ask the bench where a cache keyed by user id can leak data, then judge each seat.\"}]})\n"
                "    print(reply[\"messages\"][-1].content)\n\n\nasyncio.run(main())")
    oa_agents = ("# pip install openai-agents\nimport asyncio\n" + imp_os
                 + "from agents import Agent, OpenAIChatCompletionsModel, Runner, set_tracing_disabled\n"
                   "from agents.mcp import MCPServerStreamableHttp\nfrom openai import AsyncOpenAI\n\n"
                   "set_tracing_disabled(True)\n"
                 + f"bench = AsyncOpenAI(base_url={q(base)}, api_key={key_py})\n\n\n"
                   "async def main():\n    async with MCPServerStreamableHttp(\n"
                 + "            name=\"benchwarmers\", client_session_timeout_seconds=900,\n"
                 + f"            params={{\"url\": {q(mcp)}, \"sse_read_timeout\": 900"
                 + (f",\n                    {auth_py}" if keys else "") + "}) as panel:\n"
                   "        agent = Agent(name=\"Assistant\", instructions=\"Answer briefly.\", mcp_servers=[panel],\n"
                 + f"                      model=OpenAIChatCompletionsModel(model={q(model)}, openai_client=bench))\n"
                 + f"        result = await Runner.run(agent, {ask})\n"
                   "        print(result.final_output)\n\n\nasyncio.run(main())")
    plugin_at = ("" if root == DEFAULT_SERVER else f", so set it to <code>{esc(root)}</code>")
    any_text = "" if keys else " Keys are not asked for here, so any text works."
    return [
        {"id": "opencode", "name": "opencode",
         "gets": "An agent with its tools on any collection (route mode), and the panel's ask and verdict as MCP tools.",
         "where": "<code>~/.config/opencode/opencode.json</code>, or <code>opencode.json</code> in a project; merge"
                  " into what is there. Pick a model with <code>/models</code>"
                  f" (<code>benchwarmers/{esc(model)}</code>)."
                  + (f" The key comes from <code>{env}</code>." if keys else ""),
         "blocks": [_snip("opencode.json", "json", js(opencode))],
         "watch": "<code>timeout</code> is in milliseconds and covers each MCP call: without it opencode stops"
                  " waiting long before a panel answers.",
         "docs": [("https://github.com/anomalyco/opencode/blob/v1.19.5/packages/web/src/content/docs/providers.mdx",
                   "v1.19.5's providers"),
                  ("https://github.com/anomalyco/opencode/blob/v1.19.5/packages/web/src/content/docs/mcp-servers.mdx",
                   "MCP servers")],
         "docs_note": "the current release's own docs. opencode.ai already shows the next config"
                      " (<code>providers</code>, <code>mcp.servers</code>), which v1.19.5 does not read."},
        {"id": "vscode", "name": "VS Code chat (Custom Endpoint)",
         "gets": "Copilot chat's agent on a collection (route mode), with your own key, and the panel as MCP tools.",
         "where": "<code>chatLanguageModels.json</code>: in the chat's model picker, Manage Models, Add Models,"
                  " Custom Endpoint; add the object to the list there. <code>mcp.json</code>: the command"
                  " <b>MCP: Open User Configuration</b>. VS Code asks for the key once and keeps it." + any_text,
         "blocks": [_snip("chatLanguageModels.json", "json", js(vscode)), _snip("mcp.json", "json", js(vscode_mcp))],
         "watch": "Keep <code>toolCalling: true</code>: VS Code hides a model without it from the agent picker."
                  " Restart VS Code if the models do not appear.",
         "docs": [("https://code.visualstudio.com/docs/copilot/customization/language-models", "language models"),
                  ("https://code.visualstudio.com/docs/agents/reference/mcp-configuration", "MCP configuration")]},
        {"id": "codex", "name": "Codex CLI",
         "gets": "Codex on a collection over the Responses API (route mode), and the panel as MCP tools.",
         "where": "<code>~/.codex/config.toml</code> (<code>%USERPROFILE%\\.codex\\config.toml</code> on Windows)."
                  " The first four lines go above the file's first <code>[table]</code>."
                  + (f" The key comes from <code>{env}</code>." if keys else ""),
         "blocks": [_snip("config.toml", "toml", codex)],
         "watch": "<code>web_search = \"disabled\"</code>: left on, Codex sends OpenAI's hosted search, which the"
                  " gateway refuses (no other model can run it), and every turn fails.",
         "docs": [("https://learn.chatgpt.com/docs/config-file/config-reference", "configuration reference")]},
        {"id": "zed", "name": "Zed",
         "gets": "Zed's agent on a collection (route mode), and the panel as MCP tools.",
         "where": "<code>settings.json</code>: the command <b>zed: open settings file</b>. Zed reads the key from"
                  " <code>BENCHWARMERS_API_KEY</code>, named after the provider id, or from the provider's settings"
                  " screen; never from settings.json." + any_text,
         "blocks": [_snip("settings.json", "json", js(zed)), *_env_snips("BENCHWARMERS_API_KEY")],
         "watch": ("settings.json takes no variable, so the MCP header holds the key as text: replace"
                   f" <code>{esc(KEY_HOLDER)}</code> there yourself, and keep the file out of synced dotfiles."
                   if keys else "Zed asks for a key even where none is needed: set the variable to any text."),
         "docs": [("https://zed.dev/docs/ai/use-api-access", "API access"), ("https://zed.dev/docs/ai/mcp", "MCP")]},
        {"id": "continue", "name": "Continue",
         "gets": "Continue's chat, edit and agent on a collection (route mode), and the panel as MCP tools.",
         "where": "<code>~/.continue/config.yaml</code> (<code>%USERPROFILE%\\.continue\\config.yaml</code> on"
                  " Windows); in a file you have, add the items to its <code>models</code> and"
                  " <code>mcpServers</code>."
                  + (f" <code>secrets.{env}</code> is read from <code>~/.continue/.env</code> or the environment."
                     if keys else ""),
         "blocks": [_snip("config.yaml", "yaml", continue_)],
         "watch": "Keep <code>capabilities: [tool_use]</code>: without it Continue offers the model for chat,"
                  " not agent mode.",
         "docs": [("https://docs.continue.dev/customize/model-providers/top-level/openai", "OpenAI provider"),
                  ("https://docs.continue.dev/customize/deep-dives/mcp", "MCP"),
                  ("https://docs.continue.dev/reference", "config.yaml reference")]},
        {"id": "cline", "name": "Cline and Kilo Code",
         "gets": "Their agent on a collection (route mode), set in their own screens; Cline takes the panel as"
                 " MCP tools too.",
         "where": "Cline: its settings, API Provider. Kilo Code: Settings, Providers, Custom provider. Cline's MCP:"
                  " the MCP Servers icon, Configure, Configure MCP Servers." + any_text,
         "blocks": [_snip("Cline", "text", _fields([("API Provider", "OpenAI Compatible"), ("Base URL", base),
                                                    ("API Key", KEY_HOLDER), ("Model ID", model),
                                                    ("Context Window Size", ctx), ("Max Output Tokens", out),
                                                    ("Image Support", "on" if rows[0][4] else "off")]), True),
                    _snip("Kilo Code", "text", _fields([("Provider ID", "benchwarmers"),
                                                        ("Display name", "Benchwarmers"),
                                                        ("Provider API", "OpenAI Compatible"), ("Base URL", base),
                                                        ("API key", KEY_HOLDER if keys else "(leave empty)"),
                                                        ("Models", ", ".join(m for m, *_ in rows))]), True),
                    _snip("Cline: cline_mcp_settings.json", "json", js(cline_mcp))],
         "watch": "Keep <code>\"type\": \"streamableHttp\"</code>: without it Cline tries the old SSE transport."
                  " Its <code>timeout</code> is in seconds (60 unless set).",
         "docs": [("https://docs.cline.bot/provider-config/openai-compatible", "Cline: OpenAI Compatible"),
                  ("https://docs.cline.bot/mcp/configuring-mcp-servers", "Cline: MCP servers"),
                  ("https://kilo.ai/docs/providers/openai-compatible", "Kilo Code: OpenAI Compatible")]},
        {"id": "claude-code", "name": "Claude Code",
         "gets": "The panel as MCP tools, with the plugin's <code>/benchwarmers:ask</code>, its skill and its"
                 " reminder of seats still unjudged; or Claude Code itself on a collection (route mode).",
         "where": f"The plugin finds the server at <code>BENCHWARMERS_URL</code>, <code>{DEFAULT_SERVER}</code>"
                  f" unless set{plugin_at}" + (f", and sends <code>{env}</code>." if keys else ".")
                  + " Route mode: the shell you start <code>claude</code> from." + any_text,
         "blocks": [_snip("In Claude Code: the plugin", "text",
                          "/plugin marketplace add WendellResearch-AI/benchwarmers\n"
                          "/plugin install benchwarmers@benchwarmers"),
                    _snip("Claude Code on a collection: a shell", "sh",
                          lines(f"export ANTHROPIC_BASE_URL={root}", f"export ANTHROPIC_API_KEY='{KEY_HOLDER}'",
                                f"export ANTHROPIC_MODEL={model}",
                                f"export ANTHROPIC_DEFAULT_HAIKU_MODEL=bench/{fast}:route", "claude"), True)],
         "watch": "The plugin's Stop hook needs the server on the same machine; with none running, every turn"
                  " ends with a hook error.",
         "docs": [("https://github.com/WendellResearch-AI/benchwarmers#from-claude-code-and-other-agents",
                   "README, From Claude Code and other agents")],
         "docs_note": "checked end to end with Claude Code 2.1.299 on 2026-10-23 (GATEWAY.md, Route mode)."},
        {"id": "code", "name": "LangChain and agent SDKs",
         "gets": "Your own Python: a LangChain (LangGraph) agent or an OpenAI Agents SDK agent on a collection with"
                 " your tools (route mode), a panel's merged answer as one step of a chain, and the panel as ask"
                 " and verdict tools.",
         "where": "Your code, with Python 3.10 or later and the packages on each snippet's first line. Any other"
                  " framework that takes an OpenAI-compatible model (a base URL, a key, a model name) is set up the"
                  " same way." + (f" The key comes from <code>{env}</code>." if keys else ""),
         "blocks": [_snip("LangChain: an agent with your tools", "python", lc_agent),
                    _snip("LangChain: a panel's merged answer, as one step", "python", lc_panel),
                    _snip("LangChain: the panel as tools (MCP)", "python", lc_mcp),
                    _snip("OpenAI Agents SDK: an agent with the panel as tools", "python", oa_agents)],
         "watch": "A panel refuses a request that carries tools, so an agent's model is always <code>:route</code>"
                  " and <code>:merged</code> is for a step without them. A panel takes minutes: keep the long"
                  " timeouts (in seconds), and <code>max_retries=0</code> on a panel, as a retry runs every seat"
                  " again. Without <code>set_tracing_disabled(True)</code> the Agents SDK sends its traces to"
                  " OpenAI.",
         "docs": [("https://docs.langchain.com/oss/python/langchain/agents", "LangChain: agents"),
                  ("https://docs.langchain.com/oss/python/langchain/mcp", "LangChain: MCP"),
                  ("https://openai.github.io/openai-agents-python/models/", "Agents SDK: models"),
                  ("https://openai.github.io/openai-agents-python/mcp/", "Agents SDK: MCP")],
         "docs_note": "langchain 1.5.1, langchain-mcp-adapters 0.3.2 and openai-agents 0.25.0."},
        {"id": "chat", "name": "Chat apps",
         "gets": "LobeHub, AnythingLLM, Cherry Studio, Chatbox and LibreChat: a chat window on a collection, one"
                 " merged answer per question, every seat still called and recorded.",
         "where": "Each app's settings, as an OpenAI-compatible (custom) provider. LibreChat:"
                  " <code>librechat.yaml</code>"
                  + (f", with <code>{env}</code> in its <code>.env</code>." if keys else ".") + any_text,
         "blocks": [_snip("Any of them", "text", _fields([("Base URL", base), ("API key", KEY_HOLDER),
                                                          ("Model", f"bench/{cols[0][0]}:merged")]), True),
                    _snip("LibreChat: librechat.yaml", "yaml", librechat)],
         "watch": "An app in Docker reaches this machine at another address than 127.0.0.1, and that host:port"
                  " goes in <code>BENCH_ALLOWED_HOSTS</code>. Turn automatic titles off: each runs a whole panel.",
         "docs": [("https://www.librechat.ai/docs/configuration/librechat_yaml/object_structure/custom_endpoint",
                   "LibreChat: custom endpoints")]},
        {"id": "goose", "name": "goose",
         "gets": "goose on a collection (route mode), and the panel as an MCP extension.",
         "where": "The provider: <code>~/.config/goose/custom_providers/benchwarmers.json</code>. The extension:"
                  " <code>~/.config/goose/config.yaml</code>. On Windows both are under"
                  " <code>%APPDATA%\\Block\\goose\\config\\</code>."
                  + (f" goose reads the provider's key from <code>{env}</code>, never from config.yaml." if keys
                     else ""),
         "blocks": [_snip("custom_providers/benchwarmers.json", "json", js(goose)),
                    _snip("config.yaml", "yaml", goose_ext)],
         "watch": ("The extension's header holds the key as text: replace"
                   f" <code>{esc(KEY_HOLDER)}</code> there yourself." if keys else
                   "<code>timeout</code> is in seconds; goose's usual 300 can end a long panel."),
         "docs": [("https://goose-docs.ai/docs/getting-started/providers", "providers"),
                  ("https://goose-docs.ai/docs/getting-started/using-extensions", "extensions"),
                  ("https://goose-docs.ai/docs/guides/config-files", "config files")]},
    ]


def clients_page() -> str:
    """Clients: the apps down the left, the one chosen beside them (bench.js
    pick), the first on arrival: what it gets, its base URL, model and key, where
    its set-up goes and the snippets (client_setups) from this install's address,
    collections and whether keys are asked for, each with Copy. In the panel
    beside the page, what holds for every app, and the gateway key: one made
    there is shown once and goes only into this page's blocks marked data-key
    (bench.js)."""
    root = gateway_url().removesuffix("/v1")
    offered = sorted((n, c) for n, c in bench_collections.load().items() if not c.get("ad_hoc"))
    offered.sort(key=lambda nc: nc[0] != "core")                   # core first, where there is one
    cols = []
    for n, c in offered:
        together = bench_collections.described(n, c).get("together") or {}
        cols.append((n, together.get("context_window"), "vision" in (together.get("capabilities") or [])))
    keys = guard.keys_required()

    def rows(pairs) -> str:
        return "<dl class='rows'>" + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in pairs) + "</dl>"
    if keys:
        key = ("<h3>Gateway key</h3><div class='form-panel'><p class='rules'>Every call needs one. Make one for these"
               f" apps and it fills in every <code>{esc(KEY_HOLDER)}</code> on this page that is not in a file: it"
               " is shown this once and kept as a hash. Or use one you have.</p>"
               "<label class='field' for='cl-name'>Name for a new key</label>"
               "<input id='cl-name' placeholder='e.g. opencode' autocomplete='off'>"
               "<div class='errors' id='cl-errors' role='alert'></div>"
               "<div class='done' id='cl-done' hidden>Made: copy what you need now, as it is gone when this page"
               " reloads. Settings lists it.</div>"
               "<div class='actions'><button class='btn primary' type='button' id='cl-make'>Make a key</button></div>"
               f"<p class='rules'>Most snippets read it from <code>{KEY_ENV}</code>:</p>"
               + "".join(_snip_html("key", i, s) for i, s in enumerate(_env_snips(KEY_ENV))) + "</div>")
    else:
        key = ("<p>Keys are not asked for on this machine, so the snippets carry none where an app allows it;"
               f" where one wants a key, any text works in place of <code>{esc(KEY_HOLDER)}</code>. Turn them on"
               " (<a href='/settings' data-go='settings'>Settings</a>, Gateway keys) and this panel offers to make"
               " one.</p>")
    aside = ("<aside class='facts' id='facts-clients' aria-labelledby='facts-clients-h'>"
             "<div class='bar'><h2 id='facts-clients-h'>This install</h2>"
             "<button type='button' class='linkish facts-close'>Close</button></div><div class='facts-body'>"
             + rows((("Gateway", f"<span class='mono'>{esc(root)}/v1</span>"),
                     ("MCP server", f"<span class='mono'>{esc(root)}/mcp</span>"),
                     ("Collections", (esc(", ".join(n for n, _ in offered)) or "none yet")
                      + "<br><span class='muted'>ad hoc ones are left out: name one yourself to use it</span>"),
                     ("Keys", "asked for on every call" if keys else "not asked for here")))
             + "<p>Agents get a collection in route mode (one member with its tools,"
             " <code>bench/&lt;collection&gt;:route</code>); chat windows a merged answer (<code>:merged</code>);"
             " the MCP server the panel itself, as ask and verdict tools.</p>" + key + "</div></aside>")
    if not cols:
        return ("<div class='panel'><p>No collection yet: make one on <button class='btn' type='button'"
                " data-go='collections'>Collections</button>, and the set-ups appear here.</p></div>" + aside)
    apps = client_setups(root, cols, keys)
    nav = "".join(f"<li><button type='button' aria-controls='client-{c['id']}' aria-pressed='{str(not i).lower()}'>"
                  f"{esc(c['name'])}</button></li>" for i, c in enumerate(apps))
    views = "".join(
        f"<section class='client' id='client-{c['id']}' aria-labelledby='client-{c['id']}-h'{' hidden' * bool(i)}>"
        f"<h2 id='client-{c['id']}-h'>{esc(c['name'])}</h2><p class='sub'>{c['gets']}</p><div>"
        + rows((("Base URL", f"<span class='mono'>{esc(root)}/v1</span>"),
                ("Model", f"<span class='mono'>bench/{esc(cols[0][0])}:{'merged' if c['id'] == 'chat' else 'route'}</span>"
                          + (", or another collection" if len(cols) > 1 else "")),
                ("Key", "a gateway key: make one in This install" if keys else "none needed here")))
        + f"<p class='where'>{c['where']}</p>"
        + "".join(_snip_html(c["id"], i, s) for i, s in enumerate(c["blocks"]))
        + f"<p class='watch'><b>Watch for:</b> {c['watch']}</p>"
        + "<p class='rules'>Written from " + ", ".join(ext_link(u, t) for u, t in c["docs"])
        + f", read {CLIENTS_READ}" + (f": {c['docs_note']}" if c.get("docs_note") else ".") + "</p></div></section>"
        for i, c in enumerate(apps))
    return ("<p class='sub'>A ready set-up for each app, filled in from this install: copy it into the file or"
            f" screen named.</p><div class='split'><nav aria-label='Apps'><ul>{nav}</ul></nav>{views}</div>{aside}")


def _snip_html(group: str, i: int, s: dict) -> str:
    pid = f"cl-{group}-{i}"
    return (f"<div class='snip'><div class='snip-head'><span class='mono'>{esc(s['label'])}</span>"
            f"<button class='btn small' type='button' data-copy='{pid}'>Copy</button></div>"
            f"<pre class='log' id='{pid}' data-lang='{s['lang']}'{' data-key' if s['key'] else ''}>"
            f"{esc(s['text'])}</pre></div>")


def pane(key: str, actions: tuple | None = None) -> str:
    """One tab's page, as its section holds it. The page renders the tab shown
    (tabs); bench.js fetches the others from GET /api/pane when first opened."""
    if key == "needs":
        groups, auto = actions or open_actions()
        return actions_page(groups, auto)
    make = globals()[next(fn for k, _, fn in TABS if k == key)]
    # Kept between requests: Benchmarks, a second of work and the home page; Quota, a month of the ledger read.
    return kept(key, make) if key in ("suite", "quotas") else make()


def href(key: str) -> str:
    """A page's own address: Benchmarks is /, every other page /<key>."""
    return "/" if key == "suite" else f"/{key}"


def page_for(path: str) -> str:
    """The page an address names (href; /benchmarks is Benchmarks too). Any
    other address shows the home page."""
    key = path.strip("/")
    return key if any(k == key for k, _, _ in TABS) else "suite"


def tabs(shown: str = "suite", theme: str = DEFAULT_THEME) -> str:
    """The frame (ui/UI.md): the rail (the name on its bench, the pages as links,
    the onboarding queue, Onboard a model, the theme), then the page pane, each
    page a section under its bar, then the popups. Only the page shown is
    rendered: rendering every one took seconds. The rest are their bars alone
    until opened (data-lazy, bench.js load). Action needed's count is read once."""
    actions = open_actions()
    count = {"needs": open_count(actions[0])}
    shown = page_for(shown)

    def link(key: str, label: str) -> str:
        n = count.get(key)
        return (f"<li><a data-tab='{key}' href='{href(key)}'" + (" aria-current='page'" if key == shown else "")
                + (f" aria-label='{esc(label)}, {n} open'>{esc(label)}<span class='n' aria-hidden='true'>{n}</span>"
                   if n else f">{esc(label)}") + "</a></li>")

    def bar(key: str, label: str) -> str:
        # Under 1280px a page's own facts (aside.facts) are a sheet, opened from its bar.
        toggle = (f"<button type='button' class='btn small facts-open' aria-controls='facts-{key}'"
                  f" aria-expanded='false'>{FACTS_OPEN[key]}</button>" if key in FACTS_OPEN else "")
        return f"<div class='bar'><h1 tabindex='-1'>{esc(label)}</h1>{BAR_TOOLS.get(key, '')}{toggle}</div>"
    nav = "".join(f"<h2>{esc(group)}</h2><ul>" + "".join(link(key, label) for key, label, _ in pages) + "</ul>"
                  for group, pages in SECTIONS)
    panes = "".join(f"<section class='tab active' id='tab-{key}'>{bar(key, label)}{pane(key, actions)}</section>"
                    if key == shown else f"<section class='tab' id='tab-{key}' data-lazy>{bar(key, label)}</section>"
                    for key, label, _ in TABS)
    picks = "".join(f"<button type='button' data-theme-pick='{esc(t)}' aria-pressed='{str(t == theme).lower()}'"
                    + (f" aria-label='{esc(THEME_LABELS[t])}' title='{esc(THEME_LABELS[t])}'>{THEME_ICONS[t]}"
                       if t in THEME_ICONS else f">{esc(THEME_LABELS.get(t, t.title()))}") + "</button>"
                    for t in themes())
    return (
        "<div class='app'><nav class='tabs' aria-label='Pages'><div class='brand'>"
        "<a class='word' href='/' data-go='suite'><span>Benchwarmers</span></a>"
        # The credit is fixed, not a setting (RELEASE-PLAN decision 7).
        "<p class='by'>by <a href='https://wendellresearch.ai' target='_blank' rel='noopener'>Wendell Research</a></p>"
        "<button type='button' class='btn menu' id='menu' aria-expanded='false' aria-controls='rail-body'>Menu</button>"
        # Search: the palette (bench.js palOpen), Ctrl+K anywhere.
        "</div><div class='rail-body' id='rail-body'><button type='button' class='search' id='pal-open'"
        " aria-haspopup='dialog' aria-keyshortcuts='Control+K'>Search<kbd>Ctrl K</kbd></button>"
        f"{nav}<div class='foot'>"
        "<button class='btn fresh' id='fresh' hidden>New results · Show</button>"
        # The onboarding queue's progress (bench.js fills it from /api/onboard-queue).
        # Down the sidebar: what it is doing, a bar for the batch, each model a line and the
        # running one's five steps under it (bench.js oqRender).
        "<div class='oq' id='oq' hidden><p class='oq-cap' id='oq-cap' role='status'>Onboarding</p>"
        "<span class='oq-bar' id='oq-bar'></span><ul class='oq-models' id='oq-models'></ul>"
        "<button class='linkish' id='oq-open'>Details</button>"
        "<button class='linkish' id='oq-stop'>Stop</button>"
        "<button class='linkish' id='oq-clear'>Clear</button></div>"
        "<button class='btn primary' id='onboard-open'>Onboard a model</button>"
        # Saved as Settings' picker saves it (bench.js theme).
        f"<div class='pick-theme' role='group' aria-labelledby='theme-cap'><p id='theme-cap'>Theme</p>"
        f"<span class='pill'>{picks}</span></div>"
        f"</div></div></nav><main class='panes'>{panes}</main>{editor()}"
        # The palette: one field, one list of what it finds (bench.js palDraw).
        "<dialog id='palette' aria-label='Go to a page, a model or a collection'><input id='pal-q' type='text'"
        " role='combobox' aria-expanded='true' aria-controls='pal-list' aria-autocomplete='list' autocomplete='off'"
        " spellcheck='false' aria-label='Go to' placeholder='Go to a page, a model, a collection…'>"
        "<div id='pal-list' role='listbox' aria-label='Found'></div></dialog></div>")


def saved_theme() -> str:
    theme = load_json(UI_CONFIG).get("theme")
    return theme if theme in themes() else DEFAULT_THEME


def save_theme(theme: str) -> dict:
    if theme not in themes():
        return {"ok": False, "errors": [f"no theme called {theme!r}; there are {', '.join(themes())}"]}
    import locked
    locked.update(UI_CONFIG, lambda d: d.update(theme=theme))
    return {"ok": True, "errors": [], "theme": theme}


def make_gateway_key(name: str, collections=None, rpm=None, who: str = "ui") -> dict:
    """A named gateway key, in this response only: the data folder keeps its
    hash (metrics/guard.py), the change log its name. `collections`: the ones
    it may call, none for all; `rpm`: its cap a minute, empty for none."""
    name = name.strip().lower()
    collections = collections or []
    if not (isinstance(collections, list) and all(isinstance(c, str) for c in collections)):
        return {"ok": False, "errors": ["collections must be a list of collection names"]}
    unknown = [c for c in collections if c not in bench_collections.load()]
    if unknown:
        return {"ok": False, "errors": [f"no collection named {', '.join(unknown)}"]}
    if rpm in ("", None):
        rpm = None
    elif isinstance(rpm, str) and rpm.strip().isdigit():
        rpm = int(rpm)
    try:
        value = guard.make_key(name, collections, rpm)
    except ValueError as exc:
        return {"ok": False, "errors": [str(exc)]}
    _log_change(datetime.datetime.now().astimezone().isoformat(timespec="seconds"), who, "",
                "gateway key", None, {"name": name, "collections": sorted(set(collections)), "rpm": rpm})
    return {"ok": True, "errors": [], "name": name, "key": value}


def revoke_gateway_key(name: str, who: str = "ui") -> dict:
    if not guard.revoke_key(name):
        return {"ok": False, "errors": [f"no gateway key named {name!r}"]}
    _log_change(datetime.datetime.now().astimezone().isoformat(timespec="seconds"), who, "",
                "gateway key", name, None)
    return {"ok": True, "errors": []}


def set_section(id_: str, title: str, sub: str, body: str, form: bool = True) -> str:
    """A Settings section: its heading, one sentence, its controls (a Save at their foot)."""
    return (f"<div class='panel{' form-panel' * form}' id='{id_}'><h2>{title}</h2><p class='sub'>{sub}</p>"
            f"{body}</div>")


def set_rows(rows) -> str:
    """Settings' facts: a label, its value, and under the value what sets it, an
    environment variable read at start (shown here, set only there)."""
    return "<dl class='rows'>" + "".join(
        f"<dt>{esc(k)}</dt><dd>{v}" + "".join(f"<span class='set-by'>{esc(s)}</span>" for s in src) + "</dd>"
        for k, v, *src in rows) + "</dl>"


def gateway_keys_card() -> str:
    """Settings' Gateway keys: where the gateway answers, whether a key is asked
    for (both from the environment), the keys, and a new one."""
    def when(stamp) -> str:
        return esc(str(stamp or "")[:16].replace("T", " ")) or "<span class='muted'>never</span>"
    rows = "".join(f"<tr><td class='mono'>{esc(n)} <span class='muted'>{guard.PREFIX}…{esc(e.get('hint', ''))}</span></td>"
                   f"<td>{esc(', '.join(e.get('collections') or [])) or 'all'}</td>"
                   f"<td class='num'>{esc(e.get('rpm') or '—')}</td>"
                   f"<td class='when'>{when(e.get('created'))}</td><td class='when'>{when(e.get('last_used'))}</td>"
                   f"<td><button class='btn' data-revoke-key='{esc(n)}'>Revoke</button></td></tr>"
                   for n, e in sorted(guard.gateway_keys().items()) if isinstance(e, dict))
    if guard.auth_on():
        asked = "on every call to /v1/… and /bench/verdict: an admin token is set"
    elif guard.keys_required():
        asked = "on every call, on this machine too"
    else:
        asked = "not yet: this machine is served with no key until one of these is set"
    bind = guard.bind()
    scopes = "".join(f"<label class='check'><input type='checkbox' name='k-scope' value='{esc(c)}'>"
                     f" {bench_collections.PREFIX}{esc(c)}</label>" for c in sorted(bench_collections.load()))
    return set_section(
        "set-keys", "Gateway keys", "A key lets a chat app, an agent or another machine call the gateway, never"
        " this dashboard: it is sent as <code>Authorization: Bearer &lt;key&gt;</code>.",
        set_rows((("Answering on", f"{esc(bind)}:{PORT}/v1, and {esc(bind)}:{gateway.PORT} (gateway.py)",
                   "BENCH_BIND", "BENCH_PORT", "BENCH_GATEWAY_PORT"),
                  ("Public address", esc(guard.public_url() or "none"), "BENCHWARMERS_PUBLIC_URL"),
                  ("A key is asked", esc(asked), "BENCHWARMERS_REQUIRE_KEYS", "BENCHWARMERS_ADMIN_TOKEN")))
        + "<h3>Keys</h3>"
        f"<div class='scroll'><table><thead><tr>{th('Name')}{th('May call')}{th('A minute', True)}{th('Made')}"
        f"{th('Last used')}<th></th></tr></thead>"
        f"<tbody>{rows or '<tr><td colspan=6 class=muted>No keys yet.</td></tr>'}</tbody></table></div>"
        "<p class='rules'>Revoked, a key stops at once. Its name is on every call it makes (Logs, Gateway calls).</p>"
        "<label class='field' for='k-name'>Name for a new key</label>"
        "<input id='k-name' placeholder='e.g. open-webui' autocomplete='off'>"
        "<fieldset><legend class='field'>Collections it may call (none ticked: all)</legend>"
        f"{scopes or '<span class=muted>No collections yet.</span>'}</fieldset>"
        "<label class='field' for='k-rpm'>Requests a minute (empty: no cap)</label>"
        "<input id='k-rpm' type='number' min='1' max='10000' placeholder='no cap' autocomplete='off'>"
        "<div class='errors' id='k-errors' role='alert'></div>"
        "<div class='done' id='k-done' hidden><p>Copy it now: it is shown this once and kept only as a hash.</p>"
        "<input id='k-value' class='mono' readonly></div>"
        "<div class='actions'><button class='btn primary' id='k-make'>Make a key</button></div>")


# The sign-in page's own styles: bench.css is behind the login it asks for. Hardwood's
# court is the whole page here (--floor, Wendell at centre court) with the card in its
# left half; the other themes, their plain ground. The name on its bench as the rail has it.
SIGNIN_CSS = (
    "body{margin:0;min-height:100vh;min-height:100dvh;display:grid;place-items:center;padding:16px;box-sizing:border-box;"
    "background:var(--floor,var(--paper));border-top:.4rem solid var(--court-line,transparent);color:var(--ink);"
    "font:400 .875rem/1.5 var(--font-display);color-scheme:var(--scheme,light)}"
    "form{display:grid;gap:8px;width:min(22rem,100%);box-sizing:border-box;padding:26px 28px 22px;"
    "border:1px solid var(--ink);border-radius:6px;background:var(--paper-2)}"
    ".word{justify-self:start;margin-bottom:10px;font:800 30px/1 var(--font-display);letter-spacing:-.04em}"
    ".word span{position:relative;display:inline-block;padding:0 .14em .6em}"
    ".word span::after{content:'';position:absolute;left:-.08em;right:-.08em;bottom:.46em;height:.28em;"
    "background:var(--bench-seat,var(--brand));border-radius:.06em}"
    ".word span::before{content:'';position:absolute;left:.18em;right:.18em;bottom:0;height:.5em;--leg:var(--bench-leg,var(--ink-2));"
    "background:linear-gradient(var(--leg),var(--leg)) left top/.11em 100% no-repeat,"
    "linear-gradient(var(--leg),var(--leg)) right top/.11em 100% no-repeat,"
    "linear-gradient(var(--leg),var(--leg)) center 66%/100% .07em no-repeat}"
    "label{font-weight:500}p{margin:0}code{font-family:var(--font-mono)}"
    "input,button{height:36px;box-sizing:border-box;border-radius:var(--radius);font:inherit}"
    "input{padding:0 10px;border:1px solid color-mix(in oklab,var(--muted) 50%,var(--paper));background:var(--paper-2);"
    "color:var(--ink);font-family:var(--font-mono)}"
    "input[aria-invalid=true]{border-color:var(--err)}#e{color:var(--err)}#e:empty{display:none}"
    "button{margin-top:6px;border:1px solid var(--ink);background:var(--ink);color:var(--paper);font-weight:500;cursor:pointer}"
    "button:hover{background:var(--ink-2)}.hint{margin-top:6px;color:var(--muted);overflow-wrap:anywhere}"
    ":focus-visible{outline:2px solid var(--link);outline-offset:1px}"
    "@media (min-width:800px){html[data-theme='hardwood'] form{position:absolute;"
    "left:max(12rem,calc(50% - (100vh - 6rem) * .47));top:calc(5rem + (100vh - 6rem) / 2);translate:-50% -50%}}"
    "@media (max-width:799px){html[data-theme='hardwood'] body{place-items:start center}}")


def login_page(theme: str | None = None, wrong: bool = False) -> str:
    """Sign-in, when an admin token is set: one field. A plain form post (the
    Handler's _login), so it works with scripts off; with them the token goes
    as JSON, through the same guard as every write, and a wrong one is said
    beside the field, which keeps what was typed. It loads nothing behind the
    login: its styles inline, the mascot as data, the system's own face.
    Opened from a link on another site, SameSite=Strict holds the cookie back,
    so a browser already signed in is sent on to the dashboard."""
    import base64
    theme = theme if theme in themes() else saved_theme()
    mascot = "data:image/png;base64," + base64.b64encode((UI / "mascot.png").read_bytes()).decode()
    return (f"<!doctype html><html lang='en' data-theme='{esc(theme)}'><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'><title>Sign in · Benchwarmers</title>"
            + favicon(theme) + f"<style>{brand_css(fonts=False).replace('/ui/mascot.png', mascot)}{SIGNIN_CSS}</style>"
            "<form method='post' action='/login'><span class='word'><span>Benchwarmers</span></span>"
            "<label for='t'>Admin token</label><input id='t' name='token' type='password' autocomplete='current-password'"
            f" required aria-describedby='e'{' aria-invalid=true autofocus' if wrong else ''}>"
            f"<p id='e' role='alert'>{'That is not the admin token.' if wrong else ''}</p><button>Sign in</button>"
            "<p class='hint'>The token this server was started with (<code>BENCHWARMERS_ADMIN_TOKEN</code>).</p></form>"
            "<script>"
            "fetch('/api/stamp').then(function (r) { if (r.ok) location.replace('/'); });"
            "var f = document.forms[0], t = f.token, e = document.getElementById('e');"
            "f.addEventListener('submit', function (ev) {"
            " ev.preventDefault();"
            " fetch('/login', {method: 'POST', headers: {'Content-Type': 'application/json'},"
            "  body: JSON.stringify({token: t.value})}).then(function (r) {"
            "  if (r.ok) return location.replace('/');"
            "  e.textContent = 'That is not the admin token.'; t.setAttribute('aria-invalid', 'true'); t.select(); });"
            "});</script>")


# ---- Export and import: this install's settings, or everything, as one zip --
# Moving an install is a copy of its data folder (metrics/paths.py); this is that
# copy as a download and an upload. The keys are not in it: they are in the llm
# key store, and only the endpoints' own travel, only when asked.
MOVE_SCOPES = {"settings": ("config",), "all": ("config", "metrics", "perf", "runs", "suite")}
IMPORT_MAX = 2_000_000_000            # bytes; everything on the owner's install is about 90 MB
_KEY_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


def _endpoint_keys() -> dict:
    """The stored keys the endpoints use: each one's key, admin key and refresh
    token. Never one from the environment, nor the llm CLI's own others."""
    names = set()
    for entry in endpoints.load().values():
        key = entry.get("key", "")
        names |= {key, endpoints.admin_name(entry), f"{key}.refresh"}
    return {n: v for n, v in endpoints._stored().items() if n in names and isinstance(v, str) and v}


def export_zip(scope: str, keys: bool, out) -> dict:
    """Write an export to the binary file `out`: the scope's folders (lock files
    never), keys.json when `keys`, and manifest.json. Returns the manifest."""
    import zipfile
    if scope not in MOVE_SCOPES:
        raise ValueError(f"scope is one of {', '.join(MOVE_SCOPES)}")
    made = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    manifest = {"benchwarmers_export": 1, "release": RELEASE, "made": made, "scope": scope,
                "files": 0, "keys": 0, "skipped": []}
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for top in MOVE_SCOPES[scope]:
            for path in sorted((DATA / top).rglob("*")):
                if not path.is_file() or path.suffix == ".lock":
                    continue
                name = path.relative_to(DATA).as_posix()
                try:
                    z.write(path, name)
                    manifest["files"] += 1
                except OSError as exc:                 # held open by another process
                    manifest["skipped"].append(f"{name}: {exc.strerror or exc}")
        if keys:
            have = _endpoint_keys()
            z.writestr("keys.json", json.dumps(have, indent=2))
            manifest["keys"] = len(have)
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
    return manifest


def _movable(name: str, tops: tuple) -> bool:
    """A name an import may write: relative, inside one of the scope's folders,
    no way out of the data folder, not a lock file."""
    parts = name.split("/")
    return (parts[0] in tops and len(parts) > 1 and "\\" not in name and ":" not in name
            and not any(p in ("", ".", "..") for p in parts) and not name.endswith(".lock"))


def import_zip(src, apply: bool) -> dict:
    """Read an export (`src`, a binary file) and say what is in it. With
    `apply`: this install's files in its scope go to backups/ first (an export
    of them), then its files replace this install's of the same name and its
    keys go to the key store. Files this install has and it does not are left."""
    import shutil
    import zipfile

    def refused(why: str) -> dict:
        return {"ok": False, "errors": [why]}
    try:
        z = zipfile.ZipFile(src)
    except zipfile.BadZipFile:
        return refused("that is not a zip file")
    with z:
        try:
            m = json.loads(z.read("manifest.json"))
        except (KeyError, ValueError):
            return refused("it has no manifest.json: not a Benchwarmers export")
        if not isinstance(m, dict) or m.get("benchwarmers_export") != 1 or m.get("scope") not in MOVE_SCOPES:
            return refused("its manifest is not one this version reads")
        tops = MOVE_SCOPES[m["scope"]]
        files = [i for i in z.infolist() if not i.is_dir() and i.filename not in ("manifest.json", "keys.json")]
        bad = [i.filename for i in files if not _movable(i.filename, tops)]
        if bad:
            return refused(f"{len(bad)} file(s) in it are outside {', '.join(tops)}, e.g. {bad[0]!r}: nothing imported")
        try:
            keys = json.loads(z.read("keys.json")) if "keys.json" in z.namelist() else {}
        except ValueError:
            return refused("its keys.json is not JSON")
        if not (isinstance(keys, dict) and all(isinstance(v, str) and _KEY_NAME.match(k) for k, v in keys.items())):
            return refused("its keys.json is not a list of key names and keys")
        out = {"ok": True, "errors": [], "scope": m["scope"], "release": str(m.get("release", "")),
               "made": str(m.get("made", "")), "files": len(files), "bytes": sum(i.file_size for i in files),
               "replaces": sum((DATA / i.filename).is_file() for i in files), "keys": len(keys)}
        if out["bytes"] > 10 * IMPORT_MAX:              # zipfile stops each file at its stated size
            return refused(f"it unpacks to {out['bytes']:,} bytes: more than any install holds")
        if not apply:
            return out
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = DATA / "backups" / f"before-import-{stamp}.zip"
        backup.parent.mkdir(parents=True, exist_ok=True)
        with open(backup, "wb") as f:
            export_zip(m["scope"], bool(keys), f)
        out["backup"] = backup.relative_to(DATA).as_posix()
        done = 0
        try:
            for i in files:
                dest = DATA / i.filename
                dest.parent.mkdir(parents=True, exist_ok=True)
                part = dest.with_name(f".{dest.name}.{os.getpid()}.part")
                try:
                    with z.open(i) as r, open(part, "wb") as w:
                        shutil.copyfileobj(r, w)
                    os.replace(part, dest)
                finally:
                    part.unlink(missing_ok=True)
                done += 1
        except (OSError, zipfile.BadZipFile) as exc:     # a full disk, a file held open, a damaged zip
            return refused(f"stopped after {done} of {len(files)} files ({exc}); what it replaced is in"
                           f" {out['backup']}")
        for name, value in keys.items():
            endpoints.set_key(name, value)
        return out


def move_card() -> str:
    """Settings' Export and import (export_zip, import_zip; bench.js)."""
    return set_section(
        "set-move", "Export and import", "Take this install to another machine, or keep a copy of it: one .zip,"
        " imported on the other install's Settings page.",
            # No wrapper: `.pick` is the onboarding picker's 3-column grid row, and squeezed these into it.
            "<label class='check'><input type='radio' name='x-scope' value='settings' checked"
            " data-kept> <span><b>Settings only</b>: endpoints, collections, roles, model options, the schedule,"
            " gateway keys, the theme (<code>config/</code>)</span></label>"
            "<label class='check'><input type='radio' name='x-scope' value='all' data-kept> <span><b>Everything</b>:"
            " the settings, and every result, history, log and saved answer the bench judges by</span></label>"
            "<label class='check'><input type='checkbox' id='x-keys' data-kept> <span>With the endpoints' API keys."
            " The file then holds them: keep it like a password, and delete it once imported.</span></label>"
            "<div class='actions'><button class='btn' type='button' id='x-export'>Download export</button></div>"
            "<h3>Import</h3>"
            "<p class='rules'>Its files replace this install's of the same name; files it does not have are left."
            " This install's own go to <code>backups/</code> first. Restart afterwards (Restart, on this page),"
            " so every part reads them.</p>"
            "<label class='field' for='x-file'>The .zip to import</label>"
            "<input type='file' id='x-file' accept='.zip,application/zip'>"
            "<div class='done' id='x-what' role='status'></div><div class='errors' id='x-errors' role='alert'></div>"
            "<div class='actions'><button class='btn primary' type='button' id='x-import' disabled>Import</button>"
            "</div>")


def access_card() -> str:
    """Settings' Sign-in and access: who may reach this install, from the
    environment: shown here, never set here. Each needs a restart, and a page
    that could change its own login could open itself to anyone. No secret is
    shown, only whether one is set."""
    bind = guard.bind()
    hosts, origins = sorted(guard._env("BENCH_ALLOWED_HOSTS")), sorted(guard._env("BENCH_ALLOWED_ORIGINS"))
    from_env = sorted(k[len(endpoints.ENV_KEY):].lower().replace("_", "-")
                      for k, v in os.environ.items() if k.startswith(endpoints.ENV_KEY) and v)
    return set_section("set-access", "Sign-in and access", "Set in the environment and read at start, never on this"
                       " page: one that could change its own sign-in could open itself to anyone.", set_rows((
                           ("Admin sign-in", "on: the dashboard asks for the token" if guard.auth_on()
                            else "off, which only this machine allows", "BENCHWARMERS_ADMIN_TOKEN"),
                           ("Reached from", "this machine only" if guard._loopback(bind) else "other machines too",
                            "BENCH_BIND"),
                           ("Other host names", esc(", ".join(hosts) or "none"), "BENCH_ALLOWED_HOSTS"),
                           ("Browser apps on other sites", esc(", ".join(origins) or "none: same origin only"),
                            "BENCH_ALLOWED_ORIGINS"),
                           ("Endpoint keys from the environment", esc(", ".join(from_env) or "none: all in the llm key store"),
                            f"{endpoints.ENV_KEY}<NAME>"))), form=False)


def restart_block() -> str:
    """Settings' Restart: what runs now, and its button (bench.js restart()). Info has one too (info_page)."""
    running, disk = on_disk()
    now = (f"Running {esc(RELEASE)} ({esc(running)}), the code on disk." if running == disk else
           f"<strong>Newer code is on disk</strong> ({esc(disk)}; running {esc(RELEASE)}, {esc(running)}).")
    # restart(): stopped and started again by serve.py's own parent process.
    return set_section("set-restart", "Restart", "Starts the dashboard again on the code on disk, for an update or a"
                       " setting read at start, whatever runs it.",
                       f"<p>{now}</p><p class='rules'>It waits for onboarding, a scheduled job and gateway calls in"
                       " progress, or cuts them off if you insist; the page comes back in a few seconds.</p>"
                       "<div class='actions'><button class='btn' type='button' id='restart'>Restart Benchwarmers</button>"
                       " <span id='restart-out' role='status'></span></div>")


RESTART = 75                       # the exit code serve.py's parent reads as "start me again"
SERVER = None                      # the running ThreadingHTTPServer, set in __main__
_answering = [0]                   # gateway calls being answered now
_answering_lock = threading.Lock()


def _follow_parent() -> None:
    """End with serve.py's parent however it went (a service stopped, a
    terminal closed), so no orphan keeps the port. Its process is waited on,
    not a pipe read: on Windows a thread blocked reading stdin stalls every
    subprocess started meanwhile (git, the onboarding, the jobs)."""
    ppid = int(os.environ.get("BENCH_PARENT") or 0)
    if not ppid:
        return
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x00100000, False, ppid)          # SYNCHRONIZE
        if not handle:
            return                                                  # cannot watch it: stay up
        k32.WaitForSingleObject(handle, 0xFFFFFFFF)                 # until it ends
    else:
        while os.getppid() == ppid:                                 # a new parent: the old one ended
            time.sleep(2)
    os._exit(0)


def restart(force: bool = False, who: str = "ui") -> dict:
    """End this dashboard and start it again on the code now on disk: after
    an update, or a setting read at start. serve.py runs it as a child of its
    own parent process (see __main__), which starts it again, so this works
    however serve.py was started: Servy, the npm or pip launcher, Docker or a
    terminal. Refused while something would be cut off, unless `force`."""
    import scheduler
    if os.environ.get("BENCH_SERVED") != "1" or SERVER is None:
        return {"ok": False, "errors": ["this dashboard was not started by serve.py's own parent process,"
                                        " so nothing would start it again: restart it the way you started it"]}
    busy = ([f"onboarding is running ({sum(i['state'] in ('queued', 'running') for i in _OQ['items'])} left):"
             " Stop it in the queue, or wait"] if _OQ["worker"] is not None else []) \
        + ([f"a scheduled job is running: {', '.join(scheduler.RUNNING)}"] if scheduler.RUNNING else []) \
        + ([f"{_answering[0]} gateway call(s) are being answered"] if _answering[0] else [])
    if busy and not force:
        return {"ok": False, "busy": True, "errors": busy}
    _log_change(datetime.datetime.now().astimezone().isoformat(timespec="seconds"), who, "", "restart", *on_disk())
    threading.Timer(0.5, SERVER.shutdown).start()      # after this reply has gone
    return {"ok": True, "errors": [], "cut": busy}


def save_scheduler(mode: str, jobs, who: str = "ui") -> dict:
    """The scheduler's mode and which jobs it runs (config/scheduler.json).
    The running scheduler reads the file every tick, so this applies within a
    minute, no restart. Only a job turned off is written: new jobs start on."""
    import locked
    import scheduler
    if mode not in scheduler.MODES:
        return {"ok": False, "errors": [f"mode: one of {', '.join(scheduler.MODES)}"]}
    jobs = jobs if isinstance(jobs, dict) else {}
    unknown = sorted(set(jobs) - set(scheduler.JOBS))
    if unknown:
        return {"ok": False, "errors": [f"no job called {', '.join(unknown)}"]}
    off = {name: False for name, want in jobs.items() if want is False}
    old = scheduler.settings()
    locked.update(scheduler.CONFIG, lambda d: d.update(mode=mode, jobs=off))
    _log_change(datetime.datetime.now().astimezone().isoformat(timespec="seconds"), who, "", "scheduler",
                old, {"mode": mode, "jobs": off})
    return {"ok": True, "errors": []}


def save_client(name: str, who: str = "ui") -> dict:
    """The model client for every call the bench makes (config/client.json,
    metrics/client.chosen), read per call, so it applies to the next one.
    BENCH_CLIENT, when set, wins, so the choice is refused rather than ignored."""
    import shutil
    import client as model_client
    if name not in model_client.CLIENTS:
        return {"ok": False, "errors": [f"client: one of {', '.join(model_client.CLIENTS)}"]}
    if (os.environ.get("BENCH_CLIENT") or "").strip():
        return {"ok": False, "errors": ["BENCH_CLIENT is set in this server's environment and wins:"
                                        " unset it and restart to choose here"]}
    if name == "llm" and not shutil.which("llm"):
        return {"ok": False, "errors": ["the llm CLI is not installed here (not on PATH): install it"
                                        " (pip install llm) or keep the built-in client"]}
    old = model_client.chosen()[0]
    locked.update(model_client.CHOICE, lambda d: d.update(client=name))
    _log_change(datetime.datetime.now().astimezone().isoformat(timespec="seconds"), who, "",
                "model client", old, name)
    # Not kept in step while it was not the client (_sync_cli): brought up to
    # date now, and on every save of it, so saving it again retries a failed sync.
    return {"ok": True, "errors": [], "sync": _sync_cli() if name == "llm" else ""}


def client_card() -> str:
    import shutil
    import client as model_client
    now, source = model_client.chosen()
    has_llm, pinned = bool(shutil.which("llm")), source == "environment"
    labels = {"builtin": "Built-in: nothing to install", "llm": "The llm CLI: its plugins and its own log"}
    opts = "".join(f"<option value='{k}'{' selected' if k == now else ''}"
                   f"{' disabled' if k == 'llm' and not has_llm else ''}>{esc(label)}"
                   f"{' (not installed)' if k == 'llm' and not has_llm else ''}</option>" for k, label in labels.items())
    where = {"environment": "Set by <code>BENCH_CLIENT</code> in this server's environment, which wins: unset it"
                            " and restart to choose here.",
             "settings": "Chosen here (<code>config/client.json</code>); <code>BENCH_CLIENT</code> would win.",
             "default": "The default, as nothing has chosen yet; <code>BENCH_CLIENT</code> would win."}[source]
    off = " disabled" if pinned else ""
    return set_section(
        "set-client", "Model client", "What makes every model call the bench makes (gateway seats, the suite,"
        " the probes, auto-retire's checks); the next call uses it.",
        f"<label class='field' for='mc-client'>Client</label><select id='mc-client'{off}>{opts}</select>"
        f"<p class='rules'>{where} Whichever it is, a model on a sign-in that renews or on ChatGPT's plan, and any"
        " call with images, goes through the built-in client.</p>"
        "<div class='errors' id='mc-errors' role='alert'></div><div class='done' id='mc-done'></div>"
        f"<div class='actions'><button class='btn primary' id='mc-save'{off}>Save client</button></div>")


def save_own_client_free(providers, who: str = "ui") -> dict:
    """Which providers' free models work only in their own app, so Discover
    leaves them out (sources.free_in_own_client_only). Written to
    config/connections.json `free_in_own_client_only`, only where it differs
    from the built-in, so a later release's list still reaches this install."""
    import sources
    if not isinstance(providers, dict) or not all(isinstance(v, bool) for v in providers.values()):
        return {"ok": False, "errors": ["providers: a map of models.dev provider id to true or false"]}
    known = set(sources.cached("modelsdev")) | set(sources.FREE_IN_OWN_CLIENT_ONLY)
    unknown = sorted(set(providers) - known)
    if unknown:
        return {"ok": False, "errors": [f"not a models.dev provider id: {', '.join(unknown)}"]}
    over = {p: on for p, on in providers.items() if on != sources.FREE_IN_OWN_CLIENT_ONLY.get(p, False)}
    old = sorted(p for p, on in sources.free_in_own_client_only().items() if on)

    def change(d):
        d.pop("free_in_own_client_only", None)
        if over:
            d["free_in_own_client_only"] = over
    locked.update(endpoints.CONNECTIONS, change)
    _log_change(datetime.datetime.now().astimezone().isoformat(timespec="seconds"), who, "",
                "free in own client only", old, sorted(p for p, on in sources.free_in_own_client_only().items() if on))
    return {"ok": True, "errors": []}


def own_client_card() -> str:
    import sources
    rows = "".join(f"<tr><td>{esc(p)}</td><td><input type='checkbox' name='oc-prov' value='{esc(p)}'"
                   f" aria-label='Leave out the free models of {esc(p)}'{' checked' if on else ''}></td></tr>"
                   for p, on in sorted(sources.free_in_own_client_only().items()))
    return set_section(
        "set-own", "Free only in their own app", "Discover leaves out the free models of the providers ticked here,"
        " whose free tier answers only in their own app; their paid models stay.",
        "<table><thead><tr>" + th("Provider") + th("Free models left out") + f"</tr></thead><tbody>{rows}</tbody></table>"
        "<p class='rules'>opencode, for one, says its free tier \"can only be used from within opencode\".</p>"
        "<label class='field' for='oc-add'>Add a provider</label>"
        "<input id='oc-add' placeholder='its models.dev id' autocomplete='off'>"
        "<div class='errors' id='oc-errors' role='alert'></div><div class='done' id='oc-done'></div>"
        "<div class='actions'><button class='btn primary' id='oc-save'>Save</button></div>")


def settings_page() -> str:
    """Its sections in as many columns as fit (bench.css .cols.flow), each with
    its own Save: the theme (saved as picked), the model client, sign-in and
    access (shown only: the environment sets it), the scheduler, gateway keys,
    export and import, providers whose free tier is theirs alone, and Restart.
    The panel beside them: the sections, and what this install runs as now."""
    import paths
    import scheduler
    import client as model_client
    current = saved_theme()
    swatch = ("--paper", "--band", "--ink", "--brand", "--primary", "--signal", "--link", "--err")
    cards = []
    for name in themes():           # picked as the rail's switch is (bench.js theme): the same buttons
        merged = {**_tokens(BASE_THEME), **_tokens(name)}
        dots = "".join(f"<span style='background:{esc(merged.get(t, '').strip())}'></span>" for t in swatch)
        cards.append(f"<button type='button' class='theme-card' data-theme-pick='{esc(name)}'"
                     f" aria-pressed='{str(name == current).lower()}'><b>{esc(THEME_LABELS.get(name, name.title()))}</b>"
                     f"<span class='swatch' aria-hidden='true'>{dots}</span></button>")
    conf, now = scheduler.settings(), datetime.datetime.now()
    state, on = load_json(scheduler.STATE), scheduler.enabled(conf)
    # A job a row: its name and what it does under it, whether it runs, when last and next.
    rows = "".join(
        f"<tr><td><span title='runs {esc(' '.join(args))}'>{esc(job)}</span>"
        f"<span class='conn'>{esc(scheduler.ABOUT.get(job, ''))}</span></td><td><input type='checkbox' name='s-job'"
        f" value='{esc(job)}' aria-label='Run {esc(job)}'{' checked' if job in on else ''}></td>"
        f"<td class='when'>{esc(state.get(job, '—')[:16].replace('T', ' '))}</td>"
        f"<td class='when'>{scheduler.next_due(slots, now):%a %d %b %H:%M}</td></tr>"
        for job, (args, slots) in scheduler.JOBS.items())
    modes = "".join(f"<option value='{esc(k)}' data-why='{esc(why)}'{' selected' if k == conf['mode'] else ''}>"
                    f"{esc(label)}</option>" for k, (label, why) in scheduler.MODES.items())
    sections = [
        set_section("set-theme", "Theme", "Applied and saved the moment you pick one; every theme passes the"
                    " contrast check.", f"<div class='themes' role='group' aria-label='Theme'>{''.join(cards)}</div>"
                    "<p class='rules'>The \"by Wendell Research\" credit is not a setting.</p>", form=False),
        client_card(), access_card(),
        set_section("set-scheduler", "Scheduler", "The recurring checks, run by the dashboard itself; a change"
                    " applies within a minute, without a restart.",
                    f"<label class='field' for='s-mode'>Mode</label><select id='s-mode' data-was='{esc(conf['mode'])}'>"
                    f"{modes}</select><p class='rules' id='s-mode-why'></p>"
                    "<div class='scroll'><table class='jobs'><thead><tr>" + th("Job") + th("On") + th("Counted up to")
                    + th("Next") + f"</tr></thead><tbody>{rows}</tbody></table></div>"
                    "<p class='rules'>Run makes model calls on its own schedule. If something else also runs these"
                    " jobs (cron, Windows Task Scheduler), turn it off in the same step, or every job runs twice.</p>"
                    "<div class='errors' id='s-errors' role='alert'></div><div class='done' id='s-done'></div>"
                    "<div class='actions'><button class='btn primary' id='s-save'>Save scheduler</button></div>"),
        gateway_keys_card(), move_card(), own_client_card(), restart_block()]
    links = "".join(f"<li><a href='#{i}'>{t}</a></li>" for i, t in re.findall(r"<div class='panel[^']*' id='([\w-]+)'><h2>([^<]+)</h2>",
                                                                               "".join(sections)))
    chosen, source = model_client.chosen()
    facts = (("Version", f"{esc(RELEASE)}, <span class='mono'>{esc(COMMIT or '—')}</span>"
              + (f" of {esc(RELEASE_DATE)}" if RELEASE_DATE else "")),
             ("Data folder", f"<span class='mono'>{esc(str(DATA))}</span> ({esc(paths.WHY)})", "BENCHWARMERS_HOME"),
             ("Port", str(PORT), "BENCH_PORT"), ("Scheduler", esc(scheduler.MODES[conf["mode"]][0])),
             ("Model client", esc(chosen), *(["BENCH_CLIENT"] if source == "environment" else [])))
    return (f"<div class='cols flow'>{''.join(sections)}</div>"
            "<aside class='facts' id='facts-settings' aria-labelledby='facts-settings-h'>"
            "<div class='bar'><h2 id='facts-settings-h'>On this page</h2>"
            "<button type='button' class='linkish facts-close'>Close</button></div><div class='facts-body'>"
            f"<nav class='jump' aria-labelledby='facts-settings-h'><ol>{links}</ol></nav>"
            f"<h3>This install now</h3>{set_rows(facts)}</div></aside>")


def endpoints_page() -> str:
    """The endpoints, a row each (name, base URL, key, models on the bench, when
    the daily served check last listed it); one clicked opens beside the list in
    the docked panel (bench.js openEp): its facts, its actions, what it serves.
    With none open, the panel holds what applies to all of them and your own
    apps. Under the list, rate limits per connection (bench.js fills the apps
    and the limits from /api/connect and /api/connections)."""
    eps = endpoint_list()["endpoints"]
    # What each endpoint serves, without asking it: the bench's own models on
    # it, plus what the daily served check (scripts/check_deprecations.py)
    # last listed, keyed `served` on the default endpoint, `served@endpoint`
    # elsewhere. A page that needs the network would be down when you need it.
    snap = load_json(DATA / "config" / "served-snapshot.json")
    checked = snap.get("updated", "")
    names, default = {e["name"] for e in eps}, next((e["name"] for e in eps if e["default"]), "")
    serves: dict[str, dict[str, str]] = {n: {} for n in names}     # endpoint -> served -> last seen
    for key, day in (snap.get("last_seen") or {}).items():
        served, _, ep = key.rpartition("@")
        if ep not in names:
            served, ep = key, default
        serves[ep][served] = day
    on: dict[str, dict[str, str]] = {n: {} for n in names}         # endpoint -> served -> alias
    for r in endpoints.registry():
        on.setdefault(r["endpoint"], {})[r["served"]] = r["alias"]
    for a, d in decide.load().items():
        on.setdefault(d["endpoint"], {})[d["served"]] = a
    tags, status, data = load_json(DATA / "config" / "provider-tags.json"), model_status(), endpoints.load()
    table, docks = [], []
    for i, e in enumerate(eps):
        mine, listed = on.get(e["name"], {}), serves.get(e["name"], {})
        rows = []
        for served in sorted(set(mine) | set(listed), key=lambda s: (s not in mine, s.lower())):
            alias, day = mine.get(served), listed.get(served)
            # A model on the bench by its alias (and its connection), what it is served as under
            # it; one only served by its id and connection. Its state at the right.
            if alias:
                label, css, why = status.get(alias, ("decision", "", "a decision model"))
                rows.append(f"<tr><td>{named(alias)}</td><td>served as <span class='mono'>{esc(served)}</span></td>"
                            f"<td>{chip(label, css, why)}</td></tr>")
            else:
                gone = day and day != checked
                conn = (tags.get(served, "").split("/")[0] if e["hub"] == "openwebui" else e["name"]) or ""
                rows.append(f"<tr><td><span class='alias'>{esc(served)}</span><span class='conn'>{esc(conn)}</span></td>"
                            "<td>not on the bench</td><td>"
                            + (chip(f"last seen {day}", "warn") if gone else chip("served", "")) + "</td></tr>")
        now = sum(1 for d in listed.values() if d == checked)
        seen = max(listed.values(), default="")
        # When the daily served check last had its list: on its last run, or an older day (it did not answer since).
        when = (f"<td class='when' data-sort='{esc(seen)}'>{esc(seen)}</td>" if seen and seen == checked else
                f"<td data-sort='{esc(seen)}'>" + chip(f"not listed since {seen}", "warn",
                                                       f"the daily served check on {checked} got no list from it")
                + "</td>" if seen else "<td class='muted' data-sort=''>—</td>")
        # Its actions (bench.js reads the endpoint from data-ep): a sign-in again
        # where it came from one; Make default and Remove on all but the default.
        signed = e["connect"]
        acts = "<button class='btn' type='button' data-ep-act='change'>Change key</button>"
        if signed and signed.get("flow") and signed["flow"] != "local":
            acts += "<button class='btn' type='button' data-ep-act='again'>Sign in again</button>"
        if not e["default"]:
            acts += "<button class='btn' type='button' data-ep-act='default'>Make default</button>"
        acts += "<button class='btn' type='button' data-dock-close>Close</button>"
        if not e["default"]:
            acts += "<button class='linkish danger' type='button' data-ep-act='remove'>Remove</button>"
        until = (" until " + signed["expires"][:10]) if signed and signed.get("expires") and not signed.get("refreshes") else ""
        key = (chip("key set" if e["key_set"] else "no key", "ok" if e["key_set"] else "warn")
               + ((" " + chip("sign in again", "warn") if signed.get("expired") else " " + chip("signed in" + until, "via"))
                  if signed else ""))
        name, hub = esc(e["name"]), endpoints.HUBS.get(e["hub"], "")
        # The row: its name opens it (a button, for a keyboard); the models it serves
        # are what the page's filter finds it by too (bench.js applyFilter, data-find).
        table.append(
            f"<tr class='c-row ep-row' data-name='{name}' data-find='{esc(' '.join(sorted(set(mine) | set(listed) | set(mine.values()))))}'>"
            f"<td><button type='button' class='c-open alias' aria-label='Open endpoint {name}'>{name}</button>"
            + (chip("default", "") if e["default"] else "") + f"</td><td class='base' title='{esc(e['base'])}'>"
            f"<span class='mono'>{esc(e['base'])}</span></td><td>{key}</td><td class='num'>{len(mine)}</td>"
            f"<td class='num'>{now if seen else '—'}</td>{when}</tr>")
        facts = (("Base URL", f"<span class='mono'>{esc(e['base'])}</span>"),
                 ("Kind", esc(e["kind"]) + (f", {esc(hub)}" if hub else "")),
                 ("API", esc(endpoints.APIS.get(data.get(e["name"], {}).get("api") or "chat", ""))),
                 ("Key", key),
                 ("Signed in", (esc(signed.get("label") or signed.get("provider", "")) + esc(until)) if signed else ""),
                 ("On the bench", f"{len(mine)} model{'s' * (len(mine) != 1)}"
                  + (", the default endpoint" if e["default"] else "")),
                 ("Listed", f"{now} on {esc(checked)}, by the daily served check" if seen == checked and seen
                  else f"not since {esc(seen)}" if seen else "never by the daily served check"))
        docks.append(
            f"<dialog class='dock' id='ep-{i}' data-name='{name}' data-ep='{esc(json.dumps(e))}' aria-labelledby='ep-{i}-h'>"
            f"<div class='bar'><p>Endpoint</p></div><div class='dock-body'><h2 class='dock-head' id='ep-{i}-h' tabindex='-1'>{name}</h2>"
            "<dl class='rows'>" + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in facts if v) + "</dl>"
            f"<h3>What it serves</h3><p class='sub'>The bench's models on it, then what the daily served check"
            " listed.</p>" + (f"<table class='serves'><tbody>{''.join(rows)}</tbody></table>" if rows else
                              "<p class='muted'>Nothing yet: no model on the bench comes through it, and the daily"
                              " served check has no list from it.</p>") + "</div>"
            f"<div class='dock-foot'>{acts}</div></dialog>")
    keyed = sum(1 for e in eps if e["key_set"] and not (e["connect"] or {}).get("expired"))
    facts = (("Endpoints", f"{len(eps)}" + (f", the default {esc(default)}" if default else "")),
             ("With a key", f"{keyed} of {len(eps)}"),
             ("Models through them", sum(len(m) for m in on.values())),
             ("Last listed", f"{esc(checked)}, by the daily served check" if checked else "not yet"))
    return (
        # The list and the rate limits: one under the other, side by side on a wide screen (bench.css).
        "<div class='cols'><div class='panel'><p class='sub'>Where models are served from, a base URL and a key: prompts go"
        " straight there, so add only endpoints you would send private source to.</p>"
        # Model servers running here, not endpoints yet: bench.js asks /api/connect/local on show.
        "<div class='found' id='e-found' hidden><h2>Found on this machine</h2><div id='e-found-list'></div></div>"
        + ("<div class='scroll'><table class='ep-table'><thead><tr>" + th("Endpoint") + th("Base URL") + th("Key")
           + th("On the bench", True) + "<th data-sortable tabindex='0' class='num' title='models its list held when"
           " the daily served check last asked'>Listed</th>" + th("Last listed")
           + f"</tr></thead><tbody>{''.join(table)}</tbody></table></div>" if eps else
           empty_state("No endpoint yet: every model is served from one.", step_button(),
                       "Add an endpoint pastes a key for any OpenAI-compatible API, or signs in to a provider on its"
                       " own page."))
        + "</div><div class='panel'><h2>Rate limits per connection</h2>"
        "<p class='sub'>What a key behind each connection allows; the models on that connection share it.</p>"
        "<details class='key'><summary>How these limits work</summary>"
        "<p>From the provider's docs or freellmapi.co/models. A model's own limits (its panel) win, and limits the"
        " bench learns from errors only ever lower these. rpm and rpd are requests a minute and a day, tpm and tpd"
        " tokens, rpmo requests a calendar month (UTC), for a trial key's monthly cap. The built-in client keeps"
        " to them: when one is used up it waits if the call can still finish in time, else it does not send.</p>"
        "<p>The bench also learns a connection's own rpm and rpd: when a provider refuses"
        " with a 429 that names no number, the requests the bench had sent in that minute or day are the"
        " limit, near enough. Met once it is only noted; met again at about the same count, a minute or a"
        " day later, it is kept to like a limit of yours, unless you set your own here, which always wins."
        " A learned limit not met again for 14 days is forgotten, so a raised limit is found; Forget drops"
        " one now.</p></details>"
        "<div class='scroll' id='l-list'></div><div class='errors' id='l-errors' role='alert'></div>"
        "<div class='done' id='l-done'></div></div></div>" + "".join(docks) +
        # What applies to every endpoint, beside the list (bench.css aside.facts).
        "<aside class='facts' id='facts-endpoints' aria-labelledby='facts-endpoints-h'>"
        "<div class='bar'><h2 id='facts-endpoints-h'>All endpoints</h2>"
        "<button type='button' class='linkish facts-close'>Close</button></div><div class='facts-body'>"
        "<dl class='rows'>" + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in facts) + "</dl>"
        "<p>Keys are kept where <code>llm keys set</code> keeps them, never in the repository, and"
        " are never shown again.</p><p><b>Add an endpoint</b>, in the bar, pastes a"
        " key for any OpenAI-compatible API, a hub such as Open WebUI or a local runtime, or signs in to a"
        " provider on its own page.</p>"
        "<h3>Use your own app</h3><div class='form-panel'><p class='rules'>Some providers need an app registered"
        " with them before anyone can sign in. Benchwarmers ships Wendell Research's where it has one; register"
        " your own and paste its public client id to use yours instead (never a secret: none of these use one)."
        " Endpoints already signed in keep working with the app they were signed in with.</p>"
        "<div id='cn-apps'></div><div class='errors' id='cn-app-errors' role='alert'></div>"
        "<div class='done' id='cn-app-done'></div></div></div></aside>")


def _reply(by_prompt: dict) -> tuple | None:
    """Models' Reply: the daily probe's median seconds to a one-word answer (tiny),
    with the 18 KB prompt's (large) and tokens a second on hover, as a cell
    (text, sort, title); None when it never answered."""
    def p50(r):
        try:
            return float(r["p50_s"]) if r and int(r.get("answered") or 0) > 0 else None
        except (KeyError, TypeError, ValueError):
            return None
    tiny, large = p50(by_prompt.get("tiny")), p50(by_prompt.get("large"))
    if tiny is None and large is None:
        return None
    shown = tiny if tiny is not None else large
    why = "; ".join(b for b in (
        f"one-word prompt {tiny:.1f}s" if tiny is not None else "",
        f"18 KB prompt {large:.1f}s" if large is not None else "",
        f"{(by_prompt.get('large') or {}).get('tokens_per_s')} tok/s"
        if (by_prompt.get("large") or {}).get("tokens_per_s") else "") if b)
    return f"{shown:.1f}s", f"{shown:.2f}", f"median, the daily probe: {why}"


# ---- a table whose columns are (key, heading, class, what it means), its cells
# (text, sort[, title]) or None where nothing is known (Models, Quota) ------------
def _cell(css: str, v) -> str:
    """A cell, or a quiet dash where nothing is known, below every figure in a sort."""
    if not v:
        return f"<td class='{css} muted'" + (" data-sort='-1'" if "num" in css else "") + ">—</td>"
    text, sort, *title = v
    return (f"<td class='{css}'" + (f" data-sort='{esc(sort)}'" if sort is not None else "")
            + (f" title='{esc(title[0])}'" if title else "") + f">{text}</td>")


def _known(cols: tuple, rows: list) -> list:
    """The columns something is known in, in some row; the first always."""
    return [c for i, c in enumerate(cols) if not i or any(r.get(c[0]) for r in rows)]


def _head(cols) -> str:
    """The sortable headings, each with what it means in its title."""
    return "".join(f"<th data-sortable tabindex='0'" + (f" class='{css}'" if css else "")
                   + (f" title='{esc(why)}'" if why else "") + f">{esc(label)}</th>" for _, label, css, why in cols)


def _at(iso) -> tuple | None:
    """A moment as a cell (text, sort, title): local time on one line, a weekday
    and the time within six days of now, else the date and time."""
    try:
        when = datetime.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None
    near = abs(when.timestamp() - time.time()) < 6 * 86400
    return when.strftime("%a %H:%M" if near else "%Y-%m-%d %H:%M"), int(when.timestamp()), when.strftime("%Y-%m-%d %H:%M")


# Models' columns (models_page): key, heading, class, what it means (the heading's
# title and the panel's key). As on Discover, an `opt` column leaves a narrow list
# and a `wide` one joins a wide one (bench.css .list); a column with nothing known
# in any row is left out.
MODEL_COLS = (
    ("model", "Model", "", ""),
    ("state", "State", "", "What its last probe found: healthy, throttled, not found and the like; hover one for why"),
    ("through", "Through", "", "The endpoint of this install it is reached through"),
    ("context", "Context", "num", "Context window, in tokens"),
    ("output", "Max output", "num", "The longest answer, in tokens"),
    ("input", "Input", "opt", "What it takes in"),
    ("reply", "Reply", "num", "The daily probe's median seconds to a one-word answer; hover one for the 18 KB prompt's"),
    ("role", "Role", "wide", "What the bench picks it for"),
    ("can", "Can", "wide", "Calls tools, reads images, keeps to a JSON schema, reasons before answering"),
    ("price", "Price", "num wide", "List price, dollars per million tokens in / out; a free tier may still apply"),
    ("answered", "Last answered", "wide when", "The day the daily probe last had an answer from it"),
    *((k, k, "num opt", catalog.LIMIT_LABELS[k].capitalize()) for k in catalog.LIMIT_KEYS))


def models_page() -> str:
    """Every model on the bench in one table, a group of rows per connection it
    comes from, each folding under its heading (bench.js fold): its state, the
    endpoint it is reached through and what is known of its size, speed, price
    and limits, more columns as the pane widens. A name opens the model docked
    beside the list; with none open, the panel holds the counts and the key."""
    # Which of this install's endpoints each model is reached THROUGH, beside
    # the connection (provider) it comes FROM: an OpenRouter model may be on
    # OpenRouter's own endpoint or arrive through Open WebUI's.
    through = {r["alias"]: r["endpoint"] for r in endpoints.registry()}
    decisions = decide.load()
    through.update({a: d["endpoint"] for a, d in decisions.items()})
    # Every model, decision models too (connections_view is chat models only,
    # for what a collection may hold): they are on the bench like any other.
    directory, status = model_directory(), model_status()

    def aside(body: str) -> str:              # with no model open: what the panel is for (bench.css aside.facts)
        return ("<aside class='facts' id='facts-models' aria-labelledby='facts-models-h'>"
                "<div class='bar'><h2 id='facts-models-h'>All models</h2>"
                f"<button type='button' class='linkish facts-close'>Close</button></div><div class='facts-body'>{body}"
                "</div></aside>")
    if not directory:
        return (empty_state("Nobody is on the bench yet.", step_button(),
                            "A model joins it by onboarding: picked from what an endpoint serves, probed with 4"
                            " calls and run through the internal suite.")
                + aside("<p>Nobody is on the bench yet. Once a model is onboarded, choosing it opens its editor"
                        " here.</p>"))
    # The daily probe's latest reading per model and prompt (perf/history.csv), and its last answer.
    speed: dict[str, dict] = {}
    answered: dict[str, str] = {}
    for r in perf_rows():
        speed.setdefault(r["model"], {})[r.get("prompt", "")] = r
        if int(r.get("answered") or 0) > 0:
            answered[r["model"]] = max(answered.get(r["model"], ""), r.get("timestamp", ""))

    def num(v):
        return (f"{v:,}", v) if isinstance(v, int) else None
    # Each model's cells, (text, sort[, title]) by column, None where nothing is known.
    cells: dict[str, dict] = {}
    for a, d in directory.items():
        f = catalog.info(a)
        lim, cost = f.get("limits") or {}, f.get("cost") or {}
        label, css, why = status.get(a, ("", "", ""))
        free, ep = price_of(a, d["served"], f), through.get(a, "?")
        can = ", ".join(v for k, v in CAP_LABELS.items() if f.get(k))
        cells[a] = {"state": (chip(label, css, why), label) if label else None,
                    "through": (chip(ep, "via", f"reached through the {ep} endpoint"), ep),
                    "context": num(f.get("context_window")), "output": num(f.get("max_output")),
                    "input": (esc(", ".join(f["input_modalities"])), None) if f.get("input_modalities") else None,
                    "reply": _reply(speed.get(a, {})), "role": (esc(d["role"]), d["role"]),
                    "can": (can, can) if can else None,
                    "price": ("free", 0) if free == "free" else
                    (f"{cost['input']:g} / {cost['output']:g}", cost["input"])
                    if all(isinstance(cost.get(k), (int, float)) for k in ("input", "output")) else
                    ("paid", 1e9) if free == "paid" else None,
                    "answered": (answered[a][:10], answered[a]) if a in answered else None,
                    **{k: num(lim.get(k)) for k in catalog.LIMIT_KEYS}}
    cols = _known(MODEL_COLS, list(cells.values()))

    def row(a: str) -> str:
        kind = (" " + chip("decision", "", "a decision model: answers typed questions, not chat")
                if a in decisions else "")
        if directory[a].get("ad_hoc"):
            kind += " " + chip("ad hoc", "adhoc", "ad hoc only: never chosen by the bench on its own, only when you"
                               " name it (a collection member, with, --model)")
        return (f"<tr><td><a href='#' class='model' data-alias='{esc(a)}'><span class='alias'>{esc(a)}</span></a>"
                f"{kind}</td>" + "".join(_cell(css, cells[a].get(k)) for k, _, css, _ in cols[1:]) + "</tr>")
    by: dict[str, list[str]] = {}
    for a in sorted(directory):
        by.setdefault(catalog.connection_of(a), []).append(a)
    # A group per connection, its heading a button that folds it; the page's one box
    # filters them all (bench.js applyFilter, data-conn), a heading sorts within each.
    body = "".join(
        f"<tbody data-conn='{esc(c)}'><tr class='grp'><th colspan='{len(cols)}' scope='rowgroup'>"
        f"<button type='button' aria-expanded='true'><span class='fold-chev' aria-hidden='true'></span>{esc(c)}"
        f"<span class='muted'>{len(m)} model{'s' * (len(m) != 1)}</span></button></th></tr>"
        + "".join(row(a) for a in m) + "</tbody>" for c, m in sorted(by.items()))
    states: dict[str, list] = {}
    for a in directory:
        label, css, _ = status.get(a) or ("decision model", "", "")
        states.setdefault(re.sub(r" in \d+d$", "", label), [css, 0])[1] += 1

    def rows(pairs) -> str:
        return "<dl class='rows'>" + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in pairs) + "</dl>"
    facts = (("On the bench", f"{len(directory)} models"), ("Connections", len(by)),
             ("Endpoints", len({through.get(a) for a in directory})),
             ("Ad hoc only", sum(1 for d in directory.values() if d.get("ad_hoc"))),
             ("Decision models", len(decisions)))
    return (
        "<p class='sub'>Every model on the bench, by the connection it comes from; a name opens it beside the list.</p>"
        f"<div class='list'><table class='m-table'><thead><tr>{_head(cols)}</tr></thead>{body}</table></div>"
        # With no model open: the counts, and what the columns mean.
        + aside(rows(facts) + "<h3>By state</h3>"
                + rows((chip(k, css), n) for k, (css, n) in sorted(states.items(), key=lambda kv: -kv[1][1]))
                + "<h3>By connection</h3>" + rows((esc(c), len(m)) for c, m in sorted(by.items()))
                + "<p>Open a model to change its alias, role, facts or call options, or to retire it.</p>"
                "<details class='key'><summary>What the columns mean</summary><dl>"
                + "".join(f"<dt>{esc(label)}</dt><dd>{esc(why)}</dd>" for _, label, _, why in cols if why)
                + "</dl></details>"))


def quota_tiles() -> list[dict]:
    """metrics/quotaview.py's tiles, each named as the collection popup names
    its provider, unless the live read named the credentials behind it."""
    import quotaview
    return [dict(t, name=t["name"] or provider_label(t["tag"])) for t in quotaview.tiles()]


def quota_op(which: str, tag) -> dict:
    """Refresh on one tile, which picks a free read, one short call or the
    records (quotaview.refresh): {ok, errors, note, tile}; "order" saves the
    tiles' order (a list of tags, empty for the default sort)."""
    import quotaview
    if which == "order":
        return quotaview.save_order(tag)
    if tag not in {t["id"] for t in quota_tiles()}:
        return {"ok": False, "errors": [f"no tile called {tag!r}"]}
    if which == "about":                               # the Models popup: how it is reached
        try:
            return {"ok": True, "errors": [], **quotaview.about(tag)}
        except (ValueError, OSError) as exc:
            return {"ok": False, "errors": [str(exc)]}
    try:
        r = quotaview.refresh(tag)
    except (ValueError, OSError) as exc:              # OSError: URLError and timeouts
        return {"ok": False, "errors": [str(exc)]}
    if not r.get("ok"):
        return {"ok": False, "errors": r.get("errors") or ["it failed"]}
    return {"ok": True, "errors": [], "note": r.get("note", ""),
            "tile": next((t for t in quota_tiles() if t["id"] == tag), None)}


# Quota's Known limits (quotas_page): a row a limit.
QUOTA_COLS = (("conn", "Connection", "", ""), ("limit", "Limit", "", "What is counted, and over what window"),
              ("left", "Left", "num", "What is left of it now"), ("used", "Used", "num", "What is used of it now"),
              ("of", "Allowed", "num", "All of it, in the provider's own unit"),
              ("reset", "Resets", "when", "When it fills again, in local time"),
              ("from", "Known from", "", "The provider's quota endpoint, its replies' headers, or a cap the bench"
                                         " counts its own calls against"))


def quotas_page() -> str:
    """Quota management: a tile per connection (bench.js qTile, from the tiles
    in quota-data, metrics/quotaview.py): what is left and when it resets,
    shaped by how its provider limits, who is resting, its Refresh, and its
    Models, which opens it docked beside the tiles (qModels). Under them the
    resets of the next week and what the bench sent (its usage ledger,
    metrics/ledger.py), each chart drawn as it comes near; then every limit
    known, a row a limit. With no connection docked the panel holds the counts
    and how the page works."""
    import learn
    import ledger
    now = datetime.datetime.now().astimezone()
    t = now.timestamp()
    sent: dict = {}
    month = ledger.month_start(t)
    for row in ledger.rows(min(t - 86400, month)):
        s = sent.setdefault(row.get("connection") or "?", [0, 0, 0, 0])
        n, tok = row.get("attempts") or 0, (row.get("tokens_in") or 0) + (row.get("tokens_out") or 0)
        if row["t"] >= t - 86400:
            s[0], s[1] = s[0] + n, s[1] + tok
        if row["t"] >= month:
            s[2], s[3] = s[2] + n, s[3] + tok

    def num(v):
        return (f"{v:,}" if isinstance(v, int) else f"{v:,.2f}", v) if isinstance(v, (int, float)) else None
    tiles = quota_tiles()
    limits = []
    for q in tiles:
        conn = (esc(q["name"]), q["name"].lower())
        src = (q.get("source") or [""])[0]
        # Its windows: what its quota endpoint or its replies' headers said, its pool, the caps counted for it.
        for label, w, frm, why in (
                [((g["label"] + " · " if g["label"] else "") + w["label"], w, src, "") for g in q["groups"]
                 for w in g["windows"]]
                + [(p["label"], dict(p, remaining=None), src, "") for p in q["pool"]]
                + [(w["label"], w, g["label"], g.get("title", "")) for g in q["caps"] for w in g["windows"]]):
            used = w.get("used", w["of"] - w["remaining"] if w.get("of") is not None and w.get("remaining") is not None
                         else None)
            # Left: the figure (the tiles carry the meters), what is left of what in its title.
            left = w.get("left")
            limits.append({"conn": conn, "limit": (esc(label), label),
                           "left": None if left is None else (f"{left}%", left)
                           + ((f"{w['remaining']:,} of {w['of']:,} left",) if w.get("remaining") is not None else ()),
                           "used": num(used), "of": num(w.get("of")), "reset": _at(w.get("reset_at")),
                           "from": (esc(frm), frm, why) if why else (esc(frm), frm) if frm else None})
    cols = _known(QUOTA_COLS, limits)
    # What the bench sent, a row a connection the ledger names, by the name the page gives it
    # (two hosts of one provider are one row), most this month first: the chart (bench.js,
    # from quota-data) and, folded under it, its numbers.
    named_sent: dict = {}
    for c, s in sent.items():
        row = named_sent.setdefault(provider_label(c) if c != "?" else "", [0, 0, 0, 0])
        row[:] = [a + b for a, b in zip(row, s)]
    by_month = sorted(named_sent.items(), key=lambda kv: (-kv[1][2], kv[0]))
    sent_table = ("<table><thead><tr>" + th("Connection") + th("Requests, 24 h", True) + th("Tokens, 24 h", True)
                  + th("Requests, this month", True) + th("Tokens, this month", True) + "</tr></thead><tbody>"
                  + "".join(f"<tr><td>{esc(c) or '<span class=muted>not recorded</span>'}</td>"
                            + "".join(f"<td class='num'>{v:,}</td>" for v in s) + "</tr>" for c, s in by_month)
                  + "</tbody></table>" if sent else "<p class='muted'>No call in the ledger this month.</p>")
    data = json.dumps({"tiles": tiles,
                       "sent": [{"provider": c or "not recorded", "day": s[0], "month": s[2]} for c, s in by_month if s[2]]},
                      ensure_ascii=False).replace("</", "<\\/")
    entered = catalog.connection_limits()
    keys = catalog.CONNECTION_LIMIT_KEYS
    lim_rows = [f"<tr><td>{esc(conn)}</td>" + "".join(
                    f"<td class='num'>{lim[k]:,}</td>" if lim.get(k) else "<td class='num muted'>—</td>" for k in keys)
                + "<td>you</td></tr>" for conn, lim in sorted(entered.items()) if any(lim.get(k) for k in keys)]
    # Learned from providers' own error messages (metrics/learn.py), per model.
    # Shown without the raw message: it can carry an account id.
    for alias, rec in sorted(learn.learned().items()):
        for k, ev in (rec.get("limits") or {}).items():
            if k in keys:
                lim_rows.append(f"<tr><td>{named(alias)}</td>"
                                + "".join(f"<td class='num'>{ev['value']:,}</td>" if kk == k
                                          else "<td class='num muted'>—</td>" for kk in keys)
                                + f"<td>learned {esc(ev.get('at', '')[:10])} ({esc(ev.get('where', ''))})</td></tr>")
    # And per connection, from 429s that named no number, where you set none.
    for conn, axes in sorted(learn.connection_learned().items()):
        for k, ev in axes.items():
            if not (entered.get(conn) or {}).get(k):
                lim_rows.append(f"<tr><td>{esc(conn)}</td>"
                                + "".join(f"<td class='num'>{ev['value']:,}</td>" if kk == k
                                          else "<td class='num muted'>—</td>" for kk in keys)
                                + f"<td>learned {esc(ev.get('at', '')[:10])} (its 429s)</td></tr>")
    # The tiles (bench.js qDraw), then the two charts, side by side where each has 52rem (.cols.fit;
    # each a report: drawn when it comes near, bench.js drawNear), then Known limits.
    body = (
        "<p class='sub'>What is left with each connection, when it resets, and who is resting.</p>"
        "<div class='panel'><div class='q-tiles' id='q-tiles'></div></div><div class='cols fit'>"
        f"<div class='panel report' id='report-q-resets'>{report_head('Resets, next 7 days')}<p class='sub'>A dot"
        " where a limit resets (a daily one every day), a bar while a model rests, the line is now.</p>"
        "<div class='chart' id='chart-resets'></div></div>"
        + chart_card("What the bench sent", "Requests per connection: this month, and the last 24 hours inside it.",
                     "chart-sent", sent_table,
                     "<p>One row per connection, most requests this month first. The pale bar is this month's"
                     " requests, the dark one inside it the last 24 hours'; the label reads 24 h / month. Retries"
                     " count. Calls through the llm CLI are not in the ledger.</p>", retired=False)
        .replace("<div class='panel'", "<div class='panel report' id='report-q-sent'", 1)
        + "</div><section class='panel'><h2>Known limits</h2><p class='sub'>What a provider states, or a cap the"
          " bench counts its own calls against.</p>"
        + (f"<div class='scroll'><table><thead><tr>{_head(cols)}</tr></thead><tbody>"
           + "".join("<tr>" + "".join(_cell(css, r.get(k)) for k, _, css, _ in cols) + "</tr>" for r in limits)
           + "</tbody></table></div>" if limits else "<p class='muted'>None stated, and none set: Endpoints sets a cap.</p>")
        + "<details class='key'><summary>How limits are known</summary><p>A limit is what you enter per connection"
          " (Endpoints, Rate limits per connection), or what a provider states in a rate-limit error, which the"
          " bench writes down by itself (or, when the error names no number, works out for the connection from the"
          " requests it had sent). A model's own limits (its panel) win, and learned ones only ever lower a limit,"
          " until they have not been met again for 14 days and are forgotten.</p></details>"
          f"<details class='key'><summary>Every limit known · {len(lim_rows)}</summary>"
        + ("<div class='scroll'><table><thead><tr>" + th("Connection or model") + "".join(th(k, True) for k in keys)
           + th("From") + f"</tr></thead><tbody>{''.join(lim_rows)}</tbody></table></div>" if lim_rows
           else "<p class='muted'>None known yet.</p>")
        + "</details></section>")
    if not (tiles or sent or lim_rows):                # an empty bench: what goes here, and the step that fills it
        body = empty_state("Nothing to measure yet: each connection a model comes from is counted here.",
                           step_button())
    # With no connection docked, the panel: the counts, and how the page works.
    soon = min(((a, q["name"]) for q in tiles for a in [_at(q.get("next_reset"))] if a),
               key=lambda p: p[0][1], default=None)
    facts = (("Connections", len(tiles)), ("Out", sum(1 for q in tiles if q["out"])),
             ("Free to call", f"{sum(q['live'] for q in tiles)} of {sum(q['bench'] for q in tiles)} models"),
             ("Next reset", f"{soon[0][0]} · {esc(soon[1])}" if soon else "<span class='muted'>—</span>"),
             ("Sent this month", f"{sum(s[2] for s in sent.values()):,} requests"))
    return (
        body
        # The connection in hand, docked beside the tiles (bench.js qModels fills it).
        + "<dialog class='dock' id='q-models' aria-labelledby='q-models-h'><div class='bar'><p>Connection</p></div>"
        "<div class='dock-body'><h2 class='dock-head' id='q-models-h' tabindex='-1'></h2>"
        "<div class='sub q-reach' id='q-models-about'></div><p class='sub' id='q-models-sub'></p>"
        "<h3>Its models</h3><div id='q-models-list'></div></div><div class='dock-foot'>"
        "<button class='btn' type='button' id='q-models-close'>Close</button></div></dialog>"
        "<aside class='facts' id='facts-quotas' aria-labelledby='facts-quotas-h'>"
        "<div class='bar'><h2 id='facts-quotas-h'>All connections</h2>"
        "<button type='button' class='linkish facts-close'>Close</button></div><div class='facts-body'>"
        "<dl class='rows'>" + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in facts) + "</dl>"
        "<p>A tile per connection: what is left of each limit it states and when that resets, who is resting"
        " after a rate limit, and its models, which its Models button opens here.</p>"
        "<h3>Refresh</h3><p>A tile's Refresh asks the provider for free where it can (CLI Proxy API's credentials,"
        " OpenRouter); where a provider states its limits only in its replies, it sends one 16-token request to"
        " read them; elsewhere it reads the bench's own records again. Its title says which. Refresh all live, in"
        " the bar, asks every one that tells for free.</p>"
        "<h3>The meters</h3><p>Ten segments filled like a bar, what is left leading the line and its reset at the"
        " end: green from 70% left, amber from 25%, red under that. A connection that is out, a limit used up or"
        " every model resting, has a red edge. Where nothing is stated, the meter is its calls of the last 24"
        " hours, red for those that failed.</p>"
        "<h3>Order</h3><p>Drag a tile, or move it with the arrow keys, to keep it where you want it. Sort puts"
        " them back: out first, then the soonest reset.</p></div></aside>"
        f"<script type='application/json' id='quota-data'>{data}</script>")


def _from_end(path: pathlib.Path):
    """A log's lines that hold something, newest first, read from its end in
    growing chunks, so a caller that stops early never reads the rest: the
    gateway's log runs to 50 MB. Nothing when it cannot be read."""
    try:
        fh = open(path, "rb")
    except OSError:
        return
    with fh:
        end, back, cut = fh.seek(0, os.SEEK_END), 1 << 18, b""
        while end:
            start = max(end - back, 0)
            fh.seek(start)
            lines = (fh.read(end - start) + cut).split(b"\n")
            cut = lines.pop(0) if start else b""        # may be cut: finished by the chunk before it
            for line in reversed(lines):
                if line.strip():
                    yield line.decode("utf-8", "replace").rstrip("\r")
            end, back = start, back * 4


def _log_rows(path: pathlib.Path, limit: int = 200) -> list[str]:
    """The last `limit` lines that hold something, newest first. Read from the
    end: the gateway's log runs to 50 MB, and was read whole for 200 lines."""
    from itertools import islice
    return list(islice(_from_end(path), limit))


def logs_page() -> str:
    """The logs down the left (name, size, last written), the one chosen beside
    them in a box that scrolls by itself (bench.js pick): the newest 200
    lines of each, newest first, from the files that already exist. Gateway
    calls show who sat and how it went, never the prompt or the answers."""
    gw_file, changes_file, sched_file = (DATA / "runs" / "gateway" / "calls.jsonl", DATA / "config" / "changes.jsonl",
                                         DATA / "runs" / "scheduler.log")
    gw = []
    for line in _log_rows(gw_file):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        seats = ", ".join(f"{s.get('alias')} {s.get('status')}" for s in r.get("seats", []))
        gw.append(f"<tr><td class='when'>{esc(r.get('at', '')[:16].replace('T', ' '))}</td>"
                  f"<td>{esc(r.get('model', ''))}</td><td>{esc(r.get('shape', ''))}</td><td>{esc(seats)}</td></tr>")
    changes = []
    for line in _log_rows(changes_file):
        try:
            c = json.loads(line)
        except ValueError:
            continue
        changes.append(f"<tr><td class='when'>{esc(c.get('at', '')[:16].replace('T', ' '))}</td>"
                       f"<td>{esc(c.get('by', ''))}</td><td>{esc(c.get('alias') or '')}</td>"
                       f"<td>{esc(c.get('field', ''))}</td><td>{esc(str(c.get('new') or ''))[:160]}</td></tr>")
    sched = [f"<div class='log-line'>{esc(line)}</div>" for line in _log_rows(sched_file)]
    runs = sorted((DATA / "runs").glob("onboard-*.log"), reverse=True)
    onboard = [f"<li class='log-line'>{esc(p.name)}</li>" for p in runs[:30]]

    def table(heads, rows):
        return rows and ("<table><thead><tr>" + "".join(th(h) for h in heads)
                         + f"</tr></thead><tbody>{''.join(rows)}</tbody></table>")

    def size(paths) -> str:
        """The files' bytes and when one was last written, as the list says it."""
        st = [p.stat() for p in paths if p.exists()]
        n = sum(s.st_size for s in st)
        if not st:
            return "not written yet"
        return ((f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{max(1, round(n / 1e3))} KB")
                + time.strftime(" · %Y-%m-%d %H:%M", time.localtime(max(s.st_mtime for s in st))))
    # Each log: its name, files, what it holds, and its lines as the page shows them.
    logs = (("Gateway calls", [gw_file], f"The last {len(gw)} requests in <code>runs/gateway/calls.jsonl</code>:"
             " who sat and how each seat went, never the prompt or the answers.",
             table(("When", "Collection", "Shape", "Seats"), gw), len(gw)),
            ("Changes", [changes_file], f"The last {len(changes)} changes in <code>config/changes.jsonl</code>:"
             " a model, a collection or a setting, and who changed it.",
             table(("When", "By", "Model", "What", "New"), changes), len(changes)),
            ("Scheduler", [sched_file], f"The last {len(sched)} lines of <code>runs/scheduler.log</code>:"
             " each job as it starts and ends.", "".join(sched), len(sched)),
            ("Onboarding runs", runs, f"The newest {len(onboard)} of {len(runs)} onboarding logs,"
             " <code>runs/onboard-*.log</code>, by name.", f"<ul>{''.join(onboard)}</ul>" if onboard else "", len(onboard)))
    slug = {name: "log-" + re.sub(r"[^a-z]+", "-", name.lower()) for name, *_ in logs}
    empty = "<div class='log-line muted'>Nothing yet.</div>"
    nav = "".join(f"<li><button type='button' aria-controls='{slug[name]}'"
                  f" aria-pressed='{str(i == 0).lower()}'><span>{name}</span><span class='n'>{n}</span>"
                  f"<small>{size(paths)}</small></button></li>"
                  for i, (name, paths, _, _, n) in enumerate(logs))
    views = "".join(f"<section id='{slug[name]}' data-log='{name}' aria-labelledby='{slug[name]}-h'{' hidden' if i else ''}>"
                    f"<h2 id='{slug[name]}-h'>{name}</h2><p class='sub'>{sub}</p>"
                    f"<div class='log-box' tabindex='0' role='region' aria-labelledby='{slug[name]}-h'>"
                    f"{body or empty}</div></section>"
                    for i, (name, _, sub, body, _) in enumerate(logs))
    # Export: a popup for the format and the logs (bench.js), each as the page shows it,
    # the filter applied. Text, every log chosen in one file to read; CSV, a file per log.
    picks = "".join(f"<label class='check'><input type='checkbox' name='lx-log' value='{esc(t)}' checked> {esc(t)}</label>"
                    for t, *_ in logs)
    export = ("<dialog id='logs-export-dialog' aria-labelledby='lx-title'><h2 id='lx-title'>Export logs</h2>"
              "<fieldset><legend class='field'>Format</legend>"
              "<label class='check'><input type='radio' name='lx-format' value='txt' checked> Text (.txt)</label>"
              "<label class='check'><input type='radio' name='lx-format' value='csv'> CSV (.csv)</label></fieldset>"
              f"<fieldset><legend class='field'>Logs</legend>{picks}</fieldset>"
              "<p class='rules' id='lx-note'></p><div class='errors' id='lx-errors' role='alert'></div>"
              "<div class='actions'><button class='btn' type='button' id='lx-cancel'>Cancel</button>"
              "<button class='btn primary' type='button' id='lx-go'>Export</button></div></dialog>")
    return f"<div class='split logs'><nav aria-label='Logs'><ul>{nav}</ul></nav>{views}</div>{export}"


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def version() -> tuple[str, str, str]:
    """(release, date, commit). The release is the VERSION file, with the
    branch appended off `main` (0.1.0-dev), so a dev checkout and the one in
    daily use are told apart at a glance; date and commit come from git when
    there is one (a Docker image has none)."""
    release = read(ROOT / "VERSION").strip() or "0.0.0"
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    if branch and branch not in ("main", "HEAD"):
        release += "-" + re.sub(r"[^A-Za-z0-9.]+", "-", branch)
    commit, _, date = _git("log", "-1", "--format=%h %cs").partition(" ")
    return release, date, commit


RELEASE, RELEASE_DATE, COMMIT = version()
VERSION = f"{RELEASE} {RELEASE_DATE}".strip()           # the X-Benchwarmers-Version header


_disk: list = [0.0, ""]                 # (when git was asked, its answer)


def on_disk() -> tuple[str, str]:
    """(running, on disk): what this process started from and what a restart
    would start, by commit, or by the VERSION file where there is no git.
    git is asked at most every KEEP_FOR seconds (it was 0.1 to 0.4 s of each
    Settings and Info): a new commit shows within that."""
    if COMMIT:
        if time.monotonic() - _disk[0] > KEEP_FOR or not _disk[1]:
            _disk[:] = [time.monotonic(), _git("log", "-1", "--format=%h") or COMMIT]
        return COMMIT, _disk[1]
    return RELEASE, read(ROOT / "VERSION").strip()


def info_page() -> str:
    """The project and this copy of its code, as groups of facts that share the
    width (bench.css .cols.fit). What this install runs as (its version, data
    folder, port, scheduler, client) is on Settings, and the bench's counts on
    Benchmarks: linked, not said twice."""
    # The release this is, or on a branch the one it builds on (0.5.6-dev -> v0.5.6).
    tag, doc = RELEASE.split("-")[0], lambda name: ext_link(f"{connect.REPO}/blob/main/{name}", name)
    groups = (
        ("Benchwarmers", (
            ("Source", ext_link(connect.REPO, connect.REPO.removeprefix("https://"))),
            ("Release notes", ext_link(f"{connect.REPO}/releases/tag/v{tag}", f"v{tag}")),
            ("Licence", "MIT, " + ext_link(f"{connect.REPO}/blob/main/LICENSE", "LICENSE")),
            ("Credits", ext_link(f"{connect.REPO}/blob/main/NOTICE", "NOTICE")),
            ("Made by", ext_link("https://wendellresearch.ai", "Wendell Research")))),
        ("This copy", (
            ("Code folder", f"<span class='mono'>{esc(str(ROOT))}</span>"),
            ("Python", f"{esc(sys.version.split()[0])} <span class='mono'>{esc(sys.executable)}</span>"),
            ("Each response", "says which build answered, in <code>X-Benchwarmers-Version</code>"),
            ("Running as", "the version, data folder, port, scheduler and model client are on"
             " <a href='/settings' data-go='settings'>Settings</a>"),
            ("The bench", "its models, endpoints and collections are counted on"
             " <a href='/' data-go='suite'>Benchmarks</a>, beside the reports"))),
        ("Read more", tuple((label, doc(name)) for label, name in (
            ("Start here", "README.md"), ("Using it", "USAGE.md"), ("The gateway", "GATEWAY.md"),
            ("Your apps", "CLIENTS.md"), ("The benchmarks", "BENCHMARKS.md"), ("Hosting", "HOSTING.md")))),
        ("Your data", tuple((label, doc(name)) for label, name in (
            ("Privacy", "PRIVACY.md"), ("Security", "SECURITY.md"), ("Terms", "TERMS.md")))))
    # Restart here too, as on Settings, with its own ids: both pages can be in the document at once.
    running, disk = on_disk()
    now = (f"Running {esc(RELEASE)} ({esc(running)}), the code on disk." if running == disk else
           f"<strong>Newer code is on disk</strong> ({esc(disk)}; running {esc(RELEASE)}, {esc(running)}).")
    restart_panel = ("<div class='panel'><h2>Restart</h2>"
                     f"<p>{now}</p><p class='rules'>Starts the dashboard again on the code on disk, whatever runs"
                     " it. It waits for onboarding, a scheduled job and gateway calls in progress, or cuts them off"
                     " if you insist; the page comes back in a few seconds.</p>"
                     "<div class='actions'><button class='btn' type='button' id='restart-info'>Restart Benchwarmers"
                     "</button> <span id='restart-info-out' role='status'></span></div></div>")
    return ("<div class='cols fit'>" + "".join(
        f"<div class='panel'><h2>{t}</h2><dl class='rows'>" + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows)
        + "</dl></div>" for t, rows in groups) + restart_panel + "</div>")


def directory_json() -> str:
    """window.BENCH: every model, its facts and options. On every page, so kept
    between requests (kept); its stamp is the one it was worked out at."""
    return kept("directory", _directory_json)


def _directory_json() -> str:
    directory = model_directory()
    facts = {a: catalog.info(a) for a, d in directory.items() if d.get("kind") != "decision"}
    options = {a: o for a, o in load_json(OPTIONS_PATH).items() if a in facts and isinstance(o, dict)}
    # A decision model's role suggestion from onboarding (decide.suggest_role): shown, never applied.
    suggested = {a: s for a, s in (locked.read_json(decide.DECIDED).get("roles") or {}).items() if isinstance(s, dict)}
    # Each collection's name and size: the palette's (bench.js palDraw).
    collections = {n: len(c.get("members") or []) for n, c in bench_collections.load().items()}
    return json.dumps({"models": directory, "facts": facts, "options": options, "roles": model_roles.load()["roles"],
                       "suggested": suggested, "retired": retired(), "onboard": onboard_issues(), "stamp": data_key()[1],
                       "repo": connect.REPO, "collections": collections},
                      ensure_ascii=False).replace("</", "<\\/")


def editor() -> str:
    return (
        # A model, docked beside the page (bench.js dock): not a popup. Its Save at a fixed foot.
        "<dialog class='dock' id='model-dialog' aria-labelledby='m-title'>"
        "<div class='bar'><p>Model</p></div><div class='dock-body'>"
        "<h2 id='m-title' tabindex='-1'></h2><p class='served' id='m-served'></p>"
        "<div class='note' id='m-gone' hidden></div>"
        "<div class='note' id='m-onboard' hidden></div>"
        "<div id='m-editor'><div class='rows'>"
        "<label for='m-alias'>Alias</label><input id='m-alias' class='mono' spellcheck='false'>"
        "<p class='rules' id='m-alias-why'>The name <code>llm -m</code> takes and every record is"
        " keyed by. Letters, digits, dot, dash, underscore; unused, and never a retired name.</p>"
        "<p class='note' id='m-danger' hidden>Renaming rewrites the llm registry and records"
        " old → new in <code>alias-history.json</code>, so suite outputs, offloads, perf"
        " history and run notes keep following this model.</p>"
        "<label for='m-role'>Role</label><select id='m-role'></select>"
        "<p class='rules' id='m-role-why'></p>"
        "<p class='note' id='m-role-hint' hidden></p>"
        "<label for='m-adhoc'>Ad hoc only</label><span><input type='checkbox' id='m-adhoc'></span>"
        "<p class='rules'>Never chosen by the bench on its own: no explorer seat, no router"
        " pick, no scheduled suite, perf or availability runs. Still called whenever you choose"
        " it: as a collection member (or merger), with <code>with</code>, or with"
        " <code>--model</code>. For metered, capped or trial keys.</p></div>"
        "<fieldset id='m-qtypes' hidden><legend class='field'>Question types</legend>"
        "<label class='check'><input type='checkbox' value='noul'> yes/no"
        " <span class='muted'>(noul: a probability that the answer is yes)</span></label>"
        "<label class='check'><input type='checkbox' value='choice'> choice"
        " <span class='muted'>(one option from a set, with a probability for each)</span></label>"
        "<label class='check'><input type='checkbox' value='score'> score"
        " <span class='muted'>(a level on an ordered scale)</span></label>"
        "<p class='rules'>What callers may ask it. Removing one only stops the bench asking;"
        " adding one sends a test question first and is refused if the model does not answer"
        " it.</p></fieldset>"
        "<div class='profile' id='m-profile'></div>"
        # The probe and the whole suite again, now (onboard.py --rerun), through the onboarding queue.
        "<div class='rerun' id='m-rerun-box'><button class='btn' type='button' id='m-rerun'>Run the probe and"
        " full suite now</button><p class='rules'>Its 4-call probe and every suite task, one model at a time"
        " through the onboarding queue, which the sidebar shows. For a model onboarded quick, or to see where it"
        " stands today.</p><div class='errors' id='m-rerun-errors' role='alert'></div></div>"
        "<details id='m-facts' class='facts-box'><summary>Model facts</summary>"
        "<p class='rules'>From models.dev, FreeLLMAPI, OpenRouter, the endpoint and what the bench"
        " learned from errors and probes; the grey text is the current value and its source. Fill"
        " a box to set your own value (it wins); empty it to hand the field back.</p>"
        "<div class='facts'>"
        "<div><label class='field' for='f-context_window'>Context window (tokens)</label>"
        "<input id='f-context_window' type='number' min='1'><div class='src' id='s-context_window'></div></div>"
        "<div><label class='field' for='f-max_output'>Max output (tokens)</label>"
        "<input id='f-max_output' type='number' min='1'><div class='src' id='s-max_output'></div></div>"
        + "".join(f"<div><label class='field' for='f-{k}'>{label}</label><select id='f-{k}'>"
                  "<option value=''>auto</option><option value='yes'>yes</option>"
                  f"<option value='no'>no</option></select><div class='src' id='s-{k}'></div></div>"
                  for k, label in (("tools", "Tool calling"), ("vision", "Vision / OCR"),
                                   ("structured_output", "Structured output"), ("reasoning", "Reasoning")))
        + "<div><label class='field' for='f-knowledge_cutoff'>Knowledge cutoff</label>"
        "<input id='f-knowledge_cutoff' maxlength='20'><div class='src' id='s-knowledge_cutoff'></div></div>"
        "<div><label class='field'>This model's rate limits</label><div class='limits' id='f-limits'>"
        + "".join(f"<label>{k} <input type='number' min='1' data-k='{k}' placeholder='—'></label>"
                  for k in ("rpm", "rpd", "tpm", "tpd"))
        + "</div><div class='src' id='s-limits'></div></div>"
        "</div><div class='errors' id='f-errors' role='alert'></div>"
        "</details>"
        # How the bench calls it (serve.save_model_options): every caller, gateway, suite and probes.
        "<details id='m-opts' class='facts-box'><summary>Call options</summary>"
        "<label class='field' for='co-max'>Max tokens per answer</label>"
        "<input id='co-max' type='number' min='1' max='1000000'>"
        "<p class='rules'>The most one answer may use (<code>max_tokens</code>), on every call the bench"
        " makes to it. A reasoning model thinks out of the same budget and answers with an empty string"
        " when it runs out, which looks like a dead model: raise it then. Blank leaves it to the"
        " endpoint. <span id='co-ceiling'></span></p>"
        "<div class='errors' id='co-errors' role='alert'></div>"
        "</details>"
        "</div></div><div class='dock-foot'>"
        "<div class='errors' id='m-errors' role='alert'></div><div class='done' id='m-done'></div>"
        "<button class='btn primary' id='m-save'>Save</button>"
        "<button class='btn primary' id='m-restore' hidden>Restore</button>"
        "<button class='btn' id='m-close'>Close</button>"
        "<details class='remove' id='m-remove-box'><summary>Retire this model…</summary>"
        "<p class='rules'>Deregisters the alias, records it in <code>retired.json</code> so the"
        " name is never reused, takes it out of every collection it is a member or merger of,"
        " and clears its problems. Deletes no evidence, and Restore brings it back, into the"
        " collections that still exist. The daily check also retires models on its own when they are"
        " clearly gone.</p>"
        "<label class='field' for='m-reason'>Why <span class='muted'>(optional, recorded)</span></label>"
        "<input id='m-reason' placeholder='e.g. free tier withdrawn 2026-10-15'>"
        "<div class='actions'><button class='btn danger' id='m-remove'>Retire</button></div>"
        "</details></div></dialog>"
        # The page's own confirm (bench.js ask): window.confirm can be blocked.
        "<dialog id='ask-dialog' aria-labelledby='ask-text'>"
        "<p id='ask-text' style='white-space:pre-line'></p>"
        f"<div id='ask-onboard' hidden>{onboard_choice('ask')}</div>"     # Discover's Add only
        "<div class='actions'><button class='btn' id='ask-no' type='button'>Cancel</button>"
        "<button class='btn primary' id='ask-yes' type='button'>OK</button></div></dialog>"
        # onboarding
        "<dialog id='onboard-dialog' aria-labelledby='o-title'>"
        "<h2 id='o-title'>Onboard a model</h2>"
        "<div id='o-choose'>"
        "<p class='served'>Everything your endpoints serve that is not on the bench, retired or"
        " dismissed. Onboarding registers it, probes it (4 calls) and runs the internal"
        " suite: minutes, and it spends calls. Pick one or several: they run one at a time,"
        " with progress at the foot of the sidebar. A model from somewhere else? Add its endpoint first.</p>"
        "<input type='search' id='o-find' placeholder='Search models or connections'"
        " aria-label='Search models'>"
        # How old the list is (serve.LISTED_FOR), and asking again now.
        "<p class='muted' id='o-age' hidden><span></span>"
        " <button class='linkish' type='button' id='o-refresh'>Refresh</button></p>"
        "<div class='picker' id='o-picker' role='group' aria-label='Served models'></div>"
        # serve.ONBOARD_MODES: how much each onboarding spends.
        f"{onboard_choice('o')}"
        "</div>"
        "<div id='o-run' hidden><p class='served' id='o-result'></p><ol class='steps' id='o-steps'></ol>"
        "<details><summary class='muted'>Full log</summary><pre class='log' id='o-log'></pre></details></div>"
        "<div id='o-queue' hidden><p class='served' id='o-queue-sum'></p><ul class='oq-list' id='o-queue-list'></ul></div>"
        "<div class='errors' id='o-errors' role='alert'></div>"
        "<div class='actions'><button class='btn' id='o-close'>Close</button>"
        "<button class='btn' id='o-dismiss' disabled>Dismiss model</button>"
        "<button class='btn primary' id='o-start' disabled>Onboard model</button></div>"
        "</dialog>"
        # Add an endpoint (Manage's button), or a row's Change key: the key form alone.
        "<dialog id='endpoint-dialog' aria-labelledby='e-title'>"
        "<h2 id='e-title'>Add an endpoint</h2>"
        "<p class='served' id='e-blurb'>Paste an API key for any OpenAI-compatible API (one that lists"
        " <code>/models</code>), or pick a provider and sign in on its own page: the key it hands back is"
        " kept where a pasted one is. Only providers that publish a sign-in for other apps are listed.</p>"
        # API key first, then each provider (metrics/connect.py); bench.js fills it.
        "<div class='choices' id='cn-list' role='group' aria-label='How to add it'></div>"
        # A provider: the new endpoint's name, then the sign-in.
        "<div id='cn-form' hidden>"
        "<label class='field' for='cn-name'>Name for this endpoint</label>"
        "<input id='cn-name' spellcheck='false' autocomplete='off'>"
        "<p class='rules'>Its own name and its own key, so one provider can be several endpoints"
        " (a pasted key and a sign-in, or two accounts).</p>"
        "<div class='actions'><button class='btn' data-close>Cancel</button>"
        "<button class='btn primary' id='cn-go'>Sign in</button></div></div>"
        "<div id='e-form'>"
        "<label class='field' for='e-name'>Name</label>"
        "<input id='e-name' spellcheck='false' autocomplete='off' placeholder='e.g. openrouter'>"
        "<label class='field' for='e-base'>Base URL</label>"
        "<input id='e-base' spellcheck='false' autocomplete='off' placeholder='e.g. https://openrouter.ai/api/v1'>"
        # What it is (endpoints.KINDS): a hub runs that hub's own steps (Open WebUI: owui.py,
        # provider tags); the rest are called directly. Asked when adding only.
        "<div id='e-kinds'><label class='field' for='e-kind'>Kind</label><select id='e-kind'>"
        + "".join(f"<option value='{k}'>{k.capitalize()} ({esc(ex)})</option>" for k, ex in endpoints.KINDS.items())
        + "</select><div id='e-hub-row' hidden><label class='field' for='e-hub'>Which hub</label><select id='e-hub'>"
        + "".join(f"<option value='{k}'>{esc(label)}</option>" for k, label in endpoints.HUBS.items())
        + "</select><p class='rules'>Open WebUI's base is its <code>/api</code>, e.g."
        " <code>http://localhost:3000/api</code>, with an admin's API key. LiteLLM's and CLI Proxy"
        " API's are their <code>/v1</code>, e.g. <code>http://localhost:4000/v1</code> and"
        " <code>http://localhost:8317/v1</code>.</p></div>"
        # What it speaks (endpoints.APIS): the built-in client builds the native requests.
        "<label class='field' for='e-api'>API</label><select id='e-api'>"
        + "".join(f"<option value='{k}'>{esc(label)}</option>" for k, label in endpoints.APIS.items() if k != "responses")
        + "</select><p class='rules'>Chat completions unless the provider has none for your key. Native"
        " Anthropic's base is <code>https://api.anthropic.com/v1</code>, Gemini's"
        " <code>https://generativelanguage.googleapis.com/v1beta</code>; their models are called by the"
        " built-in client, and route mode does not pick them.</p></div>"
        "<label class='field' for='e-key'>API key</label>"
        "<input id='e-key' type='password' autocomplete='off' placeholder='leave blank to keep the current one'>"
        "<p class='rules'>Stored where <code>llm keys set</code> stores it, never in the"
        " repository and never shown again. Test asks <code>/models</code>; Save is open once it"
        " answers. Where that list is public, the key is proven by the first real call.</p>"
        # A hub's admin key, where it is not the API key (endpoints.admin_key).
        "<div id='e-admin-row' hidden><label class='field' for='e-admin'>Admin key</label>"
        "<input id='e-admin' type='password' autocomplete='off' placeholder='only where it is not the API key'>"
        "<p class='rules'>What adds a model to the hub: LiteLLM's master key (blank if the API key is it),"
        " CLI Proxy API's management key (always its own). Open WebUI needs none beyond an admin's"
        " API key. Kept like the API key; blank keeps the current one.</p></div>"
        "<div class='actions'><button class='btn' data-close>Cancel</button>"
        "<button class='btn' id='e-test'>Test</button>"
        "<button class='btn primary' id='e-save' disabled title='Test it first'>Save</button></div>"
        "</div>"
        # A sign-in in progress (from Add, or a row's Sign in again): where to finish it.
        "<div id='cn-wait' hidden><p class='served' id='cn-msg'></p></div>"
        "<div class='errors' id='e-errors' role='alert'></div><div class='done' id='e-done'></div>"
        "<div class='actions' id='e-after' hidden><button class='btn' data-close>Close</button></div>"
        "</dialog>"
        # verdict on one gateway seat
        "<dialog id='verdict-dialog' aria-labelledby='v-title'>"
        "<h2 id='v-title'>Verdict</h2>"
        "<p class='served' id='v-meta'></p>"
        "<details><summary>Prompt</summary><pre class='log answer' id='v-prompt'></pre></details>"
        "<label class='field'>Answer</label><pre class='log answer' id='v-answer'></pre>"
        "<p class='rules'>Judge it after checking it against the code: models have agreed,"
        " three at once, on a bug that did not exist.</p>"
        "<div class='pick'><label class='check'><input type='radio' name='v-useful' value='yes'> Useful</label>"
        "<label class='check'><input type='radio' name='v-useful' value='no'> Not useful</label></div>"
        "<label class='field' for='v-note'>Note</label>"
        "<input id='v-note' maxlength='500' autocomplete='off' placeholder='what it found, or why not'>"
        "<label class='field' for='v-real'>Findings it got right, verified</label>"
        "<textarea id='v-real' rows='2'></textarea>"
        "<label class='field' for='v-false'>Claims it made that are not true</label>"
        "<textarea id='v-false' rows='2'></textarea>"
        "<p class='rules'>One sentence per line. suite/mine.py turns these into benchmark tasks.</p>"
        "<div class='errors' id='v-errors' role='alert'></div><div class='done' id='v-done'></div>"
        "<div class='actions'><button class='btn' id='v-close'>Close</button>"
        "<button class='btn primary' id='v-save'>Save verdict</button></div>"
        "</dialog>")


MAX_BODY = 1_000_000   # bytes; the largest form is a collection or an endpoint


class Handler(gateway.Handler):
    """The dashboard, and on the same port the gateway's routes, `/v1/...` and
    `/bench/...` (one port, for container hosts that expose one): those go to
    gateway.Handler, which takes a gateway key. Every other route is the
    dashboard's and takes the login cookie when an admin token is set;
    /health, /login and Connect's callback take neither (metrics/guard.py)."""

    def do_GET(self):                                    # noqa: N802
        path = self.path.split("?", 1)[0]
        if guard.gateway_route(path):
            return super().do_GET()
        if path == "/health":
            # For a container's health check, which may send any Host: answered
            # before the Host check, so it says nothing but that it is up.
            return self._json(200, {"ok": True})
        # Host checked on every GET: a DNS-rebinding page could otherwise read
        # any of these replies (metrics/guard.py).
        try:
            guard.check(self.headers, PORT, post=False, max_body=0)
        except guard.Refused as r:
            self._json(r.status, {"ok": False, "errors": [str(r)]})
            return
        if path == "/login":                           # ?theme= previews a theme, as on every page
            theme = re.search(r"[?&]theme=([\w-]+)", self.path)
            return self._page(login_page(theme and theme.group(1))) if guard.auth_on() else self._redirect("/")
        callback = self.path.startswith((connect.CALLBACK + "?", connect.CALLBACK + "/"))
        if not callback and not self._admitted(path):
            return
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(self.path).query)
        if self.path.startswith("/api/onboard?"):
            self._json(200, onboard_status(q.get("log", [""])[0]))
            return
        if self.path in ("/api/candidates", "/api/candidates?fresh=1"):
            self._json(200, onboard_candidates(fresh=self.path.endswith("fresh=1")))
            return
        if self.path == "/api/onboard-queue":
            self._json(200, onboard_queue())
            return
        if self.path == "/api/needs":                  # after an action there: that page, not the whole one
            with locked.snapshot():
                groups, auto = open_actions()
                # `first`: first run's next step now ("" when set up), so a step done goes at once.
                self._json(200, {"html": actions_page(groups, auto), "count": open_count(groups),
                                 "first": first_run()})
            return
        if path == "/api/pane":                        # a tab's page, when first opened (bench.js load)
            key = q.get("tab", [""])[0]
            if not any(k == key for k, _, _ in TABS):
                self._json(404, {"ok": False, "errors": [f"no tab {key!r}"]})
                return
            with locked.snapshot():
                try:
                    page = pane(key)
                except RuntimeError as exc:            # endpoints.json unreadable: say which and why, as the page does
                    page = f"<p>{esc(exc)}</p>"
            self._json(200, {"ok": True, "html": page})
            return
        if self.path == "/api/directory":              # window.BENCH again, after a retire or restore
            with locked.snapshot():
                self._json(200, json.loads(directory_json()))
            return
        if self.path == "/api/endpoints":
            self._json(200, endpoint_list())
            return
        if self.path == "/api/connect":
            self._json(200, connect_list())
            return
        if self.path == "/api/connect/local":          # Endpoints' "Found on this machine", when it is shown
            self._json(200, connect_local())
            return
        if callback:
            # The provider sends the browser back here. A GET that saves, as
            # OAuth callbacks are: the state is single-use and unguessable, and
            # only a sign-in this dashboard started has one. No login cookie:
            # SameSite=Strict keeps it off the provider's cross-site redirect.
            from urllib.parse import parse_qsl
            query = dict(parse_qsl(urlparse(self.path).query))
            query["state"] = connect.callback_id(self.path, query)
            try:
                out = connect_save(connect.finish(query))
            except connect.ConnectError as exc:
                out = {"ok": False, "errors": [str(exc)], "state": "error"}
            except Exception as exc:                   # noqa: BLE001 -- the tab must still hear something
                out = {"ok": False, "errors": [f"could not save it ({type(exc).__name__}: {exc})"], "state": "error"}
            wrote()
            connect.settle(query.get("state", ""), out)
            body = connect_page(out).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")     # the code is in this URL
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/collections":
            with locked.snapshot():                    # each file read once (it read 1,300)
                self._json(200, collections_view())
            return
        read_only = {"/api/status": status_view, "/api/models": models_view,
                     "/api/gateway-keys": gateway_keys_view}
        if path in read_only:
            try:
                self._json(200, read_only[path]())
            except RuntimeError as exc:                # endpoints: none yet, or a file it cannot read
                self._json(400, {"ok": False, "errors": [str(exc)]})
            return
        if self.path == "/api/connections":
            self._json(200, connections_view())
            return
        if self.path.startswith("/api/discover-model?"):
            res = discover_model(q.get("provider", [""])[0], q.get("model", [""])[0])
            self._json(200 if res["ok"] else 404, res)
            return
        if self.path.startswith("/api/gateway-seat?"):
            res = gateway_seat(q.get("call", [""])[0])
            self._json(200 if res["ok"] else 404, res)
            return
        if self.path == "/api/stamp":
            self._json(200, {"stamp": data_stamp()})
            return
        if path == "/api/export":                      # Settings' Export and import: the zip, as a download
            scope, keys = q.get("scope", ["settings"])[0], q.get("keys", [""])[0] == "1"
            if scope not in MOVE_SCOPES or (self.headers.get("Sec-Fetch-Site") or "").lower() == "cross-site":
                self._json(400, {"ok": False, "errors": ["scope is settings or all, asked from this page"]})
                return
            import shutil
            import tempfile
            with tempfile.TemporaryFile() as tmp:
                export_zip(scope, keys, tmp)
                size = tmp.tell()
                tmp.seek(0)
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", f"attachment; filename=\"benchwarmers-{scope}"
                                 f"{'-with-keys' if keys else ''}-{datetime.date.today()}.zip\"")
                self.send_header("Content-Length", str(size))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                shutil.copyfileobj(tmp, self.wfile)
            return
        if self.path.startswith(("/fonts/", "/ui/")):
            # Only the named font files, ASSETS and CODE; anything else is a
            # 404, so this cannot be walked to other paths.
            name = path.split("/", 2)[2]
            file = font_file(name) if path.startswith("/fonts/") else \
                (UI / name if (name in ASSETS or name in CODE) and (UI / name).exists() else
                 LOGOS[name] if name in LOGOS and LOGOS[name].exists() else None)
            if not file:
                self.send_error(404)
                return
            data = file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ASSETS.get(name) or CODE.get(name)
                             or ("image/svg+xml" if name in LOGOS else "font/woff2"))
            self.send_header("X-Content-Type-Options", "nosniff")
            if name in CODE:
                # Its address names its version (versioned): kept for good, unless
                # the file changed after the page that asked was made.
                data = self._length(data)
                self.send_header("Cache-Control", "max-age=31536000, immutable"
                                 if self.path == versioned(name) else "no-cache")
            else:
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(data)
            return
        theme = q.get("theme", [None])[0]               # a preview; else the saved one
        try:
            page = render(theme, page_for(path))       # the page the address names (href)
        except RuntimeError as exc:                    # endpoints.json unreadable: say which and why
            page = f"<!doctype html><meta charset='utf-8'><title>Benchwarmers</title><p>{esc(exc)}</p>"
        self._page(page)                               # gzipped beyond this machine (_length)

    def do_POST(self):                                   # noqa: N802
        """One endpoint, one operation: change a model's role or alias.

        Same-origin only in practice (127.0.0.1, no CORS headers), JSON in,
        JSON out, and every rule lives in `change_model` — this method only
        parses and reports.
        """
        path = self.path.split("?", 1)[0]
        if guard.gateway_route(path):
            with _answering_lock:
                _answering[0] += 1                     # restart() waits for these
            try:
                return super().do_POST()
            finally:
                with _answering_lock:
                    _answering[0] -= 1
        if path == "/login":
            return self._login()
        if path == "/api/import":                      # Settings' Export and import: the zip as the body
            try:
                length = guard.check(self.headers, PORT, post=True, max_body=IMPORT_MAX, kind="application/zip")
            except guard.Refused as r:
                return self._json(r.status, {"ok": False, "errors": [str(r)]})
            if not self._admitted(path):
                return
            import tempfile
            with tempfile.TemporaryFile() as tmp:
                while length > 0:
                    chunk = self.rfile.read(min(length, 1 << 20))
                    if not chunk:
                        break
                    tmp.write(chunk)
                    length -= len(chunk)
                tmp.seek(0)
                result = import_zip(tmp, apply="apply=1" in self.path) if length == 0 else \
                    {"ok": False, "errors": ["the upload stopped before its end"]}
            wrote()
            return self._json(200 if result["ok"] else 400, result)
        ops = {"/api/model": lambda p: change_model(str(p.get("alias", "")),
                                                    p.get("new_alias"), p.get("new_role"),
                                                    new_ad_hoc=p.get("new_ad_hoc"),
                                                    new_question_types=p.get("new_question_types")),
               "/api/remove": lambda p: remove_model(str(p.get("alias", "")), str(p.get("reason", ""))),
               "/api/restore": lambda p: restore_model(str(p.get("alias", ""))),
               "/api/retry": lambda p: retry_model(str(p.get("alias") or "")),
               "/api/discover/refresh": lambda p: discover_refresh(),
               "/api/discover/add": lambda p: discover_add(str(p.get("connection") or ""),
                                                           str(p.get("model") or ""),
                                                           endpoint=str(p.get("endpoint") or ""),
                                                           mode=str(p.get("mode") or "full"),
                                                           vision=p.get("vision") is True),
               "/api/onboard": lambda p: start_onboard(str(p.get("served", "")),
                                                       str(p.get("endpoint", "")),
                                                       mode=str(p.get("mode") or "full"),
                                                       vision=p.get("vision") is True),
               "/api/onboard-queue": lambda p: queue_onboard(p.get("items")),
               "/api/onboard-queue/stop": lambda p: stop_onboard_queue(),
               "/api/onboard-queue/clear": lambda p: clear_onboard_queue(),
               "/api/dismiss": lambda p: dismiss(p.get("served"), bool(p.get("undo"))),
               "/api/endpoint": lambda p: save_endpoint(str(p.get("name", "")),
                                                        str(p.get("base", "")),
                                                        str(p.get("key", "")), new=bool(p.get("new")),
                                                        kind=str(p.get("kind") or ""), hub=str(p.get("hub") or ""),
                                                        admin=str(p.get("admin") or ""),
                                                        test_only=bool(p.get("test")), api=str(p.get("api") or "")),
               "/api/endpoint/remove": lambda p: remove_endpoint(str(p.get("name", "")),
                                                                 with_models=bool(p.get("with_models"))),
               "/api/connect/start": lambda p: connect_start(str(p.get("provider") or ""),
                                                             str(p.get("endpoint") or ""), bool(p.get("code"))),
               "/api/endpoint/default": lambda p: set_default(str(p.get("name") or "")),
               "/api/connect/poll": lambda p: connect_poll(str(p.get("id") or "")),
               "/api/connect/app": lambda p: connect_app(str(p.get("provider") or ""),
                                                         str(p.get("client_id") or "")),
               # `or ""`, not a default: JSON null must not become the text "None".
               "/api/collection": lambda p: save_collection(str(p.get("name") or ""), p.get("members"),
                                                            str(p.get("shape") or ""),
                                                            str(p.get("description") or ""),
                                                            p.get("tags"), str(p.get("reply") or ""),
                                                            str(p.get("merger") or ""), p.get("ad_hoc") is True,
                                                            max_calls=p.get("max_calls"),
                                                            max_explorers=p.get("max_explorers")),
               "/api/collection/remove": lambda p: delete_collection(str(p.get("name") or "")),
               "/api/model-facts": lambda p: save_model_facts(str(p.get("alias") or ""), p.get("facts")),
               "/api/model-options": lambda p: save_model_options(str(p.get("alias") or ""), p.get("max_tokens")),
               "/api/scheduler": lambda p: save_scheduler(str(p.get("mode") or ""), p.get("jobs")),
               "/api/client": lambda p: save_client(str(p.get("client") or "")),
               "/api/own-client-free": lambda p: save_own_client_free(p.get("providers")),
               "/api/connection-limits": lambda p: save_connection_limits(str(p.get("connection") or ""),
                                                                          p.get("limits")),
               "/api/connection-learned/forget": lambda p: forget_connection_learned(
                   str(p.get("connection") or ""), str(p.get("axis") or "")),
               "/api/verdict": lambda p: record_verdict(str(p.get("call") or ""), p.get("useful"),
                                                        str(p.get("note") or ""), p.get("real"),
                                                        p.get("false")),
               "/api/gateway-settings": lambda p: save_gateway_settings(
                   p.get("max_calls"), p.get("max_explorers"), pick_collection=p.get("pick_collection", KEEP),
                   pick_confidence=p.get("pick_confidence", KEEP)),
               "/api/gateway-key": lambda p: make_gateway_key(str(p.get("name") or ""), p.get("collections"),
                                                              p.get("rpm")),
               "/api/gateway-key/remove": lambda p: revoke_gateway_key(str(p.get("name") or "")),
               "/api/theme": lambda p: save_theme(str(p.get("theme") or "")),
               "/api/quota/refresh": lambda p: quota_op("refresh", str(p.get("tag") or "")),
               "/api/quota/about": lambda p: quota_op("about", str(p.get("tag") or "")),
               "/api/alerts/dismiss": lambda p: dismiss_alert(str(p.get("key") or "")),
               "/api/quota/order": lambda p: quota_op("order", p.get("order")),
               "/api/restart": lambda p: restart(p.get("force") is True)}
        # SAME ORIGIN ONLY. Any page open in the browser can POST a plain form
        # to 127.0.0.1, and this used to parse whatever arrived as JSON -- so a
        # website could retire models, and now could set a key. The checks are
        # shared with the gateway (metrics/guard.py). Then the login, then the
        # route: a stranger learns nothing, not even which routes exist.
        try:
            length = guard.check(self.headers, PORT, post=True, max_body=MAX_BODY)
        except guard.Refused as r:
            self._json(r.status, {"ok": False, "errors": [str(r)]})
            return
        if not self._admitted(path):
            return
        if self.path not in ops:
            self._json(404, {"ok": False, "errors": ["unknown endpoint"]})
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            result = ops[self.path](payload)
        except (ValueError, TypeError) as exc:
            result = {"ok": False, "errors": [f"bad request: {exc}"],
                      "changed": [], "sync": ""}
        except RuntimeError as exc:                    # endpoints: none yet, or a file it cannot read
            result = {"ok": False, "errors": [str(exc)], "changed": [], "sync": ""}
        finally:
            wrote()                                    # before the answer: the next page is never from before it
        self._json(200 if result["ok"] else 400, result)

    def _admitted(self, path: str) -> bool:
        """The login cookie, when an admin token is set: a page asked for
        without it goes to the login page, anything else gets a 401."""
        try:
            self.who = guard.admin_caller(self.headers)
            return True
        except guard.Refused as r:
            if self.command == "GET" and not path.startswith("/api/"):
                self._redirect("/login")
            else:
                self._json(r.status, {"ok": False, "errors": [str(r)]})
            return False

    def _login(self) -> None:
        """POST /login {"token"}: the login cookie, for the admin token only.
        The sign-in page's own form, posted with scripts off, may send it as a
        form (token=...), through the same guard: then the answer is a page,
        the dashboard or the sign-in again saying the token was wrong. A form
        is a type another site's page can post, so it must also name this
        dashboard as its Origin: a browser that sends none gets no answer."""
        from urllib.parse import parse_qs
        form = (self.headers.get("Content-Type") or "").lower().startswith("application/x-www-form-urlencoded")
        try:
            length = guard.check(self.headers, PORT, post=True, max_body=MAX_BODY,
                                 kind="application/x-www-form-urlencoded" if form else "application/json")
            if form and (self.headers.get("Origin") or "").strip().lower() not in guard.origins(PORT):
                raise guard.Refused(403, "the sign-in form is posted from its own page")
            body = self.rfile.read(length) or b""
            given = (parse_qs(body.decode("utf-8", "replace")).get("token", [""])[0] if form
                     else json.loads(body or b"{}").get("token"))
        except guard.Refused as r:
            return self._json(r.status, {"ok": False, "errors": [str(r)]})
        except (ValueError, AttributeError):
            return self._json(400, {"ok": False, "errors": ['send {"token": "..."}']})
        if not (isinstance(given, str) and guard.login(given)):
            if form:
                return self._page(login_page(wrong=True), status=401)
            return self._json(401, {"ok": False, "errors": ["that is not the admin token"]})
        self.who = "admin"
        if form:
            return self._redirect("/", cookie=guard.session_cookie())
        self._json(200, {"ok": True, "errors": []}, cookie=guard.session_cookie())

    def _redirect(self, where: str, cookie: str = "") -> None:
        self.send_response(303)
        self.send_header("Location", where)
        self.send_header("Content-Length", "0")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def _length(self, body: bytes) -> bytes:
        """Content-Length, gzipped first when the caller takes gzip and is not
        on this machine: the page is 2.1 MB, 185 KB gzipped, which a network
        feels and loopback does not (there it only costs CPU). Returns the
        bytes to write."""
        import gzip
        import ipaddress
        # gzip as a token, not refused with q=0 ("gzip;q=0" is a no).
        takes = any(name.strip().lower() == "gzip" and not re.fullmatch(r"\s*q\s*=\s*0(\.0*)?\s*", q)
                    for name, _, q in (p.partition(";") for p in (self.headers.get("Accept-Encoding") or "").split(",")))
        if (len(body) > 1024 and takes
                and not ipaddress.ip_address(self.client_address[0].removeprefix("::ffff:")).is_loopback):
            body = gzip.compress(body, 6)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(body)))
        return body

    def _page(self, html_text: str, status: int = 200) -> None:
        body = html_text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        body = self._length(body)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: dict, cookie: str = "", headers: dict | None = None) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        body = self._length(body)
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        for k, v in (headers or {}).items():          # the gateway's WWW-Authenticate, Retry-After
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        # Every reply says which build answered it (RELEASE-PLAN "The dashboard").
        self.send_header("X-Benchwarmers-Version", VERSION)
        super().end_headers()

    def log_request(self, code="-", size="-"):
        """The access log (route, status, who) once logins are on; without
        them the dashboard stays as quiet as it always was."""
        if guard.auth_on():
            guard.access(self, code)

    def log_message(self, *_args):
        """Quiet. A dashboard does not need to narrate every refresh."""


if __name__ == "__main__":
    if os.environ.get("BENCH_SERVED") != "1":
        # The dashboard runs as this process's child, so Restart (restart())
        # can end it and have it started again on the code now on disk,
        # whatever started serve.py. The child ends with this one (_follow_parent).
        env = {**os.environ, "BENCH_SERVED": "1", "BENCH_PARENT": str(os.getpid())}
        try:
            while (code := subprocess.call([sys.executable, os.path.abspath(__file__), *sys.argv[1:]],
                                           env=env, stdin=subprocess.DEVNULL)) == RESTART:
                print("Benchwarmers: restarting on the code now on disk", flush=True)
        except KeyboardInterrupt:
            code = 0
        sys.exit(code)
    threading.Thread(target=_follow_parent, daemon=True).start()
    address = guard.bind()
    guard.startup(address)                             # beyond this machine, only with an admin token
    shown = "localhost" if address == "127.0.0.1" else address
    print(f"Benchwarmers on http://{shown}:{PORT}{'  (login on)' if guard.auth_on() else ''}  (ctrl-c to stop)")
    import paths
    paths.make()                                       # a fresh data folder's layout, before any writer
    import scheduler
    scheduler.start()                                  # off unless config/scheduler.json says otherwise
    threading.Thread(target=_warm, daemon=True).start()
    SERVER = ThreadingHTTPServer((address, PORT), Handler)
    SERVER.serve_forever()                             # returns only when restart() shuts it down
    sys.exit(RESTART)
