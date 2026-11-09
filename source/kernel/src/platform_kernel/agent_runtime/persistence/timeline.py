from __future__ import annotations

import json
from pathlib import Path
import re

from ...database import append_event, connect_database, read_session_events
from ..models import AgentFinalV5, AgentPlanStepV2, AgentRun
from .projection import stable_error


_PUBLIC_DIAGNOSTIC_REASONS = frozenset({
    "CLARIFICATION_CONTEXT_INVALID", "CLARIFICATION_REPLY_INVALID", "EMPTY_REPLY",
    "EXECUTION_STATUS_MISSING", "EXECUTION_STATUS_NOT_TERMINAL",
    "OBSERVATION_ARGUMENTS_INVALID", "OBSERVATION_ARGUMENTS_JSON_INVALID",
    "OPERATOR_PAGE_LIMIT_INVALID",
    "REPLY_CONCLUSION_REQUIRED", "REPLY_EXECUTION_STATUS_INVALID",
    "REPLY_CITATION_INVALID", "REPLY_CITATION_REQUIRED", "REPLY_SCHEMA_INVALID",
    "REPLY_SECTION_EMPTY", "REPLY_UNRESOLVED_ITEM_EMPTY",
    "UNRELATED_WORKFLOW_RUN", "UNRESOLVED_ITEM_FORMAT_INVALID",
})


def session_run_summaries(connection, session_id: str, project_id: str,
                          revisions: tuple[int, ...]) -> dict[int, dict]:
    """Read Agent-owned fields in the caller's Session-history snapshot."""
    placeholders = ",".join("?" for _ in revisions)
    rows = connection.execute(
        "SELECT goal_revision,agent_run_id,status,error,output_json,created_at,started_at,finished_at FROM agent_runs "
        f"WHERE session_id=? AND project_id=? AND goal_revision IN ({placeholders})",
        (session_id, project_id, *revisions),
    ).fetchall()
    activities = project_activities(connection, session_id, tuple(row["agent_run_id"] for row in rows))
    run_ids = tuple(row["agent_run_id"] for row in rows)
    terminal = {}
    if run_ids:
        terminal_events = sorted([
            *read_session_events(
                connection, session_id, "agent.complete", agent_run_ids=run_ids,
            ),
            *read_session_events(
                connection, session_id, "agent.failed", agent_run_ids=run_ids,
            ),
        ], key=lambda item: item["event_id"])
        for item in terminal_events:
            payload = item["data"]
            terminal[payload["agent_run_id"]] = payload
    outputs = {
        row["agent_run_id"]: (
            None
            if row["output_json"] is None
            else AgentFinalV5.model_validate_json(row["output_json"])
        )
        for row in rows
    }
    return {row["goal_revision"]: {
        "agent_run_id": row["agent_run_id"], "status": row["status"],
        "error_code": stable_error(row["error"]),
        "timing": {key: row[key] for key in ("created_at", "started_at", "finished_at")},
        "activities": activities[row["agent_run_id"]],
        "diagnostics": (
            _public_diagnostics(terminal.get(row["agent_run_id"]))
            or ({"reason": row["error"]}
                if row["error"] == "AGENT_INTENT_ROUTING_INVALID" else None)
        ),
        "agent_metrics": (
            None if row["agent_run_id"] not in terminal
            else terminal[row["agent_run_id"]].get("agent_metrics")
        ),
        "interaction_options": (
            [] if outputs[row["agent_run_id"]] is None
            else [item.model_dump() for item in
                  outputs[row["agent_run_id"]].interaction_options]
        ),
        "reply": (
            None if outputs[row["agent_run_id"]] is None
            else outputs[row["agent_run_id"]].presentation.model_dump()
        ),
        "plans": [{"revision": plan["revision"], "goal": plan["goal"],
                   "steps": json.loads(plan["steps_json"]), "reason": plan["reason"]}
                  for plan in connection.execute(
                      "SELECT * FROM agent_plans WHERE agent_run_id=? ORDER BY revision",
                      (row["agent_run_id"],),
                  )],
    } for row in rows}


