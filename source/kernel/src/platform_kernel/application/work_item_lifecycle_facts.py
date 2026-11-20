"""Read the durable Task, preparation, and execution facts behind a WorkItem."""

from __future__ import annotations

from dataclasses import dataclass
import json
from sqlite3 import Connection, Row

from .task_api import preparation_in_connection, task_in_connection
from .work_item_links import OWNED_LINKED_SESSIONS_SQL


@dataclass(frozen=True)
class WorkflowFact:
    run_id: str
    status: str
    output_names: tuple[str, ...]


@dataclass(frozen=True)
class SessionFacts:
    session_id: str
    task: dict | None = None
    preparation: dict | None = None
    workflows: tuple[WorkflowFact, ...] = ()
    evidence_workflows: tuple[WorkflowFact, ...] = ()
    confirmation_id: str | None = None
    confirmation_decision_state: str | None = None
    rejected: bool = False
    stop_pending: bool = False
    agent_status: str | None = None
    agent_error: str | None = None
    stopped_before_start: bool = False
    continuation_stop: str | None = None
    goal_revision: int | None = None


def collect_session_facts(
    connection: Connection, work_item_id: str, project_id: str, subject: str,
) -> tuple[SessionFacts, ...]:
    """Use the caller's SQLite snapshot and preserve the existing Task contract."""
    sessions = connection.execute(
        """SELECT s.session_id, s.goal_revision, s.pending_agent_run_id
           FROM work_items AS item
           JOIN work_item_sessions AS link ON link.work_item_id = item.work_item_id
           JOIN sessions AS s ON s.session_id = link.session_id
             AND s.project_id = item.project_id AND s.owner_subject = item.owner_subject
           WHERE item.work_item_id = ? AND item.project_id = ? AND item.owner_subject = ?
           ORDER BY link.linked_at, s.session_id""",
        (work_item_id, project_id, subject),
    ).fetchall()
    return tuple(_session_facts(connection, session, project_id) for session in sessions)


def _session_facts(connection: Connection, session: Row, project_id: str) -> SessionFacts:
    session_id = session["session_id"]
    task = task_in_connection(connection, session_id)
    spec = task["spec"] if task is not None else None
    scope = {
        "session_id": session_id, "project_id": project_id,
        "task_id": spec["task_id"] if spec is not None else None,
        "task_revision": spec["revision"] if spec is not None else None,
    }
    agents = _related_agents(connection, scope)
    agent = agents[0] if agents else None
    unstarted = _unstarted_control(connection, scope, session)
    if unstarted is not None and (agent is None or unstarted["goal_revision"] > agent["goal_revision"]):
        agent = None
    else:
        unstarted = None

    execution_agents = agents
    if task is None:
        execution_agents = [agent] if agent is not None else []
    workflows = _latest_workflows(connection, scope, [
        row["agent_run_id"] for row in execution_agents if row["execution_linked"]
    ])
    evidence = _evidence_workflows(connection, scope, spec) if spec is not None else ()
    stop_targets = {
        row["agent_run_id"] for row in workflows if row["status"] in ("QUEUED", "RUNNING")
    }
    if agent is not None:
        stop_targets.add(agent["agent_run_id"])
    stop_pending = _stop_pending(connection, session_id, stop_targets) or bool(
        unstarted and unstarted["stop_pending"]
    )

    preparation = confirmation = pending = continuation = None
    if agent is not None:
        preparation = _current_preparation(connection, scope, agent)
        confirmation = _latest_confirmation(connection, scope, agent)
        if (
            confirmation is not None and confirmation["status"] == "PENDING"
            and agent["goal_revision"] == session["goal_revision"]
        ):
            pending = confirmation
        continuation = connection.execute(
            """SELECT json_extract(payload_json, '$.continuation_stop') AS reason
               FROM session_events WHERE session_id = ? AND event_type = 'agent.complete'
                 AND json_extract(payload_json, '$.agent_run_id') = ?
               ORDER BY event_id DESC LIMIT 1""",
            (session_id, agent["agent_run_id"]),
        ).fetchone()

    focus = agent if agent is not None else unstarted
    return SessionFacts(
        session_id=session_id, task=task, preparation=preparation,
        workflows=tuple(map(_workflow_fact, workflows)), evidence_workflows=evidence,
        confirmation_id=pending["confirmation_id"] if pending is not None else None,
        confirmation_decision_state=pending["decision_state"] if pending is not None else None,
        rejected=confirmation is not None and confirmation["status"] == "REJECTED",
        stop_pending=stop_pending,
        agent_status=agent["status"] if agent is not None else None,
        agent_error=agent["error"] if agent is not None else None,
        stopped_before_start=bool(unstarted and unstarted["stopped"]),
        continuation_stop=continuation["reason"] if continuation is not None else None,
        goal_revision=focus["goal_revision"] if focus is not None else None,
    )


