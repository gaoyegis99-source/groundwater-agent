from platform_kernel.interaction.progress import build_turn_progress


def _turn(*, task_status="SUCCEEDED", stage=None, attempt=1, previous=()):
    action = {
        "tool_call_id": "case:yaozhanbao-transfer-case",
        "arguments": {
            "pretrain.epochs": 80,
            "finetune.epochs": 100,
            "pretrain.hiddenSize": 64,
        },
        "input_references": {f"input-{index}": {} for index in range(19)},
    }
    task = {
        "workflow_run_id": f"workflow-{attempt}",
        "tool_call_id": action["tool_call_id"],
        "status": task_status,
        "stage": stage,
        "attempt": attempt,
        "output_count": 25 if task_status == "SUCCEEDED" else 0,
        "error_code": None if task_status == "SUCCEEDED" else "ALGORITHM_FAILED",
        "created_at": "2026-10-13T01:00:02Z",
        "started_at": "2026-10-13T01:00:03Z",
        "finished_at": (
            "2026-10-13T01:03:03Z"
            if task_status in {"SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED"}
            else None
        ),
    }
    return {
        "agent_run_id": "agent-1",
        "timing": {
            "created_at": "2026-10-13T01:00:00Z",
            "started_at": "2026-10-13T01:00:00Z",
            "finished_at": task["finished_at"],
        },
        "plans": [{"steps": [{"step_id": "run-case"}]}],
        "confirmations": [{
            "status": "CONFIRMED",
            "created_at": "2026-10-13T01:00:01Z",
            "actions": [action],
        }],
        "execution_tasks": [*previous, task],
    }


def test_case_progress_projects_concise_business_milestones():
    progress = build_turn_progress(_turn())
    assert [item["code"] for item in progress] == [
        "CASE_MATCHED",
        "INPUTS_VERIFIED",
        "PLAN_READY",
        "WAITING_CONFIRMATION",
        "EXECUTION_QUEUED",
        "EXECUTION_RUNNING",
        "RESULTS_PUBLISHING",
        "EXECUTION_SUCCEEDED",
    ]
    assert progress[1]["facts"] == {
        "verified_inputs": 19,
        "total_inputs": 19,
        "profile": "standard",
    }
    assert progress[-1]["facts"]["output_count"] == 25
    assert progress[1]["title"] == "核验案例输入"
    assert progress[1]["summary"] == (
        "已核验 19 项同批输入，版本、对象和校验和均匹配。"
    )
    assert progress[-1]["summary"] == "执行成功，已发布 25 项成果。"
    assert len({item["progress_id"] for item in progress}) == len(progress)


def test_running_stage_changes_user_progress_without_tool_events():
    executing = build_turn_progress(_turn(task_status="RUNNING", stage="EXECUTING"))
    assert executing[-1]["code"] == "EXECUTION_RUNNING"
    assert executing[-1]["status"] == "RUNNING"
    publishing = build_turn_progress(_turn(task_status="RUNNING", stage="PUBLISHING"))
    assert publishing[-2]["status"] == "COMPLETE"
    assert publishing[-1]["code"] == "RESULTS_PUBLISHING"
    assert publishing[-1]["status"] == "RUNNING"


def test_only_latest_attempt_is_projected_and_recovery_is_explicit():
    failed = {
        "workflow_run_id": "workflow-1",
        "tool_call_id": "case:yaozhanbao-transfer-case",
        "status": "FAILED",
        "stage": None,
        "attempt": 1,
        "output_count": 0,
        "error_code": "ALGORITHM_FAILED",
        "created_at": "2026-10-13T01:00:02Z",
        "started_at": "2026-10-13T01:00:03Z",
        "finished_at": "2026-10-13T01:00:04Z",
    }
    progress = build_turn_progress(_turn(attempt=2, previous=(failed,)))
    assert not any(item["workflow_run_id"] == "workflow-1" for item in progress)
    assert progress[-1]["code"] == "RECOVERY_SUCCEEDED"
    assert progress[-1]["facts"]["attempt"] == 2


def test_read_only_turn_has_no_synthetic_progress():
    assert build_turn_progress({
        "agent_run_id": "agent-1",
        "timing": {},
        "plans": [],
        "confirmations": [],
        "execution_tasks": [],
    }) == []
