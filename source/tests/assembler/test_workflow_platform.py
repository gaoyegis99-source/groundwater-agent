import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from shutil import copy2
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "platform/assembler"))

from assembler import compile_product  # noqa: E402
from assembler_workflow_platform import load_workflow_platform, require_immutable_platform_refs  # noqa: E402
from executor_profile import ExecutorOperationalProfile  # noqa: E402
from platform_kernel.agent_runtime.rule_preparation import prepare_operator_rules  # noqa: E402
from platform_workflow_compiler.composition.execution import ArtifactComposition  # noqa: E402
from scripts.development.workflow_profile import (  # noqa: E402
    describe_workflow_result,
    select_workflow_profile,
)


PRODUCT = ROOT / "products/groundwater"
TREND_PROFILE = "groundwater.monitoring-trend.seasonal-and-threshold"
FLOW_PROFILE = "groundwater.flow-field.same-time-darcy"
PARAMETERS = {
    "groupField": "station",
    "timeField": "time",
    "valueField": "value",
    "frequency": "month",
    "expectedStart": "2020-01-01",
    "expectedEnd": "2025-12-31",
    "baselineStart": "2020-01-01",
    "baselineEnd": "2022-12-31",
    "comparisonStart": "2023-01-01",
    "comparisonEnd": "2025-12-31",
    "aggregation": "mean",
    "seasonFrequency": "month",
    "eventComparison": "above",
    "eventThreshold": 10,
}


def read(path):
    return json.loads(path.read_bytes())


def by_id(items, identity):
    return next(item for item in items if item.get("ref", item)["id"] == identity)


def data_ref(**values):
    return {
        "kind": "data_version", "service": "data-primary", "datasetId": "dataset",
        "versionId": "version", "semanticId": "semantic", "objectId": "object",
        "mediaType": "application/vnd.apache.parquet", "format": "parquet",
        "sizeBytes": 120, "checksum": "sha256:" + "0" * 64, **values,
    }


@pytest.fixture(scope="module")
def platform_bundle(tmp_path_factory):
    from pytest import MonkeyPatch

    with MonkeyPatch.context() as env:
        env.setenv("PLATFORM_GENERATED_ROOT", str(tmp_path_factory.mktemp("workflow-platform")))
        bundle = compile_product(
            ROOT, PRODUCT / "product-manifest.json", executor_profile=ExecutorOperationalProfile(),
        )
    platform = read(bundle / "workflow-platform.json")
    return bundle, platform, by_id(platform["profiles"], TREND_PROFILE)["ref"]


def test_assembly_links_all_presets_to_exact_implementations_and_product_entries(platform_bundle):
    bundle, platform, ref = platform_bundle
    profile = by_id(platform["profiles"], TREND_PROFILE)
    lock = read(bundle / "assembly-lock.json")
    assert {item["id"] for item in lock["workflowPlatform"]["profiles"]} == {TREND_PROFILE, FLOW_PROFILE}
    assert ref in lock["workflowPlatform"]["profiles"]
    assert (
        lock["workflowPlatform"]["checksum"]
        == "sha256:" + hashlib.sha256((bundle / "workflow-platform.json").read_bytes()).hexdigest()
    )
    assert len(platform["workflows"]) == 18
    assert len({item["workflowRef"]["checksum"] for item in platform["workflows"]}) == 18
    trend = next(item for item in platform["workflows"] if item["workflowRef"] == profile["workflowRef"])
    assert len(trend["operators"]) == 10
    assert all(op["implementationRevision"] for op in trend["operators"])
    assert trend["profiles"] == [{"profileRef": ref, "modelRef": profile["modelRef"]}]
    assert {"kind": "PRODUCT_MODEL_ENTRY", "id": "groundwater.monitoring-trend"} in trend["models"][0]["entries"]
    assert trend["models"][0]["resourceAssessment"]["maxParallelNodes"] == 4
    assert trend["models"][0]["resourceAssessment"]["runtimes"]["native"]["concurrentNodesUpperBound"] == 2
    assert trend["compositionUnsupported"]


