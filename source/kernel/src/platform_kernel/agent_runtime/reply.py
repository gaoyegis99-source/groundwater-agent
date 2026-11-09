from __future__ import annotations

import json

from pydantic import ValidationError as PydanticValidationError

from ..errors import AgentRunError
from .evidence import collect_turn_evidence
from .models import (
    AgentFinalV5,
    ReplyPresentationV1,
    ReplySectionV1,
    ReplySourceV2,
    SubmittedReplyV6,
    normalize_reply_json,
    reply_validation_diagnostics,
)
from .persistence import (
    allowed_workflow_run_ids,
    get_run,
    load_execution_context,
    load_request_context,
)
from .tooling import validate_clarification_reply, validate_terminal_reply_context


def _reply_section(
    segment,
    reads: dict[tuple[str, int, str], str],
    sources: list[ReplySourceV2],
    source_indexes: dict[tuple[str, int, str], int],
    *,
    empty_reason: str,
) -> ReplySectionV1:
    if not segment.text.strip():
        raise ValueError(empty_reason)
    indexes = []
    for citation in segment.citations:
        key = (citation.version_id, citation.unit_id, citation.checksum)
        if key not in reads:
            raise ValueError("REPLY_CITATION_INVALID")
        index = source_indexes.get(key)
        if index is None:
            index = len(sources)
            source_indexes[key] = index
            title = " ".join(reads[key].split()) or "知识依据"
            sources.append(ReplySourceV2(
                title=title,
                version_id=citation.version_id,
                unit_id=citation.unit_id,
                checksum=citation.checksum,
            ))
        if index not in indexes:
            indexes.append(index)
    return ReplySectionV1(markdown=segment.text, source_indexes=indexes)


def _message_source_title(title: str) -> str:
    return title.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _execution_status(actions) -> str:
    counts = {"COMPLETE": 0, "FAILED": 0, "REJECTED": 0, "CANCELLED": 0}
    output_count = 0
    for action in actions:
        if action.status not in counts:
            raise ValueError("EXECUTION_STATUS_NOT_TERMINAL")
        counts[action.status] += 1
        output_count += len(action.outputs)
    if not any(counts.values()):
        raise ValueError("EXECUTION_STATUS_MISSING")
    labels = {"COMPLETE": "已完成", "FAILED": "未完成（执行失败）", "REJECTED": "已拒绝", "CANCELLED": "已取消"}
    summary = "；".join(f"{labels[status]} {count} 项分析" for status, count in counts.items() if count)
    if output_count:
        summary += f"；生成 {output_count} 份结果，可在分析结果中查看或下载"
    return summary + "。"


