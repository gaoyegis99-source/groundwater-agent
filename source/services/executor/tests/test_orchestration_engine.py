from platform_executor_service.orchestration import (
    LeaseLost,
    NodeSnapshot,
    TaskFailed,
    TaskSucceeded,
    WorkflowSnapshot,
    finalization_ready,
    transition,
)


def _snapshot(*nodes, stage="DAG") -> WorkflowSnapshot:
    return WorkflowSnapshot("RUNNING", stage, tuple(nodes))


def _node(node_id, status, *dependencies) -> NodeSnapshot:
    return NodeSnapshot(node_id, status, tuple(dependencies))


def test_success_unlocks_only_nodes_with_all_dependencies_satisfied() -> None:
    snapshot = _snapshot(
        _node("left", "SUCCEEDED"),
        _node("right", "RUNNING"),
        _node("join", "WAITING", "left", "right"),
    )

    assert transition(snapshot, TaskSucceeded("left")).ready_node_ids == ()
    assert transition(snapshot, TaskSucceeded("right")).ready_node_ids == ("join",)


def test_failure_skips_the_complete_downstream_closure() -> None:
    snapshot = _snapshot(
        _node("source", "RUNNING"),
        _node("middle", "WAITING", "source"),
        _node("leaf", "WAITING", "middle"),
        _node("independent", "READY"),
    )

    commands = transition(snapshot, TaskFailed("source"))

    assert commands.skipped_node_ids == ("leaf", "middle")


def test_first_lease_loss_retries_and_second_loss_fails() -> None:
    snapshot = _snapshot(_node("task", "RUNNING"))

    assert transition(snapshot, LeaseLost("task", 0)).retry_node_ids == ("task",)
    second = transition(snapshot, LeaseLost("task", 1))
    assert second.retry_node_ids == ()


def test_finalization_requires_every_node_to_succeed() -> None:
    assert finalization_ready(
        _snapshot(_node("left", "SUCCEEDED"), _node("right", "SUCCEEDED"))
    )
    assert not finalization_ready(
        _snapshot(_node("left", "SUCCEEDED"), _node("right", "READY"))
    )
