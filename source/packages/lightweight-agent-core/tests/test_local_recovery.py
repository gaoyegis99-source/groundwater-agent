from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path

import platform_lightweight_agent.recovery as recovery_module
import pytest
from platform_lightweight_agent import (
    ApprovalDecision,
    FileWorkspace,
    FunctionToolExecutor,
    LocalRecoveryError,
    NodeContract,
    RunSnapshot,
    RunRetentionPolicy,
    SQLiteRunJournal,
    ToolContext,
    ToolReference,
    ValueBinding,
    WorkflowEngine,
    approval_step,
    create_local_recovery_package,
    plan_local_recovery_package,
    preview_local_artifact_retention,
    restore_local_recovery_package,
    snapshot_file_references,
    tool_step,
    validate_local_recovery_package,
    workflow,
)
from platform_lightweight_agent.cli import main as cli_main


def _saved_snapshot(
    journal: SQLiteRunJournal,
    reference: dict[str, object],
    *,
    status: str = "SUCCEEDED",
    in_inputs: bool = False,
) -> None:
    journal.create(
        RunSnapshot(
            run_id="files-run",
            workflow={
                "schema_version": "v6-light-workflow-v1",
                "workflow_id": "files",
                "version": "1",
                "steps": [],
            },
            inputs={"file": reference} if in_inputs else {},
            state={"values": {}} if in_inputs else {"values": {"file": reference}},
            status=status,
            driver_bindings={},
        ),
        "RUN_STARTED",
        {},
    )


def test_local_recovery_round_trip_keeps_referenced_files(tmp_path: Path) -> None:
    workspace = FileWorkspace.under(tmp_path / "files")
    source = tmp_path / "input.txt"
    source.write_text("durable", encoding="utf-8")
    reference = workspace.stage_input(source, "documents/input.txt")
    journal = SQLiteRunJournal(tmp_path / "runs.sqlite")
    _saved_snapshot(journal, reference.to_mapping())

    package = create_local_recovery_package(
        journal, workspace, tmp_path / "local-recovery.zip"
    )
    restored_journal, restored_workspace = restore_local_recovery_package(
        package, tmp_path / "restored"
    )

    assert restored_journal.get("files-run").status == "SUCCEEDED"
    assert restored_workspace.resolve_input(reference).read_text(encoding="utf-8") == "durable"
    assert list((tmp_path / "restored" / "files" / "outputs").iterdir()) == []


def test_snapshot_inputs_are_protected_and_required_for_backup(tmp_path: Path) -> None:
    workspace = FileWorkspace.under(tmp_path / "files")
    source = tmp_path / "approval-input.txt"
    source.write_text("read after approval", encoding="utf-8")
    reference = workspace.stage_input(source, "approval/input.txt")
    journal = SQLiteRunJournal(tmp_path / "runs.sqlite")
    _saved_snapshot(
        journal,
        reference.to_mapping(),
        status="WAITING_APPROVAL",
        in_inputs=True,
    )

    assert snapshot_file_references(journal.get("files-run")) == (reference,)
    assert preview_local_artifact_retention(journal, workspace).protected == (reference,)
    package = create_local_recovery_package(journal, workspace, tmp_path / "backup.zip")
    restored_journal, restored_workspace = restore_local_recovery_package(
        package, tmp_path / "restored"
    )
    assert restored_journal.get("files-run").inputs["file"] == reference.to_mapping()
    assert restored_workspace.resolve_input(reference).read_text(encoding="utf-8") == (
        "read after approval"
    )

    wrong_workspace = FileWorkspace.under(tmp_path / "wrong-files")
    with pytest.raises(LocalRecoveryError, match="LOCAL_RECOVERY_ARTIFACT_MISSING"):
        create_local_recovery_package(
            journal, wrong_workspace, tmp_path / "must-not-exist.zip"
        )


def test_local_recovery_fails_closed_when_a_referenced_file_is_missing(
    tmp_path: Path,
) -> None:
    workspace = FileWorkspace.under(tmp_path / "files")
    source = tmp_path / "input.txt"
    source.write_text("durable", encoding="utf-8")
    reference = workspace.stage_input(source, "input.txt")
    journal = SQLiteRunJournal(tmp_path / "runs.sqlite")
    _saved_snapshot(journal, reference.to_mapping())
    workspace.resolve_input(reference).unlink()

    with pytest.raises(LocalRecoveryError, match="LOCAL_RECOVERY_ARTIFACT_MISSING"):
        create_local_recovery_package(journal, workspace, tmp_path / "backup.zip")


