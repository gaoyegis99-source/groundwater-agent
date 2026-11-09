"""Small, deterministic synthetic INPUTs and factual declarations; no IO or execution."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import random
import struct

VERSION = 5
SEED = 20260928
NOTICE = "合成演示资料，不代表真实监测，不用于实际地下水决策。"
CORE_METADATA = {
    "scenario_id": "groundwater-unified-v5",
    "data_origin": "SYNTHETIC",
    "evidence_level": "SYNTHETIC_TEST_ONLY",
    "dataset_version": "5.0.0",
    "quality_status": "SYNTHETIC_UNVALIDATED",
    "source_reference": "synthetic-generator:20260928",
}
FIELDS = (
    "well_id",
    "observed_at",
    "water_level_m",
    "electrical_conductivity_us_cm",
    "quality_flag",
    "longitude_deg",
    "latitude_deg",
    *CORE_METADATA,
)
FIELD_UNITS = {
    "well_id": "无量纲标识",
    "observed_at": "日历日期（YYYY-MM-DD）",
    "water_level_m": "m",
    "electrical_conductivity_us_cm": "µS/cm",
    "quality_flag": "类别",
    "longitude_deg": "degree",
    "latitude_deg": "degree",
    **{name: "文本" for name in CORE_METADATA},
}
FIELD_MEANINGS = {
    "well_id": "虚构监测井编号，与井点资料的 well_id 对应。",
    "observed_at": "模拟采样日期，无时刻或时区含义。",
    "water_level_m": "相对虚构统一高程基准的水位高程，不是埋深。",
    "electrical_conductivity_us_cm": "模拟电导率，未做实际仪器校准或温度补偿。",
    "quality_flag": "simulated_valid 或 simulated_review，均为生成器分配的演示标签，不是实测质量结论。",
    "longitude_deg": "WGS84 经度；不是米制平面坐标。",
    "latitude_deg": "WGS84 纬度；不是米制平面坐标。",
    "scenario_id": "共享合成数据场景标识，跨表相同。",
    "data_origin": "SYNTHETIC 表示完全合成，非真实采集。",
    "evidence_level": "SYNTHETIC_TEST_ONLY，仅供功能测试。",
    "dataset_version": "合成数据集版本，不是上传后的 Resource version。",
    "quality_status": "SYNTHETIC_UNVALIDATED，不提供实际监测质量保证。",
    "source_reference": "固定种子生成来源，不引用真实井或观测。",
}


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def _checksum(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _csv_dict_bytes(rows: list[dict], fields: list[str] | None = None) -> bytes:
    fields = list(rows[0]) if fields is None else fields
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows({field: row.get(field, "") for field in fields} for row in rows)
    return stream.getvalue().encode("utf-8")


def build_core() -> dict:
    """One synthetic station/month truth shared by every preparation entry point."""
    from pyproj import Transformer

    transform = Transformer.from_crs("EPSG:32650", "EPSG:4326", always_xy=True)
    generator = random.Random(SEED)
    stations, aliases, observations, drivers, chemistry, risks, measures = (
        [],
        [],
        [],
        [],
        [],
        [],
        [],
    )

    def coordinate(x, y):
        return [round(value, 9) for value in transform.transform(x, y)]

    for index in range(20):
        well = f"W{index + 1:03}"
        x, y = 500000 + index % 5 * 1000, 3500000 + index // 5 * 1000
        longitude, latitude = coordinate(x, y)
        stations.append(
            {
                "well_id": well,
                "well_name": f"虚构监测井{index + 1:02}",
                "x": x,
                "y": y,
                "longitude_deg": longitude,
                "latitude_deg": latitude,
                "coordinate_crs": "EPSG:32650",
                "reference_elevation_m": 120.0,
                "vertical_datum": "SYNTHETIC_LOCAL_DATUM",
                "aquifer_id": f"A{index // 10 + 1}",
                "region_id": f"R{index // 10 + 1}",
            }
        )
        aliases.append(
            {
                "alias": f"DEMO-{index + 1:03}",
                "well_id": well,
                "alias_type": "synthetic_external_id",
            }
        )
        aliases.append(
            {
                "alias": f"{index + 1:03}",
                "well_id": well,
                "alias_type": "synthetic_numeric_text",
            }
        )
        risk_id = f"RISK-{index + 1:03}"
        risks.append(
            {
                "risk_id": risk_id,
                "well_id": well,
                "category": "synthetic_land_use",
                "x": x + 100,
                "y": y + 100,
                "coordinate_crs": "EPSG:32650",
            }
        )
        measures.append(
            {
                "measure_id": f"MEASURE-{index + 1:03}",
                "risk_id": risk_id,
                "well_id": well,
                "action": "synthetic_inspection",
                "cost_cny": 1000 + index * 50,
            }
        )
        for month_index in range(36):
            year, month = 2023 + month_index // 12, month_index % 12 + 1
            date = f"{year}-{month:02}-01"
            change = (
                0.03 * month_index
                if index < 5
                else -0.03 * month_index
                if index < 10
                else 0
                if index < 15
                else 0.3 * math.sin(2 * math.pi * month_index / 12)
            )
            level = round(
                90 + index * 0.1 + change + generator.uniform(-0.005, 0.005), 6
            )
            conductivity = round(440.0 + index * 18 + month_index * 2, 6)
            observations.append(
                {
                    "well_id": well,
                    "observed_at": date,
                    "water_level_m": level,
                    "x": x,
                    "y": y,
                    "reference_elevation_m": 120.0,
                    "depth_to_water_m": round(120.0 - level, 6),
                    "electrical_conductivity_us_cm": conductivity,
                    "quality_flag": "simulated_review"
                    if (index + month) % 5 == 0
                    else "simulated_valid",
                    "longitude_deg": longitude,
                    "latitude_deg": latitude,
                }
            )
            drivers.append(
                {
                    "well_id": well,
                    "observed_at": date,
                    "rainfall_mm": round(
                        45
                        + 20 * math.sin(2 * math.pi * month_index / 12)
                        + index * 0.1,
                        6,
                    ),
                    "pumping_m3": round(1000 + index * 25 + month_index * 3, 6),
                }
            )
            for analyte, value, unit, limit in (
                ("electrical_conductivity", conductivity, "uS/cm", 1.0),
                (
                    "nitrate_n",
                    round(2 + index * 0.1 + month_index * 0.01, 6),
                    "mg/L",
                    0.05,
                ),
                ("chloride", round(30 + index + month_index * 0.2, 6), "mg/L", 0.1),
            ):
                chemistry.append(
                    {
                        "well_id": well,
                        "observed_at": date,
                        "analyte": analyte,
                        "value": value,
                        "unit": unit,
                        "detection_limit": limit,
                        "quality_flag": "SYNTHETIC_TEST_ONLY",
                    }
                )

    def feature(kind, coordinates, properties):
        return {
            "type": "Feature",
            "geometry": {"type": kind, "coordinates": coordinates},
            "properties": {"notice": NOTICE, **properties},
        }

    layers = {
        "wells": [
            feature(
                "Point",
                [row["longitude_deg"], row["latitude_deg"]],
                {"well_id": row["well_id"]},
            )
            for row in stations
        ],
        "boundary": [
            feature(
                "Polygon",
                [
                    [
                        coordinate(x, y)
                        for x, y in (
                            (499500, 3499500),
                            (504500, 3499500),
                            (504500, 3503500),
                            (499500, 3503500),
                            (499500, 3499500),
                        )
                    ]
                ],
                {"area_id": "DEMO-AREA-01", "name": "虚构研究区"},
            )
        ],
        "rivers": [
            feature(
                "LineString",
                [coordinate(x, y) for x, y in points],
                {"name": f"虚构河流{i + 1}"},
            )
            for i, points in enumerate(
                (
                    [(499700, 3500500), (502000, 3500800), (504300, 3501000)],
                    [(501000, 3499700), (501500, 3501500), (502000, 3503300)],
                )
            )
        ],
    }
    for rows in (stations, aliases, observations, drivers, chemistry, risks, measures):
        for row in rows:
            row.update(CORE_METADATA)
    return {
        "stations": stations,
        "aliases": aliases,
        "observations": observations,
        "drivers": drivers,
        "water-quality": chemistry,
        "risk-sources": risks,
        "measures": measures,
        "layers": {
            name: {"type": "FeatureCollection", "notice": NOTICE, "features": features}
            for name, features in layers.items()
        },
    }


def scalar_raster_bytes() -> bytes:
    """Return a deterministic, uncompressed GeoTIFF without a native dependency.

    Raster processing remains in the separately locked native environment.  The
    ordinary CLI only needs to publish this tiny fixed fixture, so emitting the
    small TIFF/GeoTIFF tag set directly keeps that dependency boundary honest.
    """

    width, height = 25, 20
    pixels = struct.pack(
        f"<{width * height}f", *(index / 10 for index in range(width * height))
    )
    software = b"Groundwater V6 deterministic demo\0"
    pixel_scale = struct.pack("<3d", 200.0, 200.0, 0.0)
    tiepoint = struct.pack("<6d", 0.0, 0.0, 0.0, 499500.0, 3503500.0, 0.0)
    # GeoTIFF 1.0 header followed by five inline GeoKeys: projected model,
    # pixel-is-area raster, WGS84 geographic CRS, EPSG:32650 and metre units.
    geo_keys = struct.pack(
        "<24H",
        1,
        1,
        0,
        5,
        1024,
        0,
        1,
        1,
        1025,
        0,
        1,
        1,
        2048,
        0,
        1,
        4326,
        3072,
        0,
        1,
        32650,
        3076,
        0,
        1,
        9001,
    )
    metadata = (
        b'<GDALMetadata><Item name="AREA_OR_POINT">Area</Item>'
        b'<Item name="value_unit">dimensionless</Item></GDALMetadata>\0'
    )
    nodata = b"-9999\0"

    entry_count = 17
    external_base = 8 + 2 + entry_count * 12 + 4
    external = bytearray()

    def add_external(value: bytes, alignment: int = 2) -> int:
        padding = (-(external_base + len(external))) % alignment
        external.extend(b"\0" * padding)
        offset = external_base + len(external)
        external.extend(value)
        return offset

    software_offset = add_external(software)
    pixel_scale_offset = add_external(pixel_scale, 8)
    tiepoint_offset = add_external(tiepoint, 8)
    geo_keys_offset = add_external(geo_keys, 2)
    metadata_offset = add_external(metadata)
    nodata_offset = add_external(nodata)
    pixels_offset = add_external(pixels, 4)

    def short(value: int) -> bytes:
        return struct.pack("<H", value) + b"\0\0"

    def long(value: int) -> bytes:
        return struct.pack("<I", value)

    # TIFF types: ASCII=2, SHORT=3, LONG=4 and DOUBLE=12.
    entries = [
        (256, 4, 1, long(width)),
        (257, 4, 1, long(height)),
        (258, 3, 1, short(32)),
        (259, 3, 1, short(1)),
        (262, 3, 1, short(1)),
        (273, 4, 1, long(pixels_offset)),
        (277, 3, 1, short(1)),
        (278, 4, 1, long(height)),
        (279, 4, 1, long(len(pixels))),
        (284, 3, 1, short(1)),
        (305, 2, len(software), long(software_offset)),
        (339, 3, 1, short(3)),
        (33550, 12, 3, long(pixel_scale_offset)),
        (33922, 12, 6, long(tiepoint_offset)),
        (34735, 3, 24, long(geo_keys_offset)),
        (42112, 2, len(metadata), long(metadata_offset)),
        (42113, 2, len(nodata), long(nodata_offset)),
    ]
    if entries != sorted(entries):
        raise AssertionError("TIFF_TAG_ORDER_INVALID")
    directory = struct.pack("<H", len(entries)) + b"".join(
        struct.pack("<HHI", tag, field_type, count) + value
        for tag, field_type, count, value in entries
    )
    directory += struct.pack("<I", 0)
    return b"II" + struct.pack("<HI", 42, 8) + directory + bytes(external)


def build_fast_transfer_files(layer_names: dict[str, str]) -> dict[str, bytes]:
    """Workbook and spatial projections of four core wells, with stable file bytes."""
    from datetime import datetime
    import sqlite3
    from zipfile import ZipFile, ZipInfo

    import numpy as np
    from openpyxl import Workbook
    import pyarrow as pa
    import pyogrio
    from pyproj import Transformer
    from rasterio.io import MemoryFile
    from rasterio.transform import from_origin
    from shapely.geometry import shape
    from shapely.ops import transform

    core = build_core()
    wells = (
        ("CORE_SOURCE", "W001"),
        ("CORE_SOURCE", "W002"),
        ("CORE_TARGET", "W011"),
        ("CORE_TARGET", "W012"),
    )
    months = sorted({row["observed_at"] for row in core["observations"]})[:16]
    stations = {row["well_id"]: row for row in core["stations"]}
    observations = {
        (row["well_id"], row["observed_at"]): row for row in core["observations"]
    }
    drivers = {(row["well_id"], row["observed_at"]): row for row in core["drivers"]}
    book = Workbook()
    book.properties.created = book.properties.modified = datetime(2026, 9, 28)
    sheet = book.active
    sheet.title = "Sheet1"
    sheet.append(
        [
            "Region",
            "Well_ID",
            "Lon",
            "Lat",
            *["W" + date for date in months],
            *["Pre" + date[:7].replace("-", "") for date in months],
        ]
    )
    for region, well in wells:
        station = stations[well]
        sheet.append(
            [
                region,
                well,
                station["x"],
                station["y"],
                *[observations[well, date]["depth_to_water_m"] for date in months],
                *[drivers[well, date]["rainfall_mm"] for date in months],
            ]
        )
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    # OOXML ZIP timestamps are packaging metadata, not observation facts.
    stable = io.BytesIO()
    with ZipFile(stream) as source, ZipFile(stable, "w") as target:
        for name in sorted(source.namelist()):
            content = source.read(name)
            if name == "docProps/core.xml":
                import re

                content = re.sub(
                    rb"<dcterms:modified[^>]*>.*?</dcterms:modified>",
                    b'<dcterms:modified xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="dcterms:W3CDTF">2026-09-28T00:00:00Z</dcterms:modified>',
                    content,
                )
            target.writestr(ZipInfo(name, (2026, 9, 28, 0, 0, 0)), content)
    files = {"observations.xlsx": stable.getvalue()}
    project = Transformer.from_crs("EPSG:4326", "EPSG:32650", always_xy=True)
    for name, layer in (
        ("boundary", "boundary"),
        ("counties", "boundary"),
        ("townships", "boundary"),
        ("rivers", "rivers"),
        ("groundwater_systems", "boundary"),
        ("railways", "rivers"),
        ("basin_boundary", "boundary"),
    ):
        geometry = [
            transform(project.transform, shape(feature["geometry"]))
            for feature in core["layers"][layer]["features"]
        ]
        data = pa.table(
            {
                "id": list(range(1, len(geometry) + 1)),
                "geometry": [item.wkb for item in geometry],
            }
        )
        stream = io.BytesIO()
        pyogrio.write_arrow(
            data,
            stream,
            layer=layer_names[name],
            driver="GPKG",
            geometry_name="geometry",
            geometry_type=geometry[0].geom_type,
            crs="EPSG:32650",
        )
        # Normalize only our freshly generated in-memory GeoPackage metadata.
        with sqlite3.connect(":memory:") as database:
            database.deserialize(stream.getvalue())
            database.execute(
                "UPDATE gpkg_contents SET last_change='2026-09-28T00:00:00.000Z'"
            )
            database.commit()
            files[name + ".gpkg"] = database.serialize()
    with MemoryFile() as memory:
        # Include a 2 km halo around the core study area. The map extent also
        # adds padding and expands to 1.6 aspect ratio before pixel rounding.
        with memory.open(
            driver="GTiff",
            width=45,
            height=40,
            count=3,
            dtype="uint8",
            crs="EPSG:32650",
            transform=from_origin(497500, 3505500, 200, 200),
        ) as dataset:
            y, x = np.indices((40, 45))
            dataset.write(np.stack([60 + x + y, 80 + x, 100 + y]).astype("uint8"))
        files["rgb.tif"] = memory.read()
    files["provenance.json"] = _json_bytes(
        {
            "dataset_id": "groundwater-synthetic-v5",
            "notice": NOTICE,
            "source_wells": ["W001", "W002"],
            "target_wells": ["W011", "W012"],
            "months": months,
            "fields": {
                "Well_ID": "stations.well_id",
                "Lon": "stations.x",
                "Lat": "stations.y",
                "W<date>": "observations.depth_to_water_m",
                "Pre<month>": "drivers.rainfall_mm",
            },
            "coordinate_crs": "EPSG:32650",
            "water_unit": "m",
            "rainfall_unit": "mm",
            "aliases": [
                row
                for row in core["aliases"]
                if row["well_id"] in {well for _, well in wells}
            ],
            "spatial_scope": "背景面重用核心研究区，背景线重用核心河流，RGB为研究区外扩2公里的合成像元，覆盖加边距和纵横比调整后的地图范围；均非真实县乡、铁路或水文地质划分。",
            "legacy_fixture": "tests/e2e/groundwater/transfer_fixture.py is a separate algorithm regression, not this business dataset",
        }
    )
    return files


def build_demo_files() -> dict[str, bytes]:
    """Return shuffled projections of the shared core, never a second station truth."""
    core = build_core()
    rows = [
        {
            key: (
                f"{row[key]:.9f}"
                if key in {"longitude_deg", "latitude_deg"}
                else f"{row[key]:.6f}"
                if isinstance(row[key], float)
                else row[key]
            )
            for key in FIELDS
        }
        for row in core["observations"]
    ]
    random.Random(SEED).shuffle(rows)
    monitoring = _csv_dict_bytes(rows, list(FIELDS))
    project = {
        "schemaVersion": "table-project-rules.v1",
        "columns": [
            {"source": name, "target": name, "type": field_type}
            for name, field_type in zip(
                FIELDS,
                (
                    "string",
                    "date",
                    "float64",
                    "float64",
                    "string",
                    "float64",
                    "float64",
                    *("string" for _ in CORE_METADATA),
                ),
                strict=True,
            )
        ],
    }
    filter_rules = {
        "schemaVersion": "table-filter-rules.v1",
        "condition": {"field": "quality_flag", "op": "eq", "value": "simulated_valid"},
    }
    sort = {
        "schemaVersion": "table-sort-rules.v1",
        "keys": [
            {"field": name, "direction": "asc", "nulls": "last"}
            for name in ("observed_at", "well_id")
        ],
    }
    join = {
        "schemaVersion": "table-join-rules.v1",
        "keys": [
            {"left": "well_id", "right": "well_id"},
            {"left": "observed_at", "right": "observed_at"},
        ],
    }
    drivers = _csv_dict_bytes(core["drivers"])
    centimetres = [dict(row) for row in rows]
    for row in centimetres:
        row["water_level_m"] = f"{float(row['water_level_m']) * 100:.6f}"
        row["declared_value_unit"] = "cm"
    alias_rows = [dict(row) for row in rows]
    aliases = {"W001": "001", "W002": "DEMO-002"}
    for row in alias_rows:
        row["well_id"] = aliases.get(row["well_id"], row["well_id"])
    invalid_rows = [dict(row) for row in rows]
    for row, value in zip(
        invalid_rows, ("not-a-number", "NaN", "Infinity", "-Infinity"), strict=False
    ):
        row["water_level_m"] = value
    unknown_crs = [
        {"well_id": row["well_id"], "x": row["x"], "y": row["y"]}
        for row in core["stations"]
    ]
    wells = {**core["layers"]["wells"], "name": "points"}
    study_area = {**core["layers"]["boundary"], "name": "polygons"}
    return {
        "authoring-monitoring.csv": monitoring,
        "authoring-project-rules.json": _json_bytes(project),
        "authoring-filter-rules.json": _json_bytes(filter_rules),
        "authoring-sort-rules.json": _json_bytes(sort),
        "authoring-join-rules.json": _json_bytes(join),
        "authoring-drivers.csv": drivers,
        "authoring-wells.geojson": _json_bytes(wells),
        "authoring-study-area.geojson": _json_bytes(study_area),
        "authoring-scalar-raster.geotiff": scalar_raster_bytes(),
        "authoring-quality-centimetres.csv": _csv_dict_bytes(
            centimetres, [*FIELDS, "declared_value_unit"]
        ),
        "authoring-quality-alias.csv": _csv_dict_bytes(alias_rows, list(FIELDS)),
        "authoring-quality-invalid-numbers.csv": _csv_dict_bytes(
            invalid_rows, list(FIELDS)
        ),
        "authoring-quality-unknown-crs.csv": _csv_dict_bytes(unknown_crs),
        "authoring-quality-illegal-rules.json": _json_bytes(
            {
                "schemaVersion": "table-sort-rules.v1",
                "keys": [{"field": "observed_at", "direction": "asc", "nulls": "last"}],
                "unexpected": True,
            }
        ),
    }


def build_demo_assets() -> list[dict]:
    """Describe each file before HTTP publication assigns authoritative Resource IDs."""
    files = build_demo_files()
    crs = {
        "horizontal": "EPSG:4326",
        "coordinate_order": ["longitude", "latitude"],
        "coordinate_unit": "degree",
    }
    parent_monitoring = _checksum(files["authoring-monitoring.csv"])
    entries = [
        (
            "monitoring",
            "csv",
            "text/csv",
            "编排演示－月度监测表",
            "统一核心 20 口虚构井的 2023 至 2025 年水位和电导率资料，行顺序已打乱。",
            {
                "row_count": 720,
                "well_count": 20,
                "unique_key": ["well_id", "observed_at"],
                "field_units": dict(FIELD_UNITS),
                "field_meanings": dict(FIELD_MEANINGS),
                "crs": dict(crs),
                "vertical_datum": "虚构统一高程基准，不对应国家高程基准",
                "date_range": ["2023-01-01", "2025-12-01"],
                "missing_values": False,
            },
        ),
        (
            "project-rules",
            "json",
            "application/json",
            "编排演示－监测表类型与列规则",
            "监测表各列的同名保留及明确类型声明。",
            {
                "rule_schema_version": "table-project-rules.v1",
                "referenced_fields": list(FIELDS),
                "field_meanings": {
                    "columns": "源字段、目标字段及目标类型列表；水位、电导率和坐标声明为 float64，日期声明为 date。"
                },
            },
        ),
        (
            "filter-rules",
            "json",
            "application/json",
            "编排演示－监测标签筛选规则",
            "quality_flag 等于 simulated_valid 的条件声明，标签没有实测质量保证。",
            {
                "rule_schema_version": "table-filter-rules.v1",
                "referenced_fields": ["quality_flag"],
                "field_meanings": {"condition": "针对模拟类别标签的相等条件。"},
            },
        ),
        (
            "sort-rules",
            "json",
            "application/json",
            "编排演示－日期与井号排序规则",
            "先按 observed_at、再按 well_id 升序；空值放在最后。",
            {
                "rule_schema_version": "table-sort-rules.v1",
                "referenced_fields": ["observed_at", "well_id"],
                "field_meanings": {"keys": "按声明次序比较的字段、方向和空值位置。"},
            },
        ),
        (
            "join-rules",
            "json",
            "application/json",
            "编排演示－井月关联规则",
            "按 well_id 与 observed_at 两个字段精确相等关联。",
            {
                "rule_schema_version": "table-join-rules.v1",
                "referenced_fields": ["well_id", "observed_at"],
                "field_meanings": {
                    "keys": "监测与驱动资料的井号和月日期复合键；不执行别名或近邻匹配。"
                },
            },
        ),
        (
            "drivers",
            "csv",
            "text/csv",
            "编排演示－逐井月度驱动表",
            "与统一核心监测表同为 20 井、36 月，降雨和开采量按井月明确给定。",
            {
                "row_count": 720,
                "well_count": 20,
                "unique_key": ["well_id", "observed_at"],
                "field_units": {
                    "well_id": "无量纲标识",
                    "observed_at": "日历日期（YYYY-MM-DD）",
                    "rainfall_mm": "mm",
                    "pumping_m3": "m3",
                },
                "field_meanings": {
                    "well_id": FIELD_MEANINGS["well_id"],
                    "observed_at": FIELD_MEANINGS["observed_at"],
                    "rainfall_mm": "逐井月度合成降雨量，不代表经验证因果驱动。",
                    "pumping_m3": "逐井月度合成开采量。",
                },
                "date_range": ["2023-01-01", "2025-12-01"],
                "missing_values": False,
            },
        ),
        (
            "wells",
            "geojson",
            "application/geo+json",
            "编排演示－虚构监测井点",
            "20 个统一核心虚构井点，与月度监测表的井号和经纬度逐一对应。",
            {
                "feature_count": 20,
                "geometry": "Point",
                "crs": dict(crs),
                "ogr_layer_name": "points",
                "field_units": {"well_id": "无量纲标识"},
                "field_meanings": {"well_id": FIELD_MEANINGS["well_id"]},
            },
        ),
        (
            "study-area",
            "geojson",
            "application/geo+json",
            "编排演示－虚构研究区边界",
            "包含全部演示井点的单一矩形区域，不是行政边界或真实含水层边界。",
            {
                "feature_count": 1,
                "geometry": "Polygon",
                "crs": dict(crs),
                "ogr_layer_name": "polygons",
                "field_units": {"area_id": "无量纲标识", "name": "文本"},
                "field_meanings": {
                    "area_id": "虚构研究区编号。",
                    "name": "模拟区域名称。",
                },
            },
        ),
        (
            "scalar-raster",
            "geotiff",
            "image/tiff",
            "编排演示－确定性标量栅格",
            "25×20 单波段无量纲梯度，EPSG:32650、200 米像元；不是地下水位面、DEM或遥感影像。",
            {
                "width": 25,
                "height": 20,
                "band_count": 1,
                "value_unit": "dimensionless",
                "crs": {"horizontal": "EPSG:32650", "coordinate_unit": "m"},
                "extent": [499500, 3499500, 504500, 3503500],
            },
        ),
        (
            "quality-centimetres",
            "csv",
            "text/csv",
            "编排异常－厘米水位值",
            "从统一监测表派生，数值整体乘以100且实际单位为厘米；字段旧名仍含 _m，故不能按米直接分析。",
            {
                "quality_variant": "CENTIMETRES",
                "parent_checksum": parent_monitoring,
                "row_count": 720,
                "field_units": {
                    "water_level_m": "cm",
                    "declared_value_unit": "文本常量 cm",
                },
                "limitations": [
                    "当前安装目录没有数值仿射单位换算算子；不得改用父资源绕过此质量变体。"
                ],
            },
        ),
        (
            "quality-alias",
            "csv",
            "text/csv",
            "编排异常－未解析井号别名",
            "从统一监测表派生，W001/W002 被替换为 001/DEMO-002；本资源不携带可核验的别名映射。",
            {
                "quality_variant": "UNRESOLVED_WELL_ALIAS",
                "parent_checksum": parent_monitoring,
                "row_count": 720,
                "identity_status": "UNRESOLVED",
                "limitations": [
                    "同区域、名称相似和前导零不能证明实体相同；需另行观察权威别名映射。"
                ],
            },
        ),
        (
            "quality-invalid-numbers",
            "csv",
            "text/csv",
            "编排异常－非法数值字面量",
            "从统一监测表派生，文件开头四条 water_level_m 分别为非数值文本、NaN、Infinity 与 -Infinity。",
            {
                "quality_variant": "INVALID_NUMBERS",
                "parent_checksum": parent_monitoring,
                "row_count": 720,
                "invalid_field": "water_level_m",
                "invalid_row_count": 4,
                "inspection_scope": "字典声明来自确定性生成器；表格 HEAD_SAMPLE 不是全文件质量审计。",
            },
        ),
        (
            "quality-unknown-crs",
            "csv",
            "text/csv",
            "编排异常－未声明坐标系点表",
            "20 行 well_id/x/y 坐标值，但发布资料没有可核验 CRS、轴顺序或坐标单位声明。",
            {
                "quality_variant": "MISSING_CRS",
                "parent_checksum": _checksum(files["authoring-wells.geojson"]),
                "row_count": 20,
                "fields": ["well_id", "x", "y"],
                "crs": None,
                "limitations": ["不得从数值范围、文件名或同区域资料猜测 CRS。"],
            },
        ),
        (
            "quality-illegal-rules",
            "json",
            "application/json",
            "编排异常－非法排序规则",
            "声明 table-sort-rules.v1，但包含 Schema 禁止的 unexpected 字段。",
            {
                "quality_variant": "ILLEGAL_RULE_SCHEMA",
                "parent_checksum": _checksum(files["authoring-sort-rules.json"]),
                "rule_schema_version": "table-sort-rules.v1",
                "schema_status": "INVALID_ADDITIONAL_PROPERTY",
                "limitations": ["必须替换为通过严格 Schema 校验的独立规则资源。"],
            },
        ),
    ]
    return [
        {
            "key": key,
            "file_name": f"authoring-{key}.{fmt}",
            "name": name,
            "description": f"{NOTICE}{description}",
            "media_type": media_type,
            "format": fmt,
            "content": files[f"authoring-{key}.{fmt}"],
            "declaration": {
                "schemaVersion": "workflow-authoring-demo-declaration.v1",
                "generator_version": VERSION,
                "generator_seed": SEED,
                "synthetic": True,
                "dataset_id": "groundwater-synthetic-v5",
                "assumptions": [
                    NOTICE,
                    "使用固定种子生成数值并打乱监测行顺序；不含观测缺测、真实校准或水文地质实证。",
                ],
                **declaration,
            },
        }
        for key, fmt, media_type, name, description, declaration in entries
    ]
