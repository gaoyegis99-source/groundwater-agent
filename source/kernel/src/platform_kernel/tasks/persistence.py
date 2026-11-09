from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import uuid4

from ..database import append_event, connect_database, utc_now
from ..errors import AgentRunError, ResourceNotFoundError
from .models import TaskContextV1, TaskSpecV1, TaskUpdate
from .preparation import save_preparation_connection
from .progress import derive_task_progress


def _spec(connection, session_id):
    row = connection.execute(
        "SELECT r.* FROM tasks t JOIN task_revisions r ON r.task_id=t.task_id "
        "AND r.revision=t.revision WHERE t.session_id=?", (session_id,),
    ).fetchone()
    return None if row is None else (json.loads(row["spec_json"]), json.loads(row["evidence_json"]))


def _user_source(connection, run):
    row = connection.execute(
        """SELECT message_id,content FROM messages
           WHERE session_id=? AND goal_revision=? AND role='user'""",
        (run["session_id"], run["goal_revision"]),
    ).fetchone()
    if row is None:
        raise ValueError("TASK_SOURCE_MESSAGE_MISSING")
    return row


def _quote(quote, message):
    if not quote or not quote.strip() or quote not in message:
        raise ValueError("TASK_USER_SOURCE_INVALID")


def _review_entries(message, phrases):
    """Match whole review entries, including multiline quotes, never text prefixes."""
    remaining = message.strip()
    matched = set()
    ordered = sorted({phrase.strip() for phrase in phrases}, key=len, reverse=True)
    while remaining:
        phrase = next(
            (
                value
                for value in ordered
                if remaining == value or remaining.startswith(value + "\n")
            ),
            None,
        )
        if phrase is None:
            return set()
        matched.add(phrase)
        remaining = remaining[len(phrase):].strip()
    return matched


def _apply_requirements(
    spec,
    evidence,
    update,
    *,
    message,
    source_message_id,
    source_goal_revision,
):
    for field in ("constraints", "criteria"):
        for item in getattr(update, field):
            _quote(item.source_quote, message)
            if item.id.startswith("constraint:"):
                raise ValueError("TASK_REQUIREMENT_ID_RESERVED")
            existing = {value["id"]: value for value in spec[field]}
            if item.id in existing:
                raise ValueError("TASK_REQUIREMENT_ID_EXISTS")
            if item.replaces:
                old = existing.get(item.replaces)
                if old is None or message.strip() not in {
                    f'将{old["text"]}改为{item.source_quote}',
                    f'把{old["text"]}改为{item.source_quote}',
                }:
                    raise ValueError("TASK_REPLACEMENT_NOT_AUTHORIZED")
                spec[field] = [
                    value for value in spec[field] if value["id"] != item.replaces
                ]
                evidence.pop(item.replaces, None)
                if field == "criteria":
                    for step in spec["steps"]:
                        step["criterion_ids"] = [
                            item.id if value == item.replaces else value
                            for value in step["criterion_ids"]
                        ]
                if field == "constraints":
                    old_review_id = "constraint:" + item.replaces
                    spec["criteria"] = [
                        value
                        for value in spec["criteria"]
                        if value["id"] != old_review_id
                    ]
                    evidence.pop(old_review_id, None)
                    for step in spec["steps"]:
                        step["criterion_ids"] = [
                            "constraint:" + item.id
                            if value == old_review_id
                            else value
                            for value in step["criterion_ids"]
                        ]
            value = item.model_dump(exclude={"replaces"})
            # Source text carries user authority; model wording is interpretation.
            value["text"] = item.source_quote
            value["interpretation"] = (
                item.text if item.text != item.source_quote else None
            )
            value["source_message_id"] = source_message_id
            value["source_goal_revision"] = source_goal_revision
            spec[field].append(value)
            if field == "constraints":
                # Free text has no mechanical predicate, so it needs user review.
                spec["criteria"].append(
                    {
                        **value,
                        "id": "constraint:" + item.id,
                        "kind": "USER_REVIEW",
                        "model_id": None,
                        "output_names": [],
                    }
                )


