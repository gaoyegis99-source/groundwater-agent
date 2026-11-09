from __future__ import annotations

import json
import httpx

from scripts.evaluation import evaluate_groundwater_agent as evaluation


def test_http_failure_reports_status_and_validation_location_without_prompt(monkeypatch):
    request = httpx.Request("POST", "http://gateway/api/v12/sessions/session/messages")
    response = httpx.Response(422, request=request, json={
        "code": "VALIDATION_ERROR",
        "errors": [{
            "pointer": "/message", "code": "string_too_long",
            "detail": "secret prompt text",
        }],
    })

    def fail(*args):
        raise httpx.HTTPStatusError("secret prompt text", request=request, response=response)

    monkeypatch.setattr(evaluation, "run_case", fail)
    monkeypatch.setattr(evaluation, "prepare_directory", lambda client: None)
    (report,) = evaluation.run_suite(
        "http://gateway", publish=lambda *args: None, mode="fixed",
        repetitions=1, scenarios=("trend",),
    )
    assert report["error_status"] == 422
    assert report["error_body"] == {
        "code": "VALIDATION_ERROR",
        "validation": [{"pointer": "/message", "code": "string_too_long"}],
    }
    assert "secret prompt text" not in json.dumps(report)


def test_failed_session_assertion_reports_state_without_agent_message(monkeypatch):
    session = {
        "agent_steps": ["RUNNING", "FAILED"],
        "confirmation": {"status": "CONFIRMED"},
        "workflow_run_ids": ["workflow-1"],
        "agent_output": {
            "message": "secret prompt text",
            "unresolved_items": ["WORKFLOW_DEADLINE_EXCEEDED", "secret prompt text"],
        },
    }

    def fail(*args):
        raise AssertionError(session)

    monkeypatch.setattr(evaluation, "run_case", fail)
    monkeypatch.setattr(evaluation, "prepare_directory", lambda client: None)
    (report,) = evaluation.run_suite(
        "http://gateway", publish=lambda *args: None, mode="fixed",
        repetitions=1, scenarios=("version_freeze",),
    )
    assert report["assertion_detail"] == {
        "agent_steps": ["RUNNING", "FAILED"],
        "confirmation_status": "CONFIRMED",
        "workflow_run_ids": ["workflow-1"],
        "unresolved_codes": ["WORKFLOW_DEADLINE_EXCEEDED"],
    }
    assert "secret prompt text" not in json.dumps(report)


def test_semantic_cases_share_pagination_inputs_without_cumulative_context(monkeypatch):
    resources = []
    cases = []

    def upload(client, project, name, description, content):
        resources.append((project, name, content))

    def run_case(client, scenario, report, *args):
        assert len(resources) == 41
        cases.append((scenario, report["iteration"]))

    monkeypatch.setattr(evaluation, "upload", upload)
    monkeypatch.setattr(evaluation, "run_case", run_case)
    reports = evaluation.run_suite(
        "http://gateway", publish=lambda *args: None, mode="fixed",
        repetitions=3, before_read=lambda *args: None,
    )
    assert len(cases) == len(evaluation.SCENARIOS) * 3
    assert len(resources) == len({name for _, name, _ in resources}) == 41
    assert all(project == "groundwater-demo" for project, _, _ in resources)
    assert all(report["status"] == "PASS" for report in reports)


def test_excluded_stale_case_does_not_prepare_resources(monkeypatch):
    def unexpected(client):
        raise AssertionError("Excluded cases must not upload INPUTs")

    monkeypatch.setattr(evaluation, "prepare_directory", unexpected)
    reports = evaluation.run_suite(
        "http://gateway", publish=lambda *args: None, mode="fixed",
        repetitions=1, scenarios=("stale",),
    )
    assert reports[0]["status"] == "NOT_RUN"
