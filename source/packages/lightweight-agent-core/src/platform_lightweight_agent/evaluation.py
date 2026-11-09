"""Explicit, non-gating live-model evaluation built on Pydantic Evals."""

from __future__ import annotations

import asyncio
import json
import math
import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from threading import Lock
from typing import Any

from .drivers import (
    AgentInvocationUsage,
    OpenAIAgentsDriver,
    PydanticAIDriver,
)
from .engine import FunctionToolExecutor, ToolExecutionFailedError
from .journal import RunEvent, RunSnapshot, SQLiteRunJournal
from .portability import (
    DriverBinding,
    PortableProject,
    PortableTarget,
    agent_address_key,
    compile_project_context,
)
from .runtime import (
    DriverFactory,
    ProjectRuntime,
    load_project_runtime,
    load_resume_runtime,
)
from .spec import (
    ApprovalDecision,
    AgentToolSpec,
    JsonValue,
    StepKind,
    ToolReference,
    WorkflowSpec,
    WorkflowValidationError,
)

EVALUATION_REPORT_V1 = "v6-agent-eval-report-v1"


def _package_version(*names: str) -> str:
    for name in names:
        try:
            return version(name)
        except PackageNotFoundError:
            continue
    return "unknown"


def _load_json(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WorkflowValidationError(f"{label} cannot be read as JSON") from error


def run_live_evaluation(
    source: str | Path,
    *,
    provider: str,
    model: str,
    output: str | Path,
    allow_model_requests: bool = False,
) -> Mapping[str, JsonValue]:
    """Run an explicitly authorized, serial live evaluation and save JSON."""

    if not allow_model_requests:
        raise WorkflowValidationError("live evaluation requires --allow-model-requests")
    if provider not in {"pydantic-ai", "openai-agents"}:
        raise WorkflowValidationError("evaluation provider is unsupported")
    compilation = compile_project_context(source)
    project = compilation.project
    if (
        project.kind != "workflow"
        or project.workflow_path is None
        or project.evaluation_cases_path is None
    ):
        raise WorkflowValidationError(
            "live evaluation requires a workflow project with evaluation cases"
        )
    workflow = compilation.workflow
    agent_definitions = [
        (definition, step)
        for definition in (workflow, *compilation.subworkflows)
        for step in _walk_steps(definition.steps)
        if step.kind is StepKind.AGENT
    ]
    if len(agent_definitions) != 1:
        raise WorkflowValidationError(
            "live evaluation currently requires exactly one Agent step"
        )
    definition, step = agent_definitions[0]
    if workflow.schema_version == "v6-light-workflow-v1":
        raise WorkflowValidationError("live evaluation requires typed Agent tools")
    if workflow.schema_version in {"v6-light-workflow-v3", "v6-light-workflow-v4"}:
        from .workflow_v3 import _optional_dependencies

        _optional_dependencies()
    tool_specs = tuple(
        AgentToolSpec.from_mapping(item) for item in step.config.get("tools", [])
    )
    instructions_path = project.agent_instruction_paths.get(
        agent_address_key(definition.workflow_id, definition.version, step.step_id)
    )
    if instructions_path is None:
        raise WorkflowValidationError("Agent instructions are missing")
    instructions = instructions_path.read_text(encoding="utf-8")
    raw_cases = _load_json(project.evaluation_cases_path, "evaluation cases")
    cases = _normalize_cases(raw_cases)
    observations: dict[str, list[AgentInvocationUsage]] = {
        case["id"]: [] for case in cases
    }
    active_case: str | None = None

    def observe(usage: AgentInvocationUsage) -> None:
        if active_case is not None:
            observations[active_case].append(usage)

    driver_name, driver_version, driver_factory, close_driver = _evaluation_driver_factory(
        provider,
        model,
        instructions,
        observe,
    )
    sdk_version = (
        _package_version("pydantic-ai", "pydantic-ai-slim")
        if provider == "pydantic-ai"
        else _package_version("openai-agents")
    )
    tool_manifests = _project_tool_manifests(project)
    target = _evaluation_target(
        compilation.subworkflows,
        definition,
        step.step_id,
        driver_name,
        driver_version,
        model,
        tool_manifests,
    )

    try:
        from pydantic_evals import Case, Dataset
        from pydantic_evals.evaluators import Evaluator, EvaluatorContext
    except ImportError as error:
        raise RuntimeError(
            "Install platform-lightweight-agent-core[evaluation] to run evaluations"
        ) from error

    class V6TrajectoryEvaluator(
        Evaluator[dict[str, Any], dict[str, Any], dict[str, Any]]
    ):
        def evaluate(self, ctx: EvaluatorContext) -> Mapping[str, bool]:  # type: ignore[type-arg]
            expected_tools = ctx.metadata.get("expected_tools", [])
            actual_tools = ctx.output.get("tools", [])
            expected_arguments = ctx.metadata.get("expected_arguments")
            needle = ctx.metadata.get("expected_message_contains")
            task_complete = ctx.output.get("error") is None
            if isinstance(needle, str) and needle:
                task_complete = task_complete and needle in ctx.output.get(
                    "message", ""
                )
            return {
                "task_completion": bool(task_complete),
                "tool_selection_correct": actual_tools == expected_tools,
                "tool_arguments_correct": (
                    True
                    if expected_arguments is None
                    else ctx.output.get("arguments") == expected_arguments
                ),
            }

    dataset = Dataset(
        name="v6-agent-live-evaluation",
        cases=[
            Case(
                name=case["id"],
                inputs=case,
                metadata={
                    "expected_tools": case["expected_tools"],
                    "expected_arguments": case.get("expected_arguments"),
                    "expected_message_contains": case.get("expected_message_contains"),
                },
            )
            for case in cases
        ],
        evaluators=[V6TrajectoryEvaluator()],
    )
    exact_to_name: dict[tuple[str, str, str], str] = {
        (spec.tool.tool_id, spec.tool.version, spec.tool.package_digest): spec.name
        for spec in tool_specs
    }
    for manifest in tool_manifests:
        reference = _manifest_tool_reference(manifest)
        exact_to_name.setdefault(_tool_identity(reference), reference.tool_id)

    async def task(case: dict[str, Any]) -> dict[str, Any]:
        nonlocal active_case
        active_case = case["id"]
        try:
            if case.get("prior_tool_results"):
                raise WorkflowValidationError(
                    "Engine-backed evaluation cases cannot inject prior tool results"
                )
            simulator = _EvaluationToolSimulator(case, exact_to_name)
            with tempfile.TemporaryDirectory(prefix="v6-agent-evaluation-") as directory:
                journal_path = Path(directory) / "runs.sqlite"
                journal = SQLiteRunJournal(journal_path)
                runtime = load_project_runtime(
                    project,
                    target,
                    journal,
                    executor=simulator.executor(tool_manifests),
                    driver_factories={driver_name: driver_factory},
                )
                run_id = "evaluation-" + _safe_case_id(case["id"])
                snapshot = await runtime.engine.run_async(
                    runtime.workflow,
                    case["input"],
                    run_id=run_id,
                )
                approval_resumed = False
                approval_rounds = 0
                while snapshot.status == "WAITING_APPROVAL":
                    approval_rounds += 1
                    if approval_rounds > 64:
                        raise WorkflowValidationError(
                            "evaluation exceeded the bounded approval rounds"
                        )
                    pending = snapshot.state.get("v3", {}).get(
                        "pending_approvals", {}
                    )
                    if not isinstance(pending, Mapping) or not pending:
                        raise WorkflowValidationError(
                            "evaluation run is waiting without a pending approval"
                        )
                    if case.get("approval_resume") is True and not approval_resumed:
                        # Rebuild from the checked project plus frozen SQLite state.
                        # Neither an SDK result nor a framework checkpoint is reused.
                        journal = SQLiteRunJournal(journal_path)
                        runtime = load_resume_runtime(
                            project.root,
                            target,
                            journal,
                            run_id,
                            executor=simulator.executor(tool_manifests),
                            driver_factories={driver_name: driver_factory},
                        )
                        approval_resumed = True
                    decisions = tuple(
                        ApprovalDecision(approval_id, True)
                        for approval_id in sorted(pending)
                    )
                    snapshot = await runtime.engine.resume_async(
                        run_id,
                        approvals=decisions,
                    )
                return _render_engine_case(
                    runtime,
                    snapshot,
                    exact_to_name,
                    approval_resumed=approval_resumed,
                )
        finally:
            active_case = None

    try:
        evaluation_report = dataset.evaluate_sync(
            task,
            name=f"{provider}:{model}",
            max_concurrency=1,
            progress=False,
            metadata={
                "workflow_digest": workflow.definition_digest,
                "provider": provider,
                "model": model,
            },
        )
    finally:
        close_driver()
    rendered_cases: list[dict[str, JsonValue]] = []
    assertion_totals = {
        "task_completion": [],
        "tool_selection_correct": [],
        "tool_arguments_correct": [],
    }
    durations: list[float] = []
    for report_case in evaluation_report.cases:
        assertions = {
            name: bool(result.value)
            for name, result in report_case.assertions.items()
            if name in assertion_totals
        }
        for name, value in assertions.items():
            assertion_totals[name].append(value)
        durations.append(report_case.task_duration)
        case_usage = observations.get(report_case.name, [])
        rendered_cases.append(
            {
                "id": report_case.name,
                "output": _json_safe(report_case.output),
                "assertions": assertions,
                "duration_seconds": report_case.task_duration,
                "usage": [item.to_mapping() for item in case_usage],
            }
        )
    failures: list[dict[str, str]] = [
        {"id": failure.name, "code": "EVALUATION_CASE_FAILED"}
        for failure in evaluation_report.failures
    ]
    seen_cases = {
        item["id"] for item in (*rendered_cases, *failures)
    }
    failures.extend(
        {"id": case["id"], "code": "EVALUATION_CASE_MISSING"}
        for case in cases
        if case["id"] not in seen_cases
    )
    for assertions in assertion_totals.values():
        assertions.extend(False for _ in failures)
    complete = (
        len(rendered_cases) == len(cases)
        and not failures
        and all(all(values) for values in assertion_totals.values())
    )
    all_usage = [item for values in observations.values() for item in values]
    report: dict[str, JsonValue] = {
        "schema_version": EVALUATION_REPORT_V1,
        "result": "PASS" if complete else "FAIL",
        "created_at": datetime.now(UTC).isoformat(),
        "workflow": {
            "workflow_id": workflow.workflow_id,
            "version": workflow.version,
            "definition_digest": workflow.definition_digest,
        },
        "runtime": {
            "provider": provider,
            "model": model,
            "sdk_version": sdk_version,
            "pydantic_evals_version": _package_version("pydantic-evals"),
            "genai_prices_version": _package_version("genai-prices"),
            "max_concurrency": 1,
        },
        "summary": {
            "case_count": len(cases),
            "completed_case_count": len(rendered_cases),
            "failed_case_count": len(failures),
            "task_completion_rate": _rate(assertion_totals["task_completion"]),
            "tool_selection_correct_rate": _rate(
                assertion_totals["tool_selection_correct"]
            ),
            "tool_arguments_correct_rate": _rate(
                assertion_totals["tool_arguments_correct"]
            ),
            "latency_p50_seconds": _percentile(durations, 0.50),
            "latency_p95_seconds": _percentile(durations, 0.95),
            "request_count": _usage_total(all_usage, "request_count"),
            "input_tokens": _usage_total(all_usage, "input_tokens"),
            "output_tokens": _usage_total(all_usage, "output_tokens"),
            "total_tokens": _usage_total(all_usage, "total_tokens"),
            "estimated_cost": _estimate_cost(all_usage, model),
        },
        "cases": rendered_cases,
        "failures": failures,
        "release_gate": False,
    }
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


_DRIVER_CAPABILITIES = frozenset(
    {"host_managed_execution", "native_tools", "structured_output"}
)


def _evaluation_driver_factory(
    provider: str,
    model: str,
    instructions: str,
    observe: Any,
) -> tuple[str, str, DriverFactory, Callable[[], None]]:
    """Construct adapters through the same frozen project-driver boundary.

    The evaluation-only factory lets the report observe neutral SDK usage while
    retaining normal project routing and resume checks.  The provider adapter
    itself is still the production PydanticAI/Agents implementation.
    """

    driver_type = PydanticAIDriver if provider == "pydantic-ai" else OpenAIAgentsDriver
    deepseek_model = model.removeprefix("deepseek:") if model.startswith("deepseek:") else None
    if deepseek_model == "":
        raise WorkflowValidationError("DeepSeek evaluation model name is missing")
    client = _deepseek_client() if deepseek_model is not None else None

    def build(frozen_instructions: str) -> Any:
        if deepseek_model is not None:
            if provider == "pydantic-ai":
                from pydantic_ai import Agent
                from pydantic_ai.models.openai import OpenAIChatModel
                from pydantic_ai.providers.deepseek import DeepSeekProvider

                agent = Agent(
                    OpenAIChatModel(
                        deepseek_model,
                        provider=DeepSeekProvider(openai_client=client),
                    ),
                    instructions=frozen_instructions,
                    model_settings={
                        "extra_body": {
                            "thinking": {"type": "disabled"},
                            "max_tokens": 1024,
                        }
                    },
                )
            else:
                from agents import Agent, ModelSettings
                from agents.models.openai_responses import OpenAIResponsesModel

                agent = Agent(
                    name="workflow-agent",
                    instructions=frozen_instructions,
                    model=OpenAIResponsesModel(deepseek_model, openai_client=client),
                    model_settings=ModelSettings(
                        max_tokens=1024,
                        timeout=30,
                    ),
                )
            return driver_type(
                agent=agent,
                model=model,
                instructions=frozen_instructions,
                usage_observer=observe,
                model_name=model,
            )
        return driver_type(
            model=model,
            instructions=frozen_instructions,
            usage_observer=observe,
            model_name=model,
        )

    probe = build(instructions)
    driver_version = getattr(probe, "driver_version", None)
    if not isinstance(driver_version, str) or not driver_version:
        raise WorkflowValidationError("evaluation driver has no stable version")
    factory_name = f"evaluation-{provider}"
    first: list[Any] = [probe]

    def create(binding: DriverBinding, frozen_instructions: str) -> Any:
        if binding.model != model:
            raise WorkflowValidationError("evaluation model binding changed")
        return first.pop() if first else build(frozen_instructions)

    def close() -> None:
        if client is not None:
            asyncio.run(client.close())

    return (
        factory_name,
        driver_version,
        DriverFactory(
            factory_name,
            driver_version,
            _DRIVER_CAPABILITIES,
            (),
            create,
        ),
        close,
    )


def _deepseek_client() -> Any:
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise WorkflowValidationError("DEEPSEEK_API_KEY is required for DeepSeek evaluation")
    import httpx
    from openai import AsyncOpenAI

    return AsyncOpenAI(
        api_key=key,
        base_url="https://api.deepseek.com",
        max_retries=0,
        http_client=httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False),
    )


