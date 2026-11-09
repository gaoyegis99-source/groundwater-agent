from __future__ import annotations

import asyncio
from dataclasses import replace
import json

from agents.testing import ScriptedModel, ModelStep, function_call
from kernel.tests.reply_testing import submit_reply
import httpx
import pytest

from platform_kernel.composition import PlatformApplication
from platform_kernel.contracts import JobStatusV6
from platform_kernel.database import connect_database
from platform_kernel.errors import AgentRunError
import platform_kernel.interaction.confirmation as confirmation_module
from platform_kernel.interaction.confirmation import (
    claim_decision,
    finalize_decision,
    request_decision,
)
from platform_kernel.interaction.persistence import reserve_turn
from platform_kernel.workflow_execution.persistence import claim_dispatch_batch, save_job_status


def _final(message: str):
    return submit_reply(message)


def _waiting_steps():
    return [
        ModelStep(output=[function_call(
            "inspect_data_version", {"version_id": "a"}, call_id="inspect",
        )]),
        ModelStep(
            output=[
                function_call(
                    "record_plan",
                    {
                        "goal": "run",
                        "steps": [
                            {
                                "step_id": "run",
                                "tool": "run_summary",
                                "purpose": "run",
                            }
                        ],
                        "reason": "requested",
                    },
                    call_id="plan",
                )
            ]
        ),
        ModelStep(
            output=[
                function_call("run_summary", {"resource_id": "a"}, call_id="action")
            ]
        ),
    ]


def _application(settings, model):
    def service(request):
        if request.url.path.endswith("inspect_data_version"):
            request_body = json.loads(request.content)
            arguments = request_body.get("arguments", request_body)
            version_id = arguments.get("version_id", arguments.get("id"))
            assert isinstance(version_id, str)
            return httpx.Response(200, json={"data": {
                "status": "OK", "versionId": version_id,
                "datasetId": "dataset-a",
                "semanticId": "semantics-a", "objects": [{
                    "objectId": "object-a", "role": "raw", "mediaType": "text/csv",
                    "format": "csv", "size": 8, "checksum": "sha256:" + "a" * 64,
                }],
            }})
        return httpx.Response(500)
    return PlatformApplication(
        settings=settings,
        model=model,
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(service)
        ),
    )


async def _recover_interaction(application):
    application.agent_runtime.recover_interrupted()
    starts = application.interaction.prepare_recovery()
    while await application.interaction.tick():
        pass
    for session_id in starts:
        await application.interaction.resume_pending_start(session_id)


def test_requested_agent_start_is_recovered_once(kernel_settings) -> None:
    settings = kernel_settings("knowledge")
    first = _application(settings, ScriptedModel([]))
    session = first.interaction.create_session().session
    reserve_turn(
        first.database, session.session_id, "hello", "run", "p"
    )

    restarted = _application(
        settings, ScriptedModel([ModelStep(output=[_final("recovered")])])
    )
    asyncio.run(_recover_interaction(restarted))
    view = restarted.interaction.get(session.session_id).session
    assert view.agent_run_id == "run"
    assert view.agent_output.message == "recovered"
    asyncio.run(_recover_interaction(restarted))
    with connect_database(restarted.database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0] == 1


def test_existing_complete_agent_run_is_only_projected(kernel_settings) -> None:
    settings = kernel_settings("knowledge")
    first = _application(
        settings, ScriptedModel([ModelStep(output=[_final("already complete")])])
    )
    session = first.interaction.create_session().session
    reserve_turn(
        first.database, session.session_id, "hello", "run", "p"
    )
    run = asyncio.run(
        first.agent_runtime.start(
            session.session_id,
            "p",
            1,
            "hello",
            (),
            agent_run_id="run",
        )
    )
    assert run.status == "COMPLETE"

    restarted = _application(settings, ScriptedModel([]))
    asyncio.run(_recover_interaction(restarted))
    assert (
        restarted.interaction.get(session.session_id).session.agent_output.message
        == "already complete"
    )


def test_running_reject_decision_is_recovered_without_job(kernel_settings) -> None:
    settings = kernel_settings()
    model = ScriptedModel([*_waiting_steps(), ModelStep(output=[_final("rejected")])])
    first = _application(settings, model)
    session = first.interaction.create_session().session
    waiting = asyncio.run(first.interaction.turn(
        session.session_id, "run", dialogue_policy="EXECUTION_PROPOSAL",
    )).session
    confirmation_id = waiting.confirmation.confirmation_id
    asyncio.run(first.interaction.reject(session.session_id, confirmation_id, "no"))
    with connect_database(first.database) as connection:
        connection.execute(
            "UPDATE confirmations SET decision_state = 'RUNNING' "
            "WHERE confirmation_id = ?",
            (confirmation_id,),
        )

    restarted = _application(settings, model)
    asyncio.run(_recover_interaction(restarted))
    recovered = restarted.interaction.get(session.session_id).session
    assert recovered.confirmation.status == "REJECTED"
    assert recovered.agent_output.message == "已拒绝 1 项分析。"
    with connect_database(restarted.database) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0] == 0
        )


