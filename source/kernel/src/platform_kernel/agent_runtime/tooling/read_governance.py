"""Read-tool budgets, convergence, retry, and duplicate governance."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from agents import FunctionTool

from ...errors import AgentRunError
from ...tool_progress import builtin_progress_definition, control_tool_outcome, validate_builtin_progress_intent
from ..evidence import _data_reference
from ..governance import (
    advance_from_discovery,
    evidence_read_enabled,
    governance,
    non_submit_enabled,
    preparation_tool_enabled,
    read_tool_enabled,
)
from ..protocol_repair import stage_protocol_retry

from .observations import _normalize_observation_arguments
from .replies import (
    _has_evidence_references, _record_read_evidence,
    _record_retrieved_evidence,
)


def _pending_exact_evidence(control: dict[str, Any]) -> bool:
    read = {tuple(identity) for identity in control["knowledge_evidence"]}
    return any(
        tuple(identity) not in read
        for identity in control.get("knowledge_exact_candidates", [])
    )


def _proposal_evidence_read_enabled(context, agent) -> bool:
    control = governance(context.context)
    return read_tool_enabled(context, agent) or (
        control["phase"] == "PROPOSAL" and _pending_exact_evidence(control)
    )


def _control_result(
    status: str, outcome_code: str, **details: object
) -> dict[str, object]:
    return {
        "status": status,
        **details,
        "progressOutcome": control_tool_outcome(outcome_code),
    }




def _prepare_tools(
    tools: list[Any], *, database: Path, provider: str, read_names: set[str],
    budget_names: set[str], converge_evidence: bool,
    finalize_after_evidence: bool,
    preparation_requires_knowledge: bool = False,
) -> list[Any]:
    for tool in tools:
        tool.params_json_schema = _strip_schema_titles(tool.params_json_schema)
        if tool.output_json_schema is not None:
            tool.output_json_schema = _strip_schema_titles(tool.output_json_schema)
        if tool.name != "submit_reply":
            tool.is_enabled = non_submit_enabled
        if tool.name in read_names:
            tool.is_enabled = (
                (evidence_read_enabled if converge_evidence else _proposal_evidence_read_enabled)
                if tool.name == "read_evidence"
                else (
                    (
                        _knowledge_backed_preparation_enabled
                        if preparation_requires_knowledge
                        else preparation_tool_enabled
                    )
                    if tool.name == "prepare_task_inputs"
                    else read_tool_enabled
                )
            )
            _govern_read_tool(
                tool, database,
                provider=provider,
                accepts_budget=tool.name in budget_names,
                converge_evidence=converge_evidence,
                finalize_after_evidence=finalize_after_evidence,
            )
    return tools


def _knowledge_backed_preparation_enabled(context, agent) -> bool:
    return (
        preparation_tool_enabled(context, agent)
        and bool(governance(context.context)["knowledge_evidence"])
    )


def _strip_schema_titles(value: Any, *, property_map: bool = False) -> Any:
    if isinstance(value, list):
        return [_strip_schema_titles(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key == "title" and not property_map:
            continue
        is_property_map = key in {"properties", "patternProperties", "$defs", "definitions"}
        result[key] = _strip_schema_titles(item, property_map=is_property_map)
    return result


def _govern_read_tool(
    tool: FunctionTool,
    database: Path,
    *,
    provider: str,
    accepts_budget: bool,
    converge_evidence: bool,
    finalize_after_evidence: bool,
) -> None:
    invoke = tool.on_invoke_tool
    name = tool.name

    async def governed(context, arguments_json: str):
        control = governance(context.context)
        if control["phase"] != "DISCOVERY" and not (
            name == "read_evidence"
            and control["phase"] == "PROPOSAL"
            and (converge_evidence or _pending_exact_evidence(control))
        ) and not (
            name == "prepare_task_inputs"
            and control["phase"] == "PROPOSAL"
        ):
            return _control_result(
                "BUDGET_EXHAUSTED",
                "control.read_budget_exhausted",
                next_phase=control["phase"],
            )
        try:
            arguments = json.loads(
                arguments_json,
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
            )
            arguments = _normalize_observation_arguments(
                arguments, provider=provider, tool_name=name,
                accepts_budget=accepts_budget,
                input_schema=tool.params_json_schema,
            )
            arguments_json = json.dumps(
                arguments, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            )
            identity_arguments = (
                {key: value for key, value in arguments.items() if key != "progress_intent"}
                if isinstance(arguments, dict) else arguments
            )
            canonical = json.dumps(
                identity_arguments,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError, json.JSONDecodeError, UnicodeEncodeError) as error:
            diagnostics = {"reason": "OBSERVATION_ARGUMENTS_JSON_INVALID"}
            if _repair_current_provider_batch(database, context, control):
                return _control_result(
                    "RETRY_REQUIRED",
                    "control.retry_required",
                    **diagnostics,
                    instruction=f"Call {name} once more with valid JSON arguments.",
                )
            raise AgentRunError(
                "AGENT_MODEL_OUTPUT_INVALID",
                diagnostics=diagnostics,
            ) from error
        digest = hashlib.sha256(f"{name}\n{canonical}".encode("utf-8")).hexdigest()
        if digest in control["successful_read_hashes"]:
            advance_from_discovery(control, "DUPLICATE")
            return _control_result(
                "DUPLICATE",
                "control.duplicate_stopped",
                next_phase=control["phase"],
            )
        if control["read_tool_count"] >= control["read_tool_limit"]:
            advance_from_discovery(control, "BUDGET")
            return _control_result(
                "BUDGET_EXHAUSTED",
                "control.read_budget_exhausted",
                next_phase=control["phase"],
            )
        control["read_tool_count"] += 1
        try:
            if builtin_progress_definition(name) is not None:
                validate_builtin_progress_intent(
                    name, arguments.get("progress_intent") if isinstance(arguments, dict) else None,
                )
            result = await invoke(context, arguments_json)
        except AgentRunError as error:
            diagnostics = error.diagnostics or {}
            progress_invalid = diagnostics.get("reason") in {"PROGRESS_INTENT_INVALID", "PROGRESS_INTENT_MISMATCH"}
            observation_arguments_invalid = diagnostics.get("reason") in {
                "OBSERVATION_ARGUMENTS_JSON_INVALID",
                "OBSERVATION_ARGUMENTS_INVALID",
            }
            catalog_arguments_invalid = (
                name in {"list_operators", "get_operator"}
                and diagnostics.get("reason") in {
                    "OPERATOR_CURSOR_INVALID",
                    "OPERATOR_PAGE_LIMIT_INVALID",
                    "OPERATOR_QUERY_INVALID",
                    "OPERATOR_QUERY_REQUIRED",
                    "OPERATOR_ID_INVALID",
                    "OPERATOR_VERSION_INVALID",
                    "OPERATOR_UNKNOWN",
                }
            )
            if progress_invalid or observation_arguments_invalid or catalog_arguments_invalid:
                control["read_tool_count"] -= 1
                if _repair_current_provider_batch(database, context, control):
                    return _control_result(
                        "RETRY_REQUIRED",
                        "control.retry_required",
                        **diagnostics,
                        instruction=_schema_repair_instruction(name, diagnostics, tool.params_json_schema),
                    )
            raise
        data = result.get("data") if isinstance(result, dict) else None
        record_digest = True
        if name == "discover_datasets":
            # Different wording often resolves to the same immutable Data set.
            # Treat that as discovery convergence so the Agent inspects or
            # proposes from the identities it already has instead of spending
            # more Provider stages on equivalent searches.
            result_digest = _dataset_search_result_digest(data)
            if result_digest is not None:
                if result_digest in control["successful_read_hashes"]:
                    advance_from_discovery(control, "DUPLICATE")
                    record_digest = False
                digest = result_digest
        if name == "read_evidence":
            evidence_completion = (
                converge_evidence and control["phase"] == "PROPOSAL"
            )
            _record_read_evidence(control, result)
            if evidence_completion and finalize_after_evidence:
                if control["knowledge_evidence"]:
                    control["phase"] = "FINALIZATION"
                    control["finalizing"] = True
                    control["finalization_pending"] = False
                    control["finalization_reason"] = "EVIDENCE"
                else:
                    control["phase"] = "DISCOVERY"
                    control["finalizing"] = False
                    control["finalization_reason"] = None
        elif name in {"retrieve_evidence", "lookup_knowledge"}:
            _record_retrieved_evidence(control, result)
            if (
                converge_evidence
                and len(control["data_evidence"]) <= 1
                and _has_evidence_references(data)
            ):
                # A read-only answer needs the full immutable evidence next.
                # For a single source, reserve the remaining requests for
                # reading and replying. Several inspected versions may each
                # need a separate bound retrieval before the combined read.
                control["phase"] = "PROPOSAL"
                control["finalizing"] = False
                control["finalization_pending"] = False
                control["finalization_reason"] = None
        elif name in {"inspect_data_version", "inspect_data_table"} and (
            isinstance(data, dict) and data.get("status", "OK") == "OK"
        ):
            reference = _data_reference(data)
            control["data_evidence"][reference.version_id] = (
                reference.as_storage_dict()
            )
        if record_digest and not (
            isinstance(data, dict) and data.get("status") == "STALE"
        ):
            control["successful_read_hashes"].append(digest)
        if control["read_tool_count"] >= control["read_tool_limit"]:
            advance_from_discovery(control, "BUDGET")
        return result

    tool.on_invoke_tool = governed


def _repair_current_provider_batch(
    database: Path, context, control: dict[str, Any],
) -> bool:
    """Share one bounded retry across invalid calls emitted together.

    DeepSeek can emit several parallel read calls.  The first invalid call marks
    the Provider stage for attempt 2; additional invalid calls in that original
    batch share that repair instead of consuming or defeating it.  At
    the start of attempt 2, usage capture clears provider_retry_stage, so another
    invalid call still fails closed rather than creating an unbounded retry.
    """
    return (
        stage_protocol_retry(database, context)
        or control["provider_retry_stage"] is not None
    )


def _schema_repair_instruction(name: str, diagnostics: dict[str, Any], schema: dict | None = None) -> str:
    reason = diagnostics.get("reason")
    if reason in {"PROGRESS_INTENT_INVALID", "PROGRESS_INTENT_MISMATCH"}:
        progress = (schema or {}).get("properties", {}).get("progress_intent", {}).get("properties", {})
        capability = progress.get("capability", {}).get("const")
        template = progress.get("template_key", {}).get("const")
        return (
            f"Call {name} once more with the same operation arguments and a progress_intent object "
            f'with capability="{capability}", template_key="{template}", and a short objective '
            "without a prefix or trailing punctuation."
        )
    if name == "get_operator" and reason in {
        "OPERATOR_ID_INVALID",
        "OPERATOR_VERSION_INVALID",
        "OPERATOR_UNKNOWN",
    }:
        return (
            "Call get_operator exactly once more. Copy operator_id and version "
            "exactly as returned by one list_operators item in this Turn; "
            "do not translate, prefix, suffix, or guess either value."
        )
    if name == "list_operators" and reason in {
        "OPERATOR_CURSOR_INVALID",
        "OPERATOR_PAGE_LIMIT_INVALID",
        "OPERATOR_QUERY_INVALID",
        "OPERATOR_QUERY_REQUIRED",
    }:
        return (
            "Call list_operators exactly once more with at least one short capability, "
            "query, or domain selector. Omit after for the first page; otherwise copy "
            "the exact next_cursor returned by the preceding page. Use an integer "
            "limit from 1 through 50."
        )
    if name == "read_evidence":
        return (
            "Call read_evidence exactly once more with one JSON object containing "
            "refs and optional budget only. refs must be a non-empty array of exact "
            "version_id, unit_id and checksum objects copied out of completed retrieval "
            "results; do not include query, scope, binding or other fields."
        )
    if name == "inspect_data_table":
        return (
            "Call inspect_data_table exactly once more with one JSON object "
            "containing version_id only. Copy the exact versionId reported by "
            "the completed inspect_data_version call; do not include object_id, "
            "dataset_id or other fields."
        )
    issues = diagnostics.get("issues")
    if name == "discover_datasets" and isinstance(issues, list):
        if any(
            isinstance(issue, dict) and issue.get("path") in {"/spatial_extent", "/time_window"}
            for issue in issues
        ):
            return (
                "Call discover_datasets once more. Omit spatial_extent and time_window "
                "unless the user needs those filters. A time_window needs two ISO dates "
                "and a spatial_extent needs both crs and bbox."
            )
        if any(
            isinstance(issue, dict) and issue.get("path") == "/after"
            for issue in issues
        ):
            return (
                "Call discover_datasets once more. For the first page omit after "
                "entirely or set it to JSON null. For a later page, after must be "
                "a string containing digits only; never use placeholder text such "
                "as undefined, unset, none or 未设置."
            )
    if name in {"retrieve_evidence", "lookup_knowledge"} and isinstance(issues, list):
        if any(
            isinstance(issue, dict) and issue.get("path") == "/binding"
            for issue in issues
        ):
            return (
                f"Call {name} once more. For a general question set binding to null "
                "and scope to general. Otherwise binding must be one exact object "
                "with project_id, dataset_id, version_id and semantic_id; never pass "
                "binding as text or an array."
            )
    return f"Call {name} once more with arguments matching its schema."


def _dataset_search_result_digest(data: object) -> str | None:
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return None
    identities = []
    for item in data["items"]:
        if not isinstance(item, dict):
            return None
        dataset_id = item.get("datasetId")
        version_id = item.get("currentVersion")
        if not isinstance(dataset_id, str) or not isinstance(version_id, str):
            return None
        identities.append((dataset_id, version_id))
    canonical = json.dumps(
        sorted(identities), ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(
        f"discover_datasets:result\n{canonical}".encode("utf-8")
    ).hexdigest()
