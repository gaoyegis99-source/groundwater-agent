import asyncio
import json

import httpx

from platform_kernel.agent_runtime.persistence import finalize_recovered_run, get_run
from platform_kernel.application.query_dtos import ConfirmedAction
from platform_kernel.application.agent_projection import AgentProjectionAdapter
from platform_kernel.database import connect_database, utc_now
from platform_kernel.interaction.persistence import create_session
from platform_kernel.interaction.revisions import revise_confirmation
from platform_kernel.interaction.state_projection import ensure_confirmation
from platform_kernel.interaction.recovery import create_recovery_confirmation
from platform_kernel.workflow_execution.persistence.queue import enqueue_batch, recovery_source
from platform_kernel.workflow_execution.persistence.status import save_rejection


def test_confirmation_revision_is_immutable_and_idempotent(kernel_settings):
    settings = kernel_settings()
    database = settings.data_root / "kernel.sqlite3"
    create_session(database, "session", "p")
    model = settings.models[0]
    now = utc_now()
    with connect_database(database) as connection:
        connection.execute(
            "UPDATE sessions SET goal_revision=1,agent_run_id='agent' WHERE session_id='session'"
        )
        connection.execute(
            """INSERT INTO agent_runs(
                   agent_run_id,session_id,project_id,goal_revision,provider,model,status,
                   steps_json,output_json,input_tokens,output_tokens,total_tokens,trace_id,
                   error,created_at,started_at,finished_at,duration_ms
               ) VALUES('agent','session','p',1,'fixed','fixed','WAITING_CONFIRMATION',
                        '["RUNNING","WAITING_CONFIRMATION"]',NULL,0,0,0,'trace',NULL,?,?,NULL,NULL)""",
            (now, now),
        )
        connection.execute(
            """INSERT INTO agent_actions VALUES(
                   'call','agent',0,?,?,?,?,?,?,?,1,?,'{}',?,?,'PENDING',NULL)""",
            (model.tool_name, model.model_id, model.model_version, model.model_checksum,
             model.workflow_id, model.workflow_version, model.workflow_checksum,
             json.dumps({"resource_id": "input", "axis": "columns"}),
             model.input_schema_checksum, "old-fingerprint"),
        )
    run = get_run(database, "agent")
    with connect_database(database) as connection:
        original = ensure_confirmation(connection, run)

    async def exercise():
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"versionId": "input", "datasetId": "dataset-1", "semanticId": "semantics-1", "objects": [{"objectId": "object-1", "role": "raw", "mediaType": "text/csv", "format": "csv", "size": 8, "checksum": "sha256:" + "a" * 64}]}))
        async with httpx.AsyncClient(transport=transport) as client:
            args = ({"tool_call_id": "call", "arguments": {
                "resource_id": "input", "axis": "rows",
            }},)
            first = await revise_confirmation(
                database, client, settings.services_by_name["data"], settings.models,
                session_id="session", confirmation_id=original, project_id="p",
                request_id="revision-request", expected_goal_revision=1, actions=args,
            )
            replay = await revise_confirmation(
                database, client, settings.services_by_name["data"], settings.models,
                session_id="session", confirmation_id=original, project_id="p",
                request_id="revision-request", expected_goal_revision=1, actions=args,
            )
            return first, replay

    first, replay = asyncio.run(exercise())
    assert first.confirmation_id == replay.confirmation_id
    assert first.revision == 2
    assert first.actions[0].arguments["axis"] == "rows"
    with connect_database(database) as connection:
        assert connection.execute(
            "SELECT status FROM confirmations WHERE confirmation_id=?", (original,),
        ).fetchone()[0] == "SUPERSEDED"
        assert connection.execute(
            "SELECT COUNT(*) FROM confirmations WHERE status='PENDING'",
        ).fetchone()[0] == 1


