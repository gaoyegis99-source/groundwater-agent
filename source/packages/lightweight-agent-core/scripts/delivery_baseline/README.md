# V6 delivery verification

This isolated gate builds a wheel from a fresh explicit source copy,
installs only `[workflow]` with `uv`, and never imports the development tree.
It exercises the installed CLI, a real local tool execution, and a full local
recovery handoff followed by approval and tool execution. It records install
size, cold import, CLI startup, actual execution/recovery latency, RSS, source
tree identity, wheel SHA-256, and lock-file SHA-256. It establishes observed
evidence; it deliberately does not invent release thresholds.

```bash
python scripts/delivery_baseline/measure.py \
  --samples 5 \
  --output /tmp/v6-delivery-baseline.json
```

With `--archive-artifacts /new/absolute/directory`, the same clean wheel also
produces two immutable run fixtures: one waiting on a stable approval identity
and one whose tool action is already complete. The archive records wheel, lock,
source and golden hashes under `v6-compatibility-baseline-v1`, then runs:

```bash
python scripts/delivery_baseline/verify_compatibility.py \
  --baseline /immutable/baseline \
  --output /tmp/compatibility-verification.json \
  --matrix-output /tmp/compatibility-matrix.json \
  --consumer-source-sha256 sha256:... \
  --consumer-wheel /path/to/the-installed-consumer.whl
```

The verifier uses the public recovery and journal APIs, completes the pending
approval, and installs a fail-if-called executor while reading the completed
run. It associates the supplied wheel with the package actually imported by
this process by checking wheel and installed files against the wheel's standard
`RECORD`. The producing wheel reports `BASELINE_ESTABLISHED`; this explicitly
makes no historical cross-version claim. A verified different wheel may report
`COMPATIBLE_CANDIDATE`. Missing or unassociated consumer identity reports
`IDENTITY_UNKNOWN`, exits nonzero, and never creates a historical claim. Driver
continuation remains a separate, exact-driver-binding policy and is not covered
by these two fixtures.

Generated reports are written only to the requested temporary or external
output path. They are not source inputs and are never checked into this tree.
No model, credential, external service, or network request is used after
installation.
