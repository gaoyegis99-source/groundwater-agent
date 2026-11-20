# Testing and Coverage

Testing is conversion evidence, not a final ceremony.

## Baseline

- Detect documented compile, test, integration, static, coverage, and package commands.
- Inventory every test source by language, framework, eligible container, and test case before
  accepting a runner result. Discover every test runner/adapter, project/target, environment,
  profile/configuration, and integration label; clear only stale reports for the command being
  rerun.
- Inspect the effective dependency/build graph for every required runner/adapter. Follow
  [language-adapters.md](./language-adapters.md). Examples include JUnit Platform engines, .NET test
  SDK/adapters, pytest plugins/environments, and CTest/framework registration.
- Parse fresh Surefire/Failsafe, TRX, pytest/CTest JUnit XML, or equivalent native reports and
  reconcile every eligible container/case. Verify representative tests from every detected adapter.
  Treat zero expected tests, a missing container report, an unexpectedly lower count, or a silently
  absent adapter/project/executable as failure.
- Record commands, exit codes, totals, failures, errors, skips, reports, and environment limits.
- Separate pre-existing failures from migration findings and continue independent work.

Persist the reviewed inventory, adapters, eligible containers, representative tests, and minimum
executed counts in
[test-discovery-baseline.json](./test-discovery-baseline-schema.md). Never derive an authoritative
baseline by summing totals from different commands, reruns, engines, or overlapping report sets.

### Mandatory Test Gates

Use three independent, ordered gates:

1. **Runner/adapter availability**: every detected source framework has a compatible runner/adapter
   in the effective dependency/build graph. When `establish-baseline` is on, add the smallest
   managed test dependency/configuration needed to restore it. For Spring Boot Maven projects, add
   `org.junit.vintage:junit-vintage-engine` without a version when the parent manages it; Jupiter
   sources likewise require `org.junit.jupiter:junit-jupiter-engine`.
2. **Discovery reconciliation**: run native list/collect discovery and the complete suite, parse
   fresh native results, prove every eligible container/case executed, verify representative tests
   from each adapter, and compare counts with the reviewed baseline. A green build exit alone is
   insufficient.
3. **Failure repair**: classify all exposed failures. When `fix-broken-tests` is on, repair stale or
   migration-caused production/test expectations without weakening assertions and rerun the
   complete discovered suite until failures and errors are zero.

`fix-broken-tests` applies to every failure/error exposed by the complete discovered suite,
including failures that existed before migration. "Pre-existing", "legacy", or "broad test debt"
is a classification, not permission to skip repair. Cluster failures by root cause and failing
class, repair production defects first, and record each materially different attempt. Do not start
coverage-generation waves while the complete-suite repair backlog remains. If a class cannot be
repaired safely after three materially different attempts, record the exact contract ambiguity or
external prerequisite and next action; the run remains `PARTIAL` and artifact validation must not
claim test discovery passed.

Keep `failIfNoTests` and `failIfNoSpecifiedTests` enabled. Prohibit `skipTests`, failure-ignore
settings, new test exclusions, and unexplained disabled tests. Any unavoidable intentional
exclusion requires its container/test identity, exact reason, review timestamp, and an intentional
baseline update.

Measure current production coverage during assessment. Prefer repository tooling; otherwise use a
standard command-line plugin or temporary untracked configuration. Record tool/version, commit,
scope, exclusions, profiles, report path, and raw covered/missed line and branch counters. Calculate
aggregate percentages from counters, never averages or estimates.

Capture framework/toolchain lanes plus integration and combined coverage when the repository can
execute them:

- **Framework/toolchain**: each applicable unit/characterization adapter or environment, such as
  Vintage, Jupiter, xUnit, pytest, GoogleTest, or compiler configuration.
- **Integration**: Docker, application-to-database, and other integration suites instrumenting the
  same production classes.
- **Combined**: unique production instructions, lines, branches, methods, and classes exercised by
  either lane.

