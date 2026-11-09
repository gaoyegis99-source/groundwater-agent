"""Reproduce the 14 new smoke artifacts in independent Worker subprocesses."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "operator-pack" / "src"))

from moperators.catalog import OperatorCatalog  # noqa: E402
from moperators.smoke import bundled_catalog_directory  # noqa: E402

IDS = (
    "table.timeseries_unpivot",
    "timeseries.chronological_split",
    "preprocessing.standard_scaler_fit",
    "preprocessing.standard_scaler_transform",
    "preprocessing.standard_scaler_inverse",
    "timeseries.supervised_windows",
    "statistics.regression_metrics",
    "ml.gru_attention_pretrain",
    "ml.gru_attention_finetune",
    "ml.gru_attention_predict",
    "vector.from_xy",
    "vector.tag_membership",
    "vector.envelope",
    "raster.window",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=bundled_catalog_directory())
    args = parser.parse_args()
    import torch

    assert torch.__version__ == "2.7.1+cpu"
    catalog = OperatorCatalog.load(args.catalog)
    report = {"torch": torch.__version__, "catalogDigest": catalog.digest, "operators": {}}
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for identity in IDS:
            smoke = catalog.smoke_cases[(identity, "1.0.0")]
            runs = []
            for iteration in range(2):
                directory = root / identity / str(iteration)
                directory.mkdir(parents=True)
                inputs = {}
                for port, ref in smoke.inputs.items():
                    fixture = catalog.fixtures[ref.fixture_id]
                    path = directory / port
                    shutil.copyfile(catalog.fixture_root / fixture.path, path)
                    inputs[port] = [
                        {
                            "path": str(path),
                            "mediaType": fixture.media_type,
                            "format": fixture.format,
                            "metadata": {},
                        }
                    ]
                payload = directory / "request.json"
                result = directory / "result.json"
                payload.write_text(
                    json.dumps(
                        {
                            "operatorId": identity,
                            "operatorVersion": "1.0.0",
                            "inputs": inputs,
                            "parameters": smoke.parameters,
                            "outputDirectory": str(directory / "outputs"),
                        }
                    )
                )
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "moperators.runtime.task",
                        str(payload),
                        str(result),
                    ],
                    check=True,
                    timeout=120,
                    env={
                        **os.environ,
                        "OMP_NUM_THREADS": "1",
                        "OPENBLAS_NUM_THREADS": "1",
                        "MKL_NUM_THREADS": "1",
                    },
                )
                body = json.loads(result.read_text())
                assert "error" not in body, body
                runs.append(
                    {
                        port: hashlib.sha256(Path(a["path"]).read_bytes()).hexdigest()
                        for port, a in body["outputs"].items()
                    }
                )
            assert runs[0] == runs[1], identity
            report["operators"][identity] = runs[0]
    report["status"] = "PASS"
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
