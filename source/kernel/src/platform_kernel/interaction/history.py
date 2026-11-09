"""Read-only projections for the conversation directory and message history."""

from pathlib import Path
import json

from ..application.agent_api import (
    get_run_connection,
    project_activities,
    session_run_summaries,
)
from ..application.workflow_api import session_execution_tasks
from ..database import connect_database, utc_now
from ..application.task_api import (
    preparation_in_connection,
    task_in_connection,
)
from ..errors import ResourceNotFoundError
from .api_projection import confirmation_response
from .actors import DEFAULT_LOCAL_ACTOR_SUBJECT
from .models import Session, SessionView
from .progress import build_turn_progress
from .state_projection import confirmation_from_row
from .control_proposal_queries import (
    list_control_messages_connection, list_control_proposals_connection,
)


def list_sessions(
    database: Path, project_id: str, before: int | None, limit: int, *,
    owner_subject: str = DEFAULT_LOCAL_ACTOR_SUBJECT, query: str = "",
    archived: bool = False,
) -> dict:
    escaped_query = (
        query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    with connect_database(database) as connection:
        rows = connection.execute(
            """SELECT s.rowid AS cursor, s.*,
                      COALESCE(r.workflow_draft_id,mr.workflow_draft_id) AS workflow_draft_id,
                      COALESCE(s.title,(SELECT substr(m.content,1,120) FROM messages m
                       WHERE m.session_id=s.session_id AND m.role='user'
                       ORDER BY m.message_id LIMIT 1)) AS display_title,
                      (SELECT e.created_at FROM session_events e
                       WHERE e.session_id=s.session_id AND e.event_type='session.created'
                       ORDER BY e.event_id LIMIT 1) AS created_at,
                      (EXISTS(
                        SELECT 1 FROM confirmations c
                        WHERE c.session_id=s.session_id AND (
                          c.reason LIKE 'SYNTHETIC_TEST_ONLY%'
                          OR c.reason LIKE '【验证会话】%'
                          OR c.reason='别名命中验证完成，不重复执行'
                        )
                      ) OR EXISTS(
                        SELECT 1 FROM messages vm
                        WHERE vm.session_id=s.session_id AND (
                          vm.content LIKE '%SYNTHETIC_TEST_ONLY%'
                          OR vm.content LIKE '【验证会话】%'
                        )
                      )) AS verification_session
               FROM sessions s LEFT JOIN agent_requests r
                 ON r.agent_run_id=COALESCE(s.pending_agent_run_id,s.agent_run_id)
               LEFT JOIN message_requests mr
                 ON mr.session_id=s.session_id AND mr.goal_revision=s.goal_revision
                AND mr.routing_status NOT IN ('CONTROL_PENDING','CONTROL_COMPLETE')
               WHERE s.project_id=? AND s.owner_subject=?
                 AND ((?=1 AND s.archived_at IS NOT NULL)
                      OR (?=0 AND s.archived_at IS NULL))
                 AND (?='' OR COALESCE(s.title,(SELECT substr(q.content,1,120)
                       FROM messages q WHERE q.session_id=s.session_id
                       AND q.role='user' ORDER BY q.message_id LIMIT 1),'')
                       LIKE ? ESCAPE '\\')
                 AND (? IS NULL OR s.rowid < ?)
               ORDER BY s.rowid DESC LIMIT ?""",
            (
                project_id, owner_subject, int(archived), int(archived), query,
                f"%{escaped_query}%", before, before, limit + 1,
            ),
        ).fetchall()
    page = rows[:limit]
    return {
        "items": [{
            **{key: row[key] for key in (
                "session_id", "project_id", "created_at", "goal_revision",
                "agent_run_id", "pending_agent_run_id", "workflow_draft_id",
            )},
            "title": (
                f"【验证会话】{row['display_title'] or '未命名验证'}"
                if row["verification_session"]
                and not (row["display_title"] or "").startswith("【验证会话】")
                else row["display_title"]
            ),
            "archived": row["archived_at"] is not None,
        } for row in page],
        "next_cursor": page[-1]["cursor"] if len(rows) > limit else None,
    }


def session_view(database: Path, project_id: str, session_id: str) -> SessionView:
    """Project a complete Session view from one read transaction."""
    with connect_database(database) as connection:
        connection.execute("BEGIN")
        row = connection.execute(
            """SELECT s.*,
                      COALESCE(r.workflow_draft_id,mr.workflow_draft_id) AS workflow_draft_id
               FROM sessions AS s
               LEFT JOIN agent_requests AS r
                 ON r.agent_run_id = COALESCE(
                     s.pending_agent_run_id, s.agent_run_id
                 )
               LEFT JOIN message_requests AS mr
                 ON mr.session_id=s.session_id AND mr.goal_revision=s.goal_revision
                AND mr.routing_status NOT IN ('CONTROL_PENDING','CONTROL_COMPLETE')
               WHERE s.session_id = ? AND s.project_id = ?""",
            (session_id, project_id),
        ).fetchone()
        if row is None:
            raise ResourceNotFoundError("SESSION_NOT_FOUND")
        confirmation_row = connection.execute(
            """SELECT * FROM confirmations
               WHERE session_id = ? AND goal_revision = ?
               ORDER BY rowid DESC LIMIT 1""",
            (session_id, row["goal_revision"]),
        ).fetchone()
        confirmation = (
            None
            if confirmation_row is None
            else confirmation_from_row(connection, confirmation_row)
        )
        run = (
            None
            if row["agent_run_id"] is None
            else get_run_connection(
                connection, row["agent_run_id"], row["project_id"]
            )
        )
        workflow_ids = () if run is None else tuple(
            dict.fromkeys(
                action.workflow_run_id
                for action in run.actions
                if action.workflow_run_id is not None
            )
        )
        remote_outputs = () if run is None else tuple(
            reference
            for action in run.actions
            for reference in action.remote_outputs
        )
        session = Session(
            session_id=row["session_id"],
            project_id=row["project_id"],
            goal_revision=row["goal_revision"],
            agent_run_id=row["agent_run_id"],
            confirmation=confirmation,
            agent_output=None if run is None else run.output,
            workflow_run_ids=workflow_ids,
            remote_outputs=remote_outputs,
            workflow_draft_id=row["workflow_draft_id"],
            task=task_in_connection(connection, session_id),
            control_proposals=tuple(list_control_proposals_connection(connection, session_id)),
        )
        return SessionView(session, () if run is None else run.steps)


def message_history(database: Path, project_id: str, session_id: str,
                    before: int | None, limit: int) -> dict:
    with connect_database(database) as connection:
        # Every domain projects through this connection so a completion cannot
        # split the messages, process history, approval state and event cursor.
        connection.execute("BEGIN")
        session = connection.execute(
            "SELECT * FROM sessions WHERE session_id=? AND project_id=?", (session_id, project_id),
        ).fetchone()
        if session is None:
            raise ResourceNotFoundError("SESSION_NOT_FOUND")
        rows = connection.execute(
            "SELECT message_id,goal_revision,role,content FROM messages "
            "WHERE session_id=? AND (? IS NULL OR message_id < ?) "
            "ORDER BY message_id DESC LIMIT ?",
            (session_id, before, before, limit + 1),
        ).fetchall()
        page = list(reversed(rows[:limit]))
        revisions = tuple(sorted({row["goal_revision"] for row in page}))
        summaries = session_run_summaries(connection, session_id, project_id, revisions)
        tasks = session_execution_tasks(
            connection, session_id, project_id,
            tuple(run["agent_run_id"] for run in summaries.values()),
        )
        turns = []
        for revision in revisions:
            message_request = connection.execute(
                """SELECT * FROM message_requests
                   WHERE session_id=? AND goal_revision=?
                     AND routing_status NOT IN ('CONTROL_PENDING','CONTROL_COMPLETE')
                   ORDER BY rowid DESC LIMIT 1""",
                (session_id, revision),
            ).fetchone()
            if revision in summaries:
                run = summaries[revision]
            elif message_request is not None:
                route_status = message_request["routing_status"]
                run = {
                    "agent_run_id": message_request["agent_run_id"],
                    "status": (
                        "FAILED"
                        if route_status == "FAILED"
                        else "ROUTING"
                        if route_status in {"ROUTING_REQUESTED", "ROUTING_RUNNING"}
                        else "QUEUED"
                    ),
                    "error_code": message_request["error_code"],
                    "timing": {
                        "created_at": message_request["created_at"],
                        "started_at": message_request["route_started_at"],
                        "finished_at": message_request["route_finished_at"],
                    },
                    "activities": project_activities(
                        connection, session_id, (message_request["agent_run_id"],),
                    )[message_request["agent_run_id"]],
                    "plans": [],
                    "diagnostics": (
                        {"reason": message_request["error_code"]}
                        if message_request["error_code"] == "AGENT_INTENT_ROUTING_INVALID"
                        else None
                    ),
                    "agent_metrics": None,
                    "interaction_options": [],
                    "reply": None,
                }
            else:
                # Only the current durable start can have a message before its
                # AgentRun exists. Earlier Turns always retain their Run.
                assert revision == session["goal_revision"] and session["pending_agent_run_id"] is not None
                run = {
                    "agent_run_id": session["pending_agent_run_id"], "status": "QUEUED", "error_code": None,
                    "timing": {"created_at": None, "started_at": None, "finished_at": None},
                    "activities": project_activities(
                        connection, session_id, (session["pending_agent_run_id"],),
                    )[session["pending_agent_run_id"]], "plans": [],
                    "diagnostics": None, "agent_metrics": None,
                    "interaction_options": [],
                    "reply": None,
                }
            request = connection.execute(
                "SELECT mode,workflow_draft_id FROM agent_requests WHERE agent_run_id=?",
                (run["agent_run_id"],),
            ).fetchone()
            request_mode = (
                request["mode"]
                if request is not None
                else message_request["resolved_mode"] or message_request["requested_mode"]
            )
            workflow_draft_id = (
                request["workflow_draft_id"]
                if request is not None
                else message_request["workflow_draft_id"]
            )
            confirmations = connection.execute(
                "SELECT * FROM confirmations WHERE session_id=? AND goal_revision=? ORDER BY rowid",
                (session_id, revision),
            ).fetchall()
            confirmation_items = [{
                **confirmation_response(confirmation_from_row(connection, row)),
                "decision": row["decision"], "decision_state": row["decision_state"],
            } for row in confirmations]
            turn = {
                "goal_revision": revision, **run, "request_mode": request_mode,
                "request_id": None if message_request is None else message_request["request_id"],
                "requested_mode": None if message_request is None else message_request["requested_mode"],
                "resolved_mode": None if message_request is None else message_request["resolved_mode"],
                "routing_status": None if message_request is None else message_request["routing_status"],
                "routing_source": None if message_request is None else message_request["routing_source"],
                "intent_decision": None if message_request is None else message_request["intent_decision"],
                "semantic_intent": None if message_request is None else message_request["semantic_intent"],
                "task_action": None if message_request is None else message_request["task_action"],
                "control_intent": None if message_request is None else message_request["control_intent"],
                "clarification_reason": None if message_request is None else message_request["clarification_reason"],
                "dialogue_policy": None if message_request is None else message_request["dialogue_policy"],
                "clarification_required": False if message_request is None else bool(message_request["clarification_required"]),
                "corrects_request_id": None if message_request is None else message_request["corrects_request_id"],
                "corrects_agent_run_id": next((item[0] for item in connection.execute("SELECT target_agent_run_id FROM message_corrections WHERE session_id=? AND request_id=?", (session_id, None if message_request is None else message_request["request_id"]))), None),
                "routing_metrics": (
                    None
                    if message_request is None or message_request["requested_mode"] != "AUTO"
                    else {
                        "provider": message_request["route_provider"],
                        "model": message_request["route_model"],
                        "request_count": message_request["route_provider_requests"],
                        "input_tokens": (
                            message_request["route_input_tokens"]
                            if message_request["route_finished_at"] is not None
                            else None
                        ),
                        "output_tokens": (
                            message_request["route_output_tokens"]
                            if message_request["route_finished_at"] is not None
                            else None
                        ),
                        "total_tokens": (
                            message_request["route_total_tokens"]
                            if message_request["route_finished_at"] is not None
                            else None
                        ),
                        "duration_ms": message_request["route_duration_ms"],
                        "unknown_attempts": message_request["unknown_cost_attempts"],
                    }
                ),
                "workflow_draft_id": workflow_draft_id,
                "confirmations": confirmation_items,
                "execution_tasks": [{
                    **task, "goal_revision": revision,
                    "confirmation_id": task["confirmation_id"],
                } for task in tasks.get(run["agent_run_id"], [])],
            }
            turn["progress_updates"] = build_turn_progress(turn)
            turns.append(turn)
        request_references = {
            revision: json.loads(row["input_references_json"])
            for revision in revisions
            if (row := connection.execute(
                """SELECT input_references_json FROM message_requests
                   WHERE session_id=? AND goal_revision=?
                     AND routing_status NOT IN ('CONTROL_PENDING','CONTROL_COMPLETE')
                   ORDER BY rowid DESC LIMIT 1""",
                (session_id, revision),
            ).fetchone()) is not None
        }
        return {
            "session_id": session_id, "goal_revision": session["goal_revision"],
            "items": [{
                **dict(row),
                "input_references": (
                    request_references.get(row["goal_revision"], [])
                    if row["role"] == "user" else []
                ),
            } for row in page], "turns": turns,
            "next_cursor": page[0]["message_id"] if len(rows) > limit else None,
            "event_cursor": connection.execute(
                "SELECT COALESCE(MAX(event_id),0) FROM session_events WHERE session_id=?",
                (session_id,),
            ).fetchone()[0],
            "snapshot_at": utc_now(),
            "task": task_in_connection(connection, session_id),
            "preparation": preparation_in_connection(
                connection,
                session_id,
                current_goal_revision=session["goal_revision"],
            ),
            "control_requests": [dict(item) for item in connection.execute(
                "SELECT request_id,agent_run_id,status FROM run_controls WHERE session_id=? ORDER BY rowid", (session_id,))],
            "control_proposals": list_control_proposals_connection(connection, session_id),
            "control_messages": list_control_messages_connection(connection, session_id),
        }
