"""Exercise scenario comparison, native PEST and GeoTIFF with synthetic inputs."""

import argparse
import json
import math
from pathlib import Path
import secrets
import shutil
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile

import numpy as np
import rasterio
from rasterio.transform import from_origin

from porous_remote.calibration import calibrate
from porous_remote.maps import prepare_map
from porous_remote.packages import project_definition, write_json
from porous_remote.results import HEAD, _frames
from porous_remote.scenarios import compare_scenarios
from porous_remote.science import compile_definition, simulate


def run(fixture: Path, vendor: Path, root: Path) -> dict:
    sys.path.insert(0, str(vendor / "src"))
    keyfile = root / "key"
    keyfile.write_bytes(secrets.token_bytes(48))
    key = keyfile.read_bytes()
    definition = project_definition(fixture)
    target = 1.6
    scenario = {
        "scenarioId": "increased-pumping", "name": "Increased pumping",
        "modifiers": {"wellMultiplier": target},
    }
    with ZipFile(root / "scenario.zip", "w") as archive:
        archive.writestr("model_definition.json", json.dumps(definition))
        archive.writestr("scenarios.json", json.dumps({
            "schemaVersion": "porous-scenarios.v1", "scenarios": [scenario],
        }))
    for name in ("scenarios", "compile", "truth", "pest", "recalculate", "map"):
        (root / name).mkdir()
    compare_scenarios(root / "scenario.zip", root / "scenarios", vendor, key)
    baseline, altered = json.loads((root / "scenarios/scenario_table.json").read_bytes())
    assert altered["meanFinalHead"] < baseline["meanFinalHead"]
    compile_definition(definition, root / "compile", vendor, key)
    model = root / "compile/model_package.zip"
    simulate(model, root / "truth", vendor, key, scenario=scenario, flow_only=True)
    dims = tuple(definition["grid"][field] for field in ("layers", "rows", "columns"))
    _, _, values = list(_frames(root / "truth/rawResults/model.hds", HEAD, dims))[-1]
    observations = [
        {"time": 2400, "layer": 1, "row": row, "column": column,
         "head": values[(row - 1) * dims[2] + column - 1], "weight": 100}
        for row, column in ((16, 35), (15, 35), (16, 34))
    ]
    write_json(root / "observations.json", {
        "schemaVersion": "porous-pest-head.v1",
        "parameters": [{"modifier": "wellMultiplier", "initial": 1.0, "lower": 0.5, "upper": 2.0}],
        "observations": observations, "maxIterations": 8,
    })
    try:
        pest = calibrate(model, root / "observations.json", root / "pest", vendor, keyfile)
    except Exception:
        for name in ("pest.log", "case.rec", "forward/logs/worker.log"):
            log = root / "pest" / name
            if log.is_file():
                print(log.read_text(errors="replace")[-4000:], file=sys.stderr)
        raise
    assert math.isclose(pest["calibrated_parameters"]["wellMultiplier"], target, rel_tol=1e-3), pest
    assert pest["residual_diagnostics"]["objective"] <= 1e-4, pest
    recalculated = simulate(root / "pest/calibrated_model_package.zip", root / "recalculate", vendor, key)
    assert recalculated["effective_parameters"]["appliedScenario"]["modifiers"] == pest["calibrated_parameters"]
    values = np.arange(16, dtype=np.float32).reshape((4, 4))
    with rasterio.open(
        root / "original.tiff", "w", driver="GTiff", height=4, width=4, count=1, dtype="float32",
        crs="EPSG:4326", transform=from_origin(112, 36, 0.01, 0.01), nodata=-9999.0,
    ) as raster:
        raster.write(values, 1)
    metadata = prepare_map(root / "original.tiff", root / "map")["crs_attribution_metadata"]
    with rasterio.open(root / "map/geospatial_output.geotiff") as raster:
        assert np.array_equal(raster.read(1), values)
        assert raster.crs.to_epsg() == metadata["epsg"] == 4326
    return {"scenario": {"meanFinalHeadChange": altered["meanFinalHeadChange"]},
            "pest": pest, "map": metadata}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--vendor", type=Path, default=Path("/app/vendor"))
    parser.add_argument("--fixtures", type=Path)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="porous-science-") as directory:
        root = Path(directory)
        result = run(args.fixture, args.vendor, root)
        if args.fixtures is not None:
            for name in ("scenario.zip", "observations.json", "original.tiff"):
                shutil.copyfile(root / name, args.fixtures / name)
        print(json.dumps(result, ensure_ascii=False))
