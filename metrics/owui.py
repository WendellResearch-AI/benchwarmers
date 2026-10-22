"""The Open WebUI hub adapter: add a model as it is otherwise done by hand, then hand it to onboarding.

    python metrics/owui.py <connection> <model id>     # what would change, changes nothing

One of three hub adapters (litellm_hub.py, cliproxy.py), each exposing
SUPPORTS, CHECKED, endpoint(), plan() and add(); serve.discover_add picks one
by the endpoint's hub (endpoints.adapter).

THE HAND STEPS (2026-10-18)
---------------------------
1. Edit the connection: add the model to its Model IDs list (advanced
   settings). Only when the list is in use; an empty list means Open WebUI
   already shows everything the provider serves.
2. Find the model in Admin > Models: make it public, tag it with the
   connection's name (`nvidia-api`, `groq`, ...), give it the provider logo.

The logo, the capability switches and the default features are copied from
a model already on the same connection, because every model on a connection
carries the same ones. A connection is found by its tag: the Open WebUI
connection (`urlIdx`) its tagged models are served from. Then
`scripts/onboard.py` registers, probes and runs the suite as for any model.

Admin calls with the endpoint's admin key (endpoints.admin_key: an admin's
API key, which is the endpoint's own key unless one is stored apart); keys
are never printed, and the config is written back exactly as read, plus one
id. The endpoint is whichever is a hub of type openwebui (`endpoints.find_hub`);
an install without one has no Open WebUI to add to, and `endpoint()` says so.
"""

from __future__ import annotations

import collections
import json
import pathlib
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "metrics"))
sys.path.insert(0, str(ROOT / "suite"))
import endpoints  # noqa: E402

PUBLIC = [{"principal_type": "user", "principal_id": "*", "permission": "read"}]
COPIED = ("profile_image_url", "capabilities", "defaultFeatureIds")
# What this hub takes (RELEASE-PLAN section 2, "Adding a model to a hub"); the
# dashboard says only what is here. "" where the hub has no such thing.
SUPPORTS = {"allow_list": "Model IDs list", "tags": "the connection's tag",
            "logo": "copied from the connection's other models (PNG, JPEG, GIF or WebP)",
            "access": "public: every signed-in user",
            "link": "/?settings=admin:models (v0.11+, the Models tab; up to v0.10 /admin/settings/models?id=<id>)"}
CHECKED = ("0.12",)                              # 0.12.3, 2026-10-21: the routes below and /api/version


def endpoint() -> str:
    """The Open WebUI hub's endpoint name. ValueError when there is none: the
    dashboard shows it as the reason, and nothing has been sent anywhere."""
    name = endpoints.find_hub("openwebui")
    if not name:
        raise ValueError("no endpoint is an Open WebUI hub; add it on the Endpoints page as kind hub, Open WebUI")
    return name


