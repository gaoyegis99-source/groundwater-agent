from __future__ import annotations

from math import isclose
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


def _read_vector_geometries(item: StagedInput, layer: str | None) -> tuple[str, list[Any]]:
    try:
        import pyogrio
        import shapely
    except ImportError as exc:
        raise ValueError("BLOCKED_BY_ENVIRONMENT") from exc
    metadata, data = pyogrio.read_arrow(item.path, layer=layer)
    geometry_name = _geometry_column_name(metadata, data)
    values = data[geometry_name].to_numpy(zero_copy_only=False)
    geometries = shapely.from_wkb(values)
    if (
        shapely.is_missing(geometries).any()
        or shapely.is_empty(geometries).any()
        or not shapely.is_valid(geometries).all()
    ):
        raise ValueError("INVALID_GEOMETRY")
    crs = str(metadata.get("crs") or item.metadata.get("crs") or "")
    if not crs:
        raise ValueError("CRS_REQUIRED")
    return crs, list(geometries)


def _geometry_column_name(metadata: dict[str, Any], data: Any) -> str:
    """Resolve pyogrio's geometry column across named and GeoArrow forms."""
    declared = metadata.get("geometry_name")
    if isinstance(declared, str) and declared in data.column_names:
        return declared
    candidates = []
    for field in data.schema:
        field_metadata = field.metadata or {}
        extension_name = field_metadata.get(b"ARROW:extension:name", b"")
        if extension_name in {b"geoarrow.wkb", b"ogc.wkb"}:
            candidates.append(field.name)
    if len(candidates) != 1:
        raise ValueError("INVALID_GEOMETRY")
    return candidates[0]


def _monitoring_coverage_summary(geometries: list[Any], crs_value: str) -> dict[str, Any]:
    try:
        import numpy as np
        import shapely
        from pyproj import CRS
        from pyproj.exceptions import CRSError
        from shapely.geometry import MultiPoint
    except ImportError as exc:
        raise ValueError("BLOCKED_BY_ENVIRONMENT") from exc
    if not np.isin(shapely.get_type_id(geometries), [0, 4]).all():
        raise ValueError("POINT_GEOMETRY_REQUIRED")
    coordinates = shapely.get_coordinates(geometries, include_z=False)
    points = [(float(x), float(y)) for x, y in coordinates[:, :2]]
    unique_points = list(dict.fromkeys(points))
    if len(unique_points) < 3:
        raise ValueError("INSUFFICIENT_UNIQUE_POINTS")
    hull = MultiPoint(unique_points).convex_hull
    if hull.geom_type != "Polygon" or hull.is_empty or hull.area <= 0:
        raise ValueError("COLLINEAR_POINTS")
    try:
        crs = CRS.from_user_input(crs_value)
    except CRSError as exc:
        raise ValueError("CRS_UNSUPPORTED") from exc
    if crs.is_geographic:
        area, perimeter = crs.get_geod().geometry_area_perimeter(hull)
        area_square_metres = abs(float(area))
        perimeter_metres = float(perimeter)
        crs_kind = "GEOGRAPHIC"
        measurement_method = "GEODESIC_ELLIPSOID"
    elif crs.is_projected:
        factors = [
            float(axis.unit_conversion_factor)
            for axis in crs.axis_info[:2]
            if axis.unit_conversion_factor is not None
        ]
        if (
            len(factors) != 2
            or min(factors) <= 0
            or not isclose(factors[0], factors[1], rel_tol=1e-12, abs_tol=0)
        ):
            raise ValueError("CRS_UNSUPPORTED")
        area_square_metres = float(hull.area) * factors[0] ** 2
        perimeter_metres = float(hull.length) * factors[0]
        crs_kind = "PROJECTED"
        measurement_method = "PLANAR_PROJECTED_TO_METRES"
    else:
        raise ValueError("CRS_UNSUPPORTED")
    return {
        "inputFeatureCount": len(geometries),
        "inputPointCount": len(points),
        "uniquePointCount": len(unique_points),
        "duplicatePointCount": len(points) - len(unique_points),
        "hullGeometryType": hull.geom_type,
        "crs": crs.to_string(),
        "crsKind": crs_kind,
        "measurementMethod": measurement_method,
        "areaSquareMeters": area_square_metres,
        "perimeterMeters": perimeter_metres,
        "areaUnit": "m2",
        "perimeterUnit": "m",
        "resultKind": "GEOMETRIC_CONVEX_HULL",
        "interpretation": (
            "Convex hull of unique monitoring-well point coordinates; exact duplicate "
            "coordinates are counted but do not alter the hull."
        ),
        "limitations": [
            "This geometric hull is not an aquifer boundary, groundwater influence area, "
            "or pumping capture zone.",
            "The hull interior does not demonstrate monitoring completeness or hydraulic "
            "connection.",
            "Area and perimeter are sensitive to the outermost wells and ignore vertical "
            "separation.",
        ],
    }


def _generic_dissolve_summary() -> dict[str, Any]:
    return {
        "resultKind": "DISSOLVED_VECTOR",
        "interpretation": "Vector features dissolved with the requested QGIS parameters.",
        "limitations": [
            "This generic dissolve summary does not infer scientific coverage or topology."
        ],
    }


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
                "OUTPUT_FLOW_DIRECTIONS": str(output_dir / "temporary-flow-directions.tif"),
                "OUTPUT_WATERSHED_BASINS": str(output_dir / "temporary-watershed-basins.tif"),
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
            summary = _generic_dissolve_summary()
            if parameters.get("pointCoverageMode", False):
                crs, geometries = _read_vector_geometries(source, parameters.get("layer"))
                summary = _monitoring_coverage_summary(geometries, crs)
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
        result = _vector_output(output_dir / output_names[operator_id])
        if operator_id == "vector.dissolve":
            result["summary"] = {"kind": "value", "value": summary}
        return result
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
                        f"{parameters['extentMinX']},{parameters['extentMaxX']},"
                        f"{parameters['extentMinY']},{parameters['extentMaxY']} [{crs}]"
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
