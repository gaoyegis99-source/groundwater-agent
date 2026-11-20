# Postgres Skills setup

This plugin starts `@microsoft/postgres-mcp` as a local stdio process through
`npx`. It does not include database credentials or connect to a database until
you select and connect a profile.

## Prerequisites

1. Install a supported agent: Claude Code, GitHub Copilot CLI, or Codex CLI.
   Follow [Starting with nothing](#starting-with-nothing) if you do not have one yet.
2. Install Node.js with `npx` available on `PATH`, then follow the
   [plugin quick start](../README.md#get-started). Review the MCP server entry
   before enabling it.
3. Use a dedicated, least-privilege PostgreSQL role. Prefer a read-only role and
   a read-only connection profile for exploration.
4. For Azure control-plane operations, install the Azure CLI and authenticate
   with `az login` yourself. The plugin must not initiate an interactive login.

## Starting with nothing

You need an internet connection, an account for one supported AI agent, and
[Node.js LTS](https://nodejs.org/en/download). Node.js includes `npx`, which
runs the bundled PostgreSQL MCP server.

This guide sets up the agent tools, not a PostgreSQL server. Before connecting,
you need a running database you can reach and a dedicated, read-only role.
If you do not have a database, [install PostgreSQL](https://www.postgresql.org/download/)
for development or create a development database with your cloud provider.
Ask your database administrator to provision the role and supply connection details.

1. **Install Node.js LTS**, then open a new terminal and verify:

   ```text
   node --version
   npx --version
   ```

2. **Install one AI agent:**

   **Claude Code** requires a
   [Pro, Max, Team, Enterprise, or Console account](https://claude.com/pricing).
   The free Claude plan does not include Claude Code.

   | Operating system | Installation command |
   | --- | --- |
   | Windows PowerShell | `irm https://claude.ai/install.ps1 \| iex` |
   | macOS, Linux, or WSL | `curl -fsSL https://claude.ai/install.sh \| bash` |

   **GitHub Copilot CLI** requires an active
   [GitHub Copilot subscription](https://github.com/features/copilot/plans).
   Organization administrators must allow Copilot CLI. The npm installation
   requires Node.js 22 or later; Windows also requires PowerShell 6 or later.

   ```text
   npm install -g @github/copilot
   ```

   **Codex CLI** supports ChatGPT Plus, Pro, Business, Edu, or Enterprise, or
   an OpenAI API key.

   | Operating system | Installation command |
   | --- | --- |
   | Windows PowerShell | `powershell -ExecutionPolicy ByPass -c "irm https://chatgpt.com/codex/install.ps1 \| iex"` |
   | macOS or Linux | `curl -fsSL https://chatgpt.com/codex/install.sh \| sh` |

   On Windows, [Git for Windows](https://git-scm.com/downloads/win) is optional
   but recommended for Claude Code and Codex.

3. **Start the agent and sign in:**

   | Agent | Start command | First-time sign-in |
   | --- | --- | --- |
   | Claude Code | `claude` | Follow the browser prompt. |
   | GitHub Copilot CLI | `copilot` | Enter `/login` and follow the prompts. |
   | Codex CLI | `codex` | Select **Sign in with ChatGPT**, or configure an API key. |

   Exit the agent after sign-in so you are back at your normal terminal prompt.

4. **Install Postgres Skills** using the commands in the
   [plugin quick start](../README.md#get-started), then start a new agent session.

5. **Create and connect a read-only profile** using the steps below.

## Configure a database connection

The recommended method is a saved connection profile. Profile metadata is
stored under `~/.postgres-mcp/`; passwords are stored separately in the
operating-system keyring.

Run these commands in your terminal, replacing the example connection details.
The single-line commands work in PowerShell and macOS/Linux shells.
Use your database provider's TLS requirements; `verify-full` requires a trusted
server certificate and a matching host name.

```text
npx -y @microsoft/postgres-mcp connection add my-database "host=db.example.com user=agent_user dbname=app sslmode=verify-full" --access-mode ro
npx -y @microsoft/postgres-mcp connection set-password my-database
npx -y @microsoft/postgres-mcp connection list
```

The password command uses a hidden prompt. Do not put passwords in plugin
configuration, prompts, source files, command arguments, or shell history.

After creating the profile, ask your agent to list the available PostgreSQL
profiles and connect to `my-database`.

## Permissions and approvals

- The PostgreSQL role is the effective security boundary. The MCP server can do
  anything that role is allowed to do.
- Keep profiles read-only unless the task requires writes. Use both
  `--access-mode ro` and database-level read-only privileges.
- The agent may run read-only inspection after you select a connection and allow
  the relevant tools.
- The skills instruct the agent to describe and request confirmation before any
  data, schema, role, extension, configuration, file-import, or Azure resource change.
- These instructions do not enforce approvals at the database layer. Combine
  database privileges, connection access modes, and agent tool permissions.
- Review the target, SQL, command, and expected impact before approving a
  change. Do not enable blanket approval for write tools.
- Prefer development or anonymized data over production data.

## Data access and privacy

The local MCP process can access the selected database and return schema metadata,
query results, and diagnostics to your AI agent. That information becomes part of
your interaction with the agent and follows your AI provider's account and organization
data-handling settings. A local MCP server does not mean the model runs locally.

CSV tools can read only approved local paths. Keep the allowlist narrow and set
`POSTGRES_MCP_DISABLE_CWD_ACCESS=1` if the MCP startup directory should not be
readable.

This plugin starts postgres-mcp with `--no-telemetry` and does not transmit
database content to Microsoft for plugin telemetry. Installing or updating the
MCP package requires access to the npm registry. Azure workflows may communicate
with Azure services through the Azure CLI after you authenticate.

Review the postgres-mcp
[usage and security documentation](https://github.com/microsoft/postgres-mcp/blob/main/USAGE.md)
before connecting sensitive or production databases.

## Remove access

Disconnect the active profile, then remove it:

```sh
npx -y @microsoft/postgres-mcp connection remove my-database
```

Review the operating-system keyring and `~/.postgres-mcp/` if you also need to
remove locally stored credentials or configuration.
