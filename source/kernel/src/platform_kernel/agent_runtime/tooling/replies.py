"""Reply normalization, validation, and evidence bookkeeping."""

from __future__ import annotations

import json
import re


from ..models import (
    AgentPlanStepV2, InteractionOptionV1,
    SubmittedReplyV6, WorkflowDraftInput,
    Utf8String,
    normalize_reply_json,
)
from ..evidence import (
    exact_retrieval_identities, knowledge_identity, trusted_read_identity,
)
from ..reply_schema import reply_schema


_VISIBLE_TECHNICAL_IDENTIFIER = re.compile(
    r"sha256:[0-9a-f]{64}|"
    r"\b(?:submit_reply|run_[a-z0-9_]+)\b|"
    r"\b(?:deepseek|minimax)\b|"
    r"(?:knowledge|workflow|resource|model|session|draft|agent[_-]?run|"
    r"tool[_-]?call)[ _-]?id|workflow[ _-]?run|chunk[ _-]?index|checksum|"
    r"/api/v\d+/knowledge|https?://[^\s)]+|"
    r"\b(?:groundwater|knowledge|workflow)\.[a-z0-9._-]+@\d+(?:\.\d+)+\b|"
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)

_DIRECT_RECOMMENDATION = re.compile(r"建议|优先|适合|recommend", re.IGNORECASE)
_COMPARISON_META_LANGUAGE = re.compile(
    r"当前项目|资料目录|能力边界|只能给出"
)


_WORKFLOW_DRAFT_FIELDS = frozenset(
    field.alias or name
    for name, field in WorkflowDraftInput.model_fields.items()
)


def _drop_unknown_complete_draft_fields(value: object) -> object:
    """Remove Provider-only draft annotations using the authoritative model.

    DeepSeek's Responses endpoint does not enforce the large workflow-draft
    schema strictly and sometimes adds an explanatory sibling such as a note.
    Such fields have no business meaning.  Only a structurally complete draft
    is normalized, so a malformed or partial object is still returned verbatim
    to normal validation and its bounded repair path.
    """
    if not isinstance(value, dict) or not _WORKFLOW_DRAFT_FIELDS <= set(value):
        return value
    return {
        key: child for key, child in value.items()
        if key in _WORKFLOW_DRAFT_FIELDS
    }


class _PlanStepInput(AgentPlanStepV2):
    # Provider-supplied display metadata never changes the public AgentPlanV2.
    title: Utf8String | None = None


