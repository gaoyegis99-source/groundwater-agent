# Platform Operator Pack

`platform-operator-pack==2.0.0` is a static capability package, not a network
service. It contains exactly 106 immutable operator manifests (80 `native`, 15
`qgis`, 11 `remote_http`), their smoke cases and fixtures, the implementation lock, pure
implementations, a checked-in identity-to-function mapping, and the restricted
subprocess entrypoint used by Executor Workers.

It deliberately contains no API, administration plane, database, registry,
upload flow, outbox, message broker, object store, or independent cache. The
Executor Control owns scheduling and durable state; Workers only import this
package and execute a statically configured operator identity.

Artifact ports declare media type and format pairs. Conditional `formatCases`
describe the exact output pair for each input-format branch of `crs.reproject`;
the Assembler propagates those pairs through the DAG, and Control checks actual
Worker output against the compiled pair. `data.convert` streams Parquet and
Arrow IPC File batches without changing their Arrow schema, nulls or metadata.
JSON conversion retains native JSON numbers, booleans and nulls and rejects
values it cannot encode losslessly, including integers outside the interoperable
53-bit exact range and duplicate field names. CSV is textual: it has no column type or
null-versus-empty-string distinction.

Data-backed Worker inputs may include a checkpointed `dataContract` projection.
The monitoring time-series operators use its IANA timezone for calendar periods
and its declared null sentinels for numeric eligibility. Offset timestamps are
converted into that named calendar zone rather than UTC-bucketed; ambiguous
naive local timestamps fail closed. Groups retained by the input but containing
no numeric observations produce explicit `NOT_EVALUABLE` results instead of
failing the complete workflow.

Run the static checks with:

```bash
pytest -q packages/operator-pack/tests
ruff check packages/operator-pack
```

The native and QGIS smoke suites are executed in their corresponding pinned
Worker images so each manifest is verified against the system dependencies of
its declared runtime.

## Historical implementation identity assessment

The following native/QGIS assessment was recorded at `e669e54`, before the 11
fixed remote-operation manifests were added, using `catalog/implementation-lock.json` and
`platform/assembler/assembler_domain.py`; no lock or publication contract is changed
by this assessment. The 95 entries list 56 distinct source files. Each entry lists
10–19 files; nine files occur in all 95 entries, including `native.py`, the static
mapping, runtime task handling and shared helpers. Another seven files occur in all
80 native entries. Today Assembler hashes each entry's declared file list, then
combines that digest with its manifest to check `implementationRevision`. A change
to any of the nine common files therefore requires refreshing all 95 identities,
even if only one operator's behavior changes.

| Option | Identity spread | Cost and constraint |
| --- | --- | --- |
| Hash the complete package source for every operator | Every source change changes all 95 identities | Simple, conservative validation; larger publication and downstream pin churn. |
| Keep the current declared file lists | Shared-file changes still spread widely | No new dependency analysis; lists can overstate or omit actual runtime dependencies. |
| Hash each operator's verified transitive source dependencies | A local implementation change can affect only its consumers | Requires a complete, deterministic graph for imports, dynamic dispatch, static mapping and runtime files; unknown edges must conservatively include the shared scope. |

Recommendation: retain the current lock until independent operator changes cause
material pin churn. Then prototype a dependency graph and compare its affected set
against import tracing, Worker image contents and the 95 native/QGIS smoke cases. An identity
change must introduce a new versioned lock format and update Assembler drift checks,
catalog/model pins, Executor publication checks and release acceptance evidence
together. Existing published revisions must remain resolvable; a smaller digest
cannot be retroactively substituted for them.

`cartography.render-png@1.0.0` renders one local GeoJSON or GeoTIFF/COG file
to a 1024 × 768 PNG with coordinate axes and a legend/colorbar. `title` is
optional; `valueField` optionally colors GeoJSON features by a finite numeric
property. Raster rendering uses the first band and excludes nodata. Rotated
grids and rasters without CRS are rejected. The operator does not reproject,
fetch basemaps, publish services, or create map projects. PNG follows ordinary
Executor artifact publication.
