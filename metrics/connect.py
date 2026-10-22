"""Connect a provider by signing in on its own page, instead of pasting a key.

    python metrics/connect.py              # each provider: its flow, and whether it can be used here
    python metrics/connect.py --selftest   # every flow against a fake provider

RELEASE-PLAN.md Phase 5, "Connect buttons", and decision 13: only flows a
provider publishes for third-party apps. Never another tool's client, never a
subscription meant for the provider's own tools (Claude plans, and the coding
assistants' subscription logins that a proxy such as CLI Proxy API holds).

FOUR FLOWS COVER EVERY PROVIDER
-------------------------------
- `key`     PKCE, then the provider hands back an ordinary API key
            (OpenRouter, NanoGPT). Nothing expires, nothing to refresh.
- `oauth`   authorization code with PKCE, to a callback on this machine; an
            access token, and a refresh token when the provider gives one
            (Hugging Face, Pollinations, ChatGPT).
- `device`  the user types a short code on the provider's page; no callback,
            so it works where the browser is on another machine (GitHub).
            Any provider with a `device` URL also signs in this way when
            asked (`by_code`): the dashboard asks when it is not reached on
            this machine, where a callback to 127.0.0.1 could not arrive.
- `local`   nothing to sign in to here (Ollama: a local Ollama that is signed
            in to ollama.com serves its cloud models too).

WHERE THINGS GO
---------------
The key or access token goes where a pasted key goes: the llm CLI's keys.json
under the endpoint's key name (`endpoints.set_key`). A refresh token goes in
the same store as `<key name>.refresh`. The endpoint is saved by serve.py's
`save_endpoint`, with the same test and rules as a typed one, plus a `connect`
record (provider, when, expiry; never a secret). `fresh()` renews an expiring
token just before a call; `metrics/client.py` asks it, and endpoints that need
it are always called through that client, whatever BENCH_CLIENT says.

ADDING A PROVIDER
-----------------
1. Read its docs for a flow meant for third-party apps. If its only sign-in is
   a subscription login for its own tools, stop: decision 13 says no.
2. Add an entry to PROVIDERS. Fields:
     label, flow, base     shown name; one of FLOWS; its OpenAI-compatible base
     authorize, token      the authorize page; where the code (or device code,
                           or refresh token) is exchanged
     device                where a device code is requested (the device
                           flow, or signing in by code); `device_json` when
                           that request is JSON rather than a form
     client_id             a PUBLIC client id, if the provider needs one. Never
                           a secret: this file ships. Empty means "register an
                           app first" (`register` says where), and an install
                           can supply its own in config/connect.json
     scope, params         authorize scope; extra authorize parameters, which
                           may use {host_id} and {nonce}
     token_params          extra fields on every token request
     loopback              the callback host the provider accepts:
                           "127.0.0.1" (default) or "localhost"
     entry                 fields for the endpoint: its `kind` (required, one
                           of endpoints.KINDS); and when it is new, headers,
                           `api: responses`, `models_url`
     docs, hint            its docs; one line for when /models fails
3. Run `python metrics/connect.py --selftest`: it checks every entry's shape,
   and every flow against a fake provider.
4. Add an endpoint on the Endpoints page, pick the provider and sign in once for real.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import pathlib
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import endpoints  # noqa: E402
import locked  # noqa: E402
from paths import DATA  # noqa: E402

CONFIG = DATA / "config" / "connect.json"
FLOWS = ("key", "oauth", "device", "local")
CALLBACK = "/connect/callback"
PENDING_S = 900                                  # a sign-in left open longer is dropped (GitHub's device codes last 15 min)
EARLY_S = 300                                    # renew a token this long before it expires
UA = {"User-Agent": "benchwarmers", "Accept": "application/json"}
REPO = "https://github.com/WendellResearch-AI/benchwarmers"

PROVIDERS: dict[str, dict] = {
    "openrouter": {
        "label": "OpenRouter", "flow": "key", "base": "https://openrouter.ai/api/v1",
        "authorize": "https://openrouter.ai/auth",
        "token": "https://openrouter.ai/api/v1/auth/keys",
        "params": {"key_label": "Benchwarmers"}, "loopback": "localhost",
        # Who is calling, truthfully: some free models answer only named apps.
        "entry": {"kind": "aggregator", "headers": {"HTTP-Referer": REPO, "X-Title": "Benchwarmers"}},
        "docs": "https://openrouter.ai/docs/use-cases/oauth-pkce",
    },
    "nanogpt": {
        "label": "NanoGPT", "flow": "key", "base": "https://api.nano-gpt.com/api/v1",
        "authorize": "https://nano-gpt.com/auth",
        "token": "https://nano-gpt.com/api/v1/auth/keys",
        "scope": "api.use models.read", "params": {"client_name": "Benchwarmers"},
        "entry": {"kind": "aggregator"},
        "docs": "https://docs.nano-gpt.com/api-reference/miscellaneous/oauth-pkce",
    },
    "huggingface": {
        "label": "Hugging Face", "flow": "oauth", "base": "https://router.huggingface.co/v1",
        "authorize": "https://huggingface.co/oauth/authorize",
        "token": "https://huggingface.co/oauth/token",
        "device": "https://huggingface.co/oauth/device",
        "client_id": "", "scope": "openid inference-api",
        "register": "https://huggingface.co/settings/applications/new (no secret; redirect"
                    " http://127.0.0.1/connect/callback; scopes openid, inference-api)",
        "entry": {"kind": "aggregator"},             # its router fronts many inference providers
        "docs": "https://huggingface.co/docs/hub/oauth",
    },
    "pollinations": {
        "label": "Pollinations", "flow": "oauth", "base": "https://gen.pollinations.ai/v1",
        "authorize": "https://enter.pollinations.ai/authorize",
        "token": "https://enter.pollinations.ai/api/oauth/token",
        "device": "https://enter.pollinations.ai/api/device/code", "device_json": True,
        "client_id": "", "params": {"models": "text", "expiry": "30"}, "loopback": "localhost",
        "register": "https://enter.pollinations.ai/keys (an app key, pk_...; redirect"
                    " http://localhost/connect/callback)",
        "entry": {"kind": "aggregator"},
        "docs": "https://github.com/pollinations/pollinations/blob/main/BRING_YOUR_OWN_POLLEN.md",
    },
    "chatgpt": {
        "label": "ChatGPT plan", "flow": "oauth", "base": "https://api.openai.com/v1",
        "authorize": "https://auth.openai.com/api/accounts/authorize",
        "token": "https://auth.openai.com/api/accounts/oauth/token",
        # Registered during the first sign-in; the issued id comes back on the
        # callback and is used from then on.
        "client_id": "dynamic_agent_client",
        "scope": "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct",
        "params": {"agent_name_hint": "Benchwarmers", "ext_agent_host_id": "{host_id}",
                   "nonce": "{nonce}", "resource": "https://api.openai.com/v1"},
        "token_params": {"resource": "https://api.openai.com/v1"},
        "entry": {"kind": "provider", "api": "responses"},
        "docs": "https://developers.openai.com/siwc/token-sharing-open-source",
    },
    "github": {
        "label": "GitHub Models", "flow": "device", "base": "https://models.github.ai/inference",
        "device": "https://github.com/login/device/code",
        "token": "https://github.com/login/oauth/access_token",
        "client_id": "",
        "register": "a GitHub App: Developer settings > GitHub Apps > New; Enable Device Flow;"
                    " Account permissions > Models: Read",
        "entry": {"kind": "provider", "models_url": "https://models.github.ai/catalog/models"},
        "docs": "https://docs.github.com/en/github-models",
    },
    "ollama": {
        "label": "Ollama (this machine)", "flow": "local", "base": "http://localhost:11434/v1",
        "hint": "is Ollama running? For its cloud models, run `ollama signin` once",
        "entry": {"kind": "local"},
        "docs": "https://docs.ollama.com/openai",
    },
}


class ConnectError(Exception):
    """A sign-in that cannot go on; the text is for the person signing in."""


_pending: dict[str, dict] = {}                   # id -> one sign-in in progress
_lock = threading.Lock()


def _config() -> dict:
    return locked.read_json(CONFIG)


def host_id() -> str:
    """This install's stable id, made once (ChatGPT binds a sign-in to it). Not a secret."""
    def change(data: dict) -> str:
        return data.setdefault("host_id", f"urn:uuid:{uuid.uuid4()}")
    return locked.update(CONFIG, change)


