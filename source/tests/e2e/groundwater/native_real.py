"""Run verified real data and a classification control through the public API."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import subprocess
import time

import httpx

from scripts.lib.e2e_http import public_api_headers
from tests.e2e.groundwater.model_lifecycle import (
    REAL_DERIVED_TEST_SAMPLE,
    SYNTHETIC_TEST_ONLY,
    _execute,
    _publish,
    _request,
)


REAL_CASES = {
    "local-monitoring-trend": ("monitoring-trend", "aggregate-summary"),
    "usgs-mrva-flow-field": ("flow-field", "head-raster"),
    "usgs-mrva-kriging": ("kriging", "kriging-prediction"),
    "odnr-franklin-drastic": ("drastic", "drastic-index"),
}


def _digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _real_inputs(root: Path, case: str, declaration: dict) -> tuple[dict, dict]:
    entry = declaration["cases"][case]
    summary_path = root / "cases" / case / "summary.json"
    summary_bytes = summary_path.read_bytes()
    assert (
        entry["status"] == "PASS" and _digest(summary_bytes) == entry["summarySha256"]
    )
    summary = json.loads(summary_bytes)
    assert summary["caseId"] == case and summary["status"] == "PASS"
    inputs = {}
    for name, item in summary["inputs"].items():
        path = Path(item["path"]).resolve(strict=True)
        assert path.is_relative_to(root / "prepared"), path
        content = path.read_bytes()
        assert len(content) == item["size"] and _digest(content) == item["sha256"], path
        inputs[name] = (item, content)
    return summary, inputs


def _semantics(case: str, name: str) -> dict:
    if case == "local-monitoring-trend":
        return {"fields": {"value": {"meaning": "water_level_elevation", "unit": "m"}}}
    if case == "usgs-mrva-flow-field":
        return {
            "crs": "EPSG:5070",
            "vertical_datum": "NAVD88",
            "fields": {
                "reference_elevation_m": {
                    "meaning": "reference_elevation",
                    "unit": "m",
                },
                "depth_to_water_m": {"meaning": "depth_to_water", "unit": "m"},
            },
        }
    if case == "usgs-mrva-kriging":
        return {"crs": "EPSG:5070", "vertical_datum": "NAVD88"}
    return {"crs": "EPSG:3735"} if name != "config" else {}


def _classification_inputs(source: Path) -> dict:
    fixtures_root = source / "packages/operator-pack/catalog/fixtures"
    index = json.loads((fixtures_root / "index.json").read_text())["fixtures"]
    ids = {
        "data": "table.document.table",
        "rules": "table.document.classification.rules",
        "bindings": "table.document.classification.bindings",
    }
    inputs = {}
    for name, fixture_id in ids.items():
        item = index[fixture_id]
        content = (fixtures_root / item["path"]).read_bytes()
        assert _digest(content) == item["sha256"]
        inputs[name] = (item, content)
    return inputs


_COMPARE_RASTER = """
import base64
import json
import sys

import numpy as np
from rasterio.io import MemoryFile

actual_bytes, reference_bytes, tolerance = json.load(sys.stdin)
with MemoryFile(base64.b64decode(actual_bytes)) as actual_file, MemoryFile(
    base64.b64decode(reference_bytes)
) as reference_file, actual_file.open() as actual, reference_file.open() as reference:
    assert (actual.crs, actual.transform, actual.shape, actual.dtypes) == (
        reference.crs, reference.transform, reference.shape, reference.dtypes
    ), "RASTER_GRID_MISMATCH"
    assert actual.nodata == reference.nodata or (
        actual.nodata is not None and reference.nodata is not None
        and np.isnan(actual.nodata) and np.isnan(reference.nodata)
    ), "RASTER_NODATA_MISMATCH"
    values = actual.read(1, masked=True)
    expected = reference.read(1, masked=True)
    assert np.array_equal(np.ma.getmaskarray(values), np.ma.getmaskarray(expected)), (
        "RASTER_MASK_MISMATCH"
    )
    difference = np.abs(values.compressed().astype("float64") - expected.compressed())
    assert difference.size and np.isfinite(difference).all(), "RASTER_VALUES_INVALID"
    maximum = float(difference.max())
    assert maximum <= tolerance, f"RASTER_VALUE_MISMATCH: {maximum} > {tolerance}"