def _normalize_provider_reply_json(
    value: str,
    provider: str,
    *,
    drafting: bool = False,
    execution_result: bool = False,
) -> str:
    """Adapt known Provider segment shorthand before contract validation.

    MiniMax and DeepSeek may encode a reply segment as a string
    even when the advertised tool schema uses ``{text, citations}``.  The adapter
    only restores that structural wrapper; it does not add prose, citations,
    options, or execution facts.
    """
    if provider not in {"minimax", "deepseek"}:
        return normalize_reply_json(value)
    if provider in {"minimax", "deepseek"} and execution_result:
        # The Provider has no authority over an already terminal action.  In
        # particular, do not require its acknowledgement arguments to be valid
        # reply JSON before replacing them: Providers can double-encode,
        # truncate, or invent fields in this tiny forced tool call after a large
        # execution context. Persisted Kernel facts supply every user-visible
        # execution field below.
        return json.dumps({
            "conclusion": None,
            "details": [],
            "unresolved_items": [],
            "referenced_workflow_runs": [],
            "include_execution_status": False,
            "interaction_options": [],
            "workflow_draft": None,
        }, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    normalized = normalize_reply_json(value)
    parsed = json.loads(normalized)
    if not isinstance(parsed, dict):
        return normalized
    if provider == "minimax" or provider == "deepseek" and drafting:
        for field in (
            "details", "unresolved_items", "referenced_workflow_runs",
            "interaction_options",
        ):
            parsed.setdefault(field, [])
        parsed.setdefault("include_execution_status", False)
        if provider == "minimax":
            parsed.setdefault("workflow_draft", None)

    def segment(item):
        if not isinstance(item, str):
            return item
        try:
            nested = json.loads(item)
        except json.JSONDecodeError:
            nested = None
        if (
            isinstance(nested, dict)
            and set(nested) <= {"text", "citations"}
            and isinstance(nested.get("text"), str)
            and isinstance(nested.get("citations", []), list)
        ):
            return {
                "text": nested["text"],
                "citations": nested.get("citations", []),
            }
        return {"text": item, "citations": []}

    conclusion = parsed.get("conclusion")
    if isinstance(conclusion, str):
        parsed["conclusion"] = segment(conclusion)
    for field in ("details", "unresolved_items"):
        items = parsed.get(field)
        if isinstance(items, list):
            parsed[field] = [
                segment(item)
                for item in items
            ]
    for item in [
        parsed.get("conclusion"),
        *(parsed.get("details") if isinstance(parsed.get("details"), list) else []),
        *(parsed.get("unresolved_items")
          if isinstance(parsed.get("unresolved_items"), list) else []),
    ]:
        if not isinstance(item, dict):
            continue
        if "citations" not in item:
            item["citations"] = []
        if provider == "minimax" and isinstance(item.get("citations"), str):
            try:
                decoded = json.loads(item["citations"])
            except json.JSONDecodeError:
                decoded = [] if item["citations"] == "" else None
            if isinstance(decoded, (list, dict)):
                item["citations"] = decoded
        if provider == "minimax" and isinstance(item.get("citations"), dict):
            # MiniMax commonly encodes one schema item as the object itself.
            # Preserve it verbatim as a singleton list; evidence validation below
            # still requires its exact version/unit/checksum identity.
            citation = item["citations"]
            wrapper = next(iter(citation)) if len(citation) == 1 else None
            if wrapper in {"item", "citations"}:
                nested = citation[wrapper]
                if isinstance(nested, str):
                    try:
                        nested = json.loads(nested)
                    except json.JSONDecodeError:
                        pass
                if isinstance(nested, list):
                    item["citations"] = nested
                    continue
                if isinstance(nested, dict):
                    citation = nested
            item["citations"] = [citation]
        if provider == "deepseek" and isinstance(item.get("citations"), list):
            item["citations"] = [
                _normalize_deepseek_citation(citation)
                for citation in item["citations"]
            ]
    options = parsed.get("interaction_options")
    if provider == "minimax" and isinstance(options, list):
        parsed["interaction_options"] = [
            validated.model_dump()
            for item in options
            if (validated := _valid_interaction_option(item)) is not None
        ]
    if drafting or parsed.get("unresolved_items") == []:
        parsed["interaction_options"] = []
    if provider == "deepseek" and drafting:
        parsed["workflow_draft"] = _drop_unknown_complete_draft_fields(
            parsed.get("workflow_draft")
        )
    if provider == "minimax" and not drafting:
        # Workflow drafts are forbidden outside WORKFLOW_DRAFT mode. MiniMax
        # sometimes emits the JSON null token as a string, so enforce the only
        # contract-valid value instead of spending the protocol repair attempt.
        parsed["workflow_draft"] = None
    return json.dumps(
        parsed, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )


def _normalize_deepseek_citation(value: object) -> object:
    """Canonicalize observed DeepSeek citation aliases without inventing evidence."""
    if not isinstance(value, dict):
        return value
    citation = dict(value)
    legacy_version = citation.get("knowledge_version_id")
    citation.pop("document_title", None)
    if "version_id" not in citation and isinstance(legacy_version, str):
        citation["version_id"] = legacy_version
        citation.pop("knowledge_version_id")
    elif citation.get("version_id") == legacy_version:
        citation.pop("knowledge_version_id", None)
    return citation


def _valid_interaction_option(value: object) -> InteractionOptionV1 | None:
    try:
        return InteractionOptionV1.model_validate(value)
    except (TypeError, ValueError):
        return None


def validate_clarification_reply(reply: SubmittedReplyV6) -> str:
    if (
        reply.conclusion is not None
        or reply.details
        or len(reply.unresolved_items) != 1
        or reply.unresolved_items[0].citations
        or reply.referenced_workflow_runs
        or reply.include_execution_status
        or reply.workflow_draft is not None
        or not 2 <= len(reply.interaction_options) <= 3
    ):
        raise ValueError("CLARIFICATION_REPLY_INVALID")
    question = reply.unresolved_items[0].text
    if question != question.strip() or re.fullmatch(
        r"[^。！？.!?\r\n]+[?？]", question
    ) is None:
        raise ValueError("CLARIFICATION_REPLY_INVALID")
    return question


def validate_terminal_reply_context(
    reply: SubmittedReplyV6,
    request_context: dict | None,
) -> None:
    """Validate reply choices that depend on the Kernel-owned request policy.

    The public reply model deliberately has no authorization fields.  These
    invariants therefore belong at the request boundary, where the Kernel knows
    whether this turn may propose execution or author a workflow.
    """
    context = request_context or {
        "mode": "DIALOGUE",
        "dialogue_policy": "READ_ONLY",
        "clarification_only": False,
    }
    mode = context.get("mode")
    policy = context.get("dialogue_policy")
    clarification_only = context.get("clarification_only", False)
    if reply.conclusion is not None and len(reply.conclusion.text) > 160:
        raise ValueError("REPLY_CONCLUSION_TOO_LONG")
    visible_text = [
        *(segment.text for segment in (
            *(() if reply.conclusion is None else (reply.conclusion,)),
            *reply.details,
            *reply.unresolved_items,
        )),
        *(
            text
            for option in reply.interaction_options
            for text in (option.label, option.description, option.message)
        ),
    ]
    if reply.workflow_draft is not None:
        visible_text.extend((
            reply.workflow_draft.title,
            reply.workflow_draft.summary,
            *reply.workflow_draft.unresolved_items,
            *reply.workflow_draft.limitations,
            *(
                source.explanation
                for source in reply.workflow_draft.parameter_sources
            ),
        ))
    if any(_VISIBLE_TECHNICAL_IDENTIFIER.search(text) for text in visible_text):
        raise ValueError("REPLY_TECHNICAL_IDENTIFIER_VISIBLE")
    comparison_terms = context.get("comparison_terms", [])
    if comparison_terms:
        combined = "\n".join(visible_text)
        if (
            not isinstance(comparison_terms, list)
            or len(comparison_terms) != 2
            or any(
                not isinstance(term, str) or term not in combined
                for term in comparison_terms
            )
            or _DIRECT_RECOMMENDATION.search(combined) is None
        ):
            raise ValueError("REPLY_COMPARISON_INCOMPLETE")
        if _COMPARISON_META_LANGUAGE.search(combined) is not None:
            raise ValueError("REPLY_COMPARISON_META_LANGUAGE")
    required_response_terms = context.get("required_response_terms", [])
    if required_response_terms:
        combined = "\n".join(visible_text).casefold()
        if (
            not isinstance(required_response_terms, list)
            or any(
                not isinstance(term, str) or term.casefold() not in combined
                for term in required_response_terms
            )
        ):
            raise ValueError("REPLY_REQUESTED_TERMS_INCOMPLETE")
    if clarification_only:
        validate_clarification_reply(reply)
        return
    if mode != "WORKFLOW_DRAFT" and reply.workflow_draft is not None:
        raise ValueError("AGENT_WORKFLOW_DRAFT_INVALID")
    if mode == "WORKFLOW_DRAFT" and (
        reply.workflow_draft is None and not reply.unresolved_items
    ):
        raise ValueError("AGENT_WORKFLOW_DRAFT_INVALID")
    if (
        policy == "EXECUTION_PROPOSAL"
        and not reply.include_execution_status
        and not reply.unresolved_items
    ):
        # A successful proposal terminates through an executable-model
        # interruption, never through submit_reply.  A final reply at this point
        # is valid only when it identifies concrete blockers.
        raise ValueError("EXECUTION_PROPOSAL_NOT_CREATED")
    if not reply.unresolved_items and reply.interaction_options:
        raise ValueError("INTERACTION_OPTIONS_WITHOUT_UNRESOLVED_ITEM")


def _reply_citations(reply: SubmittedReplyV6):
    segments = [
        *(() if reply.conclusion is None else (reply.conclusion,)),
        *reply.details,
        *reply.unresolved_items,
    ]
    return [citation for segment in segments for citation in segment.citations]


def _validate_reply_evidence(reply: SubmittedReplyV6, control: dict) -> None:
    allowed = {tuple(item) for item in control["knowledge_evidence"]}
    citations = _reply_citations(reply)
    if any(
        (item.version_id, item.unit_id, item.checksum) not in allowed
        for item in citations
    ):
        raise ValueError("REPLY_CITATION_INVALID")
    body = [
        *(() if reply.conclusion is None else (reply.conclusion,)),
        *reply.details,
    ]
    if allowed and body and not any(segment.citations for segment in body):
        raise ValueError("REPLY_CITATION_REQUIRED")


def _record_read_evidence(control: dict, result: object) -> None:
    if not isinstance(result, dict) or not isinstance(result.get("data"), dict):
        return
    items = result["data"].get("items")
    if not isinstance(items, list):
        return
    evidence = [list(item) for item in control["knowledge_evidence"]]
    candidates = [
        list(item) for item in control.get("knowledge_exact_candidates", [])
    ]
    for item in items:
        if not isinstance(item, dict):
            continue
        observed = knowledge_identity(item)
        if observed is None:
            continue
        version_id, unit_id, _checksum = observed
        identity = trusted_read_identity(
            item, {tuple(candidate) for candidate in candidates},
        )
        evidence = [
            current for current in evidence
            if current[:2] != [version_id, unit_id]
        ]
        candidates = [
            current for current in candidates
            if current[:2] != [version_id, unit_id]
        ]
        if identity is not None:
            evidence.append(list(identity))
    control["knowledge_evidence"] = evidence
    if "knowledge_exact_candidates" in control:
        control["knowledge_exact_candidates"] = candidates


def _record_retrieved_evidence(control: dict, result: object) -> None:
    candidates = [
        list(item) for item in control.get("knowledge_exact_candidates", [])
    ]
    for identity in exact_retrieval_identities(result):
        candidates = [
            current for current in candidates
            if current[:2] != list(identity[:2])
        ]
        candidates.append(list(identity))
    if "knowledge_exact_candidates" in control:
        control["knowledge_exact_candidates"] = candidates


def _has_evidence_references(data: object) -> bool:
    """Return true only when a retrieval result can be passed to read_evidence."""
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return False
    return any(
        isinstance(item, dict)
        and all(
            isinstance(item.get(key), str) and item[key]
            for key in ("versionId", "unitId", "checksum")
        )
        for item in items
    )


def _clarification_reply_schema() -> dict:
    schema = reply_schema()
    schema["properties"]["conclusion"] = {"type": "null"}
    schema["properties"]["details"]["maxItems"] = 0
    schema["properties"]["unresolved_items"].update(
        {"minItems": 1, "maxItems": 1}
    )
    schema["properties"]["referenced_workflow_runs"]["maxItems"] = 0
    schema["properties"]["include_execution_status"] = {
        "type": "boolean", "const": False
    }
    schema["properties"]["interaction_options"].update(
        {"minItems": 2, "maxItems": 3}
    )
    segment = schema["$defs"]["ReplySegmentV1"]
    segment["properties"]["text"]["pattern"] = r"^[^。！？.!?\r\n]+[?？]$"
    segment["properties"]["citations"]["maxItems"] = 0
    return schema
