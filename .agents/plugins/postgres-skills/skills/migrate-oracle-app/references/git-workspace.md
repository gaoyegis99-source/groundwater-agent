# Codebase Git Workspace

Assessment and conversion use one `codebase` folder. It is both the codebase being assessed and the
workspace edited during conversion. Never create or require a second codebase target folder.

## Required Preflight

Before assessment or conversion:

1. Resolve `codebase` to an existing directory.
2. Run Git discovery and require its canonical worktree root to equal the resolved codebase path.
3. Require at least one reachable `HEAD` commit and record its full hash.
4. Require an attached branch for conversion or tracked assessment baseline enablement and record
   its name.
5. Record `git status --porcelain=v1 --untracked-files=all` and relevant file digests.

If any requirement fails, stop before the selected mode starts and state the exact user action. Do
not run `git init`, create/switch branches, stage, commit, stash, reset, restore, clean, or push.

Assessment may inspect a dirty repository but must record that its baseline is not reproducible and
must not make tracked baseline-enablement edits. Baseline enablement requires clean codebase-owned
paths before its preview. Conversion requires clean codebase-owned paths except exact
assessment-owned baseline-enablement changes whose paths, before/after digests, authorization
source, commands, and run ID validate against current `assessment-data.json`. Current-run artifacts under
`.github/migrate-oracle-app/` may also remain when their persisted run ID and digests validate them.
Any other dirty or ambiguous path requires user cleanup before mutable assessment or conversion.

## In-Place Source Changes

- Write authorized baseline-enablement changes, converted production code, tests, shims, migration
  SQL, and reports inside the codebase repository using its existing layout.
- Keep changes unstaged and uncommitted for customer review. Existing index state must not change.
- Before each bounded unit, capture the digests of files it may edit.
- After each unit, record modified/deleted paths from Git diff and untracked paths from Git status,
  along with the validating commands and result.
- If a file changed outside the skill since its captured digest, do not overwrite or revert it.
  Defer that unit, report the conflict, and continue independent units.
- Roll back an unsuccessful unit only through precise edits owned by that unit. Never use broad Git
  restore/reset/checkout/clean operations.

## Coverage Git Ignore Rules

Only change the codebase root `.gitignore` when source edits are already authorized and static scope
is `MIGRATION_REQUIRED`:

- In standalone `convert`, include coverage ignore housekeeping in the conversion preview and apply
  it after source-edit approval.
- In `assess-and-convert`, apply it at the authorized conversion edit stage without another prompt,
  or within a previewed assessment baseline-enablement batch.
- In standalone `assess`, include `.gitignore` in the exact baseline-enablement preview and edit it
  only after that batch is approved.
- Read-only assessment must not create or modify `.gitignore`; record the recommended rules as
  unapplied instead. `NO_CONVERSION_REQUIRED` and `SCOPE_REVIEW_REQUIRED` never authorize this edit.

Capture the existing file digest or absence, then append only missing rules, creating `.gitignore`
only when absent:

```gitignore
/.github/migrate-oracle-app/assessment/coverage/
/.github/migrate-oracle-app/conversions/coverage/
```

Preserve existing content and line endings; repeated runs must not duplicate rules. Never add a
blanket coverage rule or ignore the entire migration artifact tree: reports, state, diffs, and DDL
keep their existing Git behavior. If explicit unignore rules conflict or the file changed outside
the skill, preserve user intent, defer the ignore update, report the exact conflict, and continue
independent work.

Verify the effective rules for each generated coverage artifact with `git check-ignore --no-index`;
do not assume that the presence of the two lines proves they are effective. Record the `.gitignore`
change and its before/after digests in the authorized batch and final file inventory.

Ignore rules do not untrack already committed coverage. Report any already tracked coverage as a
manual follow-up; never run `git rm --cached`, change the index, or delete coverage to reduce Git
noise. Retain current local coverage for comparison and artifact validation. Existing
ownership-validated rerun replacement and stale-data isolation still apply.

## Final Git Evidence

The assessment report lists any baseline-enablement files and their authorization source
(standalone confirmation or `assess-and-convert` invocation) separately from semantic conversion.
The conversion report appendix includes repository root, starting branch and commit, final status,
diff summary, deferred conflicts, and an exhaustive file inventory. List every modified, added,
deleted, renamed, and generated codebase path individually with its Git kind, category/unit, and
result. Reconcile the path set and counts against final porcelain status, tracked diff name/status,
and untracked-file output after report artifacts exist. Do not replace exact paths with grouped
phrases. New files remain untracked until the user reviews and stages them. The skill never claims
that Git history contains the conversion; it reports that all changes are visible in the working
tree for review.

Ignored local coverage belongs in coverage evidence, not the Git-reconciled Changes inventory.
An authorized `.gitignore` edit remains a normal modified or added path in that inventory.

Conversion artifacts use the stable `.github/migrate-oracle-app/conversions/` path. On rerun,
validate prior run ownership and digests before replacing skill-owned files. Timestamped conversion
subdirectories are not valid current output and should be flattened or removed only when they are
proven skill-owned; never remove an unrelated user directory.