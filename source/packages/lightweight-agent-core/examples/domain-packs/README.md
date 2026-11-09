# Declarative domain-pack examples

These two `v6-domain-pack-v1` examples demonstrate that one V6 runtime can
assemble unrelated business domains without adding a domain branch to the
engine:

- `groundwater`: a checked local measurement followed by human approval and a
  calculation.
- `csv-statistics`: a real, standard-library capability wheel that validates a
  staged CSV and selected numeric column, pauses for approval, computes
  statistics, and publishes an immutable JSON result artifact.

The domain declarations remain credential-free and declarative. They contain a
versioned workflow, exact `ToolReference` values, tool permission and recovery
metadata, a dependency lock, pinned method/test assets, and a required
`ValidationReportV1` gate. `groundwater` deliberately retains placeholder tool
identities. `csv-statistics/capability` is the separately built and installed
PyPA distribution; its entry points locate code but do not grant execution
permission.

From this package directory, check or compile either source package with the
optional authoring dependency:

```bash
uv run --all-extras platform-agent-workflow domain check examples/domain-packs/groundwater
uv run --all-extras platform-agent-workflow domain compile \
  examples/domain-packs/csv-statistics /tmp/csv-project \
  --parameter-profile small
```

Compilation produces the existing `v6-agent-project-v1` format plus a frozen
`domain-lock.json`; it does not execute or import a tool. A host must still
install the CSV wheel, bind its three exact release deliveries, provide a
`FileWorkspace`, and grant `files.read` / `files.write`. The CSV profile is
selected explicitly at compile time and materialized into ordinary v4 node
bindings. Bundle v2 transports the definition and lock, not executable code;
the target environment binds the published wheel again.
