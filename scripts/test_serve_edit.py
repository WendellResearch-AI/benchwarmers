"""Exercise serve.change_model against a COPY of the config, never the real one.

    python scripts/test_serve_edit.py

Each rule the popup enforces is tried once and must refuse; then a role change
and a rename are applied to the copy and checked. The llm sync is not run:
serve.ROOT points at the copy, which has no scripts/sync_config.py, so the
sync reports that it could not run. Alerts and endpoints point at copies too,
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

    # Call options: max_tokens set, refused above the model's max output or as
    # a fraction or for no model, then cleared; the llm CLI's copy is stubbed.
    told, real_info = [], serve.catalog.info
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
    try:
        shutil.which = lambda name: "/usr/bin/" + name                 # llm installed: the default is llm
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
    finally:
        shutil.which = real_which
        os.environ.pop("BENCH_CLIENT", None)
        if env is not None:
            os.environ["BENCH_CLIENT"] = env
    ok = (first == ("llm", "default") and bare == ("builtin", "default") and made["ok"] and picked == (("builtin", "settings"), "builtin")
          and pinned == (("llm", "environment"), False) and not no_llm and not unknown)
    print(("ok   " if ok else "FAIL ") + "model client choice" + ("" if ok else f" -> {first} {made} {picked} {pinned}"))
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

    def fake_spawn(served, endpoint="", who="ui"):
        order.append(served)
        (runs / f"onboard-{served}.log").write_text(f"onboarding {served} from e  ->  alias {served}-x\n"
                                                    + outcome[served][1], encoding="utf-8")
        return {"ok": True, "errors": [], "log": f"onboard-{served}.log"}, Proc(served)

    def settle():
        for _ in range(200):
            if not serve.onboard_queue()["running"]:
                return
            time.sleep(0.05)

    kept = (serve._spawn_onboard, serve._onboard_refusal, serve.DATA)
    serve._spawn_onboard, serve._onboard_refusal, serve.DATA = fake_spawn, lambda s, e: [], tmp
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
        serve.queue_onboard([{"served": s, "endpoint": "e"} for s in ("ok", "quiet", "bad")])
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
          and cleared == [] and bad_input == (False, False, False))
    print(("ok   " if ok else "FAIL ") + "onboarding queue" + ("" if ok else f" -> {stopped} {order} {batch}"))
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
    shutil.rmtree(tmp, ignore_errors=True)
    print("\nall rules hold" if not failures else f"\n{failures} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
