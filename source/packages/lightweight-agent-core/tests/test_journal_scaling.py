from __future__ import annotations

import gc
import json
import runpy
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest
import platform_lightweight_agent.workflow_v3 as workflow_v3
from platform_lightweight_agent import (
    CancellationToken,
    InMemoryRunJournal,
    LocalToolExecutor,
    RunSnapshot,
    SQLiteRunJournal,
    WorkflowEngine,
    RunRetentionPolicy,
    WorkflowValidationError,
)
from platform_lightweight_agent.journal import (
    RunConflictError,
    RunControl,
    RunJournalBusyError,
    RunJournalError,
)
from platform_lightweight_agent.workflow_v3 import _monitor_persisted_control


def test_state_cost_probe_measures_real_approval_resume_and_sqlite_writes() -> None:
    probe = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "scripts/measure_state_cost.py")
    )
    report = probe["measure"](sizes=(1, 2), storages=("memory", "sqlite"))

    assert report["schema_version"] == "v6-state-cost-v4"
    for row in report["results"]:
        assert row["status"] == "SUCCEEDED"
        assert row["run_commit_count"] > 0
        assert row["resume_commit_count"] > 0
        assert row["approval_resume_ms"] > 0
        assert row["completed_tool_replays"] == 0
        assert row["run_state_validated_bytes"] > 0
        assert row["resume_state_validated_bytes"] > 0
        if row["storage"] == "sqlite":
            assert row["sqlite_run_snapshot_write_bytes"] > 0
            assert row["sqlite_resume_snapshot_write_bytes"] > 0
            assert row["sqlite_run_cell_write_bytes"] > 0
            assert row["sqlite_cell_write_count"] > 0
            assert row["sqlite_run_transaction_ms"] > 0
        else:
            assert row["journal_reopen_ms"] is None
        if row["steps"] == 2:
            assert isinstance(row["incremental_run_ms_per_step"], float)


def _snapshot(run_id: str = "scaling") -> RunSnapshot:
    return RunSnapshot(
        run_id=run_id,
        workflow={
            "schema_version": "v6-light-workflow-v1",
            "workflow_id": "journal-scaling",
            "version": "1",
            "steps": [
                {
                    "id": "review",
                    "kind": "approval",
                    "config": {"prompt": "continue?"},
                }
            ],
        },
        inputs={},
        state={"values": {}, "payload": "x" * 512},
        status="RUNNING",
        driver_bindings={},
        execution_manifest={
            "schema_version": "v6-execution-manifest-v1",
            "frozen": "y" * 4096,
        },
    )


