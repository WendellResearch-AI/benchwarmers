"""One request check for both servers, the dashboard (serve.py) and the gateway.

    python metrics/guard.py      # self-check

Both bind to 127.0.0.1 by default, which keeps other machines out but not
other web pages: any site open in the browser can send requests to localhost.

- **Host allow-list, on every request.** DNS rebinding points a site's own
  name at 127.0.0.1, so the browser treats our replies as that site's and
  lets its script read them (prompts and answers, endpoints, keys' names).
  The Host header still carries the site's name, so only our own names, port
  included, are answered. More (a Docker service name, a proxy) come from
  BENCH_ALLOWED_HOSTS, comma-separated host:port, and BENCHWARMERS_PUBLIC_URL.
- **On POST, JSON only and never cross-site.** A page can send a plain form
  or text/plain to us without asking, but not application/json: that needs
  a CORS preflight neither server answers. A browser also stamps
  Sec-Fetch-Site and Origin, which a page cannot remove; cross-site or a
  foreign Origin is refused. API clients (SDKs, curl, agents, Open WebUI's
  backend) send neither and are unaffected. BENCH_ALLOWED_ORIGINS adds a
  browser UI on another port.
- **A body-size cap**, so one request cannot make a server read gigabytes.

WHO MAY ASK (RELEASE-PLAN Phase 5, "Token login before Docker")
---------------------------------------------------------------
Decided by ROUTE, never by what a request happens to carry, so an API call
with a bearer never meets the login cookie and a browser's cookie never opens
the gateway:

- the gateway's routes (`/v1/...`, `/bench/...`, on either port) take a
  GATEWAY KEY as `Authorization: Bearer <key>`: named, revocable, kept only
  as a SHA-256 in `config/gateway-keys.json`, shown once when made. A key
  never opens the dashboard, so a chat UI or agent given one cannot change
  settings, read keys or spend through it.
- every other route is the dashboard's and takes the LOGIN COOKIE, set by
  posting BENCHWARMERS_ADMIN_TOKEN to /login: `<expiry>.<HMAC of it keyed by
  the token>`, HttpOnly, SameSite=Strict, Secure when the public URL is https.
  Changing the token signs every browser out. Open without either: /health,
  the login page, and Connect's callback (the provider's redirect back is
  cross-site, so a Strict cookie never rides on it; its single-use,
  unguessable state is what protects it).
- NO ADMIN TOKEN, NO AUTH: on 127.0.0.1 without BENCHWARMERS_ADMIN_TOKEN both
  servers answer as they always did (no login, no key), and the checks above
  are what keep web pages out. Binding anywhere else refuses to start without
  a token (`startup`), so a server on a network always asks.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import http.cookies
import ipaddress
import os
import re
import secrets
import sys
import time
import urllib.parse

from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
import locked  # noqa: E402

KEYS = DATA / "config" / "gateway-keys.json"
KEY_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")
COOKIE = "bw_session"
SESSION_S = 7 * 24 * 3600
MIN_TOKEN = 16
GATEWAY_ROUTES = ("/v1/", "/bench/")


class Refused(Exception):
    """The request is not served. `status` is the HTTP code to answer with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _env(name: str) -> set[str]:
    return {v.strip().lower() for v in os.environ.get(name, "").split(",") if v.strip()}


def bind() -> str:
    """The address both servers listen on: BENCH_BIND, else this machine only."""
    return os.environ.get("BENCH_BIND", "").strip() or "127.0.0.1"


def public_url() -> str:
    return os.environ.get("BENCHWARMERS_PUBLIC_URL", "").strip().rstrip("/")


def _public() -> tuple[str, str]:
    """(host, origin) of BENCHWARMERS_PUBLIC_URL, or ("", "") when unset or not
    an http(s) URL. ponytail: the host only; serving under a path prefix behind
    a proxy is not supported."""
    u = urllib.parse.urlsplit(public_url())
    if u.scheme not in ("http", "https") or not u.netloc or "@" in u.netloc:
        return "", ""
    return u.netloc.lower(), f"{u.scheme}://{u.netloc}".lower()


