from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import platform_lightweight_agent.journal as journal_module
import pytest
from platform_lightweight_agent import (
    BACKUP_RECOVERY_STATUS,
    JOURNAL_STORAGE_FORMAT_V1,
    JOURNAL_STORAGE_VERSION,
    ApprovalDecision,
    InMemoryRunJournal,
    LocalToolExecutor,
    NodeContract,
    RunRetentionPolicy,
    RunSnapshot,
    SQLiteRunJournal,
    ToolExecutionResult,
    ToolExecutionUncertainError,
    ToolReference,
    ValueBinding,
    WorkflowEngine,
    WorkflowRunError,
    explain_run,
    preview_recovery,
    tool_step,
    workflow,
)
from platform_lightweight_agent.cli import main
from platform_lightweight_agent.engine import _action_id
from platform_lightweight_agent.journal import RunJournalError

TOOL = ToolReference("inspection.lookup", "1", "sha256:" + "a" * 64)


def _workflow() -> dict[str, object]:
    return {
        "schema_version": "v6-light-workflow-v1",
        "workflow_id": "inspection-workflow",
        "version": "1",
        "steps": [{"id": "review", "kind": "approval", "config": {"prompt": "?"}}],
    }


def _snapshot(
    run_id: str = "inspection-run", *, status: str = "WAITING_APPROVAL"
) -> RunSnapshot:
    return RunSnapshot(
        run_id=run_id,
        workflow=_workflow(),
        inputs={},
        state={
            "values": {},
            "completed": [],
            "branches": {},
            "actions": {},
            "agents": {},
            "approved_steps": [],
            "pending": {"kind": "approval", "step_id": "review"},
            "failure": None,
        },
        status=status,
        driver_bindings={},
    )


def test_read_only_run_explanation_never_exposes_arguments_or_executes() -> None:
    state = dict(_snapshot(status="RECONCILING").state)
    state["pending"] = None
    state["actions"] = {
        "action-1": {
            "status": "RECONCILING",
            "step_key": "write",
            "request": {
                "action_id": "action-1",
                "tool": TOOL.to_mapping(),
                "arguments": {"opaque": "must-not-appear"},
            },
        }
    }
    snapshot = _snapshot(status="RECONCILING").next_revision(state=state)

    explanation = explain_run(snapshot).to_mapping()
    preview = preview_recovery(snapshot).to_mapping()

    serialized = json.dumps({"explanation": explanation, "preview": preview})
    assert "must-not-appear" not in serialized
    assert explanation["effects"] == "not_executed"
    assert explanation["reason"] == {"code": "EXTERNAL_RESULT_UNKNOWN"}
    assert preview["operations"] == [
        {
            "kind": "query_action_result",
            "action_id": "action-1",
            "step_id": "write",
            "execution": "never_redispatch",
            "effects": "reconciliation_query_only",
        }
    ]


def test_legacy_approval_preview_does_not_invent_an_approval_id() -> None:
    preview = preview_recovery(_snapshot()).to_mapping()

    assert preview["operations"] == [
        {
            "kind": "await_legacy_approval",
            "step_id": "review",
            "execution": "resume_with_boolean_approval",
            "effects": "not_executed",
        }
    ]


def test_engine_explanation_is_a_read_only_journal_view() -> None:
    journal = InMemoryRunJournal()
    saved = journal.create(_snapshot(), "RUN_STARTED", {})
    engine = WorkflowEngine(journal, object(), {})  # type: ignore[arg-type]

    assert engine.explain(saved.run_id).to_mapping()["run_id"] == saved.run_id
    assert journal.get(saved.run_id).revision == saved.revision