Probe the Docker engine without changing Docker Desktop or service state. An already-running engine
may be used. If it is unavailable, start/resume Docker Desktop only when
`start-docker-desktop` is explicitly enabled. Otherwise record the Docker/integration limitation;
do not force the desktop application open on the user's behalf.

Keep execution data and reports separate by lane, then create the combined report by merging
compatible native execution data and regenerating the report against the same production scope.
Never add lane counters because the same code may be covered by more than one lane. Use JaCoCo,
Coverlet/dotnet-coverage, coverage.py, gcov/lcov/gcovr, or llvm-cov according to the detected
toolchain. Never merge incompatible binaries, compiler configurations, target frameworks, or
coverage formats.

Generate coverage only after the complete-suite discovery and failure-repair gates pass. Delete or
isolate prior execution data before each lane. Record separate applicable framework/toolchain,
integration, and overlap-safe combined lanes. Prove each execution-data file was created during its
current lane run and each coverage report was generated after discovery reconciliation; stale
coverage data invalidates coverage.

For Maven conversion waves, follow
[performance-and-execution.md](./performance-and-execution.md): prepare all wave sources, run
`test-compile` once, execute direct Surefire/Failsafe goals against precompiled classes with unique
report/exec paths, merge with the JaCoCo CLI, run the complete suite once, then run one final clean
package. Generate per-wave XML/exec evidence but final HTML at most once.

For each available lane, report line and branch coverage independently. Include instruction,
method, class, and complexity counters when the native tool provides them, but never collapse
different counters into one "overall coverage" percentage. Mark an unavailable lane or metric with
the exact reason instead of treating it as zero or silently omitting it.

## Coverage Artifact Retention

