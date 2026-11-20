# Performance and Execution

Preserve every migration gate while minimizing repeated lifecycle work, blocking waits, schema
round trips, and report I/O.

## Mandatory Runtime Gates

Initialize [runtime-coordinator.md](./runtime-coordinator.md) after active migration scope is known.
The helpers enforce primary ownership with an exclusive execution lease, cached per-module Java
release preflight and draft validation, content-verified manifest reuse, and checkpointed final
artifact assembly. These are executable gates, not merely optimization suggestions.

The Maven runner reports its `executionOwner`, Java preflight/cache status, and durable checkpoint
receipt. The final assembler runs artifact record/verification with `--require-runtime`, which
checks actual journal/owner/assembly provenance. Children submit evidence; they do not initialize
competing Maven/Docker environments or publish state.

## Maven Precompiled Wave Sequence

For Maven projects, prepare all planned unit and integration wave test sources before execution,
then use `scripts/run-maven-waves.mjs` with a run-specific JSON configuration:

1. Resolve dependencies/plugins and the JaCoCo CLI once.
2. Run `test-compile` once per changed input/output generation after the selected wave sources are
   ready; retain the warmed Maven environment for later generations.
3. Run each unit and integration wave against the precompiled classes with direct
   `surefire:test` or `failsafe:integration-test`.
4. Use offline mode for repeated wave/full-suite commands.
5. Clear the runner's default native report directory before each wave, then copy its fresh XML into
   a unique wave report directory. Give every wave a unique JaCoCo execution-data file.
6. Merge execution data and generate XML with the JaCoCo CLI. Generate HTML at most once, for the
   final combined report, and omit it when XML is sufficient.
7. Use the last validation wave as the complete precompiled suite when its unrestricted runner
   covers that lane: one Surefire execution, plus one Failsafe execution/verify for a separate
   integration lifecycle. Do not launch the same complete suite again just for a second label.
8. Run pre-package source/classification checks.
9. Run one final online `clean package` with test execution skipped because the complete suite
   already passed immediately before it.
10. Inspect every final WAR/JAR/ZIP archive for required and forbidden entries.

Do not invoke `test`, `verify`, `package`, or compilation lifecycle phases for individual waves.
Direct test-plugin goals must fail on zero or missing specified tests.

If production or test source changes after the compile-once boundary, invalidate the compiled-class
digest, return to planning, and rerun `test-compile`; do not execute stale classes. Record the
invalidation rather than pretending compile-once still held.

Example:

```powershell
node <skill-directory>\scripts\run-maven-waves.mjs `
  <codebase> `
  <wave-config.json> `
  --summary <codebase>\.github\migrate-oracle-app\conversions\wave-run-summary.json
