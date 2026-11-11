"""Evaluate the configured Intent Router against the frozen Chinese gold set.

The report keeps responseKind and semanticIntent separate.  An exact-pair score
alone would hide whether a regression changed the interaction mode or only the
semantic label.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Iterable

from platform_kernel.intent_routing import IntentRouter, IntentRoutingError
from platform_kernel.settings import IntentRouterSettings


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "intent-routing-zh.v1.json"
EXPECTED_CATEGORIES = (
    "ordinary_consult",
    "comparison",
    "execution",
    "execution_negated",
    "status_query",
    "workflow_draft",
    "attachment_read",
    "missing_field_clarification",
)
RESPONSE_KINDS = (
    "DIALOGUE",
    "WORKFLOW_DRAFT",
    "CONTROL",
    "NEEDS_CLARIFICATION",
)
SEMANTIC_INTENTS = (
    "CONSULT",
    "COMPARE",
    "DESIGN",
    "EXECUTE_ON_DATA",
    "CREATE_WORKFLOW",
    "REVIEW_RESULT",
    "STATUS_QUERY",
    "UNSPECIFIED",
    "UNKNOWN",
)
REQUIRED_ENVIRONMENT = (
    "PLATFORM_INTENT_ROUTER_PROVIDER",
    "PLATFORM_INTENT_ROUTER_PROTOCOL",
    "PLATFORM_INTENT_ROUTER_BASE_URL",
    "PLATFORM_INTENT_ROUTER_MODEL",
    "PLATFORM_INTENT_ROUTER_API_KEY_FILE",
    "PLATFORM_INTENT_ROUTER_CONTEXT_WINDOW_TOKENS",
    "PLATFORM_INTENT_ROUTER_MAX_OUTPUT_TOKENS",
    "PLATFORM_INTENT_ROUTER_REQUEST_TIMEOUT_SECONDS",
    "PLATFORM_INTENT_ROUTER_SEGMENT_TIMEOUT_SECONDS",
)
REPORT_SCOPE = {
    "component": "MODEL_INTENT_ROUTER",
    "repetitionsPerCase": 1,
    "kernelOwnedRules": "EXCLUDED",
    "accuracyClaim": "FROZEN_CORPUS_ONLY",
}

_CASE_FIELDS = {
    "caseId",
    "category",
    "message",
    "hasInputReferences",
    "expected",
}
_EXPECTED_FIELDS = {"responseKind", "semanticIntent"}


def load_cases(path: Path = FIXTURE) -> list[dict]:
    """Load and strictly validate the frozen corpus contract."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("INTENT_ROUTING_GOLD_INVALID") from error
    if (
        not isinstance(raw, dict)
        or set(raw) != {"schemaVersion", "cases"}
        or raw["schemaVersion"] != "intent-routing-zh-gold.v1"
        or not isinstance(raw["cases"], list)
    ):
        raise ValueError("INTENT_ROUTING_GOLD_INVALID")
    cases = raw["cases"]
    if len(cases) != 24:
        raise ValueError("INTENT_ROUTING_GOLD_INVALID")
    identifiers: set[str] = set()
    category_counts = {category: 0 for category in EXPECTED_CATEGORIES}
    for case in cases:
        if not isinstance(case, dict) or set(case) != _CASE_FIELDS:
            raise ValueError("INTENT_ROUTING_GOLD_INVALID")
        case_id = case["caseId"]
        category = case["category"]
        message = case["message"]
        expected = case["expected"]
        if (
            not isinstance(case_id, str)
            or not case_id
            or case_id in identifiers
            or not isinstance(category, str)
            or category not in category_counts
            or not isinstance(message, str)
            or not message.strip()
            or not any("\u4e00" <= character <= "\u9fff" for character in message)
            or type(case["hasInputReferences"]) is not bool
            or not isinstance(expected, dict)
            or set(expected) != _EXPECTED_FIELDS
            or expected["responseKind"] not in RESPONSE_KINDS
            or expected["semanticIntent"] not in SEMANTIC_INTENTS
            or not _legal_expected_pair(expected)
        ):
            raise ValueError("INTENT_ROUTING_GOLD_INVALID")
        identifiers.add(case_id)
        category_counts[category] += 1
    if set(category_counts.values()) != {3}:
        raise ValueError("INTENT_ROUTING_GOLD_INVALID")
    return cases


