# Assessment

Assessment is database-read-only and tracked-source read-only by default. It drives toward a
measurable conversion baseline, always completes with the best available evidence, and never uses a
blocked status. On a clean attached branch it may apply a separately approved, option-gated
baseline-enablement batch under [options.md](./options.md).

Resolve target-backed assessment inputs with [connection-context.md](./connection-context.md). If
connection context is omitted or unavailable, finish code-only assessment with `ACTION NEEDED` and
do not invent target compatibility findings.

Automatically ingest and evaluate optional notes under [coding-notes.md](./coding-notes.md).
Persist `codingNotes` and include the generated **Coding Notes Processing** native task list,
including on no-conversion/scope-review exits. A checked item records evaluation, not validation.

## Executive Summary First

After the title and sanitized run context, make **Executive Summary** the first substantive section
of `assessment-report.md`. State the overall assessment, primary automated conversion scope, two or
three highest-impact exceptions, and the behavior-preserving recommendation. Follow it with a compact
Readiness Scorecard using only canonical `READY`, `CONVERTIBLE`, or `ACTION NEEDED` meanings,
presented as plain title-case cells such as `✅ Ready`, `🔧 Convertible`, and `⚠️ Action needed`.
Never bold or render report status labels in all caps.

Then add **Application & Database Architecture** and put the code map under **Application
Overview**.

Explain:

- Important repository tree, modules, languages, frameworks, and dependencies
- Entry points, runtime components, request/job paths, build boundaries, and packaged artifacts
- Datasources, pools, persistence units, repositories, and transaction boundaries
- SQL, routine, sequence, mapping, and Oracle integration locations
- Unit, integration, and end-to-end test roots, engines, and coverage collection points
- Likely conversion units, dependency order, risk areas, and affected test boundaries

Follow [diagrams.md](./diagrams.md). The assessment must include:

- **Codebase Architecture Map** in Application & Database Architecture
- **Codebase / Database Dependency Map**
- Up to three evidence-backed critical database interaction sequence diagrams
- A measured **Coverage Heatmap** derived from package/class raw counters

Add the non-Mermaid **Oracle Migration Hotspots** canonical table under **Migration Impact
Assessment**; do not repeat it as a separate Oracle-usage table or final findings table. Add
**PostgreSQL Target Readiness**, separating application-required/found/missing objects and verified
versus not-yet-verified capabilities. Put status-free coverage tables and the heatmap under **Test &
Validation Readiness** → **Code Coverage**. Use actual components and relative paths; omit generated,
vendored, inactive, or irrelevant content. Missing evidence becomes a labeled limitation, never an
invented node, relationship, hotspot, flow, or coverage value.

## Inspect the Codebase

Inspect production code, tests, resources, build files, manifests, deployment configuration, and
SQL. Separate active consumers from comments, documentation, generated output, and inactive code.

Find:

- Oracle drivers, providers, dialects, URLs, datasources, and framework integration
- Oracle JDBC APIs, types, cursors, exceptions, SQL/PLSQL, packages, hints, and sequence use
- Embedded, native, dynamic, and configuration-loaded SQL
- Function/procedure calls, parameter and result contracts, and transaction behavior
- ORM types, generated IDs, LOB/JSON handling, fixed schemas, DTOs, and public formats
- Private or unavailable dependencies and every active call site

If `oracle-ddl-folder` is supplied, index it using
[oracle-ddl-source.md](./oracle-ddl-source.md). Reconcile code-derived Oracle identities to the
index without bulk-reading or bulk-converting unrelated objects. Resolve the selected schema under
the supplied migration-project/ancestor root, preferring
`.github/postgres-migrations/<user-provided-name>/artifacts/oracle/<schema>` and otherwise requiring
at least one object-type directory immediately beneath a schema folder. Traverse every discovered
type directory without assuming a fixed list. Assessment records layout resolution, unique matches,
package-member matches, missing definitions, duplicates, files containing multiple primary
objects, filename-fallback warnings, auxiliary definitions, and hashes. It never copies Oracle
bodies into the report.

## Inspect the Target

Derive a bounded object list from active code. Query only those PostgreSQL objects and required
dependencies. Capture exact schema-qualified identities, object kinds, columns, types, precision,
scale, length, nullability, defaults, keys, sequences, routine overloads and arguments, return
shapes, search path, server version, and required extensions.

Follow the evidence-first batching and timeout protocol in
[postgres-mcp.md](./postgres-mcp.md). Never call `postgres_mcp_db_context`, `pgsql_db_context`, or
any tool ending in `_db_context`, even for one object. Verify scalar connection facts with bounded
catalog queries, resolve object identity by exact schema/name, then retrieve only contract fields
needed by active code and direct dependencies.

The selected target contract is authoritative for codebase-facing names and types.
For a target object that is missing, the uniquely matched extracted Oracle DDL is authoritative for
source behavior. If no unique source definition is available, assessment builds a best-effort
behavior-evidence inventory from call sites, mappings, tests, DTOs, dependencies, and observed side
effects, and labels body completeness/confidence and customer-validation requirements. A target
signature or application call shape alone is not authoritative evidence of the original body.

## Establish the Baseline

Before this stage, run the skill-local static detector:

```text
node "<skill-directory>\scripts\detect-migration-scope.mjs" "<codebase>"
```

`<skill-directory>` is the directory containing the loaded `SKILL.md`; the detector is
under `<skill-directory>\scripts`. Resolve its absolute path from that directory, regardless
of the current working directory, and do not search for a fallback copy.

