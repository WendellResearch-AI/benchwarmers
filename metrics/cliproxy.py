"""The CLI Proxy API hub adapter: a model added to one of its openai-compatibility providers.

    python metrics/cliproxy.py <provider> <model id>     # what would change, changes nothing

CLI Proxy API reaches OpenAI-compatible upstreams through the
`openai-compatibility` list in its config.yaml: a provider each, with a name,
a base-url, its upstream keys and a `models` list of {name, alias}. Only
listed models are served, as the alias (else the name), and also as
`<prefix>/<alias>` when the provider has a prefix. A provider is a
CONNECTION here, by its name. Adding a model is one PATCH of that provider's
`models` through the management API, which saves config.yaml and reloads
itself. The PATCH carries the models list and nothing else, so no upstream
key is ever sent back (the GET does return them; nothing here prints one).

The list is REPLACED WHOLE, so it is read, merged and written: read again
just before the PATCH and read back after. Anything in the list sent that is
missing afterwards means something else wrote it meanwhile, and this stops
and says so. ponytail: an edit landing between that last read and the PATCH
is still lost, the read-modify-write ceiling owui.py notes; CLI Proxy API has
no version field to make the write conditional.

The management key is not one of its API keys: it is the endpoint's admin key
(endpoints.admin_key, with no fallback to the API key). CLI Proxy API bans an
address for 30 minutes after 5 refused keys, so nothing here retries a
refusal, and without a stored key nothing is sent at all.

Its sign-ins (OAuth credentials: Antigravity, Codex) are read for Quota
management the way its own console reads them: `credentials` lists them and
`requests/api-call` makes one sign-in's request to its provider's quota
endpoint with the sign-in's token (sign_ins, api_call; quotaview.read_signins
is the flow). Same management key.

Checked against CLI Proxy API 8.0.36's source (2026-10-21):
internal/api/server_management.go (GET and PATCH
/v0/management/openai-compatibility), internal/api/handlers/management/
config_lists.go (PATCH {"name", "value": {"models": [...]}} replaces only the
fields given; the reply is {"status": "ok"}), handler.go (Authorization:
Bearer or X-Management-Key; the X-CPA-VERSION header; 401 for a wrong key,
403 "remote management disabled" from another machine unless
remote-management.allow-remote is true), server_management.go again (every
management route 404s while remote-management.secret-key is unset).
"""

from __future__ import annotations

import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "metrics"))
import endpoints  # noqa: E402

# What this hub takes (RELEASE-PLAN section 2, "Adding a model to a hub"); "" where it has none.
SUPPORTS = {"allow_list": "models list", "tags": "", "logo": "", "access": "",
            "link": "/management.html#/ai-providers"}
CHECKED = ("8.0",)
PATH = "/v0/management/openai-compatibility"
CONSOLE = ("/v8/management", "/v0/management")   # the sign-ins' routes: the console's prefix, then the older one


def endpoint() -> str:
    """The CLI Proxy API hub's endpoint name; ValueError when there is none."""
    name = endpoints.find_hub("cliproxy")
    if not name:
        raise ValueError("no endpoint is a CLI Proxy API hub; add it on the Endpoints page as kind hub,"
                         " CLI Proxy API")
    return name


def _secret(name: str) -> str:
    """The management key; ValueError, before anything is sent, when it is not stored."""
    secret = endpoints.admin_key(name, fallback=False)
    if not secret:
        env = endpoints.ENV_KEY + endpoints.admin_name(endpoints.load()[name]).upper().replace("-", "_")
        raise ValueError(f"CLI Proxy API's management key is not stored: give it as the admin key on the"
                         f" Endpoints page (Change key on {name}), or set {env}")
    return secret


def _call(method: str = "GET", body: dict | None = None) -> tuple[dict, str]:
    """(reply, X-CPA-VERSION) from the openai-compatibility route."""
    name = endpoint()
    secret = _secret(name)
    req = urllib.request.Request(endpoints.base(name).removesuffix("/v1") + PATH, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json",
                                          "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15 if body is None else 60) as r:
            return json.loads(r.read() or b"null") or {}, r.headers.get("X-CPA-VERSION", "")
    except urllib.error.HTTPError as exc:
        raise ValueError(_refusal(exc)) from None


def _refusal(exc: urllib.error.HTTPError) -> str:
    """What to change, from a refused management call."""
    try:
        said = str(json.loads(exc.read()).get("error") or "")[:200]
    except (ValueError, AttributeError):
        said = ""
    if exc.code == 404 and said == "item not found":   # the PATCH's: no provider of that name now
        return "the provider left CLI Proxy API while this ran; nothing changed"
    if exc.code == 404:
        return ("CLI Proxy API's management API is off: set remote-management.secret-key in its config.yaml"
                " (every management route answers 404 without one)")
    if exc.code == 403 and "remote management disabled" in said:
        return ("CLI Proxy API refuses management from another machine: set remote-management.allow-remote:"
                " true in its config.yaml")
    if exc.code == 401:
        return (f"CLI Proxy API refused the management key ({said}); it bans an address for 30 minutes after"
                " 5 refusals, so check the key before trying again")
    return f"CLI Proxy API answered {exc.code}: {said}"


def _providers() -> tuple[list[dict], str]:
    data, release = _call()
    return data.get("openai-compatibility") or [], release


def _named(entries: list[dict], connection: str) -> dict | None:
    return next((e for e in entries if e.get("name") == connection), None)


def _alias(m: dict) -> str:
    """What a listed model is served as: its alias, else its name."""
    return str(m.get("alias") or m.get("name") or "").strip()


def upstreams() -> dict[str, tuple[str, str]]:
    """Provider -> (base URL, the prefix its served ids take, "" for none).
    What Discover matches a provider against (discover.hub_routes)."""
    return {e["name"]: (str(e.get("base-url") or "").rstrip("/"), str(e.get("prefix") or ""))
            for e in _providers()[0] if e.get("name") and not e.get("disabled")}


def _console(path: str, body: dict | None = None) -> dict:
    """One sign-ins' management call (moved from quotaview, 2026-11-20): the
    console's prefix first, the older on a 404. A 401 is never retried."""
    name = endpoint()
    head = {"Authorization": f"Bearer {_secret(name)}", "Content-Type": "application/json",
            "Accept": "application/json"}
    for prefix in CONSOLE:
        req = urllib.request.Request(endpoints.base(name).removesuffix("/v1") + prefix + path, headers=head,
                                     data=None if body is None else json.dumps(body).encode())
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read() or b"null") or {}
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and prefix != CONSOLE[-1]:
                continue
            if exc.code == 401:
                raise ValueError("CLI Proxy API refused the management key; check it before trying again"
                                 " (5 refusals ban this address for 30 minutes)") from None
            if exc.code == 404:
                raise ValueError("CLI Proxy API's management API is off (every route 404s without a secret"
                                 " key)") from None
            raise ValueError(f"CLI Proxy API answered {exc.code} on {path}") from None


