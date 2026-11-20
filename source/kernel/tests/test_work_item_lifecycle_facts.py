from __future__ import annotations

import json

import pytest

from platform_kernel.application.task_api import save_preparation_connection
from platform_kernel.application.work_item_lifecycle_facts import WorkflowFact, collect_session_facts
from platform_kernel.database import append_event, connect_database, utc_now
from platform_kernel.interaction.persistence import create_session


@pytest.fixture
def connection(kernel_settings):
    settings = kernel_settings()
    database = settings.data_root / "kernel.sqlite3"
    create_session(database, "session", "p", "alice")
    with connect_database(database) as connection:
        now = utc_now()
        connection.execute(
            """INSERT INTO work_items(work_item_id,project_id,owner_subject,title,revision,created_at,updated_at)
               VALUES('work','p','alice','Monitoring',1,?,?)""", (now, now),
        )
        connection.execute("INSERT INTO work_item_sessions VALUES('work','session',?)", (now,))
        yield connection


def _agent(connection, run_id, goal, status="COMPLETE", error=None):
    now = utc_now()
    connection.execute(
        """INSERT INTO agent_runs(
               agent_run_id,session_id,project_id,goal_revision,provider,model,status,steps_json,
               input_tokens,output_tokens,total_tokens,trace_id,created_at,started_at,error
           ) VALUES(?,'session','p',?,'fixed','model',?,'[]',0,0,0,'trace',?,?,?)""",
        (run_id, goal, status, now, now, error),
    )
    _current(connection, run_id, goal)


def _current(connection, run_id, goal):
    connection.execute(
        "UPDATE sessions SET goal_revision=?, agent_run_id=? WHERE session_id='session'",
        (goal, run_id),
    )


def _task(connection, author, *, revision=2, result_evidence=None):
    criterion = {
        "id": "delivery", "kind": "RESULT", "text": "Published summary",
        "model_id": "model", "output_names": ["summary"],
    }
    spec = {
        "task_id": "task", "session_id": "session", "revision": revision,
        "objective": "Monitoring", "source_goal_revision": 1, "source_message_id": 1,
        "constraints": [], "criteria": [criterion], "suggestions": [],
        "unresolved_items": [], "steps": [],
    }
    now = utc_now()
    connection.execute(
        "INSERT INTO tasks VALUES('task','session',?,'ACTIVE',?) "
        "ON CONFLICT(task_id) DO UPDATE SET revision=excluded.revision",
        (revision, now),
    )
    connection.execute(
        "INSERT INTO task_revisions VALUES('task',?,?,?, ?,?,'{}',?)",
        (revision, author, f"update-{revision}", json.dumps(spec),
         json.dumps({"delivery": result_evidence} if result_evidence else {}), now),
    )


def _task_run(connection, agent, revision):
    connection.execute(
        "INSERT INTO task_runs VALUES('task',?,?,'TASK_CONTINUATION',?)", (agent, revision, utc_now()),
    )


def _confirmation(connection, agent, goal, *, binding, status="PENDING", decision=None, decision_state=None):
    confirmation = f"confirm-{agent}-{binding}"
    connection.execute(
        """INSERT INTO confirmations(confirmation_id,session_id,goal_revision,agent_run_id,
               status,decision,decision_state) VALUES(?,'session',?,?,?,?,?)""",
        (confirmation, goal, agent, status, decision, decision_state),
    )
    connection.execute("INSERT INTO confirmation_tasks VALUES(?,'task',?)", (confirmation, binding))
    return confirmation


def _workflow(connection, run_id, agent, status, *, confirmation=None, attempt=1, retry_of=None, call="call"):
    outputs = []
    if status == "SUCCEEDED":
        outputs.append({
            "kind": "data_version", "service": "data-primary", "dataset_id": f"dataset-{run_id}",
            "version_id": f"version-{run_id}", "semantic_id": f"semantics-{run_id}",
            "object_id": f"object-{run_id}", "format": "json", "media_type": "application/json",
            "size_bytes": 20, "checksum": "sha256:" + "a" * 64, "output_name": "summary",
        })
    connection.execute(
        """INSERT INTO workflow_runs(run_id,agent_run_id,session_id,tool_call_id,confirmation_id,
               attempt,retry_of_workflow_run_id,position,project_id,model_id,model_version,model_checksum,
               workflow_id,workflow_version,workflow_checksum,execution_service,executor_id,
               arguments_json,input_references_json,status,outputs_json,created_at)
           VALUES(?,?,'session',?,?,?,?,0,'p','model','1','model-digest','workflow','1',
                  'workflow-digest','executor','workflow-dag','{}','{}',?,?,?)""",
        (run_id, agent, call, confirmation, attempt, retry_of, status, json.dumps(outputs), utc_now()),
    )


def _prepare(connection, agent):
    save_preparation_connection(connection, agent_run_id=agent, tool_call_id=f"prepare-{agent}", snapshots=({
        "target_kind": "EXECUTABLE_MODEL", "target_id": "model", "status": "BLOCKED",
        "blocking_reasons": ["DATA_REQUIRED"],
    },))


