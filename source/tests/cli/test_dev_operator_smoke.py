from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.development.dev as dev


def test_native_runtime_commands_are_locked_hashed_and_offline(
    tmp_path: Path,
) -> None:
    environment = tmp_path / "runtime/venv"
    wheelhouse = tmp_path / "dependencies/python"

    commands = dev._native_environment_commands(
        "/tools/uv", environment, wheelhouse, "3.12.8"
    )

    assert commands == (
        [
            "/tools/uv",
            "venv",
            "--python",
            "3.12.8",
            "--offline",
            "--no-python-downloads",
            str(environment),
        ],
        [
            "/tools/uv",
            "pip",
            "install",
            "--python",
            str(environment / "bin/python"),
            "--require-hashes",
            "--offline",
            "--no-index",
            "--find-links",
            str(wheelhouse / "native"),
            "--link-mode",
            "copy",
            "--strict",
            "-r",
            str(dev.ROOT / "environments/native/requirements.lock"),
        ],
        [
            "/tools/uv",
            "pip",
            "check",
            "--python",
            str(environment / "bin/python"),
            "--offline",
        ],
    )


def test_dev_operator_smoke_uses_xdg_cache_and_parses_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cache = tmp_path / "cache" / "groundwater-v6"
    calls: list[dict[str, object]] = []
    private_roots: list[tuple[object, tuple[str, ...]]] = []
    paths = SimpleNamespace(cache=cache)

    monkeypatch.setattr(dev, "activate", lambda **_kwargs: None)
    monkeypatch.setattr(dev, "lifecycle", lambda: paths)
    monkeypatch.setattr(
        dev,
        "ensure_private_roots",
        lambda value, names: private_roots.append((value, names)),
        raising=False,
    )

    def run_operator_smoke(
        catalog_directory: Path,
        operator_id: str,
        operator_version: str,
        run_directory: Path,
        **kwargs: object,
    ) -> dict[str, object]:
        calls.append(
            {
                "catalog": catalog_directory,
                "operatorId": operator_id,
                "operatorVersion": operator_version,
                "runDirectory": run_directory,
                **kwargs,
            }
        )
        return {
            "schemaVersion": "operator-smoke-run-result.v1",
            "status": "PASS",
            "runDirectory": str(run_directory),
        }

    monkeypatch.setattr(dev, "run_operator_smoke", run_operator_smoke, raising=False)
    runtime_python = tmp_path / "native-runtime/bin/python"

    @contextmanager
    def native_runtime_python():
        yield (
            runtime_python,
            {
                "dependencyScope": "python",
                "dependencyDigest": "fixture-digest",
            },
        )

    monkeypatch.setattr(
        dev, "_native_runtime_python", native_runtime_python, raising=False
    )

    dev.main(
        [
            "operator-smoke",
            "--operator-id",
            "timeseries.gap_runs",
            "--operator-version",
            "1.0.0",
            "--fixture",
            "observations=monitoring.timeseries",
            "--parameter",
            'frequency="month"',
            "--parameter",
            'expectedStart="2020-01-01"',
        ]
    )

    assert private_roots == [(paths, ("cache",))]
    assert len(calls) == 1
    call = calls[0]
    assert call["catalog"] == dev.ROOT / "packages/operator-pack/catalog/operators"
    assert call["operatorId"] == "timeseries.gap_runs"
    assert call["operatorVersion"] == "1.0.0"
    assert call["fixture_overrides"] == {"observations": ("monitoring.timeseries",)}
    assert call["parameter_overrides"] == {
        "frequency": "month",
        "expectedStart": "2020-01-01",
    }
    assert call["python_executable"] == runtime_python
    assert call["runtime_evidence"] == {
        "dependencyScope": "python",
        "dependencyDigest": "fixture-digest",
    }
    run_directory = Path(str(call["runDirectory"]))
    assert run_directory.parent == cache / "operator-runs"
    assert not run_directory.exists()
    assert json.loads(capsys.readouterr().out)["status"] == "PASS"


def test_dev_operator_smoke_rejects_parameter_override_with_stable_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = SimpleNamespace(cache=tmp_path / "cache" / "groundwater-v6")
    monkeypatch.setattr(dev, "activate", lambda **_kwargs: None)
    monkeypatch.setattr(dev, "lifecycle", lambda: paths)
    monkeypatch.setattr(dev, "ensure_private_roots", lambda *_args: None, raising=False)

    with pytest.raises(SystemExit, match="2"):
        dev.main(
            [
                "operator-smoke",
                "--operator-id",
                "timeseries.gap_runs",
                "--operator-version",
                "1.0.0",
                "--parameter",
                "frequency=not-json",
            ]
        )

    assert json.loads(capsys.readouterr().err) == {
        "status": "FAILED",
        "code": "OPERATOR_PARAMETER_OVERRIDE_INVALID",
    }


def test_dev_operator_smoke_rejects_output_outside_xdg_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = SimpleNamespace(cache=tmp_path / "cache" / "groundwater-v6")
    monkeypatch.setattr(dev, "activate", lambda **_kwargs: None)
    monkeypatch.setattr(dev, "lifecycle", lambda: paths)
    monkeypatch.setattr(dev, "ensure_private_roots", lambda *_args: None, raising=False)

    with pytest.raises(SystemExit, match="2"):
        dev.main(
            [
                "operator-smoke",
                "--operator-id",
                "timeseries.gap_runs",
                "--operator-version",
                "1.0.0",
                "--output-dir",
                str(tmp_path / "outside"),
            ]
        )

    assert json.loads(capsys.readouterr().err) == {
        "status": "FAILED",
        "code": "OPERATOR_RUN_OUTPUT_OUTSIDE_CACHE",
    }
