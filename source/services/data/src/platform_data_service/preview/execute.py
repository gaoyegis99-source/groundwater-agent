from datetime import date, datetime, time, timedelta
from decimal import Decimal
import io
import json
import math
from pathlib import Path
import sqlite3
import struct
import zipfile
from xml.etree import ElementTree

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from pyproj import CRS, Transformer
from shapely import from_wkb
from shapely.geometry import mapping
from shapely.ops import transform

from ..adapters.database import canonical, digest
from ..adapters.sandbox import restrict_query
from ..errors import DataProblemError


def _scalar(value):
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DataProblemError("NONFINITE_PREVIEW_RESULT")
        return value
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, (Decimal, timedelta)):
        return str(value)
    if isinstance(value, bytes):
        return f"<binary {len(value)} bytes>"
    return str(value)


def _field_type(values):
    present = [value for value in values if value is not None]
    if not present:
        return "null"
    kinds = set()
    for value in present:
        if isinstance(value, bool):
            kinds.add("boolean")
        elif isinstance(value, int):
            kinds.add("integer")
        elif isinstance(value, (float, Decimal)):
            kinds.add("number")
        elif isinstance(value, datetime):
            kinds.add("datetime")
        elif isinstance(value, (date, time)):
            kinds.add("date")
        else:
            kinds.add("string")
    if kinds <= {"integer", "number"}:
        return "number" if "number" in kinds else "integer"
    return next(iter(kinds)) if len(kinds) == 1 else "string"


def _unique_headers(values, count):
    used = set()
    fields = []
    for index in range(count):
        raw = values[index] if index < len(values) else None
        base = str(raw).strip() if raw not in (None, "") else get_column_letter(index + 1)
        candidate = base[:200]
        suffix = 2
        while candidate in used:
            candidate = f"{base[:190]}_{suffix}"
            suffix += 1
        used.add(candidate)
        fields.append(candidate)
    return fields


def _validate_xlsx(path: Path, expanded_limit: int):
    try:
        with zipfile.ZipFile(path) as archive:
            expanded = 0
            for info in archive.infolist():
                name = info.filename.replace("\\", "/")
                expanded += info.file_size
                if expanded > expanded_limit:
                    raise DataProblemError("PREVIEW_ARCHIVE_TOO_LARGE", 413, limit=expanded_limit)
                if info.flag_bits & 1:
                    raise DataProblemError("PREVIEW_ENCRYPTED_FILE", 422)
                lowered = name.lower()
                if "vbaproject.bin" in lowered:
                    raise DataProblemError("PREVIEW_MACRO_FORBIDDEN", 422)
                if lowered.startswith("xl/externallinks/"):
                    raise DataProblemError("PREVIEW_EXTERNAL_LINK_FORBIDDEN", 422)
                if lowered.endswith(".rels"):
                    try:
                        relationships = ElementTree.fromstring(archive.read(info))
                    except (ElementTree.ParseError, RuntimeError, zipfile.BadZipFile):
                        raise DataProblemError("PREVIEW_FILE_INVALID", 422, format="xlsx") from None
                    if any(
                        relationship.attrib.get("TargetMode", "").lower()
                        == "external"
                        for relationship in relationships
                    ):
                        raise DataProblemError("PREVIEW_EXTERNAL_LINK_FORBIDDEN", 422)
    except zipfile.BadZipFile:
        raise DataProblemError("PREVIEW_FILE_INVALID", 422, format="xlsx") from None


def _xlsx(plan, path, settings):
    _validate_xlsx(path, settings.preview_expanded_bytes)
    source = path.open("rb")
    try:
        workbook = load_workbook(
            source, read_only=True, data_only=False, keep_links=False
        )
    except Exception as error:
        source.close()
        raise DataProblemError("PREVIEW_FILE_INVALID", 422, format="xlsx") from error
    try:
        sheets = [
            {
                "name": sheet.title,
                "state": sheet.sheet_state,
                "max_row": sheet.max_row,
                "max_column": sheet.max_column,
            }
            for sheet in workbook.worksheets
        ]
        if not sheets:
            raise DataProblemError("PREVIEW_FILE_INVALID", 422, format="xlsx")
        selected = plan.get("sheet_name")
        if selected is None:
            selected = next(
                (item["name"] for item in sheets if item["state"] == "visible"),
                sheets[0]["name"],
            )
        if selected not in workbook.sheetnames:
            raise DataProblemError("PREVIEW_SELECTOR_NOT_FOUND", 404, sheet_name=selected)
        sheet = workbook[selected]
        column_count = min(sheet.max_column or 0, settings.preview_columns)
        source_rows = sheet.max_row or 0
        iterator = sheet.iter_rows(
            min_row=1,
            max_row=min(source_rows, settings.preview_rows + 2),
            max_col=column_count or 1,
            values_only=True,
        )
        raw = [list(row) for row in iterator]
        header = raw[0] if raw else []
        names = _unique_headers(header, column_count)
        data_rows = raw[1 : settings.preview_rows + 1]
        fields = [
            {
                "id": f"c{index}",
                "name": name,
                "type": _field_type(
                    [row[index] if index < len(row) else None for row in data_rows]
                ),
            }
            for index, name in enumerate(names)
        ]
        rows = [
            [_scalar(row[index] if index < len(row) else None) for index in range(column_count)]
            for row in data_rows
        ]
        truncated = source_rows > settings.preview_rows + 1 or (
            sheet.max_column or 0
        ) > settings.preview_columns
        return {
            "kind": "xlsx_table",
            "metadata": {
                "sheets": sheets,
                "selected_sheet": selected,
                "effective_range": sheet.calculate_dimension(),
                "source_rows": max(0, source_rows - 1),
                "source_columns": sheet.max_column or 0,
            },
            "fields": fields,
            "rows": rows,
            "features": [],
            "truncated": truncated,
            "warnings": (["预览已按行数或列数上限截断。"] if truncated else []),
        }
    finally:
        workbook.close()
        source.close()