def test_recovery_creates_new_attempt_without_overwriting_failure(kernel_settings):
    settings = kernel_settings()
    database = settings.data_root / "kernel.sqlite3"
    create_session(database, "session", "p")
    model = settings.models[0]
    now = utc_now()
    with connect_database(database) as connection:
        connection.execute("UPDATE sessions SET goal_revision=1,agent_run_id='agent' WHERE session_id='session'")
        connection.execute(
            """INSERT INTO agent_runs VALUES(
                   'agent','session','p',1,'fixed','fixed','COMPLETE','[]',NULL,
                   0,0,0,'trace',NULL,?,?,?,0)""", (now, now, now),
        )
        connection.execute(
            """INSERT INTO agent_actions VALUES(
                   'call','agent',0,?,?,?,?,?,?,?,1,?,'{}',?,?,'PENDING',NULL)""",
            (model.tool_name, model.model_id, model.model_version, model.model_checksum,
             model.workflow_id, model.workflow_version, model.workflow_checksum,
             json.dumps({"resource_id": "input", "axis": "columns"}),
             model.input_schema_checksum, "fingerprint"),
        )
    with connect_database(database) as connection:
        confirmation_id = ensure_confirmation(connection, get_run(database, "agent"))
        connection.execute("UPDATE confirmations SET status='CONFIRMED' WHERE confirmation_id=?", (confirmation_id,))
        connection.execute("UPDATE agent_actions SET status='APPROVED' WHERE agent_run_id='agent'")
        action = ConfirmedAction(
            tool_call_id="call", position=0, tool=model.tool_name,
            model_id=model.model_id, model_version=model.model_version,
            model_checksum=model.model_checksum, workflow_id=model.workflow_id,
            workflow_version=model.workflow_version, workflow_checksum=model.workflow_checksum,
            input_schema_checksum=model.input_schema_checksum,
            arguments={"resource_id": "input", "axis": "columns"},
        )
        source = enqueue_batch(
            connection, agent_run_id="agent", session_id="session", project_id="p",
            confirmation_id=confirmation_id, actions=(action,), models={model.model_id: model},
            services={service.name: service for service in settings.services},
        )[0]
    save_rejection(database, source, "EXECUTION_REQUEST_INVALID")
    recovery = create_recovery_confirmation(
        database, session_id="session", workflow_run_id=source.run_id,
        project_id="p", request_id="recover", expected_goal_revision=1,
        source=recovery_source(database, "p", "session", source.run_id),
    )
    assert recovery.kind == "RECOVERY"
    with connect_database(database) as connection:
        retried = enqueue_batch(
            connection, agent_run_id="agent", session_id="session", project_id="p",
            confirmation_id=recovery.confirmation_id, actions=(action,),
            models={model.model_id: model},
            services={service.name: service for service in settings.services},
        )[0]
    assert retried.attempt == 2
    assert retried.retry_of_workflow_run_id == source.run_id
    with connect_database(database) as connection:
        rows = connection.execute(
            "SELECT run_id,status,attempt FROM workflow_runs ORDER BY attempt"
        ).fetchall()
    assert [(row["run_id"], row["status"], row["attempt"]) for row in rows] == [
        (source.run_id, "FAILED", 1), (retried.run_id, "QUEUED", 2),
    ]