def test_sqlite_journal_lifecycle_backup_restore_and_retention(tmp_path: Path) -> None:
    journal_path = tmp_path / "runs.sqlite"
    journal = SQLiteRunJournal(journal_path)
    completed = journal.create(
        _snapshot("completed", status="SUCCEEDED"), "RUN_SUCCEEDED", {}
    )
    journal.create(_snapshot("live", status="RECONCILING"), "RUN_RECONCILING", {})

    info = journal.storage_info()
    assert info.format == JOURNAL_STORAGE_FORMAT_V1
    assert info.schema_version == JOURNAL_STORAGE_VERSION
    assert info.migrations == tuple(range(1, JOURNAL_STORAGE_VERSION + 1))

    archive = journal.backup_to(tmp_path / "runs.backup.sqlite")
    with sqlite3.connect(archive) as backup_connection:
        assert backup_connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert not Path(f"{archive}-wal").exists()
    assert not Path(f"{archive}-shm").exists()
    restored = SQLiteRunJournal.restore_from_backup(
        archive, tmp_path / "restored.sqlite"
    )
    with sqlite3.connect(restored.path) as restored_connection:
        assert restored_connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert restored.get(completed.run_id).to_mapping() == completed.to_mapping()
    assert restored.get("live").status == BACKUP_RECOVERY_STATUS
    assert [item.run_id for item in restored.recovery_holds()] == ["live"]
    assert restored.storage_info() == info

    occupied = tmp_path / "occupied.sqlite"
    sidecar = Path(f"{occupied}-wal")
    sidecar.write_bytes(b"unrelated-sidecar")
    with pytest.raises(RunJournalError, match="WAL sidecars"):
        journal.backup_to(occupied, overwrite=True)
    assert sidecar.read_bytes() == b"unrelated-sidecar"
    sidecar.unlink()
    sidecar.symlink_to(tmp_path / "missing-sidecar-target")
    with pytest.raises(RunJournalError, match="WAL sidecars"):
        journal.backup_to(occupied, overwrite=True)
    assert sidecar.is_symlink()

    link = tmp_path / "backup-link.sqlite"
    link.symlink_to(archive)
    with pytest.raises(RunJournalError, match="regular file"):
        SQLiteRunJournal.restore_from_backup(link, tmp_path / "ignored.sqlite")

    report = journal.apply_retention(
        RunRetentionPolicy(archive_after_seconds=0), now=10_000_000_000
    )
    assert report.archived_run_ids == ("completed",)
    assert journal.status("completed").archived_at == 10_000_000_000
    assert journal.status("live").archived_at is None

    purged = journal.apply_retention(
        RunRetentionPolicy(archive_after_seconds=0, purge_after_seconds=0),
        now=10_000_000_001,
    )
    assert purged.purged_run_ids == ("completed",)
    with pytest.raises(KeyError):
        journal.get("completed")
    assert journal.get("live").status == "RECONCILING"


def test_backup_restore_holds_unknown_action_until_explicit_query_recovery(
    tmp_path: Path,
) -> None:
    class ExternalLedger:
        def __init__(self) -> None:
            self.execute_calls = 0
            self.reconcile_calls = 0
            self.result: ToolExecutionResult | None = None

        def execute(self, request):  # type: ignore[no-untyped-def]
            self.execute_calls += 1
            self.result = ToolExecutionResult(request.action_id, True, {"value": 3})
            raise ToolExecutionUncertainError("receipt lost after side effect")

        def reconcile(self, request):  # type: ignore[no-untyped-def]
            self.reconcile_calls += 1
            assert self.result is not None
            assert request.action_id == self.result.action_id
            return self.result

    source_path = tmp_path / "source.sqlite"
    ledger = ExternalLedger()
    source = WorkflowEngine(SQLiteRunJournal(source_path), ledger, {})
    spec = workflow(
        "backup-query",
        "1",
        (tool_step("write", TOOL, {"value": 3}, approval_required=False),),
        schema_version="v6-light-workflow-v1",
    )
    unknown = source.run(spec, run_id="backup-query-run")
    assert unknown.status == "RECONCILING"
    assert ledger.execute_calls == 1

    archive = SQLiteRunJournal(source_path).backup_to(tmp_path / "query.backup.sqlite")
    restored_journal = SQLiteRunJournal.restore_from_backup(
        archive, tmp_path / "query-restored.sqlite"
    )
    restored = WorkflowEngine(restored_journal, ledger, {})

    held = restored.get(unknown.run_id)
    assert held.status == BACKUP_RECOVERY_STATUS
    preview = restored.preview_recovery(held.run_id).to_mapping()
    assert preview["blockers"] == [
        {
            "code": "BACKUP_RECOVERY_REQUIRED",
            "risk_code": "DUPLICATE_EFFECT_POSSIBLE",
        }
    ]
    with pytest.raises(WorkflowRunError, match="BACKUP_RECOVERY_REQUIRED"):
        restored.resume(held.run_id)
    with pytest.raises(RunJournalError, match="BACKUP_SOURCE_OWNER_STOP_REQUIRED"):
        restored.release_backup_recovery(held.run_id, source_owner_stopped=False)

    released = restored.release_backup_recovery(held.run_id, source_owner_stopped=True)
    assert released.status == "RECONCILING"
    completed = restored.resume(held.run_id)

    assert completed.status == "SUCCEEDED"
    assert ledger.execute_calls == 1
    assert ledger.reconcile_calls == 1


