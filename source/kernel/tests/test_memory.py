from __future__ import annotations

from kernel.tests.task_testing import preserve_context_headroom, task_overhead


import asyncio
from dataclasses import replace
import json

from agents import Agent, RunConfig, Usage, UserError
from agents.testing import ScriptedModel, ModelStep, assistant_message
from kernel.tests.reply_testing import submit_reply
import httpx
import httpx2
from openai import APIConnectionError
import pytest

from platform_kernel.composition import PlatformApplication
from platform_kernel.database import connect_database
from platform_kernel.errors import AgentRunError, UpstreamServiceError
from platform_kernel.agent_runtime.context_usage import context_tokens
from platform_kernel.agent_runtime.memory import token_count
from platform_kernel.agent_runtime.models import MemorySummaryV3
from platform_kernel.agent_runtime import sdk_execution
from platform_kernel.agent_runtime.sdk_execution.activity import _RunUsageCapture
from platform_kernel.agent_runtime.sdk_execution.context import _compact, _initial_turns
from platform_kernel.agent_runtime.sdk_execution.usage_ports import ScopedActivityRecorder


def test_recent_two_full_turns_survive_a_newer_memory_watermark():
    history = tuple(
        item
        for revision in range(1, 6)
        for item in (
            (revision, "user", f"user-{revision}"),
            (revision, "assistant", f"assistant-{revision}"),
        )
    )

    turns = _initial_turns(history, through=5)

    assert [item.goal_revision for item in turns] == [4, 5]


def _final(message: str):
    return submit_reply(message)


def _summary():
    return {
        "goals": ["analyze"],
        "constraints": ["preserve identifiers"],
        "decisions": ["use knowledge"],
        "unresolved_items": [],
    }


def _history(count: int = 9):
    return tuple(
        item
        for revision in range(1, count + 1)
        for item in (
            (revision, "user", f"turn {revision} " + "x " * 100),
            (revision, "assistant", f"answer {revision} " + "y " * 100),
        )
    )


def _application(
    kernel_settings, model, *, constrained: bool = False,
    context_window_tokens: int = 3800,
):
    settings = kernel_settings("knowledge")
    if constrained:
        settings = replace(
            settings,
            agent_context_window_tokens=context_window_tokens,
            agent_max_output_tokens=500,
        )
    application = PlatformApplication(
        settings=settings,
        model=model,
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: None)),
    )
    with connect_database(application.database) as connection:
        connection.execute(
            "INSERT INTO sessions (session_id, project_id, goal_revision, agent_run_id, pending_agent_run_id, agent_start_state, owner_subject) VALUES ('session','project',10,NULL,NULL,NULL,'local-researcher')"
        )
    preserve_context_headroom(application)
    return application


def _run(application, model, run_id: str = "run"):
    application.agent_runtime.sdk_execution = replace(
        application.agent_runtime.sdk_execution,
        model=model,
    )
    return asyncio.run(
        application.agent_runtime.start(
            "session",
            "project",
            10,
            "current",
            _history(),
            agent_run_id=run_id,
        )
    )


