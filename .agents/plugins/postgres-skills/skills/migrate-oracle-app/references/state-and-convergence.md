# State and Convergence

Persist understanding once, refresh it on evidence, and spend expensive validation only where it
can change the next decision. Optimize for maximum useful migration, not green at any cost.

## Persistent State

Use `assessment-data.json` for discovery and `conversion-state.json` for mutable execution state.
After each completed or deferred unit, append bounded state operations and changed section fragments
with the primary-owned [runtime coordinator](./runtime-coordinator.md). Its fsynced, hash-chained
journal is the resumable execution state; do not rewrite full JSON/Markdown after every unit.
Materialize canonical state/reports only at assessment or final conversion reporting boundaries.

Conversion state always resides at
`.github/migrate-oracle-app/conversions/conversion-state.json`. A rerun replaces the prior stable
artifact set rather than creating a new run directory. Persist prior-artifact ownership/digests and
the overwrite/obsolete-file cleanup result.

Follow [conversion-state-schema.md](./conversion-state-schema.md) for exact key names, types, and
required zero/unavailable evidence. Do not invent alternate casing or state shapes.

Persist sanitized:

- Plugin version, run ID, codebase repository root, branch, starting commit, and exact run/phase
  timestamps
- Effective migration options, explicit user source, resolution timestamp, and option changes
- Selected PostgreSQL provider, actually visible tools, exact-name discovery queries/results,
  failed-search errors, rejected wrong-family results, and final availability status
- Sanitized connection context, profile-reference digest, database, schema, access mode, server
   capabilities, contract digest, and validation timestamp
- File digests and module/package/dependency graph
- Build, test, coverage, and packaging commands with tool versions
- Test engines, test-to-module mapping, baseline results, and raw coverage counters/report paths by
  unit, integration, and combined lane; metric; and production scope
- Untouched raw baseline attempt; authorized baseline-enablement batch, file digests, authorization
  source, rollback, and enabled-baseline result
- Ranked uncovered-behavior backlog, independent unit/integration wave policy (minimum two and
  maximum ten in each enabled lane), aspirational 80% combined coverage level, per-wave results,
  measured deltas, and exact stop reason
- Active Oracle findings and bounded PostgreSQL object signatures
- Code-derived PostgreSQL object manifest with evidence paths, expected kinds/contract fields,
  bounded MCP batch membership, query digests, returned OIDs/signatures, elapsed times, timeouts,
  per-object status, and refresh reason
- Target-metadata acquisition strategy, zero `_db_context` calls, zero schema-wide/catalog-wildcard
  queries, exact scalar/name/OID batch bounds, per-query row ceilings, and any policy violations.
  An attempted `_db_context` call invalidates completion evidence even when it timed out.
- Optional extracted Oracle DDL input root, resolved schema/type-directory layout, limits/digest,
  and sanitized index; one-primary-object file paths, auxiliary definitions, hashes, kinds, package
  members, and index errors; every missing-target-object source match,
  reconstruction provenance, body completeness/confidence, evidence/assumptions/limitations,
  deploy/undo output, updated application call site, synthetic Docker proof, persistent result, and
  required customer validation. Never persist Oracle bodies or claim inferred behavior is proven.
- Docker image/configuration identity and authoritative target-context digest
- `test-target-writes` authorization source/digest, exact saved-profile/database/schema context
  digest, production-driver target digest, refreshed role/access mode/privileges/locks, write-impact
  digest, Docker prerequisite result, and invalidation reason
- Synthetic integration fixture ledger by sanitized environment/table: marker, fixture loads, rows
  created/reused/cleaned/rolled back/remaining, peak rows, cleanup method/status, and evidence path
- Fixture-to-behavior mapping: test/scenario, production entry point, PostgreSQL objects, fixture
  table/count summary, exercised behavior, assertions, and result
- Database-object test matrix: object kind totals, exact schema-qualified identity/signature,
  active/discovered/exercised state, production entry point, invocation form, fixture dependencies,
  assertions, generated values/side effects, environment, cleanup proof, result, and blocked reason.
  Persist explicit function/procedure zero counts when none were active or exercised.
- Authoritative target fixture ledger: exact marker, planned/actual per-table insert counts, ledgered
  keys represented only by sanitized digests, cleanup order/method, deleted counts, and per-table
  zero-remaining proof
- Authoritative target call ledger: production entry point, application-driver invocation evidence,
  affected routines/tables/views, assertions, result, and MCP versus driver call classification
- Active code-derived sequence ledger: schema-qualified sequence, source evidence, expected and
  actual call counts, every consumed value, calling production entry point, and permanent
  non-rollback acknowledgement
- Three independent validation outcomes: Docker PostgreSQL runtime, authoritative target contract,
  and authoritative target runtime/write. Persist status, exact passed/deferred scenarios, and
  readiness impact for each.
