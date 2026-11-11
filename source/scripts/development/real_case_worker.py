from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import date, datetime
import hashlib
import json
import math
from pathlib import Path
import statistics
from typing import Any


FOOT_TO_METRE = 0.3048
FACTORS = ("D", "R", "A", "S", "T", "I", "C")
ODNR_FIELDS = {
    "D": "DTWRating",
    "R": "RECRating",
    "A": "AQRating",
    "S": "SORating",
    "T": "TOPRating",
    "I": "VADRating",
    "C": "HYCORating",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def prepare_local(source: Path, output: Path, stats_path: Path) -> None:
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    table = pq.read_table(
        source,
        columns=["station_id", "month", "metric_id", "value", "unit_code"],
    )
    start = date(2018, 1, 1)
    baseline_end = date(2021, 12, 31)
    comparison_start = date(2022, 1, 1)
    end = date(2025, 12, 31)
    metric = "water_level_elevation_monthly_mean"
    selected = table.filter(
        pc.and_(
            pc.equal(table["metric_id"], metric),
            pc.and_(
                pc.greater_equal(table["month"], start),
                pc.less_equal(table["month"], end),
            ),
        )
    )
    rows = selected.to_pylist()
    if {row["unit_code"] for row in rows} != {"m"}:
        raise ValueError("LOCAL_UNIT_INVALID")
    baseline_counts: Counter[str] = Counter()
    comparison_counts: Counter[str] = Counter()
    for row in rows:
        if row["month"] <= baseline_end:
            baseline_counts[row["station_id"]] += 1
        elif row["month"] >= comparison_start:
            comparison_counts[row["station_id"]] += 1
    eligible = {
        station
        for station in baseline_counts
        if baseline_counts[station] >= 24 and comparison_counts[station] >= 24
    }
    retained = sorted(
        (row for row in rows if row["station_id"] in eligible),
        key=lambda row: (row["station_id"], row["month"]),
    )
    keys = [(row["station_id"], row["month"]) for row in retained]
    if len(keys) != len(set(keys)):
        raise ValueError("LOCAL_DUPLICATE_GRAIN")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["station", "time", "value"])
        writer.writeheader()
        for row in retained:
            writer.writerow(
                {
                    "station": row["station_id"],
                    "time": row["month"].isoformat(),
                    "value": format(float(row["value"]), ".15g"),
                }
            )
    _write_json(
        stats_path,
        {
            "schemaVersion": "real-case-preparation.v1",
            "case": "local-monitoring-trend",
            "metric": metric,
            "unit": "m",
            "sourceRows": table.num_rows,
            "candidateRows": len(rows),
            "retainedRows": len(retained),
            "eligibleStationCount": len(eligible),
            "eligibilityRule": "at least 24 monthly values in both 2018-2021 and 2022-2025",
            "duplicateStationMonthCount": 0,
            "outputSha256": _sha256(output),
        },
    )


