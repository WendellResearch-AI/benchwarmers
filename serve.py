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
`config/alias-history.json`, the `llm` registry re-synced. A model's REMOVAL
(same popup, confirmed, reason optional): deregistered, taken
out of every collection, and recorded in `config/retired.json`; no evidence
is deleted. And ONBOARDING
(header button): `scripts/onboard.py` started in the background with its log
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
import html
import json
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
THEME_LABELS = {"rainforest": "Rainforest Light", "dark": "Rainforest Dark", "hardwood": "Hardwood"}
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


def brand_css() -> str:
    """Every theme's tokens, and the @font-face rules. The default theme
    supplies every token on `:root` (faces, measure, radius); each other theme
    lays its own over them under `html[data-theme=<name>]`, so the page holds
    them all and switching is one attribute (bench.js), with no reload.
    scripts/check_themes.py checks each one's contrast."""
    def block(name: str) -> str:
        return "".join(f"{k}:{v.strip()};" for k, v in _tokens(name).items())
    scoped = "".join(f"html[data-theme='{name}']{{{block(name)}}}" for name in themes() if name != BASE_THEME)
    faces = "".join(
        f"@font-face{{font-family:'{'Chivo Mono' if fam == 'chivo-mono' else 'Chivo'}';"
        f"font-weight:{name.split('-')[-2]};font-style:normal;font-display:swap;"
        f"src:url('/fonts/{name}') format('woff2');}}"
        for name, fam in FONTS.items())
    return f":root{{{block(BASE_THEME)}}}{scoped}{faces}"


def font_file(name: str) -> pathlib.Path | None:
    path = (UI / "fonts" / name) if name in FONTS else None
    return path if path and path.exists() else None


# Images a theme may draw (Hardwood's centre court): named here, served from
# ui/ with a fixed type, so the route cannot be walked to other files.
ASSETS = {"mascot.png": "image/png"}


def read(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def load_json(path: pathlib.Path) -> dict:
    return json.loads(read(path) or "{}")


def data_stamp() -> float:
    """Newest change to anything the page renders from. The page polls this
    and offers a reload when it moves, rather than reloading under you."""
    paths = [*DATA.glob("config/*.json*"), *DATA.glob("config/*.yaml"),
             *DATA.glob("metrics/*.json*"), DATA / "perf" / "history.csv",
             *DATA.glob("runs/*.md")]
    paths = [p for p in paths if p.name != UI_CONFIG.name]     # a theme switch is already on the page
    return max((p.stat().st_mtime for p in paths if p.exists()), default=0.0)


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
    """
    from run import images_only
    row = re.compile(r"^\|\s*([\w-]+)\s*\|\s*`([^`]+)`\s*\|\s*([^|]+?)\s*\|"
                     r"\s*([^|]*?)\s*\|\s*([\d.]+)\s*\|")
    scores: dict[tuple[str, str], dict] = {}
    skip = images_only()
    for path in sorted(DATA.glob("runs/suite-*.md")):
        for line in read(path).splitlines():
            if (m := row.match(line.strip())):
                task, model, found, invented, seconds = m.groups()
                model = current_alias(model)
                if model in skip or task.startswith("vision-"):
                    continue
                scores[(task, model)] = {
                    "found": found, "invented": invented.strip(" —-"),
                    "seconds": float(seconds)}
    return scores


def perf_rows() -> list[dict]:
    path = DATA / "perf" / "history.csv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    for row in rows:
        row["model"] = current_alias(row.get("model", ""))
    return rows


def registered() -> dict[str, str]:
    """model_name -> llm id."""
    out, current = {}, None
    for line in read(DATA / "config" / "extra-openai-models.yaml").splitlines():
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


def retired() -> dict[str, dict]:
    """Aliases removed from the bench by hand, with when and why."""
    return load_json(DATA / "config" / "retired.json")


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
        d = decide.load().get(model)
        sub = " · ".join(filter(None, [
            f"{d['served']} · via {d['via']}" if d else tag_for(model),
            "ad hoc" if model in set(load_json(ROLES_PATH).get("ad_hoc", [])) else ""]))
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
        try:
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "sync_config.py")],
                cwd=str(ROOT), capture_output=True, text=True, timeout=120,
                encoding="utf-8", errors="replace")
            sync_report = ("synced to the llm CLI" if result.returncode == 0
                           else f"sync_config.py exited {result.returncode}:"
                                f" {(result.stderr or result.stdout).strip()[:300]}"
                                " — run it by hand")
        except (OSError, subprocess.SubprocessError) as exc:
            sync_report = f"could not run scripts/sync_config.py ({exc}); run it by hand"

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
    data, cap = bench_collections.load(), bench_collections.settings()["max_calls"]
    back = []
    for name, fields in was.items():
        c = data.get(name)
        if not isinstance(c, dict):
            continue
        old = json.loads(json.dumps(c))
        members = c.get("members") or []
        if "members" in fields and alias not in members and len(members) < cap:
            c["members"] = members + [alias]
        if "merger" in fields and not c.get("merger"):
            c["merger"] = alias
        if c != old:
            back.append(name)
            _log_change(stamp, who, alias, f"collection {name}", old, c)
    if back:
        _write_atomic(bench_collections.FILE, _dump(data))
    return back


def _sync_cli() -> str:
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
    also the button for a person who knows sooner. It deregisters the alias from the llm registry (and re-syncs),
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


def retry_model(alias: str, who: str = "ui") -> dict:
    """One fresh call to a model on a retiring alert. An answer is written to
    perf/history.csv as evidence (scripts/auto_retire.py reads it, so the
    clock resets) and the alert is cleared now rather than at the next 5am
    check. A failure writes nothing: the daily probe keeps the record."""
    names = registered()
    if alias not in names.values():
        return {"ok": False, "errors": [f"{alias!r} is not registered"]}
    from run import ask_once, failure_class, llm_binary
    body, seconds = ask_once(llm_binary(), alias, "Reply with exactly one word: ready", 90)
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
    served = {a: s for s, a in names.items()}.get(alias, "")
    # Exactly this model's: auto_retire.py raises model:pending:<served> with its alias.
    cleared = [k for k, a in _alerts.active().items()
               if a.get("kind") == "retiring" and (a.get("alias") == alias or k == f"model:pending:{served}")]
    for key in cleared:
        _alerts.clear(key)
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
LISTED_FOR = 300
_listed: dict[str, tuple] = {}


def onboard_candidates(fresh: bool = False) -> dict:
    """What every endpoint serves that the bench could onboard, for the picker.

    Served, minus registered, minus retired, minus dismissed (those come back
    separately so a dismissal can be undone), each endpoint asked separately
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
    sets them from the file), so a change here is made there too; None clears
    it. The built-in client reads the file itself."""
    import shutil
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


def connections_view() -> dict:
    """Each connection the chat models use, its models, and its limits."""
    by = {}
    for a in _chat_aliases():
        by.setdefault(catalog.connection_of(a), []).append(a)
    lim = catalog.connection_limits()
    return {"ok": True, "errors": [], "keys": list(catalog.CONNECTION_LIMIT_KEYS),
            "labels": {k: catalog.LIMIT_LABELS[k] for k in catalog.CONNECTION_LIMIT_KEYS},
            "connections": [{"name": c, "models": sorted(m), "limits": lim.get(c, {})}
                            for c, m in sorted(by.items())]}


def _chat_aliases() -> list[str]:
    return sorted(a for a, d in model_directory().items() if d.get("kind") != "decision")


def collections_view() -> dict:
    """Collections, gateway settings, and what a collection may hold."""
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
    roles = model_roles.load()
    facts = {}
    for a in _chat_aliases():
        f = catalog.info(a)
        facts[a] = {"caps": {c: f.get(c) for c in catalog.CAPS}, "context": f.get("context_window"),
                    "price": price_of(a, served[a], f), "role": (roles.get("models") or {}).get(a, "general")}
    return {"ok": True, "errors": [], "collections": rows, "settings": s,
            "shapes": shapes, "models": _chat_aliases(), "served": served, "tag_suggestions": suggestions,
            "facts": facts, "shape_roles": {k: v for k, v in roles["shapes"].items() if k in shapes},
            "long_context": LONG_CONTEXT}


TAG_PURPOSES = ("second-opinion", "security", "long-context", "fast", "free", "tools", "json")
LONG_CONTEXT = 128_000          # the long-context tag: members known to hold at least this many tokens


def save_collection(name: str, members, shape: str, description: str, tags=None,
                    reply: str = "all", merger: str = "", who: str = "ui") -> dict:
    """Create or replace a collection. Every member must be a chat model on the
    bench (a decision model cannot answer a prompt), no more than max_calls.
    The description and tags are what an orchestrator reads on /v1/models to
    choose a collection, so they say what it is for."""
    # The name is what follows "bench/"; one typed with the prefix is taken as meant.
    name, shape = (name or "").strip().removeprefix(bench_collections.PREFIX), (shape or "").strip()
    description = " ".join((description or "").split())
    if isinstance(tags, str):
        tags = tags.split(",")
    tags = list(dict.fromkeys(t.strip().lower() for t in (tags or []) if isinstance(t, str) and t.strip()))
    s = bench_collections.settings()
    errors = []
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
        errors.append(f"at most {s['max_calls']} members (max calls per request, in settings)")
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
                  "reply": reply, "merger": merger}
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


def save_gateway_settings(max_calls, max_explorers, who: str = "ui") -> dict:
    errors = []
    if not isinstance(max_calls, int) or isinstance(max_calls, bool) or not 1 <= max_calls <= 10:
        errors.append("max calls must be a whole number from 1 to 10")
    if not isinstance(max_explorers, int) or isinstance(max_explorers, bool) or not 0 <= max_explorers <= 2:
        errors.append("max explorers must be 0, 1 or 2 (route.py seats at most two)")
    too_big = [n for n, c in bench_collections.load().items()
               if isinstance(max_calls, int) and len(c.get("members", [])) > max_calls]
    if too_big:
        errors.append(f"these collections have more members than {max_calls}: {', '.join(too_big)}")
    if bench_collections.unreadable(bench_collections.SETTINGS_FILE):
        errors.append("config/gateway.json is not valid JSON; fix it by hand first")
    if errors:
        return {"ok": False, "errors": errors}
    raw = bench_collections._read(bench_collections.SETTINGS_FILE)
    old = bench_collections.settings()
    raw.pop("default_collection", None)
    raw.update({"max_calls": max_calls, "max_explorers": max_explorers})
    _write_atomic(bench_collections.SETTINGS_FILE, _dump(raw))
    _log_change(datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                who, "", "gateway settings", old, bench_collections.settings())
    return {"ok": True, "errors": [], "changed": ["config/gateway.json"]}


# How much an onboarding spends. Full: the 4-call probe, then the suite now.
# Quick: the probe only; the scheduled `suite --next` passes fill the suite in,
# six tasks a run, so the same calls are spread out, not saved. Quick + ad
# hoc: the probe only, and the bench never calls it on its own: the only one
# that saves calls, for a metered, capped or trial key. Apart from the mode,
# `vision` (the picker's Vision model box) gives it role vision as it is
# registered: a seat only for requests that carry images, and its suite is the
# vision tasks alone (suite/run.applies), now or through the scheduled passes.
ONBOARD_MODES = {"full": [], "quick": ["--skip-suite"], "quick-adhoc": ["--skip-suite", "--ad-hoc"]}


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
# nothing; the header shows progress from GET /api/onboard-queue. Only
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
    before the first), so the header can fill by fifths of a model."""
    with _OQ_LOCK:
        running = _OQ["worker"] is not None
        out = {"ok": True, "errors": [], "running": running, "stopping": running and _OQ["stop"],
               "items": [dict(i) for i in _OQ["items"]]}
    for i in out["items"]:
        if i["state"] == "running":
            text = read(DATA / "runs" / i["log"]) if i["log"] else ""
            i["step"] = max((int(s) for s in re.findall(r"\[([1-5])/5\]", text)), default=0)
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
                alias = re.search(r"->\s+alias\s+(\S+)", text)
                with _OQ_LOCK:
                    nxt["alias"] = alias.group(1) if alias else None
                state, note = _onboard_outcome(code, text)
        except Exception as exc:                         # noqa: BLE001 -- one model must not end the batch
            state, note, code = "failed", f"{type(exc).__name__}: {exc}", None
        with _OQ_LOCK:
            nxt.update(state=state, note=note, exit=code)


