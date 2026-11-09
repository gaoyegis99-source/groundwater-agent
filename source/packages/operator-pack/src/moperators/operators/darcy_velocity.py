from __future__ import annotations

import math
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


def _same_grid(left: rasterio.io.DatasetReader, right: rasterio.io.DatasetReader) -> bool:
    return (
        left.crs == right.crs
        and left.width == right.width
        and left.height == right.height
        and left.transform == right.transform
    )


def run(
    gradient_source: RasterInput,
    direction_source: RasterInput,
    parameters: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    conductivity = float(parameters["hydraulicConductivityMetersPerDay"])
    porosity = float(parameters["effectivePorosity"])
    if not math.isfinite(conductivity) or conductivity < 0:
        raise ValueError("PARAMETER_INVALID")
    if not math.isfinite(porosity) or not 0 < porosity <= 1:
        raise ValueError("PARAMETER_INVALID")
    with (
        rasterio.open(gradient_source.path) as gradient_ds,
        rasterio.open(direction_source.path) as direction_ds,
    ):
        validate_grid(gradient_ds)
        validate_grid(direction_ds)
        if not _same_grid(gradient_ds, direction_ds):
            raise ValueError("GRID_MISMATCH")
        gradient = gradient_ds.read(1, masked=True).astype(np.float64)
        direction = direction_ds.read(1, masked=True).astype(np.float64)
        valid = ~(np.ma.getmaskarray(gradient) | np.ma.getmaskarray(direction))
        valid &= np.isfinite(gradient.filled(np.nan)) & np.isfinite(direction.filled(np.nan))
        valid &= gradient.filled(-1) >= 0
        valid &= (direction.filled(-1) >= 0) & (direction.filled(360) < 360)
        nodata = float(gradient_ds.nodata) if gradient_ds.nodata is not None else -9999.0
        output_profile = profile(gradient_ds, nodata)
        flux = np.full(gradient.shape, nodata, dtype=np.float32)
        velocity = np.full(gradient.shape, nodata, dtype=np.float32)
        east = np.full(gradient.shape, nodata, dtype=np.float32)
        north = np.full(gradient.shape, nodata, dtype=np.float32)
        magnitude = conductivity * gradient.filled(0)
        seepage = magnitude / porosity
        radians = np.deg2rad(direction.filled(0))
        flux[valid] = magnitude[valid]
        velocity[valid] = seepage[valid]
        east[valid] = (seepage * np.sin(radians))[valid]
        north[valid] = (seepage * np.cos(radians))[valid]
    outputs = {
        "darcyFlux": ("darcy-flux.tif", flux),
        "seepageVelocity": ("seepage-velocity.tif", velocity),
        "velocityEast": ("velocity-east.tif", east),
        "velocityNorth": ("velocity-north.tif", north),
    }
    result = {
        port: write(output_dir / name, array, output_profile)
        for port, (name, array) in outputs.items()
    }
    result["summary"] = {
        "kind": "value",
        "value": {
            "validCellCount": int(np.count_nonzero(valid)),
            "velocityUnit": "m/day",
        },
    }
    return result
