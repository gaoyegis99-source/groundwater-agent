# Migration Options

Assessment is database-read-only and tracked-source read-only by default. Options permit specific
assessment or conversion actions and all default to `off` unless the user explicitly enables them.
An enabled option permits its bounded action. Explicit `create-persistent-ddl` is also authorization
for the create-only database writes defined below.

## Canonical Options

- `establish-baseline`: make the minimum reversible dependency, build, test-runner, or coverage
  harness edits needed to compile and measure a pre-conversion baseline without changing production
  behavior.
- `create-shims`: create isolated temporary shims for genuinely unavailable dependencies during
  baseline enablement or conversion.
- `create-persistent-ddl`: create evidence-backed missing objects in the selected persistent
  PostgreSQL target after reconstruction/conversion and Docker proof, without an additional prompt.
  Execute the generated create-only DDL and verify the target objects; SQL files alone are not the
  requested result.
- `test-target-writes`: run bounded authoritative runtime tests against the exact selected
  PostgreSQL target using marker-owned synthetic rows and active code-derived sequence calls after
  the corresponding Docker production-path tests pass. This option never authorizes object
  creation and is separate from `create-persistent-ddl`.
- `start-docker-desktop`: permit one bounded attempt to launch or resume an installed Docker Desktop
  application when the Docker engine is not already running. It defaults off.
- `remove-unused-artifacts`: permit removal of repository files whose only disposition is proven
  unused, including checked-in JARs and other vendored binary artifacts. It defaults off.
- `create-unit-tests`: add focused unit and characterization tests for converted code with missing
  coverage. When enabled, run at least two bounded unit/characterization waves; further waves up to
  ten require a specific additional coverage opportunity.
- `create-integration-tests`: add PostgreSQL/Docker integration and end-to-end tests for converted
  database behavior. When enabled, independently run at least two bounded integration waves through
  the production boundary, reusing one run-scoped Docker environment; further waves up to ten
  require a specific additional coverage opportunity.
- `fix-broken-tests`: edit stale or broken affected tests while preserving valid behavior.

`tests` is a convenience alias that enables `establish-baseline`, `create-unit-tests`,
`create-integration-tests`, and `fix-broken-tests`. It does not enable `test-target-writes`.
It does not enable `start-docker-desktop` or `remove-unused-artifacts`. There is no `all` alias
because shims, persistent database creation, target writes, desktop state changes, and file
deletions must remain explicit choices.

## Invocation

Accept a comma-separated argument:

```text
options=establish-baseline,create-shims,create-persistent-ddl,tests,test-target-writes,start-docker-desktop,remove-unused-artifacts
```

Natural language is equivalent, for example: "Enable dependency shims and all test support, but do
not create persistent database objects." Expand aliases into canonical booleans. Do not infer an
option from a normal assessment or conversion request.

## Resolution and State

Before assessment tries baseline enablement and before the conversion preview:

1. Start all canonical options as `off`.
2. Apply only explicit invocation or natural-language selections.
3. Persist every canonical boolean and the user statement that enabled it in
   `assessment-data.json`, then carry it into `conversion-state.json`.
4. Show every option and expected action before the relevant stage.
5. Ask only when the user's wording is contradictory or cannot resolve to one option state.

An assessment may recommend options, but recommendations do not enable them. Before tracked
baseline-enablement edits, show the exact files, reason, behavior-preservation evidence, commands,
expected baseline scope, rollback, and Git impact. In standalone `assess`, ask once for that
unchanged batch. In `assess-and-convert`, the invocation authorizes the batch when its required
options are explicitly on; persist the preview and continue without prompting. Standalone `convert`
still requires source approval, while `assess-and-convert` proceeds directly into reversible
conversion edits. Changing an option or batch after authorization requires updating state and
re-previewing the affected scope. Completed work is not undone automatically when an option is
later disabled.

## Enforcement

- An enabled option permits the action only when its normal evidence and safety checks pass.
- A `NO_CONVERSION_REQUIRED` scope decision takes precedence over every option. Baseline enablement,
  test creation/repair, Docker startup, persistent DDL, target writes, and unused-artifact removal
  are not applicable and must not execute.
- Probe Docker availability without starting anything. If the engine already responds, use it
  whether `start-docker-desktop` is on or off and do not relaunch Desktop.
