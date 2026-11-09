from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from ..contracts import DataVersionRefV2
from .mode import AgentModeCapabilities


@dataclass(frozen=True)
class RunLimits:
    request_limit: int
    discovery_request_limit: int
    proposal_reserve: int
    read_tool_limit: int
    token_limit: int | None
    finalization_reserve: int


_KEYS = {
    "mode", "phase", "request_limit", "discovery_request_limit",
    "proposal_reserve", "read_tool_limit", "token_limit",
    "finalization_reserve", "request_count", "provider_attempt_count",
    "read_tool_count", "total_tokens",
    "finalizing", "finalization_pending", "finalization_reason",
    "successful_read_hashes",
    "knowledge_evidence", "knowledge_exact_candidates", "data_evidence",
    "catalog_queries",
    "schema_retry_stages", "provider_retry_stage",
}
_WITHOUT_DATA_EVIDENCE_KEYS = _KEYS - {"data_evidence"}
_WITHOUT_KNOWLEDGE_CANDIDATES_KEYS = _KEYS - {"knowledge_exact_candidates"}
_WITHOUT_BOTH_EVIDENCE_KEYS = _KEYS - {
    "data_evidence", "knowledge_exact_candidates",
}


def run_limits(capabilities: AgentModeCapabilities) -> RunLimits:
    if capabilities.clarification_only:
        return RunLimits(2, 0, 2, 0, None, 0)
    if capabilities.mode == "WORKFLOW_DRAFT":
        return RunLimits(14, 11, 3, 24, 300_000, 40_000)
    if capabilities.allow_model_tools:
        # Every exact model input may need one immutable-version inspection and
        # one table-schema inspection before the action can be frozen.  Keep
        # that I/O budget separate from the five logical discovery stages so a
        # wide, trusted model contract cannot be made impossible by a fixed
        # per-tool cap.
        widest_model = max(
            (len(model.data_input_parameters) for model in capabilities.models),
            default=0,
        )
        return RunLimits(
            11, 5, 6, max(12, widest_model * 2), 120_000, 20_000
        )
    return RunLimits(8, 7, 1, 12, 120_000, 20_000)


def new_governance(capabilities: AgentModeCapabilities) -> dict[str, Any]:
    limits = run_limits(capabilities)
    return {
        "mode": capabilities.mode,
        "phase": "FINALIZATION" if capabilities.clarification_only else "DISCOVERY",
        "request_limit": limits.request_limit,
        "discovery_request_limit": limits.discovery_request_limit,
        "proposal_reserve": limits.proposal_reserve,
        "read_tool_limit": limits.read_tool_limit,
        "token_limit": limits.token_limit,
        "finalization_reserve": limits.finalization_reserve,
        "request_count": 0,
        "provider_attempt_count": 0,
        "read_tool_count": 0,
        "total_tokens": 0,
        "finalizing": capabilities.clarification_only,
        "finalization_pending": False,
        "finalization_reason": "BUDGET" if capabilities.clarification_only else None,
        "successful_read_hashes": [],
        "knowledge_evidence": [],
        "knowledge_exact_candidates": [],
        "data_evidence": {},
        "catalog_queries": {},
        "schema_retry_stages": [],
        "provider_retry_stage": None,
    }