def sign_ins() -> list[dict]:
    """Its sign-ins, in the bench's shape: {provider, auth_index, disabled,
    account (a Codex sign-in's ChatGPT account id), project (Antigravity's)}."""
    return [{"provider": str(c.get("provider") or ""), "auth_index": c.get("auth_index"),
             "disabled": bool(c.get("disabled")), "account": (c.get("id_token") or {}).get("chatgpt_account_id"),
             "project": c.get("project_id") or ""}
            for c in _console("/credentials").get("files") or [] if isinstance(c, dict)]


def api_call(auth_index, method: str, url: str, header: dict, data: str | None = None) -> dict:
    """One sign-in's own request, made by the proxy with the sign-in's token
    (`$TOKEN$` in `header`): {status_code, body}."""
    return _console("/requests/api-call", {"auth_index": auth_index, "method": method, "url": url,
                                           "header": header, **({"data": data} if data is not None else {})})


def plan(connection: str, model_id: str) -> dict:
    """What adding would change. Writes nothing; ValueError when it cannot be done here."""
    entries, release = _providers()
    entry = _named(entries, connection)
    if entry is None:
        raise ValueError(f"CLI Proxy API has no openai-compatibility provider named {connection!r}")
    if entry.get("disabled"):
        raise ValueError(f"the {connection} provider is switched off in CLI Proxy API")
    prefix = str(entry.get("prefix") or "")
    # One id on two providers: CLI Proxy API would share its calls between them.
    # A prefixed provider serves the bare alias too (unless force-model-prefix),
    # so prefixes are not compared. ponytail: an OAuth model of the same id is not.
    elsewhere = next((e.get("name") for e in entries if e is not entry and not e.get("disabled")
                      and any(_alias(m) == model_id for m in e.get("models") or [])), None)
    if elsewhere:
        raise ValueError(f"CLI Proxy API's {elsewhere} provider already serves {model_id}; listing it on"
                         f" {connection} too would share its calls between them")
    models = entry.get("models") or []
    return {"connection": connection, "base_url": entry.get("base-url"), "model_id": model_id,
            "served": f"{prefix}/{model_id}" if prefix else model_id,
            "add_to_list": not any(_alias(m) == model_id for m in models), "listed": len(models),
            "version": release, "version_note": endpoints.version_note("cliproxy", release, CHECKED)}


def add(connection: str, model_id: str) -> tuple[str, list[str]]:
    """Do it. (served id, what was done)."""
    p = plan(connection, model_id)
    done = [p["version_note"]] if p["version_note"] else []
    entry = _named(_providers()[0], connection)        # again, just before the write: it decides, not plan()
    if entry is None:
        raise ValueError(f"the {connection} provider left CLI Proxy API while this ran; nothing changed")
    sent = list(entry.get("models") or [])
    if any(_alias(m) == model_id for m in sent):
        done.append(f"the {connection} provider already lists {model_id}; nothing changed")
    else:
        sent.append({"name": model_id, "alias": model_id})
        _call("PATCH", {"name": connection, "value": {"models": sent}})
        back = (_named(_providers()[0], connection) or {}).get("models") or []
        have = {(m.get("name"), m.get("alias")) for m in back}
        lost = [_alias(m) for m in sent if (m.get("name"), m.get("alias")) not in have]
        if lost:
            raise ValueError(f"CLI Proxy API's {connection} list changed while this wrote it: {', '.join(lost)}"
                             " missing from it now, so something else edited it at the same moment; look at its"
                             f" providers page ({endpoints.base(endpoint()).removesuffix('/v1')}{SUPPORTS['link']})"
                             " before adding again")
        done.append(f"added {model_id} to the {connection} provider's models ({len(sent)} listed);"
                    " CLI Proxy API reloads by itself")
    for _ in range(10):                        # the reload runs after the reply
        if p["served"] in endpoints.models(endpoint()):
            break
        time.sleep(2)
    else:
        done.append(f"CLI Proxy API does not list {p['served']} yet; onboarding will say if it never does")
    return p["served"], done


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    print(json.dumps(plan(sys.argv[1], sys.argv[2]), indent=2))


if __name__ == "__main__":
    main()
