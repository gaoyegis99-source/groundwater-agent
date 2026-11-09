from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.windows import Window

from moperators.operators.extension_common import summary


def run(inputs: dict[str, Any], parameters: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    rasters = inputs.get("rasters", ())
    if not 2 <= len(rasters) <= 16:
        raise ValueError("INPUT_CARDINALITY_INVALID")
    band = int(parameters.get("band", 1))
    statistic = parameters["statistic"]
    if statistic not in {"min", "max", "mean", "median", "sum", "populationStddev"}:
        raise ValueError("PARAMETER_INVALID")
    minimum = int(parameters.get("minimumValidInputs", 1))
    if minimum < 1 or minimum > len(rasters):
        raise ValueError("PARAMETER_INVALID")
    output = output_dir / "cell-statistics.tif"
    valid_cells = 0
    nodata_cells = 0
    with ExitStack() as stack:
        datasets = [stack.enter_context(rasterio.open(item.path)) for item in rasters]
        reference = datasets[0]
        if reference.crs is None:
            raise ValueError("CRS_REQUIRED")
        grid = (reference.crs, reference.width, reference.height, reference.transform)
        for dataset in datasets:
            if dataset.crs is None:
                raise ValueError("CRS_REQUIRED")
            if (dataset.crs, dataset.width, dataset.height, dataset.transform) != grid:
                raise ValueError("GRID_MISMATCH")
            if band < 1 or band > dataset.count:
                raise ValueError("BAND_INVALID")
        profile = reference.profile.copy()
        profile.update(count=1, dtype="float32", nodata=np.nan)
        with rasterio.open(output, "w", **profile) as target:
            for row_offset in range(0, reference.height, 512):
                height = min(512, reference.height - row_offset)
                for column_offset in range(0, reference.width, 512):
                    width = min(512, reference.width - column_offset)
                    window = Window.from_slices(
                        (row_offset, row_offset + height),
                        (column_offset, column_offset + width),
                    )
                    arrays = [
                        dataset.read(band, window=window, masked=True)
                        for dataset in datasets
                    ]
                    values = np.stack(
                        [np.asarray(array.filled(np.nan), dtype=np.float64) for array in arrays]
                    )
                    count = np.sum(np.isfinite(values), axis=0)
                    with np.errstate(invalid="ignore", divide="ignore"):
                        calculated = _calculate(values, statistic)
                    calculated[count < minimum] = np.nan
                    valid_cells += int(np.sum(count >= minimum))
                    nodata_cells += int(np.sum(count < minimum))
                    target.write(calculated.astype(np.float32), 1, window=window)
    return {
        "raster": {
            "kind": "artifact",
            "path": str(output),
            "fileName": output.name,
            "mediaType": "image/tiff",
            "format": "geotiff",
        },
        "summary": summary(
            {
                "inputCount": len(rasters),
                "validCellCount": valid_cells,
                "noDataCellCount": nodata_cells,
            }
        ),
    }


def _calculate(values: np.ndarray, statistic: str) -> np.ndarray:
    if statistic == "min":
        return np.nanmin(values, axis=0)
    if statistic == "max":
        return np.nanmax(values, axis=0)
    if statistic == "mean":
        return np.nanmean(values, axis=0)
    if statistic == "median":
        return np.nanmedian(values, axis=0)
    if statistic == "sum":
        return np.nansum(values, axis=0)
    return np.nanstd(values, axis=0)
