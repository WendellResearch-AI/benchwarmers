"""One list of models worth adding, from models.dev and FreeLLMAPI, deduped.

    python metrics/discover.py           # summary: free models by reach, new this week

The shopping list: Discover's Add (serve.py) puts a model on the bench by one
of the routes below. A model offered by the same provider in both sources
appears once, carrying both sources and the better-filled fields.

FREE
----
models.dev lists a price per provider, which is the PAID price even where the
provider has a free tier (Groq, Mistral, NVIDIA). So a model counts as free if
models.dev prices it at 0 (or its id ends :free), OR FreeLLMAPI lists it,
since FreeLLMAPI is a catalog of free tiers. The paid ones are kept, with
their price, behind the dashboard's toggle. A provider whose free models work
only in its own client (opencode) has those left out; see
sources.FREE_IN_OWN_CLIENT_ONLY.

REACH
-----
  on bench     already registered here
  connected    this install has a ROUTE to the provider (below)
  easy to add  the provider has an OpenAI-compatible API (models.dev gives its
               base URL), so it is one new endpoint (or hub connection) away
  other        a native API only, or a reseller nobody has vetted

ROUTES
------
The ways this install can reach a provider, each a row's `routes`:
  openwebui    a connection in Open WebUI, known because a bench model on the
               Open WebUI hub (endpoints.find_hub) is served from it. Adding
               goes through metrics/owui.py, so the model also appears in the
               chats. None without an Open WebUI hub.
  direct       an endpoint of its own in config/endpoints.json whose name or
               base URL is the provider's (models.dev gives the base). Adding
               is plain onboarding on that endpoint: bench only.
  hub          a connection in a LiteLLM or CLI Proxy API hub (hub_routes),
               whose name is the provider's or whose base URL is its API.
               Only the hub knows its connections, so it is asked when a
               model's popup opens, not here: build() never waits on the
               network. Adding goes through that hub's adapter.
               ponytail: so these routes do not count toward reach, and a
               paid model reachable only through such a hub stays hidden.
The same model offered by two providers (OpenRouter and NVIDIA) has a route
on each; the dashboard's Discover popup lists them together by `norm`.

NEW is the date a model was first seen in either list, kept in
runs/discover-seen.json (gitignored). The first build records everything as a
baseline, so only models that appear afterwards are badged new.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
SEEN = DATA / "runs" / "discover-seen.json"
sys.path.insert(0, str(ROOT / "metrics"))
sys.path.insert(0, str(ROOT / "suite"))
import sources  # noqa: E402

# FreeLLMAPI platform -> the bench connection host that serves it, where you have one.
FREELLMAPI_TO_CONNECTION = {"groq": "groq", "nvidia": "nvidia-api", "mistral": "mistral",
                            "openrouter": "openrouter", "ollama": "ollama-cloud",
                            "cerebras": "cerebras", "llm7": "llm7-api"}
NEW_DAYS = 7


def _on_bench() -> list[tuple[str, str, str, str, str]]:
    """(endpoint, connection, normalised model, alias, served) for every registered model."""
    import endpoints
    import route
    return [(r["endpoint"], route.connection_of(r["alias"]), sources.norm_model(r["served"]), r["alias"],
             r["served"]) for r in endpoints.registry()]


def _direct(md: dict) -> dict[str, list[str]]:
    """models.dev provider -> the endpoints of its own this install has (not a
    hub, which fronts many): by the connection-name map, else by base URL."""
    import endpoints
    names = sources.connection_to_modelsdev()
    by_base = {(p.get("api") or "").rstrip("/"): prov for prov, p in md.items() if p.get("api")}
    out: dict[str, list[str]] = {}
    data = endpoints.load()
    for name, e in data.items():
        prov = None if endpoints.hub(name, data) else names.get(name) or by_base.get(e["base"].rstrip("/"))
        if prov:
            out.setdefault(prov, []).append(name)
    return out


def _routes(r: dict, bench: list, owui_conns: set[str], direct: dict, owui: str | None) -> list[dict]:
    """How this install could add one row's model, and whether it already has.
    By the exact id, not `norm`: `vendor/m:free` on the bench is not `vendor/m`
    (paid) on the bench. Open WebUI may serve it as `<prefix>.<id>`. `owui`:
    the Open WebUI hub's endpoint, None without one (then `owui_conns` is empty)."""
    conn, name, mid, out = r["connection"], r["provider_name"], r["model"], []
    if conn and conn in owui_conns:
        out.append({"via": "openwebui", "connection": conn, "endpoint": owui,
                    "label": f"{name} via Open WebUI ({conn} connection)",
                    "note": "Also puts it in Open WebUI's chats: the connection's Model IDs,"
                            " a public record, its tag and logo.",
                    "on_bench_as": next((a for ep, c, _n, a, s in bench if ep == owui and c == conn
                                         and (s == mid or s.endswith("." + mid))), None)})
    for ep in direct.get(r["provider"], []):
        out.append({"via": "direct", "connection": conn or ep, "endpoint": ep,
                    "label": f"{name} direct ({ep} endpoint)",
                    "note": "Bench only: registered on its own endpoint, never in Open WebUI's chats.",
                    "on_bench_as": next((a for e, _c, _n, a, s in bench if e == ep and s == mid), None)})
    if len(out) > 1:
        # Two routes to one provider. On this install both use the one account
        # (OpenRouter's 50 free calls a day are per account, not per route).
        for x in out:
            x["note"] += f" Shares one {name} quota with the other {name} route when both use the same account."
    return out


