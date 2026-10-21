"""The LiteLLM hub adapter: a model added as a new deployment on a connection LiteLLM already has.

    python metrics/litellm_hub.py <connection> <model id>     # what would change, changes nothing

Named litellm_hub, not litellm: metrics/ is on sys.path, and a module called
litellm would stand in for the real package in anything that imports it.

LiteLLM serves each model as a deployment: a public `model_name` and the
`litellm_params` that reach the upstream (`groq/<id>`, an `api_base`, a key).
A model is added with POST /model/new, which works only in LiteLLM's database
mode (DATABASE_URL set, and STORE_MODEL_IN_DB=True or general_settings
store_model_in_db), and only with the master key or an admin's: the
endpoint's admin key (endpoints.admin_key, falling back to the endpoint's
own key, which is often the master key).

A CONNECTION is an upstream: the host of a deployment's `api_base` when it
sets one (an `openai/...` deployment pointed at any OpenAI-compatible API),
else its provider (`groq` for `groq/llama-...`). The new deployment copies
one sibling's connection-level params (COPIED), so it reaches the same
upstream the same way. LiteLLM never hands an `api_key` back, so a sibling's
key travels only as a stored credential (`litellm_credential_name`); a
sibling without one is copied without a key, and LiteLLM then uses the
provider's environment variable (GROQ_API_KEY), as that sibling does unless it
carries a key of its own. Onboarding's probe says which.
ponytail: one connection per upstream; two accounts on one provider count as
one, and the sibling with a credential is the one copied.

Checked against LiteLLM 1.106.1's source (2026-10-21):
proxy/management_endpoints/model_management_endpoints.py (POST /model/new:
no database refuses with "No DB Connected", database mode off with
"STORE_MODEL_IN_DB", both before anything is written; the reply is the stored
row, which may carry the encrypted key, so nothing here prints it),
proxy/proxy_server.py (GET /model/info: `data`, each with `model_name`,
`litellm_params` without `api_key` and other secrets masked with '*',
`model_info.db_model`), proxy/health_endpoints (GET /health/readiness/details:
`db` and `litellm_version`; releases before it said both on /health/readiness).
"""

from __future__ import annotations

import json
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "metrics"))
import endpoints  # noqa: E402

# What this hub takes (RELEASE-PLAN section 2, "Adding a model to a hub"); "" where it has none.
SUPPORTS = {"allow_list": "deployments, as one more",
            "tags": "a label in model_info (LiteLLM's own tags drive routing, so not those)", "logo": "",
            "access": "open to every key without a model list (access groups, keys and teams stay LiteLLM's)",
            "link": "/ui/models-and-endpoints?model=<deployment id>"}
CHECKED = ("1.106",)
# A sibling's params that say where the upstream is and how to reach it, not
# which model it is or what it costs.
COPIED = ("api_base", "api_version", "custom_llm_provider", "litellm_credential_name", "organization",
          "extra_headers", "timeout", "stream_timeout", "max_retries")
NO_DB = ("LiteLLM runs without a database, and adds a model only in its database mode: give it"
         " DATABASE_URL (Postgres) and STORE_MODEL_IN_DB=True, restart it, then add again")


def endpoint() -> str:
    """The LiteLLM hub's endpoint name; ValueError when there is none."""
    name = endpoints.find_hub("litellm")
    if not name:
        raise ValueError("no endpoint is a LiteLLM hub; add it on the Manage page (under Endpoints) as kind hub, LiteLLM")
    return name


