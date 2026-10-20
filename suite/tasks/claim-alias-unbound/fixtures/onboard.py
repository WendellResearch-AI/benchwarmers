"""Take a model from "added to Open WebUI" to "usable, with evidence".

    python scripts/onboard.py "vendor/new-model:free"
    python scripts/onboard.py "vendor/new-model:free" --skip-suite

Models arrive every few days. Without a process each one is registered by hand,
half-tested, and then either trusted on no evidence or forgotten — and the
router cannot recommend what it has no rows for. This does the whole sequence
in one pass and writes down what it found.

WHAT IT DOES, AND WHY IN THIS ORDER
-----------------------------------
1. **Confirm the endpoint serves it.** Fails immediately if not, because every
   later step would otherwise produce a confusing failure instead of a clear
   one. `gpt-oss-120b` was registered under a name the endpoint never served
   and spent a whole 16-model suite run scoring 0/11 before anyone noticed.
2. **Derive the alias** by the standing rule: drop the vendor prefix (the
   connection tag already carries it), turn `:` into `-`. So
   `poolside/laguna-s-2.1:free` becomes `laguna-s-2.1-free`. Version numbers
   stay, because there will be a 2.2.
3. **Register and sync**, then refresh the provider tags so the connection is
   known — which is what the router uses to keep a panel independent.
4. **Probe availability** before spending anything: two prompt sizes, two
   samples. A model that cannot answer "ready" will not answer a 46 KB task,
   and finding that out here costs four calls instead of six large ones.
5. **Run the internal suite**, the only tier entitled to rank. Not the floor
   tier: 164 calls for a contaminated number that cannot rank anyway.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
Judge. It reports what happened and where the model lands; whether to route
work there is a decision with a person in it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import subprocess
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
YAML = ROOT / "config" / "extra-openai-models.yaml"
PY = ROOT / ".venv" / "Scripts" / "python.exe"
sys.path.insert(0, str(ROOT / "metrics"))
import endpoints  # noqa: E402
import decide  # noqa: E402


def alias_for(served_name: str, taken: dict[str, str] | None = None,
              strip_prefix: str = "") -> str:
    """The standing naming rule, plus disambiguation when it collides.

    `codestral-latest` is served by BOTH mistral and llm7-api, and the rule
    alone gives them the same alias — which would silently overwrite one
    registration with the other and mix two different endpoints' results into
    one row. When the name is already taken by a DIFFERENT served model, the
    connection is appended: `codestral-latest-llm7-api`.

    `strip_prefix` removes an Open WebUI connection prefix before the rule is
    applied, so `llm7.codestral-latest` is recognised as the same MODEL as
    mistral's and gets the collision suffix, rather than sailing past the check
    as a unique name and landing as the alias `llm7.codestral-latest`. It is
    explicit rather than sniffed, because a leading dotted segment is not
    reliably a prefix — `gpt-3.5-turbo` would lose its version to a guess.
    """
    if strip_prefix and served_name.startswith(strip_prefix):
        served_name_stripped = served_name[len(strip_prefix):]
    else:
        served_name_stripped = served_name
    base = served_name_stripped.split("/")[-1].replace(":", "-")
    if not taken or taken.get(base) in (None, served_name):
        return base
    tags = json.loads((ROOT / "config" / "provider-tags.json")
                      .read_text(encoding="utf-8"))
    tag = (tags.get(served_name) or "alt").split("/")[0]
    return f"{base}-{tag}"


def existing_aliases(include_retired: bool = True) -> dict[str, str]:
    """alias -> the served name it is already pointing at.

    Retired aliases count as taken: a retired name is never reused, because
    the run notes and perf history still say it and would merge two models.
    """
    import re
    retired = json.loads((ROOT / "config" / "retired.json").read_text(encoding="utf-8")
                         or "{}") if include_retired and (ROOT / "config" / "retired.json").exists() else {}
    out, current = {a: r.get("served", "") for a, r in retired.items()}, None
    out.update({a: d["served"] for a, d in decide.load().items()})
    for line in YAML.read_text(encoding="utf-8").splitlines():
        if (m := re.match(r"\s*-?\s*model_id:\s*(.+)", line)):
            current = m.group(1).strip().strip("\"'")
        elif (m := re.match(r"\s*model_name:\s*(.+)", line)):
            out[current] = m.group(1).strip().strip("\"'")
    return out


def served_models(endpoint: str | None = None) -> dict[str, dict]:
    """served id -> its /models entry, from any endpoint in config/endpoints.json."""
    return endpoints.models(endpoint)


def connection_prefix(entry: dict) -> str:
    """The prefix Open WebUI put in front of the upstream id, or "".

    Open WebUI keeps the upstream's own id at `openai.openai.id`, so
    `llm7.codestral-latest` carries `codestral-latest` and the prefix is the
    difference -- read, not typed by hand, and not guessed from a dot
    (`gpt-3.5-turbo` has one and no prefix). A plain OpenAI-compatible endpoint
    has no such field and adds no prefix, so "" is right there too.
    """
    served = entry.get("id", "")
    upstream = ((entry.get("openai") or {}).get("openai") or {}).get("id", "")
    if upstream and served != upstream and served.endswith(upstream):
        return served[:-len(upstream)]
    return ""


def already_registered(served_name: str, endpoint: str) -> str | None:
    # Exact match against the parsed model_name VALUES, not a substring check
    # against the raw text. A substring check false-positives whenever one
    # served name is a prefix of another already registered -- `mercury-2`
    # against a file that already holds `model_name: mercury-2.5` reads as
    # "already registered" and silently skips writing the real entry, which
    # then fails every later step for a reason that looks like the model
    # being broken instead of never having been registered at all.
    # Per endpoint: the same id on a second endpoint is a second model.
    return served_name if any(r["served"] == served_name and r["endpoint"] == endpoint
                              for r in endpoints.registry()) else None


def register(alias: str, served_name: str, endpoint: str | None = None) -> None:
    with YAML.open("a", encoding="utf-8") as fh:
        fh.write(endpoints.yaml_block(alias, served_name, endpoint))


def run(*args: str, timeout: int = 3600) -> subprocess.CompletedProcess:
    return subprocess.run([str(PY), *args], cwd=str(ROOT), timeout=timeout,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def onboard_decision(name: str, endpoint: str, via: str, alias: str) -> None:
    """Record a decision model: where its Decisions API answers and which
    question types it takes. Same five step markers, so the dashboard modal
    follows it like any other onboarding."""
    print(f"  [2/5] decision model: answers through {via}'s Decisions API, not chat")
    types = decide.question_types(via, name)
    print(f"  [3/5] question types: {', '.join(types)}")
    data = decide.load()
    data[alias] = {"served": name, "endpoint": endpoint, "via": via, "question_types": types,
                   "onboarded": dt.date.today().isoformat()}
    decide.FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    roles_path = ROOT / "config" / "model-roles.json"
    roles = json.loads(roles_path.read_text(encoding="utf-8"))
    roles.setdefault("models", {})[alias] = "decision"
    roles_path.write_text(json.dumps(roles, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"  [4/5] recorded as {alias} in config/decision-models.json, role decision")
    print("  [5/5] no chat probe or suite: they cannot test a decision model."
          f" Ask it with: python metrics/decide.py {alias} \"<text>\" --noul \"<question>\"")
    stamp = dt.date.today().isoformat()
    note = ROOT / "runs" / f"onboard-{stamp}.md"
    with note.open("a", encoding="utf-8") as fh:
        fh.write(f"\n## Onboarded `{alias}` ({name}) — {stamp}\n\n"
                 f"Decision model, found on {endpoint}, answering through {via}'s Decisions API."
                 f" Question types: {', '.join(types)}. Not in the llm"
                 f" yaml and never a panel seat; its test is agreement with known verdicts.\n")
    print(f"\nwrote {note}")


def main() -> None:
    # route.py's panel contains em dashes, and this script inherits Windows'
    # cp1252 stdout when run from a scheduled task or a plain console. Without
    # this the run completes, prints most of its report, and then dies on the
    # last few lines BEFORE writing the onboarding note -- losing the record of
    # a run that actually succeeded.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("served_name", help="the id the endpoint serves it under")
    ap.add_argument("--endpoint", default=None,
                    help="a name from config/endpoints.json; Open WebUI by default")
    ap.add_argument("--skip-suite", action="store_true",
                    help="register and probe only")
    args = ap.parse_args()

    name = args.served_name
    endpoint = args.endpoint or endpoints.default()
    # 1. served?
    try:
        live = served_models(endpoint)
    except Exception as exc:                       # noqa: BLE001
        sys.exit(f"cannot reach the {endpoint} endpoint: {exc}")
    alias = alias_for(name, existing_aliases(),
                      connection_prefix(live.get(name, {})))
    print(f"onboarding {name} from {endpoint}  ->  alias {alias}\n")
    if name not in live:
        near = [s for s in live if alias.split("-")[0] in s][:5]
        sys.exit(f"{name} is not served.\n"
                 f"  Did you mean: {', '.join(near) if near else '(nothing similar)'}\n"
                 f"  Add it to {endpoint} first — registering a name the "
                 f"endpoint does not serve is how a model scores 0/11 for a "
                 f"fortnight before anyone looks.")
    print("  [1/5] served by the endpoint")

    # A decision model refuses chat, so registering it in the llm yaml, the
    # chat probe and the suite would only record failures against it. Some
    # listings say so (Inception's: supported_endpoints ["decisions"]); others
    # (OpenRouter's, via Open WebUI) say nothing, and then the chat refusal does.
    listed = live[name].get("supported_endpoints") or []
    decision_only = "decisions" in listed and "chat.completions" not in listed
    if decision_only or (not listed and decide.refuses_chat(endpoint, name)):
        # Called on the endpoint it was picked from, like any model.
        why = decide.probe(endpoint, name)
        if why is None:
            onboard_decision(name, endpoint, endpoint, alias)
            return
        hint = ("  Open WebUI relays decisions only with ENABLE_OPENAI_API_PASSTHROUGH=True"
                " in its environment.\n" if "passthrough" in why.lower() else
                "  Its connection has no /decisions under its base. An OpenRouter-hosted decision"
                " model cannot go through Open WebUI (its Decisions API is outside /api/v1);"
                " pick it from openrouter (direct) instead.\n" if " 404" in why else
                "  The provider refused the call; try again once its limit resets.\n")
        sys.exit(f"  STOPPING: a decision model, but {endpoint}'s Decisions API did not answer:\n"
                 f"    {why}\n{hint}"
                 "  Not registered as a chat model, because every chat call to it would fail.")

    # 2/3. register
    if already_registered(name, endpoint):
        print("  [2/5] already registered — leaving the yaml alone")
    else:
        register(alias, name, endpoint)
        print(f"  [2/5] registered as {alias}")
    run("scripts/sync_config.py", timeout=300)
    run("scripts/provider_tags.py", timeout=300)
    tags = json.loads((ROOT / "config" / "provider-tags.json")
                      .read_text(encoding="utf-8"))
    print(f"  [3/5] synced; connection is {tags.get(name, '?')}")

    # 4. availability
    print("  [4/5] probing availability (4 calls)...")
    probe = run("perf/run.py", "--model", alias, "-n", "2", "--timeout", "90",
                timeout=1200)
    answered = "0a/" not in probe.stdout
    for line in probe.stdout.splitlines():
        if alias in line and ("tiny" in line or "large" in line):
            print(f"        {line.strip()}")
    if not answered:
        print("\n  STOPPING: it answered nothing. Registered so availability "
              "keeps watching it,\n  but there is no point spending suite "
              "calls on a model that cannot reply.")
        return

    # 5. the tier that decides routing
    if args.skip_suite:
        print("  [5/5] suite skipped by request")
    else:
        print("  [5/5] running the internal suite...")
        suite = run("suite/run.py", "--model", alias, "--timeout", "300",
                    timeout=3600)
        for line in suite.stdout.splitlines():
            if alias in line or line.startswith("## "):
                print(f"        {line.strip()[:110]}")

    print("\nWhere it lands:")
    panel = run("metrics/route.py", timeout=300)
    for line in panel.stdout.splitlines()[:8]:
        print(f"  {line}")

    stamp = dt.date.today().isoformat()
    note = ROOT / "runs" / f"onboard-{stamp}.md"
    with note.open("a", encoding="utf-8") as fh:
        fh.write(f"\n## Onboarded `{alias}` ({name}) — {stamp}\n\n"
                 f"Connection: {tags.get(name, '?')}. "
                 f"Availability probe: {'answered' if answered else 'NO ANSWER'}.\n\n"
                 f"Suite scores are in the dated suite note. Whether to route "
                 f"work here is a judgement, not an output of this script.\n")
    print(f"\nwrote {note}")


if __name__ == "__main__":
    main()