def _quoted(name):
    return '"' + name.replace('"', '""') + '"'


def _geometry(blob):
    if not isinstance(blob, bytes) or len(blob) < 8 or blob[:2] != b"GP":
        raise ValueError("invalid GeoPackage geometry header")
    flags = blob[3]
    envelope_code = (flags >> 1) & 0b111
    envelope_lengths = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}
    if envelope_code not in envelope_lengths:
        raise ValueError("invalid GeoPackage envelope")
    endian = "<" if flags & 1 else ">"
    srs_id = struct.unpack(endian + "i", blob[4:8])[0]
    offset = 8 + envelope_lengths[envelope_code]
    return from_wkb(blob[offset:]), srs_id


def _coordinate_count(value):
    if isinstance(value, (list, tuple)):
        if value and all(isinstance(item, (int, float)) for item in value):
            return 1
        return sum(_coordinate_count(item) for item in value)
    return 0


def _crs(connection, srs_id):
    row = connection.execute(
        """SELECT organization,organization_coordsys_id,definition
        FROM gpkg_spatial_ref_sys WHERE srs_id=?""",
        (srs_id,),
    ).fetchone()
    if row is None:
        raise ValueError("CRS metadata missing")
    organization = row["organization"]
    code = row["organization_coordsys_id"]
    if organization and code is not None:
        try:
            return CRS.from_user_input(f"{organization}:{code}")
        except Exception:
            pass
    definition = row["definition"]
    if not definition or definition == "undefined":
        raise ValueError("CRS definition unavailable")
    return CRS.from_user_input(definition)


def _sqlite_type(declared):
    value = (declared or "").upper()
    if "INT" in value:
        return "integer"
    if any(token in value for token in ("REAL", "FLOA", "DOUB", "NUM", "DEC")):
        return "number"
    if "BOOL" in value:
        return "boolean"
    if any(token in value for token in ("BLOB", "BINARY")):
        return "binary"
    return "string"