def test_running_confirm_decision_is_recovered_to_waiting_execution(
    kernel_settings,
) -> None:
    settings = kernel_settings()
    model = ScriptedModel(_waiting_steps())
    first = _application(settings, model)
    session = first.interaction.create_session().session
    waiting = asyncio.run(first.interaction.turn(
        session.session_id, "run", dialogue_policy="EXECUTION_PROPOSAL",
    )).session
    confirmation_id = waiting.confirmation.confirmation_id
    request_decision(
        first.database, session.session_id, confirmation_id, "CONFIRM", None,
        "p",
    )
    with connect_database(first.database) as connection:
        connection.execute(
            "UPDATE confirmations SET decision_state = 'RUNNING' "
            "WHERE confirmation_id = ?",
            (confirmation_id,),
        )

    restarted = _application(settings, model)
    asyncio.run(_recover_interaction(restarted))
    recovered = restarted.interaction.get(session.session_id)
    assert recovered.session.confirmation.status == "CONFIRMED"
    assert recovered.agent_steps[-1] == "WAITING_EXECUTION"
    with connect_database(restarted.database) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0] == 1
        )


def test_restart_confirm_dispatches_frozen_identity_and_arguments(
    kernel_settings,
) -> None:
    settings = kernel_settings()
    first = _application(settings, ScriptedModel(_waiting_steps()))
    session = first.interaction.create_session().session
    waiting = asyncio.run(
        first.interaction.turn(
            session.session_id, "run", dialogue_policy="EXECUTION_PROPOSAL",
        )
    ).session
    frozen = waiting.confirmation.actions[0]
    changed_runtime = replace(
        settings.models[0],
        default_parameters={"axis": "rows"},
        deadline_seconds=120,
    )
    restarted = _application(
        replace(settings, models=(changed_runtime,)), ScriptedModel([])
    )

    confirmed = asyncio.run(
        restarted.interaction.confirm(
            session.session_id,
            waiting.confirmation.confirmation_id,
        )
    )

    assert confirmed.agent_steps[-1] == "WAITING_EXECUTION"
    (workflow_run,) = restarted.workflow_execution.for_agent(waiting.agent_run_id)
    assert (
        workflow_run.model_id,
        workflow_run.model_version,
        workflow_run.model_checksum,
        workflow_run.workflow_id,
        workflow_run.workflow_version,
        workflow_run.workflow_checksum,
    ) == (
        frozen.model_id,
        frozen.model_version,
        frozen.model_checksum,
        frozen.workflow_id,
        frozen.workflow_version,
        frozen.workflow_checksum,
    )
    assert workflow_run.arguments == {"axis": "columns", "resource_id": "a"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("missing", None),
        ("tool_name", "renamed_summary"),
        ("model_version", "9.9.9"),
        ("model_checksum", "sha256:changed-model"),
        ("workflow_id", "reference.changed"),
        ("workflow_version", "9.9.9"),
        ("workflow_checksum", "sha256:changed-workflow"),
        ("input_schema_checksum", "sha256:changed-schema"),
    ],
)
def test_restart_confirm_fails_closed_when_frozen_definition_is_unavailable(
    kernel_settings, field, value
) -> None:
    settings = kernel_settings()
    first = _application(settings, ScriptedModel(_waiting_steps()))
    session = first.interaction.create_session().session
    waiting = asyncio.run(
        first.interaction.turn(
            session.session_id, "run", dialogue_policy="EXECUTION_PROPOSAL",
        )
    ).session
    confirmation_id = waiting.confirmation.confirmation_id
    models = () if field == "missing" else (
        replace(settings.models[0], **{field: value}),
    )
    restarted = _application(replace(settings, models=models), ScriptedModel([]))

    with pytest.raises(
        AgentRunError, match="WORKFLOW_DEFINITION_UNAVAILABLE"
    ) as failure:
        asyncio.run(
            restarted.interaction.confirm(
                session.session_id, confirmation_id
            )
        )
    assert failure.value.agent_run_id == waiting.agent_run_id

    run = restarted.agent_runtime.get(waiting.agent_run_id)
    assert run.status == "FAILED"
    assert run.error == "WORKFLOW_DEFINITION_UNAVAILABLE"
    assert [action.status for action in run.actions] == ["FAILED"]
    assert [action.error for action in run.actions] == [
        "WORKFLOW_DEFINITION_UNAVAILABLE"
    ]
    repeated = asyncio.run(
        restarted.interaction.confirm(
            session.session_id, confirmation_id
        )
    )
    assert repeated.agent_steps[-1] == "FAILED"
    asyncio.run(_recover_interaction(restarted))

    with connect_database(restarted.database) as connection:
        confirmation = connection.execute(
            "SELECT status, decision_state FROM confirmations "
            "WHERE confirmation_id = ?",
            (confirmation_id,),
        ).fetchone()
        assert tuple(confirmation) == ("CONFIRMED", None)
        assert connection.execute(
            "SELECT COUNT(*) FROM workflow_runs"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM workflow_dispatches"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM agent_run_state"
        ).fetchone()[0] == 0
        events = connection.execute(
            "SELECT event_type FROM session_events WHERE session_id = ?",
            (session.session_id,),
        ).fetchall()
    event_types = [row["event_type"] for row in events]
    assert event_types.count("agent.failed") == 1
    assert event_types.count("confirmation.confirmed") == 1


