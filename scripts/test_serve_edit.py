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
    run10 = [r for r in t["tr-down"]["runs"] if r[0] == (today - dt.timedelta(days=10)).isoformat()]
    twice = [r[:3] for r in t["tr-twice"]["runs"]]
    late = late and "tr-late" in t
    # The chart's and the table's order: by flag, the biggest move first; too little by name.
    flags = {"x": ("higher", 0.1), "y": ("higher", 0.3), "z": ("lower", -0.1), "b": ("too little", 0.1), "a": ("too little", 0.9)}
    order = sorted(flags, key=lambda m: serve._trend_order(m, dict(zip(("state", "change"), flags[m]))))
    ok = ({m: s["state"] for m, s in w.items()} == {"tr-down": "lower", "tr-once": "steady", "tr-thin": "too little"}
          and [[k, round(v, 9)] for k, v in w["tr-down"]["per"]] == [["tb", -0.6], ["ta", -0.4], ["tc", -0.4]]
          and order == ["z", "y", "x", "a", "b"]
          and (w["tr-down"]["tasks"], w["tr-down"]["down"], w["tr-down"]["runs"], w["tr-down"]["before"]) == (3, 3, 2, 2)
          and abs(w["tr-down"]["change"] + 1.4 / 3) < 1e-9 and w["tr-once"]["runsDown"] == 1
          and "tr-old" not in t and len(run10) == 1 and abs(run10[0][1] + 0.5) < 1e-9 and run10[0][2] == 2
          and ("ta", "tr-down") in serve._suite_scores() and serve.trend_rows() is serve.trend_rows()
          and late and twice == [[(today - dt.timedelta(days=50)).isoformat(), None, 0],
                                 [(today - dt.timedelta(days=10)).isoformat(), 0.5, 1]])
    print(("ok   " if ok else "FAIL ") + "trend: a model against itself, two runs to be a trend"
          + ("" if ok else f" -> {w} {run10} {twice} late {late}"))
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
    decide.FILE, decide.LOG, decide.DECIDED, decide.RESULTS = real_decide
    shutil.rmtree(tmp, ignore_errors=True)
    print("\nall rules hold" if not failures else f"\n{failures} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