def test_backup_idempotent_redispatch_reuses_the_original_action_id(
    tmp_path: Path,
) -> None:
    spec = workflow(
        "backup-idempotent",
        "1",
        (tool_step("write", TOOL, {"value": 5}, approval_required=False),),
        schema_version="v6-light-workflow-v1",
    )
    execution_id = "backup-idempotent-execution"
    action_id = _action_id(execution_id, "write", 0)
    request = {
        "action_id": action_id,
        "tool": TOOL.to_mapping(),
        "arguments": {"value": 5},
    }
    state = {
        "values": {},
        "completed": [],
        "branches": {},
        "actions": {
            action_id: {
                "request": request,
                "status": "DISPATCHED",
                "step_id": "write",
                "capabilities": {"accepts_idempotency_key": True},
            }
        },
        "agents": {},
        "approved_steps": [],
        "pending": None,
        "failure": None,
    }

    class IdempotentExecutor:
        def __init__(self) -> None:
            self.action_ids: list[str] = []

        def execute(self, request):  # type: ignore[no-untyped-def]
            self.action_ids.append(request.action_id)
            return ToolExecutionResult(request.action_id, True, {"value": 5})

        def reconcile(self, request):  # type: ignore[no-untyped-def]
            del request

    source_journal = SQLiteRunJournal(tmp_path / "idempotent-source.sqlite")
    executor = IdempotentExecutor()
    source_engine = WorkflowEngine(source_journal, executor, {})
    source_journal.create(
        RunSnapshot(
            run_id="backup-idempotent-run",
            workflow=spec.to_mapping(),
            inputs={},
            state=state,
            status="RUNNING",
            driver_bindings={},
            execution_id=execution_id,
            execution_manifest=source_engine._execution_manifest(spec),
        ),
        "ACTION_DISPATCHED",
        {"action_id": action_id},
    )
    archive = source_journal.backup_to(tmp_path / "idempotent.backup.sqlite")
    restored_journal = SQLiteRunJournal.restore_from_backup(
        archive, tmp_path / "idempotent-restored.sqlite"
    )

    engine = WorkflowEngine(restored_journal, executor, {})
    released = engine.release_backup_recovery(
        "backup-idempotent-run",
        source_owner_stopped=True,
        strategy="idempotent-redispatch",
    )
    assert released.status == "RUNNING"

    completed = engine.resume("backup-idempotent-run")

    assert completed.status == "SUCCEEDED"
    assert executor.action_ids == [action_id]


