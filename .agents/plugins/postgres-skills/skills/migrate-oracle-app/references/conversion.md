# Conversion

Conversion consumes `assessment-data.json` directly and owns implementation, repair, tests,
coverage, packaging, and resumable state.

Re-ingest the current invocation's optional notes under [coding-notes.md](./coding-notes.md).
Do not inherit stale assessment checkmarks. Persist every note's disposition in `codingNotes`;
the final assembler automatically generates **Coding Notes Processing**, including honest pending
actions/conflicts. Evaluation checkboxes never replace build, target or runtime gates.

Follow [git-workspace.md](./git-workspace.md) for in-place repository preflight and change tracking.
Use [state-and-convergence.md](./state-and-convergence.md) to avoid repeated discovery, define
bounded units, cluster failures, cap repair loops, delegate execution, and measure convergence.
Use [diagrams.md](./diagrams.md) to preserve assessment identities and visualize actual
transformation and evidence.
Apply every detected toolchain in [language-adapters.md](./language-adapters.md); mixed-language
repositories require independent build, test-discovery, coverage, integration, and packaging
evidence for each applicable adapter.
Use [performance-and-execution.md](./performance-and-execution.md) to compile once, execute
precompiled waves, merge coverage without repeated lifecycles, and package cleanly once.
Resolve all opt-in actions with [options.md](./options.md) before the conversion preview.
Resolve and compare the target with [connection-context.md](./connection-context.md) before reusing
assessment evidence or conversion state.
Use [runtime-coordinator.md](./runtime-coordinator.md) for content-verified source/target-object
manifest hits, the Java pre-generation gate, primary-only execution, and bounded checkpoints.
Publish full state/reports only at reporting boundaries through its template assembler and
runtime-required artifact record/verification; do not regenerate canonical artifacts per test wave.

## Preflight

- Require `assessment-data.json.scopeDecision.status` to be `MIGRATION_REQUIRED`. When it is
  `NO_CONVERSION_REQUIRED`, render
  [no-conversion-report-template.md](./no-conversion-report-template.md), create no file diff, make
  no application changes, run artifact validation, and stop.
- When assessment status is `SCOPE_REVIEW_REQUIRED`, conversion must not start. Report the exact
  uninspected files and resolve scope before any install/build/test/Docker/target/edit action.
- Validate assessment, repository/file digests, and connection-context digest.
- Refresh only evidence invalidated by drift.
- Use only `.github/migrate-oracle-app/conversions/` for conversion artifacts; never create a
  timestamped/named child folder.
- Load or create the stable `conversion-state.json`; resume passed units without repeating their
  discovery when baseline/context digests still match.
- On rerun, validate the prior artifact ownership/run ID/digests, then atomically replace only the
  skill-owned `conversion-report.md`, `conversion-state.json`, `file-diffs.md`, `coverage/`, and
  `database-ddl/` set. Remove obsolete prior skill-owned files no longer produced by the rerun.
  Preserve any unrelated or concurrently modified user-owned path and stop the conflicting unit.
- Persist the effective options and their user-supplied source; omitted options are off.
- Require a clean attached codebase branch and initial commit, allowing only validated
  assessment-owned baseline-enablement changes from the current run; record the original Git
  baseline and enabled-baseline boundary.
- Show every option as on/off and a concise preview of dependency, configuration, SQL, mapping,
  shim, test, coverage ignore housekeeping, database impact, and sanitized target context.
- In standalone `convert`, ask once before reversible source edits.
- In `assess-and-convert`, persist the preview and continue immediately; the invocation already
  authorizes reversible source edits and explicitly enabled options.