def hub_routes(rows: list[dict]) -> tuple[list[list[dict]], list[str]]:
    """The hub routes (see ROUTES) for each of `rows`, and the hubs that could
    not be asked. Each LiteLLM or CLI Proxy API hub is asked once, by its
    adapter's upstreams(); an install with neither asks nothing."""
    import endpoints
    hubs, errors = [], []
    for which, label in endpoints.HUBS.items():
        ep = endpoints.find_hub(which) if which != "openwebui" else None      # Open WebUI's are in build()
        if not ep:
            continue
        mod = endpoints.adapter(which)
        try:
            hubs.append((which, label, ep, mod.SUPPORTS, mod.upstreams()))
        except Exception as exc:                        # noqa: BLE001
            errors.append(f"could not ask {label} ({ep}) which connections it has: {exc}")
    bench = _on_bench() if hubs else []
    out = []
    for r in rows:
        api, mine = (r.get("api") or "").rstrip("/"), []
        for which, label, ep, can, ups in hubs:
            for conn, (base, prefix) in ups.items():
                if conn not in (r["provider"], r.get("connection")) and not (base and base == api):
                    continue
                served = f"{prefix}/{r['model']}" if prefix else r["model"]
                # Only what the hub takes (its adapter's SUPPORTS): no logo or tags where it has none.
                said = [f"{label}: into the {conn} connection's {can['allow_list']}"]
                said += [f"{k}: {can[k]}" for k in ("tags", "logo", "access") if can[k]]
                mine.append({"via": "hub", "hub": which, "connection": conn, "endpoint": ep,
                             "label": f"{r['provider_name']} via {label} ({conn} connection)",
                             "note": f"Also serves it to everything else that uses {label}.",
                             "steps": ["; ".join(said), "The bench: register it and onboard it as chosen below"],
                             "on_bench_as": next((a for e, _c, _n, a, s in bench if e == ep and s == served), None)})
        out.append(mine)
    return out, errors


