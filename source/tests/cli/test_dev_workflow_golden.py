from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.development.dev as dev


def test_dev_workflow_golden_uses_xdg_cache_and_locked_native_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cache = tmp_path / "cache" / "groundwater-v6"
    paths = SimpleNamespace(cache=cache)
    calls: list[dict[str, object]] = []
    runtime_python = tmp_path / "native-runtime/bin/python"

    monkeypatch.setattr(dev, "activate", lambda **_kwargs: None)
    monkeypatch.setattr(dev, "lifecycle", lambda: paths)
    monkeypatch.setattr(dev, "ensure_private_roots", lambda *_args: None)

    @contextmanager
    def native_runtime_python():
        yield (
            runtime_python,
            {
                "dependencyScope": "python",
                "dependencyDigest": "sha256:fixture",
                "defaultRuntimeMutation": "NONE",
            },
        )

    monkeypatch.setattr(dev, "_native_runtime_python", native_runtime_python)

    def run_workflow_golden(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {
            "schemaVersion": "workflow-golden-run-result.v1",
            "status": "PASS",
            "caseId": kwargs["case_id"],
            "runDirectory": str(kwargs["run_directory"]),
        }

    monkeypatch.setattr(dev, "run_workflow_golden", run_workflow_golden)

    dev.main(["workflow-golden", "--case", "monitoring-trend"])

    assert len(calls) == 1
    call = calls[0]
    assert call["repository_root"] == dev.ROOT
    assert call["catalog_directory"] == (
        dev.ROOT / "packages/operator-pack/catalog/operators"
    )
    assert call["case_file"] == (
        dev.ROOT
        / "products/groundwater/development/workflow-golden-cases.v1.json"
    )
    assert call["baseline_file"] == (
        dev.ROOT
        / "products/groundwater/development/workflow-golden-baselines.v1.json"
    )
    assert call["case_id"] == "monitoring-trend"
    assert call["python_executable"] == runtime_python
    assert call["runtime_evidence"] == {
        "dependencyScope": "python",
        "dependencyDigest": "sha256:fixture",
        "defaultRuntimeMutation": "NONE",
    }
    run_directory = Path(str(call["run_directory"]))
    assert run_directory.parent == cache / "workflow-golden-runs"
    assert json.loads(capsys.readouterr().out)["status"] == "PASS"


def test_dev_workflow_golden_returns_stable_json_for_unknown_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts.development.workflow_golden import WorkflowGoldenRefusal

    paths = SimpleNamespace(cache=tmp_path / "cache" / "groundwater-v6")
    monkeypatch.setattr(dev, "activate", lambda **_kwargs: None)
    monkeypatch.setattr(dev, "lifecycle", lambda: paths)
    monkeypatch.setattr(dev, "ensure_private_roots", lambda *_args: None)

    @contextmanager
    def native_runtime_python():
        yield tmp_path / "python", {}

    monkeypatch.setattr(dev, "_native_runtime_python", native_runtime_python)
    monkeypatch.setattr(
        dev,
        "run_workflow_golden",
        lambda **_kwargs: (_ for _ in ()).throw(
            WorkflowGoldenRefusal("WORKFLOW_GOLDEN_CASE_NOT_FOUND")
        ),
    )

    with pytest.raises(SystemExit, match="2"):
        dev.main(["workflow-golden", "--case", "missing"])

    assert json.loads(capsys.readouterr().err) == {
        "code": "WORKFLOW_GOLDEN_CASE_NOT_FOUND",
        "status": "FAILED",
    }


def test_workflow_container_task_command_is_read_only_offline_and_pinned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "source"
    repository.mkdir()
    output_parent = tmp_path / "cache/workflow-golden-runs"
    output_parent.mkdir(parents=True)
    runtime = tmp_path / "build/native-1"
    python_distribution = tmp_path / "python/cpython-3.12.8"
    interpreter = python_distribution / "bin/python3.12"
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()
    python = runtime / "venv/bin/python"
    python.parent.mkdir(parents=True)
    python.symlink_to(interpreter)

    monkeypatch.setattr(dev, "ROOT", repository)
    monkeypatch.setattr(
        dev, "docker_arguments", lambda arguments: ["/project/bin/docker", *arguments]
    )

    command = dev._workflow_container_task_command(
        "sha256:" + "a" * 64,
        python,
        output_parent,
    )

    assert command[0] == "/project/bin/docker"
    assert command[-2:] == ["sha256:" + "a" * 64, str(python)]
    assert ["--pull", "never"] == command[command.index("--pull") :][:2]
    assert ["--network", "none"] == command[command.index("--network") :][:2]
    assert "--read-only" in command
    assert ["--cap-drop", "ALL"] == command[command.index("--cap-drop") :][:2]
    assert "no-new-privileges" in command
    assert "0:0" in command
    mounts = [
        command[index + 1] for index, item in enumerate(command) if item == "--mount"
    ]
    assert f"type=bind,src={repository},dst={repository},readonly" in mounts
    assert f"type=bind,src={runtime},dst={runtime},readonly" in mounts
    assert (
        f"type=bind,src={python_distribution},dst={python_distribution},readonly"
        in mounts
    )
    assert f"type=bind,src={output_parent},dst={output_parent}" in mounts
