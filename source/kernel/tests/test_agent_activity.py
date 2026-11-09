from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace
from uuid import uuid4

from agents.testing import ScriptedModel, ModelStep, assistant_message, function_call
import httpx
import pytest

from kernel.tests.test_agent_state_integrity import _application, _create, _snapshot, _waiting
from kernel.tests.test_sdk_execution_governance import _final
from platform_kernel.agent_runtime import persistence
from platform_kernel.agent_runtime.persistence import timeline as persistence_timeline
from platform_kernel.agent_runtime.sdk_execution.activity import (
    _RunUsageCapture,
)
from platform_kernel.agent_runtime.sdk_execution.usage_ports import (
    CallbackActivityLog,
    CallbackActivityRecorder,
    ScopedActivityRecorder,
)
from platform_kernel.api import create_app
from platform_kernel.composition import PlatformApplication
from platform_kernel.database import connect_database, read_session_events


def _activities(app, session_id, revision=1):
    with connect_database(app.database) as connection:
        row = connection.execute(
            "SELECT agent_run_id FROM agent_runs WHERE session_id=? AND goal_revision=?",
            (session_id, revision),
        ).fetchone()
        run_id = row["agent_run_id"] if row is not None else connection.execute(
            "SELECT pending_agent_run_id FROM sessions WHERE session_id=? AND goal_revision=?",
            (session_id, revision),
        ).fetchone()[0]
        return persistence.project_activities(connection, session_id, (run_id,))[run_id]


def _record_open(app, run, *, kind="SEGMENT", activity_id="segment", segment_id="segment"):
    persistence.save_activity(app.database, run.session_id, {
        "agent_run_id": run.agent_run_id, "goal_revision": run.goal_revision,
        "segment_id": segment_id, "activity_id": activity_id,
        "kind": kind, "purpose": "INITIAL" if kind == "SEGMENT" else "PROCESSING",
        "status": "RUNNING", "tool_name": None, "tool_call_id": None, "error_code": None,
    })


def test_long_post_exposes_running_tool_and_retains_fast_activities(kernel_settings):
    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()

        async def observe(request):
            entered.set()
            await release.wait()
            return httpx.Response(200, json={"data": [{"text": "PRIVATE_OBSERVATION_BODY"}]})

        model = ScriptedModel([
            ModelStep(output=[function_call("retrieve_evidence", {"query": "PRIVATE_ARGUMENT"}, call_id="read")]),
            ModelStep(output=[_final()]),
        ])
        app = PlatformApplication(settings=kernel_settings("knowledge"), model=model,
                                  client=httpx.AsyncClient(transport=httpx.MockTransport(observe)))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(application=app)),
                                     base_url="http://kernel") as client:
            session_id = (await client.post("/api/v12/sessions", json={"projectId": "p"})).json()["sessionId"]
            accepted = await client.post(
                f"/api/v12/sessions/{session_id}/messages",
                json={
                    "requestId": str(uuid4()),
                    "message": "PRIVATE_USER_MESSAGE",
                    "requestedMode": "DIALOGUE",
                },
            )
            assert accepted.status_code == 202
            task = asyncio.create_task(
                app.interaction.resume_pending_start(session_id)
            )
            try:
                await asyncio.wait_for(entered.wait(), 10)
                history = (await client.get(f"/api/v12/sessions/{session_id}/messages")).json()
                activities = history["turns"][0]["activities"]
                assert not task.done()
                assert [(item["kind"], item["status"]) for item in activities] == [
                    ("SEGMENT", "RUNNING"), ("MODEL_REQUEST", "COMPLETE"), ("TOOL", "RUNNING"),
                ]
                release.set()
                await task
                finished = _activities(app, session_id)
                assert [item["kind"] for item in finished] == ["SEGMENT", "MODEL_REQUEST", "TOOL", "MODEL_REQUEST"]
                assert all(item["status"] == "COMPLETE" and item["finished_at"] for item in finished)
                assert len({item["activity_id"] for item in finished}) == 4
                with connect_database(app.database) as connection:
                    events = read_session_events(connection, session_id, "agent.activity")
                text = json.dumps(events)
                assert all(value not in text for value in ("PRIVATE_ARGUMENT", "PRIVATE_OBSERVATION_BODY", "PRIVATE_USER_MESSAGE"))
                for event in events:
                    assert set(event["data"]) == {
                        "agent_run_id", "goal_revision", "segment_id", "activity_id", "kind", "purpose",
                        "status", "tool_name", "tool_call_id", "error_code", "title", "summary",
                    } | ({"context_usage"} if event["data"]["kind"] == "MODEL_REQUEST" else set()) \
                      | ({"output_bytes", "output_tokens"} if event["data"]["kind"] == "TOOL" and event["data"]["status"] == "COMPLETE" else set())
                    if event["data"]["kind"] == "MODEL_REQUEST":
                        context = event["data"]["context_usage"]
                        assert set(context) == {"tokenizer", "total_tokens", "available_input_tokens", "excess_tokens", "breakdown"}
                        assert context["tokenizer"] == "o200k_base"
                        assert sum(context["breakdown"].values()) == context["total_tokens"]
                segment = finished[0]
                transitions = [event for event in events if event["data"]["activity_id"] == segment["activity_id"]]
                assert [event["data"]["status"] for event in transitions] == ["QUEUED", "RUNNING", "COMPLETE"]
                assert segment["created_at"] == transitions[0]["created_at"]
                assert segment["started_at"] == transitions[1]["created_at"]
                assert segment["finished_at"] == transitions[-1]["created_at"]
            finally:
                release.set()
                if not task.done():
                    await asyncio.gather(task, return_exceptions=True)
        await app.client.aclose()

    asyncio.run(exercise())