If it returns `NO_CONVERSION_REQUIRED`, stop before dependency installation, build, test discovery,
coverage, target connection, or edits. Render
[no-conversion-assessment-template.md](./no-conversion-assessment-template.md), report the detected
application technologies and zero active database/Oracle findings, classify passive artifacts as
retained optional housekeeping, and run artifact validation. Do not establish a baseline for an
application with no migration-sensitive database code.
If it returns `SCOPE_REVIEW_REQUIRED`, report `ACTION NEEDED` with the exact uninspected paths and
reasons and stop before execution. Resolve those files statically or obtain user scope guidance;
never fall through to the expensive migration workflow merely because the detector was inconclusive.

Follow [testing-and-coverage.md](./testing-and-coverage.md). Detect and run the smallest meaningful
untouched build, every safely supported test engine/profile, and current coverage. Inventory
existing tests by module, language, framework, eligible container, and test case. Follow
[language-adapters.md](./language-adapters.md), inspect the effective runner/adapter dependency
graph, and reconcile fresh native reports to the inventory.
Persist the reviewed result using
[test-discovery-baseline-schema.md](./test-discovery-baseline-schema.md). Separate pre-existing
failures from migration findings. A zero-test success, silently absent engine, or eligible class
without an execution report is not green.

If missing dependencies or build/test configuration prevent compilation or coverage:

1. Preserve the untouched command, diagnostics, commit, status, and report paths as the raw baseline
   attempt.
2. Classify the minimum behavior-preserving enablement batch and its required options.
3. If the options are off, report the exact options/actions and continue assessment.
4. If the options are on, require a clean attached branch and preview the exact tracked changes. In
   standalone `assess`, obtain approval before editing. In `assess-and-convert`, persist the preview
   and continue under the invocation's source-edit authorization without prompting.
5. Apply only authorized baseline-enablement changes. Do not perform semantic Oracle-to-PostgreSQL
   conversion and do not generate tests.
6. Rerun compile and all discovered tests. When `fix-broken-tests` is enabled, repair failures
   exposed by restored engines. Reconcile eligible classes and minimum counts before generating
   fresh engine-separated and combined coverage. This result is the **enabled baseline** and the
   authoritative comparison point for conversion.

Capture unit, integration, and overlap-safe combined coverage lanes where supported, keeping line
and branch counters separate. If enablement still cannot establish coverage, report `ACTION NEEDED`
with exact evidence; do not invent or estimate a baseline.

## Persist Discovery

Follow [state-and-convergence.md](./state-and-convergence.md). Store file digests, module/build/test
structure, active Oracle findings, bounded target contracts, commands, and coverage counters in
`assessment-data.json`. Reuse this evidence during conversion. Rescan only changed inputs or an area
whose compiler, test, or database evidence disproves the saved model.

The active Oracle finding manifest is exhaustive and path-based. Include provider APIs,
datasources/JNDI, dialects, SQL/query constructs, routines, sequences, mappings, migration scripts,
packaging, and deployment descriptors. It is the conversion-scope denominator; do not replace it
with a predicted or actual modified-file count.

## Report

Use the exact H1 title `# Oracle to PostgreSQL Application Code Assessment`.
Render from [assessment-report-template.md](./assessment-report-template.md). Preserve its complete
section order, tables, navigation, and evidence rows; specialize labels/content but do not omit
required blocks. Before assessment completion run the plugin artifact validator in assessment mode
and repair every failure.
Add `<a id="top"></a>` before the H1. After the sanitized run metadata/evidence note, add a
`## Table of Contents` linking every top-level report section through explicit stable HTML anchors.
Place `[Back to Top](#top)` at the end of every top-level section except Table of Contents,
including the final appendix.

Use this exact progressive hierarchy:

1. **Executive Summary** with overall assessment and Readiness Scorecard
2. **Application & Database Architecture** with Application Overview, architecture/dependency
   diagrams, and critical interaction sequences
3. **Migration Impact Assessment** with one canonical Oracle Migration Hotspots table
4. **PostgreSQL Target Readiness** with extracted Oracle DDL source reconciliation,
   required/found/missing objects, and verified/unverified capabilities
5. **Test & Validation Readiness** with existing baseline, Code Coverage tables, measured Coverage
   Heatmap, and migration-sensitive gaps
6. **Conversion Success Criteria** listing the exact evidence conversion must produce
7. **Assessment Outcome** with one decisive recommendation and compact scope/prerequisite/validation
   fields
8. **Appendix / Run Evidence** with effective options, baseline enablement/rollback, commands and
   evidence paths, and Run Metrics

Do not add a final Findings table that repeats the scorecard, impact, target-readiness, or gap rows.
Keep operational details out of the main narrative.

For every `ACTION NEEDED`, state the affected boundary, exact next action, and independent work that
can continue. Offer an alternate strategy only when evidence supports it.

Journal sanitized machine evidence with
[runtime-coordinator.md](./runtime-coordinator.md) and assemble `assessment-data.json` plus the
exact-template assessment report at the assessment boundary, not after each discovery operation.
Reuse reviewed source/code-derived target manifests through the verified cache. Run Java preflight
before authorized Java test source preparation, without changing baseline-generation restrictions
or static-scope early-stop behavior.
Persist source digests, code-map data, active
findings, likely conversion issues, bounded target contracts, commands, test totals, raw coverage
counters by lane/metric/scope, coverage report paths, diagram graph models, hotspot table rows,
critical-flow models, coverage-heatmap nodes with raw counters/grouping, target-readiness rows,
success criteria, outcome fields, sanitized connection context/digest, effective options,
raw/enabled baseline evidence, authorized file digests, coverage section rows, exact phase/run
timing, and recommendation.