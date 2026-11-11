from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from moperators.smoke import SmokeRunRefusal, run_operator_smoke

PACK_ROOT = Path(__file__).resolve().parents[1]
CATALOG = PACK_ROOT / "catalog" / "operators"


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def test_single_operator_smoke_publishes_replayable_checksummed_run(
    tmp_path: Path,
) -> None:
    pytest.importorskip("numexpr", reason="requires the Native dependency domain")
    run_directory = tmp_path / "operator-run"

    result = run_operator_smoke(
        CATALOG,
        "timeseries.gap_runs",
        "1.0.0",
        run_directory,
    )

    assert result["schemaVersion"] == "operator-smoke-run-result.v1"
    assert result["status"] == "PASS"
    assert result["runDirectory"] == str(run_directory)
    summary_path = Path(result["summaryPath"])
    assert summary_path == run_directory / "summary.json"
    assert result["summarySha256"] == _sha256(summary_path)

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["schemaVersion"] == "operator-smoke-run.v1"
    assert summary["operator"] == {
        "id": "timeseries.gap_runs",
        "version": "1.0.0",
        "runtime": "native",
        "implementationRevision": summary["operator"]["implementationRevision"],
    }
    assert summary["catalogDigest"].startswith("sha256:")
    assert summary["manifestDigest"].startswith("sha256:")
    assert summary["smokeDigest"].startswith("sha256:")
    assert summary["invocationSha256"].startswith("sha256:")
    assert summary["fixtures"]["observations"] == [
        {
            "fixtureId": "monitoring.timeseries",
            "format": "parquet",
            "mediaType": "application/vnd.apache.parquet",
            "sha256": "sha256:e26d0e609d0dff73b74bce9eb58f9d23b8d90d084b64a22c5daad399806fd101",
            "sizeBytes": 2150,
        }
    ]
    gaps = summary["outputs"]["gaps"]
    assert gaps["kind"] == "artifact"
    assert gaps["path"] == str(run_directory / "outputs" / "gap-runs.parquet")
    assert gaps["sha256"] == _sha256(Path(gaps["path"]))
    assert gaps["sizeBytes"] > 0
    value = summary["outputs"]["summary"]
    assert value["kind"] == "value"
    assert value["sha256"].startswith("sha256:")
    assert value["value"]["gapRunCount"] >= 1

    request = json.loads((run_directory / "request.json").read_text(encoding="utf-8"))
    runtime_result = json.loads((run_directory / "runtime-result.json").read_text(encoding="utf-8"))
    assert request["outputDirectory"] == str(run_directory / "outputs")
    assert runtime_result["outputs"]["gaps"]["path"] == gaps["path"]
    assert not any(tmp_path.glob(".operator-run.*"))


def test_single_operator_smoke_rejects_invalid_parameter_before_writing(
    tmp_path: Path,
) -> None:
    run_directory = tmp_path / "invalid-run"

    with pytest.raises(SmokeRunRefusal) as captured:
        run_operator_smoke(
            CATALOG,
            "timeseries.gap_runs",
            "1.0.0",
            run_directory,
            parameter_overrides={"frequency": "week"},
        )

    assert captured.value.code == "OPERATOR_PARAMETERS_INVALID"
    assert str(captured.value) == "OPERATOR_PARAMETERS_INVALID"
    assert not run_directory.exists()


def test_single_operator_smoke_rejects_unknown_fixture_with_stable_code(
    tmp_path: Path,
) -> None:
    with pytest.raises(SmokeRunRefusal) as captured:
        run_operator_smoke(
            CATALOG,
            "timeseries.gap_runs",
            "1.0.0",
            tmp_path / "invalid-fixture",
            fixture_overrides={"observations": ("missing.fixture",)},
        )

    assert captured.value.code == "OPERATOR_FIXTURE_NOT_FOUND"
