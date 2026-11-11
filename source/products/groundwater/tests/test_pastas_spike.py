from __future__ import annotations

import csv
import json
from pathlib import Path
import sys

import pytest


PRODUCT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PRODUCT_ROOT.parents[1]
if str(PRODUCT_ROOT) not in sys.path:
    sys.path.insert(0, str(PRODUCT_ROOT))

from research.pastas_spike import (  # noqa: E402
    PastasSpikeError,
    assert_gru_comparison_compatible,
    load_spike_config,
    regression_metrics,
    run_pastas_spike,
)


SPIKE_ROOT = PRODUCT_ROOT / "spikes/pastas"
CONFIG = SPIKE_ROOT / "synthetic-benchmark.config.json"


def _config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def test_pastas_spike_config_freezes_the_scientific_boundary() -> None:
    config = load_spike_config(CONFIG)

    assert config.frequency == "D"
    assert config.stress_value_policy == "nonnegative"
    assert config.observation_semantics == "hydraulic_head"
    assert config.response_function == "Exponential"
    assert config.recharge_model == "Linear"
    assert config.stress_lag_days == 0
    assert config.warmup_days == 365
    assert config.observation_missing_policy == "drop"
    assert config.stress_missing_policy == "reject"
    assert config.metric_contract == "regression-metrics.v1"
    assert config.evaluation_mode == "chronological-holdout"

    unsupported = _config()
    unsupported["model"]["response_function"] = "Gamma"
    with pytest.raises(PastasSpikeError, match="PASTAS_SPIKE_RESPONSE_UNSUPPORTED"):
        load_spike_config(unsupported)

    hidden_lag = _config()
    hidden_lag["model"]["stress_lag_days"] = 1
    with pytest.raises(PastasSpikeError, match="PASTAS_SPIKE_LAG_UNSUPPORTED"):
        load_spike_config(hidden_lag)

    overlap = _config()
    overlap["periods"]["validation_start"] = overlap["periods"]["calibration_end"]
    with pytest.raises(PastasSpikeError, match="PASTAS_SPIKE_PERIODS_INVALID"):
        load_spike_config(overlap)

    incompatible_units = _config()
    incompatible_units["units"]["evaporation"] = "m/day"
    with pytest.raises(
        PastasSpikeError, match="PASTAS_SPIKE_STRESS_UNITS_INCOMPATIBLE"
    ):
        load_spike_config(incompatible_units)

    user_input = load_spike_config(SPIKE_ROOT / "user-input.example.config.json")
    assert user_input.input_kind == "USER_SUPPLIED_SPIKE_INPUT"
    assert user_input.generator_id is None
    assert user_input.generator_seed is None


def test_pastas_dependency_is_pinned_outside_every_default_runtime() -> None:
    audit = json.loads(
        (SPIKE_ROOT / "dependency-audit.json").read_text(encoding="utf-8")
    )
    assert audit["decision"] == "ACCEPTED_FOR_INDEPENDENT_SPIKE_ONLY"
    assert audit["default_runtime_changed"] is False
    assert audit["package"] == {
        "name": "pastas",
        "version": "2.0.1",
        "requires_python": ">=3.11,<3.16",
        "license_expression": "MIT",
        "wheel": {
            "filename": "pastas-2.0.1-py3-none-any.whl",
            "python_tag": "py3-none-any",
            "sha256": "sha256:97a1e338ac4e8578f17b48281b75f001b08ffbf15748ebd6291a6b47f41a3c27",
            "size_bytes": 216910,
        },
    }
    assert "pastas==2.0.1" in (SPIKE_ROOT / "requirements.lock").read_text(
        encoding="utf-8"
    )
    spike_lock = (SPIKE_ROOT / "uv.lock").read_text(encoding="utf-8")
    assert 'name = "pastas"' in spike_lock
    assert 'version = "2.0.1"' in spike_lock

    default_dependency_inputs = (
        SOURCE_ROOT / "pyproject.toml",
        SOURCE_ROOT / "uv.lock",
        SOURCE_ROOT / "environments/native/pyproject.toml",
        SOURCE_ROOT / "environments/native/requirements.lock",
        SOURCE_ROOT / "environments/native/uv.lock",
        SOURCE_ROOT / "environments/qgis/pyproject.toml",
        SOURCE_ROOT / "environments/qgis/requirements.lock",
        SOURCE_ROOT / "environments/qgis/uv.lock",
    )
    assert all(
        "pastas" not in path.read_text(encoding="utf-8").lower()
        for path in default_dependency_inputs
    )


