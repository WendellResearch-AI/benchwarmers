"""The LiteLLM and CLI Proxy API hub adapters against fake hubs: nothing is sent anywhere real.

    python scripts/test_hubs.py

The fakes answer with the shapes each hub's own source produces (recorded
below from LiteLLM 1.106.1 and CLI Proxy API 8.0.36, 2026-10-21; hosts and
keys made up), and refuse a wrong key as the real ones do. Checked: a dry run
writes nothing; a real add sends what it should and no key; LiteLLM without
its database mode is refused with what to change, before or at /model/new;
CLI Proxy API refuses with no management key before sending anything, never
gets the API key on a management route, and stops when its list changes
underneath; Discover offers each hub's connections and its Add goes through
the adapter of the endpoint's hub. BENCHWARMERS_HOME and LLM_USER_PATH point
at a temp folder before anything is imported.
"""

from __future__ import annotations

import copy
import json
import os
import pathlib
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
TMP = pathlib.Path(tempfile.mkdtemp(prefix="bench-hubs-"))
os.environ["BENCHWARMERS_HOME"] = str(TMP)
os.environ["LLM_USER_PATH"] = str(TMP / "llm")
(TMP / "config").mkdir()
(TMP / "llm").mkdir()
# Keys from the environment, as a container would have them. LiteLLM's API key
# is its master key (no admin key: the fallback); CLI Proxy API's management
# key is its own.
os.environ.update(BENCHWARMERS_KEY_LITE="sk-master-test", BENCHWARMERS_KEY_CPA="cpa-api-test",
                  BENCHWARMERS_KEY_CPA_ADMIN="mgmt-test", BENCHWARMERS_KEY_PLAIN="sk-master-test")
sys.path[:0] = [str(ROOT), str(ROOT / "metrics"), str(ROOT / "suite"), str(ROOT / "scripts")]

# LiteLLM 1.106.1: GET /model/info (api_key removed, secrets masked), the readiness details,
# POST /model/new's stored row, and its errors as ProxyException bodies.
LITE_DEPLOYMENTS = [
    {"model_name": "llama-3.3-70b",
     "litellm_params": {"model": "groq/llama-3.3-70b-versatile", "litellm_credential_name": "groq-cred",
                        "max_retries": 2, "rpm": 30, "input_cost_per_token": 5.9e-07},
     "model_info": {"id": "9b1c7e", "db_model": True, "direct_access": True, "access_via_team_ids": []}},
    {"model_name": "mistral-small",
     "litellm_params": {"model": "openai/mistral-small-latest", "api_base": "https://api.mistral.example/v1",
                        "extra_headers": {"Authorization": "Bear*****************9f2c"}, "timeout": 60},
     "model_info": {"id": "41aa02", "db_model": False}}]
LITE_READY = {"status": "healthy", "db": "connected", "cache": None, "litellm_version": "1.106.1",
              "success_callbacks": [], "use_aiohttp_transport": True, "log_level": "WARNING",
              "is_detailed_debug": False, "show_no_redis_warning": False}
LITE_ERRORS = {
    "no-db": "{'error': \"No DB Connected. Here's how to do it - https://docs.litellm.ai/docs/proxy/virtual_keys\"}",
    "no-store": "{'error': \"Set `'STORE_MODEL_IN_DB='True'` in your env to enable this feature.\"}"}
# CLI Proxy API 8.0.36: GET /v0/management/openai-compatibility (upstream keys included, as it sends them).
CPA_PROVIDERS = [
    {"name": "openrouter", "disabled": False, "base-url": "https://openrouter.example/api/v1",
     "api-key-entries": [{"api-key": "sk-or-upstream-never-sent", "auth-index": "a1"}],
     "models": [{"name": "vendor/old:free", "alias": "old-free"}], "auth-index": "c3"},
    {"name": "team", "disabled": False, "prefix": "team", "base-url": "https://llm.team.example/v1",
     "api-key-entries": [{"api-key": "team-upstream-never-sent"}], "models": [{"name": "m1", "alias": "m1"}]}]

S: dict = {}
SEEN: list[tuple[str, str, dict | None]] = []


def reset(**over) -> None:
    S.clear()
    S.update(lite=copy.deepcopy(LITE_DEPLOYMENTS), ready=dict(LITE_READY), store=True,
             cpa=copy.deepcopy(CPA_PROVIDERS), version="8.0.36", race=False)
    S.update(over)
    SEEN.clear()


def writes() -> list[str]:
    return [p for m, p, _b in SEEN if m in ("POST", "PATCH", "PUT", "DELETE")]