```

Use `--dry-run` to validate and inspect the exact command plan without executing it.

## Two Mandatory Waves and Batched Scenarios

Each enabled test-generation lane requires two waves, not five. Wave one should cover the ranked
changed/database-facing behaviors in a small cohesive test class or suite. Wave two refines genuine
gaps or validates the complete applicable suite. A validation wave can have no new test files; list
its actually executed tests as `validatedTests` instead of manufacturing duplicate tests.

Several test methods/scenarios are not several waves. Execute related scenarios together with one
class/suite selector and shared setup. Never launch Maven, Java, and PostgreSQL separately for each
scenario merely to increase the wave count. Keep unit and integration attribution distinct.

Mark an unrestricted last wave with `kind=validation`, `fullSuite=true`, and an empty `tests` list
to reuse it as the complete-suite gate. The runner omits a duplicate complete-suite launch. Only one
wave may credit each runner; do not count the same mixed Surefire suite independently for both lanes.
Derive Surefire/Failsafe usage and class naming from the project, not from the example template.

Waves three through ten are optional. Require `continuationEvidence` naming the uncovered behavior,
nonnegative `expectedNewCoveredLines` and `expectedNewCoveredBranches` with a positive sum, and a
specific value-based `reason`. Prepare extra tests only after measured counters and the ranked
backlog identify a worthwhile opportunity. A plateau is evidence to reconsider the next wave, not
proof that all attainable coverage has been reached.

## Run-Scoped Shared PostgreSQL

The primary creates and readies one Docker PostgreSQL environment before integration execution,
with the target image/extensions and labels `migrate-oracle-app.run-id` and
`migrate-oracle-app.configuration-digest`. The latter hashes the bootstrap/configuration contract.
Use an explicit loopback port binding and explicit synthetic database/user/password. Do not change
Docker Desktop state unless `start-docker-desktop` is enabled.

Supply its full `containerId`, immutable `imageId`, `configurationDigest`, database, and current
`runId` in `dockerEnvironment`. The Maven runner borrows this environment: it verifies ownership,
image, configuration, running state, and PostgreSQL readiness, but never starts/builds/stops a
container per test process. It privately injects `MIGRATION_TEST_PG_JDBC_URL`, `MIGRATION_TEST_PG_HOST`,
`MIGRATION_TEST_PG_PORT`, `MIGRATION_TEST_PG_DATABASE`, `MIGRATION_TEST_PG_USER`, and
`MIGRATION_TEST_PG_PASSWORD` into test processes. Never persist these credential/endpoint values.

Generated integration harnesses must consume that endpoint and prove the production driver binds
to it. Do not put container start/stop in test methods, per-class JVM hooks, or each Maven invocation.
Reset only marker-owned fixtures or roll back each scenario so reuse never means data leakage.
If an existing harness needs unapproved source edits to support the endpoint, report the exact
reuse limitation rather than pretending setup was shared.

Retain this environment through all waves and complete-suite runs in the same migration invocation.
Recreate only on documented image/extensions/bootstrap/configuration changes or contamination.
The primary verifies cleanup and removes only the exact owned container at run end, including
after failure. Never enable global Testcontainers reuse or retain containers across invocations.

## Same-Run Preparation Reuse and Finalization

Keep the normal Maven local repository between invocations; never use a fresh repository, reinstall
Maven/JDK, or run `clean`/dependency setup for individual waves. Wave arguments reject lifecycle
goals that would accidentally reintroduce this work.

Set `runId` and retain the runner's `preparationProof`. For another execution in the same migration
run, pass that opaque proof as `preparedBuild`. The runner compares dependency/build-environment,
source, and compiled-output digests before skipping completed warmup or compilation. Changed source
or test inputs trigger a fresh test compilation without resetting unchanged dependency setup.
Changed build/dependency inputs invalidate both. Include every module POM, parent/settings/toolchain
input and compiled output in `dependencyInputs`, `compiledInputs`, and `compiledOutputs`.

Use `finalize=false` while deciding whether more coverage work is valuable. This collects the
complete-suite and XML evidence but avoids repeated packaging and HTML generation. When the stop
decision is final, set `preparedBuild` from that summary, `finalize=true`, and `finalizeOnly=true`.
Only unchanged build outputs, test configuration/shared environment, and verified full-suite/coverage
evidence permit finalization without rerunning tests. Generate optional final HTML, run pre-package
checks, then perform the single clean package and archive inspection.

If tests or production inputs change, do not use finalize-only: execute and validate the changed
generation first, retaining the same owned PostgreSQL environment when its configuration is stable.

## Dependency Warmup

- Run Maven dependency/plugin resolution once before compilation.
- Resolve the JaCoCo CLI during the same warmup phase.
- Use the warmed local repository and `-o` for direct wave and full-suite test execution.
- Keep the final `clean package` online so it remains the one authoritative clean dependency and
  package lifecycle check.
- Never install new dependencies unless the manifest changed or warmup proves one missing.

## Coverage I/O

- Persist one `.exec` and native test XML report directory per wave.
- Do not generate per-wave HTML.
- Merge overlap-safe execution data with the native coverage CLI, not another Maven lifecycle.
- Generate one final combined XML report and zero or one final HTML report.
- Keep coverage artifacts outside `target/` when a later `clean` would delete them.

## Packaging

Before the final package:

- Verify temporary shims/helpers are in the intended source set.
- Verify required runtime-driver inputs and assembly mappings.
- Reject forbidden main-source or archive classifications.
- Record the pre-package check result.

After the single final `clean package`, inspect every archive using `jar tf` or the repository's
native archive tool. Verify required driver/configuration entries and forbidden dependencies/classes.
Generate `driver-packaging-plan.json` from runtime descriptors and assembly layout, then run
`inspect-driver-packaging.mjs`. A driver found only inside a nested WAR does not satisfy an external
server library path.
Start from
[driver-packaging-plan-template.json](./driver-packaging-plan-template.json). Each descriptor entry
must list the exact deployment archive pattern implied by its external PostgreSQL `<fileset dir>`
and `includes`; patterns inside `WEB-INF`, `BOOT-INF`, or a `.war/` path are invalid for an external
server library.

## Agent Scheduling

Follow [agent-orchestration.md](./agent-orchestration.md). Launch independent background agents and
continue primary work until completion notification. Do not perform fixed synchronous waits such as
180-second waits when useful independent work remains. Use task agents for verbose builds/tests so
only concise results return to primary context.

## Target Metadata

- Build the exact code-derived object manifest before querying.
- Batch same-kind identity and privilege checks within documented size limits.
- Issue independent read-only identity, privilege, lock, recovery, and capability checks in
  parallel when the client supports parallel tool calls.
- Keep each result distinct in evidence even when calls run concurrently.
- Never run database modifications in parallel or race reads against a mutation.

## Required Evidence

For Maven runs persist:

- Dependency warmup phase count and commands
- Test-compile count, compiled input/output digests, verified reuse, and exact invalidations
- Direct/offline runner status
- Per-wave command, class list, report directory, execution-data path, timestamps, and result
- Lifecycle compile invocations during waves, which must be zero
- Full precompiled suite generation count and one or two runner executions per validated generation
- Coverage merge tool and final XML/HTML counts
- Pre-package checks
- Final clean-package count, which must be one
- Archive inspection results
- Blocking child-agent wait seconds and background-notification behavior
- Parallel target metadata batches and per-check results
- Run-scoped Docker identity digest, creation/recreation reasons, endpoint-binding/fixture-isolation
  assertions, process reuse, and cleanup proof
- Per-command start/completion/elapsed milliseconds and the first current coverage-measurement
  timestamp. Instrument test generation, compatibility repairs, Docker readiness, target checks,
  report/state assembly, and artifact validation separately in the primary ledger; do not hide
  them in one stabilization interval.

Detect the effective JDK/source release before generating Java tests; do not emit text blocks or
newer language features into a Java 8 project. Reuse unchanged source/object/target manifests and
render reports once at the final boundary, without weakening fresh pre-write safety checks.
