from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NodeSnapshot:
    node_id: str
    status: str
    dependencies: tuple[str, ...]


@dataclass(frozen=True)
class WorkflowSnapshot:
    status: str
    stage: str
    nodes: tuple[NodeSnapshot, ...]


@dataclass(frozen=True)
class JobAdmitted:
    pass


@dataclass(frozen=True)
class TaskSucceeded:
    node_id: str


@dataclass(frozen=True)
class TaskFailed:
    node_id: str


@dataclass(frozen=True)
class LeaseLost:
    node_id: str
    previous_loss_count: int


OrchestrationEvent = (
    JobAdmitted
    | TaskSucceeded
    | TaskFailed
    | LeaseLost
)


@dataclass(frozen=True)
class TransitionCommands:
    ready_node_ids: tuple[str, ...] = ()
    skipped_node_ids: tuple[str, ...] = ()
    retry_node_ids: tuple[str, ...] = ()


def transition(
    snapshot: WorkflowSnapshot, event: OrchestrationEvent
) -> TransitionCommands:
    if isinstance(event, (JobAdmitted, TaskSucceeded)):
        succeeded = {
            node.node_id for node in snapshot.nodes if node.status == "SUCCEEDED"
        }
        if isinstance(event, TaskSucceeded):
            succeeded.add(event.node_id)
        ready = tuple(
            sorted(
                node.node_id
                for node in snapshot.nodes
                if node.status == "WAITING"
                and set(node.dependencies) <= succeeded
            )
        )
        return TransitionCommands(ready_node_ids=ready)
    if isinstance(event, TaskFailed):
        return _failed(snapshot, event.node_id)
    if isinstance(event, LeaseLost):
        if event.previous_loss_count == 0:
            return TransitionCommands(retry_node_ids=(event.node_id,))
        return TransitionCommands()
    raise TypeError(type(event))


def finalization_ready(snapshot: WorkflowSnapshot) -> bool:
    return (
        snapshot.status == "RUNNING"
        and snapshot.stage == "DAG"
        and bool(snapshot.nodes)
        and all(node.status == "SUCCEEDED" for node in snapshot.nodes)
    )


def _failed(
    snapshot: WorkflowSnapshot, node_id: str
) -> TransitionCommands:
    affected = {node_id}
    changed = True
    while changed:
        changed = False
        for node in snapshot.nodes:
            if (
                node.node_id not in affected
                and node.status in {"WAITING", "READY"}
                and affected.intersection(node.dependencies)
            ):
                affected.add(node.node_id)
                changed = True
    return TransitionCommands(
        skipped_node_ids=tuple(sorted(affected - {node_id})),
    )