def _related_agents(connection: Connection, scope: dict) -> list[Row]:
    return connection.execute(
        """WITH execution_agents AS (
               SELECT agent_run_id FROM task_revisions
                 WHERE task_id = :task_id AND revision = :task_revision
               UNION SELECT agent_run_id FROM task_runs
                 WHERE task_id = :task_id AND task_revision = :task_revision
               UNION SELECT c.agent_run_id FROM confirmation_tasks AS ct
                 JOIN confirmations AS c ON c.confirmation_id = ct.confirmation_id
                 WHERE ct.task_id = :task_id AND ct.task_revision = :task_revision
                   AND c.session_id = :session_id
               UNION SELECT agent_run_id FROM workflow_runs
                 WHERE :task_id IS NULL AND session_id = :session_id AND project_id = :project_id
               UNION SELECT c.agent_run_id FROM confirmations AS c
                 LEFT JOIN confirmation_tasks AS ct ON ct.confirmation_id = c.confirmation_id
                 WHERE :task_id IS NULL AND c.session_id = :session_id AND ct.confirmation_id IS NULL
                   AND EXISTS (SELECT 1 FROM confirmation_actions AS action
                               WHERE action.confirmation_id = c.confirmation_id)
           )
           SELECT a.agent_run_id, a.goal_revision, a.status, a.error,
                  execution.agent_run_id IS NOT NULL AS execution_linked
           FROM agent_runs AS a
           LEFT JOIN execution_agents AS execution ON execution.agent_run_id = a.agent_run_id
           LEFT JOIN task_preparations AS p ON p.agent_run_id = a.agent_run_id
             AND p.session_id = a.session_id
             AND p.task_id IS :task_id AND p.base_task_revision IS :task_revision
           WHERE a.session_id = :session_id AND a.project_id = :project_id
             AND (execution.agent_run_id IS NOT NULL OR p.preparation_id IS NOT NULL)
           ORDER BY a.goal_revision DESC, a.rowid DESC""",
        scope,
    ).fetchall()


def _current_preparation(connection: Connection, scope: dict, agent: Row) -> dict | None:
    preparation = preparation_in_connection(
        connection, scope["session_id"], current_goal_revision=agent["goal_revision"],
    )
    if preparation is not None and (
        preparation["agent_run_id"], preparation["goal_revision"],
        preparation["task_id"], preparation["base_task_revision"],
    ) == (agent["agent_run_id"], agent["goal_revision"], scope["task_id"], scope["task_revision"]):
        return preparation
    return None


def _latest_confirmation(connection: Connection, scope: dict, agent: Row) -> Row | None:
    return connection.execute(
        """SELECT c.confirmation_id, c.status, c.decision_state
           FROM confirmations AS c
           LEFT JOIN confirmation_tasks AS ct ON ct.confirmation_id = c.confirmation_id
           WHERE c.session_id = :session_id AND c.agent_run_id = :agent_run_id
             AND c.goal_revision = :goal_revision
             AND ct.task_id IS :task_id AND ct.task_revision IS :task_revision
           ORDER BY c.revision DESC, c.rowid DESC LIMIT 1""",
        scope | {"agent_run_id": agent["agent_run_id"], "goal_revision": agent["goal_revision"]},
    ).fetchone()


