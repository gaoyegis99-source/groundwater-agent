"""Derive task progress from durable task, Agent and execution facts."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

from .models import TaskProgressV1
from .preparation import preparation_in_connection


_BLOCKING_CONTINUATION_REASONS = {
    "BUDGET": "AGENT_BUDGET_EXCEEDED",
    "DUPLICATE": "AGENT_OBSERVATION_LOOP",
}


@dataclass(frozen=True)
class _RuntimeFacts:
    goal_revision: int
    pending_agent_run: bool
    latest: Any
    pending_confirmation: Any
    running_workflow: Any
    continuation_stop: Any
    rejected_confirmation: Any
    preparation: dict[str, object] | None
    blocked_execution_entries: list[dict[str, object]]


def derive_task_progress(connection, spec, evidence, *, routing_run_id: str | None = None):
    checks, outputs = _criterion_progress(connection, spec, evidence)
    facts = _runtime_facts(connection, spec, routing_run_id=routing_run_id)
    state = _task_state(spec, checks, facts)
    steps = _step_progress(spec, checks)
    if state == "COMPLETE" and any(not step["complete"] for step in steps):
        state = "WAITING_INPUT"
    metrics = _task_metrics(connection, spec)
    reasons = _blocking_reasons(facts)
    completed = connection.execute(
        "SELECT 1 FROM task_completions WHERE task_id=? AND revision=?",
        (spec["task_id"], spec["revision"]),
    ).fetchone()
    if completed:
        state = "COMPLETE"
        reasons = []
    return TaskProgressV1(
        lifecycle="COMPLETE" if state == "COMPLETE" else "ACTIVE",
        state=state,
        criteria=checks,
        steps=steps,
        results=list({resource["version_id"]: resource for resource in outputs}.values()),
        metrics=metrics,
        blocking_reasons=reasons,
    )


def _criterion_progress(connection, spec, evidence):
    checks, outputs = [], []
    for criterion in spec["criteria"]:
        proof = evidence.get(criterion["id"])
        satisfied = False
        if proof:
            run = connection.execute(
                "SELECT * FROM agent_runs WHERE agent_run_id=?",
                (proof["agent_run_id"],),
            ).fetchone()
            if criterion["kind"] == "USER_REVIEW":
                satisfied = bool(proof["review_quote"])
            elif criterion["kind"] == "REPLY":
                satisfied = run["status"] == "COMPLETE" and run["output_json"] is not None
            elif criterion["kind"] == "DRAFT":
                satisfied, proof = _draft_progress(connection, run, proof)
            elif criterion["kind"] == "RESULT":
                satisfied, resources = _result_progress(
                    connection, criterion, proof,
                )
                if satisfied:
                    outputs.extend(resources)
        checks.append(
            {
                "id": criterion["id"],
                "satisfied": satisfied,
                "evidence": proof if satisfied else None,
            }
        )
    return checks, outputs


def _draft_progress(connection, run, proof):
    draft = connection.execute(
        """SELECT draft_id,payload_json FROM workflow_drafts
           WHERE agent_run_id=?""",
        (proof["agent_run_id"],),
    ).fetchone()
    satisfied = (
        run["status"] == "COMPLETE"
        and draft is not None
        and draft["payload_json"] is not None
    )
    if satisfied:
        proof = {**proof, "workflow_draft_id": draft["draft_id"]}
    return satisfied, proof


def _result_progress(connection, criterion, proof):
    workflow_run = connection.execute(
        "SELECT * FROM workflow_runs WHERE run_id=?",
        (proof["workflow_run_id"],),
    ).fetchone()
    if (
        not workflow_run
        or workflow_run["status"] != "SUCCEEDED"
        or workflow_run["model_id"] != criterion["model_id"]
    ):
        return False, []
    resources = json.loads(workflow_run["outputs_json"])
    satisfied = set(criterion["output_names"]) <= {
        resource.get("output_name") for resource in resources
    }
    return satisfied, resources if satisfied else []


def _runtime_facts(connection, spec, *, routing_run_id: str | None = None) -> _RuntimeFacts:
    current = connection.execute(
        "SELECT * FROM sessions WHERE session_id=?", (spec["session_id"],)
    ).fetchone()
    goal_revision = current["goal_revision"]
    pending_agent_run = current["pending_agent_run_id"]
    routing_current_turn = (
        routing_run_id is not None
        and pending_agent_run == routing_run_id
        and current["agent_start_state"] == "ROUTING_RUNNING"
    )
    if routing_current_turn:
        # Reserving this turn advances the session revision and clears its
        # active run. Route the message using the task state it was answering.
        goal_revision -= 1
        latest = connection.execute(
            """SELECT agent_run_id,status,error FROM agent_runs
               WHERE session_id=? AND goal_revision=?
               ORDER BY rowid DESC LIMIT 1""",
            (spec["session_id"], goal_revision),
        ).fetchone()
    else:
        latest = connection.execute(
            "SELECT agent_run_id,status,error FROM agent_runs WHERE agent_run_id=?",
            (current["agent_run_id"],),
        ).fetchone()
    latest_run_id = latest["agent_run_id"] if latest is not None else None
    pending = connection.execute(
        "SELECT 1 FROM confirmations WHERE session_id=? AND status='PENDING'",
        (spec["session_id"],),
    ).fetchone()
    running = connection.execute(
        """SELECT 1 FROM workflow_runs WHERE session_id=?
           AND status IN ('QUEUED','RUNNING')""",
        (spec["session_id"],),
    ).fetchone()
    continuation_stop = connection.execute(
        """SELECT json_extract(payload_json,'$.continuation_stop') reason
           FROM session_events WHERE session_id=? AND event_type='agent.complete'
           AND json_extract(payload_json,'$.agent_run_id')=?
           ORDER BY event_id DESC LIMIT 1""",
        (spec["session_id"], latest_run_id),
    ).fetchone()
    rejected = connection.execute(
        """SELECT 1 FROM confirmations
           WHERE agent_run_id=? AND status='REJECTED'""",
        (latest_run_id,),
    ).fetchone()
    preparation = preparation_in_connection(
        connection, spec["session_id"], current_goal_revision=goal_revision,
    )
    if preparation is not None and preparation["task_id"] != spec["task_id"]:
        preparation = None
    blocked_entries = (
        []
        if preparation is None
        else [
            entry
            for entry in preparation["entries"]
            if entry["status"] == "BLOCKED"
            and entry["target_kind"] == "EXECUTABLE_MODEL"
        ]
    )
    return _RuntimeFacts(
        goal_revision=goal_revision,
        pending_agent_run=pending_agent_run is not None and not routing_current_turn,
        latest=latest,
        pending_confirmation=pending,
        running_workflow=running,
        continuation_stop=continuation_stop,
        rejected_confirmation=rejected,
        preparation=preparation,
        blocked_execution_entries=blocked_entries,
    )


def _task_state(spec, checks, facts: _RuntimeFacts) -> str:
    latest = facts.latest
    if facts.pending_agent_run or (
        latest and latest["status"] == "RUNNING"
    ):
        return "ANALYZING"
    if facts.running_workflow or (
        latest and latest["status"] == "WAITING_EXECUTION"
    ):
        return "EXECUTING"
    if facts.pending_confirmation:
        return "WAITING_CONFIRMATION"
    if latest and latest["status"] in {"FAILED", "CANCELLED"}:
        return "BLOCKED"
    if facts.rejected_confirmation or _continuation_blocking_reason(facts):
        return "BLOCKED"
    if _current_blocked_preparation(facts):
        return "WAITING_INPUT"
    if spec["unresolved_items"]:
        return "WAITING_INPUT"
    if any(
        criterion["kind"] == "USER_REVIEW" and not check["satisfied"]
        for criterion, check in zip(spec["criteria"], checks)
    ):
        return "WAITING_REVIEW"
    if all(check["satisfied"] for check in checks):
        return "COMPLETE"
    return "ACTIVE"


def _current_blocked_preparation(facts: _RuntimeFacts) -> bool:
    return bool(
        facts.blocked_execution_entries
        and facts.preparation["goal_revision"] == facts.goal_revision
    )


def _continuation_blocking_reason(facts: _RuntimeFacts) -> str | None:
    reason = facts.continuation_stop["reason"] if facts.continuation_stop else None
    return _BLOCKING_CONTINUATION_REASONS.get(reason)


def _step_progress(spec, checks):
    done = {check["id"] for check in checks if check["satisfied"]}
    steps, completed_steps = [], set()
    for step in spec["steps"]:
        complete = (
            set(step["criterion_ids"]) <= done
            and set(step["depends_on"]) <= completed_steps
            and not step["gaps"]
        )
        steps.append({"id": step["id"], "complete": complete})
        if complete:
            completed_steps.add(step["id"])
    return steps


def _task_metrics(connection, spec):
    metrics = dict(
        connection.execute(
            """SELECT COALESCE(SUM(input_tokens),0) input_tokens,
               COALESCE(SUM(output_tokens),0) output_tokens,
               COALESCE(SUM(total_tokens),0) total_tokens,
               COALESCE(SUM(duration_ms),0) duration_ms
               FROM agent_runs WHERE agent_run_id IN
               (SELECT agent_run_id FROM task_runs WHERE task_id=?)""",
            (spec["task_id"],),
        ).fetchone()
    )
    metrics["scope"] = "EXPLICITLY_LINKED_RUNS"
    activity_counts = connection.execute(
        """SELECT json_extract(e.payload_json,'$.kind') kind,
           COUNT(DISTINCT json_extract(e.payload_json,'$.agent_run_id') || ':' ||
           json_extract(e.payload_json,'$.activity_id')) n
           FROM session_events e JOIN agent_runs a
           ON a.agent_run_id=json_extract(e.payload_json,'$.agent_run_id')
           WHERE e.session_id=? AND e.event_type='agent.activity'
           AND a.agent_run_id IN
           (SELECT agent_run_id FROM task_runs WHERE task_id=?) GROUP BY kind""",
        (spec["session_id"], spec["task_id"]),
    ).fetchall()
    counts = {row["kind"]: row["n"] for row in activity_counts}
    metrics.update(
        request_count=counts.get("MODEL_REQUEST", 0),
        observation_call_count=counts.get("TOOL", 0),
    )
    return metrics


def _blocking_reasons(facts: _RuntimeFacts) -> list[str]:
    reasons = ["CALCULATION_REJECTED"] if facts.rejected_confirmation else []
    if _current_blocked_preparation(facts):
        reasons.extend(
            reason
            for entry in facts.blocked_execution_entries
            for reason in entry.get("blocking_reasons", [])
        )
    continuation_reason = _continuation_blocking_reason(facts)
    if continuation_reason:
        reasons.append(continuation_reason)
    if facts.latest and facts.latest["error"]:
        error = facts.latest["error"]
        reasons.append(
            error
            if re.fullmatch(r"[A-Z][A-Z0-9_]*", error)
            else "AGENT_FAILED"
        )
    return reasons
