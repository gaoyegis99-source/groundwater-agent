"""Combine normalized, attributable indicator evidence for human review."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import date
from pathlib import Path
from sys import float_info

SIMULATION_FIELDS = {
    "recharge_r_m3_a": "m3/a",
    "outflow_qout_m3_a": "m3/a",
    "pumping_qp_m3_a": "m3/a",
    "initial_water_level_s0_m": "m",
    "residual_drawdown_sr_m": "m",
}
UNITS = {
    **SIMULATION_FIELDS,
    "well_yield_m3_d": "m3/d",
    "annual_pumping_qa_m3_a": "m3/a",
    "available_qe_m3_a": "m3/a",
    "supply_qs": "m3/a",
    "demand_qg": "m3/a",
    "class_iii_count_nd": "count",
    "total_water_points_nz": "count",
    "pollutant_exceedance_count_nc": "count",
    "recovery_hours": "h",
    "hydraulic_conductivity_m_d": "m/d",
}
QUALITY = {"class_iii_count_nd", "total_water_points_nz", "pollutant_exceedance_count_nc"}
RESTRICTIONS = {"prohibited_area", "extraction_cap_exceeded", "severe_damage_or_unusable_quality"}
REQUIRED = (set(UNITS) - {"recovery_hours", "hydraulic_conductivity_m_d"}) | RESTRICTIONS
TEXT_FIELDS = {"aquifer_media", "vadose_media"} | RESTRICTIONS
MANAGEMENT = {
    "annual_pumping_qa_m3_a", "available_qe_m3_a", "supply_qs", "demand_qg",
} | RESTRICTIONS
VOLUME_FIELDS = {"recharge_r_m3_a", "outflow_qout_m3_a", "pumping_qp_m3_a"}


def _scope(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) != {"waterSource", "startDate", "endDate"}:
        raise ValueError("ASSESSMENT_SCOPE_INVALID")
    if (
        not isinstance(value["waterSource"], str)
        or not 1 <= len(value["waterSource"].strip()) <= 200
        or not isinstance(value["startDate"], str)
        or not isinstance(value["endDate"], str)
        or date.fromisoformat(value["startDate"]) > date.fromisoformat(value["endDate"])
    ):
        raise ValueError("ASSESSMENT_SCOPE_INVALID")
    return value


def _validate_indicator(field: str, record: dict, role: str) -> str | None:
    if not isinstance(record, dict) or set(record) != {"value", "unit", "basis", "source"}:
        return "INVALID_RECORD"
    if any(
        not isinstance(record[key], str) or not record[key].strip()
        for key in ("basis", "source")
    ):
        return "MISSING_BASIS_OR_SOURCE"
    if role == "simulation" and field not in SIMULATION_FIELDS:
        return "SIMULATION_FIELD_NOT_ALLOWED"
    if field in QUALITY and role != "monitoring":
        return "MONITORING_REQUIRED"
    if field in MANAGEMENT and role != "management":
        return "MANAGEMENT_EVIDENCE_REQUIRED"
    if field in TEXT_FIELDS:
        value = record["value"]
        if record["unit"] is not None or not isinstance(value, str) or not value.strip():
            return "INVALID_CATEGORY"
        if field in RESTRICTIONS and value not in {"是", "否", "待核查"}:
            return "INVALID_CATEGORY"
        return None
    if field not in UNITS or record["unit"] != UNITS[field]:
        return "UNIT_OR_FIELD_MISMATCH"
    value = record["value"]
    if type(value) not in (float, int) or not 0 <= value <= float_info.max:
        return "INVALID_NUMERIC_VALUE"
    if field in QUALITY and type(value) is not int:
        return "INTEGER_COUNT_REQUIRED"
    return None


def prepare(documents: dict[str, dict]) -> tuple[dict | None, dict]:
    """Return a candidate only if exact-scope evidence passes the checks."""
    if set(documents) != {"simulation", "monitoring", "management"}:
        raise ValueError("ASSESSMENT_SOURCES_INVALID")
    for role, document in documents.items():
        if (
            not isinstance(document, dict)
            or set(document) != {"scope", "fields", "synthetic"}
            or type(document["synthetic"]) is not bool
            or not isinstance(document["fields"], dict)
        ):
            raise ValueError(f"ASSESSMENT_EVIDENCE_INVALID:{role}")
    scope = _scope(documents["management"]["scope"])
    values = {}
    sources = {}
    issues = []
    synthetic = False
    for role, document in documents.items():
        if _scope(document["scope"]) != scope:
            issues.append({"role": role, "code": "SCOPE_MISMATCH"})
            continue
        synthetic |= document["synthetic"]
        for field, record in document["fields"].items():
            error = _validate_indicator(field, record, role)
            if error is None and field in values:
                error = "MULTIPLE_SOURCES_FOR_FIELD"
            if error:
                issues.append({"role": role, "field": field, "code": error})
                continue
            values[field] = record["value"]
            sources[field] = {"role": role, **record}

    issues.extend(
        {"field": field, "code": "FIELD_MISSING"} for field in sorted(REQUIRED - values.keys())
    )
    if not issues:
        for field in ("demand_qg", "available_qe_m3_a", "total_water_points_nz",
                      "initial_water_level_s0_m", "recovery_hours"):
            if values.get(field, 1) <= 0:
                issues.append({"field": field, "code": "POSITIVE_VALUE_REQUIRED"})
        for numerator, denominator in (
            ("class_iii_count_nd", "total_water_points_nz"),
            ("pollutant_exceedance_count_nc", "total_water_points_nz"),
            ("residual_drawdown_sr_m", "initial_water_level_s0_m"),
        ):
            if values[numerator] > values[denominator]:
                issues.append({"field": numerator, "code": "EXCEEDS_REFERENCE"})
        outflow = values["outflow_qout_m3_a"] + values["pumping_qp_m3_a"]
        if not 0 < outflow <= float_info.max or not math.isfinite(outflow):
            issues.append(
                {"field": "outflow_qout_m3_a", "code": "WATER_BALANCE_DENOMINATOR_INVALID"}
            )
    report = {
        "status": "BLOCKED" if issues else "REVIEW_REQUIRED",
        "scope": scope,
        "fields": sources,
        "issues": issues,
    }
    if issues:
        return None, report
    return {
        "context": {
            "water_source": scope["waterSource"],
            "groundwater_type": "孔隙型",
            "evaluation_time": scope["endDate"],
        },
        "synthetic_example": synthetic,
        **values,
    }, report


def _from_budget(summary: dict, management: dict) -> tuple[dict, dict]:
    management = dict(management)
    mappings = management.pop("simulationBudget")
    scope = _scope(management["scope"])
    evidence = {"scope": scope, "fields": {}, "synthetic": management["synthetic"]}
    if not isinstance(mappings, list) or not 1 <= len(mappings) <= 3:
        raise ValueError("SIMULATION_BUDGET_MAPPING_INVALID")
    budget = summary.get("waterBudget")
    days = (date.fromisoformat(scope["endDate"]) - date.fromisoformat(scope["startDate"])).days
    if (
        not isinstance(budget, dict) or summary.get("normalTermination") is not True
        or budget.get("scope") != "entire-model" or days <= 0 or budget.get("durationDays") != days
    ):
        raise ValueError("SIMULATION_BUDGET_SCOPE_OR_PERIOD_MISMATCH")
    for mapping in mappings:
        if not isinstance(mapping, dict) or set(mapping) != {
            "field", "direction", "term", "basis"
        }:
            raise ValueError("SIMULATION_BUDGET_MAPPING_INVALID")
        field, direction, term, basis = (
            mapping[k] for k in ("field", "direction", "term", "basis")
        )
        if (
            field not in VOLUME_FIELDS or field in evidence["fields"]
            or direction != ("in" if field == "recharge_r_m3_a" else "out")
            or not isinstance(term, str) or term in {"STORAGE", ""}
            or not isinstance(basis, str) or not basis.strip()
        ):
            raise ValueError("SIMULATION_BUDGET_MAPPING_INVALID")
        try:
            amount = budget["cumulativeM3"][direction][term]
        except (KeyError, TypeError) as error:
            raise ValueError("SIMULATION_BUDGET_TERM_UNAVAILABLE") from error
        if type(amount) not in (float, int) or not math.isfinite(amount) or amount < 0:
            raise ValueError("SIMULATION_BUDGET_TERM_INVALID")
        evidence["fields"][field] = {
            "value": amount * (365 / days), "unit": "m3/a",
            "basis": f"{basis}。全模型累计量 {amount:g} m³ 按 {days} 日折算为 365 日年均量。",
            "source": f"V6 模拟成果 waterBudget.cumulativeM3.{direction}.{term}",
        }
    return evidence, management


def _load(paths: dict[str, Path]) -> tuple[dict, dict]:
    documents, hashes = {}, {}
    for role, path in paths.items():
        with path.open("rb") as stream:
            content = stream.read(1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            raise ValueError(f"ASSESSMENT_EVIDENCE_TOO_LARGE:{role}")
        documents[role] = json.loads(content)
        hashes[role] = f"sha256:{hashlib.sha256(content).hexdigest()}"
    return documents, hashes


def prepare_operator(operator_id: str, inputs: dict, parameters: dict, output_dir: Path) -> dict:
    from .transfer_common import write_json

    paths = {role: inputs[role][0].path for role in ("simulation", "monitoring", "management")}
    hashes = {}
    try:
        documents, hashes = _load(paths)
        if (
            isinstance(documents["management"], dict)
            and "simulationBudget" in documents["management"]
        ):
            documents["simulation"], documents["management"] = _from_budget(
                documents["simulation"], documents["management"],
            )
        candidate, report = prepare(documents)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
        code = str(error)
        if not code.startswith(("ASSESSMENT_", "SIMULATION_BUDGET_")):
            code = "ASSESSMENT_EVIDENCE_INVALID"
        candidate = None
        report = {"status": "BLOCKED", "fields": {}, "issues": [{"code": code}]}
    report["inputSha256"] = hashes
    return {
        "assessment_input": write_json(
            candidate if candidate is not None else {"status": "BLOCKED", "assessmentInput": None},
            output_dir / "assessment-input.json",
        ),
        "field_sources": write_json(report, output_dir / "field-sources.json"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for role in ("simulation", "monitoring", "management"):
        parser.add_argument(f"--{role}", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    documents, hashes = _load(
        {role: getattr(args, role) for role in ("simulation", "monitoring", "management")}
    )
    candidate, report = prepare(documents)
    args.output_dir.mkdir(mode=0o700)
    report["inputSha256"] = hashes
    (args.output_dir / "field-sources.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    if candidate is None:
        return 2
    (args.output_dir / "assessment-input.json").write_text(
        json.dumps(candidate, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
