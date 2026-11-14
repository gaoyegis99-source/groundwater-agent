from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import subprocess
import sys

import pytest

from platform_workflow_compiler import WorkflowValidationError
from platform_workflow_compiler.composition import (
    definition_digest,
    load_json,
    validate_composition,
    validate_document,
    validate_operator_manifest,
    validate_vocabulary,
    validator_identity,
)

FIXTURES = Path(__file__).parent / "fixtures" / "composition"


def fixture(name):
    return load_json((FIXTURES / f"{name}.json").read_bytes())


@pytest.fixture
def catalog():
    manifests = [load_json(path.read_bytes()) for path in sorted(FIXTURES.glob("sample.*.json"))]
    return {(item["id"], item["version"]): item for item in manifests}


def report(workflow, catalog):
    result = validate_composition(workflow, catalog, fixture("vocabulary"), environment_ref=fixture("environment"))
    validate_document("composition-validation.v1", result)
    return result


def fact(workflow, name, property_name):
    return next(item for item in workflow["inputs"][name]["facts"] if item["property"] == property_name)


def reseal(operator, workflow):
    operator["definitionDigest"] = definition_digest(operator)
    for node in workflow["nodes"]:
        if node["operator"]["id"] == operator["id"]:
            node["operator"]["definitionDigest"] = operator["definitionDigest"]
    for parameter in workflow["parameters"].values():
        source = parameter["source"]
        if source["kind"] == "OPERATOR_DEFAULT" and source["ref"]["id"] == operator["id"]:
            source["ref"]["digest"] = operator["definitionDigest"]


@pytest.mark.parametrize(
    ("name", "order", "stage", "check", "facts"),
    [
        ("table-positive", ["filter", "summary"], "beforeNode", "DEFERRED", {"unit": "m", "grain": "dataset"}),
        ("raster-positive", ["convert", "mosaic"], "compile", "PASS", {"unit": "m", "verticalDatum": "sample-datum"}),
        ("model-positive", ["predict"], "compile", "PASS", {"unit": "m", "quantityKind": "water-head.v1"}),
    ],
)
def test_positive_examples(catalog, name, order, stage, check, facts):
    workflow = fixture(name)
    result = report(workflow, catalog)
    assert result["status"] == "VALIDATED"
    assert result["diagnostics"] == []
    assert result["topologicalOrder"] == order
    assert [(item["stage"], item["state"]) for item in result["checks"]] == [(stage, check)]
    assert facts.items() <= result["outputs"]["result"]["facts"].items()
    assert result["definitionDigest"] == definition_digest(workflow)
    assert report(deepcopy(workflow), catalog) == result


@pytest.mark.parametrize(
    ("name", "code", "path"),
    [
        ("table-negative", "SEMANTIC_QUANTITY_MISMATCH", "/nodes/1/inputs/data/quantityKind"),
        ("raster-negative", "GRID_MISMATCH", "/nodes/1/checks/aligned-grids"),
        ("model-negative", "MODEL_FEATURE_ORDER_MISMATCH", "/nodes/0/checks/input-model-match"),
    ],
)
def test_negative_examples(catalog, name, code, path):
    result = report(fixture(name), catalog)
    assert result["status"] == "INVALID"
    match = next(item for item in result["diagnostics"] if item["code"] == code)
    assert match["path"] == path
    assert match["factSources"]


def test_unit_conversion_requires_an_explicit_node(catalog):
    workflow = fixture("raster-positive")
    workflow["nodes"].pop(0)
    workflow["parameters"].clear()
    workflow["nodes"][0]["inputs"]["rasters"]["collection"][1] = {"workflowInput": "right"}
    result = report(workflow, catalog)
    assert result["status"] == "INVALID"
    assert "ADAPTER_REQUIRED" in {item["code"] for item in result["diagnostics"]}


@pytest.mark.parametrize("state", ["unknown", "conflict", "sample"])
def test_missing_or_unverified_facts_cannot_prove_model_compatibility(catalog, state):
    workflow = fixture("model-positive")
    item = fact(workflow, "features", "featureOrder")
    if state == "sample":
        item["verificationScope"] = "sample"
    else:
        item["state"] = state
        item.pop("value")
        if state == "conflict":
            item["conflictingRefs"] = [item["sourceRef"], item["subjectRef"]]
    result = report(workflow, catalog)
    assert result["status"] == "INCOMPLETE"
    assert result["checks"][0]["state"] == "UNKNOWN"


def test_model_training_overlap(catalog):
    workflow = fixture("model-positive")
    fact(workflow, "features", "evaluationInterval")["value"][0] = "2024-12-31T23:59:59Z"
    result = report(workflow, catalog)
    assert result["status"] == "INVALID"
    assert result["diagnostics"][0]["code"] == "TRAINING_EVALUATION_OVERLAP"