def test_restart_confirm_prevalidates_the_entire_ordered_batch(
    kernel_settings,
) -> None:
    settings = kernel_settings()
    first_model = settings.models[0]
    second_model = replace(
        first_model,
        model_id="reference.rows",
        model_checksum="sha256:rows-model",
        tool_name="run_rows",
        default_parameters={"axis": "rows"},
    )
    initial_settings = replace(settings, models=(first_model, second_model))
    model = ScriptedModel(
        [
            ModelStep(output=[function_call(
                "inspect_data_version", {"version_id": "a"}, call_id="inspect",
            )]),
            ModelStep(
                output=[
                    function_call(
                        "record_plan",
                        {
                            "goal": "run both",
                            "steps": [
                                {
                                    "step_id": "columns",
                                    "tool": "run_summary",
                                    "purpose": "columns",
                                },
                                {
                                    "step_id": "rows",
                                    "tool": "run_rows",
                                    "purpose": "rows",
                                },
                            ],
                            "reason": "requested",
                        },
                        call_id="plan",
                    )
                ]
            ),
            ModelStep(
                output=[
                    function_call(
                        "run_summary", {"resource_id": "a"}, call_id="first"
                    ),
                    function_call(
                        "run_rows", {"resource_id": "a"}, call_id="second"
                    ),
                ]
            ),
        ]
    )
    first = _application(initial_settings, model)
    session = first.interaction.create_session().session
    waiting = asyncio.run(
        first.interaction.turn(
            session.session_id, "run", dialogue_policy="EXECUTION_PROPOSAL",
        )
    ).session
    drifted_second = replace(
        second_model, workflow_checksum="sha256:changed-workflow"
    )
    restarted = _application(
        replace(settings, models=(first_model, drifted_second)),
        ScriptedModel([]),
    )

    with pytest.raises(AgentRunError, match="WORKFLOW_DEFINITION_UNAVAILABLE"):
        asyncio.run(
            restarted.interaction.confirm(
                session.session_id,
                waiting.confirmation.confirmation_id,
            )
        )

    run = restarted.agent_runtime.get(waiting.agent_run_id)
    assert [action.status for action in run.actions] == ["FAILED", "FAILED"]
    assert [action.error for action in run.actions] == [
        "WORKFLOW_DEFINITION_UNAVAILABLE",
        "WORKFLOW_DEFINITION_UNAVAILABLE",
    ]
    with connect_database(restarted.database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM workflow_runs"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM workflow_dispatches"
        ).fetchone()[0] == 0


