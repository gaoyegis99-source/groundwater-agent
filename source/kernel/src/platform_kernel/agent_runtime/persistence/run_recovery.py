from __future__ import annotations

import json
import logging
from pathlib import Path

from ...application.ports import AgentProjectionPort
from ...database import append_event, connect_database, duration_ms, utc_now
from ..models import AgentFinalV5, AgentRun, ReplyPresentationV1, ReplySectionV1
from .projection import project_run, stable_error
from .timeline import close_activities_connection
from ...application.task_api import (
    refresh_task,
)

from .run_lifecycle import fail_run
from .run_queries import _project_current


_LOGGER = logging.getLogger(__name__)


def finalize_recovered_run(
    database: Path, agent_run_id: str, agent_projection: AgentProjectionPort,
) -> AgentRun | None:
    """Project a terminal recovery attempt without replaying the Provider.

    A normal execution keeps its SDK checkpoint and resumes through
    ``resume_execution``. A recovery confirmation is created after that original
    continuation has already reached a terminal AgentRun and its checkpoint has
    been removed. The immutable WorkflowRun attempts remain authoritative, so the
    recovery terminal response is rendered from those facts in one transaction.
    """
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        agent = connection.execute(
            "SELECT * FROM agent_runs WHERE agent_run_id=?", (agent_run_id,),
        ).fetchone()
        if agent is None:
            return None
        confirmation = connection.execute(
            """SELECT * FROM confirmations
               WHERE agent_run_id=? AND kind='RECOVERY' AND status='CONFIRMED'
               ORDER BY revision DESC,rowid DESC LIMIT 1""",
            (agent_run_id,),
        ).fetchone()
        if confirmation is None:
            return None
        workflow_rows = connection.execute(
            """SELECT * FROM workflow_runs WHERE confirmation_id=?
               ORDER BY position,attempt,run_id""",
            (confirmation["confirmation_id"],),
        ).fetchall()
        if not workflow_rows or any(
            row["status"] in {"QUEUED", "RUNNING"} for row in workflow_rows
        ):
            return None
        workflow_ids = [row["run_id"] for row in workflow_rows]
        current_output = (
            None
            if agent["output_json"] is None
            else AgentFinalV5.model_validate_json(agent["output_json"])
        )
        succeeded = all(row["status"] == "SUCCEEDED" for row in workflow_rows)
        status = "COMPLETE" if succeeded else "FAILED"
        if (
            agent["status"] == status
            and current_output is not None
            and current_output.workflow_runs == workflow_ids
        ):
            return project_run(connection, agent)
        outputs = [
            output
            for row in workflow_rows
            for output in json.loads(row["outputs_json"] or "[]")
        ]
        if succeeded:
            details = [
                ReplySectionV1(
                    markdown="首次执行未完成；已使用相同确认内容完成恢复执行。",
                    source_indexes=[],
                ),
                ReplySectionV1(
                    markdown=(
                        f"恢复批次已发布 {len(outputs)} 项可核验成果，可在任务工作台的成果页查看或下载。"
                    ),
                    source_indexes=[],
                ),
                ReplySectionV1(
                    markdown="执行沿用已冻结的数据版本和参数；早先失败记录继续保留在执行历史中。",
                    source_indexes=[],
                ),
            ]
            summary = f"恢复后已完成 {len(workflow_rows)} 项分析；生成 {len(outputs)} 份结果。"
            error = None
        else:
            codes = list(dict.fromkeys(
                stable_error(row["error"]) or "WORKFLOW_FAILED"
                for row in workflow_rows if row["status"] != "SUCCEEDED"
            ))
            details = [
                ReplySectionV1(
                    markdown="恢复批次未能完成，已保留此前执行记录和冻结输入。",
                    source_indexes=[],
                ),
                ReplySectionV1(
                    markdown="失败代码：" + "、".join(codes),
                    source_indexes=[],
                ),
            ]
            summary = f"恢复执行失败 {len(codes)} 项；未发布失败批次的成果。"
            error = codes[0]
        presentation = ReplyPresentationV1(
            conclusion=None,
            details=details,
            execution_summary=ReplySectionV1(
                markdown=summary, source_indexes=[],
            ),
            unresolved_items=[],
            sources=[],
        )
        message = "\n\n".join([
            *(section.markdown for section in details),
            summary,
        ])
        output = AgentFinalV5(
            message=message,
            presentation=presentation,
            workflow_runs=workflow_ids,
            unresolved_items=[],
            interaction_options=[],
        )
        finished_at = utc_now()
        steps = [*json.loads(agent["steps_json"]), status]
        changed = connection.execute(
            """UPDATE agent_runs SET status=?,steps_json=?,output_json=?,error=?,
                      finished_at=?,duration_ms=?
               WHERE agent_run_id=?""",
            (
                status,
                json.dumps(steps),
                output.model_dump_json(),
                error,
                finished_at,
                duration_ms(agent["started_at"], finished_at),
                agent_run_id,
            ),
        ).rowcount
        if changed != 1:
            raise ValueError("AGENT_RECOVERY_FINALIZATION_INVALID")
        agent_projection.upsert_assistant_message(
            connection, agent["session_id"], agent["goal_revision"], message,
        )
        request_count, observation_call_count = agent_projection.activity_counts(
            connection, agent["session_id"], agent_run_id,
        )
        event_payload = {
            "agent_run_id": agent_run_id,
            "confirmation_id": confirmation["confirmation_id"],
            "workflow_run_ids": workflow_ids,
            "recovered": True,
            "error": error,
            "agent_metrics": {
                "request_count": request_count,
                "observation_call_count": observation_call_count,
                "input_tokens": agent["input_tokens"],
                "output_tokens": agent["output_tokens"],
                "total_tokens": agent["total_tokens"],
                "duration_ms": duration_ms(agent["started_at"], finished_at),
            },
        }
        append_event(
            connection,
            agent["session_id"],
            "agent.complete" if succeeded else "agent.failed",
            event_payload,
        )
        refresh_task(connection, agent["session_id"])
        row = connection.execute(
            "SELECT * FROM agent_runs WHERE agent_run_id=?", (agent_run_id,),
        ).fetchone()
        return project_run(connection, row)


