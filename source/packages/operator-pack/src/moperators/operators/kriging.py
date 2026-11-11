from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from math import ceil, isfinite, sqrt
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

NODATA = -9999.0
MAX_GRID_CELLS = 4_000_000
SUPPORTED_TABLE_FORMATS = {"csv", "json", "parquet", "arrow-ipc-file"}
VARIOGRAM_PARAMETER_NAMES = {
    "variogramRange",
    "variogramSill",
    "variogramNugget",
}


@dataclass(frozen=True)
class Variogram:
    model: str
    range_value: float
    sill: float
    nugget: float
    fit_source: str
    fit_sse: float

    @property
    def partial_sill(self) -> float:
        return self.sill - self.nugget


@dataclass(frozen=True)
class Grid:
    min_x: float
    min_y: float
    max_x: float
    max_y: float
    pixel_size: float
    width: int
    height: int


@dataclass
class KrigingContext:
    coordinates: NDArray[np.float64]
    values: NDArray[np.float64]
    variogram: Variogram
    kriging_type: str
    neighbor_count: int
    minimum_neighbors: int
    search_radius: float | None
    exact: bool
    drift_center: NDArray[np.float64]
    drift_scale: NDArray[np.float64]
    regularization: float
    condition_numbers: list[float] = field(default_factory=list)
    system_cache: dict[tuple[int, ...], NDArray[np.float64]] = field(default_factory=dict)


def _artifact(path: Path, media_type: str, format_id: str) -> dict[str, Any]:
    return {
        "kind": "artifact",
        "path": str(path),
        "fileName": path.name,
        "mediaType": media_type,
        "format": format_id,
    }


def _value(value: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "value", "value": value}


def _load_rows(item: Any) -> list[dict[str, Any]]:
    if item.format_id not in SUPPORTED_TABLE_FORMATS:
        raise ValueError("INPUT_FORMAT_UNSUPPORTED")
    if item.format_id == "csv":
        with item.path.open(encoding="utf-8", newline="") as stream:
            return [dict(row) for row in csv.DictReader(stream)]
    if item.format_id == "json":
        value = json.loads(item.path.read_text(encoding="utf-8"))
        if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
            raise ValueError("INPUT_INVALID")
        return value
    try:
        import pyarrow.ipc as arrow_ipc
        import pyarrow.parquet as parquet
    except ImportError as exc:
        raise ValueError("BLOCKED_BY_ENVIRONMENT") from exc
    if item.format_id == "parquet":
        return parquet.read_table(item.path).to_pylist()
    with arrow_ipc.open_file(item.path) as reader:
        return reader.read_all().to_pylist()


def _null_values(item: Any) -> frozenset[str]:
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    contract = metadata.get("dataContract", {})
    values = contract.get("nullValues", []) if isinstance(contract, dict) else []
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise ValueError("INPUT_METADATA_INVALID")
    return frozenset(values)


def _number(value: Any, null_values: frozenset[str]) -> float:
    if value is None or (
        isinstance(value, str)
        and (not value.strip() or value in null_values or value.strip() in null_values)
    ):
        raise ValueError("NON_NUMERIC_VALUE")
    if isinstance(value, bool):
        raise ValueError("NON_NUMERIC_VALUE")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("NON_NUMERIC_VALUE") from exc
    if not isfinite(result):
        raise ValueError("NON_NUMERIC_VALUE")
    return result


