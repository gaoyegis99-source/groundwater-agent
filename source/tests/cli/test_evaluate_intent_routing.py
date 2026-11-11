from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.evaluation import evaluate_intent_routing as evaluation


EXPECTED_CATEGORIES = {
    "ordinary_consult",
    "comparison",
    "execution",
    "execution_negated",
    "status_query",
    "workflow_draft",
    "attachment_read",
    "missing_field_clarification",
}


def _perfect_predictions(cases: list[dict]) -> list[dict]:
    return [
        {
            "caseId": case["caseId"],
            "responseKind": case["expected"]["responseKind"],
            "semanticIntent": case["expected"]["semanticIntent"],
        }
        for case in cases
    ]


def test_chinese_gold_fixture_freezes_required_categories_and_boundaries() -> None:
    cases = evaluation.load_cases()

    assert len(cases) == 24
    assert {case["category"] for case in cases} == EXPECTED_CATEGORIES
    assert all(
        sum(case["category"] == category for case in cases) == 3
        for category in EXPECTED_CATEGORIES
    )
    assert len({case["caseId"] for case in cases}) == 24
    assert all(any("\u4e00" <= character <= "\u9fff" for character in case["message"])
               for case in cases)

    by_id = {case["caseId"]: case for case in cases}
    assert by_id["negated-explain-first"]["expected"] == {
        "responseKind": "DIALOGUE",
        "semanticIntent": "CONSULT",
    }
    assert by_id["compare-previous-current-results"]["expected"] == {
        "responseKind": "DIALOGUE",
        "semanticIntent": "COMPARE",
    }
    assert by_id["attachment-list-columns"]["expected"] == {
        "responseKind": "DIALOGUE",
        "semanticIntent": "CONSULT",
    }
    assert by_id["missing-field-explicit-execution"]["expected"] == {
        "responseKind": "DIALOGUE",
        "semanticIntent": "EXECUTE_ON_DATA",
    }
    assert by_id["missing-field-explicit-workflow"]["expected"] == {
        "responseKind": "WORKFLOW_DRAFT",
        "semanticIntent": "CREATE_WORKFLOW",
    }
    assert by_id["missing-field-ambiguous-request"]["expected"] == {
        "responseKind": "NEEDS_CLARIFICATION",
        "semanticIntent": "UNKNOWN",
    }


def test_score_report_separates_pair_response_kind_and_semantic_accuracy() -> None:
    cases = evaluation.load_cases()
    predictions = _perfect_predictions(cases)
    predictions[0]["semanticIntent"] = "DESIGN"
    workflow_index = next(
        index
        for index, prediction in enumerate(predictions)
        if prediction["caseId"] == "workflow-reusable-trend"
    )
    predictions[workflow_index].update({
        "responseKind": "DIALOGUE",
        "semanticIntent": "CONSULT",
    })

    report = evaluation.score_predictions(cases, predictions)

    assert report["schemaVersion"] == "intent-routing-zh-evaluation-report.v1"
    assert report["status"] == "FAIL"
    assert report["scope"] == {
        "component": "MODEL_INTENT_ROUTER",
        "repetitionsPerCase": 1,
        "kernelOwnedRules": "EXCLUDED",
        "accuracyClaim": "FROZEN_CORPUS_ONLY",
    }
    assert report["counts"] == {
        "cases": 24,
        "exactPairCorrect": 22,
        "responseKindCorrect": 23,
        "semanticIntentCorrect": 22,
    }
    assert report["accuracy"] == {
        "exactPair": 22 / 24,
        "responseKind": 23 / 24,
        "semanticIntent": 22 / 24,
    }
    assert report["dimensions"]["responseKind"]["byExpected"][
        "WORKFLOW_DRAFT"
    ]["actual"]["DIALOGUE"] == 1
    assert report["dimensions"]["semanticIntent"]["byExpected"][
        "CONSULT"
    ]["actual"]["DESIGN"] == 1
    workflow = report["categories"]["workflow_draft"]
    assert workflow["exactPairCorrect"] == 2
    assert workflow["responseKindCorrect"] == 2
    assert workflow["semanticIntentCorrect"] == 2
    assert report["cases"][0]["match"] == {
        "exactPair": False,
        "responseKind": True,
        "semanticIntent": False,
    }
    assert not any(
        case["message"] in json.dumps(report, ensure_ascii=False)
        for case in cases
    )


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unknown"])
def test_score_report_requires_one_prediction_for_every_gold_case(mutation: str) -> None:
    cases = evaluation.load_cases()
    predictions = _perfect_predictions(cases)
    if mutation == "missing":
        predictions.pop()
    elif mutation == "duplicate":
        predictions[-1] = dict(predictions[0])
    else:
        predictions[-1]["caseId"] = "not-in-gold"

    with pytest.raises(ValueError, match="INTENT_ROUTING_PREDICTIONS_INVALID"):
        evaluation.score_predictions(cases, predictions)


def test_load_cases_rejects_malformed_or_incomplete_fixture(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.json"
    malformed.write_text(json.dumps({
        "schemaVersion": "intent-routing-zh-gold.v1",
        "cases": [{
            "caseId": "only-one",
            "category": "ordinary_consult",
            "message": "请解释。",
            "hasInputReferences": False,
            "expected": {
                "responseKind": "DIALOGUE",
                "semanticIntent": "CONSULT",
            },
        }],
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="INTENT_ROUTING_GOLD_INVALID"):
        evaluation.load_cases(malformed)


class _RecordingRouter:
    def __init__(self, expected: dict[str, dict]) -> None:
        self.expected = expected
        self.calls: list[dict] = []

    async def route(self, **call):
        self.calls.append(call)
        case_id = call["request_agent_run_id"].removeprefix("intent-gold-")
        expected = self.expected[case_id]
        return SimpleNamespace(
            intent=SimpleNamespace(
                response_kind=expected["responseKind"],
                semantic_intent=expected["semanticIntent"],
            ),
            provider_request_count=1,
            usage=SimpleNamespace(input_tokens=10, output_tokens=2, total_tokens=12),
            duration_ms=5,
        )


def test_run_cases_uses_real_router_boundary_and_attachment_context() -> None:
    cases = evaluation.load_cases()
    router = _RecordingRouter({case["caseId"]: case["expected"] for case in cases})

    records = evaluation.run_cases(router, cases)

    assert len(records) == len(router.calls) == 24
    assert all(record["errorCode"] is None for record in records)
    assert sum(
        bool(call["candidate_context"]["input_references"])
        for call in router.calls
    ) == sum(case["hasInputReferences"] for case in cases)
    assert all(call["memory"] is None and call["history"] == () for call in router.calls)
    assert [call["message"] for call in router.calls] == [
        case["message"] for case in cases
    ]
    report = evaluation.score_predictions(cases, records)
    assert report["status"] == "PASS"
    assert set(report["accuracy"].values()) == {1.0}


def test_cli_without_router_configuration_writes_blocked_external_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in evaluation.REQUIRED_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    output = tmp_path / "intent-routing-report.json"
    monkeypatch.setattr(
        "sys.argv",
        ["evaluate_intent_routing.py", "--output", str(output)],
    )

    with pytest.raises(SystemExit) as stopped:
        evaluation.main()

    assert stopped.value.code == 2
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["schemaVersion"] == "intent-routing-zh-evaluation-report.v1"
    assert report["status"] == "BLOCKED"
    assert report["reason"] == "MISSING_ROUTER_CONFIGURATION"
    assert report["fields"] == list(evaluation.REQUIRED_ENVIRONMENT)
    assert report["fixtureChecksum"].startswith("sha256:")
