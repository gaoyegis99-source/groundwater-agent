"""Automatic summaries, review binding, and provider report combination."""
from __future__ import annotations

from collections import defaultdict
from statistics import median

from .definitions import (
    AUTOMATIC_GATES,
    EXPECTED_DOMAIN_PACKAGE,
    EXPECTED_MODEL_IDS,
    REPETITIONS,
    REVIEW_DIMENSIONS,
    SUPPORTED_PROVIDERS,
    _canonical_json,
    _sha256,
)
from .identity import (
    configuration_identity,
    data_fixture_checksum,
    fixture_checksum,
    load_cases,
)

def _expected_case_keys(cases: list[dict]) -> set[tuple[str, int]]:
    return {
        (case["case_id"], iteration)
        for case in cases
        for iteration in range(1, REPETITIONS + 1)
    }


def _binding_context(report: dict) -> dict:
    return {
        "configuration": report["configuration"],
        "fixture_checksum": report["fixture_checksum"],
        "data_fixture_checksum": report["data_fixture_checksum"],
        "release_identity": report["release_identity"],
    }


def _response_id(context: dict, record: dict) -> str:
    return _sha256(_canonical_json({
        "schemaVersion": "reply-quality-response-binding.v1",
        **context,
        "case_id": record["case_id"],
        "category": record["category"],
        "iteration": record["iteration"],
        "provider": record["provider"],
        "model": record["model"],
        "session_id": record["session_id"],
        "agent_run_id": record["agent_run_id"],
        "review_content": record["review_content"],
    }))


def _record_id(record: dict) -> str:
    return f"{record.get('case_id', '?')}#{record.get('iteration', '?')}"


def _gate(
    *, expected: int, assessed: int, failure_ids: list[str],
) -> dict:
    failures = list(dict.fromkeys(failure_ids))
    status = (
        "FAIL" if failures
        else "PASS" if assessed == expected
        else "NOT_ASSESSED"
    )
    return {
        "status": status,
        "expected": expected,
        "assessed": assessed,
        "violations": len(failures),
        "failure_ids": failures,
    }


def _assertion_gate(records: list[dict], name: str, planned: int) -> dict:
    assessed_records = [
        record for record in records
        if type(record.get("assertions", {}).get(name)) is bool
    ]
    return _gate(
        expected=planned,
        assessed=len(assessed_records),
        failure_ids=[
            _record_id(record) for record in assessed_records
            if record["assertions"][name] is False
        ],
    )


def _provider_request_policy_valid(items: object, configuration: dict) -> bool:
    if not isinstance(items, list) or not items:
        return False
    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        if not isinstance(item, dict) or any(
            item.get(key) != configuration[key]
            for key in ("provider", "model", "protocol")
        ) or item.get("attempt") not in {1, 2}:
            return False
        stage = item.get("stage")
        if not isinstance(stage, str) or not stage:
            return False
        grouped[stage].append(item)
    for attempts in grouped.values():
        attempts.sort(key=lambda item: item["attempt"])
        if [item["attempt"] for item in attempts] not in ([1], [1, 2]):
            return False
        if len(attempts) == 2 and (
            attempts[0].get("outcome") != "FAILED"
            or attempts[0].get("error_class")
            != "PROTOCOL_OR_SCHEMA_INVALID"
        ):
            return False
    return True


