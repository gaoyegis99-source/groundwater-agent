from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

from scripts.development.product_selection import load_selected_product_by_id

from scripts.evaluation import evaluate_reply_quality as quality
from scripts.evaluation.reply_quality import (
    assessment,
    definitions,
    identity,
    reports,
    scenarios,
)


def _environment(monkeypatch, provider: str = "minimax") -> None:
    values = {
        "PLATFORM_AGENT_PROVIDER": provider,
        "PLATFORM_AGENT_PROTOCOL": "chat_completions",
        "PLATFORM_AGENT_BASE_URL": "https://provider.invalid/v1",
        "PLATFORM_AGENT_MODEL": f"{provider}-test-model",
        "PLATFORM_AGENT_API_KEY_FILE": "/run/secrets/test",
        "PLATFORM_AGENT_CONTEXT_WINDOW_TOKENS": "131072",
        "PLATFORM_AGENT_MAX_OUTPUT_TOKENS": "4096",
        "PLATFORM_AGENT_REQUEST_TIMEOUT_SECONDS": "120",
        "PLATFORM_AGENT_SEGMENT_TIMEOUT_SECONDS": "300",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_frozen_reply_quality_identity_matches_current_product() -> None:
    root = Path(__file__).resolve().parents[2]
    selected = load_selected_product_by_id(root, "groundwater")

    assert selected.manifest["domainPackage"] == definitions.EXPECTED_DOMAIN_PACKAGE
    assert {
        selected.domain.definitions[path]["modelId"]
        for path in selected.domain.manifest["executableModels"]
    } == definitions.EXPECTED_MODEL_IDS


def _release_identity() -> dict:
    identities = (
        ("groundwater.summary", "run_summary"),
        ("groundwater.monitoring-trend", "run_monitoring_trend"),
        ("groundwater.monitoring-coverage", "run_monitoring_coverage"),
        ("groundwater.flow-field", "run_flow_field"),
        (
            "groundwater.transfer-learning-analysis",
            "run_transfer_learning_analysis",
        ),
        ("groundwater.render-map", "render_map"),
        (
            "groundwater.source-sustainability",
            "run_source_sustainability",
        ),
        ("groundwater.kriging", "run_kriging"),
        ("groundwater.rule-classification", "run_rule_classification"),
        ("groundwater.drastic", "run_drastic"),
        ("groundwater.porous-agricultural-inversion", "run_porous_agri_inversion"),
        ("groundwater.porous-calibration", "run_porous_pest_calibration"),
        ("groundwater.porous-inversion-sampling", "run_porous_inversion_sample"),
        ("groundwater.porous-inversion-training", "run_porous_inversion_train"),
        ("groundwater.porous-map", "run_porous_map_prepare"),
        ("groundwater.porous-scenario-comparison", "run_porous_scenario_compare"),
        ("groundwater.porous-simulation", "run_porous_simulation"),
        ("groundwater.sustainability-input-preparation", "prepare_source_sustainability"),
    )
    models = sorted(
        (
            {
                "modelId": model_id,
                "modelVersion": "1.0.0",
                "modelChecksum": "sha256:" + f"{index:064x}",
                "workflowId": model_id,
                "workflowVersion": "1.0.0",
                "workflowChecksum": "sha256:" + f"{index + 10:064x}",
                "tool": tool,
            }
            for index, (model_id, tool) in enumerate(identities, 1)
        ),
        key=lambda x: x["modelId"],
    )
    outputs = {
        tool: (["trend-result"] if tool == "run_monitoring_trend" else ["result"])
        for _, tool in identities
    }
    contracts = {
        tool: {
            "inputSchemaChecksum": "sha256:" + f"{index + 20:064x}",
            "defaultParameters": (
                {"alpha": 0.05, "threshold": 3.5}
                if tool == "run_monitoring_trend"
                else {}
            ),
            "executionService": "executor",
            "executorId": "workflow-dag",
            "finalOutputs": outputs[tool],
        }
        for index, (_, tool) in enumerate(identities, 1)
    }
    return {
        "domain_package": dict(definitions.EXPECTED_DOMAIN_PACKAGE),
        "assembly_manifest_checksum": "sha256:" + "a" * 64,
        "assembly_lock_checksum": "sha256:" + "b" * 64,
        "models": models,
        "model_identity_checksum": definitions._sha256(
            definitions._canonical_json(models)
        ),
        "final_outputs": outputs,
        "execution_contracts": contracts,
        "operator_catalog_digest": "sha256:" + "c" * 64,
        "draft_operators": {
            "table.sort": {
                "version": "1.0.0",
                "implementationRevision": "sha256:" + "d" * 64,
            },
            "statistics.describe": {
                "version": "1.0.0",
                "implementationRevision": "sha256:" + "e" * 64,
            },
        },
    }


def _ordinary_output(text: str = "地下水水位是水面相对基准面的高程。") -> dict:
    presentation = {
        "conclusion": {"markdown": text, "source_indexes": []},
        "details": [],
        "execution_summary": None,
        "unresolved_items": [],
        "sources": [],
    }
    return {
        "message": text,
        "presentation": presentation,
        "workflow_runs": [],
        "unresolved_items": [],
        "interaction_options": [],
    }


def _context(
    output: dict,
    *,
    dialogue_policy: str | None = "READ_ONLY",
) -> tuple[dict, dict, dict]:
    session = {
        "confirmation": None,
        "workflow_run_ids": [],
        "remote_outputs": [],
        "workflow_draft_id": None,
    }
    turn = {
        "status": "COMPLETE",
        "resolved_mode": "DIALOGUE",
        "dialogue_policy": dialogue_policy,
        "intent_decision": None,
        "clarification_required": False,
        "confirmations": [],
        "execution_tasks": [],
        "reply": output["presentation"],
    }
    run = {
        "status": "COMPLETE",
        "error_code": None,
        "provider": "minimax",
        "model": "minimax-test-model",
    }
    return session, turn, run


def _assertions() -> dict[str, bool]:
    return {
        "final_reply_structurally_valid": True,
        "configured_provider_used_without_fallback": True,
        "provider_retry_policy_valid": True,
        "conclusion_within_two_sentences": True,
        "details_within_target": True,
        "duplicate_sources_absent": True,
        "technical_identifier_leak_absent": True,
        "read_only_side_effect_absent": True,
        "known_fact_error_absent": True,
        "generic_continuation_absent": True,
        "dialogue_policy_exact": True,
        "interaction_option_contract_valid": True,
        "option_auto_followup_exact": True,
        "scenario_oracle_passed": True,
    }


def _records(provider: str = "minimax") -> list[dict]:
    return [
        {
            "case_id": case["case_id"],
            "category": case["category"],
            "iteration": iteration,
            "status": "PASS",
            "session_id": f"session-{case['case_id']}-{iteration}",
            "agent_run_id": f"run-{case['case_id']}-{iteration}",
            "provider": provider,
            "model": f"{provider}-test-model",
            "provider_requests": [
                {
                    "stage": "AGENT_INITIAL:1",
                    "attempt": 1,
                    "provider": provider,
                    "model": f"{provider}-test-model",
                    "protocol": "chat_completions",
                    "outcome": "SUCCEEDED",
                    "error_class": None,
                }
            ],
            "assertions": _assertions(),
            "review_content": {
                "question": case["message"],
                "conclusion": "匿名回复",
                "details": [],
                "execution_summary": None,
                "unresolved_items": [],
                "sources": [],
                "interaction_options": [],
                "business_card": None,
            },
        }
        for case in identity.load_cases()
        for iteration in range(1, 4)
    ]


def _report(monkeypatch, provider: str = "minimax") -> dict:
    _environment(monkeypatch, provider)
    return reports.summarize(
        _records(provider),
        cases=identity.load_cases(),
        release_identity=_release_identity(),
    )


def _reviews(report: dict) -> dict:
    bundle = reports.blind_review_bundle(report)
    return {
        "schemaVersion": "reply-quality-human-review.v1",
        "bundle_id": bundle["bundle_id"],
        "reviews": [
            {
                "case_id": record["case_id"],
                "iteration": record["iteration"],
                "response_id": record["response_id"],
                "reviewer_id": reviewer,
                **{name: 4 for name in definitions.REVIEW_DIMENSIONS},
            }
            for record in report["runs"]
            for reviewer in ("reviewer-a", "reviewer-b")
        ],
    }


def test_fixture_freezes_eight_categories_and_twenty_four_runs() -> None:
    cases = identity.load_cases()
    assert len(cases) == 8
    assert len({case["category"] for case in cases}) == 8
    assert len(cases) * definitions.REPETITIONS == 24
    assert identity.fixture_checksum().startswith("sha256:")
    assert (
        identity.data_fixture_checksum() == definitions.EXPECTED_DATA_FIXTURE_CHECKSUM
    )


def test_assessment_accepts_concise_structured_reply(monkeypatch) -> None:
    _environment(monkeypatch)
    case = identity.load_cases()[0]
    output = _ordinary_output()
    session, turn, run = _context(output)

    assertions, review = assessment.assess_reply(
        case=case,
        output=output,
        session=session,
        turn=turn,
        run=run,
        function_names=["submit_reply"],
        card_context=None,
        execution_outcome=None,
    )

    assert all(assertions.values())
    assert review["conclusion"] == output["presentation"]["conclusion"]["markdown"]
    assert review["sources"] == []


def test_assessment_rejects_visible_internal_identity(monkeypatch) -> None:
    _environment(monkeypatch)
    case = identity.load_cases()[0]
    output = _ordinary_output("地下水结论 checksum sha256:" + "a" * 64)
    session, turn, run = _context(output)

    with pytest.raises(AssertionError, match="TECHNICAL_IDENTIFIER_LEAK_ABSENT"):
        assessment.assess_reply(
            case=case,
            output=output,
            session=session,
            turn=turn,
            run=run,
            function_names=["submit_reply"],
            card_context=None,
            execution_outcome=None,
        )


def test_assessment_rejects_message_that_does_not_match_presentation(
    monkeypatch,
) -> None:
    _environment(monkeypatch)
    case = identity.load_cases()[0]
    output = _ordinary_output()
    output["message"] = "重复或遗漏的线性正文。"
    session, turn, run = _context(output)

    with pytest.raises(AssertionError, match="LINEAR_MESSAGE_MISMATCH"):
        assessment.assess_reply(
            case=case,
            output=output,
            session=session,
            turn=turn,
            run=run,
            function_names=["submit_reply"],
            card_context=None,
            execution_outcome=None,
        )


def test_assessment_enforces_options_required_all_and_option_leaks(monkeypatch) -> None:
    _environment(monkeypatch)
    case = next(
        item
        for item in identity.load_cases()
        if item["category"] == "method_comparison"
    )
    output = _ordinary_output("趋势分析适合时间变化。")
    session, turn, run = _context(output)
    with pytest.raises(AssertionError, match="KNOWN_FACT_ERROR_ABSENT"):
        assessment.assess_reply(
            case=case,
            output=output,
            session=session,
            turn=turn,
            run=run,
            function_names=["submit_reply"],
            card_context=None,
            execution_outcome=None,
        )

    output = _ordinary_output("趋势用于时间变化，流场用于空间方向。")
    output["interaction_options"] = [
        {
            "label": "submit_reply",
            "description": "技术调用",
            "message": "继续比较",
        }
    ]
    session, turn, run = _context(output)
    with pytest.raises(AssertionError, match="TECHNICAL_IDENTIFIER_LEAK_ABSENT"):
        assessment.assess_reply(
            case=case,
            output=output,
            session=session,
            turn=turn,
            run=run,
            function_names=["submit_reply"],
            card_context=None,
            execution_outcome=None,
        )

    direct = identity.load_cases()[0]
    with pytest.raises(AssertionError, match="INTERACTION_OPTIONS_INVALID"):
        assessment.assess_reply(
            case=direct,
            output=output,
            session=session,
            turn=turn,
            run=run,
            function_names=["submit_reply"],
            card_context=None,
            execution_outcome=None,
        )


def test_assessment_matches_authoritative_execution_output_summary(monkeypatch) -> None:
    _environment(monkeypatch)
    case = next(
        item
        for item in identity.load_cases()
        if item["category"] == "execution_success"
    )
    summary = "已完成 1 项分析；生成 1 份结果，可在分析结果中查看或下载。"
    output = {
        "message": summary,
        "presentation": {
            "conclusion": None,
            "details": [],
            "execution_summary": {
                "markdown": summary,
                "source_indexes": [],
            },
            "unresolved_items": [],
            "sources": [],
        },
        "workflow_runs": ["opaque-run"],
        "unresolved_items": [],
        "interaction_options": [],
    }
    session, turn, run = _context(
        output,
        dialogue_policy="EXECUTION_PROPOSAL",
    )

    assertions, review = assessment.assess_reply(
        case=case,
        output=output,
        session=session,
        turn=turn,
        run=run,
        function_names=["submit_reply"],
        card_context={
            "kind": "execution",
            "analysis": "监测趋势分析",
            "status": "执行成功",
            "results": "已发布 1 项结果",
        },
        execution_outcome="SUCCEEDED",
        execution_output_count=1,
    )

    assert all(assertions.values())
    assert review["execution_summary"] == summary


def test_assessment_allows_explanation_before_authoritative_execution_summary(
    monkeypatch,
) -> None:
    _environment(monkeypatch)
    case = next(
        item
        for item in identity.load_cases()
        if item["category"] == "execution_success"
    )
    detail = "本次完成迁移学习回测，结果仅用于开发调试。"
    summary = "已完成 1 项分析；生成 1 份结果，可在分析结果中查看或下载。"
    output = {
        "message": f"{detail}\n\n{summary}",
        "presentation": {
            "conclusion": None,
            "details": [{"markdown": detail, "source_indexes": []}],
            "execution_summary": {
                "markdown": summary,
                "source_indexes": [],
            },
            "unresolved_items": [],
            "sources": [],
        },
        "workflow_runs": ["opaque-run"],
        "unresolved_items": [],
        "interaction_options": [],
    }
    session, turn, run = _context(
        output,
        dialogue_policy="EXECUTION_PROPOSAL",
    )

    assertions, review = assessment.assess_reply(
        case=case,
        output=output,
        session=session,
        turn=turn,
        run=run,
        function_names=["submit_reply"],
        card_context={
            "kind": "execution",
            "analysis": "监测趋势分析",
            "status": "执行成功",
            "results": "已发布 1 项结果",
        },
        execution_outcome="SUCCEEDED",
        execution_output_count=1,
    )

    assert all(assertions.values())
    assert review["details"] == [detail]
    assert review["execution_summary"] == summary


def test_confirmation_requires_exact_release_identity_and_arguments() -> None:
    release = _release_identity()
    model = scenarios._release_model(release, "run_monitoring_trend")
    contract = release["execution_contracts"]["run_monitoring_trend"]
    resource = {
        "kind": "data_version",
        "service": "data-primary",
        "project_id": "groundwater-demo",
        "dataset_id": "dataset-1",
        "version_id": "version-1",
        "semantic_id": "semantic-1",
        "object_id": "object-1",
        "media_type": "text/csv",
        "format": "csv",
        "size_bytes": 12,
        "checksum": "sha256:" + "a" * 64,
    }
    asset = {
        "resource": resource,
        "analyses": {
            "run_monitoring_trend": {
                "bindings": {
                    "groupField": "井号",
                    "timeField": "观测日",
                    "valueField": "水头",
                },
                "parameters": {"frequency": "month"},
            },
        },
    }
    session = {
        "goal_revision": 1,
        "workflow_run_ids": [],
        "remote_outputs": [],
    }
    action = {
        "tool_call_id": "call-1",
        "tool": model["tool"],
        "model_id": model["modelId"],
        "model_version": model["modelVersion"],
        "model_checksum": model["modelChecksum"],
        "workflow_id": model["workflowId"],
        "workflow_version": model["workflowVersion"],
        "workflow_checksum": model["workflowChecksum"],
        "input_schema_checksum": contract["inputSchemaChecksum"],
        "fingerprint": "f" * 64,
        "input_references": {
            "versionId": {
                key: value for key, value in resource.items() if key != "project_id"
            },
        },
        "arguments": {
            "alpha": 0.05,
            "threshold": 3.5,
            "versionId": "version-1",
            "groupField": "井号",
            "timeField": "观测日",
            "valueField": "水头",
            "frequency": "month",
        },
    }
    confirmation = {
        "status": "PENDING",
        "revision": 1,
        "kind": "INITIAL",
        "replaces_confirmation_id": None,
        "source_workflow_run_id": None,
        "goal_revision": 1,
        "agent_run_id": "run-1",
        "actions": [action],
    }

    scenarios._verify_confirmation(
        confirmation,
        asset,
        release,
        session,
        "run-1",
    )
    wrong = deepcopy(confirmation)
    wrong["actions"][0]["arguments"]["versionId"] = "other"
    with pytest.raises(
        AssertionError,
        match="CONFIRMATION_ARGUMENTS_MISMATCH",
    ) as mismatch:
        scenarios._verify_confirmation(wrong, asset, release, session, "run-1")
    assert mismatch.value.argument_difference_paths == ["/versionId"]
    wrong_reference = deepcopy(confirmation)
    wrong_reference["actions"][0]["input_references"]["versionId"]["checksum"] = (
        "sha256:" + "b" * 64
    )
    with pytest.raises(
        AssertionError,
        match="CONFIRMATION_INPUT_REFERENCES_MISMATCH",
    ):
        scenarios._verify_confirmation(
            wrong_reference,
            asset,
            release,
            session,
            "run-1",
        )


def test_draft_oracle_accepts_arbitrary_valid_output_name() -> None:
    release = _release_identity()
    resource = {
        "kind": "data_version",
        "service": "data-primary",
        "dataset_id": "dataset-1",
        "version_id": "version-1",
        "semantic_id": "semantic-1",
        "object_id": "object-1",
        "checksum": "sha256:" + "f" * 64,
        "media_type": "text/csv",
        "format": "csv",
        "size_bytes": 12,
    }
    nodes = [
        {
            "node_id": "sort",
            "operator_id": "table.sort",
            "operator_version": "1.0.0",
            "inputs": {
                "data": {"workflow_input": "source"},
                "rules": {"workflow_input": "rules"},
            },
        },
        {
            "node_id": "describe",
            "operator_id": "statistics.describe",
            "operator_version": "1.0.0",
            "inputs": {
                "data": {"node_output": {"node_id": "sort", "port": "data"}},
            },
        },
    ]
    payload = {
        "schema_version": "workflow-draft.v1",
        "execution_allowed": False,
        "operator_catalog_digest": release["operator_catalog_digest"],
        "title": "排序后描述统计",
        "summary": "先排序，再计算描述统计。",
        "workflow": {
            "workflow_id": "draft.draft-1",
            "version": "0.1.0",
            "nodes": nodes,
            "inputs": [
                {
                    "name": "source",
                    "formats": ["csv"],
                    "media_types": ["text/csv"],
                },
                {
                    "name": "rules",
                    "formats": ["json"],
                    "media_types": ["application/json"],
                },
            ],
            "outputs": [
                {
                    "name": "descriptive_statistics",
                    "node_output": {"node_id": "describe", "port": "summary"},
                }
            ],
        },
        "operators": [
            {
                "node_id": node["node_id"],
                "operator_id": node["operator_id"],
                "operator_version": node["operator_version"],
                "implementation_revision": release["draft_operators"][
                    node["operator_id"]
                ]["implementationRevision"],
                "title": title,
            }
            for node, title in zip(
                nodes,
                ("排序", "描述统计"),
                strict=True,
            )
        ],
        "missing_inputs": [
            {
                "kind": "DATA",
                "name": "rules",
                "required_by": [{"node_id": "sort", "port": "rules"}],
            }
        ],
        "data_bindings": [
            {
                "input_name": "source",
                "data_ref": resource,
            }
        ],
        "unresolved_items": ["缺少排序规则资料"],
        "parameter_values": {},
        "parameter_sources": [],
        "limitations": [],
    }
    context = scenarios._draft_card_context(
        {
            "schema_version": "workflow-draft-resource.v1",
            "draft_id": "draft-1",
            "session_id": "session-1",
            "agent_run_id": "run-1",
            "goal_revision": 1,
            "status": "COMPLETE",
            "error_code": None,
            "diagnostics": None,
            "payload": payload,
        },
        {"resource": resource},
        {
            "workflow_draft_id": "draft-1",
            "session_id": "session-1",
            "confirmation": None,
            "workflow_run_ids": [],
            "remote_outputs": [],
        },
        {
            "agent_run_id": "run-1",
            "goal_revision": 1,
            "confirmations": [],
            "execution_tasks": [],
        },
        release,
    )

    assert context["outputs"] == ["描述统计结果"]


def test_summarize_rejects_duplicate_matrix_or_run_identity(monkeypatch) -> None:
    _environment(monkeypatch)
    records = _records()
    records[-1]["case_id"] = records[0]["case_id"]
    records[-1]["category"] = records[0]["category"]
    records[-1]["iteration"] = records[0]["iteration"]
    report = reports.summarize(
        records,
        cases=identity.load_cases(),
        release_identity=_release_identity(),
    )
    assert report["status"] == "FAILED"
    gate = report["automatic_gates"]["case_iteration_matrix_exact"]
    assert gate["status"] == "FAIL" and gate["violations"] > 0

    records = _records()
    records[-1]["session_id"] = records[0]["session_id"]
    report = reports.summarize(
        records,
        cases=identity.load_cases(),
        release_identity=_release_identity(),
    )
    assert report["status"] == "FAILED"
    gate = report["automatic_gates"]["session_and_agent_runs_unique_24"]
    assert gate["status"] == "FAIL" and gate["violations"] > 0


def test_summarize_distinguishes_violation_from_unassessed_checks(monkeypatch) -> None:
    _environment(monkeypatch)
    records = _records()
    failed = records[0]
    failed["status"] = "FAILED"
    failed.pop("assertions")
    failed.pop("review_content")
    failed.pop("response_id", None)
    failed["failure"] = "AGENT_MODEL_OUTPUT_INVALID"

    report = reports.summarize(
        records,
        cases=identity.load_cases(),
        release_identity=_release_identity(),
    )

    assert report["status"] == "FAILED"
    assert report["automatic_gates"]["provider_fallbacks_zero"] == {
        "status": "PASS",
        "expected": 24,
        "assessed": 24,
        "violations": 0,
        "failure_ids": [],
    }
    final_gate = report["automatic_gates"]["valid_final_reply_24_of_24"]
    assert final_gate["status"] == "NOT_ASSESSED"
    assert (final_gate["assessed"], final_gate["violations"]) == (23, 0)
    all_checks = report["automatic_gates"]["all_automatic_checks_pass"]
    assert all_checks["status"] == "FAIL"
    assert all_checks["failure_ids"] == [f"{failed['case_id']}#{failed['iteration']}"]


def test_blind_bundle_and_finalize_bind_exact_content_and_provider(monkeypatch) -> None:
    report = _report(monkeypatch)
    assert report["status"] == "AWAITING_HUMAN_REVIEW"
    bundle = reports.blind_review_bundle(report)
    assert len(bundle["responses"]) == 24
    assert "configuration" not in bundle
    assert "provider" not in json.dumps(bundle)
    assert all(
        item["response_id"].startswith("sha256:") for item in bundle["responses"]
    )
    reviews = _reviews(report)

    final = reports.finalize_report(report, reviews)

    assert final["status"] == "PASS"
    assert final["human_review"]["review_count"] == 48
    assert set(final["human_review"]["dimension_medians"].values()) == {4}
    bad = deepcopy(reviews)
    bad["reviews"][0]["directness"] = 2
    assert reports.finalize_report(report, bad)["status"] == "FAILED"

    mutated = deepcopy(report)
    mutated["runs"][0]["review_content"]["conclusion"] = "被改写"
    with pytest.raises(ValueError, match="REPORT_NOT_REVIEWABLE"):
        reports.blind_review_bundle(mutated)

    wrong_binding = deepcopy(reviews)
    wrong_binding["reviews"][0]["response_id"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="REVIEWS_INVALID"):
        reports.finalize_report(report, wrong_binding)


def test_finalize_requires_one_global_reviewer_pair(monkeypatch) -> None:
    report = _report(monkeypatch)
    reviews = _reviews(report)
    reviews["reviews"][3]["reviewer_id"] = "reviewer-c"

    with pytest.raises(ValueError, match="TWO_REVIEWERS_REQUIRED"):
        reports.finalize_report(report, reviews)


def test_combine_requires_two_independent_passing_providers(monkeypatch) -> None:
    minimax_report = _report(monkeypatch, "minimax")
    minimax = reports.finalize_report(minimax_report, _reviews(minimax_report))
    deepseek_report = _report(monkeypatch, "deepseek")
    deepseek = reports.finalize_report(deepseek_report, _reviews(deepseek_report))

    combined = reports.combine_reports([minimax, deepseek])

    assert combined["status"] == "PASS"
    assert set(combined["providers"]) == {"minimax", "deepseek"}
    with pytest.raises(ValueError, match="PROVIDER_PAIR_INVALID"):
        reports.combine_reports([minimax, deepcopy(minimax)])
    mismatched = deepcopy(deepseek)
    mismatched["data_fixture_checksum"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError):
        reports.combine_reports([minimax, mismatched])
    failed = deepcopy(deepseek)
    failed["status"] = "FAILED"
    with pytest.raises(ValueError):
        reports.combine_reports([minimax, failed])


def test_missing_configuration_writes_blocked_report(
    tmp_path,
    monkeypatch,
) -> None:
    for name in definitions.REQUIRED_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        identity, "load_release_identity", lambda _path: _release_identity()
    )
    output = tmp_path / "reply-quality.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["scripts.evaluation.evaluate_reply_quality", "run", "--output", str(output)],
    )

    with pytest.raises(SystemExit) as result:
        quality.main()

    assert result.value.code == 2
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "BLOCKED[real-reply-quality]"
    assert report["planned_run_count"] == 24
    assert report["data_fixture_checksum"] == definitions.EXPECTED_DATA_FIXTURE_CHECKSUM