def _read_points(
    item: Any, parameters: dict[str, Any]
) -> tuple[NDArray[np.float64], NDArray[np.float64], str]:
    rows = _load_rows(item)
    if not rows:
        raise ValueError("EMPTY_INTERPOLATION_INPUT")
    fields = (parameters["xField"], parameters["yField"], parameters["valueField"])
    if any(any(field not in row for field in fields) for row in rows):
        raise ValueError("FIELD_REQUIRED")
    null_values = _null_values(item)
    raw_coordinates = np.asarray(
        [
            [
                _number(row[parameters["xField"]], null_values),
                _number(row[parameters["yField"]], null_values),
            ]
            for row in rows
        ],
        dtype=np.float64,
    )
    values = np.asarray(
        [_number(row[parameters["valueField"]], null_values) for row in rows],
        dtype=np.float64,
    )
    try:
        from pyproj import CRS, Transformer

        source_crs = CRS.from_user_input(parameters["sourceCrs"])
        target_crs = CRS.from_user_input(parameters["targetCrs"])
    except (ImportError, TypeError, ValueError) as exc:
        if isinstance(exc, ImportError):
            raise ValueError("BLOCKED_BY_ENVIRONMENT") from exc
        raise ValueError("CRS_INVALID") from exc
    metric_axes = target_crs.axis_info[:2]
    if (
        not target_crs.is_projected
        or len(metric_axes) < 2
        or any(
            axis.unit_conversion_factor is None
            or not np.isclose(float(axis.unit_conversion_factor), 1.0)
            for axis in metric_axes
        )
    ):
        raise ValueError("PROJECTED_CRS_REQUIRED")
    try:
        transformer = Transformer.from_crs(source_crs, target_crs, always_xy=True)
        transformed_x, transformed_y = transformer.transform(
            raw_coordinates[:, 0], raw_coordinates[:, 1]
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("CRS_TRANSFORM_FAILED") from exc
    coordinates = np.column_stack((transformed_x, transformed_y)).astype(np.float64, copy=False)
    if not np.all(np.isfinite(coordinates)):
        raise ValueError("CRS_TRANSFORM_FAILED")
    if len(np.unique(coordinates, axis=0)) != len(coordinates):
        raise ValueError("DUPLICATE_COORDINATES")
    if len(coordinates) < 4:
        raise ValueError("INSUFFICIENT_INTERPOLATION_POINTS")
    return coordinates, values, str(metric_axes[0].unit_name or "metre")


def _pairwise_distances(coordinates: NDArray[np.float64]) -> NDArray[np.float64]:
    deltas = coordinates[:, None, :] - coordinates[None, :, :]
    return np.sqrt(np.sum(deltas * deltas, axis=2))


def _correlation(model: str, distances: NDArray[np.float64], range_value: float):
    scaled = np.asarray(distances, dtype=np.float64) / range_value
    if model == "spherical":
        return np.where(
            scaled < 1.0,
            1.0 - 1.5 * scaled + 0.5 * scaled**3,
            0.0,
        )
    if model == "exponential":
        return np.exp(-3.0 * scaled)
    if model == "gaussian":
        return np.exp(-3.0 * scaled**2)
    raise ValueError("VARIOGRAM_MODEL_INVALID")


def _semivariogram_shape(
    model: str, distances: NDArray[np.float64], range_value: float
) -> NDArray[np.float64]:
    return 1.0 - _correlation(model, distances, range_value)


def _empirical_variogram(
    coordinates: NDArray[np.float64],
    values: NDArray[np.float64],
    bin_count: int,
) -> tuple[NDArray[np.float64], NDArray[np.float64], list[dict[str, Any]]]:
    row_indices, column_indices = np.triu_indices(len(coordinates), 1)
    distances = np.linalg.norm(coordinates[row_indices] - coordinates[column_indices], axis=1)
    semivariances = 0.5 * (values[row_indices] - values[column_indices]) ** 2
    maximum = float(np.max(distances))
    if maximum <= 0:
        raise ValueError("DUPLICATE_COORDINATES")
    edges = np.linspace(0.0, maximum, bin_count + 1)
    bins: list[dict[str, Any]] = []
    for index in range(bin_count):
        lower = float(edges[index])
        upper = float(edges[index + 1])
        if index == 0:
            selected = (distances >= lower) & (distances <= upper)
        else:
            selected = (distances > lower) & (distances <= upper)
        count = int(np.count_nonzero(selected))
        bins.append(
            {
                "lowerDistance": lower,
                "upperDistance": upper,
                "pairCount": count,
                "meanDistance": (float(np.mean(distances[selected])) if count else None),
                "meanSemivariance": (float(np.mean(semivariances[selected])) if count else None),
            }
        )
    return distances, semivariances, bins


def _fit_variogram(
    model: str,
    distances: NDArray[np.float64],
    semivariances: NDArray[np.float64],
    *,
    exact: bool,
    parameters: dict[str, Any],
) -> Variogram:
    present = VARIOGRAM_PARAMETER_NAMES & parameters.keys()
    if present and present != VARIOGRAM_PARAMETER_NAMES:
        raise ValueError("VARIOGRAM_PARAMETERS_INCOMPLETE")
    if present:
        range_value = float(parameters["variogramRange"])
        sill = float(parameters["variogramSill"])
        nugget = float(parameters["variogramNugget"])
        if (
            not all(isfinite(value) for value in (range_value, sill, nugget))
            or range_value <= 0
            or sill <= 0
            or nugget < 0
            or nugget >= sill
        ):
            raise ValueError("VARIOGRAM_PARAMETERS_INVALID")
        fitted = nugget + (sill - nugget) * _semivariogram_shape(model, distances, range_value)
        return Variogram(
            model,
            range_value,
            sill,
            nugget,
            "user-specified",
            float(np.sum((semivariances - fitted) ** 2)),
        )

    positive = distances[distances > 0]
    minimum = float(np.min(positive))
    maximum = float(np.max(positive))
    lower = max(minimum * 0.5, maximum / 1000.0)
    upper = max(maximum * 2.0, lower * 1.01)
    candidates = np.geomspace(lower, upper, 48)
    best: tuple[float, float, float, float] | None = None
    for range_value in candidates:
        shape = _semivariogram_shape(model, distances, float(range_value))
        if exact:
            denominator = float(np.dot(shape, shape))
            partial_sill = (
                max(0.0, float(np.dot(shape, semivariances)) / denominator)
                if denominator > 0
                else 0.0
            )
            nugget = 0.0
        else:
            design = np.column_stack((np.ones_like(shape), shape))
            coefficients = np.linalg.lstsq(design, semivariances, rcond=None)[0]
            nugget = max(0.0, float(coefficients[0]))
            partial_sill = max(0.0, float(coefficients[1]))
        fitted = nugget + partial_sill * shape
        score = float(np.sum((semivariances - fitted) ** 2))
        candidate = (score, float(range_value), nugget, partial_sill)
        if best is None or candidate < best:
            best = candidate
    assert best is not None
    score, range_value, nugget, partial_sill = best
    value_scale = max(float(np.var(semivariances)), float(np.var(semivariances) ** 0.5), 1.0)
    minimum_sill = value_scale * 1e-12
    if partial_sill <= minimum_sill:
        partial_sill = max(float(np.var(semivariances)), minimum_sill)
        nugget = 0.0 if exact else min(nugget, partial_sill * 0.5)
    return Variogram(
        model,
        range_value,
        nugget + partial_sill,
        nugget,
        "deterministic-least-squares",
        score,
    )


def _drift_matrix(coordinates: NDArray[np.float64], context: KrigingContext) -> NDArray[np.float64]:
    if context.kriging_type == "ordinary":
        return np.ones((len(coordinates), 1), dtype=np.float64)
    normalized = (coordinates - context.drift_center) / context.drift_scale
    return np.column_stack((np.ones(len(coordinates)), normalized))


def _neighbor_indices(
    context: KrigingContext,
    target: NDArray[np.float64],
    *,
    exclude: int | None,
) -> tuple[NDArray[np.int64], NDArray[np.float64]]:
    distances = np.linalg.norm(context.coordinates - target, axis=1)
    candidates = np.ones(len(distances), dtype=bool)
    if exclude is not None:
        candidates[exclude] = False
    if context.search_radius is not None:
        candidates &= distances <= context.search_radius
    indices = np.flatnonzero(candidates)
    if len(indices):
        order = np.argsort(distances[indices], kind="stable")
        indices = indices[order[: context.neighbor_count]]
    return indices.astype(np.int64, copy=False), distances


def _system_inverse(context: KrigingContext, indices: NDArray[np.int64]) -> NDArray[np.float64]:
    key = tuple(int(value) for value in indices)
    cached = context.system_cache.get(key)
    if cached is not None:
        return cached
    coordinates = context.coordinates[indices]
    distances = _pairwise_distances(coordinates)
    covariance = context.variogram.partial_sill * _correlation(
        context.variogram.model,
        distances,
        context.variogram.range_value,
    )
    covariance[np.diag_indices_from(covariance)] += (
        context.variogram.nugget + context.regularization
    )
    drift = _drift_matrix(coordinates, context)
    if np.linalg.matrix_rank(drift) < drift.shape[1]:
        raise ValueError("DRIFT_RANK_DEFICIENT")
    size = len(indices) + drift.shape[1]
    system = np.zeros((size, size), dtype=np.float64)
    system[: len(indices), : len(indices)] = covariance
    system[: len(indices), len(indices) :] = drift
    system[len(indices) :, : len(indices)] = drift.T
    condition = float(np.linalg.cond(system))
    context.condition_numbers.append(condition)
    try:
        inverse = np.linalg.inv(system)
    except np.linalg.LinAlgError:
        inverse = np.linalg.pinv(system, rcond=1e-12)
    if not np.all(np.isfinite(inverse)):
        raise ValueError("KRIGING_NUMERICAL_FAILURE")
    context.system_cache[key] = inverse
    return inverse


def _predict(
    context: KrigingContext,
    target: NDArray[np.float64],
    *,
    exclude: int | None = None,
) -> tuple[float, float] | None:
    indices, distances = _neighbor_indices(context, target, exclude=exclude)
    if len(indices) < context.minimum_neighbors:
        return None
    if exclude is None and context.exact:
        coincident = indices[distances[indices] <= max(context.regularization, 1e-12)]
        if len(coincident):
            return float(context.values[coincident[0]]), 0.0
    local_coordinates = context.coordinates[indices]
    drift = _drift_matrix(local_coordinates, context)
    if np.linalg.matrix_rank(drift) < drift.shape[1]:
        return None
    target_distances = np.linalg.norm(local_coordinates - target, axis=1)
    covariance = context.variogram.partial_sill * _correlation(
        context.variogram.model,
        target_distances,
        context.variogram.range_value,
    )
    target_drift = _drift_matrix(target.reshape(1, 2), context)[0]
    right_hand_side = np.concatenate((covariance, target_drift))
    solution = _system_inverse(context, indices) @ right_hand_side
    weights = solution[: len(indices)]
    multipliers = solution[len(indices) :]
    prediction = float(np.dot(weights, context.values[indices]))
    variance = float(
        context.variogram.sill - np.dot(weights, covariance) - np.dot(multipliers, target_drift)
    )
    tolerance = max(context.variogram.sill, 1.0) * 1e-8
    if not isfinite(prediction) or not isfinite(variance) or variance < -tolerance:
        raise ValueError("KRIGING_NUMERICAL_FAILURE")
    return prediction, max(0.0, variance)


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    evaluated = [row for row in rows if row["status"] == "EVALUATED"]
    residuals = np.asarray([row["residual"] for row in evaluated], dtype=np.float64)
    standardized = np.asarray(
        [
            row["standardizedResidual"]
            for row in evaluated
            if row["standardizedResidual"] is not None
        ],
        dtype=np.float64,
    )
    return {
        "evaluatedCount": len(evaluated),
        "notEvaluatedCount": len(rows) - len(evaluated),
        "meanError": float(np.mean(residuals)) if residuals.size else None,
        "rmse": float(np.sqrt(np.mean(residuals**2))) if residuals.size else None,
        "meanStandardizedResidual": (float(np.mean(standardized)) if standardized.size else None),
        "rmsse": (float(np.sqrt(np.mean(standardized**2))) if standardized.size else None),
        "residualConvention": "observed-minus-predicted",
    }


def _idw_loo(context: KrigingContext, power: float) -> dict[str, Any]:
    residuals: list[float] = []
    for index, target in enumerate(context.coordinates):
        indices, distances = _neighbor_indices(context, target, exclude=index)
        if len(indices) < context.minimum_neighbors:
            continue
        local_distances = distances[indices]
        weights = 1.0 / np.power(local_distances, power)
        prediction = float(np.dot(weights, context.values[indices]) / np.sum(weights))
        residuals.append(float(context.values[index] - prediction))
    values = np.asarray(residuals, dtype=np.float64)
    return {
        "evaluatedCount": len(residuals),
        "meanError": float(np.mean(values)) if values.size else None,
        "rmse": float(np.sqrt(np.mean(values**2))) if values.size else None,
        "power": power,
        "residualConvention": "observed-minus-predicted",
        "interpretation": (
            "Same-point-set LOO diagnostic; Kriging is not guaranteed to outperform "
            "IDW on every dataset."
        ),
    }


def _leave_one_out(context: KrigingContext) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, target in enumerate(context.coordinates):
        result = _predict(context, target, exclude=index)
        if result is None:
            rows.append(
                {
                    "index": index,
                    "x": float(target[0]),
                    "y": float(target[1]),
                    "observed": float(context.values[index]),
                    "predicted": None,
                    "residual": None,
                    "krigingVariance": None,
                    "standardError": None,
                    "standardizedResidual": None,
                    "status": "INSUFFICIENT_NEIGHBORS",
                }
            )
            continue
        prediction, variance = result
        residual = float(context.values[index] - prediction)
        standard_error = sqrt(variance) if variance > 0 else 0.0
        rows.append(
            {
                "index": index,
                "x": float(target[0]),
                "y": float(target[1]),
                "observed": float(context.values[index]),
                "predicted": prediction,
                "residual": residual,
                "krigingVariance": variance,
                "standardError": standard_error,
                "standardizedResidual": (
                    residual / standard_error if standard_error > 1e-15 else None
                ),
                "status": "EVALUATED",
            }
        )
    return rows


def _training_exactness(context: KrigingContext) -> dict[str, Any]:
    residuals: list[float] = []
    for index, target in enumerate(context.coordinates):
        result = _predict(context, target)
        if result is None:
            continue
        residuals.append(float(context.values[index] - result[0]))
    values = np.asarray(residuals, dtype=np.float64)
    return {
        "evaluatedCount": len(residuals),
        "maxAbsoluteError": (float(np.max(np.abs(values))) if values.size else None),
        "rmse": float(np.sqrt(np.mean(values**2))) if values.size else None,
        "expectedExact": context.exact,
    }


def _grid(coordinates: NDArray[np.float64], parameters: dict[str, Any]) -> Grid:
    extent_names = ("extentMinX", "extentMinY", "extentMaxX", "extentMaxY")
    present = [name in parameters for name in extent_names]
    if any(present) and not all(present):
        raise ValueError("EXTENT_INCOMPLETE")
    if all(present):
        min_x, min_y, max_x, max_y = (float(parameters[name]) for name in extent_names)
    else:
        min_x, min_y = np.min(coordinates, axis=0)
        max_x, max_y = np.max(coordinates, axis=0)
        min_x, min_y, max_x, max_y = map(float, (min_x, min_y, max_x, max_y))
    pixel_size = float(parameters["pixelSize"])
    if (
        not all(isfinite(value) for value in (min_x, min_y, max_x, max_y, pixel_size))
        or pixel_size <= 0
        or max_x <= min_x
        or max_y <= min_y
    ):
        raise ValueError("EXTENT_INVALID")
    width = ceil((max_x - min_x) / pixel_size)
    height = ceil((max_y - min_y) / pixel_size)
    if width < 1 or height < 1 or width * height > MAX_GRID_CELLS:
        raise ValueError("GRID_TOO_LARGE")
    return Grid(min_x, min_y, max_x, max_y, pixel_size, width, height)


def _predict_grid(
    context: KrigingContext, grid: Grid
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    prediction = np.full((grid.height, grid.width), np.nan, dtype=np.float64)
    variance = np.full_like(prediction, np.nan)
    for row_index in range(grid.height):
        y_value = grid.max_y - (row_index + 0.5) * grid.pixel_size
        for column_index in range(grid.width):
            x_value = grid.min_x + (column_index + 0.5) * grid.pixel_size
            result = _predict(context, np.asarray([x_value, y_value], dtype=np.float64))
            if result is not None:
                prediction[row_index, column_index], variance[row_index, column_index] = result
    if not np.any(np.isfinite(prediction)):
        raise ValueError("INSUFFICIENT_INTERPOLATION_NEIGHBORS")
    return prediction, variance


def _predict_idw_grid(context: KrigingContext, grid: Grid, power: float) -> NDArray[np.float64]:
    prediction = np.full((grid.height, grid.width), np.nan, dtype=np.float64)
    for row_index in range(grid.height):
        y_value = grid.max_y - (row_index + 0.5) * grid.pixel_size
        for column_index in range(grid.width):
            x_value = grid.min_x + (column_index + 0.5) * grid.pixel_size
            target = np.asarray([x_value, y_value], dtype=np.float64)
            indices, distances = _neighbor_indices(context, target, exclude=None)
            if len(indices) < context.minimum_neighbors:
                continue
            local_distances = distances[indices]
            coincident = indices[local_distances <= 1e-12]
            if len(coincident):
                prediction[row_index, column_index] = context.values[coincident[0]]
                continue
            weights = 1.0 / np.power(local_distances, power)
            prediction[row_index, column_index] = float(
                np.dot(weights, context.values[indices]) / np.sum(weights)
            )
    return prediction


def _compare_idw_grid(
    kriging: NDArray[np.float64], idw: NDArray[np.float64], power: float
) -> dict[str, Any]:
    kriging_valid = np.isfinite(kriging)
    idw_valid = np.isfinite(idw)
    common = kriging_valid & idw_valid
    differences = kriging[common] - idw[common]
    return {
        "comparisonGrid": "same-grid",
        "gridWidth": kriging.shape[1],
        "gridHeight": kriging.shape[0],
        "commonCellCount": int(np.count_nonzero(common)),
        "krigingValidCellCount": int(np.count_nonzero(kriging_valid)),
        "idwValidCellCount": int(np.count_nonzero(idw_valid)),
        "krigingOnlyCellCount": int(np.count_nonzero(kriging_valid & ~idw_valid)),
        "idwOnlyCellCount": int(np.count_nonzero(idw_valid & ~kriging_valid)),
        "meanDifference": float(np.mean(differences)),
        "meanAbsoluteDifference": float(np.mean(np.abs(differences))),
        "rmseDifference": float(np.sqrt(np.mean(differences**2))),
        "maximumAbsoluteDifference": float(np.max(np.abs(differences))),
        "differenceConvention": "kriging-minus-idw",
        "idwPower": power,
        "interpretation": (
            "Same-grid surface diagnostic; Kriging is not guaranteed to outperform "
            "IDW on every dataset. Differences are not errors without independent truth."
        ),
    }


def _write_raster(
    path: Path,
    values: NDArray[np.float64],
    grid: Grid,
    target_crs: str,
    *,
    quantity: str,
) -> None:
    try:
        import rasterio
        from rasterio.transform import from_origin
    except ImportError as exc:
        raise ValueError("BLOCKED_BY_ENVIRONMENT") from exc
    finite = np.isfinite(values)
    encoded = np.where(finite, values, NODATA)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=grid.width,
        height=grid.height,
        count=1,
        dtype="float64",
        crs=target_crs,
        transform=from_origin(grid.min_x, grid.max_y, grid.pixel_size, grid.pixel_size),
        nodata=NODATA,
        compress="deflate",
        predictor=3,
    ) as dataset:
        dataset.write(encoded, 1)
        dataset.write_mask(finite.astype(np.uint8) * 255)
        dataset.update_tags(1, quantity=quantity)


def _write_loo(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "index",
        "x",
        "y",
        "observed",
        "predicted",
        "residual",
        "krigingVariance",
        "standardError",
        "standardizedResidual",
        "status",
    ]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    name: (
                        format(value, ".17g")
                        if isinstance(value, float)
                        else ""
                        if value is None
                        else value
                    )
                    for name, value in row.items()
                }
            )


