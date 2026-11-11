"""Run the pinned upstream compiler and solver worker with fixed Linux paths."""

from __future__ import annotations

import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys

from .packages import extract_sealed, project_definition, seal, write_json
from .quality import concentration_quality
from .numerics import METHOD, configure_transport, read_audit

SOLVERS = {"MODFLOW-2005": "mf2005", "MODFLOW-NWT": "mfnwt"}
_CONTROLS = re.compile(r"[\x00-\x1f\x7f]")
_PATH_KEYS = {"path", "file", "series", "sourceDirectory", "solverExecutable", "fileId"}
_BUDGET_LINE = re.compile(r"^\s*([A-Z][A-Z0-9 /_-]*?)\s*=\s*([0-9.EeDd+-]+)")


def _water_budget(listing: Path, definition: dict, duration: float) -> dict | None:
    if definition["units"]["length"] != "m" or definition["units"]["time"] != "day":
        return None
    section = None
    budget = {}
    with listing.open(encoding="ascii", errors="replace") as stream:
        for line in stream:
            if "VOLUMETRIC BUDGET FOR ENTIRE MODEL" in line:
                budget = {"in": {}, "out": {}}
                section = None
            if not budget:
                continue
            label = line.lstrip().split(maxsplit=1)
            if label and label[0] in {"IN:", "OUT:"}:
                section = label[0][:-1].lower()
            if line.lstrip().startswith("PERCENT DISCREPANCY"):
                section = None
            if section and (match := _BUDGET_LINE.match(line)):
                name, raw = match.groups()
                if name.startswith(("TOTAL", "IN - OUT", "PERCENT")):
                    continue
                amount = float(raw.replace("D", "E").replace("d", "e"))
                if not math.isfinite(amount) or amount < 0:
                    return None
                budget[section][name.strip()] = amount
    if not budget or not all(budget.values()) or not math.isfinite(duration) or duration <= 0:
        return None
    return {"cumulativeM3": budget, "durationDays": duration, "scope": "entire-model"}


def _safe_definition(value: object, field: str = "") -> None:
    if field in _PATH_KEYS and value not in (None, ""):
        raise ValueError("INLINE_MODEL_REQUIRED")
    if field == "executable" and value not in (None, ""):
        raise ValueError("SERVER_SOLVER_REQUIRED")
    if field == "filename" and value != "model.ftl":
        raise ValueError("FLOW_LINK_FILENAME_INVALID")
    if isinstance(value, str) and _CONTROLS.search(value):
        raise ValueError("MODEL_CONTROL_CHARACTERS_INVALID")
    if isinstance(value, dict):
        if value.get("kind") in {"file", "upload"}:
            raise ValueError("INLINE_MODEL_REQUIRED")
        for name, item in value.items():
            if _CONTROLS.search(name):
                raise ValueError("MODEL_CONTROL_CHARACTERS_INVALID")
            _safe_definition(item, name)
    elif isinstance(value, list):
        for item in value:
            _safe_definition(item, field)


def _runtime_definition(definition: dict, vendor: Path) -> dict:
    from gw_platform.http_definition import prepare_definition

    definition = prepare_definition(
        definition, {"template": vendor / "template", "appRoot": vendor}, vendor
    )
    definition["solver"]["executable"] = str(
        vendor / "bin" / SOLVERS[definition["solver"]["target"]]
    )
    transport = definition["transport"]
    configure_transport(transport, vendor)
    return definition


def compile_model(source: Path, job: Path, vendor: Path, key: bytes) -> dict:
    compile_definition(project_definition(source), job, vendor, key)
    return {}


def compile_definition(definition: dict, job: Path, vendor: Path, key: bytes) -> None:
    from gw_platform.model_builder import create_platform_model_project

    _safe_definition(definition)
    grid = definition["grid"]
    dims = [grid[field] for field in ("layers", "rows", "columns")]
    if any(type(size) is not int or size < 1 for size in dims) or (
        dims[0] * dims[1] * dims[2] > 100_000
    ):
        raise ValueError("SERVICE_GRID_LIMIT_EXCEEDED")
    project = job / "project"
    config = _runtime_definition(definition, vendor)
    create_platform_model_project(project, config)
    public_definition = job / "model_definition.json"
    write_json(public_definition, definition)
    model = project / "models/base"
    files = {"model_definition.json": public_definition}
    files.update({
        path.relative_to(project).as_posix(): path
        for path in sorted(model.rglob("*")) if path.is_file()
    })
    seal(job / "model_package.zip", "model", files, key)
    compiled = json.loads((model / "build_manifest.json").read_bytes())
    write_json(job / "validation_report.json", {
        "status": "passed",
        "engine": compiled["target"],
        "grid": compiled["grid"],
        "packages": compiled["packages"],
        "periodCount": len(compiled["stressPeriods"]),
    })


