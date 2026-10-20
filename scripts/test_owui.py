"""metrics/owui.py against a fake Open WebUI: nothing is sent anywhere.

    python scripts/test_owui.py

The config write must give back every key and every other connection exactly
as read, add the id once and only where the connection lists ids, and the
model record must come out public, tagged and with the sibling's logo.
"""

from __future__ import annotations

import copy
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "metrics"), str(ROOT / "suite")]
import owui  # noqa: E402

CONFIG = {"ENABLE_OPENAI_API": True,
          "OPENAI_API_BASE_URLS": ["https://api.groq.com/openai/v1", "https://api.llm7.io/v1"],
          "OPENAI_API_KEYS": ["gsk_real_key_1", "llm7_real_key_2"],
          "OPENAI_API_CONFIGS": {"0": {"enable": True, "prefix_id": "", "model_ids": ["groq/compound"]},
                                 "1": {"enable": True, "prefix_id": "llm7", "model_ids": []}}}
MODELS = [{"id": "groq/compound", "urlIdx": 0, "tags": [{"name": "groq-api"}]},
          {"id": "llm7.codestral-latest", "urlIdx": 1, "tags": [{"name": "llm7-api"}]}]
RECORDS = [{"id": "groq/compound", "name": "groq/compound", "base_model_id": None, "params": {},
            "meta": {"profile_image_url": "data:image/webp;base64,LOGO", "capabilities": {"vision": True},
                     "tags": [{"name": "groq-api"}]}}]


def fake(state):
    def call(path, body=None):
        state["calls"].append((path, copy.deepcopy(body)))
        if path == "/api/models":
            return {"data": state["models"]}
        if path == "/openai/config":
            return copy.deepcopy(state["config"])
        if path == "/openai/config/update":
            state["config"] = copy.deepcopy(body)
            return body
        if path == "/api/v1/models/base":
            return copy.deepcopy(state["records"])
        if path in ("/api/v1/models/create", "/api/v1/models/model/update"):
            state["records"] = [r for r in state["records"] if r["id"] != body["id"]] + [body]
            state["models"] = state["models"] + [{"id": body["id"], "urlIdx": 0, "tags": body["meta"]["tags"]}]
            return body
        raise AssertionError(path)
    return call


def run(connection, model, registered):
    state = {"config": copy.deepcopy(CONFIG), "models": list(MODELS), "records": copy.deepcopy(RECORDS), "calls": []}
    owui._call = fake(state)
    owui.endpoints.models = lambda name, timeout=30: {m["id"]: m for m in state["models"]}
    import route
    import run as suite_run
    suite_run._registered_names = lambda: registered
    route.connection_of = lambda alias: {"compound": "groq", "codestral": "llm7-api"}[alias]
    served, done = owui.add(connection, model)
    return state, served, done


state, served, done = run("groq", "moonshotai/kimi-k2", {"compound": "groq/compound"})
cfg = state["config"]
assert served == "moonshotai/kimi-k2", served
assert cfg["OPENAI_API_KEYS"] == CONFIG["OPENAI_API_KEYS"], "keys changed"
assert cfg["OPENAI_API_BASE_URLS"] == CONFIG["OPENAI_API_BASE_URLS"]
assert cfg["OPENAI_API_CONFIGS"]["1"] == CONFIG["OPENAI_API_CONFIGS"]["1"], "another connection changed"
assert cfg["OPENAI_API_CONFIGS"]["0"]["model_ids"] == ["groq/compound", "moonshotai/kimi-k2"]
rec = next(r for r in state["records"] if r["id"] == served)
assert rec["access_grants"] == owui.PUBLIC
assert rec["meta"]["tags"] == [{"name": "groq-api"}], rec["meta"]["tags"]      # Open WebUI's tag, not the bench's name
assert rec["meta"]["profile_image_url"] == "data:image/webp;base64,LOGO"
assert any("logo" in d for d in done), done

# An empty Model IDs list means "everything": nothing is written, and the prefix names the id.
state, served, done = run("llm7-api", "mistral-small", {"codestral": "llm7.codestral-latest"})
assert served == "llm7.mistral-small", served
assert not any(p == "/openai/config/update" for p, _ in state["calls"]), "wrote a config it did not need to"

# The same id already served by ANOTHER connection is refused, not doubled.
MODELS.append({"id": "llm7.qwen", "urlIdx": 0, "tags": []})
try:
    run("llm7-api", "qwen", {"codestral": "llm7.codestral-latest"})
    raise AssertionError("a collision with another connection was not refused")
except ValueError as exc:
    assert "already serves" in str(exc), exc
print("owui checks pass")