def build() -> list[dict]:
    import endpoints
    md, fl = sources.cached("modelsdev"), sources.cached("freellmapi")
    md_conn = {v: k for k, v in sources.connection_to_modelsdev().items()}
    bench = _on_bench()
    # Open WebUI's connections, as far as the bench can tell without asking it.
    owui = endpoints.find_hub("openwebui")
    owui_conns = {c for ep, c, *_ in bench if ep == owui} - {"?"}
    direct = _direct(md)
    rows: dict[tuple[str, str], dict] = {}

    for prov, p in md.items():
        openai_like = bool(p.get("api")) or p.get("npm") == "@ai-sdk/openai-compatible"
        for mid, m in (p.get("models") or {}).items():
            out_mods = (m.get("modalities") or {}).get("output") or ["text"]
            if "text" not in out_mods or m.get("status") == "deprecated":
                continue
            cost = m.get("cost")
            mods = m.get("modalities") if isinstance(m.get("modalities"), dict) else {}
            rows[(prov, sources.norm_model(mid))] = {
                "in_mods": _mods(mods.get("input")), "out_mods": _mods(mods.get("output")),
                "provider": prov, "provider_name": p.get("name", prov), "model": mid,
                "name": m.get("name") or mid, "sources": ["models.dev"],
                "free": mid.endswith(":free") or sources.is_free(cost) is True,
                "cost": cost, "context": (m.get("limit") or {}).get("context"),
                "output": (m.get("limit") or {}).get("output"),
                "tools": m.get("tool_call"), "reasoning": m.get("reasoning"),
                "vision": "image" in ((m.get("modalities") or {}).get("input") or []),
                "released": m.get("release_date"), "knowledge": m.get("knowledge"),
                "open_weights": m.get("open_weights"), "api": p.get("api"),
                "openai_like": openai_like, "connection": md_conn.get(prov), "limits": None}

    for m in fl.get("models") or []:
        plat = m.get("platform", "")
        prov = sources.FREELLMAPI_TO_MODELSDEV.get(plat, plat)
        key = (prov, sources.norm_model(m.get("modelId", "")))
        lim = {k: v for k, v in (m.get("limits") or {}).items() if v} or None
        row = rows.get(key)
        if row:
            row["sources"].append("freellmapi")
            row["free"] = True                      # listed in a free-tier catalog
            row["limits"] = lim
            row["context"] = row["context"] or m.get("contextWindow")
            row["tools"] = row["tools"] if row["tools"] is not None else m.get("supportsTools")
        else:
            rows[key] = {
                "in_mods": ["text", "image"] if m.get("supportsVision") else ["text"], "out_mods": ["text"],
                "provider": prov, "provider_name": prov, "model": m.get("modelId"),
                "name": m.get("displayName") or m.get("modelId"), "sources": ["freellmapi"],
                "free": True, "cost": None, "context": m.get("contextWindow"), "output": None,
                "tools": m.get("supportsTools"), "reasoning": None,
                "vision": m.get("supportsVision"), "released": None, "knowledge": None,
                "open_weights": None, "api": None,
                "openai_like": False, "connection": FREELLMAPI_TO_CONNECTION.get(plat),
                "limits": lim}

    # The same model through ANOTHER provider is fine to add, but worth knowing.
    by_model: dict[str, list[str]] = {}
    on_conn: dict[tuple[str, str], str] = {}
    for _ep, conn, model, alias, _s in bench:
        by_model.setdefault(model, []).append(f"{alias} via {conn}")
        on_conn.setdefault((conn, model), alias)
    seen = _read_seen()
    today = dt.date.today().isoformat()
    baseline = not seen
    walled = {p for p, on in sources.free_in_own_client_only().items() if on}
    out = []
    for key, r in rows.items():
        k = "|".join(key)
        if k not in seen:
            seen[k] = "baseline" if baseline else today
        r["first_seen"] = seen[k]
        r["new"] = seen[k] not in ("baseline",) and seen[k] >= (dt.date.today() - dt.timedelta(days=NEW_DAYS)).isoformat()
        if r["free"] and r["provider"] in walled:
            continue                # free only in the provider's own client (sources.FREE_IN_OWN_CLIENT_ONLY)
        conn = r["connection"]
        r["norm"] = key[1]
        r["routes"] = _routes(r, bench, owui_conns, direct, owui)
        # Where there are routes they decide, by exact id: the bench's `v/m:free`
        # leaves the paid `v/m` addable, and shows under also_on instead.
        mine = (next((x["on_bench_as"] for x in r["routes"] if x["on_bench_as"]), None) if r["routes"]
                else on_conn.get((conn, key[1])) if conn else None)
        r["on_bench_as"] = mine
        r["also_on"] = [x for x in by_model.get(key[1], []) if not mine or not x.startswith(mine + " ")]
        r["reach"] = ("on bench" if mine else
                      "connected" if r["routes"] else
                      "easy to add" if r["openai_like"] else "other")
        out.append(r)
    _write_seen(seen)
    order = {"connected": 0, "easy to add": 1, "on bench": 2, "other": 3}
    out.sort(key=lambda r: (not r["free"], not r["new"], order[r["reach"]], r["provider"], r["model"]))
    return out


