from __future__ import annotations

import json
import math
from pathlib import Path

PALETTES = ("viridis", "cividis", "plasma", "magma", "Blues", "RdBu_r")
NO_DATA_COLOR = "#d9d9d9"
LIMITATIONS = [
    "No reprojection is performed; coordinates and extent use the source CRS.",
    (
        "The image contains one source only and has no basemap, interactive layers, "
        "or publication service."
    ),
    (
        "Colors visualize the selected values but do not establish data accuracy, "
        "provenance, or analytical validity."
    ),
]


def _format_mismatch(path: Path, format_id: str) -> bool:
    try:
        with path.open("rb") as stream:
            prefix = stream.read(64)
    except OSError as exc:
        raise ValueError("CARTOGRAPHY_SOURCE_INVALID") from exc
    is_tiff = prefix.startswith((b"II*\x00", b"MM\x00*"))
    looks_json = prefix.lstrip(b"\xef\xbb\xbf \t\r\n").startswith((b"{", b"["))
    return (format_id == "geojson" and is_tiff) or (format_id in {"geotiff", "cog"} and looks_json)


def _style(parameters: dict) -> dict:
    title = parameters.get("title", "")
    field = parameters.get("valueField", "")
    palette = parameters.get("palette", "viridis")
    unit = parameters.get("unit", "")
    width = parameters.get("width", 1024)
    height = parameters.get("height", 768)
    if (
        not isinstance(title, str)
        or not isinstance(field, str)
        or palette not in PALETTES
        or not isinstance(unit, str)
        or isinstance(width, bool)
        or not isinstance(width, int)
        or not 640 <= width <= 4096
        or isinstance(height, bool)
        or not isinstance(height, int)
        or not 480 <= height <= 4096
    ):
        raise ValueError("CARTOGRAPHY_PARAMETER_INVALID")
    return {
        "title": title,
        "valueField": field,
        "palette": palette,
        "unit": unit,
        "width": width,
        "height": height,
    }


def _label(name: str, unit: str) -> str:
    return f"{name} ({unit})" if unit else name


def _extent(bounds) -> dict[str, float]:
    return {
        "minX": float(bounds[0]),
        "minY": float(bounds[1]),
        "maxX": float(bounds[2]),
        "maxY": float(bounds[3]),
    }


def _summary(source_kind: str, source_format: str, crs, extent, style, data) -> dict:
    base_label = style["valueField"] if source_kind == "vector" else "Band 1"
    return {
        "sourceKind": source_kind,
        "sourceFormat": source_format,
        "crs": crs.to_string(),
        "extent": extent,
        "image": {"width": style["width"], "height": style["height"]},
        "style": {
            "title": style["title"],
            "valueField": style["valueField"] or None,
            "palette": style["palette"],
            "unit": style["unit"],
            "colorbarLabel": _label(base_label, style["unit"]),
            "noDataLabel": "No data",
            "noDataColor": NO_DATA_COLOR,
        },
        "data": data,
        "interpretation": "Static rendering of one existing spatial data file in its source CRS.",
        "limitations": LIMITATIONS,
    }


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

    values: list[float | None] = []
    if field:
        field_seen = False
        for feature in features:
            properties = feature.get("properties")
            if isinstance(properties, dict) and field in properties:
                field_seen = True
                value = properties[field]
            else:
                value = None
            if value is None:
                values.append(None)
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError("CARTOGRAPHY_FIELD_INVALID")
            values.append(float(value))
        if not field_seen or not any(value is not None for value in values):
            raise ValueError("CARTOGRAPHY_FIELD_INVALID")
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


def _draw_vector(figure, axes, source: Path, style: dict):
    from matplotlib import colormaps
    from matplotlib.cm import ScalarMappable
    from matplotlib.colors import Normalize
    from matplotlib.patches import Patch
    from shapely import total_bounds

    field = style["valueField"]
    geometries, values, crs = _features(source, field)
    numeric_values = [value for value in values if value is not None]
    if numeric_values and min(numeric_values) == max(numeric_values):
        lower, upper = numeric_values[0] - 0.5, numeric_values[0] + 0.5
    elif numeric_values:
        lower, upper = min(numeric_values), max(numeric_values)
    else:
        lower, upper = 0.0, 1.0
    color_scale = ScalarMappable(
        norm=Normalize(vmin=lower, vmax=upper), cmap=colormaps[style["palette"]]
    )
    if values:
        color_scale.set_array(numeric_values)
    for index, geometry in enumerate(geometries):
        value = values[index] if values else None
        color = (
            color_scale.to_rgba(value)
            if value is not None
            else NO_DATA_COLOR
            if values
            else "#2879ad"
        )
        _draw_geometry(axes, geometry, color)
    if values:
        figure.colorbar(color_scale, ax=axes, label=_label(field, style["unit"]))
        if any(value is None for value in values):
            axes.legend(handles=[Patch(facecolor=NO_DATA_COLOR, label="No data")])
    else:
        axes.legend(handles=[Patch(facecolor="#2879ad", label="Features")])
    axes.autoscale_view()
    axes.margins(0.08)
    return (
        crs,
        _extent(total_bounds(geometries)),
        {
            "countBasis": "FEATURES",
            "featureCount": len(geometries),
            "validValueCount": len(numeric_values),
            "noDataCount": sum(value is None for value in values) if values else 0,
        },
    )


