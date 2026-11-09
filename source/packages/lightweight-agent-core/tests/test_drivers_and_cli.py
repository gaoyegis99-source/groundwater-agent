from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import anyio
import pytest
from platform_lightweight_agent import (
    AgentBudget,
    AgentDecision,
    AgentToolSpec,
    HostRuntime,
    LocalToolExecutor,
    NodeContract,
    RunRetentionPolicy,
    SQLiteRunJournal,
    ToolCall,
    ToolRecoveryCapabilities,
    ToolReference,
    ValueBinding,
    WorkflowEngine,
    agent_step,
    approval_step,
    tool_step,
    workflow,
)
from platform_lightweight_agent.cli import main
from platform_lightweight_agent.drivers import (
    OpenAIAgentsDriver,
    PydanticAIDriver,
    ScriptedAgentDriver,
)
from platform_lightweight_agent.engine import AgentContext


def _context() -> AgentContext:
    return AgentContext(
        run_id="run-1",
        workflow_id="workflow",
        workflow_version="1",
        step_id="agent",
        turn=1,
        inputs={"topic": "birds"},
        step_input={"question": "what now?"},
        values={"topic": "birds"},
        prior_tool_results=(),
    )


class _OpenAIResult:
    final_output = '{"message":"ok","tool_calls":[]}'


class _PydanticResult:
    def __init__(self) -> None:
        self.output = {"message": "ok", "tool_calls": []}


def test_openai_and_pydantic_adapters_share_the_same_decision_contract() -> None:
    prompts: list[str] = []

    def openai_runner(agent: object, prompt: str) -> _OpenAIResult:
        assert agent is not None
        prompts.append(prompt)
        return _OpenAIResult()

    def pydantic_runner(agent: object, prompt: str) -> _PydanticResult:
        assert agent is not None
        prompts.append(prompt)
        return _PydanticResult()

    openai = OpenAIAgentsDriver(
        agent=object(), runner=openai_runner, driver_version="openai-test"
    )
    pydantic = PydanticAIDriver(
        agent=object(), runner=pydantic_runner, driver_version="pydantic-test"
    )

    assert openai.decide(_context()).to_mapping() == {"message": "ok", "tool_calls": []}
    assert pydantic.decide(_context()).to_mapping() == {
        "message": "ok",
        "tool_calls": [],
    }
    assert all('"step_id":"agent"' in prompt for prompt in prompts)
    assert all("Do not include credentials" in prompt for prompt in prompts)


def test_openai_agents_sdk_driver_constructs_without_leaking_sdk_types() -> None:
    pytest.importorskip("agents")
    received: list[object] = []

    def runner(agent: object, prompt: str) -> _OpenAIResult:
        received.append(agent)
        assert '"workflow_id":"workflow"' in prompt
        return _OpenAIResult()

    driver = OpenAIAgentsDriver(model="gpt-4o-mini", runner=runner)
    assert driver.decide(_context()).to_mapping() == {"message": "ok", "tool_calls": []}
    assert len(received) == 1


def test_pydantic_ai_slim_driver_constructs_without_invoking_a_model() -> None:
    pytest.importorskip("pydantic_ai")
    received: list[object] = []

    def runner(agent: object, prompt: str) -> _PydanticResult:
        received.append(agent)
        assert '"workflow_id":"workflow"' in prompt
        return _PydanticResult()

    driver = PydanticAIDriver(model="test", runner=runner)
    assert driver.decide(_context()).to_mapping() == {"message": "ok", "tool_calls": []}
    assert len(received) == 1


@pytest.mark.anyio
async def test_scripted_async_driver_observes_cancellation_checkpoint() -> None:
    driver = ScriptedAgentDriver(
        [AgentDecision("offline", output={"message": "offline"})]
    )

    with anyio.CancelScope() as scope:
        scope.cancel()
        with pytest.raises(anyio.get_cancelled_exc_class()):
            await driver.decide_async(_context())