Follow [git-workspace.md](./git-workspace.md#coverage-git-ignore-rules) for the two scoped root
`.gitignore` rules. Apply them only within an already-authorized source-edit batch. Read-only
assessment leaves `.gitignore` unchanged and records the recommended rules as unapplied.

Retain the current assessment and conversion coverage locally, including native execution data,
XML, and any generated HTML. These files support lane merging, baseline comparison, detailed
investigation, and artifact validation; ignoring them does not make them disposable during the run.
Do not delete coverage merely to reduce Git noise. Freshness isolation and ownership-validated
rerun replacement still apply.

Keep raw counters and artifact paths in assessment/conversion state even when the files are ignored.
Ignored coverage is not part of the Git-reconciled Changes inventory. Reports, state, diff evidence,
and DDL remain outside the new ignore rules. If detailed evidence must survive a fresh clone or be
shared for audit, recommend a separate run-artifact archive rather than committing raw coverage.

## Baseline Enablement

Run and preserve the untouched build/coverage attempt first. If it succeeds, that is the
authoritative baseline and assessment remains tracked-source read-only.

If it fails before coverage because of unavailable dependencies or build/test configuration:

1. Classify the failure and prove the minimum non-semantic repair.
2. Resolve required options from [options.md](./options.md). Dependency/build/test-runner/coverage
   edits require `establish-baseline`; an active missing API also requires `create-shims`; stale test
   content requires `fix-broken-tests`.
3. Require a clean attached branch and capture candidate file digests.
4. Show the exact batch, why behavior is preserved, commands, expected coverage scope, Git impact,
   and precise rollback. In standalone `assess`, obtain approval for that unchanged batch. In
   `assess-and-convert`, persist the preview and continue without prompting.
5. Apply only dependency declarations, isolated shims, build/test-runner profiles, coverage
   configuration, and proven stale-test repairs. Classify proven-unused dependencies/artifacts but
   remove them only when `remove-unused-artifacts` is explicitly enabled.
6. Rerun compile and the complete reconciled suite across every existing supported test
   engine/profile. Repair failures under `fix-broken-tests`, then generate fresh engine-separated
   and combined coverage only after reconciliation passes.

Do not convert providers, datasources, SQL, mappings, routines, or runtime behavior to establish a
baseline. Do not add or run generated application tests before baseline capture. Keep approved
changes unstaged, record them as assessment-owned baseline enablement, and carry them into
conversion.

Freeze the coverage comparison scope from production classes present in the untouched commit.
Assessment-owned shims and harness helpers are outside that comparison scope and must be reported as
a separate support scope. Validate a shim with isolated contract checks whose execution data is not
merged into unit, integration, or combined application baseline coverage. Conversion reports both
the final result on the frozen comparison scope and final full-production coverage including new
production classes.

Record both outcomes:

- **Raw baseline attempt**: untouched commit/status, command, diagnostics, and why coverage was or
  was not measurable.
- **Enabled baseline**: exact approved changes, command, tests, coverage lanes/counters, and
  limitations. This is the authoritative pre-conversion comparison point.

If options are off, standalone approval is declined, or enablement remains impossible, report
`ACTION NEEDED` with the exact cause and next action. Conversion may continue independent work, but
it must label the baseline unavailable and can report final coverage only, not a numeric
improvement delta.

## Change Loop

Use the cheapest validation ladder that can falsify the current unit:

1. Changed-file checks and compile/type-check for the affected area.
2. Narrowest affected unit tests.
3. Affected module/package tests.
4. Reused Docker integration tests for database-touching units.
5. Full compile/test only at baseline, cross-cutting milestones, and final validation.

Attribute failures and repair production defects or stale affected tests, then rerun the same
focused command. Use [state-and-convergence.md](./state-and-convergence.md) to cluster root causes,
bound retries, and delegate repetitive execution and log summarization.

Never weaken valid behavior assertions. Update obsolete Oracle SQL/APIs, constructors, reflected
fields, fixtures, matchers, engines, or lifecycle assumptions only when `fix-broken-tests` is on and
the current production contract proves the test stale.

Missing tests are not passing tests. If a previously absent engine exposes failures,
`fix-broken-tests` applies to those failures exactly as it does to already-discovered failures. Do
not restore green by removing the engine, adding exclusions, disabling tests, ignoring failures, or
lowering the reviewed discovery baseline.

## Improve Coverage

Compare final and baseline coverage with the same tool, version, production scope, exclusions,
metrics, and profiles. Report raw counts, percentages, and percentage-point deltas overall, by
changed module, and for changed or affected migration-sensitive paths. Show unit, integration, and
combined results separately and make combined coverage the headline result. Use the frozen
untouched-class scope for numeric before/after deltas and also report final full-production scope so
new production code cannot disappear from quality reporting.

In customer-facing reports, call the frozen untouched-class scope **Original production classes —
fixed comparison set**. Name a selected high-risk behavioral subset by its exact size, for example
**Eight migration-critical classes**, and list every included class. Never imply that this subset is
all modified files or classes.

Do not add a numeric target/objective column or use a percentage as a readiness verdict. Internally,
work toward an aspirational 80% combined line coverage level and improve branch coverage separately
where safely measurable. Improve measured coverage while meaningful, safely testable gaps remain,
prioritizing changed or converted migration-sensitive scope. Critical database-facing paths require
direct behavioral assertions through the production call path and real PostgreSQL driver in Docker;
a high aggregate percentage is not a substitute.

Iterate:

- Add characterization tests before changing uncovered behavior.
- When `create-unit-tests` is on, generate focused unit/characterization tests for converted behavior
  without coverage, including SQL, binding, mappings, nulls, ranges, precision, generated keys,
  transactions, exceptions, serialization, and shims.
- When `create-integration-tests` is on, add Docker tests for queries, routines, DDL, fixtures, and
  real-driver behavior.
- Build a ranked backlog of uncovered behaviors, prioritizing changed code, its active callers,
  database access, transactions, errors, and high-branch-complexity paths.
- When `create-unit-tests` is on, process at least two bounded unit/characterization waves.
- When `create-integration-tests` is on, independently process at least two bounded integration
  waves through the production boundary and real PostgreSQL driver.
- Never exceed ten waves in either lane. Keep each wave to no more than five related behaviors;
  unit waves never count toward the integration minimum and integration waves never count toward the
  unit minimum.
- Rerun the affected test lane and fresh combined coverage after each wave. Record planned
  behaviors, newly covered raw counters, percentage-point deltas, assertions added, and failures.
- Do not stop either enabled lane before wave two. Wave two may be complete-suite validation using
  existing assertions; record `kind=validation` and `validatedTests` instead of adding duplicate
  tests. Batch related scenarios in one execution against shared setup.
- After wave two, further waves are optional and must have `continuationEvidence`: a specific
  uncovered behavior, expected additional covered line/branch counters, and a value-based reason.
  Continue only for worthwhile ranked gaps, even above the aspirational 80% level when high-risk
  behavior warrants it. Never run extra waves solely to reach a percentage or the ten-wave cap.
- Stop at any permitted point when every remaining backlog item has an allowed exact classification
  or no enabled test option can safely and meaningfully cover it. Never add trivial tests just to
  satisfy the wave count or percentage aspiration.
- Classify each remaining item as unreachable, generated, platform-owned, destructive-only,
  dependent on an unavailable external system, or not safely isolatable. Include the evidence and
  exact next action. A safely testable low-priority gap may instead be `deferred-low-yield` only with
  source/measurement evidence, expected new line/branch counters, estimated additional runtime,
  exact next action, and a `stopDecision` of `low-marginal-value` with a reason. Never use this
  classification for high-risk behavior. "Legacy code" and "low value" alone are not evidence.

Overall line and branch coverage must not regress without a precise justified exclusion. New shims
and migration infrastructure should have direct tests for every measurable supported behavior. Do
not game coverage with empty, duplicate, trivial, assertion-free, excluded-production, or
implementation-only tests.

Existing tests are always detected and run regardless of options. When test creation/repair options
are off, measure and report gaps without editing test files. Never let generated tests contaminate
the assessment baseline.

Reconcile final test execution with the authoritative enabled baseline by engine/profile. A lower
final test count is a validation failure until every missing test is explained as renamed,
intentionally profile-separated, environment-gated, removed with evidence, or restored and rerun.
Persist the reconciliation; a green smaller suite never silently replaces the baseline.

## Final Gates

Run without hidden skips: changed-file formatting, static checks, clean compilation, every unit test
engine/profile, Docker integration, codebase-to-routine end-to-end tests, full package lifecycle,
fresh final coverage, residual Oracle scan, and deployable artifact inspection. A required zero-test
run or required failing suite prevents `READY`.

Put these gates in the conversion report's Validation Results table defined by
[conversion.md](./conversion.md). Every row has a dedicated emoji/text status cell. Mixed outcomes
such as passed Docker tests plus a skipped external integration use `⚠️ Passed with warnings`;
never present them as fully passed.

Assessment puts **Code Coverage** under **Test & Validation Readiness**; conversion puts coverage
under **Test & Coverage Results**. Use the exact tables in [diagrams.md](./diagrams.md). Neither
report includes a coverage legend. Assessment includes a raw-counter Coverage Heatmap and starts its
tables with Coverage Baseline; conversion includes a Migration Validation Coverage diagram and
coverage improvement/comparison tables.

Assessment includes baseline provenance, unit/integration/combined lane coverage, and
migration-sensitive gaps. Conversion includes baseline/final comparison, lane contribution, three
to ten unit/characterization waves when enabled and feasible, and remaining gaps. Tables use raw
covered/total counters, percentages, and percentage-point deltas without numeric target/objective
columns. Report the 80% aspiration in narrative/policy evidence only, never as a pass/fail table
target. Report full production and changed/converted scope separately, include optional method
coverage as its own metric when available, and identify test engines including JUnit Vintage or
Jupiter.

Never report a lone value such as `Coverage: 36%`; name the lane, metric, scope, raw counters, and
measurement command.