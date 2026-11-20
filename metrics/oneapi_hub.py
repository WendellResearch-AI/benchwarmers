"""The one-api and new-api hub adapter: which channel serves each model, and what a key has left.

    python metrics/oneapi_hub.py <endpoint>     # what each job finds; changes nothing

one-api (github.com/songquanpeng/one-api, MIT) and new-api
(github.com/QuantumNous/new-api, AGPL-3.0, begun as its fork) are key
gateways: CHANNELS (an upstream each: a type, a base URL, its keys, a comma
list of `models`, a `model_mapping` of served name to upstream id) behind
TOKENS, the API keys clients use. A name several enabled channels list is
spread across them by priority and weight. The routes both keep, which their
forks mostly keep too:

    GET /api/status                          keyless; data.version
    GET /api/channel/?p=N                    admin; the channels, keys left out
    GET /v1/models                           a token; the names it may call
    GET /v1/dashboard/billing/subscription   a token; hard_limit_usd, the quota whole
    GET /v1/dashboard/billing/usage          a token; total_usage, 100 times what is spent

and where they differ (DIFF): one-api lists channels as a bare list in pages
from 0 of its ItemsPerPage (10), new-api as {items, total} in pages from 1 of
up to 100. The admin token: one-api's system access token (Personal
settings); new-api's access token (`nap_...`, Security settings) with the
channel read scope. new-api releases before its access tokens also wanted a
New-Api-User header, which is not sent. one-api answers a refused token with
200 and success false, not 401, so `success` is read on every reply.

Its jobs (endpoints.ADAPTERS), all read-only:
  virtual_on a name its enabled channels send upstream as different ids is
             several models under one name (admin; endpoints.virtual)
  served_by  the enabled channels listing each name (admin), named as the app
             names a connection (_slug)
  quota      the key's balance in the hub's own unit (the key's own, or its
             user's if the hub's DisplayTokenStatEnabled is off), read with the
             API key; and, as notes, the channels the hub switched off after
             failures (admin) and a version note
Left out: add (a channel's models could be written, but the name would then
share calls with every other channel listing it: add it in the hub), and
upstreams, which only Discover's Add uses. Never a channel test or a balance
update: both make a call upstream.
ponytail: every enabled channel counts, whatever its group; a key whose group
reaches only some of them may see fewer models behind a name than said.

Checked against one-api v0.6.10's source (commit 8df4a26, 2025-02-21) and
new-api v1.0.0-rc.51's (commit 8542c9d, 2026-11-20): router/api.go and
router/api-router.go, router/channel-router.go (GET /api/channel/ needs the
channel read permission), router/relay.go and relay-router.go (/v1/models),
router/dashboard.go (billing), middleware/auth.go (TokenAuth; AdminAuth with
an access token), model/channel.go (status 1 on, 2 off by hand, 3 off by the
hub; the key omitted from lists), controller/channel.go (one-api `data` a
list, new-api data.items), common/page_info.go (new-api's pages),
controller/billing.go (an unlimited token is 100000000), controller/misc.go
(GetStatus), docs/authentication.md (new-api: no New-Api-User since its
access tokens).
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "metrics"))
import endpoints  # noqa: E402

# Where the two differ, by hub (endpoints.HUBS): the first page of the channel
# list, and the releases this was checked against.
DIFF = {"oneapi": {"first_page": 0, "checked": ("0.6",)},
        "newapi": {"first_page": 1, "checked": ("1.0",)}}
ON, OFF_BY_HUB = 1, 3                                 # a channel's status
UNLIMITED = 100000000                                  # hard_limit_usd of a token without a limit


def _label(endpoint: str) -> str:
    return endpoints.HUBS[endpoints.hub(endpoint)]


def _call(endpoint: str, path: str, secret: str = "") -> dict:
    """GET off the hub's root; ValueError with its own words when it refuses."""
    head = {"Accept": "application/json", **({"Authorization": f"Bearer {secret}"} if secret else {})}
    req = urllib.request.Request(endpoints.base(endpoint).removesuffix("/v1") + path, headers=head)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read())
        except ValueError:
            body = None
        body = body if isinstance(body, dict) else {}
        err = body.get("error")
        data = {"success": False, "message": (err.get("message") if isinstance(err, dict) else err)
                or body.get("message") or f"HTTP {exc.code}"}
    except OSError as exc:                             # down, or too slow: a refusal too, never a crash
        raise ValueError(f"could not reach {_label(endpoint)} on {path.split('?')[0]}:"
                         f" {getattr(exc, 'reason', None) or exc}") from None
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(data, dict) and (data.get("success") is False or err):
        said = err.get("message") if isinstance(err, dict) else err or data.get("message")
        raise ValueError(f"{_label(endpoint)} refused {path.split('?')[0]}: {str(said)[:200]}")
    return data


