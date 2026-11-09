"""Project durable orchestration facts into concise user-facing progress."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


_TERMINAL = frozenset({"COMPLETE", "FAILED", "INTERRUPTED"})

_PROGRESS_TITLES = {
    "CASE_MATCHED": "匹配分析案例",
    "INPUTS_VERIFIED": "核验案例输入",
    "PLAN_READY": "准备执行方案",
    "WAITING_CONFIRMATION": "确认执行参数",
    "EXECUTION_QUEUED": "提交执行任务",
    "EXECUTION_RUNNING": "执行分析工作流",
    "RESULTS_PUBLISHING": "发布分析成果",
    "EXECUTION_SUCCEEDED": "完成分析任务",
    "EXECUTION_FAILED": "分析任务未完成",
    "RECOVERY_SUCCEEDED": "恢复后执行成功",
}


def _progress_presentation(
    code: str, status: str, facts: Mapping[str, object]
) -> tuple[str, str]:
    title = _PROGRESS_TITLES[code]
    if code == "CASE_MATCHED":
        return title, f"已匹配固定案例 {facts['case_id']}。"
    if code == "INPUTS_VERIFIED":
        return (
            title,
            f"已核验 {facts['verified_inputs']} 项同批输入，"
            "版本、对象和校验和均匹配。",
        )
    if code == "PLAN_READY":
        return title, f"执行方案已准备，共 {facts['step_count']} 个业务步骤。"
    if code == "WAITING_CONFIRMATION":
        return title, (
            "请核对数据和参数后确认执行。"
            if status == "RUNNING"
            else "执行参数已经确认。"
        )
    if code == "EXECUTION_QUEUED":
        return title, (
            "任务已提交，正在等待执行器接收。"
            if status == "RUNNING"
            else "执行器已接收任务。"
        )
    if code == "EXECUTION_RUNNING":
        descriptions = {
            "RUNNING": "正在运行已确认的分析步骤。",
            "COMPLETE": "分析工作流执行完成。",
        }
        return title, descriptions.get(status, "分析工作流执行中断。")
    if code == "RESULTS_PUBLISHING":
        return title, (
            "正在核验并原子发布全部命名成果。"
            if status == "RUNNING"
            else "全部命名成果已经发布。"
        )
    if code in {"EXECUTION_SUCCEEDED", "RECOVERY_SUCCEEDED"}:
        return title, f"执行成功，已发布 {facts['output_count']} 项成果。"
    terminal = facts.get("terminal_status")
    description = (
        "执行超时。"
        if terminal == "TIMED_OUT"
        else "执行已取消。"
        if terminal == "CANCELLED"
        else "执行失败。"
    )
    return title, description


def build_turn_progress(turn: Mapping[str, object]) -> list[dict[str, object]]:
    """Return stable milestones without exposing Provider or tool-call diagnostics.

    The projection deliberately reads authoritative Plan, Confirmation and
    WorkflowRun facts. It does not infer progress from model prose and it remains
    available for historical Turns that predate this projection.
    """
    plans = _mappings(turn.get("plans"))
    confirmations = _mappings(turn.get("confirmations"))
    tasks = _mappings(turn.get("execution_tasks"))
    actions = [
        action
        for confirmation in confirmations
        for action in _mappings(confirmation.get("actions"))
    ]
    case_actions = [
        action for action in actions
        if str(action.get("tool_call_id") or "").startswith("case:")
    ]
    if not plans and not confirmations and not tasks:
        return []

    agent_run_id = str(turn.get("agent_run_id") or "")
    timing = turn.get("timing") if isinstance(turn.get("timing"), Mapping) else {}
    created_at = _string(timing.get("created_at"))
    started_at = _string(timing.get("started_at")) or created_at
    progress: list[dict[str, object]] = []

    if case_actions:
        case_id = str(case_actions[0]["tool_call_id"])[len("case:"):]
        progress.append(_item(
            agent_run_id, "case-matched", "CASE_MATCHED", "COMPLETE",
            created_at, started_at, started_at, {"case_id": case_id},
        ))
        references = case_actions[0].get("input_references")
        input_count = len(references) if isinstance(references, Mapping) else 0
        arguments = case_actions[0].get("arguments")
        args = arguments if isinstance(arguments, Mapping) else {}
        facts: dict[str, object] = {
            "verified_inputs": input_count, "total_inputs": input_count,
        }
        profile = _profile(args)
        if profile is not None:
            facts["profile"] = profile
        progress.append(_item(
            agent_run_id, "inputs-verified", "INPUTS_VERIFIED", "COMPLETE",
            created_at, started_at, started_at, facts,
        ))

    if plans:
        progress.append(_item(
            agent_run_id, "plan-ready", "PLAN_READY", "COMPLETE",
            created_at, started_at, started_at,
            {"step_count": len(_sequence(plans[-1].get("steps")))},
        ))

    if confirmations:
        current = confirmations[-1]
        status = str(current.get("status") or "")
        progress_status = (
            "RUNNING" if status == "PENDING"
            else "COMPLETE" if status == "CONFIRMED"
            else "INTERRUPTED"
        )
        confirmation_at = _string(current.get("created_at")) or started_at
        first_task_at = _string(tasks[0].get("created_at")) if tasks else None
        progress.append(_item(
            agent_run_id, "waiting-confirmation", "WAITING_CONFIRMATION",
            progress_status, confirmation_at, confirmation_at,
            first_task_at if progress_status == "COMPLETE" else (
                confirmation_at if progress_status in _TERMINAL else None
            ),
            {"action_count": len(_mappings(current.get("actions")))},
        ))

    latest_tasks = _latest_tasks(tasks)
    if latest_tasks:
        progress.extend(_batch_progress(agent_run_id, latest_tasks))
    return progress


def _batch_progress(
    agent_run_id: str,
    tasks: list[Mapping[str, object]],
) -> list[dict[str, object]]:
    statuses = [str(task.get("status") or "") for task in tasks]
    stages = [task.get("stage") for task in tasks]
    created = _earliest(_string(task.get("created_at")) for task in tasks)
    started = _earliest(
        _string(task.get("started_at")) or _string(task.get("created_at"))
        for task in tasks
    )
    finished = _latest(_string(task.get("finished_at")) for task in tasks)
    attempt = max(int(task.get("attempt") or 1) for task in tasks)
    run_id = str(tasks[0].get("workflow_run_id") or "") if len(tasks) == 1 else None
    facts = {"attempt": attempt, "workflow_count": len(tasks)}
    result: list[dict[str, object]] = []
    queue_status = "RUNNING" if all(status == "QUEUED" for status in statuses) else "COMPLETE"
    result.append(_item(
        agent_run_id, "execution:queued", "EXECUTION_QUEUED", queue_status,
        created, created, None if queue_status == "RUNNING" else started, facts,
        workflow_run_id=run_id,
    ))

    if not all(status == "QUEUED" for status in statuses):
        active = any(status in {"QUEUED", "RUNNING"} for status in statuses)
        all_succeeded = all(status == "SUCCEEDED" for status in statuses)
        execution_status = (
            "RUNNING" if active and not all(
                status == "SUCCEEDED" or stage == "PUBLISHING"
                for status, stage in zip(statuses, stages, strict=True)
            )
            else "COMPLETE" if active or all_succeeded
            else "FAILED" if any(status in {"FAILED", "TIMED_OUT"} for status in statuses)
            else "INTERRUPTED"
        )
        result.append(_item(
            agent_run_id, "execution:running", "EXECUTION_RUNNING",
            execution_status, started, started,
            finished if execution_status in _TERMINAL else None, facts,
            workflow_run_id=run_id,
        ))
    publishing = any(stage == "PUBLISHING" for stage in stages)
    succeeded = all(status == "SUCCEEDED" for status in statuses)
    if publishing or succeeded:
        publishing_status = "RUNNING" if publishing and not succeeded else "COMPLETE"
        result.append(_item(
            agent_run_id, "execution:publishing", "RESULTS_PUBLISHING",
            publishing_status, started, started,
            finished if publishing_status == "COMPLETE" else None, facts,
            workflow_run_id=run_id,
        ))

    if succeeded:
        code = "RECOVERY_SUCCEEDED" if attempt > 1 else "EXECUTION_SUCCEEDED"
        result.append(_item(
            agent_run_id, "execution:terminal", code, "COMPLETE",
            finished or started, finished or started, finished or started,
            {**facts, "output_count": sum(int(task.get("output_count") or 0) for task in tasks)},
            workflow_run_id=run_id,
        ))
    elif all(status in {"SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED"} for status in statuses):
        terminal_status = next(status for status in statuses if status != "SUCCEEDED")
        failure = next(task for task in tasks if str(task.get("status") or "") != "SUCCEEDED")
        result.append(_item(
            agent_run_id, "execution:terminal", "EXECUTION_FAILED",
            "FAILED" if terminal_status != "CANCELLED" else "INTERRUPTED",
            finished or started, finished or started, finished or started,
            {**facts, "terminal_status": terminal_status,
             "error_code": failure.get("error_code") or "WORKFLOW_FAILED"},
            workflow_run_id=run_id,
        ))
    return result


def _latest_tasks(tasks: list[Mapping[str, object]]) -> list[Mapping[str, object]]:
    latest: dict[str, Mapping[str, object]] = {}
    order: list[str] = []
    for task in tasks:
        key = str(task.get("tool_call_id") or task.get("workflow_run_id") or "")
        if key not in latest:
            order.append(key)
        if int(task.get("attempt") or 1) >= int(latest.get(key, {}).get("attempt") or 0):
            latest[key] = task
    return [latest[key] for key in order]


def _profile(arguments: Mapping[str, object]) -> str | None:
    pretrain = arguments.get("pretrain.epochs")
    finetune = arguments.get("finetune.epochs")
    hidden = arguments.get("pretrain.hiddenSize")
    if (pretrain, finetune, hidden) == (80, 100, 64):
        return "standard"
    if (pretrain, finetune, hidden) == (5, 5, 32):
        return "development_fast"
    return None


def _item(
    agent_run_id: str,
    progress_id: str,
    code: str,
    status: str,
    created_at: str | None,
    started_at: str | None,
    finished_at: str | None,
    facts: dict[str, object],
    *,
    workflow_run_id: str | None = None,
) -> dict[str, object]:
    title, summary = _progress_presentation(code, status, facts)
    return {
        "progress_id": f"{agent_run_id}:{progress_id}",
        "agent_run_id": agent_run_id,
        "workflow_run_id": workflow_run_id,
        "code": code,
        "status": status,
        "scope": "WORKFLOW" if workflow_run_id else "TURN",
        "title": title,
        "summary": summary,
        "facts": facts,
        "created_at": created_at,
        "started_at": started_at,
        "finished_at": finished_at,
    }


def _mappings(value: object) -> list[Mapping[str, object]]:
    return [item for item in _sequence(value) if isinstance(item, Mapping)]


def _sequence(value: object) -> Sequence[object]:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else ()


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _earliest(values) -> str | None:
    present = [value for value in values if value is not None]
    return min(present) if present else None


def _latest(values) -> str | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None
