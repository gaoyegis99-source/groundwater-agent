"""Verify CPU diffusion training, reproducible sampling and native agricultural inversion."""

import argparse
from io import BytesIO
import json
from pathlib import Path
import secrets
import shutil
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile

import numpy as np

from porous_remote.agriculture import _modified, _run_heads, invert_agriculture
from porous_remote.inversion import sample, train
from porous_remote.packages import project_definition, write_json


def run(fixture: Path, vendor: Path, root: Path) -> dict:
    sys.path.insert(0, str(vendor / "src"))
    key = secrets.token_bytes(48)
    rng = np.random.default_rng(42)
    perm = rng.uniform(0.02, 0.1, size=(4, 5, 64, 64)).astype(np.float32)
    disp = rng.uniform(-0.1, -0.001, size=(4, 65, 65)).astype(np.float32)
    head = rng.uniform(-1, -0.01, size=(4, 50, 2)).astype(np.float32)
    with ZipFile(root / "training.zip", "w") as archive:
        for name, array in (("perm", perm), ("disp", disp), ("head", head)):
            memory = BytesIO()
            np.save(memory, array, allow_pickle=False)
            archive.writestr(name + ".npy", memory.getvalue())
        archive.writestr("training.json", json.dumps({
            "schemaVersion": "porous-inversion-training.v1", "seed": 42,
            "epochs": 1, "batchSize": 1, "learningRate": 0.0002,
            "model": {"baseChannels": 32, "conditionDimension": 32,
                      "timeEmbeddingDimension": 32, "diffusionSteps": 8},
        }))
    for name in ("train", "sample", "sample-repeat", "agriculture"):
        (root / name).mkdir()
    training = train(root / "training.zip", root / "train", key)["metrics"]
    assert training["device"] == "cpu" and 0 < training["bestValidationLoss"] < 10
    sample_config = root / "sampling.json"
    write_json(sample_config, {
        "schemaVersion": "porous-inversion-sampling.v1", "seed": 42,
        "samples": 2, "steps": 4, "guidance": 2.0,
        "disp": disp[-1].tolist(), "head": head[-1].tolist(), "referencePerm": perm[-1].tolist(),
    })
    weights = root / "train/weights.binary"
    sampled = sample(weights, sample_config, root / "sample", key)["metrics"]
    sample(weights, sample_config, root / "sample-repeat", key)
    posterior = np.load(root / "sample/posterior_samples.npy", allow_pickle=False)
    assert posterior.shape == (2, 5, 64, 64) and np.isfinite(posterior).all()
    assert 0 < sampled["meanPosteriorStd"] < 10 and 0 < sampled["rmse"] < 10
    assert np.array_equal(posterior, np.load(root / "sample-repeat/posterior_samples.npy", allow_pickle=False))
    definition = project_definition(fixture)
    monitors = [{"id": "W1", "layer": 1, "row": 16, "column": 35}]
    records = [{"time": time} for time in range(300, 2401, 300)]
    truth = _run_heads(
        _modified(definition, {"W1-R1": [0]}, [1.6]),
        records, monitors, root / "truth", vendor, key,
    )
    agriculture_input = root / "agricultural.zip"
    with ZipFile(agriculture_input, "w") as archive:
        archive.writestr("model_definition.json", json.dumps(definition))
        archive.writestr("agricultural.json", json.dumps({
            "schemaVersion": "porous-agricultural-response.v1", "agriculturalWells": [1],
            "monitors": monitors, "calibrationCount": 6,
            "observations": [{**record, "head": heads.tolist()}
                             for record, heads in zip(records, truth, strict=True)],
        }))
    agricultural = invert_agriculture(agriculture_input, root / "agriculture", vendor, key)["metrics"]
    assert agricultural["shape"] == [3, 8, 1]
    assert 0 <= agricultural["validationOptimizedRmseM"] < agricultural["validationBaselineRmseM"]
    return {"training": training, "sampling": sampled, "agricultural": agricultural}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--vendor", type=Path, default=Path("/app/vendor"))
    parser.add_argument("--fixtures", type=Path)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="porous-inversion-") as directory:
        root = Path(directory)
        result = run(args.fixture, args.vendor, root)
        if args.fixtures is not None:
            for name in ("training.zip", "sampling.json", "agricultural.zip"):
                shutil.copyfile(root / name, args.fixtures / name)
        print(json.dumps(result, ensure_ascii=False))
