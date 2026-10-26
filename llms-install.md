# Installing Benchwarmers (for an AI agent)

Benchwarmers is a self-hosted panel gateway: one prompt goes to a collection
of models at once and every answer comes back side by side. Its MCP server is
a small process that calls a running Benchwarmers, published on npm as
`@wendellresearch-ai/benchwarmers` and on PyPI as `benchwarmers`. Nothing to
clone or build.

## Requirements

- Python 3.11 or later (`python --version`). Benchwarmers is a Python app, the
  standard library only.
- Node.js 18 or later (`node --version`), for `npx`. Without Node, use the
  PyPI route under "If it doesn't start".
- A running Benchwarmers with at least one collection. If the user has none,
  ask them to start one (`npx @wendellresearch-ai/benchwarmers`, or
  `pipx install benchwarmers` then `benchwarmers`), add a model provider on
  the dashboard it opens at http://localhost:8122, onboard a few models and
  make a collection. Those steps are the user's: they choose the providers
  and sign in or paste their own keys. Never invent a key.

## Add the server

Add this entry to the MCP settings (for Cline: `cline_mcp_settings.json`,
opened from the MCP Servers panel, Configure tab):

```json
{
  "mcpServers": {
    "benchwarmers": {
      "command": "npx",
      "args": ["-y", "@wendellresearch-ai/benchwarmers", "mcp"],
      "timeout": 900,
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

Keep any servers already in the file; add `benchwarmers` beside them. The
`timeout` matters: a panel waits for every model, which can take minutes, and
Cline's default of 60 seconds ends most of them.

Optional environment variables, on the same entry as `"env": { ... }`:

- `BENCHWARMERS_URL`: the server, default `http://127.0.0.1:8122`. Set it
  only if the user runs Benchwarmers on another port or machine.
- `BENCHWARMERS_KEY`: a gateway key from the dashboard's Settings, needed for
  a server on another machine or one that requires keys. Ask the user for it.

## Check it works

1. The server should show as connected with four tools: `list_collections`,
   `ask`, `verdict` and `pending_verdicts`. The first start downloads the
   package and can take a few seconds.
2. Call `list_collections`. It returns the user's collections and what each
   is for. An error saying no Benchwarmers is running means the dashboard is
   not started: ask the user to start it.
3. Try it: `ask` a collection a short question, show the user every seat's
   answer under its model's name, then send a `verdict` for each seat you
   judged. The tool descriptions carry the rest of the rules.

## If it doesn't start

- `npx` not found: Node.js isn't on the PATH. Use the PyPI package instead:
  `"command": "uvx", "args": ["benchwarmers", "mcp"]`, or after
  `pipx install benchwarmers`, `"command": "benchwarmers", "args": ["mcp"]`.
- On Windows, if Cline cannot start `npx` itself: `"command": "cmd"`,
  `"args": ["/c", "npx", "-y", "@wendellresearch-ai/benchwarmers", "mcp"]`.
- Python not found or older than 3.11: ask the user to install a current
  Python from https://www.python.org.