def _facts(connection):
    changes = connection.total_changes
    (fact,) = collect_session_facts(connection, "work", "p", "alice")
    assert connection.total_changes == changes
    return fact


def test_current_task_uses_all_exact_links_and_keeps_historical_evidence(connection):
    _agent(connection, "old", 1)
    _task(connection, "old", revision=1)
    _workflow(connection, "historical", "old", "SUCCEEDED")
    _workflow(connection, "historical-retry", "old", "SUCCEEDED", attempt=2, retry_of="historical")
    _agent(connection, "author", 2)
    _task_run(connection, "author", 1)
    _task(connection, "author", result_evidence={
        "criterion_id": "delivery", "agent_run_id": "old", "workflow_run_id": "historical",
    })
    _workflow(connection, "by-author", "author", "SUCCEEDED")
    obsolete_confirmation = _confirmation(connection, "author", 2, binding=1, status="CONFIRMED")
    _workflow(connection, "wrong-revision", "author", "FAILED", confirmation=obsolete_confirmation, call="obsolete")
    _agent(connection, "linked", 3)
    _task_run(connection, "linked", 2)
    _workflow(connection, "failed", "linked", "FAILED")
    _workflow(connection, "retried", "linked", "RUNNING", attempt=2, retry_of="failed")
    _agent(connection, "confirmed", 4, "WAITING_EXECUTION")
    confirmed = _confirmation(connection, "confirmed", 4, binding=2, status="CONFIRMED")
    _workflow(connection, "by-confirmation", "confirmed", "QUEUED", confirmation=confirmed)
    append_event(connection, "session", "agent.complete", {"agent_run_id": "confirmed", "continuation_stop": "DUPLICATE"})
    _agent(connection, "unrelated", 5, "FAILED", "CONSULT_FAILED")
    append_event(connection, "session", "agent.complete", {"agent_run_id": "unrelated", "continuation_stop": "BUDGET"})
    connection.execute(
        "INSERT INTO run_controls(session_id,request_id,agent_run_id,requested_by,status,created_at) VALUES"
        "('session','relevant-stop','linked','alice','PROCESSING',?),"
        "('session','consult-stop','unrelated','alice','REQUESTED',?)",
        (utc_now(), utc_now()),
    )

    facts = _facts(connection)
    assert facts.task["spec"]["revision"] == 2
    assert facts.task["progress"]["criteria"][0]["satisfied"]
    assert set(facts.workflows) == {
        WorkflowFact("by-author", "SUCCEEDED", ("summary",)), WorkflowFact("retried", "RUNNING", ()),
        WorkflowFact("by-confirmation", "QUEUED", ()),
    }
    assert facts.evidence_workflows == (WorkflowFact("historical", "SUCCEEDED", ("summary",)),)
    assert (facts.goal_revision, facts.agent_status, facts.agent_error, facts.continuation_stop) == (
        4, "WAITING_EXECUTION", None, "DUPLICATE",
    )
    assert facts.stop_pending and not facts.stopped_before_start
    assert collect_session_facts(connection, "work", "p", "bob") == ()
    assert collect_session_facts(connection, "work", "other-project", "alice") == ()


def test_preparation_is_bound_to_relevant_goal_and_current_task_base_revision(connection):
    _agent(connection, "author", 1)
    _task(connection, "author", revision=1)
    _agent(connection, "outdated", 2, "RUNNING")
    _prepare(connection, "outdated")
    _agent(connection, "revised", 3)
    _task(connection, "revised", revision=2)
    assert _facts(connection).preparation is None

    _agent(connection, "prepare", 4, "RUNNING")
    _prepare(connection, "prepare")
    _agent(connection, "consult", 5, "CANCELLED", "AGENT_STOPPED")
    facts = _facts(connection)
    assert facts.goal_revision == 4
    assert facts.preparation["agent_run_id"] == "prepare"
    assert facts.preparation["base_task_revision"] == 2
    assert facts.preparation["fresh_for_current_turn"]
    assert facts.preparation["entries"][0]["blocking_reasons"] == ["DATA_REQUIRED"]
    assert facts.agent_status == "RUNNING"


def test_confirmation_requires_current_task_and_current_session_goal(connection):
    _agent(connection, "author", 1, "WAITING_CONFIRMATION")
    _task(connection, "author")
    confirmation = _confirmation(connection, "author", 1, binding=1, decision_state="REQUESTED")
    assert _facts(connection).confirmation_id is None
    connection.execute("UPDATE confirmation_tasks SET task_revision=2")
    facts = _facts(connection)
    assert (facts.confirmation_id, facts.confirmation_decision_state, facts.rejected) == (
        confirmation, "REQUESTED", False,
    )
    _agent(connection, "consult", 2)
    facts = _facts(connection)
    assert facts.confirmation_id is None and facts.goal_revision == 1

    connection.execute("UPDATE confirmations SET status='REJECTED', decision='REJECT', decision_state=NULL")
    assert _facts(connection).rejected
    connection.execute("UPDATE confirmation_tasks SET task_revision=1")
    assert not _facts(connection).rejected


