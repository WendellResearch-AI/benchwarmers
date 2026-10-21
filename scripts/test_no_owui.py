"""Benchwarmers without Open WebUI, and the owner's install with it, against a fake server.

    python scripts/test_no_owui.py

Two installs in one temp data folder (BENCHWARMERS_HOME and LLM_USER_PATH are
set before anything is imported, so no real file is read or written):

1. A stranger whose only endpoint is an OpenRouter-like aggregator, the
   default. Registry, connections, onboarding candidates, the served check
   and auto-retire's plan all work, and no Open WebUI path is ever requested:
   owui.py, provider_tags.py and reprefix.py refuse or skip cleanly.
2. The owner's shape: endpoints.json with no `kind` anywhere, an `openwebui`
   default at a bare /api root with a `{path}` decisions passthrough, an
   OpenRouter endpoint and a local Ollama. Every Open WebUI path still runs.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
TMP = pathlib.Path(tempfile.mkdtemp(prefix="bench-no-owui-"))
os.environ["BENCHWARMERS_HOME"] = str(TMP)
os.environ["LLM_USER_PATH"] = str(TMP / "llm")
(TMP / "config").mkdir()
(TMP / "llm").mkdir()
sys.path[:0] = [str(ROOT), str(ROOT / "metrics"), str(ROOT / "suite"), str(ROOT / "scripts")]

SEEN: list[str] = []
LISTS = {
    "/v1/models": ["vendor/a:free", "vendor/b:free", "vendor/c", "vendor/d", "vendor/e", "vendor/f"],
    "/or/api/v1/models": ["vendor/x:free", "vendor/y"],
    "/ol/v1/models": ["llama-local"],
}
OWUI_MODELS = [{"id": "fast-1", "urlIdx": 0}, {"id": "llm7.codestral", "urlIdx": 1},
               {"id": "fresh-2", "urlIdx": 0}, {"id": "mercury-decide", "urlIdx": 1}]
OWUI_CONFIG = {"OPENAI_API_BASE_URLS": ["https://api.groq.com/openai/v1", "https://api.llm7.io/v1"],
               "OPENAI_API_KEYS": ["never-read-1", "never-read-2"]}


class Fake(BaseHTTPRequestHandler):
    def do_GET(self):                            # noqa: N802
        SEEN.append(self.path)
        if self.path in LISTS:
            return self._send({"data": [{"id": i} for i in LISTS[self.path]]})
        if self.path == "/api/models":
            return self._send({"data": OWUI_MODELS})
        if self.path == "/openai/config":
            return self._send(OWUI_CONFIG)
        self._send({}, 404)

    def do_POST(self):                           # noqa: N802
        SEEN.append(self.path)
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path == "/openai/decisions":                  # Open WebUI's passthrough
            return self._send({"answers": {"probe": {"answer": True}}})
        self._send({}, 404)

    def _send(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a):
        pass


def write(name: str, data) -> None:
    path = TMP / "config" / name
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def quietly(fn, *args):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        result = fn(*args)
    return result, out.getvalue()


def run_main(module, argv: list[str]):
    """A script's main() as its command line would run it: (exit, printed)."""
    saved, sys.argv = sys.argv, argv
    try:
        return quietly(module.main)
    except SystemExit as exc:
        return exc.code, ""
    finally:
        sys.argv = saved


def owui_calls() -> list[str]:
    return [p for p in SEEN if p.startswith(("/api/", "/openai/"))]


