"""Public model lists the bench reads: FreeLLMAPI's free catalog and models.dev.

    python metrics/sources.py            # fetch them now (normally once a day)

Discover leads with FreeLLMAPI (easier for a person to follow: a page per
model, its limits, ranks and quirks) and fills the rest from models.dev, which
alone has prices, paid models, providers' API base URLs and modalities.

WHY THESE TWO
-------------
A comparison on 2026-10-17 of models.dev, FreeLLMAPI, truefoundry/models,
ClawLabsAI/free-ai-models and OpenRouter's own list found:
- models.dev (MIT, anomalyco/models.dev): 232 providers, ~9,000 models, price
  per model per provider, context and output limits, tool calling, reasoning,
  modalities, and each provider's API base URL. OpenRouter's free models are in
  it exactly (19 of 19), so OpenRouter needs no separate pull.
- FreeLLMAPI's free monthly catalog (MIT project, tashfeenahmed/freellmapi):
  219 of its 360 free models and 17 of its providers are not in models.dev, and
  it carries rate limits (rpm/rpd/tpm/tpd) and per-model operating notes.
  Only the free monthly tier is read, never the premium feed.
- truefoundry/models was stale for free models and ClawLabs duplicated
  models.dev's coverage, so neither is pulled; each is a small adapter later.

Both are cached in runs/cache/ (gitignored) and refetched at most once a day:
nothing here is called per request.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
CACHE = DATA / "runs" / "cache"
FEEDS = {
    "modelsdev": "https://models.dev/api.json",
    "freellmapi": "https://api.freellmapi.co/v1/latest",
    # The model pages freellmapi.co has (its sitemap): a page exists only for
    # what its monthly snapshot lists, so Discover links only to those.
    "freellmapi-pages": "https://freellmapi.co/sitemap-core.xml",
}
MAX_AGE_S = 20 * 3600

# The bench's connection hosts (route.connection_of) -> models.dev provider id.
# An install's own names (a proxy fronting one vendor) come from
# config/connections.json `models_dev`, which wins.
CONNECTION_TO_MODELSDEV = {
    "nvidia-api": "nvidia", "ollama-cloud": "ollama-cloud", "mistral": "mistral",
    "groq": "groq", "openrouter": "openrouter",
    "api.inceptionlabs.ai": "inception", "api.reflection.ai": "reflection",
    "cerebras": "cerebras",
}


def connection_to_modelsdev() -> dict[str, str]:
    """The built-in map with this install's own names over it."""
    import endpoints
    return {**CONNECTION_TO_MODELSDEV, **(endpoints.connections().get("models_dev") or {})}


# models.dev providers whose free models answer only inside the provider's own
# client, so Discover leaves those out; their paid models stay. opencode, tried
# 2026-10-21 through Open WebUI: "opencode's free tier can only be used from
# within opencode". opencode-go serves the same free ids, assumed the same.
# An install switches a provider either way on Settings, which writes
# config/connections.json `free_in_own_client_only`, e.g. {"opencode": false}.
FREE_IN_OWN_CLIENT_ONLY = {"opencode": True, "opencode-go": True}


def free_in_own_client_only(over: dict | None = None) -> dict[str, bool]:
    """Provider -> whether its free models are left out: built-in, with this
    install's switches over it."""
    if over is None:
        import endpoints
        over = endpoints.connections().get("free_in_own_client_only")
    if not isinstance(over, dict):
        over = {}                   # a hand-written list or true: ignored rather than taking Discover down
    return {**FREE_IN_OWN_CLIENT_ONLY, **over}
# FreeLLMAPI platform id -> models.dev provider id, where they are the same
# provider under another name (from the 2026-10-17 comparison).
FREELLMAPI_TO_MODELSDEV = {
    "cloudflare": "cloudflare-workers-ai", "ollama": "ollama-cloud", "ovh": "ovhcloud",
    "radeon": "amd", "zhipu": "zhipuai",
}


def fetch(name: str, force: bool = False) -> dict:
    """A feed, from the cache when it is under a day old."""
    path = CACHE / f"{name}.json"
    if not force and path.exists() and time.time() - path.stat().st_mtime < MAX_AGE_S:
        return json.loads(path.read_text(encoding="utf-8"))
    req = urllib.request.Request(FEEDS[name], headers={"User-Agent": "benchwarmers"})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = (json.load(r) if not FEEDS[name].endswith(".xml") else
                {"slugs": sorted(set(re.findall(r"/model/([^<\"/]+)<", r.read().decode("utf-8"))))})
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return data


def cached(name: str) -> dict:
    """The last fetched copy, or {} — for pages that must not wait on the network."""
    try:
        return json.loads((CACHE / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def norm_model(model_id: str) -> str:
    """Comparable model name: last path segment, no :free, no date/version tail."""
    s = model_id.lower().split("/")[-1].removesuffix(":free")
    s = re.sub(r"^(llm7|us|eu|apac|global)\.", "", s)
    prev = None
    while prev != s:
        prev = s
        # ...and reasoning-effort tails a proxy adds to one model (gemini-3.8-flash-high).
        s = re.sub(r"(@[\w.]+|-v\d+:\d+|-\d{4}-\d{2}-\d{2}|-\d{8}|-latest|-(?:high|medium|low|minimal))$", "", s)
    return s


def modelsdev_entry(provider: str, served: str) -> dict | None:
    """models.dev's record of `served` under one provider: exact id, else by name."""
    models = (cached("modelsdev").get(provider) or {}).get("models") or {}
    if served in models:
        return models[served]
    key = norm_model(served)
    return next((m for k, m in models.items() if norm_model(k) == key), None)


def modelsdev_anywhere(served: str) -> tuple[str, dict] | None:
    key = norm_model(served)
    for prov, p in cached("modelsdev").items():
        for k, m in (p.get("models") or {}).items():
            if norm_model(k) == key:
                return prov, m
    return None


def freellmapi_entry(served: str, platform: str | None = None) -> dict | None:
    key = norm_model(served)
    for m in cached("freellmapi").get("models") or []:
        if norm_model(m.get("modelId", "")) == key and (platform is None or m.get("platform") == platform):
            return m
    return None


def freellmapi_name(display_name: str) -> str:
    """A FreeLLMAPI display name without its trailing note, "Llama 4 Scout (CF)"
    -> "Llama 4 Scout": the name freellmapi.co gives the model on every platform."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", display_name or "").strip()


def freellmapi_page(display_name: str, pages: set[str]) -> str | None:
    """The model's freellmapi.co page, if `pages` (its sitemap) has it. The site
    names a page by the display name without its note, lowercased, each run of
    other characters one dash (buildBundles in its app.js, read 2026-10-28)."""
    slug = "-".join(re.findall(r"[a-z0-9]+", freellmapi_name(display_name).lower()))
    return f"https://freellmapi.co/model/{slug}" if slug in pages else None


def is_free(cost: dict | None) -> bool | None:
    if not cost:
        return None
    vals = [cost.get("input"), cost.get("output")]
    return None if all(v is None for v in vals) else all((v or 0) == 0 for v in vals)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for name in FEEDS:
        try:
            data = fetch(name, force=True)
            size, unit = {"modelsdev": (len(data), "providers"),
                          "freellmapi-pages": (len(data.get("slugs", [])), "model pages")}.get(
                              name, (len(data.get("models", [])), "models"))
            print(f"  {name}: {size} {unit} at {dt.datetime.now():%H:%M}")
        except Exception as exc:                       # noqa: BLE001
            print(f"  {name}: could not fetch ({type(exc).__name__}: {exc}); keeping the cached copy")


if __name__ == "__main__":
    main()
