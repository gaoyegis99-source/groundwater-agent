"""Represent current-Turn, fully read observations as trusted evidence."""

from __future__ import annotations

import ast
from dataclasses import dataclass
import json
from typing import Any

from ..contracts import DataVersionRefV2
from ..errors import AgentRunError


KnowledgeKey = tuple[str, str, str]
OperatorKey = tuple[str, str, str]


@dataclass(frozen=True)
class TurnEvidence:
    knowledge: dict[KnowledgeKey, str]
    data_versions: dict[str, DataVersionRefV2]
    operators: frozenset[OperatorKey]
    prepared_arguments: dict[
        str, list[tuple[dict[str, object], str]]
    ]
    prepared_targets: frozenset[str]


def knowledge_identity(value: object) -> KnowledgeKey | None:
    if not isinstance(value, dict):
        return None
    identity = (
        value.get("versionId"), value.get("unitId"), value.get("checksum"),
    )
    if not all(isinstance(part, str) and part for part in identity):
        return None
    return identity


def exact_retrieval_identities(value: object) -> tuple[KnowledgeKey, ...]:
    """Return fully observed identities whose Data binding was checked exactly."""
    if not isinstance(value, dict):
        return ()
    data = value.get("data")
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return ()
    identities = []
    for item in items:
        identity = knowledge_identity(item)
        if (
            identity is not None
            and item.get("status") == "OK"
            and item.get("bindingMatch") == "EXACT"
            and item.get("content")
        ):
            identities.append(identity)
    return tuple(dict.fromkeys(identities))


def trusted_read_identity(
    value: object, exact_retrievals: set[KnowledgeKey] | frozenset[KnowledgeKey],
) -> KnowledgeKey | None:
    """Trust a historical bound read only after an exact same-Turn retrieval."""
    identity = knowledge_identity(value)
    if (
        identity is None
        or value.get("status") != "OK"
        or not value.get("content")
    ):
        return None
    binding_match = value.get("bindingMatch")
    if binding_match in {"GENERAL", "EXACT"} or (
        binding_match == "NOT_CHECKED" and identity in exact_retrievals
    ):
        return identity
    return None


def read_supersedes_identity(
    current: KnowledgeKey, observed: KnowledgeKey, status: object,
) -> bool:
    # STALE reports a checksum mismatch for the requested identity only.
    if status == "STALE":
        return current == observed
    return current[:2] == observed[:2]


def _object(value: object) -> dict[str, object]:
    parsed = ast.literal_eval(value) if isinstance(value, str) else value
    if not isinstance(parsed, dict):
        raise AgentRunError("OBSERVATION_RESPONSE_INVALID")
    return parsed


def _payload(value: object) -> dict[str, object]:
    parsed = _object(value)
    if not isinstance(parsed.get("data"), dict):
        raise AgentRunError("OBSERVATION_RESPONSE_INVALID")
    return parsed["data"]


def _data_reference(data: dict[str, object]) -> DataVersionRefV2:
    objects = data.get("objects")
    if not isinstance(objects, list):
        raise AgentRunError("OBSERVATION_RESPONSE_INVALID")
    raw = [item for item in objects if isinstance(item, dict) and item.get("role") == "raw"]
    if len(raw) != 1:
        raise AgentRunError("OBSERVATION_RESPONSE_INVALID")
    item = raw[0]
    try:
        return DataVersionRefV2.from_dict({
            "kind": "data_version",
            "service": "data-primary",
            "datasetId": data["datasetId"],
            "versionId": data["versionId"],
            "semanticId": data["semanticId"],
            "objectId": item["objectId"],
            "mediaType": item["mediaType"],
            "format": item["format"],
            "sizeBytes": item["size"],
            "checksum": item["checksum"],
        })
    except (KeyError, TypeError, ValueError) as error:
        raise AgentRunError("OBSERVATION_RESPONSE_INVALID") from error