def summarize(
    records: list[dict], *, cases: list[dict], release_identity: dict,
) -> dict:
    planned = len(cases) * REPETITIONS
    configuration = configuration_identity()
    context = {
        "configuration": configuration,
        "fixture_checksum": fixture_checksum(),
        "data_fixture_checksum": data_fixture_checksum(),
        "release_identity": release_identity,
    }
    bound_records = []
    for source in records:
        record = dict(source)
        if all(key in record for key in (
            "provider", "model", "review_content", "case_id", "category",
            "iteration", "session_id", "agent_run_id",
        )):
            record["response_id"] = _response_id(context, record)
        bound_records.append(record)
    records = bound_records
    passed = [record for record in records if record["status"] == "PASS"]
    assertion_names = (
        "final_reply_structurally_valid",
        "configured_provider_used_without_fallback",
        "provider_retry_policy_valid",
        "conclusion_within_two_sentences",
        "details_within_target",
        "duplicate_sources_absent",
        "technical_identifier_leak_absent",
        "read_only_side_effect_absent",
        "known_fact_error_absent",
        "generic_continuation_absent",
        "dialogue_policy_exact",
        "interaction_option_contract_valid",
        "option_auto_followup_exact",
        "scenario_oracle_passed",
    )
    counts = {
        name: sum(
            record.get("assertions", {}).get(name) is True for record in records
        )
        for name in assertion_names
    }
    expected_keys = _expected_case_keys(cases)
    expected_categories = {
        case["case_id"]: case["category"] for case in cases
    }
    actual_keys = [
        (record.get("case_id"), record.get("iteration")) for record in records
    ]
    session_ids = [record.get("session_id") for record in records]
    agent_run_ids = [record.get("agent_run_id") for record in records]
    matrix_failures = [
        _record_id(record) for record in records
        if (record.get("case_id"), record.get("iteration")) not in expected_keys
        or record.get("category")
        != expected_categories.get(record.get("case_id"))
        or actual_keys.count((record.get("case_id"), record.get("iteration"))) > 1
    ]
    matrix_failures.extend(
        f"missing:{case_id}#{iteration}"
        for case_id, iteration in sorted(expected_keys - set(actual_keys))
    )
    identity_records = [
        record for record in records
        if isinstance(record.get("session_id"), str) and record["session_id"]
        and isinstance(record.get("agent_run_id"), str)
        and record["agent_run_id"]
    ]
    identity_failures = [
        _record_id(record) for record in identity_records
        if session_ids.count(record["session_id"]) > 1
        or agent_run_ids.count(record["agent_run_id"]) > 1
    ]
    provider_records = [
        record for record in records
        if isinstance(record.get("provider"), str)
        and isinstance(record.get("model"), str)
    ]
    provider_request_records = [
        record for record in records
        if isinstance(record.get("provider_requests"), list)
        and record["provider_requests"]
    ]
    response_records = [
        record for record in records
        if isinstance(record.get("response_id"), str)
    ]
    response_ids = [record["response_id"] for record in response_records]
    gates = {
        "run_count_24": _gate(
            expected=24, assessed=len(records),
            failure_ids=[] if len(records) == planned == 24 else ["report"],
        ),
        "case_iteration_matrix_exact": _gate(
            expected=24, assessed=len(actual_keys), failure_ids=matrix_failures,
        ),
        "session_and_agent_runs_unique_24": _gate(
            expected=24, assessed=len(identity_records),
            failure_ids=identity_failures,
        ),
        "valid_final_reply_24_of_24": _assertion_gate(
            records, "final_reply_structurally_valid", planned,
        ),
        "provider_fallbacks_zero": _gate(
            expected=24, assessed=len(provider_records),
            failure_ids=[
                _record_id(record) for record in provider_records
                if record["provider"] != configuration["provider"]
                or record["model"] != configuration["model"]
            ],
        ),
        "provider_retry_policy_24": _gate(
            expected=24, assessed=len(provider_request_records),
            failure_ids=[
                _record_id(record) for record in provider_request_records
                if not _provider_request_policy_valid(
                    record["provider_requests"], configuration,
                )
            ],
        ),
        "all_automatic_checks_pass": _gate(
            expected=24, assessed=len(records),
            failure_ids=[
                _record_id(record) for record in records
                if record.get("status") != "PASS"
            ],
        ),
        "technical_identifier_leaks_zero": _assertion_gate(
            records, "technical_identifier_leak_absent", planned,
        ),
        "duplicate_sources_zero": _assertion_gate(
            records, "duplicate_sources_absent", planned,
        ),
        "read_only_side_effects_zero": _assertion_gate(
            records, "read_only_side_effect_absent", planned,
        ),
        "known_fact_errors_zero": _assertion_gate(
            records, "known_fact_error_absent", planned,
        ),
        "generic_continuation_zero": _assertion_gate(
            records, "generic_continuation_absent", planned,
        ),
        "dialogue_policy_exact_24": _assertion_gate(
            records, "dialogue_policy_exact", planned,
        ),
        "interaction_option_contracts_24": _assertion_gate(
            records, "interaction_option_contract_valid", planned,
        ),
        "option_auto_followups_exact": _assertion_gate(
            records, "option_auto_followup_exact", planned,
        ),
        "scenario_oracles_24_of_24": _assertion_gate(
            records, "scenario_oracle_passed", planned,
        ),
        "response_bindings_24": _gate(
            expected=24, assessed=len(response_records),
            failure_ids=[
                _record_id(record) for record in response_records
                if not record["response_id"].startswith("sha256:")
                or response_ids.count(record["response_id"]) > 1
            ],
        ),
    }
    return {
        "schemaVersion": "reply-quality-evaluation-report.v2",
        "status": (
            "AWAITING_HUMAN_REVIEW"
            if all(gate["status"] == "PASS" for gate in gates.values())
            else "FAILED"
        ),
        **context,
        "planned_run_count": planned,
        "run_count": len(records),
        "pass_count": len(passed),
        "automatic_counts": counts,
        "automatic_gates": gates,
        "runs": records,
}