def stranger(fake: str) -> None:
    write("endpoints.json", {"openrouter": {"base": fake + "/v1", "key": "openrouter", "default": True,
                                            "kind": "aggregator"}})
    write("alias-history.json", "{}")
    import endpoints
    write("extra-openai-models.yaml", endpoints.yaml_block("a-free", "vendor/a:free")
          + endpoints.yaml_block("b-free", "vendor/b:free"))
    import route
    import serve
    import check_deprecations
    import auto_retire
    import owui
    import provider_tags
    import reprefix

    assert (endpoints.kind("openrouter"), endpoints.hub("openrouter")) == ("aggregator", "")
    assert endpoints.find_hub("openwebui") is None and not endpoints.recorded_whole("openrouter")
    assert {r["alias"]: r["endpoint"] for r in endpoints.registry()} == {"a-free": "openrouter", "b-free": "openrouter"}
    # No tags file at all: the endpoint is the connection.
    assert not (TMP / "config" / "provider-tags.json").exists()
    assert route.connection_of("a-free") == route.full_tag_of("b-free") == "openrouter"
    assert serve.tag_for("a-free") == "openrouter"

    # Onboarding candidates: what it lists minus what is registered; nothing badged new,
    # because the served check records only registered models here.
    c = serve.onboard_candidates()
    assert c["ok"] and not c["errors"], c
    assert sorted(x["served"] for x in c["candidates"]) == ["vendor/c", "vendor/d", "vendor/e", "vendor/f"], c
    assert {x["group"] for x in c["candidates"]} == {"openrouter (direct)"} and not any(x["new"] for x in c["candidates"])

    # The served check: the registered models only, not the aggregator's catalogue.
    assert check_deprecations.served() == {"vendor/a:free", "vendor/b:free"}
    code, out = run_main(check_deprecations, ["check_deprecations.py", "--dry-run"])
    assert code == 0 and "2 served, 2 registered" in out, (code, out)
    assert not (TMP / "config" / "served-snapshot.json").exists()          # a dry run writes nothing

    # Auto-retire groups by connection without tags: both models on the one
    # connection failing at once is held as the connection, not retired.
    import datetime as dt
    models = {"a-free": "vendor/a:free", "b-free": "vendor/b:free"}
    tags, on_owui = auto_retire.connections(models)
    assert tags == {"vendor/a:free": "openrouter", "vendor/b:free": "openrouter"} and on_owui == set(), tags
    today = dt.date(2026, 10, 21)
    rows = [{"alias": a, "date": today, "answered": False, "failure": "entitlement", "detail": "402"} for a in models]
    p = auto_retire.plan(today, models, set(models.values()), {}, rows, {}, {}, tags, on_owui)
    assert set(p["held"]) == {"openrouter"} and not p["confirm"], p
    gone = auto_retire.plan(today, {"a-free": "vendor/a:free"}, {"x.vendor/a:free"},
                            {"vendor/a:free": "2026-10-15"}, [], {}, {}, tags, on_owui)
    assert dict(gone["retire"])["a-free"].startswith("not served by its endpoint"), gone   # no prefix guess

    # Every Open WebUI-only path refuses or skips, and says why.
    try:
        owui.endpoint()
        raise AssertionError("owui found a hub that is not there")
    except ValueError as exc:
        assert "Open WebUI hub" in str(exc)
    r = serve.discover_add("groq", "vendor/z")
    assert not r["ok"] and "Open WebUI hub" in r["errors"][0], r
    _, out = quietly(provider_tags.main)
    assert "no Open WebUI hub" in out and not (TMP / "config" / "provider-tags.json").exists(), out
    code, _ = run_main(reprefix, ["reprefix.py", "groq", "--prefix", "g."])
    assert "Open WebUI" in str(code), code

    # Adding an endpoint: an explicit kind, a hub only with its type.
    assert not serve.save_endpoint("odd", fake + "/v1", "k", new=True, kind="cloud")["ok"]
    assert not serve.save_endpoint("odd", fake + "/v1", "k", new=True, kind="hub")["ok"]
    # Test (the form's button before Save) answers as Save would and writes nothing, the key included.
    t = serve.save_endpoint("tried", fake + "/v1", "k-tried", new=True, test_only=True)
    assert t["ok"] and "served" in t and "tried" not in endpoints.load() and "tried" not in endpoints.keys(), t
    assert serve.save_endpoint("plain", fake + "/v1", "k", new=True)["ok"]
    assert not serve.save_endpoint("plain", fake + "/v1", "k", new=True, test_only=True)["ok"]   # names checked in Test too
    assert serve.save_endpoint("mine", fake + "/v1", "k", new=True, kind="hub", hub="openwebui")["ok"]
    data = endpoints.load()
    assert data["plain"]["kind"] == "provider" and "hub" not in data["plain"], data["plain"]
    assert (data["mine"]["kind"], data["mine"]["hub"]) == ("hub", "openwebui"), data["mine"]
    assert serve.save_endpoint("mine", fake + "/v1", "k2")["ok"] and endpoints.hub("mine") == "openwebui"  # Change key keeps it
    rows = {e["name"]: e for e in serve.endpoint_list()["endpoints"]}
    assert (rows["openrouter"]["kind"], rows["mine"]["hub"]) == ("aggregator", "openwebui"), rows

    assert owui_calls() == [], owui_calls()


