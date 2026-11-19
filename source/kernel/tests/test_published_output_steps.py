from dataclasses import replace

from kernel.tests.test_confirmation_recovery import _seed_pending_confirmation
from platform_kernel.agent_runtime.persistence.context import list_session_results
from platform_kernel.agent_runtime.persistence.run_queries import get_run as get_agent_run
from platform_kernel.application.query_dtos import ConfirmedAction
from platform_kernel.contracts import DataVersionRefV2, JobStatusV6
from platform_kernel.database import connect_database
from platform_kernel.interaction.access import dispatch_confirmation_current
from platform_kernel.interaction.confirmation import claim_decision, request_decision
from platform_kernel.interaction.output_steps import freeze_output_steps
from platform_kernel.interaction.recovery import create_recovery_confirmation
from platform_kernel.interaction.state_projection import confirmation_from_row
from platform_kernel.workflow_execution.models import DispatchWork
from platform_kernel.workflow_execution.persistence.output_steps import enqueue_output_step
from platform_kernel.workflow_execution.persistence.queue import enqueue_batch, runs_for_agent
from platform_kernel.workflow_execution.persistence.queue import recover_dispatches, recovery_source
from platform_kernel.workflow_execution.persistence.status import save_job_status, save_rejection


def test_published_output_step_keeps_committed_source_and_recovers_only_itself(kernel_settings):
    settings = kernel_settings()
    original = settings.models[0]
    target = replace(
        original, model_id="fixture.static-map", tool_name="render_map",
        final_outputs=("map-image", "map-summary"), default_parameters={"width": 1024},
    )
    models = {model.model_id: model for model in (original, target)}
    services = {service.name: service for service in settings.services}
    confirmation_id = _seed_pending_confirmation(settings)
    database = settings.data_root / "kernel.sqlite3"
    agent = get_agent_run(database, "agent")
    with connect_database(database) as connection:
        freeze_output_steps(connection, confirmation_id, agent.actions, tuple(models.values()), ({
            "sourceModel": original.model_id, "output": "points", "targetModel": target.model_id,
            "targetInput": "sourceVersionId", "arguments": {},
        },))
        projected = confirmation_from_row(connection, connection.execute(
            "SELECT * FROM confirmations WHERE confirmation_id=?", (confirmation_id,),
        ).fetchone())
        assert projected.published_output_steps[0]["output"] == "points"
        assert not runs_for_agent(database, "agent")
        connection.execute("UPDATE confirmations SET decision='CONFIRM', status='CONFIRMED' WHERE confirmation_id=?",
                           (confirmation_id,))
        connection.execute("UPDATE agent_actions SET status='APPROVED' WHERE agent_run_id='agent'")
        (run,) = enqueue_batch(
            connection, agent_run_id="agent", session_id="session", project_id="p",
            confirmation_id=confirmation_id, models=models, services=services,
            actions=(ConfirmedAction(
                tool_call_id="call", position=0, tool=original.tool_name,
                model_id=original.model_id, model_version=original.model_version, model_checksum=original.model_checksum,
                workflow_id=original.workflow_id, workflow_version=original.workflow_version,
                workflow_checksum=original.workflow_checksum, input_schema_checksum=original.input_schema_checksum,
                arguments={"resource_id": "input"},
            ),),
        )
    reference = DataVersionRefV2(
        "data_version", "data-primary", "dataset", "frozen", "semantics", "object",
        "application/geo+json", "geojson", 30, "sha256:" + "a" * 64, "points",
    )
    status = JobStatusV6("job", run.run_id, "SUCCEEDED", outputs=(reference,))
    dispatch = DispatchWork(run, "SUBMITTED", "future")
    def on_published(connection, source, outputs):
        enqueue_output_step(connection, source, outputs, models=models, services=services)

    assert save_job_status(database, dispatch, status, on_published=on_published)
    assert save_job_status(database, dispatch, status, on_published=on_published)
    recover_dispatches(database)
    source, step = runs_for_agent(database, "agent")
    assert source.status == "SUCCEEDED"
    assert step.input_references == {"sourceVersionId": reference}
    assert dispatch_confirmation_current(database, step)
    assert not dispatch_confirmation_current(database, replace(step, arguments={"sourceVersionId": "other"}))
    save_rejection(database, step, "CARTOGRAPHY_SOURCE_INVALID")
    report = list_session_results(database, "session", "p", 1, 0)
    assert report["items"][0]["remote_outputs"] == [reference.as_storage_dict()]
    recovery = create_recovery_confirmation(
        database, session_id="session", workflow_run_id=step.run_id, project_id="p",
        request_id="retry-map", expected_goal_revision=1,
        source=recovery_source(database, "p", "session", step.run_id),
    )
    assert [action.model_id for action in recovery.actions] == [target.model_id]
    assert recovery.actions[0].input_references == step.input_references
    assert request_decision(database, "session", recovery.confirmation_id, "CONFIRM", None, "p")
    approval = claim_decision(database, recovery.confirmation_id)
    assert approval is not None
    with connect_database(database) as connection:
        (retry,) = enqueue_batch(
            connection, agent_run_id="agent", session_id="session", project_id="p",
            confirmation_id=recovery.confirmation_id, models=models, services=services, actions=approval.actions,
        )
    assert retry.retry_of_workflow_run_id == step.run_id
    assert retry.input_references == {"sourceVersionId": reference}
    assert dispatch_confirmation_current(database, retry)