def _call(path: str, body: dict | None = None):
    name = endpoint()
    root = endpoints.base(name).removesuffix("/api")
    req = urllib.request.Request(root + path, data=None if body is None else json.dumps(body).encode(),
                                 method="GET" if body is None else "POST",
                                 headers={"Authorization": f"Bearer {endpoints.admin_key(name)}",
                                          "Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"null")


def version() -> tuple[str, str]:
    """(release, endpoints.version_note's sentence). Advice only: a hub that
    will not say is still added to."""
    try:
        v = str((_call("/api/version") or {}).get("version") or "")
    except Exception:                                  # noqa: BLE001
        v = ""
    return v, endpoints.version_note("openwebui", v, CHECKED)


def connections() -> dict[str, tuple[int, str]]:
    """Connection name -> (Open WebUI connection index, the tag its models carry).

    The bench names a connection from config/provider-tags.json (`groq`,
    `mistral`), Open WebUI's tags differ (`groq-api`, `mistral-studio`), so
    the two meet at the index the bench's own registered models are served
    from. Open WebUI's tag names are accepted as well."""
    import route
    from run import _registered_names
    data = _call("/api/models").get("data", [])
    by_id = {m["id"]: m for m in data}
    tag_at: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
    for m in data:
        for t in m.get("tags") or []:
            if m.get("urlIdx") is not None:
                tag_at[m["urlIdx"]][t["name"]] += 1
    at: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for alias, served in _registered_names().items():
        m = by_id.get(served)
        if m and m.get("urlIdx") is not None:
            at[route.connection_of(alias)][m["urlIdx"]] += 1
    out = {}
    for name, c in at.items():
        idx = c.most_common(1)[0][0]
        tags = tag_at.get(idx)                   # .get: indexing a defaultdict would add an empty one
        out[name] = (idx, tags.most_common(1)[0][0] if tags else name)
    for idx, c in tag_at.items():
        tag = c.most_common(1)[0][0]
        out.setdefault(tag, (idx, tag))
    return out


def plan(connection: str, model_id: str) -> dict:
    """What adding would change. Raises ValueError when it cannot be done here."""
    release, note = version()
    found = connections().get(connection)
    if found is None:
        raise ValueError(f"no Open WebUI connection is known as {connection!r}: none of the bench's"
                         " models is served from it and no Open WebUI model carries that tag")
    idx, tag = found
    cfg = _call("/openai/config")
    if idx >= len(cfg.get("OPENAI_API_BASE_URLS") or []):
        raise ValueError(f"Open WebUI's connections changed while this ran; try again")
    conn = (cfg.get("OPENAI_API_CONFIGS") or {}).get(str(idx), {})
    if conn.get("enable") is False:
        raise ValueError(f"the {connection} connection is switched off in Open WebUI")
    prefix = conn.get("prefix_id") or ""
    served = f"{prefix}.{model_id}" if prefix else model_id
    # One id, two connections: Open WebUI would serve only one of them.
    elsewhere = next((m.get("urlIdx") for m in _call("/api/models").get("data", [])
                      if m.get("id") == served and m.get("urlIdx") not in (None, idx)), None)
    if elsewhere is not None:
        raise ValueError(f"Open WebUI already serves {served} from connection {elsewhere}; give the"
                         f" {connection} connection a prefix in Open WebUI so the ids differ")
    listed = conn.get("model_ids") or []
    records = {m["id"]: m for m in _call("/api/v1/models/base")}
    sibling = next((m for m in records.values()
                    if any(t.get("name") == tag for t in (m.get("meta") or {}).get("tags") or [])
                    and (m.get("meta") or {}).get("profile_image_url")), None)
    return {"connection": connection, "tag": tag, "index": idx, "base_url": cfg["OPENAI_API_BASE_URLS"][idx],
            "model_id": model_id, "served": served,
            "add_to_list": bool(listed) and model_id not in listed,
            "record": "update" if served in records else "create",
            "copy_from": sibling["id"] if sibling else None, "version": release, "version_note": note}


def add(connection: str, model_id: str) -> tuple[str, list[str]]:
    """Do it. (served id, what was done)."""
    p = plan(connection, model_id)
    done = [p["version_note"]] if p["version_note"] else []
    if p["add_to_list"]:
        cfg = _call("/openai/config")
        # The write sends every key back as read. 0.12.3 returns them whole; a
        # version that masks them would have this overwrite real keys.
        if any(isinstance(k, str) and ch in k for k in cfg.get("OPENAI_API_KEYS", []) for ch in "*•"):
            raise ValueError("Open WebUI returned masked API keys; writing the config back would"
                             " replace them, so add the model ID to the connection by hand")
        # Decided again on the fresh read: the list may have changed since plan().
        # ponytail: read-modify-write; an edit made in Open WebUI in the same second is lost.
        ids = cfg["OPENAI_API_CONFIGS"][str(p["index"])].setdefault("model_ids", [])
        if ids and model_id not in ids:
            ids.append(model_id)
            _call("/openai/config/update", cfg)
            done.append(f"added {model_id} to the {connection} connection's Model IDs")
    records = {m["id"]: m for m in _call("/api/v1/models/base")}
    old = records.get(p["served"]) or {}
    meta = dict(old.get("meta") or {})
    sib = (records.get(p["copy_from"]) or {}).get("meta") or {}
    for k in COPIED:
        if sib.get(k) and not meta.get(k):
            meta[k] = sib[k]
    tags = [t for t in meta.get("tags") or [] if t.get("name") != p["tag"]]
    meta["tags"] = tags + [{"name": p["tag"]}]
    form = {"id": p["served"], "name": old.get("name") or p["served"], "base_model_id": old.get("base_model_id"),
            "meta": meta, "params": old.get("params") or {}, "access_grants": PUBLIC, "is_active": True}
    try:
        _call("/api/v1/models/model/update" if old else "/api/v1/models/create", form)
    except Exception as exc:                          # noqa: BLE001
        raise ValueError("; ".join(done + [f"then the model record failed ({exc}); finish it in"
                                           " Admin > Models"])) from exc
    done.append(f"{'updated' if old else 'created'} its model record: public, tagged {p['tag']}"
                + (f", logo and switches from {p['copy_from']}" if p["copy_from"] else ", no logo found to copy"))
    # Open WebUI rebuilds its model list on request; wait until it serves the new id.
    for _ in range(10):
        if p["served"] in endpoints.models(endpoint()):
            break
        time.sleep(2)
    else:
        done.append(f"Open WebUI does not list {p['served']} yet; onboarding will say if it never does")
    return p["served"], done


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    print(json.dumps(plan(sys.argv[1], sys.argv[2]), indent=2))


if __name__ == "__main__":
    main()
