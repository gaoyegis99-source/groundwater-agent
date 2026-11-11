"""Ordinary file-to-file table, time-series and regression operators."""

from __future__ import annotations

from datetime import datetime
from math import floor

import numpy as np

from moperators.operators.transfer_common import (
    numeric,
    one,
    read_json,
    table,
    write_array,
    write_json,
    write_table,
)


def unpivot(_identity, inputs, parameters, output_dir):
    import pyarrow as pa

    data = table(inputs)
    rules = read_json(one(inputs, "mapping"), "table-timeseries-unpivot.v1")
    time_field = parameters["timeField"]
    id_fields, series = rules["idFields"], rules["series"]
    names = [s["field"] for s in series]
    output_names = [*id_fields, time_field, "series_order", *names]
    if len(output_names) != len(set(output_names)):
        raise ValueError("OUTPUT_FIELD_CONFLICT")
    required = set(id_fields) | set(rules["uniqueKeys"])
    if not required <= set(data.column_names):
        raise ValueError("FIELD_REQUIRED")
    timelines = []
    source_columns = []
    for feature in series:
        mapping = {}
        for item in feature["columns"]:
            moment = datetime.fromisoformat(item["time"])
            if moment.tzinfo is not None:
                raise ValueError("TIMEZONE_UNSUPPORTED")
            if moment in mapping:
                raise ValueError("DUPLICATE_TIMESTAMP")
            mapping[moment] = item["column"]
        timelines.append(mapping)
        source_columns.extend(mapping.values())
    if any(set(t) != set(timelines[0]) for t in timelines):
        raise ValueError("TIME_AXIS_MISMATCH")
    if len(source_columns) != len(set(source_columns)):
        raise ValueError("DUPLICATE_FIELD")
    values = {name: numeric(data, name) for name in source_columns}
    moments = sorted(timelines[0])
    rows, seen = [], set()
    for ordinal, row in enumerate(data.to_pylist()):
        key = tuple(row[name] for name in rules["uniqueKeys"])
        if any(item is None for item in key) or key in seen:
            raise ValueError("DUPLICATE_SERIES")
        seen.add(key)
        for moment in moments:
            rows.append(
                {
                    **{name: row[name] for name in id_fields},
                    time_field: moment.isoformat(),
                    "series_order": ordinal,
                    **{
                        name: float(values[timelines[i][moment]][ordinal])
                        for i, name in enumerate(names)
                    },
                }
            )
    if not rows:
        raise ValueError("EMPTY_RESULT")
    result = pa.Table.from_pylist(rows)
    for name in names:
        result = result.set_column(
            result.column_names.index(name), name, result[name].cast(pa.float32())
        )
    return {"data": write_table(result, output_dir / "series.parquet")}


def _groups(data, group_field, time_field):
    if not {group_field, time_field} <= set(data.column_names):
        raise ValueError("FIELD_REQUIRED")
    groups = {}
    for index, row in enumerate(data.to_pylist()):
        group = row[group_field]
        if group is None or row[time_field] is None:
            raise ValueError("FIELD_REQUIRED")
        moment = datetime.fromisoformat(str(row[time_field]))
        if moment.tzinfo is not None:
            raise ValueError("TIMEZONE_UNSUPPORTED")
        series = groups.setdefault(group, {})
        if moment in series:
            raise ValueError("DUPLICATE_TIMESTAMP")
        series[moment] = index
    if not groups:
        raise ValueError("EMPTY_RESULT")
    axis = sorted(next(iter(groups.values())))
    if any(sorted(series) != axis for series in groups.values()):
        raise ValueError("TIME_AXIS_MISMATCH")
    return groups, axis


def split(_identity, inputs, parameters, output_dir):
    import pyarrow as pa

    data = table(inputs)
    groups, axis = _groups(data, parameters["groupField"], parameters["timeField"])
    end = floor(len(axis) * parameters["trainRatio"])
    window = parameters["inputWindow"]
    if end <= window or end > len(axis):
        raise ValueError("TRAINING_WINDOW_INSUFFICIENT")
    if parameters["trainRatio"] < 1 and end == len(axis):
        raise ValueError("HOLDOUT_REQUIRED")
    ranges = {
        "fitRows": (0, end),
        "trainTargets": (window, end),
        "holdoutTargets": (end, len(axis)),
    }
    outputs = {}
    for name, (start, stop) in ranges.items():
        indices = [series[moment] for series in groups.values() for moment in axis[start:stop]]
        result = data.take(pa.array(indices, type=pa.int64()))
        outputs[name] = write_table(result, output_dir / f"{name}.parquet")
    outputs["split"] = write_json(
        {
            "schemaVersion": "chronological-split.v1",
            "strategy": "shared-time-axis-chronological",
            "monthCount": len(axis),
            "trainMonthCount": end,
            "holdoutMonthCount": len(axis) - end,
            "groupCount": len(groups),
            "trainRatio": parameters["trainRatio"],
            "trainingStart": axis[0].isoformat(),
            "trainingEnd": axis[end - 1].isoformat(),
            "holdoutStart": axis[end].isoformat() if end < len(axis) else None,
            "holdoutEnd": axis[-1].isoformat() if end < len(axis) else None,
            "inputWindow": window,
            "fitScope": "training-rows-only",
            "evaluationMode": (
                "single-step-holdout-with-observed-history" if end < len(axis) else None
            ),
        },
        output_dir / "split.json",
    )
    return outputs


