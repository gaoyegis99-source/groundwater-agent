"""Build the reproducible, simulated Yaozhanbao assessment case."""

from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import json
from pathlib import Path


CASE_ID = "yaozhanbao-synthetic-2026"
DATA_NATURE = "SYNTHETIC_TEST_ONLY"
SCOPE = {
    "waterSource": "腰站堡水源地（模拟案例）",
    "startDate": "2026-01-01",
    "endDate": "2026-12-31",
}


def _write_json(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _reference(path: Path) -> dict:
    return {
        "path": path.name,
        "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def build_case(output: Path) -> Path:
    output.mkdir(parents=True, exist_ok=False)
    recharge_weights = (3, 3, 4, 5, 7, 10, 16, 20, 13, 8, 6, 5)
    annual_totals = {
        "outflow_m3": 4_200_000,
        "pumping_m3": 20_600_000,
        "supply_m3": 19_800_000,
        "demand_m3": 21_000_000,
    }
    used = dict.fromkeys(annual_totals, 0)
    budget = []
    for month, weight in enumerate(recharge_weights, start=1):
        days = calendar.monthrange(2026, month)[1]
        row = {
            "month": f"2026-{month:02d}",
            "data_nature": DATA_NATURE,
            "recharge_m3": 25_200_000 * weight // 100,
        }
        for name, annual in annual_totals.items():
            volume = annual * days // 365 if month < 12 else annual - used[name]
            row[name] = volume
            used[name] += volume
        row["storage_change_m3"] = (
            row["recharge_m3"] - row["outflow_m3"] - row["pumping_m3"]
        )
        budget.append(row)
    _write_csv(output / "water-budget.csv", budget)
    totals = {
        name: sum(row[name] for row in budget)
        for name in budget[0]
        if name.endswith("_m3")
    }

    quality = [
        {
            "point_id": f"SIM-Q{point:02d}",
            "sample_date": "2026-05-18",
            "data_nature": DATA_NATURE,
            "simulated_quality_class": "II"
            if point <= 6
            else "III"
            if point <= 35
            else "IV",
            "simulated_characteristic_exceedance": int(point > 37),
        }
        for point in range(1, 41)
    ]
    _write_csv(output / "water-quality.csv", quality)
    recovery = [
        {
            "well_id": "SIM-P01",
            "pump_stop": "2026-05-19T08:00:00+08:00",
            "data_nature": DATA_NATURE,
            "elapsed_h": hours,
            "drawdown_m": drawdown,
        }
        for hours, drawdown in (
            (0, 12.5),
            (1, 8.2),
            (4, 4.4),
            (8, 2.4),
            (12, 1.3),
            (24, 0.6),
        )
    ]
    _write_csv(output / "well-recovery.csv", recovery)
    well = {
        "dataNature": DATA_NATURE,
        "wellId": "SIM-P01",
        "wellYieldM3PerDay": 4800,
        "hydraulicConductivityMPerDay": 35,
        "aquiferMedia": "中粗砂岩、砾岩",
        "vadoseMedia": "砂土",
    }
    _write_json(output / "well-test.json", well)
    management = {
        "dataNature": DATA_NATURE,
        "availableM3PerYear": 24_000_000,
        "assumedExtractionCapM3PerYear": 22_000_000,
        "assumedProhibitedArea": False,
        "assumedSevereDamageOrUnusableQuality": False,
    }
    _write_json(output / "management-assumptions.json", management)

    fields = {role: {} for role in ("simulation", "monitoring", "management")}

    def record(
        role: str, field: str, value: object, unit: str | None, basis: str, source: str
    ) -> None:
        fields[role][field] = {
            "value": value,
            "unit": unit,
            "basis": basis,
            "source": source,
        }

    for role, field, column in (
        ("simulation", "recharge_r_m3_a", "recharge_m3"),
        ("simulation", "outflow_qout_m3_a", "outflow_m3"),
        ("simulation", "pumping_qp_m3_a", "pumping_m3"),
        ("management", "annual_pumping_qa_m3_a", "pumping_m3"),
        ("management", "supply_qs", "supply_m3"),
        ("management", "demand_qg", "demand_m3"),
    ):
        record(
            role,
            field,
            totals[column],
            "m3/a",
            f"模拟全年 12 个月 {column} 合计",
            "water-budget.csv",
        )
    for field, key, unit in (
        ("well_yield_m3_d", "wellYieldM3PerDay", "m3/d"),
        ("hydraulic_conductivity_m_d", "hydraulicConductivityMPerDay", "m/d"),
        ("aquifer_media", "aquiferMedia", None),
        ("vadose_media", "vadoseMedia", None),
    ):
        record(
            "monitoring",
            field,
            well[key],
            unit,
            "SIM-P01 单井试验或介质分类假设",
            "well-test.json",
        )
    record(
        "monitoring",
        "initial_water_level_s0_m",
        recovery[0]["drawdown_m"],
        "m",
        "模拟停泵起点降深",
        "well-recovery.csv",
    )
    record(
        "monitoring",
        "residual_drawdown_sr_m",
        recovery[-1]["drawdown_m"],
        "m",
        "同井观测终点残余降深",
        "well-recovery.csv",
    )
    record(
        "monitoring",
        "recovery_hours",
        recovery[-1]["elapsed_h"],
        "h",
        "同井连续恢复观测时长",
        "well-recovery.csv",
    )
    record(
        "monitoring",
        "total_water_points_nz",
        len(quality),
        "count",
        "模拟独立点位总数",
        "water-quality.csv",
    )
    record(
        "monitoring",
        "class_iii_count_nd",
        sum(row["simulated_quality_class"] in {"I", "II", "III"} for row in quality),
        "count",
        "按模拟类别统计达到或优于Ⅲ类",
        "water-quality.csv",
    )
    record(
        "monitoring",
        "pollutant_exceedance_count_nc",
        sum(row["simulated_characteristic_exceedance"] for row in quality),
        "count",
        "按模拟特征污染指标标记统计点位",
        "water-quality.csv",
    )
    record(
        "management",
        "available_qe_m3_a",
        management["availableM3PerYear"],
        "m3/a",
        "模拟可开采量假设",
        "management-assumptions.json",
    )
    for field, key in (
        ("prohibited_area", "assumedProhibitedArea"),
        ("severe_damage_or_unusable_quality", "assumedSevereDamageOrUnusableQuality"),
    ):
        record(
            "management",
            field,
            "是" if management[key] else "否",
            None,
            "模拟管理情景假设",
            "management-assumptions.json",
        )
    record(
        "management",
        "extraction_cap_exceeded",
        "是"
        if totals["pumping_m3"] > management["assumedExtractionCapM3PerYear"]
        else "否",
        None,
        "模拟年度开采量与假设控制上限比较",
        "water-budget.csv + management-assumptions.json",
    )
    evidence = {}
    for role, records in fields.items():
        path = output / f"{role}-evidence.json"
        _write_json(path, {"scope": SCOPE, "synthetic": True, "fields": records})
        evidence[role] = _reference(path)
    manifest = output / "case.json"
    _write_json(
        manifest,
        {
            "schemaVersion": "sustainability-synthetic-case.v1",
            "caseId": CASE_ID,
            "dataNature": DATA_NATURE,
            "evidence": evidence,
            "rawInputs": [
                _reference(output / name)
                for name in (
                    "water-budget.csv",
                    "water-quality.csv",
                    "well-recovery.csv",
                    "well-test.json",
                    "management-assumptions.json",
                )
            ],
            # Independently computed from fixed bands and recommended weights.
            "expected": {
                "score": 88.37075,
                "min": 83.327,
                "max": 93.4145,
                "grade": "II",
            },
        },
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    print(build_case(args.output_dir))


if __name__ == "__main__":
    main()
