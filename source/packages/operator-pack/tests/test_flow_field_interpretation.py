from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import numpy as np

try:
    import rasterio  # noqa: F401
except ModuleNotFoundError:
    rasterio_stub = ModuleType("rasterio")
    rasterio_stub.io = SimpleNamespace(DatasetReader=object)  # type: ignore[attr-defined]
    sys.modules["rasterio"] = rasterio_stub

from moperators.operators import darcy_velocity, head_calculate, hydraulic_gradient


@dataclass(frozen=True)
class TableInput:
    path: Path
    format_id: str = "csv"
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class Transform:
    a: float = 10.0
    b: float = 0.0
    d: float = 0.0
    e: float = -10.0


@dataclass(frozen=True)
class Crs:
    value: str = "EPSG:32650"
    is_projected: bool = True
    linear_units: str = "metre"

    def to_string(self) -> str:
        return self.value


class Dataset:
    def __init__(self, values: np.ndarray, *, crs: Crs | None = None) -> None:
        self.values = np.ma.array(values, mask=np.zeros(values.shape, dtype=bool))
        self.crs = crs or Crs()
        self.transform = Transform()
        self.count = 1
        self.height, self.width = values.shape
        self.nodata = -9999.0
        self.profile = {
            "driver": "GTiff",
            "dtype": "float32",
            "count": 1,
            "height": self.height,
            "width": self.width,
            "crs": self.crs,
            "transform": self.transform,
        }

    def __enter__(self) -> Dataset:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _band: int, *, masked: bool) -> np.ma.MaskedArray:
        assert masked
        return self.values.copy()


def _artifact(path: Path, values: np.ndarray, captured: dict[str, np.ndarray]) -> dict:
    captured[path.name] = values.copy()
    return {
        "kind": "artifact",
        "path": str(path),
        "fileName": path.name,
        "mediaType": "image/tiff",
        "format": "geotiff",
    }


def test_head_calculation_reports_basis_units_and_computed_status(tmp_path: Path) -> None:
    path = tmp_path / "observations.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["well", "elevation", "depth"])
        writer.writeheader()
        writer.writerow({"well": "A", "elevation": "12345", "depth": "2345"})

    result = head_calculate.run(
        TableInput(path),
        {
            "referenceElevationField": "elevation",
            "depthToWaterField": "depth",
            "referenceElevationUnit": "cm",
            "depthToWaterUnit": "mm",
            "outputField": "head_m",
        },
        tmp_path,
    )

    import pyarrow.parquet as parquet

    row = parquet.read_table(result["heads"]["path"]).to_pylist()[0]
    assert row["head_m"] == 121.105
    summary = result["summary"]["value"]
    assert summary["resultKind"] == "COMPUTED_HEAD_ELEVATION"
    assert summary["referenceElevationUnit"] == "cm"
    assert summary["depthToWaterUnit"] == "mm"
    assert summary["outputUnit"] == "m"
    assert "reference elevation" in summary["calculation"].lower()
    assert "same vertical datum" in " ".join(summary["limitations"]).lower()


def test_gradient_summary_defines_projected_grid_direction_and_limitations(
    tmp_path: Path, monkeypatch: Any
) -> None:
    values = np.tile(-np.arange(5, dtype=np.float64), (5, 1))
    dataset = Dataset(values)
    captured: dict[str, np.ndarray] = {}
    monkeypatch.setattr(hydraulic_gradient.rasterio, "open", lambda _path: dataset, raising=False)
    monkeypatch.setattr(
        hydraulic_gradient,
        "write",
        lambda path, array, _profile: _artifact(path, array, captured),
    )

    result = hydraulic_gradient.run(SimpleNamespace(path=tmp_path / "head.tif"), {}, tmp_path)

    assert np.allclose(captured["hydraulic-gradient.tif"][1:-1, 1:-1], 0.1)
    assert np.allclose(captured["groundwater-flow-direction.tif"][1:-1, 1:-1], 90)
    summary = result["summary"]["value"]
    assert summary["gradientUnit"] == "dimensionless"
    assert summary["horizontalCrs"] == "EPSG:32650"
    assert summary["horizontalUnit"] == "m"
    assert summary["directionUnit"] == "degree"
    assert "grid north" in summary["directionConvention"].lower()
    assert "decreasing hydraulic head" in summary["directionConvention"].lower()
    assert summary["resultKind"] == "DERIVED_FROM_INTERPOLATED_HEAD"
    assert "interpolation" in " ".join(summary["limitations"]).lower()


def test_velocity_summary_separates_darcy_flux_from_seepage_and_observation(
    tmp_path: Path, monkeypatch: Any
) -> None:
    gradient = Dataset(np.full((3, 3), 0.1, dtype=np.float64))
    direction = Dataset(np.full((3, 3), 90.0, dtype=np.float64), crs=gradient.crs)
    datasets = {"gradient.tif": gradient, "direction.tif": direction}
    captured: dict[str, np.ndarray] = {}
    monkeypatch.setattr(
        darcy_velocity.rasterio,
        "open",
        lambda path: datasets[Path(path).name],
        raising=False,
    )
    monkeypatch.setattr(
        darcy_velocity,
        "write",
        lambda path, array, _profile: _artifact(path, array, captured),
    )

    result = darcy_velocity.run(
        SimpleNamespace(path=tmp_path / "gradient.tif"),
        SimpleNamespace(path=tmp_path / "direction.tif"),
        {
            "hydraulicConductivityMetersPerDay": 10,
            "effectivePorosity": 0.25,
        },
        tmp_path,
    )

    assert np.allclose(captured["darcy-flux.tif"], 1.0)
    assert np.allclose(captured["seepage-velocity.tif"], 4.0)
    assert np.allclose(captured["velocity-east.tif"], 4.0)
    assert np.allclose(captured["velocity-north.tif"], 0.0, atol=1e-6)
    summary = result["summary"]["value"]
    assert summary["darcyFluxUnit"] == "m/day"
    assert summary["seepageVelocityUnit"] == "m/day"
    assert summary["darcyFluxFormula"] == "q = K * |grad h|"
    assert summary["seepageVelocityFormula"] == "v = q / n_e"
    assert summary["hydraulicConductivityMetersPerDay"] == 10
    assert summary["effectivePorosity"] == 0.25
    assert summary["resultKind"] == "MODEL_DERIVED_NOT_OBSERVED"
    limitations = " ".join(summary["limitations"]).lower()
    assert "homogeneous" in limitations and "isotropic" in limitations
    assert "not an observed" in limitations