def test_collection_alias_does_not_bypass_duplicate_rule(catalog):
    workflow = fixture("raster-positive")
    workflow["nodes"].pop(0)
    workflow["parameters"].clear()
    workflow["inputs"]["right"]["dataRef"] = workflow["inputs"]["left"]["dataRef"]
    workflow["inputs"]["right"]["facts"] = deepcopy(workflow["inputs"]["left"]["facts"])
    workflow["nodes"][0]["inputs"]["rasters"]["collection"][1] = {"workflowInput": "right"}
    assert report(workflow, catalog)["diagnostics"][0]["code"] == "COLLECTION_DUPLICATE_INPUT"


def test_collection_cardinality_and_cycles(catalog):
    workflow = fixture("raster-positive")
    workflow["nodes"][1]["inputs"]["rasters"]["collection"].pop(0)
    assert report(workflow, catalog)["diagnostics"][0]["code"] == "COLLECTION_CARDINALITY_INVALID"
    workflow = fixture("table-positive")
    workflow["nodes"][0]["inputs"]["data"] = {"nodeOutput": {"nodeId": "summary", "port": "summary"}}
    assert report(workflow, catalog)["diagnostics"][0]["code"] == "WORKFLOW_CYCLE"


def test_defaults_and_definition_checksums_are_frozen(catalog):
    workflow = fixture("table-positive")
    workflow["definitionDigest"] = definition_digest(workflow)
    workflow["parameters"]["minimum"]["value"] = 2
    assert report(workflow, catalog)["diagnostics"][0]["code"] == "COMPOSITION_DEFINITION_DIGEST_MISMATCH"
    workflow.pop("definitionDigest")
    assert report(workflow, catalog)["diagnostics"][0]["code"] == "COMPOSITION_PARAMETER_DEFAULT_INVALID"


@pytest.mark.parametrize(
    ("parameter", "expected"),
    [
        ({"$ref": "https://example.invalid/schema"}, "COMPOSITION_SCHEMA_KEYWORD_UNSUPPORTED"),
        ({"type": "string", "format": "unimplemented"}, "COMPOSITION_SCHEMA_KEYWORD_UNSUPPORTED"),
        ({"type": "string", "format": "date-time", "default": "2025-01-01"}, "COMPOSITION_PARAMETER_DEFAULT_INVALID"),
    ],
)
def test_schema_subset_fails_closed(catalog, parameter, expected):
    operator = catalog["sample.table.filter", "1.0.0"]
    operator["parametersSchema"]["properties"]["column"] = parameter
    operator["definitionDigest"] = definition_digest(operator)
    with pytest.raises(WorkflowValidationError) as caught:
        validate_operator_manifest(operator, fixture("vocabulary"))
    assert caught.value.code == expected


def test_installed_validator_is_required(catalog):
    workflow = fixture("raster-positive")
    operator = catalog["sample.raster.mosaic", "1.0.0"]
    operator["constraints"][0]["validatorRef"]["digest"] = "sha256:" + "0" * 64
    reseal(operator, workflow)
    assert report(workflow, catalog)["diagnostics"][0]["code"] == "COMPOSITION_VALIDATOR_MISMATCH"


def test_no_deferred_success_for_missing_scientific_fact(catalog):
    workflow = fixture("model-positive")
    operator = catalog["sample.model.predict", "1.0.0"]
    operator["constraints"][0]["checkStage"] = "beforeNode"
    reseal(operator, workflow)
    item = fact(workflow, "features", "evaluationInterval")
    item["state"] = "unknown"
    item.pop("value")
    result = report(workflow, catalog)
    assert result["status"] == "INCOMPLETE"
    assert result["checks"][0]["state"] == "UNKNOWN"


def test_canonical_json_vectors_and_invalid_json():
    payload = {"numbers": [333333333.33333329, 1e30, 4.50, 2e-3, 1e-27, -0.0, 1e-6, 1e21], "\ue000": 1, "😀": 2}
    canonical = '{"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27,0,0.000001,1e+21],"😀":2,"\ue000":1}'
    assert definition_digest(payload) == "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
    assert definition_digest({"array": [1, 2]}) != definition_digest({"array": [2, 1]})
    for content in ('{"nested":{"a":1,"a":2}}', '{"a":NaN}', '{"a":9007199254740993}', r'{"a":"\ud800"}'):
        with pytest.raises(WorkflowValidationError):
            load_json(content)


