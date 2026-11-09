from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
import random
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as parquet
import pytest

from moperators.operators import table_filter, table_project, table_sort
from moperators.operators.table_document_common_v1 import read_rules
from scripts.lib.workflow_authoring_demo import (
    SEED,
    build_core,
    build_demo_assets,
    build_demo_files,
)


def test_assets_are_byte_reproducible_and_declarations_contain_only_source_facts():
    state = random.getstate()
    first = build_demo_files()
    assert build_demo_files() == first
    assert random.getstate() == state
    assets = build_demo_assets()
    assert {item["file_name"]: item["content"] for item in assets} == first
    assert len(assets) == 14
    assert sum(map(len, first.values())) < 800_000
    assert {item["key"] for item in assets} == {
        "monitoring", "project-rules", "filter-rules", "sort-rules", "join-rules", "drivers",
        "wells", "study-area", "scalar-raster", "quality-centimetres", "quality-alias",
        "quality-invalid-numbers", "quality-unknown-crs", "quality-illegal-rules",
    }
    for item in assets:
        declaration = item["declaration"]
        assert declaration["synthetic"] is True and declaration["generator_seed"] == SEED
        assert declaration["assumptions"]
        assert not {"resource_id", "workflow", "model_id", "tool", "arguments", "expected", "recommended"} & declaration.keys()
    assets[0]["declaration"]["field_units"]["water_level_m"] = "changed"
    assert build_demo_assets()[0]["declaration"]["field_units"]["water_level_m"] == "m"


def test_rule_files_pass_real_loaders_and_operate_on_the_generated_monitoring_table(tmp_path):
    files = build_demo_files()
    for name, content in files.items():
        (tmp_path / name).write_bytes(content)
    inputs = {
        key: SimpleNamespace(path=tmp_path / filename, format_id=filename.rsplit(".", 1)[1])
        for key, filename in [("monitoring", "authoring-monitoring.csv"), *[
            (key, f"authoring-{key}-rules.json") for key in ("project", "filter", "sort")
        ]]
    }
    for name in ("project", "filter", "sort"):
        assert read_rules(inputs[name], f"table-{name}-rules.v1")["schemaVersion"] == f"table-{name}-rules.v1"
    for name in ("project", "filter", "sort"):
        (tmp_path / name).mkdir()
    projected = table_project.run({"data": [inputs["monitoring"]], "rules": [inputs["project"]]}, {}, tmp_path / "project")
    projected_table = parquet.read_table(projected["data"]["path"])
    assert projected_table.num_rows == 720
    assert projected_table.schema.field("water_level_m").type == pa.float64()
    assert projected_table.schema.field("observed_at").type == pa.date32()
    data = SimpleNamespace(path=Path(projected["data"]["path"]), format_id="parquet")
    filtered = table_filter.run({"data": [data], "rules": [inputs["filter"]]}, {}, tmp_path / "filter")
    matched = parquet.read_table(filtered["matched"]["path"]).to_pylist()
    rejected = parquet.read_table(filtered["rejected"]["path"]).to_pylist()
    assert matched and rejected and len(matched) + len(rejected) == 720
    assert all(row["quality_flag"] == "simulated_valid" for row in matched)
    assert all(row["quality_flag"] == "simulated_review" for row in rejected)
    data = SimpleNamespace(path=Path(filtered["matched"]["path"]), format_id="parquet")
    ordered = table_sort.run({"data": [data], "rules": [inputs["sort"]]}, {}, tmp_path / "sort")
    actual = parquet.read_table(ordered["data"]["path"]).to_pylist()
    assert actual == sorted(matched, key=lambda row: (row["observed_at"], row["well_id"]))


