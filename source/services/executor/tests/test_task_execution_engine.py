from platform_executor_service.task_execution import TaskOutcome, WorkerCapacity


def test_worker_capacity_and_outcome_keep_worker_v1_payload_rules() -> None:
    body = {
        "worker_id": "native-1",
        "runtime": "native",
        "catalog_digest": "sha256:catalog",
        "slots": 2,
        "memory_bytes": 1024,
    }
    assert WorkerCapacity.from_body(body) == WorkerCapacity(
        "native-1", "native", 2, 1024
    )
    outcome = TaskOutcome.succeeded(
        {"values": {"summary": {"count": 1}}, "stdout": "ok", "stderr": ""},
        [{"name": "summary", "kind": "value"}],
    )
    assert outcome.values_json == '{"summary":{"count":1}}'
    assert outcome.stdout_tail == "ok"
