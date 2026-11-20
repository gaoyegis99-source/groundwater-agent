<a id="top"></a>
# Oracle application migration

The `migrate-oracle-app` skill in Postgres Skills assesses and converts Oracle-backed
application codebases to PostgreSQL. It uses evidence-backed Mermaid architecture,
dependency, traceability, and validation diagrams, plus iterative builds and tests.
It covers Java, .NET, Python, C, and C++ database-facing application code; it is not
an Oracle database export or bulk data-transfer service.

This guide describes the migration workflow through GitHub Copilot CLI and VS Code
Copilot. The skill is included in the existing `postgres-skills` plugin and shares
its `postgres-mcp` server. See the [main README](../README.md) for plugin installation
across supported clients, [SETUP.md](SETUP.md) for connection and privacy guidance,
and the [plugin changelog](CHANGELOG.md) for release history.

## Table of Contents

- [Included](#included)
- [Prerequisites](#prerequisites)
- [Deterministic PostgreSQL Provider Selection](#postgres-provider-selection)
- [Option 1 — VS Code with the Copilot Session Target](#vscode-copilot)
- [Option 2 — Copilot CLI](#copilot-cli)
- [Supported Installation Boundary](#installation-boundary)
- [Run the Skill](#run-skill)
- [Skill Settings and Options](#skill-settings-options)
- [Examples](#examples)
- [Test the Plugin Contracts](#test-contracts)
- [Versioning](#versioning)
- [Update or Remove](#update-remove)
- [Safety](#safety)
- [Complete End-to-End Environment Setup](#complete-setup)

<a id="included"></a>
## Included

- `plugin.json`: portable plugin manifest and authoritative version
- `mcp.json`: portable bundled Microsoft PostgreSQL MCP server
- `.mcp.json`: matching local-workspace MCP configuration for plugin development
- `CHANGELOG.md`: release history
- `skills/migrate-oracle-app/SKILL.md`: workflow and safety rules
- `skills/migrate-oracle-app/references/`: phase-specific instructions
- `skills/migrate-oracle-app/references/*-report-template.md`: exact
  assessment/conversion report structures
- `skills/migrate-oracle-app/references/test-discovery-baseline-schema.md`:
  reviewed runner/adapter, container/case discovery, and minimum-count contract
- `skills/migrate-oracle-app/references/language-adapters.md`: conversion,
  test discovery, integration, packaging, and coverage rules for Java, .NET, Python, C, and C++
- `skills/migrate-oracle-app/scripts/validate-migration-artifacts.mjs`: fail-closed report/state/evidence validator
- `skills/migrate-oracle-app/scripts/coding-notes.mjs`: read-only notes ingestion, private provenance,
  content-digest verification, exhaustive note inventory and native report task lists
- `skills/migrate-oracle-app/scripts/migration-runtime.mjs`: primary-owner execution lease, verified source/target-object
  caches, bounded recovery checkpoints, and final report/state assembly
- `skills/migrate-oracle-app/scripts/java-test-preflight.mjs`: effective per-module Java release detection and draft syntax
  validation before generating/staging Java tests
- `skills/migrate-oracle-app/scripts/format-run-metrics.mjs`: renders consistent readable report timing from exact machine
  evidence and the run's pinned local time zone
- `skills/migrate-oracle-app/scripts/detect-migration-scope.mjs`: static pre-execution detector for active database and Oracle
  application surfaces. From the skill directory, its fixed location is
  `scripts\detect-migration-scope.mjs` within the skill directory.
- `skills/migrate-oracle-app/scripts/index-oracle-ddl.mjs`: resolves a schema/type-directory layout below a read-only
  migration-project root and indexes one primary source object per file without persisting bodies.
- `skills/migrate-oracle-app/scripts/run-maven-waves.mjs`: compile-once, direct/offline wave execution, JaCoCo CLI merge, and
  single final clean-package runner
- `skills/migrate-oracle-app/scripts/inspect-driver-packaging.mjs`: reconciles packaged runtime descriptors with exact
  PostgreSQL driver locations and detects WAR-only driver copies
- `skills/migrate-oracle-app/scripts/postgres-provider-policy.mjs`: enforces deterministic VS Code extension versus Copilot
  CLI bundled-MCP selection without fallback or provider mixing
- `tests/`: option, report-contract, and artifact-validator tests
- `package.json`: migration test and skill-local script commands
- [Local tests](#test-contracts): commands and test prerequisites

The plugin does not bundle a database or credentials. VS Code with the **Copilot Session Target**
uses the Microsoft PostgreSQL extension's MCP tools. Copilot CLI starts the bundled Microsoft
`postgres-mcp` server through `npx`. Both paths use Docker for PostgreSQL integration tests and
Node.js for the plugin's deterministic migration scripts.

Before any dependency installation, target connection, build, tests, Docker, or edits, the skill
runs a static migration-scope detector. A frontend-only application with no active JDBC, SQL,
datasource, ORM, Oracle provider/API, migration SQL, or other database-facing code exits as
**No application conversion required**. Detected technology such as Angular or React is reported;
DDL and database integration are marked not applicable; passive JARs remain optional housekeeping.
Unreadable, oversized, symbolic-link, or unknown non-inert files produce `SCOPE_REVIEW_REQUIRED`;
the workflow stops for focused review rather than launching the full migration.

[Back to Top](#top)

<a id="prerequisites"></a>
## Prerequisites

### Required for Every Installation

| Requirement | Why it is required | Verify |
|---|---|---|
| Git | The codebase must be an existing Git repository with an attached branch and at least one commit. | `git --version` |
| Node.js 22 or newer | Runs scope detection, DDL indexing, policy checks, artifact validation, and optimized test orchestration. | `node --version` |
| npm and npx | Installs dependencies and starts the bundled PostgreSQL MCP server in Copilot CLI. | `npm --version`; `npx --version` |
| GitHub Copilot access | Runs the Copilot harness in VS Code or Copilot CLI. | Sign in and start a Copilot session |
| Network access | Required for Git, npm, Maven/other package feeds, Docker images, and the selected PostgreSQL target. | Verify corporate proxy/VPN and package feeds |
| Target PostgreSQL profile | Required for authoritative target-backed assessment or conversion. | List profiles through the selected PostgreSQL provider |

**Node.js remains required in VS Code.** The PostgreSQL extension supplies database MCP tools, but
it does not replace the plugin's Node.js scripts. If `node` is missing, stop before migration work;
do not report success based only on database-tool availability.

### Required for PostgreSQL Integration Testing

| Requirement | Why it is required | Verify |
|---|---|---|
| Docker Desktop or Docker Engine | Runs disposable PostgreSQL integration environments and synthetic-data tests. | `docker version` |
| Docker Compose plugin | Supports projects that define integration environments through Compose. | `docker compose version` |
| Sufficient local resources | Java builds and PostgreSQL containers need disk, memory, and available ports. | Start Docker and run `docker info` |

Docker Desktop is user-controlled. Start it before the migration, or explicitly enable
`start-docker-desktop`; the skill does not start or resume it by default.

### Additional Requirements for Java Codebases

- Install the JDK major version required by the codebase. Determine it from `.sdkmanrc`,
  `.java-version`, Maven Toolchains, `maven.compiler.release`, `maven.compiler.source/target`, or
  the build documentation. Do not silently upgrade the application to a newer Java release.
- Prefer the repository Maven Wrapper (`mvnw.cmd` / `mvnw`) when present. Otherwise install Maven
  3.9 or the version required by the project.
- In VS Code, install Microsoft's **Extension Pack for Java** for language diagnostics, test
  discovery, debugging, and Maven integration.
- Ensure corporate Maven repositories, mirrors, certificates, and proxy settings are configured
  before baseline execution.

See [Complete End-to-End Environment Setup](#complete-setup) for installation
and verification steps.

[Back to Top](#top)

<a id="postgres-provider-selection"></a>
## Deterministic PostgreSQL Provider Selection

Provider selection occurs once at run start. There is no automatic fallback and providers are never
mixed.

| Agent harness | Required PostgreSQL provider | Tool family |
|---|---|---|
| Native VS Code Copilot, including **Copilot Session Target** | Microsoft PostgreSQL extension for VS Code (`PostgreSQL MCP`) | `pgsql_*` |
| Copilot CLI, including CLI connected to VS Code | Plugin-bundled NPM server (`postgres-mcp`) | `postgres_mcp_*` |

### Operation Mapping

| Purpose | VS Code Copilot | Copilot CLI |
|---|---|---|
| List profiles | `pgsql_list_connection_profiles` | `postgres_mcp_list_connection_profiles` |
| Connect | `pgsql_connect` | `postgres_mcp_connect` |
| List databases | `pgsql_list_databases` | `postgres_mcp_list_databases` |
| Capabilities | `pgsql_get_server_capabilities` | `postgres_mcp_get_server_capabilities` |
| Bounded query | `pgsql_query` | `postgres_mcp_query` |
| Authorized modification | `pgsql_modify` | `postgres_mcp_modify` |
| Disconnect | `pgsql_disconnect` | `postgres_mcp_disconnect` |

### Exact-Name Deferred Tool Discovery

Deferred PostgreSQL tools must be searched by their exact function names for the selected provider.
Use these tool-search queries, not a broad mix of server display names and generic operation words:

**VS Code Copilot**

```text
pgsql_list_connection_profiles pgsql_connect pgsql_list_databases pgsql_get_server_capabilities pgsql_query pgsql_modify pgsql_disconnect
```

**Copilot CLI**

```text
postgres_mcp_list_connection_profiles postgres_mcp_connect postgres_mcp_list_databases postgres_mcp_get_server_capabilities postgres_mcp_query postgres_mcp_modify postgres_mcp_disconnect
```

Search results are bounded, not a complete inventory. If only the wrong family or a subset of tools
is returned, reject wrong-provider results, keep correctly resolved tools, and search each missing
operation individually, for example `pgsql_query` or `pgsql_get_server_capabilities`. Repeated
results are deduplicated. Use at most one exact-name batch plus one probe per operation.

The skill cannot declare the provider unavailable or skip target-backed MCP work based on one
truncated or wrong-family response. It records the actual discovery queries and returned identifiers;
artifact validation checks that every unresolved operation received its exact-name probe.
Failed lookups are reported as search failures, not evidence that the extension or NPM server is
absent. There is never a cross-provider fallback.

Never use `pgsql_db_context`, `postgres_mcp_db_context`, any operation ending in `_db_context`, or
`pgsql_visualize_schema`. Migration discovery uses exact-name and OID-bounded catalog queries.

Treat VS Code extension profiles and NPM MCP profiles as separate stores. The same profile display
name does not prove the same target. Every query, modification, cleanup, and disconnect in a run
uses the selected provider.

[Back to Top](#top)

<a id="vscode-copilot"></a>
## Option 1 — VS Code with the Copilot Session Target (Recommended)

This is distinct from the VS Code **Local** Session Target. VS Code Agent Host runs the Copilot SDK
harness and supplies editor context, diagnostics, tool access, sessions, and visual change review.

### Required VS Code Components

1. Current VS Code with workspace trust enabled for the migration codebase.
2. GitHub Copilot access and the GitHub Copilot extension.
3. Microsoft **PostgreSQL** extension for VS Code. Confirm that it contributes the
   `PostgreSQL MCP` `pgsql_*` tools.
4. Node.js 22+, Git, Docker, and the project language/build toolchain installed on the machine.

### Install the Plugin From Git

1. Set `chat.plugins.enabled` to `true`.
2. Open the Command Palette.
3. Run **Chat: Install Plugin From Source**.
4. Select the `postgres-skills` payload from `https://github.com/microsoft/postgres-skills`
   using the client's supported source selector. The plugin root is `plugin/`, not
   the repository root. See the [main installation guide](../README.md#get-started)
   for marketplace-based CLI installation.
5. Review and approve the trust prompt.
6. Open Extensions and search for `@agentPlugins`.
7. Open **Chat: Configure Skills** or Chat gear → **Plugins** and verify
   `migrate-oracle-app` is enabled.
8. Open a new Agent session and select **Copilot** as the Session Target, not **Local**.

### Load This Local Checkout During Development

Add the plugin root to VS Code `settings.json`:

```json
{
  "chat.plugins.enabled": true,
  "chat.pluginLocations": {
    "C:\\dev\\repos\\postgres-skills\\plugin": true
  }
}
```

Run **Developer: Reload Window**, start a new Copilot session, and verify the plugin and skill.

### Verify the VS Code PostgreSQL Provider

Before invoking the migration:

1. Open the Chat tool picker.
2. Confirm `PostgreSQL MCP` exposes `pgsql_list_connection_profiles`, `pgsql_connect`,
   `pgsql_list_databases`, `pgsql_get_server_capabilities`, `pgsql_query`, `pgsql_modify`, and
   `pgsql_disconnect`.
3. Configure a saved extension connection profile for the target.
4. Prefer a least-privilege read-only profile for assessment and use a separate write-enabled
   profile only for explicitly authorized persistent DDL or target-write tests.
5. If any required `pgsql_*` operation is missing, repair the extension installation. Do not
   fallback to `postgres-mcp`.

[Back to Top](#top)

<a id="copilot-cli"></a>
## Option 2 — Copilot CLI

Copilot CLI owns the agent loop even when connected to VS Code. It always uses the plugin-bundled
NPM PostgreSQL MCP server.

### Configure the Bundled PostgreSQL MCP Profile

Create a least-privilege read-only profile:

```powershell
npx -y @microsoft/postgres-mcp@latest connection add my-database "host=db.example.com user=agent_user dbname=app sslmode=verify-full" --access-mode ro
npx -y @microsoft/postgres-mcp@latest connection set-password my-database
npx -y @microsoft/postgres-mcp@latest connection list
```

Enter the password only at the hidden `set-password` prompt. Never place it in a prompt, command,
configuration file, source file, fixture, or report. Configure a separate saved `rw` profile only
when approved target writes are required.

### Run This Local Checkout

```powershell
Set-Location C:\dev\repos\postgres-skills\plugin
copilot --plugin-dir .
```

Verify discovery:

```powershell
copilot --plugin-dir . plugin list
copilot --plugin-dir . skill list
copilot --plugin-dir . mcp list
```

Local development uses `.mcp.json` because some Copilot CLI versions do not activate `mcp.json`
from `--plugin-dir` alone.

### Install Persistently From Git

```shell
copilot plugin marketplace add microsoft/postgres-skills
copilot plugin install postgres-skills@postgres-skills
```

Verify and enable:

```shell
copilot plugin list
copilot skill list
copilot mcp list
copilot plugin enable postgres-skills
```

The plugin's `mcp.json` uses the moving `@microsoft/postgres-mcp@latest` release tag
under the shared server ID `postgres-mcp`; it is not pinned to a fixed package version.

[Back to Top](#top)

<a id="installation-boundary"></a>
## Supported Installation Boundary

The complete Postgres Skills plugin installs the skill, its local `scripts/` and
`references/`, and the shared MCP configuration. Copying only `SKILL.md` omits required
scripts and references. Skills-only use also requires an independently configured
compatible PostgreSQL provider for target-backed work.

[Back to Top](#top)

<a id="run-skill"></a>
## Run the Skill

Type `/` and select **migrate-oracle-app**, or ask naturally:

```text
Assess this Oracle codebase against the target PostgreSQL database.
Convert the codebase using the recommended approach.
Assess and convert this Oracle codebase using the target PostgreSQL database.
```

[Back to Top](#top)

<a id="skill-settings-options"></a>
## Skill Settings and Options

### Generated Reports

Assessment writes `.github/migrate-oracle-app/assessment/assessment-report.md`,
`assessment-data.json`, `test-discovery-baseline.json`, and local `coverage/`.
Conversion writes `.github/migrate-oracle-app/conversions/conversion-report.md`,
`conversion-state.json`, `file-diffs.md`, local `coverage/`, and `database-ddl/`
when needed and no repository convention exists. These paths are inside the
application Git repository, not the installed skill directory.


- `assessment-report.md` uses the title **Oracle to PostgreSQL Application Code Assessment**.
- `conversion-report.md` uses the title **Oracle to PostgreSQL Application Code Conversion**.
- When static scope detection returns `NO_CONVERSION_REQUIRED`, the reports use the dedicated
  no-conversion templates, document the detected technology stack, mark DDL/database integration as
  not applicable, and state that installs/builds/tests/Docker/target calls were not run by design.
- Assessment persists `test-discovery-baseline.json`. A successful build is not accepted until
  required runners/adapters are present and fresh native reports reconcile every eligible test
  container/case.
- Conversion reports adapter availability, discovery reconciliation, and broken-test repair as
  separate gates. Coverage is generated afterward in separate applicable framework/toolchain,
  integration, and overlap-safe combined lanes.
- When `test-target-writes` is enabled, reports reconcile whether applicable production paths
  actually wrote through the authoritative PostgreSQL driver. A read-only or otherwise unexecuted
  requested gate is `⚠️ Not executed` and makes the conversion `PARTIAL`; it is never silently
  deferred as runtime validation.
- `fix-broken-tests` covers every failure/error exposed by complete discovery, including
  pre-existing failures. Reports list per-class repair attempts and cannot pass artifact validation
  while test discovery remains red.
- Conversion reports reconcile every active Oracle finding by category. Modified-file count is not
  a scope target; safely convertible remaining findings must be zero.
- The primary agent acts as orchestrator. When child agents are available and at least two ready
  units are independent, the skill must launch a parallel batch for discovery, disjoint
  implementation, failure clusters, test waves, or verbose build/test work. Reports include agent
  assignments, ownership, overlap, and fallback evidence.
- Maven runs pre-resolve dependencies, compile tests once per changed input generation, execute each unit/integration wave
  directly against precompiled classes with separate XML/JaCoCo evidence, merge coverage through
  the JaCoCo CLI, run the complete suite once, and perform one final clean package plus archive
  inspection.
- Enabled test creation requires at least two unit/characterization waves and two integration
  waves. The lane minimums are independent; extra value-backed waves are optional up to ten.
- Both reports include a linked Table of Contents with stable section anchors and a Back to Top link
  at the end of every top-level section except the Table of Contents itself.
- The executive conversion scorecard contains exactly six customer outcomes: application
  conversion, build/package, unit tests, PostgreSQL integration tests, target validation, and
  production readiness. File counts and change inventory remain in the detailed appendix.
- Conversion scorecards use `Area | Result | Status`. Status is the last column and uses one emoji
  plus a plain title-case label such as `✅ Passed` or `⚠️ Action needed`; Area and Result otherwise
  remain text-only.
- A concise `Next Steps / Actions Needed` table immediately follows the scorecard and contains no
  more than five required/recommended customer actions, named owners, and an ordered recommended
  production-readiness path. It is a handoff checklist, not a duplicate of every later warning.
- The Modified Tracked Files inventory begins with a Git-derived summary of updated file count,
  additions, deletions, and total changed lines.
- Every modified-file row includes current-file updated line ranges from zero-context Git hunks;
  deletion-only changes use explicit deletion-point notation.
- Every modified path links to a sequential Markdown heading in one sanitized `file-diffs.md`
  artifact. Literal endpoints, credentials, email addresses, and customer values are redacted or
  withheld. IDs use `FILE-00001` format with at least five digits and expand for larger file counts.
- `file-diffs.md` includes its own linked Table of Contents and every file section ends with Back to
  Top and Back to Conversion Report navigation.
- Conversion artifacts always use one stable `.github/migrate-oracle-app/conversions/` directory.
  Reruns replace the prior skill-owned report/state/diff/coverage/DDL set instead of creating dated
  subfolders.

### Modes

| Mode | Purpose | Database behavior | Source changes |
| --- | --- | --- | --- |
| `assess` | Produce an executive-first assessment with architecture/dependency diagrams, critical database flows, Oracle Migration Hotspots, PostgreSQL target readiness, measured coverage heatmap, success criteria, and a decisive outcome. | Always database-read-only. Without connection context, assessment completes code-only with `ACTION NEEDED`. | Read-only by default. May apply an approved, option-gated baseline-enablement batch on a clean attached branch. |
| `convert` | Convert from a current assessment using bounded compile/test/Docker loops. | Uses the supplied target context for validation. Explicit `create-persistent-ddl` and `test-target-writes` authorize their separate bounded write classes. | After conversion-preview approval. |
| `assess-and-convert` | Run assessment and conversion as one uninterrupted, fire-and-forget workflow. | Assessment remains database-read-only; conversion executes only explicitly enabled create-only DDL or target runtime tests. | The invocation authorizes reversible source edits and explicit options; previews are recorded without pausing. |

### Invocation Settings

| Setting | Required | Default | Details |
| --- | --- | --- | --- |
| `mode` | Recommended | Inferred from an unambiguous natural-language request | One of `assess`, `convert`, or `assess-and-convert`. |
| `codebase` | Yes, unless the current workspace is unambiguous | Current trusted workspace | Root of the Oracle codebase Git repository. Assessment and conversion operate in this one repository; conversion edits it in place. |
| `postgres-connection` | Target-backed assessment/conversion | None | Exact saved Microsoft `postgres-mcp` profile name or supported ID. Never pass a password or connection string. |
| `database` | Target-backed assessment/conversion | Unique database from selected profile, when provable | Exact PostgreSQL database. An explicit value overrides the profile default; the skill never silently falls back. |
| `schema` | Required for `test-target-writes` | One safe schema from codebase evidence and search path | Exact PostgreSQL schema. Target-write testing never derives or inherits it. |
| `oracle-ddl-folder` | No | None | Read-only migration-project/ancestor root. The preferred layout is `.github/postgres-migrations/<user-provided-name>/artifacts/oracle/<schema>/<object-type>`. The middle name and set of object-type folders are dynamic; any bounded layout with the selected schema and at least one object-type folder is accepted. If absent or unmatched, the skill performs documented best-effort reconstruction and synthetic Docker validation. It does not authorize target writes. |
| `notes` | No | None | Read-only coding-notes file or migration project directory containing `results/application_guidance/coding_notes.md`; automatically reconciled in every report. |
| `postgres-image` | No | Selected target's PostgreSQL major version | Docker image used for the disposable integration-test environment. |
| `options` | No | Every canonical option is `off` | Comma-separated migration options. Assessment and conversion may recommend options but never enable them implicitly. |

Do not put spaces inside the comma-separated `options` value. Paths containing spaces should be
quoted according to the client or shell invoking the skill.

### Coding Notes Processing

Supply `notes="/path/to/coding_notes.md"` or `notes="/path/to/migration-project"`.
Directory input resolves only `results/application_guidance/coding_notes.md` beneath that
explicit directory, never another directory or guessed file. Missing, unreadable, ambiguous and
omitted inputs are reported honestly; notes are optional guidance and never authorize source edits,
database access or migration options.

Every assessment/conversion report, including no-conversion and scope-review exits, automatically
contains **Coding Notes Processing** with native Markdown task-list checkboxes. All inventoried
items retain stable IDs and evidence-backed dispositions: applied, no-change-needed, not-applicable,
conflicting or pending. Checked means read/evaluated with a recorded disposition, **not** necessarily
implemented or runtime-validated; unread items stay unchecked and pending actions/conflicts retain
their next actions. Content digests and private local provenance invalidate stale processing
evidence and notes-dependent caches on reruns. Raw notes and private absolute paths are not copied
into public notes state/reports; normal privacy and migration safety rules remain mandatory.
Path references are keyed with a private per-worktree secret, not unsalted absolute-path hashes.
Copied reports or CI without private provenance require explicit notes re-ingestion and evaluation;
verification does not silently skip missing evidence. Repeated prose labels receive generated IDs,
while genuinely explicit identifiers retain their IDs and duplicate-ID conflicts remain visible.

The loaded skill's `scripts/coding-notes.mjs` resolves input and generates the checklist from the
same `codingNotes` object used by runtime finalization and artifact validation. Resolve its absolute
path from the loaded skill directory independently of the application's current working directory.
See the [notes contract](skills/migrate-oracle-app/references/coding-notes.md) for input limits,
evidence schema, early exits and rerun behavior. Notes checks do not replace any target/test/runtime
gate.

### Codebase Git Repository

| Requirement | Behavior |
| --- | --- |
| Repository root | `codebase` must resolve to the Git worktree root, not a separate source or destination folder. |
| Initial history | The repository must have at least one commit so conversion has a stable baseline. |
| Branch | Conversion and tracked assessment baseline enablement require an attached branch, not detached `HEAD`. |
| Clean start | Baseline enablement requires clean codebase-owned paths. Conversion also permits exact current-run baseline-enablement edits when their authorization source and digests validate against assessment state. |
| In-place edits | Converted code, tests, shims, DDL, and reports are written inside the `codebase` repository. |
| Change tracking | The skill records the starting commit and branch, then captures modified and untracked paths after every bounded unit. |
| Staging and commits | The skill never stages, commits, creates branches, stashes, resets, or pushes. Final changes remain in the working tree for customer review. |
| Concurrent edits | If a file changes outside the skill after its baseline digest, the affected unit pauses rather than overwriting it. Independent units may continue. |
| Missing Git setup | The skill stops before assessment/conversion and asks the user to initialize Git, select a branch, create an initial commit, or clean the tree. It does not perform those actions automatically. |

### Coverage Artifacts and Git Ignore Rules

The skill retains detailed coverage locally and adds these rules to the codebase root `.gitignore`
only when source edits are already authorized:

```gitignore
/.github/migrate-oracle-app/assessment/coverage/
/.github/migrate-oracle-app/conversions/coverage/
```

Standalone `convert` applies them after conversion-preview approval. `assess-and-convert` applies
them in its authorized source-edit stage. Standalone `assess` can include them in an approved exact
baseline-enablement batch; otherwise read-only assessment leaves `.gitignore` unchanged and records
the recommendation. No-conversion and scope-review stops never apply them.

Existing ignore content is preserved and repeated runs do not duplicate rules. These rules leave
reports, state, diffs, and DDL outside their scope. Already tracked coverage is not automatically
untracked, and coverage is not deleted to reduce Git noise. The local files remain available for
baseline comparison and artifact validation, but a fresh clone will not contain ignored evidence;
use a separate run-artifact archive when it must be shared or retained for audit.

### Migration Options

| Option | Default | When enabled | When disabled | Safety boundary |
| --- | --- | --- | --- | --- |
| `establish-baseline` | `off` | Permits the minimum reversible dependency, build, test-runner, or coverage-harness edits needed to compile and measure pre-conversion coverage. | Preserves the failed untouched attempt, reports the exact blocker, and continues independent assessment. | Requires a separate exact preview approval. Cannot perform semantic Oracle-to-PostgreSQL conversion or generate tests before baseline capture. |
| `create-shims` | `off` | Creates a minimal, isolated, tested compatibility shim after ordinary dependency resolution is proven unavailable, including during baseline enablement. | Documents the dependency, defers dependent units, and continues independent work. | An active missing API needs both `establish-baseline` and `create-shims` during assessment. Never guesses business/security behavior. |
| `create-persistent-ddl` | `off` | Reconstructs or converts missing codebase-owned objects, Docker-validates the DDL, then executes `CREATE` in the selected persistent PostgreSQL database and verifies each object without another prompt. | Generates and Docker-validates deploy/undo scripts but performs no persistent write. | Requires source-backed or disclosed best-effort reconstruction, a saved write-enabled profile, fresh target checks, Docker proof, privileges, exact DDL hash, rollback, and immediate verification. Inferred or fail-closed bodies still require customer validation. Never permits `ALTER`, `REPLACE`, `DROP`, seeding, DML, or customer-data changes. |
| `test-target-writes` | `off` | Runs Docker-proven mutation scenarios against the exact saved-profile/database/schema target using `postgres_mcp_modify`, marker-owned rows, and active code-derived sequence calls. | Performs read-only target contract validation only. | Requires a saved `rw` profile, modify-tool availability, writable server/role defaults, fresh identity/privilege/lock/contract checks, an exact impact preview, recorded sequence increments, marker-scoped cleanup, and zero remaining rows. Never creates objects or changes customer rows. |
| `start-docker-desktop` | `off` | Permits one bounded attempt to launch/resume an installed Docker Desktop when the engine is not already running. | Uses an already-running engine, but never launches Desktop or changes its service/active state. | Does not permit install/update, autostart/settings changes, license acceptance, service reconfiguration, or restart loops. |
| `remove-unused-artifacts` | `off` | Permits evidence-backed removal of unused checked-in JARs or other repository artifacts. | Retains the file and reports it as `Unused — retained`. | Requires zero active code/build/package references, unchanged digest, rollback, and passing build/tests/package/archive inspection. |
| `create-unit-tests` | `off` | Adds focused unit and characterization tests for converted behavior lacking coverage. | Reports unit coverage gaps without adding tests. | Cannot weaken valid assertions or add superficial tests for percentage gain. |
| `create-integration-tests` | `off` | Adds Docker PostgreSQL integration and codebase-to-database end-to-end tests. | Reports integration gaps without adding tests. | Uses the production driver/path and marker-owned disposable data. |
| `fix-broken-tests` | `off` | Repairs stale or broken affected tests after production behavior is verified, including tests needed for an enabled baseline. | Runs and reports existing tests but does not edit test files. | Production defects are fixed first; valid behavior assertions are preserved. |
| `tests` | Alias | Enables `establish-baseline`, `create-unit-tests`, `create-integration-tests`, and `fix-broken-tests`. | N/A | Does not enable shims, persistent DDL, or target writes. There is intentionally no `all` alias. |

Existing tests are always discovered and run, regardless of options. Disabled options never become
blanket blockers. Assessment first preserves the untouched build attempt. If approved enablement is
needed, its resulting coverage is labeled **enabled baseline** and becomes conversion's comparison
point. The comparison scope is frozen to production classes in the untouched commit. Assessment
shims and harness helpers are reported separately and their isolated checks are not merged into
application baseline coverage. Generated application tests never run before baseline capture.
Assessment and conversion reports show every canonical option as on or off, its source, actions
performed, and actions skipped.

### Code Coverage Report Sections

Assessment places Code Coverage under Test & Validation Readiness; conversion places it under Test &
Coverage Results. Coverage data uses Markdown tables plus assessment and migration-validation
Mermaid views derived from actual evidence.

Neither report includes a coverage legend. Assessment starts directly with Coverage Baseline, and
its baseline, lane, and migration-sensitive tables have no Status column. Conversion starts directly
with Coverage Comparison; its comparison and lane tables have no Objective or Status column.

Assessment tables show raw/enabled baseline provenance, `🧪 Unit`, `🔗 Integration`, and
`📊 Combined` lanes, and migration-sensitive gaps. Conversion tables show baseline versus final
line/branch results, percentage-point deltas, lane contribution, coverage-wave progress, and every
remaining gap. Coverage cells always use `covered/total (percentage)` or
`Unavailable — <exact cause>`.

Coverage comparison labels use **Original production classes — fixed comparison set** for the
starting-commit class set. Selected high-risk subsets use explicit names such as **Eight
migration-critical classes** and list their members; they are not presented as every modified file
or class.

### Run Timing

Both reports contain Run Metrics in their execution-evidence appendices.

Both tables contain `Assessment`, `Conversion`, and `Total Runs`, with only `Phase`, `Start`,
`Completed`, and `Wall Clock Duration` columns. The machine's local time zone is captured at run
start, stored in `runMetrics.timeZone`, and labeled once above each table.

Start and Completed use readable timestamps such as `4:10pm, June 10, 2026`. Durations spell out
minutes and seconds, including zero values, and include hours when nonzero: `0 minutes, 45 seconds`,
`3 minutes, 0 seconds`, or `1 hour, 54 minutes, 30 seconds`. Display values round to the nearest
second; JSON retains ISO 8601 timestamps and exact elapsed milliseconds.

`format-run-metrics.mjs` generates the section from the shared `runMetrics` evidence shape, and
artifact validation rejects formatting or timing drift. Assessment-only and static-scope reports
mark Conversion as `Not run`; interrupted/running phases are explicitly labeled rather than
presented as complete. Total Runs is the actual wall-clock span, not a sum of phase durations.
Combined runs synchronize the final timing snapshot into both reports before artifact validation.

### Database Test Environment

The conversion report combines persistent database changes, temporary integration fixtures,
production behavior exercised, and cleanup evidence under `## Database Test Environment`.
It includes a database-object matrix showing exactly which functions, procedures, sequences,
tables, views, triggers, and native queries were exercised with synthetic data; functions and
procedures are listed explicitly even when the count is zero.
The fixture table appears under `### Synthetic Test Data` without a separate status legend; cleanup
status remains visible on each row.

The **Synthetic Test Data** table reports, per schema-qualified table:

- Fixture-load count
- Exact rows created
- Peak marker-owned rows present
- Exact safe rows reused
- Rows cleaned or rolled back
- Rows remaining after final cleanup
- Cleanup status

The **Behavior Exercised** table maps those fixtures to the integration test/scenario, production
service/repository/DAO entry point, PostgreSQL objects, behavior exercised, and meaningful assertions
or observed outcomes.

Statuses are `✅ CLEANED`, `✅ ROLLED BACK`, `♻️ REUSED`, `⚠️ ACTION NEEDED`, and
`➖ NONE CREATED`. Counts come from database/test evidence, not fixture-file lines or SQL-statement
counts. Reports include markers and counts but never synthetic or customer row values.

### Validation Results

The conversion report's `## Validation Results` table always uses:

| Stage / gate | Scope / evidence | Result | Readiness impact |
| --- | --- | --- | --- |

Every row contains an emoji and text label:

| Status | Meaning |
| --- | --- |
| ✅ Passed | Every required check for the gate passed. |
| ⚠️ Passed with warnings | Required checks passed, but an optional skip or bounded warning remains. |
| ❌ Failed | A required check failed. |
| ⏭️ Skipped | The gate was intentionally not run; the reason and required/optional classification are shown. |
| ➖ Not available | An environment or tool prerequisite prevented execution. |

All customer-facing Status and Result columns use one emoji plus plain title-case text. Labels are
never bold or rendered in all caps; canonical uppercase codes are reserved for machine state.

Mixed outcomes are never shown as fully passed. For example, PostgreSQL Docker tests passing while
an external OAuth test is skipped is `⚠️ Passed with warnings`.

### Exhaustive Changes Inventory

The conversion report lists every working-tree path individually under modified tracked files, new
source/test files, deleted or renamed files when present, and generated migration artifacts. Each
row includes its exact repository-relative path, Git change kind, conversion category, and result.
The inventory is reconciled against final Git status and cannot use grouped phrases such as
"three tests" or "assessment artifacts" as a substitute for exact paths.

### Remaining Work & Exceptions

The conversion report separates remaining work into Required Before Production, Recommended, and No
Action Required tables. Each actionable row identifies evidence/impact, the exact next action, and
the prerequisite or owner.

No Action Required explicitly names completed database/provider/SQL areas so customers know which
items are closed.

### Coverage Convergence

Coverage applies to every instrumented production path regardless of whether a unit test,
integration test, or application-level test executed it. The skill records three views:

| View | Meaning |
| --- | --- |
| Framework/toolchain | Production code exercised by each applicable unit/characterization adapter or environment, including JUnit, .NET, Python, and C/C++ frameworks. |
| Integration | Production code exercised by Docker, application-to-PostgreSQL, and other integration suites. |
| Combined | Unique production code exercised by either lane, calculated from merged native execution data rather than by adding percentages. |

No numeric coverage target is displayed or used as a report verdict. When test generation is
enabled, the skill runs at least two unit/characterization waves and independently at least two
integration waves, never exceeding ten in either lane. Further waves require a specific worthwhile
uncovered behavior, not repeated unchanged tests. It works toward an aspirational 80% combined
line coverage level while
reporting branch coverage separately. Critical paths still require direct assertions through the
production PostgreSQL call path; the aspiration never replaces behavioral evidence or becomes an
Objective column.

Reports show baseline and final raw counters, percentages, and percentage-point deltas separately
for unit, integration, and combined line/branch coverage. They also show full-scope versus
changed/converted scope, coverage-wave progress, the exact stop reason, and every remaining gap.
A report never displays a numeric target/objective column or collapses line and branch coverage
into one percentage.

### Reusable Test Setup

Related test scenarios run together, not as separate Maven/Java/Docker startups. The primary retains
one ownership-verified Docker PostgreSQL environment for all test processes within a migration run,
injects its endpoint privately, isolates marker-owned fixtures per scenario, and verifies cleanup
at run end. Containers are not retained across separate skill invocations.

The Maven runner retains the normal local repository and reuses verified same-run dependency and
compiled-output preparation. Source/build changes invalidate only the appropriate generation.
An unrestricted final validation wave can also satisfy the complete-suite gate, without another
identical launch. Optional waves need concrete expected additional line/branch coverage and a
value-based reason; two waves are a default, not proof that coverage is mathematically maximal.

With `finalize=false`, collect test/coverage evidence before deciding on further work. Reuse the
returned `preparationProof` as `preparedBuild`; when inputs, test configuration, and outputs remain
unchanged, `finalizeOnly=true` performs the final report/package checks without rerunning tests.
Per-command timing and separate generation, compatibility-repair, readiness, target-check, and
reporting timings identify overhead that test batching alone cannot remove.

### Code-Backed Generation and Reporting Controls

The primary runtime is initialized once after active scope is confirmed. `java-preflight` captures
effective module compiler settings and the runtime JDK, caches unchanged build evidence, and
rejects incompatible draft syntax before Maven compilation. It does not guess a newer language
release or claim to replace the semantic compiler.

Reviewed source findings and code-derived target-object manifests use content/directory/context
fingerprints. Unchanged payloads are reused; changed or corrupt evidence is rejected or invalidated.
Fresh target-write safety checks are never replaced by a cache.

Only the registered primary may hold the workspace execution lease. The Maven runner uses that
lease for preparation and test/Docker work, rather than allowing each child agent to recreate setup.
During conversion, append changed state/section data to a durable checkpoint journal. At reporting
boundaries, the assembler reconstructs full machine state and exact template-based reports once,
then independently validates owner/journal/assembly provenance.

See [the runtime coordinator reference](./skills/migrate-oracle-app/references/runtime-coordinator.md)
for CLI requests, cache rules, recovery, and phase/final publication.

Existing JUnit 3/4 tests may continue through JUnit Vintage when required by the application. New
Java tests use JUnit Jupiter when the build supports it; Vintage is a compatibility engine, not a
coverage tool or a separate coverage category.

### Connection Context Outcomes

| Supplied context | Behavior |
| --- | --- |
| Profile + database | Resolves the exact saved profile, verifies and connects to the exact database, then validates server/database/schema context. |
| Profile + database + schema | Uses and verifies all three exact values. |
| Only profile or only database | Derives the missing value only when MCP metadata proves one unique choice; otherwise asks for it. |
| No profile/database | Assessment completes code-only with `ACTION NEEDED`. Conversion may continue independent source work but cannot claim target validation, use `READY`, or create persistent DDL. |
| Context differs on resume | Invalidates cached target metadata, DB diagram edges, generated DDL proof, write authorizations, and dependent units; preserves unchanged code/build/test evidence. |
| Read-only profile | Supports assessment and read-only target validation. Persistent DDL requires an explicitly selected write-enabled profile. |

`postgres_mcp_query` always uses a read-only inspection transaction. Its
`transaction_read_only=on` result is not evidence that the saved profile or server is read-only;
write capability is derived from profile `ro`/`rw` mode, `postgres_mcp_modify` availability,
`default_transaction_read_only`, privileges, role settings, and recovery state.

The skill persists only a one-way profile-reference digest plus sanitized database, schema,
capabilities, access mode, and contract digest. It never persists passwords, hosts, usernames,
connection strings, or internal profile IDs.

### Bounded PostgreSQL Discovery

The skill never downloads an entire target schema for context. It first builds an exact manifest
from active code—schema, object, kind, referenced columns, routine signatures, sequences, and direct
dependencies—then:

1. Probes exact names for object kind and OID in batches of at most 20 names.
2. Retrieves required contract detail for at most 10 returned OIDs per query.
3. Expands only direct dependencies needed for SQL behavior, Docker bootstrap, generated keys, or
   DDL ordering.
4. Caches per-object results and refreshes only drifted evidence.

`postgres_mcp_db_context`, `pgsql_db_context`, and every current/future tool ending in `_db_context`
are prohibited for all migration operations, including one-object inspection and connection
verification. Tool availability is not permission. Use list-database/server-capability tools plus
bounded scalar, exact-name, and OID-based `postgres_mcp_query` catalog probes. Broad
`information_schema` scans, catalog `SELECT *`, and unrelated row/count/size retrieval are also
prohibited. After a timeout, the skill never repeats the same request: it records the failed batch,
splits or reduces it to identity-only probes, and defers only the affected object if one narrower
attempt still fails. A timeout is never treated as proof that an object is absent.

The static scope detector emits phase/count progress to stderr and reserves stdout for its JSON
result. It excludes dependency/build/output directories at every depth, uses a linear
comment-aware embedded-SQL scanner, and stops with review-required evidence when its 50,000-file,
100 MB text, or 30-second scan budget is exhausted.

[Back to Top](#top)

<a id="examples"></a>
## Examples

Pass the saved PostgreSQL MCP profile and exact database as invocation context:

```text
/migrate-oracle-app mode=assess codebase=. postgres-connection=my-profile database=my_database schema=app
```

Enable shims, persistent missing-object creation, and all test support:

```text
/migrate-oracle-app mode=convert codebase=C:\src\oracle-app oracle-ddl-folder="C:\dev\example-migrations\Migration Projects" postgres-connection=my-profile database=my_database schema=CWF options=create-shims,create-persistent-ddl,tests
```

When `oracle-ddl-folder` is supplied, the skill first resolves
`.github\postgres-migrations\<user-provided-name>\artifacts\oracle\<schema>` or a flexible schema
folder containing one or more object-type directories. It traverses whatever type directories are
present rather than requiring a fixed list, matches only code-derived target objects that are
actually missing, translates each uniquely matched object one-off, and updates its application call
sites to the resulting PostgreSQL contract. It does not bulk-convert the folder. When the folder is
absent or an object is missing/ambiguous, the skill
reconstructs the object best-effort from code, mappings, tests, DTOs, dependencies, and observed
side effects; generates deploy/undo DDL; updates calls; and runs synthetic Docker validation. If no
body can be inferred, the generated body fails closed with an explicit error. The report marks it
as generated, identifies missing/unproven body semantics, and requires customer validation.

With `create-persistent-ddl` enabled, reconstruction and Docker validation are followed by actual
creation in the selected PostgreSQL database through the selected provider's modify operation.
Generating SQL files or creating objects only in Docker does not satisfy the option. The skill
immediately verifies each schema-qualified object and its contract using the matching query
operation. A matching object already present at the fresh target check is verified without being
replaced. If write access, privileges, Docker proof, or another safety prerequisite is unavailable,
the report records `Not executed`, the exact reason and next action, and a `PARTIAL` result.
This deployment does not require `test-target-writes` or any test-generation option.

Also run bounded authoritative target runtime tests through an explicitly selected write profile:

```text
/migrate-oracle-app mode=convert codebase=C:\src\oracle-app postgres-connection=my-write-profile database=my_database schema=app options=create-shims,create-persistent-ddl,tests,test-target-writes
```

Enable only generated unit tests and broken-test repair:

```text
/migrate-oracle-app mode=convert codebase=. postgres-connection=my-profile database=my_database options=create-unit-tests,fix-broken-tests
```

Natural language is equivalent: "Convert using profile my-profile and database my_database. Enable
dependency shims and all test support, but do not create persistent database objects."

If multiple plugins provide a skill with the same name, select the plugin-qualified entry shown by
the client.

[Back to Top](#top)

<a id="test-contracts"></a>
<a id="local-tests"></a>
## Test the Plugin Contracts

The migration contract suite uses Node.js's built-in test runner and has no npm
test dependencies. Run from the `plugin/` directory (or use `npm --prefix plugin test`
from the repository root):

```powershell
npm test
```

Archive-packaging tests require a working JDK with `jar` on `PATH`. On macOS,
use a real, non-symlinked `TMPDIR` if DDL tests reject the default temporary path.
The existing repository pytest suite remains separate and unchanged.

Validate generated migration artifacts against the exact report/evidence contract:

```powershell
npm run validate:artifacts -- C:\path\to\codebase both
```

The contract suite covers option resolution, read-only profile rejection, privilege failure, marker
isolation, cleanup failure, sequence recording, target drift, successful target runtime validation,
and required instruction propagation.

[Back to Top](#top)

<a id="versioning"></a>
## Versioning

The `version` field in `plugin.json` is the single source of truth.
The current plugin version is `0.3.0`.
Do not add a nonstandard version field to `SKILL.md`.

Use Semantic Versioning:

- Increment **patch** for compatible fixes and instruction improvements.
- Increment **minor** for new backward-compatible migration capabilities.
- Increment **major** for incompatible workflow, artifact, or behavior changes.

For release maintenance, follow the repository's `update-plugin-release` skill. Keep
the plugin host manifests, `package.json`, and listed plugin version aligned; leave
marketplace `metadata.version` independent. Record plugin changes in
[the plugin changelog](CHANGELOG.md) and listing changes in
[the marketplace changelog](../CHANGELOG.md). Publishing commits or tags requires
explicit approval. Installed users can then run `copilot plugin update postgres-skills`.

Check the current source version in PowerShell:

```powershell
(Get-Content .\plugin.json -Raw | ConvertFrom-Json).version
```

[Back to Top](#top)

<a id="update-remove"></a>
## Update or Remove

For a plugin installed from Git:

```shell
copilot plugin update postgres-skills
copilot plugin uninstall postgres-skills
```

A local plugin loaded with `--plugin-dir` is not installed. Stop the session to unload it; start a
new session with the same flag to load current local files.

In VS Code, manage the plugin from the **Agent Plugins - Installed** section of the Extensions view
or from the Chat gear menu under **Plugins**.

[Back to Top](#top)

<a id="safety"></a>
## Safety

Assessment is always database-read-only and tracked-source read-only by default. Exact
option-gated baseline-enablement edits require approval in standalone `assess`, and standalone
`convert` requires source-edit approval. `assess-and-convert` is one-shot: its invocation authorizes
both reversible source stages, so recorded previews do not pause the run. Explicit
`create-persistent-ddl` authorizes validated create-only batches, while `test-target-writes`
separately authorizes Docker-proven, marker-owned target runtime tests and recorded active-sequence
increments. Every other persistent PostgreSQL write requires a separate explicit request and
just-in-time confirmation. Secrets and connection strings must never be placed in prompts, source,
fixtures, or reports.

[Back to Top](#top)

<a id="complete-setup"></a>
## Complete End-to-End Environment Setup

Follow one complete path below before starting a migration. Commands use PowerShell where Windows
needs a concrete example; use the equivalent package manager and shell on macOS or Linux.

### A. Install Common Prerequisites

#### Windows

Install Git, VS Code, Node.js, and Docker Desktop with `winget`, or use the official installers:

```powershell
winget install --id Git.Git -e
winget install --id Microsoft.VisualStudioCode -e
winget install --id OpenJS.NodeJS.LTS -e
winget install --id Docker.DockerDesktop -e
```

Close and reopen terminals after installation, start Docker Desktop manually, and verify:

```powershell
git --version
node --version
npm --version
npx --version
docker version
docker compose version
code --version
```

Node.js must report version 22 or newer. Docker must show a responding client and server.

#### macOS

Install current Git, Node.js 22 or newer, VS Code, and Docker Desktop through their official
installers or Homebrew:

```shell
brew install git node
brew install --cask visual-studio-code docker
```

Start Docker Desktop and run the same version checks.

#### Linux

Install Git, VS Code, Node.js 22 or newer, Docker Engine, and the Docker Compose plugin using the
official distribution/vendor instructions. Distribution repositories may contain an older Node.js;
verify the actual version rather than assuming the package is sufficient.

After Docker installation:

```shell
docker version
docker compose version
```

If Docker requires elevated privileges, follow the official Docker post-installation guidance.
Do not weaken machine security solely to avoid an appropriate `sudo` or group policy.

### B. Prepare the Migration Codebase

The application directory must already be a Git repository:

```powershell
Set-Location C:\path\to\application
git rev-parse --show-toplevel
git symbolic-ref --quiet --short HEAD
git rev-parse HEAD
git status --short --branch --untracked-files=all
```

Requirements:

- The resolved Git root equals the `codebase` argument.
- The repository has at least one commit.
- The branch is attached rather than detached.
- Review existing working-tree changes before conversion; the skill preserves rather than discards
  them.

### C. Set Up a Complete Java Development Environment

Skip this section only when the codebase does not contain Java/Maven modules.

#### 1. Determine the Required Java Version

Inspect, in this order:

1. `.sdkmanrc`, `.java-version`, or Maven Toolchains configuration.
2. `pom.xml` properties such as `maven.compiler.release`,
   `maven.compiler.source`, and `maven.compiler.target`.
3. Maven Compiler Plugin and framework requirements.
4. Repository build documentation or CI configuration.

Install that major version. Java 17 is a common baseline and is used in the examples below, but do
not change an application's source/target release merely to match the example.

#### 2. Install a JDK

Windows example using Eclipse Temurin 17:

```powershell
winget install --id EclipseAdoptium.Temurin.17.JDK -e
```

macOS examples:

```shell
brew install --cask temurin@17
# Or use the project's required JDK distribution/version.
```

Ubuntu/Debian example:

```shell
sudo apt update
sudo apt install openjdk-17-jdk
```

Open a new terminal and verify that both the runtime and compiler exist:

```shell
java -version
javac -version
```

If multiple JDKs are installed, set `JAVA_HOME` to the selected JDK and place
`$JAVA_HOME/bin` before other Java installations on `PATH`. Confirm the active Java home with:

```shell
java -XshowSettings:properties -version
```

Review the `java.home` line.

#### 3. Configure Maven

Prefer the repository wrapper when present:

```powershell
.\mvnw.cmd -version
```

```shell
./mvnw -version
```

If the repository has no wrapper, install Maven 3.9 or the version required by the project:

```shell
# macOS
brew install maven

# Ubuntu/Debian
sudo apt install maven
```

On Windows, install Maven from the Apache Maven binary distribution or an approved package manager,
set `MAVEN_HOME`, and add `%MAVEN_HOME%\bin` to `PATH`.

Verify Maven uses the intended Java installation:

```shell
mvn -version
```

The Java version and Java home shown by Maven must match the selected project JDK.

#### 4. Configure Maven Repositories and Certificates

Before migration:

- Configure corporate mirrors/proxies in the user Maven `settings.xml`.
- Import required corporate certificate authorities using approved enterprise procedures.
- Confirm access to Maven Central or the repository's internal artifact manager.
- Never commit usernames, passwords, access tokens, or private repository credentials.

Warm the dependency graph with the repository's normal command, for example:

```powershell
.\mvnw.cmd -B -DskipTests dependency:go-offline
```

or:

```shell
mvn -B -DskipTests dependency:go-offline
```

#### 5. Configure Java Support in VS Code

Install Microsoft's **Extension Pack for Java**. It includes Java language support, debugging,
testing, Maven integration, and project management.

Open the application repository and wait for Java project import to complete. Resolve Java/Maven
diagnostics before starting the migration unless they are the baseline failures the skill is
expected to assess.

Run a basic untouched baseline:

```powershell
.\mvnw.cmd -B test
```

or:

```shell
./mvnw -B test
```

Record any failure; do not edit tests merely to manufacture a green baseline.

### D. Complete VS Code Copilot Setup — Option 1

1. Install and sign in to GitHub Copilot in VS Code.
2. Install Microsoft's **PostgreSQL** extension for VS Code.
3. Open the migration codebase and trust the workspace.
4. Configure and save the target PostgreSQL connection through the extension. Use a
   least-privilege read-only profile for assessment. Configure a separate approved write-enabled
   profile only when persistent DDL or target-write tests are requested.
5. Confirm the extension exposes these Chat tools:

   ```text
   pgsql_list_connection_profiles
   pgsql_connect
   pgsql_list_databases
   pgsql_get_server_capabilities
   pgsql_query
   pgsql_modify
   pgsql_disconnect
   ```

6. Enable Agent Plugins:

   ```json
   "chat.plugins.enabled": true
   ```

7. Install the plugin with **Chat: Install Plugin From Source**, or configure the local checkout
   through `chat.pluginLocations`.
8. Verify the plugin under `@agentPlugins`.
9. Verify the skill through **Chat: Configure Skills**.
10. Start a new Agent session and select **Copilot** as the Session Target. Do not select
    **Local** for this supported path.
11. Confirm Node.js and Docker from the VS Code integrated terminal:

    ```powershell
    node --version
    npx --version
    docker version
    ```

12. Invoke the skill, for example:

    ```text
    /migrate-oracle-app mode=assess-and-convert codebase=C:\path\to\application postgres-connection=my-vscode-profile database=app_lab schema=cwf options=tests
    ```

13. Review generated assessment/conversion reports and Git diffs in VS Code.

If the `pgsql_*` tools are missing, repair the PostgreSQL extension. Do not use the plugin-bundled
NPM MCP server as a fallback in VS Code Copilot.

### E. Complete Copilot CLI Setup — Option 2

1. Install Node.js 22+, Git, Docker, and the application language/build tools.
2. Install and authenticate Copilot CLI using the current official GitHub instructions.
3. Create the NPM MCP saved profile:

   ```powershell
   npx -y @microsoft/postgres-mcp@latest connection add my-cli-profile "host=db.example.com user=agent_user dbname=app sslmode=verify-full" --access-mode ro
   npx -y @microsoft/postgres-mcp@latest connection set-password my-cli-profile
   npx -y @microsoft/postgres-mcp@latest connection list
   ```

4. Install the plugin from Git:

   ```shell
   copilot plugin marketplace add microsoft/postgres-skills
   copilot plugin install postgres-skills@postgres-skills
   ```

   For local development, start it from the plugin checkout:

   ```powershell
   copilot --plugin-dir .
   ```

5. Verify:

   ```shell
   copilot plugin list
   copilot skill list
   copilot mcp list
   ```

6. Change to the application Git root before starting the migration.
7. Invoke the skill with the CLI profile:

   ```text
   /migrate-oracle-app mode=assess-and-convert codebase=C:\path\to\application postgres-connection=my-cli-profile database=app_lab schema=cwf options=tests
   ```

8. If Copilot CLI is connected to VS Code, remember that the CLI still owns the session and uses
   `postgres-mcp`, not the VS Code extension.
9. Review generated reports and the final Git working tree.

If `postgres-mcp` cannot start, verify Node.js, `npx`, npm/network access, and
`.mcp.json`/`mcp.json`. Do not fall back to VS Code `pgsql_*` tools.

[Back to Top](#top)