class Fake(BaseHTTPRequestHandler):
    def _route(self, method):                    # noqa: C901
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n)) if n else None
        SEEN.append((method, self.path, body))
        auth = self.headers.get("Authorization", "")
        if self.path.startswith("/lite/"):
            if auth != "Bearer sk-master-test":
                return self._send({"error": {"message": "Authentication Error, Invalid proxy server token passed.",
                                             "type": "auth_error", "param": "None", "code": "401"}}, 401)
            path = self.path[len("/lite"):]
            if path == "/health/readiness/details":
                return self._send(S["ready"])
            if path == "/model/info":
                return self._send({"data": S["lite"]})
            if path == "/v1/models":
                return self._send({"data": [{"id": d["model_name"], "object": "model"} for d in S["lite"]],
                                   "object": "list"})
            if path == "/model/new" and method == "POST":
                if S["ready"]["db"] != "connected" or not S["store"]:
                    msg = LITE_ERRORS["no-db" if S["ready"]["db"] != "connected" else "no-store"]
                    return self._send({"error": {"message": msg, "type": "auth_error", "param": "None",
                                                 "code": "500"}}, 500)
                S["lite"].append({**body, "model_info": {**body["model_info"], "id": "77e0d1", "db_model": True}})
                return self._send({"model_id": "77e0d1", "model_name": body["model_name"],
                                   "litellm_params": "gAAAAB-encrypted-row", "model_info": body["model_info"],
                                   "created_by": "default_user_id"})
        if self.path.startswith("/cpa/"):
            path = self.path[len("/cpa"):]
            if path == "/v1/models":
                if auth != "Bearer cpa-api-test":
                    return self._send({"error": "invalid api key"}, 401)
                ids = [(f"{e['prefix']}/" if e.get("prefix") else "") + (m.get("alias") or m["name"])
                       for e in S["cpa"] for m in e["models"]]
                return self._send({"data": [{"id": i, "object": "model"} for i in ids], "object": "list"})
            if path == "/v0/management/openai-compatibility":
                if auth != "Bearer mgmt-test":
                    return self._send({"error": "invalid management key"}, 401)
                if method == "GET":
                    return self._send({"openai-compatibility": S["cpa"]}, headers={"X-CPA-VERSION": S["version"]})
                if method == "PATCH":
                    entry = next((e for e in S["cpa"] if e["name"] == body["name"]), None)
                    if entry is None:
                        return self._send({"error": "item not found"}, 404)
                    entry["models"] = body["value"]["models"]         # replaced whole, as the real one does
                    if S["race"]:                                     # someone else's stale write lands after ours
                        entry["models"] = entry["models"][:-1]
                    return self._send({"status": "ok"}, headers={"X-CPA-VERSION": S["version"]})
        self._send({"detail": "Not Found"}, 404)

    def do_GET(self):                            # noqa: N802
        self._route("GET")

    def do_POST(self):                           # noqa: N802
        self._route("POST")

    def do_PATCH(self):                          # noqa: N802
        self._route("PATCH")

    def _send(self, data, status=200, headers=None):
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_a):
        pass


def refused(fn, *args) -> str:
    try:
        fn(*args)
    except ValueError as exc:
        return str(exc)
    raise AssertionError(f"{fn.__module__}.{fn.__name__}{args} was not refused")


def litellm() -> None:
    import litellm_hub as lite
    reset()
    p = lite.plan("groq", "moonshotai/kimi-k2")          # dry run
    assert writes() == [], writes()
    assert p["served"] == "moonshotai/kimi-k2" and p["deployment"] == "create" and p["version_note"] == "", p
    assert p["litellm_params"] == {"model": "groq/moonshotai/kimi-k2", "litellm_credential_name": "groq-cred",
                                   "max_retries": 2}, p["litellm_params"]         # no price, no rpm, no key
    assert p["database_mode"] is True and "groq-cred" in p["key"], p
    # A masked header is never sent back; the api_base host is the connection.
    m = lite.plan("api.mistral.example", "mistral-large")
    assert m["litellm_params"] == {"model": "openai/mistral-large", "api_base": "https://api.mistral.example/v1",
                                   "timeout": 60}, m["litellm_params"]
    assert m["not_copied"] == ["extra_headers"], m                             # said, not silently dropped
    assert set(lite.upstreams()) == {"groq", "api.mistral.example"}
    assert "already serves" in refused(lite.plan, "groq", "mistral-small")         # one name, two upstreams

    served, done = lite.add("groq", "moonshotai/kimi-k2")                        # a real add
    sent = [b for meth, path, b in SEEN if path == "/lite/model/new"]
    assert served == "moonshotai/kimi-k2" and len(sent) == 1, SEEN
    assert sent[0] == {"model_name": served, "litellm_params": p["litellm_params"],
                       "model_info": {"tags": ["groq"]}}, sent[0]
    assert "api_key" not in json.dumps(sent[0]) and "gAAAAB" not in " ".join(done), done
    assert any("77e0d1" in d for d in done), done
    assert lite.add("groq", "moonshotai/kimi-k2")[1][-1].endswith("nothing added")    # twice: no second deployment

    # No database: refused by the dry run, before /model/new.
    reset(ready={**LITE_READY, "db": "Not connected"})
    assert "DATABASE_URL" in refused(lite.add, "groq", "x") and writes() == [], writes()
    # A database, but models kept in config.yaml: /model/new refuses, nothing is added.
    reset(store=False)
    assert "STORE_MODEL_IN_DB=True" in refused(lite.add, "groq", "x") and len(S["lite"]) == 2
    # Another release is said, not refused.
    reset(ready={**LITE_READY, "litellm_version": "1.107.2"})
    assert "1.107.2" in lite.plan("groq", "x")["version_note"]