def _public_diagnostics(terminal: dict | None) -> dict[str, object] | None:
    if terminal is None:
        return None
    diagnostics = terminal.get("diagnostics")
    if (
        isinstance(diagnostics, dict)
        and diagnostics.get("kind") == "WORKFLOW_VALIDATION"
        and isinstance(diagnostics.get("code"), str)
        and re.fullmatch(r"[A-Z][A-Z0-9_]{2,95}", diagnostics["code"])
        and isinstance(diagnostics.get("path"), str)
        and len(diagnostics["path"]) <= 512
        and (not diagnostics["path"] or diagnostics["path"].startswith("/"))
    ):
        return {
            "reason": "WORKFLOW_VALIDATION",
            "issues": [{
                "path": diagnostics["path"],
                "type": diagnostics["code"],
            }],
        }
    reason = diagnostics.get("reason") if isinstance(diagnostics, dict) else None
    if reason not in _PUBLIC_DIAGNOSTIC_REASONS:
        return None
    result: dict[str, object] = {"reason": reason}
    if reason == "REPLY_SCHEMA_INVALID":
        issues = diagnostics.get("issues")
        if isinstance(issues, list) and all(
            isinstance(issue, dict)
            and isinstance(issue.get("path"), str)
            and isinstance(issue.get("type"), str)
            for issue in issues
        ):
            result["issues"] = [
                {
                    "path": issue["path"],
                    "type": issue["type"],
                    **(
                        {"code": issue["code"]}
                        if isinstance(issue.get("code"), str)
                        and issue["code"] in {
                            "WORKFLOW_DRAFT_DATA_BINDINGS_INVALID",
                            "WORKFLOW_DRAFT_PARAMETER_SOURCES_INVALID",
                        }
                        else {}
                    ),
                }
                for issue in issues
            ]
    elif reason == "OBSERVATION_ARGUMENTS_INVALID":
        issues = diagnostics.get("issues")
        if isinstance(issues, list) and all(
            isinstance(issue, dict)
            and isinstance(issue.get("path"), str)
            and isinstance(issue.get("validator"), str)
            for issue in issues
        ):
            result["issues"] = [
                {"path": issue["path"], "type": issue["validator"]}
                for issue in issues
            ]
    return result


def save_activity(database: Path, session_id: str, payload: dict) -> None:
    with connect_database(database) as connection:
        stopped = connection.execute("SELECT 1 FROM agent_runs WHERE agent_run_id=? AND status='CANCELLED'", (payload['agent_run_id'],)).fetchone()
        if stopped:
            return
        append_event(connection, session_id, "agent.activity", payload)


def load_initial_activity(database: Path, session_id: str, agent_run_id: str) -> dict | None:
    with connect_database(database) as connection:
        events = read_session_events(
            connection, session_id, "agent.activity", agent_run_ids=(agent_run_id,),
        )
    return next((event["data"] for event in events
                 if event["data"]["kind"] == "SEGMENT" and event["data"]["purpose"] == "INITIAL"), None)


def project_activities(connection, session_id: str, agent_run_ids: tuple[str, ...]) -> dict[str, list[dict]]:
    """Fold requested activity events once within the caller snapshot."""
    events = read_session_events(
        connection, session_id, "agent.activity", agent_run_ids=agent_run_ids,
    )
    by_run = {run_id: {} for run_id in agent_run_ids}
    for event in events:
        activity = event["data"]
        activities = by_run[activity["agent_run_id"]]
        previous = activities.get(activity["activity_id"])
        started = None if previous is None else previous["started_at"]
        activities[activity["activity_id"]] = {
            **activity,
            "created_at": event["created_at"] if previous is None else previous["created_at"],
            "started_at": event["created_at"] if started is None and activity["status"] == "RUNNING" else started,
            "finished_at": event["created_at"] if activity["status"] in {"COMPLETE", "FAILED", "INTERRUPTED"} else None,
        }
    return {run_id: list(activities.values()) for run_id, activities in by_run.items()}


def close_activities_connection(connection, run: AgentRun, status: str, error: str | None = None,
                                failed_activity_id: str | None = None) -> None:
    activities = project_activities(connection, run.session_id, (run.agent_run_id,))
    for activity in activities[run.agent_run_id]:
        if activity["status"] not in {"QUEUED", "RUNNING"}:
            continue
        activity_status = status if activity["kind"] == "SEGMENT" else (
            "FAILED" if status == "FAILED" and activity["activity_id"] == failed_activity_id else "INTERRUPTED"
        )
        append_event(connection, run.session_id, "agent.activity", {
            **{key: value for key, value in activity.items()
               if key not in {"created_at", "started_at", "finished_at"}},
            "status": activity_status,
            "error_code": stable_error(error),
        })


def save_plan(
    database: Path,
    agent_run_id: str,
    goal: str,
    steps: list[AgentPlanStepV2],
    reason: str,
) -> int:
    with connect_database(database) as connection:
        current = connection.execute(
            """SELECT COALESCE(MAX(revision), 0) AS revision
               FROM agent_plans WHERE agent_run_id = ?""",
            (agent_run_id,),
        ).fetchone()["revision"]
        revision = current + 1
        connection.execute(
            "INSERT INTO agent_plans VALUES (?, ?, ?, ?, ?)",
            (
                agent_run_id,
                revision,
                goal,
                json.dumps(
                    [step.model_dump() for step in steps],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                reason,
            ),
        )
        run = connection.execute(
            "SELECT session_id,goal_revision FROM agent_runs WHERE agent_run_id=?", (agent_run_id,),
        ).fetchone()
        append_event(connection, run["session_id"], "agent.plan_recorded", {
            "agent_run_id": agent_run_id, "goal_revision": run["goal_revision"],
            "plan_revision": revision,
        })
        return revision
