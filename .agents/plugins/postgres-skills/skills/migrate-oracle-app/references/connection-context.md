# PostgreSQL Connection Context

Use explicit invocation context for every target-backed assessment or conversion:

```text
postgres-connection=<saved-profile-name-or-id> database=<exact-database-name> [schema=<schema-name>]
```

Accept natural-language equivalents such as "use profile staging-pg and database orders". Never
accept a password, URI, JDBC URL, or raw connection string in the request.

## Resolution

1. Select the provider and complete exact-name deferred discovery with
   [postgres-provider-routing.md](./postgres-provider-routing.md#exact-name-deferred-tool-discovery).
   Reject wrong-family results and individually probe missing operations before declaring tools
   unavailable; a bounded tool-search response is not a complete inventory.
2. List saved profiles through the selected provider:
   `pgsql_list_connection_profiles` in VS Code Copilot or
   `postgres_mcp_list_connection_profiles` in Copilot CLI.
3. Resolve `postgres-connection` by exact profile name or supported ID. A single unambiguous
   case-insensitive match may be proposed and confirmed; never guess among multiple matches.
4. Use the selected profile to verify `database` exists and connect to that exact database. An
   explicit database always overrides a profile default; never silently fall back to another one.
5. Resolve `schema` from the explicit argument, codebase evidence, or one safe search-path match.
   Ask only when multiple plausible schemas would change object identity.
6. Verify current database, current role, saved-profile access mode, modification-tool availability,
   `default_transaction_read_only`, recovery/replica state, server version/capabilities, Azure
   status, schema/search path, and required extensions before target-backed discovery. Use
   the selected capabilities operation and bounded scalar/exact-name `pgsql_query` or
   `postgres_mcp_query` statements. Never call `postgres_mcp_db_context`, `pgsql_db_context`, or
   any tool ending in `_db_context`, even for one object or connection verification.

The selected query operation (`pgsql_query` or `postgres_mcp_query`) is read-only inspection.
`transaction_read_only=on` there does not mean the saved profile or server is read-only. Determine
write capability from the selected provider's profile metadata, modify-tool availability
(`pgsql_modify` or `postgres_mcp_modify`), `default_transaction_read_only`,
role/database/schema privileges, role-level settings, and `pg_is_in_recovery()`.

When `create-persistent-ddl` is on and code-derived objects are missing, require a saved write-enabled
profile for the selected database and resolved schema, available `pgsql_modify` or
`postgres_mcp_modify`, `default_transaction_read_only=off`, a primary server, and required creation
privileges. This is required even when `test-target-writes` is off. If the profile is read-only or
write capability is unavailable, request a write-enabled saved profile reference for that same
target; never silently downgrade the option or switch providers. Unresolved prerequisites become
an explicit `not-executed` persistent-DDL result and make conversion `PARTIAL`.

When `test-target-writes` is on, require all three values explicitly in the current request:
`postgres-connection`, `database`, and `schema`. Do not derive or inherit any of them. Resolve only
a saved `rw` profile and reject profile-level/server-default read-only or indeterminate access
without attempting a write. Execute authorized DDL, marker fixtures, bounded mutations, sequence
calls, and cleanup through the selected provider's modify operation; never extract profile
credentials, translate them into a connection string, or request credentials through chat.
Production-driver target validation may be reported separately when an approved application
binding exists, but its absence does not block the authorized MCP modification path.

If only one of profile or database is supplied, derive the missing value only when the saved profile
and MCP metadata identify one unique choice; otherwise ask for it. If no context is supplied,
assessment may continue code-only with `ACTION NEEDED`. Conversion may continue independent source
work but cannot claim target validation, use `READY`, or execute `create-persistent-ddl`.

## Persistence and Display

Persist in `assessment-data.json` and `conversion-state.json`:

- Harness/provider selection, server ID, tool family, rendered operation names, profile-store
  source, fallback flags, mixed-provider operation count, and exact-name discovery queries,
  returned identifiers, rejected results, and final availability status
- MCP server ID and a one-way digest of the saved profile reference
- Exact database and resolved schema
- Server type/version, Azure status, saved-profile access mode, modify-tool availability,
  default/inspection transaction modes, recovery state, and relevant capabilities
- Validation timestamp and digest of bounded target contracts
- For target-write authorization: current-request authorization digest, MCP modification target
  digest, privilege/lock validation timestamp, and exact write-impact digest

Never persist or report passwords, connection strings, hosts, usernames, or internal profile IDs.
The preview may show the saved profile's display name supplied by the user, database, schema, access
mode, and server type/version. Customer-readable reports omit the profile reference and identify the
target by sanitized environment label, database, and schema.

Treat VS Code extension profiles and NPM MCP profiles as separate stores. A profile display name
that exists in both stores does not permit cross-provider reuse. Changing provider requires a fresh
run and invalidates all target-dependent evidence and authorizations.

## Context Changes and Resume

Before conversion or resume, compare the supplied context with persisted state. A change to profile,
database, schema, server identity, access mode, or contract digest invalidates prior target metadata,
compatibility findings, database diagram edges, generated DDL proof, and write authorizations.

For `test-target-writes`, also compare role, privileges, relevant lock state, MCP modification
target digest, affected object signatures, fixture plan, sequence manifest, and production entry
points immediately before execution. Any drift invalidates the exact target-write impact and stops
only dependent tests. Never continue under a stale preview or silently reconnect to another target.

Refresh only the target-dependent evidence and affected conversion units. Preserve independent code,
build, test, and coverage evidence when their inputs are unchanged. Record the old/new sanitized
context, invalidated evidence, and refresh result. Never execute a write under context inherited from
a different target.