- When `start-docker-desktop` is off and the engine does not respond, never launch Docker Desktop,
  run `docker desktop start`, start/resume a Docker service, start a Docker WSL distribution, or
  otherwise change Docker's active state. Record the exact unavailable integration limitation and
  continue independent work.
- When `start-docker-desktop` is explicitly on, it permits one bounded launch/resume attempt for an
  already-installed Docker Desktop application and a bounded readiness wait. It does not permit
  installation, update, login-item/autostart changes, settings changes, license acceptance, service
  reconfiguration, or repeated restart loops. Record the command/action, prior/final state, elapsed
  readiness time, and result.
- When `remove-unused-artifacts` is off, do not delete a repository file merely because no active
  use was found. Retain it and record path, kind, digest, reference/build/package evidence, and
  classification `Unused — retained`.
- When `remove-unused-artifacts` is explicitly on, removal requires an unchanged baseline digest,
  zero active code/build/package references, an exact rollback command, and passing post-removal
  build, complete tests, package, and archive inspection. Do not remove user-owned or ambiguous
  files. The option never authorizes cleanup outside the repository.
- Normal build-tool cleanup of generated output directories and overwrite/removal of validated
  skill-owned `.github/migrate-oracle-app` artifacts are not unused-artifact removal.
- `establish-baseline` permits only non-semantic dependency declarations, build/test-runner
  profiles, and coverage harness changes needed for measurement. Removal of an unused dependency
  declaration or checked-in artifact additionally requires `remove-unused-artifacts`. It never
  permits Oracle-to-PostgreSQL SQL, provider, datasource, mapping, or runtime-behavior conversion.
  This includes adding a compatible managed test runner/adapter such as `junit-vintage-engine`, a
  .NET test adapter, a Python test plugin, or CTest registration when source inventory proves tests
  exist but native discovery cannot execute them.
- An unavailable active API needs `create-shims` as well as `establish-baseline`; never hide a
  missing API behind a guessed implementation.
- `create-persistent-ddl` must be explicitly present in the current invocation or equivalent
  unambiguous current-request language. It pre-authorizes only `CREATE` statements for missing
  codebase-owned objects in the exact resolved target. It never bypasses privileges, fresh target
  checks, Docker validation, exact DDL hashing, rollback generation, or immediate verification.
  `oracle-ddl-folder` is source evidence, not authorization. When a missing routine, package
  member, trigger, view, materialized view, or type has no unique indexed Oracle DDL source,
  persistent creation may use a documented best-effort reconstruction after representative
  synthetic Docker validation. Missing-body implementations must fail closed, remain `PARTIAL`,
  and carry a required customer-validation action; silent no-op/unconditional-success bodies remain
  prohibited.
  Record the batch and execute without prompting. It never permits `ALTER`, `REPLACE`, `DROP`,
  seeding, DML, or customer-data changes.
- With `create-persistent-ddl` on, actual target creation and immediate catalog verification are
  required when missing objects and passing safety evidence exist. Execute through the selected
  provider's modify operation (`pgsql_modify` or `postgres_mcp_modify`), then verify identity, kind,
  and contract through its matching query operation. The option is independent of
  `test-target-writes`, `tests`, and test-generation options. An inferred or fail-closed body is not
  by itself a reason to skip creation after the reconstruction protocol passes.
- Resolve write capability for `create-persistent-ddl` during preflight, not only for target-write
  tests. Require a saved write-enabled profile for the selected database/schema, available modify
  operation, writable server/role defaults, and a primary server. If unavailable, request a
  write-enabled saved profile reference; never request credentials or silently change profiles.
- Reconcile `optionResults.create-persistent-ddl` and every missing object's `persistentCreate`
  result. Only `created` with successful modify execution and immediate verification, or
  `already-present` with a fresh matching contract and no replacement, satisfies an applicable
  object. Record unmet safety prerequisites as `not-executed` with an exact reason and next action,
  and make conversion `PARTIAL`, not `RUNTIME_VALIDATION_NEEDED`. A failed write/verification is
  `failed`; retain successful independent creations and never automatically run target undo DDL.
  `not-applicable` is valid only when the code-derived missing-object manifest is empty.