def test_retry_required_tool_activity_is_not_presented_as_complete():
    recorded = []
    hooks = _RunUsageCapture(
        1000,
        segment={"agent_run_id": "run", "goal_revision": 1, "segment_id": "segment"},
        activity_recorder=CallbackActivityRecorder(recorded.append),
        observation_names=frozenset(),
        purpose="PROCESSING",
    )
    context = SimpleNamespace(
        tool_call_id="call",
        tool_arguments=json.dumps({
            "progress_intent": {
                "capability": "workflow.operator.discovery",
                "objective": "查找稳定排序算子",
                "template_key": "workflow.operator.discovery",
            }
        }),
        context={},
    )
    tool = SimpleNamespace(name="list_operators")

    async def exercise():
        await hooks.on_tool_start(context, None, tool)
        await hooks.on_tool_end(
            context,
            None,
            tool,
            {
                "status": "RETRY_REQUIRED",
                "progressOutcome": {
                    "outcomeCode": "control.retry_required",
                    "facts": {},
                },
            },
        )

    asyncio.run(exercise())

    assert [item["status"] for item in recorded] == ["RUNNING", "FAILED"]
    assert recorded[-1]["summary"] == (
        "工具参数未通过合同校验，Agent 正在按合同修正后重试。"
    )


def test_waiting_for_compute_slot_is_queued(kernel_settings):
    async def exercise():
        app = PlatformApplication(settings=kernel_settings(), model=ScriptedModel([ModelStep(output=[_final()])]))
        queued = asyncio.Event()
        dependencies = app.agent_runtime.sdk_execution

        def record(session_id, payload):
            dependencies.activity_log.record(session_id, payload)
            if payload["kind"] == "SEGMENT" and payload["status"] == "QUEUED":
                queued.set()

        slots = asyncio.Semaphore(0)
        app.agent_runtime.sdk_execution = replace(
            dependencies,
            agent_slots=slots,
            activity_log=CallbackActivityLog(record),
        )
        session_id = app.interaction.create_session().session.session_id
        app.interaction.message(
            session_id, str(uuid4()), "hello", "DIALOGUE"
        )
        task = asyncio.create_task(app.interaction.resume_pending_start(session_id))
        await asyncio.wait_for(queued.wait(), 2)
        activity = _activities(app, session_id)[0]
        assert activity["status"] == "QUEUED" and activity["started_at"] is None
        slots.release()
        await task
        assert _activities(app, session_id)[0]["status"] == "COMPLETE"
        await app.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("invalid_arguments", [False, True])
def test_failed_observation_never_becomes_completed_tool(kernel_settings, invalid_arguments):
    arguments = {"unexpected": "secret"} if invalid_arguments else {"query": "secret"}
    steps = [ModelStep(output=[function_call(
        "retrieve_evidence", arguments, call_id="read",
    )])]
    if invalid_arguments:
        steps.append(ModelStep(output=[function_call(
            "retrieve_evidence", arguments, call_id="read-retry",
        )]))
    model = ScriptedModel(steps)
    app = PlatformApplication(settings=kernel_settings("knowledge"), model=model,
                              client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(503))))
    session_id = app.interaction.create_session().session.session_id
    from platform_kernel.errors import AgentRunError, UpstreamServiceError

    with pytest.raises(AgentRunError if invalid_arguments else UpstreamServiceError):
        app.interaction.message(
            session_id, str(uuid4()), "hello", "DIALOGUE"
        )
        asyncio.run(app.interaction.resume_pending_start(session_id))
    activities = _activities(app, session_id)
    tool = [item for item in activities if item["kind"] == "TOOL"][-1]
    assert tool["status"] == "FAILED"
    assert tool["error_code"] == ("AGENT_MODEL_OUTPUT_INVALID" if invalid_arguments else "OBSERVATION_SERVICE_UNAVAILABLE")
    assert activities[0]["status"] == "FAILED"
    assert len(model.calls) == (2 if invalid_arguments else 1)


