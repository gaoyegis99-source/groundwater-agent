# PostgreSQL Provider Routing

Select one PostgreSQL MCP provider at run start and use it for the entire run. Provider selection is
based on the harness that owns the agent session, not on which MCP server happens to respond first.
Never mix connection profiles, reads, writes, or cleanup operations across providers.

## Supported Harnesses

### VS Code Copilot, including Copilot Session Target

Native VS Code Copilot uses the extension provider, including when VS Code Agent Host runs the
Copilot SDK harness with the Copilot Session Target. A Copilot CLI process merely connected to
VS Code remains CLI-owned and uses the NPM provider instead.

Requirements:

- Microsoft PostgreSQL extension for VS Code is installed, enabled, and trusted.
- The extension exposes the `PostgreSQL MCP` server and its `pgsql_*` tools.
- A saved extension connection profile resolves the supplied `postgres-connection`.
- Node.js 22+ is installed and `node`, `npm`, and `npx` are on `PATH` for the skill's deterministic
  scripts. The extension does not replace this Node.js requirement.

Use only:

| Logical operation | VS Code PostgreSQL extension operation |
|---|---|
| List profiles | `pgsql_list_connection_profiles` |
| Connect | `pgsql_connect` |
| List databases | `pgsql_list_databases` |
| Server capabilities | `pgsql_get_server_capabilities` |
| Bounded read | `pgsql_query` |
| Authorized modification | `pgsql_modify` |
| Disconnect | `pgsql_disconnect` |

Only after exact-name discovery and individual missing-operation probes, if a required extension
operation remains unresolved, report the exact missing tool and stop
target-backed work. Do not fall back to `postgres-mcp`, `psql`, a
JDBC connection string, or another database server. Independent code-only work may continue with
target validation marked unavailable.

### Copilot CLI

This includes standalone Copilot CLI and Copilot CLI connected to VS Code. The CLI process owns the
agent loop in both cases.

Requirements:

- Node.js 22+ and `npx` are installed and on `PATH`.
- The plugin-bundled `postgres-mcp` server starts from `mcp.json` or `.mcp.json`.
- A saved NPM MCP connection profile resolves the supplied `postgres-connection`.

Use only:

| Logical operation | Bundled NPM MCP operation |
|---|---|
| List profiles | `postgres_mcp_list_connection_profiles` |
| Connect | `postgres_mcp_connect` |
| List databases | `postgres_mcp_list_databases` |
| Server capabilities | `postgres_mcp_get_server_capabilities` |
| Bounded read | `postgres_mcp_query` |
| Authorized modification | `postgres_mcp_modify` |
| Disconnect | `postgres_mcp_disconnect` |

If the bundled server cannot start because `node`/`npx` is missing or package installation fails,
report the exact prerequisite. Tool absence must first pass exact-name discovery and individual
missing-operation probes. Stop target-backed work when a prerequisite remains unresolved. Do not
fall back to VS Code extension tools, including when the CLI is connected to VS Code.

## Provider Preflight

Before profile resolution:

1. Identify the harness owner as `vscode-copilot` or `copilot-cli`.
2. Verify Node.js 22+ and the deterministic skill scripts before any migration stage.
3. Inventory actually visible PostgreSQL tools as `discovery.initialTools`; do not guess rendered
   callable identifiers from the mapping table.
4. Resolve every mapped logical operation from the selected provider only. When tools are deferred
   or unresolved, complete the exact-name discovery protocol below before deciding availability.
5. Reject wrong-provider results and deduplicate repeated identifiers. Confirm missing or ambiguous
   operations with individual exact-name probes before stopping target-backed work.
6. Persist the harness, provider, server ID, rendered tool names, profile-store source, and actual
   discovery queries/results/status.
7. Set `fallbackAllowed=false`, `fallbackAttempted=false`, and
   `mixedProviderOperationCount=0`.
8. Use only the selected provider until disconnect and run completion.