def _validate_bound_report(report: dict, *, statuses: set[str]) -> None:
    gates = report.get("automatic_gates")
    release = report.get("release_identity", {})
    release_models = release.get("models", [])
    if (
        report.get("schemaVersion") not in {
            "reply-quality-evaluation-report.v2",
            "reply-quality-final-report.v2",
        }
        or report.get("status") not in statuses
        or report.get("fixture_checksum") != fixture_checksum()
        or report.get("data_fixture_checksum") != data_fixture_checksum()
        or report.get("configuration", {}).get("provider")
        not in SUPPORTED_PROVIDERS
        or release.get("domain_package") != EXPECTED_DOMAIN_PACKAGE
        or not isinstance(release_models, list)
        or len(release_models) != len(EXPECTED_MODEL_IDS)
        or {
            item.get("modelId")
            for item in release_models
        }
        != EXPECTED_MODEL_IDS
        or release.get("model_identity_checksum")
        != _sha256(_canonical_json(release_models))
        or set(release.get("execution_contracts", {}))
        != {item.get("tool") for item in release_models}
        or set(release.get("draft_operators", {}))
        != {"table.sort", "statistics.describe"}
        or not isinstance(gates, dict)
        or set(gates) != AUTOMATIC_GATES
        or any(
            not isinstance(gate, dict)
            or set(gate) != {
                "status", "expected", "assessed", "violations", "failure_ids",
            }
            or gate["status"] != "PASS"
            or type(gate["expected"]) is not int
            or type(gate["assessed"]) is not int
            or type(gate["violations"]) is not int
            or not isinstance(gate["failure_ids"], list)
            for gate in gates.values()
        )
        or report.get("planned_run_count") != 24
        or report.get("run_count") != 24
        or report.get("pass_count") != 24
    ):
        raise ValueError("REPLY_QUALITY_REPORT_NOT_FINALIZABLE")
    runs = report.get("runs")
    if not isinstance(runs, list) or len(runs) != 24:
        raise ValueError("REPLY_QUALITY_REPORT_BINDING_INVALID")
    expected_keys = _expected_case_keys(load_cases())
    expected_categories = {
        case["case_id"]: case["category"] for case in load_cases()
    }
    actual_keys = [(item.get("case_id"), item.get("iteration")) for item in runs]
    if (
        len(actual_keys) != len(set(actual_keys))
        or set(actual_keys) != expected_keys
        or any(item.get("status") != "PASS" for item in runs)
        or any(
            item.get("category") != expected_categories.get(item.get("case_id"))
            or item.get("provider") != report["configuration"]["provider"]
            or item.get("model") != report["configuration"]["model"]
            for item in runs
        )
        or any(
            item.get("response_id")
            != _response_id(_binding_context(report), item)
            for item in runs
        )
        or len({item["response_id"] for item in runs}) != 24
        or len({item.get("session_id") for item in runs}) != 24
        or len({item.get("agent_run_id") for item in runs}) != 24
    ):
        raise ValueError("REPLY_QUALITY_REPORT_BINDING_INVALID")


