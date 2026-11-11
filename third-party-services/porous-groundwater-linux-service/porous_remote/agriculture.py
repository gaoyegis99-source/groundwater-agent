"""Calibrate agricultural pumping with grouped MODFLOW response and bounded ridge."""

from copy import deepcopy
import math
from pathlib import Path
import re

from .packages import read_definitions, seal, write_json
from .results import HEAD, _active, _frames, _valid
from .science import compile_definition, simulate

RIDGES = (0.01, 0.03, 0.1, 0.3, 1.0, 3.0, 10.0)
PERTURBATION = 0.20


def _finite(value: object) -> bool:
    return type(value) in (float, int) and math.isfinite(value)


def _configuration(definition: dict, config: dict) -> tuple:
    if (
        set(config) != {"schemaVersion", "agriculturalWells", "monitors", "observations", "calibrationCount"}
        or config["schemaVersion"] != "porous-agricultural-response.v1"
        or definition["units"]["length"] != "m" or definition["units"]["head"] != "m"
    ):
        raise ValueError("AGRICULTURAL_SCHEMA_INVALID")
    wells = definition["boundaries"]["WEL"]
    selected = config["agriculturalWells"]
    if not isinstance(selected, list) or not 1 <= len(selected) <= len(wells) or any(
        type(index) is not int or not 1 <= index <= len(wells) for index in selected
    ) or len(set(selected)) != len(selected):
        raise ValueError("AGRICULTURAL_WELL_SELECTION_INVALID")
    monitors = config["monitors"]
    if not isinstance(monitors, list) or not 1 <= len(monitors) <= 8:
        raise ValueError("AGRICULTURAL_MONITOR_COUNT_INVALID")
    identifiers = set()
    grid = definition["grid"]
    for monitor in monitors:
        if (
            not isinstance(monitor, dict) or set(monitor) != {"id", "layer", "row", "column"}
            or not isinstance(monitor["id"], str) or not re.fullmatch(r"[A-Za-z0-9-]{1,32}", monitor["id"])
            or monitor["id"] in identifiers
            or not all(type(monitor[key]) is int and 1 <= monitor[key] <= grid[size]
                       for key, size in (("layer", "layers"), ("row", "rows"), ("column", "columns")))
        ):
            raise ValueError("AGRICULTURAL_MONITOR_INVALID")
        identifiers.add(monitor["id"])
    observations = config["observations"]
    split = config["calibrationCount"]
    if (
        not isinstance(observations, list) or not 5 <= len(observations) <= 240
        or type(split) is not int or not 4 <= split < len(observations)
    ):
        raise ValueError("AGRICULTURAL_PERIOD_SPLIT_INVALID")
    prior = 0
    for row in observations:
        if (
            not isinstance(row, dict) or set(row) != {"time", "head"}
            or not _finite(row["time"]) or row["time"] <= prior
            or not isinstance(row["head"], list) or len(row["head"]) != len(monitors)
            or not all(value is None or _finite(value) for value in row["head"])
        ):
            raise ValueError("AGRICULTURAL_OBSERVATION_INVALID")
        prior = row["time"]
    groups = {}
    for index in selected:
        well = wells[index - 1]
        if not _finite(well.get("flow")) or well["flow"] >= 0:
            raise ValueError("AGRICULTURAL_WELL_MUST_BE_PUMPING")
        distances = [
            math.hypot((well["row"] - monitor["row"]) * grid["cellHeight"],
                       (well["column"] - monitor["column"]) * grid["cellWidth"])
            for monitor in monitors
        ]
        nearest = min(range(len(distances)), key=distances.__getitem__)
        distance = distances[nearest]
        if distance > 4000:
            raise ValueError("AGRICULTURAL_WELL_OUTSIDE_MONITOR_RINGS")
        group = f"{monitors[nearest]['id']}-R{1 if distance <= 2000 else 2}"
        groups.setdefault(group, []).append(index - 1)
    return monitors, observations, split, groups


def _run_heads(definition, observations, monitors, work, vendor, key):
    import numpy as np

    work.mkdir()
    compiled, run = work / "compile", work / "run"
    compiled.mkdir()
    run.mkdir()
    compile_definition(definition, compiled, vendor, key)
    simulate(compiled / "model_package.zip", run, vendor, key, flow_only=True)
    grid = definition["grid"]
    dims = grid["layers"], grid["rows"], grid["columns"]
    active = _active(definition, compiled / "project")
    no_flow = float(definition["properties"].get("noFlowHead", -999.99))
    times = {row["time"] for row in observations}
    layers = {monitor["layer"] for monitor in monitors}
    frames = {
        (time, layer): values for time, layer, values in
        _frames(run / "rawResults/model.hds", HEAD, dims) if time in times and layer in layers
    }
    if len(frames) != len(times) * len(layers):
        raise ValueError("AGRICULTURAL_OBSERVATION_TIME_UNAVAILABLE")
    results = []
    for observation in observations:
        values = []
        for monitor in monitors:
            layer = monitor["layer"]
            cell = (monitor["row"] - 1) * grid["columns"] + monitor["column"] - 1
            value = frames[(observation["time"], layer)][cell]
            if not _valid(value, active[layer - 1][cell], "head", no_flow):
                raise ValueError("AGRICULTURAL_MONITOR_CELL_INACTIVE")
            values.append(value)
        results.append(values)
    return np.asarray(results, dtype=np.float64)


