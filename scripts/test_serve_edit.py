"""Exercise serve.change_model against a COPY of the config, never the real one.

    python scripts/test_serve_edit.py

Each rule the popup enforces is tried once and must refuse; then a role change
and a rename are applied to the copy and checked. The llm sync is not run:
with the built-in client it is skipped, and with the llm CLI serve.ROOT points
at the copy, which has no scripts/sync_config.py, so the sync reports that it
could not run. Alerts and endpoints point at copies too,
because removing a model clears its alerts and a regressed endpoint check
would write the endpoint file.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "metrics"))
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
sys.path.insert(0, str(ROOT))
import serve  # noqa: E402


def main() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="bench-edit-"))
    cfg = tmp / "config"
    cfg.mkdir()
    for name in ("model-roles.json", "extra-openai-models.yaml",
                 "alias-history.json", "model-options.json", "provider-tags.json"):
        shutil.copy(DATA / "config" / name, cfg / name)
    (tmp / "scripts").mkdir()
    (tmp / "metrics").mkdir()
    shutil.copy(ROOT / "metrics" / "aliases.py", tmp / "metrics" / "aliases.py")
    # point serve at the copy
    serve.ROOT = tmp
    serve.DATA = tmp            # the data paths serve builds inline follow this one
    serve.ROLES_PATH = cfg / "model-roles.json"
    serve.YAML_PATH = cfg / "extra-openai-models.yaml"
    serve.HISTORY_PATH = cfg / "alias-history.json"
    serve.OPTIONS_PATH = cfg / "model-options.json"
    serve.CHANGES_PATH = cfg / "changes.jsonl"
    serve.RETIRED_PATH = cfg / "retired.json"
    # every other path serve fixes at import, so nothing here touches the real data
    serve.DISMISSED_PATH = cfg / "dismissed.json"
    serve.ENDPOINT_CHECKS = cfg / "endpoint-checks.json"
    serve.UI_CONFIG = cfg / "ui.json"
    serve.ONBOARD_ISSUES_PATH = tmp / "metrics" / "onboard-issues.json"
    import aliases
    aliases.HISTORY = cfg / "alias-history.json"
    import alerts
    shutil.copy(DATA / "metrics" / "alerts.json", tmp / "metrics" / "alerts.json")
    alerts.FILE = tmp / "metrics" / "alerts.json"
    # Decision models too: a rename or removal of one writes decide.FILE, and
    # which model the rename picks depends on the data (sorted first).
    import decide
    if (DATA / "config" / "decision-models.json").exists():
        shutil.copy(DATA / "config" / "decision-models.json", cfg / "decision-models.json")
    decide.FILE = cfg / "decision-models.json"
    shutil.copy(DATA / "config" / "endpoints.json", cfg / "endpoints.json")
    serve.endpoints.FILE = cfg / "endpoints.json"
    serve.endpoints.YAML = serve.YAML_PATH
    # Collections: removing a model takes it out of them, and Restore puts it back.
    serve.bench_collections.FILE = cfg / "collections.json"
    serve.bench_collections.SETTINGS_FILE = cfg / "gateway.json"
    real = {p: p.read_bytes() for p in (DATA / "metrics" / "alerts.json", DATA / "config" / "endpoints.json")}
    real_collections = (DATA / "config" / "collections.json").read_bytes() \
        if (DATA / "config" / "collections.json").exists() else None

    d = serve.model_directory()
    some = sorted(d)[0]
    retired = next(iter(json.loads((cfg / "alias-history.json").read_text(encoding="utf-8"))))
    other = next(a for a in d if a != some)
    failures = 0

    def expect_refused(label, **kw):
        nonlocal failures
        r = serve.change_model(**kw)
        ok = (not r["ok"]) and r["errors"] and not r["changed"]
        print(("ok   " if ok else "FAIL ") + label + ("" if ok else f" -> {r}"))
        failures += 0 if ok else 1

    expect_refused("unknown alias", alias="no-such-model", new_alias="x", new_role="general")
    expect_refused("nothing to change", alias=some, new_alias=some, new_role=d[some]["role"])
    expect_refused("unknown role", alias=some, new_alias=some, new_role="wizard")
    expect_refused("alias with a space", alias=some, new_alias="bad name", new_role=d[some]["role"])
    expect_refused("alias taken", alias=some, new_alias=other, new_role=d[some]["role"])
    expect_refused("retired alias reused", alias=some, new_alias=retired, new_role=d[some]["role"])
    expect_refused("case-only collision", alias=some, new_alias=other.upper(), new_role=d[some]["role"])

    # role change
    new_role = "code" if d[some]["role"] != "code" else "general"
    r = serve.change_model(some, some, new_role)
    ok = r["ok"] and serve.model_directory()[some]["role"] == new_role
    print(("ok   " if ok else "FAIL ") + f"role change {some}: {d[some]['role']} -> {new_role}")
    failures += 0 if ok else 1

    # ad hoc: a flag, not a role — setting it leaves the role alone
    expect_refused("ad_hoc not a bool", alias=some, new_alias=some, new_role=None,
                   new_ad_hoc="yes")
    r = serve.change_model(some, some, None, new_ad_hoc=True)
    ok = (r["ok"] and serve.model_directory()[some]["ad_hoc"] is True
          and serve.model_directory()[some]["role"] == new_role)
    print(("ok   " if ok else "FAIL ") + f"ad hoc on {some}, role kept")
    failures += 0 if ok else 1
    expect_refused("ad hoc already set", alias=some, new_alias=some, new_role=None,
                   new_ad_hoc=True)

    # rename
    r = serve.change_model(some, some + "-renamed", None)
    d2 = serve.model_directory()
    hist = json.loads((cfg / "alias-history.json").read_text(encoding="utf-8"))
    roles = json.loads((cfg / "model-roles.json").read_text(encoding="utf-8"))["models"]
    held = json.loads((cfg / "model-roles.json").read_text(encoding="utf-8"))["ad_hoc"]
    ok = (r["ok"] and some not in d2 and some + "-renamed" in d2
          and some + "-renamed" in held and some not in held
          and d2[some + "-renamed"]["served"] == d[some]["served"]
          and hist.get(some) == some + "-renamed"
          and roles.get(some + "-renamed") == new_role and some not in roles
          and aliases.current(some) == some + "-renamed")
    print(("ok   " if ok else "FAIL ") + f"rename {some} -> {some}-renamed; sync: {r['sync'][:60]}")
    failures += 0 if ok else 1
    # and the old name can never come back
    expect_refused("old name refused after rename", alias=some + "-renamed",
                   new_alias=some, new_role=None)
    lines = (cfg / "changes.jsonl").read_text(encoding="utf-8").splitlines()
    print(("ok   " if len(lines) == 3 else "FAIL ") + f"{len(lines)} changes logged")
    failures += 0 if len(lines) == 3 else 1

    # removal
    victim = some + "-renamed"
    served = d2[victim]["served"]
    r = serve.remove_model("no-such", "whatever it is")
    ok = not r["ok"] and not r["changed"]
    print(("ok   " if ok else "FAIL ") + "remove: unknown alias"); failures += 0 if ok else 1
    # In three collections: a member of two, the merger of one; a fourth without it.
    colls = {"with-it": {"members": [other, victim], "merger": victim, "reply": "merged"},
             "member": {"members": [victim, other], "merger": ""},
             "doomed": {"members": [victim], "merger": ""},
             "without": {"members": [other], "merger": other}}
    (cfg / "collections.json").write_text(json.dumps(colls), encoding="utf-8")
    logged = len(serve.CHANGES_PATH.read_text(encoding="utf-8").splitlines())
    before = len(serve.registered())
    r = serve.remove_model(victim, "retired by provider on 2026-09-15")
    d3 = serve.model_directory()
    retired = json.loads((cfg / "retired.json").read_text(encoding="utf-8"))
    yaml = (cfg / "extra-openai-models.yaml").read_text(encoding="utf-8")
    ok = (r["ok"] and victim not in d3 and len(serve.registered()) == before - 1
          and retired[victim]["served"] == served and f"model_id: {victim}\n" not in yaml
          and victim not in json.loads((cfg / "model-roles.json").read_text(encoding="utf-8"))["models"]
          and victim not in json.loads((cfg / "model-roles.json").read_text(encoding="utf-8"))["ad_hoc"]
          and served not in serve.registered())
    print(("ok   " if ok else "FAIL ") + f"removed {victim}; registry {before} -> {len(serve.registered())}")
    failures += 0 if ok else 1
    after = json.loads((cfg / "collections.json").read_text(encoding="utf-8"))
    log = [json.loads(x) for x in serve.CHANGES_PATH.read_text(encoding="utf-8").splitlines()[logged:]]
    ok = (after == {"with-it": {"members": [other], "merger": "", "reply": "merged"},
                    "member": {"members": [other], "merger": ""},
                    "doomed": {"members": [], "merger": ""},
                    "without": colls["without"]}
          and retired[victim]["collections"] == {"with-it": ["members", "merger"], "member": ["members"],
                                                 "doomed": ["members"]}
          and "config/collections.json" in r["changed"]
          and sorted(e["field"] for e in log if e["field"].startswith("collection "))
          == ["collection doomed", "collection member", "collection with-it"])
    print(("ok   " if ok else "FAIL ") + "removed from its collections, recorded and logged"
          + ("" if ok else f" -> {after} {retired[victim]} {r['changed']} {log}")); failures += 0 if ok else 1
    expect_refused("retired name never reused", alias=other, new_alias=victim, new_role=None)
    # The collection dialog's member search matches the served name too, so the view carries it.
    view = serve.collections_view()
    ok = (set(view["served"]) == set(view["models"]) and victim not in view["served"]
          and view["served"].get(other) == d3[other]["served"])
    print(("ok   " if ok else "FAIL ") + "collection view: served name for every member choice"); failures += 0 if ok else 1
    # Kept between requests (serve.kept), never across a write: with the data's stamp held
    # still, so only the write itself can tell, a role changed through the page is in the
    # very next answer, and with nothing written the answer is the one kept.
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    server = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    held = serve.data_stamp, serve.PORT
    serve.data_stamp, serve.PORT = (lambda: 1.0), server.server_port

    def ask(path, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{server.server_port}{path}",
                                     None if body is None else json.dumps(body).encode(),
                                     {"Host": f"127.0.0.1:{server.server_port}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    try:
        before = ask("/api/directory")["models"][other]["role"]
        made = serve._kept["directory"][1]
        again = ask("/api/directory")["models"][other]["role"]
        kept_it = serve._kept["directory"][1] == made
        role = "code" if before != "code" else "general"
        changed = ask("/api/model", {"alias": other, "new_alias": other, "new_role": role})
        after = ask("/api/directory")["models"][other]["role"]
    finally:
        serve.data_stamp, serve.PORT = held
        server.shutdown()
    ok = kept_it and again == before and changed["ok"] and after == role
    print(("ok   " if ok else "FAIL ") + "a page kept between requests, and never answered from before a write"
          + ("" if ok else f" -> {before} {again} {kept_it} {changed} {after}")); failures += 0 if ok else 1
    # Clients: every snippet parses in its format, names each collection, sets the
    # timeouts a panel needs, and a made key goes only into a shell line or a field.
    import tomllib
    try:
        import yaml                                     # comes with the llm CLI; the built-in client's venv may lack it
        load_yaml = yaml.safe_load
    except ImportError:
        load_yaml = None
    bad = []
    for keys in (False, True):
        setups = {c["id"]: c for c in serve.client_setups("http://127.0.0.1:8122", [("core", 200_000, False),
                                                                                    ("fast", None, True)], keys)}
        parsed = {}
        for c in setups.values():
            for b in c["blocks"]:
                parse = {"json": json.loads, "toml": tomllib.loads, "yaml": load_yaml,
                         "python": lambda t: compile(t, "<snippet>", "exec")}.get(b["lang"])
                try:
                    parsed[c["id"], b["label"]] = parse(b["text"]) if parse else b["text"]
                except Exception as exc:                # noqa: BLE001
                    bad.append(f"{c['id']} {b['label']} does not parse: {exc}")
                if b["key"] and b["lang"] not in ("sh", "bat", "text"):
                    bad.append(f"{c['id']} {b['label']}: a key would go into a file")
            alltext = "\n".join(b["text"] for b in c["blocks"])
            want = ("bench/core:merged", "bench/fast:merged") if c["id"] == "chat" else \
                ("bench/core:route", "bench/fast:route") if c["id"] not in ("codex", "code") else ("bench/core:route",)
            bad += [f"{c['id']} lacks {w}" for w in want if w not in alltext]
        oc, cx = parsed.get(("opencode", "opencode.jsonc")), parsed.get(("codex", "config.toml"))
        cxp = parsed.get(("codex", "benchwarmers.config.toml")) or {}     # Codex's profile: the model half
        if oc and cx:
            p = oc["providers"]["benchwarmers"]                 # opencode 2's shape
            if (oc["mcp"]["servers"]["benchwarmers"]["type"] != "remote"
                    or cx["mcp_servers"]["benchwarmers"]["tool_timeout_sec"] != 900
                    or cx["model_providers"]["benchwarmers"]["wire_api"] != "responses" or "model" in cx
                    or cxp.get("web_search") != "disabled" or cxp.get("model_provider") != "benchwarmers"
                    or p["models"]["fast"]["modelID"] != "bench/fast:route"
                    or p["models"]["fast"]["limit"]["context"] != serve.UNKNOWN_CONTEXT
                    or ("env" in p) != keys or ("env_key" in cx["model_providers"]["benchwarmers"]) != keys):
                bad.append(f"opencode or Codex settings wrong (keys={keys})")
        if parsed.get(("cline", "Cline: cline_mcp_settings.json"), {}).get("mcpServers", {}).get("benchwarmers", {}).get("timeout") != 900:
            bad.append("Cline's MCP timeout is not 900 s")
        if not all(m["toolCalling"] for m in parsed.get(("vscode", "chatLanguageModels.json"), [{}])[0].get("models", [{}])):
            bad.append("a VS Code model without toolCalling is hidden from the agent picker")
        if load_yaml and parsed.get(("goose", "config.yaml"), {}).get("extensions", {}).get("benchwarmers", {}).get("timeout") != 900:
            bad.append("goose's extension timeout is not 900 s")
    # Connect's choices: each way an app offers, with or without its name as a label, still parses,
    # leaves out the half not chosen, and sends the name only where no key is used.
    for way in serve.WAYS:
        for label, keys in (("", False), ("opencode", False), ("opencode", True)):
            for c in serve.client_setups("http://127.0.0.1:8122", [("core", 200_000, False)], keys, way, label):
                if way not in c["ways"]:
                    continue
                text = "\n".join(b["text"] for b in c["blocks"])
                for b in c["blocks"]:
                    parse = {"json": json.loads, "toml": tomllib.loads, "yaml": load_yaml,
                             "python": lambda t: compile(t, "<snippet>", "exec")}.get(b["lang"])
                    try:
                        parse and parse(b["text"])
                    except Exception as exc:            # noqa: BLE001
                        bad.append(f"{c['id']} {way} {label!r} {b['label']} does not parse: {exc}")
                if not c["blocks"]:
                    bad.append(f"{c['id']} {way}: no set-up at all")
                if way == "model" and "/mcp" in text and c["id"] != "claude-code":
                    bad.append(f"{c['id']} model-only still carries the MCP server")
                if way == "panel" and ":route" in text:
                    bad.append(f"{c['id']} panel-only still carries a route model")
                if keys and any(s in text for s in ("Bearer opencode", '"opencode"', "'opencode'", "=opencode")):
                    bad.append(f"{c['id']} sends the label with a key in use")
    ok = not bad
    print(("ok   " if ok else "FAIL ") + "client set-ups: JSON, TOML" + (", YAML" if load_yaml else " (YAML unread: no PyYAML)")
          + " parse, collections named, timeouts set, keys only in shell lines and fields" + ("" if ok else f" -> {bad}"))
    failures += 0 if ok else 1

    # Connect's Set up, in a throwaway home: each app's files written, theirs kept beside
    # them, a file with comments left alone, a second run with nothing to write.
    bad, home = [], pathlib.Path(tempfile.mkdtemp(prefix="bench-setup-home-"))
    saved_env = {k: os.environ.get(k) for k in ("USERPROFILE", "HOME", "APPDATA", "XDG_CONFIG_HOME")}
    os.environ.update(USERPROFILE=str(home), HOME=str(home), APPDATA=str(home / "AppData"),
                      XDG_CONFIG_HOME=str(home / ".config"))
    try:
        dirs = serve._app_dirs()
        for p, text in ((dirs["vscode"] / "chatLanguageModels.json", '[{"name": "Other", "vendor": "x"}]'),
                        (dirs["zed"] / "settings.json", '// my settings\n{"theme": "One"}'),
                        (dirs["codex"] / "config.toml", 'model = "mine"\n\n[mcp_servers.other]\nurl = "x"\n'),
                        (dirs["goose"] / "config.yaml", "extensions:\n  other:\n    enabled: true\nactive_provider: x\n")):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        for app in serve.SETUP_APPS:
            r = serve.setup_apply(app, "both", "", "my-app")
            if not r["ok"]:
                bad.append(f"{app}: {r['errors']}")
                continue
            if app == "zed" and not r["left"]:
                bad.append("zed's settings with comments were written over")
            if app != "zed" and not r["written"]:
                bad.append(f"{app} wrote nothing")
            if serve.setup_plan(app, "both", "", "my-app")["files"]:
                bad.append(f"{app}: a second Set up would write again")
        vs = json.loads((dirs["vscode"] / "chatLanguageModels.json").read_text(encoding="utf-8"))
        if [e["name"] for e in vs] != ["Other", "Benchwarmers"]:
            bad.append(f"VS Code's own models not kept: {vs}")
        cx = tomllib.loads((dirs["codex"] / "config.toml").read_text(encoding="utf-8"))
        prof = dirs["codex"] / "benchwarmers.config.toml"      # Codex's profile: plain `codex` keeps its own model
        cxp = tomllib.loads(prof.read_text(encoding="utf-8")) if prof.exists() else {}
        if (cx.get("model") != "mine" or "model_provider" in cx or "other" not in cx["mcp_servers"]
                or "benchwarmers" not in cx["model_providers"] or cxp.get("model_provider") != "benchwarmers"):
            bad.append(f"Codex merge wrong: {cx} {cxp}")
        gy = (dirs["goose"] / "config.yaml").read_text(encoding="utf-8")
        if "  other:" not in gy or gy.count("  benchwarmers:") != 1 or "active_provider: x" not in gy:
            bad.append(f"goose merge wrong: {gy!r}")
        if load_yaml and "benchwarmers" not in (load_yaml(gy).get("extensions") or {}):
            bad.append("goose's config.yaml no longer parses with our extension")
        (dirs["goose"] / "config.yaml").write_text("extensions:\n# mine\n    developer:\n      enabled: true\n",
                                                   encoding="utf-8")
        r = serve.setup_plan("goose", "both", "", "my-app")
        if any(f["path"].endswith("config.yaml") for f in r["files"]) or \
                not any("config.yaml" in w and "by hand" in w for w in r["left"]):
            bad.append(f"goose's config.yaml in a shape Set up cannot edit was not left to you: {r['left']}")
        oc = json.loads((dirs["opencode"] / "plugins" / "benchwarmers" / "benchwarmers.json").read_text(encoding="utf-8"))
        if oc.get("name") != "my-app" or not (dirs["opencode"] / "plugins" / "benchwarmers" / "index.ts").exists():
            bad.append(f"opencode plugin not installed: {oc}")
        if not list(dirs["codex"].glob("config.toml.bak-*")):
            bad.append("no copy kept of Codex's own config.toml")
    finally:
        for k, v in saved_env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(home, ignore_errors=True)
    ok = not bad
    print(("ok   " if ok else "FAIL ") + "Set up: each app's files written, theirs kept, comments left alone, a"
          " second run writes nothing" + ("" if ok else f" -> {bad}"))
    failures += 0 if ok else 1
    # Does it work? for an app whose set-up sends no name (Codex: every call is local): a call
    # with no name made after its Set up stands in for it, marked unnamed, and Connect's list
    # shows its ✓; one made before the Set up does not.
    gw = serve.DATA / "runs" / "gateway"
    gw.mkdir(parents=True, exist_ok=True)
    serve._log_change("2030-01-01T12:00:00+01:00", "ui", "", "app set-up", None, {"app": "codex", "files": []})

    def local_call(at):
        with (gw / "routes.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": at, "model": "bench/x", "key": "local", "alias": "m1"}) + "\n")
    local_call("2030-01-01T11:59:59+01:00")
    before, page_before = serve.client_seen("codex"), serve.clients_page()
    local_call("2030-01-01T12:00:05+01:00")
    after, page_after = serve.client_seen("codex"), serve.clients_page()
    (gw / "routes.jsonl").unlink()
    ok = (not before["seen"] and not before.get("unnamed") and "Codex CLI<small" not in page_before
          and after["seen"] and after.get("unnamed") is True and after["model"] == "bench/x"
          and after["at"] == "2030-01-01T12:00:05+01:00" and "Codex CLI<small" in page_after)
    print(("ok   " if ok else "FAIL ") + "Does it work?: a call with no name after Set up counts, one before it"
          " does not" + ("" if ok else f" -> {before} {after}"))
    failures += 0 if ok else 1
    # goose's config.yaml, merged as text: ours once, two spaces in, theirs and the rest as they
    # were; a layout it cannot edit safely is refused (None), never written twice or misplaced.
    # Checked on the text, so it runs without PyYAML; parsed too where PyYAML is there.
    gx = next(b["text"] for c in serve.client_setups("http://127.0.0.1:8122", [("core", 200_000, False)], False)
              if c["id"] == "goose" for b in c["blocks"] if b["label"] == "config.yaml")
    ours = gx.split("\n", 1)[1].rstrip("\n")                    # our entry, without "extensions:"
    dev, mem = "  developer:\n    enabled: true\n", "  memory:\n    enabled: true\n"
    old_ours = "  benchwarmers:\n    enabled: false\n"
    cases = {
        "plain, ours replaced": ("extensions:\n" + dev + old_ours + "    timeout: 300\n" + mem + "GOOSE_MODEL: x\n",
                                 "extensions:\n" + ours + "\n" + dev + mem + "GOOSE_MODEL: x\n"),
        "none yet, appended": ("GOOSE_MODEL: x\n", "GOOSE_MODEL: x\n" + gx.rstrip("\n") + "\n"),
        "a comment two in, inside our old entry": ("extensions:\n" + dev + old_ours + "  # old\n    timeout: 300\n" + mem,
                                                   "extensions:\n" + ours + "\n" + dev + mem),
        "their comments before the next entry and the next key": (
            "extensions:\n" + dev + old_ours + "\n  # memory\n" + mem + "# model\nGOOSE_MODEL: x\n",
            "extensions:\n" + ours + "\n" + dev + "\n  # memory\n" + mem + "# model\nGOOSE_MODEL: x\n"),
        "a column-0 comment before our old entry": ("extensions:\n" + dev + "# kept for later\n" + old_ours, None),
        "a column-0 comment, then entries four in": ("extensions:\n# mine\n    developer:\n      enabled: true\n"
                                                     "GOOSE_MODEL: x\n", None),
        "a two-space comment, then entries four in": ("extensions:\n  # mine\n    developer:\n      enabled: true\n", None),
        "a tab": ("extensions:\n" + dev + "  memory:\n\tenabled: true\n", None),
        "a sequence": ("extensions:\n- developer\n- memory\nGOOSE_MODEL: x\n", None),
        "a sequence two in": ("extensions:\n  - developer\n", None),
        "a flow mapping": ("extensions: {developer: {enabled: true}}\n", None),
        "a quoted key": ('"extensions":\n' + dev, None),
        "a flow mapping at the top": ("{}\n", None),             # appending would make it invalid YAML
        "a document end": ("GOOSE_MODEL: x\n...\n", None),
    }
    bad = []
    for name, (old, want) in cases.items():
        got = serve._yaml_block(old, gx)
        if got != want:
            bad.append(f"{name}: {got!r}")
        elif got and load_yaml:
            a, b = load_yaml(old) or {}, load_yaml(got)
            theirs = {k: v for k, v in (a.get("extensions") or {}).items() if k != "benchwarmers"}
            if (b["extensions"].get("benchwarmers") != load_yaml(gx)["extensions"]["benchwarmers"]
                    or {k: v for k, v in b["extensions"].items() if k != "benchwarmers"} != theirs
                    or {k: v for k, v in a.items() if k != "extensions"} != {k: v for k, v in b.items() if k != "extensions"}):
                bad.append(f"{name}: parses to something else: {b}")
    ok = not bad
    print(("ok   " if ok else "FAIL ") + "goose's config.yaml: ours once, two spaces in, theirs kept; comments, tabs,"
          " a list or another indent refused" + (", parsed" if load_yaml else "") + ("" if ok else f" -> {bad}"))
    failures += 0 if ok else 1

    # Codex's Set up: the model as a profile, never half-switching plain `codex`; the user's
    # config.toml kept key for key, or left to them. Each file here once broke the merge.
    bad, home = [], pathlib.Path(tempfile.mkdtemp(prefix="bench-codex-home-"))
    saved_env = {k: os.environ.get(k) for k in ("USERPROFILE", "HOME", "APPDATA", "XDG_CONFIG_HOME")}
    os.environ.update(USERPROFILE=str(home), HOME=str(home), APPDATA=str(home / "AppData"),
                      XDG_CONFIG_HOME=str(home / ".config"))
    try:
        ctoml = serve._app_dirs()["codex"] / "config.toml"
        prof = ctoml.with_name("benchwarmers.config.toml")
        ctoml.parent.mkdir(parents=True)

        def planned(path: pathlib.Path, theirs: str):
            path.write_text(theirs, encoding="utf-8")
            plan = serve.setup_plan("codex", "both", "", "my-app")
            return plan, next((f["text"] for f in plan["files"] if f["path"] == str(path)), None)
        for what, theirs, holds in (
                ("a header with a comment", '[model_providers.benchwarmers]\nname = "old"\n\n'
                 '[mcp_servers.github] # my github\ncommand = "npx"\n',
                 lambda c: c["mcp_servers"]["github"] == {"command": "npx"}),
                ("[[array]] tables", '[mcp_servers.benchwarmers]\nurl = "old"\n\n[[hooks]]\nrun = "a"\n\n'
                 '[[hooks]]\nrun = "b"\n', lambda c: c["hooks"] == [{"run": "a"}, {"run": "b"}]),
                ("a quoted key", '"model" = "mine"\nweb_search = "live"\n',
                 lambda c: c["model"] == "mine" and c["web_search"] == "live"),
                ("a dotted key", 'model_providers.benchwarmers.name = "x"\n', lambda c: True),
                ("our header with a comment", '[mcp_servers.benchwarmers] # managed\nurl = "old"\n', lambda c: True),
                ("a [table] line in a string", 'developer_instructions = """\nBe brief.\n[rules]\nNo emojis.\n"""\n',
                 lambda c: c["developer_instructions"] == "Be brief.\n[rules]\nNo emojis.\n")):
            plan, text = planned(ctoml, theirs)
            try:
                c = tomllib.loads(text or "")
                good = (text is not None and "model_provider" not in c and holds(c)
                        and c["model_providers"]["benchwarmers"]["name"] == "Benchwarmers"
                        and c["mcp_servers"]["benchwarmers"]["url"].endswith("/mcp"))
            except (tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
                good, c = False, exc
            if not good:
                bad.append(f"{what}: {c if text is not None else plan['left']}")
        for what, theirs in (("a dotted key inside a table", '[model_providers]\nbenchwarmers.name = "x"\n'),
                             ("a file that is not TOML", "model = \n"),
                             # an empty table of theirs that the text split would fold into ours
                             ("a ''' string, then an empty table", "[model_providers.benchwarmers]\nname = '\"\"\"'\n\n"
                                                                     "[user_table]\ndata = {}\n"),
                             ("an empty table with a comma in its name", '[model_providers.benchwarmers]\nname = "old"\n\n'
                                                                          '["a,b"]\n')):
            plan, text = planned(ctoml, theirs)
            if text is not None or not any(str(ctoml) in why for why in plan["left"]):
                bad.append(f"{what}: not left to you ({plan['left']})")
        ctoml.write_text("", encoding="utf-8")
        plan, text = planned(prof, '"model" = "mine"\napproval_policy = "never"\n')
        try:
            p = tomllib.loads(text or "")
        except tomllib.TOMLDecodeError as exc:
            p = {"error": str(exc)}
        if (not str(p.get("model")).startswith("bench/") or p.get("model_provider") != "benchwarmers"
                or p.get("web_search") != "disabled" or p.get("approval_policy") != "never"
                or "codex --profile benchwarmers" not in plan["after"]):
            bad.append(f"the profile: {p} {plan['after']!r}")
    finally:
        for k, v in saved_env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(home, ignore_errors=True)
    ok = not bad
    print(("ok   " if ok else "FAIL ") + "Codex Set up: a profile for the model, your config.toml kept key for key"
          " or left to you" + ("" if ok else f" -> {bad}"))
    failures += 0 if ok else 1

    # Set up's edges, each in a fresh throwaway home (review 2026-11-20, findings 4, 5, 7, 8, 9).
    homes, kept_keys, kept_write = [], serve.guard.keys_required, serve._write_atomic
    serve.guard.keys_required = lambda: False            # a key only where a set-up asks for one

    def fresh() -> dict:
        h = pathlib.Path(tempfile.mkdtemp(prefix="bench-setup-home-"))
        homes.append(h)
        os.environ.update(USERPROFILE=str(h), HOME=str(h), APPDATA=str(h / "AppData"),
                          XDG_CONFIG_HOME=str(h / ".config"))
        return serve._app_dirs()

    def put(p: pathlib.Path, text: str) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def shapes():
        """A JSON root that is not what our snippet is: left by hand, never merged or crashed on."""
        bad = []
        for root in ("[]", "null", "3", '"x"'):
            dirs = fresh()
            for app, p in (("zed", dirs["zed"] / "settings.json"), ("vscode", dirs["vscode"] / "mcp.json"),
                           ("goose", dirs["goose"] / "custom_providers" / "benchwarmers.json")):
                put(p, root)
                r = serve.setup_apply(app, "both", "", "")
                if not r["ok"] or p.read_text(encoding="utf-8") != root \
                        or not any(str(p) in w and "not the shape expected" in w for w in r["left"]):
                    bad.append(f"{app} {root}: {r}")
        dirs = fresh()
        p = dirs["vscode"] / "chatLanguageModels.json"
        put(p, '{"a": {"keep": 1}}')
        r = serve.setup_apply("vscode", "both", "", "")
        if p.read_text(encoding="utf-8") != '{"a": {"keep": 1}}' or not any(str(p) in w for w in r["left"]):
            bad.append(f"VS Code's models file holding an object: {p.read_text(encoding='utf-8')} {r}")
        return bad

    def folder():
        """A folder where a file goes: left by hand, the app's other file still written."""
        dirs, bad = fresh(), []
        for app, p in (("vscode", dirs["vscode"] / "mcp.json"), ("zed", dirs["zed"] / "settings.json")):
            p.mkdir(parents=True)
            r = serve.setup_apply(app, "both", "", "")
            if not r["ok"] or not p.is_dir() or not any(str(p) in w for w in r["left"]):
                bad.append(f"{app}: {r}")
        if not (dirs["vscode"] / "chatLanguageModels.json").exists():
            bad.append("VS Code's models not written beside it")
        return bad

    def half():
        """A write that fails half-way: ok false, which files were written and which not, and logged."""
        dirs, seen = fresh(), len(serve.CHANGES_PATH.read_text(encoding="utf-8").splitlines())

        def refuse(path, text):
            if pathlib.Path(path).name == "mcp.json":
                raise PermissionError(13, "Permission denied", str(path))
            kept_write(path, text)
        serve._write_atomic = refuse
        try:
            r = serve.setup_apply("vscode", "both", "", "")
        finally:
            serve._write_atomic = kept_write
        log = [json.loads(x) for x in serve.CHANGES_PATH.read_text(encoding="utf-8").splitlines()[seen:]]
        said = " ".join(r["errors"])
        bad = [] if (not r["ok"] and "mcp.json" in said and "chatLanguageModels.json" in said
                     and [pathlib.Path(f).name for f in r["written"]] == ["chatLanguageModels.json"]
                     and not (dirs["vscode"] / "mcp.json").exists()) else [r]
        if [[pathlib.Path(f).name for f in e["new"]["files"]] for e in log] != [["chatLanguageModels.json"]]:
            bad.append(f"change log: {log}")
        return bad

    def unkeyed():
        """A key, then none: our own entries replaced whole (no stale header, no input left), yours kept."""
        dirs, bad = fresh(), []
        mine = {"inputs": [{"type": "promptString", "id": "other-key"}],
                "servers": {"other": {"type": "http", "url": "x", "headers": {"Authorization": "Bearer ${input:other-key}"}}}}
        put(dirs["vscode"] / "mcp.json", json.dumps(mine))
        put(dirs["zed"] / "settings.json", json.dumps({
            "context_servers": {"other": {"url": "y"}},
            "language_models": {"openai_compatible": {"benchwarmers": {"api_url": "old", "stale": 1},
                                                      "other": {"api_url": "z"}}}}))
        for app in ("vscode", "zed"):
            serve.setup_apply(app, "both", "", "", True)
        vs = json.loads((dirs["vscode"] / "mcp.json").read_text(encoding="utf-8"))
        if [i["id"] for i in vs["inputs"]] != ["other-key", "benchwarmers-key"] or "headers" not in vs["servers"]["benchwarmers"]:
            bad.append(f"the keyed set-up itself: {vs}")
        for app in ("vscode", "zed"):
            serve.setup_apply(app, "both", "", "")
        vs = json.loads((dirs["vscode"] / "mcp.json").read_text(encoding="utf-8"))
        zd = json.loads((dirs["zed"] / "settings.json").read_text(encoding="utf-8"))
        if vs["inputs"] != mine["inputs"] or vs["servers"]["other"] != mine["servers"]["other"] \
                or "headers" in vs["servers"]["benchwarmers"]:
            bad.append(f"VS Code: {vs}")
        oc = zd["language_models"]["openai_compatible"]
        if "headers" in zd["context_servers"]["benchwarmers"] or zd["context_servers"]["other"] != {"url": "y"} \
                or "stale" in oc["benchwarmers"] or oc["other"] != {"api_url": "z"}:
            bad.append(f"Zed: {zd}")
        return bad

    def placeholder():
        """Keyed Zed and goose keep <your gateway key> in a header: said by hand, and Zed's Done names its variable."""
        dirs, bad = fresh(), []
        for app, p in (("zed", dirs["zed"] / "settings.json"), ("goose", dirs["goose"] / "config.yaml")):
            for r in (serve.setup_apply(app, "both", "", "", True), serve.setup_plan(app, "both", "", "", True)):
                if not any(serve.KEY_HOLDER in w and str(p) in w for w in r["left"]):
                    bad.append(f"{app}: {r['left']}")
            if any(serve.KEY_HOLDER in w for w in serve.setup_plan(app, "both", "", "")["left"]):
                bad.append(f"{app} without a key still says to replace it")
        after = serve.setup_plan("zed", "both", "", "", True)["after"]
        if "BENCHWARMERS_API_KEY" not in after or "any text" in after or serve.KEY_ENV in after.replace("BENCHWARMERS_API_KEY", ""):
            bad.append(f"Zed's Done: {after}")
        return bad

    def typed():
        """A key typed over <your gateway key> in Zed's or goose's header stays through a second keyed Set up;
        an app's name there (a label, from a set-up with no key) is still replaced."""
        dirs, bad, secret = fresh(), [], "bwk_typed0123456789"
        zed, goose = dirs["zed"] / "settings.json", dirs["goose"] / "config.yaml"
        for app in ("zed", "goose"):
            serve.setup_apply(app, "both", "", "", True)
        for p in (zed, goose):
            put(p, p.read_text(encoding="utf-8").replace(serve.KEY_HOLDER, secret))
        put(goose, goose.read_text(encoding="utf-8").replace("timeout: 900", "timeout: 300"))   # so there is a change to write
        put(zed, zed.read_text(encoding="utf-8").replace('"api_url"', '"stale": 1, "api_url"'))
        for app, p in (("zed", zed), ("goose", goose)):
            plan = serve.setup_plan(app, "both", "", "", True)
            if any(serve.KEY_HOLDER in w for w in plan["left"]):
                bad.append(f"{app}: told to replace a key that is there: {plan['left']}")
            r = serve.setup_apply(app, "both", "", "", True)
            text = p.read_text(encoding="utf-8")
            if not r["written"] or secret not in text or serve.KEY_HOLDER in text or "stale" in text or "timeout: 300" in text:
                bad.append(f"{app}: the typed key was not kept, or ours not written: {r} {text!r}")
            if secret in json.dumps(r) or secret in json.dumps(serve._no_text(plan)):
                bad.append(f"{app}: the key is in what the page is sent")
        dirs = fresh()
        zed, goose = dirs["zed"] / "settings.json", dirs["goose"] / "config.yaml"
        for app, p in (("zed", zed), ("goose", goose)):
            serve.setup_apply(app, "both", "", "my-app")                     # no key: the header holds the label
            serve.setup_apply(app, "both", "", "my-app", True)
            text = p.read_text(encoding="utf-8")
            if "Bearer my-app" in text or serve.KEY_HOLDER not in text:
                bad.append(f"{app}: a label was kept where the key goes: {text!r}")
        return bad

    def linked():
        """A symlinked settings.json: written through to its target, the link kept."""
        dirs = fresh()
        real, link = homes[-1] / "dotfiles" / "zed-settings.json", dirs["zed"] / "settings.json"
        put(real, '{"theme": "One"}')
        link.parent.mkdir(parents=True, exist_ok=True)
        try:
            link.symlink_to(real)
        except OSError:
            return None                                  # Windows without admin or Developer Mode
        r = serve.setup_apply("zed", "both", "", "")
        got = json.loads(real.read_text(encoding="utf-8"))
        return [] if r["ok"] and link.is_symlink() and got.get("theme") == "One" and "context_servers" in got \
            else [f"{r} link={link.is_symlink()} target={got}"]

    try:
        for label, fn in (("Set up: a JSON root of another shape is left by hand", shapes),
                          ("Set up: a folder where a file goes is left by hand", folder),
                          ("Set up: a write that fails says what was written and what was not", half),
                          ("Set up: a key, then none, leaves no key behind", unkeyed),
                          ("Set up: the key placeholder in Zed's and goose's files is said by hand", placeholder),
                          ("Set up: a key typed in place of the placeholder is kept by the next Set up", typed),
                          ("Set up: a symlinked file is written through, the link kept", linked)):
            try:
                bad = fn()
            except Exception as exc:                     # noqa: BLE001  a crash is this check failing
                bad = [f"{type(exc).__name__}: {exc}"]
            if bad is None:
                print("skip " + label + ": this OS refused to make a symlink (admin or Developer Mode needed)")
                continue
            print(("ok   " if not bad else "FAIL ") + label + ("" if not bad else f" -> {bad}"))
            failures += 0 if not bad else 1
    finally:
        serve.guard.keys_required, serve._write_atomic = kept_keys, kept_write
        for k, v in saved_env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        for h in homes:
            shutil.rmtree(h, ignore_errors=True)
    # restore: same alias back, out of retired.json, role and ad hoc kept, and
    # back into the collections that still exist (one deleted, one changed since)
    serve.delete_collection("doomed")
    serve.bench_collections.FILE.write_text(json.dumps({**json.loads(serve.bench_collections.FILE.read_text(
        encoding="utf-8")), "member": {"members": [other, "someone-new"], "merger": ""}}), encoding="utf-8")
    r = serve.restore_model(victim)
    retired = json.loads((cfg / "retired.json").read_text(encoding="utf-8"))
    roles = json.loads((cfg / "model-roles.json").read_text(encoding="utf-8"))
    ok = (r["ok"] and serve.registered().get(served) == victim and victim not in retired
          and roles["models"].get(victim) == new_role and victim in roles["ad_hoc"])
    print(("ok   " if ok else "FAIL ") + f"restored {victim}" + ("" if ok else f" -> {r}"))
    failures += 0 if ok else 1
    back = json.loads((cfg / "collections.json").read_text(encoding="utf-8"))
    ok = (back == {"with-it": {"members": [other, victim], "merger": victim, "reply": "merged"},
                   "member": {"members": [other, "someone-new", victim], "merger": ""},
                   "without": colls["without"]}
          and "config/collections.json" in r["changed"])
    print(("ok   " if ok else "FAIL ") + "restored into the collections that still exist"
          + ("" if ok else f" -> {back} {r['changed']}")); failures += 0 if ok else 1
    # One that was in no collection: collections.json untouched and not reported.
    (cfg / "collections.json").write_text(json.dumps({"without": colls["without"]}), encoding="utf-8")
    r = serve.remove_model(victim, "")              # no reason given: the popup confirmed it
    gone = json.loads((cfg / "retired.json").read_text(encoding="utf-8"))[victim]
    ok = (r["ok"] and "config/collections.json" not in r["changed"]
          and "collections" not in gone and gone["why"] == "retired by hand")
    r2 = serve.restore_model(victim)
    ok = ok and r2["ok"] and "config/collections.json" not in r2["changed"]
    print(("ok   " if ok else "FAIL ") + "in no collection: collections.json not touched"
          + ("" if ok else f" -> {r} {r2}")); failures += 0 if ok else 1
    ok = not serve.restore_model(victim)["ok"]
    print(("ok   " if ok else "FAIL ") + "restore refused when not retired"); failures += 0 if ok else 1
    serve.remove_model(victim, "retired again for the onboarding checks")
    # onboarding refusals (nothing is started)
    for label, kw in (("onboard: empty", dict(served="")),
                      ("onboard: spaces", dict(served="vendor/model x")),
                      ("onboard: already registered", dict(served=d3[other]["served"])),
                      ("onboard: retired served model", dict(served=served)),
                      ("onboard: shell characters", dict(served='m" & calc & "')),
                      ("onboard: unknown endpoint", dict(served="vendor/m", endpoint="nope"))):
        r = serve.start_onboard(**kw)
        ok = not r["ok"] and "log" not in r
        print(("ok   " if ok else "FAIL ") + label + ("" if ok else f" -> {r}")); failures += 0 if ok else 1
    print(("ok   " if serve.onboard_status("../etc/passwd")["ok"] is False else "FAIL ") + "onboard log name guarded")
    # How an onboarding stopped: shown for a registered model until it answers.
    serve.ONBOARD_ISSUES_PATH.parent.mkdir(parents=True, exist_ok=True)
    serve.ONBOARD_ISSUES_PATH.write_text(json.dumps({
        other: {"at": "2026-10-22T16:43:34+02:00", "step": "availability probe", "failure": "capacity",
                "detail": "429 free-models-per-day", "log": ""},
        "gone-model": {"failure": "structural"}}), encoding="utf-8")
    shown = serve.onboard_issues({other: ("throttled", "warn", "")})
    ok = list(shown) == [other] and shown[other]["label"] == "throttled" and shown[other]["css"] == "warn"
    ok = ok and not serve.onboard_issues({other: ("healthy", "ok", "")})      # answered since: gone
    print(("ok   " if ok else "FAIL ") + "onboard issues: registered and not answering only"
          + ("" if ok else f" -> {shown}")); failures += 0 if ok else 1
    serve.ONBOARD_ISSUES_PATH.unlink()
    # endpoints: refused before any network call or write
    for label, args in (("endpoint: bad name", ("Bad Name", "https://x.example/v1", "")),
                        ("endpoint: bad url", ("good", "ftp://x", "")),
                        ("endpoint: default not removable", None)):
        r = serve.save_endpoint(*args) if args else serve.remove_endpoint(serve.endpoints.default())
        ok = not r["ok"]
        print(("ok   " if ok else "FAIL ") + label); failures += 0 if ok else 1

    # Removing an endpoint with a model on it: refused unless asked, then the
    # model retires with it, and Restore will not move it to another endpoint.
    eps = json.loads((cfg / "endpoints.json").read_text(encoding="utf-8"))
    eps["edit-test-ep"] = {"base": "https://edit-test.example/v1", "key": "edit-test-ep"}
    (cfg / "endpoints.json").write_text(json.dumps(eps), encoding="utf-8")
    serve.YAML_PATH.write_text(serve.read(serve.YAML_PATH).rstrip("\n") + "\n"
                               + serve.endpoints.yaml_block("edit-test-m", "vendor/edit-test", "edit-test-ep"),
                               encoding="utf-8")
    refused = serve.remove_endpoint("edit-test-ep")
    r = serve.remove_endpoint("edit-test-ep", with_models=True)
    back = serve.restore_model("edit-test-m")
    ok = (not refused["ok"] and "1 model" in refused["errors"][0]
          and r["ok"] and r["retired"] == ["edit-test-m"] and "edit-test-ep" not in serve.endpoints.load()
          and "edit-test-m" in json.loads((cfg / "retired.json").read_text(encoding="utf-8"))
          and "edit-test-m" not in serve.model_directory()
          and not back["ok"] and "was removed" in back["errors"][0])
    print(("ok   " if ok else "FAIL ") + "endpoint removed with its model; restore refused without it"
          + ("" if ok else f" -> {refused} {r} {back}"))
    failures += 0 if ok else 1

    # Add's Provider box links models.dev's doc, so only an https:// one reaches the page.
    import sources
    real_cached = sources.cached
    sources.cached = lambda feed: {"good": {"api": "https://a.example/v1", "doc": "https://a.example/docs"},
                                   "js": {"api": "https://b.example/v1", "doc": "javascript:alert(1)"},
                                   "plain": {"api": "https://c.example/v1", "doc": "http://c.example/docs"},
                                   "odd": {"api": "https://d.example/v1", "doc": ["https://d.example"]}} \
        if feed == "modelsdev" else real_cached(feed)
    try:
        docs = {p["id"]: p["doc"] for p in serve.endpoint_providers()["providers"]}
    finally:
        sources.cached = real_cached
    ok = docs == {"good": "https://a.example/docs", "js": "", "plain": "", "odd": ""}
    print(("ok   " if ok else "FAIL ") + "provider docs: https:// links only" + ("" if ok else f" -> {docs}"))
    failures += 0 if ok else 1

    # Call options: max_tokens set, refused above the model's max output or as
    # a fraction or for no model, then cleared; the llm CLI's copy is stubbed.
    told, real_info, llm_option = [], serve.catalog.info, serve._llm_option
    serve._llm_option = lambda alias, name, value: told.append((alias, name, value)) or "stub"
    serve.catalog.info = lambda a: {**real_info(a), "max_output": 50_000}
    chat = next(a for a, m in sorted(serve.model_directory().items()) if m.get("kind") != "decision")
    before = json.loads(serve.OPTIONS_PATH.read_text(encoding="utf-8"))
    try:
        put = serve.save_model_options(chat, 31_337)
        mid = json.loads(serve.OPTIONS_PATH.read_text(encoding="utf-8"))
        refused = [serve.save_model_options(chat, 60_000), serve.save_model_options(chat, 12.5),
                   serve.save_model_options("no-such-model", 100), serve.save_model_options(chat, True)]
        gone = serve.save_model_options(chat, None)
        after = json.loads(serve.OPTIONS_PATH.read_text(encoding="utf-8"))
    finally:
        serve.catalog.info = real_info
    ok = (put["ok"] and mid[chat]["max_tokens"] == 31_337 and not any(r["ok"] for r in refused)
          and gone["ok"] and "max_tokens" not in after.get(chat, {}) and after.get("_why") == before.get("_why")
          and told == [(chat, "max_tokens", 31_337), (chat, "max_tokens", None)])
    print(("ok   " if ok else "FAIL ") + f"call options for {chat}" + ("" if ok else f" -> {put} {refused} {gone} {told}"))
    failures += 0 if ok else 1

    # The scheduler: a mode and the jobs turned off, nothing else written.
    import scheduler
    scheduler.CONFIG = cfg / "scheduler.json"
    bad = [serve.save_scheduler("sometimes", {}), serve.save_scheduler("off", {"nope": True})]
    good = serve.save_scheduler("log-only", {"perf": False, "suite": True})
    conf = scheduler.settings()
    ok = (not any(r["ok"] for r in bad) and good["ok"]
          and conf == {"mode": "log-only", "jobs": {"perf": False}} and "perf" not in scheduler.enabled(conf))
    print(("ok   " if ok else "FAIL ") + "scheduler mode and jobs" + ("" if ok else f" -> {bad} {good} {conf}"))
    failures += 0 if ok else 1

    # The model client: chosen here unless BENCH_CLIENT is set; llm only where installed.
    import client as model_client
    model_client.CHOICE = cfg / "client.json"
    env, real_which = os.environ.pop("BENCH_CLIENT", None), shutil.which
    ran, real_run = [], serve.subprocess.run
    try:
        shutil.which = lambda name: "/usr/bin/" + name                 # llm installed: still built-in
        first = model_client.chosen()
        shutil.which = lambda name: None                               # not installed (npm, a container)
        bare = model_client.chosen()
        shutil.which = real_which
        made = serve.save_client("builtin")
        picked = (model_client.chosen(), model_client.backend())
        os.environ["BENCH_CLIENT"] = "llm"
        pinned = (model_client.chosen(), serve.save_client("builtin")["ok"])
        del os.environ["BENCH_CLIENT"]
        shutil.which = lambda name: None
        no_llm = serve.save_client("llm")["ok"]
        shutil.which = real_which
        unknown = serve.save_client("curl")["ok"]
        # With the built-in client nothing is mirrored into the llm CLI (5-6 s
        # a save); choosing the llm CLI syncs it once.
        serve.subprocess.run = lambda args, **kw: ran.append(args) or serve.subprocess.CompletedProcess(args, 0, "", "")
        quiet = (serve._sync_cli(), llm_option(chat, "max_tokens", 5), list(ran))
        shutil.which = lambda name: "/usr/bin/" + name
        switched, again = serve.save_client("llm"), serve.save_client("llm")
    finally:
        serve.subprocess.run, shutil.which = real_run, real_which
        os.environ.pop("BENCH_CLIENT", None)
        if env is not None:
            os.environ["BENCH_CLIENT"] = env
    ok = (first == ("builtin", "default") and bare == ("builtin", "default") and made["ok"] and picked == (("builtin", "settings"), "builtin")
          and pinned == (("llm", "environment"), False) and not no_llm and not unknown)
    print(("ok   " if ok else "FAIL ") + "model client choice" + ("" if ok else f" -> {first} {made} {picked} {pinned}"))
    failures += 0 if ok else 1
    # Saving it again syncs again: the way to retry a failed sync.
    ok = (quiet == (serve.NOT_SYNCED, serve.NOT_SYNCED, []) and switched["sync"] == "synced to the llm CLI"
          and again["sync"] == "synced to the llm CLI" and len(ran) == 2
          and all(str(r[-1]).endswith("sync_config.py") for r in ran))
    print(("ok   " if ok else "FAIL ") + "llm CLI synced only when it is the client" + ("" if ok else f" -> {quiet} {switched} {ran}"))
    failures += 0 if ok else 1

    # Free only in their own app: only what differs from the built-in is written; bad input refused.
    import sources
    real_conn, endpoints_mod = serve.endpoints.CONNECTIONS, serve.endpoints
    endpoints_mod.CONNECTIONS = cfg / "connections.json"
    try:
        endpoints_mod.CONNECTIONS.write_text('{"paid": ["x"]}', encoding="utf-8")
        off = serve.save_own_client_free({"opencode": False, "opencode-go": True})["ok"]
        wrote = json.loads(endpoints_mod.CONNECTIONS.read_text(encoding="utf-8"))
        seen_off = sources.free_in_own_client_only()["opencode"]
        back = serve.save_own_client_free({"opencode": True, "opencode-go": True})["ok"]
        cleared = json.loads(endpoints_mod.CONNECTIONS.read_text(encoding="utf-8"))
        refused = (serve.save_own_client_free({"no-such-provider-x": True})["ok"],
                   serve.save_own_client_free({"opencode": "no"})["ok"], serve.save_own_client_free(["opencode"])["ok"])
        card = serve.own_client_card()
    finally:
        endpoints_mod.CONNECTIONS = real_conn
    ok = (off and wrote == {"paid": ["x"], "free_in_own_client_only": {"opencode": False}} and seen_off is False
          and back and cleared == {"paid": ["x"]} and refused == (False, False, False)
          and "value='opencode'" in card and "oc-save" in card)
    print(("ok   " if ok else "FAIL ") + "free only in their own app" + ("" if ok else f" -> {wrote} {cleared} {refused}"))
    failures += 0 if ok else 1

    # The onboarding queue, with onboard.py faked: one at a time, Stop skips the
    # rest, each outcome read from its exit and log, Clear forgets a finished batch.
    import threading
    import time
    hold = threading.Event()
    runs = tmp / "runs"
    runs.mkdir(exist_ok=True)
    outcome = {"a": (0, "  [1/5] served\n  [3/5] synced\n"), "ok": (0, ""),
               "quiet": (0, "  STOPPING: it answered nothing.\n"),
               "bad": (1, "bad is not served.\n")}
    order = []

    class Proc:
        def __init__(self, served):
            self.served = served

        def wait(self):
            if self.served == "a":
                hold.wait(10)
            return outcome[self.served][0]

    modes = {}

    def fake_spawn(served, endpoint="", who="ui", mode="full", vision=False):
        order.append(served)
        modes[served] = mode + (" vision" if vision else "")
        (runs / f"onboard-{served}.log").write_text(f"onboarding {served} from e  ->  alias {served}-x\n"
                                                    + outcome[served][1], encoding="utf-8")
        return {"ok": True, "errors": [], "log": f"onboard-{served}.log"}, Proc(served)

    def settle():
        for _ in range(200):
            if not serve.onboard_queue()["running"]:
                return
            time.sleep(0.05)

    kept = (serve._spawn_onboard, serve._onboard_refusal, serve.DATA)
    serve._spawn_onboard, serve._onboard_refusal, serve.DATA = fake_spawn, lambda s, e, m="full": [], tmp
    try:
        first = serve.queue_onboard([{"served": "a", "endpoint": "e"}, {"served": "ok", "endpoint": "e"}])
        twice = serve.queue_onboard([{"served": "ok", "endpoint": "e"}])["ok"]
        for _ in range(200):                             # Stop once `a` is under way, as a person would
            if order:
                break
            time.sleep(0.05)
        stop = serve.stop_onboard_queue()
        stopping = stop["stopping"] and [i["state"] for i in stop["items"]] == ["running", "skipped"]
        busy_clear = serve.clear_onboard_queue()["ok"]
        after_stop = serve.queue_onboard([{"served": "quiet", "endpoint": "e"}])["ok"]   # picked after Stop: runs
        step = None                                      # the running model's step, from its log
        for _ in range(200):
            run = [i for i in serve.onboard_queue()["items"] if i["state"] == "running"]
            if run and run[0].get("step"):
                step = run[0]["step"]
                break
            time.sleep(0.05)
        hold.set()
        settle()
        stopped = {i["served"]: i["state"] for i in serve.onboard_queue()["items"]}
        order.remove("quiet")
        serve.queue_onboard([{"served": s, "endpoint": "e", "mode": m, "vision": s == "quiet"} for s, m in
                             (("ok", "full-adhoc"), ("quiet", "quick"), ("bad", "quick-adhoc"))])
        settle()
        batch = {i["served"]: (i["state"], i["alias"], bool(i["note"])) for i in serve.onboard_queue()["items"]}
        cleared = serve.clear_onboard_queue()["items"]
        bad_input = (serve.queue_onboard([])["ok"], serve.queue_onboard("ok")["ok"],
                     serve.stop_onboard_queue()["ok"])
    finally:
        hold.set()
        serve._spawn_onboard, serve._onboard_refusal, serve.DATA = kept
    ok = (first["ok"] and not twice and stopping and not busy_clear and after_stop and step == 3
          and stopped == {"a": "done", "ok": "skipped", "quiet": "no answer"} and order == ["a", "ok", "quiet", "bad"]
          and batch == {"ok": ("done", "ok-x", False), "quiet": ("no answer", "quiet-x", True),
                        "bad": ("failed", "bad-x", True)}
          and cleared == [] and bad_input == (False, False, False)
          and modes == {"a": "full", "ok": "full-adhoc", "quiet": "quick vision", "bad": "quick-adhoc"}
          and serve.ONBOARD_MODES["full-adhoc"] == ["--ad-hoc"])            # the suite now, then ad hoc
    print(("ok   " if ok else "FAIL ") + "onboarding queue" + ("" if ok else f" -> {stopped} {order} {batch} {modes}"))
    failures += 0 if ok else 1

    # Restart: refused unless serve.py's parent started it (nothing would start it
    # again); refused while a job runs, unless forced; then the server shuts down.
    import scheduler
    kept = os.environ.get("BENCH_SERVED"), serve.SERVER
    shut = []
    try:
        os.environ.pop("BENCH_SERVED", None)
        alone = serve.restart()
        os.environ["BENCH_SERVED"] = "1"
        serve.SERVER = type("Server", (), {"shutdown": lambda self: shut.append(1)})()
        scheduler.RUNNING["suite"] = True
        busy, forced = serve.restart(), serve.restart(force=True)
        time.sleep(0.8)                                  # the shutdown waits half a second
    finally:
        scheduler.RUNNING.pop("suite", None)
        serve.SERVER = kept[1]
        os.environ.pop("BENCH_SERVED", None) if kept[0] is None else os.environ.update(BENCH_SERVED=kept[0])
    ok = (not alone["ok"] and busy.get("busy") and not busy["ok"] and "suite" in busy["errors"][0]
          and forced["ok"] and forced["cut"] and shut == [1])
    print(("ok   " if ok else "FAIL ") + "restart" + ("" if ok else f" -> {alone} {busy} {forced} {shut}"))
    failures += 0 if ok else 1

    # Action needed: an alert whose models have all left the bench is not shown,
    # before the daily check rewrites the file (2026-10-28: a kilo connection
    # alert stayed after Retire all 4).
    kept_data, kept_reg = serve.DATA, serve.registered
    with tempfile.TemporaryDirectory() as tmp:
        (pathlib.Path(tmp) / "metrics").mkdir()
        (pathlib.Path(tmp) / "metrics" / "alerts.json").write_text(json.dumps({
            "connection:held:gone.example": {"kind": "connection", "message": "every model on gone.example is failing",
                                             "aliases": ["g1", "g2"], "first_seen": "2026-10-28"},
            "connection:held:here.example": {"kind": "connection", "message": "every model on here.example is failing",
                                             "aliases": ["h1", "g1"], "first_seen": "2026-10-28"},
            "retiring:x:old": {"kind": "retiring", "alias": "old", "message": "old retires", "first_seen": "2026-10-28"}}),
            encoding="utf-8")
        serve.DATA, serve.registered = pathlib.Path(tmp), lambda: {"h1-served": "h1"}
        try:
            shown = serve.actions_page(*serve.open_actions())
        finally:
            serve.DATA, serve.registered = kept_data, kept_reg
    ok = "here.example" in shown and "gone.example" not in shown and "old retires" not in shown
    print(("ok   " if ok else "FAIL ") + "action needed drops alerts on retired models")
    failures += 0 if ok else 1

    # Retry is one request: no client retries after a 429 (2026-11-02: each
    # UnoRouter Retry waited its 60 s and sent a second, refused, request).
    import run
    asked, kept_ask = {}, run.ask_once

    def fake_ask(*a, **kw):
        asked.update(kw)
        raise StopIteration                      # stop before anything is written
    run.ask_once = fake_ask
    try:
        serve.retry_model(next(iter(serve.registered().values())))
    except StopIteration:
        pass
    finally:
        run.ask_once = kept_ask
    ok = asked.get("retries") == 0
    print(("ok   " if ok else "FAIL ") + "Retry sends one request, no client retries" + ("" if ok else f" -> {asked}"))
    failures += 0 if ok else 1

    # A provider's words end where its link begins, and are not cut short here:
    # the page cuts them to a line, and a phone, with no hover, shows them whole.
    text, url = serve.said("Error: Error code: 403 - {'error': {'message': 'm needs your consent. Give it once at"
                           " https://p.example/consents?model=m; it covers every model.'}}")
    long, _ = serve.said("Error: Error code: 429 - {'error': {'message': 'Too many requests. " + "w " * 80 + "end'}}")
    ok = text.endswith("Give it once at") and url == "https://p.example/consents?model=m" and long.endswith("end")
    print(("ok   " if ok else "FAIL ") + "action needed shows a provider's words whole, its link after them")
    failures += 0 if ok else 1

    # Dismiss: one picker key or many in one write, a change-log line each;
    # undo brings back only the ones named; nothing named is refused.
    serve.DISMISSED_PATH = cfg / "dismissed.json"
    seen = len(serve.CHANGES_PATH.read_text(encoding="utf-8").splitlines())
    many, one = serve.dismiss(["vendor/a", "vendor/b@openrouter"]), serve.dismiss("vendor/c")
    gone = set(json.loads(serve.DISMISSED_PATH.read_text(encoding="utf-8")))
    back = serve.dismiss(["vendor/a", "vendor/c"], undo=True)
    left = set(json.loads(serve.DISMISSED_PATH.read_text(encoding="utf-8")))
    log = [(e["field"], e["new"]) for e in map(json.loads, serve.CHANGES_PATH.read_text(encoding="utf-8").splitlines()[seen:])]
    refused = tuple(serve.dismiss(x)["ok"] for x in ([], "  ", None, [3]))
    ok = (many["ok"] and one["ok"] and back["ok"] and gone == {"vendor/a", "vendor/b@openrouter", "vendor/c"}
          and left == {"vendor/b@openrouter"} and refused == (False, False, False, False)
          and log == [("dismissed", "vendor/a"), ("dismissed", "vendor/b@openrouter"), ("dismissed", "vendor/c"),
                      ("undismissed", "vendor/a"), ("undismissed", "vendor/c")])
    print(("ok   " if ok else "FAIL ") + "dismiss in bulk, and undo" + ("" if ok else f" -> {gone} {left} {log} {refused}"))
    failures += 0 if ok else 1

    # The member picker's provider chips: a proxy's vendor, else the bare host.
    names = {"nvidia-api": "nvidia", "api.unorouter.com": "unorouter", "router.requesty.ai": "requesty",
             "cli-proxy-api/codex": "codex", "openrouter": "openrouter", "apihub.agnes-ai.com": "agnes-ai"}
    got = {t: serve.provider_label(t) for t in names}
    ok = got == names
    print(("ok   " if ok else "FAIL ") + "provider chip names" + ("" if ok else f" -> {got}"))
    failures += 0 if ok else 1

    # An answer clears every alert it disproves (2026-11-02: big-pickle answered a
    # Retry and stayed listed, its unrecognised-failure alert open), never a price;
    # a held connection loses the model, and goes with its last one.
    alerts.raise_alert("model:unclassified:t/served", "registration", "t fails oddly")
    alerts.raise_alert("model:pending:t/served", "retiring", "t retires", alias="t")
    alerts.raise_alert("model:paid:t/served", "went paid", "t has a price", alias="t")
    alerts.raise_alert("model:pending:t/served-too", "retiring", "another retires", alias="t2")
    alerts.raise_alert("connection:held:x", "connection", "all fail", aliases=["t", "t2"])
    alerts.raise_alert("connection:held:y", "connection", "all fail", aliases=["t"])
    cleared = alerts.answered("t", "t/served")
    left = alerts.active()
    ok = (set(cleared) == {"model:unclassified:t/served", "model:pending:t/served", "connection:held:y"}
          and "model:paid:t/served" in left and "model:pending:t/served-too" in left
          and left["connection:held:x"]["aliases"] == ["t2"])
    for k in ("model:paid:t/served", "model:pending:t/served-too", "connection:held:x"):
        alerts.clear(k)
    print(("ok   " if ok else "FAIL ") + "an answer clears what it disproves, not a price" + ("" if ok else f" -> {cleared}"))
    failures += 0 if ok else 1

    # Got it clears a notice (NVIDIA changed its figures), never a state still wrong.
    alerts.raise_alert("test:notice", "limits", "a notice", dismiss=True)
    alerts.raise_alert("test:state", "limits", "a state")
    ok = (serve.dismiss_alert("test:notice")["ok"] and not serve.dismiss_alert("test:state")["ok"]
          and "test:notice" not in alerts.active() and "test:state" in alerts.active())
    alerts.clear("test:state")
    print(("ok   " if ok else "FAIL ") + "Got it dismisses a notice, not a state")
    failures += 0 if ok else 1

    # The owner's files are as they were. alerts.json can change under a
    # running suite, so only what this test could do counts: the victim's
    # alerts cleared from the real file.
    real_alerts = DATA / "metrics" / "alerts.json"
    victim_keys = {k for k in json.loads(real[real_alerts] or b"{}") if k.endswith(f":{served}")}
    ok = (real[DATA / "config" / "endpoints.json"] == (DATA / "config" / "endpoints.json").read_bytes()
          and victim_keys <= set(json.loads(real_alerts.read_bytes() or b"{}"))
          and real_collections == ((DATA / "config" / "collections.json").read_bytes()
                                   if (DATA / "config" / "collections.json").exists() else None))
    print(("ok   " if ok else "FAIL ") + "the real alerts, endpoints and collections were not touched")
    failures += 0 if ok else 1

    # The Logs page reads a log from its end, and still gets whole lines.
    log = cfg / "big.log"
    lines = [f"{i} " + "x" * 2000 for i in range(400)]
    log.write_text("\n".join(lines) + "\n\n", encoding="utf-8")
    ok = (serve._log_rows(log) == lines[::-1][:200] and serve._log_rows(log, 100) == lines[::-1][:100]
          and serve._log_rows(log.with_name("none.log")) == [] and list(serve._from_end(log)) == lines[::-1])
    print(("ok   " if ok else "FAIL ") + "a log's last lines, read from its end")
    failures += 0 if ok else 1

    # Confidently wrong is scored again when a task or a saved answer changes.
    answer = cfg / "tasks" / "t" / "output" / "m.md"
    answer.parent.mkdir(parents=True)
    (cfg / "tasks" / "t" / "task.json").write_text("{}", encoding="utf-8")
    answer.write_text("a", encoding="utf-8")
    before = serve._tasks_key(cfg / "tasks")
    answer.write_text("ab", encoding="utf-8")
    ok = len(before) == 2 and serve._tasks_key(cfg / "tasks") != before and serve._tasks_key(cfg / "none") == ()
    print(("ok   " if ok else "FAIL ") + "a changed answer changes the scoring's key")
    failures += 0 if ok else 1

    # Suite scores and statuses are worked out once a render; what one pane
    # changes in its copy, the next does not see.
    (serve.DATA / "runs" / "suite-2026-01-01.md").write_text("| t1 | `m1` | 1/2 | — | 3.0 |\n", encoding="utf-8")
    with serve.locked.snapshot():
        serve.model_status().clear()
        for v in serve.suite_scores().values():
            v.clear()
        ok = (serve.model_status() == serve._model_status() and serve._suite_scores()
              and serve.suite_scores() == serve._suite_scores())
    print(("ok   " if ok else "FAIL ") + "suite scores and statuses once a render, a copy each")
    failures += 0 if ok else 1

    # Trend: a model against itself, task by task. A drop in two runs is a trend;
    # the same drop in one run, or against one earlier run, is not. A retried row
    # ("2.0 (x2)") is a score, and a renamed alias goes by its current name.
    import datetime as dt
    hist = json.loads(serve.HISTORY_PATH.read_text(encoding="utf-8"))
    serve.HISTORY_PATH.write_text(json.dumps({**hist, "tr-old": "tr-down"}), encoding="utf-8")
    aliases.forget()
    today = dt.date.today()

    def note(ago, rows):
        day = (today - dt.timedelta(days=ago)).isoformat()
        with (serve.DATA / "runs" / f"suite-{day}.md").open("a", encoding="utf-8") as fh:
            fh.write(f"\n## Suite run {day}\n\n| Task | Model | Found | Invented | Seconds |\n| --- | --- | --- | --- | --- |\n"
                     + "".join(f"| {t} | `{m}` | {f} | — | 2.0 (x2) |\n" for t, m, f in rows))
    three = ("ta", "tb", "tc")
    note(50, [(t, m, "5/5") for m in ("tr-old", "tr-once", "tr-thin") for t in three] + [("ta", "tr-twice", "0/1")])
    note(50, [("ta", "tr-twice", "1/1")])               # the same task again that day: one score, the mean
    note(40, [(t, m, "5/5") for m in ("tr-old", "tr-once") for t in three] + [("ta", "tr-down", "timeout")])
    note(10, [("ta", "tr-down", "3/5"), ("tb", "tr-down", "2/5"), ("ta", "tr-thin", "2/5"), ("tb", "tr-thin", "2/5")]
         + [(t, "tr-once", "5/5") for t in three])
    note(3, [("tc", "tr-down", "3/5"), ("tc", "tr-thin", "2/5")] + [(t, "tr-once", "1/5") for t in three])
    note(10, [("ta", "tr-twice", "1/1")])
    # A note appended in the middle of a render is read from disk, not from that render's copy.
    with serve.locked.snapshot():
        serve.note_rows()
        note(0, [("ta", "tr-late", "1/1")])
        late = any(r[3] == "tr-late" for r in serve.note_rows())
    t = serve.trend_rows()["models"]
    w = {m: t[m]["windows"][serve.TREND_DAYS[0]] for m in ("tr-down", "tr-once", "tr-thin")}
    twice = t["tr-twice"]
    late = late and "tr-late" in t and not t["tr-late"]["compared"]
    # The chart's and the table's order: by flag, the biggest move first; too little by name.
    flags = {"x": ("higher", 0.1), "y": ("higher", 0.3), "z": ("lower", -0.1), "b": ("too little", 0.1), "a": ("too little", 0.9)}
    order = sorted(flags, key=lambda m: serve._trend_order(m, dict(zip(("state", "change"), flags[m]))))
    ok = ({m: s["state"] for m, s in w.items()} == {"tr-down": "lower", "tr-once": "steady", "tr-thin": "too little"}
          and [[k, round(v, 9)] for k, v in w["tr-down"]["per"]] == [["tb", -0.6], ["ta", -0.4], ["tc", -0.4]]
          and order == ["z", "y", "x", "a", "b"]
          and (w["tr-down"]["tasks"], w["tr-down"]["down"], w["tr-down"]["runs"], w["tr-down"]["before"]) == (3, 3, 2, 2)
          and abs(w["tr-down"]["change"] + 1.4 / 3) < 1e-9 and w["tr-once"]["runsDown"] == 1
          and "tr-old" not in t and t["tr-down"]["last"] == (today - dt.timedelta(days=3)).isoformat()
          and ("ta", "tr-down") in serve._suite_scores() and serve.trend_rows() is serve.trend_rows()
          and late and twice["compared"] and twice["last"] == (today - dt.timedelta(days=10)).isoformat()
          and abs(twice["windows"][serve.TREND_DAYS[0]]["change"] - 0.5) < 1e-9)   # day 50's two scores: one mean
    print(("ok   " if ok else "FAIL ") + "trend: a model against itself, two runs to be a trend"
          + ("" if ok else f" -> {w} {twice} late {late}"))
    failures += 0 if ok else 1

    # An address per page: / and /benchmarks are Benchmarks, /<key> the others,
    # anything else the home page; each page's link is its address.
    ok = (serve.page_for("/") == serve.page_for("/benchmarks") == serve.page_for("/nope") == "suite"
          and serve.page_for("/models") == serve.page_for("/models/") == "models"
          and all(serve.page_for(serve.href(k)) == k for k, _, _ in serve.TABS))
    print(("ok   " if ok else "FAIL ") + "an address per page, and the home page for any other")
    failures += 0 if ok else 1

    # gzip for a caller off this machine that takes it; never over loopback.
    import gzip

    class Reply:
        def __init__(self, address, accept):
            self.headers, self.client_address, self.sent = {"Accept-Encoding": accept}, (address, 1), {}

        def send_header(self, k, v):
            self.sent[k] = v
    body = b"<p>a page</p>" * 500
    far, near, plain = Reply("203.0.113.20", "gzip, deflate, br"), Reply("::ffff:127.0.0.1", "gzip"), Reply("10.0.0.2", "")
    refused, other = Reply("10.0.0.3", "gzip;q=0, identity"), Reply("10.0.0.4", "x-gzip-not")
    ok = (gzip.decompress(serve.Handler._length(far, body)) == body and far.sent["Content-Encoding"] == "gzip"
          and int(far.sent["Content-Length"]) < len(body) and serve.Handler._length(near, body) == body
          and serve.Handler._length(plain, body) == body and serve.Handler._length(refused, body) == body
          and serve.Handler._length(other, body) == body
          and "Content-Encoding" not in {**near.sent, **plain.sent, **refused.sent, **other.sent}
          and plain.sent["Content-Length"] == str(len(body)))
    print(("ok   " if ok else "FAIL ") + "gzip off this machine only" + ("" if ok else f" -> {far.sent} {near.sent}"))
    failures += 0 if ok else 1

    # Uptime counts each call once: a gateway seat from its log, a probe from its
    # row, the suite's calls from the ledger only after its last failure kept
    # without text; the ledger's own lines for the gateway and the probe are skipped.
    import datetime as dt
    import ledger
    import time
    home = serve.DATA                                   # `tmp` names another folder by now
    ledger.DIR = home / "usage"
    ledger.DIR.mkdir()
    now = time.time()
    at = dt.datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds")
    (home / "runs" / "gateway").mkdir(parents=True, exist_ok=True)
    seat = {"call": "p.member1", "seat": "member", "alias": "m1", "status": "ok", "answer": "yes", "error": "", "seconds": 2}
    panel = {"at": at, "shape": "design", "seats": [
        seat, {**seat, "status": "error", "answer": "", "error": "Error: Error code: 503 - high demand"},
        {**seat, "status": "error", "answer": "", "error": "Error: Error code: 429 - Daily token quota exceeded."}]}
    calls = home / "runs" / "gateway" / "calls.jsonl"
    calls.write_text(json.dumps(panel) + "\n", encoding="utf-8")
    (home / "perf").mkdir(exist_ok=True)
    (home / "perf" / "history.csv").write_text(
        "timestamp,model,prompt,n,p50_s,p95_s,min_s,max_s,chars_per_s,answered,refused,error,timeout,failure,detail\n"
        f"{at[:19]},m1,tiny,1,1.5,1.5,1.5,1.5,0,1,0,0,0,,\n", encoding="utf-8")

    def line(dt_s, caller, status, **more):
        return json.dumps({"t": now + dt_s, "at": at, "alias": "m1", "connection": "c1", "endpoint": "e",
                           "served": "m1", "status": status, "attempts": 1, "seconds": 3.0, "caller": caller,
                           "purpose": "", **more}, separators=(",", ":")) + "\n"
    ledger._file(now).write_text(
        line(-30, "serve", "ok") + line(-20, "suite/run", "ok") + line(-10, "suite/run", "error")
        + line(10, "suite/run", "ok") + line(20, "suite/run", "error", error="Error: Error code: 503 - overloaded"),
        encoding="utf-8")
    first = serve._uptime_rows()["models"]["m1"][-1]
    with calls.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({**panel, "seats": [seat]}) + "\n")
    again = serve._uptime_rows()
    ok = (first[:3] == [3, 2, 1] and again["models"]["m1"][-1][:3] == [4, 2, 1]
          and again["providers"]["c1"][-1][:3] == [4, 2, 1] and first[3] == {"busy": 2, "our quota": 1})
    print(("ok   " if ok else "FAIL ") + "uptime counts each call once, the new ones only"
          + ("" if ok else f" -> {first} {again['models']['m1'][-1]}"))
    failures += 0 if ok else 1

    # Decision models in Uptime, from their own log; and an error no rule knows, counted as a
    # decision model said, marked so in the hover and the list (never asked here).
    real_decide = decide.FILE, decide.LOG, decide.DECIDED, decide.RESULTS
    decide.FILE, decide.LOG = home / "config" / "decision-models.json", home / "metrics" / "decisions.jsonl"
    decide.DECIDED, decide.RESULTS = home / "metrics" / "decided.json", home / "metrics" / "decide-bench.json"
    decide.FILE.write_text(json.dumps({"dmA": {"served": "dmA", "endpoint": "e", "via": "e",
                                               "question_types": ["choice", "noul"]},
                                       "dmB": {"served": "v/dmB:free", "endpoint": "e", "via": "e",
                                               "question_types": ["choice"]}}),
                           encoding="utf-8")
    decide.LOG.write_text(
        json.dumps({"at": at, "alias": "dmB", "served": "v/dmB:free", "via": "e", "answers": {"q": {}}, "error": "",
                    "seconds": 0.4}) + "\n"
        + json.dumps({"at": at, "alias": "dmB", "served": "v/dmB:free", "via": "e", "answers": None,
                      "error": "404 No endpoints found for v/dmB:free.", "seconds": 0.1}) + "\n", encoding="utf-8")
    odd_error = "Error: the gremlins ate it"
    decide.DECIDED.write_text(json.dumps({"errors": {decide.shape(odd_error): {"class": "busy", "confidence": 0.81,
                                                                                "by": "dmA"}},
                                          "roles": {"m1": {"role": "code", "confidence": 0.7, "by": "dmA"}}}),
                              encoding="utf-8")
    with calls.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({**panel, "seats": [{**seat, "status": "error", "answer": "", "error": odd_error}]}) + "\n")
    u = serve._uptime_rows()
    marked = "busy (decided by dmA at 0.81)"
    ok = (u["models"]["dmB"][-1][:4] == [1, 0, 1, {"setup: not found": 1}]
          and u["models"]["m1"][-1][3].get(marked) == 1 and serve._down(marked)
          and [o[4:] for o in u["odd"]] == [[serve._shape(odd_error), "busy, decided by dmA at 0.81"]]
          and any(r["name"] == "dmB" for r in serve.uptime_data()["models"])
          and "decided by dmA at 0.81" in serve.uptime_card())
    print(("ok   " if ok else "FAIL ") + "uptime: decision models' calls, and decided shapes marked"
          + ("" if ok else f" -> {u['models'].get('dmB')} {u['models']['m1'][-1]} {u['odd']}"))
    failures += 0 if ok else 1

    # The Decision models card: ranked from MIN_ITEMS items, said so below that; the role
    # suggestion reaches the popup's data, and the collection pick is saved beside the limits.
    cal = [{"from": 0.8, "n": 8, "said": 0.9, "right": 0.875}]
    decide.RESULTS.write_text(json.dumps({"updated": at, "models": {
        "dmA": {"errors": {"asked": 8, "answered": 8, "correct": 7, "accuracy": 0.875, "macro_f1": 0.8, "brier": 0.1,
                           "ece": 0.03, "calibration": cal, "confident": {"cutoff": 0.6, "coverage": 1.0, "accuracy": 0.875},
                           "seconds": 0.3, "failures": {}, "last": at}},
        "dmB": {"errors": {"asked": 1, "answered": 0, "correct": 0, "failures": {"setup": 1}, "seconds": 0.1,
                           "last": at}}}}), encoding="utf-8")
    html = serve.decision_card()
    data = serve.decision_data()
    ok = ("1 of 1" in html and "below 8 items" in html and "7/8" in html and "88% <span class='muted'>of 8"
          in html and data["points"] == [{"model": "dmA", "set": "errors", "said": 0.9, "right": 0.875, "n": 8}]
          and "best of 1 ranked on the errors set: 7 right of 8 asked" in html
          and json.loads(serve.directory_json())["suggested"]["m1"]["role"] == "code")
    print(("ok   " if ok else "FAIL ") + "decision card: ranks, below the floor, uses, role suggestion"
          + ("" if ok else f" -> {data}"))
    failures += 0 if ok else 1
    gw = serve.bench_collections.SETTINGS_FILE
    saved = [serve.save_gateway_settings(5, 2, pick_collection=False, pick_confidence=0.8),
             serve.save_gateway_settings(5, 2)]                       # not sent: left as they are
    kept = serve.bench_collections.pick_settings()
    saved.append(serve.save_gateway_settings(5, 2, pick_collection=True, pick_confidence=None))
    bad = [serve.save_gateway_settings(5, 2, pick_collection="yes"), serve.save_gateway_settings(5, 2, pick_confidence=0),
           serve.save_gateway_settings(5, 2, pick_confidence=True)]
    ok = (all(r["ok"] for r in saved) and not any(r["ok"] for r in bad)
          and kept == {"pick_collection": False, "pick_confidence": 0.8}
          and json.loads(gw.read_text(encoding="utf-8")).get("pick_collection") is True
          and "pick_confidence" not in json.loads(gw.read_text(encoding="utf-8")))
    print(("ok   " if ok else "FAIL ") + "gateway settings: the collection pick, on, off and its confidence"
          + ("" if ok else f" -> {saved} {bad} {kept}"))
    failures += 0 if ok else 1
    # Export and import: the settings and the endpoints' keys out of this copy, into
    # an empty install; a preview writes nothing; a name outside the scope is refused whole.
    import io
    import zipfile
    real_llm, there = serve.endpoints.LLM_DIR, serve.DATA
    serve.endpoints.LLM_DIR = there
    ep = next(e for e in serve.endpoints.load().values() if e.get("key"))
    (there / "keys.json").write_text(json.dumps({ep["key"]: "k-1", "unrelated": "k-2"}), encoding="utf-8")
    (cfg / "x.json.lock").write_text("", encoding="utf-8")
    plain, with_keys, evil = io.BytesIO(), io.BytesIO(), io.BytesIO()
    serve.export_zip("settings", False, plain)
    made = serve.export_zip("settings", True, with_keys)
    names = zipfile.ZipFile(plain).namelist()
    carried = json.loads(zipfile.ZipFile(with_keys).read("keys.json"))
    other = there / "other"
    other.mkdir()
    serve.DATA = serve.endpoints.LLM_DIR = other
    with_keys.seek(0)
    look = serve.import_zip(with_keys, apply=False)
    wrote_nothing = not (other / "config").exists()
    with_keys.seek(0)
    done = serve.import_zip(with_keys, apply=True)
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("manifest.json", json.dumps({"benchwarmers_export": 1, "scope": "settings"}))
        z.writestr("config/../../outside.txt", "x")
    evil.seek(0)
    refused = serve.import_zip(evil, apply=True)
    ok = ("manifest.json" in names and "keys.json" not in names and "config/endpoints.json" in names
          and not any(n.endswith(".lock") for n in names) and carried == {ep["key"]: "k-1"} and made["keys"] == 1
          and look["ok"] and look["replaces"] == 0 and look["keys"] == 1 and wrote_nothing
          and done["ok"] and (other / done["backup"]).is_file()
          and (other / "config" / "endpoints.json").read_bytes() == (cfg / "endpoints.json").read_bytes()
          and json.loads((other / "keys.json").read_text(encoding="utf-8")) == {ep["key"]: "k-1"}
          and not refused["ok"] and not (there / "outside.txt").exists() and not (there.parent / "outside.txt").exists())
    serve.DATA, serve.endpoints.LLM_DIR = there, real_llm
    print(("ok   " if ok else "FAIL ") + "export and import: settings and keys out, into an empty install, nothing outside"
          + ("" if ok else f" -> {names[:5]} {carried} {look} {done} {refused}"))
    failures += 0 if ok else 1
    decide.FILE, decide.LOG, decide.DECIDED, decide.RESULTS = real_decide
    shutil.rmtree(tmp, ignore_errors=True)
    print("\nall rules hold" if not failures else f"\n{failures} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
