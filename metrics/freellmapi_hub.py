"""The freellmapi hub adapter: which of its /v1 models are not one model, and what is left per provider.

    python metrics/freellmapi_hub.py <endpoint>     # what each job finds; changes nothing

freellmapi (github.com/tashfeenahmed/freellmapi, MIT) puts one /v1 over the
free tiers of many providers, with the keys it holds. Its /v1/models lists,
before its catalog, entries that are routers, not models: `auto` (it picks a
model per request), `fusion` (a panel and a judge, answering as one),
`auto:<profile>` (a named fallback chain) and Claude-named slots
(`claude-sonnet-4-5`: a model it picks, or one its operator pinned). A verdict
must be about one model (COMPARISON.md), so onboarding leaves those out
(virtual, through endpoints.virtual). A catalog row is one model: freellmapi
"unifies" the providers that
serve the same model under one id and fails over between them, which keeps
that one model's seat answering.

Everything here is asked with the endpoint's own key, freellmapi's unified API
key. Its admin API (/api) wants a dashboard login session, which is not a key
to keep, so nothing here asks it; a client profile's key (sk-cp-...) lists
models but is refused the status routes.

Its jobs (endpoints.ADAPTERS): virtual, and quota (what is left per provider
pool, and each provider's state: rate-limited, or keys failing their check,
for its Quota tile). Left out: add (a model
comes with a provider's key, added in freellmapi), upstreams and served_by
(its /v1/models does not say which provider serves a row, and under unify it
may be several), and a version note (/livez says its OpenAPI spec's version,
0.4.1 for many releases; the release is said only under /api).

Checked against freellmapi 0.16.37's source (commit 7ad9097, 2026-11-20):
server/src/routes/proxy.ts (GET /v1/models: buildCatalogEntries, the router
entries first; only catalog rows carry `supported_parameters`, since #514,
and `execution_status`), server/src/services/anthropic-map.ts
(CLAUDE_FAMILY_ALIASES: claude-opus-4-5, claude-sonnet-4-5, claude-haiku-4-5),
server/src/services/model-listing.ts and model-groups.ts (unify: one row per
model, over its providers), server/src/routes/status.ts (GET /v1/providers
and /v1/quota-forecast: the unified key only, else 401; /livez keyless),
server/src/services/quota-forecast.ts (a pool's requests left, limit and
reset), server/src/middleware/requireAuth.ts (/api: a dashboard session).
"""

from __future__ import annotations

import json
import pathlib
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "metrics"))
import endpoints  # noqa: E402

ROUTERS = {"auto": "freellmapi's router, which picks a model for each request",
           "fusion": "a panel of models and a judge, answering as one"}


def virtual(entry: dict) -> str:
    """Why a /models entry is not one model, "" when it is (endpoints.virtual).
    A Claude-named slot is told by having no supported_parameters, which every
    catalog row has carried since #514 (2026-07), before the slots existed."""
    i = str(entry.get("id") or "")
    if i in ROUTERS:
        return ROUTERS[i]
    if i.startswith("auto:"):
        return f"a fallback chain, freellmapi's {i[5:]} profile"
    if i.startswith("claude-") and "supported_parameters" not in entry:
        return "a slot freellmapi routes to a model it picks, or one its operator pinned"
    return ""


def _get(endpoint: str, path: str) -> dict:
    req = urllib.request.Request(endpoints.base(endpoint) + path, headers={
        "Authorization": f"Bearer {endpoints.key(endpoint)}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise ValueError(f"freellmapi refused {endpoint}'s key on /v1{path}: it answers there to its unified"
                             " API key only, not a client profile's") from None
        raise ValueError(f"freellmapi answered {exc.code} on /v1{path}") from None


def quota(endpoint: str) -> dict:
    """Each provider pool's requests left, as Quota management's tiles take
    them, and each provider that cannot be called now, as notes."""
    groups = [{"label": str(p.get("pool") or p.get("platform") or "").replace("::", " · "),
               "windows": [{"label": "requests", "left": p["remaining_pct"], "remaining": p.get("remaining"),
                            "of": p.get("limit"), "reset_at": p.get("reset_at")}]}
              for p in _get(endpoint, "/quota-forecast").get("pools") or []
              if isinstance(p, dict) and isinstance(p.get("remaining_pct"), (int, float))]
    notes = []
    for p in _get(endpoint, "/providers").get("providers") or []:
        name = p.get("name") or p.get("platform")
        if p.get("status") == "rate_limited":
            notes.append(f"{name}: rate-limited" + (f" until {p['resume_at']}" if p.get("resume_at") else ""))
        elif p.get("status") == "invalid":
            notes.append(f"{name}: its keys fail freellmapi's check"
                         + (f" ({str(p['last_error'])[:120]})" if p.get("last_error") else ""))
    return {"groups": groups, "notes": notes}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    ep = sys.argv[1]
    print(json.dumps({"virtual": {i: why for i, e in endpoints.models(ep).items() if (why := virtual(e))},
                      "quota": quota(ep)}, indent=2))


if __name__ == "__main__":
    main()