def _legal_expected_pair(expected: dict) -> bool:
    response_kind = expected["responseKind"]
    semantic_intent = expected["semanticIntent"]
    if response_kind == "CONTROL":
        return semantic_intent == "UNSPECIFIED"
    if response_kind == "WORKFLOW_DRAFT":
        return semantic_intent == "CREATE_WORKFLOW"
    if semantic_intent == "CREATE_WORKFLOW":
        return response_kind == "WORKFLOW_DRAFT"
    return True


def fixture_checksum(path: Path = FIXTURE) -> str:
    try:
        content = path.read_bytes()
    except OSError as error:
        raise ValueError("INTENT_ROUTING_GOLD_INVALID") from error
    return "sha256:" + hashlib.sha256(content).hexdigest()


def score_predictions(cases: list[dict], predictions: list[dict]) -> dict:
    """Score one prediction per gold case without collapsing either dimension."""

    prediction_by_id = _prediction_index(cases, predictions)
    evaluated: list[dict] = []
    for case in cases:
        prediction = prediction_by_id[case["caseId"]]
        expected = case["expected"]
        actual_response = prediction.get("responseKind")
        actual_semantic = prediction.get("semanticIntent")
        response_match = actual_response == expected["responseKind"]
        semantic_match = actual_semantic == expected["semanticIntent"]
        evaluated.append({
            "caseId": case["caseId"],
            "category": case["category"],
            "expected": dict(expected),
            "actual": {
                "responseKind": actual_response,
                "semanticIntent": actual_semantic,
            },
            "match": {
                "exactPair": response_match and semantic_match,
                "responseKind": response_match,
                "semanticIntent": semantic_match,
            },
            "errorCode": prediction.get("errorCode"),
        })

    total = len(evaluated)
    exact_correct = sum(item["match"]["exactPair"] for item in evaluated)
    response_correct = sum(item["match"]["responseKind"] for item in evaluated)
    semantic_correct = sum(item["match"]["semanticIntent"] for item in evaluated)
    return {
        "schemaVersion": "intent-routing-zh-evaluation-report.v1",
        "status": "PASS" if exact_correct == total else "FAIL",
        "scope": dict(REPORT_SCOPE),
        "counts": {
            "cases": total,
            "exactPairCorrect": exact_correct,
            "responseKindCorrect": response_correct,
            "semanticIntentCorrect": semantic_correct,
        },
        "accuracy": {
            "exactPair": exact_correct / total,
            "responseKind": response_correct / total,
            "semanticIntent": semantic_correct / total,
        },
        "dimensions": {
            "responseKind": _dimension_summary(
                evaluated, "responseKind", RESPONSE_KINDS
            ),
            "semanticIntent": _dimension_summary(
                evaluated, "semanticIntent", SEMANTIC_INTENTS
            ),
        },
        "categories": _category_summary(evaluated),
        "cases": evaluated,
    }