def test_successful_recovery_reprojects_terminal_agent_reply(kernel_settings):
    settings = kernel_settings()
    database = settings.data_root / "kernel.sqlite3"
    create_session(database, "session", "p")
    model = settings.models[0]
    now = utc_now()
    prior_output = {
        "message": "首次执行失败。",
        "presentation": {
            "conclusion": None,
            "details": [],
            "execution_summary": {
                "markdown": "执行失败。", "source_indexes": [],
            },
            "unresolved_items": [], "sources": [],
        },
        "workflow_runs": ["failed-run"],
        "unresolved_items": [], "interaction_options": [],
    }
    output = {
        "kind": "data_version", "service": "data-primary",
        "dataset_id": "dataset", "version_id": "version",
        "semantic_id": "semantic", "object_id": "object",
        "media_type": "application/json", "format": "json",
        "size_bytes": 2, "checksum": "sha256:" + "a" * 64,
        "output_name": "result",
    }
    with connect_database(database) as connection:
        connection.execute(
            "UPDATE sessions SET goal_revision=1,agent_run_id='agent' WHERE session_id='session'"
        )
        connection.execute(
            """INSERT INTO agent_runs VALUES(
                   'agent','session','p',1,'fixed','fixed','FAILED',?, ?,
                   10,2,12,'trace','WORKFLOW_FAILED',?,?,?,0)""",
            (json.dumps(["RUNNING", "FAILED"]), json.dumps(prior_output), now, now, now),
        )
        connection.execute(
            """INSERT INTO messages(session_id,goal_revision,role,content)
               VALUES('session',1,'user','run'),('session',1,'assistant','首次执行失败。')"""
        )
        connection.execute(
            """INSERT INTO agent_actions(
                   tool_call_id,agent_run_id,position,tool,model_id,model_version,
                   model_checksum,workflow_id,workflow_version,workflow_checksum,
                   plan_revision,arguments_json,input_references_json,
                   schema_checksum,fingerprint,status,error
               ) VALUES(
                   'call','agent',0,?,?,?,?,?,?,?,1,'{}','{}',?,?,'APPROVED',NULL)""",
            (model.tool_name, model.model_id, model.model_version, model.model_checksum,
             model.workflow_id, model.workflow_version, model.workflow_checksum,
             model.input_schema_checksum, "fingerprint"),
        )
        connection.execute(
            """INSERT INTO confirmations(
                   confirmation_id,session_id,goal_revision,agent_run_id,status,reason,
                   decision,decision_state,revision,kind,replaces_confirmation_id,
                   source_workflow_run_id,request_id,request_fingerprint,created_at
               ) VALUES('recovery','session',1,'agent','CONFIRMED',NULL,
                        'CONFIRM',NULL,2,'RECOVERY',NULL,'failed-run','request','fingerprint',?)""",
            (now,),
        )
        connection.execute(
            """INSERT INTO workflow_runs(
                   run_id,agent_run_id,session_id,tool_call_id,confirmation_id,attempt,
                   retry_of_workflow_run_id,position,project_id,model_id,model_version,
                   model_checksum,workflow_id,workflow_version,workflow_checksum,
                   execution_service,executor_id,arguments_json,input_references_json,
                   status,remote_job_id,outputs_json,error,created_at,started_at,finished_at,
                   duration_ms
                   ) VALUES('recovered-run','agent','session','call','recovery',2,
                        NULL,0,'p',?,?,?,?,?,?,?,?,'{}','{}','SUCCEEDED','job',?,NULL,?,?,?,1)""",
            (
                model.model_id, model.model_version, model.model_checksum,
                model.workflow_id, model.workflow_version, model.workflow_checksum,
                model.execution_service, model.executor_id, json.dumps([output]), now, now, now,
            ),
        )

    agent_projection = AgentProjectionAdapter()
    recovered = finalize_recovered_run(database, "agent", agent_projection)
    assert recovered is not None
    assert recovered.status == "COMPLETE"
    assert recovered.error is None
    assert recovered.output is not None
    assert recovered.output.workflow_runs == ["recovered-run"]
    assert "恢复后已完成" in recovered.output.message
    with connect_database(database) as connection:
        message = connection.execute(
            "SELECT content FROM messages WHERE role='assistant'",
        ).fetchone()[0]
        events = connection.execute(
            "SELECT event_type FROM session_events ORDER BY event_id",
        ).fetchall()
    assert "恢复后已完成" in message
    assert events[-1][0] == "agent.complete"
    replay = finalize_recovered_run(database, "agent", agent_projection)
    assert replay is not None
    with connect_database(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM session_events WHERE event_type='agent.complete'",
        ).fetchone()[0] == 1
