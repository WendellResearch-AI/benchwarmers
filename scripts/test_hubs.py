"""The hub adapters against fake hubs: nothing is sent anywhere real.

    python scripts/test_hubs.py

The fakes answer with the shapes each hub's own source produces (recorded
below from LiteLLM 1.106.1 and CLI Proxy API 8.0.36, 2026-10-21, and
OmniRoute 3.8.54, freellmapi 0.16.37, one-api v0.6.10 and new-api
v1.0.0-rc.51, 2026-11-20; hosts and keys made up), and refuse a wrong key as
the real ones do. freellmapi's routers and a one-api or new-api name sent
upstream as two models are kept out of onboarding; one-api's and new-api's
channel lists are read whole across their different pages, their access
token only on the admin route and the API key only on /v1; their channels
group the Onboard picker and tag provider_tags.py's record; each hub's own
quota reaches its tile; a hub that takes no model says so. Also checked: a
dry run writes nothing; a real add sends
what it should and no key; LiteLLM without its database mode is refused with
what to change, before or at /model/new; CLI Proxy API refuses with no
management key before sending anything, never gets the API key on a
management route, and stops when its list changes underneath; OmniRoute's
combos and routers are kept out of onboarding, a key without the manage
scope is said as that, and a provider id it has no connection under is
refused before the add; each hub's sign-ins' quota is read through its own
adapter; Discover offers each hub's connections and its Add goes through the
adapter of the endpoint's hub. BENCHWARMERS_HOME and LLM_USER_PATH point at a
temp folder before anything is imported.
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
# OmniRoute's API key is inference-only; its admin key has the manage scope.
os.environ.update(BENCHWARMERS_KEY_LITE="sk-master-test", BENCHWARMERS_KEY_CPA="cpa-api-test",
                  BENCHWARMERS_KEY_CPA_ADMIN="mgmt-test", BENCHWARMERS_KEY_PLAIN="sk-master-test",
                  BENCHWARMERS_KEY_OMNI="omni-api-test", BENCHWARMERS_KEY_OMNI_ADMIN="omni-manage-test",
                  BENCHWARMERS_KEY_FL="fl-unified", BENCHWARMERS_KEY_OA="sk-oa", BENCHWARMERS_KEY_OA_ADMIN="oa-access",
                  BENCHWARMERS_KEY_NA="sk-na", BENCHWARMERS_KEY_NA_ADMIN="nap_test")
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
# Its sign-ins (the console's management routes), and what each provider's quota endpoint answers.
CPA_CREDS = [{"provider": "antigravity", "auth_index": "a1", "project_id": "proj-1", "disabled": False},
             {"provider": "codex", "auth_index": "a2", "id_token": {"chatgpt_account_id": "acct-2"}},
             {"provider": "codex", "auth_index": "a3", "disabled": True}]
AG_SUMMARY = {"groups": [{"displayName": "Gemini Models", "buckets": [
    {"window": "5h", "resetTime": "2026-11-20T23:00:00Z", "remainingFraction": 0.5}]}]}
WHAM = {"plan_type": "plus", "rate_limit": {"primary_window": {
    "used_percent": 25, "limit_window_seconds": 18000, "reset_at": 1795707512}}}
# OmniRoute 3.8.54: /v1/models (combos and auto/* owned_by "combo", a provider's models
# `<alias>/<id>`), /api/providers' count, the CLIProxyAPI-compatible quota routes.
OMNI_MODELS = [
    {"id": "auto", "object": "model", "owned_by": "combo", "root": "auto", "parent": None},
    {"id": "auto/coding", "object": "model", "owned_by": "combo", "root": "auto/coding", "parent": None},
    {"id": "my-fusion", "object": "model", "owned_by": "combo", "root": "my-fusion", "parent": None},
    {"id": "cerebras/fast", "object": "model", "owned_by": "combo", "root": "cerebras/fast", "parent": None},
    {"id": "cx/gpt-5.1-codex", "object": "model", "owned_by": "codex", "root": "gpt-5.1-codex", "parent": None},
    {"id": "cerebras/qwen-3-coder", "object": "model", "owned_by": "cerebras", "root": "qwen-3-coder", "parent": None},
    {"id": "mynode/m", "object": "model", "owned_by": "mynode", "root": "m", "parent": None},
    {"id": "gemini/text-embedding-004", "object": "model", "owned_by": "gemini", "root": "text-embedding-004",
     "type": "embedding"}]
OMNI_CONNECTIONS = {"cerebras": [{"id": "c1", "provider": "cerebras", "apiKey": "csk-upstream-never-sent"}],
                    "codex": [{"id": "c9", "provider": "codex"}]}       # mynode's id is a UUID, not its prefix
OMNI_FILES = [{"id": "c9", "auth_index": "c9", "provider": "codex", "email": "a@example.com", "disabled": False,
               "id_token": {"chatgpt_account_id": "ws-1", "plan_type": "plus"}},
              {"id": "c8", "auth_index": "c8", "provider": "claude", "disabled": False}]
OMNI_ALLOWED = {("GET", "https://chatgpt.com/backend-api/wham/usage"), ("GET", "https://api.anthropic.com/api/oauth/usage")}
# freellmapi 0.16.37: /v1/models (its routers first, without supported_parameters), /v1/providers,
# /v1/quota-forecast.
FL_MODELS = [
    {"id": "auto", "object": "model", "owned_by": "freellmapi", "available": True},
    {"id": "fusion", "object": "model", "owned_by": "freellmapi", "available": True},
    {"id": "claude-sonnet-4-5", "object": "model", "owned_by": "freellmapi", "name": "Sonnet slot (auto-routed)"},
    {"id": "auto:coding", "object": "model", "owned_by": "freellmapi", "available": True},
    {"id": "llama-3.3-70b", "object": "model", "owned_by": "freellmapi", "available": True,
     "execution_status": "ready", "supported_parameters": ["tools"]},
    {"id": "qwen3-coder", "object": "model", "owned_by": "freellmapi", "available": False,
     "execution_status": "needsKey", "supported_parameters": []}]
FL_PROVIDERS = {"providers": [
    {"platform": "groq", "name": "Groq", "status": "healthy", "keys": 1, "requests_remaining_pct": 80},
    {"platform": "cerebras", "name": "Cerebras", "status": "rate_limited", "keys": 1,
     "resume_at": "2026-11-20T12:00:00.000Z"},
    {"platform": "zhipu", "name": "Zhipu", "status": "invalid", "keys": 1, "last_error": "401 bad key"}],
    "counts": {"healthy": 1, "rate_limited": 1, "invalid": 1, "unknown": 0}}
FL_POOLS = [
    {"platform": "groq", "pool": "groq::account", "used": 200, "remaining": 800, "limit": 1000, "remaining_pct": 80,
     "reset_at": "2026-11-21T00:00:00.000Z", "low_balance": False, "seconds_until_reset": 3600},
    {"platform": "cerebras", "pool": "cerebras::default", "used": None, "remaining": None, "limit": 900,
     "remaining_pct": None, "reset_at": None, "low_balance": False}]
# one-api v0.6.10 and new-api v1.0.0-rc.51: GET /api/channel/ (keys left out). llama-70b goes upstream as two
# models; mixtral's second channel was switched off by the hub (3); mistral-small is one model on two channels.
CHANNELS = [
    {"id": 1, "type": 1, "status": 1, "name": "Groq (free)", "base_url": "https://api.groq.example",
     "models": "llama-70b,mixtral", "model_mapping": "", "group": "default", "priority": 0, "response_time": 420},
    {"id": 2, "type": 1, "status": 1, "name": "Cerebras", "base_url": "https://api.cerebras.example",
     "models": "llama-70b", "model_mapping": '{"llama-70b": "llama-3.3-70b"}', "group": "default"},
    {"id": 3, "type": 1, "status": 1, "name": "Mistral", "models": "mistral-small", "model_mapping": "{}"},
    {"id": 4, "type": 1, "status": 3, "name": "Old proxy", "models": "mixtral", "model_mapping": '{"mixtral": "x"}'},
    {"id": 5, "type": 1, "status": 1, "name": "DeepInfra", "models": "mistral-small", "model_mapping": None}]
ONE = {"oa": {"key": "sk-oa", "admin": "oa-access", "release": "v0.6.10"},
       "na": {"key": "sk-na", "admin": "nap_test", "release": "v1.0.0-rc.51"}}

S: dict = {}
SEEN: list[tuple[str, str, dict | None]] = []
AUTH: list[tuple[str, str]] = []                  # (path, Authorization) of every request


def reset(**over) -> None:
    S.clear()
    S.update(lite=copy.deepcopy(LITE_DEPLOYMENTS), ready=dict(LITE_READY), store=True,
             cpa=copy.deepcopy(CPA_PROVIDERS), version="8.0.36", race=False,
             omni=copy.deepcopy(OMNI_MODELS), omni_version="3.8.54",
             oa={"whole": 10.0, "used": 2.5, **ONE["oa"]}, na={"whole": 100000000, "used": 3.0, **ONE["na"]})
    S.update(over)
    SEEN.clear()
    AUTH.clear()


def writes() -> list[str]:
    return [p for m, p, _b in SEEN if m in ("POST", "PATCH", "PUT", "DELETE")]


class Fake(BaseHTTPRequestHandler):
    def _route(self, method):                    # noqa: C901
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n)) if n else None
        SEEN.append((method, self.path, body))
        auth = self.headers.get("Authorization", "")
        AUTH.append((self.path, auth))
        if self.path.startswith("/fl/"):
            path = self.path[len("/fl"):]
            profile = auth == "Bearer sk-cp-profile"                  # a client profile's key: models only
            if auth != "Bearer fl-unified" and not (profile and path == "/v1/models"):
                return self._send({"error": {"message": "Invalid API key", "type": "authentication_error"}}, 401)
            if path == "/v1/models":
                return self._send({"object": "list", "data": FL_MODELS})
            if path == "/v1/providers":
                return self._send(FL_PROVIDERS)
            if path == "/v1/quota-forecast":
                return self._send({"generated_at": "2026-11-20T11:00:00Z", "pools": FL_POOLS})
        if self.path[:4] in ("/oa/", "/na/"):
            return self._one(self.path[1:3], self.path[3:], auth)
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
            if path in ("/v0/management/credentials", "/v0/management/requests/api-call"):   # /v8/... 404s here
                if auth != "Bearer mgmt-test":
                    return self._send({"error": "invalid management key"}, 401)
                if path.endswith("credentials"):
                    return self._send({"files": CPA_CREDS})
                return self._send(self._quota(body))
        if self.path.startswith("/omni/"):
            path = self.path[len("/omni"):]
            if path == "/v1/models":
                if auth not in ("Bearer omni-api-test", "Bearer omni-manage-test"):
                    return self._send({"error": {"message": "Invalid API key"}}, 401)
                return self._send({"object": "list", "data": S["omni"]})
            if path.startswith("/api/"):                          # management: a dashboard session or a manage key
                if auth == "Bearer omni-api-test":
                    return self._send({"error": {"message": "API key lacks 'manage' scope. Enable it in the API"
                                                            " Keys dashboard.", "type": "invalid_request"}}, 403)
                if auth != "Bearer omni-manage-test":
                    return self._send({"error": {"message": "Invalid API key", "type": "invalid_request"}}, 401)
                if path == "/api/monitoring/health":
                    return self._send({"status": "healthy", "system": {"version": S["omni_version"], "pid": 1}})
                if path.startswith("/api/providers?provider="):
                    conns = OMNI_CONNECTIONS.get(path.split("=", 1)[1].split("&")[0], [])
                    return self._send({"connections": conns[:1], "total": len(conns)})
                if path == "/api/provider-models" and method == "POST":
                    S["omni"].append({"id": f"{body['provider']}/{body['modelId']}", "object": "model",
                                      "owned_by": body["provider"], "root": body["modelId"]})
                    return self._send({"model": {"id": body["modelId"], "source": "manual"}})
                if path == "/api/v0/management/auth-files":
                    return self._send({"files": OMNI_FILES})
                if path == "/api/v0/management/api-call":
                    if (body["method"], body["url"]) not in OMNI_ALLOWED:
                        return self._send({"error": "This upstream request is not allowed through the CLIProxyAPI"
                                                    " compatibility endpoint.", "code": "api_call_not_allowed"}, 403)
                    return self._send(self._quota(body))
        self._send({"detail": "Not Found"}, 404)

    @staticmethod
    def _quota(body: dict) -> dict:
        """A hub's api-call: the sign-in's provider answering its quota request."""
        if "retrieveUserQuotaSummary" in body["url"]:
            return {"status_code": 200, "body": json.dumps(AG_SUMMARY)}
        if body["url"].endswith("wham/usage"):
            return {"status_code": 200, "body": json.dumps(WHAM)}
        return {"status_code": 404, "body": ""}

    def _one(self, hub: str, path: str, auth: str):
        """one-api (oa) or new-api (na), as each answers."""
        new, me = hub == "na", S[hub]
        route, _, query = path.partition("?")
        if route == "/api/status":                       # keyless
            return self._send({"success": True, "message": "", "data": {
                "version": me["release"], "quota_per_unit": 500000, **({"enable_drawing": True} if new else {})}})
        if route == "/api/channel/":
            if auth != f"Bearer {me['admin']}":
                # one-api refuses with 200 and success false; new-api with a 401.
                return (self._send({"success": False, "message": "Unauthorized, invalid access token",
                                    "code": "AUTH_UNAUTHORIZED"}, 401) if new else
                        self._send({"success": False, "message": "access token invalid"}))
            q = dict(kv.split("=") for kv in query.split("&") if "=" in kv)
            rows = copy.deepcopy(CHANNELS)
            if new:                                      # pages from 1, page_size up to 100
                page, size = max(int(q.get("p", 0)), 1), min(int(q.get("page_size", 10)), 100)
                return self._send({"success": True, "message": "", "data": {
                    "items": rows[(page - 1) * size:page * size], "total": len(rows), "page": page, "page_size": size}})
            p = int(q.get("p", 0))                       # pages from 0 of ItemsPerPage, 2 here; page_size ignored
            return self._send({"success": True, "message": "", "data": rows[p * 2:(p + 1) * 2]})
        if route.startswith("/v1/"):
            if auth != f"Bearer {me['key']}":
                return self._send({"error": {"message": "invalid token", "type": "one_api_error"}}, 401)
            if route == "/v1/models":
                names = sorted({m for c in CHANNELS if c["status"] == 1 for m in c["models"].split(",")})
                return self._send({"object": "list", "data": [{"id": n, "object": "model", "owned_by": "custom"}
                                                              for n in names]})
            if route == "/v1/dashboard/billing/subscription":
                return self._send({"object": "billing_subscription", "has_payment_method": True,
                                   "soft_limit_usd": me["whole"], "hard_limit_usd": me["whole"],
                                   "system_hard_limit_usd": me["whole"], "access_until": 0})
            if route == "/v1/dashboard/billing/usage":
                return self._send({"object": "list", "total_usage": me["used"] * 100})
        return self._send({"success": False, "message": "not found"}, 404)

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


def omniroute() -> None:
    import endpoints
    import omniroute as omni
    reset()
    # Combos and routers are not one model: the picker and onboarding leave them out.
    several = endpoints.virtual("omni")
    assert [m["id"] for m in OMNI_MODELS if several(m)] == ["auto", "auto/coding", "my-fusion", "cerebras/fast"]
    assert endpoints.virtual("lite")({"owned_by": "combo"}) == ""             # a hub without the job: every entry is one
    assert omni.upstreams() == {"codex": ("", "cx"), "cerebras": ("", "cerebras"), "mynode": ("", "mynode"),
                                "gemini": ("", "gemini")}, omni.upstreams()
    p = omni.plan("cerebras", "zai-glm-4.6")                                    # dry run
    assert writes() == [] and p["served"] == "cerebras/zai-glm-4.6" and p["add_to_list"], p
    assert p["version"] == "3.8.54" and p["version_note"] == "", p
    assert omni.plan("codex", "gpt-5.1-codex")["add_to_list"] is False          # served already
    assert "no model of a provider" in refused(omni.plan, "nobody", "x")
    assert "no connection under the provider id" in refused(omni.plan, "mynode", "x")   # a node: owned_by its prefix
    assert "as a combo" in refused(omni.plan, "cerebras", "fast")

    served, done = omni.add("cerebras", "zai-glm-4.6")                           # a real add
    posts = [b for m, path, b in SEEN if m == "POST"]
    assert served == "cerebras/zai-glm-4.6" and posts == [{"provider": "cerebras", "modelId": "zai-glm-4.6"}], SEEN
    assert done[-1].startswith("added zai-glm-4.6"), done                        # listed at once: no wait said
    assert "never-sent" not in json.dumps(done) and omni.add("cerebras", "zai-glm-4.6")[1][-1].endswith("nothing added")

    # The API key alone, without the manage scope: said as that, and nothing added.
    reset()
    saved = os.environ.pop("BENCHWARMERS_KEY_OMNI_ADMIN")
    try:
        assert "manage scope" in refused(omni.plan, "cerebras", "x") and writes() == []
        assert omni.plan("codex", "gpt-5.1-codex")["version_note"]                # served already: only the release unknown
    finally:
        os.environ["BENCHWARMERS_KEY_OMNI_ADMIN"] = saved
    reset(omni_version="3.9.1")
    assert "3.9.1" in omni.plan("cerebras", "x")["version_note"]

    # The picker: one-model entries only (the bench's own data folder is empty here).
    import serve
    got = serve.onboard_candidates(fresh=True)
    mine = {c["served"] for c in got["candidates"] if c["endpoint"] == "omni"}
    assert "cx/gpt-5.1-codex" in mine and not mine & {"auto", "auto/coding", "my-fusion", "cerebras/fast"}, mine


def signins() -> None:
    """Quota management: a tag's sign-ins read through the hub that holds them, by its adapter."""
    import quotaview
    reset()
    kept = quotaview._tags
    quotaview._tags = lambda: {"cpa/antigravity": ["a"], "cpa/codex": ["b"], "omni/codex": ["c"], "omni": ["d"]}
    try:
        assert [quotaview.signin_hub(t)[0] for t in ("cpa/codex", "omni/codex")] == ["cliproxy", "omniroute"]
        assert quotaview.signin_hub("lite") is None and quotaview.signin_hub("openrouter") is None
        assert quotaview.how_refreshed("omni/codex") == "live"
        ag = quotaview.read_signins("cpa/antigravity")              # the console's /v8 404s here: the older /v0
        assert ag["groups"][0]["windows"][0]["left"] == 50 and ag["name"] == "antigravity", ag
        cx = quotaview.read_signins("cpa/codex")                    # the disabled one is not asked
        assert cx["plan"] == "plus" and cx["groups"][0]["windows"][0]["left"] == 75, cx
        calls = [b for _m, path, b in SEEN if path.endswith("api-call") and "/v0/" in path]
        assert [c["auth_index"] for c in calls] == ["a1", "a2"] and calls[1]["header"]["Chatgpt-Account-Id"] == "acct-2"
        assert any("/v8/" in path for _m, path, _b in SEEN), "the console's prefix was not asked first"
        assert json.loads(calls[0]["data"]) == {"project": "proj-1"}, calls[0]
        SEEN.clear()
        om = quotaview.read_signins("omni/codex")
        assert om["plan"] == "plus" and om["groups"][0]["windows"][0]["left"] == 75, om
        sent = [b for _m, path, b in SEEN if path.endswith("api-call")]
        assert sent[0]["auth_index"] == "c9" and sent[0]["header"]["Chatgpt-Account-Id"] == "ws-1", sent
        # A bare tag is the sign-ins no sibling tag names: Claude's, which has no reader here.
        assert "no quota reader for claude" in refused(quotaview.read_signins, "omni")
        assert "lists no antigravity sign-in" in refused(quotaview.read_signins, "omni/antigravity")
        # Reached through Open WebUI: the tag is the name connections.json gives the hub's address.
        (TMP / "config" / "connections.json").write_text('{"hosts": {"127.0.0.1": "proxy-box"}}', encoding="utf-8")
        assert quotaview.signin_hub("proxy-box/codex")[0] == "cliproxy"     # two at one address: the first
    finally:
        quotaview._tags = kept
        (TMP / "config" / "connections.json").unlink(missing_ok=True)


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
            {"provider": "nobody", "provider_name": "Nobody", "model": "n", "connection": None, "api": None},
            {"provider": "cerebras", "provider_name": "Cerebras", "model": "qwen-3-coder", "connection": None,
             "api": "https://api.cerebras.ai/v1"}]
    routes, errors = discover.hub_routes(rows)
    assert errors == [] and writes() == [], (errors, writes())
    got = [[(x["hub"], x["connection"], x["endpoint"]) for x in r] for r in routes]
    assert got == [[("litellm", "groq", "lite")], [("cliproxy", "openrouter", "cpa")],
                   [("litellm", "api.mistral.example", "lite")], [], [("omniroute", "cerebras", "omni")]], got
    assert "custom models" in routes[4][0]["steps"][0] and "logo" not in routes[4][0]["steps"][0], routes[4]
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
    r = serve.discover_add("cerebras", "llama-4", endpoint="omni")
    assert r["ok"] and onboarded[-1] == ("cerebras/llama-4", "omni", "full", False), r
    changes = [json.loads(line)["field"] for line in serve.CHANGES_PATH.read_text(encoding="utf-8").splitlines()]
    assert {"litellm-add", "cliproxy-add", "openwebui-add", "omniroute-add"} <= set(changes), changes

    # The admin key from the Endpoints form: kept as <key>-admin, never in the reply.
    r = serve.save_endpoint("lite", endpoints.load()["lite"]["base"], "", admin="sk-master-test")
    assert r["ok"] and "lite-admin" in r["note"] and "sk-master" not in json.dumps(r), r
    assert endpoints._stored()["lite-admin"] == "sk-master-test"
    assert serve.save_endpoint("plain", endpoints.load()["plain"]["base"], "", admin="nope")["ok"]
    assert "plain-admin" not in endpoints._stored(), "a provider got an admin key"


