"""Structured reply content assessment and automatic assertions."""
from __future__ import annotations

import os
import re

from .definitions import GENERIC_CONTINUATION, OPTION_FORBIDDEN, TECHNICAL_LEAK

def _sentence_count(text: str) -> int:
    return len([part for part in re.split(r"[。！？!?]+", text) if part.strip()])


def _sections(presentation: dict) -> list[dict]:
    return [
        *([] if presentation["conclusion"] is None else [presentation["conclusion"]]),
        *presentation["details"],
        *([] if presentation["execution_summary"] is None else [presentation["execution_summary"]]),
        *presentation["unresolved_items"],
    ]


def _review_content(
    presentation: dict,
    interaction_options: list[dict],
    card_context: dict | None,
) -> dict:
    return {
        "conclusion": None if presentation["conclusion"] is None else presentation["conclusion"]["markdown"],
        "details": [item["markdown"] for item in presentation["details"]],
        "execution_summary": (
            None
            if presentation["execution_summary"] is None
            else presentation["execution_summary"]["markdown"]
        ),
        "unresolved_items": [
            item["markdown"] for item in presentation["unresolved_items"]
        ],
        "sources": [
            {"title": item["title"], "unit": item["unit_id"]}
            for item in presentation["sources"]
        ],
        "interaction_options": interaction_options,
        "business_card": card_context,
    }


def _text_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in value for text in _text_values(item)]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _text_values(item)]
    return []

def _expected_linear_message(presentation: dict, shape: str) -> str:
    if shape == "clarification":
        unresolved = presentation["unresolved_items"]
        if len(unresolved) != 1:
            raise AssertionError("CLARIFICATION_PRESENTATION_INVALID")
        return unresolved[0]["markdown"]
    parts = []
    conclusion = presentation["conclusion"]
    if conclusion is not None:
        parts.append(conclusion["markdown"])
    parts.extend(item["markdown"] for item in presentation["details"])
    execution_summary = presentation["execution_summary"]
    if execution_summary is not None:
        parts.append(execution_summary["markdown"])
    unresolved = [
        item["markdown"] for item in presentation["unresolved_items"]
    ]
    if unresolved:
        parts.append("需要补充：\n" + "\n".join(unresolved))
    source_titles = list(dict.fromkeys(
        item["title"].replace("\\", "\\\\").replace("[", "\\[").replace(
            "]", "\\]"
        )
        for item in presentation["sources"]
    ))
    if source_titles:
        parts.append("依据：" + "；".join(source_titles))
    return "\n\n".join(parts)