@pytest.mark.parametrize("with_state", [False, True])
def test_recovery_interrupts_activities_atomically(kernel_settings, monkeypatch, with_state):
    app = _application(kernel_settings())
    session = app.interaction.create_session().session
    run = _create(app, session)
    if with_state:
        run = _waiting(app, run)
        persistence.claim_state(app.database, run, expected_status="WAITING_CONFIRMATION")
        run = app.agent_runtime.get(run.agent_run_id)
    _record_open(app, run)
    _record_open(app, run, kind="MODEL_REQUEST", activity_id="model")
    before = _snapshot(app.database)
    append = persistence_timeline.append_event

    def fail_activity(connection, session_id, event_type, payload):
        append(connection, session_id, event_type, payload)
        if event_type == "agent.activity":
            raise RuntimeError("activity write failed")

    with monkeypatch.context() as patch:
        patch.setattr(persistence_timeline, "append_event", fail_activity)
        with pytest.raises(RuntimeError, match="activity write failed"):
            persistence.recover_running_run(
                app.database, run, app.agent_projection
            )
    assert _snapshot(app.database) == before
    assert persistence.recover_running_run(
        app.database, run, app.agent_projection
    ) == ("WAITING_CONFIRMATION" if with_state else "FAILED")
    assert {activity["status"] for activity in _activities(app, session.session_id)} == {"INTERRUPTED"}
    after = _snapshot(app.database)
    app.agent_runtime.recover_interrupted()
    assert _snapshot(app.database) == after


def test_recovery_after_claim_without_segment_still_notifies(kernel_settings):
    app = _application(kernel_settings())
    session = app.interaction.create_session().session
    waiting = _waiting(app, _create(app, session))
    persistence.claim_state(app.database, waiting, expected_status="WAITING_CONFIRMATION")
    before = app.interaction.events_after(session.session_id, 0)[-1]["event_id"]
    app.agent_runtime.recover_interrupted()
    events = app.interaction.events_after(session.session_id, before)
    assert [item["event_type"] for item in events] == ["agent.waiting_confirmation"]


@pytest.mark.parametrize("status,code", [("COMPLETE", None), ("FAILED", "UNATTRIBUTED_FAILURE")])
def test_terminal_does_not_invent_open_child_success_or_failure(kernel_settings, status, code):
    app = _application(kernel_settings())
    session = app.interaction.create_session().session
    run = _create(app, session)
    _record_open(app, run)
    _record_open(app, run, kind="MODEL_REQUEST", activity_id="model")
    persistence.finish_run(
        app.database, run, agent_projection=app.agent_projection,
        status=status, output=None,
                           usage=persistence.UsageDelta(0, 0, 0), error=code)
    activities = _activities(app, session.session_id)
    assert activities[0]["status"] == status
    assert activities[1]["status"] == "INTERRUPTED"


def test_plan_event_failure_rolls_back_plan(kernel_settings, monkeypatch):
    app = _application(kernel_settings())
    session = app.interaction.create_session().session
    run = _create(app, session)
    append = persistence_timeline.append_event

    def fail_plan(connection, session_id, event_type, payload):
        append(connection, session_id, event_type, payload)
        raise RuntimeError("plan event failed")

    with monkeypatch.context() as patch:
        patch.setattr(persistence_timeline, "append_event", fail_plan)
        with pytest.raises(RuntimeError, match="plan event failed"):
            persistence.save_plan(app.database, run.agent_run_id, "plan", [], "reason")
    assert app.agent_runtime.get(run.agent_run_id).plans == ()
    assert persistence.save_plan(app.database, run.agent_run_id, "plan", [], "reason") == 1
    with connect_database(app.database) as connection:
        summary = persistence.session_run_summaries(connection, session.session_id, "p", (1,))[1]
    assert summary["plans"] == [{"revision": 1, "goal": "plan", "steps": [], "reason": "reason"}]