def channels(endpoint: str) -> list[dict]:
    """Every channel, as the hub lists them (it leaves the keys out).
    ValueError without the admin token, which is never the API key."""
    secret = endpoints.admin_key(endpoint, fallback=False)
    if not secret:
        env = endpoints.ENV_KEY + endpoints.admin_name(endpoints.load()[endpoint]).upper().replace("-", "_")
        raise ValueError(f"{_label(endpoint)}'s access token is not stored: give it as the admin key on the"
                         f" Endpoints page (open {endpoint}), or set {env}")
    out: dict = {}
    page = DIFF[endpoints.hub(endpoint)]["first_page"]
    for _ in range(100):           # ponytail: 100 pages, 1,000 channels on one-api; ask for more if one has them
        data = _call(endpoint, f"/api/channel/?p={page}&page_size=100", secret).get("data")
        rows = data.get("items") if isinstance(data, dict) else data
        rows = [c for c in rows or [] if isinstance(c, dict) and c.get("id") not in out]
        if not rows:               # the end, or a hub that ignores the page asked
            break
        out.update((c.get("id"), c) for c in rows)
        if isinstance(data, dict) and len(out) >= int(data.get("total") or 0):
            break
        page += 1
    return list(out.values())


def _slug(c: dict) -> str:
    """A channel as the app names a connection: its name in [a-z0-9.-]
    (`Groq (free)` is groq-free), else its id."""
    return re.sub(r"[^a-z0-9.]+", "-", str(c.get("name") or "").lower()).strip("-") or f"channel-{c.get('id')}"


def _sent(c: dict) -> dict[str, str]:
    """A channel's served names -> the upstream id each is sent as."""
    try:
        mapping = json.loads(c.get("model_mapping") or "{}")
    except ValueError:
        mapping = {}
    mapping = mapping if isinstance(mapping, dict) else {}
    return {m: str(mapping.get(m) or m) for m in (x.strip() for x in str(c.get("models") or "").split(",")) if m}


def _serving(endpoint: str) -> dict[str, list[tuple[str, str]]]:
    """served name -> [(connection, upstream id)] over the enabled channels."""
    out: dict[str, list] = {}
    for c in channels(endpoint):
        if c.get("status") == ON:
            for name, up in _sent(c).items():
                out.setdefault(name, []).append((_slug(c), up))
    return out


def virtual_on(endpoint: str):
    """The test of the endpoint's /models entries (endpoints.virtual): a name
    its enabled channels send upstream as more than one id is a different
    model from call to call. Without the access token nothing is known and
    every entry passes; served_by says why where it shows (the picker)."""
    try:
        serving = _serving(endpoint)
    except ValueError:
        return lambda _entry: ""
    why = {}
    for name, via in serving.items():
        ups = sorted({up for _c, up in via})
        if len(ups) > 1:
            why[name] = f"one name over {len(ups)} models ({', '.join(ups)}), which {_label(endpoint)} spreads its calls across"
    return lambda entry: why.get(str(entry.get("id") or ""), "")


def served_by(endpoint: str) -> dict[str, list[str]]:
    """{served id: the connections (channels) serving it}."""
    return {name: list(dict.fromkeys(c for c, _up in via)) for name, via in _serving(endpoint).items()}


def quota(endpoint: str) -> dict:
    """The API key's balance as one meter, with no reset (it is not a window),
    and as notes what else is known: unlimited, an expiry, channels switched
    off by the hub, its release."""
    key, label, notes, groups = endpoints.key(endpoint), _label(endpoint), [], []
    sub = _call(endpoint, "/v1/dashboard/billing/subscription", key)
    whole = float(sub.get("hard_limit_usd") or 0)
    used = float(_call(endpoint, "/v1/dashboard/billing/usage", key).get("total_usage") or 0) / 100
    if whole >= UNLIMITED:
        notes.append(f"this key has no quota limit in {label}")
    elif whole > 0:
        left = max(0.0, whole - used)
        groups.append({"label": "", "windows": [{"label": f"balance, in {label}'s unit", "left": round(100 * left / whole),
                                                 "remaining": round(left, 2), "of": round(whole, 2), "reset_at": None}]})
    if int(sub.get("access_until") or 0) > 0:
        notes.append("this key expires " + dt.datetime.fromtimestamp(int(sub["access_until"]), dt.timezone.utc)
                     .isoformat(timespec="minutes"))
    try:
        off = sorted({_slug(c) for c in channels(endpoint) if c.get("status") == OFF_BY_HUB})
        if off:
            notes.append(f"switched off by {label} after failing: {', '.join(off)}")
    except ValueError as exc:
        notes.append(f"channels not read: {exc}")
    release = str((_call(endpoint, "/api/status").get("data") or {}).get("version") or "")
    note = endpoints.version_note(endpoints.hub(endpoint), release, DIFF[endpoints.hub(endpoint)]["checked"])
    return {"groups": groups, "notes": notes + ([note] if note else [])}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    ep, test = sys.argv[1], virtual_on(sys.argv[1])
    print(json.dumps({"virtual": {i: why for i, e in endpoints.models(ep).items() if (why := test(e))},
                      "served_by": served_by(ep), "quota": quota(ep)}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
