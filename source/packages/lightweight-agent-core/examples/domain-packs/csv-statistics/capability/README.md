# CSV statistics capability wheel

This is the executable counterpart to the declarative `csv-statistics` domain
pack. It uses only the Python standard library and exposes three PyPA entry
points. Discovery locates code; the V6 target must still bind each exact wheel
digest and grant `files.read` / `files.write`.

The validation and calculation tools resolve only `FileReference` values via
the host-provided `ToolContext.files`. Calculation verifies the PASS receipt
against the current file, column and frozen parameter profile before creating
an action-isolated, immutable result artifact.

Build reproducibly from this directory, then regenerate the frozen domain
identities from the package root:

```bash
uv lock
uv export --format pylock.toml --locked --no-dev --no-emit-project \
  --output-file pylock.toml
SOURCE_DATE_EPOCH=0 uv build --wheel --out-dir ../artifacts
cd ..
PYTHONPATH=../../../src uv run --project ../../.. --all-extras \
  python generate_release.py \
  --wheel artifacts/v6_csv_statistics_capability-1.0.0-py3-none-any.whl
```

The generator emits one tool-specific sidecar for each exact reference. The
legacy single-tool `tool-manifest.json` protocol remains available to older
tool packages.