def _geopackage(plan, path, settings):
    with path.open("rb") as source:
        header = source.read(16)
    if header != b"SQLite format 3\x00":
        raise DataProblemError("PREVIEW_FILE_INVALID", 422, format="geopackage")
    uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
    except sqlite3.Error as error:
        raise DataProblemError("PREVIEW_FILE_INVALID", 422, format="geopackage") from error
    try:
        application_id = connection.execute("PRAGMA application_id").fetchone()[0]
        if application_id != 0x47504B47:
            raise DataProblemError("PREVIEW_FILE_INVALID", 422, format="geopackage")
        layers = [
            dict(row)
            for row in connection.execute(
                """SELECT c.table_name,c.identifier,c.description,c.srs_id,
                g.column_name AS geometry_column,g.geometry_type_name
                FROM gpkg_contents c JOIN gpkg_geometry_columns g
                ON g.table_name=c.table_name WHERE c.data_type='features'
                ORDER BY c.table_name"""
            )
        ]
        if not layers:
            raise DataProblemError("PREVIEW_FILE_INVALID", 422, format="geopackage")
        selected_name = plan.get("layer_name") or layers[0]["table_name"]
        layer = next((item for item in layers if item["table_name"] == selected_name), None)
        if layer is None:
            raise DataProblemError("PREVIEW_SELECTOR_NOT_FOUND", 404, layer_name=selected_name)
        table = _quoted(layer["table_name"])
        geometry_column = layer["geometry_column"]
        columns = [dict(row) for row in connection.execute(f"PRAGMA table_info({table})")]
        attributes = [item for item in columns if item["name"] != geometry_column]
        selected_columns = attributes[: settings.preview_columns]
        field_sql = ",".join(_quoted(item["name"]) for item in selected_columns)
        select_sql = (
            (field_sql + ",") if field_sql else ""
        ) + _quoted(geometry_column)
        source_rows = connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        records = connection.execute(
            f"SELECT {select_sql} FROM {table} LIMIT ?",
            (settings.preview_rows + 1,),
        ).fetchall()
        truncated = len(records) > settings.preview_rows or len(attributes) > settings.preview_columns
        records = records[: settings.preview_rows]
        warnings = []
        transformer = None
        map_available = True
        try:
            source_crs = _crs(connection, layer["srs_id"])
            if source_crs != CRS.from_epsg(4326):
                projection = Transformer.from_crs(source_crs, 4326, always_xy=True)
                transformer = projection.transform
        except Exception:
            map_available = False
            warnings.append("坐标参考系无法转换，仍可查看属性表。")
        rows = []
        features = []
        coordinate_count = 0
        bounds = None
        geometry_types = set()
        for row_index, record in enumerate(records):
            properties = {
                item["name"]: _scalar(record[item["name"]]) for item in selected_columns
            }
            rows.append(list(properties.values()))
            feature = None
            blob = record[geometry_column]
            if blob is not None and map_available:
                try:
                    geometry, _ = _geometry(blob)
                    if transformer is not None:
                        geometry = transform(transformer, geometry)
                    value = mapping(geometry)
                    count = _coordinate_count(value.get("coordinates"))
                    if coordinate_count + count > settings.preview_coordinates:
                        truncated = True
                        map_available = False
                        warnings.append("地图几何已达到坐标数量上限，其余要素仅显示属性。")
                    else:
                        coordinate_count += count
                        geometry_types.add(geometry.geom_type)
                        current = geometry.bounds
                        if current:
                            bounds = current if bounds is None else (
                                min(bounds[0], current[0]),
                                min(bounds[1], current[1]),
                                max(bounds[2], current[2]),
                                max(bounds[3], current[3]),
                            )
                        feature = {
                            "type": "Feature",
                            "id": row_index,
                            "geometry": value,
                            "properties": properties,
                        }
                except Exception:
                    warnings.append(f"第 {row_index + 1} 个要素的几何无效，已保留属性。")
            features.append(feature)
        if truncated:
            warnings.append("预览已按行数、列数或几何上限截断。")
        return {
            "kind": "geopackage_layer",
            "metadata": {
                "layers": layers,
                "selected_layer": selected_name,
                "feature_count": source_rows,
                "geometry_types": sorted(geometry_types),
                "crs": f"srs_id:{layer['srs_id']}",
                "bbox_wgs84": list(bounds) if bounds else None,
                "map_available": any(features),
            },
            "fields": [
                {
                    "id": item["name"],
                    "name": item["name"],
                    "type": _sqlite_type(item["type"]),
                }
                for item in selected_columns
            ],
            "rows": rows,
            "features": features,
            "truncated": truncated,
            "warnings": list(dict.fromkeys(warnings)),
        }
    except sqlite3.Error as error:
        raise DataProblemError("PREVIEW_FILE_INVALID", 422, format="geopackage") from error
    finally:
        connection.close()


def execute(plan, store, settings, work, preview_id):
    obj = plan["input"]["object"]
    store.verify(obj)
    restrict_query(store.objects, work, settings)
    path = store.path(obj)
    if plan["kind"] == "xlsx_table":
        result = _xlsx(plan, path, settings)
    elif plan["kind"] == "geopackage_layer":
        result = _geopackage(plan, path, settings)
    else:
        raise RuntimeError("UNSUPPORTED_PREVIEW_KIND")

    encoded = canonical(result).encode()
    while len(encoded) > settings.result_bytes and result["rows"]:
        keep = max(0, len(result["rows"]) // 2)
        result["rows"] = result["rows"][:keep]
        result["features"] = result["features"][:keep]
        result["truncated"] = True
        result["warnings"] = list(
            dict.fromkeys(result["warnings"] + ["预览结果已按响应大小限制截断。"])
        )
        encoded = canonical(result).encode()
    if len(encoded) > settings.result_bytes:
        raise DataProblemError("PREVIEW_RESULT_BUDGET", 413)
    obj_result = store.put(io.BytesIO(encoded), settings.result_bytes)
    receipt = {
        "preview_id": preview_id,
        "input": {
            "dataset_id": plan["input"]["dataset_id"],
            "version_id": plan["input"]["version_id"],
            "semantic_id": plan["input"]["semantic_id"],
            "object_id": obj["id"],
            "checksum": obj["checksum"],
        },
        "kind": plan["kind"],
        "plan_digest": digest(plan),
        "returned_rows": len(result["rows"]),
        "truncated": result["truncated"],
        "result_checksum": obj_result["checksum"],
        "engine": {"name": "platform-preview", "version": "1"},
    }
    json.dumps(receipt, allow_nan=False)
    return {"object": obj_result, "receipt": receipt}
