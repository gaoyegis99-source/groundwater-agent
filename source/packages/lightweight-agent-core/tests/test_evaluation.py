from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from platform_lightweight_agent import (
    AgentDecision,
    AgentInvocationUsage,
    ToolCall,
    ToolReference,
)
from platform_lightweight_agent import evaluation
from platform_lightweight_agent.cli import main as cli_main
from platform_lightweight_agent.evaluation import run_live_evaluation
from platform_lightweight_agent.portability import scaffold_project


def test_live_evaluation_requires_explicit_network_authorization(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="allow-model-requests"):
        run_live_evaluation(
            tmp_path,
            provider="pydantic-ai",
            model="test:model",
            output=tmp_path / "report.json",
        )


@pytest.mark.parametrize("provider", ["pydantic-ai", "openai-agents"])
def test_pydantic_evals_core_produces_neutral_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    pytest.importorskip("copier")
    pytest.importorskip("pydantic_evals")
    project = scaffold_project("workflow", tmp_path / "project", project_name="eval")
    tool = json.loads(project.tools_path.read_text(encoding="utf-8"))[0]  # type: ignore[union-attr]
    reference = ToolReference.from_mapping(
        {
            key: tool[key]
            for key in (
                "tool_id",
                "version",
                "package_digest",
            )
        }
    )

    class FakeDriver:
        driver_version = "fake-native-v2@1"

        def __init__(self, **kwargs):  # type: ignore[no-untyped-def]
            self.observe = kwargs["usage_observer"]
            self.instructions = kwargs["instructions"]
            self.model = kwargs["model"]

        def execution_binding(self):  # type: ignore[no-untyped-def]
            return {
                "driver_version": self.driver_version,
                "provider": "fake",
                "model": self.model,
                "instructions": self.instructions,
                "protocol": "native-tools-v2",
            }

        def decide(self, context):  # type: ignore[no-untyped-def]
            raise AssertionError(f"V4 evaluation used sync decide: {context}")

        async def decide_async(self, context):  # type: ignore[no-untyped-def]
            observed = AgentInvocationUsage(
                request_count=1,
                input_tokens=10,
                output_tokens=2,
                total_tokens=12,
                duration_seconds=0.01,
                provider="fake",
                model="fake",
            )
            self.observe(observed)
            usage = observed.to_mapping()
            if context.prior_tool_results:
                return AgentDecision(
                    "finished", output={"message": "finished"}, usage=usage
                )
            request = str(context.step_input).lower()
            if "two separate" in request:
                return AgentDecision(
                    "",
                    (
                        ToolCall(reference, {"query": "aquifer"}),
                        ToolCall(reference, {"query": "well"}),
                    ),
                    usage=usage,
                )
            if "look up" in request:
                query = (
                    "protected-aquifer"
                    if "protected-aquifer" in request
                    else "aquifer"
                )
                return AgentDecision(
                    "",
                    (ToolCall(reference, {"query": query}),),
                    usage=usage,
                )
            return AgentDecision(
                "acknowledged", output={"message": "acknowledged"}, usage=usage
            )

    monkeypatch.setattr(
        (
            "platform_lightweight_agent.evaluation.PydanticAIDriver"
            if provider == "pydantic-ai"
            else "platform_lightweight_agent.evaluation.OpenAIAgentsDriver"
        ),
        FakeDriver,
    )
    output = tmp_path / "report.json"
    report = run_live_evaluation(
        project.root,
        provider=provider,
        model="fake:model",
        output=output,
        allow_model_requests=True,
    )

    assert report["schema_version"] == "v6-agent-eval-report-v1"
    assert report["result"] == "PASS"
    assert report["release_gate"] is False
    assert report["summary"]["case_count"] == 5
    assert report["summary"]["completed_case_count"] == 5
    assert report["summary"]["failed_case_count"] == 0
    assert report["summary"]["request_count"] == 8
    by_id = {item["id"]: item for item in report["cases"]}
    assert by_id["parallel-multi-tool"]["output"]["tools"] == [
        "lookup",
        "lookup",
    ]
    assert by_id["approval-recovery"]["output"]["approval_resumed"] is True
    assert by_id["approval-recovery"]["output"]["run_status"] == "SUCCEEDED"
    assert by_id["approval-recovery"]["output"]["last_event_sequence"] > 1
    assert json.loads(output.read_text(encoding="utf-8")) == report


