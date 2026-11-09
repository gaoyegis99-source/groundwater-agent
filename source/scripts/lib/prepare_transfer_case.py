"""Prepare the deterministic workbook/GIS transfer case without network access."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import date
from openpyxl import load_workbook


LAYERS = {
    "counties": "GIS0065__县界",
    "townships": "GIS0064__乡镇界",
    "rivers": "GIS0066__盆地水系",
    "groundwater_systems": "GIS0068__盆地地下水系统细分",
    "railways": "GIS0074__铁路",
    "basin_boundary": "GIS0059__张家口盆地范围",
}
MEDIA = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "json": "application/json",
    "geopackage": "application/geopackage+sqlite3",
    "geotiff": "image/tiff",
}


def save(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )


def checksum(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def validate_dataset(value):
    if not isinstance(value, dict) or set(value) != {"name", "description", "tags", "origin"}:
        raise ValueError("DATASET_DECLARATION_INVALID")
    if any(not isinstance(value[key], str) or not value[key].strip() for key in ("name", "description", "origin")):
        raise ValueError("DATASET_DECLARATION_INVALID")
    if not isinstance(value["tags"], list) or any(not isinstance(tag, str) or not tag.strip() for tag in value["tags"]):
        raise ValueError("DATASET_DECLARATION_INVALID")
    return value


def prepare(
    data_root, workbook, imagery, output, source_region="CBH", target_region="YZB", *, dataset
):
    dataset = validate_dataset(dataset)
    output.mkdir(parents=True, exist_ok=False)
    book = load_workbook(workbook, read_only=True, data_only=True)
    try:
        sheet = book["Sheet1"]
        rows = list(sheet.values)
    finally:
        book.close()
    headers = list(rows[0])
    series = {"water_depth": {}, "precipitation": {}}
    for header in headers:
        match = re.fullmatch(r"W(\d{4})-(\d{1,2})-(\d{1,2})", str(header))
        if match:
            moment = date(*map(int, match.groups()))
            if moment.day != 1:
                raise ValueError("Monthly water-level dates must be month starts")
            series["water_depth"][moment.isoformat()] = header
        match = re.fullmatch(r"Pre(\d{4})(\d{2})", str(header))
        if match:
            series["precipitation"][date(*map(int, match.groups()), 1).isoformat()] = (
                header
            )
    if not series["water_depth"] or set(series["water_depth"]) != set(
        series["precipitation"]
    ):
        raise ValueError("Water-level and precipitation month mappings differ")
    regions = Counter(row[headers.index("Region")] for row in rows[1:])
    if not regions[source_region] or not regions[target_region]:
        raise ValueError("Both selected regions must contain wells")
    rules = {
        "monthMapping": {
            "schemaVersion": "table-timeseries-unpivot.v1",
            "idFields": ["Region", "Well_ID", "Lon", "Lat"],
            "uniqueKeys": ["Region", "Well_ID"],
            "series": [
                {
                    "field": field,
                    "columns": [{"time": t, "column": cols[t]} for t in sorted(cols)],
                }
                for field, cols in series.items()
            ],
        },
        "sourceFilter": {
            "schemaVersion": "table-filter-rules.v1",
            "condition": {"field": "Region", "op": "eq", "value": source_region},
        },
        "targetFilter": {
            "schemaVersion": "table-filter-rules.v1",
            "condition": {"field": "Region", "op": "eq", "value": target_region},
        },
        **{
            name + "-scaler-rules": {
                "schemaVersion": "standard-scaler-fields.v1",
                "fields": list(series),
            }
            for name in ["source", "target"]
        },
        **{
            name + "-windows-rules": {
                "schemaVersion": "supervised-window-fields.v1",
                "featureFields": list(series),
                "carryFields": ["Lon", "Lat"],
            }
            for name in ["source", "target", "holdout"]
        },
        "inverse-rules": {
            "schemaVersion": "standard-scaler-fields.v1",
            "fields": ["actual", "predicted", "persistence"],
        },
        "imagery-rules": {
            "schemaVersion": "raster-window-bands.v1",
            "bands": [1, 2, 3],
        },
    }
    paths = {
        "workbook": (workbook.resolve(), "xlsx"),
        "imagery": (imagery.resolve(), "geotiff"),
        "boundary": (
            data_root
            / "00_标准化数据/06_水源地面状边界/v1/03_腰站堡/腰站堡_面状边界.gpkg",
            "geopackage",
        ),
    }
    vector_root = (
        data_root
        / "00_标准化数据/05_统一坐标系/v1/01_矢量_GeoPackage/07_工程模型与空间底图"
    )
    paths.update(
        {
            name: (vector_root / (layer + ".gpkg"), "geopackage")
            for name, layer in LAYERS.items()
        }
    )
    for name, rule in rules.items():
        path = output / (name + ".json")
        save(path, rule)
        paths[name] = (path.resolve(), "json")
    resources = {
        name: {
            "path": str(path.resolve()),
            "format": fmt,
            "mediaType": MEDIA[fmt],
            "sizeBytes": path.stat().st_size,
            "checksum": checksum(path),
        }
        for name, (path, fmt) in paths.items()
    }
    count = len(series["water_depth"])
    train = int(count * 0.8)
    manifest = {
        "schemaVersion": "transfer-case-inputs.v2",
        "dataset": dataset,
        "resources": resources,
        "expected": {
            "sourceWells": regions[source_region],
            "targetWells": regions[target_region],
            "months": count,
            "trainMonths": train,
            "holdoutMonths": count - train,
            "predictions": regions[target_region] * (count - train),
        },
        "parameters": {"map-month.targetTime": max(series["water_depth"])},
    }
    save(output / "inputs.json", manifest)
    return manifest