def freellmapi() -> None:
    import endpoints
    import freellmapi_hub as fl
    reset()
    test = endpoints.virtual("fl")
    assert {i for i, e in endpoints.models("fl").items() if test(e)} == {"auto", "fusion", "claude-sonnet-4-5",
                                                                         "auto:coding"}
    assert fl.virtual({"id": "claude-3-haiku", "supported_parameters": []}) == ""     # a real catalog row
    q = fl.quota("fl")
    assert q["groups"] == [{"label": "groq · account", "windows": [{"label": "requests", "left": 80, "remaining": 800,
                                                                    "of": 1000, "reset_at": "2026-11-21T00:00:00.000Z"}]}], q
    assert q["notes"] == ["Cerebras: rate-limited until 2026-11-20T12:00:00.000Z",
                          "Zhipu: its keys fail freellmapi's check (401 bad key)"], q
    assert writes() == [] and not hasattr(fl, "add") and not hasattr(fl, "served_by")
    os.environ["BENCHWARMERS_KEY_FL"] = "sk-cp-profile"                           # lists, but no status routes
    try:
        assert "unified" in refused(fl.quota, "fl")
    finally:
        os.environ["BENCHWARMERS_KEY_FL"] = "fl-unified"


def one_family() -> None:
    """one-api and new-api through the one adapter, each with its own pages and refusals."""
    import endpoints
    import oneapi_hub as one
    for ep, label in (("oa", "one-api"), ("na", "new-api")):
        reset()
        assert len(one.channels(ep)) == 5, (ep, SEEN)                            # every page, none twice
        pages = [p for m, p, _b in SEEN if "/api/channel/" in p]
        assert pages[0].startswith(f"/{ep}/api/channel/?p={0 if ep == 'oa' else 1}&"), pages
        test = endpoints.virtual(ep)
        v = {i: why for i, e in endpoints.models(ep).items() if (why := test(e))}
        assert list(v) == ["llama-70b"] and "(llama-3.3-70b, llama-70b)" in v["llama-70b"] and label in v["llama-70b"], v
        assert one.served_by(ep) == {"llama-70b": ["groq-free", "cerebras"], "mixtral": ["groq-free"],
                                     "mistral-small": ["mistral", "deepinfra"]}, one.served_by(ep)
        q = one.quota(ep)
        assert f"switched off by {label} after failing: old-proxy" in q["notes"], q
        assert not any("not a release" in n for n in q["notes"]), q                  # a release it was checked against
        # The access token only on the admin route, the API key only on /v1, nothing on /api/status.
        for path, auth in AUTH:
            want = ("" if "/api/status" in path else f"Bearer {ONE[ep]['admin']}" if "/api/channel/" in path
                    else f"Bearer {ONE[ep]['key']}")
            assert auth == want, (path, auth)
        assert writes() == [] and not hasattr(one, "add") and not hasattr(one, "upstreams")
        # No access token stored: the channels are not asked, every name passes, and the balance is still read.
        env = f"BENCHWARMERS_KEY_{ep.upper()}_ADMIN"
        saved = os.environ.pop(env)
        try:
            SEEN.clear()
            assert endpoints.virtual(ep)({"id": "llama-70b"}) == "" and env in refused(one.served_by, ep)
            assert not any("/api/channel/" in p for _m, p, _b in SEEN), SEEN
            assert any(n.startswith("channels not read") for n in one.quota(ep)["notes"])
            os.environ[env] = "wrong"                                                 # refused: 200 or 401
            assert f"{label} refused /api/channel/" in refused(one.channels, ep)
        finally:
            os.environ[env] = saved
    reset()
    assert one.quota("oa")["groups"] == [{"label": "", "windows": [
        {"label": "balance, in one-api's unit", "left": 75, "remaining": 7.5, "of": 10.0, "reset_at": None}]}]
    q = one.quota("na")
    assert q["groups"] == [] and "this key has no quota limit in new-api" in q["notes"], q
    reset(na={**S["na"], "release": "v1.1.2"})
    assert any("new-api 1.1.2 is not a release" in n for n in one.quota("na")["notes"])


