# Pastas 2.0.1 independent spike

This directory owns a dependency and numerical experiment for ADV-01. It does
not publish a Groundwater model, workflow, Operator, UI entry, or default runtime
dependency.

The frozen experiment accepts one daily CSV with `time`, `head`,
`precipitation`, and `evaporation`. The configuration makes every current
scientific choice explicit: the observation is hydraulic head; observation gaps
are dropped; stress gaps, missing daily steps, negative precipitation and negative
evaporation are rejected; precipitation and evaporation must use the same unit;
recharge is linear; the response is exponential; there is no pure-delay parameter;
warmup is 365 days; and calibration precedes a chronological validation holdout.

Create the independent environment and run its tests:

```bash
export UV_PROJECT_ENVIRONMENT=/tmp/groundwater-pastas-spike-venv
uv sync --project products/groundwater/spikes/pastas --frozen --group test
uv run --project products/groundwater/spikes/pastas --frozen --group test \
  python -m pytest -q products/groundwater/tests/test_pastas_spike.py
```

Run an input outside the source tree into a new output directory:

```bash
uv run --project products/groundwater/spikes/pastas --frozen \
  python products/groundwater/scripts/run_pastas_spike.py \
  --input /absolute/path/single-well-daily.csv \
  --config /absolute/path/reviewed-user-input.config.json \
  --output /absolute/new/output-directory
```

Copy `user-input.example.config.json` outside the source tree and review its
columns, units, periods, split identity, and assumptions before using that command.
`synthetic-benchmark.config.json` is reserved for the deterministic test fixture;
using it for field data would record false provenance.

The result contains observed/simulated/residual series, fitted parameters,
calibration and validation metrics, convergence/Jacobian diagnostics, exact
input/config/artifact checksums, and a V12 research-acceptance assessment. The
assessment deliberately leaves domain review, field validation, and GRU comparison
as `NOT_ASSESSED`. Pastas and GRU results may be compared only after the metric
contract, chronological split, evaluation mode, and both calibration- and
validation-row checksums match.

Pastas 2.0.1 emitted five upstream Pandas 4 compatibility warnings during the
locked synthetic test on 2026-11-10. They do not change the observed numerical
result, but remain recorded as dependency-upgrade risk rather than being suppressed.