- `test-target-writes` must be explicitly present in the current invocation or equivalent
  unambiguous current-request language. It authorizes only the exact impact preview for the saved
  write-enabled MCP profile, exact database, and exact schema supplied in that request. The
  authorization is invalid when the profile, database, schema, server identity, role, access mode,
  bounded contract digest, production entry points, fixture plan, or write impact changes.
- Resolve write capability during input/preflight. When `test-target-writes` is on and the selected
  saved profile is `ro`, `postgres_mcp_modify` is unavailable, the server/role default is read-only,
  the server is a replica, or capability is indeterminate, request a write-enabled saved profile
  reference for the same explicit database/schema. Ignore `transaction_read_only=on` from
  `postgres_mcp_query` when classifying capability; that tool always uses a read-only inspection
  transaction. Require `default_transaction_read_only=off` for writes. Never request credentials or
  silently switch profiles.
- `test-target-writes` requires the corresponding Docker production-path tests to pass first. It
  permits marker-owned synthetic `INSERT` and cleanup `DELETE` operations, state-mutating routine
  calls through production application paths, and `nextval()` only for active code-derived
  sequences. It never permits customer-row updates/deletes, `TRUNCATE`, `ALTER`, `REPLACE`, `DROP`,
  object creation, or customer-data changes. Execute authorized target mutations and cleanup through
  `postgres_mcp_modify`; production-driver target runtime checks are separate evidence when an
  approved application binding exists.
- Before target writes, persist the exact affected objects, planned fixture count per table,
  sequence-call count, production entry points, assertions, cleanup order and predicates, and
  permanent non-rollback effects. Record every consumed sequence value; sequence advancement is an
  acceptable authorized permanent side effect and must never be rewound.
- Insert only rows provably owned by the unique `MIG_TEST_<run-id>` marker. Cleanup deletes only
  ledgered marker-owned rows, then proves zero marker-owned rows remain in every affected table. A
  failed ownership, privilege, lock, contract, production-path, assertion, or cleanup check defers
  only dependent target-write tests and never broadens authorization or changes tools.
- `test-target-writes` never implies `create-persistent-ddl`, and `create-persistent-ddl` never
  implies `test-target-writes`. When a required object is missing, target runtime testing remains
  deferred unless the object is independently created under the separate option and revalidated.
- If target context drifts, the batch changes after validation, or any safety proof fails, do not
  switch tools. Revalidate when safe; otherwise do not execute the batch, continue independent
  source work, and report the exact blocker. A persisted option from an older/different target is
  not authorization.
- Reconcile every enabled `test-target-writes` option. When applicable paths exist, only a passed
  write execution satisfies it. If the user does not provide a writable profile or another safety
  prerequisite remains unavailable, record `not-executed`, make conversion `PARTIAL`, and show the
  next action. Never downgrade it to `RUNTIME_VALIDATION_NEEDED`, `not applicable`, or a successful
  skipped result.
- `create-shims` never permits guessed business/security behavior or a broad replacement library.
- Baseline shim contract checks run separately from application baseline coverage. They do not add
  execution data to the unit, integration, or combined baseline and shim classes are outside the
  frozen comparison scope.
- Test options never permit weakened assertions, unrelated test rewrites, or coverage gaming.
- Unit and integration wave minimums are independent. Waves in one lane cannot satisfy the
  two-wave minimum in the other.
- `fix-broken-tests` may repair proven stale tests needed for the enabled baseline, but generated
  unit or integration tests must not run or affect coverage until the authoritative baseline is
  captured. Failures exposed after restoring a missing runner/adapter are included; the option never
  permits removing it, excluding/disabling tests, ignoring failures, or lowering discovery counts
  to regain a green build.
- When `fix-broken-tests` is enabled, every failure/error in the complete discovered suite is in
  scope, including pre-existing failures. Record per-class repair attempts and rerun the complete
  suite to zero failures/errors; do not relabel an unattempted backlog as legacy debt.
- Assessment never performs persistent DDL or any Oracle/PostgreSQL write, regardless of options.
  `test-target-writes` is conversion-only.
- When an option is off, perform discovery and reporting but do not make that class of edit/write.
- A disabled option is not a blocker. Continue independent assessment or conversion and report the
  skipped action.

Assessment and conversion reports list effective options, requested source, authorization context,
actions attempted, actions completed, actions skipped because an option was off or a safety check
failed, and remaining human review.