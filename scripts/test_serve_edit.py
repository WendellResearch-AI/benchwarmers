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
    shutil.copy(DATA / "config" / "endpoints.json", cfg / "endpoints.json")
    serve.endpoints.FILE = cfg / "endpoints.json"
    serve.endpoints.YAML = serve.YAML_PATH
    real = {p: p.read_bytes() for p in (DATA / "metrics" / "alerts.json", DATA / "config" / "endpoints.json")}

    d = serve.model_directory()
    some = sorted(d)[0]
    retired = next(iter(json.loads((cfg / "alias-history.json").read_text())))
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
    hist = json.loads((cfg / "alias-history.json").read_text())
    roles = json.loads((cfg / "model-roles.json").read_text())["models"]
    held = json.loads((cfg / "model-roles.json").read_text())["ad_hoc"]
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
    lines = (cfg / "changes.jsonl").read_text().splitlines()
    print(("ok   " if len(lines) == 3 else "FAIL ") + f"{len(lines)} changes logged")
    failures += 0 if len(lines) == 3 else 1

    # removal
    victim = some + "-renamed"
    served = d2[victim]["served"]
    for label, kw in (("remove: alias not typed", dict(typed="nope", reason="retired by provider")),
                      ("remove: no reason", dict(typed=victim, reason="")),
                      ("remove: unknown alias", dict(typed="x", reason="whatever it is"))):
        r = serve.remove_model("no-such" if "unknown" in label else victim, **kw)
        ok = not r["ok"] and not r["changed"]
        print(("ok   " if ok else "FAIL ") + label); failures += 0 if ok else 1
    before = len(serve.registered())
    r = serve.remove_model(victim, victim, "retired by provider on 2026-09-15")
    d3 = serve.model_directory()
    retired = json.loads((cfg / "retired.json").read_text())
    yaml = (cfg / "extra-openai-models.yaml").read_text()
    ok = (r["ok"] and victim not in d3 and len(serve.registered()) == before - 1
          and retired[victim]["served"] == served and f"model_id: {victim}\n" not in yaml
          and victim not in json.loads((cfg / "model-roles.json").read_text())["models"]
          and victim not in json.loads((cfg / "model-roles.json").read_text())["ad_hoc"]
          and served not in serve.registered())
    print(("ok   " if ok else "FAIL ") + f"removed {victim}; registry {before} -> {len(serve.registered())}")
    failures += 0 if ok else 1
    expect_refused("retired name never reused", alias=other, new_alias=victim, new_role=None)
    # restore: same alias back, out of retired.json, role and ad hoc kept
    r = serve.restore_model(victim)
    retired = json.loads((cfg / "retired.json").read_text())
    roles = json.loads((cfg / "model-roles.json").read_text())
    ok = (r["ok"] and serve.registered().get(served) == victim and victim not in retired
          and roles["models"].get(victim) == new_role and victim in roles["ad_hoc"])
    print(("ok   " if ok else "FAIL ") + f"restored {victim}" + ("" if ok else f" -> {r}"))
    failures += 0 if ok else 1
    ok = not serve.restore_model(victim)["ok"]
    print(("ok   " if ok else "FAIL ") + "restore refused when not retired"); failures += 0 if ok else 1
    serve.remove_model(victim, victim, "retired again for the onboarding checks")
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
    # endpoints: refused before any network call or write
    for label, args in (("endpoint: bad name", ("Bad Name", "https://x.example/v1", "")),
                        ("endpoint: bad url", ("good", "ftp://x", "")),
                        ("endpoint: default not removable", None)):
        r = serve.save_endpoint(*args) if args else serve.remove_endpoint(serve.endpoints.default())
        ok = not r["ok"]
        print(("ok   " if ok else "FAIL ") + label); failures += 0 if ok else 1

    # The owner's files are as they were. alerts.json can change under a
    # running suite, so only what this test could do counts: the victim's
    # alerts cleared from the real file.
    real_alerts = DATA / "metrics" / "alerts.json"
    victim_keys = {k for k in json.loads(real[real_alerts] or b"{}") if k.endswith(f":{served}")}
    ok = (real[DATA / "config" / "endpoints.json"] == (DATA / "config" / "endpoints.json").read_bytes()
          and victim_keys <= set(json.loads(real_alerts.read_bytes() or b"{}")))
    print(("ok   " if ok else "FAIL ") + "the real alerts and endpoints were not touched")
    failures += 0 if ok else 1
    shutil.rmtree(tmp, ignore_errors=True)
    print("\nall rules hold" if not failures else f"\n{failures} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