def test_no_task_consultation_does_not_replace_latest_business_facts(connection):
    _agent(connection, "pure-consultation", 1)
    initial = _facts(connection)
    assert initial.task is None and initial.goal_revision is None
    _agent(connection, "prepared", 2)
    _prepare(connection, "prepared")
    _agent(connection, "consult", 3, "FAILED", "UNRELATED_FAILURE")
    facts = _facts(connection)
    assert facts.goal_revision == 2 and facts.preparation["agent_run_id"] == "prepared"

    _agent(connection, "executed", 4)
    _workflow(connection, "published", "executed", "SUCCEEDED")
    _agent(connection, "latest-consult", 5, "CANCELLED", "AGENT_STOPPED")
    facts = _facts(connection)
    assert (facts.goal_revision, facts.agent_status, facts.agent_error) == (4, "COMPLETE", None)
    assert facts.workflows == (WorkflowFact("published", "SUCCEEDED", ("summary",)),)
    assert facts.preparation is None and not facts.stopped_before_start


def _unstarted_request(connection, run_id, goal, *, business):
    policy = "EXECUTION_PROPOSAL" if business else "READ_ONLY"
    intent = "EXECUTE_ON_DATA" if business else "CONSULT"
    connection.execute(
        """INSERT INTO message_requests(agent_run_id,session_id,request_id,goal_revision,message,
               input_references_json,requested_mode,routing_source,routing_status,intent_decision,
               dialogue_policy,semantic_intent,task_action,clarification_required,route_input_tokens,
               route_output_tokens,route_total_tokens,route_attempts,route_provider_requests,
               unknown_cost_attempts,created_at)
           VALUES(?,'session',?,?,'Prompt','[]','DIALOGUE','EXPLICIT','DECIDED','DIALOGUE',
                  ?,?,'NONE',0,0,0,0,0,0,0,?)""",
        (run_id, f"request-{run_id}", goal, policy, intent, utc_now()),
    )
    connection.execute(
        "UPDATE sessions SET goal_revision=?, pending_agent_run_id=? WHERE session_id='session'", (goal, run_id),
    )


def _stop(connection, agent_run_id, request_id="stop"):
    connection.execute(
        """INSERT INTO run_controls(session_id,request_id,agent_run_id,requested_by,status,created_at)
           VALUES('session',?,?,'alice','REQUESTED',?)""",
        (request_id, agent_run_id, utc_now()),
    )


def test_pre_agent_stop_needs_targeted_durable_completion_and_business_context(connection):
    _unstarted_request(connection, "calculation", 1, business=True)
    _stop(connection, "calculation")
    facts = _facts(connection)
    assert facts.stop_pending and not facts.stopped_before_start
    connection.execute("UPDATE run_controls SET status='COMPLETE'")
    assert not _facts(connection).stopped_before_start
    connection.execute("UPDATE message_requests SET routing_status='FAILED',error_code='AGENT_STOPPED'")
    connection.execute("UPDATE sessions SET pending_agent_run_id=NULL")
    assert _facts(connection).stopped_before_start
    _unstarted_request(connection, "consult", 2, business=False)
    connection.execute(
        "UPDATE message_requests SET requested_mode='AUTO',routing_source='MODEL' WHERE agent_run_id='consult'",
    )
    connection.execute(
        "INSERT INTO message_workflow_selections VALUES (?, ?)",
        ("consult", '{"model_id":"candidate","business_capability":{}}'),
    )
    _stop(connection, "consult", "stop-consult")
    connection.execute("UPDATE run_controls SET status='COMPLETE' WHERE agent_run_id='consult'")
    connection.execute("UPDATE message_requests SET routing_status='FAILED',error_code='AGENT_STOPPED' WHERE agent_run_id='consult'")
    connection.execute("UPDATE sessions SET pending_agent_run_id=NULL")
    assert _facts(connection).goal_revision == 1


def test_unstarted_task_stop_requires_control_target_bound_to_current_revision(connection):
    _agent(connection, "author", 1)
    _task(connection, "author")
    _unstarted_request(connection, "calculation", 2, business=True)
    _stop(connection, "calculation", "control-proposal:stop-proposal")
    connection.execute(
        """INSERT INTO control_proposals(proposal_id,session_id,source_request_id,intent,target_type,
               target_id,task_revision,status,created_at) VALUES('stop-proposal','session','stop-message',
               'STOP_ANALYSIS','AGENT_RUN','calculation',1,'COMPLETE',?)""", (utc_now(),),
    )
    connection.execute("UPDATE message_requests SET routing_status='FAILED',error_code='AGENT_STOPPED'")
    connection.execute("UPDATE run_controls SET status='COMPLETE'")
    connection.execute("UPDATE sessions SET pending_agent_run_id=NULL")
    assert not _facts(connection).stopped_before_start
    connection.execute("UPDATE control_proposals SET task_revision=2")
    assert _facts(connection).stopped_before_start
