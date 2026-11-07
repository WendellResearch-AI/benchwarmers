"""One list of models worth adding, from FreeLLMAPI, models.dev and the providers' own lists, deduped.

    python metrics/discover.py           # summary: free models by reach, new this week
    python metrics/discover.py --refresh # fetch every list now, as Discover's Refresh does

The shopping list: Discover's Add (serve.py) puts a model on the bench by one
of the routes below. A model offered by the same provider in both sources
appears once, carrying both sources. FreeLLMAPI leads where it lists the
model (its name, its provider's name, context, tools, limits, its smartest
and fastest ranks, size, monthly budget, quirks and freellmapi.co page);
models.dev fills the rest, and alone has the paid models and prices, API base
URLs (reach, direct routes), modalities, release and knowledge dates.
Image and audio generation in FreeLLMAPI's list are left out: the bench asks
for text.

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
               Open WebUI hub (endpoints.find_hub) is served from it, or from
               the providers' lists (below). A row is that connection's when
               the provider's API host is the connection's, or its id
               (models.dev's or FreeLLMAPI's) is the connection's name or a
               part of its host: FreeLLMAPI's xkiro is api.xkiro.com. Adding
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

THE PROVIDERS' OWN LISTS
------------------------
The catalogs trail the providers by days, so a model a provider started
serving this morning is in neither. Open WebUI passes each connection's own
/models through (owui.lists), whatever its Model IDs list allows, so
refresh() keeps every connection's in runs/cache/provider-lists.json. A model
there that neither catalog has for that provider is a row of its own, source
"provider": its id, and what the list says of its name, context, price and
abilities (each provider says different things; many say nothing of price,
which is then unknown, not paid). Only through Open WebUI: the providers'
keys stay where they are, and nothing is sent anywhere new.

LINKS
-----
A row's `page` is its freellmapi.co page (where the sitemap has one), and
`md_page` its models.dev page: /models/<lab>/<id> by models.dev's canonical
id, else the provider's own page there.

NEW is the date a model was first seen in any list, kept in
runs/discover-seen.json (gitignored). The first build records everything as a
baseline, so only models that appear afterwards are badged new; so does the
first list of each connection.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys
from urllib.parse import urlsplit

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
LISTS = "provider-lists"                 # runs/cache/provider-lists.json, written by refresh()


def refresh(force: bool = False) -> list[str]:
    """Fetch the catalogs (sources.FEEDS) and the providers' own lists, each at
    most once a day unless `force`. What went wrong, as sentences: a list that
    could not be fetched keeps its cached copy."""
    import endpoints
    said = []
    for name in sources.FEEDS:
        try:
            sources.fetch(name, force)
        except Exception as exc:                       # noqa: BLE001
            said.append(f"could not fetch {name} ({exc}); kept the cached copy")
    age = sources.age_s(LISTS)
    if not endpoints.find_hub("openwebui") or (not force and age is not None and age < sources.MAX_AGE_S):
        return said
    import owui
    try:
        got, failed = owui.lists()
    except Exception as exc:                           # noqa: BLE001
        return said + [f"could not ask Open WebUI for its providers' lists ({exc}); kept the cached copy"]
    old = {c["names"][0]: c for c in sources.cached(LISTS).get("connections") or []}
    conns = [{**c, "models": [s for s in map(_slim, c["models"]) if s]} for c in got]
    sources.store(LISTS, {"at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                          "connections": conns + [old[n] for n in failed if n in old]})
    return said + [f"{n} would not list its models ({why})" + ("; kept its last list" if n in old else "")
                   for n, why in failed.items()]


def _slim(m: dict) -> dict | None:
    """What Discover keeps of one model in a provider's own list; None when the
    list says it is not a chat model (images, audio, embeddings, another API).
    Each provider says it its own way: these are the ways seen 2026-11-07 in
    OpenRouter's, Requesty's, xKiro's, Mistral's and UnoRouter's lists."""
    arch = m.get("architecture") if isinstance(m.get("architecture"), dict) else {}
    caps = m.get("capabilities") if isinstance(m.get("capabilities"), dict) else {}
    outs = m.get("output_modalities") or arch.get("output_modalities")
    ins = m.get("input_modalities") or arch.get("input_modalities")
    apis = m.get("supported_endpoint_types")
    if ((isinstance(outs, list) and outs and "text" not in outs) or m.get("modality") not in (None, "chat", "text")
            or m.get("api") not in (None, "chat") or caps.get("completion_chat") is False
            or (isinstance(apis, list) and apis and "openai" not in apis)):
        return None
    tier, price = m.get("access_tier"), m.get("pricing") if isinstance(m.get("pricing"), dict) else {}
    nums = []
    for v in (price.get("prompt"), price.get("completion"), price.get("input"), price.get("output"),
              m.get("input_price"), m.get("output_price")):
        try:
            nums.append(float(v))
        except (TypeError, ValueError):
            pass
    vision = caps.get("vision", m.get("supports_vision"))
    if vision is None and isinstance(ins, list):
        vision = "image" in ins
    ctx = m.get("context_length") or m.get("context_window") or m.get("max_context_length")
    return {"id": str(m["id"]), "name": m.get("display_name") or m.get("name"),
            "context": ctx if isinstance(ctx, int) and ctx > 0 else None,
            "free": (True if str(m["id"]).endswith(":free") or tier == "free" else
                     False if tier in ("paid", "premium") else all(x == 0 for x in nums) if nums else None),
            "tools": caps.get("tools", caps.get("function_calling", m.get("supports_tool_calling"))),
            "vision": vision, "reasoning": caps.get("reasoning", m.get("supports_reasoning"))}