def run_kriging(
    points: Any, parameters: dict[str, Any], output_dir: Path
) -> dict[str, dict[str, Any]]:
    coordinates, values, distance_unit = _read_points(points, parameters)
    kriging_type = parameters.get("krigingType", "ordinary")
    if kriging_type not in {"ordinary", "universal"}:
        raise ValueError("KRIGING_TYPE_INVALID")
    model = parameters.get("variogramModel", "spherical")
    if model not in {"spherical", "exponential", "gaussian"}:
        raise ValueError("VARIOGRAM_MODEL_INVALID")
    neighbor_count = int(parameters.get("neighborCount", 12))
    minimum_neighbors = int(parameters.get("minimumNeighbors", 4))
    if (
        neighbor_count < 1
        or minimum_neighbors < (3 if kriging_type == "universal" else 1)
        or minimum_neighbors > neighbor_count
    ):
        raise ValueError("NEIGHBOR_CONFIGURATION_INVALID")
    search_radius_value = parameters.get("searchRadius")
    search_radius = None if search_radius_value is None else float(search_radius_value)
    if search_radius is not None and (not isfinite(search_radius) or search_radius <= 0):
        raise ValueError("NEIGHBOR_CONFIGURATION_INVALID")
    idw_power = float(parameters.get("idwPower", 2.0))
    if not isfinite(idw_power) or idw_power <= 0:
        raise ValueError("IDW_POWER_INVALID")
    drift_center = np.mean(coordinates, axis=0)
    drift_scale = np.ptp(coordinates, axis=0)
    drift_scale[drift_scale == 0] = 1.0
    if kriging_type == "universal":
        drift = np.column_stack(
            (
                np.ones(len(coordinates)),
                (coordinates - drift_center) / drift_scale,
            )
        )
        if np.linalg.matrix_rank(drift) < 3:
            raise ValueError("DRIFT_RANK_DEFICIENT")
    bin_count = int(parameters.get("variogramBinCount", 10))
    if not 4 <= bin_count <= 30:
        raise ValueError("VARIOGRAM_BIN_COUNT_INVALID")
    distances, semivariances, empirical_bins = _empirical_variogram(coordinates, values, bin_count)
    exact = bool(parameters.get("exact", True))
    variogram = _fit_variogram(
        model,
        distances,
        semivariances,
        exact=exact,
        parameters=parameters,
    )
    context = KrigingContext(
        coordinates=coordinates,
        values=values,
        variogram=variogram,
        kriging_type=kriging_type,
        neighbor_count=min(neighbor_count, len(coordinates)),
        minimum_neighbors=minimum_neighbors,
        search_radius=search_radius,
        exact=exact,
        drift_center=drift_center,
        drift_scale=drift_scale,
        regularization=max(variogram.sill, 1.0) * 1e-12,
    )
    grid = _grid(coordinates, parameters)
    loo_rows = _leave_one_out(context)
    loo_metrics = _metrics(loo_rows)
    idw_comparison = _idw_loo(context, idw_power)
    training_exactness = _training_exactness(context)
    prediction, variance = _predict_grid(context, grid)
    idw_grid = _predict_idw_grid(context, grid, idw_power)
    idw_grid_comparison = _compare_idw_grid(prediction, idw_grid, idw_power)
    output_dir.mkdir(parents=True, exist_ok=True)
    prediction_path = output_dir / "kriging-prediction.tif"
    variance_path = output_dir / "kriging-variance.tif"
    loo_path = output_dir / "kriging-loo.csv"
    _write_raster(
        prediction_path,
        prediction,
        grid,
        parameters["targetCrs"],
        quantity="kriging-prediction",
    )
    _write_raster(
        variance_path,
        variance,
        grid,
        parameters["targetCrs"],
        quantity="kriging-variance",
    )
    _write_loo(loo_path, loo_rows)

    finite_variance = variance[np.isfinite(variance)]
    diagnostics = {
        "schemaVersion": "variogram-diagnostics.v1",
        "model": variogram.model,
        "fitSource": variogram.fit_source,
        "range": variogram.range_value,
        "sill": variogram.sill,
        "nugget": variogram.nugget,
        "partialSill": variogram.partial_sill,
        "fitSse": variogram.fit_sse,
        "pairCount": len(distances),
        "binCount": bin_count,
        "bins": empirical_bins,
        "distanceUnit": distance_unit,
        "targetCrs": parameters["targetCrs"],
        "exact": exact,
        "driftBasis": (["intercept"] if kriging_type == "ordinary" else ["intercept", "x", "y"]),
        "covarianceConvention": (
            "sill-minus-fitted-semivariogram with diagonal nugget and "
            "documented numerical regularization"
        ),
        "regularization": context.regularization,
    }
    summary = {
        "schemaVersion": "kriging-summary.v1",
        "krigingType": kriging_type,
        "drift": "constant" if kriging_type == "ordinary" else "linear-x-y",
        "exact": exact,
        "pointCount": len(coordinates),
        "valueField": parameters["valueField"],
        "valueUnit": parameters.get("valueUnit", ""),
        "sourceCrs": parameters["sourceCrs"],
        "targetCrs": parameters["targetCrs"],
        "distanceUnit": distance_unit,
        "grid": {
            "extent": [grid.min_x, grid.min_y, grid.max_x, grid.max_y],
            "pixelSize": grid.pixel_size,
            "width": grid.width,
            "height": grid.height,
            "validCellCount": int(np.count_nonzero(np.isfinite(prediction))),
            "noDataCellCount": int(np.count_nonzero(~np.isfinite(prediction))),
        },
        "neighbors": {
            "neighborCount": context.neighbor_count,
            "minimumNeighbors": context.minimum_neighbors,
            "searchRadius": context.search_radius,
        },
        "variogram": {
            "model": variogram.model,
            "range": variogram.range_value,
            "sill": variogram.sill,
            "nugget": variogram.nugget,
            "fitSource": variogram.fit_source,
        },
        "variance": {
            "kind": "model-based-kriging-variance",
            "minimum": float(np.min(finite_variance)),
            "maximum": float(np.max(finite_variance)),
            "nonnegative": bool(np.all(finite_variance >= 0)),
            "unit": (f"{parameters['valueUnit']}^2" if parameters.get("valueUnit") else None),
        },
        "trainingExactness": training_exactness,
        "loo": loo_metrics,
        "idwLooComparison": idw_comparison,
        "idwGridComparison": idw_grid_comparison,
        "maximumSystemConditionNumber": (
            max(context.condition_numbers) if context.condition_numbers else None
        ),
        "limitations": [
            "Kriging variance is conditional on the selected variogram, drift, neighbors, "
            "and input locations; it is not a complete confidence interval.",
            "Cells outside or near the edge of the observation network are spatial "
            "extrapolation and require separate review.",
            "LOO metrics assess internal point prediction only and are not external validation.",
            "The same-point-set IDW LOO comparison is diagnostic; Kriging is not "
            "guaranteed to outperform IDW on every dataset.",
        ],
    }
    return {
        "prediction": _artifact(prediction_path, "image/tiff", "geotiff"),
        "variance": _artifact(variance_path, "image/tiff", "geotiff"),
        "variogramDiagnostics": _value(diagnostics),
        "loo": _artifact(loo_path, "text/csv", "csv"),
        "summary": _value(summary),
    }