def _project_tool_manifests(
    project: PortableProject,
) -> tuple[Mapping[str, JsonValue], ...]:
    if project.tools_path is None:
        raise WorkflowValidationError("evaluation project has no tool catalogue")
    raw = _load_json(project.tools_path, "tool catalogue")
    if not isinstance(raw, list) or any(not isinstance(item, Mapping) for item in raw):
        raise WorkflowValidationError("evaluation tool catalogue must be a list")
    return tuple(dict(item) for item in raw)


def _manifest_tool_reference(manifest: Mapping[str, JsonValue]) -> ToolReference:
    try:
        return ToolReference.from_mapping(
            {
                key: manifest[key]
                for key in ("tool_id", "version", "package_digest")
            }
        )
    except KeyError as error:
        raise WorkflowValidationError(
            "evaluation tool manifest is missing its exact reference"
        ) from error


def _tool_identity(reference: ToolReference) -> tuple[str, str, str]:
    return reference.tool_id, reference.version, reference.package_digest


def _evaluation_target(
    subworkflows: Sequence[WorkflowSpec],
    definition: WorkflowSpec,
    step_id: str,
    driver_name: str,
    driver_version: str,
    model: str,
    manifests: Sequence[Mapping[str, JsonValue]],
) -> PortableTarget:
    tools = tuple(_manifest_tool_reference(item) for item in manifests)
    permissions: set[str] = set()
    credential_bindings: set[str] = set()
    for manifest in manifests:
        raw_permissions = manifest.get("permissions", [])
        if not isinstance(raw_permissions, list) or any(
            not isinstance(item, str) for item in raw_permissions
        ):
            raise WorkflowValidationError("evaluation tool permissions are invalid")
        permissions.update(raw_permissions)
        credential_ref = manifest.get("credential_ref")
        if isinstance(credential_ref, str):
            credential_bindings.add(credential_ref)
    return PortableTarget(
        trusted_tools=tools,
        allowed_permissions=frozenset(permissions),
        credential_bindings=frozenset(credential_bindings),
        driver_bindings=(
            DriverBinding(
                step_id,
                driver_name,
                driver_version,
                model,
                _DRIVER_CAPABILITIES,
                workflow_id=definition.workflow_id,
                workflow_version=definition.version,
            ),
        ),
        subworkflow_bindings={
            f"{item.workflow_id}@{item.version}": {
                "workflow_id": item.workflow_id,
                "version": item.version,
                "definition_digest": item.definition_digest,
            }
            for item in subworkflows
        },
    )


