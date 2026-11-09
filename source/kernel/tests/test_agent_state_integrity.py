from __future__ import annotations


import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace

from agents.testing import ScriptedModel
import httpx
import pytest

from platform_kernel.agent_runtime import persistence
from platform_kernel.agent_runtime.persistence import run_lifecycle, run_recovery
from platform_kernel.agent_runtime.memory import MemoryUpdate
from platform_kernel.agent_runtime.models import AgentFinalV5, MemorySummaryV3
from platform_kernel.agent_runtime.persistence import UsageDelta
from platform_kernel.agent_runtime.persistence import load_execution_context
from platform_kernel.agent_runtime.result_processing import PreparedAction
from platform_kernel.composition import PlatformApplication
from platform_kernel.contracts import JobStatusV6
from platform_kernel.database import connect_database, utc_now
from platform_kernel.errors import AgentRunError


def _application(settings):
    return PlatformApplication(
        settings=settings,
        model=ScriptedModel([]),
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(500))),
    )


def _create(application, session, run_id="run", revision=1):
    persistence.create_run(
        application.database,
        agent_run_id=run_id,
        session_id=session.session_id,
        project_id=session.project_id,
        goal_revision=revision,
        provider="deepseek",
        model="test-model",
        trace_id=f"trace-{run_id}",
        started_at=utc_now(),
    )
    return application.agent_runtime.get(run_id)


def _action(application, call_id="action"):
    model = application.settings.models[0]
    return PreparedAction(
        tool_call_id=call_id,
        position=0,
        tool=model.tool_name,
        model_id=model.model_id,
        model_version=model.model_version,
        model_checksum=model.model_checksum,
        workflow_id=model.workflow_id,
        workflow_version=model.workflow_version,
        workflow_checksum=model.workflow_checksum,
        plan_revision=1,
        arguments_json='{"axis":"columns","resource_id":"a"}',
        schema_checksum=model.input_schema_checksum,
        fingerprint=f"fingerprint-{call_id}",
    )


def _waiting(application, run):
    persistence.save_waiting_result(
        application.database, run, (_action(application, f"action-{run.agent_run_id}"),),
        {"checkpoint": run.agent_run_id}, UsageDelta(0, 0, 0), None,
        application.agent_projection,
    )
    return application.agent_runtime.get(run.agent_run_id)


