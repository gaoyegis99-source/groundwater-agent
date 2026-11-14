"""Read physical raster facts from the exact immutable TIFF object."""

from hashlib import sha256
from math import isfinite, isnan
import warnings

import rasterio
from rasterio.errors import NotGeoreferencedWarning, RasterioIOError


def inspect_raster(path, checksum):
    # Restrict identification to TIFF and suppress auxiliary files. Only the
    # published TIFF bytes belong to the Data object and its checksum.
    try:
        with warnings.catch_warnings(record=True) as notices:
            warnings.simplefilter("always", NotGeoreferencedWarning)
            return _describe(path, checksum, notices)
    except RasterioIOError:
        return None


def _describe(path, checksum, notices):
    with (
        rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_PAM_ENABLED="NO", PROJ_NETWORK="OFF"),
        rasterio.open(path, driver="GTiff") as raster,
    ):
        transform = raster.transform.to_gdal()
        georeferenced = not any(isinstance(n.message, NotGeoreferencedWarning) for n in notices)
        crs = raster.crs
        authority = crs.to_authority(confidence_threshold=100) if crs else None
        crs_id = None
        if crs:
            crs_id = ":".join(authority) if authority else "wkt:sha256:" + sha256(crs.to_wkt().encode()).hexdigest()
        grid = None
        if crs_id and georeferenced and all(map(isfinite, transform)) and (
            transform[1] * transform[5] != transform[2] * transform[4]
        ):
            grid = {
                "crs": crs_id, "width": raster.width, "height": raster.height,
                "transform": list(transform),
            }
        return {
            "source_checksum": checksum,
            "crs": crs_id,
            "grid": grid,
            "bands": [
                {"mask_flags": sorted(flag.name for flag in flags), "dtype": dtype,
                 "nodata": value if value is None or isfinite(value) else "nan" if isnan(value) else "infinite"}
                for dtype, value, flags in zip(raster.dtypes, raster.nodatavals, raster.mask_flag_enums, strict=True)
            ],
        }