@pytest.mark.anyio
async def test_backup_query_holds_a_ready_action_after_the_old_owner_continued(
    tmp_path: Path,
) -> None:
    """A copied READY action may have been dispatched after the backup."""

    empty = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    value = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
        "additionalProperties": False,
    }
    spec = workflow(
        "backup-ready-query",
        "1",
        (
            tool_step(
                "write",
                TOOL,
                approval_required=True,
                contract=NodeContract(empty, value, {}),
            ),
        ),
        schema_version="v6-light-workflow-v3",
        input_schema=empty,
        output_schema=value,
        output_bindings={"/value": ValueBinding("step_output", "/value", "write")},
    )

    class Ledger:
        def __init__(self) -> None:
            self.execute_calls = 0
            self.reconcile_calls = 0
            self.results: dict[str, ToolExecutionResult] = {}

        async def execute_async(self, request, cancellation):  # type: ignore[no-untyped-def]
            del cancellation
            self.execute_calls += 1
            result = ToolExecutionResult(request.action_id, True, {"value": 8})
            self.results[request.action_id] = result
            return result

        async def reconcile_async(self, request):  # type: ignore[no-untyped-def]
            self.reconcile_calls += 1
            return self.results.get(request.action_id)

    source_path = tmp_path / "ready-source.sqlite"
    ledger = Ledger()
    source = WorkflowEngine(SQLiteRunJournal(source_path), ledger, {})
    waiting = await source.run_async(spec, {}, run_id="ready-backup-run")
    approval_id = next(iter(waiting.state["v3"]["pending_approvals"]))
    ready = await source.resume_async(
        waiting.run_id,
        approvals=(ApprovalDecision(approval_id, True),),
        advance=False,
    )
    action_id = next(iter(ready.state["actions"]))
    assert ready.state["actions"][action_id]["status"] == "READY"

    archive = SQLiteRunJournal(source_path).backup_to(tmp_path / "ready.backup.sqlite")
    source_completed = await source.resume_async(ready.run_id)
    assert source_completed.status == "SUCCEEDED"
    assert ledger.execute_calls == 1

    restored_journal = SQLiteRunJournal.restore_from_backup(
        archive, tmp_path / "ready-restored.sqlite"
    )
    restored = WorkflowEngine(restored_journal, ledger, {})
    held = restored.get(ready.run_id)
    assert held.status == BACKUP_RECOVERY_STATUS
    preview = restored.preview_recovery(held.run_id).to_mapping()
    assert preview["operations"] == [
        {
            "kind": "release_backup_recovery",
            "execution": "requires_source_owner_stop_confirmation",
            "effects": "not_executed",
            "strategies": ["query", "idempotent-redispatch"],
            "risk_code": "DUPLICATE_EFFECT_POSSIBLE",
            "at_risk_action_ids": [action_id],
        }
    ]
    with pytest.raises(RunJournalError, match="BACKUP_IDEMPOTENCY_NOT_DECLARED"):
        restored.release_backup_recovery(
            held.run_id,
            source_owner_stopped=True,
            strategy="idempotent-redispatch",
        )
    released = restored.release_backup_recovery(
        held.run_id, source_owner_stopped=True, strategy="query"
    )
    assert released.status == "RECONCILING"
    assert released.state["actions"][action_id]["status"] == "RECONCILING"

    completed = await restored.resume_async(held.run_id)

    assert completed.status == "SUCCEEDED"
    assert ledger.execute_calls == 1
    assert ledger.reconcile_calls == 1