def _latest_workflows(connection: Connection, scope: dict, agent_ids: list[str]) -> tuple[Row, ...]:
    if not agent_ids:
        return ()
    rows = connection.execute(
        """SELECT w.run_id, w.status, w.outputs_json, w.agent_run_id, w.tool_call_id
           FROM workflow_runs AS w
           LEFT JOIN confirmations AS c ON c.confirmation_id = w.confirmation_id
             AND c.session_id = w.session_id AND c.agent_run_id = w.agent_run_id
           LEFT JOIN confirmation_tasks AS ct ON ct.confirmation_id = c.confirmation_id
           WHERE w.session_id = :session_id AND w.project_id = :project_id
             AND w.agent_run_id IN (SELECT value FROM json_each(:agent_ids))
             AND (w.confirmation_id IS NULL OR (
                 c.confirmation_id IS NOT NULL AND ct.task_id IS :task_id AND ct.task_revision IS :task_revision
             ))
           ORDER BY w.agent_run_id, w.tool_call_id, w.attempt DESC""",
        scope | {"agent_ids": json.dumps(agent_ids)},
    )
    latest: dict[tuple[str, str], Row] = {}
    for row in rows:
        latest.setdefault((row["agent_run_id"], row["tool_call_id"]), row)
    return tuple(latest.values())


def _evidence_workflows(connection: Connection, scope: dict, spec: dict) -> tuple[WorkflowFact, ...]:
    stored = connection.execute(
        "SELECT evidence_json FROM task_revisions WHERE task_id = :task_id AND revision = :task_revision",
        scope,
    ).fetchone()
    evidence = json.loads(stored["evidence_json"])
    pins: dict[str, set[tuple[str, str]]] = {}
    for criterion in spec["criteria"]:
        proof = evidence.get(criterion["id"])
        if criterion["kind"] == "RESULT" and proof is not None and proof.get("workflow_run_id"):
            pins.setdefault(proof["workflow_run_id"], set()).add((proof["agent_run_id"], criterion["model_id"]))
    if not pins:
        return ()
    rows = connection.execute(
        """SELECT w.run_id, w.agent_run_id, w.model_id, w.status, w.outputs_json
           FROM workflow_runs AS w
           JOIN agent_runs AS a ON a.agent_run_id = w.agent_run_id
             AND a.session_id = w.session_id AND a.project_id = w.project_id
           WHERE w.session_id = :session_id AND w.project_id = :project_id
             AND w.run_id IN (SELECT value FROM json_each(:run_ids))
           ORDER BY w.run_id""",
        scope | {"run_ids": json.dumps(tuple(pins))},
    )
    return tuple(_workflow_fact(row) for row in rows if (row["agent_run_id"], row["model_id"]) in pins[row["run_id"]])


def _workflow_fact(row: Row) -> WorkflowFact:
    return WorkflowFact(row["run_id"], row["status"], tuple(
        name for output in json.loads(row["outputs_json"])
        if isinstance(name := output.get("output_name"), str) and name
    ))


def _stop_pending(connection: Connection, session_id: str, agent_ids: set[str]) -> bool:
    return bool(agent_ids) and connection.execute(
        """SELECT 1 FROM run_controls WHERE session_id = ? AND status IN ('REQUESTED', 'PROCESSING')
             AND agent_run_id IN (SELECT value FROM json_each(?)) LIMIT 1""",
        (session_id, json.dumps(sorted(agent_ids))),
    ).fetchone() is not None


