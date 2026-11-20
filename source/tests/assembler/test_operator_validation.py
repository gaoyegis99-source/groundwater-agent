import asyncio
import hashlib
import json
from pathlib import Path

from agents.tool_context import ToolContext
import pytest

from assembler_common import _checksum
from assembler_domain import _load_operator_catalog
from operator_metadata import load_operator_validation
from platform_kernel.agent_runtime.models import WorkflowDraftInput
from platform_kernel.agent_runtime.tooling.catalog_tools import build_catalog_tools
from platform_kernel.agent_runtime.workflow_draft import prepare_workflow_draft
from platform_kernel.application.operator_catalog import operator_metadata


KEY = "table.sort@1.0.0"
REVISION = "sha256:" + "1" * 64
OLD_REVISION = "sha256:" + "2" * 64
OPERATOR = {"id": "table.sort", "version": "1.0.0", "runtime": "native"}
CATALOG = {("table.sort", "1.0.0"): {**OPERATOR, "implementationRevision": REVISION}}


def write_file(directory, name, content):
    path = directory / name
    payload = json.dumps(content).encode()
    path.write_bytes(payload)
    return {"path": name, "checksum": "sha256:" + hashlib.sha256(payload).hexdigest()}


@pytest.fixture
def evidence(tmp_path):
    legacy = write_file(tmp_path, "legacy.json", {
        "schemaVersion": "operator-validation.v1",
        "checkedOn": "2026-11-19",
        "reportChecksum": "sha256:" + "f" * 64,
        "operators": {
            KEY: {"implementationRevision": OLD_REVISION, "coverage": "BROWSER_SINGLE_NODE"},
        },
    })
    identity = {
        "implementationRevision": REVISION,
        "manifestDigest": _checksum(OPERATOR),
        "runtime": "native",
        "smokeDigest": "sha256:" + "c" * 64,
    }
    check = {
        "status": "PASSED", "checkedOn": "2026-11-20", "scope": "Exact fixture contract",
    }
    artifact = write_file(tmp_path, "contract-check.json", {
        "schemaVersion": "operator-evidence-receipt.v1",
        "technicalChecks": {KEY: {**identity, "results": {"SCHEMA_CONTRACT": check}}},
    })
    return {
        "schemaVersion": "operator-validation.v2",
        "legacyTechnicalEvidence": legacy,
        "operators": {
            KEY: {
                "technicalVerification": {
                    **identity, "checks": {"SCHEMA_CONTRACT": {**check, "artifact": artifact}},
                },
                "scientificReview": {"status": "NOT_ASSESSED", "reason": "No domain review"},
            },
        },
    }


def reviewed(directory):
    return {
        "status": "REVIEWED",
        "implementationRevision": REVISION,
        "manifestDigest": _checksum(OPERATOR),
        "checkedOn": "2026-11-20",
        "reviewer": "Test reviewer",
        "scope": "Benchmark for the declared input domain",
        "benchmark": write_file(directory, "benchmark.json", {"case": "example"}),
        "domainReview": write_file(directory, "review.json", {"conclusion": "example"}),
    }


def load(directory, evidence):
    reference = write_file(directory, "evidence.json", evidence)
    return load_operator_validation(directory, reference, operators=CATALOG)


def test_v2_preserves_the_legacy_technical_projection(tmp_path, evidence):
    assert load(tmp_path, evidence) == load_operator_validation(
        tmp_path, evidence["legacyTechnicalEvidence"],
    )


def test_v2_requires_the_frozen_catalog(tmp_path, evidence):
    reference = write_file(tmp_path, "evidence.json", evidence)
    with pytest.raises(ValueError, match="PRODUCT_OPERATOR_VALIDATION_INVALID"):
        load_operator_validation(tmp_path, reference)


@pytest.mark.parametrize("field", ["runtime", "implementationRevision", "manifestDigest", "smokeDigest", "outcome"])
def test_v2_refuses_technical_claims_without_matching_receipt(tmp_path, evidence, field):
    record = evidence["operators"][KEY]["technicalVerification"]
    if field == "runtime":
        record["runtime"] = "qgis"
    elif field == "outcome":
        record["checks"]["SCHEMA_CONTRACT"]["status"] = "FAILED"
    else:
        record[field] = "sha256:" + "d" * 64

    with pytest.raises(ValueError, match="PRODUCT_OPERATOR_VALIDATION_INVALID"):
        load(tmp_path, evidence)


