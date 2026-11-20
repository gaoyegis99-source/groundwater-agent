"""Project the WorkItem facts suitable for a single Agent model request."""

from ..access import RequestIdentity
from ..application.work_item_context import WorkItemContextBlock, WorkItemContextRequest
from ..application.work_item_lifecycle_facts import SessionFacts


def _current_session(facts: SessionFacts, track: dict, goal_revision: int) -> dict[str, object]:
    value: dict[str, object] = {
        "sessionId": facts.session_id,
        "state": track["state"],
        "reason": track["reason"],
        "stopPending": facts.stop_pending,
    }
    if facts.task is not None:
        spec, progress = facts.task["spec"], facts.task["progress"]
        value["task"] = {
            "id": spec["task_id"], "revision": spec["revision"],
            "lifecycle": progress["lifecycle"], "state": progress["state"],
        }
    preparation = facts.preparation
    if preparation is not None:
        blocked = [entry for entry in preparation["entries"] if entry["status"] == "BLOCKED"]
        value["preparation"] = {
            "status": preparation["status"], "revision": preparation["revision"],
            "goalRevision": preparation["goal_revision"],
            "freshForCurrentTurn": preparation["goal_revision"] == goal_revision,
            "blockers": [
                {key: issue[key] for key in ("kind", "code", "input", "parameter") if issue.get(key)}
                for entry in blocked for issue in entry.get("blocking_issues", ())
            ][:3],
            "blockingReasons": [
                reason[:100] for entry in blocked for reason in entry.get("blocking_reasons", ())
            ][:3],
        }
    if facts.confirmation_id is not None:
        value["pendingConfirmation"] = {
            "id": facts.confirmation_id, "decisionState": facts.confirmation_decision_state,
        }
    publications = {entry["run_id"]: entry["state"] for entry in track["publications"]}
    if facts.workflows:
        value["runs"] = [
            {"id": run.run_id, "execution": run.status, "publication": publications.get(run.run_id)}
            for run in facts.workflows[:4]
        ]
    return value


def context_blocks(
    request: WorkItemContextRequest, actor: RequestIdentity, context: dict, lifecycle: dict,
    facts: SessionFacts, history: tuple[dict, ...], selection: dict | None,
) -> tuple[WorkItemContextBlock, ...]:
    """Keep cross-Session summaries source-labelled and free of messages and identifiers of resources."""
    item = context["work_item"]
    item_id, revision = item["work_item_id"], item["revision"]
    tracks = {entry["session_id"]: entry for entry in lifecycle["tracks"]}
    blocks = [
        WorkItemContextBlock("L0", "AGENT_REQUEST", request.agent_run_id, request.goal_revision, {
            "projectId": request.project_id, "sessionId": request.session_id, "workItemId": item_id,
            "subject": actor.subject, "roles": sorted(actor.roles), "requestId": request.request_id,
            "goalRevision": request.goal_revision, "intent": request.intent,
            "taskAction": request.task_action, "permissions": request.permissions,
        }),
        WorkItemContextBlock("L1", "WORK_ITEM", item_id, revision, {
            "title": item["title"][:120], "status": item["status"],
            "state": lifecycle["state"], "reason": lifecycle["reason"],
            "eventCursor": lifecycle["event_cursor"],
        }),
        WorkItemContextBlock(
            "L1", "CURRENT_SESSION", request.session_id, request.goal_revision,
            _current_session(facts, tracks[request.session_id], request.goal_revision),
        ),
    ]
    if item["description"]:
        blocks.append(WorkItemContextBlock("L1", "WORK_ITEM_DESCRIPTION", item_id, revision, {
            "description": item["description"][:360],
            "truncated": len(item["description"]) > 360,
        }))
    if selection is not None:
        blocks.append(WorkItemContextBlock(
            "L2", "CAPABILITY_SELECTION", request.agent_run_id, request.goal_revision, selection,
        ))
    blocks.extend(
        WorkItemContextBlock("L2", "WORK_ITEM_REFERENCE", entry["link_id"], revision, entry["reference"])
        for entry in (*context["objects"], *context["assets"])
    )
    blocks.extend(
        WorkItemContextBlock("L3", "LINKED_SESSION", row["session_id"], row["goal_revision"], {
            "sessionId": row["session_id"], "observedAt": row["observed_at"], "category": "FACT",
            "sessionGoalRevision": row["goal_revision"], "businessGoalRevision": row["business_goal_revision"],
            "lastActivityAt": row["last_activity_at"], "workItemEventCursor": lifecycle["event_cursor"],
            "state": tracks[row["session_id"]]["state"], "reason": tracks[row["session_id"]]["reason"],
        })
        for row in history if row["session_id"] in tracks
    )
    return tuple(blocks)