def render_submitted_reply(
    database,
    agent_run_id: str,
    result,
    *,
    final_output: str | None = None,
) -> AgentFinalV5:
    terminal_output = result.final_output if final_output is None else final_output
    try:
        terminal = json.loads(terminal_output)
    except (TypeError, json.JSONDecodeError):
        terminal = None
    if (
        isinstance(terminal, dict)
        and terminal.get("status") == "RETRY_EXHAUSTED"
        and isinstance(terminal.get("diagnostics"), dict)
    ):
        raise AgentRunError(
            "AGENT_MODEL_OUTPUT_INVALID", diagnostics=terminal["diagnostics"]
        )
    try:
        submitted = SubmittedReplyV6.model_validate_json(
            normalize_reply_json(terminal_output)
        )
    except PydanticValidationError as error:
        raise AgentRunError(
            "AGENT_MODEL_OUTPUT_INVALID",
            diagnostics=reply_validation_diagnostics(error),
        ) from error
    try:
        clarification_only = load_request_context(
            database, agent_run_id
        ).get("clarification_only", False)
        if type(clarification_only) is not bool:
            raise ValueError("CLARIFICATION_CONTEXT_INVALID")
        run = get_run(database, agent_run_id)
        request_context = load_request_context(database, agent_run_id)
        validate_terminal_reply_context(submitted, request_context)
        allowed = allowed_workflow_run_ids(database, run)
        if any(run_id not in allowed for run_id in submitted.referenced_workflow_runs):
            raise ValueError("UNRELATED_WORKFLOW_RUN")
        workflow_runs = list(dict.fromkeys([
            *(action.workflow_run_id for action in run.actions if action.workflow_run_id),
            *submitted.referenced_workflow_runs,
        ]))
        facts = load_execution_context(
            database, run.session_id, before_goal_revision=run.goal_revision + 1
        )
        referenced = set(workflow_runs)
        actions = tuple(
            action for action in facts.actions
            if action.goal_revision == run.goal_revision
            or action.workflow_run_id in referenced
        )
        reads = collect_turn_evidence(result.to_input_list()).knowledge
        if clarification_only:
            question = validate_clarification_reply(submitted)
            if workflow_runs or reads:
                raise ValueError("CLARIFICATION_REPLY_INVALID")
            return AgentFinalV5(
                message=question,
                presentation=ReplyPresentationV1(
                    conclusion=None,
                    details=[],
                    execution_summary=None,
                    unresolved_items=[ReplySectionV1(
                        markdown=question, source_indexes=[],
                    )],
                    sources=[],
                ),
                workflow_runs=[],
                unresolved_items=[question],
                interaction_options=submitted.interaction_options,
            )
        if submitted.include_execution_status and (
            submitted.conclusion is not None
            or submitted.unresolved_items
            or submitted.workflow_draft is not None
        ):
            raise ValueError("REPLY_EXECUTION_STATUS_INVALID")
        if not submitted.include_execution_status and submitted.conclusion is None:
            raise ValueError("REPLY_CONCLUSION_REQUIRED")
        sources: list[ReplySourceV2] = []
        source_indexes: dict[tuple[str, int, str], int] = {}
        conclusion = (
            None
            if submitted.conclusion is None
            else _reply_section(
                submitted.conclusion,
                reads,
                sources,
                source_indexes,
                empty_reason="REPLY_SECTION_EMPTY",
            )
        )
        details = [
            _reply_section(
                segment,
                reads,
                sources,
                source_indexes,
                empty_reason="REPLY_SECTION_EMPTY",
            )
            for segment in submitted.details
        ]
        body = [
            *(() if submitted.conclusion is None else (submitted.conclusion,)),
            *submitted.details,
        ]
        if reads and body and not any(segment.citations for segment in body):
            raise ValueError("REPLY_CITATION_REQUIRED")
        execution_summary = None
        if submitted.include_execution_status:
            execution_summary = ReplySectionV1(
                markdown=_execution_status(actions), source_indexes=[],
            )
        questions = []
        unresolved_items = []
        for item in submitted.unresolved_items:
            if len(item.text.splitlines()) != 1:
                raise ValueError("UNRESOLVED_ITEM_FORMAT_INVALID")
            unresolved_items.append(_reply_section(
                item,
                reads,
                sources,
                source_indexes,
                empty_reason="REPLY_UNRESOLVED_ITEM_EMPTY",
            ))
            questions.append(item.text)
        parts = [
            *(section.markdown for section in [conclusion, *details] if section is not None),
            *(() if execution_summary is None else (execution_summary.markdown,)),
            *(() if not questions else ("需要补充：\n" + "\n".join(questions),)),
        ]
        if not parts:
            raise ValueError("EMPTY_REPLY")
        source_titles = list(dict.fromkeys(
            _message_source_title(source.title) for source in sources
        ))
        if source_titles:
            parts.append("依据：" + "；".join(source_titles))
        return AgentFinalV5(
            message="\n\n".join(parts),
            presentation=ReplyPresentationV1(
                conclusion=conclusion,
                details=details,
                execution_summary=execution_summary,
                unresolved_items=unresolved_items,
                sources=sources,
            ),
            workflow_runs=workflow_runs,
            unresolved_items=questions,
            interaction_options=submitted.interaction_options,
        )
    except (ValueError, TypeError, KeyError, SyntaxError, UnicodeEncodeError) as error:
        known_reasons = {
            "CLARIFICATION_CONTEXT_INVALID", "UNRELATED_WORKFLOW_RUN",
            "CLARIFICATION_REPLY_INVALID", "REPLY_CONCLUSION_REQUIRED",
            "REPLY_EXECUTION_STATUS_INVALID", "REPLY_SECTION_EMPTY",
            "REPLY_CITATION_REQUIRED", "REPLY_CITATION_INVALID",
            "REPLY_UNRESOLVED_ITEM_EMPTY", "UNRESOLVED_ITEM_FORMAT_INVALID",
            "EMPTY_REPLY", "EXECUTION_STATUS_NOT_TERMINAL",
            "EXECUTION_STATUS_MISSING",
            "AGENT_WORKFLOW_DRAFT_INVALID", "EXECUTION_PROPOSAL_NOT_CREATED",
            "INTERACTION_OPTIONS_WITHOUT_UNRESOLVED_ITEM",
            "REPLY_CONCLUSION_TOO_LONG", "REPLY_TECHNICAL_IDENTIFIER_VISIBLE",
        }
        reason = str(error) if isinstance(error, ValueError) and str(error) in known_reasons else "REPLY_SCHEMA_INVALID"
        raise AgentRunError("AGENT_MODEL_OUTPUT_INVALID", diagnostics={"reason": reason}) from error
