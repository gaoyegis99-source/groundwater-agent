# FloPy 3.12.0 / MODFLOW 6.8.1 independent ADV-02 spike

This directory owns a dependency and numerical spike only. It does not add
FloPy, MODFLOW 6, or `modflowapi` to the root, Native, QGIS, catalog, model,
workflow, UI, or default deployment dependency domains.

The frozen case is a synthetic, uncalibrated, steady confined model. Three
identical rows and eleven columns produce a one-dimensional analytical head
field while retaining a valid two-dimensional GeoTIFF for the existing
`raster.contour@1.0.0` handoff. Its UTM coordinates and hydraulic parameters
are benchmark fixtures, not site assumptions or a real prediction.

## Supply-chain boundary

Only these three locked USGS-hosted inputs are accepted:

- `mf6.8.1_linux.zip` from the `code.usgs.gov` release asset recorded in
  `mf6-binary.lock.json`;
- `LICENSE.md` at tag `6.8.1` from the official USGS repository API;
- `DISCLAIMER.md` at tag `6.8.1` from the official USGS repository API.

The build script verifies size, SHA-256, `code.json`, ELF64 x86-64 identity,
license, and disclaimer before creating a temporary build context. The Docker
build has no network and the Dockerfile contains no package-manager or remote
download step. No MF6 binary or release archive is committed to this repository.

## Locked FloPy environment

From `source/`:

```bash
export UV_PROJECT_ENVIRONMENT=/tmp/groundwater-modflow6-spike-venv
uv sync --project products/groundwater/spikes/modflow6 --frozen --group test
uv run --project products/groundwater/spikes/modflow6 --frozen --group test \
  python -m pytest -q products/groundwater/tests/test_modflow6_spike.py
```

`uv.lock` fixes the complete Python 3.12 resolution. `requirements.lock`
contains the runtime-only artifact hashes. No FloPy optional extra is selected.

## Build the isolated MF6 image

After obtaining the three official files outside the source tree:

```bash
uv run --project products/groundwater/spikes/modflow6 --frozen \
  python products/groundwater/scripts/build_modflow6_spike_image.py \
  --archive /absolute/path/mf6.8.1_linux.zip \
  --license /absolute/path/LICENSE.md \
  --disclaimer /absolute/path/DISCLAIMER.md \
  --tag groundwater-mf6-spike:6.8.1
```

The image uses a pinned local Ubuntu base and contains only the verified `mf6`
executable plus USGS release/license evidence. `libmf6.so` is verified but not
included because this executable spike does not use `modflowapi`.

## Run and verify the analytical case

The output path must be a new absolute directory outside `source/`:

```bash
uv run --project products/groundwater/spikes/modflow6 --frozen \
  python products/groundwater/scripts/run_modflow6_spike.py \
  --config products/groundwater/spikes/modflow6/steady-linear.config.json \
  --image groundwater-mf6-spike:6.8.1 \
  --output /tmp/groundwater-mf6-steady-linear
```

The runner rechecks the image architecture, fixed labels, and `mf6 -v`; runs
with no network, a read-only root filesystem, dropped capabilities, one CPU,
and 512 MiB RAM; and atomically publishes model inputs, binary heads, cell
budget, listings, stdout, CSV heads, GeoTIFF heads, contour handoff, hashes,
metrics, convergence, acceptance states, and limitations.

For a fully exercised test using official release inputs and the built image:

```bash
export GROUNDWATER_MF6_681_ARCHIVE=/absolute/path/mf6.8.1_linux.zip
export GROUNDWATER_MF6_681_LICENSE=/absolute/path/LICENSE.md
export GROUNDWATER_MF6_681_DISCLAIMER=/absolute/path/DISCLAIMER.md
export GROUNDWATER_MF6_SPIKE_IMAGE=groundwater-mf6-spike:6.8.1
uv run --project products/groundwater/spikes/modflow6 --frozen --group test \
  python -m pytest -q products/groundwater/tests/test_modflow6_spike.py
```

A passing result validates only the fixed analytical case. Platform Compose,
snapshot restoration, archive rebuild, provider behavior, calibration,
uncertainty, transient packages, WEL/RCH/OBS, PRT, and PEST++ remain outside
this spike and must not be inferred from it.
