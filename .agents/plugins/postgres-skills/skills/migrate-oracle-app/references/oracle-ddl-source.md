# Extracted Oracle DDL Source

`oracle-ddl-folder=<path>` supplies a read-only migration-project or ancestor root from which the
skill resolves authoritative Oracle object definitions for missing PostgreSQL object conversion.
The folder is optional. A unique matching source definition is the preferred evidence, but an
absent folder or unmatched object does not stop best-effort reconstruction.

The folder is evidence, not an output location. Never edit, rename, delete, format, or copy source
DDL files into the codebase.

## Folder Contract

- The supplied path resolves to an existing regular directory outside or inside the codebase. It
  need not be the schema directory itself.
- Prefer this canonical shape beneath the supplied root, where `<migration-name>` is an arbitrary
  end-user-provided directory name:

  ```text
  .github/postgres-migrations/<migration-name>/artifacts/oracle/<SCHEMA>/
  |-- FUNCTION/
  |-- INDEX/
  |-- PACKAGE/
  |-- PROCEDURE/
  |-- SCHEMA/
  |-- SEQUENCE/
  |-- TABLE/
  |-- TRIGGER/
  `-- VIEW/
  ```

- The object-type folders shown above are an example, not a required set. A migration may contain
  fewer folders, additional folders, or differently named object types.
- For flexible layouts, recursively locate the selected schema directory at any bounded depth. The
  bare minimum is that the schema directory directly contains at least one non-hidden object-type
  directory. The supplied path may therefore be the migration-project root, an arbitrary ancestor,
  the Oracle-artifacts root, or the schema directory itself.
- Use the invocation's resolved `schema` value to select the schema folder case-insensitively. When
  more than one matching flexible schema folder exists and no unique canonical folder exists, stop
  with an ambiguity instead of merging them.
- Treat every immediate non-hidden directory beneath the selected schema as an object-type
  directory and traverse it recursively. Ignore unrelated files elsewhere under the supplied
  migration-project root.
- Known folder names such as `TABLE`, `PROCEDURE`, and `VIEW` enable folder-versus-DDL kind
  validation. Unknown or new folder names remain valid: infer the primary kind from parsed DDL or,
  when the syntax is unsupported, index the schema/folder/filename identity with an explicit
  warning so the model can inspect that source file.
- Every file has exactly one primary object whose kind matches its containing object-type
  directory. A `TABLE` file may also contain bundled supporting indexes/constraints and other
  dependent statements; these are recorded as auxiliary definitions, not treated as extra primary
  files.
- One package specification and body may coexist in one file or appear in two companion files
  because they are components of one package identity. Duplicate specifications or duplicate bodies
  are invalid.
- Separate files with the same normalized schema/kind/object identity are ambiguous and invalid,
  except the package specification/body companion pair.
- Supported extensions are `.sql`, `.ddl`, `.pls`, `.plsql`, `.pks`, `.pkb`, `.pck`, `.spc`,
  `.bdy`, `.prc`, `.fnc`, `.trg`, `.vw`, `.tps`, `.tpb`, and `.plb`.
- Symbolic links, non-regular entries, unsupported file extensions inside an object-type directory,
  primary schema mismatches, known-folder kind mismatches, and files defining multiple primary
  objects make the source index invalid.
- A file is limited to 10 MB; the folder is limited to 10,000 files and 250 MB.

Before target object conversion, run:

```text
node "<skill-directory>\scripts\index-oracle-ddl.mjs" "<oracle-ddl-folder>" "<codebase>\.github\migrate-oracle-app\assessment\oracle-ddl-index.json" --schema "<schema>"
```

The index persists the supplied root, resolved schema path, layout kind, all object-type
directories actually discovered, the arbitrary migration name for canonical layouts, schema
candidates, relative file paths, normalized primary identities, identity source, auxiliary
definitions, kinds, package-member names, sizes, hashes, warnings, and errors. It never persists
Oracle DDL bodies. An invalid index is `ACTION NEEDED`; do not select files by filename guess or use
ambiguous content as source evidence. Treat affected source DDL as unavailable and continue through
the best-effort reconstruction protocol.

## Matching Missing PostgreSQL Objects

Start from the code-derived PostgreSQL missing-object manifest. For each missing object:

1. Resolve the original Oracle identity from the active call site, mapping, SQL, or assessment
   finding: schema, standalone object or package, member name, object kind, argument count/order,
   Oracle datatypes, return/out shape, and source paths.
2. Match the normalized Oracle schema/object kind/name to exactly one indexed DDL file. For a
   package member, match the package file and confirm the member appears in its package-member
   inventory.
3. Open only that matched DDL file. Read direct dependency files only after the body references an
   exact dependency and only through their own unique index entries.
4. Reconcile the DDL declaration with the application call signature. A name-only match is
   insufficient when overloads exist. Argument direction, order, types, defaults, result shape, and
   transaction behavior must agree or be explicitly mapped.
5. Record the source relative path and SHA-256 digest. Never place the complete Oracle body in
   reports, state, prompts to external systems, or generated diff evidence.

No unique match activates the best-effort reconstruction protocol below. Never combine fragments
from multiple plausible files or claim that a signature proves the original body.

## One-Off Conversion

Convert only code-derived objects that are missing from the selected PostgreSQL target. Do not bulk
translate every file in `oracle-ddl-folder`.

For each uniquely matched object:

1. Translate Oracle declarations and body semantics to PostgreSQL, including datatype mappings,
   package-member flattening/naming, exception behavior, cursor/result contracts, sequence use,
   dynamic SQL, transaction assumptions, and directly referenced dependencies.
2. Produce ordered deploy and exact undo DDL inside the codebase migration layout. Preserve the
   Oracle source file unchanged.
3. Update every selected application call site to the resulting PostgreSQL schema-qualified
   procedure/function identity and correct JDBC/.NET/Python/native invocation form. Do not update a
   call site before the translated target contract is fixed.
4. Create characterization tests from source DDL branches and observed application expectations.
   Validate deploy, invocation through the production driver path, side effects, errors, cleanup,
   undo, zero-object verification, and redeploy in Docker.
5. Reconcile generated PostgreSQL DDL, updated call sites, Docker evidence, and the source DDL hash
   in assessment/conversion state and reports.
6. When `create-persistent-ddl` is explicitly enabled and every normal authorization check passes,
   execute the translated plain `CREATE` statements in the selected PostgreSQL database through
   the selected provider's modify operation, then immediately verify through its query operation.
   Generating deploy/undo files or deploying only in Docker is not completion.

## Best-Effort Reconstruction When Source DDL Is Unavailable

When `oracle-ddl-folder` is not supplied or no unique source object matches:

1. Reconstruct the Oracle-facing contract from every active call site: schema/package/member name,
   object kind, parameter direction/order/type, return or cursor shape, transaction handling,
   expected exceptions, consumed results, and observed side effects.
2. Gather behavioral evidence from repository SQL fragments, DAOs/repositories, ORM mappings,
   entities/DTOs, callers, assertions, mocks, fixtures, migration notes, target table constraints,
   and directly referenced objects. Do not rely on the object name or signature alone when stronger
   evidence exists.
3. Produce a PostgreSQL implementation using the strongest consistent behavior supported by that
   evidence. Record every inferred branch, default, query, mutation, exception, and omitted behavior.
4. If no executable business body can be inferred, create an explicit fail-closed implementation
   with the required PostgreSQL signature that raises a descriptive exception stating that the
   original Oracle body was unavailable. Do not create a silent `NULL`/no-op,
   unconditional-success, fabricated mutation, or fabricated return-value body.
5. Generate deploy and undo DDL and update application call sites to the reconstructed PostgreSQL
   contract. Label the output `best-effort-reconstruction` or `signature-only-fail-closed`, not
   source-converted.
6. Create representative synthetic input values and the smallest coherent marker-owned row graph
   needed by the inferred behavior. Invoke the production driver path in Docker; assert outputs or
   the expected fail-closed error, row/transaction side effects, cleanup, undo, zero-object
   verification, and redeploy.
7. When `create-persistent-ddl` is enabled and normal write checks pass, create the generated object
   in the selected PostgreSQL database through the selected provider's modify operation and
   immediately verify its identity and contract with the matching query operation. Do not stop at
   reconstruction or Docker-only deployment, and do not require `test-target-writes`. Report it as
   generated/inferred or fail-closed—not as behaviorally equivalent—and preserve exact undo DDL.
8. Require customer/database-owner validation before production readiness. The conversion remains
   `PARTIAL` while any generated body has unproven business semantics.

Best-effort means useful and testable, not falsely authoritative. Never claim that synthetic tests
prove omitted Oracle business logic.

## Required Evidence

Persist:

- Whether `oracle-ddl-folder` was supplied; the input root; selected schema; arbitrary migration
  name; canonical/flexible layout; dynamically discovered schema/type-directory paths; candidate
  schema paths; warnings; and folder digest
- Index command/status, limits, file count, object count, errors, relative paths, identities, kinds,
  package members, sizes, and file SHA-256 values
- Every missing target object and its exact source match or fallback reconstruction provenance
- Match method, signature reconciliation, source-body availability, body completeness, semantic
  confidence, evidence, assumptions, and limitations
- Generated deploy/undo paths and SHA-256 hashes
- Updated application call sites
- Docker behavior/cleanup evidence
- Whether persistent creation was requested, attempted, and verified; per-object authorization,
  exact deployed DDL hash, selected modify/query operations, timestamps, target verification, or
  the precise reason and next action when creation could not execute
- Synthetic inputs/fixture rows, assertions, outputs/errors, side effects, cleanup, and limitations
- Whether customer validation is required and the exact validation action

Changing the folder, any source file digest, target identity, missing-object manifest, translated
DDL, or application call contract invalidates the affected conversion and write authorization.