def _host(base: str) -> str:
    return urlsplit(base).hostname or "" if "//" in base else base


def _conn_for(ids: list[str], api: str | None, hosts: dict[str, str]) -> str | None:
    """The Open WebUI connection (`hosts`: name -> its API host, the bench's
    names first) that serves this provider: the provider's API host is its
    host, or one of the provider's `ids` is its name or a part of its host."""
    want = _host(api or "")
    for name, host in hosts.items():
        parts = host.split(".")[:-1] if "." in host else [host]
        if (want and host == want) or any(i and (i == name or i in parts) for i in ids):
            return name
    return None


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
    pages = set(sources.cached("freellmapi-pages").get("slugs") or [])
    platform_names = {p.get("id"): p.get("name") for p in fl.get("platforms") or []}
    md_conn = {v: k for k, v in sources.connection_to_modelsdev().items()}
    bench = _on_bench()
    # Open WebUI's connections, as far as the bench can tell without asking it:
    # its models' connections, then the providers' lists, with their API hosts.
    owui = endpoints.find_hub("openwebui")
    lists = (sources.cached(LISTS).get("connections") or []) if owui else []
    hosts: dict[str, str] = dict.fromkeys(c for ep, c, *_ in bench if ep == owui and c != "?")
    for c in lists:
        for n in c["names"]:
            hosts[n] = _host(c["base"])
    hosts = {n: h or n for n, h in hosts.items()}
    owui_conns = set(hosts)

    def conn_of(ids: list[str], api: str | None, known: str | None) -> str | None:
        return known if known in hosts else _conn_for(ids, api, hosts) or known

    direct = _direct(md)
    rows: dict[tuple[str, str], dict] = {}
    # models.dev's own page for a model: by its canonical id, which names its lab.
    labs = {str(m["canonical_model_id"]).split("/")[0] for p in md.values()
            for m in (p.get("models") or {}).values() if m.get("canonical_model_id")}

    for prov, p in md.items():
        openai_like = bool(p.get("api")) or p.get("npm") == "@ai-sdk/openai-compatible"
        conn = conn_of([prov], p.get("api"), md_conn.get(prov))
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
                "openai_like": openai_like, "connection": conn, "limits": None,
                "rank": None, "speed": None, "size": None, "budget": None, "quirks": [], "page": None,
                "md_page": "https://models.dev/" + (f"models/{m['canonical_model_id']}" if m.get("canonical_model_id")
                                                    else f"models/{prov}/{mid}" if prov in labs else f"providers/{prov}")}

    for m in fl.get("models") or []:
        if (m.get("modality") or "chat") != "chat":
            continue                                # image or audio generation
        plat = m.get("platform", "")
        prov = sources.FREELLMAPI_TO_MODELSDEV.get(plat, plat)
        key = (prov, sources.norm_model(m.get("modelId", "")))
        lead = {"sources": ["freellmapi"], "free": True,          # listed in a free-tier catalog
                "name": sources.freellmapi_name(m.get("displayName") or "") or m.get("modelId"),
                "provider_name": platform_names.get(plat) or prov,
                "limits": {k: v for k, v in (m.get("limits") or {}).items() if v} or None,
                "rank": m.get("intelligenceRank"), "speed": m.get("speedRank"),
                "size": m.get("sizeLabel") or None, "budget": m.get("monthlyTokenBudget") or None,
                "quirks": [{k: q.get(k) for k in ("title", "body", "severity")} for q in m.get("quirks") or []],
                "page": sources.freellmapi_page(m.get("displayName") or "", pages)}
        row = rows.get(key)
        if row:
            row.update(lead, sources=list(dict.fromkeys(["freellmapi", *row["sources"]])),
                       context=m.get("contextWindow") or row["context"],          # the free tier's, where it says
                       tools=row["tools"] if m.get("supportsTools") is None else m.get("supportsTools"))
        else:
            rows[key] = {
                **lead, "in_mods": ["text", "image"] if m.get("supportsVision") else ["text"], "out_mods": ["text"],
                "provider": prov, "model": m.get("modelId"),
                "cost": None, "context": m.get("contextWindow"), "output": None,
                "tools": m.get("supportsTools"), "reasoning": None,
                "vision": m.get("supportsVision"), "released": None, "knowledge": None,
                "open_weights": None, "api": None, "md_page": None,
                "openai_like": False, "connection": conn_of([prov, plat], None, FREELLMAPI_TO_CONNECTION.get(plat))}

    # What each connection's provider lists that neither catalog has for it.
    listed = set()
    for c in lists:
        conn = c["names"][0]                     # the bench's name for it, as conn_of picks
        provs = sorted({r["provider"] for r in rows.values() if r["connection"] in c["names"]}) or [conn]
        have = {k[1] for k in rows if k[0] in provs}
        name = next((r["provider_name"] for r in rows.values() if r["provider"] == provs[0]), c["names"][-1])
        listed.add(conn)
        for m in c["models"]:
            key = (provs[0], sources.norm_model(m["id"]))
            if key[1] in have:
                continue
            have.add(key[1])
            rows[key] = {
                "in_mods": ["text", "image"] if m.get("vision") else ["text"], "out_mods": ["text"],
                "provider": provs[0], "provider_name": name, "model": m["id"], "name": m.get("name") or m["id"],
                "sources": ["provider"], "free": m.get("free"), "cost": None, "context": m.get("context"),
                "output": None, "tools": m.get("tools"), "reasoning": m.get("reasoning"), "vision": m.get("vision"),
                "released": None, "knowledge": None, "open_weights": None, "api": c["base"], "openai_like": True,
                "connection": conn, "limits": None, "rank": None, "speed": None, "size": None, "budget": None,
                "quirks": [], "page": None, "md_page": None}

    # The same model through ANOTHER provider is fine to add, but worth knowing.
    by_model: dict[str, list[str]] = {}
    on_conn: dict[tuple[str, str], str] = {}
    for _ep, conn, model, alias, _s in bench:
        by_model.setdefault(model, []).append(f"{alias} via {conn}")
        on_conn.setdefault((conn, model), alias)
    seen = _read_seen()
    today = dt.date.today().isoformat()
    baseline = not seen
    first_list = {c for c in listed if f"list|{c}" not in seen}   # a connection's first list is its baseline
    walled = {p for p, on in sources.free_in_own_client_only().items() if on}
    out = []
    for key, r in rows.items():
        k = "|".join(key)
        if k not in seen:
            seen[k] = ("baseline" if baseline or (r["sources"] == ["provider"] and r["connection"] in first_list)
                       else today)
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
    seen.update({f"list|{c}": today for c in first_list})
    _write_seen(seen)
    order = {"connected": 0, "easy to add": 1, "on bench": 2, "other": 3}
    # Within each reach, FreeLLMAPI's smartest first; the models it does not rank
    # after, new ones first. Not new first overall: models.dev adds hundreds a
    # week, which buried the ranked ones (the badge still marks them).
    out.sort(key=lambda r: (not r["free"], order[r["reach"]], r["rank"] is None, r["rank"] or 0, not r["new"],
                            r["provider"], r["model"]))
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
    # freellmapi.co names a page by the display name without its note; only its sitemap's pages exist.
    assert sources.freellmapi_page("Llama 4 Scout (CF)", {"llama-4-scout"}) == "https://freellmapi.co/model/llama-4-scout"
    assert sources.freellmapi_page("GLM-4.7 FlashX", {"llama-4-scout"}) is None
    # FreeLLMAPI leads where it lists a model, models.dev fills the rest; image models out; smartest first.
    md = {"acme": {"name": "Acme", "api": "https://acme.example/v1", "models": {
        "acme/big": {"name": "Big", "cost": {"input": 1, "output": 2}, "limit": {"context": 8000},
                     "modalities": {"input": ["text", "image"], "output": ["text"]}, "release_date": "2026-01-01"},
        "acme/small": {"name": "Small", "cost": {"input": 0, "output": 0}},
        "acme/tiny": {"name": "Tiny", "cost": {"input": 0, "output": 0}}}}}
    fl = {"platforms": [{"id": "acme", "name": "Acme Cloud"}], "models": [
        {"platform": "acme", "modelId": "acme/big", "displayName": "Big One (Acme)", "intelligenceRank": 3,
         "speedRank": 9, "sizeLabel": "Large", "monthlyTokenBudget": "~3M", "contextWindow": 4000,
         "limits": {"rpm": 10, "rpd": None}, "quirks": [{"slug": "q", "title": "T", "body": "B", "severity": "info"}]},
        {"platform": "acme", "modelId": "acme/painter", "displayName": "Painter", "modality": "image"},
        {"platform": "acme", "modelId": "acme/tiny", "displayName": "Tiny", "intelligenceRank": 1}]}
    feeds = {"modelsdev": md, "freellmapi": fl, "freellmapi-pages": {"slugs": ["big-one"]}}
    import tempfile
    kept = sources.cached, globals()["_on_bench"], globals()["SEEN"]
    try:
        sources.cached = lambda name: feeds.get(name, {})
        globals().update(_on_bench=lambda: [], SEEN=pathlib.Path(tempfile.mkdtemp()) / "seen.json")
        rows = [r for r in build() if r["provider"] == "acme"]
    finally:
        sources.cached = kept[0]
        globals().update(_on_bench=kept[1], SEEN=kept[2])
    big = next(r for r in rows if r["model"] == "acme/big")
    assert (big["sources"], big["name"], big["provider_name"]) == (["freellmapi", "models.dev"], "Big One", "Acme Cloud"), big
    assert big["free"] and big["context"] == 4000 and big["limits"] == {"rpm": 10} and big["released"] == "2026-01-01", big
    assert (big["rank"], big["speed"], big["size"], big["budget"]) == (3, 9, "Large", "~3M"), big
    assert big["page"] == "https://freellmapi.co/model/big-one" and big["quirks"] == [
        {"title": "T", "body": "B", "severity": "info"}] and big["vision"], big
    assert [r["model"] for r in rows] == ["acme/tiny", "acme/big", "acme/small"], [r["model"] for r in rows]
    # A connection is found by its API host or by the provider's id in its host; the bench's name wins.
    hosts = {"xk": "api.xkiro.com", "api.xkiro.com": "api.xkiro.com", "router.requesty.ai": "router.requesty.ai",
             "cli-proxy-api": "203.0.113.7"}
    assert _conn_for(["xkiro"], None, hosts) == "xk"
    assert _conn_for(["requesty"], "https://router.requesty.ai/v1", hosts) == "router.requesty.ai"
    assert _conn_for(["com"], None, hosts) is None and _conn_for(["acme"], "https://acme.example/v1", hosts) is None
    # A provider's own list: chat models only, price where it says one, unknown where it does not.
    assert _slim({"id": "a", "architecture": {"output_modalities": ["image"]}}) is None
    assert _slim({"id": "b", "supported_endpoint_types": ["aihorde"]}) is None
    assert _slim({"id": "c", "capabilities": {"completion_chat": False}}) is None
    assert _slim({"id": "d:free"})["free"] is True and _slim({"id": "e"})["free"] is None
    assert _slim({"id": "f", "pricing": {"prompt": "0", "completion": "0"}})["free"] is True
    assert _slim({"id": "g", "input_price": 2.5e-07, "output_price": 1e-06})["free"] is False
    assert _slim({"id": "h", "access_tier": "premium", "capabilities": {"vision": True}, "context_length": 9})[
        "free"] is False and _slim({"id": "h", "capabilities": {"vision": True}})["vision"] is True
    # With a list cached: acme's connection is found by its host, the list's new model is a row of its
    # own (the first list of a connection is a baseline, so not new), and models.dev links each model.
    feeds["provider-lists"] = {"connections": [{"names": ["acme-conn"], "base": "https://acme.example/v1",
                                                "models": [_slim({"id": "acme/big"}), _slim({"id": "acme/fresh"})]}]}
    import endpoints
    try:
        sources.cached = lambda name: feeds.get(name, {})
        globals().update(_on_bench=lambda: [], SEEN=pathlib.Path(tempfile.mkdtemp()) / "seen.json")
        endpoints_hub, endpoints.find_hub = endpoints.find_hub, lambda which: "owui-ep" if which == "openwebui" else None
        rows = {r["model"]: r for r in build() if r["provider"] == "acme"}
    finally:
        sources.cached, endpoints.find_hub = kept[0], endpoints_hub
        globals().update(_on_bench=kept[1], SEEN=kept[2])
    assert set(rows) == {"acme/big", "acme/small", "acme/tiny", "acme/fresh"}, sorted(rows)
    assert rows["acme/big"]["connection"] == "acme-conn" and rows["acme/big"]["reach"] == "connected", rows["acme/big"]
    assert rows["acme/fresh"]["sources"] == ["provider"] and not rows["acme/fresh"]["new"], rows["acme/fresh"]
    assert rows["acme/big"]["md_page"] == "https://models.dev/providers/acme", rows["acme/big"]["md_page"]
    print("discover selftest ok")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if "--selftest" in sys.argv:
        _selftest()
        return
    if "--refresh" in sys.argv:
        for line in refresh(force=True) or ["every list fetched"]:
            print(" ", line)
    rows = build()
    free = [r for r in rows if r["free"]]
    by = {}
    for r in free:
        by[r["reach"]] = by.get(r["reach"], 0) + 1
    print(f"{len(rows)} models ({len(free)} free) from FreeLLMAPI + models.dev;"
          f" free by reach: {by}; new this week: {sum(r['new'] for r in rows)}")
    both = sum(1 for r in rows if len(r["sources"]) > 1)
    print(f"{both} listed by both sources (shown once)")
    for r in [r for r in free if r["reach"] == "connected"][:12]:
        print(f"  {r['provider']:<14} {r['model'][:48]:<48} ctx {r['context'] or '?'}")


if __name__ == "__main__":
    main()
