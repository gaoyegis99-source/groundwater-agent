"""Run Linux PEST against the fixed MODFLOW forward model."""

import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .packages import MAX_DEFINITION, extract_sealed, seal, write_json
from .results import HEAD, _active, _frames, _valid
from .scenarios import MULTIPLIERS
from .science import simulate


def _number(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _configuration(source: Path, definition: dict) -> dict:
    if source.stat().st_size > MAX_DEFINITION:
        raise ValueError("CALIBRATION_SIZE_EXCEEDED")
    config = json.loads(source.read_bytes())
    if (
        not isinstance(config, dict)
        or set(config) != {"schemaVersion", "parameters", "observations", "maxIterations"}
        or config["schemaVersion"] != "porous-pest-head.v1"
        or type(config["maxIterations"]) is not int or not 1 <= config["maxIterations"] <= 12
    ):
        raise ValueError("CALIBRATION_SCHEMA_INVALID")
    parameters = config["parameters"]
    observations = config["observations"]
    if (
        not isinstance(parameters, list) or not 1 <= len(parameters) <= 6
        or not isinstance(observations, list) or not len(parameters) <= len(observations) <= 1000
    ):
        raise ValueError("CALIBRATION_COUNT_INVALID")
    used = set()
    for i, parameter in enumerate(parameters, start=1):
        if not isinstance(parameter, dict) or set(parameter) != {"modifier", "initial", "lower", "upper"}:
            raise ValueError("CALIBRATION_PARAMETER_INVALID")
        modifier, lo, start, hi = (parameter[field] for field in ("modifier", "lower", "initial", "upper"))
        if (
            not isinstance(modifier, str) or modifier not in MULTIPLIERS or modifier in used
            or not all(map(_number, (lo, start, hi))) or not 0.0001 <= lo <= start <= hi <= 1000
            or lo == hi
        ):
            raise ValueError("CALIBRATION_PARAMETER_INVALID")
        used.add(modifier)
        parameter["name"] = f"p{i}"
    grid = definition["grid"]
    for i, observation in enumerate(observations, start=1):
        if not isinstance(observation, dict) or set(observation) != {
            "time", "layer", "row", "column", "head", "weight",
        }:
            raise ValueError("CALIBRATION_OBSERVATION_INVALID")
        if (
            not all(_number(observation[field]) for field in ("time", "head", "weight"))
            or observation["time"] <= 0 or not 0 < observation["weight"] <= 10000
            or not all(type(observation[field]) is int and 1 <= observation[field] <= grid[dimension]
                       for field, dimension in (("layer", "layers"), ("row", "rows"), ("column", "columns")))
        ):
            raise ValueError("CALIBRATION_OBSERVATION_INVALID")
        observation["name"] = f"o{i}"
    return config


def _scenario(parameters: list[dict], values: dict[str, float]) -> dict:
    return {
        "scenarioId": "pest-calibration", "name": "PEST head calibration",
        "modifiers": {item["modifier"]: values[item["name"]] for item in parameters},
    }


def _write_case(job: Path, config: dict) -> None:
    parameters, observations = config["parameters"], config["observations"]
    (job / "params.tpl").write_text(
        "ptf ~\n" + "".join(f"{p['name']} ~ {p['name']:<14} ~\n" for p in parameters)
    )
    (job / "params.dat").write_text(
        "".join(f"{p['name']} {p['initial']:.12g}\n" for p in parameters)
    )
    (job / "head.ins").write_text(
        "pif @\n" + "".join(f"l1 w !{o['name']}!\n" for o in observations)
    )
    # Use the original application's PEST settings with a Linux Python command.
    lines = [
        "pcf", "* control data", "norestart estimation",
        f"{len(parameters)} {len(observations)} 1 0 1",
        "1 1 single point 1 0 0", "10.0 2.0 0.3 0.03 10", "3.0 3.0 0.001", "0.1",
        f"{config['maxIterations']} 0.005 4 4 0.005 4", "1 1 1",
        "* parameter groups", "flow relative 0.05 0.001 switch 2.0 parabolic",
        "* parameter data",
        *(f"{p['name']} none factor {p['initial']:.12g} {p['lower']:.12g} {p['upper']:.12g} "
          "flow 1.0 0.0 1" for p in parameters),
        "* observation groups", "head", "* observation data",
        *(f"{o['name']} {o['head']:.12g} {o['weight']:.12g} head" for o in observations),
        "* model command line", f"{sys.executable} -B -m porous_remote.calibration",
        "* model input/output", "params.tpl params.dat", "head.ins head.out",
    ]
    (job / "case.pst").write_text("\n".join(lines) + "\n", encoding="ascii")


def _forward(config: dict, values: dict[str, float], job: Path) -> list[float]:
    source, vendor, keyfile = (Path(config[name]) for name in ("source", "vendor", "keyfile"))
    sys.path.insert(0, str(vendor / "src"))
    key = keyfile.read_bytes()
    work = job / "forward"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir()
    scenario = _scenario(config["parameters"], values)
    simulate(source, work, vendor, key, scenario=scenario, flow_only=True)
    project = work / "project"
    definition = json.loads((project / "model_definition.json").read_bytes())
    grid = definition["grid"]
    dims = grid["layers"], grid["rows"], grid["columns"]
    active = _active(definition, project)
    no_flow = float(definition["properties"].get("noFlowHead", -999.99))
    observations = config["observations"]
    requested = {(float(o["time"]), o["layer"]) for o in observations}
    frames = {
        (time, layer): cells for time, layer, cells in _frames(work / "rawResults/model.hds", HEAD, dims)
        if (time, layer) in requested
    }
    if frames.keys() != requested:
        raise ValueError("CALIBRATION_TIME_OR_LAYER_UNAVAILABLE")
    predictions = []
    for observation in observations:
        layer = observation["layer"]
        index = (observation["row"] - 1) * dims[2] + observation["column"] - 1
        prediction = frames[(observation["time"], layer)][index]
        if not _valid(prediction, active[layer - 1][index], "head", no_flow):
            raise ValueError("CALIBRATION_OBSERVATION_CELL_INACTIVE")
        predictions.append(prediction)
    return predictions


def calibrate(source: Path, observations: Path, job: Path, vendor: Path, keyfile: Path) -> dict:
    from gw_platform.pest import _parse_parameter_file

    key = keyfile.read_bytes()
    baseline = job / "baseline"
    extract_sealed(source, baseline, "model", key)
    if (baseline / "calibration-scenario.json").exists():
        raise ValueError("CALIBRATION_BASELINE_REQUIRED")
    config = _configuration(observations, json.loads((baseline / "model_definition.json").read_bytes()))
    config.update({"source": str(source), "vendor": str(vendor), "keyfile": str(keyfile)})
    write_json(job / "forward-config.json", config)
    _write_case(job, config)
    with (job / "pest.log").open("wb") as log:
        subprocess.run(
            [str(vendor / "bin/pest"), "case"], cwd=job,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
            stdout=log, stderr=subprocess.STDOUT, check=True,
        )
    values = {row["name"]: row["value"] for row in _parse_parameter_file(job / "case.par")}
    if any(
        p["name"] not in values or not p["lower"] <= values[p["name"]] <= p["upper"]
        for p in config["parameters"]
    ):
        raise ValueError("CALIBRATION_RESULT_INVALID")
    scenario = _scenario(config["parameters"], values)
    predictions = _forward(config, values, job)
    residuals = [
        {"observed": observation["head"], "simulated": prediction,
         "weight": observation["weight"], "residual": observation["head"] - prediction,
         "time": observation["time"], "layer": observation["layer"],
         "row": observation["row"], "column": observation["column"]}
        for observation, prediction in zip(config["observations"], predictions, strict=True)
    ]
    diagnostics = {
        "objective": math.fsum((row["residual"] * row["weight"]) ** 2 for row in residuals),
        "observations": len(residuals),
        "rootMeanSquareResidual": math.sqrt(math.fsum(row["residual"] ** 2 for row in residuals) / len(residuals)),
    }
    write_json(job / "residuals.json", {"diagnostics": diagnostics, "rows": residuals})
    write_json(baseline / "calibration-scenario.json", scenario)
    seal(job / "calibrated_model_package.zip", "model", {
        path.relative_to(baseline).as_posix(): path for path in baseline.rglob("*") if path.is_file()
    }, key)
    seal(job / "calibration_package.zip", "calibration", {
        name: job / name for name in ("case.pst", "case.par", "case.rec", "residuals.json")
    }, key)
    return {"calibrated_parameters": scenario["modifiers"], "residual_diagnostics": diagnostics}


def main() -> None:
    job = Path.cwd()
    config = json.loads((job / "forward-config.json").read_bytes())
    values = {
        name: float(value) for name, value in
        (line.split() for line in (job / "params.dat").read_text().splitlines())
    }
    predictions = _forward(config, values, job)
    (job / "head.out").write_text(
        "".join(f"{observation['name']} {prediction:.12g}\n"
                for observation, prediction in zip(config["observations"], predictions, strict=True))
    )


if __name__ == "__main__":
    main()
