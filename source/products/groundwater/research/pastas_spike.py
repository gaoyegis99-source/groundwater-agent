"""Isolated Pastas dependency and numerical spike for one groundwater well.

This module is deliberately outside the Operator Catalog and runtime locks.  It
imports Pastas lazily so ordinary product checks and deployments do not acquire
the optional scientific dependency merely by importing Groundwater research
governance code.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, timedelta
import hashlib
import importlib
from importlib import metadata
import json
import math
from pathlib import Path
import platform
import shutil
import tempfile
from typing import Any, Mapping, Sequence
import warnings


PASTAS_VERSION = "2.0.1"
PASTAS_WHEEL_SHA256 = (
    "sha256:97a1e338ac4e8578f17b48281b75f001b08ffbf15748ebd6291a6b47f41a3c27"
)
CONFIG_SCHEMA = "groundwater-pastas-spike-config.v1"
METRIC_CONTRACT = "regression-metrics.v1"
SOURCE_ROOT = Path(__file__).resolve().parents[3]


class PastasSpikeError(ValueError):
    """A stable refusal at the independent spike trust boundary."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _digest(value: object) -> str:
    return _digest_bytes(_canonical_bytes(value))


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _validated_paths(
    input_csv: str | Path,
    output_directory: str | Path,
) -> tuple[Path, Path]:
    source = Path(input_csv)
    target = Path(output_directory)
    if not source.is_absolute() or source.is_symlink() or not source.is_file():
        raise PastasSpikeError("PASTAS_SPIKE_INPUT_INVALID")
    if _is_within(source.resolve(strict=True), SOURCE_ROOT):
        raise PastasSpikeError("PASTAS_SPIKE_INPUT_MUST_BE_EXTERNAL")
    if not target.is_absolute():
        raise PastasSpikeError("PASTAS_SPIKE_OUTPUT_MUST_BE_ABSOLUTE")
    if target.exists() or target.is_symlink():
        raise PastasSpikeError("PASTAS_SPIKE_OUTPUT_EXISTS")
    if _is_within(target.resolve(strict=False), SOURCE_ROOT):
        raise PastasSpikeError("PASTAS_SPIKE_OUTPUT_MUST_BE_EXTERNAL")
    return source, target


