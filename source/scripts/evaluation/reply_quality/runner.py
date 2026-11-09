"""Live fixture preparation and single-run orchestration."""
from __future__ import annotations

import re
import time
from uuid import uuid4

import httpx

from scripts.evaluation.evaluate_agent import (
    request,
    submit_message,
    upload,
    wait_rejection,
    wait_session,
)
from scripts.evaluation.evaluate_groundwater_agent import dictionary, document, fixture
from scripts.lib.wire_json import from_wire_json, to_wire_json

from .assessment import assess_reply
from .definitions import PROJECT
from .identity import data_fixture_checksum
from .scenarios import (
    _draft_card_context,
    _execution_card_context,
    _redacted_path,
    _service_error_code,
    _verify_confirmation,
    _verify_execution_history,
    _verify_provider_requests,
    _verify_workflow,
)

def _wait_history(
    client: httpx.Client, session_id: str, *, timeout_seconds: float = 600,
) -> tuple[dict, dict]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        session = request(client, "GET", f"/api/v12/sessions/{session_id}")
        if session["agent_steps"] and session["agent_steps"][-1] in {
            "WAITING_CONFIRMATION", "COMPLETE", "FAILED",
        }:
            history = request(
                client, "GET", f"/api/v12/sessions/{session_id}/messages"
            )
            if history["turns"]:
                return session, history["turns"][-1]
        time.sleep(0.25)
    raise AssertionError("REPLY_QUALITY_TIMEOUT")


def _publish_document(
    knowledge: httpx.Client, identifier: str, body: dict, data_ref: dict,
) -> dict:
    source_names = {
        "sourceId": "source_id", "publishedAt": "published_at",
        "accessedAt": "accessed_at", "reviewAfter": "review_after",
        "validThrough": "valid_through", "lifecycleStatus": "lifecycle_status",
    }
    package = {
        "schema_version": "knowledge-package.v1",
        "project_id": body["projectId"],
        "idempotency_key": "reply-quality-" + identifier,
        "documents": [{
            "knowledge_id": identifier,
            "kind": "definition",
            "title": body["title"],
            "summary": body["summary"],
            "language": body["language"],
            "content_version": body["contentVersion"],
            "sources": [
                {source_names.get(key, key): value for key, value in source.items()}
                for source in body["sources"]
            ],
            "units": [{
                "unit_id": f"unit-{index}",
                "content": unit["content"],
                "source_refs": unit["sourceRefs"],
            } for index, unit in enumerate(body["chunks"])],
            "bindings": [{
                "service_binding": "data-primary",
                "project_id": data_ref["project_id"],
                "dataset_id": data_ref["dataset_id"],
                "version_id": data_ref["version_id"],
                "semantic_id": data_ref["semantic_id"],
            }],
            "provenance": {"purpose": "reply-quality-evaluation"},
        }],
    }
    headers = {"X-Project-ID": body["projectId"]}
    response = knowledge.post(
        "/api/v12/knowledge/imports", json=to_wire_json(package), headers=headers,
    )
    response.raise_for_status()
    import_id = from_wire_json(response.json())["import_id"]
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        state = knowledge.get(
            "/api/v12/knowledge/imports/" + import_id, headers=headers,
        )
        state.raise_for_status()
        job = from_wire_json(state.json())
        if job["state"] == "SUCCEEDED":
            break
        if job["state"] == "FAILED":
            raise ValueError("REPLY_QUALITY_KNOWLEDGE_IMPORT_FAILED")
        time.sleep(0.25)
    else:
        raise ValueError("REPLY_QUALITY_KNOWLEDGE_IMPORT_TIMEOUT")
    versions = job["result"]["versions"]
    if (
        len(versions) != 1
        or versions[0].get("knowledge_id") != identifier
        or not isinstance(versions[0].get("version_id"), str)
    ):
        raise ValueError("REPLY_QUALITY_KNOWLEDGE_PUBLICATION_MISMATCH")
    version = knowledge.get(
        "/api/v12/knowledge/versions/" + versions[0]["version_id"],
        headers=headers,
    )
    version.raise_for_status()
    detail = from_wire_json(version.json())
    if (
        detail.get("document_id") != identifier
        or len(detail.get("body", {}).get("units", [])) != len(body["chunks"])
        or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", detail.get("checksum", "")
        )
    ):
        raise ValueError("REPLY_QUALITY_KNOWLEDGE_PUBLICATION_MISMATCH")
    return {
        "knowledge_id": identifier,
        "version_id": detail["version_id"],
        "checksum": detail["checksum"],
        "unit_ids": [unit["unit_id"] for unit in detail["body"]["units"]],
    }


