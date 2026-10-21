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
(same popup, typed back to confirm, reason required): deregistered and
recorded in `config/retired.json`; no evidence is deleted. And ONBOARDING
(header button): `scripts/onboard.py` started in the background with its log
tailed here. The upstream model id is never editable. Nothing else can write.

Every table carries a KEY beside it saying what each column measures. A
benchmark nobody can read is a benchmark nobody uses, and these numbers each
mean something narrower than their name suggests — an efficiency below 1.0 in
particular means the model arrived while wandering, which a success rate hides.

It binds to 127.0.0.1 on purpose. Nothing here is secret, but a benchmark
dashboard is not a thing to put on a network by accident.
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "metrics"))
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
PORT = 8122

# Once, at import: roster() used to insert two entries on every page render.
for _d in ("metrics", "suite", "scripts"):
    sys.path.insert(0, str(ROOT / _d))
from aliases import current as current_alias  # noqa: E402
import guard  # noqa: E402


UI = ROOT / "ui"

# THE THEME IS A FILE IN THIS REPO. Until 2026-10-19 the colours and faces were
# read live from the AI site's stylesheet in a sibling repository, which a
# public install does not have; the Wendell Research palette is now
# ui/themes/rainforest.css (the default theme) and the Chivo faces (OFL, licence
# beside them) are in ui/fonts.
THEMES = UI / "themes"
DEFAULT_THEME = "rainforest"
FONTS = {f"{family}-latin-{weight}-normal.woff2": family
         for family, weights in (("chivo", (400, 500, 700, 800)),
                                 ("chivo-mono", (400, 500)))
         for weight in weights}


def themes() -> list[str]:
    return sorted(p.stem for p in THEMES.glob("*.css"))


def _tokens(name: str) -> dict[str, str]:
    block = re.search(r":root\s*\{(.*?)\}", read(THEMES / f"{name}.css"), re.S)
    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", block.group(1))) if block else {}


def brand_css(theme: str = DEFAULT_THEME) -> str:
    """`:root` tokens and @font-face rules. The default theme supplies every
    token (faces, measure, radius); another theme lays its colours over it.
    Colours only (RELEASE-PLAN decision 7); scripts/check_themes.py checks
    each one's contrast. Until the Settings page, `?theme=<name>` previews one."""
    merged = _tokens(DEFAULT_THEME)
    if theme != DEFAULT_THEME and theme in themes():
        merged.update(_tokens(theme))
    tokens = "".join(f"{name}:{value.strip()};" for name, value in merged.items())
    faces = "".join(
        f"@font-face{{font-family:'{'Chivo Mono' if fam == 'chivo-mono' else 'Chivo'}';"
        f"font-weight:{name.split('-')[-2]};font-style:normal;font-display:swap;"
        f"src:url('/fonts/{name}') format('woff2');}}"
        for name, fam in FONTS.items())
    return f":root{{{tokens}}}{faces}"