def test_pastas_metrics_are_identical_to_the_gru_metric_contract() -> None:
    metrics = regression_metrics([2.0, 2.0], [1.0, 3.0])
    assert metrics == {
        "mae": 1.0,
        "rmse": 1.0,
        "r2": None,
        "r2_reason": "CONSTANT_OBSERVATIONS",
        "sample_count": 2,
    }

    single = regression_metrics([2.0], [1.5])
    assert single["r2"] is None
    assert single["r2_reason"] == "INSUFFICIENT_SAMPLES"


def test_pastas_spike_refuses_runtime_state_inside_source_tree(tmp_path: Path) -> None:
    source = tmp_path / "single-well-daily.csv"
    source.write_text(
        "time,head,precipitation,evaporation\n",
        encoding="utf-8",
    )
    forbidden_output = SOURCE_ROOT / f".pastas-spike-forbidden-{tmp_path.name}"

    with pytest.raises(
        PastasSpikeError,
        match="PASTAS_SPIKE_INPUT_MUST_BE_EXTERNAL",
    ):
        run_pastas_spike(CONFIG, CONFIG, tmp_path / "unused-output")

    with pytest.raises(
        PastasSpikeError,
        match="PASTAS_SPIKE_OUTPUT_MUST_BE_EXTERNAL",
    ):
        run_pastas_spike(source, CONFIG, forbidden_output)


def test_gru_comparison_requires_the_same_split_rows_and_metrics() -> None:
    pastas = {
        "comparison_contract": {
            "metric_contract": "regression-metrics.v1",
            "split_id": "synthetic-daily-v1",
            "evaluation_mode": "chronological-holdout",
            "calibration_rows_sha256": "sha256:" + "c" * 64,
            "validation_rows_sha256": "sha256:" + "a" * 64,
            "split_sha256": "sha256:" + "d" * 64,
        }
    }
    gru = json.loads(json.dumps(pastas))
    result = assert_gru_comparison_compatible(pastas, gru)
    assert result == {
        "status": "COMPATIBLE",
        "metric_contract": "regression-metrics.v1",
        "split_id": "synthetic-daily-v1",
        "calibration_rows_sha256": "sha256:" + "c" * 64,
        "validation_rows_sha256": "sha256:" + "a" * 64,
        "split_sha256": "sha256:" + "d" * 64,
    }

    gru["comparison_contract"]["validation_rows_sha256"] = "sha256:" + "b" * 64
    with pytest.raises(PastasSpikeError, match="PASTAS_GRU_COMPARISON_ROWS_MISMATCH"):
        assert_gru_comparison_compatible(pastas, gru)

    gru = json.loads(json.dumps(pastas))
    gru["comparison_contract"]["metric_contract"] = "different-metrics.v1"
    with pytest.raises(
        PastasSpikeError, match="PASTAS_GRU_COMPARISON_METRICS_MISMATCH"
    ):
        assert_gru_comparison_compatible(pastas, gru)