def test_v2_refuses_a_valid_file_digest_for_another_operators_receipt(tmp_path, evidence):
    path = tmp_path / "contract-check.json"
    receipt = json.loads(path.read_bytes())
    receipt["technicalChecks"]["table.sort@1.0.1"] = receipt["technicalChecks"].pop(KEY)
    evidence["operators"][KEY]["technicalVerification"]["checks"]["SCHEMA_CONTRACT"]["artifact"] = (
        write_file(tmp_path, path.name, receipt)
    )

    with pytest.raises(ValueError, match="PRODUCT_OPERATOR_VALIDATION_INVALID"):
        load(tmp_path, evidence)


def test_v2_distinguishes_copied_review_bytes_from_independent_evidence(tmp_path, evidence):
    review = reviewed(tmp_path)
    review["domainReview"] = write_file(tmp_path, "copy.json", {"case": "example"})
    evidence["operators"][KEY]["scientificReview"] = review

    with pytest.raises(ValueError, match="PRODUCT_OPERATOR_VALIDATION_INVALID"):
        load(tmp_path, evidence)


def test_v2_review_projects_with_its_own_revision(tmp_path, evidence):
    review = reviewed(tmp_path)
    evidence["operators"][KEY]["scientificReview"] = review

    row = load(tmp_path, evidence)[KEY]

    assert row["implementationRevision"] == OLD_REVISION
    assert row["scientificReviewImplementationRevision"] == REVISION
    assert row["scientificReview"] == {
        "benchmark": review["benchmark"], "domainReview": review["domainReview"],
    }


def test_v2_review_requires_current_manifest_digest(tmp_path, evidence):
    review = reviewed(tmp_path)
    review["manifestDigest"] = OLD_REVISION
    evidence["operators"][KEY]["scientificReview"] = review

    assert "scientificReview" not in load(tmp_path, evidence)[KEY]


@pytest.mark.parametrize("invalid", ["artifact", "outside", "unknown_version", "review", "date"])
def test_v2_rejects_invalid_evidence(tmp_path, evidence, invalid):
    row = evidence["operators"][KEY]
    contract = row["technicalVerification"]["checks"]["SCHEMA_CONTRACT"]
    if invalid == "artifact":
        (tmp_path / "contract-check.json").write_text("{}")
    elif invalid == "outside":
        contract["artifact"]["path"] = "../contract-check.json"
    elif invalid == "unknown_version":
        evidence["operators"]["table.sort@1.0.1"] = evidence["operators"].pop(KEY)
    elif invalid == "review":
        review = reviewed(tmp_path)
        review["domainReview"] = review["benchmark"]
        row["scientificReview"] = review
    else:
        contract["checkedOn"] = "2026-02-30"

    with pytest.raises(ValueError, match="PRODUCT_OPERATOR_VALIDATION_INVALID"):
        load(tmp_path, evidence)


def test_v2_preserves_a_review_when_technical_evidence_is_absent(tmp_path, evidence):
    del evidence["legacyTechnicalEvidence"]
    del evidence["operators"][KEY]["technicalVerification"]
    evidence["operators"][KEY]["scientificReview"] = reviewed(tmp_path)

    row = load(tmp_path, evidence)[KEY]

    assert row["scientificReviewImplementationRevision"] == REVISION
    assert "implementationRevision" not in row


def test_historical_v2_evidence_survives_catalog_growth_and_revision_drift(tmp_path, evidence):
    reference = write_file(tmp_path, "evidence.json", evidence)
    latest = {
        ("table.sort", "1.0.0"): {**CATALOG["table.sort", "1.0.0"], "implementationRevision": "sha256:" + "e" * 64},
        ("table.sort", "1.1.0"): {**OPERATOR, "version": "1.1.0", "implementationRevision": REVISION},
    }
    result = load_operator_validation(tmp_path, reference, operators=latest)

    assert set(result) == {KEY}
    assert result[KEY]["implementationRevision"] == OLD_REVISION