- Conversion units, dependencies, changed files, status, checks, attempts, and coverage
- Final exhaustive file inventory: repository-relative path, Git change kind
  (`modified`/`added`/`deleted`/`renamed`/`generated`), conversion unit/category, concise
  result/purpose, before/after digest when applicable, and inventory counts by kind. Persist the
  final Git-status/diff evidence digest used to reconcile it. Keep ignored local coverage in coverage
  lane evidence, not the Git-reconciled Changes inventory; include authorized `.gitignore` edits.
- Scorecard file counters: baseline tracked files from the starting commit, modified tracked files,
  new source/test files, total application files changed/added, and generated migration artifacts.
  Persist the exact Git commands/evidence used for each count.
- Tracked diff summary from final `git diff --numstat`: modified file count, additions, deletions,
  total changed lines (additions plus deletions), and any binary-file exclusions.
- Per-modified-file current line ranges derived from final `git diff --unified=0`, including explicit
  deletion-point notation for deletion-only hunks.
- Sanitized per-file diff artifact path/digest, anchor mapping, file count, redaction categories,
  withheld hunks, ToC/navigation validation, and links from the human-readable inventory.
- Validation-result rows: gate, normalized emoji/text status, required/optional classification,
  evidence/result, and readiness impact
- Remaining-work/exception rows: emoji/text status, exact area/unit, exception, evidence/impact,
  next action, prerequisite/owner, and source evidence
- Customer-facing status/result presentation: one emoji plus plain title-case text, with no bold or
  all-caps label; canonical uppercase status codes remain machine-state values only.
- Failure classes, evidence, repair history, deferred work, and human-review items
- Diagram models with stable node/edge IDs plus coverage-section rows/statuses, evidence, grouping, source
  digest, and validation status
- Benchmark counters and next safe resume action
- Agent-orchestration capability, dependency graph, eligible independent units, assignment IDs/types,
  read/write mode, owned/prohibited paths, parallel batches/timestamps, concise results, integration
  checks, ownership violations, serial fallback reason, and delegated command/test counters

Never store credentials, connection strings, customer row values, raw source snapshots, or complete
logs. Store paths to sanitized reports instead.

Reuse saved evidence when its digest and dependencies are unchanged. Refresh only when:

- A relevant file, build descriptor, test configuration, or coding rule changed
- Target identity or an object signature changed
- A compiler, test, coverage, Docker, or MCP result contradicts saved understanding
- The user changes scope, strategy, or database context
- The supplied profile, database, schema, server identity, or access mode differs from saved context
- The user changes an option that affects pending or completed units
- The target role, privileges, relevant locks, production-driver target digest, object contracts,
  fixture plan, sequence manifest, production entry points, assertions, or cleanup predicates differ
  from the recorded `test-target-writes` impact

Use `migration-runtime.mjs cache-get/cache-put` for reviewed source and code-derived target-object
manifests. Cache hits verify actual input bytes, directory membership, payload integrity and target
context; never cache write-safety decisions. Restore current state through `replay`, not repeated
source analysis or report regeneration.

## Bounded Conversion Units

Make a unit one logical module, package, dependency group, SQL/object contract, or propagation chain.
Each unit records its files, prerequisites, target contracts, affected tests, focused commands,
expected outputs, and completion checks. Keep units small enough to compile and test independently.

Process ready units in dependency order. Follow
[agent-orchestration.md](./agent-orchestration.md). When child agents are available, two or more
independent ready units must run in parallel when they do not edit the same files, use the same
mutable database state, or depend on unresolved decisions.

## Root-Cause Repair

Normalize diagnostics by tool, error code, message shape, dependency, and affected symbol. Group
shared failures before editing. Fix the root class, then rerun the narrowest discriminating check.

For one failure class:

1. Record the initial evidence and likely cause.
2. Attempt one bounded evidence-supported repair.
3. Revalidate with the same focused command.
4. If it fails differently, update the class and try a materially different repair.
5. After three unsuccessful materially different repairs, preserve diagnostics, name the human
   decision or prerequisite, defer dependent units, and continue independent work.

Retry beyond three only when new evidence materially changes the hypothesis. Never repeat the same
edit, weaken valid tests, or perform unrelated refactors to chase a green result.

## Efficient Validation and Delegation

Use this ladder: changed files, affected tests, module tests, Docker integration, then full suite.
Run the full build/test suite only for baseline, cross-cutting milestones, and final validation, or
when focused evidence cannot reveal propagation risk.

Delegate independent read-only discovery, disjoint implementation units, failure clusters, test
waves, repetitive command execution, test runs, log filtering, and result summarization. Use
task/execution agents for verbose commands and implementation agents only with exclusive path
ownership. Keep migration decisions, shared files, database approvals/writes, merges, and state
ownership in the primary agent. Return concise counters and relevant diagnostics, not full logs, to
primary context.

Minimize database calls by batching bounded metadata reads, caching target signatures, and querying
again only on drift or contradictory evidence. Do not reread unchanged files unless needed to apply
an edit or resolve a new failure.

## Benchmark Metrics

Record baseline, per-unit, milestone, and final values when the client exposes them:

- Assessment, conversion, and total wall-clock elapsed time derived from exact persisted timestamps
- Tool calls and context usage when exposed
- Compilation attempts and successful passes
- Dependency warmup commands, compile-once generation/invalidation count, lifecycle compile
  invocations during waves, direct/offline wave runner commands, native report/coverage paths, full
  precompiled-suite runner executions, pre-package checks, final clean-package count, archive checks,
  and final HTML generation count
- Verified same-run preparation reuse and input/output invalidations, shared Docker identity/image/
  configuration digests, endpoint-binding and fixture-isolation assertions, cleanup proof, and
  per-step elapsed timings. Optional waves require a concrete uncovered behavior and value evidence.
- Focused, module, Docker, and full test executions
- PostgreSQL/MCP calls, separated into reads and writes
- Production-driver target calls, marker-owned inserts/deletes, rows remaining after cleanup, and
  irreversible sequence increments, separate from Docker and MCP calls
- Files examined, converted, and left for review
- Compile, test, package, Docker runtime, authoritative target contract, and authoritative target
  runtime/write status
- Baseline/final unit, integration, and combined line and branch coverage with raw counters
- Coverage-wave count, minimum/recommended/maximum compliance, planned and newly covered behaviors,
  measured deltas, 80% aspiration progress, early-stop exception, and stop reason
- Failure classes resolved, deferred units, and unresolved human-review items
- Child-agent blocking wait seconds while independent work was ready, overlapping batch time, and
  batched/parallel target metadata read counts

Use these metrics to identify repetition. A faster run is not successful if it skips required
behavior or safety validation.

## Run Metrics Persistence

Start the run clock before repository discovery. Capture the execution machine's local IANA time
zone with `Intl.DateTimeFormat().resolvedOptions().timeZone` once at run start, not the database
server's zone or a model default. Persist it as `runMetrics.timeZone` and reuse it when rendering
either report or resuming the run.

Both `assessment-data.json` and `conversion-state.json` use the same `runMetrics` object with
`timeZone`, `assessment`, `conversion`, and `total`. Each phase explicitly declares:

- `status`: `completed`, `not-run`, `in-progress`, or `interrupted`
- `startedAt` and `completedAt` for a completed phase
- `startedAt` and `observedAt` for an in-progress or interrupted phase, with no `completedAt`
- `wallClockElapsedMs`: a nonnegative safe integer equal to the completion/observation instant
  minus the start instant

All machine timestamps remain ISO 8601 with an offset. A `not-run` phase contains only its status,
not fabricated timestamps or zero-duration measurements. Use the canonical shape in
[conversion-state-schema.md](./conversion-state-schema.md) for both reports' evidence.

For an assessment-only run, total metrics equal the assessment phase. For `assess-and-convert`,
capture the assessment boundary before conversion begins. Total Runs measures the actual run's
start-to-completion span, including intervening waits, not the sum of phase durations. Preserve known
prior assessment timings when conversion consumes an existing assessment; identify carried-forward
timing in the execution evidence rather than inventing a new assessment execution.

Freeze completed timing at the final reporting boundary after the available validation gates
finish. Synchronize the final combined-run snapshot into both states and reports before the
two-pass artifact record/verify checks. If those checks require more work, refresh completion times,
rerender both reports, and repeat both checks. Never change timing after the final report digests
have been verified.

## Run Metrics Report Section

Both reports include the same Run Metrics section in their execution-evidence appendix, including
the Assessment, Conversion, and Total Runs rows in that order. Label the pinned time zone once above
the table.

| Phase | Start | Completed | Wall Clock Duration |
|---|---|---|---|
| Assessment | 4:10pm, June 10, 2026 | 4:13pm, June 10, 2026 | 3 minutes, 0 seconds |
| Conversion | 4:13pm, June 10, 2026 | 6:07pm, June 10, 2026 | 1 hour, 54 minutes, 30 seconds |
| Total Runs | 4:10pm, June 10, 2026 | 6:07pm, June 10, 2026 | 1 hour, 57 minutes, 30 seconds |

Start and Completed use a 12-hour clock, lowercase `am`/`pm` without a preceding space, a full English
month name, day, and year. Do not show raw ISO timestamps in these cells.

Always include minutes and seconds in durations, including zero values such as
`0 minutes, 45 seconds`. Include hours only when nonzero, and use correct singular/plural words,
such as `1 minute, 1 second` or `1 hour, 54 minutes, 30 seconds`. Round to the nearest whole second
for display only; retain exact milliseconds in JSON. Never use abbreviated or seconds-only
durations.

Render this section with the shared formatter instead of formatting values by hand:

```text
node "<skill-directory>\scripts\format-run-metrics.mjs" "<assessment-data.json-or-conversion-state.json>"
```

The command reads `runMetrics` and prints the complete Markdown section. It rejects missing,
invalid, or contradictory timing evidence. Artifact validation independently requires the section
to match that evidence and requires both reports to share the final snapshot for a combined run.

For assessment-only and static-scope stops, show `Not run` in all Conversion cells. For a running or
interrupted phase, show `In progress` or `Interrupted` in Completed and prefix its observed duration
with the same label. Do not present an observation timestamp as a completion.