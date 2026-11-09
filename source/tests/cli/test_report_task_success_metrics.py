from __future__ import annotations

from agents.testing import ScriptedModel
from platform_kernel.agent_runtime.persistence import create_run
from platform_kernel.composition import PlatformApplication
from platform_kernel.database import connect_database, utc_now
from platform_kernel.tasks import save_preparation


from scripts.evaluation.report_task_success_metrics import build_report


def test_task_success_report_projects_body_free_preparation_funnel(
    kernel_settings,
):
    application = PlatformApplication(
        settings=kernel_settings(), model=ScriptedModel([]),
    )
    session_id = application.interaction.create_session().session.session_id
    create_run(
        application.database,
        agent_run_id="run-report",
        session_id=session_id,
        project_id=application.settings.project_id,
        goal_revision=0,
        provider="test",
        model="test",
        trace_id="trace-report",
        started_at=utc_now(),
    )
    save_preparation(
        application.database,
        agent_run_id="run-report",
        tool_call_id="prepare-report",
        snapshot={
            "target_kind": "EXECUTABLE_MODEL",
            "target_id": "groundwater.summary",
            "target_version": "1.0.0",
            "selection_policy": "PINNED",
            "status": "BLOCKED",
            "inputs": {},
            "knowledge_refs": [],
            "parameters": {"private": "must-not-leak"},
            "parameter_sources": {},
            "checks": [],
            "blocking_reasons": ["DATA_INPUT_UNAVAILABLE"],
        },
    )
    with connect_database(application.database) as connection:
        connection.execute(
            """INSERT INTO confirmations(
                   confirmation_id,session_id,goal_revision,agent_run_id,status,
                   revision,kind,created_at
               ) VALUES ('confirmed-report',?,0,'run-report','CONFIRMED',
                         1,'INITIAL',?)""",
            (session_id, utc_now()),
        )

    report = build_report(application.database)

    assert report["database_schema_version"] == 34
    assert report["stages"]["relevant_runs"] == 1
    assert report["stages"]["blocked_preparation"] == 1
    assert report["stages"]["confirmation_confirmed"] == 1
    assert report["blocking_reasons"] == {"DATA_INPUT_UNAVAILABLE": 1}
    assert report["prepared_targets"] == {"groundwater.summary": 1}
    assert report["scope"]["accuracy_claim"] is False
    assert "must-not-leak" not in str(report)
    with connect_database(application.database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM task_preparation_revisions"
        ).fetchone()[0] == 1