def _mods(value) -> list[str]:
    """A modality list as models.dev should give it; a few entries do not."""
    return [str(v) for v in value if isinstance(v, str)] or ["text"] if isinstance(value, list) else ["text"]


def _read_seen() -> dict:
    try:
        return json.loads(SEEN.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_seen(seen: dict) -> None:
    SEEN.parent.mkdir(parents=True, exist_ok=True)
    tmp = SEEN.with_suffix(".part")
    tmp.write_text(json.dumps(seen, ensure_ascii=False), encoding="utf-8")
    tmp.replace(SEEN)


def _selftest() -> None:
    bench = [("openwebui", "openrouter", "m", "m-owui", "or.v/m"),
             ("openrouter", "openrouter", "x", "x-direct", "v/x:free")]
    row = {"connection": "openrouter", "provider": "openrouter", "provider_name": "OpenRouter", "model": "v/m"}
    both = _routes(row, bench, {"openrouter"}, {"openrouter": ["openrouter"]}, "openwebui")
    assert [r["via"] for r in both] == ["openwebui", "direct"], both
    # On the bench through Open WebUI (under its prefix) is not on the bench through the endpoint.
    assert both[0]["on_bench_as"] == "m-owui" and both[1]["on_bench_as"] is None, both
    assert all("Shares one OpenRouter quota" in r["note"] for r in both)
    x = _routes({**row, "model": "v/x:free"}, bench, {"openrouter"}, {"openrouter": ["openrouter"]}, "openwebui")
    assert x[0]["on_bench_as"] is None and x[1]["on_bench_as"] == "x-direct", x
    paid = _routes({**row, "model": "v/x"}, bench, {"openrouter"}, {"openrouter": ["openrouter"]}, "openwebui")
    assert paid[1]["on_bench_as"] is None, paid                     # the :free slug is not the paid one
    nv = _routes({"connection": "nvidia-api", "provider": "nvidia", "provider_name": "NVIDIA", "model": "v/m"},
                 bench, {"nvidia-api"}, {}, "openwebui")
    assert len(nv) == 1 and nv[0]["connection"] == "nvidia-api" and "Shares" not in nv[0]["note"], nv
    assert _routes({"connection": "groq", "provider": "groq", "provider_name": "Groq", "model": "v/m"}, bench,
                   {"openrouter"}, {}, "openwebui") == []            # no connection, no endpoint: no route
    # No Open WebUI hub: no connections behind one, so only the endpoint's own route.
    assert [r["via"] for r in _routes(row, bench, set(), {"openrouter": ["openrouter"]}, None)] == ["direct"]
    # Free-only-in-own-client: built in, and an install's switch wins either way.
    assert sources.free_in_own_client_only({})["opencode"] is True
    over = sources.free_in_own_client_only({"opencode": False, "x": True})
    assert over["opencode"] is False and over["x"] is True and over["opencode-go"] is True, over
    assert sources.free_in_own_client_only(True) == sources.free_in_own_client_only({})   # not a map: ignored
    print("discover selftest ok")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if "--selftest" in sys.argv:
        _selftest()
        return
    rows = build()
    free = [r for r in rows if r["free"]]
    by = {}
    for r in free:
        by[r["reach"]] = by.get(r["reach"], 0) + 1
    print(f"{len(rows)} models ({len(free)} free) from models.dev + FreeLLMAPI;"
          f" free by reach: {by}; new this week: {sum(r['new'] for r in rows)}")
    both = sum(1 for r in rows if len(r["sources"]) > 1)
    print(f"{both} listed by both sources (shown once)")
    for r in [r for r in free if r["reach"] == "connected"][:12]:
        print(f"  {r['provider']:<14} {r['model'][:48]:<48} ctx {r['context'] or '?'}")


if __name__ == "__main__":
    main()