def test_profile_produces_a_reproducible_pinned_v12_selection(platform_bundle):
    bundle, platform, ref = platform_bundle
    inputs = {"observations": data_ref()}
    first = select_workflow_profile(bundle, ref, role="researcher", inputs=inputs, parameters=PARAMETERS)
    second = select_workflow_profile(bundle, ref, role="admin", inputs=inputs, parameters=dict(reversed(list(PARAMETERS.items()))))
    assert first == second
    selected = first["workflowSelection"]
    profile = by_id(platform["profiles"], TREND_PROFILE)
    assert first["inputReferences"] == [inputs["observations"]]
    assert selected["inputBindings"] == {"versionId": 0}
    assert selected["modelChecksum"] == profile["modelRef"]["checksum"]
    assert selected["workflowChecksum"] == profile["workflowRef"]["checksum"]
    assert selected["parameters"] == {
        **PARAMETERS, "seasonalTrendEnabled": True, "thresholdEventsEnabled": True,
        "alpha": 0.05, "threshold": 3.5,
    }


def test_profile_keeps_preset_operator_outside_dynamic_composition(platform_bundle):
    bundle, platform, _ = platform_bundle
    workflow_ref = by_id(platform["profiles"], TREND_PROFILE)["workflowRef"]
    trend = next(item for item in platform["workflows"] if item["workflowRef"] == workflow_ref)
    assert "timeseries.aggregate@1.0.0" in trend["compositionUnsupported"]
    executor = read(bundle / "executor-config.json")
    composition = ArtifactComposition.from_config(executor["workflowComposition"])
    result = asyncio.run(prepare_operator_rules(
        None, {
            "operator_id": "timeseries.aggregate", "operator_version": "1.0.0",
            "rules_port": "observations", "rules": {}, "data_inputs": {},
        }, composition, None, None, None,
    ))
    assert result == {"status": "RULES_BLOCKED", "code": "COMPOSITION_OPERATOR_UNSUPPORTED"}


@pytest.mark.parametrize(("ref_patch", "role", "input_patch", "parameters", "code"), [
    ({"checksum": "sha256:" + "0" * 64}, "researcher", {}, PARAMETERS, "WORKFLOW_PROFILE_NOT_FOUND"),
    ({"version": "0.9.0"}, "researcher", {}, PARAMETERS, "WORKFLOW_PROFILE_NOT_FOUND"),
    ({}, "viewer", {}, PARAMETERS, "WORKFLOW_PROFILE_ROLE_INVALID"),
    ({}, "researcher", {"format": "json"}, PARAMETERS, "WORKFLOW_PROFILE_INPUT_INVALID"),
    ({}, "researcher", {"semanticId": ""}, PARAMETERS, "WORKFLOW_PROFILE_INPUT_INVALID"),
    ({}, "researcher", {}, {**PARAMETERS, "thresholdEventsEnabled": False}, "WORKFLOW_PROFILE_PARAMETER_INVALID"),
    ({}, "researcher", {}, {**PARAMETERS, "versionId": "another"}, "WORKFLOW_PROFILE_PARAMETER_INVALID"),
    ({}, "researcher", {}, {**PARAMETERS, "eventThreshold": float("inf")}, "WORKFLOW_PROFILE_PARAMETER_INVALID"),
    ({}, "researcher", {}, {k: v for k, v in PARAMETERS.items() if k != "eventThreshold"}, "WORKFLOW_PROFILE_PARAMETER_INVALID"),
    ({}, "researcher", {}, {**PARAMETERS, "expectedStart": "not-a-date"}, "WORKFLOW_PROFILE_PARAMETER_INVALID"),
    ({}, "researcher", {}, {**PARAMETERS, "baselineStart": "2023-01-01"}, "WORKFLOW_PROFILE_PARAMETER_INVALID"),
    ({}, "researcher", {}, {**PARAMETERS, "baselineEnd": "2023-01-01"}, "WORKFLOW_PROFILE_PARAMETER_INVALID"),
    ({}, "researcher", {}, {**PARAMETERS, "baselineStart": "2020-01-01T00:00:00+00:00"}, "WORKFLOW_PROFILE_PARAMETER_INVALID"),
])
def test_profile_rejects_inexact_identity_input_or_parameter(
    platform_bundle, ref_patch, role, input_patch, parameters, code,
):
    bundle, _, ref = platform_bundle
    with pytest.raises(ValueError, match=code):
        select_workflow_profile(
            bundle, {**ref, **ref_patch}, role=role,
            inputs={"observations": data_ref(**input_patch)}, parameters=parameters,
        )


