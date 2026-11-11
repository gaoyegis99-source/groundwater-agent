import ast
import json
import sys
import tomllib
from copy import deepcopy
from pathlib import Path

import pytest

from moperators.catalog import (
    MEDIA_TYPE_FORMATS,
    OperatorCatalog,
    OperatorDefinition,
    _validate_port_formats,
)
from moperators.smoke import run_smokes

PACK_ROOT = Path(__file__).resolve().parents[1]


def test_remote_http_smoke_does_not_import_native_numeric_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "moperators.operators.native", None)

    completed = run_smokes(PACK_ROOT / "catalog/operators", "remote_http")

    assert len(completed) == 11


def test_native_dependencies_exclude_duckdb_without_arrow_table_api() -> None:
    project = tomllib.loads((PACK_ROOT / "pyproject.toml").read_text())
    assert "duckdb>=1.5.6,<2" in project["project"]["optional-dependencies"]["native"]


def test_catalog_lock_smokes_and_static_mapping_are_complete() -> None:
    catalog = OperatorCatalog.load(PACK_ROOT / "catalog" / "operators")
    identities = {(item.id, item.version) for item in catalog.operators}

    assert ("cartography.render-png", "1.0.0") in identities
    assert not any(identity.startswith("mapfactory.") for identity, _ in identities)
    assert len(identities) == 109
    assert sum(item.runtime == "native" for item in catalog.operators) == 83
    assert sum(item.runtime == "qgis" for item in catalog.operators) == 15
    assert sum(item.runtime == "remote_http" for item in catalog.operators) == 11
    assert set(catalog.smoke_cases) == identities
    mapping_source = PACK_ROOT / "src" / "moperators" / "static_implementations.py"
    tree = ast.parse(mapping_source.read_text(encoding="utf-8"))
    mapping = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "IMPLEMENTATIONS"
    )
    assert isinstance(mapping, ast.Dict)
    assert {ast.literal_eval(key) for key in mapping.keys} == identities


def test_pack_contains_no_service_control_plane() -> None:
    package = PACK_ROOT / "src" / "moperators"
    forbidden = {"admin", "api", "control", "domain", "storage", "observability"}

    assert not forbidden.intersection(path.name for path in package.iterdir() if path.is_dir())


def test_all_manifest_artifacts_declare_formats_for_control_return_validation():
    from jsonschema import Draft202012Validator

    from moperators.catalog import MEDIA_TYPE_FORMATS

    schema = json.loads(
        (PACK_ROOT / "catalog/schemas/operator-manifest.v1.schema.json").read_text()
    )
    for path in (PACK_ROOT / "catalog/operators").glob("*/*/manifest.json"):
        manifest = json.loads(path.read_text())
        Draft202012Validator(schema).validate(manifest)
        for port in manifest["outputs"]:
            if port["kind"] == "artifact":
                assert port["formats"]
                assert set(port["formats"]) <= {
                    fmt for media in port["mediaTypes"] for fmt in MEDIA_TYPE_FORMATS[media]
                }


@pytest.mark.parametrize("defect", ["missing", "overlap", "wrong_pair"])
def test_conditional_output_cases_are_exhaustive_and_exact(defect: str) -> None:
    path = PACK_ROOT / "catalog/operators/crs.reproject/1.0.0/manifest.json"
    manifest = json.loads(path.read_text())
    cases = manifest["outputs"][0]["formatCases"]
    if defect == "missing":
        cases[1]["inputFormats"].remove("laz")
    elif defect == "overlap":
        cases[1]["inputFormats"].append("geojson")
    else:
        cases[0]["mediaType"] = "application/vnd.laz"
    with pytest.raises(ValueError, match="invalid format cases"):
        _validate_port_formats(OperatorDefinition.model_validate(manifest))


def test_data_convert_parameter_cases_cover_output_format_enum() -> None:
    path = PACK_ROOT / "catalog/operators/data.convert/1.0.0/manifest.json"
    manifest = json.loads(path.read_text())
    output = manifest["outputs"][0]
    cases = output["parameterFormatCases"]

    assert {case["parameter"] for case in cases} == {"outputFormat"}
    assert {
        value for case in cases for value in case["values"]
    } == set(manifest["parametersSchema"]["properties"]["outputFormat"]["enum"])
    assert {
        (case["mediaType"], case["format"]) for case in cases
    } <= {
        (media_type, format_id)
        for media_type in output["mediaTypes"]
        for format_id in output["formats"]
        if format_id in MEDIA_TYPE_FORMATS[media_type]
    }


@pytest.mark.parametrize(
    "defect",
    [
        "unknown_parameter",
        "overlap",
        "missing_enum_value",
        "missing_enum",
        "wrong_pair",
        "value_output",
        "mixed_discriminators",
    ],
)
def test_parameter_output_cases_reject_invalid_metadata(defect: str) -> None:
    path = PACK_ROOT / "catalog/operators/data.convert/1.0.0/manifest.json"
    manifest = json.loads(path.read_text())
    output = manifest["outputs"][0]
    cases = output["parameterFormatCases"]
    if defect == "unknown_parameter":
        for case in cases:
            case["parameter"] = "unknown"
    elif defect == "overlap":
        cases[1]["values"].append(cases[0]["values"][0])
    elif defect == "missing_enum_value":
        cases.pop()
    elif defect == "missing_enum":
        del manifest["parametersSchema"]["properties"]["outputFormat"]["enum"]
    elif defect == "wrong_pair":
        cases[0].update(mediaType="image/png", format="png")
    elif defect == "value_output":
        output.update(kind="value", schema={})
    else:
        output["formatCases"] = [{
            "inputPort": "data",
            "inputFormats": ["csv"],
            "mediaType": "text/csv",
            "format": "csv",
        }]

    definition = OperatorDefinition.model_validate(deepcopy(manifest))
    with pytest.raises(ValueError, match="format cases"):
        _validate_port_formats(definition)