def _seed_action(application, *, resource_id: str = "resource-1") -> None:
    output = {
        "kind": "data_version",
        "service": "data-primary",
        "dataset_id": "dataset-1",
        "version_id": resource_id,
        "semantic_id": "semantics-1",
        "object_id": "object-1",
        "media_type": "application/json",
        "format": "json",
        "size_bytes": 12,
        "checksum": "sha256:" + "a" * 64,
    }
    with connect_database(application.database) as connection:
        connection.execute(
            """INSERT INTO agent_runs VALUES (
                   'prior', 'session', 'project', 9, 'deepseek', 'test-model',
                   'COMPLETE', '["RUNNING","COMPLETE"]', NULL, 0, 0, 0,
                   'trace-prior', NULL, '2026-01-01T00:00:00Z',
                   '2026-01-01T00:00:00Z', '2026-01-01T00:00:01Z', 1000
               )"""
        )
        connection.execute(
            """INSERT INTO agent_actions VALUES (
                   'call-1', 'prior', 0, 'run_model',
                   'model-id', '2.0.0', 'sha256:0000000000000000000000000000000000000000000000000000000000000000',
                   'workflow-id', '3.0.0', 'sha256:1111111111111111111111111111111111111111111111111111111111111111',
                   1, ?, '{}', 'sha256:schema', 'fingerprint', 'APPROVED', NULL
               )""",
            (json.dumps({
                "resource_id": "argument-must-not-project",
                "observation": "observation-must-not-project",
            }),),
        )
        connection.execute(
            """INSERT INTO workflow_runs(
                   run_id,agent_run_id,session_id,tool_call_id,position,project_id,
                   model_id,model_version,model_checksum,workflow_id,workflow_version,
                   workflow_checksum,execution_service,executor_id,arguments_json,input_references_json,status,
                   remote_job_id,outputs_json,error,created_at,started_at,finished_at,duration_ms
               ) VALUES (
                   'workflow-run-1', 'prior', 'session', 'call-1', 0, 'project',
                   'model-id', '2.0.0', 'sha256:0000000000000000000000000000000000000000000000000000000000000000',
                   'workflow-id', '3.0.0', 'sha256:1111111111111111111111111111111111111111111111111111111111111111',
                   'executor', 'workflow-dag', '{}', '{}', 'SUCCEEDED', NULL, ?, NULL,
                   '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z',
                   '2026-01-01T00:00:01Z', 1000
               )""",
            (json.dumps([output]),),
        )
def test_small_context_preserves_structured_roles_without_compaction(
    kernel_settings,
) -> None:
    model = ScriptedModel([ModelStep(output=[_final("done")])])
    application = _application(kernel_settings, model)

    run = _run(application, model)

    assert run.status == "COMPLETE"
    assert len(model.calls) == 1
    assert any(tool.name == "submit_reply" for tool in model.calls[-1].tools)
    assert isinstance(model.calls[0].input, list)
    assert [item["role"] for item in model.calls[0].input] == [
        *(["user", "assistant"] * 9),
        "user",
    ]
    assert model.calls[0].input[-1] == {"role": "user", "content": "current"}


def test_high_water_compacts_oldest_turns_and_reuses_summary(kernel_settings) -> None:
    model = ScriptedModel(
        [
            ModelStep(output=[assistant_message(json.dumps(_summary()))]),
            ModelStep(output=[_final("first")]),
        ]
    )
    application = _application(kernel_settings, model, constrained=True)

    first = _run(application, model, "first")

    assert first.status == "COMPLETE"
    assert len(model.calls) == 2
    compact_call, main_call = model.calls
    assert compact_call.tools == []
    assert compact_call.model_settings.max_tokens == 500
    assert f"within {(application.settings.agent_context_window_tokens - 500) // 10} tokens" in compact_call.system_instructions
    compact_payload = json.loads(compact_call.input[0]["content"])
    compacted_revisions = [
        item["goal_revision"] for item in compact_payload["turns"]
    ]
    assert compacted_revisions == list(range(1, 8))
    assert [item["role"] for item in main_call.input] == [
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
    ]
    assert main_call.input[1]["content"].startswith("turn 8 ")
    assert main_call.input[-1] == {"role": "user", "content": "current"}
    with connect_database(application.database) as connection:
        row = connection.execute(
            "SELECT * FROM agent_memory WHERE session_id = 'session'"
        ).fetchone()
    assert row["through_goal_revision"] == 7
    assert json.loads(row["summary_json"]) == _summary()

    reused = ScriptedModel([ModelStep(output=[_final("reused")])])
    second = _run(application, reused, "second")
    assert second.output.message == "reused"
    assert len(reused.calls) == 1
    assert reused.calls[0].input[0]["role"] == "assistant"
    assert "MemorySummaryV3" in reused.calls[0].input[0]["content"][0]["text"]


