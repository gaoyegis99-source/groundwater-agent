from __future__ import annotations

import json
import math
from pathlib import Path


def _features(path: Path, field: str):
    import numpy as np
    from rasterio.crs import CRS
    from rasterio.errors import CRSError
    from shapely import get_coordinates
    from shapely.errors import GEOSException
    from shapely.geometry import shape

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if document["type"] == "FeatureCollection":
            features = document["features"]
        elif document["type"] == "Feature":
            features = [document]
        else:
            raise ValueError("CARTOGRAPHY_SOURCE_INVALID")
        if not isinstance(features, list) or not features:
            raise ValueError("CARTOGRAPHY_SOURCE_INVALID")
        crs = CRS.from_user_input(
            document["crs"]["properties"]["name"] if "crs" in document else "OGC:CRS84"
        )
        geometries = []
        for feature in features:
            if feature["type"] != "Feature" or not isinstance(feature["geometry"], dict):
                raise ValueError("CARTOGRAPHY_SOURCE_INVALID")
            geometry = shape(feature["geometry"])
            if (
                geometry.is_empty
                or not geometry.is_valid
                or not np.isfinite(get_coordinates(geometry)).all()
            ):
                raise ValueError("CARTOGRAPHY_SOURCE_INVALID")
            geometries.append(geometry)
    except (
        KeyError,
        TypeError,
        AttributeError,
        ValueError,
        UnicodeError,
        GEOSException,
        CRSError,
    ) as exc:
        raise ValueError("CARTOGRAPHY_SOURCE_INVALID") from exc

    values = []
    if field:
        for feature in features:
            properties = feature.get("properties")
            value = properties.get(field) if isinstance(properties, dict) else None
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError("CARTOGRAPHY_FIELD_INVALID")
            values.append(value)
    return geometries, values, crs


def _draw_geometry(axes, geometry, color):
    from matplotlib.patches import PathPatch
    from matplotlib.path import Path as PlotPath
    from shapely.geometry.polygon import orient

    if geometry.geom_type == "Point":
        axes.scatter([geometry.x], [geometry.y], c=[color], s=42, edgecolors="black")
    elif geometry.geom_type in {"LineString", "LinearRing"}:
        axes.plot(*geometry.xy, color=color, linewidth=1.5)
    elif geometry.geom_type == "Polygon":
        polygon = orient(geometry)
        rings = [polygon.exterior, *polygon.interiors]
        vertices = [coordinate[:2] for ring in rings for coordinate in ring.coords]
        codes = [
            code
            for ring in rings
            for code in [
                PlotPath.MOVETO,
                *([PlotPath.LINETO] * (len(ring.coords) - 2)),
                PlotPath.CLOSEPOLY,
            ]
        ]
        axes.add_patch(PathPatch(PlotPath(vertices, codes), facecolor=color, edgecolor="black"))
    else:
        for part in geometry.geoms:
            _draw_geometry(axes, part, color)


def _draw_vector(figure, axes, source: Path, field: str):
    from matplotlib import colormaps
    from matplotlib.cm import ScalarMappable
    from matplotlib.colors import Normalize
    from matplotlib.patches import Patch

    geometries, values, crs = _features(source, field)
    color_scale = ScalarMappable(norm=Normalize(), cmap=colormaps["viridis"])
    if values:
        color_scale.set_array(values)
        color_scale.autoscale()
    for index, geometry in enumerate(geometries):
        color = color_scale.to_rgba(values[index]) if values else "#2879ad"
        _draw_geometry(axes, geometry, color)
    if values:
        figure.colorbar(color_scale, ax=axes, label=field)
    else:
        axes.legend(handles=[Patch(facecolor="#2879ad", label="Features")])
    axes.autoscale_view()
    axes.margins(0.08)
    return crs


def _draw_raster(figure, axes, source: Path, field: str):
    import numpy as np
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.errors import RasterioIOError

    if field:
        raise ValueError("CARTOGRAPHY_FIELD_INVALID")
    try:
        # Restrict the reader to a local TIFF, including when staging removes its suffix.
        with rasterio.open(source, driver="GTiff") as dataset:
            if dataset.crs is None:
                raise ValueError("CRS_REQUIRED")
            transform = dataset.transform
            if transform.b or transform.d or transform.a <= 0 or transform.e >= 0:
                raise ValueError("ROTATED_GRID_UNSUPPORTED")
            scale = max(dataset.width, dataset.height) / 1024
            height = max(1, round(dataset.height / max(1, scale)))
            width = max(1, round(dataset.width / max(1, scale)))
            values = dataset.read(
                1, out_shape=(height, width), masked=True, resampling=Resampling.nearest
            )
            values = np.ma.masked_invalid(values)
            if not values.count():
                raise ValueError("CARTOGRAPHY_SOURCE_INVALID")
            bounds = dataset.bounds
            crs = dataset.crs
    except RasterioIOError as exc:
        raise ValueError("CARTOGRAPHY_SOURCE_INVALID") from exc
    image = axes.imshow(
        values,
        extent=(bounds.left, bounds.right, bounds.bottom, bounds.top),
        origin="upper",
        cmap="viridis",
        interpolation="nearest",
    )
    figure.colorbar(image, ax=axes, label="Band 1")
    return crs


def render_png(_operator_id, inputs, parameters, output_dir: Path):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    (source,) = inputs["data"]
    figure = Figure(figsize=(8, 6), dpi=128, layout="constrained")
    FigureCanvasAgg(figure)
    axes = figure.subplots()
    field = parameters.get("valueField", "")
    if source.format_id == "geojson":
        crs = _draw_vector(figure, axes, source.path, field)
    elif source.format_id in {"geotiff", "cog"}:
        crs = _draw_raster(figure, axes, source.path, field)
    else:
        raise ValueError("CARTOGRAPHY_FORMAT_UNSUPPORTED")
    axes.set_aspect("equal", adjustable="box")
    axes.set_title(parameters.get("title", ""))
    axes.set_xlabel(f"X ({crs.to_string()})")
    axes.set_ylabel(f"Y ({crs.to_string()})")
    axes.ticklabel_format(useOffset=False, style="plain")
    axes.grid(alpha=0.2)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "image.png"
    figure.savefig(path, format="png")
    return {
        "image": {"kind": "artifact", "path": str(path), "mediaType": "image/png", "format": "png"}
    }
