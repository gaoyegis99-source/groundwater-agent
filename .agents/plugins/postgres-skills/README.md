# postgres-skills plugin

The installable plugin payload for the `postgres-skills` marketplace. It bundles
the PostgreSQL skill and the `postgres-mcp` MCP server so AI-agent CLIs (GitHub Copilot CLI,
Claude Code, Codex) can route PostgreSQL work to the right guidance and tools.

The `migrate-oracle-app` skill adds Oracle application assessment and conversion,
using the same MCP server. See the [migration guide](MIGRATION.md) for usage.

## Contents

| Path | Purpose |
| --- | --- |
| `skills/postgresql-best-practices/` | The skill (SKILL.md routing table + reference docs). |
| `skills/migrate-oracle-app/` | Oracle application migration skill, references, and scripts. |
| `tests/` | Migration script and documentation contract tests. |
| `package.json` | Migration test and script commands. |
| `plugin.json` | Agent Plugins v1.0 manifest used by GitHub Copilot. |
| `mcp.json` | Agent Plugins v1.0 declaration for the `postgres-mcp` server. |
| `.mcp.json` | Declares the `postgres-mcp` MCP server (local stdio through `npx`, with optional telemetry disabled). |
| `SETUP.md` | Connection, permissions, data-access, privacy, and removal guidance. |
| `.claude-plugin/plugin.json` | Plugin identity and display metadata for Claude Code. |
| `.codex-plugin/plugin.json` | Plugin manifest required by Codex. |

## Plugin manifests

Codex requires a manifest at **`.codex-plugin/plugin.json`** inside the plugin folder.
Without it, `codex plugin add postgres-skills@postgres-skills` fails with
`Error: missing plugin.json`.

Claude Code reads plugin metadata from **`.claude-plugin/plugin.json`**. GitHub
Copilot reads the Agent Plugins v1.0 **`plugin.json`** and **`mcp.json`** files at
the plugin root. Keep the host manifests and the listed plugin version in
`.github/plugin/marketplace.json` at the same release version. The marketplace's
`metadata.version` is versioned independently.

## Security and privacy

The plugin launches
[`@microsoft/postgres-mcp`](https://github.com/microsoft/postgres-mcp) locally
through `npx`. It can read from and write to the database permitted by the
selected PostgreSQL role, and its CSV tools can read explicitly approved local
paths. Database information returned by the MCP server becomes part of the
user's AI-assistant interaction.

The bundled configuration disables postgres-mcp telemetry. Use a dedicated,
least-privilege role and a read-only profile for exploration. The skills require
confirmation before database writes, file imports, graph writes, or Azure
resource changes. See [SETUP.md](SETUP.md) for the complete access and data-use
disclosure.