def test_pydantic_evals_case_failures_are_visible_and_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("copier")
    pytest.importorskip("pydantic_evals")
    project = scaffold_project("workflow", tmp_path / "project", project_name="eval")

    class UnusedDriver:
        driver_version = "fake-native-v2@1"

        def __init__(self, **_kwargs):  # type: ignore[no-untyped-def]
            pass

    def fail_before_execution(*_args):  # type: ignore[no-untyped-def]
        raise RuntimeError("secret-must-not-appear")

    monkeypatch.setattr(evaluation, "PydanticAIDriver", UnusedDriver)
    monkeypatch.setattr(evaluation, "_EvaluationToolSimulator", fail_before_execution)
    output = tmp_path / "failed-report.json"
    report = run_live_evaluation(
        project.root,
        provider="pydantic-ai",
        model="fake:model",
        output=output,
        allow_model_requests=True,
    )
    assert report["result"] == "FAIL"
    assert report["summary"]["case_count"] == 5
    assert report["summary"]["completed_case_count"] == 0
    assert report["summary"]["failed_case_count"] == 5
    assert len(report["failures"]) == 5
    assert "secret-must-not-appear" not in output.read_text(encoding="utf-8")


def test_empty_evaluator_result_cannot_report_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("copier")
    pydantic_evals = pytest.importorskip("pydantic_evals")
    project = scaffold_project("workflow", tmp_path / "project", project_name="eval")

    class UnusedDriver:
        driver_version = "fake-native-v2@1"

        def __init__(self, **_kwargs):  # type: ignore[no-untyped-def]
            pass

    monkeypatch.setattr(evaluation, "PydanticAIDriver", UnusedDriver)
    monkeypatch.setattr(
        pydantic_evals.Dataset,
        "evaluate_sync",
        lambda *_args, **_kwargs: SimpleNamespace(cases=[], failures=[]),
    )
    report = run_live_evaluation(
        project.root,
        provider="pydantic-ai",
        model="fake:model",
        output=tmp_path / "empty-report.json",
        allow_model_requests=True,
    )
    assert report["result"] == "FAIL"
    assert report["summary"]["case_count"] == 5
    assert report["summary"]["completed_case_count"] == 0
    assert report["summary"]["failed_case_count"] == 5
    assert {item["code"] for item in report["failures"]} == {
        "EVALUATION_CASE_MISSING"
    }


def test_evaluation_cli_returns_nonzero_for_failed_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(evaluation, "run_live_evaluation", lambda *_args, **_kwargs: {"result": "FAIL"})
    assert cli_main(
        [
            "evaluate",
            str(tmp_path),
            "--provider",
            "pydantic-ai",
            "--model",
            "test:model",
            "--output",
            str(tmp_path / "report.json"),
            "--allow-model-requests",
        ]
    ) == 1


@pytest.mark.parametrize("provider", ["pydantic-ai", "openai-agents"])
def test_deepseek_evaluation_uses_native_supported_model_configuration(
    provider: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("openai")
    pytest.importorskip("httpx")
    pytest.importorskip("pydantic_ai" if provider == "pydantic-ai" else "agents")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-offline-credential")
    name, _version, factory, close = evaluation._evaluation_driver_factory(
        provider, "deepseek:deepseek-flash", "Answer safely.", lambda _usage: None
    )
    try:
        driver = factory.create(
            SimpleNamespace(model="deepseek:deepseek-flash"), "Answer safely."
        )
        assert name == f"evaluation-{provider}"
        if provider == "pydantic-ai":
            assert type(driver._agent.model).__name__ == "OpenAIChatModel"
            assert driver._agent.model_settings == {
                "extra_body": {
                    "thinking": {"type": "disabled"},
                    "max_tokens": 1024,
                }
            }
        else:
            assert type(driver._agent.model).__name__ == "OpenAIResponsesModel"
            assert driver._agent.model_settings.max_tokens == 1024
            assert driver._agent.model_settings.reasoning is None
    finally:
        close()


def test_deepseek_evaluation_requires_a_model_name_and_environment_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ValueError, match="model name is missing"):
        evaluation._evaluation_driver_factory(
            "pydantic-ai", "deepseek:", "instructions", lambda _usage: None
        )
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY is required"):
        evaluation._evaluation_driver_factory(
            "pydantic-ai", "deepseek:deepseek-flash", "instructions", lambda _usage: None
        )
