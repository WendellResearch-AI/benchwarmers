"""Where models are served from: Open WebUI, and any other OpenAI-compatible endpoint.

    python metrics/endpoints.py        # each endpoint, whether its key is set, what it serves

`config/endpoints.json` names each endpoint:

    {"openwebui":  {"base": "http://localhost:3000/api", "key": "openwebui", "default": true},
     "openrouter": {"base": "https://openrouter.ai/api/v1", "key": "openrouter",
                    "headers": {"HTTP-Referer": "https://example.com", "X-Title": "My app"}}}

`key` is the NAME of a key in the llm CLI's own `keys.json` -- the same store
`llm keys set <name>` writes. The value never enters this repository.

Until 2026-10-15 the Open WebUI address was typed into seven files, and a model
from anywhere else needed a hand-built bypass (`direct-endpoints.json`, for the
inkling pair). Now every registration carries its endpoint's base and key name
in the llm yaml, so `llm -m` reaches any of them the same way, and everything
that asks "what is served" asks every endpoint here.

`decisions` (optional) is the full URL of the endpoint's Decisions API, for
decision models that cannot be reached through chat completions; see
`metrics/decide.py`. Open WebUI's is its `/openai/{path}` passthrough, which
answers only when the server runs with ENABLE_OPENAI_API_PASSTHROUGH=True;
`{path}` is filled per model (`decisions` for Inception, `systemone` for
Ollama), because the passthrough appends it to the connection's own base.

`unlisted_models` (optional) are ids the endpoint serves but leaves out of its
/models list, so the picker and onboarding can still see them.

`headers` go into every yaml entry registered on that endpoint. They exist for
OpenRouter's attribution gate (some free models answer only named apps), and
must describe the client that really makes the requests.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
FILE = DATA / "config" / "endpoints.json"
YAML = DATA / "config" / "extra-openai-models.yaml"
def llm_dir() -> pathlib.Path:
    """The llm CLI's own folder (its keys.json, model registry, options and log),
    as llm finds it: LLM_USER_PATH, else its per-user folder on this OS. Every
    module asks here; until 2026-10-20 four of them spelled out the Windows
    path, so none of them worked on macOS or Linux."""
    if env := os.environ.get("LLM_USER_PATH"):
        return pathlib.Path(env)
    if os.name == "nt":
        return pathlib.Path(os.environ.get("APPDATA") or pathlib.Path.home()) / "io.datasette.llm"
    if sys.platform == "darwin":
        return pathlib.Path.home() / "Library" / "Application Support" / "io.datasette.llm"
    return pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or pathlib.Path.home() / ".config") / "io.datasette.llm"


LLM_DIR = llm_dir()
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")


def load() -> dict[str, dict]:
    """Every endpoint; {} on a fresh install (no file yet, or one naming none),
    so the dashboard can render and offer to add the first. A file that cannot
    be read still raises: a writer handed {} for it would save over it.

    Never a fallback address: until 2026-10-19 a missing or broken file
    silently meant the owner's own Open WebUI on his LAN, which on anyone
    else's machine is a timeout with no explanation. Whatever needs an
    endpoint asks default() (base, key, models and yaml_block all do), and
    that refuses, so a job run before any endpoint exists says what to do."""
    try:
        data = json.loads(FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"{FILE} cannot be read: {exc}") from None
    if not isinstance(data, dict):
        raise RuntimeError(f"{FILE} is not a JSON object of endpoints")
    return data


def connections() -> dict:
    """config/connections.json: which connections are paid for, which break
    ties, names for bare-IP hosts, models.dev ids. {} when absent: a fresh
    install has no such facts, and none are assumed."""
    try:
        data = json.loads((DATA / "config" / "connections.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def default() -> str:
    data = load()
    if not data:
        raise RuntimeError(f"no endpoints configured: add one from the dashboard (python serve.py), run"
                           f" scripts/init.py --endpoint NAME BASE_URL, or create {FILE}"
                           ' like {"name": {"base": "https://.../v1", "key": "name", "default": true}}')
    return next((n for n, e in data.items() if e.get("default")), next(iter(data)))


def base(name: str | None = None) -> str:
    return load()[name or default()]["base"].rstrip("/")


ENV_KEY = "BENCHWARMERS_KEY_"


def _stored() -> dict:
    try:
        return json.loads((LLM_DIR / "keys.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def keys() -> dict:
    """Every key by name: BENCHWARMERS_KEY_<NAME> from the environment first, so
    a container needs no key store, then the llm CLI's keys.json. <NAME> is the
    key name upper-cased with '-' as '_' (BENCHWARMERS_KEY_OPENROUTER_2 for
    openrouter-2). ponytail: a hand-written key name holding '_' or '.' cannot
    come from the environment; every name the dashboard makes can."""
    env = {var[len(ENV_KEY):].lower().replace("_", "-"): value
           for var, value in os.environ.items() if var.upper().startswith(ENV_KEY) and var[len(ENV_KEY):] and value}
    return {**_stored(), **env}


def key(name: str | None = None) -> str:
    return keys().get(load()[name or default()].get("key", ""), "")


def set_key(key_name: str, value: str) -> None:
    """Store a key where `llm keys set` would. Atomic; other keys untouched,
    and never one from the environment."""
    data = _stored()
    data[key_name] = value
    import locked                                  # creates llm's folder: a container's store starts empty
    locked.write_atomic(LLM_DIR / "keys.json", json.dumps(data, indent=2) + "\n")


def models(name: str | None = None, timeout: int = 30) -> dict[str, dict]:
    """served id -> its /models entry. Raises if the endpoint cannot be asked.

    Plus the endpoint's `unlisted_models`: ids it serves but leaves out of
    /models. OpenRouter lists none of its decision models (Mercury Decide,
    Respan), the same reason they had to be typed into Open WebUI's connection
    by hand. They are taken as served on the config's word, so availability
    cannot notice one going away."""
    entry = load()[name or default()]
    import connect                                 # here: connect imports this module
    connect.fresh(entry.get("key", ""))            # an expiring sign-in renewed first; a failure shows as a 401
    live = list_models(entry, key(name), timeout)
    return {**{i: {"id": i} for i in entry.get("unlisted_models", [])}, **live}


def list_models(entry: dict, secret: str = "", timeout: int = 30) -> dict[str, dict]:
    """What an endpoint entry lists, saved or not yet (the dashboard tests one
    before saving it). `models_url` when the list lives elsewhere (GitHub
    Models: its catalog, a bare JSON list); entries marked `visibility` other
    than "list" are left out (ChatGPT's plan lists ones it will not serve)."""
    headers = dict(entry.get("headers") or {})
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    url = entry.get("models_url") or entry["base"].rstrip("/") + "/models"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as response:
        data = json.load(response)
    rows = data.get("data", []) if isinstance(data, dict) else data
    return {e["id"]: e for e in rows
            if isinstance(e, dict) and e.get("id") and e.get("visibility", "list") == "list"}


def entry_of(alias: str) -> dict:
    """The endpoints.json entry an alias is registered on, or {}."""
    data = load()
    name = endpoint_name(registrations().get(alias) or {}, data)
    return data.get(name, {}) if name else {}


def endpoint_name(reg: dict, data: dict | None = None) -> str | None:
    """The endpoint a registration is on, by base AND key name: one provider
    can be two endpoints (a pasted key and a sign-in, or two accounts) on the
    same base, but each endpoint has its own key. `data`: load(), if in hand."""
    data = load() if data is None else data
    want = (reg.get("api_base") or "").rstrip("/")
    same = [n for n, e in data.items() if e["base"].rstrip("/") == want]
    return next((n for n in same if data[n].get("key") == reg.get("api_key_name")), same[0] if same else None)


def registrations() -> dict[str, dict]:
    """alias -> its yaml entry as written by yaml_block: model_name, api_base,
    api_key_name and a `headers` dict. Not a YAML parser; the file is ours."""
    out: dict[str, dict] = {}
    current = None
    if not YAML.exists():                          # a fresh install has registered nothing yet
        return out
    for line in YAML.read_text(encoding="utf-8").splitlines():
        if (m := re.match(r"\s*-\s*model_id:\s*(.+)", line)):
            current = out.setdefault(m.group(1).strip().strip("\"'"), {"headers": {}})
        elif current is None or not line.strip() or line.lstrip().startswith("#"):
            continue
        elif (m := re.match(r"    (\S[^:]*):\s*(.+)", line)):          # under headers:
            current["headers"][m.group(1)] = m.group(2).strip().strip("\"'")
        elif (m := re.match(r"  (\w+):\s*(.*)", line)) and m.group(1) != "headers":
            current[m.group(1)] = m.group(2).strip().strip("\"'")
    return out


def registry() -> list[dict]:
    """Every yaml registration: alias, served name, and the endpoint it is on."""
    data = load()
    return [{"alias": a, "served": r.get("model_name", ""), "endpoint": endpoint_name(r, data) or "?"}
            for a, r in registrations().items()]


def snapshot_key(served: str, endpoint: str) -> str:
    """How availability records name a model: bare on the default endpoint
    (every record before 2026-10-15), `served@endpoint` elsewhere, because the
    same id can be served by two endpoints and they can go away separately."""
    return served if endpoint == default() else f"{served}@{endpoint}"


def yaml_block(alias: str, served: str, endpoint: str | None = None) -> str:
    entry = load()[endpoint or default()]
    quoted = f'"{served}"' if any(c in served for c in ":/@") else served
    lines = [f"- model_id: {alias}", f"  model_name: {quoted}",
             f'  api_base: "{entry["base"]}"', f"  api_key_name: {entry['key']}"]
    if entry.get("headers"):
        lines.append("  headers:")
        lines += [f'    {k}: "{v}"' for k, v in entry["headers"].items()]
    return "\n" + "\n".join(lines) + "\n"


def main() -> None:
    for name, entry in load().items():
        try:
            n = f"{len(models(name))} served"
        except Exception as exc:                   # noqa: BLE001
            n = f"unreachable ({type(exc).__name__})"
        mark = " (default)" if name == default() else ""
        print(f"  {name}{mark}: {entry['base']}  key {'set' if key(name) else 'MISSING'}  {n}")


def _selftest() -> None:
    """No endpoint means a refusal where one is needed, not someone's LAN; the
    yaml parser keeps headers."""
    import tempfile
    global FILE, YAML, LLM_DIR
    real = FILE, YAML, LLM_DIR
    with tempfile.TemporaryDirectory() as tmp:
        FILE, YAML = pathlib.Path(tmp) / "endpoints.json", pathlib.Path(tmp) / "models.yaml"
        LLM_DIR = pathlib.Path(tmp) / "llm"                # absent: set_key makes it
        try:
            set_key("or-2", "stored")
            os.environ[ENV_KEY + "OR_2"], os.environ[ENV_KEY + "NEW"] = "from-env", "env-only"
            assert keys()["or-2"] == "from-env" and keys()["new"] == "env-only"
            set_key("other", "x")                          # the environment's never reach the file
            assert _stored() == {"or-2": "stored", "other": "x"}, _stored()
        finally:
            os.environ.pop(ENV_KEY + "OR_2", None), os.environ.pop(ENV_KEY + "NEW", None)
        try:
            for content in (None, "{}", "not json", "[]"):
                if content is not None:
                    FILE.write_text(content, encoding="utf-8")
                fresh = content in (None, "{}")           # a fresh install: nothing yet, not damage
                try:
                    assert load() == {} and fresh, f"load() accepted {content!r}"
                except RuntimeError:
                    assert not fresh, f"load() refused {content!r}"
                try:
                    default()
                    raise AssertionError(f"default() accepted {content!r}")
                except RuntimeError:
                    pass
            FILE.unlink()
            assert registry() == [], "no yaml, no endpoints: nothing registered"
            FILE.write_text('{"or": {"base": "https://x.example/v1/", "key": "or"}}', encoding="utf-8")
            YAML.write_text(yaml_block("m1", "v/m:free", "or").replace(
                "  api_key_name: or", '  api_key_name: or\n  headers:\n    X-Title: "t"'), encoding="utf-8")
            r = registrations()["m1"]
            assert (r["model_name"], r["api_key_name"], r["headers"]) == ("v/m:free", "or", {"X-Title": "t"}), r
            assert registry() == [{"alias": "m1", "served": "v/m:free", "endpoint": "or"}], registry()
            assert entry_of("m1")["key"] == "or" and entry_of("nobody") == {}
            # One provider, two endpoints on one base: each model stays with its own.
            FILE.write_text('{"or": {"base": "https://x.example/v1/", "key": "or"},'
                            ' "or-2": {"base": "https://x.example/v1", "key": "or-2"}}', encoding="utf-8")
            YAML.write_text(yaml_block("m1", "a", "or") + yaml_block("m2", "a", "or-2"), encoding="utf-8")
            assert {r["alias"]: r["endpoint"] for r in registry()} == {"m1": "or", "m2": "or-2"}, registry()
            assert entry_of("m2")["key"] == "or-2"
            # A list as an object or a bare list (GitHub's catalog); hidden ones left out.
            listing = pathlib.Path(tmp) / "models.json"
            for body in ('{"data": [{"id": "a"}, {"id": "b", "visibility": "hide"}]}', '[{"id": "a"}]'):
                listing.write_text(body, encoding="utf-8")
                assert list(list_models({"base": "x", "models_url": listing.as_uri()})) == ["a"], body
        finally:
            FILE, YAML, LLM_DIR = real
    print("ok")


if __name__ == "__main__":
    _selftest() if "--selftest" in sys.argv else main()