def scaler_fit(_identity, inputs, parameters, output_dir):
    data = table(inputs)
    rules = read_json(one(inputs, "rules"), "standard-scaler-fields.v1")
    if not data.num_rows:
        raise ValueError("EMPTY_RESULT")
    features = []
    for field in rules["fields"]:
        values = numeric(data, field).astype(np.float64)
        variance = float(values.var())
        features.append(
            {
                "field": field,
                "mean": float(values.mean()),
                "variance": variance,
                "scale": float(np.sqrt(variance)) if variance else 1.0,
                "count": len(values),
            }
        )
    return {
        "scaler": write_json(
            {"schemaVersion": "standard-scaler.v1", "features": features},
            output_dir / "scaler.json",
        )
    }


def _scaler(inputs):
    value = read_json(one(inputs, "scaler"), "standard-scaler.v1")
    fields = [item["field"] for item in value["features"]]
    if len(fields) != len(set(fields)):
        raise ValueError("DUPLICATE_FIELD")
    for item in value["features"]:
        if not all(np.isfinite(item[name]) for name in ("mean", "scale", "variance")):
            raise ValueError("NON_NUMERIC_VALUE")
    return value["features"]


def scaler_transform(_identity, inputs, parameters, output_dir):
    import pyarrow as pa

    data = table(inputs)
    for item in _scaler(inputs):
        values = numeric(data, item["field"]).copy()
        # Keep fitted precision in each operation; its stored result still rounds to float32.
        values -= np.asarray([item["mean"]], dtype=np.float64)
        values /= np.asarray([item["scale"]], dtype=np.float64)
        data = data.set_column(
            data.column_names.index(item["field"]), item["field"], pa.array(values)
        )
    return {"data": write_table(data, output_dir / "scaled.parquet")}


def scaler_inverse(_identity, inputs, parameters, output_dir):
    import pyarrow as pa

    data = table(inputs)
    states = {item["field"]: item for item in _scaler(inputs)}
    if parameters["scalerField"] not in states:
        raise ValueError("FIELD_REQUIRED")
    state = states[parameters["scalerField"]]
    rules = read_json(one(inputs, "rules"), "standard-scaler-fields.v1")
    for field in rules["fields"]:
        values = numeric(data, field).copy()
        values *= np.asarray([state["scale"]], dtype=np.float64)
        values += np.asarray([state["mean"]], dtype=np.float64)
        data = data.set_column(data.column_names.index(field), field, pa.array(values))
    return {"data": write_table(data, output_dir / "predictions.parquet")}


def windows(_identity, inputs, parameters, output_dir):
    import pyarrow as pa

    data, targets = table(inputs), table(inputs, "targets")
    rules = read_json(one(inputs, "rules"), "supervised-window-fields.v1")
    group_field, time_field = parameters["groupField"], parameters["timeField"]
    groups, axis = _groups(data, group_field, time_field)
    required = {"series_order", *rules["carryFields"]}
    if not required <= set(data.column_names):
        raise ValueError("FIELD_REQUIRED")
    reserved = {"sample_id", "actual", "persistence"}
    if reserved & {group_field, time_field, *rules["carryFields"]}:
        raise ValueError("OUTPUT_FIELD_CONFLICT")
    features = np.column_stack([numeric(data, name) for name in rules["featureFields"]])
    labels = numeric(data, parameters["targetField"])
    rows = data.to_pylist()
    selections, seen = [], set()
    for target in targets.to_pylist():
        key = (target[group_field], datetime.fromisoformat(str(target[time_field])))
        if key in seen:
            raise ValueError("DUPLICATE_TIMESTAMP")
        seen.add(key)
        if key[0] not in groups or key[1] not in groups[key[0]]:
            raise ValueError("TARGET_NOT_FOUND")
        selections.append(key)
    selections.sort(key=lambda key: (key[1], rows[groups[key[0]][key[1]]]["series_order"]))
    batch, y, index = [], [], []
    positions = {moment: i for i, moment in enumerate(axis)}
    length = parameters["inputWindow"]
    for sample_id, (group, moment) in enumerate(selections):
        position = positions[moment]
        if position < length:
            raise ValueError("TRAINING_WINDOW_INSUFFICIENT")
        series = groups[group]
        row_index = series[moment]
        row = rows[row_index]
        batch.append(features[[series[t] for t in axis[position - length : position]]])
        y.append([labels[row_index]])
        index.append(
            {
                "sample_id": sample_id,
                group_field: group,
                time_field: moment.isoformat(),
                **{name: row[name] for name in rules["carryFields"]},
                "actual": float(labels[row_index]),
                "persistence": float(labels[series[axis[position - 1]]]),
            }
        )
    if not batch:
        raise ValueError("EMPTY_RESULT")
    return {
        "features": write_array(np.asarray(batch, dtype=np.float32), output_dir / "features.npy"),
        "targets": write_array(np.asarray(y, dtype=np.float32), output_dir / "targets.npy"),
        "sampleIndex": write_table(pa.Table.from_pylist(index), output_dir / "samples.parquet"),
    }