def test_invalid_compaction_counts_usage_and_terminalizes_run(kernel_settings) -> None:
    model = ScriptedModel(
        [
            ModelStep(
                output=[assistant_message("invalid")],
                usage=Usage(input_tokens=11, output_tokens=7, total_tokens=18),
            )
        ]
    )
    application = _application(kernel_settings, model, constrained=True)

    with pytest.raises(
        AgentRunError, match="AGENT_MEMORY_COMPACTION_INVALID"
    ) as failure:
        _run(application, model, "failed")

    run = application.agent_runtime.get(failure.value.agent_run_id)
    assert run.status == "FAILED"
    assert run.error == "AGENT_MEMORY_COMPACTION_INVALID"
    assert (run.input_tokens, run.output_tokens, run.total_tokens) == (11, 7, 18)


def test_compaction_provider_failure_maps_to_existing_upstream_error(
    kernel_settings,
) -> None:
    model = ScriptedModel(
        [
            APIConnectionError(
                request=httpx2.Request("POST", "http://agent.test/v1")
            )
        ]
    )
    application = _application(kernel_settings, model, constrained=True)

    with pytest.raises(
        UpstreamServiceError, match="AGENT_PROVIDER_UNAVAILABLE"
    ) as failure:
        _run(application, model, "provider-failed")

    run = application.agent_runtime.get(failure.value.agent_run_id)
    assert run.status == "FAILED"
    assert run.error == "AGENT_PROVIDER_UNAVAILABLE"


@pytest.mark.parametrize(
    ("cause", "error_type", "code"),
    [
        (None, AgentRunError, "AGENT_SDK_CONFIGURATION_INVALID"),
        (UpstreamServiceError("AGENT_PROVIDER_UNAVAILABLE"), UpstreamServiceError,
         "AGENT_PROVIDER_UNAVAILABLE"),
        (AgentRunError("AGENT_CONTEXT_TOO_LARGE"), AgentRunError,
         "AGENT_CONTEXT_TOO_LARGE"),
    ],
)
def test_compaction_sdk_user_error_terminalizes_with_original_cause(
    kernel_settings, cause, error_type, code,
) -> None:
    error = UserError("private SDK detail")
    error.__cause__ = cause
    model = ScriptedModel([error])
    application = _application(kernel_settings, model, constrained=True)

    with pytest.raises(error_type, match=code) as failure:
        _run(application, model, "sdk-failed")

    run = application.agent_runtime.get(failure.value.agent_run_id)
    assert run.status == "FAILED"
    assert run.error == code
    assert "private SDK detail" not in str(failure.value)
    with connect_database(application.database) as connection:
        events = connection.execute(
            "SELECT event_type FROM session_events WHERE session_id = 'session'"
        ).fetchall()
    assert sum(row[0] == "agent.failed" for row in events) == 1


def test_successful_compaction_and_main_failure_commit_memory_with_usage(
    kernel_settings,
) -> None:
    model = ScriptedModel(
        [
            ModelStep(
                output=[assistant_message(json.dumps(_summary()))],
                usage=Usage(input_tokens=1, output_tokens=2, total_tokens=3),
            ),
            ModelStep(
                output=[assistant_message("invalid")],
                usage=Usage(input_tokens=4, output_tokens=5, total_tokens=9),
            ),
            ModelStep(
                output=[assistant_message("still invalid")],
                usage=Usage(input_tokens=6, output_tokens=7, total_tokens=13),
            ),
        ]
    )
    application = _application(
        kernel_settings, model, constrained=True, context_window_tokens=4200,
    )

    with pytest.raises(AgentRunError, match="AGENT_MODEL_OUTPUT_INVALID") as failure:
        _run(application, model, "failed-main")

    run = application.agent_runtime.get(failure.value.agent_run_id)
    assert (run.input_tokens, run.output_tokens, run.total_tokens) == (11, 14, 25)
    with connect_database(application.database) as connection:
        memory = connection.execute(
            "SELECT through_goal_revision FROM agent_memory WHERE session_id = 'session'"
        ).fetchone()
    assert memory["through_goal_revision"] == 7