def simulate(
    source: Path, job: Path, vendor: Path, key: bytes, *,
    scenario: dict | None = None, flow_only: bool = False,
) -> dict:
    project = job / "project"
    extract_sealed(source, project, "model", key)
    definition_file = project / "model_definition.json"
    definition = json.loads(definition_file.read_bytes())
    calibration = project / "calibration-scenario.json"
    if calibration.is_file():
        if scenario is not None:
            raise ValueError("CALIBRATION_SCENARIO_ALREADY_SET")
        scenario = json.loads(calibration.read_bytes())
    config = _runtime_definition(definition, vendor)
    if flow_only:
        config["transport"]["enabled"] = False
    directories = {"run": str(job)}
    for name in ("snapshot", "work", "rawResults", "processedResults", "logs"):
        directory = job / name
        directory.mkdir()
        directories[name] = str(directory)
    request = job / "worker-request.json"
    write_json(request, {
        "protocolVersion": "1.0",
        "runId": job.name,
        "project": {
            "projectId": job.name,
            "name": config["name"],
            "units": config["units"],
            "_projectFile": str(project / "project.json"),
            "modelDefinition": "model_definition.json",
            "warmup": config["warmup"],
            "transport": config["transport"],
            "model": {
                "sourceDirectory": str(project / "models/base"),
                "solverExecutable": config["solver"]["executable"],
                "namefile": "model.nam",
                "solverFamily": config["solver"]["target"],
                "generatedByPlatform": True,
            },
        },
        "plugin": {"id": "org.groundwater.porous-flow", "version": "1.3.1"},
        "directories": directories,
        "scenario": scenario,
    })
    with (job / "logs/worker.log").open("wb") as log:
        subprocess.run([
            sys.executable, "-I", "-B", "-c",
            "import runpy,sys; sys.path.insert(0,sys.argv[1]); sys.argv=sys.argv[2:]; "
            "runpy.run_path(sys.argv[0],run_name='__main__')",
            str(vendor / "src"), str(vendor / "worker.py"), str(request),
        ], cwd=job, check=True, stdout=log, stderr=subprocess.STDOUT)
    summary = json.loads((job / "processedResults/summary.json").read_bytes())
    if summary["status"] != "succeeded" or not summary["normalTermination"]:
        raise ValueError("SOLVER_DID_NOT_CONVERGE")
    transport = summary["transport"]
    if config["transport"].get("enabled") and (
        not transport.get("executed") or not transport.get("normalTermination")
    ):
        raise ValueError("TRANSPORT_DID_NOT_CONVERGE")
    result = {
        "engine": summary["solverFamily"],
        "normalTermination": True,
        "waterBalance": summary["waterBalance"],
        "head": summary["finalHead"],
        "scenario": json.loads((job / "snapshot/scenario_application.json").read_bytes()),
        "transport": {
            name: transport[name]
            for name in ("enabled", "executed", "normalTermination", "concentration")
            if name in transport
        },
    }
    budget = _water_budget(job / "rawResults/model.list", definition, result["head"]["totalTime"])
    if budget is not None:
        result["waterBudget"] = budget
    if transport.get("executed"):
        result["transport"]["quality"] = concentration_quality(
            job / "rawResults/transport.ucn", config, project,
        )
        method = config["transport"]["mt3d"]["numerics"]
        result["transport"]["numerics"] = {"method": method}
        if method == METHOD:
            audit = job / "work/transport-mt3d/afc-audit.csv"
            result["transport"]["numerics"] = read_audit(audit, result["head"]["totalTime"])
            if result["transport"]["quality"]["negativeCellRecords"]:
                raise ValueError("AFC_NEGATIVE_CONCENTRATION_OUTPUT")
            shutil.copyfile(audit, job / "rawResults/transport.afc.csv")
    result_summary = job / "calculation_summary.json"
    write_json(result_summary, result)
    files = {"model_definition.json": definition_file, result_summary.name: result_summary}
    for name in ("model.hds", "model.cbc", "transport.ucn", "transport.mas", "transport.afc.csv"):
        path = job / "rawResults" / name
        if path.is_file():
            files[f"raw/{name}"] = path
    seal(job / "result_package.zip", "results", files, key)
    if scenario is not None:
        definition["appliedScenario"] = scenario
    definition["transport"]["mt3d"]["numerics"] = config["transport"]["mt3d"]["numerics"]
    definition["transport"]["mt3d"]["gcg"] = config["transport"]["mt3d"]["gcg"]
    return {"calculation_summary": result, "effective_parameters": definition}
