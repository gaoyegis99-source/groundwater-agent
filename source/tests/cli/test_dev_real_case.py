from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.development.dev as dev


def test_dev_real_case_uses_isolated_cache_and_verified_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cache = tmp_path / "cache" / "groundwater-v6"
    source_root = tmp_path / "workflow"
    source_root.mkdir()
    runtime_python = tmp_path / "native-runtime/bin/python"
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(dev, "activate", lambda **_kwargs: None)
    monkeypatch.setattr(dev, "lifecycle", lambda: SimpleNamespace(cache=cache))
    monkeypatch.setattr(dev, "ensure_private_roots", lambda *_args: None)
    monkeypatch.setattr(
        dev,
        "_verified_workflow_runtime_image",
        lambda _image: (
            "sha256:" + "a" * 64,
            {"defaultRuntimeMutation": "NONE", "network": "OFF"},
        ),
    )

    @contextmanager
    def native_runtime_python():
        yield (
            runtime_python,
            {
                "dependencyScope": "python",
                "defaultRuntimeMutation": "NONE",
            },
        )

    monkeypatch.setattr(dev, "_native_runtime_python", native_runtime_python)
    monkeypatch.setattr(
        dev,
        "_workflow_container_task_command",
        lambda *_args: ["docker", "image", "python"],
    )

    def run_real_cases(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {
            "schemaVersion": "real-case-run-result.v1",
            "status": "PASS",
            "selectedCases": ["local-monitoring-trend"],
            "runDirectory": str(kwargs["run_directory"]),
        }

    monkeypatch.setattr(dev, "run_real_cases", run_real_cases)

    dev.main(
        [
            "real-case",
            "--case",
            "local-monitoring-trend",
            "--source-root",
            str(source_root),
            "--runtime-image",
            "native:test",
        ]
    )

    assert len(calls) == 1
    call = calls[0]
    assert call["source_root"] == source_root
    assert call["source_cache"] == cache / "real-cases/sources"
    assert Path(str(call["run_directory"])).parent == cache / "real-cases/runs"
    assert call["python_executable"] == runtime_python
    assert call["task_command"] == ["docker", "image", "python"]
    assert json.loads(capsys.readouterr().out)["status"] == "PASS"
