"""The OmniRoute hub adapter: its single models told from its combos, a model added to one of its providers, its sign-ins' quota.

    python metrics/omniroute.py <connection> <model id>     # what would change, changes nothing

OmniRoute (github.com/cheaper-inference/OmniRoute) is a gateway to many
providers, with accounts, quotas and routing of its own. Its /v1/models lists
three kinds of entry:

  - one provider's model, `<prefix>/<model>`, owned_by that provider (groq,
    codex, claude...): one model, the only kind the bench should seat;
  - a combo, or one of its built-in routers (`auto`, `auto/<x>`; fusion is a
    combo strategy), all owned_by "combo": each picks a model per request,
    so a verdict on it would be about whichever answered. `virtual` names
    them, and onboarding leaves them out (endpoints.virtual);
  - embeddings, images, audio and the like, with a `type`: left to the
    picker, as any endpoint's are.

A CONNECTION is a provider OmniRoute has an account for, by the provider id
its models are owned_by. Adding a model is one POST /api/provider-models
{provider, modelId}: a custom model on that provider, which OmniRoute then
serves as `<prefix>/<model>` like the provider's own. Nothing else is sent.
A compatible node's models are owned_by its prefix, not the node's id that
POST needs, so plan() checks the provider id against /api/providers first
and refuses one OmniRoute has no connection under.

Its sign-ins' quota: OmniRoute answers CLI Proxy API's quota routes for its
Claude and Codex OAuth sign-ins and no others (an Antigravity sign-in is not
listed there): auth-files lists them, api-call makes one of four allowlisted
usage requests (ChatGPT's wham/usage among them) with the sign-in's token,
injected by OmniRoute. sign_ins and api_call; quotaview.read_signins is the
flow. Its own cached Provider Limits (/api/usage/provider-limits) cover more
providers in a shape of its own, and are not read.

Left out, Discover's free-tier source: OmniRoute's free-tier table
(/api/free-models) is reachable, but Discover's sources are the catalog
feeds, merged by their own code (discover.build), not a hub job; and the
table is OmniRoute's reading of the providers' docs, which the bench never
takes a limit from.

Keys: the endpoint's key is an OmniRoute API key. The management routes take
a key with the `manage` scope: the endpoint's admin key (endpoints.admin_key),
else its own key, which serves when it has that scope. An inference-only key
gets 403, said as that.

Checked against OmniRoute v3.8.54's source (2026-11-20):
src/app/api/v1/models/catalog.ts (combos and auto/* owned_by "combo"; a
provider's models `<alias>/<id>`, owned_by the provider id, root the id, or a
node's prefix), autoCatalogIds.ts (bare `auto` too), catalogPagination.ts (the
whole list without `limit`), src/app/api/provider-models/route.ts (POST
{provider, modelId}), src/app/api/providers/route.ts (GET ?provider=
{connections, total}), src/lib/cliproxyCompat/managementApi.ts and http.ts
(GET /v0/management/auth-files {files: [{auth_index, provider, disabled,
id_token}]}; POST /v0/management/api-call {status_code, body}, 403
api_call_not_allowed off its allowlist; a dashboard session or a manage
key), src/app/api/monitoring/health/route.ts and
src/lib/monitoring/observability.ts (system.version, for a manage key only),
src/lib/api/requireManagementAuth.ts (401 a wrong key, 403 one without manage).
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
SUPPORTS = {"allow_list": "custom models", "tags": "", "logo": "", "access": "",
            "link": "/dashboard/providers"}
CHECKED = ("3.8",)


def endpoint() -> str:
    """The OmniRoute hub's endpoint name; ValueError when there is none."""
    name = endpoints.find_hub("omniroute")
    if not name:
        raise ValueError("no endpoint is an OmniRoute hub; add it on the Endpoints page as kind hub, OmniRoute")
    return name


def virtual(entry: dict) -> str:
    """Why a /models entry is not one model, "" when it is."""
    return ("an OmniRoute combo or router (auto, fusion and the like), which picks a model per request"
            if entry.get("owned_by") == "combo" else "")