def test_reusing_a_purged_business_run_id_gets_a_fresh_execution_identity(
    tmp_path: Path,
) -> None:
    journal = SQLiteRunJournal(tmp_path / "reuse.sqlite")
    executor = LocalToolExecutor()
    calls: list[dict[str, object]] = []

    def echo(arguments: dict[str, object]) -> dict[str, object]:
        calls.append(dict(arguments))
        return {"value": arguments["value"]}

    executor.register(TOOL, echo)
    spec = workflow(
        "reuse-identity",
        "1",
        (tool_step("write", TOOL, {"value": 1}, approval_required=False),),
        schema_version="v6-light-workflow-v1",
    )
    engine = WorkflowEngine(journal, executor, {})
    first = engine.run(spec, run_id="business-name")
    first_action_id = next(iter(first.state["actions"]))

    report = journal.apply_retention(
        RunRetentionPolicy(archive_after_seconds=0, purge_after_seconds=0),
        now=10_000_000_000,
    )
    assert report.purged_run_ids == ("business-name",)

    # Keep the same executor deliberately: an old in-memory idempotency cache
    # must not mistake a new business invocation for the purged one.
    second = WorkflowEngine(journal, executor, {}).run(spec, run_id="business-name")
    second_action_id = next(iter(second.state["actions"]))

    assert second.execution_id != first.execution_id
    assert second_action_id != first_action_id
    assert calls == [{"value": 1}, {"value": 1}]
    assert journal.get("business-name").execution_id == second.execution_id


def test_legacy_snapshot_without_execution_id_has_stable_resume_identity() -> None:
    raw = _snapshot("old-run").to_mapping()
    raw.pop("execution_id")

    restored_once = RunSnapshot.from_mapping(raw)
    restored_twice = RunSnapshot.from_mapping(raw)

    assert restored_once.execution_id == restored_twice.execution_id
    # Pre-instance journals derived action IDs from run_id itself. Keep that
    # exact value so a resume does not manufacture a new idempotency key.
    assert restored_once.execution_id == "old-run"
    assert _action_id(restored_once.execution_id, "write", 0) == (
        "action-00000000-7f1e8655d070fce0cd89223b748d949b5d994963"
    )


def test_execution_identity_keeps_existing_snapshot_positional_order() -> None:
    """New identity metadata must not shift the original optional fields."""

    old_style = RunSnapshot(
        "old-positional",
        _workflow(),
        {},
        _snapshot("old-positional").state,
        "WAITING_APPROVAL",
        {},
        {"frozen": "manifest"},
        7,
    )

    assert old_style.execution_manifest == {"frozen": "manifest"}
    assert old_style.revision == 7
    assert old_style.execution_id == "old-positional"


def _write_legacy_journal(path: Path) -> None:
    snapshot = _snapshot("legacy", status="RUNNING")
    legacy_snapshot = snapshot.to_mapping()
    legacy_snapshot.pop("execution_id")
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE lightweight_agent_runs ("
            "run_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, snapshot_json TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE lightweight_agent_events ("
            "run_id TEXT NOT NULL, sequence INTEGER NOT NULL, type TEXT NOT NULL, "
            "payload_json TEXT NOT NULL, PRIMARY KEY (run_id, sequence))"
        )
        connection.execute(
            "INSERT INTO lightweight_agent_runs VALUES (?, ?, ?)",
            (snapshot.run_id, snapshot.revision, json.dumps(legacy_snapshot)),
        )
        connection.execute(
            "INSERT INTO lightweight_agent_events VALUES (?, ?, ?, ?)",
            (snapshot.run_id, 1, "RUN_STARTED", "{}"),
        )


def test_sqlite_migration_is_transactional_and_resumable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "legacy.sqlite"
    _write_legacy_journal(path)
    migrations = journal_module._JOURNAL_MIGRATIONS

    def interrupted(connection: sqlite3.Connection) -> None:
        migrations[1][2](connection)
        raise RuntimeError("simulated interrupted upgrade")

    monkeypatch.setattr(
        journal_module,
        "_JOURNAL_MIGRATIONS",
        (migrations[0], (2, "interrupted", interrupted), *migrations[2:]),
    )
    with pytest.raises(RuntimeError, match="interrupted"):
        SQLiteRunJournal(path)
    with sqlite3.connect(path) as connection:
        versions = connection.execute(
            "SELECT version FROM lightweight_agent_journal_migrations ORDER BY version"
        ).fetchall()
    assert versions == [(1,)]

    monkeypatch.setattr(journal_module, "_JOURNAL_MIGRATIONS", migrations)
    journal = SQLiteRunJournal(path)

    assert journal.storage_info().schema_version == JOURNAL_STORAGE_VERSION
    restored = journal.get("legacy")
    assert restored.workflow["workflow_id"] == "inspection-workflow"
    assert restored.execution_id == "legacy"
    assert journal.events("legacy")[0].execution_id == "legacy"