def test_confirmation_finalization_rolls_back_status_event_and_projection(
    kernel_settings, monkeypatch
) -> None:
    settings = kernel_settings()
    application = _application(settings, ScriptedModel(_waiting_steps()))
    session = application.interaction.create_session().session
    waiting = asyncio.run(
        application.interaction.turn(
            session.session_id, "run", dialogue_policy="EXECUTION_PROPOSAL",
        )
    ).session
    confirmation_id = waiting.confirmation.confirmation_id
    request_decision(
        application.database,
        session.session_id,
        confirmation_id,
        "CONFIRM",
        None,
        "p",
    )
    decision = claim_decision(application.database, confirmation_id)
    assert decision is not None
    run = application.agent_runtime.get(decision.agent_run_id)

    def fail_projection(connection, _run):
        connection.execute(
            "UPDATE sessions SET agent_run_id = NULL WHERE session_id = ?",
            (session.session_id,),
        )
        raise RuntimeError("projection failed")

    monkeypatch.setattr(confirmation_module, "project_agent_run", fail_projection)
    with pytest.raises(RuntimeError, match="projection failed"):
        finalize_decision(application.database, decision, run)

    with connect_database(application.database) as connection:
        confirmation = connection.execute(
            "SELECT status, decision_state FROM confirmations "
            "WHERE confirmation_id = ?",
            (confirmation_id,),
        ).fetchone()
        persisted_session = connection.execute(
            "SELECT agent_run_id FROM sessions WHERE session_id = ?",
            (session.session_id,),
        ).fetchone()
        confirmed_events = connection.execute(
            "SELECT COUNT(*) FROM session_events "
            "WHERE session_id = ? AND event_type = 'confirmation.confirmed'",
            (session.session_id,),
        ).fetchone()[0]
    assert tuple(confirmation) == ("PENDING", "RUNNING")
    assert persisted_session["agent_run_id"] == run.agent_run_id
    assert confirmed_events == 0


def test_completed_batch_recovers_into_authoritative_finalization(
    kernel_settings,
) -> None:
    settings = kernel_settings()
    model = ScriptedModel([*_waiting_steps(), ModelStep(output=[_final("ignored")])])
    first = _application(settings, model)

    async def interrupt_finalization():
        session = first.interaction.create_session().session
        waiting = (await first.interaction.turn(
            session.session_id, "run", dialogue_policy="EXECUTION_PROPOSAL",
        )).session
        await first.interaction.confirm(
            session.session_id, waiting.confirmation.confirmation_id
        )
        (work,) = claim_dispatch_batch(first.database, 1)
        assert save_job_status(
            first.database,
            work,
            JobStatusV6("job", work.run.run_id, "SUCCEEDED"),
        )
        assert first.agent_runtime.reconcile_execution(waiting.agent_run_id)
        completed = await first.agent_runtime.resume_execution(waiting.agent_run_id)
        first.interaction.project(completed)
        return session.session_id, waiting.agent_run_id

    session_id, run_id = asyncio.run(interrupt_finalization())
    restarted = _application(settings, model)
    asyncio.run(_recover_interaction(restarted))
    recovered = restarted.agent_runtime.get(run_id)
    assert recovered.status == "COMPLETE"
    assert recovered.output.message == "已完成 1 项分析。"
    assert [action.status for action in recovered.actions] == ["COMPLETE"]
    assert len(restarted.workflow_execution.for_agent(run_id)) == 1
    assert restarted.interaction.get(session_id).session.confirmation.status == "CONFIRMED"
    with connect_database(restarted.database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM confirmations").fetchone()[0] == 1


def test_recovered_rejection_cannot_create_a_new_action_batch(kernel_settings) -> None:
    settings = kernel_settings()
    model = ScriptedModel([
        *_waiting_steps(),
        ModelStep(output=[_final("ignored")]),
    ])
    first = _application(settings, model)

    async def reject_once():
        session = first.interaction.create_session().session
        waiting = (await first.interaction.turn(
            session.session_id, "run", dialogue_policy="EXECUTION_PROPOSAL",
        )).session
        confirmation_id = waiting.confirmation.confirmation_id
        completed = await first.interaction.reject(
            session.session_id, confirmation_id, "no",
        )
        assert completed.session.agent_output is None
        while await first.interaction.tick():
            pass
        assert (
            first.interaction.get(session.session_id).session.agent_output.message
            == "已拒绝 1 项分析。"
        )
        return session.session_id, waiting.agent_run_id, confirmation_id

    session_id, run_id, confirmation_id = asyncio.run(reject_once())
    restarted = _application(settings, ScriptedModel([]))
    asyncio.run(_recover_interaction(restarted))
    current = restarted.interaction.get(session_id).session.confirmation
    assert current.confirmation_id == confirmation_id
    assert current.status == "REJECTED"
    assert [action.status for action in restarted.agent_runtime.get(run_id).actions] == ["REJECTED"]
    with connect_database(restarted.database) as connection:
        assert connection.execute(
            "SELECT status FROM confirmations WHERE confirmation_id = ?",
            (confirmation_id,),
        ).fetchone()[0] == "REJECTED"
        assert connection.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0] == 0
