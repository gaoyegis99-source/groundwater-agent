"""Warp a selected source band onto the immutable reference grid."""

import warnings
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.errors import NotGeoreferencedWarning, RasterioIOError, WarpOperationError
from rasterio.warp import reproject
from rasterio.windows import Window


def _read_grid(stack, path):
    with warnings.catch_warnings(record=True) as notices:
        warnings.simplefilter("always", NotGeoreferencedWarning)
        dataset = stack.enter_context(rasterio.open(path, driver="GTiff"))
    if dataset.crs is None:
        raise ValueError("CRS_REQUIRED")
    affine = dataset.transform
    if (
        any(isinstance(notice.message, NotGeoreferencedWarning) for notice in notices)
        or not np.all(np.isfinite(tuple(affine)[:6]))
        or affine.determinant == 0
    ):
        raise ValueError("GRID_REQUIRED")
    return dataset


def _profile(dataset):
    return {
        "driver": "GTiff", "crs": dataset.crs, "transform": dataset.transform,
        "width": dataset.width, "height": dataset.height,
        "count": 1, "dtype": "float64", "nodata": np.nan,
        "tiled": True, "compress": "deflate", "BIGTIFF": "IF_SAFER",
    }


def _copy_masked_band(source, band, path):
    # Normalize data and masks in bounded windows before the GDAL warp.
    # A band-level NoData tag, internal mask, or alpha band may define validity.
    with rasterio.open(path, "w", **_profile(source)) as normalized:
        for row in range(0, source.height, 512):
            for column in range(0, source.width, 512):
                window = Window(
                    column, row, min(512, source.width - column), min(512, source.height - row),
                )
                values = source.read(band, window=window, masked=True, out_dtype="float64")
                values = values.filled(np.nan)
                values[~np.isfinite(values)] = np.nan
                normalized.write(values, 1, window=window)


def run(operator_id, inputs, parameters, output_dir: Path):
    band = parameters.get("band", 1)
    resampling = parameters.get("resampling")
    if resampling not in {"nearest", "bilinear", "cubic"}:
        raise ValueError("RESAMPLING_UNSUPPORTED")
    output = output_dir / "reference-grid.tif"
    try:
        with ExitStack() as stack:
            stack.enter_context(rasterio.Env(
                GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_PAM_ENABLED="NO", PROJ_NETWORK="OFF",
            ))
            source = _read_grid(stack, inputs["raster"][0].path)
            reference = _read_grid(stack, inputs["reference"][0].path)
            if operator_id == "raster.align_grid" and source.crs != reference.crs:
                raise ValueError("CRS_MISMATCH")
            if band not in source.indexes:
                raise ValueError("BAND_INVALID")
            if np.dtype(source.dtypes[band - 1]).kind not in "iuf":
                raise ValueError("NODATA_MASK_UNSUPPORTED")
            output_dir.mkdir(parents=True, exist_ok=True)
            scratch = Path(stack.enter_context(TemporaryDirectory(dir=output_dir)))
            normal_path = scratch / "normalized.tif"
            _copy_masked_band(source, band, normal_path)
            normalized = stack.enter_context(rasterio.open(normal_path, driver="GTiff"))
            with rasterio.open(output, "w", **_profile(reference)) as target:
                area_or_point = reference.tags().get("AREA_OR_POINT")
                if area_or_point:
                    target.update_tags(AREA_OR_POINT=area_or_point)
                reproject(
                    rasterio.band(normalized, 1), rasterio.band(target, 1),
                    src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling[resampling],
                    init_dest_nodata=True, num_threads=1, warp_mem_limit=64,
                )
            with rasterio.open(output, driver="GTiff") as result:
                expected_grid = reference.crs, reference.transform, reference.shape
                if (result.crs, result.transform, result.shape) != expected_grid:
                    raise ValueError("GRID_MISMATCH")
    except (RasterioIOError, WarpOperationError) as error:
        raise ValueError("RASTER_WARP_FAILED") from error
    return {"raster": {
        "kind": "artifact", "path": str(output), "fileName": output.name,
        "mediaType": "image/tiff", "format": "geotiff",
    }}