@pytest.mark.parametrize(
    "mutation",
    [
        "INSERT INTO lightweight_agent_journal_migrations VALUES (8, 'future', 1)",
        "UPDATE lightweight_agent_journal_migrations SET name='other' WHERE version=3",
        "UPDATE lightweight_agent_journal_meta SET value='5' WHERE key='schema_version'",
        "DROP TABLE lightweight_agent_values",
        "DROP INDEX lightweight_agent_runs_query_filter",
        "CREATE TABLE lightweight_agent_unknown (value TEXT)",
    ],
)
def test_sqlite_open_rejects_unknown_or_damaged_schema_without_writing(
    tmp_path: Path, mutation: str
) -> None:
    path = tmp_path / "damaged.sqlite"
    SQLiteRunJournal(path)
    with sqlite3.connect(path) as connection:
        connection.execute(mutation)
        before = tuple(connection.iterdump())

    with pytest.raises(RunJournalError, match="local run journal"):
        SQLiteRunJournal(path)

    with sqlite3.connect(path) as connection:
        assert tuple(connection.iterdump()) == before


def test_sqlite_open_rejects_partial_legacy_schema_without_creating_ledger(
    tmp_path: Path,
) -> None:
    path = tmp_path / "partial.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE lightweight_agent_runs (run_id TEXT)")
        before = tuple(connection.iterdump())

    with pytest.raises(RunJournalError, match="unknown legacy schema"):
        SQLiteRunJournal(path)

    with sqlite3.connect(path) as connection:
        assert tuple(connection.iterdump()) == before


def test_journal_lifecycle_cli_is_a_thin_wrapper(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "cli.sqlite"
    SQLiteRunJournal(path).create(
        _snapshot("cli-complete", status="SUCCEEDED"), "RUN_SUCCEEDED", {}
    )
    SQLiteRunJournal(path).create(
        _snapshot("cli-live", status="RUNNING"), "RUN_STARTED", {}
    )
    backup = tmp_path / "cli.backup.sqlite"
    restored = tmp_path / "cli.restored.sqlite"

    assert main(["journal-info", str(path)]) == 0
    assert (
        json.loads(capsys.readouterr().out)["schema_version"] == JOURNAL_STORAGE_VERSION
    )
    assert main(["journal-space", str(path)]) == 0
    assert (
        json.loads(capsys.readouterr().out)["schema_version"] == "v6-journal-space-v1"
    )
    maintenance = tmp_path / "maintenance.sqlite"
    SQLiteRunJournal(maintenance)
    assert main(["journal-vacuum", str(maintenance)]) == 0
    assert (
        json.loads(capsys.readouterr().out)["schema_version"]
        == "v6-journal-vacuum-report-v1"
    )
    assert main(["journal-backup", str(path), str(backup)]) == 0
    assert backup.is_file()
    capsys.readouterr()
    assert main(["journal-restore", str(backup), str(restored)]) == 0
    assert json.loads(capsys.readouterr().out)["recovery_hold_run_ids"] == ["cli-live"]
    assert SQLiteRunJournal(restored).get("cli-complete").status == "SUCCEEDED"
    assert (
        main(
            [
                "journal-release-recovery",
                "cli-live",
                "--journal",
                str(restored),
                "--source-owner-stopped",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "RUNNING"
    capsys.readouterr()
    assert main(["journal-retain", str(path), "--archive-after-seconds", "0"]) == 0
    assert json.loads(capsys.readouterr().out)["archived_run_ids"] == ["cli-complete"]