def hub_flows() -> None:
    """The flows that ask for these jobs: the Onboard picker, Discover's Add,
    provider_tags.py and the Quota tile; none names a hub."""
    import endpoints
    import provider_tags
    import quotaview
    import serve
    reset()
    c = serve.onboard_candidates(fresh=True)
    rows = {(x["endpoint"], x["served"]): x for x in c["candidates"]}
    assert not {("fl", m) for m in ("auto", "fusion", "auto:coding", "claude-sonnet-4-5")} & set(rows), rows
    assert ("fl", "llama-3.3-70b") in rows and ("oa", "llama-70b") not in rows and ("na", "llama-70b") not in rows
    assert rows[("oa", "mixtral")]["group"] == "oa: groq-free" and rows[("na", "mistral-small")]["group"] == \
        "na: mistral + deepinfra" and rows[("fl", "llama-3.3-70b")]["group"] == "fl (direct)", rows
    assert not any(e.startswith(("fl:", "oa:", "na:")) for e in c["errors"]) and writes() == [], c["errors"]
    assert "freellmapi takes no model through its API" in serve.discover_add("groq", "v/x", endpoint="fl")["errors"][0]

    # Where each model came from, kept: <endpoint>/<connection> where one channel serves it.
    with endpoints.YAML.open("a", encoding="utf-8") as fh:
        fh.write(endpoints.yaml_block("oa-mixtral", "mixtral", "oa") + endpoints.yaml_block("oa-mistral", "mistral-small", "oa")
                 + endpoints.yaml_block("fl-llama", "llama-3.3-70b", "fl"))
    kept, provider_tags._owui = provider_tags._owui, lambda hub: {}               # Open WebUI's part is test_no_owui's
    try:
        provider_tags.main()
    finally:
        provider_tags._owui = kept
    tags = json.loads((TMP / "config" / "provider-tags.json").read_text(encoding="utf-8"))
    assert (tags["mixtral"], tags["mistral-small"], tags["llama-3.3-70b"]) == ("oa/groq-free", "oa", "fl"), tags

    # Each tile's Refresh asks its hub's quota, and the tile shows its groups and notes.
    assert quotaview.how_refreshed("oa/groq-free") == quotaview.how_refreshed("fl") == "live"
    assert quotaview.refresh("oa/groq-free")["how"] == quotaview.refresh("fl")["how"] == "live"
    tiles = {t["id"]: t for t in quotaview.tiles()}
    assert tiles["oa/groq-free"]["kind"] == "groups" and tiles["oa/groq-free"]["groups"][0]["windows"][0]["left"] == 75
    assert tiles["fl"]["groups"][0]["label"] == "groq · account" and "Zhipu" in tiles["fl"]["notes"][1], tiles["fl"]
    assert writes() == []


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    fake = f"http://127.0.0.1:{server.server_port}"
    (TMP / "config" / "endpoints.json").write_text(json.dumps({
        "owui": {"base": fake + "/api", "key": "owui", "default": True},          # kind inferred, as the owner's
        "lite": {"base": fake + "/lite/v1", "key": "lite", "kind": "hub", "hub": "litellm"},
        "cpa": {"base": fake + "/cpa/v1", "key": "cpa", "kind": "hub", "hub": "cliproxy"},
        "omni": {"base": fake + "/omni/v1", "key": "omni", "kind": "hub", "hub": "omniroute"},
        "plain": {"base": fake + "/lite/v1", "key": "plain", "kind": "provider"},
        "fl": {"base": fake + "/fl/v1", "key": "fl", "kind": "hub", "hub": "freellmapi"},
        "oa": {"base": fake + "/oa/v1", "key": "oa", "kind": "hub", "hub": "oneapi"},
        "na": {"base": fake + "/na/v1", "key": "na", "kind": "hub", "hub": "newapi"}}), encoding="utf-8")
    import endpoints
    (TMP / "config" / "extra-openai-models.yaml").write_text(endpoints.yaml_block("x-free", "vendor/x:free", "cpa"),
                                                              encoding="utf-8")
    try:
        litellm()
        cliproxy()
        omniroute()
        signins()
        discover_add()
        freellmapi()
        one_family()
        hub_flows()
    finally:
        server.shutdown()
        shutil.rmtree(TMP, ignore_errors=True)
    print("hub adapter checks pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