def cliproxy() -> None:
    import cliproxy as cpa
    reset()
    p = cpa.plan("openrouter", "vendor/new:free")                               # dry run
    assert writes() == [] and p["add_to_list"] and p["served"] == "vendor/new:free" and not p["version_note"], p
    assert cpa.plan("team", "m2")["served"] == "team/m2"                       # a prefix names the served id
    assert cpa.plan("openrouter", "old-free")["add_to_list"] is False          # listed by its alias already
    assert cpa.plan("team", "m1")["add_to_list"] is False                      # its own list: not a collision
    assert "already serves" in refused(cpa.plan, "openrouter", "m1")          # team serves a bare m1 too
    assert cpa.upstreams() == {"openrouter": ("https://openrouter.example/api/v1", ""),
                               "team": ("https://llm.team.example/v1", "team")}

    served, done = cpa.add("openrouter", "vendor/new:free")                     # a real add
    patches = [b for meth, _p, b in SEEN if meth == "PATCH"]
    assert served == "vendor/new:free" and len(patches) == 1, SEEN
    assert patches[0] == {"name": "openrouter", "value": {"models": [
        {"name": "vendor/old:free", "alias": "old-free"}, {"name": "vendor/new:free", "alias": "vendor/new:free"}]}}
    assert "never-sent" not in json.dumps(patches[0])                           # no upstream key goes back
    assert S["cpa"][1] == CPA_PROVIDERS[1], "another provider changed"
    gets = [i for i, (meth, path, _b) in enumerate(SEEN) if meth == "GET" and "management" in path]
    at = next(i for i, (meth, _p, _b) in enumerate(SEEN) if meth == "PATCH")
    assert any(i < at for i in gets) and any(i > at for i in gets), "not read just before and after"
    assert done[-1].startswith("added vendor/new:free"), done                   # the fake took only the management key
    SEEN.clear()                                                                # listed already: the fresh read decides
    assert cpa.add("openrouter", "old-free")[1][0].endswith("nothing changed") and writes() == []

    # A provider gone by the PATCH is said as that, not as the management API being off.
    assert "left CLI Proxy API" in refused(cpa._call, "PATCH", {"name": "gone", "value": {"models": []}})
    # The list changes underneath: stop and say so.
    reset(race=True)
    assert "changed while this wrote it" in refused(cpa.add, "openrouter", "vendor/new:free")
    # No management key stored: nothing is sent at all.
    reset()
    saved = os.environ.pop("BENCHWARMERS_KEY_CPA_ADMIN")
    try:
        msg = refused(cpa.plan, "openrouter", "x")
        assert "BENCHWARMERS_KEY_CPA_ADMIN" in msg and SEEN == [], (msg, SEEN)
    finally:
        os.environ["BENCHWARMERS_KEY_CPA_ADMIN"] = saved
    # A wrong management key: said once, with the ban in mind, never retried.
    os.environ["BENCHWARMERS_KEY_CPA_ADMIN"] = "wrong"
    try:
        assert "30 minutes" in refused(cpa.plan, "openrouter", "x") and len(SEEN) == 1, SEEN
    finally:
        os.environ["BENCHWARMERS_KEY_CPA_ADMIN"] = saved
    reset(version="v8.1.0")
    assert "8.1.0" in cpa.plan("openrouter", "x")["version_note"]