def hosts(port: int) -> set[str]:
    public = _public()[0]
    return ({f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"} | _env("BENCH_ALLOWED_HOSTS")
            | ({public} if public else set()))


def check(headers, port: int, post: bool, max_body: int) -> int:
    """Raise Refused, or return how many body bytes to read (0 for a GET)."""
    host = (headers.get("Host") or "").strip().lower()
    if host not in hosts(port):
        raise Refused(403, f"unknown Host {host or '(none)'!r}; set BENCHWARMERS_PUBLIC_URL"
                           " or BENCH_ALLOWED_HOSTS to add one")
    if not post:
        return 0
    if (headers.get("Sec-Fetch-Site") or "").lower() == "cross-site":
        raise Refused(403, "cross-site requests are refused")
    origin = (headers.get("Origin") or "").strip().lower()
    allowed = {f"http://{h}" for h in hosts(port)} | _env("BENCH_ALLOWED_ORIGINS") | {_public()[1]} - {""}
    if origin and origin not in allowed:
        raise Refused(403, f"Origin {origin!r} is not allowed; set BENCH_ALLOWED_ORIGINS to add one")
    if not (headers.get("Content-Type") or "").lower().startswith("application/json"):
        raise Refused(415, "send Content-Type: application/json")
    try:
        length = int(headers.get("Content-Length") or 0)
    except ValueError:
        raise Refused(400, "Content-Length is not a number") from None
    if length < 0:
        raise Refused(400, "Content-Length is negative")
    if length > max_body:
        raise Refused(413, f"body is {length:,} bytes; the limit is {max_body:,}")
    return length


# ---- auth: decided by route ------------------------------------------------

def admin_token() -> str:
    return os.environ.get("BENCHWARMERS_ADMIN_TOKEN", "")


def auth_on() -> bool:
    return bool(admin_token())


def _loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def startup(address: str) -> None:
    """Refuse (SystemExit) a configuration that must not serve: a network
    address without an admin token, a guessable token, a public URL that is
    not one."""
    token = admin_token()
    if token and len(token) < MIN_TOKEN:
        # ponytail: no login rate limit; this floor is what makes guessing
        # hopeless. Add a limit if short tokens ever have to be allowed.
        raise SystemExit(f"BENCHWARMERS_ADMIN_TOKEN is {len(token)} characters; use {MIN_TOKEN} or more,"
                         ' e.g. python -c "import secrets; print(secrets.token_urlsafe(24))"')
    if public_url() and not _public()[0]:
        raise SystemExit(f"BENCHWARMERS_PUBLIC_URL must be http(s)://host[:port], not {public_url()!r}")
    if not token and not _loopback(address):
        raise SystemExit(f"refusing to listen on {address}: beyond this machine every request needs"
                         " a login, so set BENCHWARMERS_ADMIN_TOKEN first (or BENCH_BIND=127.0.0.1)")


def gateway_route(path: str) -> bool:
    """A route the gateway answers, on its own port or the dashboard's."""
    return path.startswith(GATEWAY_ROUTES)


def _sign(expires: int) -> bytes:
    return hmac.new(admin_token().encode(), f"session|{expires}".encode(), hashlib.sha256).hexdigest().encode()


def login(given: str) -> bool:
    """True when `given` is the admin token. Digests compared, so neither the
    token's length nor its content leaks through timing."""
    token = admin_token()
    return bool(token) and hmac.compare_digest(hashlib.sha256(given.encode()).digest(),
                                               hashlib.sha256(token.encode()).digest())


def session_cookie() -> str:
    """The Set-Cookie value for a good login."""
    expires = int(time.time()) + SESSION_S
    secure = "; Secure" if _public()[1].startswith("https://") else ""
    return (f"{COOKIE}={expires}.{_sign(expires).decode()}; Path=/; Max-Age={SESSION_S};"
            f" HttpOnly; SameSite=Strict{secure}")


def _session_ok(headers) -> bool:
    jar = http.cookies.SimpleCookie()
    try:
        jar.load(headers.get("Cookie") or "")
    except http.cookies.CookieError:
        return False
    morsel = jar.get(COOKIE)
    expires, _, sig = (morsel.value if morsel else "").partition(".")
    if not (expires.isascii() and expires.isdigit()) or int(expires) < time.time():
        return False
    return hmac.compare_digest(sig.encode(), _sign(int(expires)))


def admin_caller(headers) -> str:
    """Who is asking for a dashboard route: "admin" with a good login cookie,
    "local" when no admin token is set. Raises Refused(401)."""
    if not auth_on():
        return "local"
    if not _session_ok(headers):
        raise Refused(401, "sign in first: /login")
    return "admin"


def gateway_caller(headers) -> str:
    """Who is asking for a gateway route: the gateway key's name, or "local"
    when no admin token is set (any bearer is ignored then: SDKs send a dummy
    one). Raises Refused(401). The cookie is never looked at."""
    if not auth_on():
        return "local"
    scheme, _, value = (headers.get("Authorization") or "").partition(" ")
    name = key_name(value.strip()) if scheme.lower() == "bearer" else None
    if not name:
        raise Refused(401, "this route needs a gateway key (Authorization: Bearer <key>);"
                           " make one on the dashboard's Settings page")
    return name


# ---- gateway keys ------------------------------------------------------------

def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).hexdigest().encode()


