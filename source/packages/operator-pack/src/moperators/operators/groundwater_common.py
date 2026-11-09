from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import rasterio


class RasterInput(Protocol):
    @property
    def path(self) -> Path: ...


def artifact(path: Path) -> dict[str, Any]:
    return {
        "kind": "artifact",
        "path": str(path),
        "fileName": path.name,
        "mediaType": "image/tiff",
        "format": "geotiff",
    }


def validate_grid(dataset: rasterio.io.DatasetReader) -> None:
    transform = dataset.transform
    if not dataset.crs or not dataset.crs.is_projected:
        raise ValueError("CRS_REQUIRED")
    unit = (dataset.crs.linear_units or "").lower()
    if unit not in {"metre", "meter", "metres", "meters"}:
        raise ValueError("UNIT_UNSUPPORTED")
    if not math.isclose(transform.b, 0.0) or not math.isclose(transform.d, 0.0):
        raise ValueError("ROTATED_GRID_UNSUPPORTED")
    if transform.a <= 0 or transform.e >= 0:
        raise ValueError("GRID_MISMATCH")


def profile(dataset: rasterio.io.DatasetReader, nodata: float) -> dict[str, Any]:
    result = dataset.profile.copy()
    result.update(driver="GTiff", dtype="float32", count=1, nodata=nodata, compress="deflate")
    return result


def write(path: Path, values: np.ndarray, output_profile: dict[str, Any]) -> dict[str, Any]:
    with rasterio.open(path, "w", **output_profile) as target:
        target.write(values.astype(np.float32), 1)
    return artifact(path)
