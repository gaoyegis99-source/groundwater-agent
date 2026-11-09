from __future__ import annotations

from pathlib import Path
from typing import Any

from moperators.operators.native import StagedInput, _artifact


def _layer_source(item: StagedInput, layer: str | None) -> str:
    return f"{item.path}|layername={layer}" if layer else str(item.path)


def _run_algorithm(algorithm: str, parameters: dict[str, Any]) -> dict[str, Any]:
    try:
        import processing  # ty: ignore[unresolved-import]
        from processing.core.Processing import Processing  # ty: ignore[unresolved-import]
        from qgis.core import QgsApplication, QgsProcessingFeedback  # ty: ignore[unresolved-import]
    except (ImportError, ModuleNotFoundError) as exc:
        raise ValueError("BLOCKED_BY_ENVIRONMENT") from exc
    application = QgsApplication([], False)
    application.initQgis()
    try:
        Processing.initialize()
        return processing.run(algorithm, parameters, feedback=QgsProcessingFeedback())
    finally:
        application.exitQgis()


def _vector_output(path: Path) -> dict[str, dict[str, Any]]:
    return {"vector": _artifact(path, "application/geopackage+sqlite3", "geopackage")}


def _reject_dimensions(item: StagedInput) -> None:
    if item.metadata.get("hasZ") or item.metadata.get("hasM"):
        raise ValueError("UNSUPPORTED_DIMENSION")


def _float32_dem(source: Path, target: Path) -> None:
    try:
        import numpy as np
        import rasterio
    except ImportError as exc:
        raise ValueError("BLOCKED_BY_ENVIRONMENT") from exc
    with rasterio.open(source) as dataset:
        profile = dataset.profile.copy()
        profile.update(dtype="float32")
        with rasterio.open(target, "w", **profile) as output:
            for band in dataset.indexes:
                for _, window in dataset.block_windows(band):
                    output.write(
                        dataset.read(band, window=window).astype(np.float32),
                        band,
                        window=window,
                    )


def _merge_sources(items: list[StagedInput]) -> list[str]:
    try:
        import pyogrio
    except ImportError as exc:
        raise ValueError("BLOCKED_BY_ENVIRONMENT") from exc
    schemas: dict[str, str] = {}
    crs: str | None = None
    geometry_family: str | None = None
    dimensions: tuple[bool, bool] | None = None
    sources: list[str] = []
    for item in items:
        source = str(item.path)
        info = pyogrio.read_info(item.path)
        item_crs = str(info.get("crs") or item.metadata.get("crs") or "")
        item_geometry = str(info.get("geometry_type") or "").removeprefix("3D ")
        item_geometry = item_geometry.removeprefix("Multi")
        item_dimensions = (
            bool(item.metadata.get("hasZ")),
            bool(item.metadata.get("hasM")),
        )
        if crs is not None and item_crs != crs:
            raise ValueError("CRS_MISMATCH")
        if geometry_family is not None and item_geometry != geometry_family:
            raise ValueError("GEOMETRY_TYPE_MISMATCH")
        if dimensions is not None and item_dimensions != dimensions:
            raise ValueError("UNSUPPORTED_DIMENSION")
        crs = item_crs
        geometry_family = item_geometry
        dimensions = item_dimensions
        for field, dtype in zip(info.get("fields", []), info.get("dtypes", []), strict=True):
            name, field_type = str(field), str(dtype)
            if name in schemas and schemas[name] != field_type:
                raise ValueError("SCHEMA_MISMATCH")
            schemas[name] = field_type
        sources.append(source)
    return sources