def gateway_keys() -> dict[str, dict]:
    """name -> {"sha256", "created"}. Never a key itself."""
    return locked.read_json(KEYS)


def make_key(name: str) -> str:
    """A new key, returned this once: only its SHA-256 is kept. A slow hash
    would add nothing, the key being 256 random bits rather than a password."""
    if not KEY_NAME.match(name):
        raise ValueError("name: 2-32 lower-case letters, digits or '-', e.g. open-webui")
    value = "bwk_" + secrets.token_urlsafe(32)

    def add(data: dict) -> None:
        if name in data:
            raise ValueError(f"there is already a key named {name}: revoke it first, or pick another name")
        data[name] = {"sha256": _digest(value).decode(),
                      "created": dt.datetime.now().astimezone().isoformat(timespec="seconds")}
    locked.update(KEYS, add)
    return value


def revoke_key(name: str) -> bool:
    return locked.update(KEYS, lambda data: data.pop(name, None)) is not None


def key_name(value: str) -> str | None:
    """The name of the gateway key `value` is, or None. Every stored digest is
    compared in constant time, and all of them, whatever matched."""
    if not value:
        return None
    given, found = _digest(value), None
    for name, entry in gateway_keys().items():
        stored = str(entry.get("sha256", "")).encode() if isinstance(entry, dict) else b""
        if hmac.compare_digest(given, stored):
            found = name
    return found


def access(handler, code) -> None:
    """One access-log line on stderr: method, route, status, who. Never the
    query (a sign-in's code travels in one) or a Connect callback's id. A
    request line too broken to parse has no path or command yet."""
    route = re.sub(r"^(/connect/callback)/.*", r"\1/...", (getattr(handler, "path", "") or "").split("?", 1)[0])
    sys.stderr.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {getattr(handler, 'command', '') or '-'}"
                     f" {ascii(route[:200])[1:-1]}"
                     f" {getattr(code, 'value', code)} {getattr(handler, 'who', '-')}\n")