def owner(fake: str) -> None:
    """The owner's endpoints.json shape: no `kind` anywhere."""
    write("endpoints.json", {
        "openwebui": {"base": fake + "/api", "key": "openwebui", "default": True,
                      "decisions": fake + "/openai/{path}"},
        "openrouter": {"base": fake + "/or/api/v1", "key": "openrouter",
                       "headers": {"HTTP-Referer": "https://example.com", "X-Title": "Example"},
                       "unlisted_models": ["vendor/decide:free"]},
        "ollama": {"key": "ollama", "connect": {"provider": "ollama", "at": "2026-10-20T22:30:52-05:00"},
                   "base": fake + "/ol/v1"}})
    import endpoints
    write("extra-openai-models.yaml", endpoints.yaml_block("fast-1", "fast-1")
          + endpoints.yaml_block("codestral", "codestral")
          + endpoints.yaml_block("x-free", "vendor/x:free", "openrouter"))
    write("served-snapshot.json", {"last_seen": {"fast-1": "2026-10-20", "codestral": "2026-10-18"},
                                   "first_missing": {"codestral": "2026-10-19"}})
    import route
    import serve
    import check_deprecations
    import auto_retire
    import owui
    import provider_tags
    import reprefix
    import decide
    SEEN.clear()

    # Inferred from his real bases (a LAN address stands in as hub.example)...
    real = {"openwebui": {"base": "http://hub.example:4141/api"}, "openrouter": {"base": "https://openrouter.ai/api/v1"},
            "ollama": {"base": "http://localhost:11434/v1"}}
    kinds = {n: (endpoints.kind(n, real), endpoints.hub(n, real)) for n in real}
    assert kinds == {"openwebui": ("hub", "openwebui"), "openrouter": ("provider", ""), "ollama": ("local", "")}, kinds
    # ...and here, where every base is the fake on 127.0.0.1, the /api root still makes the one hub.
    assert [n for n in endpoints.load() if endpoints.hub(n)] == ["openwebui"]
    assert endpoints.find_hub("openwebui") == "openwebui" and endpoints.recorded_whole("openwebui")
    assert owui.endpoint() == "openwebui" and owui._call("/openai/config") == OWUI_CONFIG

    # Provider tags: Open WebUI's connections by host, the direct endpoint by name.
    _, out = quietly(provider_tags.main)
    tags = json.loads((TMP / "config" / "provider-tags.json").read_text(encoding="utf-8"))
    assert tags["fast-1"] == "groq" and tags["llm7.codestral"] == "llm7-api" and tags["vendor/x:free"] == "openrouter", tags
    assert "never-read" not in json.dumps(tags)
    assert route.connection_of("fast-1") == "groq" and route.connection_of("x-free") == "openrouter"

    # The default hub is recorded whole; the direct endpoint, its registered models only.
    assert check_deprecations.served() == {"fast-1", "llm7.codestral", "fresh-2", "mercury-decide",
                                           "vendor/x:free@openrouter"}
    c = serve.onboard_candidates()
    groups = {x["served"]: (x["group"], x["new"]) for x in c["candidates"]}
    assert groups["fresh-2"] == ("groq", True) and groups["vendor/y"] == ("openrouter (direct)", False), groups

    # Auto-retire: a prefix added in Open WebUI is a rename, said in Open WebUI's words.
    models = {"fast-1": "fast-1", "codestral": "codestral", "x-free": "vendor/x:free@openrouter"}
    conns, on_owui = auto_retire.connections(models)
    assert on_owui == {"fast-1", "codestral"} and conns["vendor/x:free@openrouter"] == "openrouter", (conns, on_owui)
    import datetime as dt
    p = auto_retire.plan(dt.date(2026, 10, 25), models, check_deprecations.served(),
                         {"codestral": "2026-10-19"}, [], {}, {}, conns, on_owui)
    assert "reprefix" in dict(p["pending"])["codestral"] and not p["retire"], p
    assert serve.model_status()["codestral"][2] == "missing from Open WebUI since 2026-10-19"
    code, out = run_main(reprefix, ["reprefix.py", "llm7-api", "--prefix", "llm7."])
    assert code is None and "codestral" in out and "llm7.codestral" in out, (code, out)

    # Decisions through Open WebUI's passthrough.
    assert decide.probe("openwebui", "mercury-decide") == (None, "decisions")
    hit = set(owui_calls())
    assert {"/openai/config", "/api/models", "/openai/decisions"} <= hit, hit


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    fake = f"http://127.0.0.1:{server.server_port}"
    try:
        stranger(fake)
        owner(fake)
    finally:
        server.shutdown()
        shutil.rmtree(TMP, ignore_errors=True)
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
