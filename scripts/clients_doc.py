"""Write CLIENTS.md from the dashboard's Clients page, so each app's set-up is in
the repository for people and agents to read, and never drifts from the page.

    python scripts/clients_doc.py           # write CLIENTS.md
    python scripts/clients_doc.py --check   # fail if CLIENTS.md is not what it would write (check.py)

One source: serve.client_setups, the function the Clients page draws. The page
fills in this install (its address, its collections, a key made on the spot);
here they are the defaults a fresh install has: the server at 127.0.0.1:8122,
collections `core` and `fast`, and every call needing a gateway key, read from
BENCHWARMERS_KEY. The page's HTML (code, links) becomes Markdown.
"""

from __future__ import annotations

import html
import os
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "CLIENTS.md"
COLLECTIONS = [("core", None, False), ("fast", None, False)]


def md(text: str) -> str:
    """The page's inline HTML as Markdown: code, links, line breaks; any other tag dropped."""
    text = re.sub(r"<code>(.*?)</code>", lambda m: "`" + html.unescape(m.group(1)) + "`", text)
    text = re.sub(r"<a [^>]*href='([^']*)'[^>]*>(.*?)</a>", r"[\2](\1)", text)
    text = re.sub(r"<br\s*/?>", "  \n", text)
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def fence(lang: str, text: str) -> str:
    tick = "````" if "```" in text else "```"
    return f"{tick}{lang}\n{text}\n{tick}"


def render() -> str:
    # serve reads the data folder at import: an empty temp one, never the install's.
    os.environ.setdefault("BENCHWARMERS_HOME", tempfile.mkdtemp(prefix="bench-clients-doc-"))
    sys.path[:0] = [str(ROOT), str(ROOT / "metrics"), str(ROOT / "suite")]
    import serve
    root = serve.DEFAULT_SERVER
    out = ["# Setting up clients",
           "",
           "Every app Benchwarmers has a ready set-up for, as the dashboard's **Clients** page shows it,"
           " written out for people and for agents setting an app up. The page fills in your own install:"
           " its address, your collections and a gateway key made on the spot. Here they are a fresh"
           f" install's: the server at `{root}`, the collections `core` and `fast`, and a gateway key"
           f" read from `{serve.KEY_ENV}`. Replace them with yours.",
           "",
           "Agents get a collection in route mode (one member with its tools, `bench/<collection>:route`);"
           " chat windows a merged answer (`:merged`); the MCP server the panel itself, as ask and verdict"
           f" tools. Gateway `{root}/v1` · MCP server `{root}/mcp`.",
           "",
           f"_Generated from `serve.client_setups` by `scripts/clients_doc.py`; each app's format follows its"
           f" own docs as read on {serve.CLIENTS_READ}. Do not edit by hand: run the script._",
           "",
           "## The gateway key",
           "",
           "When every call needs a key (`BENCHWARMERS_REQUIRE_KEYS=1`, or an admin token), make one on"
           " Settings or the Clients page. Most set-ups read it from"
           f" `{serve.KEY_ENV}`:",
           ""]
    for s in serve._env_snips(serve.KEY_ENV):
        out += [f"**{s['label']}**", "", fence(s["lang"], s["text"]), ""]
    setups = serve.client_setups(root, COLLECTIONS, True)
    out += ["## Contents", ""] + [f"- [{c['name']}](#{c['id']})" for c in setups] + [""]
    for c in setups:
        out += [f"<a id='{c['id']}'></a>", "", f"## {c['name']}", "", md(c["gets"]), "",
                f"**Where it goes:** {md(c['where'])}", ""]
        for s in c["blocks"]:
            out += [f"**{s['label']}**", "", fence(s["lang"], s["text"]), ""]
        docs = ", ".join(f"[{t}]({u})" for u, t in c["docs"])
        out += [f"**Watch for:** {md(c['watch'])}", "",
                f"Written from {docs}, read {serve.CLIENTS_READ}"
                + (f": {md(c['docs_note'])}" if c.get("docs_note") else "."), ""]
    return "\n".join(out).rstrip() + "\n"


def main() -> int:
    text = render()
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print("CLIENTS.md is not what serve.client_setups gives: run python scripts/clients_doc.py")
            return 1
        print("CLIENTS.md matches the Clients page")
        return 0
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
