"""Scenario-specific confirmation, workflow, and execution checks."""
from __future__ import annotations

from collections import defaultdict
import os
import re

import httpx

from scripts.evaluation.evaluate_agent import request

from .definitions import PROJECT


class _ConfirmationArgumentsMismatchError(AssertionError):
    def __init__(self, paths: list[str]):
        super().__init__("CONFIRMATION_ARGUMENTS_MISMATCH")
        self.argument_difference_paths = paths


def _argument_difference_paths(
    expected: object, actual: object, path: str = "",
) -> list[str]:
    if isinstance(expected, dict) and isinstance(actual, dict):
        paths = []
        for key in sorted(set(expected) | set(actual)):
            child = f"{path}/{key}"
            if key not in expected or key not in actual:
                paths.append(child)
            else:
                paths.extend(_argument_difference_paths(
                    expected[key], actual[key], child,
                ))
        return paths
    if isinstance(expected, list) and isinstance(actual, list):
        paths = []
        for index in range(max(len(expected), len(actual))):
            child = f"{path}/{index}"
            if index >= len(expected) or index >= len(actual):
                paths.append(child)
            else:
                paths.extend(_argument_difference_paths(
                    expected[index], actual[index], child,
                ))
        return paths
    return [] if expected == actual else [path or "/"]

def _release_model(release_identity: dict, tool: str) -> dict:
    matches = [
        model for model in release_identity["models"] if model["tool"] == tool
    ]
    if len(matches) != 1:
        raise AssertionError("RELEASE_MODEL_IDENTITY_MISSING")
    return matches[0]


def _expected_trend_arguments(asset: dict, release_identity: dict) -> dict:
    method = asset["analyses"]["run_monitoring_trend"]
    return {
        **release_identity["execution_contracts"]["run_monitoring_trend"][
            "defaultParameters"
        ],
        "versionId": asset["resource"]["version_id"],
        **method["bindings"],
        **method["parameters"],
    }


def _verify_confirmation(
    confirmation: dict,
    asset: dict,
    release_identity: dict,
    session: dict,
    agent_run_id: str,
) -> tuple[dict, dict]:
    actions = confirmation.get("actions")
    if (
        confirmation.get("status") != "PENDING"
        or confirmation.get("revision") != 1
        or confirmation.get("kind") != "INITIAL"
        or confirmation.get("replaces_confirmation_id") is not None
        or confirmation.get("source_workflow_run_id") is not None
        or confirmation.get("goal_revision") != session["goal_revision"]
        or confirmation.get("agent_run_id") != agent_run_id
        or session["workflow_run_ids"]
        or session["remote_outputs"]
        or not isinstance(actions, list)
        or len(actions) != 1
    ):
        raise AssertionError("EXPECTED_ONE_CONFIRMATION_ACTION")
    action = actions[0]
    if set(action) != {
        "tool_call_id", "tool", "model_id", "model_version", "model_checksum",
        "workflow_id", "workflow_version", "workflow_checksum",
        "input_schema_checksum", "fingerprint", "arguments",
        "input_references",
    }:
        raise AssertionError("CONFIRMATION_ACTION_SCHEMA_MISMATCH")
    model = _release_model(release_identity, "run_monitoring_trend")
    expected_identity = {
        "tool": model["tool"],
        "model_id": model["modelId"],
        "model_version": model["modelVersion"],
        "model_checksum": model["modelChecksum"],
        "workflow_id": model["workflowId"],
        "workflow_version": model["workflowVersion"],
        "workflow_checksum": model["workflowChecksum"],
    }
    if any(action.get(key) != value for key, value in expected_identity.items()):
        raise AssertionError("CONFIRMATION_IDENTITY_MISMATCH")
    contract = release_identity["execution_contracts"][action["tool"]]
    if (
        action["input_schema_checksum"] != contract["inputSchemaChecksum"]
        or not isinstance(action["fingerprint"], str)
        or re.fullmatch(r"[0-9a-f]{64}", action["fingerprint"]) is None
    ):
        raise AssertionError("CONFIRMATION_SCHEMA_IDENTITY_MISMATCH")
    arguments = _expected_trend_arguments(asset, release_identity)
    if action.get("arguments") != arguments:
        raise _ConfirmationArgumentsMismatchError(
            _argument_difference_paths(arguments, action.get("arguments"))[:32]
        )
    expected_reference = {
        key: value for key, value in asset["resource"].items()
        if key != "project_id"
    }
    if action.get("input_references") != {"versionId": expected_reference}:
        raise AssertionError("CONFIRMATION_INPUT_REFERENCES_MISMATCH")
    return action, arguments