def validate_governance(
    value: object, capabilities: AgentModeCapabilities,
) -> dict[str, Any]:
    expected = new_governance(capabilities)
    if not isinstance(value, dict):
        raise ValueError("AGENT_RUN_STATE_INVALID")
    if set(value) == _WITHOUT_DATA_EVIDENCE_KEYS:
        value = {**value, "data_evidence": {}}
    elif set(value) == _WITHOUT_KNOWLEDGE_CANDIDATES_KEYS:
        value = {**value, "knowledge_exact_candidates": []}
    elif set(value) == _WITHOUT_BOTH_EVIDENCE_KEYS:
        value = {
            **value,
            "knowledge_exact_candidates": [],
            "data_evidence": {},
        }
    elif set(value) != _KEYS:
        raise ValueError("AGENT_RUN_STATE_INVALID")
    for key in (
        "mode", "request_limit", "discovery_request_limit", "proposal_reserve",
        "read_tool_limit", "token_limit", "finalization_reserve",
    ):
        if value[key] != expected[key]:
            raise ValueError("AGENT_RUN_STATE_INVALID")
    if value["phase"] not in {"DISCOVERY", "PROPOSAL", "FINALIZATION"}:
        raise ValueError("AGENT_RUN_STATE_INVALID")
    for key in (
        "request_count", "provider_attempt_count", "read_tool_count", "total_tokens",
    ):
        if type(value[key]) is not int or value[key] < 0:
            raise ValueError("AGENT_RUN_STATE_INVALID")
    retry_stages = value["schema_retry_stages"]
    if (
        not isinstance(retry_stages, list)
        or len(retry_stages) != len(set(retry_stages))
        or len(retry_stages) > value["request_limit"]
        or any(not isinstance(stage, str) or not stage for stage in retry_stages)
    ):
        raise ValueError("AGENT_RUN_STATE_INVALID")
    retry_stage = value["provider_retry_stage"]
    if retry_stage is not None and (
        not isinstance(retry_stage, list)
        or len(retry_stage) != 2
        or not isinstance(retry_stage[0], str)
        or retry_stage[1] != 2
    ):
        raise ValueError("AGENT_RUN_STATE_INVALID")
    if value["request_count"] >= value["request_limit"]:
        raise ValueError("AGENT_RUN_STATE_INVALID")
    if value["read_tool_count"] > value["read_tool_limit"]:
        raise ValueError("AGENT_RUN_STATE_INVALID")
    if type(value["finalizing"]) is not bool or type(value["finalization_pending"]) is not bool or value["finalization_reason"] not in {
        None, "BUDGET", "DUPLICATE", "EVIDENCE", "EXECUTION_RESULT",
    }:
        raise ValueError("AGENT_RUN_STATE_INVALID")
    hashes = value["successful_read_hashes"]
    if not isinstance(hashes, list) or len(hashes) != len(set(hashes)) or any(
        not isinstance(item, str) or re.fullmatch(r"[0-9a-f]{64}", item) is None
        for item in hashes
    ):
        raise ValueError("AGENT_RUN_STATE_INVALID")
    for key in ("knowledge_evidence", "knowledge_exact_candidates"):
        evidence = value[key]
        if (
            not isinstance(evidence, list)
            or len(evidence) > 256
            or any(
                not isinstance(item, list)
                or len(item) != 3
                or not all(isinstance(part, str) and part for part in item)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", item[2]) is None
                for item in evidence
            )
            or len(evidence) != len({tuple(item) for item in evidence})
        ):
            raise ValueError("AGENT_RUN_STATE_INVALID")
    data_evidence = value["data_evidence"]
    if not isinstance(data_evidence, dict) or len(data_evidence) > 256:
        raise ValueError("AGENT_RUN_STATE_INVALID")
    try:
        references = {
            version_id: DataVersionRefV2.from_storage_dict(reference)
            for version_id, reference in data_evidence.items()
            if isinstance(version_id, str) and isinstance(reference, dict)
        }
    except (TypeError, ValueError):
        raise ValueError("AGENT_RUN_STATE_INVALID") from None
    if (
        len(references) != len(data_evidence)
        or any(reference.version_id != version_id for version_id, reference in references.items())
    ):
        raise ValueError("AGENT_RUN_STATE_INVALID")
    catalog_queries = value["catalog_queries"]
    if not isinstance(catalog_queries, dict) or any(
        not isinstance(key, str)
        or not isinstance(cursors, list)
        or not 1 <= len(cursors) <= 3
        or len(cursors) != len({json_cursor for json_cursor in cursors})
        or any(cursor is not None and not isinstance(cursor, str) for cursor in cursors)
        for key, cursors in catalog_queries.items()
    ):
        raise ValueError("AGENT_RUN_STATE_INVALID")
    if (
        len(hashes) > value["read_tool_count"]
        or value["finalizing"] != (value["phase"] == "FINALIZATION")
        or value["finalization_pending"]
        and value["finalization_reason"] is None
        or value["phase"] == "FINALIZATION"
        and value["finalization_reason"] is None
    ):
        raise ValueError("AGENT_RUN_STATE_INVALID")
    return value


def resume_governance(value: dict[str, Any]) -> dict[str, Any]:
    # The approved/rejected action result is already part of the resumed state.
    # Keep proposal tools enabled while the SDK reconciles the persisted
    # interruption.  The tool-end hook advances to finalization before the next
    # Provider request, so the model can only submit an authoritative status.
    value["phase"] = "PROPOSAL"
    value["finalizing"] = False
    value["finalization_pending"] = True
    value["finalization_reason"] = "EXECUTION_RESULT"
    return value


def governance(context: dict[str, Any]) -> dict[str, Any]:
    value = context["governance"]
    if not isinstance(value, dict):
        raise ValueError("AGENT_RUN_STATE_INVALID")
    return value


def non_submit_enabled(context, _agent) -> bool:
    return governance(context.context)["phase"] != "FINALIZATION"


def read_tool_enabled(context, _agent) -> bool:
    return governance(context.context)["phase"] == "DISCOVERY"


def evidence_read_enabled(context, _agent) -> bool:
    control = governance(context.context)
    return control["phase"] == "DISCOVERY" or (
        control["phase"] == "PROPOSAL" and not control["knowledge_evidence"]
    )


def preparation_tool_enabled(context, _agent) -> bool:
    return governance(context.context)["phase"] in {"DISCOVERY", "PROPOSAL"}


def advance_from_discovery(value: dict[str, Any], reason: str) -> None:
    if value["phase"] != "DISCOVERY":
        return
    if value["proposal_reserve"] > 1:
        value["phase"] = "PROPOSAL"
        value["finalizing"] = False
        value["finalization_pending"] = False
        value["finalization_reason"] = None
        return
    value["phase"] = "FINALIZATION"
    value["finalizing"] = True
    value["finalization_pending"] = False
    value["finalization_reason"] = reason


def finalization_error(context: dict[str, Any]) -> str:
    return (
        "AGENT_OBSERVATION_LOOP"
        if governance(context)["finalization_reason"] == "DUPLICATE"
        else "AGENT_BUDGET_EXCEEDED"
    )