class _EvaluationToolSimulator:
    """Per-case deterministic tool boundary used by the real workflow engine."""

    def __init__(
        self,
        case: Mapping[str, Any],
        names: Mapping[tuple[str, str, str], str],
    ) -> None:
        self._lock = Lock()
        self._names = dict(names)
        expected_arguments = case.get("expected_arguments")
        arguments = expected_arguments if isinstance(expected_arguments, list) else []
        self._remaining = [
            {
                **dict(item),
                "expected_arguments": (
                    dict(arguments[index])
                    if index < len(arguments) and isinstance(arguments[index], Mapping)
                    else None
                ),
            }
            for index, item in enumerate(case.get("simulated_tool_results", []))
        ]

    def executor(
        self, manifests: Sequence[Mapping[str, JsonValue]]
    ) -> FunctionToolExecutor:
        executor = FunctionToolExecutor()
        for manifest in manifests:
            reference = _manifest_tool_reference(manifest)
            name = self._names.get(_tool_identity(reference), reference.tool_id)

            def invoke(
                arguments: Mapping[str, JsonValue],
                *,
                tool_name: str = name,
            ) -> object:
                result = self._take(tool_name, arguments)
                if not bool(result["succeeded"]):
                    error = result.get("error")
                    raise ToolExecutionFailedError(
                        error if isinstance(error, str) else "SIMULATED_TOOL_FAILED"
                    )
                return result.get("output")

            executor.register(reference, invoke)
        return executor

    def _take(
        self, name: str, arguments: Mapping[str, JsonValue]
    ) -> Mapping[str, Any]:
        with self._lock:
            matching = [
                index
                for index, item in enumerate(self._remaining)
                if item.get("name") == name
                and item.get("expected_arguments") == dict(arguments)
            ]
            if not matching:
                matching = [
                    index
                    for index, item in enumerate(self._remaining)
                    if item.get("name") == name
                ]
            if not matching:
                raise ToolExecutionFailedError("SIMULATED_TOOL_RESULT_MISSING")
            return self._remaining.pop(matching[0])