The reference policy implementation is `scripts/postgres-provider-policy.mjs`. Its
`planPostgresToolDiscovery` returns the next required queries and derives availability from actual
visible tools and search results. `resolvePostgresProvider` alone describes the current inventory;
an unresolved inventory is not proof that deferred tools are unavailable.

## Exact-Name Deferred Tool Discovery

When PostgreSQL operations are deferred, use exact full function names in tool search, not a broad
request mixing server display names and generic descriptions. The first selected-provider query is:

**VS Code Copilot**

```text
pgsql_list_connection_profiles pgsql_connect pgsql_list_databases pgsql_get_server_capabilities pgsql_query pgsql_modify pgsql_disconnect
```

**Copilot CLI**

```text
postgres_mcp_list_connection_profiles postgres_mcp_connect postgres_mcp_list_databases postgres_mcp_get_server_capabilities postgres_mcp_query postgres_mcp_modify postgres_mcp_disconnect
```

Tool-search results are bounded, not a complete inventory. A five-result response cannot establish
that the remaining functions are absent. If a response is incomplete or contains only the other
provider's family:

1. Reject wrong-provider results; never call them or switch the selected provider.
2. Retain correctly discovered callable references and their actual identifiers.
3. Search each still-missing or ambiguous operation separately by its exact function name, such as
   `pgsql_query` or `pgsql_get_server_capabilities`. Do not repeat the broad semantic query or rely
   only on another multi-operation response.
4. Merge results, deduplicate repeated identifiers, and re-evaluate the selected provider.
5. Stop with a session-specific missing/ambiguous-tool action only after every unresolved operation
   has its individual probe. A failed search is `search-failed`, not proof of provider absence.

Use at most one exact-name batch plus one individual probe per mapped operation: eight exact-name
searches maximum. Already visible, uniquely resolved tools need no redundant search. Discovery of
`pgsql_modify` or `postgres_mcp_modify` never authorizes its invocation.

Persist `postgresProvider.discovery` with `strategy=exact-operation-names`, actual `initialTools`,
and `searches` containing `query`, `status` (`completed` or `failed`), and `returnedTools`. Failed
searches also contain a sanitized `error` and an empty results array. The final discovery status is
`available`, `unavailable`, `ambiguous`, or `search-failed`; `search-required` is an intermediate
state that must not become a final unavailable-provider claim.

Artifact validation replays this evidence. A single wrong-family or truncated search cannot justify
code-only conversion or an unavailable-provider result. Once the selected tools resolve, use them
for saved-profile resolution, connection, capabilities, and bounded target queries; do not skip
available MCP operations or claim target validation without their execution.

## Prohibited Operations

Never call:

- `pgsql_db_context`
- `postgres_mcp_db_context`
- Any current or future operation whose logical name ends in `_db_context`

Do not use `pgsql_visualize_schema` as migration discovery. It can expand a large schema and bypass
the code-derived, exact-name, OID-bounded protocol. Dashboard-oriented extension operations are also
outside this migration workflow.

## Connection Profiles

Treat VS Code extension profiles and NPM MCP profiles as separate stores unless the selected
provider proves otherwise. A matching display name does not prove matching server, database,
schema, role, access mode, or credentials.

The selected provider must:

- List and resolve the profile.
- Connect to the exact database.
- Verify the exact schema and server identity.
- Determine read/write capability using its own profile metadata and bounded queries.
- Perform every query, authorized modification, and disconnect.

Changing provider invalidates target context, object evidence, persistent-DDL authorization, and
target-write authorization. A run cannot change provider in place; start a new run and refresh all
target-dependent evidence.

## Node.js Failure

Without Node.js 22+, plugin installation can still appear in VS Code and the PostgreSQL extension
may still expose `pgsql_*` tools, but the migration cannot execute its scope detector, Oracle DDL
indexer, artifact validator, policy helpers, or optimized test runner. Stop before migration work
and report Node.js as a missing prerequisite. Do not produce a partial success based only on
database-tool availability.