def _validate_steps(spec):
    criteria = {item["id"]: item for item in spec["criteria"]}
    ids = [item["id"] for item in spec["steps"]]
    if len(ids) != len(set(ids)):
        raise ValueError("TASK_STEP_DUPLICATE")
    visited = set()
    for step in spec["steps"]:
        if (
            not set(step["depends_on"]) <= visited
            or not set(step["criterion_ids"]) <= criteria.keys()
        ):
            raise ValueError("TASK_STEP_DEPENDENCY_INVALID")
        visited.add(step["id"])
    return criteria


def _apply_evidence(connection, run, update, criteria, evidence, message):
    for item in update.evidence:
        criterion = criteria.get(item.criterion_id)
        if criterion is None:
            raise ValueError("TASK_CRITERION_UNKNOWN")
        target = item.agent_run_id or run["agent_run_id"]
        owner = connection.execute(
            "SELECT session_id,goal_revision FROM agent_runs WHERE agent_run_id=?",
            (target,),
        ).fetchone()
        if (
            owner is None
            or owner["session_id"] != run["session_id"]
            or owner["goal_revision"] < criterion["source_goal_revision"]
        ):
            raise ValueError("TASK_EVIDENCE_SCOPE_INVALID")
        if criterion["kind"] == "USER_REVIEW":
            _quote(item.review_quote, message)
            review_lines = {
                f'确认完成：{value["text"]}'
                for value in criteria.values()
                if value["kind"] == "USER_REVIEW"
            }
            entries = _review_entries(message, review_lines)
            expected = f'确认完成：{criterion["text"]}'
            if item.review_quote != expected or item.review_quote.strip() not in entries:
                raise ValueError("TASK_REVIEW_NOT_EXPLICIT")
        elif item.review_quote is not None:
            raise ValueError("TASK_REVIEW_KIND_INVALID")
        if item.workflow_run_id:
            workflow_run = connection.execute(
                "SELECT agent_run_id FROM workflow_runs WHERE run_id=?",
                (item.workflow_run_id,),
            ).fetchone()
            if workflow_run is None or workflow_run["agent_run_id"] != target:
                raise ValueError("TASK_EVIDENCE_SCOPE_INVALID")
        evidence[item.criterion_id] = {
            **item.model_dump(),
            "agent_run_id": target,
            "review_goal_revision": run["goal_revision"],
        }


def _apply(connection, run, previous, update, task_id):
    source = _user_source(connection, run)
    message = source["content"]
    revision = 0 if previous is None else previous[0]["revision"]
    if update.expected_revision != revision:
        raise ValueError("TASK_REVISION_CONFLICT")
    if previous is None:
        _quote(update.objective_quote, message)
        if not update.criteria:
            raise ValueError("TASK_CRITERIA_REQUIRED")
        spec = TaskSpecV1(
            task_id=task_id,
            session_id=run["session_id"],
            revision=0,
            objective=update.objective_quote,
            source_goal_revision=run["goal_revision"],
            source_message_id=source["message_id"],
            constraints=[],
            criteria=[],
            suggestions=[],
            unresolved_items=[],
            steps=[],
        ).model_dump()
        evidence = {}
    else:
        spec, evidence = previous
        spec = json.loads(json.dumps(spec))
        evidence = json.loads(json.dumps(evidence))
        if update.objective_quote not in {None, spec["objective"]}:
            raise ValueError("TASK_OBJECTIVE_IMMUTABLE")
    _apply_requirements(
        spec,
        evidence,
        update,
        message=message,
        source_message_id=source["message_id"],
        source_goal_revision=run["goal_revision"],
    )
    for field in ("suggestions", "unresolved_items", "steps"):
        value = getattr(update, field)
        if value is not None:
            spec[field] = (
                [item.model_dump() for item in value] if field == "steps" else value
            )
    criteria = _validate_steps(spec)
    _apply_evidence(connection, run, update, criteria, evidence, message)
    spec["revision"] = revision + int(
        previous is None or spec != previous[0] or evidence != previous[1]
    )
    TaskSpecV1.model_validate(spec)
    return spec, evidence