def _safe_case_id(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _all_events(runtime: ProjectRuntime, snapshot: RunSnapshot) -> list[RunEvent]:
    events: list[RunEvent] = []
    after = 0
    while True:
        page = list(
            runtime.engine.events(
                snapshot.run_id,
                after=after,
                limit=256,
                expected_execution_id=snapshot.execution_id,
            )
        )
        if not page:
            return events
        events.extend(page)
        after = page[-1].sequence
        if len(page) < 256:
            return events


def _render_engine_case(
    runtime: ProjectRuntime,
    snapshot: RunSnapshot,
    exact_to_name: Mapping[tuple[str, str, str], str],
    *,
    approval_resumed: bool,
) -> dict[str, Any]:
    actions = snapshot.state.get("actions", {})
    if not isinstance(actions, Mapping):
        actions = {}
    names: list[str] = []
    arguments: list[dict[str, JsonValue]] = []
    trajectory: list[dict[str, JsonValue]] = []
    events = _all_events(runtime, snapshot)
    for event in events:
        if event.type != "AGENT_ACTIONS_PROPOSED":
            continue
        turn_names: list[str] = []
        action_ids = event.payload.get("action_ids", [])
        if not isinstance(action_ids, list):
            continue
        for action_id in action_ids:
            action = actions.get(action_id)
            if not isinstance(action, Mapping):
                continue
            request = action.get("request")
            if not isinstance(request, Mapping):
                continue
            raw_tool = request.get("tool")
            raw_arguments = request.get("arguments")
            if not isinstance(raw_tool, Mapping) or not isinstance(
                raw_arguments, Mapping
            ):
                continue
            reference = ToolReference.from_mapping(raw_tool)
            name = exact_to_name.get(_tool_identity(reference), reference.tool_id)
            names.append(name)
            turn_names.append(name)
            arguments.append(dict(raw_arguments))
        turn = event.payload.get("turn")
        trajectory.append(
            {
                "turn": turn if isinstance(turn, int) else len(trajectory) + 1,
                "message": "",
                "tools": turn_names,
            }
        )
    agent_states = snapshot.state.get("agents", {})
    message = ""
    if isinstance(agent_states, Mapping):
        for state in agent_states.values():
            if isinstance(state, Mapping) and isinstance(state.get("message"), str):
                message = str(state["message"])
    if message:
        trajectory.append(
            {"turn": len(trajectory) + 1, "message": message, "tools": []}
        )
    v3 = snapshot.state.get("v3", {})
    structured_output = v3.get("output") if isinstance(v3, Mapping) else None
    failure = snapshot.state.get("failure")
    error_code = None
    if snapshot.status != "SUCCEEDED":
        error_code = (
            failure.get("code")
            if isinstance(failure, Mapping) and isinstance(failure.get("code"), str)
            else f"RUN_{snapshot.status}"
        )
    return {
        "message": message,
        "structured_output": structured_output,
        "tools": names,
        "arguments": arguments,
        "trajectory": trajectory,
        "approval_resumed": approval_resumed,
        "run_status": snapshot.status,
        "execution_id": snapshot.execution_id,
        "last_event_sequence": events[-1].sequence if events else 0,
        "error": error_code,
    }


def _normalize_cases(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise WorkflowValidationError("evaluation cases must be a non-empty list")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    allowed = {
        "id",
        "input",
        "expected_tools",
        "expected_arguments",
        "expected_message_contains",
        "prior_tool_results",
        "turn",
        "simulated_tool_results",
        "max_turns",
        "approval_resume",
    }
    for raw in value:
        if (
            not isinstance(raw, Mapping)
            or set(raw) - allowed
            or not {
                "id",
                "input",
                "expected_tools",
            }
            <= set(raw)
        ):
            raise WorkflowValidationError("evaluation case is malformed")
        case_id = raw["id"]
        inputs = raw["input"]
        tools = raw["expected_tools"]
        if (
            not isinstance(case_id, str)
            or not case_id
            or case_id in seen
            or not isinstance(inputs, Mapping)
            or not isinstance(tools, list)
            or any(not isinstance(item, str) for item in tools)
        ):
            raise WorkflowValidationError("evaluation case fields are invalid")
        seen.add(case_id)
        simulated = raw.get("simulated_tool_results")
        if simulated is not None:
            if not isinstance(simulated, list):
                raise WorkflowValidationError(
                    "evaluation simulated_tool_results must be a list"
                )
            for item in simulated:
                if (
                    not isinstance(item, Mapping)
                    or set(item) - {"name", "succeeded", "output", "error"}
                    or not {"name", "succeeded"} <= set(item)
                    or not isinstance(item["name"], str)
                    or not isinstance(item["succeeded"], bool)
                    or (item["succeeded"] and "error" in item)
                    or (
                        not item["succeeded"] and not isinstance(item.get("error"), str)
                    )
                ):
                    raise WorkflowValidationError(
                        "evaluation simulated tool result is invalid"
                    )
        max_turns = raw.get("max_turns", 4)
        if (
            not isinstance(max_turns, int)
            or isinstance(max_turns, bool)
            or not 1 <= max_turns <= 32
        ):
            raise WorkflowValidationError(
                "evaluation max_turns must be between 1 and 32"
            )
        if not isinstance(raw.get("approval_resume", False), bool):
            raise WorkflowValidationError("evaluation approval_resume must be boolean")
        result.append(json.loads(json.dumps(dict(raw))))
    return result


def _rate(values: list[bool]) -> float | str:
    return sum(values) / len(values) if values else "unknown"


def _percentile(values: list[float], fraction: float) -> float | str:
    if not values:
        return "unknown"
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def _usage_total(values: list[AgentInvocationUsage], field_name: str) -> int | str:
    raw = [getattr(item, field_name) for item in values]
    known = [item for item in raw if isinstance(item, int)]
    return sum(known) if known and len(known) == len(raw) else "unknown"


def _estimate_cost(values: list[AgentInvocationUsage], model: str) -> str:
    if not values or any(
        item.input_tokens is None or item.output_tokens is None for item in values
    ):
        return "unknown"
    try:
        from genai_prices import Usage, calc_price

        total = sum(
            (
                calc_price(
                    Usage(
                        input_tokens=item.input_tokens,
                        output_tokens=item.output_tokens,
                    ),
                    model,
                ).total_price
                for item in values
            ),
            start=0,
        )
    except (ArithmeticError, LookupError, TypeError, ValueError):
        # Price catalog gaps are data, not an evaluation failure.
        return "unknown"
    return str(total)


def _json_safe(value: object) -> JsonValue:
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _walk_steps(steps: Any):
    for step in steps:
        yield step
        for branch in step.children:
            yield from _walk_steps(branch)
