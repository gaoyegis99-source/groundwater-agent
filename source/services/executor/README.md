# Executor Service

Executes statically compiled Workflow DAGs for confirmed model calls. One logical
service consists of Control, Native Worker, QGIS Worker and the egress-only
Remote HTTP Worker.

Within Control, modules have these responsibilities:

| Module | Responsibility |
|---|---|
| `composition` | Construct stores, clients, Control, publisher and runtime |
| `runtime` | Recover, supervise reconciliation and serial result publication, close owned clients |
| `control_components` | Admission, lease, completion, cancellation, recovery and diagnostics |
| `artifacts` | Durable objects, integrity checks, promotion and disposable workspaces |
| `result_publication` / `result_finalization` / `result_recovery` | Data publication sessions and restart-safe commit/abort |
| `settings` | Read compiled configuration independently of Control |
| `worker` | Private HTTP leases, heartbeats and operator subprocesses |

Worker imports neither Control nor its SQLite module. Completion requests commit
node checkpoints; the single runtime supervisor publishes completed Jobs.
Deployment roots and service URLs are supplied explicitly by generated Compose.

Control accepts Execution Protocol v6 requests containing model and Workflow
identities, canonical arguments and an absolute deadline. It selects the complete
DAG from the generated `executor-config.v8` (or a compatible frozen v6/v7 configuration), verifies the identities and deduplicates requests by
the Kernel's WorkflowRun ID. Model `inputBindings` resolve Data v3 inputs;
`parameterBindings` map model argument names to Workflow parameters, which node
bindings then map to operator parameters.

The wire identity remains the frozen `DataVersionRefV2`. During admission,
Control reads that exact version, rechecks the raw object identity, and projects
only execution-relevant immutable facts from the bound semantic revision and
query conversion receipt: named timezone, null sentinels, declared physical
types, and field ID/name mappings. These facts are checkpointed with the input
bytes and delivered to Workers as `dataContract` metadata. Workers continue to
execute the referenced raw object, so model field parameters retain the uploaded
column names; they do not silently substitute the query Parquet object.

Control owns SQLite schema v9, durable objects, Job/node state, leases and publication checkpoints.
It schedules ready nodes within runtime, Worker capacity and model parallelism
bounds. Workers claim leases through Worker Protocol v3 and execute static
operators in subprocesses using disposable `/work` directories. Control promotes
artifacts to durable objects before committing node success and recovers from
those checkpoints after restart.

`remote_http` nodes contain only a fixed `serviceId` and operation type. The
deployment-owned `RemoteServiceBinding` supplies an HTTPS base address, an exact
allowed host, Bearer/CA secret references, timeouts and version allowlists. The
Worker derives a stable idempotency key from `jobId + nodeId`, coordinates an
ambiguous submit before any retry, checkpoints the remote task/status, verifies
download size and SHA256, and generates `run-receipt.json` without credentials.
Cancellation fences publication immediately; a cancelled remote lease may only
append termination evidence and cannot be revived.

`/internal/metrics` exposes the current expired-active and retained-LOST lease
attempt counts as gauges. Process-local counters report leases marked LOST and
attempt workspaces or orphan object files actually removed by recovery; these
counters reset when Control restarts. No run, lease, or object identifier is a
metric label. A stale worker response remains `LEASE_STALE` even after its
workspace has been reclaimed.

After a successful DAG, Control checks cancellation and deadline before claiming
`FINALIZING`. It stages one named Data output per model `finalOutputs`
entry, in declaration order. File outputs keep their format; value outputs become
canonical JSON. Control
persists the complete output manifest and commit digest at `COMMIT_PENDING`, then
requests one atomic commit. Data's `PUBLISHED` state is the public publication point;
Control reconciles a lost response from the durable group. Kernel receives overall Job status and result references;
node diagnostics stay internal to Executor. Published `DataVersionRefV2` outputs may be
used as later Workflow inputs when their ports match. See the
[architecture overview](../../docs/architecture/architecture-overview.md).

From the repository root, compile the test configuration and run:

```bash
../bin/dev compile --product groundwater
pytest -q services/executor/tests
```