def _modified(definition, groups, factors):
    modified = deepcopy(definition)
    wells = modified["boundaries"]["WEL"]
    for indices, factor in zip(groups.values(), factors, strict=True):
        for index in indices:
            wells[index]["flow"] *= float(factor)
    return modified


def invert_agriculture(source: Path, job: Path, vendor: Path, key: bytes) -> dict:
    import numpy as np
    from scipy.optimize import lsq_linear

    data = read_definitions(source, {"model_definition.json", "agricultural.json"})
    definition = data["model_definition.json"]
    monitors, observations, split, groups = _configuration(definition, data["agricultural.json"])
    observed = np.asarray([row["head"] for row in observations], dtype=np.float64)
    base = _run_heads(definition, observations, monitors, job / "baseline", vendor, key)
    responses = []
    for index in range(len(groups)):
        factors = np.ones(len(groups))
        factors[index] += PERTURBATION
        perturbed = _run_heads(
            _modified(definition, groups, factors), observations, monitors,
            job / f"response-{index}", vendor, key,
        )
        responses.append((perturbed - base) / PERTURBATION)
    response = np.stack(responses, axis=-1)

    def system(periods):
        mask = np.isfinite(observed[periods])
        if not mask.any():
            raise ValueError("AGRICULTURAL_PHASE_OBSERVATIONS_MISSING")
        return response[periods][mask], (observed[periods] - base[periods])[mask]

    def solve(matrix, target, ridge):
        augmented = np.vstack((matrix, math.sqrt(ridge) * np.eye(len(groups))))
        output = np.r_[target, np.zeros(len(groups))]
        result = lsq_linear(augmented, output, bounds=(-0.75, 1.5), lsmr_tol="auto")
        if not result.success or not np.isfinite(result.x).all():
            raise ValueError("AGRICULTURAL_OPTIMIZATION_FAILED")
        return result.x

    training_end = split * 3 // 4
    a_train, b_train = system(slice(0, training_end))
    a_holdout, b_holdout = system(slice(training_end, split))
    trials = []
    for ridge in RIDGES:
        fit = solve(a_train, b_train, ridge)
        residual = a_holdout @ fit - b_holdout
        trials.append({
            "ridge": ridge, "holdoutRmseM": float(np.sqrt(np.mean(residual**2))),
            "parameterNorm": float(np.linalg.norm(fit)),
        })
    ridge = min(trials, key=lambda item: (item["holdoutRmseM"], item["parameterNorm"]))["ridge"]
    calibration = slice(0, split)
    validation = slice(split, None)
    a_cal, b_cal = system(calibration)
    if np.any(np.max(np.abs(a_cal), axis=0) < 1e-9):
        raise ValueError("AGRICULTURAL_GROUP_UNOBSERVABLE")
    adjustments = solve(a_cal, b_cal, ridge)
    factors = 1 + adjustments
    linear = base + np.tensordot(response, adjustments, axes=(2, 0))
    final = _run_heads(
        _modified(definition, groups, factors), observations, monitors, job / "optimized", vendor, key,
    )

    def rmse(simulated, periods):
        mask = np.isfinite(observed[periods])
        if not mask.any():
            raise ValueError("AGRICULTURAL_PHASE_OBSERVATIONS_MISSING")
        return float(np.sqrt(np.mean((simulated[periods][mask] - observed[periods][mask])**2)))

    arrays = np.stack((base, linear, final))
    if not np.isfinite(arrays).all():
        raise ValueError("AGRICULTURAL_NONFINITE_HEAD")
    np.save(job / "posterior_arrays.npy", arrays, allow_pickle=False)
    metrics = {
        "method": "agricultural-pumping-response-bounded-ridge",
        "device": "cpu", "ridge": ridge, "shape": list(arrays.shape),
        "calibrationBaselineRmseM": rmse(base, calibration),
        "calibrationOptimizedRmseM": rmse(final, calibration),
        "validationBaselineRmseM": rmse(base, validation),
        "validationOptimizedRmseM": rmse(final, validation),
        "calibrationCount": split, "validationCount": len(observations) - split,
    }
    report = job / "agricultural_report.json"
    write_json(report, {
        "metrics": metrics, "ridgeTrials": trials,
        "groups": {name: {"wellRecords": [index + 1 for index in indices], "multiplier": float(factor)}
                   for (name, indices), factor in zip(groups.items(), factors, strict=True)},
        "arrayAxes": ["baseline/linear/optimized", "time", "monitor"],
        "monitors": monitors, "observations": observations,
        "agriculturalComponentSource": "explicit-pumping-record-selection",
    })
    seal(job / "report_package.zip", "agricultural-inversion", {report.name: report}, key)
    return {"metrics": metrics}