def test_registry_manifest_and_fact_schemas(catalog):
    vocabulary = fixture("vocabulary")
    assert validate_vocabulary(vocabulary) == fixture("table-positive")["registryRef"]
    assert validator_identity() == fixture("table-positive")["validatorRef"]
    for operator in catalog.values():
        validate_operator_manifest(operator, vocabulary)
    item = fact(fixture("table-positive"), "observations", "unit")
    validate_document("composition-fact.v1", item)
    item["state"] = "unknown"
    with pytest.raises(WorkflowValidationError):
        validate_document("composition-fact.v1", item)


@pytest.mark.parametrize(("name", "status", "exit_code"), [("table-positive", "VALIDATED", 0), ("table-negative", "INVALID", 1)])
def test_command_validates_examples(name, status, exit_code):
    result = subprocess.run(
        [
            sys.executable, "-m", "platform_workflow_compiler.composition", str(FIXTURES / f"{name}.json"),
            "--operator", str(FIXTURES / "sample.table.filter.json"),
            "--operator", str(FIXTURES / "sample.table.summary.json"),
            "--vocabulary", str(FIXTURES / "vocabulary.json"), "--environment", str(FIXTURES / "environment.json"),
        ],
        capture_output=True, text=True, check=False,
        env={"PYTHONPATH": str(FIXTURES.parents[2] / "src")},
    )
    assert result.returncode == exit_code, result.stderr
    assert load_json(result.stdout)["status"] == status


def test_frozen_parameter_selects_output_format(catalog):
    workflow = fixture("table-positive")
    operator = catalog["sample.table.summary", "1.0.0"]
    port = operator["outputs"][0]
    parquet = port["formatPairs"][0]
    csv = {"format": "csv", "mediaType": "text/csv"}
    port["formatPairs"].append(csv)
    port["formatCases"] = [
        {"parameter": "encoding", "values": ["parquet"], "formatPair": parquet},
        {"parameter": "encoding", "values": ["csv"], "formatPair": csv},
    ]
    operator["parametersSchema"]["properties"]["encoding"] = {"type": "string", "enum": ["csv", "parquet"]}
    operator["parameterPolicies"]["encoding"] = deepcopy(operator["parameterPolicies"]["minimum"])
    operator["parameterPolicies"]["encoding"]["defaultPolicy"] = "none"
    workflow["nodes"][1]["parameters"]["encoding"] = {"workflowParameter": "encoding"}
    workflow["parameters"]["encoding"] = {
        "value": "parquet", "source": workflow["parameters"]["field"]["source"],
    }
    reseal(operator, workflow)
    assert report(workflow, catalog)["outputs"]["result"]["formatPairs"] == [parquet]
    workflow["parameters"]["encoding"]["value"] = "csv"
    assert report(workflow, catalog)["diagnostics"][0]["code"] == "OUTPUT_CONTRACT_VIOLATION"
    port["formatCases"].pop()
    reseal(operator, workflow)
    assert report(workflow, catalog)["diagnostics"][0]["code"] == "COMPOSITION_FORMAT_CASE_INVALID"


def test_guarded_after_node_check_and_unknown_runtime_fact(catalog):
    workflow = fixture("table-positive")
    operator = catalog["sample.table.summary", "1.0.0"]
    operator["outputSemantics"]["summary"]["measured"] = ["rowCount"]
    operator["constraints"].append({
        "id": "nonempty-result",
        "severity": "error",
        "checkStage": "afterNode",
        "validatorRef": validator_identity(),
        "condition": {
            "when": {
                "predicate": "sameQuantity",
                "arguments": [{"port": "data", "property": "quantityKind"}, {"value": "water-head.v1"}],
            },
            "then": {
                "predicate": "minimumRows",
                "arguments": [
                    {"direction": "output", "port": "summary", "property": "rowCount"}, {"value": 1},
                ],
            },
        },
    })
    reseal(operator, workflow)
    result = report(workflow, catalog)
    assert result["status"] == "VALIDATED"
    assert [(item["stage"], item["state"]) for item in result["checks"]] == [
        ("beforeNode", "DEFERRED"), ("afterNode", "DEFERRED"),
    ]
    operator["constraints"][1]["condition"]["when"]["arguments"][1]["value"] = "depth-to-water.v1"
    reseal(operator, workflow)
    assert report(workflow, catalog)["checks"][1]["state"] == "SKIPPED"