def _call(path: str, body: dict | None = None):
    name = endpoint()
    root = endpoints.base(name).removesuffix("/v1")
    req = urllib.request.Request(root + path, data=None if body is None else json.dumps(body).encode(),
                                 method="GET" if body is None else "POST",
                                 headers={"Authorization": f"Bearer {endpoints.admin_key(name)}",
                                          "Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15 if body is None else 60) as r:
        return json.loads(r.read() or b"null")


def _said(exc: urllib.error.HTTPError) -> str:
    """LiteLLM's message from its {"error": {"message": ...}} reply, trimmed."""
    text = exc.read().decode("utf-8", "replace")
    try:
        err = json.loads(text).get("error")
        text = str(err.get("message") if isinstance(err, dict) else err or text)
    except (ValueError, AttributeError):
        pass
    return text[:300]


def _upstream(d: dict) -> tuple[str, str, str | None]:
    """A deployment's (connection, base URL or "", the provider prefix a new
    model id takes, None when its model string is passed through as is)."""
    p = d.get("litellm_params") or {}
    model, base = str(p.get("model") or ""), str(p.get("api_base") or "").rstrip("/")
    prefix = None if p.get("custom_llm_provider") or "/" not in model else model.split("/", 1)[0]
    name = urllib.parse.urlsplit(base).hostname if base else p.get("custom_llm_provider") or prefix or "openai"
    return name or base, base, prefix


def _deployments() -> list[dict]:
    return (_call("/model/info") or {}).get("data") or []


def upstreams() -> dict[str, tuple[str, str]]:
    """Connection -> (base URL or "", the prefix served ids take: none on LiteLLM).
    What Discover matches a provider against (discover.hub_routes)."""
    return {n: (b, "") for n, b, _p in map(_upstream, _deployments())}


def _ready() -> dict:
    """The readiness details (any valid key), or the public probe on a release before them."""
    try:
        return _call("/health/readiness/details") or {}
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        return _call("/health/readiness") or {}


def plan(connection: str, model_id: str) -> dict:
    """What adding would send. Writes nothing; ValueError when it cannot be done here."""
    ready = _ready()
    release, db = str(ready.get("litellm_version") or ""), str(ready.get("db") or "")
    if db == "Not connected":
        raise ValueError(NO_DB)
    if db and db != "connected":
        raise ValueError(f"LiteLLM's database is {db}; adding a model needs it reachable")
    deps = _deployments()
    mine = [d for d in deps if _upstream(d)[0] == connection]
    if not mine:
        raise ValueError(f"no LiteLLM deployment is on a connection called {connection!r} (a provider such as"
                         " groq, or the host of an api_base); add one model there in LiteLLM first")
    # One name, two upstreams: LiteLLM would spread the calls across both.
    same = [d for d in deps if d.get("model_name") == model_id]
    other = next((_upstream(d)[0] for d in same if _upstream(d)[0] != connection), None)
    if other:
        raise ValueError(f"LiteLLM already serves {model_id} from {other}; a second deployment under that"
                         " name would share its calls with it")
    sib = next((d for d in mine if (d.get("litellm_params") or {}).get("litellm_credential_name")), mine[0])
    p, (_n, base, prefix) = sib.get("litellm_params") or {}, _upstream(sib)
    # Masked values ('*') are LiteLLM hiding a secret; sent back they would
    # replace it. A dict holding one (extra_headers) is left out whole, and said.
    given = [k for k in COPIED if p.get(k) not in (None, "", {})]
    params = {k: p[k] for k in given if "**" not in json.dumps(p[k])}
    params["model"] = f"{prefix}/{model_id}" if prefix else model_id
    cred = params.get("litellm_credential_name")
    return {"connection": connection, "base_url": base, "model_id": model_id, "served": model_id,
            "deployment": "exists" if same else "create", "copy_from": sib.get("model_name"),
            "litellm_params": params, "model_info": {"tags": [connection]},
            "not_copied": [k for k in given if k not in params],
            "key": f"the stored credential {cred}" if cred else "none copied (LiteLLM's environment variable for it)",
            "database_mode": True if any((d.get("model_info") or {}).get("db_model") for d in deps)
            else "not shown until the add, which writes nothing if it is off",
            "version": release, "version_note": endpoints.version_note("litellm", release, CHECKED)}


def add(connection: str, model_id: str) -> tuple[str, list[str]]:
    """Do it. (served id, what was done)."""
    p = plan(connection, model_id)
    done = [p["version_note"]] if p["version_note"] else []
    if p["deployment"] == "exists":
        return p["served"], done + [f"LiteLLM already has a {model_id} deployment on {connection}; nothing added"]
    body = {"model_name": p["served"], "litellm_params": p["litellm_params"], "model_info": p["model_info"]}
    try:
        made = _call("/model/new", body) or {}
    except urllib.error.HTTPError as exc:
        said = _said(exc)
        if "STORE_MODEL_IN_DB" in said:
            raise ValueError("LiteLLM has a database but keeps its models in config.yaml: set STORE_MODEL_IN_DB=True"
                             " (or general_settings: store_model_in_db: true), restart it, then add again;"
                             " nothing was added") from None
        if "No DB Connected" in said:
            raise ValueError(NO_DB) from None
        if exc.code in (401, 403):
            raise ValueError(f"LiteLLM refused the key for /model/new ({said}): it takes the master key or an"
                             " admin's; store it as this endpoint's admin key") from None
        raise ValueError(f"LiteLLM refused /model/new: {exc.code} {said}") from None
    done.append(f"created a {model_id} deployment on the {connection} connection (LiteLLM id"
                f" {made.get('model_id', '?')}), reaching it as {p['copy_from']} does, key: {p['key']};"
                f" labelled {connection}" + (f"; not copied, as LiteLLM masks them: {', '.join(p['not_copied'])}"
                                             if p["not_copied"] else ""))
    for _ in range(10):                        # LiteLLM reloads its router from the database
        if p["served"] in endpoints.models(endpoint()):
            break
        time.sleep(2)
    else:
        done.append(f"LiteLLM does not list {p['served']} yet; onboarding will say if it never does")
    return p["served"], done


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    print(json.dumps(plan(sys.argv[1], sys.argv[2]), indent=2))


if __name__ == "__main__":
    main()