def client_id(provider: str) -> str:
    """The install's own (config/connect.json "client_ids"), else the shipped one."""
    return own_ids().get(provider) or PROVIDERS[provider].get("client_id", "")


def own_ids() -> dict:
    """This install's own client ids, by provider: an app it registered itself
    (Use your own app), or the one ChatGPT issued it at the first sign-in."""
    ids = _config().get("client_ids")
    return ids if isinstance(ids, dict) else {}


CLIENT_ID = re.compile(r"^[A-Za-z0-9._~:-]{3,200}$")


def set_own_id(provider: str, cid: str) -> None:
    """Use your own app registration for `provider`, or "" to go back to the
    shipped one. Also the way out if a shipped app is ever withdrawn: every
    install can carry on with its own. Endpoints already signed in keep
    renewing with the id they were signed in with."""
    p = PROVIDERS.get(provider)
    if not p or not p.get("register"):
        raise ConnectError(f"{provider!r} has no app to register")
    cid = (cid or "").strip()
    if cid and not CLIENT_ID.match(cid):
        raise ConnectError("a client id is 3-200 letters, digits or . _ ~ : - (a public id, never a secret)")

    def change(data: dict) -> None:
        ids = data.setdefault("client_ids", {})
        if cid:
            ids[provider] = cid
        else:
            ids.pop(provider, None)
    locked.update(CONFIG, change)