def _snapshot(database):
    with connect_database(database) as connection:
        return {
            table: tuple(tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid"))
            for table in (
                "agent_runs", "agent_actions", "agent_run_state", "agent_memory", "session_events"
            )
        }


def test_waiting_confirmation_projects_current_session_in_same_transaction(
    kernel_settings, monkeypatch,
):
    from platform_kernel.interaction import state_projection

    application = _application(kernel_settings())
    session = application.interaction.create_session().session
    with connect_database(application.database) as connection:
        connection.execute(
            "UPDATE sessions SET goal_revision = 1, pending_agent_run_id = 'run' WHERE session_id = ?",
            (session.session_id,),
        )
    run = _create(application, session)
    before = _snapshot(application.database)
    original = state_projection.ensure_confirmation

    def fail_projection(connection, saved):
        original(connection, saved)
        raise RuntimeError("confirmation storage failed")

    monkeypatch.setattr(state_projection, "ensure_confirmation", fail_projection)
    with pytest.raises(RuntimeError, match="confirmation storage failed"):
        persistence.save_waiting_result(
            application.database, run, (_action(application),),
            {"checkpoint": "run"}, UsageDelta(2, 3, 5), None,
            application.agent_projection,
        )
    assert _snapshot(application.database) == before
    with connect_database(application.database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM confirmations").fetchone()[0] == 0
    monkeypatch.setattr(state_projection, "ensure_confirmation", original)
    persistence.save_waiting_result(
        application.database, run, (_action(application),),
        {"checkpoint": "run"}, UsageDelta(2, 3, 5), None,
        application.agent_projection,
    )
    with connect_database(application.database) as connection:
        projected = connection.execute(
            "SELECT agent_run_id, pending_agent_run_id FROM sessions WHERE session_id = ?",
            (session.session_id,),
        ).fetchone()
        assert tuple(projected) == ("run", None)
        assert connection.execute("SELECT COUNT(*) FROM confirmations").fetchone()[0] == 1


def test_approved_action_reads_workflow_fact_without_reconciliation(kernel_settings):
    application = _application(kernel_settings())
    session = application.interaction.create_session().session
    with connect_database(application.database) as connection:
        connection.execute(
            "UPDATE sessions SET goal_revision = 1 WHERE session_id = ?",
            (session.session_id,),
        )
    waiting = _waiting(application, _create(application, session))
    approved = application.agent_runtime.confirm(waiting.agent_run_id, "p")
    workflow_id = approved.actions[0].workflow_run_id
    reference = {
        "kind": "data_version", "service": "data-primary", "dataset_id": "dataset-1",
        "version_id": "result-1", "semantic_id": "semantics-1", "object_id": "object-1",
        "media_type": "application/json", "format": "json", "size_bytes": 1,
            "checksum": "sha256:" + "a" * 64,
    }
    with connect_database(application.database) as connection:
        connection.execute(
            "UPDATE workflow_runs SET status = 'SUCCEEDED', outputs_json = ? WHERE run_id = ?",
            (json.dumps([reference]), workflow_id),
        )
        assert connection.execute(
            "SELECT status FROM agent_actions WHERE agent_run_id = ?",
            (waiting.agent_run_id,),
        ).fetchone()[0] == "APPROVED"
    action = application.agent_runtime.get(waiting.agent_run_id).actions[0]
    assert action.status == "COMPLETE"
    assert action.remote_outputs[0].version_id == "result-1"
    context = load_execution_context(
        application.database, session.session_id, before_goal_revision=2
    )
    assert context.actions[0].status == "COMPLETE"
    assert context.actions[0].outputs[0]["version_id"] == "result-1"


def test_trusted_direct_confirmation_retains_dispatch_authorization(kernel_settings):
    application = _application(kernel_settings())
    session = application.interaction.create_session().session
    with connect_database(application.database) as connection:
        connection.execute(
            "UPDATE sessions SET goal_revision=1 WHERE session_id=?",
            (session.session_id,),
        )
    waiting = _waiting(application, _create(application, session))
    approved = application.agent_runtime.confirm(waiting.agent_run_id, "p")
    workflow_id = approved.actions[0].workflow_run_id
    with connect_database(application.database) as connection:
        assert connection.execute(
            "SELECT confirmation_id FROM workflow_runs WHERE run_id=?",
            (workflow_id,),
        ).fetchone()[0] is None

    class Client:
        async def submit(self, _service, request):
            return JobStatusV6(
                job_id="job-1", request_id=request.request_id, status="QUEUED"
            )

    application.workflow_execution.execution_client = Client()
    application.workflow_execution.expected_definition_set_digest = None

    async def tick():
        return [result async for result in application.workflow_execution.tick()]

    assert len(asyncio.run(tick())) == 1
    with connect_database(application.database) as connection:
        assert connection.execute(
            "SELECT state FROM workflow_dispatches WHERE workflow_run_id=?",
            (workflow_id,),
        ).fetchone()[0] == "SUBMITTED"


@pytest.mark.parametrize("transition", ["create", "waiting", "complete", "failed", "invalid", "superseded"])
def test_agent_transition_and_event_roll_back_together(kernel_settings, monkeypatch, transition):
    application = _application(kernel_settings())
    session = application.interaction.create_session().session
    run = None if transition == "create" else _create(application, session)
    if transition in {"complete", "failed", "invalid", "superseded"}:
        run = _waiting(application, run)
    update = MemoryUpdate(
        0, 1, MemorySummaryV3(goals=["goal"], constraints=[], decisions=[], unresolved_items=[])
    )
    before = _snapshot(application.database)
    append_event = run_lifecycle.append_event

    def fail_event(connection, session_id, event_type, payload):
        append_event(connection, session_id, event_type, payload)
        raise RuntimeError("event storage failed")

    monkeypatch.setattr(run_lifecycle, "append_event", fail_event)
    monkeypatch.setattr(run_recovery, "append_event", fail_event)
    with pytest.raises(RuntimeError, match="event storage failed"):
        if transition == "create":
            _create(application, session)
        elif transition == "waiting":
            persistence.save_waiting_result(
                application.database, run, (_action(application),),
                {"checkpoint": "new"}, UsageDelta(2, 3, 5), update,
                application.agent_projection,
            )
        elif transition in {"complete", "failed"}:
            persistence.finish_run(
                application.database, run,
                agent_projection=application.agent_projection,
                status=transition.upper(),
                output=AgentFinalV5(
                    message="done",
                    presentation={
                        "conclusion": {"markdown": "done", "source_indexes": []},
                        "details": [], "execution_summary": None,
                        "unresolved_items": [], "sources": [],
                    },
                    workflow_runs=[], unresolved_items=[], interaction_options=[],
                ),
                usage=UsageDelta(2, 3, 5), error=None, memory_update=update,
            )
        elif transition == "invalid":
            persistence.fail_invalid_state(
                application.database, run, application.agent_projection
            )
        else:
            persistence.supersede_waiting_run(
                application.database, run, application.agent_projection
            )
    assert _snapshot(application.database) == before


def test_supersede_cas_emits_one_event(kernel_settings):
    application = _application(kernel_settings())
    session = application.interaction.create_session().session
    run = _waiting(application, _create(application, session))
    assert persistence.supersede_waiting_run(
        application.database, run, application.agent_projection
    )
    assert not persistence.supersede_waiting_run(
        application.database, run, application.agent_projection
    )
    with connect_database(application.database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM session_events WHERE event_type = 'agent.superseded'"
        ).fetchone()[0] == 1


def test_stale_terminal_transition_cannot_change_memory_or_emit_event(kernel_settings):
    application = _application(kernel_settings())
    session = application.interaction.create_session().session
    run = _create(application, session)
    persistence.fail_run(
        application.database, run, "PROCESS_INTERRUPTED", application.agent_projection
    )
    before = _snapshot(application.database)
    with pytest.raises(ValueError, match="AGENT_RUN_TRANSITION_INVALID"):
        persistence.finish_run(
            application.database, run, agent_projection=application.agent_projection,
            status="COMPLETE", output=None,
            usage=UsageDelta(2, 3, 5), error=None,
            memory_update=MemoryUpdate(
                0, 1, MemorySummaryV3(goals=[], constraints=[], decisions=[], unresolved_items=[])
            ),
        )
    assert _snapshot(application.database) == before


@pytest.mark.parametrize("previous", ["WAITING_CONFIRMATION", "WAITING_EXECUTION"])
def test_recovery_restores_previous_phase_without_duplicate_steps(kernel_settings, previous):
    application = _application(kernel_settings())
    session = application.interaction.create_session().session
    run = _waiting(application, _create(application, session))
    if previous == "WAITING_EXECUTION":
        run = application.agent_runtime.confirm(run.agent_run_id, "p")
    persistence.claim_state(application.database, run, expected_status=previous)
    application.agent_runtime.recover_interrupted()
    recovered = application.agent_runtime.get(run.agent_run_id)
    assert recovered.status == previous
    assert recovered.steps == run.steps
    application.agent_runtime.recover_interrupted()
    assert application.agent_runtime.get(run.agent_run_id).steps == run.steps


def test_recovery_rejects_impossible_persisted_phase(kernel_settings):
    application = _application(kernel_settings())
    session = application.interaction.create_session().session
    run = _waiting(application, _create(application, session))
    persistence.claim_state(application.database, run, expected_status="WAITING_CONFIRMATION")
    invalid = replace(application.agent_runtime.get(run.agent_run_id), steps=("RUNNING",))
    with pytest.raises(ValueError, match="AGENT_RUN_RECOVERY_PHASE_INVALID"):
        persistence.recover_running_run(
            application.database, invalid, application.agent_projection
        )


@pytest.mark.parametrize("scope", ["current", "prior", "other_session", "future", "missing"])
def test_final_workflow_references_are_action_and_session_scoped(kernel_settings, scope):
    application = _application(kernel_settings())
    session = application.interaction.create_session().session
    current = _create(application, session, "current", revision=2)
    if scope == "missing":
        workflow_id = "missing-workflow"
    else:
        owner_session = session
        if scope == "other_session":
            owner_session = application.interaction.create_session().session
        target = current if scope == "current" else _create(
            application, owner_session, "referenced", revision=3 if scope == "future" else 1
        )
        target = _waiting(application, target)
        target = application.agent_runtime.confirm(target.agent_run_id, owner_session.project_id)
        workflow_id = target.actions[0].workflow_run_id
        if scope == "current":
            current = target
    result = SimpleNamespace(
        context_wrapper=SimpleNamespace(context={}),
        final_output=json.dumps({
            "conclusion": {"text": "result", "citations": []},
            "details": [],
            "unresolved_items": [],
            "referenced_workflow_runs": [workflow_id],
            "include_execution_status": False,
            "interaction_options": [], "workflow_draft": None,
        }),
        to_input_list=lambda: [],
    )
    if scope in {"current", "prior"}:
        completed = application.agent_runtime._record_final(
            current, result, UsageDelta(3, 5, 8), None
        )
        assert completed.status == "COMPLETE"
        assert completed.output.workflow_runs == [workflow_id]
    else:
        with pytest.raises(AgentRunError, match="AGENT_MODEL_OUTPUT_INVALID"):
            application.agent_runtime._record_final(current, result, UsageDelta(3, 5, 8), None)
        failed = application.agent_runtime.get(current.agent_run_id)
        assert failed.status == "FAILED"
        assert failed.total_tokens == 8


def test_minimax_terminal_execution_is_projected_from_persisted_facts(
    kernel_settings,
):
    application = _application(replace(
        kernel_settings(),
        agent_provider="minimax",
        agent_protocol="chat_completions",
    ))
    session = application.interaction.create_session().session
    current = _create(application, session, "minimax-terminal")
    persistence.ensure_dialogue_request(
        application.database,
        current.agent_run_id,
        "EXECUTION_PROPOSAL",
    )
    current = _waiting(application, current)
    current = application.agent_runtime.confirm(
        current.agent_run_id,
        session.project_id,
    )
    workflow_id = current.actions[0].workflow_run_id
    with connect_database(application.database) as connection:
        connection.execute(
            "UPDATE workflow_runs SET status='SUCCEEDED', outputs_json='[]' "
            "WHERE run_id=?",
            (workflow_id,),
        )
    current = application.agent_runtime.get(current.agent_run_id)
    result = SimpleNamespace(
        context_wrapper=SimpleNamespace(context={}),
        final_output='"truncated provider acknowledgement',
        to_input_list=lambda: [],
    )

    completed = application.agent_runtime._record_final(
        current,
        result,
        UsageDelta(3, 5, 8),
        None,
    )

    assert completed.status == "COMPLETE"
    assert completed.output.message == "已完成 1 项分析。"
    assert completed.output.workflow_runs == [workflow_id]


@pytest.mark.parametrize("phase,terminal", [(phase, terminal)
    for phase in ("WAITING_CONFIRMATION", "WAITING_EXECUTION")
    for terminal in ("COMPLETE", "FAILED", "SUPERSEDED")
    if not (phase == "WAITING_EXECUTION" and terminal == "SUPERSEDED")])
def test_observation_runstate_lives_only_until_terminal(kernel_settings, phase, terminal):
    import json
    application = _application(kernel_settings())
    session = application.interaction.create_session().session
    run = _create(application, session)
    sentinel = "temporary-observation-not-memory-9521"
    persistence.save_waiting_result(
        application.database, run, (_action(application),),
        {"tool_output": sentinel}, UsageDelta(0, 0, 0), None,
        application.agent_projection,
    )
    run = application.agent_runtime.get(run.agent_run_id)
    if phase == "WAITING_EXECUTION":
        run = application.agent_runtime.confirm(run.agent_run_id, "p")
    assert sentinel in json.dumps(_snapshot(application.database)["agent_run_state"])
    persistence.claim_state(application.database, run, expected_status=phase)
    application.agent_runtime.recover_interrupted()
    run = application.agent_runtime.get(run.agent_run_id)
    assert run.status == phase
    assert sentinel in json.dumps(_snapshot(application.database)["agent_run_state"])
    if terminal == "SUPERSEDED":
        persistence.supersede_waiting_run(
            application.database, run, application.agent_projection
        )
    else:
        persistence.finish_run(
            application.database, run, agent_projection=application.agent_projection,
            status=terminal, output=None,
                               usage=UsageDelta(0, 0, 0), error=None)
    snapshot = _snapshot(application.database)
    assert snapshot["agent_run_state"] == ()
    assert sentinel not in json.dumps(snapshot)
