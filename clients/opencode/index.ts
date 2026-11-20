// Benchwarmers for opencode 2: every collection as a model, kept current, and the
// panel's ask and verdict as an MCP server. No model list to keep in opencode.jsonc.
//
// Install: the dashboard's Connect page (opencode, Set up) copies this folder to
// ~/.config/opencode/plugins/benchwarmers/ with a benchwarmers.json beside it; or copy
// it there yourself. opencode loads it by itself; models show as benchwarmers/<collection>.
//
// Settings, the first found wins: the plugin's options in opencode.jsonc
// ({"package": "<this folder>", "options": {...}}), then benchwarmers.json beside this
// file, then the environment, then the defaults:
//   url       the Benchwarmers server, without /v1 (BENCHWARMERS_URL, else http://127.0.0.1:8122)
//   name      the name its calls are logged under, sent where no key is (default "opencode")
//   keyEnv    the environment variable holding a gateway key, when one is used (BENCHWARMERS_KEY)
//   models    false leaves the collections out (the panel only)
//   mcp       false leaves the MCP server out (the collections only)
//   also      ad hoc collections to add by name: GET /v1/models leaves them out on purpose
//   refresh   seconds between looks for new or removed collections (default 60)
import { readFileSync } from "node:fs"
import { Model, Plugin, Provider } from "@opencode/plugin"

type Settings = { url: string; name: string; keyEnv: string; models: boolean; mcp: boolean; also: string[]; refresh: number }
type Listed = { id: string; together?: { context_window?: number; capabilities?: string[] } }

const OUTPUT = 16384        // the output budget an agent is told; the gateway cuts it to the model's own

function settings(options: Record<string, unknown>): Settings {
  let file: Record<string, unknown> = {}
  try {
    file = JSON.parse(readFileSync(new URL("./benchwarmers.json", import.meta.url), "utf8"))
  } catch {}
  const pick = (k: string) => (k in options ? options[k] : file[k])
  const url = String(pick("url") || process.env.BENCHWARMERS_URL || "http://127.0.0.1:8122")
  return {
    url: url.replace(/\/+$/, "").replace(/\/v1$/, ""),
    name: String(pick("name") || "opencode"),
    keyEnv: String(pick("keyEnv") || "BENCHWARMERS_KEY"),
    models: pick("models") !== false,
    mcp: pick("mcp") !== false,
    also: Array.isArray(pick("also")) ? (pick("also") as unknown[]).map(String) : [],
    refresh: Math.max(15, Number(pick("refresh")) || 60),
  }
}

export default Plugin.define({
  id: "benchwarmers",
  async setup(ctx) {
    const s = settings((ctx.options ?? {}) as Record<string, unknown>)
    // A gateway key from the environment when there is one; else the app's name, which
    // the gateway keeps as the caller (not a credential).
    const bearer = process.env[s.keyEnv] || s.name
    const pid = Provider.ID.make("benchwarmers")

    // Each collection in route mode: one member answers each turn and runs opencode's tools.
    function inventory(listed: Listed[]): Model.Info[] {
      const rows = listed.filter((m) => m.id.endsWith(":route"))
      const known = new Set(rows.map((m) => m.id.slice("bench/".length, -":route".length)))
      for (const name of s.also) if (!known.has(name)) rows.push({ id: `bench/${name}:route` })
      return rows.map((m) => {
        const name = m.id.slice("bench/".length, -":route".length)
        const context = m.together?.context_window || 128000
        const eyes = (m.together?.capabilities || []).includes("vision")
        return {
          ...Model.Info.default(pid, Model.ID.make(name)),
          modelID: m.id,
          name: `${name} (route)`,
          capabilities: { tools: true, input: eyes ? ["text", "image"] : ["text"], output: ["text"] },
          limit: { context, output: Math.min(OUTPUT, Math.floor(context / 4)) },
        } as Model.Info
      })
    }

    let models: Model.Info[] = []
    let seen = ""
    async function look(): Promise<boolean> {
      const r = await fetch(`${s.url}/v1/models`, { headers: { Authorization: `Bearer ${bearer}` } })
      if (!r.ok) throw new Error(`Benchwarmers answered ${r.status}`)
      const next = inventory(((await r.json()) as { data?: Listed[] }).data || [])
      const sig = JSON.stringify(next.map((m) => [m.id, m.limit, m.capabilities]))
      if (sig === seen) return false
      seen = sig
      models = next
      return true
    }

    if (s.models) {
      try {
        await look()
      } catch {} // the server is down: the provider waits, empty, for the next look
      await ctx.provider.transform((editor) => {
        editor.add({
          info: {
            ...Provider.Info.empty(pid),
            name: "Benchwarmers",
            activation: "enabled",
            package: "@opencode/ai/providers/openai-compatible",
            settings: { baseURL: `${s.url}/v1`, apiKey: bearer },
          },
          models,
        })
      })
    }
    if (s.mcp) {
      await ctx.mcp.transform((editor) => {
        editor.set("benchwarmers", {
          type: "remote",
          url: `${s.url}/mcp`,
          oauth: false,
          headers: { Authorization: `Bearer ${bearer}` },
        })
      })
    }
    if (!s.models) return
    // New or removed collections arrive by themselves; a failed look keeps the last list.
    const timer = setInterval(() => {
      look()
        .then((changed) => (changed ? ctx.provider.reload() : undefined))
        .catch(() => {})
    }, s.refresh * 1000)
    return () => clearInterval(timer)
  },
})