print(json.dumps({"cells": int(difference.size), "max_abs_difference": maximum, "tolerance": tolerance}))
"""


def _check_output(path: Path, expected: dict | None, compose: list[str]) -> dict:
    content = path.read_bytes()
    if expected is None:
        result = json.loads(content)
        assert result["overallClassCounts"] == {"LOW": 1, "HIGH": 1}
        assert result["unevaluatedRowCount"] == 1
        return {"class_counts": result["overallClassCounts"], "unevaluated": 1}
    elif expected["kind"] == "value":
        assert json.loads(content) == expected["value"]
        return {"json_matches_reference": True}
    reference = Path(expected["path"]).read_bytes()
    assert _digest(reference) == expected["sha256"]
    # Compare decoded pixels across the isolated task and the Compose Worker.
    # Their floating point libraries can differ at machine precision.
    tolerance = 0.0 if path.name == "drastic-index" else 1e-6
    compared = subprocess.run(
        [
            *compose,
            "exec",
            "-T",
            "executor-native-worker",
            "/usr/bin/python3",
            "-c",
            _COMPARE_RASTER,
        ],
        input=json.dumps(
            [
                base64.b64encode(content).decode("ascii"),
                base64.b64encode(reference).decode("ascii"),
                tolerance,
            ]
        ),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert compared.returncode == 0, compared.stderr
    return json.loads(compared.stdout)


def run_suite(
    base_url: str,
    source: Path,
    prepared: Path,
    evidence: Path,
    compose: list[str],
) -> list[dict]:
    prepared = prepared.resolve(strict=True)
    declaration = json.loads((prepared / "summary.json").read_text())
    manifest = source / "products/groundwater/development/real-data-cases.v2.json"
    assert declaration["status"] == "PASS"
    assert _digest(manifest.read_bytes()) == declaration["manifestSha256"]
    product = json.loads(
        (source / "products/groundwater/product-manifest.json").read_text()
    )
    domain = source / "domains/groundwater" / product["domainPackage"]["version"]
    evidence.mkdir(parents=True, exist_ok=True)
    reports = []
    with httpx.Client(
        base_url=base_url,
        timeout=300,
        trust_env=False,
        headers=public_api_headers(),
    ) as client:
        capabilities = _request(client, "GET", "/api/v12/capabilities")
        for case, (name, checked_output) in (
            *REAL_CASES.items(),
            (
                "classification-control",
                ("rule-classification", "classification-summary"),
            ),
        ):
            started = time.monotonic()
            model_id = "groundwater." + name
            print(f"native model START {case} {model_id}", flush=True)
            try:
                model = json.loads(
                    (domain / "models" / f"{name}.model.json").read_text()
                )
                if case in REAL_CASES:
                    summary, inputs = _real_inputs(prepared, case, declaration)
                    capability = next(
                        item
                        for item in capabilities["models"]
                        if item["model_id"] == model_id
                    )
                    assert (
                        capability["workflow_checksum"]
                        == summary["workflow"]["definitionChecksum"]
                    )
                    parameters = {
                        model["parameterBindings"][key]: value
                        for key, value in summary["parameters"].items()
                    }
                    expected = summary["outputs"][checked_output]
                    nature = REAL_DERIVED_TEST_SAMPLE
                    provenance = summary["sourceGroup"]
                else:
                    inputs = _classification_inputs(source)
                    parameters, expected = {}, None
                    nature, provenance = (
                        SYNTHETIC_TEST_ONLY,
                        "Operator Pack classification control",
                    )
                versions = {}
                for port, (item, content) in inputs.items():
                    published = _publish(
                        client,
                        name=f"{case} {port}",
                        content=content,
                        format_=item["format"],
                        media_type=item["mediaType"],
                        data_nature=nature,
                        source=provenance,
                        semantics=_semantics(case, port)
                        if case in REAL_CASES
                        else None,
                    )
                    assert published["checksum"] == _digest(content)
                    versions[model["inputBindings"][port]] = published["version_id"]
                output_directory = evidence / case
                report = _execute(
                    client,
                    capabilities,
                    model_id=model_id,
                    tool=model["tool"]["name"],
                    arguments={**parameters, **versions},
                    input_versions=set(versions.values()),
                    data_nature=nature,
                    output_directory=output_directory,
                )
                report.update(
                    data_nature=nature,
                    checked_output=checked_output,
                    source=provenance,
                    input_sha256={
                        port: _digest(content) for port, (_, content) in inputs.items()
                    },
                    scientific_check=_check_output(
                        output_directory / checked_output, expected, compose
                    ),
                )
            except (
                AssertionError,
                ValueError,
                KeyError,
                StopIteration,
                OSError,
                subprocess.SubprocessError,
                httpx.HTTPError,
            ) as error:
                report = {
                    "model": model_id,
                    "status": "FAIL",
                    "error": f"{type(error).__name__}: {error}",
                }
            reports.append(
                {
                    **report,
                    "case": case,
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                }
            )
            print(f"native model {report['status']} {case}", flush=True)
            (evidence / "native-real-lifecycle.json").write_text(
                json.dumps(reports, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    return reports
