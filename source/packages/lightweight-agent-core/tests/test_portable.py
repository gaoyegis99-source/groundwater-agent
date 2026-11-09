from __future__ import annotations

import zipfile
from pathlib import Path

import platform_lightweight_agent.portable as portable_module
import pytest
from platform_lightweight_agent import (
    BundleTarget,
    FileWorkspace,
    ToolReference,
    WorkflowValidationError,
    bundle_file_references,
    export_bundle,
    import_bundle,
    read_bundle,
    tool_step,
    validate_bundle_target,
    workflow,
)

TOOL = ToolReference("demo.file", "2.0.0", "sha256:" + "2" * 64)


def _workflow():
    return workflow(
        "portable",
        "2",
        [tool_step("file", TOOL, {"source": {"$ref": "/input"}})],
        schema_version="v6-light-workflow-v1",
    )


def test_export_import_moves_only_versioned_metadata_and_explicit_files(
    tmp_path: Path,
) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "sample.txt").write_text("portable input\n", encoding="utf-8")
    output = tmp_path / "workflow.v6bundle"
    bundle = export_bundle(
        output,
        _workflow(),
        tools=[{**TOOL.to_mapping(), "display_name": "File tool"}],
        dependencies={"tool-package": "2.0.0", "python": "3.12"},
        files_root=inputs,
        files=["sample.txt"],
    )

    assert bundle.workflow.definition_digest
    target = tmp_path / "imported"
    imported = import_bundle(output, target)
    assert imported.workflow.to_mapping() == _workflow().to_mapping()
    assert imported.files["sample.txt"].startswith("sha256:")
    assert (target / "files" / "sample.txt").read_text(
        encoding="utf-8"
    ) == "portable input\n"
    # Extraction alone is not a runtime path contract.  The host explicitly
    # projects the verified bundle manifest into a confined run workspace.
    workspace = FileWorkspace.under(tmp_path / "run-files")
    staged = workspace.stage_declared_inputs(
        target / "files", bundle_file_references(imported.manifest)
    )
    assert (
        workspace.resolve_input(staged["sample.txt"]).read_text(encoding="utf-8")
        == "portable input\n"
    )
    assert not (target / "checkpoint.json").exists()


def test_bundle_rejects_credentials_and_nonempty_target(tmp_path: Path) -> None:
    with pytest.raises(WorkflowValidationError, match="credentials"):
        export_bundle(
            tmp_path / "bad.zip",
            _workflow(),
            tools=[TOOL.to_mapping()],
            dependencies={"api_key": "abc"},
        )

    archive = tmp_path / "bundle.zip"
    export_bundle(archive, _workflow(), tools=[TOOL.to_mapping()])
    target = tmp_path / "used"
    target.mkdir()
    (target / "old.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(WorkflowValidationError, match="empty"):
        import_bundle(archive, target)
    assert (target / "old.txt").read_text(encoding="utf-8") == "keep"


def test_failed_export_preserves_a_preexisting_valid_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed staging write must never truncate the last usable bundle."""

    archive = tmp_path / "portable.v6bundle"
    export_bundle(archive, _workflow(), tools=[TOOL.to_mapping()])
    previous = archive.read_bytes()

    def fail_after_partial_write(path: Path, _entries: object) -> None:
        path.write_bytes(b"partial archive")
        raise OSError("injected write failure")

    monkeypatch.setattr(
        portable_module, "_write_archive_contents", fail_after_partial_write
    )
    with pytest.raises(WorkflowValidationError, match="could not be written"):
        export_bundle(archive, _workflow(), tools=[TOOL.to_mapping()])

    assert archive.read_bytes() == previous
    assert (
        read_bundle(archive).workflow.definition_digest == _workflow().definition_digest
    )
    assert not list(tmp_path.glob(".portable.v6bundle.v6-export-*"))


def test_archive_member_limit_counts_required_metadata_and_input_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Writer and reader share one member budget, including manifest entries."""

    monkeypatch.setattr(portable_module, "_MAX_ARCHIVE_MEMBERS", 4)
    archive = tmp_path / "metadata-only.v6bundle"
    export_bundle(archive, _workflow(), tools=[TOOL.to_mapping()])
    assert read_bundle(archive).workflow.workflow_id == "portable"
    with zipfile.ZipFile(archive, "a") as edited:
        edited.writestr("files/late-input.txt", b"late")
    with pytest.raises(WorkflowValidationError, match="too many archive members"):
        read_bundle(archive)

    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "input.txt").write_text("content", encoding="utf-8")
    with pytest.raises(WorkflowValidationError, match="too many archive members"):
        export_bundle(
            tmp_path / "over-limit.v6bundle",
            _workflow(),
            tools=[TOOL.to_mapping()],
            files_root=inputs,
            files=("input.txt",),
        )


