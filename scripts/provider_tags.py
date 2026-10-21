"""Record which connection each model arrives through.

    python scripts/provider_tags.py

Open WebUI fronts several upstreams, and which one a model comes through
decides things the model itself does not: whether it shares a rate limit with
another model, whether it is a free tier, and who to ask when it disappears.
`kimi-k3` and `gemini-3.8-flash-high` are different vendors but the same
connection, so they cap together.

Writes `config/provider-tags.json` — a plain map of model id to a short tag —
which the dashboard reads. A file rather than a live call so the dashboard
stays a view of the repository and works with the network down.

**THE KEYS ARE NEVER READ.** `/openai/config` returns `OPENAI_API_KEYS`
alongside the base URLs, in plaintext. This takes `OPENAI_API_BASE_URLS` and
the host name off each URL, and nothing else, so no secret can reach a file
that is committed. Worth knowing that endpoint behaves that way.

A REFINEMENT, NOT A PREREQUISITE. Only an Open WebUI hub (endpoints.find_hub)
has connections to name; without one this writes nothing and exits cleanly,
and every model's connection is the endpoint it is registered on
(route.connection_of), which needs no file at all.
"""

from __future__ import annotations

import json
import pathlib
import sys
import urllib.request

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "metrics"))
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
OUT = DATA / "config" / "provider-tags.json"
import endpoints  # noqa: E402
TIMEOUT = 30

# Host -> the name a person would use for it. Anything unlisted falls back to
# its own hostname, which is still more use than an index.
# Connections on a local network are named by hand, because the API will not
# say: `hosts` in config/connections.json. The suffix that follows (e.g.
# `/vendor-a`) comes from the MODEL's own `owned_by` and is worth keeping: one
# proxy fronts several upstreams, so `my-proxy/vendor-a` and `my-proxy/vendor-b`
# share a host and a rate limit while being entirely different vendors.

NAMES = {
    "ollama.com": "ollama-cloud",
    "openrouter.ai": "openrouter",
    "api.groq.com": "groq",
    "api.cerebras.ai": "cerebras",
    "integrate.api.nvidia.com": "nvidia-api",
    "api.mistral.ai": "mistral",
    "api.llm7.io": "llm7-api",
    "api.openai.com": "openai",
    "api.anthropic.com": "anthropic",
}


def get(hub: str, path: str) -> dict:
    """GET off the Open WebUI server's root, where `/openai/config` and
    `/api/models` both hang, with the hub's own key."""
    request = urllib.request.Request(
        endpoints.base(hub).removesuffix("/api") + path,
        headers={"Authorization": f"Bearer {endpoints.key(hub)}"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.load(response)


def connection_names(hub: str) -> list[str]:
    """One short name per connection index. URLs only — never the keys."""
    urls = get(hub, "/openai/config").get("OPENAI_API_BASE_URLS", [])
    names = []
    for url in urls:
        host = url.split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0]
        if host.replace(".", "").isdigit():
            # A BARE IP NAMES NOTHING, and guessing from it was worse than
            # useless: a proxy labelled `lan-proxy` is described by where it
            # sits rather than what it is, and Open WebUI does not expose
            # connection names over the API to find that out —
            # `OPENAI_API_CONFIGS` has a `tags` field and it is empty. So it
            # is written down in config/connections.json `hosts`.
            names.append((endpoints.connections().get("hosts") or {}).get(host, "lan-proxy"))
        else:
            names.append(NAMES.get(host, host))
    return names


def main() -> None:
    hub = endpoints.find_hub("openwebui")
    if not hub:
        print("no Open WebUI hub: nothing to tag; each model's connection is its endpoint")
        return
    names = connection_names(hub)
    tags = {}
    for entry in get(hub, "/api/models").get("data", []):
        model_id = entry.get("id")
        if not model_id:
            continue
        if entry.get("connection_type") == "local":
            tags[model_id] = "local"
            continue
        index = entry.get("urlIdx")
        tag = names[index] if isinstance(index, int) and index < len(names) else "?"
        # The upstream owner, when it says something the connection does not.
        owner = (entry.get("openai") or {}).get("owned_by")
        if owner and owner not in ("openai", tag) and tag != "?":
            tag = f"{tag}/{owner}"
        tags[model_id] = tag

    # Models registered on another endpoint arrive through THAT endpoint,
    # whatever Open WebUI would have called them.
    for r in endpoints.registry():
        if r["endpoint"] not in ("?", hub):
            tags[r["served"]] = r["endpoint"]

    OUT.write_text(json.dumps(tags, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    print(f"wrote {OUT}  ({len(tags)} models)")
    seen: dict[str, int] = {}
    for tag in tags.values():
        seen[tag] = seen.get(tag, 0) + 1
    for tag, count in sorted(seen.items(), key=lambda kv: -kv[1]):
        print(f"  {tag:24} {count}")


if __name__ == "__main__":
    main()
