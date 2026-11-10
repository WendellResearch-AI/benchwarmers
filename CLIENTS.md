# Setting up clients

Every app Benchwarmers has a ready set-up for, as the dashboard's **Clients** page shows it, written out for people and for agents setting an app up. The page fills in your own install: its address, your collections and a gateway key made on the spot. Here they are a fresh install's: the server at `http://127.0.0.1:8122`, the collections `core` and `fast`, and a gateway key read from `BENCHWARMERS_KEY`. Replace them with yours.

Agents get a collection in route mode (one member with its tools, `bench/<collection>:route`); chat windows a merged answer (`:merged`); the MCP server the panel itself, as ask and verdict tools. Gateway `http://127.0.0.1:8122/v1` · MCP server `http://127.0.0.1:8122/mcp`.

_Generated from `serve.client_setups` by `scripts/clients_doc.py`; each app's format follows its own docs as read on 2026-11-08. Do not edit by hand: run the script._

## The gateway key

When every call needs a key (`BENCHWARMERS_REQUIRE_KEYS=1`, or an admin token), make one on Settings or the Clients page. Most set-ups read it from `BENCHWARMERS_KEY`:

**macOS, Linux: in your shell profile**

```sh
export BENCHWARMERS_KEY='<your gateway key>'
```

**Windows: once, then start the app again**

```bat
setx BENCHWARMERS_KEY "<your gateway key>"
```

## Contents