def _unstarted_control(connection: Connection, scope: dict, session: Row) -> Row | None:
    # Pre-admission requests carry no Task revision. A confirmed control proposal
    # can supply an exact Task binding for its own downstream stop request.
    return connection.execute(
        """SELECT m.goal_revision,
                  MAX(r.status IN ('REQUESTED', 'PROCESSING')
                      AND m.agent_run_id IS :pending_agent_run_id
                      AND m.goal_revision = :session_goal_revision) AS stop_pending,
                  MAX(r.status = 'COMPLETE' AND m.routing_status = 'FAILED'
                      AND m.error_code IS 'AGENT_STOPPED') AS stopped
           FROM message_requests AS m
           JOIN run_controls AS r ON r.session_id = m.session_id AND r.agent_run_id = m.agent_run_id
           LEFT JOIN control_proposals AS p ON p.session_id = m.session_id
             AND p.target_type = 'AGENT_RUN' AND p.target_id = m.agent_run_id
             AND p.intent IN ('STOP_ANALYSIS', 'CANCEL_EXECUTION') AND p.status IN ('CONFIRMED', 'COMPLETE')
             AND r.request_id = 'control-proposal:' || p.proposal_id
           WHERE m.session_id = :session_id
             AND NOT EXISTS (SELECT 1 FROM agent_runs AS a WHERE a.agent_run_id = m.agent_run_id)
             AND (:task_id IS NULL OR p.task_revision = :task_revision)
             AND (m.dialogue_policy = 'EXECUTION_PROPOSAL' OR m.intent_decision = 'WORKFLOW_DRAFT'
                  OR EXISTS (SELECT 1 FROM message_workflow_selections AS sel
                             WHERE sel.agent_run_id = m.agent_run_id))
           GROUP BY m.agent_run_id HAVING stop_pending OR stopped
           ORDER BY m.goal_revision DESC, m.rowid DESC LIMIT 1""",
        scope | {
            "pending_agent_run_id": session["pending_agent_run_id"], "session_goal_revision": session["goal_revision"],
        },
    ).fetchone()


def handoff_task_rows(
    connection: Connection, work_item_id: str, project_id: str,
    subject: str, current_session_id: str,
) -> list[Row]:
    return connection.execute(
        OWNED_LINKED_SESSIONS_SQL + """
        SELECT s.session_id, s.title AS session_title, t.task_id, t.revision,
               r.created_at AS updated_at
        FROM linked_sessions s
        JOIN tasks t ON t.session_id=s.session_id AND t.lifecycle='ACTIVE'
        JOIN task_revisions r ON r.task_id=t.task_id AND r.revision=t.revision
        WHERE s.session_id<>? AND s.archived_at IS NULL
        ORDER BY r.created_at DESC, s.session_id LIMIT 8""",
        (work_item_id, project_id, subject, current_session_id),
    ).fetchall()


def handoff_approval_rows(
    connection: Connection, work_item_id: str, project_id: str,
    subject: str, current_session_id: str,
) -> list[Row]:
    return connection.execute(
        OWNED_LINKED_SESSIONS_SQL + """
        SELECT s.session_id, s.title AS session_title, e.event_id, e.created_at AS approved_at,
               c.goal_revision, c.revision AS decision_revision,
               a.position, a.model_id, a.model_version, a.model_checksum, a.workflow_checksum,
               a.arguments_json, a.input_references_json
        FROM linked_sessions s
        JOIN confirmations c ON c.session_id=s.session_id AND c.goal_revision=s.goal_revision
        JOIN confirmation_actions a ON a.confirmation_id=c.confirmation_id
        JOIN session_events e ON e.session_id=s.session_id AND e.event_type='confirmation.confirmed'
             AND json_extract(e.payload_json, '$.confirmation_id')=c.confirmation_id
        WHERE s.session_id<>? AND s.archived_at IS NULL
          AND c.status='CONFIRMED' AND c.decision='CONFIRM' AND c.decision_subject=?
          AND json_extract(e.payload_json, '$.decision_subject')=c.decision_subject
          AND NOT EXISTS (
              SELECT 1 FROM confirmations newer WHERE newer.session_id=c.session_id
                AND newer.goal_revision=c.goal_revision AND newer.revision>c.revision
          )
        ORDER BY e.event_id DESC, a.position LIMIT 8""",
        (work_item_id, project_id, subject, current_session_id, subject),
    ).fetchall()