def test_cli_check_and_local_approval_workflow(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    workflow_path = tmp_path / "workflow.json"
    workflow_path.write_text(
        json.dumps(
            {
                "schema_version": "v6-light-workflow-v1",
                "workflow_id": "cli-demo",
                "version": "1",
                "steps": [
                    {
                        "id": "review",
                        "kind": "approval",
                        "config": {"prompt": "approve?"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    journal = tmp_path / "runs.sqlite"

    assert main(["check", str(workflow_path)]) == 0
    assert json.loads(capsys.readouterr().out)["workflow_id"] == "cli-demo"
    assert (
        main(
            [
                "run",
                str(workflow_path),
                "--journal",
                str(journal),
                "--run-id",
                "cli-run",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "WAITING_APPROVAL"
    assert main(["resume", "cli-run", "--journal", str(journal), "--approve"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "SUCCEEDED"


def test_cli_rejects_a_stale_event_cursor_and_control_identity(
    tmp_path: Path, capsys
) -> None:  # type: ignore[no-untyped-def]
    journal_path = tmp_path / "reused.sqlite"
    journal = SQLiteRunJournal(journal_path)
    engine = WorkflowEngine(journal, LocalToolExecutor(), {})
    definition = workflow(
        "cli-identity",
        "1",
        [approval_step("review", "approve?")],
        schema_version="v6-light-workflow-v1",
    )
    original = engine.run(definition, {}, run_id="cli-reused")
    cursor = engine.events(original.run_id)[-1].cursor.to_token()
    assert engine.resume(original.handle, approved=True).status == "SUCCEEDED"
    journal.apply_retention(
        RunRetentionPolicy(archive_after_seconds=0, purge_after_seconds=0),
        now=time.time() + 60,
    )
    replacement = engine.run(definition, {}, run_id="cli-reused")

    assert (
        main(
            [
                "events",
                "cli-reused",
                "--journal",
                str(journal_path),
                "--cursor",
                cursor,
            ]
        )
        == 4
    )
    assert json.loads(capsys.readouterr().err)["code"] == "RUN_EXECUTION_MISMATCH"
    assert (
        main(
            [
                "cancel",
                "cli-reused",
                "--journal",
                str(journal_path),
                "--expected-execution-id",
                original.execution_id,
            ]
        )
        == 4
    )
    assert json.loads(capsys.readouterr().err)["code"] == "RUN_EXECUTION_MISMATCH"
    assert engine.get(replacement.handle).status == "WAITING_APPROVAL"


def test_cli_rejects_oversized_inline_and_file_inputs(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    workflow_path = tmp_path / "workflow.json"
    workflow_path.write_text(
        json.dumps(
            workflow(
                "cli-input-limit",
                "1",
                [approval_step("review", "approve?")],
                schema_version="v6-light-workflow-v1",
            ).to_mapping()
        ),
        encoding="utf-8",
    )
    oversized = json.dumps({"blob": "x" * (1024 * 1024)})

    assert main(["run", str(workflow_path), "--input", oversized]) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "INPUT_TOO_LARGE"

    input_path = tmp_path / "large-input.json"
    input_path.write_text(oversized, encoding="utf-8")
    assert main(["run", str(workflow_path), "--input-file", str(input_path)]) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "INPUT_TOO_LARGE"


def test_bare_cli_delivers_native_async_tools_and_explicit_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:  # type: ignore[no-untyped-def]
    reference = ToolReference("cli.async", "1", "sha256:" + "c" * 64)
    (tmp_path / "runtime_tools.py").write_text(
        "\n".join(
            (
                "from platform_lightweight_agent import ToolReference",
                "async def lookup(arguments, context):",
                "    assert context.files is not None",
                "    return {'value': arguments['value']}",
                "def register(executor):",
                "    executor.register(",
                f"        ToolReference('cli.async', '1', {reference.package_digest!r}),",
                "        lookup,",
                "    )",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
        "additionalProperties": False,
    }
    spec = workflow(
        "cli-async",
        "1",
        (
            tool_step(
                "lookup",
                reference,
                approval_required=False,
                contract=NodeContract(
                    schema,
                    schema,
                    {"/value": ValueBinding("workflow_input", "/value")},
                ),
            ),
        ),
        schema_version="v6-light-workflow-v3",
        input_schema=schema,
        output_schema=schema,
        output_bindings={"/value": ValueBinding("step_output", "/value", "lookup")},
    )
    workflow_path = tmp_path / "workflow.json"
    workflow_path.write_text(json.dumps(spec.to_mapping()), encoding="utf-8")

    assert (
        main(
            [
                "run",
                str(workflow_path),
                "--journal",
                str(tmp_path / "runs.sqlite"),
                "--tool-registry",
                "runtime_tools:register",
                "--file-workspace",
                str(tmp_path / "files"),
                "--input",
                '{"value":9}',
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "SUCCEEDED"


def test_cli_v4_scripted_run_resumes_offline_after_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:  # type: ignore[no-untyped-def]
    reference = ToolReference("cli.scripted", "1", "sha256:" + "e" * 64)
    (tmp_path / "scripted_tools.py").write_text(
        "\n".join(
            (
                "from platform_lightweight_agent import ToolReference",
                "def lookup(arguments):",
                "    return {'answer': 'example:' + arguments['query']}",
                "def register(executor):",
                "    executor.register(",
                f"        ToolReference('cli.scripted', '1', {reference.package_digest!r}),",
                "        lookup,",
                "    )",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    empty = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    arguments = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    }
    tool_output = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": False,
    }
    final_output = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
        "additionalProperties": False,
    }
    tool = AgentToolSpec(
        "lookup",
        reference,
        "Offline lookup.",
        arguments,
        tool_output,
        capabilities=ToolRecoveryCapabilities(
            external_side_effects=False,
            supports_result_query=True,
        ),
    )
    spec = workflow(
        "cli-scripted-v4",
        "1",
        (
            agent_step(
                "research",
                "scripted",
                tools=(tool,),
                budget=AgentBudget(),
                contract=NodeContract(empty, final_output, {}),
            ),
        ),
        schema_version="v6-light-workflow-v4",
        input_schema=empty,
        output_schema=final_output,
        output_bindings={
            "/message": ValueBinding("step_output", "/message", "research")
        },
    )
    workflow_path = tmp_path / "workflow.json"
    workflow_path.write_text(json.dumps(spec.to_mapping()), encoding="utf-8")
    decisions = {
        "schema_version": "v6-scripted-decisions-v1",
        "agents": {
            "cli-scripted-v4@1:research": [
                AgentDecision(
                    "lookup", (ToolCall(reference, {"query": "aquifer"}),)
                ).to_mapping(),
                AgentDecision(
                    "done", output={"message": "example:aquifer"}
                ).to_mapping(),
            ]
        },
    }
    decisions_path = tmp_path / "scripted-decisions.json"
    decisions_path.write_text(json.dumps(decisions), encoding="utf-8")
    journal = tmp_path / "runs.sqlite"
    common = [
        "--journal",
        str(journal),
        "--tool-registry",
        "scripted_tools:register",
        "--scripted-decisions",
        str(decisions_path),
        "--driver-name",
        "scripted",
        "--strict-exit",
    ]

    assert main(["run", str(workflow_path), "--run-id", "offline", *common]) == 11
    waiting = json.loads(capsys.readouterr().out)
    assert waiting["status"] == "WAITING_APPROVAL"
    assert waiting["execution_mode"] == "simulated"
    assert waiting["model_evaluation"] is False
    approval_id = next(iter(waiting["state"]["v3"]["pending_approvals"]))

    assert (
        main(
            [
                "resume",
                "offline",
                "--approval-id",
                approval_id,
                "--approve",
                *common,
            ]
        )
        == 0
    )
    completed = json.loads(capsys.readouterr().out)
    assert completed["status"] == "SUCCEEDED"
    assert completed["state"]["v3"]["output"] == {"message": "example:aquifer"}


def test_cli_strict_exit_distinguishes_blocked_from_processed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:  # type: ignore[no-untyped-def]
    reference = ToolReference("cli.invalid-output", "1", "sha256:" + "f" * 64)
    (tmp_path / "invalid_output_tools.py").write_text(
        "\n".join(
            (
                "from platform_lightweight_agent import ToolReference",
                "def invalid(arguments):",
                "    return {'value': 'not-an-integer'}",
                "def register(executor):",
                "    executor.register(",
                f"        ToolReference('cli.invalid-output', '1', {reference.package_digest!r}),",
                "        invalid,",
                "    )",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
        "additionalProperties": False,
    }
    spec = workflow(
        "cli-invalid-output",
        "1",
        (
            tool_step(
                "invalid",
                reference,
                approval_required=False,
                contract=NodeContract(
                    schema,
                    schema,
                    {"/value": ValueBinding("workflow_input", "/value")},
                ),
            ),
        ),
        schema_version="v6-light-workflow-v3",
        input_schema=schema,
        output_schema=schema,
        output_bindings={"/value": ValueBinding("step_output", "/value", "invalid")},
    )
    workflow_path = tmp_path / "workflow.json"
    workflow_path.write_text(json.dumps(spec.to_mapping()), encoding="utf-8")
    base = [
        "run",
        str(workflow_path),
        "--journal",
        str(tmp_path / "runs.sqlite"),
        "--tool-registry",
        "invalid_output_tools:register",
        "--input",
        '{"value":1}',
    ]

    assert main([*base, "--run-id", "legacy-exit"]) == 0
    legacy = json.loads(capsys.readouterr().out)
    assert legacy["status"] == "BLOCKED"
    assert main([*base, "--run-id", "strict-exit", "--strict-exit"]) == 10
    strict = json.loads(capsys.readouterr().out)
    assert strict["status"] == "BLOCKED"
    assert strict["diagnostic"] == {
        "schema_version": "v6-cli-diagnostic-v1",
        "code": "STEP_OUTPUT_INVALID",
        "message": "workflow did not complete successfully",
        "path": "/value",
        "keyword": "type",
        "step_id": "invalid",
        "hint": "Fix the named step value to satisfy its declared JSON Schema.",
    }


def test_cli_invalid_yaml_is_a_structured_location_diagnostic(
    tmp_path: Path, capsys
) -> None:  # type: ignore[no-untyped-def]
    pytest.importorskip("yaml")
    source = tmp_path / "broken.yaml"
    source.write_text("steps:\n  - id: broken\n    kind: [\n", encoding="utf-8")

    assert main(["check", str(source)]) == 2

    error = json.loads(capsys.readouterr().err)
    assert error["schema_version"] == "v6-cli-diagnostic-v1"
    assert error["code"] == "WORKFLOW_PARSE_ERROR"
    assert error["file"] == str(source)
    assert isinstance(error["line"], int)
    assert isinstance(error["column"], int)
    assert error["path"] == "/"
    assert error["keyword"] == "syntax"
    assert "YAML" in error["hint"]


def test_cli_contract_diagnostic_names_source_and_rule(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    empty = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    approved = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"approved": {"type": "boolean"}},
        "required": ["approved"],
        "additionalProperties": False,
    }
    source = tmp_path / "invalid-binding.json"
    source.write_text(
        json.dumps(
            {
                "schema_version": "v6-light-workflow-v3",
                "workflow_id": "invalid-binding",
                "version": "1",
                "input_schema": empty,
                "output_schema": approved,
                "output_bindings": {
                    "/approved": {
                        "source": "step_output",
                        "step_id": "missing-step",
                        "pointer": "/approved",
                    }
                },
                "execution_policy": {"max_concurrency": 1},
                "steps": [
                    {
                        "id": "review",
                        "kind": "approval",
                        "config": {"prompt": "Approve?"},
                        "contract": {
                            "input_schema": empty,
                            "output_schema": approved,
                            "bindings": {},
                            "timeout_seconds": None,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert main(["check", str(source)]) == 2

    diagnostic = json.loads(capsys.readouterr().err)
    assert diagnostic["schema_version"] == "v6-cli-diagnostic-v1"
    assert diagnostic["code"] == "WORKFLOW_CONTRACT_INVALID"
    assert diagnostic["file"] == str(source)
    assert diagnostic["path"] == "/"
    assert diagnostic["keyword"] == "contract"
    assert "field or node" in diagnostic["hint"]


@pytest.mark.parametrize("error_type", [RuntimeError, ValueError])
def test_cli_does_not_relabel_programming_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
) -> None:
    workflow_path = tmp_path / "workflow.json"
    workflow_path.write_text("{}", encoding="utf-8")

    def fail(_path: Path):  # type: ignore[no-untyped-def]
        raise error_type("injected programming defect")

    monkeypatch.setattr("platform_lightweight_agent.cli._load_workflow", fail)
    with pytest.raises(error_type, match="programming defect"):
        main(["check", str(workflow_path)])


def test_cli_serve_uses_the_bounded_host_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[HostRuntime] = []

    def fake_serve(runtime, **_kwargs):  # type: ignore[no-untyped-def]
        observed.append(runtime)

    monkeypatch.setattr("platform_lightweight_agent.web.serve_web_app", fake_serve)
    assert (
        main(
            [
                "serve",
                "--journal",
                str(tmp_path / "runs.sqlite"),
                "--session-token",
                "local-test-token",
                "--max-active-runs",
                "2",
                "--max-tool-calls",
                "3",
            ]
        )
        == 0
    )
    assert isinstance(observed[0], HostRuntime)
    assert observed[0].limits.max_active_runs == 2
    assert observed[0].limits.max_tool_calls == 3


def test_cli_runs_lists_compact_projection(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    journal_path = tmp_path / "runs.sqlite"
    engine = WorkflowEngine(SQLiteRunJournal(journal_path), LocalToolExecutor(), {})
    waiting = engine.run(
        workflow(
            "cli-runs",
            "1",
            [approval_step("review", "approve?")],
            schema_version="v6-light-workflow-v1",
        ),
        {},
        run_id="cli-runs",
    )
    assert waiting.status == "WAITING_APPROVAL"

    assert main(["runs", "--journal", str(journal_path), "--pending-only"]) == 0
    page = json.loads(capsys.readouterr().out)
    assert page["schema_version"] == "v6-run-page-v1"
    assert len(page["items"]) == 1
    item = page["items"][0]
    assert item["run_id"] == "cli-runs"
    assert item["execution_id"] == waiting.execution_id
    assert item["workflow_id"] == "cli-runs"
    assert item["workflow_version"] == "1"
    assert item["status"] == "WAITING_APPROVAL"
    assert item["pending_approval_count"] == 1
    assert item["failure_code"] is None


def test_cli_project_plan_bundle_v2_diagnose_and_import(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    pytest.importorskip("copier")
    project = tmp_path / "project"
    bundle = tmp_path / "project.bundle"

    assert main(["init", "workflow", str(project), "--name", "cli-project"]) == 0
    assert json.loads(capsys.readouterr().out)["kind"] == "workflow"
    assert main(["check", str(project)]) == 0
    capsys.readouterr()
    assert main(["plan", str(project)]) == 0
    assert json.loads(capsys.readouterr().out)["effects"] == "not_executed"
    assert main(["lock", str(project)]) == 0
    capsys.readouterr()
    assert main(["export", str(project), str(bundle), "--format", "v2"]) == 0
    assert json.loads(capsys.readouterr().out)["schema_version"] == "v6-light-bundle-v2"
    target = project / "target.example.json"
    assert (
        main(
            [
                "diagnose",
                str(bundle),
                "--target-config",
                str(target),
                "--skip-dependency-check",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "compatible"
    destination = tmp_path / "imported"
    assert (
        main(
            [
                "import",
                str(bundle),
                str(destination),
                "--target-config",
                str(target),
            ]
        )
        == 0
    )
    assert (destination / "portability.json").is_file()


def test_core_import_does_not_load_services_or_optional_agent_frameworks() -> None:
    source_root = Path(__file__).parents[1] / "src"
    script = (
        "import sys; sys.path.insert(0, sys.argv[1]); import platform_lightweight_agent; "
        "print(','.join(sorted(set(sys.modules) & "
        "{'agents','copier','fastapi','genai_prices','jsonschema','pydantic',"
        "'pydantic_ai','pydantic_evals','sqlalchemy'})))"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-c", script, str(source_root)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == ""
