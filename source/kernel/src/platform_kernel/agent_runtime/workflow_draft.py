"""Validate authored drafts against the frozen catalog and this Turn's evidence."""
from __future__ import annotations

from copy import deepcopy
import json

from platform_workflow_compiler import compile_draft, WorkflowValidationError

from ..errors import AgentRunError
from .evidence import collect_turn_evidence
from .models import WorkflowDataReference, WorkflowDraftInput


def _card_summary(value: str, limit: int = 120) -> str:
    """Project verbose authored prose into a stable one-line card summary."""
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    boundaries = [
        compact.rfind(mark, 0, limit)
        for mark in ("。", "！", "？", ".", "!", "?")
    ]
    boundary = max(boundaries)
    if boundary >= 39:
        return compact[:boundary + 1]
    return compact[:limit - 1].rstrip() + "…"


def _without_unset_optional_parameters(
    draft: WorkflowDraftInput,
    compiled: dict,
    settings,
) -> dict:
    """Canonicalize dangling optional parameters to operator omission.

    A Provider can bind an optional operator parameter to a workflow parameter
    but supply no value.  That turns an optional selector into a misleading
    required draft gap.  Remove only bindings whose installed operator schema
    explicitly leaves the target parameter optional, then accept the rewrite
    only if the authoritative compiler validates the whole graph again.
    """
    missing = {
        item["name"]
        for item in compiled["missing_inputs"]
        if item["kind"] == "PARAMETER"
    }
    if not missing:
        return compiled
    graph = deepcopy(draft.graph.model_dump(by_alias=True))
    removable = set()
    for name in missing:
        targets = []
        optional = True
        for node in graph["nodes"]:
            operator = settings.operator_catalog.get((
                node["operatorId"], node["operatorVersion"],
            ))
            for parameter, binding in node["parameters"].items():
                if binding.get("workflowParameter") != name:
                    continue
                targets.append((node, parameter))
                if (
                    operator is None
                    or parameter in operator["parametersSchema"].get(
                        "required", []
                    )
                ):
                    optional = False
        if targets and optional:
            removable.add(name)
            for node, parameter in targets:
                del node["parameters"][parameter]
    if not removable:
        return compiled
    graph["parameters"] = [
        name for name in graph["parameters"] if name not in removable
    ]
    try:
        return compile_draft(
            graph,
            settings.operator_catalog,
            workflow_id=compiled["workflow"]["workflowId"],
            version=compiled["workflow"]["version"],
            operator_catalog_digest=settings.operator_catalog_digest,
            parameter_values=draft.parameter_values,
            parameter_sources=[
                source.model_dump() for source in draft.parameter_sources
            ],
            data_bindings=[
                binding.model_dump(exclude_none=True)
                for binding in draft.data_bindings
            ],
        )
    except WorkflowValidationError:
        return compiled


def prepare_workflow_draft(draft: WorkflowDraftInput, result, settings, draft_id: str) -> dict:
    items = result.to_input_list()
    try:
        evidence = collect_turn_evidence(items)
        for index, ref in enumerate(draft.knowledge_refs):
            if (
                ref.version_id,
                ref.unit_id,
                ref.checksum,
            ) not in evidence.knowledge:
                raise WorkflowValidationError("WORKFLOW_DRAFT_EVIDENCE_INVALID", f"/knowledge_refs/{index}")
        normalized_bindings = []
        for index, binding in enumerate(draft.data_bindings):
            reference = evidence.data_versions.get(binding.data_ref.version_id)
            if reference is None:
                raise WorkflowValidationError("WORKFLOW_DRAFT_EVIDENCE_INVALID", f"/data_bindings/{index}/data_ref")
            # The observed version ID is the identity anchor.  Rehydrate all
            # denormalized reference fields from current-Turn evidence instead
            # of trusting a Provider to reproduce checksums/media metadata.
            # This never guesses or substitutes a version the Agent did not
            # explicitly select.
            normalized_bindings.append(binding.model_copy(update={
                "data_ref": WorkflowDataReference.model_validate(
                    reference.as_storage_dict()
                ),
            }))
        draft = draft.model_copy(update={"data_bindings": normalized_bindings})
        for index, source in enumerate(draft.parameter_sources):
            if (source.source == "DATA" and not draft.data_bindings
                    or source.source == "KNOWLEDGE" and not draft.knowledge_refs):
                raise WorkflowValidationError("WORKFLOW_DRAFT_PARAMETER_SOURCE_INVALID", f"/parameter_sources/{index}")
        json.dumps(
            draft.model_dump(by_alias=True), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
        compiled = compile_draft(
            draft.graph.model_dump(by_alias=True), settings.operator_catalog,
            workflow_id=f"draft.{draft_id}", version="0.1.0",
            operator_catalog_digest=settings.operator_catalog_digest,
            parameter_values=draft.parameter_values,
            parameter_sources=[source.model_dump() for source in draft.parameter_sources],
            data_bindings=[
                binding.model_dump(exclude_none=True) for binding in draft.data_bindings
            ],
        )
        compiled = _without_unset_optional_parameters(
            draft, compiled, settings,
        )
        for index, operator in enumerate(compiled["operators"]):
            if (operator["operator_id"], operator["operator_version"], operator["implementation_revision"]) not in evidence.operators:
                raise WorkflowValidationError("WORKFLOW_DRAFT_OPERATOR_READ_REQUIRED", f"/graph/nodes/{index}")
    except WorkflowValidationError as error:
        raise AgentRunError("AGENT_WORKFLOW_DRAFT_INVALID", diagnostics={
            "kind": "WORKFLOW_VALIDATION", "code": error.code, "path": error.path,
        }) from error
    except (ValueError, TypeError, UnicodeEncodeError) as error:
        raise AgentRunError("AGENT_WORKFLOW_DRAFT_INVALID", diagnostics={
            "kind": "WORKFLOW_VALIDATION", "code": "WORKFLOW_DRAFT_VALUE_INVALID", "path": "",
        }) from error
    return {
        **compiled, "title": draft.title, "summary": _card_summary(draft.summary),
        "knowledge_refs": [ref.model_dump() for ref in draft.knowledge_refs],
        "unresolved_items": draft.unresolved_items, "limitations": draft.limitations,
    }
