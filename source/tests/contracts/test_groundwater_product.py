from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from assembler import load_domain, pack_domain
from jsonschema import validate
import pytest
from pyarrow import parquet


ROOT = Path(__file__).resolve().parents[2]


def test_dialogue_advice_instructions_use_natural_language_options():
    instructions = (ROOT / "domains/groundwater/6.0.0/instructions.md").read_text()
    assert "方法建议和比较属于普通 DIALOGUE" in instructions
    assert "一至三个自然语言 interaction_options" in instructions
    assert "后续新消息是独立的 AUTO Turn" in instructions
    assert "一般方法咨询只说明能力、十九类输入要求、限制和复核步骤" in instructions
    retired_surface = instructions.replace("selection_policy", "")
    for retired in (
        "recommendation", "selection", "candidate",
        "submit_reply.recommendation", "partial_arguments",
        "argument_sources", "resource_references", "requires_prior_results",
    ):
        assert retired not in retired_surface


def test_data_observations_accept_exact_version_identity_and_lineage():
    package = json.loads((ROOT / "services/data/contracts/observation-tools.v4.json").read_text())
    schemas = {tool["name"]: tool["outputSchema"] for tool in package["tools"]}
    version = {
        "status": "OK", "versionId": "version-1", "datasetId": "dataset-1",
        "semanticId": "semantics-1", "lineage": {"inputVersions": ["version-0"]},
        "objects": [{
            "objectId": "object-1", "role": "raw", "size": 2,
            "checksum": "sha256:" + "a" * 64,
            "mediaType": "application/json", "format": "json",
        }],
    }
    validate(version, schemas["inspect_data_version"])
    validate({
        "items": [{"datasetId": "dataset-1", "currentVersion": "version-1"}],
        "nextCursor": None, "totalMatches": 1, "matchBasis": "title",
    }, schemas["discover_datasets"])
    validate({
        "status": "OK", "versionId": "version-1", "datasetId": "dataset-1",
        "columns": ["value"], "rows": [{"value": 1}],
    }, schemas["inspect_data_table"])


def test_groundwater_v11_domain_upgrades_models_and_preserves_workflow_identity(tmp_path):
    after = load_domain(ROOT / "domains/groundwater/6.0.0")
    for name in after.manifest["executableModels"]:
        model = after.definitions[name]
        assert model["schemaVersion"] == "executable-model.v4"
        assert "mapFactoryAction" not in model and "outputPolicy" not in model
    summary = after.definitions["models/summary.model.json"]
    workflow = after.definitions["workflows/summary.workflow.json"]
    assert (summary["modelId"], summary["version"]) == ("groundwater.summary", "3.0.0")
    assert summary["tool"]["name"] == "run_summary"
    assert workflow["schemaVersion"] == "workflow.v6"
    assert (workflow["workflowId"], workflow["version"]) == ("groundwater.summary", "1.0.0")
    assert summary["workflowRef"]["workflowId"] == workflow["workflowId"]
    assert workflow["nodes"][0]["operatorId"] == "statistics.describe"
    assert all(after.definitions[name]["modelId"].startswith("groundwater.")
               for name in after.manifest["executableModels"])
    manifest = json.loads((ROOT / "products/groundwater/product-manifest.json").read_text())
    assert manifest["domainPackage"] == after.identity
    assert manifest["knowledgeProjects"] == ["groundwater-demo"]
    archives = [tmp_path / "first.zip", tmp_path / "second.zip"]
    for path in archives:
        pack_domain(after, path)
    assert archives[0].read_bytes() == archives[1].read_bytes()
    current = json.loads((ROOT / "tests/fixtures/groundwater-knowledge-retrieval.v1.json").read_text())
    assert len(current["cases"]) == 297


@pytest.mark.parametrize("output_field", ["hydraulic_head_m", "calculated_head"])
def test_flow_field_binds_computed_head_through_snapshot_and_idw(tmp_path, output_field):
    from moperators.operators.head_calculate import run as calculate_head
    from moperators.operators.timeseries_snapshot import run as select_snapshot

    package = load_domain(ROOT / "domains/groundwater/6.0.0")
    workflow = package.definitions["workflows/flow-field.workflow.json"]
    model = package.definitions["models/flow-field.model.json"]
    nodes = {node["nodeId"]: node for node in workflow["nodes"]}
    assert workflow["version"] == "1.1.0"
    assert model["version"] == "3.0.0"
    canonical = json.dumps(
        workflow, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    assert model["workflowRef"]["checksum"] == (
        "sha256:" + hashlib.sha256(canonical).hexdigest()
    )
    for node_id, parameter in (
        ("head", "outputField"),
        ("snapshot", "valueField"),
        ("idw", "valueField"),
    ):
        assert nodes[node_id]["parameters"][parameter] == {
            "workflowParameter": model["parameterBindings"]["valueField"]
        }
    assert nodes["idw"]["inputs"]["points"] == {
        "nodeOutput": {"nodeId": "snapshot", "port": "snapshot"}
    }

    source = tmp_path / "observations.csv"
    source.write_text("well,elevation,depth,time,x,y\nw1,100,10,2026-09-28,0,0\n")
    table_input = SimpleNamespace(path=source, format_id="csv")
    parameters = {
        "referenceElevationField": "elevation",
        "depthToWaterField": "depth",
        "referenceElevationUnit": "m",
        "depthToWaterUnit": "m",
        "outputField": output_field,
    }
    result = calculate_head(table_input, parameters, tmp_path)
    table = parquet.read_table(result["heads"]["path"])
    assert table.column(output_field)[0].as_py() == pytest.approx(90)
    snapshot = select_snapshot(
        SimpleNamespace(path=Path(result["heads"]["path"]), format_id="parquet"),
        {
            "groupField": "well",
            "timeField": "time",
            "valueField": output_field,
            "targetTime": "2026-09-28",
            "selection": "exact",
            "maxAgeDays": 0,
        },
        tmp_path,
    )
    selected = parquet.read_table(snapshot["snapshot"]["path"])
    assert selected.column(output_field)[0].as_py() == pytest.approx(90)
    assert selected.column("depth")[0].as_py() == "10"
    with pytest.raises(ValueError, match="OUTPUT_FIELD_CONFLICT"):
        calculate_head(table_input, {**parameters, "outputField": "depth"}, tmp_path)


def test_coverage_dissolves_before_hull_and_uses_existing_runtimes():
    from assembler import compile_product

    generated = compile_product(ROOT, ROOT / "products/groundwater/product-manifest.json")
    compose = json.loads((generated / "compose.json").read_text())
    assert len(compose["services"]) == 15
    assert "operator-gateway" not in compose["services"]
    executor = json.loads((generated / "executor-config.json").read_text())
    workflow = json.loads(
        (ROOT / "domains/groundwater/6.0.0/workflows/monitoring-coverage.workflow.json").read_text()
    )
    assert [n["operatorId"] for n in workflow["nodes"]] == [
        "vector.dissolve",
        "vector.convex_hull",
        "data.convert",
    ]
    assert workflow["nodes"][1]["inputs"]["vector"]["nodeOutput"] == {
        "nodeId": "dissolve",
        "port": "vector",
    }
    assert "groundwater.monitoring-coverage" in json.dumps(executor)