def ready(provider: str) -> str:
    """"" when this provider can be used here, else what is missing."""
    p = PROVIDERS[provider]
    if p["flow"] in ("oauth", "device") and not client_id(provider):
        return f"needs an app registration: under Use your own app, register {p.get('register', 'one')}"
    return ""


def _post(url: str, fields: dict, as_json: bool = False) -> dict:
    """A token request. The reply as a dict, an OAuth error included."""
    import client                                # its no-redirect opener: a 3xx never gets the code
    body = json.dumps(fields).encode() if as_json else urllib.parse.urlencode(fields).encode()
    kind = "application/json" if as_json else "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, body, {**UA, "Content-Type": kind})
    try:
        with client._OPENER.open(request, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read() or b"{}")
        except ValueError:
            data = {}
        return data if isinstance(data, dict) and data.get("error") else \
            {"error": f"HTTP {e.code}", "error_description": str(data)[:200]}
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"error": "unreachable", "error_description": str(getattr(e, "reason", e))}


def _failed(reply: dict, what: str) -> ConnectError:
    why = reply.get("error_description") or reply.get("message") or reply.get("error")
    return ConnectError(f"{what}: {why}")


def _tokens(record: dict, reply: dict, cid: str) -> dict:
    """What a successful token reply leaves for serve.py to save: for the
    endpoint the sign-in was started for (a provider can be several)."""
    secret = reply.get("key") or reply.get("access_token")
    if not secret:
        raise _failed(reply, "the provider sent no key")
    expires = None
    try:
        if reply.get("expires_in"):
            expires = (dt.datetime.now(dt.timezone.utc)
                       + dt.timedelta(seconds=int(reply["expires_in"]))).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        pass                                     # an odd expiry: treated as none, and a 401 says when
    return {"provider": record["provider"], "endpoint": record.get("endpoint") or record["provider"],
            "secret": secret, "refresh": reply.get("refresh_token", ""), "expires": expires, "client_id": cid}


def _sweep() -> None:
    now = time.time()
    for k in [k for k, v in _pending.items() if now - v["at"] > PENDING_S]:
        del _pending[k]