def discover_add(connection: str, model: str, who: str = "ui", endpoint: str = "") -> dict:
    """From the Discover tab: put a model into a hub as it is otherwise done by
    hand, through the adapter of the hub `endpoint` is (endpoints.adapter:
    Open WebUI's connection Model IDs and a public, tagged record with a logo;
    a LiteLLM deployment; a CLI Proxy API provider's models list), then the
    normal onboarding. Only for a connection the hub already has. No endpoint
    means Open WebUI's, as before there were other hubs."""
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
    started = queue_onboard([{"served": served, "endpoint": hub.endpoint()}], who)
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
    return f"<th data-sortable{' class=num' if num else ''}>{label}</th>"


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
    editor never describe the same model two ways."""
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


def first_run() -> str:
    """A fresh install's next step, until it has an endpoint, a model on the
    bench and a collection: add an endpoint (sign in with a provider, or paste
    a key), onboard a model from it, make a collection of them (RELEASE-PLAN
    Phase 4: init's steps, in the dashboard). Each opens what every install
    uses; bench.js opens the first on load, since an empty bench offers nothing else."""
    if not endpoints.load():
        step, head, label = "endpoint", "Add your first endpoint", "Add an endpoint"
        text = ("Where models are served from: sign in with a provider, or paste an API key for any"
                " OpenAI-compatible API. Then onboard a model from it.")
    elif not registered() and not decide.load():
        step, head, label = "onboard", "Onboard your first model", "Onboard a model"
        text = ("Pick a model your endpoints serve. Onboarding registers it, probes it (4 calls) and runs"
                " the internal suite, so it takes minutes and spends calls.")
    elif not bench_collections.load():
        step, head, label = "collection", "Make your first collection", "New collection"
        text = ("The models a panel always asks, and what it is for. Callers then ask for"
                f" bench/&lt;name&gt; through the gateway at {esc(gateway_url())}.")
    else:
        return ""
    return (f"<section class='problems' id='first-run' data-step='{step}' aria-labelledby='first-head'>"
            f"<div class='head'><h2 id='first-head'>{head}</h2></div>"
            f"<div class='problem'><div class='why'>{text}</div><div class='acts'>"
            f"<button class='btn primary' id='first-go'>{label}</button></div></div></section>")


def problems() -> str:
    """What needs a person, each with the button that resolves it.

    Rate limits and timeouts are not listed: they resolve themselves, or count
    toward the 7-day backstop and appear here as `retiring` when that nears.
    Automatic retirements from the last week ARE listed, with Restore, because
    a decision taken without a person should be easy for one to undo."""
    alerts = load_json(DATA / "metrics" / "alerts.json")
    names = registered()
    today = datetime.date.today()
    KIND = {"retiring": "err", "retired": "err", "connection": "warn",
            "registration": "warn", "plan": "warn"}
    items = []
    for key, a in sorted(alerts.items(), key=lambda kv: kv[1].get("first_seen", "")):
        served = key.split(":", 2)[2] if key.count(":") >= 2 else ""
        alias = a.get("alias") or names.get(served, "")
        try:
            age = (today - datetime.date.fromisoformat(a["first_seen"])).days
        except (KeyError, ValueError):
            age = 0
        why = " · ".join(filter(None, [a.get("action", ""),
                                       "open today" if age == 0 else f"open {age} days"]))
        acts = []
        if a.get("aliases"):
            acts.append(f"<button class='btn small danger' data-act='retire'"
                        f" data-aliases='{esc(' '.join(a['aliases']))}'"
                        f" data-why='{esc(a.get('message', ''))}'>Retire all {len(a['aliases'])}</button>")
        elif alias in names.values():
            if a.get("kind") == "retiring":
                acts.append(f"<button class='btn small' data-act='retry' data-alias='{esc(alias)}'"
                            " title='One call now; an answer clears this alert'>Retry</button>")
            acts.append(f"<button class='btn small' data-act='open' data-alias='{esc(alias)}'>Open</button>")
            acts.append(f"<button class='btn small danger' data-act='retire' data-alias='{esc(alias)}'"
                        f" data-why='{esc(a.get('message', ''))}'>Retire now</button>")
        items.append((key, a.get("kind", "?"), KIND.get(a.get("kind"), "warn"),
                      a.get("message", ""), why, "".join(acts)))
    cutoff = (today - datetime.timedelta(days=7)).isoformat()
    auto = [(f"retired:{alias}:{r['at']}", "", "", f"{alias} · {r['at'][:10]}",
             r.get("why", "").removeprefix("auto: "),
             f"<button class='btn small' data-act='restore' data-alias='{esc(alias)}'>Restore</button>")
            for alias, r in sorted(retired().items(), key=lambda kv: kv[1].get("at", ""), reverse=True)
            if r.get("by") == "auto" and r.get("at", "") >= cutoff]
    if not items and not auto:
        return ""

    # Each row carries its alert key: Hide (bench.js) folds the section to its
    # heading until a key it has not seen arrives, or 12 hours pass. Per browser.
    def render_rows(rows):
        return "".join(
            f"<div class='problem' data-key='{esc(key)}'><div><div class='what'>"
            f"{chip(kind, css) if kind else ''}{esc(what)}</div>"
            f"<div class='why'>{esc(why)}</div></div><div class='acts'>{acts}</div></div>"
            for key, kind, css, what, why, acts in rows)
    # Folded: a week of automatic retirements is a record to check, not a
    # list of things to do, and it must not push the roster off the screen.
    folded = (f"<details><summary>Retired automatically this week · {len(auto)}"
              " <span class='muted'>— each can be restored</span></summary>"
              f"{render_rows(auto)}</details>") if auto else ""
    return (f"<section class='problems' id='actions' aria-labelledby='actions-head'>"
            f"<div class='head'><h2 id='actions-head'>Action needed · {len(items)}</h2>"
            "<span class='sep' aria-hidden='true'>·</span>"
            "<button type='button' class='linkish' id='actions-hide' aria-expanded='true'"
            " title='Fold this away until something new needs you, or for 12 hours'>Hide</button></div>"
            + ("<p>States that will not fix themselves.</p>" if items
               else "<p>Nothing open.</p>")
            + f"{render_rows(items)}{folded}</section>")


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


def facet(name: str, label: str, values) -> str:
    """A filter by one column's value, beside a panel's search box (bench.js
    matches it against each row's data-<name>)."""
    opts = "".join(f"<option value='{esc(v)}'>{esc(v)}</option>" for v in sorted(set(values) - {""}))
    return (f"<label>{esc(label)} <select data-facet='{esc(name)}'><option value=''>any</option>"
            f"{opts}</select></label>")


def facts_cells(alias: str, f: dict | None = None) -> str:
    """Context window and capabilities, from metrics/catalog.py; a value you
    entered is marked, and an unknown one says so rather than looking empty."""
    f = f or catalog.info(alias)
    ctx = f.get("context_window")
    src = f["sources"].get("context_window", "")
    ctx_cell = (f"<td class='num' data-sort='{ctx}' title='{esc(src)}'>"
                f"{ctx // 1000:,}k{'*' if src == 'you' else ''}</td>" if ctx
                else "<td class='num muted' data-sort='-1' title='unknown: fill it in the model popup'>?</td>")
    caps = [CAP_LABELS[c] for c in catalog.CAPS if f.get(c)]
    return ctx_cell + (f"<td class='caps'>{' '.join(chip(c, '') for c in caps)}</td>" if caps
                       else "<td class='muted'>—</td>")


def roster() -> str:
    """Every model on one table: its state, what it has done, what it scored.

    Replaces a grid of offload cards that answered only "what was it used
    for". Status first, because the first question on opening this page is
    whether anything is broken; real work second; benchmarks after."""
    try:
        from offload import summary
        work = summary()
    except Exception:                              # noqa: BLE001
        work = {}
    try:
        import auto_retire
        rows, answers, _ = auto_retire.evidence()
    except Exception:                              # noqa: BLE001
        rows, answers = [], {}
    last = dict(answers)
    for r in rows:
        if r["answered"]:
            last[r["alias"]] = max(last.get(r["alias"], r["date"]), r["date"])
    suite: dict[str, list[int]] = {}
    for (_task, model), hit in suite_scores().items():
        if (m := re.match(r"(\d+)/(\d+)", hit["found"])):
            got = suite.setdefault(model, [0, 0, 0])
            got[0] += int(m.group(1)); got[1] += int(m.group(2)); got[2] += 1
    tiny = {r["model"]: float(r["p50_s"]) for r in perf_rows()
            if r["prompt"] == "tiny" and int(r.get("answered") or 0) > 0}
    status = model_status()
    new = recently_onboarded()
    stuck = onboard_issues(status)
    directory = model_directory()
    severity = {"err": 0, "warn": 1, "": 2, "ok": 3}

    body = ["<table><thead><tr>", th("Model"), th("Status"), th("Role"),
            th("Context", True), th("Can"),
            th("Offloads", True), th("Helped"), th("Suite", True),
            th("Reply time", True), th("Last answer"), "</tr></thead><tbody>"]
    # The model browser: each row says its connection, status, role and price
    # for the toolbar's filters, and the filters offer what the rows hold.
    offered: dict[str, list[str]] = {"connection": [], "status": [], "role": [], "price": []}

    def browse(**values) -> str:
        for k, v in values.items():
            offered[k].append(v)
        return "".join(f" data-{k}='{esc(v)}'" for k, v in values.items())
    for alias in sorted(status, key=lambda a: (-work.get(a, {}).get("offloads", 0), a)):
        label, css, why = status[alias]
        f = catalog.info(alias)
        served = directory.get(alias, {}).get("served") or retired().get(alias, {}).get("served", "")
        w = work.get(alias, {})
        judged, useful = w.get("judged", 0), w.get("useful", 0)
        if judged:
            rate = useful / judged
            helped = (f"<span class='helped'><span class='meter'><span"
                      f"{' class=low' if rate < .5 else ''} style='width:{max(4, round(rate * 100))}%'>"
                      f"</span></span><span class='mono'>{useful}/{judged}</span></span>")
        else:
            rate, helped = -1, "<span class='muted'>not judged</span>"
        s = suite.get(alias)
        suite_cell = (f"<td class='num' data-sort='{s[0] / s[1]:.3f}'"
                      f" title='{s[2]} task{'s' * (s[2] != 1)}'>{s[0]}/{s[1]}</td>"
                      if s and s[1] else "<td class='num muted' data-sort='-1'>—</td>")
        p50 = tiny.get(alias)
        seen = last.get(alias)
        role = directory.get(alias, {}).get("role") or retired().get(alias, {}).get("role", "")
        conn = catalog.connection_of(alias)
        if conn == "?":                    # retired: no registration left to say, but its record does
            conn = retired().get(alias, {}).get("tag", "").split("/")[0] or "unknown"
        attrs = browse(connection=conn, status=re.sub(r" in \d+d$", "", label),
                       role=role, price=price_of(alias, served, f))
        body.append(
            f"{tr(alias, attrs)}<td>{named(alias)}</td>"
            f"<td data-sort='{severity[css]}'>{chip(label, css, why)}"
            + (" " + chip("new", "new", "onboarded this week") if alias in new else "")
            + (" " + chip("onboard stopped", stuck[alias]["css"],
                          f"onboarding stopped at the {stuck[alias].get('step', 'probe')}:"
                          f" {stuck[alias]['label']}. Open the model for the error.")
               if alias in stuck else "")
            + f"</td><td>{esc(role)}</td>{facts_cells(alias, f)}"
            f"<td class='num'>{w.get('offloads', 0)}</td>"
            f"<td data-sort='{rate:.3f}'>{helped}</td>{suite_cell}"
            + (f"<td class='num' data-sort='{p50}'>{p50:.1f}s</td>" if p50 is not None
               else "<td class='num muted' data-sort='99999'>—</td>")
            + (f"<td class='mono' data-sort='{seen}'>{seen}</td>" if seen
               else "<td class='muted' data-sort='0'>never</td>")
            + "</tr>")
    # Decision models are not in the llm yaml, so no probe, suite or reply time
    # applies; the role column says what they are and what they answer.
    for alias, d in sorted(decide.load().items()):
        w = work.get(alias, {})
        body.append(
            "<tr" + browse(connection=d.get("via", ""), status="no probe", role="decision",
                           price="free" if ":free" in d["served"] else "unknown") + ">"
            f"<td>{named(alias)}</td>"
            "<td data-sort='2'><span class='muted' title='decision models have no"
            " availability probe yet'>—</span>"
            + (" " + chip("new", "new", "onboarded this week") if d.get("onboarded", "") >=
               (datetime.date.today() - datetime.timedelta(days=7)).isoformat() else "")
            + f"</td><td>decision · {esc(', '.join(d['question_types']))}</td>"
            "<td class='num muted' data-sort='-1'>—</td><td class='muted'>—</td>"
            f"<td class='num'>{w.get('offloads', 0)}</td>"
            "<td data-sort='-1'><span class='muted'>not judged</span></td>"
            "<td class='num muted' data-sort='-1'>—</td><td class='num muted' data-sort='99999'>—</td>"
            "<td class='muted' data-sort='0'>—</td></tr>")
    body.append("</tbody></table>")
    key = """<dl>
      <dt>Status</dt><dd>From the latest probe. <em>healthy</em> answered;
        <em>throttled</em> and <em>slow</em> are rate limits and timeouts, which
        never retire a model inside 7 days; <em>retiring in Nd</em> is the
        countdown before the bench retires it on its own. Hover a chip for the
        provider's own words.</dd>
      <dt>Offloads, Helped</dt><dd>Real work handed over, and how much of what
        was judged actually helped. Benchmarks never count here.</dd>
      <dt>Suite</dt><dd>Verified findings located across the internal tasks:
        the tier that decides routing.</dd>
      <dt>Reply time</dt><dd>Median seconds to answer a one-word prompt: the
        serving path's overhead, not the model's worth.</dd>
      <dt>Last answer</dt><dd>The latest real answer from any source: probe,
        suite or a useful offload. Seven days without one retires a model.</dd>
      <dt>decision</dt><dd>A decision model: it answers typed questions (yes/no,
        choice, score) with probabilities through a Decisions API, never chat.
        The role column lists the question types it takes. No probe, suite or
        reply time applies to it.</dd>
      <dt>Filters</dt><dd>Connection is where a model's calls go (its Open WebUI
        tag, or its own endpoint). Price: <em>free</em> for a <code>:free</code>
        slug, a local model, a list price of 0 or a free-tier listing;
        <em>paid</em> for a subscription or a list price; else unknown. Combine
        them with the search box.</dd>
    </dl>"""
    facets = "".join(facet(k, k.title(), v) for k, v in offered.items())
    return card("At a glance", "Every model on the bench: whether it works right "
                "now, what it has been used for, and what it scored.",
                "".join(body), key, facets)


def on_bench() -> set[str]:
    """Aliases registered now: what the charts draw."""
    return set(registered().values())


def chart_card(title: str, sub: str, chart_id: str, table: str, key: str, extra: str = "") -> str:
    """A Benchmarks panel: the chart first (drawn by bench.js with Observable
    Plot), the table folded under it, the key under that."""
    return (f"<div class='panel'><h2>{title}</h2><p class='sub'>{sub}</p>{extra}"
            f"<div class='chart' id='{chart_id}'></div>"
            "<details class='key'><summary>Table</summary><div class='toolbar'><input type='search'"
            " data-filter placeholder='Filter by model or connection' aria-label='Filter rows'>"
            "<label><input type='checkbox' data-retired> Show retired</label></div>"
            f"<div class='scroll'>{table}</div></details>"
            f"<details class='key'><summary>How to read this</summary>{key}</details></div>")


def _family(task: str) -> str:
    for prefix, name in (("claim-", "claims"), ("seeded-hard-", "seeded, whole file"),
                         ("seeded-", "seeded, function"), ("rebench-", "real bugs")):
        if task.startswith(prefix):
            return name
    return "hand-built"


_wrong: tuple = (0.0, {})


def wrong_table() -> dict:
    """wrong.table(), shared by the three cards of one render: it scores every
    suite answer, and asking three times was a second of each reload."""
    global _wrong
    import wrong
    from run import images_only
    seen = _wrong
    if time.monotonic() - seen[0] > 5:
        table = wrong.table()
        # A vision or image model keeps its reviews (real work, judged) but not
        # its code-task answers: the suite is not what it is for (suite_scores).
        for alias in images_only() & set(table):
            r = table[alias]
            r.update(answers=0, wrong=0, by={k: [0, 0] for k in r["by"]}, hunt=dict.fromkeys(r["hunt"], 0))
        seen = _wrong = (time.monotonic(), table)    # stamped once scored, not before
    return seen[1]


def charts_data() -> dict:
    """Everything the Benchmarks charts draw, for bench.js. Registered models
    only: the tables keep retired ones behind "Show retired", a chart has no toggle."""
    import route
    import wrong
    live = on_bench()
    recall, wr = route.suite_recall(), wrong_table()
    overview = [{"model": m, "recall": recall[m][0] / recall[m][1], "wrong": wr[m]["wrong"] / wr[m]["answers"],
                 "found": f"{recall[m][0]}/{recall[m][1]}", "wrongN": f"{wr[m]['wrong']}/{wr[m]['answers']}"}
                for m in sorted(live) if recall.get(m) and recall[m][1] and wr.get(m, {}).get("answers")]
    heat = []
    for (task, m), v in sorted(suite_scores().items()):
        if m in live:
            hit = re.match(r"(\d+)/(\d+)", v["found"])
            heat.append({"model": m, "task": task, "family": _family(task), "label": v["found"],
                         "rate": int(hit[1]) / max(1, int(hit[2])) if hit else None, "invented": bool(v["invented"])})
    bars = [{"model": m, "kind": k, "rate": w / r["answers"], "n": f"{w}/{n}"}
            for m, r in sorted(wr.items()) if m in live and r["answers"] for k, (w, n) in r["by"].items() if w]
    totals = {m: {"rate": r["wrong"] / r["answers"], "answers": r["answers"]}
              for m, r in wr.items() if m in live and r["answers"]}
    hunt = [{"model": m, **r["hunt"]} for m, r in sorted(wr.items()) if m in live and any(r["hunt"].values())]
    return {"overview": overview, "heat": heat, "families": ["hand-built", "claims", "seeded, function",
                                                             "seeded, whole file", "real bugs"],
            "wrong": bars, "wrongTotals": totals, "kinds": list(wrong.KINDS), "hunt": hunt,
            "work": work_rows(), "vision": vision_rows(), **perf_charts()}


def work_rows() -> list[dict]:
    """Real work handed out (metrics/offloads.jsonl): how often it helped when
    judged, and the verified claims recorded since 2026-10-17."""
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
    """
    import run
    live = on_bench()
    heat, tasks = [], {}
    for path in sorted((DATA / "suite" / "tasks").glob("vision-*/task.json")):
        spec = json.loads(read(path))
        if spec.get("draft"):
            continue
        names = list(dict.fromkeys(c["label"].split(":")[0] for c in spec["must_find"]))
        traps = {c["label"].split(":")[0] for c in spec.get("must_not_claim", [])}
        tasks[spec["id"]] = {"title": spec["title"], "fields": names}
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
            "seatKinds": list(VISION_SEATS), "work": _vision_work_rows(live)}


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
    for raw in read(calls).splitlines():
        try:
            panel = json.loads(raw)
        except ValueError:
            continue
        if panel.get("shape") != "vision":
            continue
        for s in panel.get("seats", []):
            alias = current_alias(s.get("alias", ""))
            if alias not in live:
                continue
            r = out.setdefault(alias, {**dict.fromkeys(VISION_SEATS, 0), "real": 0, "false": 0, "seconds": []})
            v = judged.get(s.get("call"))
            if s.get("status") != "ok" or not (s.get("answer") or "").strip():
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
            if kind != "no answer" and s.get("seconds"):
                r["seconds"].append(float(s["seconds"]))
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
    key = """<dl>
      <dt>The vision tasks</dt><dd>Pictures made with their answer key (<code>suite/vision_tasks.py</code>):
        a receipt, a graded card's label, a bar chart, a table, a settings screen, shapes to count and an
        invoice turned on its side. Each field is <b>right</b>, <b>misread</b> (another value),
        <b>left out</b>, or a <b>trap taken</b>: one field per task asks for something the picture does
        not show, and the right answer is <code>not shown</code>. They run for every model that takes
        images; a vision model gets only these, never the code tasks.</dd>
      <dt>Real work on images</dt><dd>Every seat of a vision panel through the gateway. <b>Checked</b> means
        its verdict lists claims, each compared with the picture by hand: the claims right and wrong
        come from these alone. <b>Useful, no claims</b> is a verdict without them, which on thicken's
        photos means the seat agreed with another seat. Agreement is not a check, since seats fail
        together, so it is never counted as right.</dd>
      <dt>The number beside a name</dt><dd>How much there is: fields asked on the tasks, seats on real work.</dd>
    </dl>"""
    return ("<div class='panel'><h2>Vision</h2><p class='sub'>Reading images: fields read right on pictures with"
            " a known answer, then real work on images and how much of it was actually checked.</p>"
            "<h3 class='chart-head'>On the vision tasks, field by field</h3><div class='chart' id='chart-vision-fields'></div>"
            "<h3 class='chart-head'>On real work with images, by how each seat was judged</h3>"
            "<div class='chart' id='chart-vision-work'></div>"
            "<details class='key'><summary>Table</summary><div class='toolbar'><input type='search'"
            " data-filter placeholder='Filter by model or connection' aria-label='Filter rows'>"
            "<label><input type='checkbox' data-retired> Show retired</label></div>"
            f"<div class='scroll'>{''.join(body)}</div></details>"
            f"<details class='key'><summary>How to read this</summary>{key}</details></div>")


def perf_charts() -> dict:
    """Reliability over the last 14 days of probes, and speed on real tasks
    against how much a model finds, for the Performance panel of Benchmarks."""
    import route
    live = on_bench()
    since = (datetime.date.today() - datetime.timedelta(days=14)).isoformat()
    outcomes: dict[tuple[str, str], int] = {}
    for r in perf_rows():
        if r["model"] in live and r.get("timestamp", "") >= since:
            o = "answered" if int(r.get("answered") or 0) > 0 else (r.get("failure") or "error")
            outcomes[(r["model"], o)] = outcomes.get((r["model"], o), 0) + 1
    seconds: dict[str, list[float]] = {}
    for (_task, m), v in suite_scores().items():
        if m in live and re.match(r"\d+/\d+", v["found"]):
            seconds.setdefault(m, []).append(v["seconds"])
    recall = route.suite_recall()
    speed = [{"model": m, "seconds": sorted(s)[len(s) // 2], "recall": recall[m][0] / recall[m][1], "tasks": len(s)}
             for m, s in sorted(seconds.items()) if recall.get(m) and recall[m][1]]
    return {"reliability": [{"model": m, "outcome": o, "count": n} for (m, o), n in sorted(outcomes.items())],
            "speed": speed}


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
    key = """<dl>
      <dt>Right of the line</dt><dd>Bug tasks (a real file with one change seeded, or a public
        file before a real fix): green found the changed line, yellow named another line,
        grey said there was no bug.</dd>
      <dt>Left of the line</dt><dd>The same files untouched: red is a false alarm, claiming a
        bug in clean code. The longer the red, the less to trust its "there's a bug here".</dd>
      <dt>Order</dt><dd>Found share minus false-alarm share, best at the top.</dd>
    </dl>"""
    return chart_card("Bug hunt", "Every task with an answer key: bugs found against false alarms on the"
                      " same files left clean. Right is good, left is bad.", "chart-hunt", "".join(body), key)


def work_card() -> str:
    """How often real offloads helped."""
    rows = work_rows()
    body = ["<table><thead><tr>", th("Model"), th("Offloads", True), th("Judged", True), th("Helped", True),
            th("Verified findings", True), th("False claims", True), "</tr></thead><tbody>"]
    for r in sorted(rows, key=lambda r: -r["useful"] / r["judged"]):
        body.append(f"<tr><td>{named(r['model'])}</td><td class='num'>{r['offloads']}</td><td class='num'>{r['judged']}</td>"
                    f"<td class='num' data-sort='{r['useful'] / r['judged']:.3f}'>{r['useful']}/{r['judged']}</td>"
                    f"<td class='num'>{r['real']}</td><td class='num'>{r['false']}</td></tr>")
    body.append("</tbody></table>")
    key = """<dl>
      <dt>Helped</dt><dd>Of the offloads judged, the share recorded useful: real work (reviews,
        enumerations) handed to the model and checked afterwards. Benchmarks never count here.</dd>
      <dt>The number beside a name</dt><dd>How many were judged. 3 of 3 is weaker evidence than 40 of 50.</dd>
      <dt>Findings and false claims</dt><dd>Verified one by one in reviews, recorded since 2026-10-17.</dd>
    </dl>"""
    return chart_card("Real work", "When a model was given real work and the result was checked: how often"
                      " it helped. The most direct measure of trust the bench has.", "chart-work", "".join(body), key)


def overview_card() -> str:
    return ("<div class='panel'><h2>Who to trust with a review</h2><p class='sub'>Each dot is a model."
            " Right: finds more of the real bugs in our own code. Up: makes fewer things up (confidently"
            " wrong on clean code, wrong lines, false claims, traps). Top right is where a reviewer should be.</p>"
            "<div class='chart' id='chart-overview'></div></div>")


def suite_card() -> str:
    scores = suite_scores()
    live = current()
    scores = {(t, m): v for (t, m), v in scores.items() if m in live}
    tasks = sorted({t for t, _ in scores})
    models = sorted({m for _, m in scores})
    if not scores:
        body = "<p class='hint'>No suite runs recorded yet.</p>"
    else:
        body = ["<table><thead><tr>" + th("Model")
                + "".join(th(esc(t), True) for t in tasks)
                + th("Invented") + "</tr></thead><tbody>"]
        for model in models:
            cells, invented = [], []
            for task in tasks:
                hit = scores.get((task, model))
                if not hit:
                    cells.append("<td class='num muted' data-sort='-2'>—</td>")
                    continue
                found = hit["found"]
                bad = found in ("timeout", "refused", "error")
                m = re.match(r"(\d+)/(\d+)", found)
                rank = f"{int(m.group(1)) / max(1, int(m.group(2))):.3f}" if m else "-1"
                cells.append(f"<td class='num{' err' if bad else ''}' data-sort='{rank}'>"
                             f"{esc(found)}</td>")
                if hit["invented"]:
                    invented.append(f"{task}: {hit['invented']}")
            flag = ("<td class='err'>" + esc("; ".join(invented)) + "</td>"
                    if invented else "<td class='muted'>none</td>")
            body.append(f"{tr(model)}<td>{named(model)}</td>"
                        + "".join(cells) + flag + "</tr>")
        body.append("</tbody></table>")
        body = "".join(body)
    key = """<dl>
      <dt>n / N</dt><dd>Verified findings the model located, out of the total
        for that task. The answers were established from source before any
        model saw them.</dd>
      <dt>Invented</dt><dd><strong>The column that matters.</strong> Something
        asserted that is not true of the code. A confident wrong answer costs
        more than a silent miss.</dd>
      <dt>timeout / refused / error</dt><dd>Not a score. The model did not
        answer — a filter, an overloaded service and a slow model are three
        different facts and are never merged.</dd>
      <dt>(tag)</dt><dd>The connection it arrives through: its Open WebUI tag, or its endpoint. Models
        sharing one share its rate limit.</dd>
    </dl>"""
    return chart_card("Our code", "Every task on our own code, one square each: green found everything,"
                      " red found nothing, grey did not answer; a dot means it invented something. Hover"
                      " for the task and score.", "chart-code", body, key)


EXTERNAL = (("LiveBench", "https://livebench.ai/", "contamination-limited questions, refreshed monthly"),
            ("models.dev", "https://models.dev/", "specs and prices per provider (the catalog reads it daily)"),
            ("BullshitBench", "https://petergpt.github.io/bullshit-benchmark/viewer/index.next.html",
             "pushing back on nonsense; the idea behind confidently wrong"))


def external_card() -> str:
    """Links, not scores: the External tab showed FuncBench numbers nobody
    refreshed and was dropped (2026-10-16). Public leaderboards are read there."""
    rows = "".join(f"<li><a href='{esc(u)}' target='_blank' rel='noopener'>{esc(n)}</a>"
                   f" <span class='muted'>— {esc(why)}</span></li>" for n, u, why in EXTERNAL)
    return ("<div class='panel'><h2>External</h2><p class='sub'>Public leaderboards, for comparison."
            " The bench does not copy their numbers; its own tests above are what routing uses.</p>"
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
            meter = (f"<span class='helped'><span class='meter'><span{' class=low' if rate > .25 else ''}"
                     f" style='width:{max(4, round(rate * 100))}%'></span></span>"
                     f"<span class='mono'>{r['wrong']}/{r['answers']}</span></span>")
        else:
            rate, meter = -1, ("<span class='muted'>reads images: no code tasks</span>" if alias in images
                               else "<span class='muted'>no tasks yet</span>")
        body.append(f"<tr><td>{named(alias)}</td><td data-sort='{rate:.3f}'>{meter}</td>"
                    + "".join(cell(*r["by"][k]) for k in wrong.KINDS)
                    + (f"<td class='num' data-sort='{r['review_false']}'>{r['review_false']} in {r['reviews']}</td>"
                       if r["reviews"] else "<td class='num muted' data-sort='-1'>—</td>") + "</tr>")
    body.append("</tbody></table>")
    key = """<dl>
      <dt>Confidently wrong</dt><dd>Answers that asserted something false, of the answers
        that could have. Being wrong is why the bench asks several models; asserting a bug
        that is not there is what sends someone to fix working code. Lower is better.</dd>
      <dt>Control</dt><dd>A seeded or real-bug control (the clean file) answered FAULT.</dd>
      <dt>Wrong line</dt><dd>A seeded or real-bug task answered FAULT at a line the change did
        not touch. Saying NO FAULT on a bug is a miss and is not counted.</dd>
      <dt>False claim</dt><dd>A claim task whose claim is false, answered VERDICT: TRUE.</dd>
      <dt>Trap</dt><dd>A hand-built task's planted falsehood, asserted.</dd>
      <dt>Reviews</dt><dd>False claims recorded in verdicts (<code>offload.py verdict|add
        --false "..."</code>), over judged offloads. Recorded in structured form only since
        2026-10-17, so older reviews show none.</dd>
    </dl>"""
    return chart_card("Confidently wrong", "Who asserts things that are not true: false alarms on clean"
                      " code, wrong lines, false claims confirmed, traps taken. Shorter is better.",
                      "chart-wrong", "".join(body), key)


def benchmarks_tab() -> str:
    data = json.dumps(charts_data(), ensure_ascii=False).replace("</", "<\\/")
    return (overview_card() + bughunt_card() + work_card() + wrong_card() + suite_card() + vision_card()
            + perf_card() + external_card()
            + f"<script type='application/json' id='charts-data'>{data}</script>")


def perf_card() -> str:
    perf = perf_rows()
    if not perf:
        body = "<p class='hint'>No perf runs recorded yet.</p>"
    else:
        live = current()
        latest = {(r["model"], r["prompt"]): r for r in perf
                  if r["model"] in live}
        widest = max((float(r["p50_s"]) for r in latest.values()), default=1) or 1
        body = ["<table><thead><tr>" + th("Model") + th("Prompt") + th("p50", True)
                + th("p95", True) + th("ch/s", True) + th("tok/s", True) + th("Outcomes")
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
    key = """<dl>
      <dt>The bars</dt><dd><strong>p50 latency, relative to the slowest row in
        this table.</strong> A full-width bar is the slowest here, not a score
        out of anything.</dd>
      <dt>p50 / p95</dt><dd>Median and 95th-percentile seconds to a complete
        answer. Medians, not means — one timeout makes a mean fiction. p95 is
        where rate limiting shows itself.</dd>
      <dt>tiny / large</dt><dd>A one-word prompt measures overhead and
        queueing; an 18 KB prompt measures throughput. They answer different
        questions.</dd>
      <dt>ch/s</dt><dd>Characters per second, from the large prompt only. A
        proxy for tokens/s, affected by how verbose a model is.</dd>
      <dt>tok/s</dt><dd>Output tokens per second from the provider's own count, first token to
        last, large prompt only. Only where the built-in client made the call and the provider
        reported usage; blank otherwise, never estimated.</dd>
      <dt>Outcomes</dt><dd>answered / refused / error / timeout.</dd>
      <dt>Weight it LEAST</dt><dd>Seconds are a wash. Speed only vetoes a model
        when it is slow enough to change how you work.</dd>
    </dl>"""
    key = key.replace("</dl>", """
      <dt>Did it answer?</dt><dd>Each probe of the last 14 days, by what came back: an answer, a
        rate limit (capacity), a timeout, or a failure the bench classifies (structural, paywalled,
        entitlement, other).</dd>
      <dt>Speed vs quality</dt><dd>Median seconds per answer on our own suite tasks (real prompts,
        not the one-word probe) against the share of findings located. Top left is fast and good.</dd>
    </dl>""")
    return chart_card("Performance", "What the serving path does — not what the models are worth. One"
                      " reading is weather; the history is climate.", "chart-speed", body, key,
                      extra="<h3 class='chart-head'>Did it answer? Last 14 days of probes</h3>"
                            "<div class='chart' id='chart-reliability'></div>"
                            "<h3 class='chart-head'>Speed vs quality on our own tasks</h3>")


def favicon(theme: str) -> str:
    """The tab icon: the header's bench, its seat in the theme's brand colour
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


def render(theme: str | None = None) -> str:
    theme = theme if theme in themes() else saved_theme()      # a ?theme preview, else the saved one
    return (
        f"<!doctype html><html lang='en' data-theme='{esc(theme)}'><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Benchwarmers</title>" + favicon(theme) +
        f"<style>{brand_css()}{read(UI / 'bench.css')}</style>"
        # One line: the name on its bench, the credit beside it, the actions at
        # the far end. (The next scheduled runs were listed under it; Settings'
        # scheduler table shows them, with what each job does.)
        "<header><h1><span>Benchwarmers</span></h1>"
        # The credit is fixed, not a setting (RELEASE-PLAN decision 7).
        "<p class='sub'>by <a href='https://wendellresearch.ai' target='_blank' rel='noopener'>Wendell Research</a></p>"
        "<div class='head-actions'>"
        "<button class='btn fresh' id='fresh' hidden>New results · Reload</button>"
        # The onboarding queue's progress (bench.js fills it from /api/onboard-queue).
        "<div class='oq' id='oq' hidden role='status'>"
        "<span class='oq-chip' id='oq-chip'><span id='oq-text'></span></span>"
        "<button class='linkish' id='oq-open'>Details</button>"
        "<button class='linkish' id='oq-stop'>Stop</button>"
        "<button class='linkish' id='oq-clear'>Clear</button></div>"
        "<button class='btn primary' id='onboard-open'>Onboard a model</button></div></header>"
        # What needs a person first, then the pages; endpoints are managed from
        # Connections now (the header button moved there).
        f"<main>{first_run()}{problems()}<div class='layout'>{tabs()}</div></main>{editor()}"
        f"<script>window.BENCH={directory_json()};</script>"
        # Observable Plot (on d3) draws the Benchmarks charts. Pinned versions from
        # jsDelivr; without a network the tables under each chart still work.
        "<script src='https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js'></script>"
        "<script src='https://cdn.jsdelivr.net/npm/@observablehq/plot@0.6.16/dist/plot.umd.min.js'></script>"
        f"<script>{read(UI / 'bench.js')}</script></html>")


def gateway_card(limit: int = 30) -> str:
    """Recent bench-gateway requests: who sat, how each seat went, and which
    verdicts are still owed. A verdict owed is evidence the router is waiting
    for; this is where it shows."""
    lines = read(DATA / "runs" / "gateway" / "calls.jsonl").splitlines()
    records = []
    for line in reversed(lines):
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
            th("Seats"), th("Owed", True), "</tr></thead><tbody>"]
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
        body.append(
            f"<tr><td class='mono' data-sort='{esc(r.get('at', ''))}'>{esc(r.get('at', '')[:16].replace('T', ' '))}</td>"
            f"<td class='mono'>{esc(r.get('model', ''))}</td>"
            f"<td class='mono'>{esc(r.get('key') or '—')}</td>"
            f"<td title='{esc(r.get('shape_by', ''))}'>{esc(r.get('shape', ''))}</td>"
            f"<td>{' '.join(chips)}</td>"
            f"<td class='num{' muted' if not row_owed else ''}'>{row_owed}</td></tr>")
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
      <dt>Owed</dt><dd>Seats without a verdict. Each is recorded as an unjudged
        offload, so rotation still counts it; a verdict tells the router whether
        it helped. The orchestrator that called can also record one:
        <code>python metrics/offload.py verdict &lt;call id&gt; --useful</code>.</dd>
      <dt>Explorer shape</dt><dd>Which models could fill explorer seats; hover for
        how it was chosen (set on the collection, or a decision model's label).</dd>
    </dl>"""
    return card("Gateway", f"The last {len(records)} bench-gateway requests:"
                f" {seats_total} seats, {owed} verdict{'s' * (owed != 1)} owed.",
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
    verdict dialog."""
    panel = call.rsplit(".", 1)[0]
    for line in reversed(read(DATA / "runs" / "gateway" / "calls.jsonl").splitlines()):
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
    paths = [sources.CACHE / f"{n}.json" for n in sources.FEEDS] + [endpoints.YAML, endpoints.FILE,
                                                                     endpoints.CONNECTIONS]
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


def discover_card() -> str:
    """Models worth adding, from models.dev and FreeLLMAPI (metrics/discover.py),
    deduped. Read from the daily cache, so the page never waits on the network;
    built once per cache refresh. A click on a row opens its popup (bench.js),
    which asks /api/discover-model for the rest: its routes are where Add is."""
    all_rows = _discover_rows()
    rows = [r for r in all_rows if r["free"] or r["reach"] in ("connected", "on bench")]
    hidden = len(all_rows) - len(rows)
    reach_css = {"on bench": "ok", "connected": "ok", "easy to add": "warn", "other": ""}
    body = ["<table><thead><tr>", th("Model"), th("Provider"), th("Reach"), th("Price"), th("Context", True),
            th("In → out"), th("Can"), th("Released"), th("Limits"), "<th></th></tr></thead><tbody>"]
    for r in rows:
        caps = [c for c, on in (("tools", r["tools"]), ("vision", r["vision"]),
                                ("reasons", r["reasoning"])) if on]
        cost = r.get("cost") or {}
        price = ("free" if r["free"] else
                 f"{cost.get('input', '?')} / {cost.get('output', '?')}" if cost else "paid")
        lim = r.get("limits") or {}
        lim_text = " · ".join(f"{v:,} {k}" for k, v in lim.items()) if lim else ""
        ctx = r.get("context")
        add = ("<button class='btn small' type='button'>Add…</button>"
               if any(not x["on_bench_as"] for x in r["routes"]) else "")
        body.append(
            f"<tr class='pick-row{'' if r['free'] else ' paid'}' data-provider='{esc(r['provider'])}'"
            f" data-model='{esc(r['model'])}'><td>"
            f"<a href='#' class='dmodel'><span class='alias'>{esc(r['name'])}</span></a>"
            + (" " + chip("new", "new", f"first seen {r['first_seen']}") if r["new"] else "")
            + f"<span class='conn'>{esc(r['model'])}"
            + (f" · on bench as {esc(r['on_bench_as'])}" if r.get("on_bench_as") else "")
            + (f" · also on bench as {esc(', '.join(r['also_on']))}" if r.get("also_on") else "")
            + "</span></td>"
            f"<td title='{esc(r['provider'])}'>{esc(r['provider_name'])}</td>"
            f"<td data-sort='{['on bench', 'connected', 'easy to add', 'other'].index(r['reach'])}'>"
            f"{chip(r['reach'], reach_css[r['reach']], r.get('api') or '')}</td>"
            f"<td title='per million tokens, input / output'>{esc(price)}</td>"
            + (f"<td class='num' data-sort='{ctx}'>{ctx // 1000:,}k</td>" if ctx
               else "<td class='num muted' data-sort='-1'>?</td>")
            + f"<td class='muted'>{esc(', '.join(r['in_mods']))} → {esc(', '.join(r['out_mods']))}</td>"
            + f"<td>{' '.join(chip(c, '') for c in caps) or '<span class=muted>—</span>'}</td>"
            f"<td class='mono muted' data-sort='{esc(r.get('released') or '')}'>{esc(r.get('released') or '')}</td>"
            f"<td class='muted'>{esc(lim_text)}</td>"
            f"<td>{add}</td></tr>")
    body.append("</tbody></table>")
    free = sum(1 for r in rows if r["free"])
    toolbar = ("<div class='toolbar'><input type='search' data-filter placeholder='Filter by model,"
               " provider or reach' aria-label='Filter rows'>"
               "<label><input type='checkbox' data-paid> Show paid (connected providers)</label></div>")
    key = """<dl>
      <dt>Provider</dt><dd>Who serves the model: a row per provider, so the same model
        from OpenRouter and from NVIDIA is two rows. Hover for its models.dev id.</dd>
      <dt>Reach</dt><dd><em>on bench</em> already registered; <em>connected</em> this
        install has a route to the provider (an Open WebUI connection, or an endpoint of its
        own); <em>easy to add</em> an OpenAI-compatible API, so one new connection away (hover
        for its base URL); <em>other</em> a native API or an unvetted reseller.</dd>
      <dt>Price</dt><dd>Free if models.dev prices it at 0 or FreeLLMAPI lists it (a catalog of
        free tiers): models.dev shows the PAID price even where a free tier exists. Otherwise
        input / output per million tokens.</dd>
      <dt>New</dt><dd>First seen in either list in the last 7 days.</dd>
      <dt>Add</dt><dd>Click a row: its popup has everything known about the model and every
        route this install has to it, each with <em>Add via this</em>. <em>Via Open WebUI</em>
        adds the model to that connection's Model IDs (when it lists them), creates its model
        record public, tagged like the connection's other models and with their logo, then runs
        the bench's onboarding (register, 4-call probe, suite), so it is in the chats too.
        <em>Via LiteLLM</em> or <em>CLI Proxy API</em> adds it to that hub's connection (one more
        deployment, or the provider's models list); those hubs are asked when the popup opens.
        <em>Direct</em> onboards it on the provider's own endpoint: bench only. A new provider
        needs a connection or an endpoint first; hover its <em>easy to add</em> chip for the
        base URL.</dd>
      <dt>Sources</dt><dd>models.dev and/or FreeLLMAPI's free catalog (named in the popup); a
        model one provider offers in both is shown once. Both are refreshed at most daily.</dd>
    </dl>"""
    return (f"<div class='panel'><h2>Discover</h2><p class='sub'>{free} free models from models.dev"
            f" and FreeLLMAPI, deduped; {hidden:,} paid models on providers you have no route"
            f" to are left out. Click a model for everything known about it and the routes it can be"
            f" added by.</p>{toolbar}"
            f"<div class='scroll'>{''.join(body)}</div>"
            f"<details class='key'><summary>How to read this</summary>{key}</details></div>")


def collections_tab() -> str:
    """Every collection; clicking one, or New collection, opens its popup."""
    return (
        "<div class='panel form-panel'><h2 id='c-title'>Collections</h2>"
        "<p class='served'>Named groups of models the bench gateway runs together. A caller"
        f" asks for <code>bench/&lt;name&gt;</code> at <code>{esc(gateway_url())}</code>;"
        " every member answers, plus explorer seats the bench fills by staleness. Two to four"
        " members is the sweet spot. Open one to edit or delete it.</p>"
        "<div class='list-actions'><button class='btn primary' id='c-new'>New collection</button>"
        "<span class='done' id='c-status' role='status'></span></div>"
        "<div id='c-list'></div>"
        # The details in a popup, like every other edit on the dashboard: for
        # the collection clicked, or a new one.
        "<dialog id='c-form' aria-labelledby='c-form-head'>"
        "<h2 id='c-form-head'>New collection</h2>"
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
        # text, case-insensitive), on the member list only; ticked ones stay shown.
        "<label class='field' for='c-member-filter'>Members</label>"
        "<input type='search' id='c-member-filter' class='member-filter' spellcheck='false' autocomplete='off'"
        " placeholder='Filter by alias or served name' aria-controls='c-members' aria-describedby='c-count'>"
        "<div id='c-members' class='members' role='group' aria-label='Members'></div>"
        "<p class='rules' id='c-count'></p>"
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
        "<div class='errors' id='c-errors' role='alert'></div><div class='done' id='c-done'></div>"
        "<div class='actions'><button class='btn danger' id='c-delete' hidden>Delete collection</button>"
        "<span class='spacer'></span><button class='btn' id='c-close'>Close</button>"
        "<button class='btn primary' id='c-save'>Save collection</button></div>"
        "</dialog>"
        "<details class='c-settings'><summary class='form-head'>Gateway settings</summary>"
        "<div class='settings'>"
        "<div><label class='field' for='g-max-calls'>Max calls per request</label>"
        "<input id='g-max-calls' type='number' min='1' max='10'></div>"
        "<div><label class='field' for='g-max-explorers'>Max explorers</label>"
        "<input id='g-max-explorers' type='number' min='0' max='2'></div>"
        "</div>"
        "<p class='rules'>Explorers = min(max explorers, max calls − members): with 5 and 2, a"
        " 2- or 3-model collection gets 2, a 4-model one 1, a 5-model one none. A request that"
        " names no collection is refused with each one's description and tags, so the caller"
        " can choose.</p>"
        "<div class='errors' id='g-errors' role='alert'></div><div class='done' id='g-done'></div>"
        "<div class='actions'>"
        "<button class='btn primary' id='g-save'>Save settings</button></div>"
        "</details></div>")


# A grouped sidebar since 2026-10-19 (RELEASE-PLAN "The dashboard"): eight
# pages were too many for a row of tabs. Collections holds the gateway's
# requests under the collections they ran; Benchmarks holds Performance.
SECTIONS = (("Observe", (("glance", "At a glance", "roster"),
                         ("suite", "Benchmarks", "benchmarks_tab"),
                         ("quotas", "Quota management", "quotas_page"),
                         ("logs", "Logs", "logs_page"))),
            ("Gateway", (("endpoints", "Endpoints", "endpoints_page"),
                         ("models", "Models", "models_page"),
                         ("collections", "Collections", "collections_and_gateway"),   # starred (bench.css)
                         ("discover", "Discover", "discover_card"))),
            ("Control", (("settings", "Settings", "settings_page"),
                         ("info", "Info", "info_page"))))
TABS = tuple(page for _, pages in SECTIONS for page in pages)
UI_CONFIG = DATA / "config" / "ui.json"


def collections_and_gateway() -> str:
    return collections_tab() + gateway_card()


def tabs() -> str:
    nav = "".join(f"<div class='group'><h2>{esc(group)}</h2>"
                  + "".join(f"<button role='tab' data-tab='{key}' aria-selected='false'>{esc(label)}</button>"
                            for key, label, _ in pages) + "</div>"
                  for group, pages in SECTIONS)
    panes = "".join(f"<section class='tab' id='tab-{key}'>{globals()[fn]()}</section>"
                    for key, _, fn in TABS)
    return f"<nav class='tabs' role='tablist' aria-label='Pages'>{nav}</nav><div class='panes'>{panes}</div>"


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


def gateway_keys_card() -> str:
    def when(stamp) -> str:
        return esc(str(stamp or "")[:16].replace("T", " ")) or "<span class='muted'>never</span>"
    rows = "".join(f"<tr><td class='mono'>{esc(n)} <span class='muted'>{guard.PREFIX}…{esc(e.get('hint', ''))}</span></td>"
                   f"<td>{esc(', '.join(e.get('collections') or [])) or 'all'}</td>"
                   f"<td class='num'>{esc(e.get('rpm') or '—')}</td>"
                   f"<td>{when(e.get('created'))}</td><td>{when(e.get('last_used'))}</td>"
                   f"<td><button class='btn' data-revoke-key='{esc(n)}'>Revoke</button></td></tr>"
                   for n, e in sorted(guard.gateway_keys().items()) if isinstance(e, dict))
    if guard.auth_on():
        asked = "An admin token is set, so every call to <code>/v1/…</code> and <code>/bench/verdict</code> needs one."
    elif guard.keys_required():
        asked = ("<code>BENCHWARMERS_REQUIRE_KEYS</code> is set, so every call to the gateway needs one, on this"
                 " machine too.")
    else:
        asked = ("None is asked for yet: without an admin token (<code>BENCHWARMERS_ADMIN_TOKEN</code>) the gateway"
                 " serves this machine with no key, as before. Set one, or <code>BENCHWARMERS_REQUIRE_KEYS=1</code>"
                 " to ask on this machine too, and every call needs a key.")
    scopes = "".join(f"<label class='check'><input type='checkbox' name='k-scope' value='{esc(c)}'>"
                     f" {bench_collections.PREFIX}{esc(c)}</label>" for c in sorted(bench_collections.load()))
    return ("<div class='panel form-panel'><h2>Gateway keys</h2><p class='muted'>For a chat UI, an agent or another"
            " machine, sent as <code>Authorization: Bearer &lt;key&gt;</code>. A key reaches the gateway only, never"
            " this dashboard; revoke it and it stops at once. Its name is on every call it makes (the Gateway"
            f" card). {asked}</p>"
            f"<table><thead><tr>{th('Name')}{th('May call')}{th('A minute', True)}{th('Made')}{th('Last used')}"
            f"<th></th></tr></thead>"
            f"<tbody>{rows or '<tr><td colspan=6 class=muted>No keys yet.</td></tr>'}</tbody></table>"
            "<label class='field' for='k-name'>Name for a new key</label>"
            "<input id='k-name' placeholder='e.g. open-webui' autocomplete='off'>"
            "<fieldset><legend class='field'>Collections it may call (none ticked: all)</legend>"
            f"{scopes or '<span class=muted>No collections yet.</span>'}</fieldset>"
            "<label class='field' for='k-rpm'>Requests a minute (empty: no cap)</label>"
            "<input id='k-rpm' type='number' min='1' max='10000' placeholder='no cap' autocomplete='off'>"
            "<div class='errors' id='k-errors' role='alert'></div>"
            "<div class='done' id='k-done' hidden><p>Copy it now: it is shown this once and kept only as a hash.</p>"
            "<input id='k-value' class='mono' readonly></div>"
            "<div class='actions'><button class='btn primary' id='k-make'>Make a key</button></div></div>")


def login_page() -> str:
    """The admin token goes in a JSON POST, through the same guard as every
    write. Opened from a link on another site, SameSite=Strict holds the
    cookie back, so a browser already signed in is sent on to the dashboard."""
    return ("<!doctype html><html lang='en'><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'><title>Benchwarmers</title>"
            + favicon(saved_theme()) +
            "<body style='font:16px system-ui;max-width:24rem;margin:4rem auto;padding:0 1rem'>"
            "<h1>Benchwarmers</h1><form id='f'><label for='t'>Admin token</label>"
            "<input id='t' type='password' autocomplete='current-password' required"
            " style='display:block;width:100%;box-sizing:border-box;font:inherit;margin:.5rem 0 1rem'>"
            "<button>Sign in</button><p id='e' role='alert'></p></form><script>"
            "fetch('/api/stamp').then(function (r) { if (r.ok) location.replace('/'); });"
            "document.getElementById('f').addEventListener('submit', function (ev) {"
            " ev.preventDefault();"
            " fetch('/login', {method: 'POST', headers: {'Content-Type': 'application/json'},"
            "  body: JSON.stringify({token: document.getElementById('t').value})}).then(function (r) {"
            "  if (r.ok) location.replace('/');"
            "  else document.getElementById('e').textContent = 'That is not the admin token.'; });"
            "});</script>")


def access_card() -> str:
    """How this install is reached and who may call it, from the environment:
    shown here, never set here. Each needs a restart, and a page that could
    change its own login could open itself to anyone. No secret is shown."""
    import paths
    bind = guard.bind()
    hosts, origins = sorted(guard._env("BENCH_ALLOWED_HOSTS")), sorted(guard._env("BENCH_ALLOWED_ORIGINS"))
    from_env = sorted(k[len(endpoints.ENV_KEY):].lower().replace("_", "-")
                      for k, v in os.environ.items() if k.startswith(endpoints.ENV_KEY) and v)
    if guard.auth_on():
        keys = "asked for on every gateway call (an admin token is set)"
    elif guard.keys_required():
        keys = "asked for on every gateway call, on this machine too"
    else:
        keys = "not asked for: this machine only, no admin token"
    rows = [("Listening on", f"{bind}:{PORT} (dashboard, gateway at /v1) · {bind}:{gateway.PORT} (gateway.py)",
             "BENCH_BIND, BENCH_PORT, BENCH_GATEWAY_PORT"),
            ("Reached from", "this machine only" if guard._loopback(bind)
             else "other machines too", "BENCH_BIND"),
            ("Admin login", "on: the dashboard asks for the token" if guard.auth_on()
             else "off, which only this machine allows", "BENCHWARMERS_ADMIN_TOKEN"),
            ("Gateway keys", keys, "BENCHWARMERS_REQUIRE_KEYS"),
            ("Public address", guard.public_url() or "none", "BENCHWARMERS_PUBLIC_URL"),
            ("Other host names", ", ".join(hosts) or "none", "BENCH_ALLOWED_HOSTS"),
            ("Browser apps on other sites", ", ".join(origins) or "none: same origin only", "BENCH_ALLOWED_ORIGINS"),
            ("Data folder", f"{DATA} ({paths.WHY})", "BENCHWARMERS_HOME"),
            ("Endpoint keys from the environment", ", ".join(from_env) or "none: all in the llm key store",
             f"{endpoints.ENV_KEY}<NAME>")]
    body = "".join(f"<tr><th scope='row'>{esc(k)}</th><td>{esc(v)}</td>"
                   f"<td class='mono set-by'>{'<br>'.join(esc(s) for s in src.split(', '))}</td></tr>"
                   for k, v, src in rows)
    return ("<div class='panel'><h2>Server and access</h2><p class='muted'>How this install is reached and who"
            " may call it. Set in the environment, read at start: change one and restart. Shown here, never"
            " set here, since a page that could change its own login could open itself to anyone. No secret"
            " is shown, only whether one is set.</p>"
            f"<table class='facts'><thead><tr>{th('Setting')}{th('Now')}{th('Set by')}</tr></thead>"
            f"<tbody>{body}</tbody></table></div>")


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
    return {"ok": True, "errors": []}


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
    return ("<div class='panel form-panel'><h2>Model client</h2><p class='muted'>What makes every model call"
            " the bench makes: gateway seats, the suite, the probes and auto-retire's checks. The next call uses"
            " it, no restart. Whichever it is, a model on a sign-in that renews or on ChatGPT's plan, and any"
            " call with images, goes through the built-in client.</p>"
            f"<label class='field' for='mc-client'>Client</label><select id='mc-client'{off}>{opts}</select>"
            f"<p class='rules'>{where}</p>"
            "<div class='errors' id='mc-errors' role='alert'></div><div class='done' id='mc-done'></div>"
            f"<div class='actions'><button class='btn primary' id='mc-save'{off}>Save client</button></div></div>")


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
    return ("<div class='panel form-panel'><h2>Free only in their own app</h2><p class='muted'>Some providers'"
            " free models answer only inside the provider's own app: opencode's say its free tier \"can only be"
            " used from within opencode\". Discover leaves out the free models of the providers ticked here;"
            " their paid models stay.</p>"
            "<table><thead><tr>" + th("Provider") + th("Free models left out") + f"</tr></thead><tbody>{rows}</tbody></table>"
            "<label class='field' for='oc-add'>Add a provider</label>"
            "<input id='oc-add' placeholder='its models.dev id' autocomplete='off'>"
            "<div class='errors' id='oc-errors' role='alert'></div><div class='done' id='oc-done'></div>"
            "<div class='actions'><button class='btn primary' id='oc-save'>Save</button></div></div>")


def settings_page() -> str:
    """Theme (saved as picked), gateway keys, the scheduler (mode and jobs),
    the model client, providers whose free tier is theirs alone, and how the
    install is reached (shown only: the environment sets it)."""
    import scheduler
    current = saved_theme()
    swatch = ("--paper", "--band", "--ink", "--brand", "--primary", "--signal", "--link", "--err")
    cards = []
    for name in themes():
        merged = {**_tokens(BASE_THEME), **_tokens(name)}
        dots = "".join(f"<span style='background:{esc(merged.get(t, '').strip())}'></span>" for t in swatch)
        cards.append(f"<label class='theme-card'><input type='radio' name='theme' value='{esc(name)}'"
                     f"{' checked' if name == current else ''}><b>{esc(THEME_LABELS.get(name, name.title()))}</b>"
                     f"<span class='swatch'>{dots}</span></label>")
    conf, now = scheduler.settings(), datetime.datetime.now()
    state, on = load_json(scheduler.STATE), scheduler.enabled(conf)
    rows = "".join(
        f"<tr><td title='runs {esc(' '.join(args))}'>{esc(job)}</td><td><input type='checkbox' name='s-job'"
        f" value='{esc(job)}' aria-label='Run {esc(job)}'{' checked' if job in on else ''}></td>"
        f"<td>{esc(state.get(job, '—')[:16].replace('T', ' '))}</td>"
        f"<td>{scheduler.next_due(slots, now):%a %d %b %H:%M}</td>"
        f"<td>{esc(scheduler.ABOUT.get(job, ''))}</td></tr>"
        for job, (args, slots) in scheduler.JOBS.items())
    modes = "".join(f"<option value='{esc(k)}' data-why='{esc(why)}'{' selected' if k == conf['mode'] else ''}>"
                    f"{esc(label)}</option>" for k, (label, why) in scheduler.MODES.items())
    return (
        "<div class='panel form-panel'><h2>Theme</h2><p class='muted'>Applied and saved the moment you pick one."
        " Every theme passes the contrast check (<code>scripts/check_themes.py</code>). The \"by Wendell"
        " Research\" credit is not a setting.</p>"
        f"<div class='themes' id='themes'>{''.join(cards)}</div>"
        "<div class='errors' id='t-errors' role='alert'></div><div class='done' id='t-done'></div></div>"
        + gateway_keys_card() +
        "<div class='panel form-panel'><h2>Scheduler</h2><p class='muted'>The recurring checks, run by the"
        " dashboard itself. A change applies within a minute, without a restart.</p>"
        f"<label class='field' for='s-mode'>Mode</label><select id='s-mode' data-was='{esc(conf['mode'])}'>{modes}</select>"
        "<p class='rules' id='s-mode-why'></p>"
        "<table><thead><tr>" + th("Job") + th("On") + th("Counted up to") + th("Next")
        + th("What it does") + f"</tr></thead><tbody>{rows}</tbody></table>"
        "<p class='rules'>Run makes model calls on its own schedule. If something else also runs these jobs"
        " (cron, Windows Task Scheduler), turn it off in the same step, or every job runs twice.</p>"
        "<div class='errors' id='s-errors' role='alert'></div><div class='done' id='s-done'></div>"
        "<div class='actions'><button class='btn primary' id='s-save'>Save scheduler</button></div></div>"
        + client_card() + own_client_card() + access_card())


def endpoints_page() -> str:
    """Each endpoint, its actions, what it serves; your own apps and rate limits
    per connection (bench.js fills those two from /api/connect and /api/connections)."""
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
    tags, status = load_json(DATA / "config" / "provider-tags.json"), model_status()
    blocks = []
    for e in eps:
        mine, listed = on.get(e["name"], {}), serves.get(e["name"], {})
        rows = []
        for served in sorted(set(mine) | set(listed), key=lambda s: (s not in mine, s.lower())):
            alias, day = mine.get(served), listed.get(served)
            if alias:
                label, css, why = status.get(alias, ("decision", "", "a decision model"))
                bench = f"<td>{named(alias)}</td>"
                rest = (f"<td>{esc(catalog.connection_of(alias))}</td>"
                        f"<td data-sort='0'>{chip(label, css, why)}</td>")
            else:
                gone = day and day != checked
                bench = "<td class='muted'>not on the bench</td>"
                rest = (f"<td>{esc((tags.get(served, '').split('/')[0] if e['hub'] == 'openwebui' else e['name']) or '—')}</td>"
                        f"<td data-sort='{2 if gone else 1}'>"
                        + (chip(f"last seen {day}", "warn") if gone else chip("served", "")) + "</td>")
            rows.append(f"<tr>{bench}<td class='mono'>{esc(served)}</td>{rest}</tr>")
        extra = sum(1 for s, d in listed.items() if s not in mine and d == checked)
        # Its actions (bench.js reads the row from data-ep): a sign-in again
        # where it came from one; Make default and Remove on all but the default.
        signed = e["connect"]
        acts = "<button class='btn small' data-ep-act='change'>Change key</button>"
        if signed and signed.get("flow") and signed["flow"] != "local":
            acts += "<button class='btn small' data-ep-act='again'>Sign in again</button>"
        if not e["default"]:
            acts += ("<button class='btn small' data-ep-act='default'>Make default</button>"
                     "<button class='btn small danger' data-ep-act='remove'>Remove</button>")
        until = (" until " + signed["expires"][:10]) if signed and signed.get("expires") and not signed.get("refreshes") else ""
        blocks.append(
            f"<div class='ep' data-ep='{esc(json.dumps(e))}'><div class='ep-head'><div>"
            f"<div class='what'><span class='alias'>{esc(e['name'])}</span>"
            + (" " + chip("default", "") if e["default"] else "")
            + " " + chip("key set" if e["key_set"] else "no key", "ok" if e["key_set"] else "warn")
            + ((" " + chip("sign in again", "warn") if signed.get("expired") else " " + chip("signed in" + until, "via"))
               if signed else "")
            + f"</div><div class='base mono'>{esc(e['base'])}</div></div><div class='acts'>{acts}</div></div>"
            f"<details class='models'><summary>{len(mine)} on the bench"
            + (f" · {extra} more served" if extra else "") + "</summary>"
            "<table><thead><tr>" + th("On the bench as") + th("Served as") + th("Connection") + th("Status")
            + f"</tr></thead><tbody>{''.join(rows)}</tbody></table></details></div>")
    return (
        "<div class='panel'><h2>Endpoints</h2><p class='sub'>Where models are served from: providers,"
        " aggregators, a hub such as Open WebUI, or a local runtime. Prompts to a model go straight to its"
        " endpoint, so add only ones you are content to send private source to. Each shows whether its key"
        " is set (never the key) and what it serves: the bench's own models, then what the daily served"
        f" check listed{' on ' + esc(checked) if checked else ''}.</p>"
        "<div class='list-actions'><button class='btn primary' id='e-add'>Add an endpoint</button>"
        "<span class='done' id='el-done'></span><span class='errors' id='el-errors' role='alert'></span></div>"
        "<div class='toolbar'><input type='search' data-filter placeholder='Find a model on any endpoint'"
        " aria-label='Filter models'></div>"
        f"{''.join(blocks)}</div>"
        "<div class='panel form-panel'><h2>Use your own app</h2>"
        "<p class='sub'>Some providers need an app registered with them before anyone can sign in."
        " Benchwarmers ships Wendell Research's where it has one; register your own and paste its"
        " public client id to use yours instead (never a secret: none of these use one). Endpoints"
        " already signed in keep working with the app they were signed in with.</p>"
        "<div id='cn-apps'></div><div class='errors' id='cn-app-errors' role='alert'></div>"
        "<div class='done' id='cn-app-done'></div></div>"
        "<div class='panel form-panel'><h2>Rate limits per connection</h2>"
        "<p class='sub'>What a key behind each connection allows, from the provider's docs or"
        " freellmapi.co/models. Models on that connection share it; a model's own limits (its"
        " popup) win, and limits the bench learns from errors only ever lower these. rpmo is calls"
        " a calendar month (UTC), for a trial key's monthly cap. The built-in client keeps to them:"
        " when one is used up it waits if the call can still finish in time, else it does not send.</p>"
        "<div id='l-list'></div><div class='errors' id='l-errors' role='alert'></div>"
        "<div class='done' id='l-done'></div></div>")


def models_page() -> str:
    """Every model on the bench by the connection it comes from."""
    cards = []
    # Which of this install's endpoints each model is reached THROUGH, beside
    # the connection (provider) it comes FROM: an OpenRouter model may be on
    # OpenRouter's own endpoint or arrive through Open WebUI's.
    through = {r["alias"]: r["endpoint"] for r in endpoints.registry()}
    decisions = decide.load()
    through.update({a: d["endpoint"] for a, d in decisions.items()})
    # Every model, decision models too (connections_view is chat models only,
    # for what a collection may hold): they are on the bench like any other.
    by: dict[str, list[str]] = {}
    for a in model_directory():
        by.setdefault(catalog.connection_of(a), []).append(a)
    for c in ({"name": n, "models": sorted(m)} for n, m in sorted(by.items())):
        rows = []
        for a in c["models"]:
            f = catalog.info(a)
            lim = f.get("limits") or {}
            num = lambda v: f"{v:,}" if isinstance(v, int) else "—"          # noqa: E731
            ep = through.get(a, "?")
            kind = (" " + chip("decision", "", "a decision model: answers typed questions, not chat")
                    if a in decisions else "")
            rows.append(f"<tr><td><a href='#' class='model' data-alias='{esc(a)}'>"
                        f"<span class='alias'>{esc(a)}</span></a>{kind}</td>"
                        f"<td data-sort='{esc(ep)}'>{chip(ep, 'via', f'reached through the {ep} endpoint')}</td>"
                        f"<td class='num' data-sort='{f.get('context_window') or 0}'>{num(f.get('context_window'))}</td>"
                        f"<td class='num' data-sort='{f.get('max_output') or 0}'>{num(f.get('max_output'))}</td>"
                        f"<td>{esc(', '.join(f.get('input_modalities') or []) or '—')}</td>"
                        + "".join(f"<td class='num'>{num(lim.get(k))}</td>" for k in catalog.LIMIT_KEYS) + "</tr>")
        # One column plan for every card, so the cards line up under each other
        # (each table sized its own columns, so they drifted from card to card).
        cols = "<colgroup><col class='c-model'><col class='c-via'><col class='c-n'><col class='c-n'>" \
               "<col class='c-input'>" + "<col class='c-lim'>" * len(catalog.LIMIT_KEYS) + "</colgroup>"
        cards.append(f"<div class='panel'><h3 class='chart-head'>{esc(c['name'])} · {len(c['models'])} models</h3>"
                     f"<table class='model-cards'>{cols}<thead><tr>" + th("Model") + th("Through")
                     + th("Context", True) + th("Max output", True)
                     + th("Input") + "".join(th(k, True) for k in catalog.LIMIT_KEYS)
                     + f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div>")
    return ("<div class='panel'><h2>Models</h2><p class='sub'>Every model on the bench by the connection it"
            " comes from, with the endpoint it is reached through and what is known of its limits. A name"
            " opens the model.</p></div>"
            + "".join(cards))


def quotas_page() -> str:
    """What limits each connection, who is resting after one, what providers
    last said was left (the built-in client's quota records, metrics/quota.py)
    and what the bench itself sent (its usage ledger, metrics/ledger.py)."""
    import learn
    import ledger
    import quota
    now = datetime.datetime.now().astimezone()
    rec_rows = []
    for r in sorted(quota.live(), key=lambda r: (r.get("connection", ""), r.get("alias", ""), r.get("metric", ""))):
        try:
            age = int((now - datetime.datetime.fromisoformat(r["observed_at"])).total_seconds() // 60)
            reset = datetime.datetime.fromisoformat(r["reset_at"]).astimezone().strftime("%Y-%m-%d %H:%M")
        except (KeyError, TypeError, ValueError):
            continue
        num = lambda v: f"{v:,}" if isinstance(v, int) else "—"          # noqa: E731
        rec_rows.append(f"<tr><td>{esc(r.get('connection', ''))}</td><td>{named(r.get('alias', ''))}</td>"
                        f"<td>{esc(r.get('metric', ''))} / {esc(r.get('window', ''))}</td>"
                        f"<td class='num'>{num(r.get('remaining'))}</td><td class='num'>{num(r.get('limit'))}</td>"
                        f"<td class='mono'>{esc(reset)}</td>"
                        f"<td>{esc(r.get('source', ''))}, {age} min ago</td></tr>")
    t = now.timestamp()
    sent: dict = {}
    for row in ledger.rows(min(t - 86400, ledger.month_start(t))):
        s = sent.setdefault(row.get("connection") or "?", [0, 0, 0, 0])
        n, tok = row.get("attempts") or 0, (row.get("tokens_in") or 0) + (row.get("tokens_out") or 0)
        if row["t"] >= t - 86400:
            s[0], s[1] = s[0] + n, s[1] + tok
        if row["t"] >= ledger.month_start(t):
            s[2], s[3] = s[2] + n, s[3] + tok
    sent_rows = [f"<tr><td>{esc(c)}</td>" + "".join(f"<td class='num'>{v:,}</td>" for v in s) + "</tr>"
                 for c, s in sorted(sent.items())]
    cool_rows = []
    for alias, c in sorted(learn.cooldowns().items(), key=lambda kv: kv[1].get("until", "")):
        try:
            left = max(0, int((datetime.datetime.fromisoformat(c["until"]) - now).total_seconds() // 60))
        except (KeyError, ValueError, TypeError):
            left = 0
        cool_rows.append(f"<tr><td>{named(alias)}</td><td>{esc(catalog.connection_of(alias))}</td>"
                         f"<td class='num' data-sort='{left}'>{left} min</td>"
                         f"<td>{'stated by the provider' if c.get('stated') else 'default wait'}</td></tr>")
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
    return (
        "<div class='panel'><h2>Quota management</h2><p class='sub'>What is left on each connection and"
        " when it resets, from the rate-limit headers and 429 replies the built-in client sees, and what"
        " the bench sent itself, from its usage ledger; who is resting after a rate limit, and every"
        " limit known. The built-in client keeps to all of these. Calls through the llm CLI are in"
        " neither record. Quotas of OAuth credentials stay in CLI Proxy API's own console.</p></div>"
        "<div class='panel'><h3 class='chart-head'>What providers said is left · " + str(len(rec_rows)) + "</h3>"
        + ("<table><thead><tr>" + th("Connection") + th("Model") + th("Counts") + th("Left", True)
           + th("Of", True) + th("Resets") + th("From") + f"</tr></thead><tbody>{''.join(rec_rows)}</tbody></table>"
           if rec_rows else "<p class='muted'>Nothing in force: no provider has stated a limit whose reset"
                            " is still ahead.</p>")
        + "</div>"
        "<div class='panel'><h3 class='chart-head'>What the bench sent</h3><p class='sub'>Requests"
        " (retries included) and tokens per connection, from the usage ledger.</p>"
        + ("<table><thead><tr>" + th("Connection") + th("Requests, 24 h", True) + th("Tokens, 24 h", True)
           + th("Requests, this month", True) + th("Tokens, this month", True)
           + f"</tr></thead><tbody>{''.join(sent_rows)}</tbody></table>"
           if sent_rows else "<p class='muted'>No call in the ledger this month.</p>")
        + "</div>"
        "<div class='panel'><h3 class='chart-head'>Resting after a rate limit · " + str(len(cool_rows)) + "</h3>"
        + ("<table><thead><tr>" + th("Model") + th("Connection") + th("Back in", True) + th("Wait")
           + f"</tr></thead><tbody>{''.join(cool_rows)}</tbody></table>" if cool_rows
           else "<p class='muted'>None: every model is free to call.</p>")
        + "</div>"
        "<div class='panel'><h3 class='chart-head'>Known limits</h3><p class='sub'>How much each connection"
        " or model allows (tokens or requests per minute or day), so the bench can space its calls."
        " Two sources: what you enter per connection (Endpoints, Rate limits per connection), and what a"
        " provider states in a rate-limit error, which the bench writes down by itself. The list grows on"
        " its own as models meet their limits. A model's own limits (its popup) win, and learned ones"
        " only ever lower a limit.</p>"
        + ("<table><thead><tr>" + th("Connection or model") + "".join(th(k, True) for k in keys) + th("From")
           + f"</tr></thead><tbody>{''.join(lim_rows)}</tbody></table>" if lim_rows
           else "<p class='muted'>None known yet.</p>")
        + "</div>")


def _log_rows(path: pathlib.Path, limit: int = 200) -> list[str]:
    return [line for line in reversed(read(path).splitlines()) if line.strip()][:limit]


def logs_page() -> str:
    """Newest first, from the files that already exist. Gateway calls show who
    sat and how it went, never the prompt or the answers."""
    gw = []
    for line in _log_rows(DATA / "runs" / "gateway" / "calls.jsonl"):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        seats = ", ".join(f"{s.get('alias')} {s.get('status')}" for s in r.get("seats", []))
        gw.append(f"<tr><td class='mono'>{esc(r.get('at', '')[:16].replace('T', ' '))}</td>"
                  f"<td>{esc(r.get('model', ''))}</td><td>{esc(r.get('shape', ''))}</td><td>{esc(seats)}</td></tr>")
    changes = []
    for line in _log_rows(DATA / "config" / "changes.jsonl"):
        try:
            c = json.loads(line)
        except ValueError:
            continue
        changes.append(f"<tr><td class='mono'>{esc(c.get('at', '')[:16].replace('T', ' '))}</td>"
                       f"<td>{esc(c.get('by', ''))}</td><td>{esc(c.get('alias') or '')}</td>"
                       f"<td>{esc(c.get('field', ''))}</td><td>{esc(str(c.get('new') or ''))[:160]}</td></tr>")
    sched = "".join(f"<div class='mono log-line'>{esc(line)}</div>"
                    for line in _log_rows(DATA / "runs" / "scheduler.log"))
    onboard = "".join(f"<li class='mono log-line'>{esc(p.name)}</li>"
                      for p in sorted((DATA / "runs").glob("onboard-*.log"), reverse=True)[:30])

    def section(title, body, n, open_=False):
        return (f"<details class='panel'{' open' if open_ else ''}><summary><b>{esc(title)}</b>"
                f" <span class='muted'>· {n}</span></summary>{body}</details>")
    return (
        "<div class='panel'><input data-filter='page' placeholder='Filter every log on this page'"
        " aria-label='Filter logs'></div>"
        + section("Gateway calls", "<table><thead><tr>" + th("When") + th("Collection") + th("Shape")
                  + th("Seats") + f"</tr></thead><tbody>{''.join(gw)}</tbody></table>", len(gw), True)
        + section("Changes", "<table><thead><tr>" + th("When") + th("By") + th("Model") + th("What")
                  + th("New") + f"</tr></thead><tbody>{''.join(changes)}</tbody></table>", len(changes))
        + section("Scheduler", sched or "<p class='muted'>Nothing yet.</p>",
                  len(_log_rows(DATA / 'runs' / 'scheduler.log')))
        + section("Onboarding runs", f"<ul>{onboard}</ul>", onboard.count("<li")))


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


def info_page() -> str:
    import scheduler
    import client as model_client
    rows = [("Version", RELEASE), ("Date", RELEASE_DATE or "unknown"), ("Commit", COMMIT or "unknown"),
            ("Python", f"{sys.version.split()[0]} · {sys.executable}"),
            ("Folder", str(ROOT)), ("Data folder", str(DATA)), ("Endpoints", str(len(endpoints.load()))),
            ("Models on the bench", str(len(_chat_aliases()))),
            ("Scheduler", scheduler.settings()["mode"]), ("Model client", model_client.chosen()[0]),
            ("Theme", saved_theme())]
    body = "".join(f"<tr><th scope='row'>{esc(k)}</th><td class='mono'>{esc(v)}</td></tr>" for k, v in rows)
    return (f"<div class='panel'><h2>Info</h2><table class='facts'><tbody>{body}</tbody></table>"
            "<p class='muted'>Benchwarmers · by Wendell Research · MIT licence"
            " (<code>LICENSE</code>, credits in <code>NOTICE</code>). Every response carries"
            " <code>X-Benchwarmers-Version</code>.</p></div>")


def directory_json() -> str:
    directory = model_directory()
    facts = {a: catalog.info(a) for a, d in directory.items() if d.get("kind") != "decision"}
    options = {a: o for a, o in load_json(OPTIONS_PATH).items() if a in facts and isinstance(o, dict)}
    return json.dumps({"models": directory, "facts": facts, "options": options, "roles": model_roles.load()["roles"],
                       "retired": retired(), "onboard": onboard_issues(), "stamp": data_stamp()},
                      ensure_ascii=False).replace("</", "<\\/")


def editor() -> str:
    return (
        "<dialog id='model-dialog' aria-labelledby='m-title'>"
        "<h2 id='m-title'></h2><p class='served' id='m-served'></p>"
        "<div class='note' id='m-gone' hidden></div>"
        "<div class='note' id='m-onboard' hidden></div>"
        "<div id='m-editor'>"
        "<label class='field' for='m-alias'>Alias</label><input id='m-alias' spellcheck='false'>"
        "<p class='rules' id='m-alias-why'>The name <code>llm -m</code> takes and every record is"
        " keyed by. Letters, digits, dot, dash, underscore; unused, and never a retired name.</p>"
        "<p class='note' id='m-danger' hidden>Renaming rewrites the llm registry and records"
        " old → new in <code>alias-history.json</code>, so suite outputs, offloads, perf"
        " history and run notes keep following this model.</p>"
        "<label class='field' for='m-role'>Role</label><select id='m-role'></select>"
        "<p class='rules' id='m-role-why'></p>"
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
        "</div><div class='errors' id='f-errors' role='alert'></div><div class='done' id='f-done'></div>"
        "<div class='actions'><button class='btn' id='f-save' type='button'>Save facts</button></div>"
        "</details>"
        # How the bench calls it (serve.save_model_options): every caller, gateway, suite and probes.
        "<details id='m-opts' class='facts-box'><summary>Call options</summary>"
        "<label class='field' for='co-max'>Max tokens per answer</label>"
        "<input id='co-max' type='number' min='1' max='1000000'>"
        "<p class='rules'>The most one answer may use (<code>max_tokens</code>), on every call the bench"
        " makes to it. A reasoning model thinks out of the same budget and answers with an empty string"
        " when it runs out, which looks like a dead model: raise it then. Blank leaves it to the"
        " endpoint. <span id='co-ceiling'></span></p>"
        "<div class='errors' id='co-errors' role='alert'></div><div class='done' id='co-done'></div>"
        "<div class='actions'><button class='btn' id='co-save' type='button'>Save options</button></div>"
        "</details>"
        "<label class='check'><input type='checkbox' id='m-adhoc'> Ad hoc only</label>"
        "<p class='rules'>Never chosen by the bench on its own: no explorer seat, no router"
        " pick, no scheduled suite, perf or availability runs. Still called whenever you choose"
        " it: as a collection member (or merger), with <code>with</code>, or with"
        " <code>--model</code>. For metered, capped or trial keys.</p>"
        "</div>"
        "<div class='errors' id='m-errors' role='alert'></div><div class='done' id='m-done'></div>"
        "<div class='actions'><button class='btn' id='m-close'>Close</button>"
        "<button class='btn primary' id='m-restore' hidden>Restore</button>"
        "<button class='btn primary' id='m-save'>Save</button></div>"
        "<details class='remove' id='m-remove-box'><summary>Retire this model…</summary>"
        "<p class='rules'>Deregisters the alias, records it in <code>retired.json</code> so the"
        " name is never reused, takes it out of every collection it is a member or merger of,"
        " and clears its problems. Deletes no evidence, and Restore brings it back, into the"
        " collections that still exist. The daily check also retires models on its own when they are"
        " clearly gone.</p>"
        "<label class='field' for='m-reason'>Why <span class='muted'>(optional, recorded)</span></label>"
        "<input id='m-reason' placeholder='e.g. free tier withdrawn 2026-10-15'>"
        "<div class='actions'><button class='btn danger' id='m-remove'>Retire</button></div>"
        "</details></dialog>"
        # The page's own confirm (bench.js ask): window.confirm can be blocked.
        "<dialog id='ask-dialog' aria-labelledby='ask-text'>"
        "<p id='ask-text' style='white-space:pre-line'></p>"
        "<div class='actions'><button class='btn' id='ask-no' type='button'>Cancel</button>"
        "<button class='btn primary' id='ask-yes' type='button'>OK</button></div></dialog>"
        # onboarding
        "<dialog id='onboard-dialog' aria-labelledby='o-title'>"
        "<h2 id='o-title'>Onboard a model</h2>"
        "<div id='o-choose'>"
        "<p class='served'>Everything your endpoints serve that is not on the bench, retired or"
        " dismissed. Onboarding registers it, probes it (4 calls) and runs the internal"
        " suite: minutes, and it spends calls. Pick one or several: they run one at a time,"
        " with progress in the header. A model from somewhere else? Add its endpoint first.</p>"
        "<input type='search' id='o-find' placeholder='Search models or connections'"
        " aria-label='Search models'>"
        # How old the list is (serve.LISTED_FOR), and asking again now.
        "<p class='muted' id='o-age' hidden><span></span>"
        " <button class='linkish' type='button' id='o-refresh'>Refresh</button></p>"
        "<div class='picker' id='o-picker' role='group' aria-label='Served models'></div>"
        # serve.ONBOARD_MODES: how much each onboarding spends.
        "<label class='check'>Onboard as <select id='o-mode'>"
        "<option value='full'>Full: probe, then the suite now</option>"
        "<option value='quick'>Quick: probe now, the suite through the scheduled passes</option>"
        "<option value='quick-adhoc'>Quick + ad hoc: probe only; the bench never calls it on its own"
        " (metered, capped or trial keys)</option></select></label>"
        "<label class='check'><input type='checkbox' id='o-vision'> Vision model: a seat only for questions"
        " about images, and its suite is the vision tasks</label>"
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
        # one Discover model: what is known, and the routes to add it by (bench.js fills it)
        "<dialog id='discover-dialog' aria-labelledby='d-title'>"
        "<h2 id='d-title'></h2><p class='served' id='d-served'></p>"
        "<div class='profile' id='d-profile'></div>"
        "<h3 class='form-head'>Routes on this install</h3>"
        "<div id='d-routes'></div>"
        "<div class='errors' id='d-errors' role='alert'></div>"
        "<div class='actions'><button class='btn' id='d-close'>Close</button></div>"
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
        if path == "/login":
            return self._page(login_page()) if guard.auth_on() else self._redirect("/")
        callback = self.path.startswith((connect.CALLBACK + "?", connect.CALLBACK + "/"))
        if not callback and not self._admitted(path):
            return
        if self.path.startswith("/api/onboard?"):
            from urllib.parse import parse_qs, urlparse
            log = parse_qs(urlparse(self.path).query).get("log", [""])[0]
            self._json(200, onboard_status(log))
            return
        if self.path in ("/api/candidates", "/api/candidates?fresh=1"):
            self._json(200, onboard_candidates(fresh=self.path.endswith("fresh=1")))
            return
        if self.path == "/api/onboard-queue":
            self._json(200, onboard_queue())
            return
        if self.path == "/api/endpoints":
            self._json(200, endpoint_list())
            return
        if self.path == "/api/connect":
            self._json(200, connect_list())
            return
        if callback:
            # The provider sends the browser back here. A GET that saves, as
            # OAuth callbacks are: the state is single-use and unguessable, and
            # only a sign-in this dashboard started has one. No login cookie:
            # SameSite=Strict keeps it off the provider's cross-site redirect.
            from urllib.parse import parse_qsl, urlparse
            query = dict(parse_qsl(urlparse(self.path).query))
            query["state"] = connect.callback_id(self.path, query)
            try:
                out = connect_save(connect.finish(query))
            except connect.ConnectError as exc:
                out = {"ok": False, "errors": [str(exc)], "state": "error"}
            except Exception as exc:                   # noqa: BLE001 -- the tab must still hear something
                out = {"ok": False, "errors": [f"could not save it ({type(exc).__name__}: {exc})"], "state": "error"}
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
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            res = discover_model(q.get("provider", [""])[0], q.get("model", [""])[0])
            self._json(200 if res["ok"] else 404, res)
            return
        if self.path.startswith("/api/gateway-seat?"):
            from urllib.parse import parse_qs, urlparse
            call = parse_qs(urlparse(self.path).query).get("call", [""])[0]
            res = gateway_seat(call)
            self._json(200 if res["ok"] else 404, res)
            return
        if self.path == "/api/stamp":
            self._json(200, {"stamp": data_stamp()})
            return
        if self.path.startswith(("/fonts/", "/ui/")):
            # Only the named font files and ASSETS; anything else is a 404, so
            # this cannot be walked to other paths.
            name = self.path.split("/", 2)[2]
            path = font_file(name) if self.path.startswith("/fonts/") else \
                (UI / name if name in ASSETS and (UI / name).exists() else None)
            if not path:
                self.send_error(404)
                return
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ASSETS.get(name, "font/woff2"))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(data)
            return
        from urllib.parse import parse_qs, urlparse
        theme = parse_qs(urlparse(self.path).query).get("theme", [None])[0]   # a preview; else the saved one
        try:
            body = render(theme).encode("utf-8")
        except RuntimeError as exc:                    # endpoints.json unreadable: say which and why
            body = f"<!doctype html><meta charset='utf-8'><title>Benchwarmers</title><p>{esc(exc)}</p>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):                                   # noqa: N802
        """One endpoint, one operation: change a model's role or alias.

        Same-origin only in practice (127.0.0.1, no CORS headers), JSON in,
        JSON out, and every rule lives in `change_model` — this method only
        parses and reports.
        """
        path = self.path.split("?", 1)[0]
        if guard.gateway_route(path):
            return super().do_POST()
        if path == "/login":
            return self._login()
        ops = {"/api/model": lambda p: change_model(str(p.get("alias", "")),
                                                    p.get("new_alias"), p.get("new_role"),
                                                    new_ad_hoc=p.get("new_ad_hoc"),
                                                    new_question_types=p.get("new_question_types")),
               "/api/remove": lambda p: remove_model(str(p.get("alias", "")), str(p.get("reason", ""))),
               "/api/restore": lambda p: restore_model(str(p.get("alias", ""))),
               "/api/retry": lambda p: retry_model(str(p.get("alias") or "")),
               "/api/discover/add": lambda p: discover_add(str(p.get("connection") or ""),
                                                           str(p.get("model") or ""),
                                                           endpoint=str(p.get("endpoint") or "")),
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
                                                            str(p.get("merger") or "")),
               "/api/collection/remove": lambda p: delete_collection(str(p.get("name") or "")),
               "/api/model-facts": lambda p: save_model_facts(str(p.get("alias") or ""), p.get("facts")),
               "/api/model-options": lambda p: save_model_options(str(p.get("alias") or ""), p.get("max_tokens")),
               "/api/scheduler": lambda p: save_scheduler(str(p.get("mode") or ""), p.get("jobs")),
               "/api/client": lambda p: save_client(str(p.get("client") or "")),
               "/api/own-client-free": lambda p: save_own_client_free(p.get("providers")),
               "/api/connection-limits": lambda p: save_connection_limits(str(p.get("connection") or ""),
                                                                          p.get("limits")),
               "/api/verdict": lambda p: record_verdict(str(p.get("call") or ""), p.get("useful"),
                                                        str(p.get("note") or ""), p.get("real"),
                                                        p.get("false")),
               "/api/gateway-settings": lambda p: save_gateway_settings(
                   p.get("max_calls"), p.get("max_explorers")),
               "/api/gateway-key": lambda p: make_gateway_key(str(p.get("name") or ""), p.get("collections"),
                                                              p.get("rpm")),
               "/api/gateway-key/remove": lambda p: revoke_gateway_key(str(p.get("name") or "")),
               "/api/theme": lambda p: save_theme(str(p.get("theme") or ""))}
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
        """POST /login {"token"}: the login cookie, for the admin token only."""
        try:
            length = guard.check(self.headers, PORT, post=True, max_body=MAX_BODY)
            given = json.loads(self.rfile.read(length) or b"{}").get("token")
        except guard.Refused as r:
            return self._json(r.status, {"ok": False, "errors": [str(r)]})
        except (ValueError, AttributeError):
            return self._json(400, {"ok": False, "errors": ['send {"token": "..."}']})
        if not (isinstance(given, str) and guard.login(given)):
            return self._json(401, {"ok": False, "errors": ["that is not the admin token"]})
        self.who = "admin"
        self._json(200, {"ok": True, "errors": []}, cookie=guard.session_cookie())

    def _redirect(self, where: str) -> None:
        self.send_response(303)
        self.send_header("Location", where)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _page(self, html_text: str) -> None:
        body = html_text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: dict, cookie: str = "", headers: dict | None = None) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
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
    address = guard.bind()
    guard.startup(address)                             # beyond this machine, only with an admin token
    shown = "localhost" if address == "127.0.0.1" else address
    print(f"Benchwarmers on http://{shown}:{PORT}{'  (login on)' if guard.auth_on() else ''}  (ctrl-c to stop)")
    import paths
    paths.make()                                       # a fresh data folder's layout, before any writer
    import scheduler
    scheduler.start()                                  # off unless config/scheduler.json says otherwise
    ThreadingHTTPServer((address, PORT), Handler).serve_forever()
