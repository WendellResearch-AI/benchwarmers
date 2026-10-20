"""What each model on the bench can take and do: context window, output limit,
capabilities, rate limits — with where every value came from.

    python metrics/catalog.py            # refresh config/model-catalog.json from the sources
    python metrics/catalog.py <alias>    # one model's merged record

THREE FILES, ONE RECORD
-----------------------
    config/model-catalog.json      WRITTEN BY THE REFRESH. Per alias, what the
                                   sources say, and which source said it.
    config/model-overrides.json    WRITTEN BY YOU (the model popup). Per alias,
                                   any field, including its own rate limits.
    config/connection-limits.json  WRITTEN BY YOU (the Endpoints dialog). Per
                                   connection: rpm, rpd, tpm, tpd.

`info(alias)` merges them: an override beats the refresh, a model's own limits
beat its connection's. Rate limits are kept per CONNECTION (nvidia-api, groq,
openrouter…) because that is what a key's limits belong to and what models
behind it share; `route.py` groups by the same host.

WHERE THE FACTS COME FROM
-------------------------
- OpenRouter's public catalog (no key), matched by model id: context window,
  max output, input modalities (image -> vision, which covers OCR), supported
  parameters (tools, structured output, reasoning), knowledge cutoff. A model
  served elsewhere under the same id gets OpenRouter's figures, marked as such:
  the same weights, but a provider can cap context lower.
- Open WebUI's /api/models, where a connection passes metadata through
  (Inception, Reflection do; most do not).
- Everything else, including rate limits, by hand: freellmapi.co/models is a
  good place to look them up. The bench never fetches it; its data licence is
  not stated.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
CATALOG = DATA / "config" / "model-catalog.json"
OVERRIDES = DATA / "config" / "model-overrides.json"
LIMITS = DATA / "config" / "connection-limits.json"
sys.path.insert(0, str(ROOT / "metrics"))

FIELDS = ("context_window", "max_output", "vision", "tools", "structured_output",
          "reasoning", "input_modalities", "output_modalities", "knowledge_cutoff",
          "release_date", "open_weights", "description")
CAPS = ("tools", "vision", "structured_output", "reasoning")
LIMIT_KEYS = ("rpm", "rpd", "tpm", "tpd")


def _read(path: pathlib.Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def catalog() -> dict:
    return _read(CATALOG).get("models", {})


def overrides() -> dict:
    return {k: v for k, v in _read(OVERRIDES).items() if not k.startswith("_")}


def connection_limits() -> dict:
    return {k: v for k, v in _read(LIMITS).items() if not k.startswith("_")}


def connection_of(alias: str) -> str:
    import route
    return route.connection_of(alias)


def info(alias: str) -> dict:
    """The merged record: refresh, then overrides; limits from the model's own
    override, else its connection. `sources` names where each field came from."""
    import learn
    auto = catalog().get(alias, {})
    mine = overrides().get(alias, {})
    seen = learn.learned().get(alias, {})
    out = {k: auto.get(k) for k in FIELDS}
    out["cost"], out["quirks"] = auto.get("cost"), auto.get("quirks")
    sources = dict(auto.get("sources", {}))
    # LEARNED (metrics/learn.py): a context window an error proved, if lower;
    # a capability seen working, whatever the refresh said.
    if (cw := (seen.get("context_window") or {}).get("value")) and cw < (out["context_window"] or 1 << 62):
        out["context_window"], sources["context_window"] = cw, f"learned {seen['context_window']['at'][:10]}"
    for cap, ev in (seen.get("capabilities") or {}).items():
        if cap in CAPS and ev.get("value"):
            out[cap], sources[cap] = True, f"probe {ev['at'][:10]}"
    for k in FIELDS:
        if mine.get(k) is not None:
            out[k], sources[k] = mine[k], "you"
    conn = connection_of(alias)
    limits = connection_limits().get(conn, {})
    listed = auto.get("limits") or {}               # the refresh's (FreeLLMAPI) figures
    out["limits"] = {k: (mine.get("limits") or {}).get(k, limits.get(k, listed.get(k)))
                     for k in LIMIT_KEYS}
    if mine.get("limits"):
        sources["limits"] = "you (this model)"
    elif any(limits.get(k) for k in LIMIT_KEYS):
        sources["limits"] = f"you ({conn})"
    for k, ev in (seen.get("limits") or {}).items():
        if k in LIMIT_KEYS and not (mine.get("limits") or {}).get(k) and \
                ev["value"] < (out["limits"].get(k) or 1 << 62):
            out["limits"][k] = ev["value"]
            sources[f"limits.{k}"] = f"learned {ev['at'][:10]}"
    if seen.get("served_as"):
        out["served_as"] = seen["served_as"]
    out.update(connection=conn, sources=sources, refreshed=_read(CATALOG).get("refreshed"))
    return out


def consolidate(aliases: list[str]) -> dict:
    """What a group of models can do together: the smallest context window
    (the real limit for the group), capabilities every member has, distinct
    connections (members behind one share its rate limit), tightest limits."""
    rows = {a: info(a) for a in aliases}
    known = [r["context_window"] for r in rows.values() if r.get("context_window")]
    tight = {}
    for k in LIMIT_KEYS:
        vals = [r["limits"][k] for r in rows.values() if r["limits"].get(k)]
        tight[k] = min(vals) if vals else None
    return {"context_window": min(known) if known else None,
            "context_unknown": [a for a, r in rows.items() if not r.get("context_window")],
            "capabilities": [c for c in CAPS if rows and all(r.get(c) for r in rows.values())],
            "connections": sorted({r["connection"] for r in rows.values()}),
            "limits": tight}


def _fetch(url: str, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def _watch_price(alias: str, served: str, conn: str, old: dict | None, new: dict | None) -> None:
    """A free model that now has a price: raise it, before calls start failing.
    models.dev can lag, so this is an early warning, not proof; the bench's own
    'paywalled' errors still decide."""
    import alerts
    import endpoints
    import sources
    # Paid by subscription (config/connections.json `paid`): a price there is expected.
    if conn in (endpoints.connections().get("paid") or ()):
        return
    key = f"model:paid:{served}"
    if sources.is_free(old) and sources.is_free(new) is False:
        alerts.raise_alert(key, "went paid",
                           f"{alias} is listed with a price now (models.dev): input"
                           f" {new.get('input')}, output {new.get('output')} per million tokens",
                           "check the provider; retire it or re-point it at a free slug", alias=alias)
    elif sources.is_free(new):
        alerts.clear(key)


def refresh() -> dict:
    """Rebuild config/model-catalog.json for every registered chat model."""
    import endpoints
    import sources
    from run import _registered_names
    for name in sources.FEEDS:
        try:
            sources.fetch(name)                      # at most once a day; cached copy otherwise
        except Exception:                            # noqa: BLE001
            pass
    before = catalog()
    registered = _registered_names()                   # alias -> served name
    try:
        orm = {m["id"]: m for m in _fetch("https://openrouter.ai/api/v1/models")["data"]}
    except Exception as exc:                           # noqa: BLE001
        sys.exit(f"cannot read OpenRouter's catalog: {exc}")
    # Each endpoint's listing kept apart, and a model read ONLY from the one it
    # is registered on: the direct openrouter endpoint lists qwen/qwen3.8-27b
    # too, and pooling the listings filed OpenRouter's 1M window as groq's.
    listings: dict[str, dict] = {}
    for name in endpoints.load():
        try:
            listings[name] = endpoints.models(name)
        except Exception:                              # noqa: BLE001
            listings[name] = {}
    home = {r["alias"]: r["endpoint"] for r in endpoints.registry()}
    models = {}
    for alias, served in registered.items():
        rec, src = {}, {}
        ep = home.get(alias, endpoints.default())
        entry = listings.get(ep, {}).get(served, {})
        slug = (entry.get("openrouter") or {}).get("slug")
        o = orm.get(served) or orm.get(slug or "") or orm.get(served.removesuffix(":free"))
        if o:
            via = "openrouter" if connection_of(alias) == "openrouter" else "openrouter (same model id)"
            arch, params = o.get("architecture") or {}, set(o.get("supported_parameters") or [])
            rec.update(context_window=o.get("context_length"),
                       max_output=(o.get("top_provider") or {}).get("max_completion_tokens"),
                       input_modalities=arch.get("input_modalities"),
                       output_modalities=arch.get("output_modalities"),
                       vision="image" in (arch.get("input_modalities") or []),
                       tools="tools" in params,
                       structured_output=bool(params & {"response_format", "structured_outputs"}),
                       reasoning=bool(params & {"reasoning", "include_reasoning"}),
                       knowledge_cutoff=o.get("knowledge_cutoff"),
                       description=(o.get("description") or "")[:240] or None)
            src.update({k: via for k, v in rec.items() if v is not None})
        # models.dev's entry FOR THE PROVIDER THAT SERVES IT beats an
        # OpenRouter same-id match: qwen3.8-27b on groq is capped at 131,042
        # tokens, which OpenRouter's 1,000,000 hid. Any-provider matches only
        # fill gaps.
        conn = connection_of(alias)
        md_prov = sources.connection_to_modelsdev().get(conn)
        md = sources.modelsdev_entry(md_prov, served) if md_prov else None
        md_src = f"models.dev {md_prov}"
        if md is None and (hit := sources.modelsdev_anywhere(served)):
            md_prov, md = hit
            md_src = f"models.dev {md_prov} (same model)"
        if md:
            mods = (md.get("modalities") or {}).get("input")
            vals = {"context_window": (md.get("limit") or {}).get("context"),
                    "max_output": (md.get("limit") or {}).get("output"),
                    "tools": md.get("tool_call"), "reasoning": md.get("reasoning"),
                    "input_modalities": mods, "vision": ("image" in mods) if mods else None,
                    "output_modalities": (md.get("modalities") or {}).get("output"),
                    "knowledge_cutoff": md.get("knowledge"), "release_date": md.get("release_date"),
                    "open_weights": md.get("open_weights")}
            exact = "(same model)" not in md_src
            for k, v in vals.items():
                if v is None:
                    continue
                if rec.get(k) is None or (exact and src.get(k, "").endswith("(same model id)")):
                    rec[k], src[k] = v, md_src
            if exact and md.get("cost"):
                rec["cost"], src["cost"] = md["cost"], md_src
        # FreeLLMAPI's free catalog: rate limits (models.dev has none), and a
        # context window only where nothing else gave one.
        fl = sources.freellmapi_entry(served, conn if conn in ("groq", "mistral", "nvidia", "openrouter") else None) \
            or sources.freellmapi_entry(served)
        if fl:
            lim = {k: v for k, v in (fl.get("limits") or {}).items() if v}
            if lim:
                rec["limits"], src["limits"] = lim, f"freellmapi {fl.get('platform')}"
            if rec.get("context_window") is None and fl.get("contextWindow"):
                rec["context_window"], src["context_window"] = fl["contextWindow"], f"freellmapi {fl.get('platform')}"
            elif fl.get("contextWindow") and fl["contextWindow"] != rec.get("context_window"):
                src["context_window_note"] = f"freellmapi says {fl['contextWindow']:,}"
            if fl.get("quirks"):
                rec["quirks"] = [q.get("title") for q in fl["quirks"] if q.get("title")][:5]
        # The serving connection's own figures, where it passes them, beat a
        # same-id OpenRouter match: they describe this provider's limits.
        feats = set(entry.get("supported_features") or [])
        own = {"context_window": entry.get("context_length"),
               "max_output": entry.get("max_output_length"),
               "input_modalities": entry.get("input_modalities"),
               "output_modalities": entry.get("output_modalities")}
        if entry.get("supported_features") is not None:
            own.update(tools="tools" in feats,
                       structured_output=bool(feats & {"json_mode", "structured_outputs"}))
        if entry.get("input_modalities") is not None:
            own["vision"] = "image" in entry["input_modalities"]
        for k, v in own.items():
            if v is not None:
                rec[k], src[k] = v, f"{ep or 'endpoint'} listing"
        rec["sources"] = src
        models[alias] = rec
        _watch_price(alias, served, conn, before.get(alias, {}).get("cost"), rec.get("cost"))
    data = {"_why": "Written by `python metrics/catalog.py`; edit config/model-overrides.json"
                    " (the model popup) instead, which wins.",
            "refreshed": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "models": models}
    CATALOG.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return data


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.path.insert(0, str(ROOT / "suite"))
    if len(sys.argv) > 1:
        print(json.dumps(info(sys.argv[1]), indent=2, ensure_ascii=False))
        return
    data = refresh()["models"]
    known = sum(1 for r in data.values() if r.get("context_window"))
    print(f"{len(data)} models; context window known for {known}. Fill the rest in the model popup.")
    for alias, r in sorted(data.items()):
        caps = ", ".join(c for c in CAPS if r.get(c)) or "-"
        print(f"  {alias:<34} ctx {r.get('context_window') or '?':>8}  {caps}")


if __name__ == "__main__":
    main()