def _prediction_index(cases: list[dict], predictions: list[dict]) -> dict[str, dict]:
    if not isinstance(predictions, list) or len(predictions) != len(cases):
        raise ValueError("INTENT_ROUTING_PREDICTIONS_INVALID")
    indexed: dict[str, dict] = {}
    for prediction in predictions:
        if not isinstance(prediction, dict):
            raise ValueError("INTENT_ROUTING_PREDICTIONS_INVALID")
        case_id = prediction.get("caseId")
        response_kind = prediction.get("responseKind")
        semantic_intent = prediction.get("semanticIntent")
        error_code = prediction.get("errorCode")
        if (
            not isinstance(case_id, str)
            or case_id in indexed
            or (
                response_kind is not None
                and response_kind not in RESPONSE_KINDS
            )
            or (
                semantic_intent is not None
                and semantic_intent not in SEMANTIC_INTENTS
            )
            or (
                (response_kind is None or semantic_intent is None)
                and not isinstance(error_code, str)
            )
        ):
            raise ValueError("INTENT_ROUTING_PREDICTIONS_INVALID")
        indexed[case_id] = prediction
    if set(indexed) != {case["caseId"] for case in cases}:
        raise ValueError("INTENT_ROUTING_PREDICTIONS_INVALID")
    return indexed


def _dimension_summary(
    evaluated: list[dict],
    dimension: str,
    order: Iterable[str],
) -> dict:
    correct = sum(item["match"][dimension] for item in evaluated)
    by_expected: dict[str, dict] = {}
    expected_values = {item["expected"][dimension] for item in evaluated}
    for expected in order:
        if expected not in expected_values:
            continue
        members = [
            item for item in evaluated
            if item["expected"][dimension] == expected
        ]
        actual: dict[str, int] = {}
        for item in members:
            value = item["actual"][dimension]
            label = "ERROR" if value is None else value
            actual[label] = actual.get(label, 0) + 1
        class_correct = sum(item["match"][dimension] for item in members)
        by_expected[expected] = {
            "total": len(members),
            "correct": class_correct,
            "accuracy": class_correct / len(members),
            "actual": actual,
        }
    return {
        "total": len(evaluated),
        "correct": correct,
        "accuracy": correct / len(evaluated),
        "byExpected": by_expected,
    }


