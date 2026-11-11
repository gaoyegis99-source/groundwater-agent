from __future__ import annotations

import sys
from math import sqrt
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from shapely.geometry import LineString, Point

try:
    import numexpr  # noqa: F401
except ModuleNotFoundError:
    numexpr_stub = ModuleType("numexpr")
    numexpr_stub.evaluate = lambda *_args, **_kwargs: None  # type: ignore[attr-defined]
    sys.modules["numexpr"] = numexpr_stub

from moperators.operators import qgis
from moperators.operators.native import StagedInput


class _ArrowField:
    def __init__(self, name: str, extension_name: bytes | None = None) -> None:
        self.name = name
        self.metadata = (
            {b"ARROW:extension:name": extension_name} if extension_name is not None else None
        )


class _ArrowTable:
    def __init__(self) -> None:
        self.column_names = ["level", "wkb_geometry"]
        self.schema = [
            _ArrowField("level"),
            _ArrowField("wkb_geometry", b"geoarrow.wkb"),
        ]


def test_geometry_column_uses_geoarrow_metadata_when_pyogrio_name_is_empty() -> None:
    assert qgis._geometry_column_name({"geometry_name": ""}, _ArrowTable()) == ("wkb_geometry")


def test_projected_coverage_summary_counts_duplicates_and_reports_metric_hull() -> None:
    geometries = [
        Point(0, 0),
        Point(1000, 0),
        Point(0, 1000),
        Point(0, 0),
    ]

    summary = qgis._monitoring_coverage_summary(geometries, "EPSG:3857")

    assert summary["inputFeatureCount"] == 4
    assert summary["inputPointCount"] == 4
    assert summary["uniquePointCount"] == 3
    assert summary["duplicatePointCount"] == 1
    assert summary["hullGeometryType"] == "Polygon"
    assert summary["crs"] == "EPSG:3857"
    assert summary["crsKind"] == "PROJECTED"
    assert summary["measurementMethod"] == "PLANAR_PROJECTED_TO_METRES"
    assert summary["areaSquareMeters"] == pytest.approx(500_000)
    assert summary["perimeterMeters"] == pytest.approx(2000 + 1000 * sqrt(2))
    assert summary["resultKind"] == "GEOMETRIC_CONVEX_HULL"
    limitations = " ".join(summary["limitations"]).lower()
    assert "aquifer boundary" in limitations
    assert "influence area" in limitations
    assert "outermost" in limitations


def test_geographic_coverage_summary_uses_ellipsoidal_measurement() -> None:
    summary = qgis._monitoring_coverage_summary(
        [Point(0, 0), Point(1, 0), Point(0, 1)],
        "EPSG:4326",
    )

    assert summary["crsKind"] == "GEOGRAPHIC"
    assert summary["measurementMethod"] == "GEODESIC_ELLIPSOID"
    assert summary["areaSquareMeters"] > 0
    assert summary["perimeterMeters"] > 0
    assert summary["areaUnit"] == "m2"
    assert summary["perimeterUnit"] == "m"


@pytest.mark.parametrize(
    ("geometries", "error"),
    [
        ([Point(0, 0), Point(1, 1), Point(1, 1)], "INSUFFICIENT_UNIQUE_POINTS"),
        ([Point(0, 0), Point(1, 1), Point(2, 2)], "COLLINEAR_POINTS"),
        (
            [Point(0, 0), Point(1, 0), LineString([(0, 0), (0, 1)])],
            "POINT_GEOMETRY_REQUIRED",
        ),
    ],
)
def test_coverage_summary_rejects_inputs_that_cannot_form_a_point_hull(
    geometries: list[Any], error: str
) -> None:
    with pytest.raises(ValueError, match=f"^{error}$"):
        qgis._monitoring_coverage_summary(geometries, "EPSG:3857")


def test_point_coverage_mode_preflights_then_preserves_qgis_dissolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = StagedInput(
        path=tmp_path / "wells.geojson",
        media_type="application/geo+json",
        format_id="geojson",
        metadata={"crs": "EPSG:3857"},
    )
    calls: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        qgis,
        "_read_vector_geometries",
        lambda _source, _layer: (
            "EPSG:3857",
            [Point(0, 0), Point(1000, 0), Point(0, 1000), Point(0, 0)],
        ),
    )
    monkeypatch.setattr(
        qgis,
        "_run_algorithm",
        lambda algorithm, parameters: calls.append((algorithm, parameters)) or {},
    )

    result = qgis.run_qgis_operator(
        "vector.dissolve",
        {"vector": [source]},
        {"pointCoverageMode": True},
        tmp_path / "outputs",
    )

    assert set(result) == {"vector", "summary"}
    assert result["summary"]["kind"] == "value"
    assert result["summary"]["value"]["duplicatePointCount"] == 1
    assert calls[0][0] == "native:dissolve"
    assert calls[0][1]["INPUT"] == str(source.path)