def test_spatial_files_and_monitoring_share_declared_coordinates_and_identifiers():
    files = build_demo_files()
    rows = list(csv.DictReader(io.StringIO(files["authoring-monitoring.csv"].decode("utf-8"))))
    wells = json.loads(files["authoring-wells.geojson"])
    area = json.loads(files["authoring-study-area.geojson"])
    assert wells["type"] == area["type"] == "FeatureCollection"
    assert len(wells["features"]) == 20 and len(rows) == 720
    by_well = {feature["properties"]["well_id"]: feature["geometry"]["coordinates"] for feature in wells["features"]}
    assert len({(row["well_id"], row["observed_at"]) for row in rows}) == len(rows)
    assert {row["observed_at"] for row in rows} == {f"{year}-{month:02d}-01" for year in range(2023, 2026) for month in range(1, 13)}
    for row in rows:
        assert by_well[row["well_id"]] == [float(row["longitude_deg"]), float(row["latitude_deg"])]
        assert all(value != "" for value in row.values())
    ring = area["features"][0]["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1] and len(ring) == 5
    for x, y in by_well.values():
        assert min(point[0] for point in ring) < x < max(point[0] for point in ring)
        assert min(point[1] for point in ring) < y < max(point[1] for point in ring)
    declarations = {item["key"]: item["declaration"] for item in build_demo_assets()}
    assert declarations["monitoring"]["crs"] == declarations["wells"]["crs"] == declarations["study-area"]["crs"]
    assert declarations["wells"]["crs"]["horizontal"] == "EPSG:4326"
    assert declarations["monitoring"]["field_units"]["water_level_m"] == "m"


def test_authoring_is_an_exact_projection_of_the_shared_twenty_well_core():
    rows = list(csv.DictReader(io.StringIO(build_demo_files()["authoring-monitoring.csv"].decode())))
    core = build_core()
    by_key = {(row["well_id"], row["observed_at"]): row for row in core["observations"]}
    assert len(by_key) == 720 and len(core["stations"]) == 20
    assert {row["well_id"] for row in rows} == {f"W{i:03}" for i in range(1, 21)}
    assert [(row["well_id"], row["observed_at"]) for row in rows] != list(by_key)
    for row in rows:
        canonical = by_key[row["well_id"], row["observed_at"]]
        for field, actual in row.items():
            assert (float(actual) if isinstance(canonical[field], float) else actual) == canonical[field]


def test_quality_resources_are_explicit_children_and_keep_detection_limits_honest(tmp_path):
    files = build_demo_files()
    assets = {item["key"]: item for item in build_demo_assets()}
    parent = "sha256:" + hashlib.sha256(files["authoring-monitoring.csv"]).hexdigest()
    for key in ("quality-centimetres", "quality-alias", "quality-invalid-numbers"):
        assert assets[key]["declaration"]["parent_checksum"] == parent
        assert assets[key]["declaration"]["quality_variant"]
    centimetres = list(csv.DictReader(io.StringIO(files["authoring-quality-centimetres.csv"].decode())))
    canonical = {(row["well_id"], row["observed_at"]): float(row["water_level_m"])
                 for row in csv.DictReader(io.StringIO(files["authoring-monitoring.csv"].decode()))}
    assert len(centimetres) == 720
    assert all(row["declared_value_unit"] == "cm" for row in centimetres)
    for row in centimetres:
        assert float(row["water_level_m"]) == pytest.approx(
            canonical[row["well_id"], row["observed_at"]] * 100,
        )
    aliases = {row["well_id"] for row in csv.DictReader(
        io.StringIO(files["authoring-quality-alias.csv"].decode()))}
    assert {"001", "DEMO-002"} <= aliases and not {"W001", "W002"} & aliases
    invalid = list(csv.DictReader(io.StringIO(files["authoring-quality-invalid-numbers.csv"].decode())))
    assert [row["water_level_m"] for row in invalid[:4]] == [
        "not-a-number", "NaN", "Infinity", "-Infinity",
    ]
    assert assets["quality-invalid-numbers"]["declaration"]["inspection_scope"].startswith(
        "字典声明来自确定性生成器"
    )
    illegal = tmp_path / "illegal.json"
    illegal.write_bytes(files["authoring-quality-illegal-rules.json"])
    with pytest.raises(ValueError, match="RULESET_INVALID"):
        read_rules(SimpleNamespace(path=illegal, format_id="json"), "table-sort-rules.v1")