def _verify_workflow(
    workflow: dict,
    action: dict,
    arguments: dict,
    release_identity: dict,
    confirmation: dict,
) -> None:
    for field in (
        "model_id", "model_version", "model_checksum", "workflow_id",
        "workflow_version", "workflow_checksum", "tool_call_id",
    ):
        if workflow.get(field) != action.get(field):
            raise AssertionError("WORKFLOW_IDENTITY_MISMATCH")
    contract = release_identity["execution_contracts"][action["tool"]]
    if (
        workflow.get("project_id") != PROJECT
        or workflow.get("agent_run_id") != confirmation["agent_run_id"]
        or workflow.get("position") != 0
        or workflow.get("execution_service") != contract["executionService"]
        or workflow.get("executor_id") != contract["executorId"]
        or workflow.get("error") is not None
        or not isinstance(workflow.get("remote_job_id"), str)
        or not workflow["remote_job_id"]
    ):
        raise AssertionError("WORKFLOW_EXECUTION_IDENTITY_MISMATCH")
    if workflow.get("arguments") != arguments:
        raise AssertionError("WORKFLOW_ARGUMENTS_MISMATCH")
    if workflow.get("status") != "SUCCEEDED":
        raise AssertionError("WORKFLOW_NOT_SUCCEEDED")
    expected_outputs = release_identity["final_outputs"][action["tool"]]
    actual_outputs = [item.get("output_name") for item in workflow["outputs"]]
    if (
        actual_outputs != expected_outputs
        or len(actual_outputs) != len(set(actual_outputs))
        or any(
            set(item) != {
                "kind", "service", "dataset_id", "version_id", "semantic_id",
                "object_id", "media_type", "format", "size_bytes", "checksum",
                "output_name",
            }
            or item["kind"] != "data_version"
            or item["service"] != "data-primary"
            or any(
                not isinstance(item[field], str) or not item[field]
                for field in ("dataset_id", "version_id", "semantic_id", "object_id")
            )
            or type(item["size_bytes"]) is not int
            or item["size_bytes"] < 0
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", item["checksum"])
            for item in workflow["outputs"]
        )
    ):
        raise AssertionError("WORKFLOW_OUTPUTS_MISMATCH")