def test_typed_value_output_can_bind_an_opted_in_parameter(catalog):
    workflow = fixture("table-positive")
    producer = catalog["sample.table.filter", "1.0.0"]
    consumer = catalog["sample.table.summary", "1.0.0"]
    schema = {"type": "integer", "minimum": 1}
    producer["outputs"].append({
        "name": "minimum", "kind": "value", "logicalType": "record", "schema": schema,
        "cardinality": {"minItems": 1, "maxItems": 1, "ordered": True, "duplicates": False},
    })
    producer["outputSemantics"]["minimum"] = {"fixed": {}, "inherit": [], "transform": [], "measured": []}
    consumer["parametersSchema"]["properties"]["minimum"] = schema
    consumer["parameterPolicies"]["minimum"] = {
        "allowNodeOutput": True, "allowedSources": ["DERIVED"], "defaultPolicy": "none", "impact": "engineering",
    }
    consumer["constraints"].clear()
    workflow["requiredFeatures"].append("value-bindings")
    workflow["parameters"].pop("minimum")
    workflow["nodes"][1]["parameters"]["minimum"] = {"nodeOutput": {"nodeId": "filter", "port": "minimum"}}
    reseal(producer, workflow)
    reseal(consumer, workflow)
    assert report(workflow, catalog)["status"] == "VALIDATED"
    producer["outputs"][1]["schema"] = {"type": "string", "maxLength": 16}
    reseal(producer, workflow)
    assert report(workflow, catalog)["diagnostics"][0]["code"] == "WORKFLOW_PARAMETER_BINDING_INVALID"


def test_undeclared_binding_and_resource_limit(catalog):
    workflow = fixture("table-positive")
    workflow["requiredFeatures"].append("dynamic-scatter")
    assert report(workflow, catalog)["diagnostics"][0]["code"] == "FEATURE_UNSUPPORTED"
    workflow["requiredFeatures"].pop()
    workflow["resourceLimits"]["maxNodeMemoryBytes"] = 1024
    assert report(workflow, catalog)["diagnostics"][0]["code"] == "COMPOSITION_RESOURCE_LIMIT"


def test_field_role_checks_physical_quantity_of_selected_column(catalog):
    workflow = fixture("table-positive")
    operator = catalog["sample.table.filter", "1.0.0"]
    operator["portSemantics"]["data"]["fieldBindings"]["value"]["requires"] = {
        "quantityKind": "water-head.v1", "unit": "m", "dtype": "float64",
    }
    reseal(operator, workflow)
    assert report(workflow, catalog)["status"] == "VALIDATED"
    fact(workflow, "observations", "fields")["value"]["head"]["quantityKind"] = "depth-to-water.v1"
    result = report(workflow, catalog)
    assert result["status"] == "INVALID"
    assert result["diagnostics"][0]["path"] == "/nodes/0/inputs/data/value/quantityKind"


def test_fact_cannot_reference_different_data_or_semantic_revision(catalog):
    workflow = fixture("table-positive")
    item = fact(workflow, "observations", "unit")
    item["subjectRef"]["digest"] = "sha256:" + "0" * 64
    assert report(workflow, catalog)["diagnostics"][0]["code"] == "COMPOSITION_FACT_SUBJECT_MISMATCH"
    item["subjectRef"]["digest"] = workflow["inputs"]["observations"]["dataRef"]["checksum"]
    item["sourceRef"]["digest"] = "sha256:" + "0" * 64
    assert report(workflow, catalog)["diagnostics"][0]["code"] == "COMPOSITION_FACT_SOURCE_MISMATCH"


def test_omitted_output_semantics_cannot_satisfy_downstream_requirements(catalog):
    workflow = fixture("table-positive")
    producer = catalog["sample.table.filter", "1.0.0"]
    producer.pop("outputSemantics")
    reseal(producer, workflow)
    result = report(workflow, catalog)
    assert result["status"] == "INCOMPLETE"
    assert ("UNIT_UNKNOWN", "/nodes/1/inputs/data/unit") in {
        (item["code"], item["path"]) for item in result["diagnostics"]
    }


def test_remote_manifest_requires_explicit_execution_policy(catalog):
    manifest = catalog["sample.table.filter", "1.0.0"]
    manifest["runtime"] = "remote_http"
    manifest["classification"]["providerKind"] = "external"
    manifest["remoteBinding"] = {"serviceId": "test", "operationType": "filter"}
    manifest["definitionDigest"] = definition_digest(manifest)
    with pytest.raises(WorkflowValidationError) as caught:
        validate_operator_manifest(manifest, fixture("vocabulary"))
    assert caught.value.code == "COMPOSITION_SCHEMA_INVALID"
    manifest["executionPolicy"] = {"sideEffect": "remoteJob", "retry": "coordinate", "determinism": "nondeterministic"}
    manifest["definitionDigest"] = definition_digest(manifest)
    validate_operator_manifest(manifest, fixture("vocabulary"))
