# Diagrams and Code Coverage Sections

Diagrams and structured tables are primary migration evidence. Assessment explains what exists and
where risk is concentrated. Conversion explains what changed, what was validated, and what remains.

## Diagram Rules

- Answer one clear question per diagram and add a one-to-three sentence interpretation below it.
- Use only inspected source, build/test reports, persisted state, and bounded database metadata.
- Give every node a stable ID derived from kind plus relative path or schema-qualified object. Keep
  the same ID from assessment through conversion.
- Store each node/edge, label, kind, evidence path/object, grouping rule, and source digest in the
  machine artifact. Do not rely on rendered Markdown as the evidence model.
- Use `flowchart LR` for dependency/lineage and `flowchart TB` for lifecycle/convergence. Use
  `sequenceDiagram` only for selected critical workflows.
- Quote labels, use simple alphanumeric Mermaid IDs, and omit secrets, hosts, credentials, customer
  values, and internal evidence IDs from visible labels.
- Include emoji plus text for status. Color may reinforce status but must never carry meaning alone.
- Target at most 25 visible nodes and 35 edges per diagram. When larger, group by module, package,
  object type, hotspot category, or outcome. Show counts on groups and put detailed evidence in text
  or a smaller scoped diagram.
- Never generate repository-wide class diagrams or full-schema ER diagrams by default. Generate a
  scoped class/ER view only when it answers a migration decision and remains readable.
- If evidence is unavailable, state the limitation and omit the unsupported relationship. Never
  create an empty decorative diagram or infer business logic.

## Assessment Diagrams and Hotspots

Generate exactly two core diagrams plus one Oracle Migration Hotspots table.

### Codebase Architecture Map

Answer: **What is this codebase and how does execution reach Oracle?**

Show actual entry points, runtime components, modules, services, data-access boundaries, drivers,
and grouped Oracle object types. Collapse low-value classes behind their owning module/package. The
primary flow is entry point → service → repository/DAO → Oracle integration → Oracle database.

### Codebase / Database Dependency Map

Answer: **Which code depends on which Oracle objects?**

Connect repository/DAO/configuration files to schema-qualified tables, views, sequences, packages,
procedures, and functions. Label edges with use such as query, write, call, map, or generate ID.
Group repeated code/object nodes, but preserve evidence paths in `assessment-data.json`.

### Oracle Migration Hotspots

Answer: **Where is conversion effort concentrated?**

Do not render this section as Mermaid. Use this table:

| Hotspot | Evidence / scope | Status | Conversion action / impact |
|---|---|---|---|

Create one row per bounded hotspot category: Oracle JDBC API, runtime configuration, SQL syntax,
PL/SQL/package call, data type/ORM mapping, sequence/identity, routine/cursor, transaction semantics,
dynamic SQL, unavailable dependency, external object contract, or measured test/coverage evidence.
Name exact modules/files or object groups, include finding counts, and use only assessment statuses
`✅ Ready`, `🔧 Convertible`, or `⚠️ Action needed`. Status/Result cells use plain title case with no
bold or all-caps labels. State the concrete conversion action or readiness impact. Never replace the
table with a flowchart.

### Coverage Heatmap

Answer: **Where should conversion-focused tests be added first?**

Under assessment **Test & Validation Readiness** → **Code Coverage**, generate one Mermaid flowchart
from fresh native coverage counters. Start at the combined baseline, then show actual
migration-relevant packages or evidence-backed logical groups with raw covered/total line counters
and percentages. Include database access, persistence configuration/model, controllers/business
callers, external integration, native SQL paths, and PostgreSQL integration availability when
measurable. Mark unavailable focused coverage explicitly rather than estimating it.

Persist every grouped node's included package/class paths and summed raw counters. Never average
package percentages, invent a group, or use color alone to indicate priority. Keep the map to at
most 15 visible nodes and explain the highest-priority gaps below it.

## Code Coverage Sections

Assessment contains **Test & Validation Readiness** → **Code Coverage**; conversion contains **Test &
Coverage Results** with coverage subsections. Coverage tables are not Mermaid and neither report
includes a coverage legend. Assessment uses the overall Coverage Heatmap; conversion uses the
Migration Validation Coverage diagram. Coverage cells use `covered/total (percentage)`;
unavailable values use `Unavailable — <exact cause>`.

## Assessment Code Coverage Section

Use these subsections under assessment **Test & Validation Readiness** → **Code Coverage**.

### Coverage Baseline

| Baseline | Build / tests | Coverage result |
|---|---|---|

Include the untouched raw attempt and, when used, the authorized enabled baseline. Name
baseline-enablement files and their authorization source, and link to sanitized evidence rather
than placing them in prose elsewhere.

### Coverage by Lane

| Lane | Scope | Tests / engines | Line coverage | Branch coverage | Evidence |
|---|---|---|---:|---:|---|