def stage_update(database: Path, context: dict, call_id: str, update: TaskUpdate, models) -> dict:
    try:
        known = {model.model_id: model for model in models}
        for item in update.criteria:
            if item.kind == "RESULT" and (item.model_id not in known or not set(item.output_names) <= set(known[item.model_id].final_outputs)):
                raise ValueError("TASK_CAPABILITY_UNKNOWN")
        pending = context.setdefault("task_updates", [])
        payload = update.model_dump()
        for old in pending:
            if old["call_id"] == call_id:
                if old["update"] != payload:
                    raise ValueError("TASK_REQUEST_CONFLICT")
                return old["receipt"]
        with connect_database(database) as connection:
            run = connection.execute("SELECT * FROM agent_runs WHERE agent_run_id=?", (context["agent_run_id"],)).fetchone()
            previous = _spec(connection, run["session_id"])
            task_id = previous[0]["task_id"] if previous else (pending[0]["task_id"] if pending else str(uuid4()))
            for old in pending:
                previous = _apply(connection, run, previous, TaskUpdate.model_validate(old["update"]), task_id)
            spec, _ = _apply(connection, run, previous, update, task_id)
        receipt = {"status": "STAGED", "revision": spec["revision"],
                   "criteria": [{"id": item["id"], "text": item["text"], "kind": item["kind"]} for item in spec["criteria"]],
                   "constraint_reviews": [{"id": item["id"], "text": item["text"]} for item in spec["criteria"] if item["id"].startswith("constraint:")]}
        pending.append({"call_id": call_id, "task_id": task_id, "update": payload, "receipt": receipt})
        return receipt
    except ValueError as error:
        if re.fullmatch(r"TASK_[A-Z_]+", str(error)) is None:
            raise
        raise AgentRunError("AGENT_TASK_UPDATE_INVALID", diagnostics={"reason": str(error)}) from error


def commit_updates(connection, run, updates):
    for item in updates or []:
        encoded = json.dumps(item["update"], ensure_ascii=False, sort_keys=True)
        old = connection.execute("SELECT update_json FROM task_update_calls WHERE agent_run_id=? AND tool_call_id=?",
                                 (run.agent_run_id, item["call_id"])).fetchone()
        if old:
            if old["update_json"] != encoded:
                raise ValueError("TASK_REQUEST_CONFLICT")
            continue
        row = connection.execute("SELECT * FROM agent_runs WHERE agent_run_id=?", (run.agent_run_id,)).fetchone()
        previous = _spec(connection, run.session_id)
        spec, evidence = _apply(connection, row, previous, TaskUpdate.model_validate(item["update"]), item["task_id"])
        connection.execute("INSERT INTO task_update_calls VALUES (?,?,?)", (run.agent_run_id, item["call_id"], encoded))
        if previous is not None and spec["revision"] == previous[0]["revision"]:
            connection.execute(
                "INSERT OR IGNORE INTO task_runs VALUES (?,?,?,?,?)",
                (spec["task_id"], run.agent_run_id, spec["revision"], "TASK_CONTINUATION", utc_now()),
            )
            continue
        if previous is None:
            connection.execute("INSERT INTO tasks VALUES (?, ?, ?, 'ACTIVE', ?)",
                               (spec["task_id"], run.session_id, spec["revision"], utc_now()))
        else:
            connection.execute("UPDATE tasks SET revision=?,lifecycle='ACTIVE' WHERE task_id=?",
                               (spec["revision"], spec["task_id"]))
        connection.execute("INSERT INTO task_revisions VALUES (?,?,?,?,?,?,?,?)", (
            spec["task_id"], spec["revision"], run.agent_run_id, item["call_id"],
            json.dumps(spec, ensure_ascii=False), json.dumps(evidence, ensure_ascii=False), encoded, utc_now(),
        ))
        connection.execute(
            "INSERT OR IGNORE INTO task_runs VALUES (?,?,?,?,?)",
            (spec["task_id"], run.agent_run_id, spec["revision"], "TASK_UPDATE", utc_now()),
        )
        append_event(connection, run.session_id, "task.created" if previous is None else "task.revised",
                     {"task_id": spec["task_id"], "task_revision": spec["revision"]})
        preparation = connection.execute(
            "SELECT snapshot_json FROM task_preparations WHERE agent_run_id=?",
            (run.agent_run_id,),
        ).fetchone()
        if preparation is not None:
            snapshot = json.loads(preparation["snapshot_json"])
            save_preparation_connection(
                connection,
                agent_run_id=run.agent_run_id,
                tool_call_id=f"task-bind:{run.agent_run_id}:{spec['revision']}",
                snapshots=tuple(snapshot["entries"]),
            )