- After source-edit authorization, apply the scoped root `.gitignore` coverage rules under
  [git-workspace.md](./git-workspace.md#coverage-git-ignore-rules). Preserve local coverage evidence
  and include any `.gitignore` edit in the authorized batch and final file inventory.

Neither source authorization path authorizes persistent database writes. Only explicit
`create-persistent-ddl` and `test-target-writes` options authorize their separate bounded write
classes.

## Iterative Conversion

Drive bounded units from the exhaustive assessment Oracle-finding manifest. Missing routines,
failing tests, or unavailable target writes block only dependent behavior; they do not justify
leaving independent providers, selected-boundary datasource descriptors, environment placeholders,
query syntax, packaging, or deployment configuration unconverted. Missing secrets are never a
reason to retain an Oracle binding—use the repository's environment/secret placeholder convention
and report runtime binding validation separately.

Work in repository dependency order, normally:

1. Coverage harness when assessment could not measure a baseline
2. PostgreSQL driver/provider and build dependencies
3. Datasource, pool, TLS, and runtime configuration
4. ORM dialect, persistence units, and type mappings
5. SQL, identifiers, null semantics, sequences, and generated keys
6. Functions, procedures, cursors, OUT values, and transactions
7. Oracle APIs, unavailable dependencies, DDL, and fixtures
8. Oracle cleanup, full validation, packaging, and artifact inspection

Define each unit as one logical module, package, database contract, or dependency group with explicit
files, dependencies, tests, and completion checks. For each unit: inspect, change, compile/type-check
immediately, run affected unit tests, repair, run affected Docker tests, then record evidence and
coverage. Do not open another change area before the first focused check. Keep independent
successful work when one area is deferred.

## Persistent Missing-Object Deployment

After each missing-object unit has reconstructed or converted its PostgreSQL DDL, reconciled call
sites, and passed synthetic Docker validation, evaluate `create-persistent-ddl` before dependent
authoritative target runtime tests. This stage is independent of `test-target-writes` and test
generation options.

- When off, retain Docker-validated deploy/undo files and record `not-requested`; never write DDL
  to the persistent target.
- When on, creation is required, not merely permitted. Follow
  [database-safety.md](./database-safety.md#persistent-create-authorization) with a saved write-enabled
  profile, the exact selected database/resolved schema, current-request authorization, fresh
  privileges/locks/dependencies, exact DDL hash, and Docker deploy/invoke/undo/redeploy proof.
- Recheck each schema-qualified identity, including exact routine signatures, immediately before
  execution. If an object now exists and matches the generated contract, record `already-present`
  with fresh query verification and do not replace it. An incompatible existing object is a
  `not-executed` conflict requiring a separate decision, not permission to alter it.
- Execute plain `CREATE` statements for still-missing objects in dependency order through the
  selected provider's modify operation: `pgsql_modify` in VS Code Copilot or `postgres_mcp_modify`
  in Copilot CLI. Use the exact hash-verified deploy DDL; never send `CREATE OR REPLACE`,
  `IF NOT EXISTS` clauses that mask conflicts, undo DDL, fixture SQL, or production routine calls as
  part of this batch. Do not use the read-only query operation or a different SQL client to write.
- Immediately use the matching query operation and bounded catalog probes to verify each created
  object's identity, kind, and expected columns/arguments/result/definition contract. Record
  `persistentCreateRequested`, `persistentCreateAttempted`, `persistentCreateVerified`, and
  `persistentCreate` authorization/execution/verification evidence for every missing object.
  A tool success response alone is not object verification.
- Source-backed, best-effort inferred, and signature-only fail-closed implementations all follow
  this deployment stage. Preserve inferred-body limitations, customer-validation actions, and the
  overall `PARTIAL` result; deployment does not prove omitted Oracle business semantics.
- Reconcile `optionResults.create-persistent-ddl` against the complete missing-object manifest.
  Generating SQL files or deploying only in Docker never satisfies an enabled applicable option.
  When a safety prerequisite is unavailable, record `not-executed` with the exact reason and next
  action and make conversion `PARTIAL`, not `RUNTIME_VALIDATION_NEEDED`. Record failed execution or
  verification as `failed`, retain successful independent work, and refresh target object state
  before any retry. Never automatically drop a created object or run target rollback scripts.

## Authoritative Target Runtime Testing

After each database-facing production path passes in Docker, evaluate `test-target-writes`:

- When off, record authoritative target runtime/write validation as skipped and continue read-only
  contract validation.
- When on, require the saved write-enabled MCP profile plus exact database and schema from the
  current request. Follow the refresh, impact-preview, marker-isolation, MCP modification,
  sequence-recording, and cleanup protocol in [database-safety.md](./database-safety.md).
- Authorize only production paths already proven in Docker. Do not broaden a batch after preview;
  persist a new preview/digest if its object, fixture, call, assertion, or cleanup set changes.
- Keep MCP inspection/fixture/cleanup calls distinct from application-driver calls. Standalone SQL
  inspection is read-only; execute authorized target mutation scenarios through
  `postgres_mcp_modify`. Production-driver execution against the authoritative target is separate
  evidence and is not required to prove that the MCP profile is writable.
- If profile access is read-only/unknown, privileges are insufficient, relevant locks are unsafe,
  the target drifts, marker ownership is ambiguous, assertions fail, or cleanup cannot prove zero,
  stop only the dependent target-write scenarios and continue independent conversion work. For a
  requested option with applicable paths, request a write-enabled saved profile reference when that
  can resolve the blocker. If writes remain unexecuted, record `⚠️ Not executed`, make the overall
  result `PARTIAL`, and do not classify the option as deferred runtime validation.

Reach compilation early and use diagnostics to find callers and type propagation. Common SQL
starting points are `CURRENT_TIMESTAMP`, deterministic `ORDER BY ... LIMIT`, `COALESCE`, `CASE`,
removal of `DUAL`, schema-qualified sequence calls, recursive CTEs, `VARCHAR`, `TEXT`, and `BYTEA`.
Always verify semantics, especially null/empty-string behavior, precision, timestamps, ordering,
transactions, and public formats.

Use functions in query form and procedures in `CALL` form. Keep refcursors in a transaction.
Retrieve numeric OUT values as `Number` before checked conversion. Test long names against
PostgreSQL's 63-byte stored identifier limit through the production call path.

## Missing Dependencies

When `create-shims` is on, create a shim only after ordinary dependency resolution is proven
unavailable. When it is off, diagnose and document the missing dependency, do not add shim files,
defer dependent units, and continue independent conversion work.

- Inventory every used type, method, constant, exception, and behavior.
- Prefer a public API or existing abstraction; otherwise implement only the used surface in one
  isolated codebase-owned adapter.
- Preserve failure behavior, fail unsupported calls explicitly, and fail security paths closed.
- Test normal, fallback, null, invalid, boundary, and security cases; add Docker tests when needed.
- Verify packaging excludes the missing library and includes the shim.

Document what was missing, what was added, supported/unsupported behavior, files and wiring, tests,
remaining parity risk, removal trigger, exact removal steps, and validation commands. Removal must be
a small deletion and wiring reversal.

## Repair and Finish

When a test fails, attribute it as pre-existing, migration-introduced, stale/broken, environment, or
external prerequisite. Fix production behavior first. When `fix-broken-tests` is on, update stale
tests without weakening valid assertions, then rerun the same focused command. The enabled option
covers every failure/error exposed by the complete discovered suite, not only migration-affected
tests. Record per-class root-cause clusters and attempts; do not stop at the label "pre-existing
debt". When it is off, do not edit test files; document the failure and continue independent work.
Rebuild Docker state before integration retries.

Before accepting any full-suite result, re-inventory test frameworks/containers/cases and compare
them with `assessment/test-discovery-baseline.json`. Enforce runner/adapter availability, complete
container-level discovery, and failure repair as three separate gates. Missing baseline containers,
a missing adapter, reduced counts, runner exclusions, ignored failures, or unexplained disabled
tests block test readiness even when the build exits zero. When `fix-broken-tests` is on, failures
newly exposed by a restored adapter are in scope for repair. Generate final coverage only after the
complete reconciled suite passes, with separate applicable framework, integration, and overlap-safe
combined evidence.

When test creation options are enabled, require at least two unit/characterization waves and two
integration waves as separate gates. Waves in one lane never satisfy the minimum in the other, and
neither lane may exceed ten waves. Further waves need a specific worthwhile uncovered behavior.
Batch related scenarios, reuse verified setup, and credit final complete-suite validation as the
last wave when its runner covers that lane without losing coverage attribution.

Normalize compiler and test diagnostics by tool, error code, message shape, dependency, and affected
symbol. Cluster failures that share a root cause, fix the class once, and rerun the narrowest check.
After three materially different unsuccessful repairs for one failure class, record its evidence and
human-review action, defer dependent units, and continue independent work. Do not chase green by
weakening behavior or broadening scope.

Finish with changed-file formatting, static checks, clean compile, all test engines/profiles, Docker
and end-to-end tests, full packaging, final coverage, residual Oracle scan, and artifact inspection.
Inspect packaged drivers, retained/removed Oracle or private dependencies, API scopes, shims, and
descriptors. Retain files classified only as unused unless `remove-unused-artifacts` is explicitly
enabled; report them in the unused-artifact ledger.
For every PostgreSQL runtime descriptor, generate a driver-packaging plan from its expected external
library path and the assembly layout. Run `inspect-driver-packaging.mjs` against final archives.
Require a matching PostgreSQL JAR at the exact deployment layer/path; a nested WAR copy alone is a
packaging failure and must be called out in Validation Results and remaining work.
Use [driver-packaging-plan-template.json](./driver-packaging-plan-template.json); do not broaden a
descriptor's filename/path pattern merely to make archive inspection pass.
Reconcile every active Oracle finding by path/category. Require zero safely convertible findings
remaining; modified-file count may be small or large but cannot replace this reconciliation.

## Validation Results Table

`conversion-report.md` contains a top-level `## Validation Results` section with:

| Stage / gate | Scope / evidence | Result | Readiness impact |
|---|---|---|---|

This is the single validation and convergence table. Do not add a separate
`Validation / Convergence Map` section. Create one row for every applicable stage: converted
units/files, formatting/compile, focused tests, module/full tests, Docker PostgreSQL runtime,
integration/end-to-end tests, packaging/artifact inspection, residual Oracle scan, authoritative
target contract validation, authoritative target runtime/write validation, and final readiness.
Include actual counts, attempts, failures, skips, coverage delta when relevant, sanitized evidence
paths, and the consequence for readiness.

Every row must contain one emoji plus its text label in `Result`:

| Status | Meaning |
|---|---|
| ✅ Passed | Every required check for this gate passed. |
| ⚠️ Passed with warnings | Required checks passed, but an optional check was skipped or a bounded warning remains. |
| ❌ Failed | A required check failed. |
| ⏭️ Skipped | The gate was intentionally not run; the result explains why and whether it was required. |
| ➖ Not available | The environment or tool could not run the gate; the exact prerequisite is stated. |

Never put bare result prose in the status position. A gate with mixed pass/skip outcomes uses
`⚠️ Passed with warnings`, not `✅ Passed`. Status and Result cells use plain title case with no
Markdown bold and no all-caps labels. A required skipped/unavailable gate prevents canonical
`READY`.
Include formatting/compile, unit tests, integration tests, package, artifact inspection, residual
active Oracle scan, and any other discovered required gate. Always split database validation into
these exact rows:

1. `Docker PostgreSQL runtime validation`
2. `Authoritative target contract validation`
3. `Authoritative target runtime/write validation`

The third row is `✅ PASSED` only when every authorized production path and assertion passes, every
sequence value is recorded, cleanup succeeds, and every affected table has zero marker-owned rows.
When `test-target-writes` was on but applicable writes did not execute, the row is
`⚠️ Not executed` and final readiness is `PARTIAL`.

## Conversion Diagrams

Do not merely regenerate the assessment diagrams. Build exactly these three diagrams from actual
edits and results:

- **Before → After Architecture**
- **Conversion Traceability Map**
- **Migration Validation Coverage**

Validation/convergence evidence belongs only in the top-level Validation Results table above.

For critical database workflows, pair the assessment sequence with an after-conversion sequence to
show preserved logical behavior. Honest partial or failed results remain visible with text status.

`conversion-report.md` records:

- The exact H1 title `# Oracle to PostgreSQL Application Code Conversion`
- Render from [conversion-report-template.md](./conversion-report-template.md). Preserve its complete
  section order, tables, navigation, and evidence rows; specialize labels/content but do not omit
  required blocks. Before conversion completion run the plugin artifact validator in conversion
  mode and repair every failure.
- `<a id="top"></a>` before the H1, a `## Table of Contents` after sanitized run metadata/evidence
  linking every top-level section through explicit stable HTML anchors, and
  `[Back to Top](#top)` at the end of every top-level section except Table of Contents, including
  the final appendix
- **Executive Conversion Summary** with exactly two concise paragraphs answering: what was
  converted/how it went, and what customer action still prevents production readiness.
- **Conversion Scorecard** with exactly six rows in this order: Application conversion, Build &
  package, Unit tests, PostgreSQL integration tests, Target validation, and Production readiness.
  Use the exact columns `Area | Result | Status`. Keep file counts and change inventory out of the
  executive scorecard; retain that Git-derived evidence in the appendix.
- **Next Steps / Actions Needed** immediately below the scorecard. Include one to five practical,
  customer-owned handoff actions using the exact columns `Priority | Area | Action needed | Owner`.
  Priorities are `Required` or `Recommended`. Do not copy every warning into this table: select only
  production blockers, required runtime validation, external-integration validation, and the
  highest-value production-confidence improvements. End with one ordered **Recommended
  production-readiness path** and a pointer to the detailed evidence below.
- In scorecard Status cells, render exactly one emoji plus a plain title-case text label with no bold or
  all-caps styling, for example `✅ Ready`, `✅ Passed`, `⚠️ Action needed`, or
  `🧪 Runtime validation needed`. Keep all other scorecard and next-step cells text-only except for
  ordinary inline code/links needed to identify an object or evidence source.
- **Conversion Transformation** with a concise before/after table, architecture and traceability
  diagrams, and customer-readable source changes
- **Oracle DDL Source Reconciliation** under Conversion Transformation with every missing target
  object, extracted Oracle source match/path/hash or fallback reconstruction provenance,
  body-completeness/confidence/limitations, translated deploy/undo paths, updated application call
  sites, synthetic Docker proof, persistent result, and exact customer-validation action. Never
  present an inferred or fail-closed body as source-equivalent.
- **Validation Results** immediately after transformation using the single de-duplicated table above
- **Test & Coverage Results** with test summary, coverage improvement/deltas, lane evidence,
  migration-validation behavioral coverage, migration-sensitive diagram, wave evidence, and
  remaining test gaps. Explain overall legacy coverage separately from converted-path validation.
- **Database Test Environment** combining persistent database changes, temporary synthetic data,
  fixture cleanup, a database-object-kind summary, a detailed object/signature execution ledger, and
  production behavior exercised. Begin with a direct statement about persistent objects/customer
  data. Use one **Synthetic Test Data** heading directly above the fixture table and no cleanup-status
  legend. Functions and procedures must appear explicitly even when zero were active/tested.
- **Remaining Work & Exceptions** split into Required Before Production, Recommended, and No Action
  Required
- **Conversion Outcome** explaining the status precisely, successful scope, external dependencies,
  and executable recommendation
- **Appendix / Execution Evidence** containing options, Run Metrics, exhaustive file inventory, and
  Git evidence
- In the appendix, capture an exhaustive path-by-path working-tree inventory after report artifacts
  are written. Separate **Modified tracked files**, **New source and test files**, **Deleted or
  renamed files** when present, and **Generated migration artifacts**. Every row contains the exact
  repository-relative path, Git change kind, conversion unit/category, and concise result/purpose.
- Directly below **Modified tracked files**, add a bold summary with the exact number of modified
  tracked files and tracked lines changed, split into additions and deletions. Derive it from final
  `git diff --numstat`; define changed lines as additions plus deletions and note that the count
  spans source, tests, build, and runtime configuration rather than source code alone.
- Add an **Updated line ranges** column to every Modified tracked files row. Derive current-file
  ranges from final zero-context diff hunks (`git diff --unified=0`), merge only truly contiguous
  ranges, preserve distinct hunks, and label deletion-only hunks as
  `after <line> (deletion point)` instead of inventing a current-file range.
- Make every Modified tracked files Path a link to its anchored section in a generated
  `file-diffs.md` artifact beside `conversion-report.md`. Keep all diffs in this single file. Use
  simple sequential Markdown headings (`## FILE-00001`, `## FILE-00002`, ...) as cross-file link
  targets rather than HTML anchors; put the exact repository path directly below each heading. Use a
  minimum numeric width of five digits and expand to the digit count of the modified-file total when
  it exceeds 99,999. Add `<a id="top"></a>` before the diff artifact H1 and a
  `## Table of Contents` linking every `FILE-...` heading. At the end of every file-diff section add
  `[Back to Top](#top) · [Back to Conversion Report](./conversion-report.md#modified-tracked-files)`;
  do not add that link under the Table of Contents itself. Each section contains that file's
  before/after Git hunks. Validate that ToC links, file headings, report links, and modified-file rows
  have identical counts and mappings. Scan before persistence and redact literal hosts, connection
  strings, credentials, email addresses, customer values, and other sensitive content; if a hunk
  cannot be sanitized safely, replace it with an explicit withheld notice rather than persisting raw
  content. Persist the artifact as a generated migration file and reconcile it in inventory counts.
- Reconcile the Changes inventory with final `git status --porcelain=v1 --untracked-files=all`,
  tracked diff name/status, and untracked-file output. Its path set and per-kind counts must match
  exactly. Never use summaries such as "three test files", "assessment artifacts", or
  "the conversion run" instead of listing each path.
- Include report/state/diff/DDL artifacts persisted under `.github/migrate-oracle-app/`.
  Record ignored local coverage files in coverage evidence, not the Git-reconciled Changes inventory;
  already tracked coverage still follows normal Git inventory rules and must not be untracked.
  Exclude ignored ephemeral build output such as `target/` unless it is intentionally persisted as a
  migration artifact.
- Run Metrics in the appendix with Assessment, Conversion, and Total Runs timing, generated by
  `format-run-metrics.mjs` from precise machine evidence in the pinned local time zone. Synchronize
  both reports' final combined-run snapshot before artifact validation.
- Existing, repaired, and added tests with results
- A Validation Results table whose every row has an emoji plus plain title-case text status
- Code Coverage under Test & Coverage Results without a legend
- Unit, integration, and overlap-safe combined coverage before/after, with line and branch metrics,
    minimum/recommended/maximum wave policy, actual wave count, stop reason, and classified remaining
    behavior; do not include numeric target/objective columns. State the aspirational 80% combined
    coverage level narratively, not as a readiness gate.
- Separate Docker PostgreSQL runtime, authoritative target contract, and authoritative target
  runtime/write results
- Inferred objects with assumptions, deploy/undo files, and execution status
- A Database Test Environment section with the exact per-table fixture ledger,
  behavior-exercised matrix, marker, cleanup proof, and persistent-database-change statement from
  [database-safety.md](./database-safety.md)
- Temporary shims, supported boundaries, and removal steps
- Required/Recommended/No Action Required remaining-work tables with exact owners and next actions
- Assumptions, unresolved human-review items, runtime configuration, and exact next actions
- Convergence metrics from `conversion-state.json`
- Three conversion diagrams and any critical before/after sequence diagrams
- Effective options, why each was enabled, actions performed, actions skipped because an option was
  off, and per-option results
- For `test-target-writes`: current-request authorization source, target-context/write-impact
  digests, exact affected objects and fixture counts, production paths/assertions, fixture ledger,
  consumed sequence values and non-rollback notice, MCP read/write counts, cleanup deletes, and
  per-table zero-remaining proof
- Sanitized target database/schema, server type/version, access mode, context validation time, and
  whether the assessment context was reused or refreshed
- Codebase repository root, starting branch/commit, final Git status/diff summary, exhaustive file
  inventory counts, and any concurrent-edit conflicts. Git Evidence may summarize counts but must
  not contradict or replace the Changes path inventory.

Keep `conversion-state.json` sanitized and resumable.