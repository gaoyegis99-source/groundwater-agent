from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
from platform_lightweight_agent import (
    FileReference,
    FileWorkspace,
    WorkflowValidationError,
    bundle_file_references,
)


def test_workspace_stages_verified_inputs_and_records_confined_outputs(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("aquifer", encoding="utf-8")
    previous_cwd = Path.cwd()
    workspace = FileWorkspace.under(tmp_path / "workspace")

    input_reference = workspace.stage_input(source, "datasets/aquifer.txt")

    assert Path.cwd() == previous_cwd
    assert workspace.resolve_input(input_reference).read_text(encoding="utf-8") == "aquifer"
    assert FileReference.from_mapping(input_reference.to_mapping()) == input_reference

    output = workspace.output_path("reports/answer.json")
    output.write_text('{"answer":"safe"}', encoding="utf-8")
    output_reference = workspace.record_output("reports/answer.json")

    assert output_reference.logical_id == "reports/answer.json"
    assert workspace.output_manifest() == (output_reference,)
    assert workspace.resolve_input(output_reference).read_text(encoding="utf-8") == (
        '{"answer":"safe"}'
    )


def test_workspace_publishes_action_isolated_immutable_outputs(tmp_path: Path) -> None:
    workspace = FileWorkspace.under(tmp_path / "workspace")
    first = workspace.action_output_path("action-first", "reports/result.txt")
    second = workspace.action_output_path("action-second", "reports/result.txt")
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")

    first_reference = workspace.record_output(
        "reports/result.txt", action_id="action-first"
    )
    second_reference = workspace.record_output(
        "reports/result.txt", action_id="action-second"
    )

    assert first_reference.logical_id == second_reference.logical_id
    assert first_reference.sha256 != second_reference.sha256
    assert workspace.resolve_input(first_reference).read_text(encoding="utf-8") == "first"
    assert workspace.resolve_input(second_reference).read_text(encoding="utf-8") == "second"
    assert workspace.published_output("action-first", "reports/result.txt") == first_reference
    assert workspace.published_manifest() == (first_reference, second_reference)
    assert [item.logical_id for item in workspace.staging_manifest()] == [
        "action-first/reports/result.txt",
        "action-second/reports/result.txt",
    ]

    # The immutable publication is a copy, so a stale staging path cannot
    # mutate a value already handed to another step.
    first.write_text("mutated staging", encoding="utf-8")
    assert workspace.resolve_published(first_reference).read_text(encoding="utf-8") == "first"
    with pytest.raises(WorkflowValidationError, match="already reserved"):
        workspace.action_output_path("action-first", "reports/result.txt")

    # A restart that reaches the same action can use its completed local
    # publication even if the temporary action directory has disappeared.
    first.unlink()
    assert (
        workspace.publish_output("reports/result.txt", action_id="action-first")
        == first_reference
    )


def test_formal_manifest_never_treats_staging_as_a_published_artifact(
    tmp_path: Path,
) -> None:
    workspace = FileWorkspace.under(tmp_path / "workspace")
    finished = workspace.action_output_path("action-done", "same/result.txt")
    unfinished = workspace.action_output_path("action-half", "same/result.txt")
    finished.write_text("published", encoding="utf-8")
    unfinished.write_text("partial", encoding="utf-8")
    reference = workspace.publish_output(
        "same/result.txt", action_id="action-done"
    )

    assert workspace.output_manifest() == (reference,)
    assert len(workspace.staging_manifest()) == 2

    # Staging may be mutated after publication; the formal manifest continues
    # to describe the immutable indexed artifact and never the half-result.
    finished.write_text("later scratch data", encoding="utf-8")
    unfinished.write_text("still incomplete", encoding="utf-8")
    assert workspace.published_manifest() == (reference,)
    assert all(
        item.sha256 != reference.sha256 for item in workspace.staging_manifest()
    )


def test_unindexed_content_blob_is_not_a_formal_output(tmp_path: Path) -> None:
    workspace = FileWorkspace.under(tmp_path / "workspace")
    staging = workspace.action_output_path("crashed-action", "result.txt")
    staging.write_text("complete bytes without publication fact", encoding="utf-8")
    reference = FileReference(
        "result.txt",
        "sha256:" + hashlib.sha256(staging.read_bytes()).hexdigest(),
        staging.stat().st_size,
    )
    orphan = workspace._published_path(reference, create_parent=True)
    orphan.write_bytes(staging.read_bytes())

    assert workspace.published_manifest() == ()
    assert workspace.staging_manifest()


def test_workspace_legacy_output_name_cannot_be_silently_reused(tmp_path: Path) -> None:
    workspace = FileWorkspace.under(tmp_path / "workspace")
    output = workspace.output_path("report.txt")
    output.write_text("published", encoding="utf-8")
    reference = workspace.record_output("report.txt")

    with pytest.raises(WorkflowValidationError, match="already reserved"):
        workspace.output_path("report.txt")
    assert workspace.resolve_input(reference).read_text(encoding="utf-8") == "published"


def test_workspace_rejects_ambient_paths_tampering_and_symlink_escape(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("first", encoding="utf-8")
    workspace = FileWorkspace.under(tmp_path / "workspace")
    reference = workspace.stage_input(source, "input.txt")

    for bad in ("../outside", "/absolute", "folder//file", "folder\\file"):
        with pytest.raises(WorkflowValidationError, match="logical_id"):
            FileReference(bad, "sha256:" + "a" * 64, 0)

    resolved = workspace.resolve_input(reference)
    resolved.write_text("changed", encoding="utf-8")
    with pytest.raises(WorkflowValidationError, match="no longer matches"):
        workspace.resolve_input(reference)
    with pytest.raises(WorkflowValidationError, match="logical_id"):
        workspace.output_path("../outside.txt")

    target = tmp_path / "outside.txt"
    target.write_text("outside", encoding="utf-8")
    linked = workspace.input_root / "linked.txt"
    try:
        linked.symlink_to(target)
    except OSError as error:  # pragma: no cover - platform without symlink support
        pytest.skip(f"symlinks unavailable: {error}")
    with pytest.raises(WorkflowValidationError, match="unavailable"):
        workspace.resolve_input(
            FileReference("linked.txt", "sha256:" + "b" * 64, 7)
        )


def test_workspace_does_not_depend_on_garbage_collection_or_current_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.txt"
    source.write_bytes(b"stable")
    workspace = FileWorkspace.under(tmp_path / "workspace")
    reference = workspace.stage_input(source, "stable.bin")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    assert os.fspath(workspace.resolve_input(reference)).startswith(
        os.fspath(workspace.input_root)
    )


def test_workspace_stages_imported_bundle_files_by_manifest_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    imported = tmp_path / "imported-project"
    source = imported / "data" / "sample.json"
    source.parent.mkdir(parents=True)
    content = b'{"aquifer":"north"}\n'
    source.write_bytes(content)
    manifest = {
        "files": {
            "data/sample.json": {
                "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
            }
        }
    }
    references = bundle_file_references(manifest)
    workspace = FileWorkspace.under(tmp_path / "run-files")

    staged = workspace.stage_declared_inputs(imported, references)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    assert staged == references
    assert workspace.resolve_input(staged["data/sample.json"]).read_bytes() == content

    source.write_text("changed", encoding="utf-8")
    with pytest.raises(WorkflowValidationError, match="bundle identity"):
        FileWorkspace.under(tmp_path / "second-run-files").stage_declared_inputs(
            imported, references
        )


def test_bundle_file_references_rejects_incomplete_or_unsafe_metadata() -> None:
    content = b"v2 payload"
    assert bundle_file_references(
        {
            "entries": {
                "workflow.json": {"sha256": "sha256:" + "a" * 64, "bytes": 2},
                "files/data/input.txt": {
                    "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
                    "bytes": len(content),
                },
            }
        }
    ) == {
        "data/input.txt": FileReference(
            "data/input.txt", "sha256:" + hashlib.sha256(content).hexdigest(), len(content)
        )
    }
    with pytest.raises(WorkflowValidationError, match="bundle file manifest"):
        bundle_file_references({"files": {"data/sample.json": {"sha256": "bad"}}})
    with pytest.raises(WorkflowValidationError, match="bundle file manifest"):
        bundle_file_references(
            {
                "entries": {
                    1: {"sha256": "sha256:" + "a" * 64, "bytes": 0},
                }
            }
        )
    with pytest.raises(WorkflowValidationError, match="logical_id"):
        bundle_file_references(
            {
                "files": {
                    "../escape": {
                        "sha256": "sha256:" + "a" * 64,
                        "bytes": 0,
                    }
                }
            }
        )
