"""Small synthetic transfer case; separate from the 32/14-well historical case."""

from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import sqlite3

from openpyxl import Workbook

from scripts.lib.prepare_transfer_case import LAYERS, prepare

FIXTURE_ROOT = (
    Path(__file__).resolve().parents[3]
    / "packages/operator-pack/catalog/fixtures/data"
)


def _copy_vector_fixture(path: Path, layer: str) -> None:
    """Clone the locked transfer polygon under the workflow's layer name."""

    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURE_ROOT / "transfer-boundary.gpkg", path)
    with sqlite3.connect(path) as database:
        row = database.execute(
            "SELECT table_name FROM gpkg_geometry_columns"
        ).fetchone()
        if row is None:
            raise ValueError("TRANSFER_VECTOR_FIXTURE_INVALID")
        original = str(row[0])
        old_identifier = '"' + original.replace('"', '""') + '"'
        new_identifier = '"' + layer.replace('"', '""') + '"'
        database.execute(
            f"ALTER TABLE {old_identifier} RENAME TO {new_identifier}"
        )
        for table in (
            "gpkg_contents",
            "gpkg_geometry_columns",
            "gpkg_ogr_contents",
        ):
            database.execute(
                f"UPDATE {table} SET table_name = ? WHERE table_name = ?",
                (layer, original),
            )
        database.execute(
            "UPDATE gpkg_contents SET identifier = ? WHERE table_name = ?",
            (layer, layer),
        )


def prepare_fixture(directory):
    directory.mkdir(parents=True)
    book = Workbook()
    sheet = book.active
    sheet.title = "Sheet1"
    months = [f"{2020 + i // 12}-{i % 12 + 1:02}-01" for i in range(16)]
    sheet.append(
        [
            "Region",
            "Well_ID",
            "Lon",
            "Lat",
            *["W" + t for t in months],
            *["Pre" + t[:7].replace("-", "") for t in months],
        ]
    )
    for index, (region, well) in enumerate(
        [("CBH", "s2"), ("CBH", "s1"), ("YZB", "t2"), ("YZB", "t1")]
    ):
        sheet.append(
            [
                region,
                well,
                500000 + index * 2,
                4500000 + index * 2,
                *[10 + index + i * 0.2 + math.sin(i) / 4 for i in range(16)],
                *[20 + i * 0.7 + index for i in range(16)],
            ]
        )
    workbook = directory / "observations.xlsx"
    book.save(workbook)
    book.close()
    data_root = directory / "data"
    boundary = (
        data_root / "00_标准化数据/06_水源地面状边界/v1/03_腰站堡/腰站堡_面状边界.gpkg"
    )
    vectors = (
        data_root
        / "00_标准化数据/05_统一坐标系/v1/01_矢量_GeoPackage/07_工程模型与空间底图"
    )
    for path, layer in [
        (boundary, "water_source_boundaries"),
        *[(vectors / (v + ".gpkg"), v) for v in LAYERS.values()],
    ]:
        _copy_vector_fixture(path, layer)
    imagery = directory / "rgb.tif"
    shutil.copyfile(FIXTURE_ROOT / "transfer-rgb.tif", imagery)
    prepared = directory / "prepared"
    manifest = prepare(data_root, workbook, imagery, prepared, dataset={
        "name": "迁移学习算法回归夹具",
        "description": "独立合成 2+2 井、16 月算法回归，不是原始腰站堡资料，也不是统一模拟包 v5 的核心观测。",
        "origin": "SYNTHETIC", "tags": ["synthetic", "algorithm-regression"],
    })
    manifest["parameters"].update(
        {
            **{
                name + ".inputWindow": 3
                for name in [
                    "source-split",
                    "source-windows",
                    "target-split",
                    "target-windows",
                    "holdout-windows",
                ]
            },
            "pretrain.epochs": 2,
            "finetune.epochs": 2,
            "pretrain.hiddenSize": 8,
            "pretrain.numHeads": 2,
            "pretrain.numLayers": 1,
            "pretrain.dropout": 0,
            "extent.paddingMeters": 10,
            "prediction-points.sourceCrs": "EPSG:32650",
            "map-points.sourceCrs": "EPSG:32650",
        }
    )
    (prepared / "inputs.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    return prepared
