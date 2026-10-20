"""One request check for both servers, the dashboard (serve.py) and the gateway.

    python metrics/guard.py      # self-check

Both bind to 127.0.0.1, which keeps other machines out but not other web
pages: any site open in the browser can send requests to localhost.

- **Host allow-list, on every request.** DNS rebinding points a site's own
  name at 127.0.0.1, so the browser treats our replies as that site's and
  lets its script read them (prompts and answers, endpoints, keys' names).
  The Host header still carries the site's name, so only our own names, port
  included, are answered. More (a Docker service name, a proxy) come from
  BENCH_ALLOWED_HOSTS, comma-separated host:port.
- **On POST, JSON only and never cross-site.** A page can send a plain form
  or text/plain to us without asking, but not application/json: that needs
  a CORS preflight neither server answers. A browser also stamps
  Sec-Fetch-Site and Origin, which a page cannot remove; cross-site or a
  foreign Origin is refused. API clients (SDKs, curl, agents, Open WebUI's
  backend) send neither and are unaffected. BENCH_ALLOWED_ORIGINS adds a
  browser UI on another port.
- **A body-size cap**, so one request cannot make a server read gigabytes.
"""

from __future__ import annotations

import os


class Refused(Exception):
    """The request is not served. `status` is the HTTP code to answer with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _env(name: str) -> set[str]:
    return {v.strip().lower() for v in os.environ.get(name, "").split(",") if v.strip()}


def hosts(port: int) -> set[str]:
    return {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"} | _env("BENCH_ALLOWED_HOSTS")


def check(headers, port: int, post: bool, max_body: int) -> int:
    """Raise Refused, or return how many body bytes to read (0 for a GET)."""
    host = (headers.get("Host") or "").strip().lower()
    if host not in hosts(port):
        raise Refused(403, f"unknown Host {host or '(none)'!r}; set BENCH_ALLOWED_HOSTS to add one")
    if not post:
        return 0
    if (headers.get("Sec-Fetch-Site") or "").lower() == "cross-site":
        raise Refused(403, "cross-site requests are refused")
    origin = (headers.get("Origin") or "").strip().lower()
    if origin and origin not in {f"http://{h}" for h in hosts(port)} | _env("BENCH_ALLOWED_ORIGINS"):
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


def _selftest() -> None:
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
    os.environ["BENCH_ALLOWED_HOSTS"] = "bench:8123"
    os.environ["BENCH_ALLOWED_ORIGINS"] = "http://localhost:3000"
    try:
        assert status(Host="bench:8123") == 0
        assert status(Origin="http://localhost:3000") == 0
    finally:
        del os.environ["BENCH_ALLOWED_HOSTS"], os.environ["BENCH_ALLOWED_ORIGINS"]
    print("ok")


if __name__ == "__main__":
    _selftest()