def _mapping(value: object, code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise PastasSpikeError(code)
    return dict(value)


def _exact_keys(value: Mapping[str, Any], expected: set[str], code: str) -> None:
    if set(value) != expected:
        raise PastasSpikeError(code)


def _nonempty_string(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PastasSpikeError(code)
    return value


def _iso_date(value: object, code: str) -> date:
    if not isinstance(value, str):
        raise PastasSpikeError(code)
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise PastasSpikeError(code) from error


def _positive_int(value: object, code: str, *, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= maximum
    ):
        raise PastasSpikeError(code)
    return value


def _positive_float(value: object, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PastasSpikeError(code)
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise PastasSpikeError(code)
    return result


@dataclass(frozen=True)
class PastasSpikeConfig:
    experiment_id: str
    time_column: str
    observation_column: str
    precipitation_column: str
    evaporation_column: str
    observation_unit: str
    precipitation_unit: str
    evaporation_unit: str
    frequency: str
    observation_missing_policy: str
    stress_missing_policy: str
    stress_value_policy: str
    duplicate_timestamp_policy: str
    observation_semantics: str
    recharge_model: str
    response_function: str
    stress_lag_days: int
    warmup_days: int
    calibration_start: date
    calibration_end: date
    validation_start: date
    validation_end: date
    solver_name: str
    max_nfev: int
    ftol: float
    xtol: float
    gtol: float
    metric_contract: str
    split_id: str
    evaluation_mode: str
    input_kind: str
    generator_id: str | None
    generator_seed: int | None
    noise_standard_deviation: float | None
    config_sha256: str
    raw: dict[str, Any] = field(repr=False)

    @property
    def period_values(self) -> dict[str, str]:
        return {
            "calibration_start": self.calibration_start.isoformat(),
            "calibration_end": self.calibration_end.isoformat(),
            "validation_start": self.validation_start.isoformat(),
            "validation_end": self.validation_end.isoformat(),
        }


def load_spike_config(source: str | Path | Mapping[str, Any]) -> PastasSpikeConfig:
    """Load and strictly validate the only supported Pastas spike contract."""

    if isinstance(source, Mapping):
        try:
            raw = json.loads(_canonical_bytes(source))
        except (TypeError, ValueError) as error:
            raise PastasSpikeError("PASTAS_SPIKE_CONFIG_INVALID") from error
        config_sha256 = _digest(raw)
    else:
        path = Path(source)
        if path.is_symlink() or not path.is_file():
            raise PastasSpikeError("PASTAS_SPIKE_CONFIG_INVALID")
        encoded = path.read_bytes()
        try:
            raw = json.loads(encoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PastasSpikeError("PASTAS_SPIKE_CONFIG_INVALID") from error
        config_sha256 = _digest_bytes(encoded)

    root = _mapping(raw, "PASTAS_SPIKE_CONFIG_INVALID")
    _exact_keys(
        root,
        {
            "schema_version",
            "experiment_id",
            "columns",
            "units",
            "time_series",
            "model",
            "periods",
            "solver",
            "comparison",
            "provenance",
        },
        "PASTAS_SPIKE_CONFIG_FIELDS_INVALID",
    )
    if root["schema_version"] != CONFIG_SCHEMA:
        raise PastasSpikeError("PASTAS_SPIKE_CONFIG_SCHEMA_UNSUPPORTED")

    columns = _mapping(root["columns"], "PASTAS_SPIKE_COLUMNS_INVALID")
    _exact_keys(
        columns,
        {"time", "observation", "precipitation", "evaporation"},
        "PASTAS_SPIKE_COLUMNS_INVALID",
    )
    column_values = {
        key: _nonempty_string(value, "PASTAS_SPIKE_COLUMNS_INVALID")
        for key, value in columns.items()
    }
    if len(set(column_values.values())) != len(column_values):
        raise PastasSpikeError("PASTAS_SPIKE_COLUMNS_INVALID")

    units = _mapping(root["units"], "PASTAS_SPIKE_UNITS_INVALID")
    _exact_keys(
        units,
        {"observation", "precipitation", "evaporation"},
        "PASTAS_SPIKE_UNITS_INVALID",
    )
    unit_values = {
        key: _nonempty_string(value, "PASTAS_SPIKE_UNITS_INVALID")
        for key, value in units.items()
    }
    if unit_values["precipitation"] != unit_values["evaporation"]:
        raise PastasSpikeError("PASTAS_SPIKE_STRESS_UNITS_INCOMPATIBLE")

    time_series = _mapping(root["time_series"], "PASTAS_SPIKE_TIME_SETTINGS_INVALID")
    _exact_keys(
        time_series,
        {
            "frequency",
            "observation_missing_policy",
            "stress_missing_policy",
            "stress_value_policy",
            "duplicate_timestamp_policy",
        },
        "PASTAS_SPIKE_TIME_SETTINGS_INVALID",
    )
    if time_series["frequency"] != "D":
        raise PastasSpikeError("PASTAS_SPIKE_FREQUENCY_UNSUPPORTED")
    if time_series["observation_missing_policy"] != "drop":
        raise PastasSpikeError("PASTAS_SPIKE_OBSERVATION_MISSING_POLICY_UNSUPPORTED")
    if time_series["stress_missing_policy"] != "reject":
        raise PastasSpikeError("PASTAS_SPIKE_STRESS_MISSING_POLICY_UNSUPPORTED")
    if time_series["stress_value_policy"] != "nonnegative":
        raise PastasSpikeError("PASTAS_SPIKE_STRESS_VALUE_POLICY_UNSUPPORTED")
    if time_series["duplicate_timestamp_policy"] != "reject":
        raise PastasSpikeError("PASTAS_SPIKE_DUPLICATE_POLICY_UNSUPPORTED")

    model = _mapping(root["model"], "PASTAS_SPIKE_MODEL_INVALID")
    _exact_keys(
        model,
        {
            "observation_semantics",
            "recharge_model",
            "response_function",
            "stress_lag_days",
            "warmup_days",
        },
        "PASTAS_SPIKE_MODEL_INVALID",
    )
    if model["observation_semantics"] != "hydraulic_head":
        raise PastasSpikeError("PASTAS_SPIKE_OBSERVATION_SEMANTICS_UNSUPPORTED")
    if model["recharge_model"] != "Linear":
        raise PastasSpikeError("PASTAS_SPIKE_RECHARGE_UNSUPPORTED")
    if model["response_function"] != "Exponential":
        raise PastasSpikeError("PASTAS_SPIKE_RESPONSE_UNSUPPORTED")
    if model["stress_lag_days"] != 0:
        raise PastasSpikeError("PASTAS_SPIKE_LAG_UNSUPPORTED")
    warmup_days = _positive_int(
        model["warmup_days"], "PASTAS_SPIKE_WARMUP_INVALID", maximum=36500
    )

    periods = _mapping(root["periods"], "PASTAS_SPIKE_PERIODS_INVALID")
    _exact_keys(
        periods,
        {"calibration_start", "calibration_end", "validation_start", "validation_end"},
        "PASTAS_SPIKE_PERIODS_INVALID",
    )
    calibration_start = _iso_date(
        periods["calibration_start"], "PASTAS_SPIKE_PERIODS_INVALID"
    )
    calibration_end = _iso_date(
        periods["calibration_end"], "PASTAS_SPIKE_PERIODS_INVALID"
    )
    validation_start = _iso_date(
        periods["validation_start"], "PASTAS_SPIKE_PERIODS_INVALID"
    )
    validation_end = _iso_date(
        periods["validation_end"], "PASTAS_SPIKE_PERIODS_INVALID"
    )
    if not (
        calibration_start <= calibration_end
        and validation_start <= validation_end
        and validation_start == calibration_end + timedelta(days=1)
    ):
        raise PastasSpikeError("PASTAS_SPIKE_PERIODS_INVALID")

    solver = _mapping(root["solver"], "PASTAS_SPIKE_SOLVER_INVALID")
    _exact_keys(
        solver,
        {"name", "max_nfev", "ftol", "xtol", "gtol"},
        "PASTAS_SPIKE_SOLVER_INVALID",
    )
    if solver["name"] != "scipy-least_squares-trf":
        raise PastasSpikeError("PASTAS_SPIKE_SOLVER_UNSUPPORTED")

    comparison = _mapping(root["comparison"], "PASTAS_SPIKE_COMPARISON_INVALID")
    _exact_keys(
        comparison,
        {"metric_contract", "split_id", "evaluation_mode"},
        "PASTAS_SPIKE_COMPARISON_INVALID",
    )
    if comparison["metric_contract"] != METRIC_CONTRACT:
        raise PastasSpikeError("PASTAS_SPIKE_METRIC_CONTRACT_UNSUPPORTED")
    if comparison["evaluation_mode"] != "chronological-holdout":
        raise PastasSpikeError("PASTAS_SPIKE_EVALUATION_MODE_UNSUPPORTED")

    provenance = _mapping(root["provenance"], "PASTAS_SPIKE_PROVENANCE_INVALID")
    _exact_keys(
        provenance,
        {
            "input_kind",
            "generator_id",
            "generator_seed",
            "noise_standard_deviation",
        },
        "PASTAS_SPIKE_PROVENANCE_INVALID",
    )
    if provenance["input_kind"] not in {
        "SYNTHETIC_TEST_ONLY",
        "USER_SUPPLIED_SPIKE_INPUT",
    }:
        raise PastasSpikeError("PASTAS_SPIKE_PROVENANCE_INVALID")
    generator_id = provenance["generator_id"]
    generator_seed = provenance["generator_seed"]
    noise_standard_deviation = provenance["noise_standard_deviation"]
    if provenance["input_kind"] == "SYNTHETIC_TEST_ONLY":
        generator_id = _nonempty_string(generator_id, "PASTAS_SPIKE_PROVENANCE_INVALID")
        if (
            isinstance(generator_seed, bool)
            or not isinstance(generator_seed, int)
            or generator_seed < 0
        ):
            raise PastasSpikeError("PASTAS_SPIKE_PROVENANCE_INVALID")
        noise_standard_deviation = _positive_float(
            noise_standard_deviation, "PASTAS_SPIKE_PROVENANCE_INVALID"
        )
    elif any(
        value is not None
        for value in (generator_id, generator_seed, noise_standard_deviation)
    ):
        raise PastasSpikeError("PASTAS_SPIKE_PROVENANCE_INVALID")

    return PastasSpikeConfig(
        experiment_id=_nonempty_string(
            root["experiment_id"], "PASTAS_SPIKE_EXPERIMENT_ID_INVALID"
        ),
        time_column=column_values["time"],
        observation_column=column_values["observation"],
        precipitation_column=column_values["precipitation"],
        evaporation_column=column_values["evaporation"],
        observation_unit=unit_values["observation"],
        precipitation_unit=unit_values["precipitation"],
        evaporation_unit=unit_values["evaporation"],
        frequency="D",
        observation_missing_policy="drop",
        stress_missing_policy="reject",
        stress_value_policy="nonnegative",
        duplicate_timestamp_policy="reject",
        observation_semantics="hydraulic_head",
        recharge_model="Linear",
        response_function="Exponential",
        stress_lag_days=0,
        warmup_days=warmup_days,
        calibration_start=calibration_start,
        calibration_end=calibration_end,
        validation_start=validation_start,
        validation_end=validation_end,
        solver_name="scipy-least_squares-trf",
        max_nfev=_positive_int(
            solver["max_nfev"], "PASTAS_SPIKE_SOLVER_INVALID", maximum=1_000_000
        ),
        ftol=_positive_float(solver["ftol"], "PASTAS_SPIKE_SOLVER_INVALID"),
        xtol=_positive_float(solver["xtol"], "PASTAS_SPIKE_SOLVER_INVALID"),
        gtol=_positive_float(solver["gtol"], "PASTAS_SPIKE_SOLVER_INVALID"),
        metric_contract=METRIC_CONTRACT,
        split_id=_nonempty_string(
            comparison["split_id"], "PASTAS_SPIKE_COMPARISON_INVALID"
        ),
        evaluation_mode="chronological-holdout",
        input_kind=provenance["input_kind"],
        generator_id=generator_id,
        generator_seed=generator_seed,
        noise_standard_deviation=noise_standard_deviation,
        config_sha256=config_sha256,
        raw=root,
    )


def regression_metrics(
    actual: Sequence[float], predicted: Sequence[float]
) -> dict[str, float | int | str | None]:
    """Match ``statistics.regression_metrics@1.0.0`` scalar definitions."""

    observed = [float(value) for value in actual]
    simulated = [float(value) for value in predicted]
    if not observed or len(observed) != len(simulated):
        raise PastasSpikeError("PASTAS_SPIKE_METRICS_INPUT_INVALID")
    if not all(math.isfinite(value) for value in (*observed, *simulated)):
        raise PastasSpikeError("PASTAS_SPIKE_METRICS_INPUT_INVALID")
    errors = [
        estimate - value for value, estimate in zip(observed, simulated, strict=True)
    ]
    mean = sum(observed) / len(observed)
    denominator = sum((value - mean) ** 2 for value in observed)
    if len(observed) < 2:
        reason = "INSUFFICIENT_SAMPLES"
    elif denominator == 0:
        reason = "CONSTANT_OBSERVATIONS"
    else:
        reason = None
    squared_error = sum(error**2 for error in errors)
    return {
        "mae": sum(abs(error) for error in errors) / len(errors),
        "rmse": math.sqrt(squared_error / len(errors)),
        "r2": None if reason else 1.0 - squared_error / denominator,
        "r2_reason": reason,
        "sample_count": len(observed),
    }


def _comparison_contract(value: Mapping[str, Any]) -> dict[str, Any]:
    contract = value.get("comparison_contract")
    if not isinstance(contract, Mapping):
        raise PastasSpikeError("PASTAS_GRU_COMPARISON_CONTRACT_MISSING")
    return dict(contract)


def assert_gru_comparison_compatible(
    pastas_report: Mapping[str, Any], gru_report: Mapping[str, Any]
) -> dict[str, str]:
    """Refuse a Pastas/GRU comparison unless its evidence rows truly match."""

    pastas = _comparison_contract(pastas_report)
    gru = _comparison_contract(gru_report)
    checks = (
        ("metric_contract", "PASTAS_GRU_COMPARISON_METRICS_MISMATCH"),
        ("split_id", "PASTAS_GRU_COMPARISON_SPLIT_MISMATCH"),
        ("evaluation_mode", "PASTAS_GRU_COMPARISON_MODE_MISMATCH"),
        ("calibration_rows_sha256", "PASTAS_GRU_COMPARISON_ROWS_MISMATCH"),
        ("validation_rows_sha256", "PASTAS_GRU_COMPARISON_ROWS_MISMATCH"),
        ("split_sha256", "PASTAS_GRU_COMPARISON_SPLIT_MISMATCH"),
    )
    for key, code in checks:
        if not isinstance(pastas.get(key), str) or pastas[key] != gru.get(key):
            raise PastasSpikeError(code)
    return {
        "status": "COMPATIBLE",
        "metric_contract": pastas["metric_contract"],
        "split_id": pastas["split_id"],
        "calibration_rows_sha256": pastas["calibration_rows_sha256"],
        "validation_rows_sha256": pastas["validation_rows_sha256"],
        "split_sha256": pastas["split_sha256"],
    }


def _load_scientific_dependencies() -> tuple[Any, Any, Any, Any]:
    modules: list[Any] = []
    for name in ("numpy", "pandas", "pastas", "scipy"):
        try:
            modules.append(importlib.import_module(name))
        except ImportError as error:
            raise PastasSpikeError(f"PASTAS_SPIKE_DEPENDENCY_MISSING:{name}") from error
    numpy, pandas, pastas, scipy = modules
    if pastas.__version__ != PASTAS_VERSION:
        raise PastasSpikeError(
            f"PASTAS_SPIKE_VERSION_MISMATCH:expected={PASTAS_VERSION}:actual={pastas.__version__}"
        )
    return numpy, pandas, pastas, scipy


def _prepare_input(source: Path, config: PastasSpikeConfig, np: Any, pd: Any) -> Any:
    if source.is_symlink() or not source.is_file():
        raise PastasSpikeError("PASTAS_SPIKE_INPUT_INVALID")
    try:
        frame = pd.read_csv(source)
    except Exception as error:
        raise PastasSpikeError("PASTAS_SPIKE_INPUT_INVALID") from error
    required_columns = (
        config.time_column,
        config.observation_column,
        config.precipitation_column,
        config.evaporation_column,
    )
    if any(column not in frame.columns for column in required_columns):
        raise PastasSpikeError("PASTAS_SPIKE_COLUMN_REQUIRED")
    if frame.empty:
        raise PastasSpikeError("PASTAS_SPIKE_INPUT_EMPTY")

    try:
        moments = pd.to_datetime(frame[config.time_column], errors="raise")
    except (TypeError, ValueError) as error:
        raise PastasSpikeError("PASTAS_SPIKE_TIME_INVALID") from error
    index = pd.DatetimeIndex(moments)
    if index.tz is not None or not index.equals(index.normalize()):
        raise PastasSpikeError("PASTAS_SPIKE_TIME_INVALID")
    if index.has_duplicates:
        raise PastasSpikeError("PASTAS_SPIKE_DUPLICATE_TIMESTAMP")
    if not index.is_monotonic_increasing:
        raise PastasSpikeError("PASTAS_SPIKE_TIME_NOT_ASCENDING")
    frame = frame.loc[:, list(required_columns)].copy()
    frame.index = index

    for column in required_columns[1:]:
        try:
            frame[column] = pd.to_numeric(frame[column], errors="raise")
        except (TypeError, ValueError) as error:
            raise PastasSpikeError("PASTAS_SPIKE_VALUE_INVALID") from error

    stresses = frame[[config.precipitation_column, config.evaporation_column]]
    if stresses.isna().to_numpy().any() or not np.isfinite(stresses.to_numpy()).all():
        raise PastasSpikeError("PASTAS_SPIKE_STRESS_MISSING")
    if (stresses.to_numpy() < 0).any():
        raise PastasSpikeError("PASTAS_SPIKE_STRESS_NEGATIVE")
    observations = frame[config.observation_column]
    finite_or_missing = observations.isna() | np.isfinite(observations.to_numpy())
    if not bool(finite_or_missing.all()):
        raise PastasSpikeError("PASTAS_SPIKE_OBSERVATION_NONFINITE")

    calibration_start = pd.Timestamp(config.calibration_start)
    validation_end = pd.Timestamp(config.validation_end)
    required_start = calibration_start - pd.Timedelta(days=config.warmup_days)
    expected = pd.date_range(required_start, validation_end, freq=config.frequency)
    window = frame.reindex(expected)
    if (
        window[[config.precipitation_column, config.evaporation_column]]
        .isna()
        .to_numpy()
        .any()
    ):
        raise PastasSpikeError("PASTAS_SPIKE_STRESS_TIMESTEP_MISSING")

    calibration = window.loc[
        pd.Timestamp(config.calibration_start) : pd.Timestamp(config.calibration_end),
        config.observation_column,
    ].dropna()
    validation = window.loc[
        pd.Timestamp(config.validation_start) : pd.Timestamp(config.validation_end),
        config.observation_column,
    ].dropna()
    if len(calibration) <= 4:
        raise PastasSpikeError("PASTAS_SPIKE_CALIBRATION_OBSERVATIONS_INSUFFICIENT")
    if len(validation) < 2:
        raise PastasSpikeError("PASTAS_SPIKE_VALIDATION_OBSERVATIONS_INSUFFICIENT")
    return window


def _json_number(value: object) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    number = float(value)
    return number if math.isfinite(number) else None


def _parameter_report(model: Any, np: Any) -> dict[str, Any]:
    rows = []
    for name, parameter in model.parameters.iterrows():
        optimal = _json_number(parameter.get("optimal"))
        lower = _json_number(parameter.get("pmin"))
        upper = _json_number(parameter.get("pmax"))
        rows.append(
            {
                "name": str(name),
                "initial": _json_number(parameter.get("initial")),
                "pmin": lower,
                "pmax": upper,
                "vary": bool(parameter.get("vary")),
                "optimal": optimal,
                "stderr": _json_number(parameter.get("stderr")),
                "at_lower_bound": bool(
                    lower is not None
                    and optimal is not None
                    and np.isclose(optimal, lower, rtol=1e-7, atol=1e-12)
                ),
                "at_upper_bound": bool(
                    upper is not None
                    and optimal is not None
                    and np.isclose(optimal, upper, rtol=1e-7, atol=1e-12)
                ),
            }
        )
    return {
        "schema_version": "groundwater-pastas-parameters.v1",
        "parameters": rows,
    }


def _solver_diagnostics(solver: Any, np: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    result = solver.result
    if result is None:
        raise PastasSpikeError("PASTAS_SPIKE_SOLVER_RESULT_MISSING")
    jacobian = np.asarray(result.jac, dtype=float)
    rank = int(np.linalg.matrix_rank(jacobian))
    varying_parameter_count = int(jacobian.shape[1])
    singular_values = np.linalg.svd(jacobian, compute_uv=False)
    condition_number = float(np.linalg.cond(jacobian))
    covariance = solver.pcov
    covariance_finite = bool(
        covariance is not None and np.isfinite(covariance.to_numpy(dtype=float)).all()
    )
    convergence = {
        "name": "scipy-least_squares-trf",
        "success": bool(result.success),
        "status": int(result.status),
        "message": str(result.message),
        "nfev": int(result.nfev),
        "cost": _json_number(result.cost),
        "optimality": _json_number(result.optimality),
        "active_mask": [
            int(value) for value in np.asarray(result.active_mask).tolist()
        ],
    }
    identifiability = {
        "assessment": "NOT_ESTABLISHED",
        "jacobian_rows": int(jacobian.shape[0]),
        "varying_parameter_count": varying_parameter_count,
        "jacobian_rank": rank,
        "jacobian_full_column_rank": rank == varying_parameter_count,
        "jacobian_condition_number": (
            condition_number if math.isfinite(condition_number) else None
        ),
        "jacobian_singular_values": [float(value) for value in singular_values],
        "covariance_finite": covariance_finite,
        "basis": (
            "Local least-squares Jacobian rank and covariance are diagnostics only; "
            "they do not establish structural or practical parameter identifiability."
        ),
    }
    return convergence, identifiability


def _metric_document(rows: list[dict[str, Any]], unit: str) -> dict[str, Any]:
    actual = [row["observed"] for row in rows]
    predicted = [row["simulated"] for row in rows]
    persistence = [row["persistence"] for row in rows]
    model = regression_metrics(actual, predicted)
    baseline = regression_metrics(actual, persistence)
    baseline_rmse = float(baseline["rmse"])
    improvement = (
        100.0 * (baseline_rmse - float(model["rmse"])) / baseline_rmse
        if baseline_rmse
        else None
    )
    return {
        "schemaVersion": METRIC_CONTRACT,
        "comparison": "model-vs-declared-baseline-on-identical-rows",
        **model,
        "fields": {
            "actual": "observed",
            "predicted": "simulated",
            "baseline": "persistence",
        },
        "unit": unit,
        "baseline": baseline,
        "rmse_improvement_pct": improvement,
        "improvement_reason": None if baseline_rmse else "ZERO_BASELINE_RMSE",
    }


def _evaluation_rows(
    window: Any, simulation: Any, config: PastasSpikeConfig, pd: Any, np: Any
) -> list[dict[str, Any]]:
    observations = window[config.observation_column].dropna()
    persistence = observations.shift(1)
    evaluation = observations.loc[
        pd.Timestamp(config.calibration_start) : pd.Timestamp(config.validation_end)
    ]
    simulated = simulation.reindex(evaluation.index)
    baselines = persistence.reindex(evaluation.index)
    if simulated.isna().any():
        raise PastasSpikeError("PASTAS_SPIKE_SIMULATION_ALIGNMENT_FAILED")
    if baselines.isna().any():
        raise PastasSpikeError("PASTAS_SPIKE_PERSISTENCE_BASELINE_MISSING")
    if not np.isfinite(simulated.to_numpy()).all():
        raise PastasSpikeError("PASTAS_SPIKE_SIMULATION_NONFINITE")

    rows: list[dict[str, Any]] = []
    validation_start = pd.Timestamp(config.validation_start)
    for moment, observed in evaluation.items():
        estimate = float(simulated.loc[moment])
        observed_value = float(observed)
        rows.append(
            {
                "time": moment.date().isoformat(),
                "period": "validation" if moment >= validation_start else "calibration",
                "observed": observed_value,
                "simulated": estimate,
                "residual": observed_value - estimate,
                "persistence": float(baselines.loc[moment]),
            }
        )
    return rows


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _write_series(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = ("time", "period", "observed", "simulated", "residual", "persistence")
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: format(row[key], ".17g")
                    if isinstance(row[key], float)
                    else row[key]
                    for key in fields
                }
            )


def _artifact(path: Path) -> dict[str, Any]:
    return {
        "path": path.name,
        "sha256": _file_digest(path),
        "size_bytes": path.stat().st_size,
    }


def _research_assessment(success: bool) -> dict[str, Any]:
    pass_status = "PASS" if success else "FAIL"
    return {
        "schemaVersion": "v12-research-acceptance.v1",
        "scientificStatus": {
            "input_checksums": {"status": "PASS"},
            "parameters_and_seeds": {"status": "PASS"},
            "algorithm_identity": {"status": "PASS"},
            "numerical_benchmark": {"status": "NOT_ASSESSED"},
            "tolerance_contract": {"status": "NOT_ASSESSED"},
            "lineage": {"status": "PASS"},
            "domain_review": {"status": "NOT_ASSESSED"},
        },
        "engineeringStatus": {
            "changed_scope_tests": {"status": "NOT_ASSESSED"},
            "contract_checks": {"status": pass_status},
            "schema_migration": {"status": "NOT_ASSESSED"},
            "compose_smoke": {"status": "NOT_ASSESSED"},
            "snapshot_restore": {"status": "NOT_ASSESSED"},
            "archive_rebuild": {"status": "NOT_ASSESSED"},
        },
        "agentExperienceStatus": {
            "deepseek_quality": {"status": "NOT_ASSESSED"},
            "minimax_quality": {"status": "NOT_ASSESSED"},
            "human_review": {"status": "NOT_ASSESSED"},
        },
    }


def run_pastas_spike(
    input_csv: str | Path,
    config_source: str | Path | Mapping[str, Any],
    output_directory: str | Path,
) -> dict[str, Any]:
    """Calibrate and validate the pinned isolated Pastas experiment."""

    config = load_spike_config(config_source)
    source, target = _validated_paths(input_csv, output_directory)
    np, pd, pastas, scipy = _load_scientific_dependencies()
    window = _prepare_input(source, config, np, pd)

    observation = window[config.observation_column].dropna().rename("head")
    precipitation = window[config.precipitation_column].rename("precipitation")
    evaporation = window[config.evaporation_column].rename("evaporation")
    model = pastas.Model(
        observation,
        name=config.experiment_id,
        freq=config.frequency,
    )
    pastas.RechargeModel(
        model=model,
        prec=precipitation,
        evap=evaporation,
        rfunc=pastas.Exponential(),
        recharge=pastas.rch.Linear(),
        settings=("prec", "evap", "evap"),
        name="recharge",
    )
    solver = pastas.solver.LeastSquares(
        model=model,
        method="trf",
        max_nfev=config.max_nfev,
        ftol=config.ftol,
        xtol=config.xtol,
        gtol=config.gtol,
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        model.solve(
            tmin=config.calibration_start.isoformat(),
            tmax=config.calibration_end.isoformat(),
            freq=config.frequency,
            warmup=float(config.warmup_days),
            report=False,
        )
    convergence, identifiability = _solver_diagnostics(solver, np)
    simulation = model.simulate(
        tmin=config.calibration_start.isoformat(),
        tmax=config.validation_end.isoformat(),
        freq=config.frequency,
        warmup=float(config.warmup_days),
    )
    rows = _evaluation_rows(window, simulation, config, pd, np)
    calibration_rows = [row for row in rows if row["period"] == "calibration"]
    validation_rows = [row for row in rows if row["period"] == "validation"]
    row_identity_fields = ("time", "observed", "persistence")

    metrics = {
        "schema_version": "groundwater-pastas-period-metrics.v1",
        "metric_contract": METRIC_CONTRACT,
        "calibration": _metric_document(calibration_rows, config.observation_unit),
        "validation": _metric_document(validation_rows, config.observation_unit),
    }
    parameters = _parameter_report(model, np)
    fitted = {row["name"]: row["optimal"] for row in parameters["parameters"]}
    response_scale = fitted.get("recharge_a")
    response_times = (
        {
            f"t{int(fraction * 100)}_days": -response_scale * math.log(1.0 - fraction)
            for fraction in (0.5, 0.9, 0.95, 0.99)
        }
        if response_scale is not None and response_scale > 0
        else None
    )
    diagnostics = {
        "schema_version": "groundwater-pastas-diagnostics.v1",
        "solver": convergence,
        "identifiability": identifiability,
        "response": {
            "function": "Exponential",
            "stress_lag_days": 0,
            "lag_meaning": (
                "No pure-delay parameter; the exponential response begins in the first "
                "daily block. Response times are fitted memory diagnostics, not a hidden lag."
            ),
            "response_times": response_times,
        },
        "warnings": [
            {"category": item.category.__name__, "message": str(item.message)}
            for item in caught
        ],
    }
    calibration_rows_sha256 = _digest(
        [{key: row[key] for key in row_identity_fields} for row in calibration_rows]
    )
    validation_rows_sha256 = _digest(
        [{key: row[key] for key in row_identity_fields} for row in validation_rows]
    )
    split_sha256 = _digest(
        {
            "metric_contract": config.metric_contract,
            "split_id": config.split_id,
            "evaluation_mode": config.evaluation_mode,
            "periods": config.period_values,
            "calibration_rows_sha256": calibration_rows_sha256,
            "validation_rows_sha256": validation_rows_sha256,
        }
    )
    comparison_contract = {
        "metric_contract": config.metric_contract,
        "split_id": config.split_id,
        "evaluation_mode": config.evaluation_mode,
        "calibration_rows_sha256": calibration_rows_sha256,
        "validation_rows_sha256": validation_rows_sha256,
        "split_sha256": split_sha256,
        "persistence_basis": "previous available observed head before each evaluated row",
        "gru_comparison_status": "NOT_ASSESSED",
    }

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))
    try:
        series_path = staging / "series.csv"
        parameters_path = staging / "parameters.json"
        metrics_path = staging / "metrics.json"
        diagnostics_path = staging / "diagnostics.json"
        config_path = staging / "configuration.json"
        assessment_path = staging / "research-acceptance.json"
        _write_series(series_path, rows)
        _write_json(parameters_path, parameters)
        _write_json(metrics_path, metrics)
        _write_json(diagnostics_path, diagnostics)
        _write_json(config_path, config.raw)
        assessment = _research_assessment(convergence["success"])
        _write_json(assessment_path, assessment)
        artifacts = {
            "series": _artifact(series_path),
            "parameters": _artifact(parameters_path),
            "metrics": _artifact(metrics_path),
            "diagnostics": _artifact(diagnostics_path),
            "configuration": _artifact(config_path),
            "research_acceptance": _artifact(assessment_path),
        }
        manifest = {
            "schema_version": "groundwater-pastas-spike-result.v1",
            "status": "PASS" if convergence["success"] else "FAIL",
            "scope": "INDEPENDENT_SPIKE_ONLY",
            "experiment_id": config.experiment_id,
            "input": {
                "path_identity": source.name,
                "sha256": _file_digest(source),
                "config_sha256": config.config_sha256,
                "input_kind": config.input_kind,
                "generator_id": config.generator_id,
                "generator_seed": config.generator_seed,
                "noise_standard_deviation": config.noise_standard_deviation,
                "units": {
                    "observation": config.observation_unit,
                    "precipitation": config.precipitation_unit,
                    "evaporation": config.evaporation_unit,
                },
            },
            "dependency": {
                "pastas_version": pastas.__version__,
                "pastas_wheel_sha256": PASTAS_WHEEL_SHA256,
                "pastas_license_expression": metadata.metadata("pastas").get(
                    "License-Expression"
                ),
                "numpy_version": np.__version__,
                "pandas_version": pd.__version__,
                "scipy_version": scipy.__version__,
                "python_version": platform.python_version(),
            },
            "algorithm": {
                "recharge_model": config.recharge_model,
                "response_function": config.response_function,
                "stress_lag_days": config.stress_lag_days,
                "frequency": config.frequency,
                "warmup_days": config.warmup_days,
                "observation_semantics": config.observation_semantics,
                "observation_missing_policy": config.observation_missing_policy,
                "stress_missing_policy": config.stress_missing_policy,
                "stress_value_policy": config.stress_value_policy,
                "solver": config.solver_name,
                "solver_settings": {
                    "max_nfev": config.max_nfev,
                    "ftol": config.ftol,
                    "xtol": config.xtol,
                    "gtol": config.gtol,
                    "random_seed": None,
                },
                "implementation_sha256": _file_digest(Path(__file__)),
            },
            "periods": config.period_values,
            "solver": convergence,
            "identifiability": identifiability,
            "comparison_contract": comparison_contract,
            "artifacts": artifacts,
            "limitations": [
                "This is an isolated dependency and synthetic numerical spike, not a published product capability.",
                "Solver convergence and local covariance are not proof of parameter identifiability.",
                "The exponential response, linear recharge, zero pure delay, and daily frequency are explicit spike assumptions, not site suitability findings.",
                "Validation is a chronological holdout from the same synthetic process, not independent field validation or future predictive assurance.",
                "A GRU comparison remains NOT_ASSESSED until a GRU report passes the exact split, row checksum, and metric-contract guard.",
            ],
        }
        _write_json(staging / "manifest.json", manifest)
        staging.replace(target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return manifest


__all__ = [
    "PastasSpikeConfig",
    "PastasSpikeError",
    "assert_gru_comparison_compatible",
    "load_spike_config",
    "regression_metrics",
    "run_pastas_spike",
]