def _prepare_asset(
    client: httpx.Client,
    knowledge: httpx.Client,
    case: dict,
    iteration: int,
) -> dict:
    data_fixture_checksum()
    content, analyses = fixture()
    resource_name = (
        f"回复质量冻结资料-{case['case_id']}-{iteration}-{uuid4().hex[:8]}"
    )
    title = resource_name + "数据字典与项目约定"
    resource = upload(
        client,
        PROJECT,
        resource_name,
        f"固定合成评测资料；字段语义和方法约定见{title}。",
        content,
    )
    # Each evaluation publishes a fresh Data version. Bind the Knowledge
    # identity and idempotency key to that immutable version so rerunning the
    # gate against a retained candidate cannot collide with an earlier run.
    knowledge_id = (
        f"reply-quality-{case['case_id']}-{iteration}-"
        f"{resource['version_id']}"
    )
    body = dictionary(resource, analyses, "m")
    body["evaluation_scope"] = "REPLY_QUALITY_FROZEN_SYNTHETIC"
    receipt = _publish_document(
        knowledge, knowledge_id, document(PROJECT, body, title), resource,
    )
    return {
        "resource_name": resource_name,
        "resource": resource,
        "analyses": analyses,
        "knowledge": receipt,
    }


def _wait_agent_run_terminal(
    client: httpx.Client,
    agent_run_id: str,
    *,
    timeout_seconds: float = 600,
) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            run = request(
                client,
                "GET",
                f"/api/v12/agent-runs/{agent_run_id}",
                params={"projectId": PROJECT},
            )
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 404:
                time.sleep(0.25)
                continue
            raise
        if run["status"] in {
            "COMPLETE",
            "WAITING_CONFIRMATION",
            "FAILED",
            "CANCELLED",
        }:
            return run
        time.sleep(0.25)
    raise AssertionError("OPTION_AUTO_FOLLOWUP_TIMEOUT")


def _submit_option_followup(
    client: httpx.Client, session_id: str, selected: dict
) -> tuple[dict, dict]:
    receipt = submit_message(
        client,
        session_id,
        selected["message"],
        requested_mode="AUTO",
    )
    if (
        receipt.get("schema_version") != "message-request.v5"
        or receipt.get("requested_mode") != "AUTO"
        or receipt.get("request", {}).get("message") != selected["message"]
        or receipt.get("request", {}).get("requested_mode") != "AUTO"
        or receipt.get("request", {}).get("corrects_request_id") is not None
    ):
        raise AssertionError("OPTION_AUTO_FOLLOWUP_MISMATCH")
    terminal = _wait_agent_run_terminal(client, receipt["agent_run_id"])
    return receipt, terminal


def _require_completed_option_followup(terminal: dict) -> None:
    if terminal.get("status") != "COMPLETE":
        raise AssertionError("OPTION_AUTO_FOLLOWUP_TERMINAL_INVALID")