def run_qgis_operator(
    operator_id: str,
    inputs: dict[str, list[StagedInput]],
    parameters: dict[str, Any],
    output_dir: Path,
) -> dict[str, dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    if operator_id == "hydrology.fill_sinks":
        source = inputs["dem"][0]
        float_dem = output_dir / "float32-dem.tif"
        output = output_dir / "filled-dem.tif"
        _float32_dem(source.path, float_dem)
        _run_algorithm(
            "native:fillsinkswangliu",
            {
                "INPUT": str(float_dem),
                "BAND": parameters.get("band", 1),
                "MIN_SLOPE": parameters.get("minimumSlopeDegrees", 0.1),
                "OUTPUT_FILLED_DEM": str(output),
                "OUTPUT_FLOW_DIRECTIONS": str(
                    output_dir / "temporary-flow-directions.tif"
                ),
                "OUTPUT_WATERSHED_BASINS": str(
                    output_dir / "temporary-watershed-basins.tif"
                ),
            },
        )
        return {"dem": _artifact(output, "image/tiff", "geotiff")}
    if operator_id == "geometry.validate":
        source = inputs["vector"][0]
        report = output_dir / "geometry-errors.gpkg"
        _run_algorithm(
            "qgis:checkvalidity",
            {
                "INPUT_LAYER": _layer_source(source, parameters.get("layer")),
                "METHOD": 2,
                "IGNORE_RING_SELF_INTERSECTION": False,
                "ERROR_OUTPUT": str(report),
                "INVALID_OUTPUT": "TEMPORARY_OUTPUT",
                "VALID_OUTPUT": "TEMPORARY_OUTPUT",
            },
        )
        try:
            import pyogrio

            error_count = int(pyogrio.read_info(report).get("features") or 0)
        except ImportError as exc:
            raise ValueError("BLOCKED_BY_ENVIRONMENT") from exc
        return {
            "report": _artifact(report, "application/geopackage+sqlite3", "geopackage"),
            "summary": {
                "kind": "value",
                "value": {"valid": error_count == 0, "errorCount": error_count},
            },
        }
    if operator_id == "vector.overlay":
        left, right = inputs["left"][0], inputs["right"][0]
        if any(item.metadata.get("hasZ") or item.metadata.get("hasM") for item in (left, right)):
            raise ValueError("UNSUPPORTED_DIMENSION")
        if left.metadata.get("crs") != right.metadata.get("crs"):
            raise ValueError("CRS_MISMATCH")
        algorithms = {
            "intersection": "native:intersection",
            "union": "native:union",
            "difference": "native:difference",
            "symmetricDifference": "native:symmetricaldifference",
        }
        output = output_dir / "overlay.gpkg"
        _run_algorithm(
            algorithms[parameters["operation"]],
            {
                "INPUT": _layer_source(left, parameters.get("leftLayer")),
                "OVERLAY": _layer_source(right, parameters.get("rightLayer")),
                "OUTPUT": str(output),
            },
        )
        return _vector_output(output)
    if operator_id in {
        "vector.dissolve",
        "vector.simplify",
        "vector.centroid",
        "vector.convex_hull",
    }:
        source = inputs["vector"][0]
        _reject_dimensions(source)
        output_names = {
            "vector.dissolve": "dissolved.gpkg",
            "vector.simplify": "simplified.gpkg",
            "vector.centroid": "centroids.gpkg",
            "vector.convex_hull": "convex-hulls.gpkg",
        }
        algorithms = {
            "vector.dissolve": "native:dissolve",
            "vector.simplify": "native:simplifygeometries",
            "vector.centroid": "native:centroids",
            "vector.convex_hull": "native:convexhull",
        }
        values: dict[str, Any] = {
            "INPUT": _layer_source(source, parameters.get("layer")),
            "OUTPUT": str(output_dir / output_names[operator_id]),
        }
        if operator_id == "vector.dissolve":
            values.update(
                {
                    "FIELD": [parameters["field"]] if parameters.get("field") else [],
                    "SEPARATE_DISJOINT": parameters.get("separateDisjoint", False),
                }
            )
        elif operator_id == "vector.simplify":
            if source.metadata.get("crsUnit") != "metre":
                raise ValueError("PROJECTED_CRS_REQUIRED")
            values.update({"METHOD": 0, "TOLERANCE": parameters["toleranceMeters"]})
        elif operator_id == "vector.centroid":
            values["ALL_PARTS"] = parameters.get("allParts", False)
        _run_algorithm(algorithms[operator_id], values)
        return _vector_output(output_dir / output_names[operator_id])
    if operator_id == "vector.spatial_join":
        target, join = inputs["target"][0], inputs["join"][0]
        if target.metadata.get("crs") != join.metadata.get("crs"):
            raise ValueError("CRS_MISMATCH")
        predicates = {
            "intersects": 0,
            "contains": 1,
            "equals": 2,
            "touches": 3,
            "overlaps": 4,
            "within": 5,
            "crosses": 6,
        }
        modes = {"oneToMany": 0, "first": 1, "largestOverlap": 2}
        output = output_dir / "spatial-join.gpkg"
        _run_algorithm(
            "native:joinattributesbylocation",
            {
                "INPUT": _layer_source(target, parameters.get("targetLayer")),
                "PREDICATE": [predicates[parameters["predicate"]]],
                "JOIN": _layer_source(join, parameters.get("joinLayer")),
                "JOIN_FIELDS": [],
                "METHOD": modes[parameters["mode"]],
                "DISCARD_NONMATCHING": parameters.get("discardNonMatching", False),
                "PREFIX": parameters.get("prefix", ""),
                "OUTPUT": str(output),
                "NON_MATCHING": "TEMPORARY_OUTPUT",
            },
        )
        return _vector_output(output)
    if operator_id == "vector.clip":
        source, mask = inputs["vector"][0], inputs["mask"][0]
        _reject_dimensions(source)
        _reject_dimensions(mask)
        if source.metadata.get("crs") != mask.metadata.get("crs"):
            raise ValueError("CRS_MISMATCH")
        output = output_dir / "clipped.gpkg"
        _run_algorithm(
            "native:clip",
            {
                "INPUT": _layer_source(source, parameters.get("vectorLayer")),
                "OVERLAY": _layer_source(mask, parameters.get("maskLayer")),
                "OUTPUT": str(output),
            },
        )
        return _vector_output(output)
    if operator_id in {
        "vector.extract_by_extent",
        "vector.multipart_to_singleparts",
        "vector.point_on_surface",
        "geometry.repair",
    }:
        source = inputs["vector"][0]
        output_names = {
            "vector.extract_by_extent": "extent-extract.gpkg",
            "vector.multipart_to_singleparts": "singleparts.gpkg",
            "vector.point_on_surface": "points-on-surface.gpkg",
            "geometry.repair": "repaired.gpkg",
        }
        algorithms = {
            "vector.extract_by_extent": "native:extractbyextent",
            "vector.multipart_to_singleparts": "native:multiparttosingleparts",
            "vector.point_on_surface": "native:pointonsurface",
            "geometry.repair": "native:fixgeometries",
        }
        if operator_id in {"vector.point_on_surface", "geometry.repair"}:
            _reject_dimensions(source)
        output = output_dir / output_names[operator_id]
        values: dict[str, Any] = {
            "INPUT": _layer_source(source, parameters.get("layer")),
            "OUTPUT": str(output),
        }
        if operator_id == "vector.extract_by_extent":
            crs = source.metadata.get("crs")
            values.update(
                {
                    "EXTENT": (
                        f'{parameters["extentMinX"]},{parameters["extentMaxX"]},'
                        f'{parameters["extentMinY"]},{parameters["extentMaxY"]} [{crs}]'
                    ),
                    "CLIP": False,
                }
            )
        elif operator_id == "vector.point_on_surface":
            values["ALL_PARTS"] = parameters.get("allParts", False)
        elif operator_id == "geometry.repair":
            values["METHOD"] = {"linework": 0, "structure": 1}[
                parameters.get("method", "structure")
            ]
        try:
            _run_algorithm(algorithms[operator_id], values)
        except (RuntimeError, OSError) as exc:
            if operator_id != "geometry.repair":
                raise
            raise ValueError("GEOMETRY_REPAIR_FAILED") from exc
        return _vector_output(output)
    if operator_id == "vector.nearest_join":
        target, join = inputs["target"][0], inputs["join"][0]
        if target.metadata.get("crs") != join.metadata.get("crs"):
            raise ValueError("CRS_MISMATCH")
        if target.metadata.get("crsUnit") != "metre" or join.metadata.get("crsUnit") != "metre":
            raise ValueError("PROJECTED_CRS_REQUIRED")
        output = output_dir / "nearest-join.gpkg"
        _run_algorithm(
            "native:joinbynearest",
            {
                "INPUT": _layer_source(target, parameters.get("targetLayer")),
                "INPUT_2": _layer_source(join, parameters.get("joinLayer")),
                "FIELDS_TO_COPY": [],
                "DISCARD_NONMATCHING": parameters.get("discardNonMatching", False),
                "PREFIX": parameters.get("prefix", "join_"),
                "NEIGHBORS": parameters.get("neighbors", 1),
                "MAX_DISTANCE": parameters.get("maxDistanceMeters"),
                "OUTPUT": str(output),
                "NON_MATCHING": "TEMPORARY_OUTPUT",
            },
        )
        return _vector_output(output)
    if operator_id == "vector.merge":
        output = output_dir / "merged.gpkg"
        _run_algorithm(
            "native:mergevectorlayers",
            {
                "LAYERS": _merge_sources(inputs["vectors"]),
                "CRS": None,
                "ADD_SOURCE_FIELDS": False,
                "OUTPUT": str(output),
            },
        )
        return _vector_output(output)
    raise ValueError(f"OPERATOR_NOT_IMPLEMENTED: {operator_id}")