def test_sqlite_stores_immutable_definition_once_and_pages_events(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runs.sqlite"
    journal = SQLiteRunJournal(path)
    current = journal.create(_snapshot(), "RUN_STARTED", {})
    for index in range(5):
        current = journal.replace(
            current.next_revision(
                state={"values": {"index": index}, "payload": "x" * 512}
            ),
            expected_revision=current.revision,
            event_type="ADVANCED",
            payload={"index": index},
        )

    with sqlite3.connect(path) as connection:
        compact = json.loads(
            connection.execute(
                "SELECT snapshot_json FROM lightweight_agent_runs"
            ).fetchone()[0]
        )
        definitions = connection.execute(
            "SELECT COUNT(*) FROM lightweight_agent_definitions"
        ).fetchone()[0]
    assert "workflow" not in compact
    assert "execution_manifest" not in compact
    assert definitions == 1
    assert journal.get(current.run_id).workflow["workflow_id"] == "journal-scaling"
    assert [event.sequence for event in journal.events(current.run_id, limit=2)] == [
        1,
        2,
    ]
    assert [
        event.sequence for event in journal.events(current.run_id, after=2, limit=2)
    ] == [3, 4]
    status = journal.status(current.run_id)
    assert status.revision == current.revision
    assert status.last_sequence == 6
    mapped = journal.events(current.run_id, limit=1)[0].to_mapping()
    assert mapped["created_at"] > 0
    assert mapped["payload_size_bytes"] >= 2


def test_sqlite_control_read_coexists_with_writer_in_wal_mode(
    tmp_path: Path,
) -> None:
    """A normal local writer does not block the execution-control reader."""

    path = tmp_path / "transient-lock.sqlite"
    journal = SQLiteRunJournal(path)
    current = journal.create(_snapshot("transient-lock"), "RUN_STARTED", {})
    with closing(journal._connect()) as configured:
        assert configured.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert configured.execute("PRAGMA synchronous").fetchone()[0] == 2
    lock = sqlite3.connect(path, isolation_level=None)
    lock.execute("BEGIN IMMEDIATE")
    started = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(journal.status, current.run_id)
            status = pending.result(timeout=2)
            lock.execute("COMMIT")
    finally:
        if lock.in_transaction:
            lock.execute("ROLLBACK")
        lock.close()

    assert status.run_id == current.run_id
    assert status.revision == current.revision
    assert status.status == current.status
    assert time.monotonic() - started < 2


def test_sqlite_control_read_classifies_an_expired_busy_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "busy-control.sqlite"
    journal = SQLiteRunJournal(path)
    saved = journal.create(_snapshot("busy-control"), "RUN_STARTED", {})
    ordinary_connect = journal._connect

    def short_wait_connect() -> sqlite3.Connection:
        connection = ordinary_connect()
        connection.execute("PRAGMA busy_timeout = 10")
        return connection

    monkeypatch.setattr(journal, "_connect", short_wait_connect)
    with closing(sqlite3.connect(path, isolation_level=None)) as lock:
        assert lock.execute("PRAGMA locking_mode = EXCLUSIVE").fetchone()[0] == "exclusive"
        lock.execute("BEGIN EXCLUSIVE")
        lock.execute(
            "UPDATE lightweight_agent_runs SET status=status WHERE run_id=?",
            (saved.run_id,),
        )
        with pytest.raises(RunJournalBusyError):
            journal.control(saved.run_id)
        lock.execute("COMMIT")
    assert journal.control(saved.run_id).revision == saved.revision


def test_control_monitor_retries_transient_busy_but_not_unbounded_busy() -> None:
    class BusyJournal:
        def __init__(self, busy_reads: int) -> None:
            self.busy_reads = busy_reads
            self.calls = 0

        def control(self, run_id: str) -> RunControl:
            self.calls += 1
            if self.calls <= self.busy_reads:
                raise RunJournalBusyError("local run control is temporarily busy")
            return RunControl(run_id, "RUNNING", 1, False, 1, "owner", None)

    async def exercise(busy_reads: int, expect_failure: bool) -> int:
        journal = BusyJournal(busy_reads)
        coordinator = SimpleNamespace(
            owner_id="owner",
            generation=1,
            snapshot=SimpleNamespace(run_id="control-monitor"),
            engine=SimpleNamespace(
                workflow_services=SimpleNamespace(journal=journal)
            ),
        )
        token = CancellationToken()
        done = anyio.Event()
        if expect_failure:
            with pytest.raises(RunJournalBusyError):
                with anyio.fail_after(1):
                    await _monitor_persisted_control(coordinator, token, done)
        else:
            async with anyio.create_task_group() as group:
                group.start_soon(_monitor_persisted_control, coordinator, token, done)
                with anyio.fail_after(1):
                    while journal.calls < busy_reads + 1:
                        await anyio.sleep(0.01)
                done.set()
        assert not token.cancelled
        return journal.calls

    assert anyio.run(exercise, 1, False) >= 2
    assert anyio.run(exercise, 3, True) == 3


def test_control_monitor_exits_if_heartbeat_races_with_owner_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ReleasedCoordinator:
        owner_id = "owner"
        generation = 1
        snapshot = SimpleNamespace(run_id="released-owner")
        engine = SimpleNamespace(
            workflow_services=SimpleNamespace(
                journal=SimpleNamespace(
                    control=lambda run_id: RunControl(
                        run_id, "RUNNING", 1, False, 1, "owner", None
                    )
                )
            )
        )

        async def commit(self, *_args: object) -> None:
            raise RunConflictError("v3 execution generation is no longer current")

    monkeypatch.setattr(workflow_v3, "_EXECUTION_HEARTBEAT_SECONDS", 0.0)

    async def exercise() -> None:
        token = CancellationToken()
        with anyio.fail_after(1):
            await _monitor_persisted_control(
                ReleasedCoordinator(), token, anyio.Event()
            )
        assert token.cancelled

    anyio.run(exercise)


def test_sqlite_stores_one_content_addressed_copy_of_large_results(
    tmp_path: Path,
) -> None:
    path = tmp_path / "values.sqlite"
    journal = SQLiteRunJournal(path)
    large = {"data": "x" * 512}
    snapshot = replace(
        _snapshot("deduplicated-values"),
        state={
            "values": {"step": large},
            "actions": {"action": {"result": {"output": large}}},
        },
    )

    saved = journal.create(snapshot, "RUN_STARTED", {})

    with sqlite3.connect(path) as connection:
        compact_json = connection.execute(
            "SELECT snapshot_json FROM lightweight_agent_runs"
        ).fetchone()[0]
        value_count = connection.execute(
            "SELECT COUNT(*) FROM lightweight_agent_values"
        ).fetchone()[0]
    assert "x" * 512 not in compact_json
    assert value_count == 1
    assert saved.state["values"]["step"] == large
    hydrated = journal.get(saved.run_id)
    assert hydrated.state["values"]["step"] == large
    assert hydrated.state["actions"]["action"]["result"]["output"] == large

    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE lightweight_agent_values SET value_json=?", ('{"data":"tampered"}',)
        )
    with pytest.raises(RunJournalError, match="digest"):
        journal.get(saved.run_id)