def _draft_diagnostic_shape(draft: object) -> dict[str, object] | None:
    """Return body-free workflow structure for failed live-quality diagnosis."""
    if not isinstance(draft, dict):
        return None
    result: dict[str, object] = {
        "status": draft.get("status"),
        "error_code": draft.get("error_code"),
        "diagnostics": draft.get("diagnostics"),
    }
    payload = draft.get("payload")
    if not isinstance(payload, dict):
        result["payload_present"] = False
        return result
    workflow = payload.get("workflow")
    workflow = workflow if isinstance(workflow, dict) else {}
    result.update({
        "payload_present": True,
        "summary_length": len(payload.get("summary", ""))
        if isinstance(payload.get("summary"), str) else None,
        "missing_inputs": [
            {
                key: item.get(key)
                for key in ("kind", "name", "required_by")
            }
            for item in payload.get("missing_inputs", [])
            if isinstance(item, dict)
        ],
        "binding_inputs": [
            item.get("input_name")
            for item in payload.get("data_bindings", [])
            if isinstance(item, dict)
        ],
        "graph_inputs": [
            item.get("name")
            for item in workflow.get("inputs", [])
            if isinstance(item, dict)
        ],
        "node_operators": [
            item.get("operator_id")
            for item in workflow.get("nodes", [])
            if isinstance(item, dict)
        ],
        "unresolved_count": len(payload.get("unresolved_items", []))
        if isinstance(payload.get("unresolved_items"), list) else None,
    })
    return result

