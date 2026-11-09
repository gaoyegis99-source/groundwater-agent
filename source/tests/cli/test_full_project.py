from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from scripts.operations import test_full_project


def test_full_project_declares_all_release_wheels() -> None:
    projects = test_full_project._wheel_projects()

    assert len(projects) == 13
    assert all((path / "pyproject.toml").is_file() for path in projects.values())


def test_full_project_classifies_missing_prerequisite() -> None:
    assert (
        test_full_project._failure_class("NOT_RUN: missing prerequisites: Docker", 1)
        == "BLOCKED[environment]"
    )
    assert (
        test_full_project._failure_class("OSERROR: no such file or directory", 127)
        == "BLOCKED[environment]"
    )
    assert (
        test_full_project._failure_class("assertion failed", 1)
        == "SOURCE_OR_TEST_FAILURE"
    )
    assert test_full_project._failure_class("", 0) == "NONE"


def test_full_project_records_pytest_skip_reasons() -> None:
    output = "================ short test summary info ================\nSKIPPED [2] services/knowledge-next/tests/test_scope.py: requires isolated database\n"
    assert test_full_project._pytest_skip_reasons(output) == [
        "services/knowledge-next/tests/test_scope.py: requires isolated database"
    ]


@pytest.mark.parametrize("stage_name", ["python_contracts", "native_operator_suite"])
def test_applicable_contract_and_native_skips_fail_stage(
    tmp_path: Path, monkeypatch, stage_name: str
) -> None:
    monkeypatch.setattr(
        test_full_project.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, "77 passed, 1 skipped in 1.00s\n", ""
        ),
    )
    runner = test_full_project.Runner(
        product="groundwater", run_id="skip123", output=tmp_path / "report.json"
    )
    stage = test_full_project.StageResult(name=stage_name, status="RUNNING")
    runner._stage = stage
    with pytest.raises(RuntimeError, match="APPLICABLE_TEST_SKIPPED:1"):
        runner.command(["pytest"])
    assert stage.commands[0].test_summary["skipped"] == 1


def test_full_project_snapshots_final_compose_output(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(test_full_project, "ROOT", tmp_path)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(
        test_full_project.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, "compose PASS\n", ""
        ),
    )
    runner = test_full_project.Runner(
        product="groundwater", run_id="compose123", output=tmp_path / "report.json"
    )
    stage = test_full_project.StageResult(name="final_compose", status="RUNNING")
    runner.stages.append(stage)
    runner._stage = stage
    runner.command(
        ["python", "scripts/checks/check_compose_e2e.py", "--product", "groundwater"]
    )
    log = (
        test_full_project.validation_cache_root(tmp_path)
        / "full-project-e2e/compose123/final-compose.log"
    )
    assert log.read_text(encoding="utf-8") == "compose PASS\n"
    assert stage.commands[0].output_log == str(log)


def test_full_project_tree_digest_is_order_independent(tmp_path: Path) -> None:
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")

    digest = test_full_project._sha256_tree(tmp_path)

    (tmp_path / "a.txt").unlink()
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    assert test_full_project._sha256_tree(tmp_path) == digest


def test_full_project_status_digest_is_stable() -> None:
    status = [" M a.py", "?? evidence.json"]
    assert test_full_project._status_sha256(status) == test_full_project._status_sha256(
        list(status)
    )


def test_full_project_snapshots_evidence_with_checksums(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(test_full_project, "ROOT", tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    source = tmp_path / "generated.json"
    source.write_text('{"status":"PASS"}\n', encoding="utf-8")

    snapshot = test_full_project._snapshot_evidence(
        "snapshot123",
        {"joint.json": source},
    )

    copied = Path(snapshot["files"]["joint.json"]["snapshot"])
    assert snapshot["status"] == "PASS"
    assert copied.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    assert snapshot["files"]["joint.json"]["sha256"] == test_full_project._sha256_file(
        copied
    )
    assert Path(snapshot["manifest"]).is_file()


def test_full_project_redacts_sensitive_log_payloads(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(test_full_project, "ROOT", tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    source = tmp_path / "compose.log"
    source.write_text(
        'task {"status":"PASS","message":"secret text",'
        '"session_id":"12345678-1234-4234-8234-123456789abc",'
        '"request_id":"0123456789abcdef0123456789abcdef"}\n'
        "api | GET /data/v3/datasets?query=private.csv HTTP/1.1 200 OK\n",
        encoding="utf-8",
    )

    snapshot = test_full_project._snapshot_evidence(
        "redact123", {"compose.log": source}
    )
    copied = Path(snapshot["files"]["compose.log"]["snapshot"])
    text = copied.read_text(encoding="utf-8")
    assert "secret text" not in text
    assert "private.csv" not in text
    assert "12345678-1234-4234-8234-123456789abc" not in text
    assert "0123456789abcdef0123456789abcdef" not in text
    assert '"status":"PASS"' in text
    assert snapshot["files"]["compose.log"]["redacted"] is True
    assert snapshot["files"]["compose.log"]["sourceSha256"]


def test_full_project_restores_completed_checkpoint(tmp_path: Path) -> None:
    runner = test_full_project.Runner(
        product="groundwater",
        run_id="checkpoint123",
        output=tmp_path / "report.json",
    )
    runner.load_report(
        {
            "schemaVersion": "full-project-test-report.v1",
            "stages": [{"name": "assembly", "status": "PASS", "commands": []}],
        }
    )

    assert runner.completed("assembly")
    assert not runner.completed("python_kernel")

    runner.force_stages.add("assembly")
    assert not runner.completed("assembly")


def test_full_project_selects_an_available_wheel_builder() -> None:
    tool = test_full_project._build_tool()

    assert tool == ["uv", "build"]
    assert test_full_project._build_check_command()


def test_full_project_keeps_failed_stage_history_before_resume(tmp_path: Path) -> None:
    runner = test_full_project.Runner(
        product="groundwater",
        run_id="history12345",
        output=tmp_path / "report.json",
    )
    failed = test_full_project.StageResult(
        name="wheels",
        status="FAIL",
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:01:00+00:00",
        failure={"failureClass": "SOURCE_OR_TEST_FAILURE"},
        commands=[
            test_full_project.CommandResult(
                command=["python", "-m", "check"],
                return_code=1,
                duration_seconds=1.0,
                failure_class="SOURCE_OR_TEST_FAILURE",
                diagnostic_codes=["RELEASE_PYTHON_3_12_REQUIRED"],
            )
        ],
    )
    runner.stages.append(failed)
    runner.replace_stage("wheels")

    assert runner.history[0]["stage"] == "wheels"
    assert runner.history[0]["commands"][0]["diagnosticCodes"] == [
        "RELEASE_PYTHON_3_12_REQUIRED"
    ]