def test_oversized_compaction_is_rejected_without_persisting_memory(
    kernel_settings,
) -> None:
    oversized = {**_summary(), "goals": ["z " * 4000]}
    model = ScriptedModel(
        [
            ModelStep(
                output=[assistant_message(json.dumps(oversized))],
                usage=Usage(input_tokens=3, output_tokens=5, total_tokens=8),
            )
        ]
    )
    application = _application(kernel_settings, model, constrained=True)

    with pytest.raises(AgentRunError, match="AGENT_MEMORY_COMPACTION_INVALID") as failure:
        _run(application, model, "still-too-large")

    run = application.agent_runtime.get(failure.value.agent_run_id)
    assert (run.input_tokens, run.output_tokens, run.total_tokens) == (3, 5, 8)
    assert len(model.calls) == 1
    with connect_database(application.database) as connection:
        memory = connection.execute(
            "SELECT through_goal_revision FROM agent_memory WHERE session_id = 'session'"
        ).fetchone()
    assert memory is None


@pytest.mark.parametrize(
    ("provider", "summary_words", "accepted"),
    [("deepseek", 1600, True), ("deepseek", 4096, False), ("minimax", 1600, False)],
)
def test_large_context_summary_bound_preserves_provider_policy(
    kernel_settings, provider, summary_words, accepted,
) -> None:
    summary = {**_summary(), "goals": ["context " * summary_words]}
    assert token_count(summary) > 1024
    assert (token_count(summary) <= 4096) == (summary_words == 1600)
    model = ScriptedModel([
        ModelStep(output=[assistant_message(json.dumps(summary))]),
        ModelStep(output=[_final("done")]),
    ])
    settings = replace(
        kernel_settings("knowledge"),
        agent_provider=provider,
        agent_protocol="chat_completions" if provider == "minimax" else "responses",
        agent_context_window_tokens=60000,
        agent_max_output_tokens=8192,
    )
    application = PlatformApplication(
        settings=settings,
        model=model,
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: None)),
    )
    with connect_database(application.database) as connection:
        connection.execute(
            "INSERT INTO sessions (session_id, project_id, goal_revision, agent_run_id, pending_agent_run_id, agent_start_state, owner_subject) VALUES ('session','project',10,NULL,NULL,NULL,'local-researcher')"
        )
    history = tuple(
        (revision, role, f"turn {revision} " + "history " * 2500)
        for revision in range(1, 10)
        for role in ("user", "assistant")
    )

    async def run():
        return await application.agent_runtime.start(
            "session", "project", 10, "current", history, agent_run_id="run"
        )

    if accepted:
        assert asyncio.run(run()).status == "COMPLETE"
        assert len(model.calls) == 2
        recent_input = model.calls[1].input[-5:]
        assert [item["role"] for item in recent_input] == [
            "user", "assistant", "user", "assistant", "user",
        ]
        assert [
            item["content"] if item["role"] == "user" else item["content"][0]["text"]
            for item in recent_input
        ] == [content for _, _, content in history[-4:]] + ["current"]
    else:
        with pytest.raises(AgentRunError, match="AGENT_MEMORY_COMPACTION_INVALID"):
            asyncio.run(run())
        assert len(model.calls) == 1
        assert application.agent_runtime.get("run").status == "FAILED"

    compact_call = model.calls[0]
    limit = 4096 if provider == "deepseek" else 1024
    assert f"within {limit} tokens" in compact_call.system_instructions
    assert compact_call.model_settings.max_tokens == (8192 if provider == "deepseek" else 1024)
    with connect_database(application.database) as connection:
        stored = connection.execute(
            "SELECT summary_json FROM agent_memory WHERE session_id = 'session'"
        ).fetchone()
    assert (stored is not None) == accepted
    if accepted:
        assert json.loads(stored["summary_json"]) == summary


