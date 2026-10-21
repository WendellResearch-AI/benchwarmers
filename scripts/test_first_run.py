"""A stranger's first run, on an empty data folder. No real files.

    python scripts/test_first_run.py

RELEASE-PLAN Phases 1 and 4. The dashboard renders with no endpoint and leads
into Add, through the real handler; a job that needs an endpoint still
refuses, saying how to add one; the readers of seed files fall back to empty
when a file is missing; the shipped roles and shapes merge under the
install's own; init copies examples/, points a checkout at the folder (a
temp pointer here) and adds a first endpoint by save_endpoint's rules
against a fake server. BENCHWARMERS_HOME and LLM_USER_PATH name temp folders
before anything imports metrics/paths.py, so no install's data and not the
real llm key store are touched.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
TMP = pathlib.Path(tempfile.mkdtemp())
DATA, LLM = TMP / "data", TMP / "llm"
DATA.mkdir()
os.environ.update(BENCHWARMERS_HOME=str(DATA), LLM_USER_PATH=str(LLM))
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
import serve  # noqa: E402
import init  # noqa: E402
import auto_retire  # noqa: E402
import onboard  # noqa: E402
import model_roles  # noqa: E402
import paths  # noqa: E402
import route  # noqa: E402
import run as suite  # noqa: E402


class Provider(BaseHTTPRequestHandler):
    def do_GET(self):                            # noqa: N802
        body = json.dumps({"data": [{"id": "m1"}, {"id": "m2"}]} if self.path == "/v1/models" else {}).encode()
        self.send_response(200 if self.path == "/v1/models" else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a):
        pass


def serve_on(handler) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main() -> int:
    assert serve.DATA == DATA and serve.endpoints.LLM_DIR == LLM, "not on the temp folders"
    provider, dash = serve_on(Provider), serve_on(serve.Handler)
    serve.PORT = dash.server_port
    home = f"http://127.0.0.1:{dash.server_port}"

    def get(path: str):
        with urllib.request.urlopen(home + path, timeout=10) as r:
            body = r.read().decode("utf-8")
        return json.loads(body) if path.startswith("/api/") else body

    def post(path: str, data: dict) -> tuple[int, dict]:
        req = urllib.request.Request(home + path, json.dumps(data).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    try:
        # 0. An endpoints file that cannot be read is not a fresh install: the
        # page says which file and why (load() refuses, so no writer saves over it).
        broken = DATA / "config" / "endpoints.json"
        broken.parent.mkdir()
        broken.write_text("{", encoding="utf-8")
        assert "cannot be read" in get("/"), "a broken endpoints file read as none"
        broken.unlink()

        # 1. No endpoint: the page renders and leads into Add; the APIs answer.
        page = get("/")
        assert "id='first-run' data-step='endpoint'" in page and "id='e-add'" in page, "no first-run step"
        assert get("/api/endpoints")["endpoints"] == []
        assert get("/api/connect")["providers"], "no providers to sign in with"
        cand = get("/api/candidates")
        assert not cand["ok"] and "add one first" in cand["errors"][0], cand
        assert get("/api/collections")["ok"] and get("/api/connections")["ok"]
        status, res = post("/api/onboard", {"served": "m1"})
        assert status == 400 and "no endpoints configured" in res["errors"][0], res   # a reply, not a dropped request
        # ...while a job that needs an endpoint refuses, saying how to add one.
        empty = TMP / "job"
        empty.mkdir()
        job = subprocess.run([sys.executable, "scripts/check_deprecations.py", "--dry-run"], cwd=ROOT,
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                             env={**os.environ, "BENCHWARMERS_HOME": str(empty)})
        assert job.returncode and "no endpoints configured" in job.stderr, job.stderr[-500:]
        assert not any(empty.iterdir()), "a refused job wrote something"

        # 2. Every seed file missing: the readers say "nothing yet".
        assert suite.registered_models() == [] and suite._registered_names() == {}
        assert suite.available_models() == []
        assert auto_retire.evidence() == ([], {}, {})
        assert auto_retire.run({}, set(), {}, dry_run=True) == ([], set())
        assert onboard.existing_aliases() == {}
        assert onboard.alias_for("v/m1", {"m1": "other/m1"}) == "m1-alt"       # a collision, no provider tags
        assert model_roles.load() == {"roles": model_roles.ROLES, "shapes": model_roles.SHAPES,
                                      "models": {}, "ad_hoc": []}
        assert route.roles_for_shape("review") == ["general", "code"] and route.role_of("x") == "general"

        # 3. A file from before the split (all four keys): its own wording wins,
        # name by name, and what it does not define still comes from the product.
        roles_file = DATA / "config" / "model-roles.json"
        roles_file.parent.mkdir(parents=True, exist_ok=True)
        roles_file.write_text(json.dumps({"roles": {"code": "mine"}, "shapes": {"review": ["general"],
                              "mine": ["light"]}, "models": {"a": "code"}, "ad_hoc": ["b"]}), encoding="utf-8")
        merged = model_roles.load()
        assert merged["roles"]["code"] == "mine" and merged["roles"]["light"] == model_roles.ROLES["light"]
        assert merged["shapes"]["review"] == ["general"] and merged["shapes"]["mine"] == ["light"]
        assert merged["shapes"]["quick"] == model_roles.SHAPES["quick"]
        assert (merged["models"], merged["ad_hoc"]) == ({"a": "code"}, ["b"])
        assert route.roles_for_shape("mine") == ["light"] and "mine" in serve.collections_view()["shapes"]
        roles_file.unlink()

        # 4. init: examples copied, the layout made, the checkout pointed, a first endpoint added.
        init.POINTER = TMP / "pointer"
        os.environ.pop("BENCHWARMERS_HOME")
        ok, said = init.run(DATA, ("fake", f"http://127.0.0.1:{provider.server_port}/v1"), "sk-test")
        assert ok, said
        assert json.loads(roles_file.read_text(encoding="utf-8"))["models"] == {}
        assert (DATA / "config" / "gateway.json").exists() and all((DATA / d).is_dir() for d in paths.LAYOUT)
        assert init.POINTER.read_text(encoding="utf-8").strip() == str(DATA.resolve())
        assert list(serve.endpoints.load()) == ["fake"] and serve.endpoints.default() == "fake"
        assert json.loads((LLM / "keys.json").read_text(encoding="utf-8")) == {"fake": "sk-test"}
        assert "data-step='onboard'" in get("/"), "the next step is not onboarding"
        serve.endpoints.YAML.write_text(serve.endpoints.yaml_block("m1", "m1", "fake"), encoding="utf-8")
        assert "data-step='collection'" in serve.first_run(), "a model on the bench, and no collection"
        (DATA / "config" / "collections.json").write_text('{"c": {"members": ["m1"]}}', encoding="utf-8")
        assert serve.first_run() == "", "set up, yet still a first-run step"
        # Again, on a folder in use: nothing overwritten, the pointer kept.
        (DATA / "config" / "gateway.json").write_text('{"max_calls": 3}', encoding="utf-8")
        os.environ.pop("BENCHWARMERS_HOME")
        ok, said = init.run(DATA)
        assert ok and "0 starter file(s)" in said[0] and "already points" in said[1], said
        assert json.loads((DATA / "config" / "gateway.json").read_text(encoding="utf-8")) == {"max_calls": 3}
        # A checkout already pointing elsewhere, or BENCHWARMERS_HOME set: left alone, and said so.
        init.POINTER.write_text("elsewhere\n", encoding="utf-8")
        os.environ.pop("BENCHWARMERS_HOME")
        ok, said = init.run(TMP / "other")
        assert ok and "left so" in said[1] and init.POINTER.read_text(encoding="utf-8") == "elsewhere\n", said
        assert (TMP / "other" / "config" / "model-roles.json").exists()
        os.environ["BENCHWARMERS_HOME"] = str(TMP / "mine")
        ok, said = init.run(TMP / "third")
        assert ok and "which wins" in said[1] and init.POINTER.read_text(encoding="utf-8") == "elsewhere\n", said
    finally:
        provider.shutdown()
        dash.shutdown()
        shutil.rmtree(TMP, ignore_errors=True)
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