def test_archive_member_validation_rejects_non_adjacent_parent_child_paths() -> None:
    """A lexical neighbour must not hide a file/directory namespace collision."""

    with pytest.raises(WorkflowValidationError, match="members overlap"):
        portable_module._validate_member_names(("a", "a-unrelated", "a/child"))


def test_import_rejects_tampered_file_content(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "sample.txt").write_text("original", encoding="utf-8")
    source = tmp_path / "source.zip"
    export_bundle(
        source,
        _workflow(),
        tools=[TOOL.to_mapping()],
        files_root=inputs,
        files=["sample.txt"],
    )
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(tampered, "w") as edited:
        for info in original.infolist():
            data = original.read(info.filename)
            if info.filename == "files/sample.txt":
                data = b"changed"
            edited.writestr(info, data)
    with pytest.raises(WorkflowValidationError, match="digest"):
        import_bundle(tampered, tmp_path / "target")


def test_bundle_target_requires_exact_tool_permission_and_credential_binding(
    tmp_path: Path,
) -> None:
    source = tmp_path / "portable.v6bundle"
    bundle = export_bundle(
        source,
        _workflow(),
        tools=[
            {
                **TOOL.to_mapping(),
                "permissions": ["filesystem.read"],
                "credential_ref": "source_binding",
            }
        ],
    )
    target = BundleTarget(
        trusted_tools=(TOOL,),
        allowed_permissions=frozenset({"filesystem.read"}),
        credential_bindings=frozenset({"source_binding"}),
    )
    validate_bundle_target(bundle, target)
    assert (
        import_bundle(
            source,
            tmp_path / "checked-import",
            target_capabilities=target,
        ).workflow.workflow_id
        == "portable"
    )

    with pytest.raises(WorkflowValidationError, match="credential"):
        import_bundle(
            source,
            tmp_path / "rejected-import",
            target_capabilities=BundleTarget(
                trusted_tools=(TOOL,),
                allowed_permissions=frozenset({"filesystem.read"}),
            ),
        )
    assert not (tmp_path / "rejected-import").exists()


def test_export_requires_agent_and_tool_references_in_the_bundle(
    tmp_path: Path,
) -> None:
    with pytest.raises(WorkflowValidationError, match="declare"):
        export_bundle(tmp_path / "missing-tools.v6bundle", _workflow())


def test_bundle_rejects_source_and_destination_symlinks(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    real = inputs / "real.txt"
    real.write_text("value", encoding="utf-8")
    linked = inputs / "linked.txt"
    linked.symlink_to(real.name)

    with pytest.raises(WorkflowValidationError, match="non-symlink"):
        export_bundle(
            tmp_path / "linked-source.zip",
            _workflow(),
            tools=[TOOL.to_mapping()],
            files_root=inputs,
            files=["linked.txt"],
        )

    archive = tmp_path / "bundle.zip"
    export_bundle(archive, _workflow(), tools=[TOOL.to_mapping()])
    actual_target = tmp_path / "actual-target"
    actual_target.mkdir()
    linked_target = tmp_path / "linked-target"
    linked_target.symlink_to(actual_target, target_is_directory=True)
    with pytest.raises(WorkflowValidationError, match="must not be a symlink"):
        import_bundle(archive, linked_target)
    assert list(actual_target.iterdir()) == []