def _draw_raster(figure, axes, source: Path, style: dict):
    import numpy as np
    import rasterio
    from matplotlib import colormaps
    from matplotlib.patches import Patch
    from rasterio.enums import Resampling
    from rasterio.errors import RasterioIOError

    if style["valueField"]:
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
            source_width = dataset.width
            source_height = dataset.height
            no_data_value = dataset.nodata
    except RasterioIOError as exc:
        raise ValueError("CARTOGRAPHY_SOURCE_INVALID") from exc
    palette = colormaps[style["palette"]].with_extremes(bad=NO_DATA_COLOR)
    image = axes.imshow(
        values,
        extent=(bounds.left, bounds.right, bounds.bottom, bounds.top),
        origin="upper",
        cmap=palette,
        interpolation="nearest",
    )
    figure.colorbar(image, ax=axes, label=_label("Band 1", style["unit"]))
    no_data_count = int(values.size - values.count())
    if no_data_count:
        axes.legend(handles=[Patch(facecolor=NO_DATA_COLOR, label="No data")])
    return (
        crs,
        _extent((bounds.left, bounds.bottom, bounds.right, bounds.top)),
        {
            "countBasis": "RENDERED_PIXELS",
            "band": 1,
            "sourceWidth": source_width,
            "sourceHeight": source_height,
            "renderedWidth": int(values.shape[1]),
            "renderedHeight": int(values.shape[0]),
            "validValueCount": int(values.count()),
            "noDataCount": no_data_count,
            "noDataValue": (
                float(no_data_value)
                if no_data_value is not None and math.isfinite(no_data_value)
                else None
            ),
        },
    )


def render_png(_operator_id, inputs, parameters, output_dir: Path):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    (source,) = inputs["data"]
    if source.format_id not in {"geojson", "geotiff", "cog"}:
        raise ValueError("CARTOGRAPHY_FORMAT_UNSUPPORTED")
    style = _style(parameters)
    if _format_mismatch(source.path, source.format_id):
        raise ValueError("CARTOGRAPHY_FORMAT_MISMATCH")
    dpi = 128
    figure = Figure(
        figsize=(style["width"] / dpi, style["height"] / dpi),
        dpi=dpi,
        layout="constrained",
    )
    FigureCanvasAgg(figure)
    axes = figure.subplots()
    if source.format_id == "geojson":
        crs, extent, data = _draw_vector(figure, axes, source.path, style)
        source_kind = "vector"
    elif source.format_id in {"geotiff", "cog"}:
        crs, extent, data = _draw_raster(figure, axes, source.path, style)
        source_kind = "raster"
    axes.set_aspect("equal", adjustable="box")
    axes.set_title(style["title"])
    axes.set_xlabel(f"X ({crs.to_string()})")
    axes.set_ylabel(f"Y ({crs.to_string()})")
    axes.ticklabel_format(useOffset=False, style="plain")
    axes.grid(alpha=0.2)
    axes.text(
        0.5,
        -0.12,
        (
            f"CRS: {crs.to_string()} | Extent: "
            f"{extent['minX']:g}, {extent['minY']:g}, "
            f"{extent['maxX']:g}, {extent['maxY']:g}"
        ),
        transform=axes.transAxes,
        ha="center",
        va="top",
        fontsize=8,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "image.png"
    figure.savefig(path, format="png")
    return {
        "image": {
            "kind": "artifact",
            "path": str(path),
            "mediaType": "image/png",
            "format": "png",
        },
        "summary": {
            "kind": "value",
            "value": _summary(source_kind, source.format_id, crs, extent, style, data),
        },
    }