def _category_summary(evaluated: list[dict]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for category in EXPECTED_CATEGORIES:
        members = [item for item in evaluated if item["category"] == category]
        exact = sum(item["match"]["exactPair"] for item in members)
        response = sum(item["match"]["responseKind"] for item in members)
        semantic = sum(item["match"]["semanticIntent"] for item in members)
        result[category] = {
            "total": len(members),
            "exactPairCorrect": exact,
            "responseKindCorrect": response,
            "semanticIntentCorrect": semantic,
            "accuracy": {
                "exactPair": exact / len(members),
                "responseKind": response / len(members),
                "semanticIntent": semantic / len(members),
            },
        }
    return result


def run_cases(router: IntentRouter, cases: list[dict]) -> list[dict]:
    """Run the corpus through an injected router; primarily useful in tests."""

    return asyncio.run(_run_cases(router, cases))


async def _run_cases(router: IntentRouter, cases: list[dict]) -> list[dict]:
    records: list[dict] = []
    for case in cases:
        case_id = case["caseId"]
        try:
            result = await router.route(
                message=case["message"],
                memory=None,
                history=(),
                task_context=None,
                candidate_context={
                    "has_task": False,
                    "allowed_task_actions": ["NONE", "CREATE"],
                    "controls_are_kernel_only": True,
                    "input_references": (
                        [_fixture_reference(case_id)]
                        if case["hasInputReferences"]
                        else []
                    ),
                },
                request_agent_run_id=f"intent-gold-{case_id}",
                session_id=f"intent-gold-{case_id}",
                goal_revision=1,
                memory_through_goal_revision=0,
            )
        except IntentRoutingError as error:
            records.append({
                "caseId": case_id,
                "responseKind": None,
                "semanticIntent": None,
                "errorCode": error.code,
                "providerRequestCount": error.provider_request_count,
                "usage": {
                    "inputTokens": error.usage.input_tokens,
                    "outputTokens": error.usage.output_tokens,
                    "totalTokens": error.usage.total_tokens,
                },
                "durationMs": error.duration_ms,
            })
            continue
        records.append({
            "caseId": case_id,
            "responseKind": result.intent.response_kind,
            "semanticIntent": result.intent.semantic_intent,
            "errorCode": None,
            "providerRequestCount": result.provider_request_count,
            "usage": {
                "inputTokens": result.usage.input_tokens,
                "outputTokens": result.usage.output_tokens,
                "totalTokens": result.usage.total_tokens,
            },
            "durationMs": result.duration_ms,
        })
    return records


def _fixture_reference(case_id: str) -> dict[str, object]:
    return {
        "kind": "data_version",
        "service": "data-primary",
        "datasetId": f"gold-dataset-{case_id}",
        "versionId": f"gold-version-{case_id}",
        "semanticId": f"gold-semantic-{case_id}",
        "objectId": f"gold-object-{case_id}",
        "mediaType": "text/csv",
        "format": "csv",
        "sizeBytes": 1,
        "checksum": "sha256:" + "0" * 64,
    }


def configuration_problem() -> tuple[str, list[str]] | None:
    missing = [name for name in REQUIRED_ENVIRONMENT if not os.environ.get(name)]
    return None if not missing else ("MISSING_ROUTER_CONFIGURATION", missing)


async def _run_configured(
    settings: IntentRouterSettings,
    cases: list[dict],
) -> list[dict]:
    router = IntentRouter(settings, agent_slots=asyncio.Semaphore(1))
    try:
        return await _run_cases(router, cases)
    finally:
        await router.close()


def _execution_summary(records: list[dict]) -> dict:
    return {
        "providerRequests": sum(record["providerRequestCount"] for record in records),
        "errors": sum(record["errorCode"] is not None for record in records),
        "tokens": {
            "input": sum(record["usage"]["inputTokens"] for record in records),
            "output": sum(record["usage"]["outputTokens"] for record in records),
            "total": sum(record["usage"]["totalTokens"] for record in records),
        },
        "durationMs": sum(record["durationMs"] for record in records),
    }


def _write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _generated_at() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(ROOT):
        parser.error("report output must be outside the repository")

    cases = load_cases(args.fixture)
    identity = fixture_checksum(args.fixture)
    problem = configuration_problem()
    if problem is not None:
        reason, fields = problem
        report = {
            "schemaVersion": "intent-routing-zh-evaluation-report.v1",
            "status": "BLOCKED",
            "scope": dict(REPORT_SCOPE),
            "reason": reason,
            "fields": fields,
            "fixtureChecksum": identity,
            "plannedCaseCount": len(cases),
            "generatedAt": _generated_at(),
        }
        _write_report(args.output, report)
        print(json.dumps(report, ensure_ascii=False))
        raise SystemExit(2)

    try:
        settings = IntentRouterSettings.from_environment()
    except (OSError, ValueError) as error:
        report = {
            "schemaVersion": "intent-routing-zh-evaluation-report.v1",
            "status": "BLOCKED",
            "scope": dict(REPORT_SCOPE),
            "reason": "INVALID_ROUTER_CONFIGURATION",
            "fields": [],
            "fixtureChecksum": identity,
            "plannedCaseCount": len(cases),
            "generatedAt": _generated_at(),
        }
        _write_report(args.output, report)
        print(json.dumps(report, ensure_ascii=False))
        raise SystemExit(2) from error

    records = asyncio.run(_run_configured(settings, cases))
    report = score_predictions(cases, records)
    report.update({
        "fixtureChecksum": identity,
        "generatedAt": _generated_at(),
        "configuration": {
            "provider": settings.provider,
            "protocol": settings.protocol,
            "model": settings.model,
            "contextWindowTokens": settings.context_window_tokens,
            "maxOutputTokens": settings.max_output_tokens,
        },
        "execution": _execution_summary(records),
    })
    _write_report(args.output, report)
    print(json.dumps({
        "status": report["status"],
        "counts": report["counts"],
        "accuracy": report["accuracy"],
        "fixtureChecksum": identity,
    }, ensure_ascii=False))
    raise SystemExit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