def test_groundwater_pilot_receipt_preserves_historical_scope():
    source = Path(__file__).resolve().parents[2]
    product_dir = source / "products/groundwater"
    product = json.loads((product_dir / "product-manifest.json").read_bytes())
    sidecar = json.loads((product_dir / product["operatorValidation"]["path"]).read_bytes())
    operators, _ = _load_operator_catalog(source)
    records = load_operator_validation(product_dir, product["operatorValidation"], operators=operators)

    for key, inherited in load_operator_validation(product_dir, sidecar["legacyTechnicalEvidence"]).items():
        assert all(records[key][field] == value for field, value in inherited.items() if field != "scientificReview")
    pilot = sidecar["operators"]
    reference = pilot["raster.reproject@1.1.0"]["technicalVerification"]["checks"]["SMOKE"]["artifact"]
    receipt = json.loads((product_dir / reference["path"]).read_bytes())
    witnessed = [key for run in receipt["workerSmokes"].values() for key in run["identities"]]
    assert len(witnessed) == len(set(witnessed)) == receipt["schemaContract"]["manifestAndSmokePairs"]
    for key, proof in receipt["technicalChecks"].items():
        technical = pilot[key]["technicalVerification"]
        assert {field: value for field, value in proof.items() if field != "results"} == {
            field: value for field, value in technical.items() if field != "checks"
        }
        run = receipt["workerSmokes"][proof["runtime"]]
        assert key in run["identities"]
        if proof["runtime"] == "remote_http":
            assert run["scope"] == "DECLARATION_PORT_CHECK"
            assert "SMOKE" not in proof["results"]
            assert technical["checks"]["SMOKE"]["status"] == "NOT_ASSESSED"
        else:
            sample = run["pilot"][key]
            assert sample["status"] == "PASS"
            assert "@".join((sample["operator"]["id"], sample["operator"]["version"])) == key
            assert sample["operator"]["runtime"] == proof["runtime"]
            assert sample["operator"]["implementationRevision"] == proof["implementationRevision"]
            assert sample["manifestDigest"] == proof["manifestDigest"]
            assert sample["smokeDigest"] == proof["smokeDigest"]


def test_reading_historical_operator_cannot_authorize_dynamic_composition(kernel_settings):
    settings = kernel_settings("groundwater")
    composition = settings.workflow_composition
    catalog = settings.operator_catalog
    identity = ("raster.reproject", "1.0.0")
    lookup = next(tool for tool in build_catalog_tools(
        catalog, composition.operators,
        metadata=lambda operator: operator_metadata(operator, settings),
    ) if tool.name == "get_operator")
    arguments = {
        "operator_id": identity[0],
        "version": identity[1],
        "progress_intent": {
            "capability": "workflow.operator.inspect",
            "objective": "Read the installed raster reprojection operator",
            "template_key": "workflow.operator.inspect",
        },
    }
    encoded_arguments = json.dumps(arguments)
    context = ToolContext(
        context={}, tool_name=lookup.name, tool_call_id="inspect", tool_arguments=encoded_arguments,
    )
    evidence = asyncio.run(lookup.on_invoke_tool(context, encoded_arguments))
    assert evidence["implementation_revision"] == catalog[identity]["implementationRevision"]
    assert evidence["validation"]["status"] == "PASSED"
    assert evidence["confirmation_supported"] is False
    assert ("raster.reproject", "1.1.0") in composition.operators

    draft = WorkflowDraftInput.model_validate({
        "title": "Raster reprojection",
        "summary": "Reproject a raster when input is available.",
        "graph": {
            "inputs": [{"name": "source", "cardinality": "one", "mediaTypes": ["image/tiff"], "formats": ["geotiff"]}],
            "parameters": ["target"],
            "nodes": [{
                "nodeId": "reproject",
                "operatorId": identity[0], "operatorVersion": identity[1],
                "inputs": {"raster": {"workflowInput": "source"}},
                "parameters": {"targetCrs": {"workflowParameter": "target"}},
            }],
            "outputs": [{"name": "raster", "nodeOutput": {"nodeId": "reproject", "port": "raster"}}],
        },
        "data_bindings": [],
        "parameter_values": {"target": "EPSG:3857"},
        "parameter_sources": [{"name": "target", "source": "USER", "explanation": "Requested CRS"}],
        "knowledge_refs": [],
        "unresolved_items": ["Provide the source raster."],
        "limitations": [],
    })
    payload = prepare_workflow_draft(draft, [
        {"type": "function_call", "call_id": "inspect", "name": "get_operator"},
        {"type": "function_call_output", "call_id": "inspect", "output": evidence},
    ], settings, "reprojection", user_goal="Reproject a raster to EPSG:3857")

    assert payload["composition_check"]["code"] == "COMPOSITION_OPERATOR_UNSUPPORTED"
    assert not payload.get("execution_allowed")