def test_rejection_uses_new_segment_without_changing_serialized_context(kernel_settings):
    from kernel.tests.test_recommendations import _application as review_application, _execute, _inspect, _plan, _reply

    app, client, _ = review_application(kernel_settings, [_inspect(), _plan(), _execute(), _reply(cited=False)])
    session_id = client.post("/api/v12/sessions", json={"projectId": "p"}).json()["sessionId"]
    accepted = client.post(
        f"/api/v12/sessions/{session_id}/messages",
        json={
            "requestId": str(uuid4()),
            "message": "summarize",
            "requestedMode": "AUTO",
        },
    )
    assert accepted.status_code == 202
    route = app.interaction.claim_route(accepted.json()["agentRunId"])
    assert route is not None
    app.interaction.resolve_route(
        route.agent_run_id,
        "DIALOGUE",
        "EXECUTION_PROPOSAL",
        provider="test",
        model="fixed-router",
        trace_id=f"route-{route.agent_run_id}",
        provider_requests=1,
        input_tokens=1,
        output_tokens=1,
        total_tokens=2,
    )
    asyncio.run(app.interaction.resume_pending_start(session_id))
    proposed = client.get(f"/api/v12/sessions/{session_id}").json()
    with connect_database(app.database) as connection:
        state = connection.execute("SELECT state_json FROM agent_run_state").fetchone()[0]
    assert "segment_id" not in state and "activity_id" not in state
    assert all(item["status"] == "COMPLETE" for item in _activities(app, session_id))
    assert client.post(f"/api/v12/sessions/{session_id}/reject", json={
        "confirmationId": proposed["confirmation"]["confirmationId"], "reason": "reconsider",
    }).status_code == 200
    asyncio.run(app.interaction.tick())
    activities = _activities(app, session_id)
    segments = [item for item in activities if item["kind"] == "SEGMENT"]
    assert [item["purpose"] for item in segments] == ["INITIAL", "RESUME_REJECTION"]
    assert len({item["segment_id"] for item in segments}) == 2
    assert all(item["status"] == "COMPLETE" for item in activities)
    tools = [item for item in activities if item["kind"] == "TOOL"]
    assert len(tools) == 1 and tools[0]["tool_name"] == "inspect_data_version"


def test_tool_hooks_filter_metadata_and_repeated_call_id_by_segment(kernel_settings):
    app = _application(kernel_settings())
    session = app.interaction.create_session().session
    run = _create(app, session)

    async def exercise():
        for segment_id in ("old-segment", "resumed-segment"):
            hooks = _RunUsageCapture(
                1000, segment={"agent_run_id": run.agent_run_id, "goal_revision": 1,
                               "segment_id": segment_id},
                activity_recorder=ScopedActivityRecorder(
                    app.agent_runtime.sdk_execution.activity_log,
                    session.session_id,
                ),
                observation_names=frozenset({"inspect_data_version"}), purpose="PROCESSING",
            )
            for name in ("inspect_data_version", "get_operator", "record_plan", "submit_reply", "run_summary"):
                tool = SimpleNamespace(name=name)
                declared = name == "get_operator"
                context = SimpleNamespace(
                    tool_call_id="replayed-call",
                    tool_arguments=json.dumps({
                        "progress_intent": {
                            "capability": "workflow.operator.inspect",
                            "objective": "核验所选算子",
                            "template_key": "workflow.operator.inspect",
                        }
                    }) if declared else '"PRIVATE_ARGUMENT"',
                )
                await hooks.on_tool_start(context, None, tool)
                await hooks.on_tool_end(
                    context,
                    None,
                    tool,
                    {
                        "progressOutcome": {
                            "outcomeCode": "operator.inspected", "facts": {},
                        }
                    } if declared else "PRIVATE_OUTPUT",
                )

    asyncio.run(exercise())
    activities = _activities(app, session.session_id)
    assert [item["purpose"] for item in activities] == [
        "OBSERVATION", "DECLARED_TOOL", "OBSERVATION", "DECLARED_TOOL",
    ]
    assert len({item["activity_id"] for item in activities}) == 4
    assert len({item["segment_id"] for item in activities}) == 2
    assert {item["tool_call_id"] for item in activities} == {"replayed-call"}
    assert "PRIVATE_" not in json.dumps(activities)


@pytest.mark.parametrize("invalid", ["json", "size"])
def test_invalid_compaction_activity_fails_before_any_complete_event(kernel_settings, invalid):
    from kernel.tests.test_memory import _application as memory_application, _run, _summary
    from platform_kernel.errors import AgentRunError

    response = "invalid" if invalid == "json" else json.dumps({**_summary(), "goals": ["large " * 4000]})
    model = ScriptedModel([ModelStep(output=[assistant_message(response)])])
    app = memory_application(kernel_settings, model, constrained=True)
    with pytest.raises(AgentRunError, match="AGENT_MEMORY_COMPACTION_INVALID"):
        _run(app, model)
    with connect_database(app.database) as connection:
        summary = persistence.session_run_summaries(connection, "session", "project", (10,))[10]
        events = read_session_events(connection, "session", "agent.activity")
    compaction = next(item for item in summary["activities"] if item["purpose"] == "COMPACTION")
    assert compaction["status"] == "FAILED"
    assert compaction["error_code"] == "AGENT_MEMORY_COMPACTION_INVALID"
    assert [event["data"]["status"] for event in events
            if event["data"]["activity_id"] == compaction["activity_id"]] == ["RUNNING", "FAILED"]