def run_record(
    client: httpx.Client,
    knowledge: httpx.Client,
    case: dict,
    iteration: int,
    release_identity: dict,
) -> dict:
    started = time.monotonic()
    record = {
        "case_id": case["case_id"],
        "category": case["category"],
        "iteration": iteration,
        "status": "FAILED",
    }
    stage = "asset_setup"
    try:
        asset = (
            _prepare_asset(client, knowledge, case, iteration)
            if case["asset"] else None
        )
        message = case["message"].format(
            resource_name=None if asset is None else asset["resource_name"],
        )
        stage = "session_create"
        created = request(
            client, "POST", "/api/v12/sessions", json={"projectId": PROJECT},
        )
        session_id = created["session_id"]
        record["session_id"] = session_id
        stage = "message_submit"
        receipt = submit_message(
            client,
            session_id,
            message,
            requested_mode=case["requested_mode"],
        )
        record["agent_run_id"] = receipt["agent_run_id"]
        stage = "agent_wait"
        session = wait_session(
            client, session_id, 600, agent_run_id=receipt["agent_run_id"],
        )
        decision = case.get("decision")
        execution_outcome = None
        confirmation = None
        action = None
        workflow = None
        if decision is not None:
            if asset is None:
                raise AssertionError("EXECUTION_ASSET_MISSING")
            confirmation = session["confirmation"]
            if (
                session["agent_steps"][-1] != "WAITING_CONFIRMATION"
                or confirmation is None
            ):
                raise AssertionError("CONFIRMATION_MISSING")
            action, arguments = _verify_confirmation(
                confirmation,
                asset,
                release_identity,
                session,
                record["agent_run_id"],
            )
            path = f"/api/v12/sessions/{session_id}"
            if decision == "confirm":
                request(
                    client,
                    "POST",
                    path + "/confirm",
                    json={"confirmationId": confirmation["confirmation_id"]},
                )
                session = wait_session(client, session_id, 600)
                if len(session["workflow_run_ids"]) != 1:
                    raise AssertionError("WORKFLOW_RUN_MISSING")
                workflow = request(
                    client,
                    "GET",
                    f"/api/v12/workflow-runs/{session['workflow_run_ids'][-1]}",
                    params={"projectId": PROJECT},
                )
                _verify_workflow(
                    workflow, action, arguments, release_identity, confirmation,
                )
                if workflow["run_id"] != session["workflow_run_ids"][0]:
                    raise AssertionError("WORKFLOW_RUN_ID_MISMATCH")
                if session["remote_outputs"] != workflow["outputs"]:
                    raise AssertionError("WORKFLOW_OUTPUT_PROJECTION_MISMATCH")
                execution_outcome = "SUCCEEDED"
            elif decision == "reject":
                request(
                    client,
                    "POST",
                    path + "/reject",
                    json={
                        "confirmationId": confirmation["confirmation_id"],
                        "reason": "冻结质量评测：拒绝执行",
                    },
                )
                session = wait_rejection(
                    client, session_id, confirmation["confirmation_id"], 600,
                )
                if session["workflow_run_ids"] or session["remote_outputs"]:
                    raise AssertionError("REJECTED_EXECUTION_HAS_SIDE_EFFECT")
                execution_outcome = "REJECTED"
            else:
                raise AssertionError("FIXTURE_DECISION_INVALID")
        stage = "history_read"
        session, turn = _wait_history(client, session_id)
        if session["agent_steps"][-1] == "FAILED":
            raise AssertionError("AGENT_FAILED")
        output = session["agent_output"]
        if output is None:
            raise AssertionError("AGENT_OUTPUT_MISSING")
        if decision is not None:
            if confirmation is None or action is None:
                raise AssertionError("EXECUTION_CONFIRMATION_MISSING")
            _verify_execution_history(
                turn, output, confirmation, action, workflow, decision,
            )
        stage = "agent_run_read"
        run = request(
            client,
            "GET",
            f"/api/v12/agent-runs/{turn['agent_run_id']}",
            params={"projectId": PROJECT},
        )
        stage = "agent_trace_read"
        trace = request(
            client,
            "GET",
            f"/api/v12/agent-runs/{turn['agent_run_id']}/trace",
            params={"projectId": PROJECT},
        )
        function_names = [
            span["span_data"]["name"]
            for span in trace["spans"]
            if span["span_data"]["type"] == "function"
        ]
        stage = "provider_requests_read"
        provider_requests = _verify_provider_requests(
            client, turn["agent_run_id"],
        )
        stage = "card_context_build"
        card_context = None
        if case["expected_shape"] == "draft":
            if asset is None:
                raise AssertionError("WORKFLOW_DRAFT_ASSET_MISSING")
            if session["workflow_draft_id"] is None:
                raise AssertionError("WORKFLOW_DRAFT_MISSING")
            draft = request(
                client,
                "GET",
                f"/api/v12/sessions/{session_id}/workflow-drafts/{session['workflow_draft_id']}",
            )
            card_context = _draft_card_context(
                draft, asset, session, turn, release_identity,
            )
        elif case["expected_shape"] == "execution":
            if asset is None or confirmation is None or decision is None:
                raise AssertionError("EXECUTION_CARD_CONTEXT_MISSING")
            card_context = _execution_card_context(
                asset,
                decision=decision,
                workflow=workflow,
            )
        stage = "reply_assessment"
        assertions, review_content = assess_reply(
            case=case,
            output=output,
            session=session,
            turn=turn,
            run=run,
            function_names=function_names,
            provider_requests=provider_requests,
            card_context=card_context,
            execution_outcome=execution_outcome,
            execution_output_count=(
                None if workflow is None else len(workflow["outputs"])
            ),
        )
        assertions["provider_retry_policy_valid"] = True
        option_followup = None
        option_followup_run = None
        if output["interaction_options"]:
            stage = "option_followup"
            selected = output["interaction_options"][0]
            option_followup, option_followup_run = _submit_option_followup(
                client, session_id, selected
            )
            record["option_followup"] = {
                "request_id": option_followup["request_id"],
                "message": option_followup["request"]["message"],
                "requested_mode": option_followup["requested_mode"],
                "terminal_status": option_followup_run["status"],
                "error_code": option_followup_run.get("error_code"),
            }
            _require_completed_option_followup(option_followup_run)
        record.update(
            status="PASS",
            provider=run["provider"],
            model=run["model"],
            token_usage=run["token_usage"],
            provider_requests=provider_requests,
            assertions=assertions,
            review_content={"question": message, **review_content},
            source_count=len(output["presentation"]["sources"]),
            details_count=len(output["presentation"]["details"]),
            option_followup=(
                None
                if option_followup is None
                else record["option_followup"]
            ),
        )
    except AssertionError as error:
        record["failure"] = str(error)
        record["failure_stage"] = stage
        difference_paths = getattr(error, "argument_difference_paths", None)
        if isinstance(difference_paths, list):
            record["argument_difference_paths"] = difference_paths
    except httpx.HTTPStatusError as error:
        record["failure"] = "HTTP_STATUS_ERROR"
        record["failure_stage"] = stage
        record["http_method"] = error.request.method
        record["http_path"] = _redacted_path(error.request.url.path)
        record["http_status"] = error.response.status_code
        record["service_error_code"] = _service_error_code(error.response)
    except httpx.HTTPError as error:
        record["failure"] = "HTTP_TRANSPORT_ERROR"
        record["failure_stage"] = stage
        record["http_method"] = error.request.method if error.request else None
        record["http_path"] = (
            None if error.request is None else _redacted_path(error.request.url.path)
        )
    except (KeyError, TypeError, ValueError) as error:
        record["failure"] = "EVALUATION_ERROR"
        record["failure_stage"] = stage
        record["evaluation_error_type"] = type(error).__name__
        if (
            isinstance(error, KeyError)
            and len(error.args) == 1
            and isinstance(error.args[0], str)
            and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", error.args[0])
        ):
            record["evaluation_error_key"] = error.args[0]
        detail = str(error)
        if re.fullmatch(r"[A-Z][A-Z0-9_:-]{0,127}", detail):
            record["evaluation_error_code"] = detail
    if record["status"] == "FAILED" and record.get("agent_run_id"):
        try:
            failed_run = request(
                client,
                "GET",
                f"/api/v12/agent-runs/{record['agent_run_id']}",
                params={"projectId": PROJECT},
            )
            record["provider"] = failed_run.get("provider")
            record["model"] = failed_run.get("model")
            record["agent_error_code"] = failed_run.get("error_code")
            record["terminal_shape"] = {
                "steps": failed_run.get("steps", []),
                "plan_count": len(failed_run.get("plans", [])),
                "action_count": len(failed_run.get("actions", [])),
                "confirmation_present": bool(
                    isinstance(locals().get("session"), dict)
                    and locals()["session"].get("confirmation")
                ),
            }
            agent_output = failed_run.get("agent_output")
            if isinstance(agent_output, dict):
                presentation = agent_output.get("presentation")
                record["terminal_shape"].update({
                    "has_conclusion": bool(
                        isinstance(presentation, dict)
                        and presentation.get("conclusion") is not None
                    ),
                    "unresolved_count": len(
                        agent_output.get("unresolved_items", [])
                    ),
                    "option_count": len(
                        agent_output.get("interaction_options", [])
                    ),
                })
            failed_trace = request(
                client,
                "GET",
                f"/api/v12/agent-runs/{record['agent_run_id']}/trace",
                params={"projectId": PROJECT},
            )
            record["function_names"] = [
                span["span_data"]["name"]
                for span in failed_trace.get("spans", [])
                if span.get("span_data", {}).get("type") == "function"
            ]
            if record.get("session_id"):
                failed_history = request(
                    client,
                    "GET",
                    f"/api/v12/sessions/{record['session_id']}/messages",
                )
                failed_turn = next((
                    turn
                    for turn in reversed(failed_history.get("turns", []))
                    if turn.get("agent_run_id") == record["agent_run_id"]
                ), None)
                if isinstance(failed_turn, dict):
                    record["agent_diagnostics"] = failed_turn.get("diagnostics")
                session_value = locals().get("session")
                draft_id = (
                    session_value.get("workflow_draft_id")
                    if isinstance(session_value, dict) else None
                )
                if isinstance(draft_id, str) and draft_id:
                    failed_draft = locals().get("draft")
                    if not isinstance(failed_draft, dict):
                        failed_draft = request(
                            client,
                            "GET",
                            f"/api/v12/sessions/{record['session_id']}"
                            f"/workflow-drafts/{draft_id}",
                        )
                    shape = _draft_diagnostic_shape(failed_draft)
                    if shape is not None:
                        record["workflow_draft_shape"] = shape
            record["provider_requests"] = request(
                client,
                "GET",
                f"/api/v12/agent-runs/{record['agent_run_id']}/provider-requests",
                params={"projectId": PROJECT},
            ).get("items", [])
        except httpx.HTTPError:
            pass
    record["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return record