def fail_invalid_state(
    database: Path, run: AgentRun, agent_projection: AgentProjectionPort
) -> None:
    finished_at = utc_now()
    with connect_database(database) as connection:
        connection.execute(
            """UPDATE agent_actions SET status = 'FAILED', error = 'AGENT_RUN_STATE_INVALID'
               WHERE agent_run_id = ? AND status = 'PENDING'""",
            (run.agent_run_id,),
        )
        transitioned = connection.execute(
            """UPDATE agent_runs SET status = 'FAILED', steps_json = ?,
                   error = 'AGENT_RUN_STATE_INVALID', finished_at = ?, duration_ms = ?
               WHERE agent_run_id = ? AND status = ?""",
            (
                json.dumps([*run.steps, "FAILED"]),
                finished_at,
                duration_ms(run.started_at, finished_at),
                run.agent_run_id,
                run.status,
            ),
        )
        if transitioned.rowcount != 1:
            raise ValueError("AGENT_RUN_TRANSITION_INVALID")
        connection.execute(
            "DELETE FROM agent_run_state WHERE agent_run_id = ?",
            (run.agent_run_id,),
        )
        close_activities_connection(connection, run, "FAILED", "AGENT_RUN_STATE_INVALID")
        append_event(
            connection,
            run.session_id,
            "agent.failed",
            {"agent_run_id": run.agent_run_id, "error": "AGENT_RUN_STATE_INVALID"},
        )
        _project_current(connection, run.agent_run_id, agent_projection)


def recover_running_run(
    database: Path, run: AgentRun, agent_projection: AgentProjectionPort
) -> str:
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        state = connection.execute(
            "SELECT state_json FROM agent_run_state WHERE agent_run_id = ?",
            (run.agent_run_id,),
        ).fetchone()
        if state is not None:
            if (
                len(run.steps) < 2
                or run.steps[-1] != "RUNNING"
                or run.steps[-2] not in {"WAITING_CONFIRMATION", "WAITING_EXECUTION"}
            ):
                raise ValueError("AGENT_RUN_RECOVERY_PHASE_INVALID")
            target = run.steps[-2]
            transitioned = connection.execute(
                """UPDATE agent_runs SET status = ?, steps_json = ?
                   WHERE agent_run_id = ? AND status = 'RUNNING'""",
                (target, json.dumps(run.steps[:-1]), run.agent_run_id),
            )
            if transitioned.rowcount != 1:
                raise ValueError("AGENT_RUN_TRANSITION_INVALID")
            close_activities_connection(connection, run, "INTERRUPTED", "PROCESS_INTERRUPTED")
            append_event(connection, run.session_id, f"agent.{target.lower()}", {
                "agent_run_id": run.agent_run_id, "goal_revision": run.goal_revision,
            })
            return target
    fail_run(database, run, "PROCESS_INTERRUPTED", agent_projection)
    return "FAILED"
