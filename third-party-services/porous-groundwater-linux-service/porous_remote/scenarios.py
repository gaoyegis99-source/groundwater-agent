"""Compare an immutable baseline with explicit upstream scenario modifiers."""

import math
from pathlib import Path
import re

from .packages import read_definitions, seal, write_json
from .science import compile_definition, simulate

MULTIPLIERS = {
    "rechargeMultiplier", "hydraulicConductivityMultiplier",
    "verticalHydraulicConductivityMultiplier", "horizontalAnisotropyMultiplier",
    "specificYieldMultiplier", "specificStorageMultiplier", "ghbConductanceMultiplier",
    "wellMultiplier",
}
SCENARIO_ID = re.compile(r"[a-z][a-z0-9-]{0,31}")


def _scenarios(value: dict) -> list[dict]:
    if set(value) != {"schemaVersion", "scenarios"} or value["schemaVersion"] != "porous-scenarios.v1":
        raise ValueError("SCENARIO_SCHEMA_INVALID")
    scenarios = value["scenarios"]
    if not isinstance(scenarios, list) or not 1 <= len(scenarios) <= 6:
        raise ValueError("SCENARIO_COUNT_INVALID")
    identifiers = {"baseline"}
    for scenario in scenarios:
        if not isinstance(scenario, dict) or set(scenario) != {"scenarioId", "name", "modifiers"}:
            raise ValueError("SCENARIO_INVALID")
        identifier, name, modifiers = (scenario[field] for field in ("scenarioId", "name", "modifiers"))
        if (
            not isinstance(identifier, str) or not SCENARIO_ID.fullmatch(identifier)
            or identifier in identifiers
            or not isinstance(name, str) or not name.strip() or len(name) > 100
            or not isinstance(modifiers, dict) or not modifiers or set(modifiers) - MULTIPLIERS
            or any(type(n) not in (float, int) or not math.isfinite(n) or not 0.0001 <= n <= 1000
                   for n in modifiers.values())
            or all(n == 1 for n in modifiers.values())
        ):
            raise ValueError("SCENARIO_INVALID")
        identifiers.add(identifier)
    return scenarios


def compare_scenarios(source: Path, job: Path, vendor: Path, key: bytes) -> dict:
    definitions = read_definitions(source, {"model_definition.json", "scenarios.json"})
    scenarios = _scenarios(definitions["scenarios.json"])
    compiled = job / "compiled"
    compiled.mkdir()
    compile_definition(definitions["model_definition.json"], compiled, vendor, key)
    model = compiled / "model_package.zip"
    runs = {}
    rows = []
    baseline_head = None
    for scenario in [None, *scenarios]:
        identifier = scenario["scenarioId"] if scenario else "baseline"
        directory = job / identifier
        directory.mkdir()
        results = simulate(model, directory, vendor, key, scenario=scenario)
        summary = results["calculation_summary"]
        if scenario is not None and not summary["scenario"]["changes"]:
            raise ValueError("SCENARIO_HAS_NO_APPLICABLE_MODIFIERS")
        head = summary["head"]["mean"]
        if baseline_head is None:
            baseline_head = head
        rows.append({
            "scenarioId": identifier, "name": scenario["name"] if scenario else "基准",
            "meanFinalHead": head, "meanFinalHeadChange": head - baseline_head,
            "waterBalance": summary["waterBalance"], "transport": summary["transport"],
            "changes": summary["scenario"]["changes"],
        })
        runs[f"{identifier}/result_package.zip"] = directory / "result_package.zip"
    table = job / "scenario_table.json"
    write_json(table, rows)
    runs[table.name] = table
    seal(job / "result_package.zip", "scenario-results", runs, key)
    return {"comparison_summary": {"baseline": "baseline", "scenarios": len(scenarios)}}