def discover_add() -> None:
    """Discover's routes and Add: each hub through its own adapter."""
    import discover
    import endpoints
    import owui
    import serve
    reset()
    rows = [{"provider": "groq", "provider_name": "Groq", "model": "vendor/new", "connection": None, "api": None},
            {"provider": "openrouter", "provider_name": "OpenRouter", "model": "vendor/x:free",
             "connection": "openrouter", "api": "https://openrouter.example/api/v1/"},
            {"provider": "mistral", "provider_name": "Mistral", "model": "mistral-large", "connection": None,
             "api": "https://api.mistral.example/v1"},
            {"provider": "nobody", "provider_name": "Nobody", "model": "n", "connection": None, "api": None}]
    routes, errors = discover.hub_routes(rows)
    assert errors == [] and writes() == [], (errors, writes())
    got = [[(x["hub"], x["connection"], x["endpoint"]) for x in r] for r in routes]
    assert got == [[("litellm", "groq", "lite")], [("cliproxy", "openrouter", "cpa")],
                   [("litellm", "api.mistral.example", "lite")], []], got
    lite_steps, cpa_steps = routes[0][0]["steps"][0], routes[1][0]["steps"][0]
    assert "tags:" in lite_steps and "logo" not in lite_steps, lite_steps          # only what each hub takes
    assert "tags" not in cpa_steps and "logo" not in cpa_steps and "access" not in cpa_steps, cpa_steps
    assert routes[1][0]["on_bench_as"] == "x-free", routes[1][0]                     # registered on cpa below
    # A hub that cannot be asked is said, and the rest still offered.
    os.environ["BENCHWARMERS_KEY_CPA_ADMIN"] = ""
    try:
        routes, errors = discover.hub_routes(rows[:2])
        assert len(errors) == 1 and "CLI Proxy API" in errors[0] and [len(r) for r in routes] == [1, 0], errors
    finally:
        os.environ["BENCHWARMERS_KEY_CPA_ADMIN"] = "mgmt-test"

    onboarded = []
    serve.queue_onboard = lambda items, who="ui": onboarded.append(
        (items[0]["served"], items[0]["endpoint"], items[0]["mode"], items[0]["vision"])) or {
        "ok": True, "errors": [], "items": []}
    owui.add = lambda c, m: (m, [f"owui added {m} on {c}"])
    r = serve.discover_add("groq", "vendor/new", endpoint="lite")
    assert r["ok"] and "/lite/model/new" in writes() and onboarded[-1] == ("vendor/new", "lite", "full", False), r
    # The confirm's Onboard as and Vision box reach the queue; a bad mode is refused before the hub has it.
    r = serve.discover_add("openrouter", "vendor/y:free", endpoint="cpa", mode="quick-adhoc", vision=True)
    assert r["ok"] and onboarded[-1] == ("vendor/y:free", "cpa", "quick-adhoc", True), r
    assert [m for m, p, _b in SEEN if "management" in p and m == "PATCH"] == ["PATCH"]
    before = len(SEEN)
    assert "mode must be" in serve.discover_add("openrouter", "vendor/q", endpoint="cpa", mode="slow")["errors"][0]
    assert len(SEEN) == before, SEEN[before:]
    r = serve.discover_add("groq-api", "vendor/z")                                 # no endpoint: Open WebUI, as before
    assert r["steps"] == ["owui added vendor/z on groq-api"] and onboarded[-1] == ("vendor/z", "owui", "full", False), r
    assert serve.discover_add("groq-api", "vendor/z", endpoint="owui")["ok"]
    assert "not a hub" in serve.discover_add("groq", "v/z", endpoint="plain")["errors"][0]
    changes = [json.loads(line)["field"] for line in serve.CHANGES_PATH.read_text(encoding="utf-8").splitlines()]
    assert {"litellm-add", "cliproxy-add", "openwebui-add"} <= set(changes), changes

    # The admin key from the Endpoints form: kept as <key>-admin, never in the reply.
    r = serve.save_endpoint("lite", endpoints.load()["lite"]["base"], "", admin="sk-master-test")
    assert r["ok"] and "lite-admin" in r["note"] and "sk-master" not in json.dumps(r), r
    assert endpoints._stored()["lite-admin"] == "sk-master-test"
    assert serve.save_endpoint("plain", endpoints.load()["plain"]["base"], "", admin="nope")["ok"]
    assert "plain-admin" not in endpoints._stored(), "a provider got an admin key"


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    fake = f"http://127.0.0.1:{server.server_port}"
    (TMP / "config" / "endpoints.json").write_text(json.dumps({
        "owui": {"base": fake + "/api", "key": "owui", "default": True},          # kind inferred, as the owner's
        "lite": {"base": fake + "/lite/v1", "key": "lite", "kind": "hub", "hub": "litellm"},
        "cpa": {"base": fake + "/cpa/v1", "key": "cpa", "kind": "hub", "hub": "cliproxy"},
        "plain": {"base": fake + "/lite/v1", "key": "plain", "kind": "provider"}}), encoding="utf-8")
    import endpoints
    (TMP / "config" / "extra-openai-models.yaml").write_text(endpoints.yaml_block("x-free", "vendor/x:free", "cpa"),
                                                              encoding="utf-8")
    try:
        litellm()
        cliproxy()
        discover_add()
    finally:
        server.shutdown()
        shutil.rmtree(TMP, ignore_errors=True)
    print("hub adapter checks pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