def assess_reply(
    *,
    case: dict,
    output: dict,
    session: dict,
    turn: dict,
    run: dict,
    function_names: list[str],
    provider_requests: list[dict] | None = None,
    card_context: dict | None,
    execution_outcome: str | None,
    execution_output_count: int | None = None,
) -> tuple[dict[str, bool], dict]:
    if run["status"] != "COMPLETE" or run.get("error_code") is not None:
        raise AssertionError("AGENT_RUN_NOT_COMPLETE")
    if (
        run["provider"] != os.environ["PLATFORM_AGENT_PROVIDER"]
        or run["model"] != os.environ["PLATFORM_AGENT_MODEL"]
    ):
        raise AssertionError("CONFIGURED_PROVIDER_NOT_USED")
    if turn["status"] != "COMPLETE":
        raise AssertionError("TURN_NOT_COMPLETE")
    if turn["resolved_mode"] != case["expected_mode"]:
        raise AssertionError("RESOLVED_MODE_MISMATCH")
    if turn.get("dialogue_policy") != case["expected_policy"]:
        raise AssertionError("DIALOGUE_POLICY_MISMATCH")
    if case["expected_shape"] == "clarification" and (
        turn["intent_decision"] != "NEEDS_CLARIFICATION"
        or turn["clarification_required"] is not True
    ):
        raise AssertionError("CLARIFICATION_ROUTE_MISMATCH")
    submit_count = function_names.count("submit_reply")
    schema_retry_recorded = any(
        item.get("attempt") == 2
        for item in (provider_requests or [])
    )
    if submit_count not in {1, 2} or (submit_count == 2 and not schema_retry_recorded):
        raise AssertionError("FINAL_REPLY_NOT_STRUCTURALLY_VALID")
    if set(output) != {
        "message", "presentation", "workflow_runs", "unresolved_items",
        "interaction_options",
    }:
        raise AssertionError("AGENT_FINAL_V5_INVALID")
    presentation = output["presentation"]
    if set(presentation) != {
        "conclusion", "details", "execution_summary", "unresolved_items", "sources",
    }:
        raise AssertionError("REPLY_PRESENTATION_INVALID")
    sources = presentation["sources"]
    interaction_options = output["interaction_options"]
    maximum_options = case.get("max_options", 3)
    if (
        not isinstance(interaction_options, list)
        or len(interaction_options) > maximum_options
        or any(
            set(item) != {"label", "description", "message"}
            or any(
                not isinstance(item[field], str)
                or not item[field]
                or item[field] != item[field].strip()
                for field in ("label", "description", "message")
            )
            for item in interaction_options
        )
        or len({item["label"] for item in interaction_options})
        != len(interaction_options)
        or len({item["description"] for item in interaction_options})
        != len(interaction_options)
        or len({item["message"] for item in interaction_options})
        != len(interaction_options)
        or any(
            OPTION_FORBIDDEN.search(item["message"]) is not None
            for item in interaction_options
        )
    ):
        raise AssertionError("INTERACTION_OPTIONS_INVALID")
    identities = [
        (item["version_id"], item["unit_id"], item["checksum"])
        for item in sources
    ]
    duplicate_sources = len(identities) != len(set(identities))
    sections = _sections(presentation)
    if output["unresolved_items"] != [
        item["markdown"] for item in presentation["unresolved_items"]
    ]:
        raise AssertionError("UNRESOLVED_ITEM_PROJECTION_MISMATCH")
    source_indexes_valid = all(
        len(section["source_indexes"]) == len(set(section["source_indexes"]))
        and all(type(index) is int and 0 <= index < len(sources)
                for index in section["source_indexes"])
        for section in sections
    )
    if not source_indexes_valid:
        raise AssertionError("SOURCE_INDEX_INVALID")
    conclusion = presentation["conclusion"]
    shape = case["expected_shape"]
    if shape in {"ordinary", "draft"}:
        if conclusion is None or presentation["execution_summary"] is not None:
            raise AssertionError("ORDINARY_PRESENTATION_INVALID")
    elif shape == "clarification":
        if (
            conclusion is not None
            or presentation["details"]
            or presentation["execution_summary"] is not None
            or len(presentation["unresolved_items"]) != 1
            or not 2 <= len(output["interaction_options"]) <= 3
        ):
            raise AssertionError("CLARIFICATION_PRESENTATION_INVALID")
    elif shape == "execution":
        if (
            conclusion is not None
            or presentation["execution_summary"] is None
            or presentation["unresolved_items"]
        ):
            raise AssertionError("EXECUTION_PRESENTATION_INVALID")
        if execution_outcome not in {"SUCCEEDED", "REJECTED"}:
            raise AssertionError("EXECUTION_OUTCOME_INVALID")
        execution_markdown = presentation["execution_summary"]["markdown"]
        if len(execution_markdown) > 120 or _sentence_count(execution_markdown) > 1:
            raise AssertionError("EXECUTION_SUMMARY_LIMIT_EXCEEDED")
        if execution_outcome == "SUCCEEDED":
            if execution_output_count is None or execution_output_count < 0:
                raise AssertionError("EXECUTION_OUTPUT_COUNT_INVALID")
            expected_summary = "已完成 1 项分析"
            if execution_output_count:
                expected_summary += (
                    f"；生成 {execution_output_count} 份结果，"
                    "可在分析结果中查看或下载"
                )
            expected_summary += "。"
        else:
            if execution_output_count not in {None, 0}:
                raise AssertionError("EXECUTION_OUTPUT_COUNT_INVALID")
            expected_summary = "已拒绝 1 项分析。"
        if execution_markdown != expected_summary:
            raise AssertionError("EXECUTION_SUMMARY_NOT_AUTHORITATIVE")
    else:
        raise AssertionError("UNKNOWN_EXPECTED_SHAPE")
    if output["message"] != _expected_linear_message(presentation, shape):
        raise AssertionError("LINEAR_MESSAGE_MISMATCH")
    if case["requires_source"] and not sources:
        raise AssertionError("SOURCE_REQUIRED")
    if len(presentation["details"]) > 3:
        raise AssertionError("DETAIL_TARGET_EXCEEDED")
    if conclusion is not None:
        if _sentence_count(conclusion["markdown"]) > 2:
            raise AssertionError("CONCLUSION_SENTENCE_LIMIT_EXCEEDED")
        maximum = case.get("max_conclusion_chars")
        if maximum is not None and len(conclusion["markdown"]) > maximum:
            raise AssertionError("CONCLUSION_CHARACTER_LIMIT_EXCEEDED")
    card_summary = (
        card_context.get("summary")
        if card_context is not None and card_context.get("kind") == "workflow_draft"
        else None
    )
    if card_summary is not None:
        maximum = case.get("max_card_summary_chars", 120)
        if len(card_summary) > maximum or _sentence_count(card_summary) > 1:
            raise AssertionError("CARD_SUMMARY_LIMIT_EXCEEDED")
    review_content = _review_content(
        presentation, interaction_options, card_context,
    )
    visible_text = "\n".join([
        output["message"], *_text_values(review_content),
    ])
    technical_leak = TECHNICAL_LEAK.search(visible_text) is not None
    folded = visible_text.casefold()
    required = case.get("required_any", [])
    known_fact_error = bool(required) and not any(
        item.casefold() in folded for item in required
    )
    known_fact_error |= any(
        item.casefold() not in folded for item in case.get("required_all", [])
    )
    known_fact_error |= any(
        item.casefold() in folded for item in case.get("forbidden", [])
    )
    if case["read_only"]:
        unexpected_side_effect = bool(
            session["confirmation"]
            or session["workflow_run_ids"]
            or session["remote_outputs"]
            or turn["confirmations"]
            or turn["execution_tasks"]
            or turn.get("plans")
        )
    else:
        unexpected_side_effect = False
    if shape == "draft":
        if session["workflow_draft_id"] is None or card_summary is None:
            raise AssertionError("WORKFLOW_DRAFT_MISSING")
        draft_unresolved = "\n".join(
            item["markdown"] for item in presentation["unresolved_items"]
        )
        if not draft_unresolved or not all(
            term in draft_unresolved for term in ("排序", "规则")
        ):
            raise AssertionError("WORKFLOW_DRAFT_VISIBLE_GAP_MISSING")
    if turn.get("reply") != presentation:
        raise AssertionError("TURN_REPLY_NOT_PERSISTED")
    assertions = {
        "final_reply_structurally_valid": True,
        "configured_provider_used_without_fallback": True,
        "conclusion_within_two_sentences": (
            conclusion is None or _sentence_count(conclusion["markdown"]) <= 2
        ),
        "details_within_target": len(presentation["details"]) <= 3,
        "duplicate_sources_absent": not duplicate_sources,
        "technical_identifier_leak_absent": not technical_leak,
        "read_only_side_effect_absent": not unexpected_side_effect,
        "known_fact_error_absent": not known_fact_error,
        "generic_continuation_absent": (
            GENERIC_CONTINUATION.search(visible_text) is None
        ),
        "dialogue_policy_exact": True,
        "interaction_option_contract_valid": True,
        "option_auto_followup_exact": True,
        "scenario_oracle_passed": True,
    }
    if not all(assertions.values()):
        failed = next(name for name, passed in assertions.items() if not passed)
        raise AssertionError(failed.upper())
    return assertions, review_content
