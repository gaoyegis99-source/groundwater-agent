"""User ownership checks at the public Kernel boundary."""

from __future__ import annotations

import json
from pathlib import Path

from ..database import append_event, connect_database, utc_now
from ..errors import CapacityLimitError, ConfirmationConflictError, ResourceNotFoundError
from ..application.query_dtos import DispatchAuthorizationRun
from ..application.task_api import require_current_confirmation


def agent_owner_subject(database: Path, agent_run_id: str) -> str | None:
    """Resolve an Agent principal through the Interaction-owned Session."""

    with connect_database(database) as connection:
        row = connection.execute(
            """SELECT s.owner_subject
                 FROM agent_runs a
                 JOIN sessions s ON s.session_id=a.session_id
                WHERE a.agent_run_id=?""",
            (agent_run_id,),
        ).fetchone()
    return None if row is None else str(row["owner_subject"])


def current_agent_turn_subject(
    database: Path, project_id: str, session_id: str, agent_run_id: str, goal_revision: int,
) -> str | None:
    with connect_database(database) as connection:
        row = connection.execute(
            """SELECT s.owner_subject FROM sessions s
               JOIN agent_runs a ON a.session_id=s.session_id AND a.project_id=s.project_id
               WHERE s.project_id=? AND s.session_id=? AND a.agent_run_id=?
                 AND s.goal_revision=? AND a.goal_revision=s.goal_revision
                 AND (s.agent_run_id=a.agent_run_id OR s.pending_agent_run_id=a.agent_run_id)
                 AND s.archived_at IS NULL""",
            (project_id, session_id, agent_run_id, goal_revision),
        ).fetchone()
    return None if row is None else str(row["owner_subject"])


def require_owned_session(
    database: Path, project_id: str, session_id: str, subject: str
) -> None:
    with connect_database(database) as connection:
        row = connection.execute(
            "SELECT 1 FROM sessions WHERE session_id=? AND project_id=? "
            "AND owner_subject=?",
            (session_id, project_id, subject),
        ).fetchone()
    if row is None:
        raise ResourceNotFoundError("SESSION_NOT_FOUND")


def require_owned_agent(
    database: Path, project_id: str, agent_run_id: str, subject: str
) -> None:
    with connect_database(database) as connection:
        row = connection.execute(
            """SELECT 1 FROM agent_runs a JOIN sessions s ON s.session_id=a.session_id
               WHERE a.agent_run_id=? AND a.project_id=? AND s.owner_subject=?""",
            (agent_run_id, project_id, subject),
        ).fetchone()
    if row is None:
        raise ResourceNotFoundError("AGENT_RUN_NOT_FOUND")


def require_owned_workflow(
    database: Path, project_id: str, run_id: str, subject: str
) -> None:
    with connect_database(database) as connection:
        row = connection.execute(
            """SELECT 1 FROM workflow_runs w JOIN sessions s ON s.session_id=w.session_id
               WHERE w.run_id=? AND w.project_id=? AND s.owner_subject=?""",
            (run_id, project_id, subject),
        ).fetchone()
    if row is None:
        raise ResourceNotFoundError("WORKFLOW_RUN_NOT_FOUND")


def dispatch_confirmation_current(database: Path, run: DispatchAuthorizationRun) -> bool:
    """Check persisted approval facts before creating another remote Job.

    The trusted direct Agent API persisted an APPROVED action but not a
    confirmation ID on its WorkflowRun. Recheck that action rather than
    treating the missing ID as unconditional authorization.
    """
    if run.confirmation_id is None:
        return _direct_dispatch_authorized(database, run)
    with connect_database(database) as connection:
        row = connection.execute(
            """SELECT c.status, c.decision, c.decision_state,
                      ca.arguments_json, ca.input_references_json
                 FROM confirmations c
                 JOIN sessions s ON s.session_id=c.session_id
                 JOIN agent_runs a ON a.agent_run_id=c.agent_run_id
                 JOIN confirmation_actions ca ON ca.confirmation_id=c.confirmation_id
                WHERE c.confirmation_id=? AND c.session_id=? AND s.project_id=?
                  AND c.agent_run_id=? AND a.session_id=c.session_id
                  AND a.project_id=s.project_id AND a.goal_revision=c.goal_revision
                  AND ca.tool_call_id=? AND ca.model_id=? AND ca.model_version=?
                  AND ca.model_checksum=? AND ca.workflow_id=?
                  AND ca.workflow_version=? AND ca.workflow_checksum=?""",
            (
                run.confirmation_id, run.session_id, run.project_id,
                run.agent_run_id, run.tool_call_id, run.model_id,
                run.model_version, run.model_checksum, run.workflow_id,
                run.workflow_version, run.workflow_checksum,
            ),
        ).fetchone()
        if row is None:
            if not _published_step_dispatch_current(connection, run):
                return False
        elif not _action_payload_matches(row, run) or row["decision"] != "CONFIRM" or not (
            row["status"] == "CONFIRMED"
            or (
                row["status"] == "PENDING"
                and row["decision_state"] in {"REQUESTED", "RUNNING"}
            )
        ):
            return False
        try:
            require_current_confirmation(connection, run.confirmation_id)
        except ConfirmationConflictError:
            return False
        return True