def collect_turn_evidence(input_items: list[dict[str, Any]]) -> TurnEvidence:
    calls = {
        item["call_id"]: item.get("name")
        for item in input_items
        if item.get("type") == "function_call"
    }
    knowledge: dict[KnowledgeKey, str] = {}
    data_versions: dict[str, DataVersionRefV2] = {}
    operators: dict[tuple[str, str], str] = {}
    latest_checks: dict[
        str, dict[str, tuple[int, tuple[dict[str, object], str] | None]]
    ] = {}
    prepared_targets: set[str] = set()
    exact_retrievals: set[KnowledgeKey] = set()
    for item in input_items:
        if item.get("type") != "function_call_output":
            continue
        tool = calls.get(item.get("call_id"))
        if tool not in {
            "read_evidence", "inspect_data_version", "inspect_data_table", "get_operator",
            "prepare_task_inputs", "retrieve_evidence", "lookup_knowledge",
        }:
            continue
        raw_output = _object(item.get("output"))
        if raw_output.get("status") in {
            "BUDGET_EXHAUSTED", "DUPLICATE", "RETRY_EXHAUSTED", "RETRY_REQUIRED",
        }:
            # Repair/budget envelopes are control-plane results, not evidence.
            # They remain in the Provider transcript so a model can correct the
            # call, but must never be interpreted as a successful read later.
            continue
        if tool == "prepare_task_inputs":
            payload = raw_output
            target_id = payload.get("target_id")
            selection_policy = payload.get("selection_policy", "PINNED")
            if not isinstance(target_id, str) or not target_id:
                raise AgentRunError("OBSERVATION_RESPONSE_INVALID")
            if selection_policy not in {"PINNED", "CURRENT_REQUIRED"}:
                raise AgentRunError("OBSERVATION_RESPONSE_INVALID")
            status = payload.get("status")
            if status not in {"READY", "BLOCKED"}:
                raise AgentRunError("OBSERVATION_RESPONSE_INVALID")
            prepared_targets.add(target_id)
            references = payload.get("data_references")
            parameters = payload.get("parameters")
            if not isinstance(references, dict) or not isinstance(parameters, dict):
                raise AgentRunError("OBSERVATION_RESPONSE_INVALID")
            try:
                arguments = dict(parameters)
                for parameter, raw in references.items():
                    if not isinstance(parameter, str):
                        raise ValueError("PREPARATION_PARAMETER_INVALID")
                    reference = DataVersionRefV2.from_storage_dict(raw)
                    arguments[parameter] = reference.version_id
                    if status == "READY":
                        data_versions[reference.version_id] = reference
                identity = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
                revision = payload.get("revision", 0)
                if type(revision) is not int or revision < 0:
                    raise ValueError("PREPARATION_REVISION_INVALID")
                checks = latest_checks.setdefault(target_id, {})
                if identity not in checks or revision >= checks[identity][0]:
                    checks[identity] = (
                        revision, (arguments, selection_policy) if status == "READY" else None,
                    )
            except (TypeError, ValueError) as error:
                raise AgentRunError("OBSERVATION_RESPONSE_INVALID") from error
            continue
        if tool == "get_operator":
            payload = raw_output
            try:
                operators[(payload["operator_id"], payload["operator_version"])] = payload[
                    "implementation_revision"
                ]
            except KeyError as error:
                raise AgentRunError("OBSERVATION_RESPONSE_INVALID") from error
            continue
        if tool in {"retrieve_evidence", "lookup_knowledge"}:
            for identity in exact_retrieval_identities(raw_output):
                exact_retrievals = {
                    current for current in exact_retrievals
                    if current[:2] != identity[:2]
                }
                exact_retrievals.add(identity)
            continue
        data = _payload(raw_output)
        if data.get("status") in {"DUPLICATE", "BUDGET_EXHAUSTED", "NOT_QUERYABLE"}:
            continue
        if tool == "read_evidence":
            evidence_items = data.get("items")
            if not isinstance(evidence_items, list):
                raise AgentRunError("OBSERVATION_RESPONSE_INVALID")
            for evidence in evidence_items:
                if not isinstance(evidence, dict):
                    raise AgentRunError("OBSERVATION_RESPONSE_INVALID")
                observed = knowledge_identity(evidence)
                identity = trusted_read_identity(evidence, exact_retrievals)
                if observed is not None:
                    status = evidence.get("status")
                    exact_retrievals = {
                        current for current in exact_retrievals
                        if not read_supersedes_identity(current, observed, status)
                    }
                    if identity is None:
                        knowledge = {
                            key: title for key, title in knowledge.items()
                            if not read_supersedes_identity(key, observed, status)
                        }
                if identity is None:
                    continue
                locator = evidence.get("locator")
                title = (
                    locator.get("title") if isinstance(locator, dict) else None
                ) or evidence.get("knowledgeId") or "知识依据"
                knowledge[identity] = str(title)
            continue
        if tool in {"inspect_data_version", "inspect_data_table"}:
            if data.get("status", "OK") != "OK":
                continue
            reference = _data_reference(data)
            data_versions[reference.version_id] = reference
            continue
    return TurnEvidence(
        knowledge=knowledge,
        data_versions=data_versions,
        operators=frozenset((*identity, revision) for identity, revision in operators.items()),
        prepared_arguments={
            target: [candidate for _, candidate in checks.values() if candidate is not None]
            for target, checks in latest_checks.items()
        },
        prepared_targets=frozenset(prepared_targets),
    )
