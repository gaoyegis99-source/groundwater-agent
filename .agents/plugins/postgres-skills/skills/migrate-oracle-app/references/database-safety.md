# Database Safety and Docker Validation

Follow [postgres-mcp.md](./postgres-mcp.md). Treat the selected MCP connection as authoritative for
target identity and compatibility; use Docker or another disposable scratch database for the fast
mutable loop. Refresh scalar connection facts and exact code-derived object contracts before each
write decision. Never call `postgres_mcp_db_context`, `pgsql_db_context`, or any other tool ending
in `_db_context`; use bounded catalog queries instead. Never place secrets or identifying
connection details in reports.

Docker Desktop is user-controlled local infrastructure. Probe the engine read-only. Use it when
already running, but do not launch/resume Docker Desktop or its service unless
`start-docker-desktop` is explicitly enabled in the current request.

## Missing Objects

When active code references an absent target object:

- Confirm absence with the exact-name identity probe and OID-driven protocol in
  [postgres-mcp.md](./postgres-mcp.md). A timeout or bounded-query failure is not absence.
- If `oracle-ddl-folder` was supplied, use
  [oracle-ddl-source.md](./oracle-ddl-source.md) to locate one unique hash-verified source object
  before reconstructing PostgreSQL DDL. For package members, match the package and member.
- Reconstruct identity, kind, columns/arguments, result shape, dependencies, side effects,
  transactions, and errors from the matched authoritative Oracle DDL plus code, mappings, tests,
  and migration rules.
- If source DDL is absent or unmatched, perform the evidence-graded best-effort reconstruction in
  [oracle-ddl-source.md](./oracle-ddl-source.md), generate deploy/undo DDL, reconcile application
  calls, and validate representative synthetic inputs/data through the production driver in Docker.
- If only the interface is known, generate a labeled fail-closed body that raises an explicit
  missing-Oracle-body error. Never emit a silent no-op, unconditional success, fabricated mutation,
  or fabricated return value.
- Mark inferred or missing-body output `PARTIAL`, preserve exact limitations, and require customer
  validation even when Docker and persistent `CREATE` checks pass.
- If identity is ambiguous, ask one focused question and continue independent work.

Create inferred objects in an approved scratch/test database or Docker environment for validation.
Create them in the persistent PostgreSQL target only when `create-persistent-ddl` is on and the
authorization protocol below passes. When the option is off, generate and validate deploy/undo
scripts but do not issue a persistent write.

Follow the repository migration layout. Otherwise use:

```text
database-ddl/
|-- README.md
|-- 01-<object-group>/deploy.sql
|-- 01-<object-group>/undo.sql
`-- integration-test/{seed.sql,verify.sql,cleanup.sql}
```

Deploy should be rerunnable when safe. Undo removes only objects created by its deploy, in reverse
dependency order.

## Persistent CREATE Authorization

Before an exact write batch:

1. Confirm `create-persistent-ddl` was explicitly supplied in the current request and persist the
   request text/digest as the authorization source.
2. For each behavior-bearing missing object, verify either the indexed Oracle source path/digest or
   the documented best-effort reconstruction evidence, body-completeness classification, synthetic
   validation, limitations, and required customer-validation action.
3. Verify a saved write-enabled profile for the selected database/resolved schema, selected-provider
   modify availability, `default_transaction_read_only=off`, and primary/non-recovery state.
   Refresh only the exact batch objects, direct dependencies, locks, role, and privileges using the
   bounded MCP protocol; never call a `_db_context` tool. Query-tool read-only isolation is not
   profile write capability.
4. Prove the current role can run the DDL or report the required grant.
5. Validate deploy, production-path invocation, behavior, undo, zero-object verification, and
   redeploy in Docker.
6. Record the sanitized target label, database, schema, object list, Oracle source hash, PostgreSQL
   DDL hash, application call-site updates, impact, grants,
   evidence, and rollback in conversion state/report.
7. Recheck exact absence before execution. For an already-present object with a matching contract,
   record `already-present` and fresh verification without writing. Defer incompatible existing
   objects; never use `CREATE OR REPLACE` or `IF NOT EXISTS` to hide a conflict.
8. Execute the exact hash-verified plain `CREATE` statements without prompting through the selected
   modify operation (`pgsql_modify` or `postgres_mcp_modify`) in dependency order. Immediately verify
   each object's identity, kind, and generated contract with the matching query operation and
   bounded catalog probes. Persistent verification is read-only; behavior/undo proof stays in Docker.
9. Persist each object's `persistentCreate` status, authorization source/context, modify-tool
   execution timestamp/result/DDL hash, and query-tool target verification. Reconcile
   `optionResults.create-persistent-ddl`; deploy/undo files or Docker-only creation cannot satisfy it.

The option is bounded write authorization, not blanket database access. Changed SQL requires fresh
Docker proof and a new recorded hash. Changed profile, database, schema, server identity, or access
mode invalidates authorization unless `create-persistent-ddl` is explicitly present in the current
request for that newly resolved context. If any prerequisite fails, record `not-executed`, the exact
reason and next action, and conversion `PARTIAL`; defer only dependent work. If all checks pass,
execute rather than handing generated SQL back for manual deployment. An inferred or fail-closed
body alone does not defer creation. This stage never requires `test-target-writes` to be enabled.

A failed CREATE or target-verification call is not success. Record `failed` and preserve the exact
error or indeterminate outcome. Do not automatically execute undo in the persistent database:
`create-persistent-ddl` does not authorize `DROP`. Refresh exact target state before a retry, since
an uncertain tool response may have left the object present. Keep verified independent creations.

Never automatically alter, replace, drop, seed, truncate, or modify data in an existing persistent
object. The only exception is the exact marker-owned runtime test plan explicitly authorized by
`test-target-writes` under the protocol below. Persistent removal or any other non-`CREATE` write
requires a separate explicit request and just-in-time confirmation.

## Authoritative Target Runtime/Write Authorization

`test-target-writes` is conversion-only authorization for bounded runtime testing against the exact
saved write-enabled MCP profile, database, and schema supplied in the current request. It is
independent from `create-persistent-ddl`, defaults to off, and never authorizes object creation.

Run a target-write scenario only after its corresponding Docker test has invoked the same production
entry point through the PostgreSQL driver and passed its assertions and cleanup. Immediately before
the target test:

1. Re-resolve the saved profile, exact database, and exact schema. Verify saved-profile `rw` mode,
   selected-provider modify availability (`pgsql_modify` or `postgres_mcp_modify`),
   `default_transaction_read_only=off`, and primary/non-recovery state. Do not classify the profile
   from `pgsql_query` or `postgres_mcp_query` inspection isolation. If true write capability is
   unavailable, mark the requested gate `not-executed`.
2. Refresh server identity, current database, role, profile access mode, default transaction mode,
   required capabilities, MCP modification target digest, and code-derived object contracts.
3. Verify only required `INSERT`, marker-scoped `DELETE`, routine execution, and sequence privileges.
   Query relevant locks for every affected relation, sequence, and routine. Do not probe privileges
   by writing.
4. Recompute the target-context and write-impact digests. Any drift in profile, database, schema,
   server, role, access mode, privileges, object signature, production path, fixture plan, or
   sequence manifest invalidates authorization and defers only dependent scenarios.
5. Persist an exact impact preview: affected schema-qualified objects; planned inserted row count
   per table; state-mutating routine calls; expected sequence call count; production entry points;
   assertions for outputs, generated values, joins, transactions, errors, and side effects;
   cleanup order and exact ownership predicates; and permanent non-rollback effects.

### Target Fixture and Call Rules

- Use one unique `MIG_TEST_<run-id>` marker for the target-write batch. Every inserted row must be
  provably marker-owned through a marker-bearing value plus ledgered primary/unique keys. If a
  required table cannot safely carry or prove marker ownership, do not test that path on the target.
- Execute authorized target DDL, marker DML, sequence calls, mutation assertions, and cleanup
  through the selected provider's modify operation (`pgsql_modify` in VS Code Copilot or
  `postgres_mcp_modify` in Copilot CLI). Use only the matching selected query operation for
  read-only pre/post verification; `transaction_read_only=on` is inspection evidence, not target
  capability evidence.
- Insert the smallest coherent graph in parent-first order. Never update or delete existing customer
  rows, reuse ambiguous rows as mutable fixtures, or run `TRUNCATE`, `ALTER`, `REPLACE`, or `DROP`.
- Do not create schemas, tables, routines, collations, extensions, or other objects. A missing object
  remains a separate `create-persistent-ddl` concern.
- Match each target mutation to a production behavior already proven through the real PostgreSQL
  driver in Docker. Execute the bounded authoritative mutation through the selected provider's
  modify operation.
  Production-driver execution against the authoritative target is separate evidence when an
  approved application binding exists; its absence does not make the MCP profile read-only.
- Permit `nextval()` only when active code references that exact sequence and the production path
  consumes it. Record every returned value and the calling entry point. Sequence advancement cannot
  be rolled back or rewound and is an acceptable permanent side effect only because the option
  explicitly authorizes and records it.
- Verify meaningful results and side effects before cleanup: returned values, generated IDs, joins,
  transaction boundaries, expected errors, and exact marker-owned row counts.

### Cleanup and Completion

Clean children before parents using predicates that match both the ledgered keys and exact marker.
Delete only marker-owned synthetic rows. After cleanup, query every affected table with its exact
marker ownership predicate and require zero remaining rows. Also verify expected transaction and
routine side effects are absent or restored; do not claim cleanup from a client success response
alone.

If cleanup fails or any marker-owned row remains, stop all further target-write scenarios for that
target, preserve the ledger and sanitized evidence, report `ACTION NEEDED`, and provide the exact
manual cleanup statement for review without executing broader deletion. Continue independent
source, Docker, coverage, packaging, and read-only target work. Never hide cleanup failure behind a
transaction rollback or a success-shaped summary.

## Synthetic Data

- Inspect approved scratch/test data first. Reuse existing usable rows when they satisfy the test
  contract and doing so is permitted; record identifiers without copying sensitive values.
- Generate synthetic data only for missing cases or empty required tables.
- Build the smallest coherent table graph needed by production paths.
- Inspect required columns, keys, checks, triggers, sequences, views, and routines.
- Use obvious synthetic values and a unique `MIG_TEST_<run-id>` marker.
- Insert parents before children; clean children before parents.
- Make seed rerunnable and delete only exact-marker rows.
- Use real sequences when testing them and never rewind a shared sequence.
- Assert exact counts, joins, views, generated values, trigger effects, outputs, and cleanup.
- Maintain a fixture ledger for every integration-test setup/reset. For each table record the
  sanitized environment, fixture load count, exact rows inserted, exact safe rows reused, peak
  marker-owned rows present, cleanup method, rows deleted or rolled back, and rows remaining.
- Count successful row insertions from database/test evidence, not from the number of SQL statements
  or fixture-file lines. If the same rows are recreated after cleanup, count each successful load
  and expose the load count.
- Map each fixture group to the test/scenario, production entry point, PostgreSQL objects, behavior
  exercised, and exact assertions/outcomes.
- For every Docker production-path test, state whether rows are required. When required, create
  marker-owned coherent rows and persist the ledger. When not required (for example sequence-only,
  DDL-only, or a routine contract double that safely returns generated constants), persist an exact
  no-row reason. Docker success without either a fixture ledger or an exact no-row reason is
  incomplete evidence.

Use Docker by default. Persistent synthetic data is permitted only under explicit
`test-target-writes` authorization and the target runtime/write protocol above.
Never overwrite, replace, truncate, or repurpose customer rows. Never copy production data into
Docker unless the user explicitly approves a sanitized export.
Never persist synthetic row values or reused customer values in state/reports; report only marker,
counts, table identities, behavior, and sanitized evidence paths.

## Docker End-to-End Lifecycle

Create one run-scoped Docker environment using the target PostgreSQL major version and required
extensions. Reuse it across conversion units. Reset only marker-owned fixtures between iterations.
Retain it across unit/integration test processes and the complete suite, not just within one JVM.
The primary owns setup/cleanup; generated test methods and JVM/class hooks must not create or stop
their own PostgreSQL containers when the shared endpoint is supplied. Verify production-driver
endpoint binding and scenario isolation. Use the ownership/configuration labels and runner
environment contract in [performance-and-execution.md](./performance-and-execution.md).
Recreate the container only when image, extensions, bootstrap DDL, configuration, or contamination
invalidates it.

```text
clean container
  -> base schema
  -> deploy
  -> inspect contract
  -> seed
  -> invoke production code paths
  -> verify outputs and side effects
  -> cleanup
  -> undo
  -> prove base objects remain
  -> redeploy