def test_private_storage_marker_is_opaque_business_json_in_every_journal(
    tmp_path: Path,
) -> None:
    business = {
        "$v6_storage": {"nested": ["business", {"digest": "not-internal"}]},
        "exact_ref_shape": {
            "$v6_storage": "ref",
            "digest": "sha256:" + "a" * 64,
            "bytes": 999,
        },
        "exact_inline_shape": {"$v6_storage": "inline", "value": {"ok": True}},
        "payload": "x" * 512,
    }
    snapshots = []
    for name, journal in (
        ("memory", InMemoryRunJournal()),
        ("sqlite", SQLiteRunJournal(tmp_path / "markers.sqlite")),
    ):
        snapshot = replace(
            _snapshot(f"marker-{name}"),
            inputs={"business": business},
            state={
                "values": {"business": business},
                "actions": {"a": {"result": {"output": business}}},
            },
        )
        journal.create(snapshot, "RUN_STARTED", {})
        snapshots.append(journal.get(snapshot.run_id).to_mapping())

    assert snapshots[0]["inputs"] == snapshots[1]["inputs"]
    assert snapshots[0]["state"] == snapshots[1]["state"]

    sqlite = SQLiteRunJournal(tmp_path / "markers.sqlite")
    sqlite.create(
        replace(_snapshot("purge-me"), status="SUCCEEDED"),
        "RUN_SUCCEEDED",
        {},
    )
    report = sqlite.apply_retention(
        RunRetentionPolicy(archive_after_seconds=0, purge_after_seconds=0),
        now=time.time() + 1,
    )
    assert report.purged_run_ids == ("purge-me",)
    assert sqlite.get("marker-sqlite").inputs["business"] == business


def test_legacy_uncompacted_snapshot_keeps_storage_shaped_business_json(
    tmp_path: Path,
) -> None:
    """Migration reads must not reinterpret an old user's ordinary object."""

    journal = SQLiteRunJournal(tmp_path / "legacy-marker.sqlite")
    business = {
        "$v6_storage": "ref",
        "digest": "sha256:" + "c" * 64,
        "bytes": 17,
    }
    snapshot = replace(_snapshot("legacy-marker"), inputs={"business": business})
    journal.create(snapshot, "RUN_STARTED", {})
    # Simulate the pre-normalization on-disk row rather than a current compact
    # row. The row has a workflow, so it never opted into private envelopes.
    with sqlite3.connect(journal.path) as connection:
        connection.execute(
            "UPDATE lightweight_agent_runs SET snapshot_json=? WHERE run_id=?",
            (json.dumps(snapshot.to_mapping()), snapshot.run_id),
        )

    restored = journal.get(snapshot.run_id)

    assert restored.inputs == {"business": business}


