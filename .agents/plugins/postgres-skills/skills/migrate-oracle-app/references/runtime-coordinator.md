# Deterministic Migration Runtime

Use `scripts/migration-runtime.mjs` after `MIGRATION_REQUIRED` scope is established. It owns the
expensive-work lease, verified manifest caches, append-only recovery checkpoints, and final artifact
assembly. Do not substitute repeated prose/state rewrites for these operations.

## Primary Ownership

Initialize once with a private JSON request containing `runId` and `actorId: "primary"`:

```text
node "<skill-directory>\scripts\migration-runtime.mjs" init "<codebase>" "<request.json>"
```

The default private runtime lives beneath the worktree's Git metadata, not in tracked report folders.
Do not initialize a repository, change Git configuration/index/branches, or put credentials in
requests. A session-workspace `runtimeDirectory` can be explicitly supplied when appropriate.

All mutation/execution APIs require the registered primary and an exclusive workspace execution
lease. The Maven runner holds that lease across preparation, Docker endpoint checks, tests,
coverage, and finalization. A child actor or concurrent setup fails explicitly rather than creating
another environment. Children return source changes and bounded evidence/section fragments to the
primary; they do not invoke unmanaged Maven/Docker setup or write canonical reports/state.

This is a cooperative workflow execution guard, not an OS sandbox: do not bypass the controlled
helpers with direct child shell commands. Never steal a live lease or kill unrelated processes.

## Java Pre-Generation Gate

Before preparing any Java test source, run:

```text
node "<skill-directory>\scripts\migration-runtime.mjs" java-preflight "<codebase>" "<request.json>"
```

The request supplies actual `mavenExecutable`, `mavenArgs`/profiles and `javaExecutable` from project
evidence. The helper discovers module POMs, Maven/JDK configuration inputs, fingerprints their
contents and environment, and captures one effective-POM/runtime-JDK snapshot on a cache miss.
Unchanged inputs reuse the verified snapshot, not another Maven initialization.

The result contains per-module main/test release levels and permitted syntax. Generate to those
levels, not the newest installed JDK. Unresolved/ambiguous settings or an older runtime JDK require
action; never guess a modern release.

Pass draft `files` to the same gate before staging/compiling them. It rejects unsupported text
blocks, `var`, records, sealed types, and switch expressions outside comments/ordinary strings.
The normal project compiler remains the semantic/API compatibility authority. A lexical gate does
not claim complete Java parsing. The Maven runner repeats the gate against current test sources,
so ignoring the generation guidance cannot silently produce a passing build.

In-repository `inputs` can include additional build roots. The shared context fingerprinter hashes
explicit Maven POM/settings/toolchain flags, default settings/toolchains, JDK release/executable
contents, and environment, including external files without exposing their paths or contents.
Provide an `externalInputDigest` for additional externally selected parent/corporate policy inputs
not discoverable from those controls. Refresh on build/profile/JDK evidence changes.

## Source and Target-Object Manifest Reuse

Use `cache-get` before expensive semantic analysis, and `cache-put` only after a reviewed manifest
is complete. Requests contain `kind`, `key`, `inputs`, and, for target objects, `contextDigest`.
Carry the lookup's `inputDigest` as `expectedInputDigest` into `cache-put`; changed inputs reject
stale analysis instead of publishing it as current.

```text
node "<skill-directory>\scripts\migration-runtime.mjs" cache-get "<codebase>" "<request.json>"
node "<skill-directory>\scripts\migration-runtime.mjs" cache-put "<codebase>" "<request.json>"
```

- `source-findings`: exhaustive source findings/dependency/ownership models, keyed by all relevant
  source/build roots and coding rules. Directory fingerprints detect new/deleted files as well as
  byte changes; mtime alone is never proof.
- `target-objects`: the code-derived object-name/kind/expected-contract manifest, bound to the
  freshly verified selected-provider/database/schema context digest.
- `java-release`: the automatically managed pre-generation evidence.

Hits return the existing verified payload. Misses state why inputs/context changed. Tampered
entries, path escapes, symbolic links, unsupported kinds, and scan-budget exhaustion fail explicitly.
Do not use stale entries or rebuild an unchanged manifest because another wave started.

Target-manifest reuse does not authorize caching target rows, privileges, locks, recovery state, or
write-safety decisions. Actual target contracts still follow bounded MCP refresh/invalidation rules;
every authorized write receives its required fresh safety checks.

## Bounded Checkpoints Instead of Full Rewrites

After each completed/deferred unit, issue one `checkpoint` request:

```json
{
  "runId": "<run-id>",
  "actorId": "primary",
  "phase": "conversion",
  "operations": [
    { "op": "append", "path": ["coverage", "unitWaves"], "values": ["<actual wave evidence>"] }
  ],
  "sections": {
    "validation-results": "<updated body of this section only>"
  }
}
```

Use actual typed evidence, not placeholder strings. Operations are `set`, `append`, or `delete`
against safe object paths. Supply only changed values/sections; never regenerate the entire
conversion state or report for a wave. Header bindings use exact template tokens such as
`"<run-id>": "actual-run-id"`.

The helper validates and fsyncs a hash-chained JSONL journal. `replay` reconstructs the exact latest
state/sections for resume without publishing partial canonical artifacts. Missing/corrupt/incomplete
records are explicit recovery failures, not success-shaped defaults. Credentials, raw source
snapshots, connection strings, and unsafe object keys are rejected.

## Phase and Final Reporting Boundaries

At assessment completion or final conversion completion, issue `finalize` with explicit `phases`,
exact template paths, and previously validated `expectedArtifacts` SHA-256 values for files being
replaced. An unrelated/user-edited artifact must not be overwritten.

The assembler requires every canonical top-level section, preserves template title/anchors/ToC/
ordering/navigation, and inserts Run Metrics centrally from precise state. It reconstructs the
complete JSON state once, stages outputs, writes an assembly receipt, then runs artifact validation
with `--require-runtime --record` and independent verification.

Persist the deterministic `codingNotes` ingestion result and per-note dispositions in phase
checkpoints under [coding-notes.md](./coding-notes.md), including `not-supplied`. The assembler
re-reads the private input locator, rejects changed notes/stale inventory, and generates
**Coding Notes Processing** centrally with native task-list checkboxes. Do not checkpoint a
hand-written notes section or equate checked with implemented/runtime-validated. Source/target
manifest input digests automatically include the checkpointed notes snapshot; re-ingest/checkpoint
the current invocation input before requesting derived findings. Java release caches are independent.
Lookup rejects missing notes checkpoints, including omitted input: checkpoint `not-supplied`
explicitly before using a source/target cache, so older notes-agnostic entries cannot be reused.

For combined runs, the assessment phase has its own publication boundary for conversion input.
At the final combined boundary, synchronize final timing into both checkpoints and finalize both
phases together. This intentional second assessment snapshot is not a per-wave rewrite.

Runtime proof is checked against the real primary owner, journal, checkpoint state, assembly
receipt and report hash. Artifact-validation bookkeeping is the only allowed post-assembly state
mutation. Never change source evidence, report prose, or timing after final verification.

Static no-conversion/scope-review exits keep their existing early-stop report/validation path; do
not initialize execution, run a Java gate, or generate/cache build evidence on those branches.
