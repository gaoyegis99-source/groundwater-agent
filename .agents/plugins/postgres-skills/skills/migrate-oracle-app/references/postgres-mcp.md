# PostgreSQL MCP Requirement

Target-backed assessment and conversion require the provider selected by
[postgres-provider-routing.md](./postgres-provider-routing.md):

- VS Code with the Copilot Session Target uses the Microsoft PostgreSQL extension's
  `PostgreSQL MCP` server and `pgsql_*` operations.
- Copilot CLI uses the plugin-provided `postgres-mcp` server and
  `postgres_mcp_*` operations, started from root `mcp.json`/`.mcp.json` with
  `@microsoft/postgres-mcp@latest`.

Never fallback or mix providers within a run.

## Activation

1. Select the provider and complete exact-name deferred tool discovery with
   [postgres-provider-routing.md](./postgres-provider-routing.md#exact-name-deferred-tool-discovery).
   Reject wrong-provider results and individually search unresolved function names; never treat one
   bounded response as proof that the selected provider is absent.
2. Verify the selected server and all required mapped tools are available.
3. Resolve invocation inputs with [connection-context.md](./connection-context.md).
4. Connect using the exact saved profile, database, and schema context from that provider.
5. Read server capabilities, then verify scalar database/schema facts with the selected bounded
   query operation (`pgsql_query` or `postgres_mcp_query`); cache them in assessment state.
6. Refresh only the affected scalar or object probes after connection changes, target drift, or
   contradictory evidence.

The selected server may advertise `pgsql_db_context` or `postgres_mcp_db_context`. Availability is
not permission to call it.
This migration workflow never invokes any tool whose function name ends in `_db_context`.

Never request or echo a password. Profiles hold metadata under the MCP configuration directory and
passwords belong in the operating-system credential store. Prefer a least-privilege read-only profile
for assessment.

If tools remain unresolved after the required exact-name probes, or the selected server, profile,
or target cannot be used, continue code-only assessment and independent
source work. Report `ACTION NEEDED` with the exact setup action. Do not use assessment `BLOCKED`,
guess target contracts, or claim target validation.

## Required Use

Use the selected provider for:

- Connection/profile discovery and exact target selection
- Server capability and Azure detection
- Bounded catalog and schema inspection derived from active codebase evidence
- Read-only contract, query, routine, and validation checks
- Authorized scratch/target DDL or DML when a write-enabled profile is explicitly selected
- Pre/post inspection and cleanup proof for `test-target-writes`; invoke the behavior under test
  through the production PostgreSQL driver path rather than treating a standalone MCP query as
  application validation

Do not silently substitute the other provider, generic `postgres-mcp`, `psql`, a codebase
connection string, or another MCP for authoritative target work. The production PostgreSQL driver
remains the authority for Docker end-to-end call behavior.

## Bounded Target Discovery

PostgreSQL MCP is not a schema-export mechanism. Discover only the contract required by active
code.

### 1. Build the Object Manifest Before MCP Calls

Extract and deduplicate from production code, mappings, configuration, SQL, tests, and migration
rules:

- Exact or safely resolved schema
- Object name and expected kind: table, view, materialized view, sequence, routine, or type
- Referenced columns and expected Java/SQL types
- Routine name, call form, argument count/names/types, and expected result shape
- Sequence consumers, generated-key behavior, and direct object dependencies
- Source evidence path and symbol for every requested object

Keep unresolved dynamic identifiers separate. Do not replace an unknown name with a whole-schema
inventory. Ask only if multiple plausible identities would change the conversion; otherwise report
the bounded unknown and continue.

### 2. Prohibit All Database-Context Tool Calls

- Never call `postgres_mcp_db_context`, `pgsql_db_context`, or any current/future MCP tool whose
  function name ends in `_db_context`.
- This prohibition applies to an entire database, one schema, one object, connection verification,
  capability discovery, timeout recovery, and attempts described as compact or read-only.
- Do not call `_db_context` once to test whether it is fast enough. Its advertised availability is
  not authorization, and no exact-object exception exists.
- Use the selected provider's list-databases and capabilities operations, and bounded
  `pgsql_query` or `postgres_mcp_query` statements for scalar connection facts and exact catalog
  objects.
- Do not use `pgsql_visualize_schema`; it can expand the selected schema beyond the code-derived
  manifest.
- Never scan all `information_schema` rows, call `SELECT *` on catalogs, enumerate unrelated
  objects, fetch table rows, or request row counts/sizes unless active code or a write-safety
  decision specifically requires them.

If a `_db_context` call is attempted despite this rule, record a policy violation, discard its
response, do not retry it, and switch to the bounded alternatives below. The run cannot pass
artifact validation. Do not clear the nonzero counter or violation from that run; produce a fresh
run with zero `_db_context` calls.

### 3. Resolve Identity Before Detail

Use the selected provider's bounded query operation (`pgsql_query` or `postgres_mcp_query`) against
`pg_catalog` in bounded batches:

1. Verify connection identity with one scalar-row query; never derive it from `_db_context`.
2. Probe only object identity: schema, name, kind, and OID for exact manifest names.
3. Normally batch at most 10 names of one object kind in an identity probe; 20 is the hard maximum.
4. Normally fetch details for at most 5 returned relation/routine OIDs per query; 10 is the hard
   maximum.
5. Retrieve only referenced columns plus keys/defaults needed by mappings or SQL.
6. Query constraints/dependencies only where `conrelid`, `confrelid`, sequence OID, routine OID, or
   type OID belongs to the current batch.
7. Add a directly discovered dependency to a later bounded batch only when it affects compilation,
   SQL semantics, generated keys, Docker bootstrap, or DDL ordering. Do not recursively expand the
   full dependency graph.

Use a scalar connection query equivalent to:

```sql
SELECT current_database() AS database_name,
       current_schema() AS schema_name,
       current_setting('search_path', true) AS search_path,
       current_setting('default_transaction_read_only', true) AS default_transaction_read_only,
       pg_catalog.pg_is_in_recovery() AS is_in_recovery,
       pg_catalog.version() AS server_version
LIMIT 1;
```

Resolve one exact schema with:

```sql
SELECT n.oid, n.nspname
FROM pg_catalog.pg_namespace AS n
WHERE n.nspname = <exact_schema>
LIMIT 1;
```

For relations, the identity probe should be equivalent to:

```sql
SELECT c.oid, n.nspname, c.relname, c.relkind
FROM pg_catalog.pg_class AS c
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = <exact_schema>
  AND c.relname = ANY (<exact_manifest_names>)
  AND c.relkind IN ('r','p','v','m','S')
ORDER BY c.relname
LIMIT 25;
```

Then query `pg_attribute`, `pg_attrdef`, and `pg_constraint` using only returned OIDs. For routines,
probe `pg_proc` joined to `pg_namespace` by exact schema/name with a limit of 25, then retrieve only
matching overload signatures. Query privileges with exact `has_database_privilege`,
`has_schema_privilege`, `has_table_privilege`, or `has_sequence_privilege` calls and relevant locks
by exact OID. Safely quote literals or use tool-supported parameters; never concatenate database
content into follow-up SQL.

For one returned relation OID, retrieve only code-referenced columns with a query equivalent to:

```sql
SELECT a.attname,
       pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
       a.attnotnull,
       pg_catalog.pg_get_expr(d.adbin, d.adrelid) AS default_expression
FROM pg_catalog.pg_attribute AS a
LEFT JOIN pg_catalog.pg_attrdef AS d
  ON d.adrelid = a.attrelid AND d.adnum = a.attnum
WHERE a.attrelid = <exact_relation_oid>
  AND a.attnum > 0
  AND NOT a.attisdropped
  AND a.attname = ANY (<code_referenced_columns>)
ORDER BY a.attnum
LIMIT 50;
```

For exact routine names, use a bounded identity/signature query equivalent to:

```sql
SELECT p.oid,
       n.nspname,
       p.proname,
       pg_catalog.pg_get_function_identity_arguments(p.oid) AS identity_arguments,
       pg_catalog.pg_get_function_result(p.oid) AS result_type
FROM pg_catalog.pg_proc AS p
JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
WHERE n.nspname = <exact_schema>
  AND p.proname = ANY (<exact_manifest_names>)
ORDER BY p.proname, p.oid
LIMIT 25;
```

### 4. Use Progressive Contract Waves

Query in this order and stop when the active code contract is proven:

1. Object existence, kind, and OID
2. Referenced columns or routine identity arguments/results
3. Relevant primary/unique/foreign keys, defaults, and sequence ownership
4. Index, trigger, extension, privilege, or dependency detail only when required by observed
   application behavior, Docker validation, or an authorized write

When identity, privilege, lock, recovery, and capability checks are independent, issue their bounded
read-only queries in parallel through the client parallel-call facility. Keep each query/result
distinct in state. Never parallelize database mutations or race metadata reads with a write.

Do not retrieve the full table definition when code uses two columns. Do not retrieve every index,
constraint, trigger, or referencing object merely because one table was found.

### 5. Timeout Handling

When a bounded MCP query call times out:

1. Record the tool, elapsed time, exact requested objects, and query/batch digest.
2. Never repeat the identical request.
3. Do not increase scope, call any `_db_context` tool, or silently switch database tools.
4. If a bounded batch failed, split it into smaller same-kind batches; reduce detail to identity
   only before retrying.
5. If a single-object identity probe fails after one materially narrower attempt, mark only that
   object's authoritative validation `ACTION NEEDED`, continue independent work, and retain Docker
   evidence separately.

A historical or accidental `_db_context` timeout is recorded as a policy violation, not as an
eligible request to split or retry. Start directly with the scalar, exact-name, or OID-bounded query
appropriate to the required fact.

A timeout is not evidence that an object is absent. Never create DDL based on a timed-out lookup.

### 6. Cache and Refresh Precisely

Persist the manifest, batch membership, returned OIDs/signatures, query digests, elapsed time,
validation timestamps, and per-object result/status. Reuse unchanged results. On source or target
drift, refresh only objects whose evidence, identity, signature, or direct dependency changed.

## Writes

Before any MCP write:

- Refresh server, database, schema, role, access mode, object state, and privileges.
- Record the exact SQL, target, impact, affected objects/rows, and rollback or cleanup.
- For missing-object `CREATE` statements, execute without prompting only when
  `create-persistent-ddl` was explicitly supplied in the current request and all
  [database-safety.md](./database-safety.md) checks pass.
- For bounded marker-owned test DML and active code-derived sequence calls, execute without another
  prompt only when `test-target-writes` was explicitly supplied in the current request, the exact
  impact remains unchanged, the corresponding Docker production-path test passed, and all
  [database-safety.md](./database-safety.md) checks pass.
- Reject a read-only or indeterminate profile before any target-write attempt. A privilege check is
  read-only; do not probe write access by mutating the target.
- Execute every write and cleanup through the selected provider's modify operation:
  `pgsql_modify` in VS Code Copilot or `postgres_mcp_modify` in Copilot CLI.
- `test-target-writes` never permits `CREATE`, customer-row changes, `TRUNCATE`, `ALTER`, `REPLACE`,
  or `DROP`. Other writes require a separate explicit request and just-in-time confirmation.
- Execute MCP fixture setup, state verification, and cleanup only through the selected write-enabled
  profile. Execute the behavior under test through the production PostgreSQL driver using a secure
  binding whose target digest matches that profile context.
- Verify the result and record a sanitized summary and MCP read/write counts. For target runtime
  testing, record the fixture ledger, every sequence value consumed, production entry points and
  assertions, cleanup deletes, and per-table zero-remaining proof.

Never fall back from an unauthorized or failed write to another connection or tool. A deferred write
stops only dependent work.