def test_inspection_does_not_hydrate_large_business_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import platform_lightweight_agent.journal as journal_module

    journal = SQLiteRunJournal(tmp_path / "inspection.sqlite")
    large = {"data": "x" * 2048}
    snapshot = replace(
        _snapshot("compact-inspection"),
        state={
            "values": {"large": large},
            "actions": {
                "action": {
                    "status": "RESOLVED",
                    "step_key": "write",
                    "request": {
                        "tool": {
                            "tool_id": "example.write",
                            "version": "1",
                            "package_digest": "sha256:" + "b" * 64,
                        }
                    },
                    "result": {"output": large},
                }
            },
        },
    )
    journal.create(snapshot, "RUN_STARTED", {})

    def forbidden(*args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        raise AssertionError("inspection hydrated business values")

    monkeypatch.setattr(journal_module, "_read_values", forbidden)
    compact = journal.inspection(snapshot.run_id)

    assert compact.state["actions"]["action"]["result"] == {}
    assert "values" not in compact.state
    explanation = WorkflowEngine(journal, LocalToolExecutor(), {}).explain(
        snapshot.run_id
    )
    assert explanation.actions[0]["result_known"] is True


def test_space_diagnostics_distinguish_logical_purge_from_physical_reclaim(
    tmp_path: Path,
) -> None:
    path = tmp_path / "space.sqlite"
    journal = SQLiteRunJournal(path)
    large = {"data": "x" * (2 * 1024 * 1024)}
    snapshot = replace(
        _snapshot("space-run"),
        status="SUCCEEDED",
        state={"values": {"large": large}},
    )
    journal.create(snapshot, "RUN_SUCCEEDED", {})
    before_purge = journal.space_info()
    journal.apply_retention(
        RunRetentionPolicy(archive_after_seconds=0, purge_after_seconds=0),
        now=time.time() + 1,
    )
    after_purge = journal.space_info()

    assert after_purge.database_size_bytes == before_purge.database_size_bytes
    assert after_purge.reusable_bytes > 0
    report = journal.vacuum()
    assert report.after.database_size_bytes < report.before.database_size_bytes
    assert report.after.reusable_bytes <= report.before.reusable_bytes


def test_vacuum_refuses_a_journal_with_live_runs(tmp_path: Path) -> None:
    journal = SQLiteRunJournal(tmp_path / "active-space.sqlite")
    journal.create(_snapshot("active"), "RUN_STARTED", {})

    with pytest.raises(RunConflictError, match="terminal"):
        journal.vacuum()


def test_vacuum_checks_temporary_disk_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import platform_lightweight_agent.journal as journal_module

    journal = SQLiteRunJournal(tmp_path / "low-space.sqlite")
    actual = journal_module.shutil.disk_usage(tmp_path)
    monkeypatch.setattr(
        journal_module.shutil,
        "disk_usage",
        lambda _path: type(actual)(actual.total, actual.used, 0),
    )

    with pytest.raises(RunJournalError, match="insufficient free space"):
        journal.vacuum()


def test_sqlite_reads_close_connections_without_waiting_for_gc(tmp_path: Path) -> None:
    proc_fds = Path("/proc/self/fd")
    if not proc_fds.is_dir():
        pytest.skip("file descriptor accounting requires procfs")
    journal = SQLiteRunJournal(tmp_path / "connections.sqlite")
    saved = journal.create(_snapshot("connection-run"), "RUN_STARTED", {})
    before = len(tuple(proc_fds.iterdir()))
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        for _ in range(40):
            assert journal.get(saved.run_id).revision == saved.revision
            assert journal.status(saved.run_id).status == "RUNNING"
            assert journal.control(saved.run_id).execution_generation == 0
            assert journal.events(saved.run_id)
        after = len(tuple(proc_fds.iterdir()))
    finally:
        if was_enabled:
            gc.enable()
    assert after <= before + 2


@pytest.mark.parametrize(
    "journal_factory",
    (
        lambda path: InMemoryRunJournal(),
        lambda path: SQLiteRunJournal(path),
    ),
)
def test_journal_returns_a_snapshot_detached_from_its_owned_storage(
    tmp_path: Path, journal_factory
) -> None:  # type: ignore[no-untyped-def]
    journal = journal_factory(tmp_path / "ownership.sqlite")
    saved = journal.create(_snapshot("owned-copy"), "RUN_STARTED", {})

    # RunSnapshot is frozen but deliberately contains JSON mappings.  A host
    # mutating its returned object must not be able to alter durable or
    # in-memory journal state.
    saved.state["payload"] = "caller mutation"

    assert journal.get(saved.run_id).state["payload"] == "x" * 512


def test_snapshot_revisions_reuse_a_verified_immutable_definition() -> None:
    snapshot = _snapshot("definition-cache")
    # ``RunSnapshot`` is frozen at the dataclass level but exposes JSON
    # mappings for ergonomic read access.  A caller cannot smuggle a changed
    # definition (or a credential-shaped field) into an otherwise state-only
    # revision through that mutable view.
    snapshot.workflow["api_key"] = "must-not-propagate"
    snapshot.execution_manifest["mutated"] = True
    snapshot.state["payload"] = "must-not-propagate"

    revised = snapshot.next_revision(state={"values": {}, "payload": "next"})

    assert "api_key" not in revised.workflow
    assert "mutated" not in revised.execution_manifest
    with pytest.raises(WorkflowValidationError, match="definition is immutable"):
        snapshot.next_revision(workflow={})

    revised.state["payload"] = "caller mutation"
    status_only = revised.next_revision(status="WAITING_APPROVAL")
    assert status_only.state["payload"] == "next"
    with pytest.raises(WorkflowValidationError, match="credentials"):
        revised.next_revision(state={"api_key": "must-be-rejected"})


@pytest.mark.parametrize("storage", ["memory", "sqlite"])
def test_internal_commits_do_not_materialize_immutable_definitions(
    tmp_path, monkeypatch, storage
) -> None:
    import platform_lightweight_agent.journal as module

    journal = (
        InMemoryRunJournal()
        if storage == "memory"
        else SQLiteRunJournal(tmp_path / "cost.sqlite")
    )
    current = journal.create(_snapshot("definition-cost"), "RUN_STARTED", {})
    decoded, validated = [], []
    original_decode, original_validate = (
        module._definition_object,
        module._validate_checkpoint_object,
    )

    def decode(serialized, name):
        decoded.append((name, len(serialized.encode("utf-8"))))
        return original_decode(serialized, name)

    def validate(value, name):
        validated.append(name)
        return original_validate(value, name)

    monkeypatch.setattr(module, "_definition_object", decode)
    monkeypatch.setattr(module, "_validate_checkpoint_object", validate)
    for index in range(50):
        current = journal.replace(
            current.next_revision(state={"values": {}, "index": index}),
            expected_revision=current.revision,
            event_type="ADVANCED",
            payload={},
        )
    assert decoded == []
    assert validated == ["state"] * 50
    # Only a public read materializes its own mutable view, at most once.
    current.workflow["changed"] = True
    assert current.workflow["changed"] is True
    assert len(decoded) == 1
    successor = current.next_revision(status="WAITING_APPROVAL")
    assert "changed" not in successor.workflow
    assert "changed" not in journal.get(current.run_id).workflow


def test_independent_mutable_sections_still_validate_new_external_values() -> None:
    current = _snapshot()
    current.inputs["api_key"] = "not-authority"
    successor = current.next_revision(state={"values": {}})
    assert successor.inputs == {}
    with pytest.raises(WorkflowValidationError, match="credentials"):
        replace(current, inputs={"api_key": "rejected"})
    with pytest.raises(WorkflowValidationError, match="credentials"):
        replace(current, driver_bindings={"password": "rejected"})


@pytest.mark.parametrize("storage", ["memory", "sqlite"])
def test_journal_projections_use_the_same_validated_state_as_storage(tmp_path, storage):
    journal = (
        InMemoryRunJournal()
        if storage == "memory"
        else SQLiteRunJournal(tmp_path / "mutation.sqlite")
    )
    current = _snapshot("mutation")
    for index in range(2):
        # Public mappings are detached views, not a second authority for the
        # persisted control/query projections.
        current.state["v3"] = {
            "cancel_requested": True,
            "pending_approvals": {"fake": {}},
            "execution": {"generation": 99, "owner_id": "fake", "lease_expires": 99.0},
        }
        if index == 0:
            journal.create(current, "SEEDED", {})
        else:
            journal.replace(
                current,
                expected_revision=current.revision - 1,
                event_type="ADVANCED",
                payload={},
            )
        assert journal.get(current.run_id).state.get("v3") is None
        control = journal.control(current.run_id)
        assert control.cancel_requested is False
        assert control.execution_generation == 0
        assert journal.list_runs().items[0].pending_approval_count == 0
        current = current.next_revision()


def test_sqlite_control_projection_tracks_v3_state_without_snapshot_read(
    tmp_path: Path,
) -> None:
    journal = SQLiteRunJournal(tmp_path / "control.sqlite")
    state = {
        "values": {},
        "v3": {
            "cancel_requested": True,
            "execution": {
                "generation": 7,
                "owner_id": "executor-test",
                "lease_expires": 123.5,
            },
        },
    }
    snapshot = replace(_snapshot("control-run"), state=state)
    journal.create(snapshot, "RUN_STARTED", {})

    control = journal.control(snapshot.run_id)

    assert control.cancel_requested is True
    assert control.execution_generation == 7
    assert control.execution_owner_id == "executor-test"
    assert control.execution_lease_expires == 123.5


@pytest.mark.parametrize(
    "journal_factory",
    (
        lambda path: InMemoryRunJournal(),
        lambda path: SQLiteRunJournal(path),
    ),
)
def test_recoverable_scan_cursor_pages_past_the_front_of_the_queue(
    tmp_path: Path, journal_factory
) -> None:  # type: ignore[no-untyped-def]
    journal = journal_factory(tmp_path / "cursor.sqlite")
    for run_id in ("recover-a", "recover-b", "recover-c"):
        journal.create(_snapshot(run_id), "RUN_STARTED", {})

    first_page = journal.recoverable_runs(limit=1)
    assert [item.run_id for item in first_page] == ["recover-a"]
    after = (first_page[0].revision, first_page[0].run_id)
    assert [item.run_id for item in journal.recoverable_runs(limit=2, after=after)] == [
        "recover-b",
        "recover-c",
    ]