def _call(path: str, body: dict | None = None):
    """One management call with the manage key; its refusal as a ValueError saying what to change."""
    name = endpoint()
    req = urllib.request.Request(endpoints.base(name).removesuffix("/v1") + path,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {endpoints.admin_key(name)}",
                                          "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20 if body is None else 60) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as exc:
        try:
            err = json.loads(exc.read()).get("error")
            said = str(err.get("message") if isinstance(err, dict) else err or "")[:200]
        except (ValueError, AttributeError):
            said = ""
        if exc.code == 403 and "manage" in said:
            raise ValueError(f"OmniRoute's key has no manage scope ({said}): give it that scope in OmniRoute's"
                             f" API Keys, or store a key that has it as the admin key (open {name} on Endpoints: A new key)") from None
        if exc.code == 401:
            raise ValueError(f"OmniRoute refused the key ({said}); store one with the manage scope as the admin"
                             f" key (open {name} on Endpoints: A new key)") from None
        raise ValueError(f"OmniRoute answered {exc.code} on {path.split('?')[0]}: {said}") from None


def version() -> tuple[str, str]:
    """(release, endpoints.version_note's sentence). Advice only."""
    try:
        v = str(((_call("/api/monitoring/health") or {}).get("system") or {}).get("version") or "")
    except Exception:                                  # noqa: BLE001
        v = ""
    return v, endpoints.version_note("omniroute", v, CHECKED)


def _prefixes(listing: dict[str, dict]) -> dict[str, str]:
    """Provider -> the prefix its served ids take, from the single models it
    lists: `<prefix>/<root>`. The first form listed where it lists two."""
    out: dict[str, str] = {}
    for mid, e in listing.items():
        root, own = str(e.get("root") or ""), str(e.get("owned_by") or "")
        if own and root and not virtual(e) and mid.endswith("/" + root):
            out.setdefault(own, mid[:-len(root) - 1])
    return out


def upstreams() -> dict[str, tuple[str, str]]:
    """Provider -> ("", the prefix its served ids take). What Discover matches
    a provider against (discover.hub_routes); OmniRoute lists no base URLs to
    an API key, so by name only."""
    return {own: ("", prefix) for own, prefix in _prefixes(endpoints.models(endpoint())).items()}


def plan(connection: str, model_id: str) -> dict:
    """What adding would send. Writes nothing; ValueError when it cannot be done here."""
    listing = endpoints.models(endpoint())
    prefix = _prefixes(listing).get(connection)
    if prefix is None:
        raise ValueError(f"OmniRoute serves no model of a provider called {connection!r}; connect that"
                         " provider in OmniRoute first")
    served = f"{prefix}/{model_id}"
    if virtual(listing.get(served) or {}):
        raise ValueError(f"OmniRoute serves {served} as a combo; a model under that name would be hidden by it")
    add = served not in listing
    if add and not (_call(f"/api/providers?provider={urllib.parse.quote(connection)}&limit=1") or {}).get("total"):
        raise ValueError(f"OmniRoute has no connection under the provider id {connection!r} (a compatible"
                         " node's models carry its prefix, not its id); add the model in OmniRoute's dashboard")
    release, note = version()
    return {"connection": connection, "model_id": model_id, "served": served, "add_to_list": add,
            "version": release, "version_note": note}


def add(connection: str, model_id: str) -> tuple[str, list[str]]:
    """Do it. (served id, what was done)."""
    p = plan(connection, model_id)
    done = [p["version_note"]] if p["version_note"] else []
    if not p["add_to_list"]:
        return p["served"], done + [f"OmniRoute already serves {p['served']}; nothing added"]
    _call("/api/provider-models", {"provider": connection, "modelId": model_id})
    done.append(f"added {model_id} to OmniRoute's {connection} provider as a custom model")
    for _ in range(10):                        # its catalog is cached for a moment
        if p["served"] in endpoints.models(endpoint()):
            break
        time.sleep(2)
    else:
        done.append(f"OmniRoute does not list {p['served']} yet; onboarding will say if it never does")
    return p["served"], done


def sign_ins() -> list[dict]:
    """Its Claude and Codex OAuth sign-ins, in the bench's shape (cliproxy.sign_ins)."""
    files = (_call("/api/v0/management/auth-files") or {}).get("files") or []
    return [{"provider": str(f.get("provider") or ""), "auth_index": f.get("auth_index"),
             "disabled": bool(f.get("disabled")), "account": (f.get("id_token") or {}).get("chatgpt_account_id"),
             "project": ""} for f in files if isinstance(f, dict)]


def api_call(auth_index, method: str, url: str, header: dict, data: str | None = None) -> dict:
    """One sign-in's usage request, made by OmniRoute with the sign-in's token:
    {status_code, body}. Only the four it allows; anything else is a 403."""
    return _call("/api/v0/management/api-call", {"auth_index": auth_index, "method": method, "url": url,
                                                  "header": header, **({"data": data} if data is not None else {})})


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    print(json.dumps(plan(sys.argv[1], sys.argv[2]), indent=2))


if __name__ == "__main__":
    main()