def _published_step_dispatch_current(connection, run) -> bool:
    row = connection.execute(
        """SELECT p.plan_json, source.outputs_json
           FROM workflow_output_sources deps
           JOIN workflow_runs source ON source.run_id=deps.source_workflow_run_id
           JOIN published_output_steps p ON p.confirmation_id=source.confirmation_id
              AND p.source_tool_call_id=source.tool_call_id
           JOIN confirmations c ON c.confirmation_id=p.confirmation_id
           JOIN sessions s ON s.session_id=c.session_id
           WHERE deps.workflow_run_id=? AND p.confirmation_id=? AND p.tool_call_id=?
             AND source.status='SUCCEEDED' AND source.agent_run_id=? AND source.session_id=?
             AND source.project_id=? AND s.project_id=?
             AND c.status='CONFIRMED' AND c.decision='CONFIRM'""",
        (run.run_id, run.confirmation_id, run.tool_call_id, run.agent_run_id, run.session_id,
         run.project_id, run.project_id),
    ).fetchone()
    if row is None:
        return False
    plan = json.loads(row["plan_json"])
    outputs = [ref for ref in json.loads(row["outputs_json"]) if ref.get("output_name") == plan["output"]]
    if len(outputs) != 1:
        return False
    reference = outputs[0]
    return (
        all(getattr(run, key) == plan[key] for key in (
            "model_id", "model_version", "model_checksum", "workflow_id", "workflow_version", "workflow_checksum",
        ))
        and run.arguments == {**plan["arguments"], plan["input"]: reference["version_id"]}
        and {key: ref.as_storage_dict() for key, ref in run.input_references.items()} == {plan["input"]: reference}
    )


def _direct_dispatch_authorized(database: Path, run: DispatchAuthorizationRun) -> bool:
    with connect_database(database) as connection:
        action = connection.execute(
            """SELECT aa.arguments_json, aa.input_references_json
                 FROM agent_actions aa
                 JOIN agent_runs a ON a.agent_run_id=aa.agent_run_id
                WHERE aa.agent_run_id=? AND aa.tool_call_id=?
                  AND aa.status='APPROVED' AND a.status='WAITING_EXECUTION'
                  AND a.session_id=? AND a.project_id=?
                  AND aa.model_id=? AND aa.model_version=?
                  AND aa.model_checksum=? AND aa.workflow_id=?
                  AND aa.workflow_version=? AND aa.workflow_checksum=?""",
            (
                run.agent_run_id, run.tool_call_id, run.session_id,
                run.project_id, run.model_id, run.model_version,
                run.model_checksum, run.workflow_id, run.workflow_version,
                run.workflow_checksum,
            ),
        ).fetchone()
        if action is None or not _action_payload_matches(action, run):
            return False
        # Direct confirmation predates the decision queue. Its pending
        # confirmation may still have no decision, but a later explicit
        # rejection/supersession must revoke the previously approved action.
        approval = connection.execute(
            """SELECT c.confirmation_id, c.status, c.decision, c.decision_state
                 FROM confirmations c
                 JOIN confirmation_actions ca
                   ON ca.confirmation_id=c.confirmation_id
                WHERE c.agent_run_id=? AND ca.tool_call_id=?
                ORDER BY c.revision DESC, c.rowid DESC LIMIT 1""",
            (run.agent_run_id, run.tool_call_id),
        ).fetchone()
        if approval is None:
            return True  # Old rows may predate persisted confirmations.
        if approval["status"] not in {"PENDING", "CONFIRMED"} or not (
            approval["decision"] is None and approval["status"] == "PENDING"
            or approval["decision"] == "CONFIRM"
            and (
                approval["status"] == "CONFIRMED"
                or approval["decision_state"] in {"REQUESTED", "RUNNING"}
            )
        ):
            return False
        try:
            require_current_confirmation(connection, approval["confirmation_id"])
        except ConfirmationConflictError:
            return False
        return True


def _action_payload_matches(row, run: DispatchAuthorizationRun) -> bool:
    try:
        return (
            json.loads(row["arguments_json"]) == run.arguments
            and json.loads(row["input_references_json"])
            == {
                name: reference.as_storage_dict()
                for name, reference in run.input_references.items()
            }
        )
    except (AttributeError, TypeError, json.JSONDecodeError):
        return False


def require_turn_capacity_connection(
    connection, project_id: str, subject: str, session_id: str, max_pending: int
) -> None:
    """Bound project-wide active and queued Agent turns."""

    del subject, session_id
    pending = connection.execute(
        """SELECT
               (SELECT COUNT(*) FROM sessions WHERE project_id=?
                  AND pending_agent_run_id IS NOT NULL)
             + (SELECT COUNT(*) FROM message_requests r JOIN sessions s USING (session_id)
                 WHERE s.project_id=? AND r.requested_mode='AUTO' AND r.routing_source='MODEL'
                   AND r.routing_status IN ('ROUTING_REQUESTED','ROUTING_RUNNING'))""",
        (project_id, project_id),
    ).fetchone()[0]
    if pending >= max_pending:
        raise CapacityLimitError("PROJECT_AGENT_QUEUE_FULL")


def require_execution_capacity_connection(
    connection, project_id: str, subject: str, confirmation_id: str
) -> None:
    """Workflow concurrency is enforced by Executor slots and its durable queue."""


def update_session_metadata(
    database: Path,
    project_id: str,
    session_id: str,
    subject: str,
    *,
    title: str | None,
    archived: bool | None,
) -> dict[str, object]:
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM sessions WHERE session_id=? AND project_id=? "
            "AND owner_subject=?",
            (session_id, project_id, subject),
        ).fetchone()
        if row is None:
            raise ResourceNotFoundError("SESSION_NOT_FOUND")
        next_title = row["title"] if title is None else title
        next_archived = row["archived_at"]
        if archived is not None:
            next_archived = utc_now() if archived else None
        connection.execute(
            "UPDATE sessions SET title=?,archived_at=? WHERE session_id=?",
            (next_title, next_archived, session_id),
        )
        append_event(connection, session_id, "session.metadata_updated", {
            "actor_subject": subject,
            "title_changed": title is not None,
            "archived": next_archived is not None,
        })
        return {
            "session_id": session_id,
            "title": next_title,
            "archived": next_archived is not None,
        }