def _metrics(actual, predicted):
    error = predicted - actual
    denominator = float(np.square(actual - actual.mean()).sum())
    reason = (
        "INSUFFICIENT_SAMPLES"
        if len(actual) < 2
        else ("CONSTANT_OBSERVATIONS" if denominator == 0 else None)
    )
    return {
        "mae": float(np.abs(error).mean()),
        "rmse": float(np.sqrt(np.square(error).mean())),
        "r2": None if reason else 1 - float(np.square(error).sum()) / denominator,
        "r2_reason": reason,
        "sample_count": len(actual),
    }


def _metric_comparison(actual, predicted, baseline):
    model = _metrics(actual, predicted)
    reference = _metrics(actual, baseline)
    improvement = (
        100 * (reference["rmse"] - model["rmse"]) / reference["rmse"] if reference["rmse"] else None
    )
    return model, reference, improvement


def regression_metrics(_identity, inputs, parameters, output_dir):
    import pyarrow as pa

    data = table(inputs, "predictions")
    if not data.num_rows:
        raise ValueError("EMPTY_RESULT")
    actual, predicted, baseline = [
        numeric(data, parameters[name], np.float64)
        for name in ("actualField", "predictedField", "baselineField")
    ]
    if parameters["groupField"] not in data.column_names:
        raise ValueError("FIELD_REQUIRED")
    if {"error", "absolute_error"} & set(data.column_names):
        raise ValueError("OUTPUT_FIELD_CONFLICT")
    data = data.append_column("error", pa.array(predicted - actual))
    data = data.append_column("absolute_error", pa.array(np.abs(predicted - actual)))
    overall, persistence, improvement = _metric_comparison(actual, predicted, baseline)
    overall.update(
        {
            "schemaVersion": "regression-metrics.v1",
            "comparison": "model-vs-declared-baseline-on-identical-rows",
            "fields": {
                "group": parameters["groupField"],
                "actual": parameters["actualField"],
                "predicted": parameters["predictedField"],
                "baseline": parameters["baselineField"],
            },
            "unit": parameters["unit"],
            "baseline": persistence,
            "rmse_improvement_pct": improvement,
            "improvement_reason": None if persistence["rmse"] else "ZERO_BASELINE_RMSE",
        }
    )
    groups = data[parameters["groupField"]].to_pylist()
    if any(group is None for group in groups):
        raise ValueError("FIELD_REQUIRED")
    per_group = []
    for group in sorted(set(groups), key=str):
        selected = np.asarray([value == group for value in groups])
        model, reference, improvement = _metric_comparison(
            actual[selected], predicted[selected], baseline[selected]
        )
        per_group.append(
            {
                parameters["groupField"]: group,
                **model,
                "baseline_mae": reference["mae"],
                "baseline_rmse": reference["rmse"],
                "baseline_r2": reference["r2"],
                "baseline_r2_reason": reference["r2_reason"],
                "rmse_improvement_pct": improvement,
                "improvement_reason": (None if reference["rmse"] else "ZERO_BASELINE_RMSE"),
                "unit": parameters["unit"],
            }
        )
    return {
        "evaluated": write_table(data, output_dir / "evaluated.parquet"),
        "overall": write_json(overall, output_dir / "metrics.json"),
        "perGroup": write_table(pa.Table.from_pylist(per_group), output_dir / "per-group.parquet"),
    }