- [opencode](#opencode)
- [VS Code chat (Custom Endpoint)](#vscode)
- [Codex CLI](#codex)
- [Zed](#zed)
- [Continue](#continue)
- [Cline and Kilo Code](#cline)
- [Claude Code](#claude-code)
- [Chat apps](#chat)
- [goose](#goose)

<a id='opencode'></a>

## opencode

An agent with its tools on any collection (route mode), and the panel's ask and verdict as MCP tools.

**Where it goes:** `~/.config/opencode/opencode.json`, or `opencode.json` in a project; merge into what is there. Pick a model with `/models` (`benchwarmers/bench/core:route`). The key comes from `BENCHWARMERS_KEY`.

**opencode.json**

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "benchwarmers": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Benchwarmers",
      "options": {
        "baseURL": "http://127.0.0.1:8122/v1",
        "apiKey": "{env:BENCHWARMERS_KEY}"
      },
      "models": {
        "bench/core:route": {
          "name": "core (route)",
          "limit": {
            "context": 128000,
            "output": 16384
          }
        },
        "bench/fast:route": {
          "name": "fast (route)",
          "limit": {
            "context": 128000,
            "output": 16384
          }
        }
      }
    }
  },
  "mcp": {
    "benchwarmers": {
      "type": "remote",
      "url": "http://127.0.0.1:8122/mcp",
      "enabled": true,
      "oauth": false,
      "headers": {
        "Authorization": "Bearer {env:BENCHWARMERS_KEY}"
      },
      "timeout": 900000
    }
  }
}
```

**Watch for:** `timeout` is in milliseconds and covers each MCP call: without it opencode stops waiting long before a panel answers.

Written from [v1.19.5's providers](https://github.com/anomalyco/opencode/blob/v1.19.5/packages/web/src/content/docs/providers.mdx), [MCP servers](https://github.com/anomalyco/opencode/blob/v1.19.5/packages/web/src/content/docs/mcp-servers.mdx), read 2026-11-08: the current release's own docs. opencode.ai already shows the next config (`providers`, `mcp.servers`), which v1.19.5 does not read.

<a id='vscode'></a>

## VS Code chat (Custom Endpoint)

Copilot chat's agent on a collection (route mode), with your own key, and the panel as MCP tools.

**Where it goes:** `chatLanguageModels.json`: in the chat's model picker, Manage Models, Add Models, Custom Endpoint; add the object to the list there. `mcp.json`: the command MCP: Open User Configuration. VS Code asks for the key once and keeps it.

**chatLanguageModels.json**

```json
[
  {
    "name": "Benchwarmers",
    "vendor": "customendpoint",
    "apiKey": "${input:benchwarmersKey}",
    "apiType": "chat-completions",
    "models": [
      {
        "id": "bench/core:route",
        "name": "core (Benchwarmers)",
        "url": "http://127.0.0.1:8122/v1/chat/completions",
        "toolCalling": true,
        "vision": false,
        "maxInputTokens": 111616,
        "maxOutputTokens": 16384
      },
      {
        "id": "bench/fast:route",
        "name": "fast (Benchwarmers)",
        "url": "http://127.0.0.1:8122/v1/chat/completions",
        "toolCalling": true,
        "vision": false,
        "maxInputTokens": 111616,
        "maxOutputTokens": 16384
      }
    ]
  }
]
```

**mcp.json**

```json
{
  "inputs": [
    {
      "type": "promptString",
      "id": "benchwarmers-key",
      "description": "Benchwarmers gateway key",
      "password": true
    }
  ],
  "servers": {
    "benchwarmers": {
      "type": "http",
      "url": "http://127.0.0.1:8122/mcp",
      "headers": {
        "Authorization": "Bearer ${input:benchwarmers-key}"
      }
    }
  }
}
```

**Watch for:** Keep `toolCalling: true`: VS Code hides a model without it from the agent picker. Restart VS Code if the models do not appear.

Written from [language models](https://code.visualstudio.com/docs/copilot/customization/language-models), [MCP configuration](https://code.visualstudio.com/docs/agents/reference/mcp-configuration), read 2026-11-08.

<a id='codex'></a>

## Codex CLI

Codex on a collection over the Responses API (route mode), and the panel as MCP tools.

**Where it goes:** `~/.codex/config.toml` (`%USERPROFILE%\.codex\config.toml` on Windows). The first four lines go above the file's first `[table]`. The key comes from `BENCHWARMERS_KEY`.

**config.toml**

```toml
model = "bench/core:route"
model_provider = "benchwarmers"
model_context_window = 128000
web_search = "disabled"

[model_providers.benchwarmers]
name = "Benchwarmers"
base_url = "http://127.0.0.1:8122/v1"
env_key = "BENCHWARMERS_KEY"
wire_api = "responses"

[mcp_servers.benchwarmers]
url = "http://127.0.0.1:8122/mcp"
bearer_token_env_var = "BENCHWARMERS_KEY"
tool_timeout_sec = 900
```

**Watch for:** `web_search = "disabled"`: left on, Codex sends OpenAI's hosted search, which the gateway refuses (no other model can run it), and every turn fails.

Written from [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference), read 2026-11-08.

<a id='zed'></a>

## Zed

Zed's agent on a collection (route mode), and the panel as MCP tools.

**Where it goes:** `settings.json`: the command zed: open settings file. Zed reads the key from `BENCHWARMERS_API_KEY`, named after the provider id, or from the provider's settings screen; never from settings.json.

**settings.json**

```json
{
  "language_models": {
    "openai_compatible": {
      "benchwarmers": {
        "api_url": "http://127.0.0.1:8122/v1",
        "available_models": [
          {
            "name": "bench/core:route",
            "display_name": "core (Benchwarmers)",
            "max_tokens": 128000,
            "max_output_tokens": 16384,
            "capabilities": {
              "tools": true,
              "images": false,
              "parallel_tool_calls": false,
              "prompt_cache_key": false
            }
          },
          {
            "name": "bench/fast:route",
            "display_name": "fast (Benchwarmers)",
            "max_tokens": 128000,
            "max_output_tokens": 16384,
            "capabilities": {
              "tools": true,
              "images": false,
              "parallel_tool_calls": false,
              "prompt_cache_key": false
            }
          }
        ]
      }
    }
  },
  "context_servers": {
    "benchwarmers": {
      "url": "http://127.0.0.1:8122/mcp",
      "headers": {
        "Authorization": "Bearer <your gateway key>"
      }
    }
  }
}
```

**macOS, Linux: in your shell profile**

```sh
export BENCHWARMERS_API_KEY='<your gateway key>'
```

**Windows: once, then start the app again**

```bat
setx BENCHWARMERS_API_KEY "<your gateway key>"
```

**Watch for:** settings.json takes no variable, so the MCP header holds the key as text: replace `` there yourself, and keep the file out of synced dotfiles.

Written from [API access](https://zed.dev/docs/ai/use-api-access), [MCP](https://zed.dev/docs/ai/mcp), read 2026-11-08.

<a id='continue'></a>

## Continue

Continue's chat, edit and agent on a collection (route mode), and the panel as MCP tools.

**Where it goes:** `~/.continue/config.yaml` (`%USERPROFILE%\.continue\config.yaml` on Windows); in a file you have, add the items to its `models` and `mcpServers`. `secrets.BENCHWARMERS_KEY` is read from `~/.continue/.env` or the environment.

**config.yaml**

```yaml
name: Benchwarmers
version: 0.0.1
schema: v1
models:
  - name: "core (Benchwarmers)"
    provider: openai
    model: "bench/core:route"
    apiBase: "http://127.0.0.1:8122/v1"
    apiKey: ${{ secrets.BENCHWARMERS_KEY }}
    roles: [chat, edit, apply]
    capabilities: [tool_use]
    defaultCompletionOptions:
      contextLength: 128000
      maxTokens: 16384
  - name: "fast (Benchwarmers)"
    provider: openai
    model: "bench/fast:route"
    apiBase: "http://127.0.0.1:8122/v1"
    apiKey: ${{ secrets.BENCHWARMERS_KEY }}
    roles: [chat, edit, apply]
    capabilities: [tool_use]
    defaultCompletionOptions:
      contextLength: 128000
      maxTokens: 16384
mcpServers:
  - name: benchwarmers
    type: streamable-http
    url: "http://127.0.0.1:8122/mcp"
    requestOptions:
      headers:
        Authorization: Bearer ${{ secrets.BENCHWARMERS_KEY }}
```

**Watch for:** Keep `capabilities: [tool_use]`: without it Continue offers the model for chat, not agent mode.

Written from [OpenAI provider](https://docs.continue.dev/customize/model-providers/top-level/openai), [MCP](https://docs.continue.dev/customize/deep-dives/mcp), [config.yaml reference](https://docs.continue.dev/reference), read 2026-11-08.

<a id='cline'></a>

## Cline and Kilo Code

Their agent on a collection (route mode), set in their own screens; Cline takes the panel as MCP tools too.

**Where it goes:** Cline: its settings, API Provider. Kilo Code: Settings, Providers, Custom provider. Cline's MCP: the MCP Servers icon, Configure, Configure MCP Servers.

**Cline**

```text
API Provider         OpenAI Compatible
Base URL             http://127.0.0.1:8122/v1
API Key              <your gateway key>
Model ID             bench/core:route
Context Window Size  128000
Max Output Tokens    16384
Image Support        off
```

**Kilo Code**

```text
Provider ID   benchwarmers
Display name  Benchwarmers
Provider API  OpenAI Compatible
Base URL      http://127.0.0.1:8122/v1
API key       <your gateway key>
Models        bench/core:route, bench/fast:route
```

**Cline: cline_mcp_settings.json**

```json
{
  "mcpServers": {
    "benchwarmers": {
      "type": "streamableHttp",
      "url": "http://127.0.0.1:8122/mcp",
      "headers": {
        "Authorization": "Bearer <your gateway key>"
      },
      "timeout": 900
    }
  }
}
```

**Watch for:** Keep `"type": "streamableHttp"`: without it Cline tries the old SSE transport. Its `timeout` is in seconds (60 unless set).

Written from [Cline: OpenAI Compatible](https://docs.cline.bot/provider-config/openai-compatible), [Cline: MCP servers](https://docs.cline.bot/mcp/configuring-mcp-servers), [Kilo Code: OpenAI Compatible](https://kilo.ai/docs/providers/openai-compatible), read 2026-11-08.

<a id='claude-code'></a>

## Claude Code

The panel as MCP tools, with the plugin's `/benchwarmers:ask`, its skill and its reminder of seats still unjudged; or Claude Code itself on a collection (route mode).

**Where it goes:** The plugin finds the server at `BENCHWARMERS_URL`, `http://127.0.0.1:8122` unless set, and sends `BENCHWARMERS_KEY`. Route mode: the shell you start `claude` from.

**In Claude Code: the plugin**

```text
/plugin marketplace add WendellResearch-AI/benchwarmers
/plugin install benchwarmers@benchwarmers
```

**Claude Code on a collection: a shell**

```sh
export ANTHROPIC_BASE_URL=http://127.0.0.1:8122
export ANTHROPIC_API_KEY='<your gateway key>'
export ANTHROPIC_MODEL=bench/core:route
export ANTHROPIC_DEFAULT_HAIKU_MODEL=bench/fast:route
claude
```

**Watch for:** The plugin's Stop hook needs the server on the same machine; with none running, every turn ends with a hook error.

Written from [README, From Claude Code and other agents](https://github.com/WendellResearch-AI/benchwarmers#from-claude-code-and-other-agents), read 2026-11-08: checked end to end with Claude Code 2.1.299 on 2026-10-23 (GATEWAY.md, Route mode).

<a id='chat'></a>

## Chat apps

LobeHub, AnythingLLM, Cherry Studio, Chatbox and LibreChat: a chat window on a collection, one merged answer per question, every seat still called and recorded.

**Where it goes:** Each app's settings, as an OpenAI-compatible (custom) provider. LibreChat: `librechat.yaml`, with `BENCHWARMERS_KEY` in its `.env`.

**Any of them**

```text
Base URL  http://127.0.0.1:8122/v1
API key   <your gateway key>
Model     bench/core:merged
```

**LibreChat: librechat.yaml**

```yaml
endpoints:
  custom:
    - name: "Benchwarmers"
      apiKey: "${BENCHWARMERS_KEY}"
      baseURL: "http://127.0.0.1:8122/v1"
      models:
        default: ["bench/core:merged", "bench/fast:merged"]
        fetch: true
      titleConvo: false
      modelDisplayLabel: "Benchwarmers"
```

**Watch for:** An app in Docker reaches this machine at another address than 127.0.0.1, and that host:port goes in `BENCH_ALLOWED_HOSTS`. Turn automatic titles off: each runs a whole panel.

Written from [LibreChat: custom endpoints](https://www.librechat.ai/docs/configuration/librechat_yaml/object_structure/custom_endpoint), read 2026-11-08.

<a id='goose'></a>

## goose

goose on a collection (route mode), and the panel as an MCP extension.

**Where it goes:** The provider: `~/.config/goose/custom_providers/benchwarmers.json`. The extension: `~/.config/goose/config.yaml`. On Windows both are under `%APPDATA%\Block\goose\config\`. goose reads the provider's key from `BENCHWARMERS_KEY`, never from config.yaml.

**custom_providers/benchwarmers.json**

```json
{
  "name": "benchwarmers",
  "engine": "openai",
  "display_name": "Benchwarmers",
  "description": "Benchwarmers collections, one model per conversation (route mode)",
  "api_key_env": "BENCHWARMERS_KEY",
  "base_url": "http://127.0.0.1:8122/v1/chat/completions",
  "models": [
    {
      "name": "bench/core:route",
      "context_limit": 128000
    },
    {
      "name": "bench/fast:route",
      "context_limit": 128000
    }
  ],
  "supports_streaming": true,
  "requires_auth": true
}
```

**config.yaml**

```yaml
extensions:
  benchwarmers:
    type: streamable_http
    name: benchwarmers
    enabled: true
    uri: "http://127.0.0.1:8122/mcp"
    headers: {Authorization: "Bearer <your gateway key>"}
    timeout: 900
```

**Watch for:** The extension's header holds the key as text: replace `` there yourself.

Written from [providers](https://goose-docs.ai/docs/getting-started/providers), [extensions](https://goose-docs.ai/docs/getting-started/using-extensions), [config files](https://goose-docs.ai/docs/guides/config-files), read 2026-11-08.