def finalize_report(report: dict, reviews: dict) -> dict:
    if report.get("schemaVersion") != "reply-quality-evaluation-report.v2":
        raise ValueError("REPLY_QUALITY_REPORT_NOT_FINALIZABLE")
    _validate_bound_report(report, statuses={"AWAITING_HUMAN_REVIEW"})
    if (
        not isinstance(reviews, dict)
        or set(reviews) != {"schemaVersion", "bundle_id", "reviews"}
        or reviews["schemaVersion"] != "reply-quality-human-review.v1"
        or reviews["bundle_id"] != blind_review_bundle(report)["bundle_id"]
        or not isinstance(reviews["reviews"], list)
    ):
        raise ValueError("REPLY_QUALITY_REVIEWS_INVALID")
    expected = {
        (record["case_id"], record["iteration"]): record["response_id"]
        for record in report["runs"]
    }
    grouped: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for review in reviews["reviews"]:
        if set(review) != {
            "case_id", "iteration", "response_id", "reviewer_id",
            *REVIEW_DIMENSIONS,
        }:
            raise ValueError("REPLY_QUALITY_REVIEWS_INVALID")
        key = (review["case_id"], review["iteration"])
        if (
            key not in expected
            or review["response_id"] != expected[key]
            or not isinstance(review["reviewer_id"], str)
            or not review["reviewer_id"]
            or any(type(review[name]) is not int or not 1 <= review[name] <= 5
                   for name in REVIEW_DIMENSIONS)
        ):
            raise ValueError("REPLY_QUALITY_REVIEWS_INVALID")
        grouped[key].append(review)
    reviewer_ids = {
        review["reviewer_id"] for review in reviews["reviews"]
    }
    if (
        len(reviews["reviews"]) != 48
        or set(grouped) != set(expected)
        or len(reviewer_ids) != 2
        or any(
            len(items) != 2
            or {item["reviewer_id"] for item in items} != reviewer_ids
            for items in grouped.values()
        )
    ):
        raise ValueError("REPLY_QUALITY_TWO_REVIEWERS_REQUIRED")
    scores = {
        name: [review[name] for items in grouped.values() for review in items]
        for name in REVIEW_DIMENSIONS
    }
    medians = {name: median(values) for name, values in scores.items()}
    human_gates = {
        "two_reviewers_per_reply": True,
        "all_scores_at_least_3": all(
            score >= 3 for values in scores.values() for score in values
        ),
        **{
            f"{name}_median_at_least_4": value >= 4
            for name, value in medians.items()
        },
    }
    return {
        **report,
        "schemaVersion": "reply-quality-final-report.v2",
        "status": "PASS" if all(human_gates.values()) else "FAILED",
        "human_review": {
            "review_schema": reviews["schemaVersion"],
            "review_count": len(reviews["reviews"]),
            "reviewer_ids": sorted(reviewer_ids),
            "review_bindings_checksum": _sha256(_canonical_json(reviews)),
            "dimension_medians": medians,
            "gates": human_gates,
        },
    }


