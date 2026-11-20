from dataclasses import replace

from fastapi.testclient import TestClient
import pytest

from platform_kernel.agent_runtime.input_roles import verify_input_roles
from platform_kernel.agent_runtime.models import WorkflowGraphDraft
from platform_kernel.api import create_app
from platform_kernel.composition import PlatformApplication
from platform_workflow_compiler import WorkflowValidationError


def test_catalog_projects_version_evidence_and_references(kernel_settings):
    settings = kernel_settings()
    identity = ("table.sort", "1.0.0")
    operator = settings.operator_catalog[identity]
    key = "@".join(identity)
    use = {"workflowId": "example.workflow", "version": "1.1.0", "models": [{"modelId": "example.model", "version": "1.2.0"}]}
    evidence = {"checkedOn": "2026-11-19", "reportChecksum": "sha256:" + "a" * 64,
                "coverage": "BROWSER_SINGLE_NODE", "implementationRevision": operator["implementationRevision"]}
    app = PlatformApplication(settings=replace(settings, operator_uses={key: [use]}, operator_validation={key: evidence}))
    response = TestClient(create_app(application=app)).get("/api/v12/capabilities")
    assert response.status_code == 200
    found = next(item for item in response.json()["operators"] if item["operatorId"] == identity[0])
    assert found["workflowUses"] == [use]
    assert found["validation"]["status"] == "PASSED"
    assert found["scientificStatus"] == "REQUIRES_CASE_AND_REVIEW"


@pytest.mark.parametrize(
    ("technical", "current_review", "expected_status", "expected_science"),
    [
        ("stale", True, "STALE", "REVIEW_EVIDENCE_ATTACHED"),
        ("absent", True, "NOT_RECORDED", "REVIEW_EVIDENCE_ATTACHED"),
        ("current", False, "PASSED", "REQUIRES_CASE_AND_REVIEW"),
    ],
)
def test_catalog_projects_scientific_revision_independently(
    kernel_settings, technical, current_review, expected_status, expected_science
):
    settings = kernel_settings()
    identity = ("table.sort", "1.0.0")
    revision = settings.operator_catalog[identity]["implementationRevision"]
    other_revision = "sha256:" + "b" * 64
    review = {
        "benchmark": {"path": "benchmark.json", "checksum": "sha256:" + "c" * 64},
        "domainReview": {"path": "review.json", "checksum": "sha256:" + "d" * 64},
    }
    evidence = {
        "scientificReview": review,
        "scientificReviewImplementationRevision": revision if current_review else other_revision,
    }
    if technical != "absent":
        evidence.update(
            checkedOn="2026-11-19", reportChecksum="sha256:" + "a" * 64,
            coverage="BROWSER_SINGLE_NODE",
            implementationRevision=revision if technical == "current" else other_revision,
        )
    app = PlatformApplication(settings=replace(settings, operator_validation={"@".join(identity): evidence}))
    response = TestClient(create_app(application=app)).get("/api/v12/capabilities")

    assert response.status_code == 200
    found = next(item for item in response.json()["operators"] if item["operatorId"] == identity[0])
    assert found["validation"]["status"] == expected_status
    assert found["scientificStatus"] == expected_science
    assert found["scientificReview"] == (review if current_review else None)


@pytest.mark.parametrize("syntax", [
    "输入角色 vector.spatial_join.target = 《目标图层》\n输入角色 join = 《属性图层》",
    "本轮输入为目标图层（target）=目标图层；用于补充属性的图层（join）=属性图层。",
])
def test_explicit_roles_detect_swapped_same_format_inputs(syntax):
    graph = WorkflowGraphDraft.model_validate({
        "inputs": [], "parameters": [],
        "nodes": [{"nodeId": "spatial", "operatorId": "vector.spatial_join", "operatorVersion": "1.0.0",
                   "inputs": {"target": {"workflowInput": "geom"}, "join": {"workflowInput": "attr"}}, "parameters": {}}],
        "outputs": [{"name": "result", "nodeOutput": {"nodeId": "spatial", "port": "vector"}}],
    })
    evidence = {"exact-target": {"title": "目标图层"}, "exact-join": {"title": "属性图层"}}
    verify_input_roles(syntax, graph, {"geom": "exact-target", "attr": "exact-join"}, evidence, evidence)
    with pytest.raises(WorkflowValidationError, match="WORKFLOW_INPUT_ROLE_MISMATCH"):
        verify_input_roles(syntax, graph, {"geom": "exact-join", "attr": "exact-target"}, evidence, evidence)
    evidence["other-target"] = {"title": "目标图层"}
    with pytest.raises(WorkflowValidationError, match="WORKFLOW_INPUT_ROLE_VERSION_REQUIRED"):
        verify_input_roles(syntax, graph, {"geom": "exact-target", "attr": "exact-join"}, evidence, evidence)
