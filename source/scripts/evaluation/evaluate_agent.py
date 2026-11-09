"""Groundwater summary search-to-result evaluation against an isolated Gateway deployment."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import httpx

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.lib.wire_json import from_wire_json, to_wire_json
from scripts.lib.e2e_http import public_api_headers


LIVE_SCENARIOS = ("selection", "no_results", "ambiguity", "version_freeze")
FIXED_SCENARIOS = (*LIVE_SCENARIOS, "rejection", "idempotency")
CONTENT = b"number\n1\n2\n3\n"
TOOLS = {
    "discover_datasets", "inspect_data_version", "inspect_data_table",
}


def request(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    expected_status: int | None = None,
    **kwargs,
):
    if "json" in kwargs and kwargs["json"] is not None:
        kwargs["json"] = to_wire_json(kwargs["json"])
    if "params" in kwargs and isinstance(kwargs["params"], dict):
        kwargs["params"] = to_wire_json(kwargs["params"])
    response = client.request(method, path, **kwargs)
    response.raise_for_status()
    if expected_status is not None and response.status_code != expected_status:
        raise AssertionError(
            f"{method} {path} returned {response.status_code}; expected {expected_status}"
        )
    return from_wire_json(response.json())


def submit_message(
    client: httpx.Client,
    session_id: str,
    message: str,
    *,
    requested_mode: str = "AUTO",
) -> dict:
    body = {
        "requestId": str(uuid4()),
        "message": message,
        "requestedMode": requested_mode,
    }
    path = f"/api/v12/sessions/{session_id}/messages"
    return request(
        client,
        "POST",
        path,
        expected_status=202,
        json=body,
    )


def upload(
    client,
    project_id,
    name,
    description,
    content=CONTENT,
    dataset_id=None,
    *,
    user_upload=False,
):
    if user_upload and dataset_id is not None:
        raise ValueError("USER_UPLOAD_CANNOT_REVISE_DATASET")
    headers = {"X-Project-ID": project_id, "X-Client-Type": "cli"}
    output = {
        "name": "file" if user_upload else "input", "title": name, "format": "csv",
        "media_type": "text/csv", "policy": "FILE_QUERYABLE",
        "tags": [] if user_upload else ["agent-evaluation", "SYNTHETIC_TEST_ONLY"],
        "semantics": {
            "description": description, "fields": {}, "grain": None,
            "time_range": None, "timezone": None, "crs": None,
            "vertical_datum": None,
            "limitations": [] if user_upload else ["SYNTHETIC_TEST_ONLY"],
            "source": "web_upload" if user_upload else "reply-quality-evaluation",
        },
    }
    if dataset_id is not None:
        history = request(
            client, "GET",
            f"/api/v12/data/datasets/{dataset_id}/versions?limit=100",
            headers=headers,
        )
        current = max(history["items"], key=lambda item: item["created_at"])
        output.update(dataset_id=dataset_id, expected_version=current["version_id"])
    publication = request(
        client, "POST", "/api/v12/data/publications", headers=headers,
        json={
            "idempotency_key": "eval-" + uuid4().hex,
            "outputs": [output], "input_versions": [],
            "provenance": (
                {"client": "platform-web-v12"}
                if user_upload
                else {"purpose": "reply-quality-evaluation"}
            ),
        },
    )
    publication_id = publication["publication_id"]
    item_name = output["name"]
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    response = client.put(
        f"/api/v12/data/publications/{publication_id}/items/{item_name}",
        content=content,
        headers={**headers, "X-Content-SHA256": digest},
    )
    response.raise_for_status()
    request(
        client, "POST", f"/api/v12/data/publications/{publication_id}/prepare",
        headers=headers,
    )
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        state = request(
            client, "GET", f"/api/v12/data/publications/{publication_id}",
            headers=headers,
        )
        if state["state"] == "READY":
            break
        if state["state"] in {"FAILED", "ABORTED"}:
            raise AssertionError(state)
        time.sleep(0.1)
    else:
        raise AssertionError("DATA_PREPARE_TIMEOUT")
    published = request(
        client, "POST", f"/api/v12/data/publications/{publication_id}/commit",
        headers=headers,
    )
    released = published["outputs"][0]
    detail = request(
        client, "GET", f"/api/v12/data/versions/{released['version_id']}",
        headers=headers,
    )
    raw = next(item for item in detail["objects"] if item["role"] == "raw")
    return {
        "kind": "data_version", "service": "data-primary",
        "project_id": project_id, "dataset_id": released["dataset_id"],
        "version_id": released["version_id"],
        "semantic_id": released["semantic_id"], "object_id": raw["object_id"],
        "media_type": raw["media_type"], "format": raw["format"],
        "size_bytes": raw["size"], "checksum": raw["checksum"],
    }


def wait_session(client, session_id, timeout_seconds, *, agent_run_id=None):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        session = request(client, "GET", f"/api/v12/sessions/{session_id}")
        if session["agent_steps"] and session["agent_steps"][-1] in {"WAITING_CONFIRMATION", "COMPLETE", "FAILED"}:
            if session["agent_steps"][-1] == "FAILED":
                failed_run_id = agent_run_id or session.get("agent_run_id")
                code = "UNKNOWN"
                if failed_run_id:
                    run = request(
                        client,
                        "GET",
                        f"/api/v12/agent-runs/{failed_run_id}",
                        params={"projectId": session["project_id"]},
                    )
                    code = run.get("error_code") or "UNKNOWN"
                raise AssertionError("AGENT_FAILED:" + code)
            return session
        if agent_run_id is not None and not session["agent_steps"]:
            try:
                run = request(
                    client,
                    "GET",
                    f"/api/v12/agent-runs/{agent_run_id}",
                    params={"projectId": session["project_id"]},
                )
            except httpx.HTTPStatusError as error:
                # AUTO routing reserves the AgentRun ID before the Router has
                # persisted that run. A short 404 in this window is pending,
                # not a failed evaluation.
                if error.response.status_code == 404:
                    time.sleep(0.25)
                    continue
                raise
            if run["status"] == "FAILED":
                raise AssertionError(
                    "AGENT_FAILED:" + (run.get("error_code") or "UNKNOWN")
                )
        time.sleep(0.25)
    raise AssertionError("AGENT_EVALUATION_TIMEOUT")


def wait_rejection(client, session_id, confirmation_id, timeout_seconds):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        session = request(client, "GET", f"/api/v12/sessions/{session_id}")
        assert not session["workflow_run_ids"] and not session["remote_outputs"], (
            "REJECTION_CREATED_EXECUTION"
        )
        step = session["agent_steps"][-1]
        assert step != "FAILED", session
        confirmation = session["confirmation"]
        assert confirmation["confirmation_id"] == confirmation_id, (
            "REJECTION_CREATED_NEW_CONFIRMATION"
        )
        if step == "COMPLETE" and confirmation["status"] == "REJECTED":
            return session
        time.sleep(0.25)
    raise AssertionError("AGENT_REJECTION_EVALUATION_TIMEOUT")


def inspect_run(client, session, project_id, report):
    run_id = session["agent_run_id"]
    run = request(
        client, "GET", f"/api/v12/agent-runs/{run_id}", params={"projectId": project_id}
    )
    trace = request(
        client,
        "GET",
        f"/api/v12/agent-runs/{run_id}/trace",
        params={"projectId": project_id},
    )
    report["agent_runs"][run_id] = {
        "provider": run["provider"],
        "model": run["model"],
        "status": run["status"],
        "token_usage": run["token_usage"],
        "trace_id": run["trace_id"],
        "error_code": run.get("error_code"),
        "timing": run.get("timing"),
    }
    functions = [
        span["span_data"]
        for span in trace["spans"]
        if span["span_data"]["type"] == "function"
    ]
    report["tool_calls"] = [span["name"] for span in functions]
    return run, functions


def verify_search(functions):
    searches = [span for span in functions if span["name"] == "discover_datasets"]
    assert searches, "SEARCH_NOT_CALLED"


def verify_observations(functions, report, allowed, primary, scenario, observation_audit):
    verify_search(functions)
    inspected = any(
        span["name"] in {"inspect_data_version", "inspect_data_table"}
        for span in functions
    )
    clarification = scenario in {"no_results", "ambiguity"}
    if not clarification:
        assert inspected, "DATA_VERSION_NOT_INSPECTED"
    if observation_audit is None:
        assertions = {
            "search_project_isolation": "NOT_OBSERVED",
            "search_results": "NOT_OBSERVED",
            "exact_data_version_inspected": "NOT_OBSERVED",
        }
    else:
        assertions = observation_audit(
            report["case_id"],
            {"allowed_ids": sorted(allowed), "primary_id": primary,
             "empty": scenario == "no_results", "ambiguous": scenario == "ambiguity",
             "inspect": not clarification},
        )
        assert assertions and all(value == "PASS" for value in assertions.values()), assertions
    report.setdefault("observation_assertions", []).append(assertions)


def verify_execution_replay(report, workflow, observation_audit):
    if observation_audit is None:
        assertions = {"execution_replay_references": "NOT_OBSERVED"}
    else:
        assertions = observation_audit(report["case_id"], {
            "kind": "execution", "tool": "run_summary",
            "workflow_run_id": report["workflow_run_id"], "outputs": workflow["outputs"],
        })
        assert assertions and all(value == "PASS" for value in assertions.values()), assertions
    report["execution_replay_assertions"] = assertions


def verify_confirmation(session, resource, model):
    confirmation = session["confirmation"]
    assert confirmation is not None, "NO_CONFIRMATION"
    assert len(confirmation["actions"]) == 1, "EXPECTED_ONE_ACTION"
    action = confirmation["actions"][0]
    assert action["tool"] == "run_summary"
    for actual, expected in (
        ("model_id", "model_id"),
        ("model_version", "model_version"),
        ("model_checksum", "model_checksum"),
        ("workflow_id", "workflow_id"),
        ("workflow_version", "workflow_version"),
        ("workflow_checksum", "workflow_checksum"),
    ):
        assert action[actual] == model[expected], f"CONFIRMATION_IDENTITY:{actual}"
    assert action["arguments"] == {
        "versionId": resource["version_id"],
        "column": "number",
        "axis": "columns",
    }, "UNEXPECTED_CONFIRMATION_ARGUMENTS"
    return confirmation


def verify_result(client, session, resource, model, report):
    assert session["agent_steps"][-1] == "COMPLETE", session
    assert len(session["workflow_run_ids"]) == 1
    workflow_id = session["workflow_run_ids"][0]
    workflow = request(
        client,
        "GET",
        f"/api/v12/workflow-runs/{workflow_id}",
        params={"projectId": resource["project_id"]},
    )
    assert workflow["status"] == "SUCCEEDED", workflow
    assert workflow["arguments"] == report["confirmation_arguments"]
    assert len(session["remote_outputs"]) == 1
    reference = session["remote_outputs"][0]
    assert workflow["outputs"] == [reference]
    assert reference["kind"] == "data_version" and reference["format"] == "json"
    assert reference["service"] == "data-primary"
    assert reference["output_name"] == "summary"
    result = request(
        client,
        "GET",
        f"/api/v12/data/versions/{reference['version_id']}",
        headers={"X-Project-ID": resource["project_id"], "X-Client-Type": "cli"},
    )
    assert result["version_id"] == reference["version_id"]
    assert result["dataset_id"] == reference["dataset_id"]
    assert result["semantic_id"] == reference["semantic_id"]
    obj = next(
        item
        for item in result["objects"]
        if item["object_id"] == reference["object_id"]
    )
    response = client.get(
        f"/api/v12/data/versions/{reference['version_id']}/objects/{reference['object_id']}/content",
        headers={"X-Project-ID": resource["project_id"], "X-Client-Type": "cli"},
    )
    response.raise_for_status()
    checksum = "sha256:" + hashlib.sha256(response.content).hexdigest()
    assert checksum == reference["checksum"] == obj["checksum"]
    assert len(response.content) == reference["size_bytes"] == obj["size"]
    summary = json.loads(response.content)
    assert result["input_versions"] == [resource["version_id"]]
    assert result["descriptor"]["provenance"]["model_checksum"] == model["model_checksum"]
    assert summary["number"]["count"] == 3 and summary["number"]["mean"] == 2.0
    run, _ = inspect_run(client, session, resource["project_id"], report)
    assert run["remote_outputs"] == [reference]
    assert run["actions"][0]["remote_outputs"] == [reference]
    assert session["agent_output"]["message"]
    assert session["agent_output"]["workflow_runs"] == [workflow_id]
    report.update(
        workflow_run_id=workflow_id,
        job_id=workflow["remote_job_id"],
        result_version_id=reference["version_id"],
        result_assertions={
            "count": 3,
            "mean": 2.0,
            "result_checksum": checksum,
            "input_checksum": resource["checksum"],
        },
    )
    return workflow


def run_case(
    client,
    scenario,
    report,
    timeout_seconds=240,
    audit=None,
    observation_audit=None,
    *,
    user_upload=False,
):
    capabilities = request(client, "GET", "/api/v12/capabilities")
    assert capabilities["product_id"] == "groundwater", "GROUNDWATER_PRODUCT_REQUIRED"
    assert TOOLS <= {item["name"] for item in capabilities["observation_tools"]}
    assert "operators" not in capabilities and "workflows" not in capabilities
    (model,) = [item for item in capabilities["models"] if item["model_id"] == "groundwater.summary"]
    assert model["model_id"] == "groundwater.summary"
    project = report["project_id"]
    resource_name = f"北区监测样本-{report['case_id']}"
    # Distinct fixture bytes make each case independently inspectable.
    precision = report["fixture_precision"]
    content = (
        "number\n" + "".join(f"{number:.{precision}f}\n" for number in (1, 2, 3))
    ).encode()
    primary = upload(
        client, project, resource_name, "正式采样", content,
        user_upload=user_upload,
    )
    decoy = upload(
        client,
        project,
        resource_name if scenario == "ambiguity" else f"{resource_name}备份",
        "测试采样",
        b"number\n100\n200\n300\n",
        user_upload=user_upload,
    )
    report["expected_version_id"] = primary["version_id"]
    session = request(client, "POST", "/api/v12/sessions", json={"projectId": project})
    session_id = session["session_id"]
    report["session_id"] = session_id
    message = (
        f"找到不存在的监测样本-{report['case_id']}，对 number 列做描述统计。"
        if scenario == "no_results"
        else f"找到{resource_name}，对 number 列做描述统计。"
    )
    evidence_marker = (
        f"\n[Evaluation case: {report['case_id']}]" if report.get("mode") == "fixed" else ""
    )
    submit_message(client, session_id, message + evidence_marker)
    session = wait_session(client, session_id, timeout_seconds)
    run, functions = inspect_run(client, session, project, report)
    allowed = {primary["version_id"], decoy["version_id"]}
    verify_observations(functions, report, allowed, primary["version_id"], scenario, observation_audit)
    assert not session["workflow_run_ids"] and not session["remote_outputs"]
    if audit is not None:
        report["before_confirmation"] = audit(project, session_id, None, primary, scenario)
    if scenario in {"no_results", "ambiguity"}:
        assert session["confirmation"] is None and run["actions"] == []
        assert session["agent_steps"][-1] == "COMPLETE"
        final = session["agent_output"]
        assert final["message"] and final["unresolved_items"], (
            "EXPECTED_UNRESOLVED_QUESTION"
        )
        report["clarification"] = final
        if scenario == "no_results":
            return
        submit_message(
            client,
            session_id,
            f"选择描述为正式采样的{resource_name}，对 number 列做描述统计。"
            + evidence_marker,
        )
        session = wait_session(client, session_id, timeout_seconds)
        _, functions = inspect_run(client, session, project, report)
        verify_observations(functions, report, allowed, primary["version_id"], "selection", observation_audit)
        assert not session["workflow_run_ids"] and not session["remote_outputs"]
        if audit is not None:
            report["before_confirmation"] = audit(project, session_id, None, primary, scenario)

    confirmation = verify_confirmation(session, primary, model)
    report["selected_version_id"] = confirmation["actions"][0]["arguments"][
        "versionId"
    ]
    report["confirmation_arguments"] = confirmation["actions"][0]["arguments"]
    report["confirmation_id"] = confirmation["confirmation_id"]
    report["confirmed_model"] = {
        key: confirmation["actions"][0][key]
        for key in (
            "model_id",
            "model_version",
            "model_checksum",
            "workflow_id",
            "workflow_version",
            "workflow_checksum",
        )
    }
    if scenario == "version_freeze":
        newer = upload(
            client,
            project,
            resource_name,
            "正式采样",
            b"number\n10\n20\n30\n",
            primary["dataset_id"],
        )
        assert (
            newer["dataset_id"] == primary["dataset_id"]
            and newer["version_id"] != primary["version_id"]
        )
        report["newer_version_id"] = newer["version_id"]
    if scenario == "rejection":
        request(
            client,
            "POST",
            f"/api/v12/sessions/{session_id}/reject",
            json={
                "confirmationId": confirmation["confirmation_id"],
                "reason": "试验拒绝执行",
            },
        )
        wait_rejection(client, session_id, confirmation["confirmation_id"], timeout_seconds)
        if audit is not None:
            report["after_rejection"] = audit(project, session_id, None, primary, scenario)
        return
    response = client.post(
        f"/api/v12/sessions/{session_id}/confirm",
        json={"confirmationId": confirmation["confirmation_id"]},
    )
    assert response.status_code == 202, response.text
    session = wait_session(client, session_id, timeout_seconds)
    workflow = verify_result(client, session, primary, model, report)
    verify_execution_replay(report, workflow, observation_audit)
    if audit is not None:
        report["store_assertions"] = audit(project, session_id, workflow, primary, scenario)


def run_suite(
    base_url, *, mode, repetitions, scenarios, timeout_seconds=240, audit=None,
    observation_audit=None,
):
    reports = []
    with httpx.Client(
        base_url=base_url,
        timeout=timeout_seconds,
        trust_env=False,
        headers=public_api_headers(),
    ) as client:
        for scenario in scenarios:
            for iteration in range(1, repetitions + 1):
                report = {
                    "mode": mode,
                    "scenario": scenario,
                    "iteration": iteration,
                    "project_id": "groundwater-demo",
                    "case_id": f"{scenario}-{iteration}",
                    "fixture_precision": len(reports),
                    "agent_runs": {},
                    "evidence": "service-stores"
                    if audit is not None
                    else "public-api-and-trace",
                }
                started = time.monotonic()
                try:
                    run_case(client, scenario, report, timeout_seconds, audit, observation_audit)
                    report["status"] = "PASS"
                except (
                    AssertionError,
                    httpx.HTTPError,
                    ValueError,
                    KeyError,
                    subprocess.CalledProcessError,
                ) as error:
                    report["status"] = "FAILED"
                    report["error"] = f"{type(error).__name__}: {error}"
                    if "session_id" in report:
                        try:
                            failed_session = request(client, "GET", f"/api/v12/sessions/{report['session_id']}")
                            if failed_session.get("agent_run_id"):
                                inspect_run(client, failed_session, report["project_id"], report)
                        except (httpx.HTTPError, ValueError, KeyError) as evidence_error:
                            report["failure_evidence"] = "NOT_OBSERVED:" + type(evidence_error).__name__
                    if isinstance(error, subprocess.CalledProcessError):
                        report["audit_stderr"] = error.stderr
                evidence_results = [
                    value
                    for assertions in [*report.get("observation_assertions", []), report.get("execution_replay_assertions", {})]
                    for value in assertions.values()
                ]
                report["assertion_coverage"] = {
                    "observed": evidence_results.count("PASS"),
                    "not_observed": evidence_results.count("NOT_OBSERVED"),
                }
                report["elapsed_seconds"] = round(time.monotonic() - started, 3)
                reports.append(report)
                print(json.dumps(report, ensure_ascii=False), flush=True)
    return reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url", required=True, help="Isolated Groundwater Gateway URL"
    )
    args = parser.parse_args()
    reports = run_suite(
        args.base_url, mode="live", repetitions=3, scenarios=LIVE_SCENARIOS
    )
    raise SystemExit(0 if all(item["status"] == "PASS" for item in reports) else 1)


if __name__ == "__main__":
    main()