def test_retention_preview_protects_referenced_publication(tmp_path: Path) -> None:
    workspace = FileWorkspace.under(tmp_path / "files")
    output = workspace.action_output_path("action-1", "result.txt")
    output.write_text("published", encoding="utf-8")
    protected = workspace.publish_output("result.txt", action_id="action-1")
    extra = workspace.action_output_path("action-2", "extra.txt")
    extra.write_text("extra", encoding="utf-8")
    workspace.publish_output("extra.txt", action_id="action-2")
    journal = SQLiteRunJournal(tmp_path / "runs.sqlite")
    _saved_snapshot(journal, protected.to_mapping())
    retention = journal.apply_retention(
        RunRetentionPolicy(archive_after_seconds=0), now=time.time() + 1
    )
    assert retention.archived_run_ids == ("files-run",)

    preview = preview_local_artifact_retention(journal, workspace)

    assert preview.protected == (protected,)
    assert any(path.endswith("/extra.txt") for path in preview.unreferenced_paths)
    assert all(not path.endswith("/result.txt") for path in preview.unreferenced_paths)

    backup_journal = SQLiteRunJournal(tmp_path / "backup-runs.sqlite")
    extra_reference = workspace.published_output("action-2", "extra.txt")
    _saved_snapshot(backup_journal, extra_reference.to_mapping())
    backup = create_local_recovery_package(
        backup_journal, workspace, tmp_path / "protected-backup.zip"
    )
    with_backup = preview_local_artifact_retention(
        journal, workspace, backups=(backup,)
    )
    assert all(
        not path.endswith("/extra.txt") for path in with_backup.unreferenced_paths
    )


