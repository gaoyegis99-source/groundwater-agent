"""Project task directory, ordered by the latest recorded turn event."""

from pathlib import Path

from ..database import connect_database


def list_task_history(database: Path, project_id: str, subject: str, cursor: int | None, limit: int) -> dict:
    # Resolve events by their durable owner. Recovery revisions and dependent
    # runs share the original AgentRun, so each task occupies one directory row.
    with connect_database(database) as db:
        db.execute("BEGIN")
        rows = db.execute(
            """WITH turn_events AS (
                 SELECT e.event_id,e.created_at,a.agent_run_id,a.session_id,a.goal_revision,
                        a.status AS agent_status,e.event_type
                 FROM sessions s JOIN session_events e ON e.session_id=s.session_id
                 LEFT JOIN confirmations c ON c.confirmation_id=json_extract(e.payload_json,'$.confirmation_id')
                 LEFT JOIN workflow_runs w ON w.run_id=json_extract(e.payload_json,'$.workflow_run_id')
                 LEFT JOIN task_preparations p ON p.preparation_id=json_extract(e.payload_json,'$.preparation_id')
                 JOIN agent_runs a ON a.session_id=s.session_id AND a.agent_run_id=coalesce(
                   json_extract(e.payload_json,'$.agent_run_id'),c.agent_run_id,w.agent_run_id,p.agent_run_id)
                 WHERE s.project_id=? AND s.owner_subject=?
               ), latest AS (
                 SELECT session_id,goal_revision,agent_run_id,agent_status,
                        max(event_id) AS cursor,created_at AS updated_at,
                        count(*) FILTER (WHERE event_type='workflow.output_step_blocked') AS blocked_outputs
                 FROM turn_events GROUP BY session_id,goal_revision
               )
               SELECT l.*, c.status AS confirmation_status, p.status AS preparation_status,
                      coalesce(ca.model_id,json_extract(p.snapshot_json,'$.entries[0].target_id')) AS model_id,
                      substr(m.content,1,100) AS title
               FROM latest l
               LEFT JOIN confirmations c ON c.rowid=(
                 SELECT c2.rowid FROM confirmations c2 WHERE c2.agent_run_id=l.agent_run_id
                 ORDER BY c2.revision DESC,c2.rowid DESC LIMIT 1)
               LEFT JOIN confirmation_actions ca ON ca.rowid=(
                 SELECT ca2.rowid FROM confirmations c2 JOIN confirmation_actions ca2 USING(confirmation_id)
                 WHERE c2.agent_run_id=l.agent_run_id ORDER BY c2.revision,ca2.position LIMIT 1)
               LEFT JOIN task_preparations p ON p.agent_run_id=l.agent_run_id
               LEFT JOIN message_requests r ON r.agent_run_id=l.agent_run_id
               LEFT JOIN messages m ON m.message_id=(SELECT min(message_id) FROM messages
                 WHERE session_id=l.session_id AND goal_revision=l.goal_revision AND role='user')
               WHERE (c.confirmation_id IS NOT NULL OR p.preparation_id IS NOT NULL
                      OR r.dialogue_policy='EXECUTION_PROPOSAL' OR r.resolved_mode='WORKFLOW_DRAFT')
                 AND (? IS NULL OR l.cursor<?)
               ORDER BY l.cursor DESC LIMIT ?""",
            (project_id, subject, cursor, cursor, limit + 1),
        ).fetchall()
        items = []
        for row in rows[:limit]:
            runs = db.execute(
                """SELECT w.status,json_array_length(w.outputs_json) AS output_count,
                          (SELECT json_extract(e.payload_json,'$.stage') FROM session_events e
                           WHERE e.session_id=w.session_id AND e.event_type='workflow.stage_changed'
                           AND json_extract(e.payload_json,'$.workflow_run_id')=w.run_id ORDER BY e.event_id DESC LIMIT 1) AS stage
                   FROM workflow_runs w
                   WHERE w.agent_run_id=? AND w.attempt=(SELECT max(other.attempt) FROM workflow_runs other
                       WHERE other.agent_run_id=w.agent_run_id AND other.tool_call_id=w.tool_call_id)
                   ORDER BY w.position""",
                (row["agent_run_id"],),
            ).fetchall()
            statuses = {run["status"] for run in runs}
            status = row["confirmation_status"] or row["preparation_status"] or row["agent_status"]
            if not runs and row["agent_status"] in {"FAILED", "CANCELLED", "SUPERSEDED"} and status != "PENDING":
                status = row["agent_status"]
            if row["confirmation_status"] != "PENDING" and runs:
                if "RUNNING" in statuses or "QUEUED" in statuses:
                    running = [run["stage"] for run in runs if run["status"] == "RUNNING"]
                    status = "QUEUED"
                    if running:
                        status = "PUBLISHING" if all(stage == "PUBLISHING" for stage in running) else "RUNNING"
                elif statuses == {"SUCCEEDED"}:
                    status = "PUBLISHED" if any(run["output_count"] for run in runs) else "SUCCEEDED"
                    if row["blocked_outputs"]:
                        status = "PARTIAL"
                elif "SUCCEEDED" in statuses:
                    status = "PARTIAL"
                else:
                    status = runs[0]["status"]
            items.append({key: row[key] for key in ("session_id", "goal_revision", "title", "model_id", "updated_at")} | {"status": status})
        return {"items": items, "next_cursor": rows[limit - 1]["cursor"] if len(rows) > limit else None}