def refresh_task(connection, session_id):
    value = _spec(connection, session_id)
    if value is None:
        return
    spec, evidence = value
    progress = derive_task_progress(connection, spec, evidence)
    old = connection.execute("SELECT lifecycle FROM tasks WHERE session_id=?", (session_id,)).fetchone()["lifecycle"]
    if old != progress.lifecycle:
        connection.execute("UPDATE tasks SET lifecycle=? WHERE session_id=?", (progress.lifecycle, session_id))
        if progress.lifecycle == "COMPLETE":
            connection.execute("INSERT OR IGNORE INTO task_completions VALUES (?,?,?)", (spec["task_id"], spec["revision"], utc_now()))
        append_event(connection, session_id, "task.completed" if progress.lifecycle == "COMPLETE" else "task.reopened",
                     {"task_id": spec["task_id"], "task_revision": spec["revision"]})


def get_task(database, session_id, project_id):
    with connect_database(database) as connection:
        connection.execute("BEGIN")
        session = connection.execute("SELECT project_id FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if session is None or session["project_id"] != project_id:
            raise ResourceNotFoundError("SESSION_NOT_FOUND")
        value = _spec(connection, session_id)
        if value is None:
            return None
        spec, evidence = value
        return TaskContextV1(
            spec=TaskSpecV1.model_validate(spec),
            progress=derive_task_progress(connection, spec, evidence),
        ).model_dump()


def task_in_connection(connection, session_id):
    value = _spec(connection, session_id)
    if value is None:
        return None
    spec, evidence = value
    return TaskContextV1(
        spec=TaskSpecV1.model_validate(spec),
        progress=derive_task_progress(connection, spec, evidence),
    ).model_dump()


def context_for_run(database, run_id):
    with connect_database(database) as connection:
        row = connection.execute("SELECT a.session_id,a.project_id FROM agent_runs a JOIN tasks t ON t.session_id=a.session_id WHERE a.agent_run_id=?", (run_id,)).fetchone()
    return None if row is None else get_task(database, row["session_id"], row["project_id"])


def bind_confirmation(connection, confirmation_id, session_id):
    task = connection.execute("SELECT * FROM tasks WHERE session_id=?", (session_id,)).fetchone()
    if task:
        connection.execute("INSERT OR IGNORE INTO confirmation_tasks VALUES (?,?,?)",
                           (confirmation_id, task["task_id"], task["revision"]))


def require_current_confirmation(connection, confirmation_id):
    row = connection.execute("SELECT c.task_revision,t.revision FROM confirmation_tasks c JOIN tasks t ON t.task_id=c.task_id WHERE c.confirmation_id=?", (confirmation_id,)).fetchone()
    if row and row["task_revision"] != row["revision"]:
        from ..errors import ConfirmationConflictError
        raise ConfirmationConflictError("CONFIRMATION_TASK_CHANGED")