```

Use the production PostgreSQL driver, configuration path, repository, and service. Exercise every
active converted function/procedure from the production method that calls it. Cover relevant null,
boundary, error, OUT/INOUT, result-set, refcursor, precision, timestamp, JSON, LOB, generated-key,
long-identifier, and transaction behavior.

Capture sanitized diagnostics and remove every process and container started by the skill, including
after failure. Remove only the exact ownership-verified container at migration-run end; never
retain it across invocations or enable global Testcontainers reuse.

## Database Test Environment Reporting

`conversion-report.md` contains **Database Test Environment**. Begin with a direct statement of
persistent object/customer-data impact. Do not include a cleanup-status legend. Put row-level
cleanup statuses directly in the synthetic-data table.

Then include:

### Synthetic Test Data

| Environment | Table | Fixture loads | Rows created | Peak rows present | Rows reused | Rows cleaned / rolled back | Rows remaining | Status |
|---|---|---:|---:|---:|---:|---:|---:|---|

Use schema-qualified table names and exact integer counts. `Rows created` is the total successful
insertion events across fixture loads. `Rows remaining` is measured after final cleanup and must be
zero for a clean run.

### Database Objects Exercised with Synthetic Data

First include an object-kind summary:

| Object kind | Active / code-derived in tested scope | Exercised | Evidence / gap |
|---|---:|---:|---|

Always include functions and procedures even when both counts are zero. Include every applicable
kind derived from active code: function, procedure, sequence, table, view/materialized view, trigger,
and native query path. Distinguish objects merely discovered or bootstrapped from objects actually
invoked by a production application path.

Then include the detailed execution ledger:

| Object / signature | Kind | Production entry point | Synthetic fixtures | Invocation form | Assertions / side effects | Environment | Result |
|---|---|---|---|---|---|---|---|

Use schema-qualified identities. For functions/procedures include argument types, OUT/INOUT and
return shape; for overloaded routines create one row per tested signature. State `SELECT`,
`CALL`, repository/native-query, generated-value, trigger-causing DML, or other invocation form.
Name exact fixture tables/row counts, returned/generated values, joins, transaction/error behavior,
side effects, and cleanup proof. Include explicit not-exercised rows for active objects blocked by a
missing contract/environment. Standalone SQL setup or catalog discovery never counts as production
path execution.

For each active code-referenced routine, query, sequence, view, and trigger in the converted scope,
classify it as exercised, contract-only, blocked, or out of scope. Every exercised routine must be
called through its production application path with the real driver and meaningful fixture/result
assertions. A test-created routine returning literal values must be labeled as a Docker contract
double and must not be presented as authoritative business-semantics validation.

### Behavior Exercised

| Test / scenario | Production entry point | PostgreSQL objects | Fixture tables / rows | Behavior exercised | Assertions / observed outcome | Result |
|---|---|---|---|---|---|---|

Include service/repository/DAO methods or other production entry points, not only test-class names.
Name query/routine/transaction behavior and the meaningful result or side effect asserted. When no
synthetic data was created, keep the section and add one `➖ None created` row explaining whether
safe existing rows, DDL-only checks, or no data-dependent integration tests were used.

## Authoritative Target Validation

Use Docker for fast iteration, then validate against the selected Azure PostgreSQL/MCP target:

- Refresh exact object signatures, server capabilities, schema/search path, and extensions.
- Run read-only contract and query checks whenever possible.
- When `test-target-writes` is off, do not run write-capable target checks.
- When `test-target-writes` is on, follow the exact target runtime/write protocol above; a shared
  schema is acceptable only when marker isolation, privileges, locks, production-path execution,
  and cleanup can all be proven.
- Never infer authority from Docker when the actual target contract differs.
- Report three separate outcomes:
  1. **Docker PostgreSQL runtime validation**
  2. **Authoritative target contract validation**
  3. **Authoritative target runtime/write validation**
- Mark authoritative target runtime/write validation `✅ PASSED` only when every authorized
  production path succeeds, every assertion passes, all sequence values are recorded, cleanup
  completes, and zero marker-owned rows remain in every affected table. An explicitly authorized
  and recorded sequence increment is an acceptable permanent non-data side effect.
- If Docker passes but required authoritative target validation is unavailable or target writes are
  disabled, use `RUNTIME VALIDATION NEEDED`, not `READY`. When `test-target-writes` was explicitly
  enabled and applicable writes did not execute, use `PARTIAL` and `⚠️ Not executed` instead.