def _draft_card_context(
    draft: dict,
    asset: dict,
    session: dict,
    turn: dict,
    release_identity: dict,
) -> dict:
    payload = draft.get("payload")
    if (
        draft.get("schema_version") != "workflow-draft-resource.v1"
        or draft.get("draft_id") != session["workflow_draft_id"]
        or draft.get("session_id") != session["session_id"]
        or draft.get("agent_run_id") != turn["agent_run_id"]
        or draft.get("goal_revision") != turn["goal_revision"]
        or draft.get("status") != "COMPLETE"
        or draft.get("error_code") is not None
        or draft.get("diagnostics") is not None
        or not isinstance(payload, dict)
        or payload.get("schema_version") != "workflow-draft.v1"
        or payload.get("execution_allowed") is not False
        or payload.get("operator_catalog_digest")
        != release_identity["operator_catalog_digest"]
        or session["confirmation"] is not None
        or session["workflow_run_ids"]
        or session["remote_outputs"]
        or turn["confirmations"]
        or turn["execution_tasks"]
    ):
        raise AssertionError("WORKFLOW_DRAFT_INVALID")
    workflow = payload["workflow"]
    if (
        workflow.get("workflow_id") != "draft." + draft["draft_id"]
        or workflow.get("version") != "0.1.0"
    ):
        raise AssertionError("WORKFLOW_DRAFT_IDENTITY_MISMATCH")
    nodes = workflow["nodes"]
    if (
        len(nodes) != 2
        or {node["operator_id"] for node in nodes}
        != {"table.sort", "statistics.describe"}
    ):
        raise AssertionError("WORKFLOW_DRAFT_OPERATOR_ORDER_MISMATCH")
    sort = next(node for node in nodes if node["operator_id"] == "table.sort")
    describe = next(
        node for node in nodes if node["operator_id"] == "statistics.describe"
    )
    source_input = sort["inputs"].get("data", {}).get("workflow_input")
    rules_input = sort["inputs"].get("rules", {}).get("workflow_input")
    inputs = {item["name"]: item for item in workflow["inputs"]}
    if (
        not source_input
        or not rules_input
        or source_input == rules_input
        or describe["inputs"].get("data") != {
            "node_output": {"node_id": sort["node_id"], "port": "data"},
        }
        or rules_input not in inputs
        or "json" not in inputs[rules_input]["formats"]
        or "application/json" not in inputs[rules_input]["media_types"]
    ):
        raise AssertionError("WORKFLOW_DRAFT_OPERATOR_CHAIN_MISMATCH")
    operators = payload["operators"]
    if (
        {item["node_id"] for item in operators}
        != {sort["node_id"], describe["node_id"]}
        or {item["operator_id"] for item in operators}
        != {"table.sort", "statistics.describe"}
        or any(
            item["operator_id"]
            != next(node for node in nodes if node["node_id"] == item["node_id"])[
                "operator_id"
            ]
            for item in operators
        )
        or any(
            node["operator_version"]
            != release_identity["draft_operators"][node["operator_id"]]["version"]
            for node in nodes
        )
        or any(
            item["operator_version"]
            != release_identity["draft_operators"][item["operator_id"]]["version"]
            or item["implementation_revision"]
            != release_identity["draft_operators"][item["operator_id"]][
                "implementationRevision"
            ]
            for item in operators
        )
    ):
        raise AssertionError("WORKFLOW_DRAFT_OPERATOR_IDENTITY_MISMATCH")
    missing = payload["missing_inputs"]
    if (
        len(missing) != 1
        or missing[0].get("kind") != "DATA"
        or missing[0].get("name") != rules_input
        or missing[0].get("required_by") != [
            {"node_id": sort["node_id"], "port": "rules"},
        ]
    ):
        raise AssertionError("WORKFLOW_DRAFT_RULES_GAP_MISSING")
    bindings = payload["data_bindings"]
    if (
        len(bindings) != 1
        or bindings[0].get("input_name") != source_input
        or bindings[0].get("data_ref") != {
            key: asset["resource"][key]
            for key in (
                "kind", "service", "dataset_id", "version_id", "semantic_id",
                "object_id", "checksum", "media_type", "format", "size_bytes",
            )
        }
        or any(binding.get("input_name") == rules_input for binding in bindings)
    ):
        raise AssertionError("WORKFLOW_DRAFT_DATA_BINDING_MISMATCH")
    if (
        len(workflow["outputs"]) != 1
        or workflow["outputs"][0]["node_output"] != {
            "node_id": describe["node_id"], "port": "summary",
        }
    ):
        raise AssertionError("WORKFLOW_DRAFT_OUTPUT_MISMATCH")
    unresolved = "\n".join(payload["unresolved_items"])
    if not all(term in unresolved for term in ("排序", "规则")):
        raise AssertionError("WORKFLOW_DRAFT_UNRESOLVED_ITEM_MISSING")
    if "axis" in payload["parameter_values"] and (
        payload["parameter_values"]["axis"] != "columns"
        or not any(
            source["name"] == "axis"
            and source["source"] == "OPERATOR_DEFAULT"
            for source in payload["parameter_sources"]
        )
    ):
        raise AssertionError("WORKFLOW_DRAFT_AXIS_INVALID")
    return {
        "kind": "workflow_draft",
        "title": payload["title"],
        "summary": payload["summary"],
        "missing": ["缺少资料：排序规则"],
        "unresolved_items": payload["unresolved_items"],
        "steps": [item["title"] for item in payload["operators"]],
        "outputs": ["描述统计结果"],
        "limitations": payload["limitations"],
        "parameter_explanations": [
            item["explanation"] for item in payload["parameter_sources"]
        ],
        "execution_allowed": False,
    }


def _execution_card_context(
    asset: dict,
    *,
    decision: str,
    workflow: dict | None,
) -> dict:
    output_count = 0 if workflow is None else len(workflow["outputs"])
    return {
        "kind": "execution",
        "analysis": "监测趋势分析",
        "resource": asset["resource_name"],
        "confirmation_summary": "已核对监测趋势分析的资料与参数",
        "decision": decision,
        "status": "执行成功" if decision == "confirm" else "已拒绝执行",
        "results": f"已发布 {output_count} 项结果" if output_count else "没有发布结果",
    }