Use `🧪 Unit`, `🔗 Integration`, and `📊 Combined` as lane labels.

### Migration-Sensitive Coverage

| Module / path | Behavior or database boundary | Existing tests and lane | Line coverage | Branch coverage | Gap / next action |
|---|---|---|---:|---:|---|

Include unit, integration, and combined rows when measurable. Distinguish the untouched raw attempt
from an authorized enabled baseline and name the frozen comparison scope. Report assessment shims or
harness helpers as a separate support scope rather than including them in application baseline
counters. Show the next safely testable behavior rather than a gap to a numeric threshold. Do not
display repository or default numeric targets. Use `Unavailable` plus the exact cause when a metric
cannot be measured; never display an estimated percentage.

## Conversion Code Coverage Section

Under conversion **Test & Coverage Results**, do not include a coverage legend. Use these
subsections.

### Coverage Comparison

| Scope | Metric | Baseline | Final | Δ percentage points |
|---|---|---:|---:|---:|

Use the label **Original production classes — fixed comparison set** for production classes present
at the starting commit. Include separate line and branch rows for that scope, a named selected
high-risk scope such as **Eight migration-critical classes**, and final full-production scope. The
migration-critical scope is not every modified file/class: list its exact classes immediately below
the table. Use `N/A` for a full-production baseline when new classes make no equivalent denominator.

### Coverage by Lane

| Lane | Baseline line | Final line | Baseline branch | Final branch | Evidence |
|---|---:|---:|---:|---:|---|

Use the same `🧪 Unit`, `🔗 Integration`, and `📊 Combined` lane labels.

### Coverage Convergence

When coverage waves ran, include:

| Wave | Planned behaviors | Tests added / repaired | Line Δ | Branch Δ | Result |
|---:|---|---|---:|---:|---|

### Remaining Coverage Gaps

| Area | Uncovered behavior | Classification / evidence | Next action | Status |
|---|---|---|---|---|

If there are no remaining gaps, include one `✅ MEASURED` row stating that no classified gap
remains. Never omit the table.

## Conversion Diagrams

Generate exactly three core diagrams from conversion state and fresh validation results.

### Before → After Architecture

Answer: **What did the migration change?**

Show separate Before and After subgraphs. Reuse assessment node IDs and labels for unchanged
components. Highlight only real provider, configuration, data-access, shim, and database-object
changes. Preserve the business-layer shape unless conversion intentionally changed it.

### Conversion Traceability Map

Answer: **What is the lineage and evidence for each converted dependency?**

Show Oracle dependency → converted file/component → PostgreSQL object/contract → test evidence →
result. Group by bounded conversion unit when the full lineage exceeds the readability budget.
Include generated object, shim, and human-review labels where applicable.

### Migration Validation Coverage

Answer: **Which converted behaviors were directly validated, and which migration-critical paths
remain external or untested?**

Show converted production components/behaviors flowing to compile, unit, Docker/integration,
authoritative target contract/runtime evidence, and final result. Include only migration-sensitive
paths, not the full legacy application. Label every node with concrete test/evidence status and keep
overall legacy coverage separate. Highlight runtime binding and external IDAM gaps explicitly.

Put all validation and convergence evidence in the single top-level Validation Results table defined
by [conversion.md](./conversion.md). Do not generate a separate Validation / Convergence Map section
or repeat the same gates in a second table or diagram.

## Remaining Work & Exceptions

Do not render this section as Mermaid. Use three compact tables:

### Required Before Production

| Area | Evidence / impact | Required action | Owner / prerequisite |
|---|---|---|---|

### Recommended

| Area | Reason | Recommended action |
|---|---|---|

### No Action Required

| Area | Evidence |
|---|---|

Create one row per bounded issue or completed area. Required actions must be executable and identify
the owner/prerequisite. Do not use vague entries such as `future work`. No Action Required names
completed migration areas so customers can distinguish closed work from unresolved work.

## Critical Workflow Sequences

Optionally select up to three high-value database-facing workflows based on business reach,
transaction complexity, routine/cursor use, or migration risk. Assessment shows the current sequence.
Conversion shows the paired PostgreSQL sequence with the same logical actors and outcome, plus the
changed database call and validating test. Do not generate a sequence for every repository method.

## Validation

- Check every visible node and edge against its persisted evidence before report generation.
- Validate Mermaid syntax for the two conversion diagrams with a repository renderer or Mermaid CLI
  when available. Otherwise check
  fenced blocks, diagram declarations, IDs, edge targets, subgraph closure, and report that render
  validation was unavailable.
- Render or preview the final Markdown when a report tool is available and fix syntax, clipping, or
  unreadable density.
- Persist diagram validation status, coverage-section rows/statuses, exception-table rows, and any
  grouping/omission rationale in assessment/conversion state.