def test_pastas_201_solves_an_independent_exponential_benchmark(tmp_path: Path) -> None:
    np = pytest.importorskip(
        "numpy", reason="independent Pastas spike environment is not active"
    )
    pd = pytest.importorskip(
        "pandas", reason="independent Pastas spike environment is not active"
    )
    pastas = pytest.importorskip(
        "pastas", reason="independent Pastas spike environment is not active"
    )
    assert pastas.__version__ == "2.0.1"

    seed = 20261110
    rng = np.random.default_rng(seed)
    count = 1095
    index = pd.date_range("2000-01-01", periods=count, freq="D")
    precipitation = np.where(
        rng.random(count) < 0.28,
        rng.gamma(1.8, 4.0, count),
        0.0,
    )
    evaporation = (
        2.2
        + 1.2 * np.sin(2 * np.pi * np.arange(count) / 365.25)
        + rng.normal(0.0, 0.08, count)
    )
    expected = {
        "recharge_A": 0.07,
        "recharge_a": 18.0,
        "recharge_f": -0.55,
        "constant_d": 30.0,
    }
    recharge = precipitation + expected["recharge_f"] * evaporation
    block_count = int(np.ceil(-expected["recharge_a"] * np.log(1.0 - 0.999)))
    block = np.asarray(
        [
            expected["recharge_A"]
            * (
                np.exp(-lag / expected["recharge_a"])
                - np.exp(-(lag + 1) / expected["recharge_a"])
            )
            for lag in range(block_count)
        ]
    )
    head = (
        expected["constant_d"]
        + np.convolve(recharge, block, mode="full")[:count]
        + rng.normal(0.0, 0.006, count)
    )

    source = tmp_path / "single-well-daily.csv"
    with source.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=["time", "head", "precipitation", "evaporation"],
        )
        writer.writeheader()
        for moment, observed, prec, evap in zip(
            index, head, precipitation, evaporation, strict=True
        ):
            writer.writerow(
                {
                    "time": moment.date().isoformat(),
                    "head": f"{observed:.12f}",
                    "precipitation": f"{prec:.12f}",
                    "evaporation": f"{evap:.12f}",
                }
            )

    output = tmp_path / "result"
    manifest = run_pastas_spike(source, CONFIG, output)

    assert manifest["status"] == "PASS"
    assert manifest["scope"] == "INDEPENDENT_SPIKE_ONLY"
    assert manifest["dependency"]["pastas_version"] == "2.0.1"
    assert manifest["solver"]["success"] is True
    assert manifest["identifiability"]["jacobian_full_column_rank"] is True
    assert manifest["identifiability"]["assessment"] == "NOT_ESTABLISHED"
    assert manifest["comparison_contract"]["metric_contract"] == "regression-metrics.v1"
    assert manifest["comparison_contract"]["gru_comparison_status"] == "NOT_ASSESSED"

    parameters = json.loads((output / "parameters.json").read_text(encoding="utf-8"))
    optimal = {row["name"]: row["optimal"] for row in parameters["parameters"]}
    assert optimal["recharge_A"] == pytest.approx(expected["recharge_A"], abs=0.01)
    assert optimal["recharge_a"] == pytest.approx(expected["recharge_a"], abs=2.0)
    assert optimal["recharge_f"] == pytest.approx(expected["recharge_f"], abs=0.05)
    assert optimal["constant_d"] == pytest.approx(expected["constant_d"], abs=0.02)

    metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
    for period in ("calibration", "validation"):
        assert metrics[period]["rmse"] < 0.01
        assert metrics[period]["r2"] > 0.95
        assert metrics[period]["sample_count"] > 100

    with (output / "series.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert set(rows[0]) == {
        "time",
        "period",
        "observed",
        "simulated",
        "residual",
        "persistence",
    }
    assert {row["period"] for row in rows} == {"calibration", "validation"}
    assert manifest["artifacts"]["series"]["sha256"].startswith("sha256:")
    assert (
        json.loads((output / "configuration.json").read_text(encoding="utf-8"))
        == _config()
    )
    assert any(
        "not proof of parameter identifiability" in item
        for item in manifest["limitations"]
    )

    from scripts.checks.check_research_acceptance import evaluate

    policy = json.loads(
        (
            SOURCE_ROOT / "docs/reference/v12-research-acceptance-policy.v1.json"
        ).read_text(encoding="utf-8")
    )
    assessment = json.loads(
        (output / "research-acceptance.json").read_text(encoding="utf-8")
    )
    acceptance = evaluate(policy, assessment, "experiment")
    assert acceptance == {
        "level": "experiment",
        "archiveStatus": "PASS",
        "scientificStatus": "NOT_ASSESSED",
        "engineeringStatus": "NOT_ASSESSED",
        "agentExperienceStatus": "NOT_ASSESSED",
    }


def test_pastas_spike_rejects_missing_stress_before_model_construction(
    tmp_path: Path,
) -> None:
    for dependency in ("numpy", "pandas", "pastas", "scipy"):
        pytest.importorskip(
            dependency, reason="independent Pastas spike environment is not active"
        )

    source = tmp_path / "missing-stress.csv"
    source.write_text(
        "time,head,precipitation,evaporation\n"
        "2000-01-01,10.0,1.0,2.0\n"
        "2000-01-02,10.1,,2.0\n",
        encoding="utf-8",
    )
    with pytest.raises(PastasSpikeError, match="PASTAS_SPIKE_STRESS_MISSING"):
        run_pastas_spike(source, CONFIG, tmp_path / "result")