def start(provider: str, port: int, endpoint: str = "", by_code: bool = False) -> dict:
    """Begin a sign-in for `endpoint` (default: the provider's id). {"url"} to
    open (key, oauth), or {"code", "verify"} to show (device, or `by_code`);
    either way an "id" to poll."""
    p = PROVIDERS.get(provider)
    if not p or p["flow"] == "local":
        raise ConnectError(f"{provider!r} has no sign-in")
    if (missing := ready(provider)):
        raise ConnectError(missing)
    if by_code and not p.get("device"):
        raise ConnectError(f"{p['label']} signs in only through a page on the machine Benchwarmers runs on;"
                           " from here, add it with an API key instead")
    cid = client_id(provider)
    sid = secrets.token_urlsafe(24)
    record = {"provider": provider, "endpoint": endpoint or provider, "client_id": cid,
              "at": time.time(), "result": None}
    if p["flow"] == "device" or by_code:
        fields = {"client_id": cid, **({"scope": p["scope"]} if p.get("scope") else {})}
        reply = _post(p["device"], fields, as_json=bool(p.get("device_json")))
        if not reply.get("device_code"):
            raise _failed(reply, "the provider would not start a sign-in")
        interval = int(reply.get("interval", 5))
        record.update(device_code=reply["device_code"], interval=interval, next=time.time() + interval)
        out = {"id": sid, "code": reply.get("user_code", ""),
               "verify": reply.get("verification_uri_complete") or reply.get("verification_uri", "")}
    else:
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        redirect = f"http://{p.get('loopback', '127.0.0.1')}:{port}{CALLBACK}"
        if p["flow"] == "key":
            # No registered redirect to match, and OpenRouter's docs never say it
            # sends `state` back: the sign-in's id rides in the path instead.
            redirect += "/" + sid
        extra = {k: v.format(host_id=host_id() if "{host_id}" in v else "",
                             nonce=secrets.token_urlsafe(16)) for k, v in p.get("params", {}).items()}
        if p["flow"] == "key":
            query = {"callback_url": redirect}
        else:
            query = {"response_type": "code", "client_id": cid, "redirect_uri": redirect}
        query.update({"state": sid, "code_challenge": challenge, "code_challenge_method": "S256",
                      **({"scope": p["scope"]} if p.get("scope") else {}), **extra})
        record.update(verifier=verifier, redirect=redirect)
        out = {"id": sid, "url": p["authorize"] + "?" + urllib.parse.urlencode(query)}
    with _lock:
        _sweep()
        _pending[sid] = record
    return out


def callback_id(path: str, query: dict) -> str:
    """The sign-in a callback belongs to: `state`, or the id in its path (key flow)."""
    tail = path.split("?")[0][len(CALLBACK):].strip("/")
    return query.get("state") or tail


def finish(query: dict) -> dict:
    """The callback: check the state, exchange the code, return _tokens' dict.
    The caller saves it and hands poll() the outcome with settle(), so the
    dashboard tab that started the sign-in can tell, and no secret waits here."""
    sid = query.get("state", "")
    with _lock:                                  # checked and marked at once: a doubled callback
        _sweep()                                 # (a prefetch, a retry) exchanges the code once
        record = _pending.get(sid)
        if not record or record.get("used") or "verifier" not in record:
            raise ConnectError("this sign-in is unknown or has expired; start it again from Benchwarmers")
        record["used"] = True
    p = PROVIDERS[record["provider"]]
    try:
        if query.get("error"):
            raise ConnectError(f"{p['label']} said: {query.get('error_description') or query['error']}")
        if not query.get("code"):
            raise ConnectError("the provider sent no code")
        if p["flow"] == "key":
            reply = _post(p["token"], {"code": query["code"], "code_verifier": record["verifier"],
                                       "code_challenge_method": "S256"}, as_json=True)
            cid = ""
        else:
            # ChatGPT's first sign-in issues this install its own client id,
            # kept at once (not with the endpoint, which a failed save loses)
            # because the next sign-in must use it.
            cid = query.get("client_id") or record["client_id"]
            if cid != record["client_id"]:
                locked.update(CONFIG, lambda data: data.setdefault("client_ids", {}).update(
                    {record["provider"]: cid}))
            reply = _post(p["token"], {"grant_type": "authorization_code", "code": query["code"],
                                       "client_id": cid, "redirect_uri": record["redirect"],
                                       "code_verifier": record["verifier"], **p.get("token_params", {})})
        return _tokens(record, reply, cid)
    except ConnectError as exc:
        record["result"] = {"error": str(exc)}
        raise