def test_tampered_assembly_catalog_is_rejected(platform_bundle, tmp_path):
    bundle, platform, ref = platform_bundle
    (tmp_path / "assembly-lock.json").write_bytes((bundle / "assembly-lock.json").read_bytes())
    tampered = deepcopy(platform)
    by_id(tampered["profiles"], TREND_PROFILE)["fixedParameters"]["thresholdEventsEnabled"] = False
    (tmp_path / "workflow-platform.json").write_text(json.dumps(tampered))
    with pytest.raises(ValueError, match="WORKFLOW_PLATFORM_BUNDLE_INVALID"):
        select_workflow_profile(
            tmp_path, ref, role="researcher", inputs={"observations": data_ref()}, parameters=PARAMETERS,
        )


@pytest.mark.parametrize(("collection", "identity"), [
    ("profiles", TREND_PROFILE), ("resultContracts", "groundwater.monitoring-trend.results"),
])
def test_released_identity_requires_a_new_version_for_new_content(platform_bundle, collection, identity):
    bundle, _, _ = platform_bundle
    previous = read(bundle / "assembly-lock.json")
    candidate = deepcopy(previous["workflowPlatform"])
    reference = by_id(candidate[collection], identity)
    reference["checksum"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="WORKFLOW_PLATFORM_IDENTITY_MUTATED"):
        require_immutable_platform_refs(previous, candidate)
    reference["version"] = "1.1.0"
    require_immutable_platform_refs(previous, candidate)


def test_result_requires_publication_and_complete_exact_inventory(platform_bundle):
    bundle, platform, ref = platform_bundle
    launch = select_workflow_profile(bundle, ref, role="researcher", inputs={"observations": data_ref()}, parameters=PARAMETERS)
    selection = launch["workflowSelection"]
    contract = by_id(platform["resultContracts"], "groundwater.monitoring-trend.results")
    run = {
        **{key: value for key, value in selection.items() if key != "inputBindings" and key != "parameters"},
        "runId": "run", "status": "SUCCEEDED", "arguments": {**selection["parameters"], "versionId": "version"},
        "outputs": [
            data_ref(outputName=output["name"], mediaType=output["mediaType"], format=output["format"], versionId=output["name"])
            for output in contract["outputs"]
        ],
    }
    publication = {"workflowRunId": "run", "state": "PUBLISHED", "publicationId": "published"}
    view = describe_workflow_result(bundle, ref, run, publication)
    assert view["resultStatus"] == "COMPLETE"
    assert view["businessAcceptance"] == "NOT_ASSESSED"
    assert view["contractRef"] == contract["ref"]
    assert [row["dataRef"] for row in view["outputs"]] == run["outputs"]
    assert describe_workflow_result(bundle, ref, run, {**publication, "state": "READY"})["resultStatus"] == "NOT_PUBLISHED"
    assert describe_workflow_result(bundle, ref, {**run, "outputs": run["outputs"][:-1]}, publication)["resultStatus"] == "INCOMPLETE"
    with pytest.raises(ValueError, match="WORKFLOW_PROFILE_RUN_MISMATCH"):
        describe_workflow_result(bundle, ref, {**run, "workflowVersion": "1.0.0"}, publication)
    with pytest.raises(ValueError, match="WORKFLOW_PROFILE_RUN_MISMATCH"):
        describe_workflow_result(bundle, ref, {**run, "arguments": {}}, publication)
    with pytest.raises(ValueError, match="WORKFLOW_PROFILE_RUN_MISMATCH"):
        profile = by_id(platform["profiles"], TREND_PROFILE)
        describe_workflow_result(bundle, ref, {**run, "arguments": profile["fixedParameters"]}, publication)
    with pytest.raises(ValueError, match="WORKFLOW_PROFILE_RUN_MISMATCH"):
        describe_workflow_result(bundle, ref, run, {**publication, "workflowRunId": "other"})
    with pytest.raises(ValueError, match="WORKFLOW_RESULT_OUTPUT_INVALID"):
        describe_workflow_result(bundle, ref, {**run, "outputs": [*run["outputs"], run["outputs"][0]]}, publication)
    with pytest.raises(ValueError, match="WORKFLOW_RESULT_OUTPUT_INVALID"):
        duplicated = [{**output, "versionId": "same-version"} for output in run["outputs"]]
        describe_workflow_result(bundle, ref, {**run, "outputs": duplicated}, publication)


