from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import rasterio

from moperators.operators.groundwater_common import (
    RasterInput,
    profile,
    validate_grid,
    write,
)


def run(source: RasterInput, parameters: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    with rasterio.open(source.path) as dataset:
        validate_grid(dataset)
        band = int(parameters.get("band", 1))
        if band < 1 or band > dataset.count:
            raise ValueError("BAND_REQUIRED")
        values = dataset.read(band, masked=True).astype(np.float64)
        valid = ~np.ma.getmaskarray(values)
        data = np.asarray(values.filled(np.nan))
        nodata = float(dataset.nodata) if dataset.nodata is not None else -9999.0
        magnitude = np.full(data.shape, nodata, dtype=np.float32)
        direction = np.full(data.shape, nodata, dtype=np.float32)
        center_valid = (
            valid[1:-1, 1:-1]
            & valid[:-2, :-2]
            & valid[1:-1, :-2]
            & valid[2:, :-2]
            & valid[:-2, 2:]
            & valid[1:-1, 2:]
            & valid[2:, 2:]
            & valid[:-2, 1:-1]
            & valid[2:, 1:-1]
        )
        dz_dx = (data[1:-1, 2:] - data[1:-1, :-2]) / (2 * dataset.transform.a)
        dz_dy_north = (data[:-2, 1:-1] - data[2:, 1:-1]) / (2 * abs(dataset.transform.e))
        local_magnitude = np.hypot(dz_dx, dz_dy_north)
        local_direction = (np.degrees(np.arctan2(-dz_dx, -dz_dy_north)) + 360) % 360
        magnitude_view = magnitude[1:-1, 1:-1]
        direction_view = direction[1:-1, 1:-1]
        magnitude_view[center_valid] = local_magnitude[center_valid]
        flowing = center_valid & (local_magnitude > 0)
        direction_view[flowing] = local_direction[flowing]
        output_profile = profile(dataset, nodata)
    valid_count = int(np.count_nonzero(magnitude != nodata))
    return {
        "magnitude": write(output_dir / "hydraulic-gradient.tif", magnitude, output_profile),
        "direction": write(
            output_dir / "groundwater-flow-direction.tif", direction, output_profile
        ),
        "summary": {
            "kind": "value",
            "value": {
                "validCellCount": valid_count,
                "gradientUnit": "dimensionless",
                "directionUnit": "degree",
            },
        },
    }
