"""Prepare a local, georeferenced GeoTIFF for the V6 raster viewer."""

import math
from pathlib import Path


def prepare_map(source: Path, job: Path) -> dict:
    import rasterio
    from rasterio.shutil import copy
    from rasterio.warp import transform_bounds

    with source.open("rb") as stream:
        if stream.read(4) not in {b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"}:
            raise ValueError("GEOTIFF_HEADER_INVALID")
    with rasterio.Env(PROJ_NETWORK="OFF", GDAL_PAM_ENABLED="NO", GDAL_TIFF_INTERNAL_MASK=True):
        with rasterio.open(source, driver="GTiff", sharing=False) as raster:
            if (
                raster.driver != "GTiff" or raster.crs is None
                or not 1 <= raster.count <= 4 or raster.width * raster.height > 8_000_000
            ):
                raise ValueError("GEOTIFF_UNSUPPORTED")
            crs = raster.crs
            bounds = transform_bounds(crs, "EPSG:4326", *raster.bounds)
            if not all(math.isfinite(value) for value in bounds) or bounds[1] >= bounds[3]:
                raise ValueError("GEOTIFF_BOUNDS_INVALID")
            copy(
                raster, job / "geospatial_output.geotiff",
                driver="GTiff", compress="deflate", tiled=True, interleave="band",
            )
            metadata = {
                "crs": crs.to_string(), "epsg": crs.to_epsg(), "wkt": crs.to_wkt(),
                "bounds": list(raster.bounds), "boundsWgs84": list(bounds),
                "transform": list(raster.transform)[:6], "width": raster.width,
                "height": raster.height, "bands": [
                    {
                        "number": band, "dtype": dtype,
                        "nodata": nodata if nodata is None or math.isfinite(nodata) else str(nodata),
                        "unit": unit, "scale": scale, "offset": offset,
                    }
                    for band, dtype, nodata, unit, scale, offset in zip(
                        raster.indexes, raster.dtypes, raster.nodatavals, raster.units,
                        raster.scales, raster.offsets, strict=True,
                    )
                ],
                "source": "用户提交的本地 GeoTIFF",
            }
    return {"crs_attribution_metadata": metadata}