@pytest.mark.parametrize(("field", "code"), [
    ("workflowRef", "WORKFLOW_PROFILE_WORKFLOW_MISMATCH"),
    ("modelRef", "WORKFLOW_PROFILE_MODEL_MISMATCH"),
    ("resultContractRef", "WORKFLOW_PROFILE_RESULT_MISMATCH"),
    ("evidence", "WORKFLOW_PROFILE_EVIDENCE_INVALID"),
    ("evidenceFixture", "WORKFLOW_PROFILE_EVIDENCE_INVALID"),
    ("evidenceCatalog", "WORKFLOW_PROFILE_EVIDENCE_INVALID"),
    ("evidenceOperator", "WORKFLOW_PROFILE_EVIDENCE_INVALID"),
    ("flowEvidenceOperator", "WORKFLOW_PROFILE_EVIDENCE_INVALID"),
    ("evidenceResult", "WORKFLOW_PROFILE_EVIDENCE_INVALID"),
    ("outputs", "WORKFLOW_RESULT_CONTRACT_INVALID"),
])
def test_assembler_rejects_drift_even_if_product_refreshes_the_file_digest(
    platform_bundle, tmp_path, field, code,
):
    bundle, _, _ = platform_bundle
    manifest = read(PRODUCT / "product-manifest.json")
    document = read(PRODUCT / manifest["workflowPlatform"]["path"])
    identity = FLOW_PROFILE if field == "flowEvidenceOperator" else TREND_PROFILE
    changed = by_id(document["profiles"], identity)
    if field == "outputs":
        by_id(document["resultContracts"], "groundwater.monitoring-trend.results")["outputs"].pop()
    elif field == "evidence":
        changed["validationEvidence"]["caseId"] = "other-case"
    elif field == "evidenceFixture":
        cases_ref = changed["validationEvidence"]["cases"]
        cases = read(PRODUCT / cases_ref["path"])
        cases["cases"]["monitoring-trend"]["inputs"]["observations"] = "missing-fixture"
        cases_ref["content"] = cases
    elif field in {"evidenceCatalog", "evidenceResult"}:
        receipt_ref = changed["validationEvidence"]["receipt"]
        receipt = read(PRODUCT / receipt_ref["path"])
        if field == "evidenceCatalog":
            del receipt["operatorCatalogDigest"]
        else:
            receipt["outputs"]["gap-runs"] = None
        receipt_ref["content"] = receipt
    elif field in {"evidenceOperator", "flowEvidenceOperator"}:
        scope_ref = changed["validationEvidence"]["scope"]
        scope = read(PRODUCT / scope_ref["path"])
        node = "velocity" if field == "flowEvidenceOperator" else "aggregate"
        scope["operatorsByNode"][node]["implementationRevision"] = "sha256:" + "a" * 64
        scope_ref["content"] = scope
    else:
        changed[field]["checksum"] = "sha256:" + "a" * 64
    source = tmp_path / "workflow-platform.json"
    changed_evidence = {
        reference["path"]: reference.pop("content")
        for kind, reference in changed["validationEvidence"].items()
        if kind in {"cases", "receipt", "scope"} and "content" in reference
    }
    for profile in document["profiles"]:
        for kind in ("cases", "receipt", "scope"):
            evidence = profile["validationEvidence"][kind]
            copied = tmp_path / evidence["path"]
            copied.parent.mkdir(parents=True, exist_ok=True)
            if evidence["path"] in changed_evidence:
                copied.write_text(json.dumps(changed_evidence[evidence["path"]]))
                evidence["checksum"] = "sha256:" + hashlib.sha256(copied.read_bytes()).hexdigest()
            else:
                copy2(PRODUCT / evidence["path"], copied)
    source.write_text(json.dumps(document))
    executor = read(bundle / "executor-config.json")
    workflows = {
        model["workflow"]["definitionChecksum"]: model["workflow"] for model in executor["models"]
    }
    with pytest.raises(ValueError, match=code):
        load_workflow_platform(
            tmp_path, {"path": source.name, "checksum": "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()},
            workflows=list(workflows.values()), models=executor["models"],
            ui=read(bundle / "product-ui.json"), worker_runtimes=executor["workerRuntimes"],
            composition=set(manifest["compositionOperators"]), catalog_digest=executor["operatorCatalogDigest"],
            fixtures_directory=ROOT / "packages/operator-pack/catalog/fixtures",
        )