def blind_review_bundle(report: dict) -> dict:
    try:
        _validate_bound_report(report, statuses={"AWAITING_HUMAN_REVIEW"})
    except ValueError as error:
        raise ValueError("REPLY_QUALITY_REPORT_NOT_REVIEWABLE") from error
    responses = [
        {
            "case_id": record["case_id"],
            "iteration": record["iteration"],
            "category": record["category"],
            "response_id": record["response_id"],
            "content": record["review_content"],
        }
        for record in report["runs"]
    ]
    bundle_identity = {
        "fixture_checksum": report["fixture_checksum"],
        "data_fixture_checksum": report["data_fixture_checksum"],
        "release_identity_checksum": report["release_identity"][
            "model_identity_checksum"
        ],
        "response_ids": [item["response_id"] for item in responses],
    }
    return {
        "schemaVersion": "reply-quality-blind-review.v1",
        "fixture_checksum": report["fixture_checksum"],
        "data_fixture_checksum": report["data_fixture_checksum"],
        "release_identity_checksum": report["release_identity"][
            "model_identity_checksum"
        ],
        "bundle_id": _sha256(_canonical_json(bundle_identity)),
        "dimensions": list(REVIEW_DIMENSIONS),
        "review_note": (
            "For replies without options, score option dimensions by whether omitting "
            "options is appropriate. For replies with options, assess relevance to the "
            "question, distinction or mutual exclusivity, natural-language actionability, "
            "and whether any option implies bypassing confirmation."
        ),
        "responses": responses,
    }


def combine_reports(reports: list[dict]) -> dict:
    if len(reports) != 2:
        raise ValueError("REPLY_QUALITY_TWO_PROVIDER_REPORTS_REQUIRED")
    for report in reports:
        if report.get("schemaVersion") != "reply-quality-final-report.v2":
            raise ValueError("REPLY_QUALITY_FINAL_REPORT_REQUIRED")
        _validate_bound_report(report, statuses={"PASS"})
        human = report.get("human_review", {})
        if (
            human.get("review_count") != 48
            or len(human.get("reviewer_ids", [])) != 2
            or not all(human.get("gates", {}).values())
        ):
            raise ValueError("REPLY_QUALITY_HUMAN_GATE_NOT_PASSED")
    by_provider = {
        report["configuration"]["provider"]: report for report in reports
    }
    if set(by_provider) != SUPPORTED_PROVIDERS:
        raise ValueError("REPLY_QUALITY_PROVIDER_PAIR_INVALID")
    minimax = by_provider["minimax"]
    deepseek = by_provider["deepseek"]
    for field in (
        "fixture_checksum", "data_fixture_checksum", "release_identity",
    ):
        if minimax[field] != deepseek[field]:
            raise ValueError("REPLY_QUALITY_PROVIDER_IDENTITY_MISMATCH")
    if (
        minimax["human_review"]["reviewer_ids"]
        != deepseek["human_review"]["reviewer_ids"]
    ):
        raise ValueError("REPLY_QUALITY_REVIEWER_PAIR_MISMATCH")
    response_ids = [
        {record["response_id"] for record in report["runs"]}
        for report in reports
    ]
    if response_ids[0] & response_ids[1]:
        raise ValueError("REPLY_QUALITY_CROSS_PROVIDER_BINDING_COLLISION")
    return {
        "schemaVersion": "reply-quality-combined-report.v2",
        "status": "PASS",
        "fixture_checksum": minimax["fixture_checksum"],
        "data_fixture_checksum": minimax["data_fixture_checksum"],
        "release_identity": minimax["release_identity"],
        "reviewer_ids": minimax["human_review"]["reviewer_ids"],
        "providers": {
            provider: {
                "status": report["status"],
                "model": report["configuration"]["model"],
                "run_count": report["run_count"],
                "review_count": report["human_review"]["review_count"],
                "dimension_medians": report["human_review"][
                    "dimension_medians"
                ],
                "response_binding_checksum": _sha256(_canonical_json(
                    [record["response_id"] for record in report["runs"]]
                )),
            }
            for provider, report in sorted(by_provider.items())
        },
    }