def _verify_provider_requests(
    client: httpx.Client, agent_run_id: str,
) -> list[dict]:
    receipt = request(
        client,
        "GET",
        f"/api/v12/agent-runs/{agent_run_id}/provider-requests",
        params={"projectId": PROJECT},
    )
    items = receipt.get("items")
    if not isinstance(items, list) or not items:
        raise AssertionError("PROVIDER_REQUEST_RECORDS_MISSING")
    expected = {
        "provider": os.environ["PLATFORM_AGENT_PROVIDER"],
        "model": os.environ["PLATFORM_AGENT_MODEL"],
        "protocol": os.environ["PLATFORM_AGENT_PROTOCOL"],
    }
    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        if (
            any(item.get(key) != value for key, value in expected.items())
            or item.get("outcome") == "STARTED"
            or item.get("attempt") not in {1, 2}
            or type(item.get("latency_ms")) is not int
            or item["latency_ms"] < 0
            or any(
                type(item.get(key)) is not int or item[key] < 0
                for key in ("input_tokens", "output_tokens", "total_tokens")
            )
        ):
            raise AssertionError("PROVIDER_REQUEST_RECORD_INVALID")
        grouped[item["stage"]].append(item)
    for attempts in grouped.values():
        attempts.sort(key=lambda item: item["attempt"])
        if [item["attempt"] for item in attempts] not in ([1], [1, 2]):
            raise AssertionError("PROVIDER_RETRY_LIMIT_EXCEEDED")
        if len(attempts) == 2 and (
            attempts[0]["outcome"] != "FAILED"
            or attempts[0]["error_class"] != "PROTOCOL_OR_SCHEMA_INVALID"
        ):
            raise AssertionError("PROVIDER_RETRY_REASON_FORBIDDEN")
    return items


def _redacted_path(path: str) -> str:
    value = re.sub(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        "{id}", path, flags=re.IGNORECASE,
    )
    return re.sub(r"(?<![A-Za-z0-9])[0-9a-f]{24,}(?![A-Za-z0-9])", "{id}", value)


def _service_error_code(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    code = error.get("code") if isinstance(error, dict) else body.get("code")
    return code if isinstance(code, str) and len(code) <= 120 else None


def _verify_execution_history(
    turn: dict,
    output: dict,
    confirmation: dict,
    action: dict,
    workflow: dict | None,
    decision: str,
) -> None:
    confirmations = turn["confirmations"]
    if len(confirmations) != 1:
        raise AssertionError("EXECUTION_CONFIRMATION_HISTORY_MISMATCH")
    persisted = confirmations[0]
    expected_decision = "CONFIRM" if decision == "confirm" else "REJECT"
    expected_status = "CONFIRMED" if decision == "confirm" else "REJECTED"
    if (
        persisted["confirmation_id"] != confirmation["confirmation_id"]
        or persisted["agent_run_id"] != turn["agent_run_id"]
        or persisted["goal_revision"] != turn["goal_revision"]
        or persisted["status"] != expected_status
        or persisted["decision"] != expected_decision
        or persisted["decision_state"] is not None
    ):
        raise AssertionError("EXECUTION_CONFIRMATION_HISTORY_MISMATCH")
    tasks = turn["execution_tasks"]
    if decision == "reject":
        if tasks or output["workflow_runs"]:
            raise AssertionError("REJECTED_EXECUTION_HISTORY_MISMATCH")
        if persisted["reason"] != "冻结质量评测：拒绝执行":
            raise AssertionError("REJECTION_REASON_MISMATCH")
        return
    if workflow is None or len(tasks) != 1:
        raise AssertionError("EXECUTION_TASK_HISTORY_MISMATCH")
    task = tasks[0]
    expected = {
        "workflow_run_id": workflow["run_id"],
        "agent_run_id": turn["agent_run_id"],
        "tool_call_id": action["tool_call_id"],
        "position": 0,
        "confirmation_id": confirmation["confirmation_id"],
        "attempt": 1,
        "retry_of_workflow_run_id": None,
        "model_id": action["model_id"],
        "model_version": action["model_version"],
        "workflow_id": action["workflow_id"],
        "workflow_version": action["workflow_version"],
        "status": "SUCCEEDED",
        "output_count": len(workflow["outputs"]),
        "is_latest_attempt": True,
        "error_code": None,
    }
    if any(task.get(key) != value for key, value in expected.items()):
        raise AssertionError("EXECUTION_TASK_HISTORY_MISMATCH")
    if output["workflow_runs"] != [workflow["run_id"]]:
        raise AssertionError("EXECUTION_OUTPUT_RUN_MISMATCH")
