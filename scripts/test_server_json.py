"""server.json, the MCP Registry's entry: it says what the packages really are.

    python scripts/test_server_json.py

A release publishes server.json to the registry after npm and PyPI
(.github/workflows/release.yml), and the registry refuses it, after the
packages are already out, when the entry and the packages disagree. So this
checks what the registry checks, and what it cannot: the name is in the
io.github.<owner> namespace GitHub's OIDC token proves (the owner as the
repository URL spells it, since the match is case-sensitive); every version is
VERSION; package.json carries the name as mcpName (npm's proof) and the README
PyPI shows carries `mcp-name: <name>` (PyPI's); the identifiers are the real
package names; the arguments start the MCP server through launcher.py; the
environment variables are the ones mcp_server.py reads. The root's mcp.json,
mcp_config.json (Antigravity's name for it), plugin.json, .cursor-plugin and
skills/ (cursor.directory, Glama and Antigravity read the repo root, not
plugin/) start the same server and carry the plugin's skill. Standard library
only: the full schema is validated by mcp-publisher in the release itself.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import launcher  # noqa: E402

SCHEMA = "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"


def main() -> int:
    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    npm = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()

    assert server["$schema"] == SCHEMA, server["$schema"]
    name = server["name"]
    owner = re.fullmatch(r"git\+https://github\.com/([^/]+)/[^/]+?\.git", npm["repository"]["url"]).group(1)
    assert name == f"io.github.{owner}/benchwarmers", f"{name}: not in io.github.{owner}, the OIDC namespace"
    assert re.fullmatch(r"[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+", name) and 3 <= len(name) <= 200, name
    assert 1 <= len(server["description"]) <= 100, f"description is {len(server['description'])} chars, max 100"
    assert 1 <= len(server.get("title", "x")) <= 100, server["title"]
    assert server["repository"] == {"url": f"https://github.com/{owner}/benchwarmers", "source": "github"}

    assert npm["version"] == version, f"package.json says {npm['version']}, VERSION says {version}"
    assert server["version"] == version, f"server.json says {server['version']}, VERSION says {version}"
    for p in server["packages"]:
        assert p["version"] == version, f"server.json's {p['registryType']} package says {p['version']}"

    # Ownership: npm's package.json and PyPI's README name the server.
    assert npm.get("mcpName") == name, f"package.json mcpName is {npm.get('mcpName')!r}, not {name}"
    assert pyproject["readme"] == "README.md", pyproject["readme"]
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert re.search(r"mcp-name: " + re.escape(name) + r"(?=\s|<|-->|$)", readme), \
        f"README.md has no 'mcp-name: {name}' followed by a space, a newline or -->"

    kinds = {p["registryType"]: p for p in server["packages"]}
    assert sorted(kinds) == ["npm", "pypi"] and len(kinds) == len(server["packages"]), list(kinds)
    assert kinds["npm"]["identifier"] == npm["name"], kinds["npm"]["identifier"]
    assert kinds["pypi"]["identifier"] == pyproject["name"], kinds["pypi"]["identifier"]

    read = set(re.findall(r'environ\.get\("(\w+)"', (ROOT / "mcp_server.py").read_text(encoding="utf-8")))
    for kind, p in kinds.items():
        assert p["transport"] == {"type": "stdio"}, (kind, p["transport"])
        args = [a["value"] for a in p["packageArguments"] if a["type"] == "positional"]
        assert launcher.plan(args).get("script") == "mcp_server.py", f"{kind}: {args} does not start mcp_server.py"
        names = [e["name"] for e in p["environmentVariables"]]
        assert set(names) == read and len(names) == len(read), f"{kind}: lists {names}, mcp_server.py reads {read}"
        for e in p["environmentVariables"]:
            assert e.get("isSecret", False) == (e["name"] == "BENCHWARMERS_KEY"), e

    # The repo root as cursor.directory and Glama read it: mcp.json starts the npm
    # package's server, and skills/ is the Claude Code plugin's skill, word for word.
    root_mcp = json.loads((ROOT / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]["benchwarmers"]
    assert root_mcp["command"] == "npx" and root_mcp["args"][:2] == ["-y", npm["name"]], root_mcp
    assert launcher.plan(root_mcp["args"][2:]).get("script") == "mcp_server.py", root_mcp["args"]
    agy = json.loads((ROOT / "mcp_config.json").read_text(encoding="utf-8"))["mcpServers"]["benchwarmers"]
    assert agy == root_mcp, f"mcp_config.json (Antigravity's) {agy} differs from mcp.json {root_mcp}"
    assert json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))["name"] == "benchwarmers"
    cursor = json.loads((ROOT / ".cursor-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert (cursor["skills"], cursor["mcpServers"]) == ("./skills/", "./mcp.json"), cursor
    lf = lambda p: p.read_text(encoding="utf-8").replace("\r\n", "\n")  # noqa: E731
    copies = sorted(p.name for p in (ROOT / "skills").iterdir())
    assert copies == sorted(p.name for p in (ROOT / "plugin" / "skills").iterdir()), copies
    for skill in copies:
        assert lf(ROOT / "skills" / skill / "SKILL.md") == lf(ROOT / "plugin" / "skills" / skill / "SKILL.md"), \
            f"skills/{skill}/SKILL.md differs from plugin/skills/{skill}/SKILL.md: copy one over the other"
    print(f"server.json ok: {name} {version}, npm and pypi; mcp.json, .cursor-plugin and skills/ match")
    return 0


if __name__ == "__main__":
    sys.exit(main())
