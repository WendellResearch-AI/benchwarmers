"""The bench CLI (cli.py) against the real dashboard handler. No real files.

    python scripts/test_cli.py

Read commands, write commands on a temp collections file and key store,
asking a collection (the panel stubbed, as in test_auth), a prompt built from
a file, the exit codes, and the admin token: the CLI sends it as a bearer,
the dashboard takes it and nothing else, and a gateway key never opens a
dashboard route.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import serve  # noqa: E402
import gateway  # noqa: E402
import guard  # noqa: E402
import cli  # noqa: E402

TOKEN = "test-admin-token-0123456789"


def bench(*argv: str, stdin: str = "") -> tuple[int, str, str]:
    """(exit code, stdout, stderr) of one `bench` command, in process."""
    out, err = io.StringIO(), io.StringIO()
    real_in = sys.stdin
    sys.stdin = io.StringIO(stdin)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main(list(argv))
            except SystemExit as e:                    # argparse's usage errors
                code = e.code if isinstance(e.code, int) else 2
    finally:
        sys.stdin = real_in
    return code, out.getvalue(), err.getvalue()


def ok(*argv: str, stdin: str = "") -> str:
    code, out, err = bench(*argv, stdin=stdin)
    assert code == 0, (argv, code, out, err)
    return out


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_port
    env_keys = ("BENCHWARMERS_ADMIN_TOKEN", "BENCHWARMERS_REQUIRE_KEYS", "BENCHWARMERS_KEY", "BENCHWARMERS_URL")
    saved_env = {k: os.environ.pop(k, None) for k in env_keys}
    saved = (guard.KEYS, serve.CHANGES_PATH, serve.PORT, serve.bench_collections.FILE, gateway.run_collection)
    asked, real_env = [], cli.env
    with tempfile.TemporaryDirectory() as tmp:
        t = pathlib.Path(tmp)
        try:
            guard.KEYS, serve.CHANGES_PATH, serve.PORT = t / "gateway-keys.json", t / "changes.jsonl", port
            serve.bench_collections.FILE = t / "collections.json"
            serve.bench_collections.FILE.write_text(json.dumps({"core": {"members": [], "description": "x"}}),
                                                    encoding="utf-8")
            gateway.run_collection = lambda name, messages, nominated, reply, who: (
                asked.append((name, messages, nominated, reply)) or "## member — m\nthe answer", {"seats": []})
            os.environ["BENCHWARMERS_URL"] = f"http://127.0.0.1:{port}/v1"      # a /v1 on the end is fine

            # Reading.
            assert "Benchwarmers" in ok("status")
            status = json.loads(ok("status", "--json"))
            assert status["gateway"].endswith(f":{port}/v1") and status["collections"] == 1, status
            assert set(status["scheduler_jobs"]) and "auth" in status, status
            assert "core" in ok("collections")
            json.loads(ok("models", "--json"))
            json.loads(ok("endpoints", "--json"))
            assert "no gateway keys" in ok("keys")

            # Collections: save what is given, keep the rest, add and remove one at a time.
            # Members must be chat models on the bench: three from this install's (or the fixtures').
            chat = [m["alias"] for m in json.loads(ok("models", "--json"))["models"] if m["kind"] == "chat"]
            assert len(chat) >= 3, chat
            a, b, c = chat[:3]
            ok("collection", "save", "bench/pair", "--members", f"{a},{b}", "--description", "two",
               "--tags", "fast")
            show = json.loads(ok("collection", "show", "pair", "--json"))
            assert show["members"] == [a, b] and show["description"] == "two", show
            data = json.loads(serve.bench_collections.FILE.read_text(encoding="utf-8"))
            assert data["pair"]["members"] == [a, b] and data["pair"]["tags"] == ["fast"], data
            ok("collection", "save", "pair", "--add", c, "--remove", a)
            data = json.loads(serve.bench_collections.FILE.read_text(encoding="utf-8"))
            assert data["pair"]["members"] == [b, c] and data["pair"]["description"] == "two", data
            ok("collection", "rm", "pair")
            assert "pair" not in json.loads(serve.bench_collections.FILE.read_text(encoding="utf-8"))
            code, _, err = bench("collection", "show", "pair")
            assert code == 1 and "no collection" in err, (code, err)

            # Gateway keys: printed once, listed by name and hint only, revoked.
            code, out, err = bench("key", "create", "cli-test", "--collections", "core", "--rpm", "3")
            key = out.strip()
            assert code == 0 and key.startswith(guard.PREFIX) and "shown this once" in err, (code, out, err)
            listed = ok("keys", "--json")
            assert "cli-test" in listed and key not in listed and "sha256" not in listed, listed
            ok("key", "revoke", "cli-test")

            # Asking: the prompt from words, a file and stdin; the options on the wire.
            f = t / "diff.txt"
            f.write_text("- old\n+ new\n", encoding="utf-8")
            out = ok("ask", "core", "review", "this", "-f", str(f), "--merged", "--with", "x1")
            assert "the answer" in out, out
            name, messages, nominated, reply = asked[-1]
            assert name == "core" and reply == "merged" and nominated == ["x1"], asked[-1]
            assert "review this" in messages[-1]["content"] and "+ new" in messages[-1]["content"], messages
            ok("ask", "bench/core", "-", "--system", "be brief", stdin="from stdin")
            assert asked[-1][1][0] == {"role": "system", "content": "be brief"}, asked[-1]
            assert asked[-1][1][-1]["content"] == "from stdin", asked[-1]
            code, _, err = bench("ask", "core")
            assert code == 2 and "nothing to ask" in err, (code, err)
            code, _, err = bench("verdict", "nope.member1", "--useful")
            assert code == 1 and "nope.member1" in err, (code, err)

            # The admin token: the server has one; what the CLI sends decides. The
            # server reads os.environ, the CLI its own settings (cli.env), here apart.
            os.environ["BENCHWARMERS_ADMIN_TOKEN"] = TOKEN
            gw_key = guard.make_key("agent")
            mine = {"BENCHWARMERS_URL": os.environ["BENCHWARMERS_URL"]}
            cli.env = lambda name: mine.get(name, "")
            code, _, err = bench("status")                          # none sent
            assert code == 1 and "BENCHWARMERS_ADMIN_TOKEN" in err, (code, err)
            mine["BENCHWARMERS_ADMIN_TOKEN"] = gw_key               # a gateway key is not the admin token
            code, _, err = bench("status")
            assert code == 1 and "not the admin token" in err, (code, err)
            mine["BENCHWARMERS_ADMIN_TOKEN"] = TOKEN
            assert "Benchwarmers" in ok("status")
            assert "admin token set" in ok("status")
            code, _, err = bench("collections")                     # the gateway's routes take a key
            assert code == 1 and "BENCHWARMERS_KEY" in err, (code, err)
            mine["BENCHWARMERS_KEY"] = gw_key
            assert "core" in ok("collections")
            cli.env = real_env
            del os.environ["BENCHWARMERS_ADMIN_TOKEN"]

            # No server: exit 3, saying where it looked.
            server.shutdown()
            server.server_close()
            code, _, err = bench("status")
            assert code == 3 and "not running" in err and str(port) in err, (code, err)
        finally:
            (guard.KEYS, serve.CHANGES_PATH, serve.PORT, serve.bench_collections.FILE,
             gateway.run_collection) = saved
            cli.env = real_env
            for k, v in saved_env.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