def test_local_recovery_rejects_tampered_artifact_before_publish(tmp_path: Path) -> None:
    workspace = FileWorkspace.under(tmp_path / "files")
    source = tmp_path / "input.txt"
    source.write_text("durable", encoding="utf-8")
    reference = workspace.stage_input(source, "input.txt")
    journal = SQLiteRunJournal(tmp_path / "runs.sqlite")
    _saved_snapshot(journal, reference.to_mapping())
    package = create_local_recovery_package(journal, workspace, tmp_path / "backup.zip")
    changed = tmp_path / "changed.zip"
    with zipfile.ZipFile(package) as original, zipfile.ZipFile(changed, "w") as target:
        for info in original.infolist():
            data = original.read(info.filename)
            if info.filename == "workspace/inputs/input.txt":
                data = b"tampered"
            target.writestr(info, data)

    with pytest.raises(LocalRecoveryError, match="digest mismatch"):
        restore_local_recovery_package(changed, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_restore_never_overwrites_an_existing_environment(tmp_path: Path) -> None:
    workspace = FileWorkspace.under(tmp_path / "files")
    source = tmp_path / "input.txt"
    source.write_text("durable", encoding="utf-8")
    reference = workspace.stage_input(source, "input.txt")
    journal = SQLiteRunJournal(tmp_path / "runs.sqlite")
    _saved_snapshot(journal, reference.to_mapping())
    package = create_local_recovery_package(journal, workspace, tmp_path / "backup.zip")
    existing = tmp_path / "existing-runtime"
    existing.mkdir()
    sentinel = existing / "must-survive.txt"
    sentinel.write_text("original", encoding="utf-8")

    with pytest.raises(LocalRecoveryError, match="OVERWRITE_UNSAFE"):
        restore_local_recovery_package(package, existing, overwrite=True)

    assert sentinel.read_text(encoding="utf-8") == "original"
    linked = tmp_path / "linked-runtime"
    linked.symlink_to(existing, target_is_directory=True)
    with pytest.raises(LocalRecoveryError, match="symlink"):
        restore_local_recovery_package(package, linked)
    assert sentinel.read_text(encoding="utf-8") == "original"


def test_final_publish_failure_leaves_package_and_no_partial_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = FileWorkspace.under(tmp_path / "files")
    source = tmp_path / "input.txt"
    source.write_text("durable", encoding="utf-8")
    reference = workspace.stage_input(source, "input.txt")
    journal = SQLiteRunJournal(tmp_path / "runs.sqlite")
    _saved_snapshot(journal, reference.to_mapping())
    package = create_local_recovery_package(journal, workspace, tmp_path / "backup.zip")
    destination = tmp_path / "new-runtime"
    original_replace = recovery_module.os.replace

    def fail_final(source_path, target_path):  # type: ignore[no-untyped-def]
        if Path(target_path) == destination:
            raise OSError("injected final publish failure")
        return original_replace(source_path, target_path)

    monkeypatch.setattr(recovery_module.os, "replace", fail_final)
    with pytest.raises(OSError, match="injected"):
        restore_local_recovery_package(package, destination)

    assert package.is_file()
    assert not destination.exists()
    monkeypatch.setattr(recovery_module.os, "replace", original_replace)
    restored_journal, restored_workspace = restore_local_recovery_package(
        package, destination
    )
    assert restored_journal.get("files-run").status == "SUCCEEDED"
    assert restored_workspace.resolve_input(reference).is_file()


def test_writer_uses_reader_capacity_rules_and_self_validates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = FileWorkspace.under(tmp_path / "files")
    source = tmp_path / "input.txt"
    source.write_text("larger than test threshold", encoding="utf-8")
    reference = workspace.stage_input(source, "input.txt")
    journal = SQLiteRunJournal(tmp_path / "runs.sqlite")
    _saved_snapshot(journal, reference.to_mapping())
    plan = plan_local_recovery_package(journal, workspace)
    assert plan.file_count >= 2
    assert plan.estimated_space_bytes > plan.total_size_bytes

    monkeypatch.setattr(recovery_module, "_MAX_ENTRY_BYTES", 8)
    with pytest.raises(LocalRecoveryError, match="entry is too large"):
        create_local_recovery_package(journal, workspace, tmp_path / "too-large.zip")
    assert not (tmp_path / "too-large.zip").exists()

    monkeypatch.setattr(recovery_module, "_MAX_ENTRY_BYTES", 256 * 1024 * 1024)
    package = create_local_recovery_package(journal, workspace, tmp_path / "valid.zip")
    validated = validate_local_recovery_package(package)
    assert validated.referenced_files == (reference,)


def test_input_file_approval_backup_restore_and_continue(tmp_path: Path) -> None:
    file_schema = {
        "type": "object",
        "properties": {
            "logical_id": {"type": "string"},
            "sha256": {"type": "string"},
            "size_bytes": {"type": "integer"},
        },
        "required": ["logical_id", "sha256", "size_bytes"],
        "additionalProperties": False,
    }

    def schema(properties, required=()):  # type: ignore[no-untyped-def]
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": properties,
            "required": list(required),
            "additionalProperties": False,
        }

    empty = schema({})
    approved = schema({"approved": {"type": "boolean"}}, ("approved",))
    file_input = schema({"file": file_schema}, ("file",))
    text_output = schema({"text": {"type": "string"}}, ("text",))
    reference = ToolReference("files.read", "1", "sha256:" + "f" * 64)
    spec = workflow(
        "file-approval-recovery",
        "1",
        (
            approval_step(
                "review", "Read the file?", contract=NodeContract(empty, approved, {})
            ),
            tool_step(
                "read",
                reference,
                approval_required=False,
                contract=NodeContract(
                    file_input,
                    text_output,
                    {"/file": ValueBinding("workflow_input", "/file")},
                ),
            ),
        ),
        schema_version="v6-light-workflow-v3",
        input_schema=file_input,
        output_schema=text_output,
        output_bindings={
            "/text": ValueBinding("step_output", "/text", "read")
        },
    )
    original_workspace = FileWorkspace.under(tmp_path / "original-files")
    source = tmp_path / "input.txt"
    source.write_text("available after approval", encoding="utf-8")
    staged = original_workspace.stage_input(source, "input.txt")
    original_journal = SQLiteRunJournal(tmp_path / "original.sqlite")

    def read(arguments, context: ToolContext):  # type: ignore[no-untyped-def]
        return {
            "text": context.files.resolve_input(arguments["file"]).read_text(
                encoding="utf-8"
            )
        }

    original_tools = FunctionToolExecutor(
        context_factory=lambda request, cancellation: ToolContext(
            request.action_id, cancellation, original_workspace
        )
    )
    original_tools.register(reference, read, implementation_version="files-read-v1")
    waiting = WorkflowEngine(original_journal, original_tools, {}).run(
        spec, {"file": staged.to_mapping()}, run_id="file-run"
    )
    assert waiting.status == "WAITING_APPROVAL"
    package = create_local_recovery_package(
        original_journal, original_workspace, tmp_path / "recovery.zip"
    )

    restored_journal, restored_workspace = restore_local_recovery_package(
        package, tmp_path / "restored"
    )
    released = restored_journal.release_backup_recovery(
        "file-run", source_owner_stopped=True
    )
    assert released.status == "WAITING_APPROVAL"
    tools = FunctionToolExecutor(
        context_factory=lambda request, cancellation: ToolContext(
            request.action_id, cancellation, restored_workspace
        )
    )

    tools.register(reference, read, implementation_version="files-read-v1")
    approval_id = next(iter(released.state["v3"]["pending_approvals"]))
    completed = WorkflowEngine(restored_journal, tools, {}).resume(
        "file-run", approvals=(ApprovalDecision(approval_id, True),)
    )

    assert completed.status == "SUCCEEDED"
    assert completed.state["v3"]["output"] == {"text": "available after approval"}


def test_local_recovery_cli_plan_check_and_restore(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = FileWorkspace.under(tmp_path / "files")
    source = tmp_path / "input.txt"
    source.write_text("cli recovery", encoding="utf-8")
    reference = workspace.stage_input(source, "input.txt")
    journal_path = tmp_path / "runs.sqlite"
    journal = SQLiteRunJournal(journal_path)
    _saved_snapshot(journal, reference.to_mapping())
    package = tmp_path / "backup.zip"

    assert cli_main(
        ["local-backup-plan", str(journal_path), str(tmp_path / "files")]
    ) == 0
    assert json.loads(capsys.readouterr().out)["scope"] == "full_workspace"
    assert cli_main(
        [
            "local-backup",
            str(journal_path),
            str(tmp_path / "files"),
            str(package),
        ]
    ) == 0
    capsys.readouterr()
    assert cli_main(["local-environment-check", str(package)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "compatible"
    assert cli_main(
        ["local-restore", str(package), str(tmp_path / "restored")]
    ) == 0
    assert "environment" in json.loads(capsys.readouterr().out)