def test_small_context_summary_remains_bounded_below_deepseek_maximum(kernel_settings):
    summary = {**_summary(), "goals": ["context " * 450]}
    assert 330 < token_count(summary) < 4096
    model = ScriptedModel([ModelStep(output=[assistant_message(json.dumps(summary))])])
    application = _application(kernel_settings, model, constrained=True)

    with pytest.raises(AgentRunError, match="AGENT_MEMORY_COMPACTION_INVALID"):
        _run(application, model)

    assert len(model.calls) == 1
    assert f"within {(application.settings.agent_context_window_tokens - 500) // 10} tokens" in model.calls[0].system_instructions
    with connect_database(application.database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM agent_memory").fetchone()[0] == 0


@pytest.mark.parametrize("provider", ["deepseek", "minimax"])
@pytest.mark.parametrize("output_limit", [16, 64])
def test_compaction_respects_small_configured_output_limit(
    kernel_settings, provider, output_limit,
):
    summary = _summary()
    assert 16 < token_count(summary) < 64
    model = ScriptedModel([
        ModelStep(output=[assistant_message(json.dumps(summary))]),
        ModelStep(output=[_final("done")]),
    ])
    application = _application(kernel_settings, model, constrained=True)
    settings = replace(application.settings, agent_provider=provider,
                       agent_context_window_tokens=3400 + task_overhead(application),
                       agent_max_output_tokens=output_limit)
    application.agent_runtime.sdk_execution = replace(
        application.agent_runtime.sdk_execution, settings=settings,
        model_settings=replace(application.agent_runtime.sdk_execution.model_settings,
                               max_tokens=output_limit),
    )
    if output_limit == 16:
        with pytest.raises(AgentRunError, match="AGENT_MEMORY_COMPACTION_INVALID"):
            _run(application, model)
        assert application.agent_runtime.get("run").status == "FAILED"
        assert len(model.calls) == 1
    else:
        assert _run(application, model).status == "COMPLETE"
        assert len(model.calls) == 2
    assert f"within {output_limit} tokens" in model.calls[0].system_instructions
    assert all(call.model_settings.max_tokens == output_limit for call in model.calls)
    with connect_database(application.database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM agent_memory").fetchone()[0] == (
            1 if output_limit == 64 else 0
        )


@pytest.mark.parametrize("overflow", [False, True])
def test_compactor_input_guard_reserves_its_actual_output(kernel_settings, overflow):
    model = ScriptedModel([
        ModelStep(output=[assistant_message(json.dumps(_summary()))]),
        ModelStep(output=[assistant_message(json.dumps(_summary()))]),
    ])
    application = _application(kernel_settings, model)
    dependencies = application.agent_runtime.sdk_execution
    dependencies = replace(
        dependencies,
        settings=replace(dependencies.settings, agent_provider="minimax",
                         agent_context_window_tokens=10000, agent_max_output_tokens=500),
    )
    inputs = [{"role": "user", "content": "history " * 500}]

    async def compact(current):
        hooks = _RunUsageCapture(
            10000, segment={"agent_run_id": "compactor", "goal_revision": 10,
                            "segment_id": "compactor-segment"},
            activity_recorder=ScopedActivityRecorder(current.activity_log, "session"),
            observation_names=frozenset(), purpose="COMPACTION",
        )
        return await _compact(
            current, RunConfig(tracing_disabled=True), inputs, summary_limit=64,
            deadline_at=asyncio.get_running_loop().time() + 10,
            activity_hooks=hooks,
        )

    asyncio.run(compact(dependencies))
    call = model.calls[0]
    compactor = Agent(name="Memory Compactor", instructions=call.system_instructions,
                      output_type=MemorySummaryV3)
    input_tokens = context_tokens(compactor, compactor.instructions, inputs)
    window = input_tokens + 64 - int(overflow)
    assert input_tokens > window - dependencies.settings.agent_max_output_tokens
    dependencies = replace(dependencies, settings=replace(
        dependencies.settings, agent_context_window_tokens=window,
    ))
    if overflow:
        with pytest.raises(sdk_execution.SDKExecutionError, match="AGENT_CONTEXT_TOO_LARGE") as failure:
            asyncio.run(compact(dependencies))
        assert failure.value.diagnostics["available_input_tokens"] == window - 64
        assert failure.value.diagnostics["excess_tokens"] == 1
        assert failure.value.diagnostics["breakdown"]["tools"] > 0
        assert len(model.calls) == 1
    else:
        summary, _ = asyncio.run(compact(dependencies))
        assert summary.model_dump() == _summary()
        assert len(model.calls) == 2
        assert input_tokens + model.calls[-1].model_settings.max_tokens == window


def test_oversized_current_message_fails_without_calling_provider(
    kernel_settings,
) -> None:
    model = ScriptedModel([])
    application = _application(kernel_settings, model)
    application.settings = replace(
        application.settings,
        agent_context_window_tokens=900,
        agent_max_output_tokens=200,
    )
    application.agent_runtime.settings = application.settings
    application.agent_runtime.sdk_execution = replace(
        application.agent_runtime.sdk_execution,
        settings=application.settings,
    )

    with pytest.raises(AgentRunError, match="AGENT_CONTEXT_TOO_LARGE") as failure:
        asyncio.run(
            application.agent_runtime.start(
                "session",
                "project",
                10,
                "z " * 1000,
                (),
                agent_run_id="too-large",
            )
        )
    assert failure.value.diagnostics["kind"] == "CONTEXT_LIMIT"
    assert failure.value.diagnostics["breakdown"]["dialogue"] >= 1000

    assert model.calls == ()
    assert application.agent_runtime.get("too-large").error == (
        "AGENT_CONTEXT_TOO_LARGE"
    )


def test_execution_context_projects_authoritative_facts_without_arguments(
    kernel_settings,
) -> None:
    model = ScriptedModel([ModelStep(output=[_final("done")])])
    application = _application(kernel_settings, model)
    _seed_action(application)

    _run(application, model)

    instructions = model.calls[0].system_instructions
    assert '"model-id"' not in instructions
    assert '"workflow-id"' not in instructions
    assert '"workflow-run-1"' in instructions
    assert '"count":1' in instructions
    assert '"retrieval_tools":["list_execution_facts","get_execution_fact"]' in instructions
    assert {tool.name for tool in model.calls[0].tools} >= {
        "list_execution_facts", "get_execution_fact",
    }
    assert '"resource_id":"resource-1"' not in instructions
    assert "argument-must-not-project" not in instructions
    assert "observation-must-not-project" not in instructions


def test_oversized_old_success_is_retrievable_without_bloating_initial_context(
    kernel_settings,
) -> None:
    model = ScriptedModel([ModelStep(output=[_final("done")])])
    application = _application(kernel_settings, model)
    huge_resource_id = "resource-" + "z" * 100_000
    _seed_action(application, resource_id=huge_resource_id)
    constrained = replace(
        application.settings,
        agent_context_window_tokens=16_000,
        agent_max_output_tokens=1_000,
    )
    application.settings = constrained
    application.agent_runtime.settings = constrained
    application.agent_runtime.sdk_execution = replace(
        application.agent_runtime.sdk_execution,
        settings=constrained,
    )

    run = asyncio.run(
        application.agent_runtime.start(
            "session",
            "project",
            10,
            "current",
            (),
            agent_run_id="facts-retrievable",
        )
    )
    assert run.status == "COMPLETE"
    assert huge_resource_id not in model.calls[0].system_instructions
    assert "workflow-run-1" in model.calls[0].system_instructions
