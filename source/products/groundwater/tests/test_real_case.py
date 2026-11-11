from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from products.groundwater.development.real_case import _load_manifest, _select_cases
from products.groundwater.development.real_case_worker import prepare_mrva


def test_real_case_manifest_selects_all_declared_cases() -> None:
    repository = Path(__file__).resolve().parents[3]
    manifest = _load_manifest(
        repository / "products/groundwater/development/real-data-cases.v1.json"
    )

    assert _select_cases(manifest, "all") == [
        "local-monitoring-trend",
        "odnr-franklin-drastic",
        "usgs-mrva-flow-field",
        "usgs-mrva-kriging",
    ]
    assert set(manifest["sourceGroups"]) == {
        "local-groundwater-v2",
        "odnr-franklin-drastic",
        "usgs-mrva-2024",
    }


def test_mrva_preparation_aggregates_duplicate_coordinates(tmp_path: Path) -> None:
    raw = tmp_path / "raw.csv"
    fields = [
        "X",
        "Y",
        "SITE_BADGE",
        "ALT_88",
        "WLA88_2024",
        "D2W_2024",
        "DATE_2024",
        "USE_2024",
    ]
    with raw.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(
            [
                {
                    "X": "1000",
                    "Y": "2000",
                    "SITE_BADGE": "A",
                    "ALT_88": "100",
                    "WLA88_2024": "90",
                    "D2W_2024": "10",
                    "DATE_2024": "20240401",
                    "USE_2024": "1",
                },
                {
                    "X": "1000",
                    "Y": "2000",
                    "SITE_BADGE": "B",
                    "ALT_88": "102",
                    "WLA88_2024": "91",
                    "D2W_2024": "11",
                    "DATE_2024": "20240402",
                    "USE_2024": "1",
                },
                {
                    "X": "3000",
                    "Y": "4000",
                    "SITE_BADGE": "C",
                    "ALT_88": "80",
                    "WLA88_2024": "75",
                    "D2W_2024": "5",
                    "DATE_2024": "20240403",
                    "USE_2024": "1",
                },
            ]
        )
    flow = tmp_path / "flow.csv"
    kriging = tmp_path / "kriging.csv"
    stats = tmp_path / "stats.json"

    prepare_mrva(raw, flow, kriging, stats)

    evidence = json.loads(stats.read_text(encoding="utf-8"))
    assert evidence["selectedWellCount"] == 3
    assert evidence["uniqueCoordinateCount"] == 2
    assert evidence["duplicateCoordinateRowsAggregated"] == 1
    with kriging.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 2
    assert rows[0]["source_well_count"] == "2"
    assert float(rows[0]["head_m"]) == pytest.approx(90.5 * 0.3048)