def _parse_usgs_date(value: str) -> date:
    text = value.strip()
    for pattern in ("%Y%m%d", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    raise ValueError("MRVA_DATE_INVALID")


def prepare_mrva(raw: Path, flow: Path, kriging: Path, stats_path: Path) -> None:
    required = {
        "X",
        "Y",
        "SITE_BADGE",
        "ALT_88",
        "WLA88_2024",
        "D2W_2024",
        "DATE_2024",
        "USE_2024",
    }
    with raw.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or not required <= set(reader.fieldnames):
            raise ValueError("MRVA_SCHEMA_INVALID")
        source_rows = list(reader)
    groups: dict[tuple[float, float], list[dict[str, Any]]] = defaultdict(list)
    residuals: list[float] = []
    identifiers: set[str] = set()
    for row in source_rows:
        if float(row["USE_2024"]) <= 0:
            continue
        identifier = row["SITE_BADGE"].strip()
        if not identifier or identifier in identifiers:
            raise ValueError("MRVA_IDENTIFIER_INVALID")
        identifiers.add(identifier)
        x = round(float(row["X"]), 6)
        y = round(float(row["Y"]), 6)
        altitude = float(row["ALT_88"])
        depth = float(row["D2W_2024"])
        head = float(row["WLA88_2024"])
        if not all(math.isfinite(value) for value in (x, y, altitude, depth, head)):
            raise ValueError("MRVA_VALUE_INVALID")
        residuals.append(abs((altitude - depth) - head))
        groups[(x, y)].append(
            {
                "id": identifier,
                "date": _parse_usgs_date(row["DATE_2024"]),
                "altitude": altitude,
                "depth": depth,
                "head": head,
            }
        )
    if not groups or max(residuals) > 0.01:
        raise ValueError("MRVA_HEAD_IDENTITY_INVALID")
    flow.parent.mkdir(parents=True, exist_ok=True)
    with (
        flow.open("w", encoding="utf-8", newline="") as flow_stream,
        kriging.open("w", encoding="utf-8", newline="") as kriging_stream,
    ):
        flow_writer = csv.DictWriter(
            flow_stream,
            fieldnames=[
                "well_id",
                "time",
                "x",
                "y",
                "reference_elevation_m",
                "depth_to_water_m",
            ],
        )
        kriging_writer = csv.DictWriter(
            kriging_stream,
            fieldnames=["x", "y", "head_m", "source_well_count"],
        )
        flow_writer.writeheader()
        kriging_writer.writeheader()
        flow_coordinate_count = 0
        negative_depth_wells_excluded = 0
        for index, ((x, y), members) in enumerate(sorted(groups.items()), start=1):
            altitude = statistics.fmean(item["altitude"] for item in members)
            depth = statistics.fmean(item["depth"] for item in members)
            head = statistics.fmean(item["head"] for item in members)
            if depth >= 0:
                flow_coordinate_count += 1
                flow_writer.writerow(
                    {
                        "well_id": f"MRVA-{index:04d}",
                        "time": max(item["date"] for item in members).isoformat(),
                        "x": format(x, ".6f"),
                        "y": format(y, ".6f"),
                        "reference_elevation_m": format(
                            altitude * FOOT_TO_METRE, ".12g"
                        ),
                        "depth_to_water_m": format(depth * FOOT_TO_METRE, ".12g"),
                    }
                )
            else:
                negative_depth_wells_excluded += len(members)
            kriging_writer.writerow(
                {
                    "x": format(x, ".6f"),
                    "y": format(y, ".6f"),
                    "head_m": format(head * FOOT_TO_METRE, ".12g"),
                    "source_well_count": len(members),
                }
            )
    _write_json(
        stats_path,
        {
            "schemaVersion": "real-case-preparation.v1",
            "sourceGroup": "usgs-mrva-2024",
            "selectedWellCount": len(identifiers),
            "uniqueCoordinateCount": len(groups),
            "duplicateCoordinateRowsAggregated": len(identifiers) - len(groups),
            "flowCoordinateCount": flow_coordinate_count,
            "negativeDepthWellsExcludedFromFlow": negative_depth_wells_excluded,
            "negativeDepthPolicy": (
                "Excluded from head.calculate flow workflow because the production "
                "operator rejects negative depth; retained in kriging as published head."
            ),
            "maximumHeadIdentityResidualFeet": max(residuals),
            "coordinateCrs": "EPSG:5070",
            "headDatum": "NAVD88",
            "flowOutputSha256": _sha256(flow),
            "krigingOutputSha256": _sha256(kriging),
        },
    )


def _quantile(sorted_values: list[int], probability: float) -> float:
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(sorted_values[lower])
    fraction = position - lower
    return sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction


def prepare_drastic(attributes: Path, config: Path, stats_path: Path) -> None:
    required = {*ODNR_FIELDS.values(), "GVIndex"}
    with attributes.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or not required <= set(reader.fieldnames):
            raise ValueError("ODNR_SCHEMA_INVALID")
        rows = list(reader)
    weights = {"D": 5, "R": 4, "A": 3, "S": 2, "T": 1, "I": 5, "C": 3}
    valid_indices: list[int] = []
    formula_mismatches = 0
    for row in rows:
        factors = {key: int(row[field]) for key, field in ODNR_FIELDS.items()}
        published = int(row["GVIndex"])
        if all(value > 0 for value in factors.values()):
            calculated = sum(factors[key] * weights[key] for key in FACTORS)
            formula_mismatches += calculated != published
            valid_indices.append(published)
    if not valid_indices or formula_mismatches:
        raise ValueError("ODNR_INDEX_IDENTITY_INVALID")
    ordered = sorted(valid_indices)
    q1, q2, q3 = (_quantile(ordered, probability) for probability in (0.25, 0.5, 0.75))
    source_url = "https://gis.ohiodnr.gov/geodata/Franklin/GV_Franklin.zip"
    guide_url = (
        "https://dam.assets.ohio.gov/image/upload/ohiodnr.gov/documents/geology/"
        "OFR2022_1_Nelson_2022.pdf"
    )
    rating_entries: dict[str, Any] = {}
    for factor, field in ODNR_FIELDS.items():
        rating_entries[factor] = {
            "band": 1,
            "unit": "published integer rating",
            "basis": f"ODNR Franklin County field {field}; identity mapping",
            "source": {
                "id": f"odnr-franklin-{field}",
                "title": f"ODNR Franklin County {field}",
                "version": "2022",
                "locator": f"{source_url}#field={field}",
                "verificationStatus": "VERIFIED",
            },
            "rules": [
                {
                    "ruleId": f"{factor}-{rating}",
                    "rating": rating,
                    "minimum": rating,
                    "maximum": rating,
                    "minimumInclusive": True,
                    "maximumInclusive": True,
                }
                for rating in range(1, 11)
            ],
        }
    classes = []
    lower: float = 0.0
    labels = (
        "Lower dataset-relative quartile",
        "Lower-middle dataset-relative quartile",
        "Upper-middle dataset-relative quartile",
        "Upper dataset-relative quartile",
    )
    for index, (upper, label) in enumerate(
        zip((q1, q2, q3, 230.0), labels, strict=True), start=1
    ):
        classes.append(
            {
                "code": index,
                "label": label,
                "order": index,
                "minimum": lower,
                "maximum": upper,
                "minimumInclusive": index == 1,
                "maximumInclusive": True,
            }
        )
        lower = upper
    payload = {
        "schemaVersion": "groundwater-drastic-config.v1",
        "configId": "odnr-franklin-county-2022",
        "title": "ODNR Franklin County published DRASTIC ratings",
        "version": "1.0.0",
        "method": "weighted-sum",
        "weights": {
            "source": {
                "id": "odnr-drastic-weights-2022",
                "title": "Ohio DRASTIC groundwater pollution potential mapping guide",
                "version": "2022",
                "locator": guide_url,
                "verificationStatus": "VERIFIED",
            },
            "values": weights,
        },
        "ratings": rating_entries,
        "classification": {
            "source": {
                "id": "odnr-franklin-development-quartiles",
                "title": "Verified polygon-record quartiles; development visualization only",
                "version": "1.0.0",
                "locator": f"derived:sha256:{_sha256(attributes)}",
                "verificationStatus": "VERIFIED",
            },
            "classes": classes,
        },
    }
    _write_json(config, payload)
    _write_json(
        stats_path,
        {
            "schemaVersion": "real-case-preparation.v1",
            "sourceGroup": "odnr-franklin-drastic",
            "polygonRecordCount": len(rows),
            "fullyRatedRecordCount": len(valid_indices),
            "publishedFormulaMismatchCount": formula_mismatches,
            "publishedIndexMinimum": min(valid_indices),
            "publishedIndexMaximum": max(valid_indices),
            "developmentQuartileBreaks": [q1, q2, q3],
            "classificationAuthority": "DERIVED_NOT_ODNR_OFFICIAL",
            "configSha256": _sha256(config),
        },
    )


def prepare_odnr_source(shapefile: Path, output: Path, stats_path: Path) -> None:
    import numpy as np
    import pyogrio
    from pyproj import CRS
    import rasterio
    from rasterio.features import rasterize
    from rasterio.transform import from_origin
    import shapely

    output.mkdir(parents=True, exist_ok=True)
    fields = [*ODNR_FIELDS.values(), "GVIndex"]
    metadata, table = pyogrio.read_arrow(shapefile, columns=fields)
    geometry_name = metadata.get("geometry_name")
    if not geometry_name:
        geometry_columns = [
            field.name
            for field in table.schema
            if (field.metadata or {}).get(b"ARROW:extension:name")
            == b"geoarrow.wkb"
        ]
        geometry_name = geometry_columns[0] if len(geometry_columns) == 1 else None
    if (
        not isinstance(geometry_name, str)
        or geometry_name not in table.column_names
        or CRS.from_user_input(metadata.get("crs")).to_epsg() != 3735
        or table.num_rows != 786
    ):
        raise ValueError("ODNR_SPATIAL_SOURCE_INVALID")
    geometries = shapely.from_wkb(table[geometry_name].to_numpy())
    if bool(np.any(shapely.is_missing(geometries))):
        raise ValueError("ODNR_SPATIAL_SOURCE_INVALID")
    attributes = output / "published-attributes.csv"
    with attributes.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        columns = {field: table[field].to_pylist() for field in fields}
        for index in range(table.num_rows):
            writer.writerow({field: columns[field][index] for field in fields})
    transform = from_origin(1755000.0, 782000.0, 1000.0, 1000.0)
    profile = {
        "driver": "GTiff",
        "height": 129,
        "width": 141,
        "count": 1,
        "dtype": "int16",
        "crs": "EPSG:3735",
        "transform": transform,
        "nodata": -9999,
        "compress": "deflate",
    }
    raster_fields = {**ODNR_FIELDS, "published-gv-index": "GVIndex"}
    for name, field in raster_fields.items():
        values = table[field].to_pylist()
        array = rasterize(
            zip(geometries, values, strict=True),
            out_shape=(profile["height"], profile["width"]),
            fill=-9999,
            transform=transform,
            all_touched=False,
            dtype="int16",
        )
        target = output / f"{name}.tif"
        with rasterio.open(target, "w", **profile) as dataset:
            dataset.write(array, 1)
    config = output / "config.json"
    prepare_drastic(attributes, config, stats_path)
    evidence = json.loads(stats_path.read_text(encoding="utf-8"))
    evidence.update(
        {
            "spatialSourceCrs": "EPSG:3735",
            "gridWidth": profile["width"],
            "gridHeight": profile["height"],
            "pixelSizeUsSurveyFeet": 1000,
            "publishedIndexRasterSha256": _sha256(output / "published-gv-index.tif"),
        }
    )
    _write_json(stats_path, evidence)


def validate_drastic(computed: Path, reference: Path, output: Path) -> None:
    import numpy as np
    import rasterio

    with rasterio.open(computed) as actual, rasterio.open(reference) as expected:
        if (
            actual.crs,
            actual.transform,
            actual.width,
            actual.height,
        ) != (expected.crs, expected.transform, expected.width, expected.height):
            raise ValueError("ODNR_VALIDATION_GRID_MISMATCH")
        actual_values = actual.read(1, masked=True)
        expected_values = expected.read(1, masked=True)
        valid = ~np.ma.getmaskarray(actual_values) & ~np.ma.getmaskarray(
            expected_values
        )
        differences = np.abs(
            np.asarray(actual_values.data, dtype=np.float64)[valid]
            - np.asarray(expected_values.data, dtype=np.float64)[valid]
        )
        maximum = float(np.max(differences)) if differences.size else math.inf
        mismatches = int(np.count_nonzero(differences > 1e-6))
    payload = {
        "schemaVersion": "real-case-validation.v1",
        "validation": "published-gv-index",
        "status": "PASS" if differences.size and mismatches == 0 else "FAIL",
        "comparedCellCount": int(differences.size),
        "mismatchCellCount": mismatches,
        "maximumAbsoluteDifference": maximum,
        "tolerance": 1e-6,
    }
    _write_json(output, payload)
    if payload["status"] != "PASS":
        raise ValueError("ODNR_VALIDATION_FAILED")


def validate_mrva(computed: Path, reference: Path, output: Path) -> None:
    import numpy as np
    import rasterio
    from rasterio.warp import Resampling, reproject

    with rasterio.open(computed) as actual, rasterio.open(reference) as expected:
        if actual.crs is None or expected.crs is None:
            raise ValueError("MRVA_VALIDATION_CRS_REQUIRED")
        actual_values = actual.read(1, masked=True)
        reference_metres = np.full(
            (actual.height, actual.width), np.nan, dtype="float64"
        )
        reproject(
            source=expected.read(1),
            destination=reference_metres,
            src_transform=expected.transform,
            src_crs=expected.crs,
            src_nodata=expected.nodata,
            dst_transform=actual.transform,
            dst_crs=actual.crs,
            dst_nodata=np.nan,
            resampling=Resampling.bilinear,
        )
        reference_metres *= FOOT_TO_METRE
        actual_data = np.asarray(actual_values.data, dtype=np.float64)
        valid = (
            ~np.ma.getmaskarray(actual_values)
            & np.isfinite(actual_data)
            & np.isfinite(reference_metres)
        )
        differences = actual_data[valid] - reference_metres[valid]
        count = int(differences.size)
        rmse = float(np.sqrt(np.mean(np.square(differences)))) if count else math.inf
        mae = float(np.mean(np.abs(differences))) if count else math.inf
        bias = float(np.mean(differences)) if count else math.inf
    payload = {
        "schemaVersion": "real-case-validation.v1",
        "validation": "official-potentiometric-raster",
        "status": "PASS" if count else "FAIL",
        "comparison": "10 km kriging minus bilinearly sampled USGS 1 km raster",
        "comparedCellCount": count,
        "rmseMetres": rmse,
        "meanAbsoluteErrorMetres": mae,
        "biasMetres": bias,
        "acceptancePolicy": "coverage gate only; errors are reported, not tuned against",
    }
    _write_json(output, payload)
    if payload["status"] != "PASS":
        raise ValueError("MRVA_VALIDATION_FAILED")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    local = subparsers.add_parser("prepare-local")
    local.add_argument("source", type=Path)
    local.add_argument("output", type=Path)
    local.add_argument("stats", type=Path)
    mrva = subparsers.add_parser("prepare-mrva")
    mrva.add_argument("raw", type=Path)
    mrva.add_argument("flow", type=Path)
    mrva.add_argument("kriging", type=Path)
    mrva.add_argument("stats", type=Path)
    drastic = subparsers.add_parser("prepare-drastic")
    drastic.add_argument("attributes", type=Path)
    drastic.add_argument("config", type=Path)
    drastic.add_argument("stats", type=Path)
    odnr = subparsers.add_parser("prepare-odnr-source")
    odnr.add_argument("shapefile", type=Path)
    odnr.add_argument("output", type=Path)
    odnr.add_argument("stats", type=Path)
    validate_d = subparsers.add_parser("validate-drastic")
    validate_d.add_argument("computed", type=Path)
    validate_d.add_argument("reference", type=Path)
    validate_d.add_argument("output", type=Path)
    validate_m = subparsers.add_parser("validate-mrva")
    validate_m.add_argument("computed", type=Path)
    validate_m.add_argument("reference", type=Path)
    validate_m.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    if args.command == "prepare-local":
        prepare_local(args.source, args.output, args.stats)
    elif args.command == "prepare-mrva":
        prepare_mrva(args.raw, args.flow, args.kriging, args.stats)
    elif args.command == "prepare-drastic":
        prepare_drastic(args.attributes, args.config, args.stats)
    elif args.command == "prepare-odnr-source":
        prepare_odnr_source(args.shapefile, args.output, args.stats)
    elif args.command == "validate-drastic":
        validate_drastic(args.computed, args.reference, args.output)
    else:
        validate_mrva(args.computed, args.reference, args.output)


if __name__ == "__main__":
    main()
