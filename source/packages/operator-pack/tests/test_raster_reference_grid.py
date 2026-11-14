"""Numerical coverage for exact reference grids and GDAL resampling."""

from types import SimpleNamespace

import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine

from moperators.operators.raster_reference_grid import run


def _write(path, transform, values, *, crs="EPSG:3857", mask=None):
    height, width = values.shape
    with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True), rasterio.open(
        path, "w", driver="GTiff", width=width, height=height, count=1,
        dtype=values.dtype, transform=transform, crs=crs,
    ) as dataset:
        dataset.write(values, 1)
        if mask is not None:
            dataset.write_mask(mask)
    return [SimpleNamespace(path=path)]


@pytest.mark.parametrize(("method", "expected"), [("nearest", 26), ("bilinear", 25), ("cubic", 25)])
def test_sampling_on_a_rotated_template(tmp_path, method, expected):
    affine = Affine(12, 2, 1000, 1, -13, 5000)
    values = np.fromfunction(lambda row, column: 10 + row + 3 * column, (8, 8), dtype=np.int16)
    reference_affine = affine @ Affine.translation(3.75, 3.75)
    inputs = {
        "raster": _write(tmp_path / "source.tif", affine, values),
        "reference": _write(
            tmp_path / "reference.tif", reference_affine, np.zeros((1, 1), dtype="int16"),
        ),
    }
    result = run("raster.align_grid", inputs, {"resampling": method}, tmp_path / "outputs")
    with rasterio.open(result["raster"]["path"]) as raster:
        assert raster.transform == reference_affine
        assert raster.shape == (1, 1)
        assert raster.crs.to_epsg() == 3857
        assert raster.read(1).item() == pytest.approx(expected)


def test_internal_mask_nonfinite_and_uncovered_cells(tmp_path):
    affine = Affine(10, 0, 1000, 0, -10, 3000)
    inputs = {
        "raster": _write(
            tmp_path / "source.tif", affine, np.array([[0, 7], [np.inf, -3]], dtype="float32"),
            mask=np.array([[255, 0], [255, 255]], dtype="uint8"),
        ),
        "reference": _write(
            tmp_path / "reference.tif", affine, np.zeros((2, 3), dtype="uint8"),
        ),
    }
    result = run("raster.align_grid", inputs, {"resampling": "nearest"}, tmp_path)
    with rasterio.open(result["raster"]["path"]) as raster:
        assert np.isnan(raster.nodata)
        values = raster.read(1, masked=True)
        assert values.mask.tolist() == [[False, True, True], [True, False, True]]
        assert values.compressed().tolist() == [0, -3]


def test_grid_alignment_rejects_different_crs(tmp_path):
    affine = Affine(10, 0, 1000, 0, -10, 3000)
    values = np.zeros((2, 2), dtype="uint8")
    inputs = {
        "raster": _write(tmp_path / "source.tif", affine, values),
        "reference": _write(tmp_path / "reference.tif", affine, values, crs="EPSG:32650"),
    }
    with pytest.raises(ValueError, match=r"^CRS_MISMATCH$"):
        run("raster.align_grid", inputs, {"resampling": "nearest"}, tmp_path)
    with pytest.raises(ValueError, match=r"^RESAMPLING_UNSUPPORTED$"):
        run("raster.reproject", inputs, {"resampling": "average"}, tmp_path)