def font_file(name: str) -> pathlib.Path | None:
    path = (UI / "fonts" / name) if name in FONTS else None
    return path if path and path.exists() else None


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
    """
    row = re.compile(r"^\|\s*([\w-]+)\s*\|\s*`([^`]+)`\s*\|\s*([^|]+?)\s*\|"
                     r"\s*([^|]*?)\s*\|\s*([\d.]+)\s*\|")
    scores: dict[tuple[str, str], dict] = {}
    for path in sorted(DATA.glob("runs/suite-*.md")):
        for line in read(path).splitlines():
            if (m := row.match(line.strip())):
                task, model, found, invented, seconds = m.groups()
                model = current_alias(model)
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
    """The Open WebUI connection a model arrives through, by its llm alias."""
    tags = load_json(DATA / "config" / "provider-tags.json")
    for model_name, alias in registered().items():
        if alias == llm_id:
            return tags.get(model_name, "")
    return tags.get(llm_id, "")


RUN_LABELS = {"availability": "Availability", "deprecations": "Deprecations",
              "suite-free": "Suite (free tier)", "suite": "Suite (1 model)", "perf": "Performance"}


def next_runs() -> list[tuple[str, str]]:
    """When the scheduled checks fire next, from the bench's own scheduler
    (metrics/scheduler.py). It used to ask Windows, five `schtasks` calls per
    page render with a US date format, which no other system has. The times
    are the same either way: during the switch-over the Windows tasks run the
    jobs and the scheduler, log-only, keeps the identical schedule."""
    import scheduler
    conf = scheduler.settings()
    if conf["mode"] == "off":
        return [("Schedule", "off (Settings)")]
    now = datetime.datetime.now()
    on = scheduler.enabled(conf)
    out = [(label, f"{scheduler.next_due(scheduler.JOBS[job][1], now):%a %H:%M}")
           for job, label in RUN_LABELS.items() if job in on]
    # Deliberate runs, not timed ones. The suite job rotates ONE paid model
    # every six hours and suite-free one free model a day; a full sweep of
    # every model is a different and much larger thing.
    out.append(("Suite (full) / Floor / Tool use", "on demand only"))
    return out


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
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


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
    known_roles = set(roles.get("roles", {}).keys()) or {"general", "code", "image"}
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


def remove_model(alias: str, typed: str, reason: str, who: str = "ui") -> dict:
    """Retire a model from the bench by hand.

    The daily check (`scripts/check_deprecations.py`, via `auto_retire.py`)
    calls this too, with `who="auto"`, when a model is clearly gone; this is
    also the button for a person who knows sooner. It deregisters the alias from the llm registry (and re-syncs),
    drops it from the role and direct-endpoint configs, records it in
    `config/retired.json` — so the alias is never reused, and onboarding the
    same served model again is a deliberate act — clears that model's alerts,
    and logs the change. It deletes NO evidence: suite outputs, offloads,
    perf history and run notes stay exactly as they are.
    """
    directory = model_directory()
    errors = []
    if alias not in directory:
        return {"ok": False, "errors": [f"{alias!r} is not a registered alias"], "changed": [], "sync": ""}
    if (typed or "").strip() != alias:
        errors.append("type the alias exactly to confirm removal")
    if len((reason or "").strip()) < 5:
        errors.append("give a reason (it is recorded)")
    if errors:
        return {"ok": False, "errors": errors, "changed": [], "sync": ""}

    served = directory[alias]["served"]
    stamp = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    changed = []

    if directory[alias].get("kind") == "decision":
        # Its whole entry goes into retired.json, so Restore puts back the
        # same endpoint, route and question types without a new probe.
        data = decide.load()
        entry = data.pop(alias)
        _write_atomic(decide.FILE, _dump(data))
        retired = load_json(RETIRED_PATH)
        retired[alias] = {"served": served, "tag": directory[alias]["tag"], "role": "decision",
                          "at": stamp, "by": who, "why": reason.strip(),
                          "endpoint": entry["endpoint"], "decision": entry,
                          **({"ad_hoc": True} if directory[alias]["ad_hoc"] else {})}
        _write_atomic(RETIRED_PATH, _dump(retired))
        roles = roles_config()
        roles.get("models", {}).pop(alias, None)
        roles["ad_hoc"] = [a for a in roles.get("ad_hoc", []) if a != alias]
        _write_atomic(ROLES_PATH, _dump(roles))
        _log_change(stamp, who, alias, "removed", served, reason.strip())
        return {"ok": True, "errors": [], "sync": "",
                "changed": ["config/decision-models.json", "config/retired.json",
                            "config/model-roles.json"]}

    # 1. the registry block — model_id line plus its indented lines
    text = read(YAML_PATH)
    block = re.compile(rf"(?:^|\n)\n?-\s*model_id:\s*{re.escape(alias)}\s*\n(?:[ \t]+.*\n?)*")
    text, n = block.subn("\n", text)
    if n != 1:
        return {"ok": False, "changed": [], "sync": "",
                "errors": [f"expected one registry block for {alias!r}, found {n}; nothing removed"]}
    _write_atomic(YAML_PATH, text.rstrip("\n") + "\n")
    changed.append("config/extra-openai-models.yaml")
    # 2. the memory of it -- enough to restore it exactly: endpoint, options too
    retired = load_json(RETIRED_PATH)
    options = load_json(OPTIONS_PATH)
    retired[alias] = {"served": served, "tag": directory[alias]["tag"],
                      "role": directory[alias]["role"], "at": stamp, "by": who,
                      "why": reason.strip(), "endpoint": directory[alias]["endpoint"],
                      **({"ad_hoc": True} if directory[alias]["ad_hoc"] else {}),
                      **({"options": options[alias]} if alias in options else {})}
    _write_atomic(RETIRED_PATH, _dump(retired))
    changed.append("config/retired.json")
    # 3. configs keyed by alias
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
    # 4. its alerts — the action they asked for has been taken
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
                                 0.0, 1, 0, 0, 0, "", "retry from the dashboard"])
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
        del retired[alias]
        _write_atomic(RETIRED_PATH, _dump(retired))
        _log_change(datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                    who, alias, "restored", None, entry["served"])
        return {"ok": True, "errors": [], "sync": "",
                "changed": ["config/decision-models.json", "config/model-roles.json",
                            "config/retired.json"]}
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
    del retired[alias]
    _write_atomic(RETIRED_PATH, _dump(retired))
    changed.append("config/retired.json")
    _log_change(datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                who, alias, "restored", None, entry["served"])
    return {"ok": True, "errors": [], "changed": changed, "sync": _sync_cli()}


DISMISSED_PATH = DATA / "config" / "dismissed.json"


def onboard_candidates() -> dict:
    """What every endpoint serves that the bench could onboard, for the picker.

    Served, minus registered, minus retired, minus dismissed (those come back
    separately so a dismissal can be undone), each endpoint asked separately
    so one being down only empties its own group. The live calls this page
    makes happen only when the picker opens: a dashboard that needs the
    network to render would be down exactly when you want to look at it.
    """
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
        try:
            return endpoints.models(name)
        except Exception as exc:                       # noqa: BLE001
            return exc
    names = list(endpoints.load())
    with ThreadPoolExecutor(max_workers=len(names)) as pool:   # all at once, not in turn
        answers = dict(zip(names, pool.map(ask, names)))
    for name, live in answers.items():
        if isinstance(live, Exception):
            errors.append(f"cannot reach {name}: {live}")
            continue
        default = name == endpoints.default()
        for served, entry in sorted(live.items()):
            key = endpoints.snapshot_key(served, name)
            if key in taken:
                continue
            # On Open WebUI the group is the connection behind it; any other
            # endpoint is its own group.
            group = (tags.get(served, "").strip("?").split("/")[0] or (
                "local" if entry.get("connection_type") == "local" else "unknown connection")
            ) if default else f"{name} (direct)"
            row = {"served": served, "endpoint": name, "key": key, "group": group,
                   "new": default and served not in first_seen}
            (gone if key in dismissed else open_).append(row)
    if not open_ and not gone and errors:
        return {"ok": False, "errors": errors}
    return {"ok": True, "errors": errors, "candidates": open_, "dismissed": gone}


def dismiss(served: str, undo: bool = False, who: str = "ui") -> dict:
    """Stop offering a served model in the picker (image models, arena, …)."""
    served = (served or "").strip()
    if not served:
        return {"ok": False, "errors": ["which model?"]}
    data = load_json(DISMISSED_PATH)
    stamp = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    if undo:
        data.pop(served, None)
    else:
        data[served] = {"at": stamp, "by": who}
    _write_atomic(DISMISSED_PATH, _dump(data))
    _log_change(stamp, who, "", "undismissed" if undo else "dismissed", None, served)
    return {"ok": True, "errors": []}


def endpoint_list() -> dict:
    """Every endpoint, and whether its key is set -- never the key itself."""
    counts: dict[str, int] = {}
    for r in endpoints.registry():
        counts[r["endpoint"]] = counts.get(r["endpoint"], 0) + 1
    rows = [{"name": n, "base": e["base"], "default": n == endpoints.default(),
             "key_set": bool(endpoints.key(n)), "models": counts.get(n, 0)}
            for n, e in endpoints.load().items()]
    return {"ok": True, "errors": [], "endpoints": rows}


def save_endpoint(name: str, base: str, secret: str, who: str = "ui") -> dict:
    """Add an endpoint, or change one's key. Tested before anything is saved.

    The key goes where `llm keys set` puts it (the CLI's own keys.json), never
    into this repository, the change log or a response. It is sent to the base
    URL typed here, which is the point: that is the endpoint being added.
    """
    name, base, secret = (name or "").strip().lower(), (base or "").strip().rstrip("/"), (secret or "").strip()
    data = endpoints.load()
    errors = []
    if not endpoints.NAME.match(name):
        errors.append("name: 2-32 lower-case letters, digits or '-', e.g. openrouter")
    if not re.fullmatch(r"https?://[^\s\"'<>]+", base):
        errors.append("base URL must start http:// or https://, e.g. https://openrouter.ai/api/v1")
    on_it = [r["alias"] for r in endpoints.registry() if r["endpoint"] == name]
    if name in data and base != data[name]["base"].rstrip("/") and on_it:
        errors.append(f"{len(on_it)} model(s) are registered on {name} at its current URL;"
                      " a new URL would strand them. Add it under a new name instead")
    if errors:
        return {"ok": False, "errors": errors}
    entry = dict(data.get(name) or {"key": name})
    entry["base"] = base
    headers = dict(entry.get("headers") or {})
    if (secret := secret or endpoints.keys().get(entry["key"], "")):
        headers["Authorization"] = f"Bearer {secret}"
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(urllib.request.Request(base + "/models", headers=headers),
                                    timeout=30) as response:
            count = len(json.load(response).get("data", []))
    except urllib.error.HTTPError as exc:
        return {"ok": False, "errors": [f"{base}/models answered {exc.code}"
                                        + (" — check the key" if exc.code in (401, 403) else "")
                                        + "; nothing saved"]}
    except Exception as exc:                           # noqa: BLE001
        return {"ok": False, "errors": [f"could not list {base}/models ({type(exc).__name__}:"
                                        f" {exc}); nothing saved"]}
    # A LISTING PROVES THE URL, NOT ALWAYS THE KEY. OpenRouter lists its
    # models to anyone, so a wrong key sails through the test above. Asked
    # once without a key: if that works too, say plainly the key is unproven.
    note = ""
    if secret:
        try:
            with urllib.request.urlopen(base + "/models", timeout=15):
                note = (" It lists models without a key, so the key itself is only"
                        " checked by the first call to one of its models.")
        except Exception:                              # noqa: BLE001
            note = " The key was checked: the list needs it."
    if secret and secret != endpoints.keys().get(entry["key"]):
        endpoints.set_key(entry["key"], secret)
    data = dict(data)
    data[name] = entry
    _write_atomic(endpoints.FILE, _dump(data))
    _log_change(datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                who, "", "endpoint", None, f"{name} {base}")
    return {"ok": True, "errors": [], "served": count, "note": note.strip()}


def remove_endpoint(name: str, who: str = "ui") -> dict:
    data = endpoints.load()
    on_it = [r["alias"] for r in endpoints.registry() if r["endpoint"] == name]
    errors = ([f"no endpoint {name!r}"] if name not in data else
              ["the default endpoint cannot be removed"] if name == endpoints.default() else
              [f"retire its models first: {', '.join(on_it)}"] if on_it else [])
    if errors:
        return {"ok": False, "errors": errors}
    data = {n: e for n, e in data.items() if n != name}
    _write_atomic(endpoints.FILE, _dump(data))
    _log_change(datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                who, "", "endpoint removed", name, None)
    return {"ok": True, "errors": [], "note": f"its key stays in the llm key store;"
                                              f" `llm keys` lists it"}


def _clean_limits(raw) -> tuple[dict, list[str]]:
    """rpm/rpd/tpm/tpd as positive whole numbers; blank clears one."""
    out, errors = {}, []
    for k in catalog.LIMIT_KEYS:
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


def save_connection_limits(connection: str, limits: dict, who: str = "ui") -> dict:
    connection = (connection or "").strip()
    known = {catalog.connection_of(a) for a in _chat_aliases()}
    if connection not in known:
        return {"ok": False, "errors": [f"{connection!r} is not a connection any model uses;"
                                        f" known: {', '.join(sorted(known))}"]}
    clean, errors = _clean_limits(limits)
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
    return {"ok": True, "errors": [], "keys": list(catalog.LIMIT_KEYS),
            "connections": [{"name": c, "models": sorted(m), "limits": lim.get(c, {})}
                            for c, m in sorted(by.items())]}


def _chat_aliases() -> list[str]:
    return sorted(a for a, d in model_directory().items() if d.get("kind") != "decision")


def collections_view() -> dict:
    """Collections, gateway settings, and what a collection may hold."""
    s = bench_collections.settings()
    rows = [{"name": n, **bench_collections.described(n, c, s)}
            for n, c in sorted(bench_collections.load().items())]
    shapes = [k for k in roles_config().get("shapes", {}) if k != "image"]
    # Tags are read only by callers (GET /v1/models, and the refusal when a
    # request names no collection), to choose a collection for a task. The
    # suggestions: the bench's task shapes, a few purposes, and every tag a
    # collection already uses, so the same idea is spelled the same way.
    used = [t for r in rows for t in r.get("tags") or []]
    suggestions = list(dict.fromkeys(shapes + list(TAG_PURPOSES) + used))
    return {"ok": True, "errors": [], "collections": rows, "settings": s,
            "shapes": shapes, "models": _chat_aliases(), "tag_suggestions": suggestions}


TAG_PURPOSES = ("second-opinion", "security", "long-context", "fast", "free")


def save_collection(name: str, members, shape: str, description: str, tags=None,
                    who: str = "ui") -> dict:
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
    shapes = [k for k in roles_config().get("shapes", {}) if k != "image"]
    if shape and shape not in shapes:
        errors.append(f"shape must be one of {', '.join(shapes)}, or empty for automatic")
    if len(description) > 300:
        errors.append("description: at most 300 characters; say what the collection is for")
    bad = [t for t in tags if not bench_collections.TAG.match(t)]
    if bad:
        errors.append(f"tags are lower-case words joined by '-', up to 24 characters: {', '.join(bad)}")
    if len(tags) > 8:
        errors.append("at most 8 tags")
    if bench_collections.unreadable(bench_collections.FILE):
        errors.append("config/collections.json is not valid JSON; fix it by hand first,"
                      " or this save would replace every collection in it")
    if errors:
        return {"ok": False, "errors": errors}
    data = bench_collections.load()
    old = data.get(name)
    data[name] = {"members": members, "shape": shape, "description": description, "tags": tags}
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


def start_onboard(served: str, endpoint: str = "", who: str = "ui") -> dict:
    """Kick off `scripts/onboard.py` for a served model, detached, logging to runs/.

    The script does its own checks in the right order (is it served, derive
    the alias, register, probe, suite) and refuses at the first one that
    fails; this only refuses what can be known without a call — a name that is
    empty, has spaces, is already registered, or was retired from here — and
    hands the rest to the script. It runs for minutes and spends calls, so it
    runs in the background and the page tails its log.
    """
    served = (served or "").strip()
    endpoint = (endpoint or "").strip() or endpoints.default()
    errors = []
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
    if errors:
        return {"ok": False, "errors": errors}
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", served)
    log = DATA / "runs" / f"onboard-{stamp}-{safe}.log"
    from run import bench_python
    cmd = [bench_python(), str(ROOT / "scripts" / "onboard.py"), served, "--endpoint", endpoint]
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
                            env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"})

    def sentinel() -> None:
        # The process outlives this request and nothing else reports its exit.
        # ponytail: lost if serve.py restarts mid-run; the log still has the steps.
        code = proc.wait()
        out.write(f"__ONBOARD_EXIT__ {code}\n")
        out.close()

    threading.Thread(target=sentinel, daemon=True).start()
    _log_change(datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                who, "", "onboard", None, served)
    return {"ok": True, "errors": [], "log": log.name}


def discover_add(connection: str, model: str, who: str = "ui") -> dict:
    """From the Discover tab: put a model into Open WebUI as it is otherwise done by hand
    (metrics/owui.py: the connection's Model IDs, a public record with the
    connection's tag and logo), then the normal onboarding. Only for a
    provider Open WebUI is already connected to."""
    if not re.fullmatch(r"[\w./:@+-]{1,200}", model or ""):
        return {"ok": False, "errors": ["the model id is the provider's id, e.g. vendor/model:free"]}
    if not re.fullmatch(r"[\w.-]{1,80}", connection or ""):
        return {"ok": False, "errors": ["choose a connection"]}
    import owui
    try:
        served, steps = owui.add(connection, model)
    except ValueError as exc:
        return {"ok": False, "errors": [str(exc)]}
    except Exception as exc:                           # noqa: BLE001
        return {"ok": False, "errors": [f"Open WebUI refused: {type(exc).__name__}: {exc}"]}
    _log_change(datetime.datetime.now().astimezone().isoformat(timespec="seconds"), who, "",
                "openwebui-add", None, f"{served} on {connection}")
    started = start_onboard(served, owui.ENDPOINT, who)
    return {**started, "served": served, "steps": steps,
            "errors": started["errors"] + ([] if started["ok"] else
                                          [f"Open WebUI now has {served}; onboard it from the Onboard dialog"])}


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
    for served, alias in registered().items():
        row = latest.get(alias)
        if alias in pending:
            days = re.search(r"in (\d+) day", pending[alias].get("message", ""))
            out[alias] = (f"retiring in {days.group(1)}d" if days else "retiring",
                          "err", pending[alias].get("action", ""))
        elif served in missing:
            out[alias] = ("not served", "err", f"missing from Open WebUI since {missing[served]}")
        elif row is None:
            out[alias] = ("untested", "", "no probe yet")
        elif int(row.get("answered") or 0) > 0:
            out[alias] = ("healthy", "ok", "answered its last probe")
        else:
            cls = row.get("failure") or ("timeout" if int(row.get("timeout") or 0) else "other")
            label, css, reading = STATUS.get(cls, STATUS["other"])
            out[alias] = (label, css, row.get("detail") or reading)
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


def charts_data() -> dict:
    """Everything the Benchmarks charts draw, for bench.js. Registered models
    only: the tables keep retired ones behind "Show retired", a chart has no toggle."""
    import route
    import wrong
    live = on_bench()
    recall, wr = route.suite_recall(), wrong.table()
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
            "work": work_rows(), **perf_charts()}


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
    import wrong
    rows = {m: r["hunt"] for m, r in wrong.table().items() if m in current() and any(r["hunt"].values())}
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
      <dt>(tag)</dt><dd>The Open WebUI connection it arrives through. Models
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
    rows = wrong.table()
    live = current()
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
            rate, meter = -1, "<span class='muted'>no tasks yet</span>"
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
    return (overview_card() + bughunt_card() + work_card() + wrong_card() + suite_card() + perf_card() + external_card()
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
                + th("p95", True) + th("ch/s", True) + th("Outcomes")
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


def render(theme: str | None = None) -> str:
    theme = theme or saved_theme()
    runs = "".join(f"<span>{esc(label)} <b>{esc(when)}</b></span>"
                   for label, when in next_runs()) or "<span>schedule unavailable</span>"
    return (
        "<!doctype html><html lang='en'><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Benchwarmers</title>"
        f"<style>{brand_css(theme)}{read(UI / 'bench.css')}</style>"
        "<header><div><h1><span>Benchwarmers</span></h1>"
        # The credit is fixed, not a setting (RELEASE-PLAN decision 7).
        "<p class='sub'>by Wendell Research · ask the whole bench, keep every answer, learn who to trust</p>"
        f"<div class='runs' aria-label='Next scheduled runs'>{runs}</div></div>"
        "<div class='head-actions'>"
        "<button class='btn fresh' id='fresh' hidden>New results · Reload</button>"
        "<button class='btn primary' id='onboard-open'>Onboard a model</button></div></header>"
        # What needs a person first, then the pages; endpoints are managed from
        # Connections now (the header button moved there).
        f"<main>{problems()}<div class='layout'>{tabs()}</div></main>{editor()}"
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
    body = ["<table><thead><tr>", th("When"), th("Collection"), th("Explorer shape"),
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
        owed += row_owed
        body.append(
            f"<tr><td class='mono' data-sort='{esc(r.get('at', ''))}'>{esc(r.get('at', '')[:16].replace('T', ' '))}</td>"
            f"<td class='mono'>{esc(r.get('model', ''))}</td>"
            f"<td title='{esc(r.get('shape_by', ''))}'>{esc(r.get('shape', ''))}</td>"
            f"<td>{' '.join(chips)}</td>"
            f"<td class='num{' muted' if not row_owed else ''}'>{row_owed}</td></tr>")
    if not records:
        body.append("<tr><td colspan='5' class='muted'>No gateway requests yet. Run"
                    " <code>python gateway.py</code> and call a collection.</td></tr>")
    body.append("</tbody></table>")
    key = """<dl>
      <dt>Seats</dt><dd>Every model that answered the request, members first, then
        explorers. Green answered, amber came back empty, red failed. ✓ judged
        useful, ✗ judged not useful; no mark means the verdict is still owed.
        Hover a chip for its seat, timing and call id; click it to read the answer
        and record a verdict.</dd>
      <dt>Owed</dt><dd>Seats without a verdict. Each is recorded as an unjudged
        offload, so rotation still counts it; a verdict tells the router whether
        it helped. The orchestrator that called can also record one:
        <code>python metrics/offload.py verdict &lt;call id&gt; --useful</code>.</dd>
      <dt>Explorer shape</dt><dd>Which models could fill explorer seats; hover for
        how it was chosen (set on the collection, or a decision model's label).</dd>
    </dl>"""
    return card("Gateway", f"The last {len(records)} bench-gateway requests:"
                f" {seats_total} seats, {owed} verdict{'s' * (owed != 1)} owed.",
                "".join(body), key)


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
    the daily feeds, and the registry and endpoints (an onboarding moves a
    model to "on bench" and a new endpoint adds routes)."""
    import discover
    import sources
    paths = [sources.CACHE / f"{n}.json" for n in sources.FEEDS] + [endpoints.YAML, endpoints.FILE]
    stamp = tuple(p.stat().st_mtime if p.exists() else 0 for p in paths)
    if _DISCOVER.get("stamp") != stamp:
        _DISCOVER.update(stamp=stamp, rows=discover.build())
    return _DISCOVER["rows"]


def discover_model(provider: str, model: str) -> dict:
    """One Discover row for its popup, with every route this install has to
    the same model: through each provider that offers it (`norm`, as
    discover.py matches them), via Open WebUI and via an endpoint of its own."""
    rows = _discover_rows()
    row = next((r for r in rows if r["provider"] == provider and r["model"] == model), None)
    if row is None:
        return {"ok": False, "errors": [f"Discover has no {model!r} from {provider!r}; reload the page"]}
    same = [row] + [r for r in rows if r["norm"] == row["norm"] and r is not row]
    routes = [{**x, "provider": r["provider_name"], "model": r["model"], "free": r["free"],
               "cost": r.get("cost"), "limits": r.get("limits")}
              for r in same for x in r["routes"]]
    return {"ok": True, "errors": [], "model": row, "routes": routes}


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
        " asks for <code>bench/&lt;name&gt;</code> at <code>http://127.0.0.1:8123/v1</code>;"
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
        "<label class='field'>Members</label><div id='c-members' class='members'></div>"
        "<p class='rules' id='c-count'></p>"
        "<label class='field' for='c-shape'>Explorer shape</label><select id='c-shape'></select>"
        "<p class='rules'>Which models may fill the explorer seats. Automatic asks a decision"
        " model to label each prompt when one is onboarded, else general.</p>"
        "<label class='field' for='c-desc'>Description</label>"
        "<textarea id='c-desc' rows='2' maxlength='300'"
        " placeholder='e.g. Adversarial code review: finding bugs and broken guarantees in a diff'></textarea>"
        "<label class='field' for='c-tags'>Tags</label>"
        "<input id='c-tags' spellcheck='false' autocomplete='off' placeholder='e.g. review, code, security'>"
        "<div class='tag-picks' id='c-tag-picks' aria-label='Suggested tags'></div>"
        "<p class='rules'>Only callers read these: an agent asking <code>GET /v1/models</code> sees"
        " each collection's description and tags and picks one for its task. Click a suggestion"
        " or type your own: comma-separated lower-case words, up to 8.</p>"
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
                         ("logs", "Logs", "logs_page"))),
            ("Gateway", (("collections", "Collections", "collections_and_gateway"),
                         ("endpoints", "Endpoints", "endpoints_page"),
                         ("quotas", "Quota management", "quotas_page"),
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


def settings_page() -> str:
    """Theme (saved), the scheduler and the model client (shown, changed in
    their config files for now: switching either for real is a deliberate
    step, see each note)."""
    import scheduler
    current = saved_theme()
    swatch = ("--paper", "--band", "--ink", "--brand", "--primary", "--signal", "--link", "--err")
    cards = []
    for name in themes():
        merged = {**_tokens(DEFAULT_THEME), **_tokens(name)}
        dots = "".join(f"<span style='background:{esc(merged.get(t, '').strip())}'></span>" for t in swatch)
        cards.append(f"<label class='theme-card'><input type='radio' name='theme' value='{esc(name)}'"
                     f"{' checked' if name == current else ''}><b>{esc(name.title())}</b>"
                     f"<span class='swatch'>{dots}</span>"
                     f"<a href='/?theme={esc(name)}' class='muted'>preview</a></label>")
    conf, now = scheduler.settings(), datetime.datetime.now()
    state = load_json(scheduler.STATE)
    rows = "".join(
        f"<tr><td>{esc(job)}</td><td>{'on' if job in scheduler.enabled(conf) else 'off'}</td>"
        f"<td>{esc(state.get(job, '—')[:16].replace('T', ' '))}</td>"
        f"<td>{scheduler.next_due(slots, now):%a %d %b %H:%M}</td><td class='mono'>{esc(' '.join(args))}</td></tr>"
        for job, (args, slots) in scheduler.JOBS.items())
    mode_note = {"off": "Nothing runs. Set <code>\"mode\"</code> in <code>config/scheduler.json</code> to"
                        " <code>log-only</code> to watch it, then <code>run</code>.",
                 "log-only": "It writes what it would run to <code>runs/scheduler.log</code> and runs"
                             " nothing; the Windows tasks still do the work. After a week of matching times:"
                             " <code>run</code>, and <code>scripts\\schedule.ps1 -Remove</code> in the same step.",
                 "run": "It runs the jobs. The Windows tasks should be removed, or every call is made twice."}
    client = os.environ.get("BENCH_CLIENT") or "llm"
    return (
        "<div class='panel form-panel'><h2>Theme</h2><p class='muted'>Colours only; every theme passes the contrast"
        " check (<code>scripts/check_themes.py</code>). The \"by Wendell Research\" credit is not a setting.</p>"
        f"<div class='themes' id='themes'>{''.join(cards)}</div>"
        "<div class='errors' id='t-errors' role='alert'></div>"
        "<div class='actions'><button class='btn primary' id='t-save'>Save theme</button></div></div>"
        f"<div class='panel'><h2>Scheduler · {esc(conf['mode'])}</h2><p class='muted'>{mode_note.get(conf['mode'], '')}</p>"
        "<table><thead><tr>" + th("Job") + th("On") + th("Counted up to") + th("Next") + th("Runs")
        + f"</tr></thead><tbody>{rows}</tbody></table></div>"
        f"<div class='panel'><h2>Model client · {esc(client)}</h2><p class='muted'>"
        "The default for calls the dashboard and gateway make (<code>BENCH_CLIENT</code>, <code>llm</code>"
        " unless set). The perf and availability tasks pass <code>--client builtin</code> for the trial week"
        " ending 2026-10-26 (RELEASE-PLAN Phase 2a).</p></div>")


def endpoints_page() -> str:
    """Each connection, what it serves and what is known of each model's
    limits. Quotas arrive with the built-in client's quota records (Phase 2b)."""
    eps = endpoint_list()["endpoints"]
    # What each endpoint serves, without asking it: the bench's own models on
    # it, plus what the daily served check (scripts/check_deprecations.py)
    # last listed, keyed `served` on the default endpoint, `served@endpoint`
    # elsewhere. A page that needs the network would be down when you need it.
    snap = load_json(DATA / "config" / "served-snapshot.json")
    checked = snap.get("updated", "")
    names, default = {e["name"] for e in eps}, endpoints.default()
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
                cells = (f"<td>{named(alias)}</td><td>{esc(catalog.connection_of(alias))}</td>"
                         f"<td data-sort='0'>{chip(label, css, why)}</td>")
            else:
                gone = day and day != checked
                cells = (f"<td class='muted'>not on the bench</td>"
                         f"<td>{esc((tags.get(served, '').split('/')[0] if e['default'] else e['name']) or '—')}</td>"
                         f"<td data-sort='{2 if gone else 1}'>"
                         + (chip(f"last seen {day}", "warn") if gone else chip("served", "")) + "</td>")
            rows.append(f"<tr><td class='mono'>{esc(served)}</td>{cells}</tr>")
        extra = sum(1 for s, d in listed.items() if s not in mine and d == checked)
        blocks.append(
            f"<div class='ep'><div class='what'><span class='alias'>{esc(e['name'])}</span>"
            + (" " + chip("default", "") if e["default"] else "")
            + " " + chip("key set" if e["key_set"] else "no key", "ok" if e["key_set"] else "warn")
            + f"</div><div class='base mono'>{esc(e['base'])}</div>"
            f"<details class='models'><summary>{len(mine)} on the bench"
            + (f" · {extra} more served" if extra else "") + "</summary>"
            "<table><thead><tr>" + th("Served as") + th("On the bench as") + th("Connection") + th("Status")
            + f"</tr></thead><tbody>{''.join(rows)}</tbody></table></details></div>")
    cards = []
    # Which of this install's endpoints each model is reached THROUGH, beside
    # the connection (provider) it comes FROM: an OpenRouter model may be on
    # OpenRouter's own endpoint or arrive through Open WebUI's.
    through = {r["alias"]: r["endpoint"] for r in endpoints.registry()}
    through.update({a: d["endpoint"] for a, d in decide.load().items()})
    for c in connections_view()["connections"]:
        rows = []
        for a in c["models"]:
            f = catalog.info(a)
            lim = f.get("limits") or {}
            num = lambda v: f"{v:,}" if isinstance(v, int) else "—"          # noqa: E731
            ep = through.get(a, "?")
            rows.append(f"<tr><td><a href='#' class='model' data-alias='{esc(a)}'>"
                        f"<span class='alias'>{esc(a)}</span></a></td>"
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
    return (
        "<div class='panel'><h2>Endpoints</h2><p class='sub'>Where calls go, whether each key is set"
        " (never the key), and the models each serves: the bench's own, then what the daily served check"
        f" listed{' on ' + esc(checked) if checked else ''}. For a model's full list, <em>Onboard a model</em>"
        " asks every endpoint live. Add an endpoint or change a key with Manage.</p>"
        "<div class='toolbar'><input type='search' data-filter placeholder='Find a model on any endpoint'"
        " aria-label='Filter models'></div>"
        f"{''.join(blocks)}"
        "<div class='actions'><button class='btn' id='endpoints-open'>Manage endpoints and limits</button></div></div>"
        "<div class='panel'><h2>Models</h2><p class='sub'>Every model on the bench by the connection it"
        " comes from, with the endpoint it is reached through and what is known of its limits. A name"
        " opens the model.</p></div>"
        + "".join(cards))


def quotas_page() -> str:
    """What limits each connection, and who is resting after one. The live
    numbers (what is left, when it resets) come with the built-in client's
    quota records, RELEASE-PLAN Phase 2b."""
    import learn
    now = datetime.datetime.now().astimezone()
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
    keys = catalog.LIMIT_KEYS
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
        " when it resets, read from provider endpoints, rate-limit headers and the bench's own call"
        " ledger, arrives with the built-in client's quota records (RELEASE-PLAN Phase 2b). Until then:"
        " who is resting after a rate limit, and every limit known. Quotas of OAuth credentials stay"
        " in CLI Proxy API's own console.</p></div>"
        "<div class='panel'><h3 class='chart-head'>Resting after a rate limit · " + str(len(cool_rows)) + "</h3>"
        + ("<table><thead><tr>" + th("Model") + th("Connection") + th("Back in", True) + th("Wait")
           + f"</tr></thead><tbody>{''.join(cool_rows)}</tbody></table>" if cool_rows
           else "<p class='muted'>None: every model is free to call.</p>")
        + "</div>"
        "<div class='panel'><h3 class='chart-head'>Known limits</h3><p class='sub'>Entered per connection"
        " (Endpoints, Manage) or learned from a provider's error; a model's own limits in its popup win,"
        " and learned ones only ever lower a limit.</p>"
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


def version() -> str:
    """The commit this is running, from git when there is one."""
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%h %cs"], cwd=ROOT, capture_output=True,
                             text=True, timeout=5)
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


VERSION = version()


def info_page() -> str:
    import scheduler
    rows = [("Version", VERSION), ("Python", f"{sys.version.split()[0]} · {sys.executable}"),
            ("Folder", str(ROOT)), ("Endpoints", str(len(endpoints.load()))),
            ("Models on the bench", str(len(_chat_aliases()))),
            ("Scheduler", scheduler.settings()["mode"]), ("Model client", os.environ.get("BENCH_CLIENT") or "llm"),
            ("Theme", saved_theme())]
    body = "".join(f"<tr><th scope='row'>{esc(k)}</th><td class='mono'>{esc(v)}</td></tr>" for k, v in rows)
    return (f"<div class='panel'><h2>Info</h2><table class='facts'><tbody>{body}</tbody></table>"
            "<p class='muted'>Benchwarmers · by Wendell Research · MIT licence"
            " (<code>LICENSE</code>, credits in <code>NOTICE</code>). Every response carries"
            " <code>X-Benchwarmers-Version</code>.</p></div>")


def directory_json() -> str:
    directory = model_directory()
    facts = {a: catalog.info(a) for a, d in directory.items() if d.get("kind") != "decision"}
    return json.dumps({"models": directory, "facts": facts, "roles": roles_config().get("roles", {}),
                       "retired": retired(), "stamp": data_stamp()},
                      ensure_ascii=False).replace("</", "<\\/")


def editor() -> str:
    return (
        "<dialog id='model-dialog' aria-labelledby='m-title'>"
        "<h2 id='m-title'></h2><p class='served' id='m-served'></p>"
        "<div class='note' id='m-gone' hidden></div>"
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
        "<label class='check'><input type='checkbox' id='m-adhoc'> Ad hoc only</label>"
        "<p class='rules'>For expensive or capped models: never seated automatically, never"
        " swept, never probed daily. Used only when named.</p>"
        "</div>"
        "<div class='errors' id='m-errors' role='alert'></div><div class='done' id='m-done'></div>"
        "<div class='actions'><button class='btn' id='m-close'>Close</button>"
        "<button class='btn primary' id='m-restore' hidden>Restore</button>"
        "<button class='btn primary' id='m-save'>Save</button></div>"
        "<details class='remove' id='m-remove-box'><summary>Retire this model…</summary>"
        "<p class='rules'>Deregisters the alias, records it in <code>retired.json</code> so the"
        " name is never reused, and clears its problems. Deletes no evidence, and Restore"
        " brings it back. The daily check also retires models on its own when they are"
        " clearly gone.</p>"
        "<label class='field' for='m-reason'>Why</label>"
        "<input id='m-reason' placeholder='e.g. free tier withdrawn 2026-10-15'>"
        "<label class='field' for='m-typed'>Type the alias to confirm</label>"
        "<input id='m-typed' spellcheck='false' autocomplete='off'>"
        "<div class='actions'><button class='btn danger' id='m-remove' disabled>Retire</button></div>"
        "</details></dialog>"
        # onboarding
        "<dialog id='onboard-dialog' aria-labelledby='o-title'>"
        "<h2 id='o-title'>Onboard a model</h2>"
        "<div id='o-choose'>"
        "<p class='served'>Everything your endpoints serve that is not on the bench, retired or"
        " dismissed. Onboarding registers it, probes it (4 calls) and runs the internal"
        " suite: minutes, and it spends calls. A model from somewhere else? Add its"
        " endpoint first.</p>"
        "<input type='search' id='o-find' placeholder='Search models or connections'"
        " aria-label='Search models'>"
        "<div class='picker' id='o-picker' role='radiogroup' aria-label='Served models'></div>"
        "</div>"
        "<div id='o-run' hidden><p class='served' id='o-result'></p><ol class='steps' id='o-steps'></ol>"
        "<details><summary class='muted'>Full log</summary><pre class='log' id='o-log'></pre></details></div>"
        "<div class='errors' id='o-errors' role='alert'></div>"
        "<div class='actions'><button class='btn' id='o-close'>Close</button>"
        "<button class='btn primary' id='o-start' disabled>Onboard</button></div>"
        "</dialog>"
        # endpoints
        "<dialog id='endpoints-dialog' aria-labelledby='e-title'>"
        "<h2 id='e-title'>Endpoints</h2>"
        "<p class='served'>Where models are served from. Open WebUI is the default; any"
        " OpenAI-compatible API can be added (it must list <code>/models</code>). Prompts"
        " to a model go straight to its endpoint, so add only providers you are content"
        " to send private source to.</p>"
        "<div id='e-list'></div>"
        "<div class='list-actions'><button class='btn' id='e-add'>Add an endpoint</button></div>"
        # The form only when asked for: Add, or a row's Change key.
        "<div id='e-form' hidden>"
        "<h3 class='form-head' id='e-form-head'>Add an endpoint</h3>"
        "<label class='field' for='e-name'>Name</label>"
        "<input id='e-name' spellcheck='false' autocomplete='off' placeholder='e.g. openrouter'>"
        "<label class='field' for='e-base'>Base URL</label>"
        "<input id='e-base' spellcheck='false' autocomplete='off' placeholder='e.g. https://openrouter.ai/api/v1'>"
        "<label class='field' for='e-key'>API key</label>"
        "<input id='e-key' type='password' autocomplete='off' placeholder='leave blank to keep the current one'>"
        "<p class='rules'>Stored where <code>llm keys set</code> stores it, never in the"
        " repository and never shown again. Saved only once <code>/models</code> answers;"
        " where that list is public, the key is proven by the first real call.</p>"
        "<div class='actions'><button class='btn' id='e-cancel'>Cancel</button>"
        "<button class='btn primary' id='e-save'>Test and save</button></div>"
        "</div>"
        "<div class='errors' id='e-errors' role='alert'></div><div class='done' id='e-done'></div>"
        "<details class='limits-block'><summary class='form-head'>Rate limits per connection</summary>"
        "<p class='rules'>What a key behind each connection allows, from the provider's docs or"
        " freellmapi.co/models. Models on that connection share it; a model's own limits (its"
        " popup) win, and limits the bench learns from errors only ever lower these.</p>"
        "<div id='l-list'></div><div class='errors' id='l-errors' role='alert'></div>"
        "<div class='done' id='l-done'></div></details>"
        "<div class='actions'><button class='btn' id='e-close'>Close</button></div>"
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


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):                                    # noqa: N802
        # Host checked on every GET: a DNS-rebinding page could otherwise read
        # any of these replies (metrics/guard.py).
        try:
            guard.check(self.headers, PORT, post=False, max_body=0)
        except guard.Refused as r:
            self._json(r.status, {"ok": False, "errors": [str(r)]})
            return
        if self.path.startswith("/api/onboard?"):
            from urllib.parse import parse_qs, urlparse
            log = parse_qs(urlparse(self.path).query).get("log", [""])[0]
            self._json(200, onboard_status(log))
            return
        if self.path == "/api/candidates":
            self._json(200, onboard_candidates())
            return
        if self.path == "/api/endpoints":
            self._json(200, endpoint_list())
            return
        if self.path == "/api/collections":
            self._json(200, collections_view())
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
        if self.path.startswith("/fonts/"):
            # Only the named brand font files, from the brand repo; anything
            # else is a 404, so this cannot be walked to other paths.
            path = font_file(self.path[len("/fonts/"):])
            if not path:
                self.send_error(404)
                return
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "font/woff2")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(data)
            return
        from urllib.parse import parse_qs, urlparse
        theme = parse_qs(urlparse(self.path).query).get("theme", [None])[0]   # a preview; else the saved one
        body = render(theme).encode("utf-8")
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
        ops = {"/api/model": lambda p: change_model(str(p.get("alias", "")),
                                                    p.get("new_alias"), p.get("new_role"),
                                                    new_ad_hoc=p.get("new_ad_hoc"),
                                                    new_question_types=p.get("new_question_types")),
               "/api/remove": lambda p: remove_model(str(p.get("alias", "")),
                                                     str(p.get("typed", "")), str(p.get("reason", ""))),
               "/api/restore": lambda p: restore_model(str(p.get("alias", ""))),
               "/api/retry": lambda p: retry_model(str(p.get("alias") or "")),
               "/api/discover/add": lambda p: discover_add(str(p.get("connection") or ""),
                                                           str(p.get("model") or "")),
               "/api/onboard": lambda p: start_onboard(str(p.get("served", "")),
                                                       str(p.get("endpoint", ""))),
               "/api/dismiss": lambda p: dismiss(str(p.get("served", "")),
                                                 bool(p.get("undo"))),
               "/api/endpoint": lambda p: save_endpoint(str(p.get("name", "")),
                                                        str(p.get("base", "")),
                                                        str(p.get("key", ""))),
               "/api/endpoint/remove": lambda p: remove_endpoint(str(p.get("name", ""))),
               # `or ""`, not a default: JSON null must not become the text "None".
               "/api/collection": lambda p: save_collection(str(p.get("name") or ""), p.get("members"),
                                                            str(p.get("shape") or ""),
                                                            str(p.get("description") or ""),
                                                            p.get("tags")),
               "/api/collection/remove": lambda p: delete_collection(str(p.get("name") or "")),
               "/api/model-facts": lambda p: save_model_facts(str(p.get("alias") or ""), p.get("facts")),
               "/api/connection-limits": lambda p: save_connection_limits(str(p.get("connection") or ""),
                                                                          p.get("limits")),
               "/api/verdict": lambda p: record_verdict(str(p.get("call") or ""), p.get("useful"),
                                                        str(p.get("note") or ""), p.get("real"),
                                                        p.get("false")),
               "/api/gateway-settings": lambda p: save_gateway_settings(
                   p.get("max_calls"), p.get("max_explorers")),
               "/api/theme": lambda p: save_theme(str(p.get("theme") or ""))}
        if self.path not in ops:
            self._json(404, {"ok": False, "errors": ["unknown endpoint"]})
            return
        # SAME ORIGIN ONLY. Any page open in the browser can POST a plain form
        # to 127.0.0.1, and this used to parse whatever arrived as JSON -- so a
        # website could retire models, and now could set a key. The checks are
        # shared with the gateway (metrics/guard.py).
        try:
            length = guard.check(self.headers, PORT, post=True, max_body=MAX_BODY)
        except guard.Refused as r:
            self._json(r.status, {"ok": False, "errors": [str(r)]})
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            result = ops[self.path](payload)
        except (ValueError, TypeError) as exc:
            result = {"ok": False, "errors": [f"bad request: {exc}"],
                      "changed": [], "sync": ""}
        self._json(200 if result["ok"] else 400, result)

    def _json(self, status: int, data: dict) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        # Every reply says which build answered it (RELEASE-PLAN "The dashboard").
        self.send_header("X-Benchwarmers-Version", VERSION)
        super().end_headers()

    def log_message(self, *_args):
        """Quiet. A dashboard does not need to narrate every refresh."""


if __name__ == "__main__":
    print(f"Benchwarmers on http://localhost:{PORT}  (ctrl-c to stop)")
    import scheduler
    scheduler.start()                                  # off unless config/scheduler.json says otherwise
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