def _selftest() -> None:
    import pathlib
    import tempfile
    global KEYS
    ok = {"Host": "127.0.0.1:8123", "Content-Type": "application/json", "Content-Length": "12"}

    def status(port=8123, post=True, cap=100, **h):
        try:
            check({**ok, **h}, port, post, cap)
            return 0
        except Refused as r:
            return r.status
    assert status() == 0
    assert status(Host="localhost:8123") == 0 and status(Host="LOCALHOST:8123") == 0
    assert status(Host="evil.example:8123") == 403            # DNS rebinding
    assert status(Host="127.0.0.1") == 403                    # port is part of the name
    assert status(port=8122) == 403                           # the other server's port
    assert status(post=False, Host="evil.example") == 403     # GETs too
    assert status(post=False, **{"Content-Type": ""}) == 0    # a GET needs no JSON type
    assert status(**{"Content-Type": "text/plain"}) == 415    # the form a page can send freely
    assert status(Origin="http://evil.example") == 403
    assert status(Origin="http://127.0.0.1:8123") == 0
    assert status(**{"Sec-Fetch-Site": "cross-site"}) == 403
    assert status(**{"Sec-Fetch-Site": "same-origin"}) == 0
    assert status(**{"Content-Length": "101"}) == 413
    assert status(**{"Content-Length": "x"}) == 400
    assert status(**{"Content-Length": "-1"}) == 400
    saved = {k: os.environ.pop(k, None) for k in ("BENCH_ALLOWED_HOSTS", "BENCH_ALLOWED_ORIGINS",
                                                    "BENCHWARMERS_PUBLIC_URL", "BENCHWARMERS_ADMIN_TOKEN")}
    try:
        os.environ["BENCH_ALLOWED_HOSTS"] = "bench:8123"
        os.environ["BENCH_ALLOWED_ORIGINS"] = "http://localhost:3000"
        assert status(Host="bench:8123") == 0
        assert status(Origin="http://localhost:3000") == 0
        # The public URL is a Host and an Origin, https included.
        os.environ["BENCHWARMERS_PUBLIC_URL"] = "https://Bench.example.com/"
        assert status(Host="bench.example.com", Origin="https://bench.example.com") == 0
        assert status(Host="bench.example.com", Origin="https://evil.example") == 403

        # Binding: this machine needs nothing; anywhere else needs a long enough token.
        def refused(address: str) -> bool:
            try:
                startup(address)
                return False
            except SystemExit:
                return True
        assert not refused("127.0.0.1") and not refused("localhost") and not refused("::1")
        assert refused("0.0.0.0") and refused("") and refused("192.0.2.7")
        os.environ["BENCHWARMERS_ADMIN_TOKEN"] = "short"
        assert refused("127.0.0.1")
        token = os.environ["BENCHWARMERS_ADMIN_TOKEN"] = "t" * 24
        assert not refused("0.0.0.0")
        os.environ["BENCHWARMERS_PUBLIC_URL"] = "bench.example.com"       # no scheme
        assert refused("0.0.0.0")
        os.environ["BENCHWARMERS_PUBLIC_URL"] = "https://bench.example.com"

        # The login cookie: only the token makes one; tampered, expired or foreign ones fail.
        assert login(token) and not login("t" * 23) and not login("")
        cookie = session_cookie()
        assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Secure" in cookie and token not in cookie
        value = cookie.split(";", 1)[0]
        assert admin_caller({"Cookie": value}) == "admin"
        assert admin_caller({"Cookie": f"theme=x; {value}"}) == "admin"
        for bad in ("", value + "0", value.replace(f"{COOKIE}=", f"{COOKIE}=1"), f"{COOKIE}=1.abc",
                    f"{COOKIE}=²." + "0" * 64, f"{COOKIE}=0.{_sign(0).decode()}", 'x="'):
            try:
                admin_caller({"Cookie": bad})
                raise AssertionError(f"accepted cookie {bad!r}")
            except Refused as r:
                assert r.status == 401
        os.environ["BENCHWARMERS_ADMIN_TOKEN"] = "u" * 24                  # a new token signs everyone out
        try:
            admin_caller({"Cookie": value})
            raise AssertionError("an old token's cookie still works")
        except Refused:
            pass

        # Gateway keys: shown once, kept hashed, constant-time, revocable; never a cookie's job.
        with tempfile.TemporaryDirectory() as tmp:
            real, KEYS = KEYS, pathlib.Path(tmp) / "gateway-keys.json"
            try:
                key = make_key("chat-ui")
                assert key.startswith("bwk_") and key not in KEYS.read_text(encoding="utf-8")
                assert gateway_keys()["chat-ui"]["sha256"] == hashlib.sha256(key.encode()).hexdigest()
                for bad in ("Bad Name", "x", "chat-ui"):                    # form, length, taken
                    try:
                        make_key(bad)
                        raise AssertionError(f"made a key named {bad!r}")
                    except ValueError:
                        pass
                other = make_key("agent")
                assert key_name(key) == "chat-ui" and key_name(other) == "agent"
                assert key_name("") is None and key_name(key + "x") is None and key_name("bwk_é") is None
                assert gateway_caller({"Authorization": f"Bearer {key}"}) == "chat-ui"
                assert gateway_caller({"Authorization": f"bearer  {key} "}) == "chat-ui"
                for headers in ({}, {"Authorization": key}, {"Authorization": f"Basic {key}"},
                                {"Cookie": value}, {"Authorization": "Bearer sk-dummy"}):
                    try:
                        gateway_caller(headers)
                        raise AssertionError(f"gateway let in {headers}")
                    except Refused as r:
                        assert r.status == 401
                try:                                                    # a key never opens the dashboard
                    admin_caller({"Authorization": f"Bearer {key}"})
                    raise AssertionError("a gateway key opened the dashboard")
                except Refused:
                    pass
                assert revoke_key("chat-ui") and not revoke_key("chat-ui") and key_name(key) is None
                assert key_name(other) == "agent"
                # No admin token: as before keys existed, whatever is sent.
                del os.environ["BENCHWARMERS_ADMIN_TOKEN"]
                assert gateway_caller({}) == "local" and gateway_caller({"Authorization": "Bearer x"}) == "local"
                assert admin_caller({}) == "local"
            finally:
                KEYS = real
        assert gateway_route("/v1/models") and gateway_route("/bench/verdict")
        assert not gateway_route("/api/endpoints") and not gateway_route("/v1") and not gateway_route("//v1/x")
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    print("ok")


if __name__ == "__main__":
    _selftest()
