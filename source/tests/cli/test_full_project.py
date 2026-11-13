from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.operations import test_full_project
from scripts.operations.render_full_project_report import (
    render_full_project_report,
    write_markdown_report,
)


@pytest.mark.parametrize("changed", ["tracked", "untracked", "legacy", "prior-drift", "unchanged"])
def test_full_project_refuses_checkpoint_without_matching_source(tmp_path, monkeypatch, changed):
    root = tmp_path / "source"
    root.mkdir()

    def git(*args):
        subprocess.run(["git", *args], cwd=root, capture_output=True, check=True)

    git("init", "-q")
    (root / "model.py").write_text("value = 1\n", encoding="utf-8")
    git("add", "model.py")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture")
    edited = root / ("model.py" if changed == "tracked" else "new_model.py")
    edited.write_text("value = 2\n", encoding="utf-8")
    monkeypatch.setattr(test_full_project, "ROOT", root)
    output = root / "prior.json"
    head, digest = test_full_project._validation_source(output)
    baseline = {"head": head, "startedAt": "2026-11-12T00:00:00Z"}
    if changed != "legacy":
        baseline["sourceDigest"] = digest
    checkpoint = json.dumps({
        "schemaVersion": "full-project-test-report.v1", "product": "groundwater",
        "runId": "00aabbccddee", "baseline": baseline,
        "sourceVerification": {"status": "FAIL"} if changed == "prior-drift" else {},
    })
    output.write_text(checkpoint, encoding="utf-8")
    output.with_suffix(".md").write_text("generated summary", encoding="utf-8")
    if changed in {"tracked", "untracked"}:
        edited.write_text("value = 3\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["validate", "--resume", "--output", str(output)])
    if changed == "unchanged":
        class ReachedRunnerError(Exception):
            pass

        def reached(**kwargs):
            assert kwargs["run_id"] == "00aabbccddee"
            raise ReachedRunnerError

        monkeypatch.setattr(test_full_project, "Runner", reached)
        with pytest.raises(ReachedRunnerError):
            test_full_project.main()
        return
    monkeypatch.setattr(test_full_project, "Runner", lambda **kwargs: pytest.fail("stale checkpoint reached runner"))
    with pytest.raises(SystemExit) as error:
        test_full_project.main()
    assert error.value.code == 2
    assert output.read_text(encoding="utf-8") == checkpoint


def test_full_project_rejects_source_drift_between_stages(tmp_path, monkeypatch):
    output = tmp_path / "report.json"
    identity = ["head", "initial"]
    monkeypatch.setattr(test_full_project, "_validation_source", lambda output: tuple(identity))
    runner = test_full_project.Runner(product="groundwater", run_id="drift", output=output)
    baseline = {"head": "head", "sourceDigest": "initial", "startedAt": "2026-11-12T00:00:00Z"}
    extra = {}
    runner.checkpoint(baseline=baseline, extra=extra)
    identity[1] = "changed"
    with pytest.raises(RuntimeError, match="VALIDATION_SOURCE_CHANGED"):
        runner.checkpoint(baseline=baseline, extra=extra)
    assert json.loads(output.read_text())["sourceVerification"]["status"] == "PASS"
    identity[1] = "initial"
    assert not runner.verify_source(baseline=baseline, extra=extra)
    assert extra["sourceVerification"]["reason"] == "VALIDATION_SOURCE_CHANGED"


def test_full_project_snapshots_only_this_attempts_evidence(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    output = tmp_path / "report.json"
    stale = cache / "data-knowledge-e2e-current.json"
    stale.write_text('{"status":"STALE"}', encoding="utf-8")
    seen = []
    fail_joint = False

    def stage(self, name, commands):
        if name in ("data_knowledge_joint", "final_compose"):
            command, = commands
            flag = "--output" if name == "data_knowledge_joint" else "--webui-report"
            path = Path(command[command.index(flag) + 1])
            assert path != stale
            seen.append((name, path))
            if name == "data_knowledge_joint":
                if fail_joint:
                    self.replace_stage(name)
                    self.stages.append(test_full_project.StageResult(name=name, status="FAIL"))
                    raise RuntimeError("simulated failure before evidence is written")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('{"status":"PASS"}', encoding="utf-8")
        self.replace_stage(name)
        self.stages.append(test_full_project.StageResult(name=name, status="PASS"))

    monkeypatch.setattr(test_full_project, "validation_cache_root", lambda root: cache)
    monkeypatch.setattr(test_full_project, "_validation_source", lambda output: ("head", "digest"))
    monkeypatch.setattr(test_full_project, "_version", lambda args: "test")
    monkeypatch.setattr(test_full_project, "_build_check_command", lambda: ["uv", "--help"])
    monkeypatch.setattr(test_full_project, "_compile_digests", lambda runner: (["digest"], {}))
    monkeypatch.setattr(test_full_project, "_build_and_check_wheels", lambda runner: None)
    monkeypatch.setattr(test_full_project.Runner, "stage", stage)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(sys, "argv", ["validate", "--output", str(output)])
    assert test_full_project.main() == 0
    first_joint, first_compose = (path for _, path in seen)
    monkeypatch.setattr(sys, "argv", ["validate", "--output", str(output), "--resume", "--rerun-stage", "data_knowledge_joint"])
    assert test_full_project.main() == 0
    assert len(seen) == 3
    assert seen[-1][1] != first_joint
    report = json.loads(output.read_text(encoding="utf-8"))
    files = report["evidenceSnapshot"]["files"]
    assert files["data-knowledge-e2e-current.json"]["source"] == str(seen[-1][1])
    assert report["evidence"]["webUi"] == str(first_compose / "results.json")
    assert stale.read_text(encoding="utf-8") == '{"status":"STALE"}'

    fail_joint = True
    assert test_full_project.main() == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    missing = report["evidenceSnapshot"]["files"]["data-knowledge-e2e-current.json"]
    assert missing == {"source": str(seen[-1][1]), "status": "MISSING"}
    assert len({path for name, path in seen if name == "data_knowledge_joint"}) == 3


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


def test_full_project_pytest_attempts_use_distinct_owned_trees(tmp_path, monkeypatch):
    paths = []

    def run(command, **kwargs):
        directory = Path(command[command.index("--basetemp") + 1])
        assert directory.parent.is_dir() and not directory.exists()
        directory.mkdir()
        (directory / "fixture").write_text("synthetic", encoding="utf-8")
        paths.append(directory)
        return subprocess.CompletedProcess(command, 0, "1 passed in 1.00s\n", "")

    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    monkeypatch.setattr(test_full_project.subprocess, "run", run)
    runner = test_full_project.Runner(product="groundwater", run_id="temp123", output=tmp_path / "report.json")
    command = [test_full_project.PYTHON, "-m", "pytest", "-q", "tests/contracts"]
    runner.stage("python_contracts", [command])
    runner.stage("python_contracts", [command])
    assert len(set(paths)) == 2
    assert all(not directory.parent.exists() for directory in paths)
    assert runner.stages[0].commands[0].test_summary == {"passed": 1}


def test_full_project_keeps_test_counts_when_temporary_cleanup_fails(tmp_path, monkeypatch):
    @contextmanager
    def failing_cleanup(command):
        yield command
        raise PermissionError("synthetic denied cleanup")

    monkeypatch.setattr(test_full_project, "isolated_pytest_command", failing_cleanup)
    monkeypatch.setattr(test_full_project.subprocess, "run", lambda command, **kwargs: (
        subprocess.CompletedProcess(command, 0, "1 passed in 1.00s\n", "")
    ))
    runner = test_full_project.Runner(product="groundwater", run_id="temp-failure", output=tmp_path / "report.json")
    command = [test_full_project.PYTHON, "-m", "pytest", "-q", "tests/contracts"]
    with pytest.raises(PermissionError, match="synthetic denied cleanup"):
        runner.stage("python_contracts", [command])
    stage, = runner.stages
    assert stage.status == "FAIL"
    assert stage.failure["failureClass"] == "VALIDATION_STAGE_IO_FAILURE"
    assert stage.commands[0].test_summary == {"passed": 1}


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


def test_full_project_builds_wheels_from_clean_source_snapshot(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text("[build-system]\n", encoding="utf-8")
    (project / "current.py").write_text("CURRENT = True\n", encoding="utf-8")
    generated = {
        project / "build/lib/deleted.py": "DELETED = True\n",
        project / "dist/stale.whl": "stale\n",
        project / "__pycache__/current.cpython-313.pyc": "stale\n",
        project / "current.egg-info/SOURCES.txt": "stale\n",
        project / "node_modules/package/index.js": "stale\n",
    }
    for path, content in generated.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    snapshot = test_full_project._prepare_wheel_build_input(
        project, tmp_path / "snapshot"
    )

    assert (snapshot / "pyproject.toml").is_file()
    assert (snapshot / "current.py").is_file()
    assert not any(
        (snapshot / relative).exists()
        for relative in (
            "build",
            "dist",
            "__pycache__",
            "current.egg-info",
            "node_modules",
        )
    )


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


def test_full_project_markdown_report_summarizes_without_command_payloads(
    tmp_path: Path,
) -> None:
    report = {
        "schemaVersion": "full-project-test-report.v1",
        "status": "PASS",
        "product": "groundwater",
        "runId": "report123",
        "startedAt": "2026-01-01T00:00:00Z",
        "finishedAt": "2026-01-01T00:01:00Z",
        "engineeringStatus": "PASS",
        "scientificStatus": "NOT_ASSESSED",
        "agentExperienceStatus": "NOT_ASSESSED",
        "baseline": {"versions": {"python": "Python 3.12.8"}},
        "stages": [
            {
                "name": "python_kernel",
                "status": "PASS",
                "commands": [
                    {
                        "command": ["pytest", "--password=must-not-render"],
                        "returnCode": 0,
                        "durationSeconds": 1.25,
                        "testSummary": {"passed": 10},
                    }
                ],
            }
        ],
        "isolation": {
            "policy": "FRESH_VOLUMES",
            "existingDeploymentMutation": False,
        },
        "evidenceSnapshot": {"status": "PASS", "files": {"a": {}}},
    }
    markdown = render_full_project_report(report)
    assert "总状态：`PASS`" in markdown
    assert "passed=10" in markdown
    assert "must-not-render" not in markdown

    source = tmp_path / "report.json"
    source.write_text(json.dumps(report), encoding="utf-8")
    destination = write_markdown_report(source)
    assert destination == tmp_path / "report.md"
    assert destination.stat().st_mode & 0o777 == 0o600