def settle(sid: str, outcome: dict) -> None:
    """What became of a finished sign-in, for poll() to report."""
    with _lock:
        if sid in _pending:
            _pending[sid]["result"] = outcome


def poll(sid: str) -> dict:
    """{"state": "pending"}, {"state": "error", "error"}, or {"state": "done", ...}
    once: a device sign-in's tokens (the caller saves them), or what settle()
    recorded for a redirect sign-in."""
    with _lock:
        _sweep()
        record = _pending.get(sid)
        # One token request at a time: polls overlap when one is slow, and a
        # second request for a used device code would overwrite the tokens.
        due = bool(record) and "device_code" in record and record["result"] is None \
            and not record.get("asking") and time.time() >= record["next"]
        if due:
            record["asking"] = True
    if not record:
        return {"state": "error", "error": "this sign-in is unknown or has expired; start it again"}
    if due:
        try:
            p = PROVIDERS[record["provider"]]
            reply = _post(p["token"], {"client_id": record["client_id"], "device_code": record["device_code"],
                                       "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
            error = reply.get("error")
            if error == "slow_down":
                record["interval"] += 5
            if error in ("authorization_pending", "slow_down"):
                record["next"] = time.time() + record["interval"]
            elif error:
                record["result"] = {"error": str(_failed(reply, "the sign-in did not finish"))}
            else:
                try:
                    record["result"] = _tokens(record, reply, record["client_id"])
                except ConnectError as exc:
                    record["result"] = {"error": str(exc)}
        finally:
            record["asking"] = False
    result = record["result"]
    if result is None:
        return {"state": "pending"}
    with _lock:
        _pending.pop(sid, None)                  # handed over once, then forgotten
    return {"state": "error", **result} if "error" in result else {"state": "done", **result}


def entry_field(tokens: dict) -> dict:
    """The endpoint's `connect` field for a fresh sign-in: no secrets."""
    return {"provider": tokens["provider"], "at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            **({"expires": tokens["expires"]} if tokens.get("expires") else {}),
            **({"client_id": tokens["client_id"]} if tokens.get("client_id") else {}),
            **({"refreshes": True} if tokens.get("refresh") else {})}


def fresh(key_name: str) -> str:
    """Renew the token behind `key_name` if it is about to expire. "" when it is
    good (or needs nothing), else why it cannot be used: the caller fails the
    call with that, and suite/run.py classes it `signin`, never a retirement."""
    name, entry = next(((n, e) for n, e in endpoints.load().items() if e.get("key") == key_name),
                       (None, {}))
    conn = entry.get("connect") or {}
    if not (conn.get("refreshes") and conn.get("expires")):
        return ""
    if _seconds_left(conn) > EARLY_S:
        return ""
    # One renewal at a time, across the gateway's threads and other processes:
    # a refresh token is single-use, so two racing renewals leave one broken.
    with renewal_lock(key_name):
        conn = (endpoints.load().get(name) or {}).get("connect") or {}
        if _seconds_left(conn) > EARLY_S:
            return ""                            # another caller renewed it meanwhile
        p = PROVIDERS.get(conn.get("provider", ""), {})
        token = endpoints.keys().get(f"{key_name}.refresh", "")
        if not (p and token):
            return f"the sign-in for {name} has expired; sign in again on the Endpoints page"
        reply = _post(p["token"], {"grant_type": "refresh_token", "refresh_token": token,
                                   "client_id": conn.get("client_id") or client_id(conn["provider"]),
                                   **p.get("token_params", {})})
        try:
            tokens = _tokens({"provider": conn["provider"], "endpoint": name}, reply, conn.get("client_id", ""))
        except ConnectError as exc:
            return f"the sign-in for {name} could not be renewed ({exc}); sign in again on the Endpoints page"
        # The refresh token first: it is spent now, and an access token without
        # its replacement could never be renewed. A crash after it only costs
        # one more renewal.
        if tokens["refresh"]:
            endpoints.set_key(f"{key_name}.refresh", tokens["refresh"])
        endpoints.set_key(key_name, tokens["secret"])
        stamp = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        locked.update(endpoints.FILE, lambda data: (data.get(name) or {}).get("connect", {}).update(
            expires=tokens["expires"], renewed=stamp))  # removed meanwhile: nothing to update
    return ""


def renewal_lock(key_name: str):
    """Held to renew a token and to save a new sign-in, so one never overwrites the other."""
    return locked.lock(endpoints.FILE.with_name(f"connect-{key_name}"))


def _seconds_left(conn: dict) -> float:
    try:
        return (dt.datetime.fromisoformat(conn["expires"]) - dt.datetime.now(dt.timezone.utc)).total_seconds()
    except (KeyError, TypeError, ValueError):
        return 0.0


def standing(conn: dict | None) -> dict | None:
    """An endpoint's `connect` record as the dashboard shows it: who signed in,
    until when, whether that has passed. No secrets."""
    if not conn:
        return None
    gone = bool(conn.get("expires")) and not conn.get("refreshes") and _seconds_left(conn) <= 0
    p = PROVIDERS.get(conn.get("provider", ""), {})
    return {**conn, "label": p.get("label", ""), "flow": p.get("flow", ""), "expired": gone}


def check() -> list[str]:
    """Every PROVIDERS entry has what its flow needs."""
    problems = []
    needs = {"key": ("authorize", "token"), "oauth": ("authorize", "token"),
             "device": ("device", "token"), "local": ()}
    for pid, p in PROVIDERS.items():
        if p.get("flow") not in FLOWS:
            problems.append(f"{pid}: flow must be one of {FLOWS}")
            continue
        if not endpoints.NAME.match(pid):
            problems.append(f"{pid}: the id names the endpoint, so it must match endpoints.NAME")
        for field in ("label", "base", "docs", *needs[p["flow"]]):
            if not p.get(field):
                problems.append(f"{pid}: {field} is missing")
        for field in ("base", "authorize", "token", "device", "docs"):
            url = p.get(field, "")
            if url and not url.startswith("https://") and not (pid == "ollama" and url.startswith("http://localhost")):
                problems.append(f"{pid}: {field} must be https")
        if p["flow"] in ("oauth", "device") and not p.get("client_id") and not p.get("register"):
            problems.append(f"{pid}: with no shipped client_id, say where to register one")
        if p.get("loopback", "127.0.0.1") not in ("127.0.0.1", "localhost"):
            problems.append(f"{pid}: loopback is 127.0.0.1 or localhost")
        for k in ("params", "token_params", "entry"):
            if not isinstance(p.get(k, {}), dict):
                problems.append(f"{pid}: {k} must be an object")
        if (p.get("entry") or {}).get("kind") not in set(endpoints.KINDS) - {"hub"}:
            problems.append(f"{pid}: entry.kind is provider, aggregator or local (a hub is added by hand)")
    return problems


def main() -> None:
    for pid, p in PROVIDERS.items():
        print(f"  {pid:<13} {p['flow']:<7} {ready(pid) or 'ready'}")


def _selftest() -> None:
    """Shapes, then every flow against a fake provider: a PKCE check that really
    hashes, single-use state, device polling, and renewal under the lock."""
    import tempfile
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    assert not (problems := check()), problems
    seen: dict = {"challenges": {}, "polls": 0}

    class Fake(BaseHTTPRequestHandler):
        def do_POST(self):                       # noqa: N802
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
            f = json.loads(raw) if self.headers.get("Content-Type", "").startswith("application/json") \
                else dict(urllib.parse.parse_qsl(raw))
            reply, status = {"error": "invalid_request"}, 400
            if self.path == "/device":
                reply, status = {"device_code": "dc", "user_code": "ABCD-1234",
                                 "verification_uri": "https://fake/device", "interval": 0}, 200
            elif f.get("grant_type", "").endswith("device_code"):
                seen["polls"] += 1
                reply, status = ({"error": "authorization_pending"}, 400) if seen["polls"] == 1 else \
                    ({"access_token": "ghu_x", "refresh_token": "ghr_x", "expires_in": 28800}, 200)
            elif f.get("grant_type") == "refresh_token":
                reply, status = ({"access_token": "fresh", "refresh_token": "r2", "expires_in": 3600}, 200) \
                    if f.get("refresh_token") == "r1" else ({"error": "invalid_grant"}, 400)
            elif "code" in f:
                want = seen["challenges"].get(f["code"])
                got = base64.urlsafe_b64encode(hashlib.sha256(f["code_verifier"].encode()).digest()).rstrip(b"=").decode()
                if want and want == got:
                    reply, status = ({"key": "sk-key"} if self.path == "/keys" else
                                     {"access_token": "at", "refresh_token": "r1", "expires_in": 3600}), 200
            body = json.dumps(reply).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    fake = f"http://127.0.0.1:{server.server_port}"
    global CONFIG
    saved = (dict(PROVIDERS), CONFIG, endpoints.FILE, endpoints.LLM_DIR)
    with tempfile.TemporaryDirectory() as tmp:
        try:
            CONFIG, endpoints.FILE, endpoints.LLM_DIR = (pathlib.Path(tmp) / "connect.json",
                                                         pathlib.Path(tmp) / "endpoints.json", pathlib.Path(tmp))
            PROVIDERS.update(
                k={**PROVIDERS["openrouter"], "authorize": fake + "/auth", "token": fake + "/keys"},
                o={**PROVIDERS["chatgpt"], "authorize": fake + "/auth", "token": fake + "/token"},
                d={**PROVIDERS["github"], "client_id": "Iv1.x", "device": fake + "/device", "token": fake + "/token"},
                h={**PROVIDERS["huggingface"]})

            def soon_past() -> str:
                return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=5)).isoformat()

            def sign_in(pid: str, port: int = 8122) -> tuple[dict, dict]:
                url = start(pid, port)["url"]
                q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
                seen["challenges"]["c-" + pid] = q["code_challenge"]
                return q, finish({"state": q["state"], "code": "c-" + pid, "client_id": "issued"})

            q, got = sign_in("k")
            assert q["callback_url"] == "http://localhost:8122/connect/callback/" + q["state"] and q["key_label"], q
            assert callback_id(f"/connect/callback/{q['state']}?code=x", {"code": "x"}) == q["state"]
            assert callback_id("/connect/callback?state=s&code=x", {"state": "s"}) == "s"
            assert got["secret"] == "sk-key" and not got["refresh"] and got["expires"] is None, got
            assert got["endpoint"] == "k"                                    # by default, the provider's id
            assert poll(q["state"]) == {"state": "pending"}                  # until it is saved
            settle(q["state"], {"ok": True, "served": 3})
            assert poll(q["state"]) == {"state": "done", "ok": True, "served": 3}
            assert poll(q["state"])["state"] == "error"                     # handed over once
            try:
                finish({"state": q["state"], "code": "c-k"})
                raise AssertionError("a used state was accepted twice")
            except ConnectError:
                pass

            q, got = sign_in("o", port=9000)
            assert q["redirect_uri"] == "http://127.0.0.1:9000/connect/callback", q
            assert q["ext_agent_host_id"] == host_id() and q["nonce"] and "{" not in q["nonce"], q
            assert q["resource"] == "https://api.openai.com/v1", q
            assert q["client_id"] == "dynamic_agent_client" and got["client_id"] == "issued", (q, got)
            assert client_id("o") == "issued"                    # kept for the next sign-in at once
            assert got["secret"] == "at" and got["refresh"] == "r1" and got["expires"], got

            # A wrong verifier is refused by the provider, and says so.
            url = start("o", 9000)["url"]
            state = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))["state"]
            seen["challenges"]["c-bad"] = "not-the-challenge"
            try:
                finish({"state": state, "code": "c-bad"})
                raise AssertionError("an exchange the provider refused looked fine")
            except ConnectError as exc:
                assert "no key" in str(exc), exc
            assert standing({"provider": "o", "expires": soon_past()})["expired"]
            assert not standing({"provider": "o", "expires": soon_past(), "refreshes": True})["expired"]
            assert poll(state)["state"] == "error"
            try:
                finish({"state": state, "error": "access_denied"})
                raise AssertionError("a finished sign-in was reused")
            except ConnectError:
                pass

            # Signing in by code where there is a device URL (JSON for Pollinations); refused where there is none.
            PROVIDERS["c"] = {**PROVIDERS["pollinations"], "client_id": "pk_x", "device": fake + "/device",
                              "token": fake + "/token"}
            c = start("c", 8122, endpoint="c", by_code=True)
            assert c["code"] == "ABCD-1234" and poll(c["id"]) == {"state": "pending"}, c
            assert poll(c["id"])["state"] == "done"
            try:
                start("k", 8122, by_code=True)
                raise AssertionError("a provider with no device URL started a code sign-in")
            except ConnectError as exc:
                assert "API key" in str(exc)
            seen["polls"] = 0

            d = start("d", 8122, endpoint="d-2")
            assert d["code"] == "ABCD-1234" and poll(d["id"]) == {"state": "pending"}
            done = poll(d["id"])
            assert done["state"] == "done" and done["secret"] == "ghu_x" and done["refresh"] == "ghr_x", done
            assert done["endpoint"] == "d-2"                                 # the one it was started for

            try:
                start("h", 8122)
                raise AssertionError("started a sign-in with no client id")
            except ConnectError as exc:
                assert "app registration" in str(exc)
            # Use your own app: set, used, cleared; refused for a provider with nothing to register.
            set_own_id("h", "hf-own")
            assert "client_id=hf-own" in start("h", 8122)["url"] and own_ids()["h"] == "hf-own"
            set_own_id("h", "")
            assert "h" not in own_ids() and ready("h")
            for bad in (("k", "x-id"), ("h", "has space"), ("h", "a" * 300)):
                try:
                    set_own_id(*bad)
                    raise AssertionError(f"accepted {bad}")
                except ConnectError:
                    pass
            set_own_id("h", "hf-own")

            # Renewal: due, renewed once, written where a key goes; then a dead refresh token.
            soon = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=60)).isoformat()
            endpoints.FILE.write_text(json.dumps({"o": {"base": "https://x/v1", "key": "o", "connect": {
                "provider": "o", "expires": soon, "client_id": "issued", "refreshes": True}}}), encoding="utf-8")
            endpoints.set_key("o", "at")
            endpoints.set_key("o.refresh", "r1")
            assert fresh("o") == "" and endpoints.keys()["o"] == "fresh" and endpoints.keys()["o.refresh"] == "r2"
            assert _seconds_left(endpoints.load()["o"]["connect"]) > EARLY_S
            assert fresh("o") == ""                                    # not due: no request
            locked.update(endpoints.FILE, lambda data: data["o"]["connect"].update(expires=soon))
            assert "sign in again" in fresh("o")                       # r2 is not one the fake accepts
            assert fresh("nobody") == ""
        finally:
            PROVIDERS.clear()
            PROVIDERS.update(saved[0])
            CONFIG, endpoints.FILE, endpoints.LLM_DIR = saved[1:]
            server.shutdown()
    print("ok")


if __name__ == "__main__":
    _selftest() if "--selftest" in sys.argv else main()
