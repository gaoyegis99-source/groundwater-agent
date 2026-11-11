from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from rasterio.windows import Window

FACTORS = ("D", "R", "A", "S", "T", "I", "C")
MAX_CONFIG_BYTES = 2 * 1024 * 1024
INDEX_NODATA = float("nan")
CLASSIFICATION_NODATA = -32768
_TEXT = {"type": "string", "minLength": 1, "maxLength": 512}
_SOURCE = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "id": _TEXT,
        "title": _TEXT,
        "version": _TEXT,
        "locator": _TEXT,
        "verificationStatus": {"enum": ["VERIFIED", "UNVERIFIED", "WITHDRAWN", "SUPERSEDED"]},
    },
    "required": ["id", "title", "version", "locator", "verificationStatus"],
}
_BOUND_PROPERTIES = {
    "minimum": {"type": "number"},
    "maximum": {"type": "number"},
    "minimumInclusive": {"type": "boolean"},
    "maximumInclusive": {"type": "boolean"},
}
_RATING_RULE = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        **_BOUND_PROPERTIES,
        "ruleId": _TEXT,
        "rating": {"type": "integer", "minimum": 1, "maximum": 10},
    },
    "required": ["ruleId", "rating", "minimumInclusive", "maximumInclusive"],
    "anyOf": [{"required": ["minimum"]}, {"required": ["maximum"]}],
}
_FACTOR_RATING = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "band": {"type": "integer", "minimum": 1},
        "unit": _TEXT,
        "basis": _TEXT,
        "source": _SOURCE,
        "rules": {
            "type": "array",
            "minItems": 1,
            "maxItems": 128,
            "items": _RATING_RULE,
        },
    },
    "required": ["band", "unit", "basis", "source", "rules"],
}
_CLASS_RULE = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        **_BOUND_PROPERTIES,
        "code": {"type": "integer", "minimum": 1, "maximum": 32767},
        "label": _TEXT,
        "order": {"type": "integer", "minimum": 1},
    },
    "required": [
        "code",
        "label",
        "order",
        "minimumInclusive",
        "maximumInclusive",
    ],
    "anyOf": [{"required": ["minimum"]}, {"required": ["maximum"]}],
}
CONFIG_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "groundwater-drastic-config.v1",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "schemaVersion": {"const": "groundwater-drastic-config.v1"},
        "configId": _TEXT,
        "title": _TEXT,
        "version": _TEXT,
        "method": {"const": "weighted-sum"},
        "weights": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "source": _SOURCE,
                "values": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        factor: {
                            "type": "number",
                            "exclusiveMinimum": 0,
                            "maximum": 100,
                        }
                        for factor in FACTORS
                    },
                    "required": list(FACTORS),
                },
            },
            "required": ["source", "values"],
        },
        "ratings": {
            "type": "object",
            "additionalProperties": False,
            "properties": {factor: _FACTOR_RATING for factor in FACTORS},
            "required": list(FACTORS),
        },
        "classification": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "source": _SOURCE,
                "classes": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 64,
                    "items": _CLASS_RULE,
                },
            },
            "required": ["source", "classes"],
        },
    },
    "required": [
        "schemaVersion",
        "configId",
        "title",
        "version",
        "method",
        "weights",
        "ratings",
        "classification",
    ],
}


@dataclass(frozen=True)
class Interval:
    minimum: float | None
    maximum: float | None
    minimum_inclusive: bool
    maximum_inclusive: bool
    value: int
    identifier: str
    label: str | None = None
    order: int | None = None

    def matches(self, values: np.ndarray) -> np.ndarray:
        selected = np.ones(values.shape, dtype=bool)
        if self.minimum is not None:
            if self.minimum_inclusive:
                selected &= values >= self.minimum
            else:
                selected &= values > self.minimum
        if self.maximum is not None:
            if self.maximum_inclusive:
                selected &= values <= self.maximum
            else:
                selected &= values < self.maximum
        return selected


@dataclass(frozen=True)
class ParsedConfig:
    raw: dict[str, Any]
    weights: dict[str, float]
    rating_rules: dict[str, tuple[Interval, ...]]
    class_rules: tuple[Interval, ...]


def _artifact(path: Path) -> dict[str, Any]:
    return {
        "kind": "artifact",
        "path": str(path),
        "fileName": path.name,
        "mediaType": "image/tiff",
        "format": "geotiff",
    }


def _value(value: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "value", "value": value}


def _reject_nonstandard_constant(value: str) -> None:
    raise ValueError(value)


def _read_config(item: Any) -> dict[str, Any]:
    if item.format_id != "json" or item.path.stat().st_size > MAX_CONFIG_BYTES:
        raise ValueError("CONFIG_INVALID")
    try:
        value = json.loads(
            item.path.read_text(encoding="utf-8"),
            parse_constant=_reject_nonstandard_constant,
        )
        Draft202012Validator(CONFIG_SCHEMA).validate(value)
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValidationError,
        ValueError,
    ) as exc:
        raise ValueError("CONFIG_INVALID") from exc
    return value


def _number(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("CONFIG_INVALID")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("CONFIG_INVALID") from exc
    if not math.isfinite(result):
        raise ValueError("CONFIG_INVALID")
    return result


def _interval(
    raw: Mapping[str, Any],
    *,
    value: int,
    identifier: str,
    label: str | None = None,
    order: int | None = None,
) -> Interval:
    minimum = _number(raw["minimum"]) if "minimum" in raw else None
    maximum = _number(raw["maximum"]) if "maximum" in raw else None
    minimum_inclusive = raw["minimumInclusive"]
    maximum_inclusive = raw["maximumInclusive"]
    if minimum is None and minimum_inclusive:
        raise ValueError("CONFIG_INVALID")
    if maximum is None and maximum_inclusive:
        raise ValueError("CONFIG_INVALID")
    if (
        minimum is not None
        and maximum is not None
        and (
            minimum > maximum
            or (minimum == maximum and not (minimum_inclusive and maximum_inclusive))
        )
    ):
        raise ValueError("CONFIG_INVALID")
    return Interval(
        minimum=minimum,
        maximum=maximum,
        minimum_inclusive=minimum_inclusive,
        maximum_inclusive=maximum_inclusive,
        value=value,
        identifier=identifier,
        label=label,
        order=order,
    )


def _sort_key(interval: Interval) -> tuple[float, int, float]:
    return (
        -math.inf if interval.minimum is None else interval.minimum,
        0 if interval.minimum_inclusive else 1,
        math.inf if interval.maximum is None else interval.maximum,
    )


def _validate_disjoint(intervals: Sequence[Interval]) -> tuple[Interval, ...]:
    ordered = tuple(sorted(intervals, key=_sort_key))
    for left, right in pairwise(ordered):
        if left.maximum is None or right.minimum is None:
            raise ValueError("CONFIG_INVALID")
        if left.maximum > right.minimum or (
            left.maximum == right.minimum and left.maximum_inclusive and right.minimum_inclusive
        ):
            raise ValueError("CONFIG_INVALID")
    return ordered


def _require_verified(source: Mapping[str, Any]) -> None:
    if source["verificationStatus"] != "VERIFIED":
        raise ValueError("SOURCE_NOT_VERIFIED")


def _parse_config(raw: dict[str, Any]) -> ParsedConfig:
    _require_verified(raw["weights"]["source"])
    _require_verified(raw["classification"]["source"])
    weights = {factor: _number(raw["weights"]["values"][factor]) for factor in FACTORS}
    rating_rules: dict[str, tuple[Interval, ...]] = {}
    for factor in FACTORS:
        rating = raw["ratings"][factor]
        _require_verified(rating["source"])
        rules = [
            _interval(
                item,
                value=item["rating"],
                identifier=item["ruleId"],
            )
            for item in rating["rules"]
        ]
        if len({rule.identifier for rule in rules}) != len(rules):
            raise ValueError("CONFIG_INVALID")
        rating_rules[factor] = _validate_disjoint(rules)

    class_rules = [
        _interval(
            item,
            value=item["code"],
            identifier=str(item["code"]),
            label=item["label"],
            order=item["order"],
        )
        for item in raw["classification"]["classes"]
    ]
    if len({rule.value for rule in class_rules}) != len(class_rules) or len(
        {rule.order for rule in class_rules}
    ) != len(class_rules):
        raise ValueError("CONFIG_INVALID")
    return ParsedConfig(
        raw=raw,
        weights=weights,
        rating_rules=rating_rules,
        class_rules=_validate_disjoint(class_rules),
    )


def _apply_intervals(
    values: np.ndarray,
    valid: np.ndarray,
    intervals: Sequence[Interval],
) -> tuple[np.ndarray, np.ndarray]:
    result = np.full(values.shape, np.nan, dtype=np.float64)
    matched = np.zeros(values.shape, dtype=bool)
    for interval in intervals:
        selected = valid & interval.matches(values)
        result[selected] = interval.value
        matched |= selected
    return result, matched


def _grid_summary(dataset: Any) -> dict[str, Any]:
    return {
        "crs": dataset.crs.to_string(),
        "width": dataset.width,
        "height": dataset.height,
        "transform": list(dataset.transform)[:6],
        "resolution": [abs(float(dataset.transform.a)), abs(float(dataset.transform.e))],
        "alignmentPolicy": "exact; no implicit resampling or reprojection",
    }


def run(
    inputs: Mapping[str, Sequence[Any]],
    parameters: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    expected_ports = {*FACTORS, "config"}
    if set(inputs) != expected_ports or any(len(inputs[name]) != 1 for name in expected_ports):
        raise ValueError("INPUT_CARDINALITY_INVALID")
    if parameters:
        raise ValueError("PARAMETER_INVALID")
    if any(inputs[factor][0].format_id not in {"geotiff", "cog"} for factor in FACTORS):
        raise ValueError("INPUT_FORMAT_UNSUPPORTED")

    config = _parse_config(_read_config(inputs["config"][0]))
    output_dir.mkdir(parents=True, exist_ok=True)
    index_path = output_dir / "drastic-index.tif"
    classification_path = output_dir / "drastic-classification.tif"
    factor_uncovered = {factor: 0 for factor in FACTORS}
    class_counts = {rule.value: 0 for rule in config.class_rules}
    total_cells = 0
    all_inputs_valid_cells = 0
    input_nodata_cells = 0
    rating_uncovered_cells = 0
    index_valid_cells = 0
    classification_uncovered_cells = 0
    classification_valid_cells = 0
    index_sum = 0.0
    index_minimum: float | None = None
    index_maximum: float | None = None

    with ExitStack() as stack:
        datasets = {
            factor: stack.enter_context(rasterio.open(inputs[factor][0].path)) for factor in FACTORS
        }
        reference = datasets["D"]
        if reference.crs is None:
            raise ValueError("CRS_REQUIRED")
        reference_grid = (
            reference.crs,
            reference.width,
            reference.height,
            reference.transform,
        )
        for factor, dataset in datasets.items():
            if dataset.crs is None:
                raise ValueError("CRS_REQUIRED")
            if (
                dataset.crs,
                dataset.width,
                dataset.height,
                dataset.transform,
            ) != reference_grid:
                raise ValueError("GRID_MISMATCH")
            if config.raw["ratings"][factor]["band"] not in dataset.indexes:
                raise ValueError("BAND_INVALID")

        index_profile = reference.profile.copy()
        index_profile.update(
            driver="GTiff",
            count=1,
            dtype="float32",
            nodata=INDEX_NODATA,
            compress="deflate",
        )
        classification_profile = reference.profile.copy()
        classification_profile.update(
            driver="GTiff",
            count=1,
            dtype="int16",
            nodata=CLASSIFICATION_NODATA,
            compress="deflate",
        )
        with (
            rasterio.open(index_path, "w", **index_profile) as index_target,
            rasterio.open(
                classification_path, "w", **classification_profile
            ) as classification_target,
        ):
            for row_offset in range(0, reference.height, 512):
                height = min(512, reference.height - row_offset)
                for column_offset in range(0, reference.width, 512):
                    width = min(512, reference.width - column_offset)
                    window = Window(column_offset, row_offset, width, height)
                    source_values: dict[str, np.ndarray] = {}
                    source_valid: dict[str, np.ndarray] = {}
                    for factor, dataset in datasets.items():
                        band = config.raw["ratings"][factor]["band"]
                        array = dataset.read(band, window=window, masked=True)
                        values = np.asarray(array.data, dtype=np.float64)
                        valid = ~np.ma.getmaskarray(array) & np.isfinite(values)
                        source_values[factor] = values
                        source_valid[factor] = valid

                    all_valid = np.logical_and.reduce([source_valid[factor] for factor in FACTORS])
                    weighted_sum = np.zeros(all_valid.shape, dtype=np.float64)
                    all_rated = np.ones(all_valid.shape, dtype=bool)
                    for factor in FACTORS:
                        ratings, matched = _apply_intervals(
                            source_values[factor],
                            source_valid[factor],
                            config.rating_rules[factor],
                        )
                        factor_uncovered[factor] += int(
                            np.count_nonzero(source_valid[factor] & ~matched)
                        )
                        all_rated &= matched
                        weighted_sum += np.nan_to_num(ratings) * config.weights[factor]

                    index_valid = all_valid & all_rated
                    index_values = np.full(all_valid.shape, np.nan, dtype=np.float64)
                    index_values[index_valid] = weighted_sum[index_valid]
                    classifications, class_matched = _apply_intervals(
                        index_values,
                        index_valid,
                        config.class_rules,
                    )
                    classification_valid = index_valid & class_matched
                    classification_values = np.full(
                        all_valid.shape,
                        CLASSIFICATION_NODATA,
                        dtype=np.int16,
                    )
                    classification_values[classification_valid] = classifications[
                        classification_valid
                    ].astype(np.int16)

                    count = int(all_valid.size)
                    total_cells += count
                    all_inputs_valid_cells += int(np.count_nonzero(all_valid))
                    input_nodata_cells += int(np.count_nonzero(~all_valid))
                    rating_uncovered_cells += int(np.count_nonzero(all_valid & ~all_rated))
                    index_count = int(np.count_nonzero(index_valid))
                    index_valid_cells += index_count
                    classification_uncovered_cells += int(
                        np.count_nonzero(index_valid & ~class_matched)
                    )
                    classification_valid_cells += int(np.count_nonzero(classification_valid))
                    if index_count:
                        selected_index = index_values[index_valid]
                        local_minimum = float(np.min(selected_index))
                        local_maximum = float(np.max(selected_index))
                        index_sum += float(np.sum(selected_index, dtype=np.float64))
                        index_minimum = (
                            local_minimum
                            if index_minimum is None
                            else min(index_minimum, local_minimum)
                        )
                        index_maximum = (
                            local_maximum
                            if index_maximum is None
                            else max(index_maximum, local_maximum)
                        )
                    for code in class_counts:
                        class_counts[code] += int(
                            np.count_nonzero(classification_valid & (classifications == code))
                        )

                    index_target.write(
                        index_values.astype(np.float32),
                        1,
                        window=window,
                    )
                    classification_target.write(
                        classification_values,
                        1,
                        window=window,
                    )

        grid = _grid_summary(reference)

    rating_ratio = (
        rating_uncovered_cells / all_inputs_valid_cells if all_inputs_valid_cells else None
    )
    output_nodata_cells = total_cells - classification_valid_cells
    summary = {
        "schemaVersion": "groundwater-drastic-summary.v1",
        "config": {
            "id": config.raw["configId"],
            "title": config.raw["title"],
            "version": config.raw["version"],
        },
        "method": "weighted-sum",
        "formula": "sum(weight[factor] * rating[factor])",
        "factorWeights": config.raw["weights"]["values"],
        "weightSource": config.raw["weights"]["source"],
        "ruleVersions": {
            factor: config.raw["ratings"][factor]["source"]["version"] for factor in FACTORS
        },
        "classificationSource": config.raw["classification"]["source"],
        "grid": grid,
        "factors": [
            {
                "factor": factor,
                "band": config.raw["ratings"][factor]["band"],
                "unit": config.raw["ratings"][factor]["unit"],
                "basis": config.raw["ratings"][factor]["basis"],
                "weight": config.weights[factor],
                "ratingRuleCount": len(config.rating_rules[factor]),
                "ratingSource": config.raw["ratings"][factor]["source"],
                "uncoveredCellCount": factor_uncovered[factor],
            }
            for factor in FACTORS
        ],
        "classes": [
            {
                "code": rule.value,
                "label": rule.label,
                "order": rule.order,
                "cellCount": class_counts[rule.value],
                "ratioOfClassificationValid": (
                    class_counts[rule.value] / classification_valid_cells
                    if classification_valid_cells
                    else None
                ),
            }
            for rule in sorted(config.class_rules, key=lambda item: item.order or 0)
        ],
        "counts": {
            "totalCellCount": total_cells,
            "allInputsValidCellCount": all_inputs_valid_cells,
            "inputNoDataCellCount": input_nodata_cells,
            "ratingUncoveredCellCount": rating_uncovered_cells,
            "indexValidCellCount": index_valid_cells,
            "classificationUncoveredCellCount": classification_uncovered_cells,
            "classificationValidCellCount": classification_valid_cells,
            "outputNoDataCellCount": output_nodata_cells,
        },
        "ratingUncoveredRatio": rating_ratio,
        "outputNoDataRatio": output_nodata_cells / total_cells if total_cells else None,
        "indexStatistics": {
            "minimum": index_minimum,
            "maximum": index_maximum,
            "mean": index_sum / index_valid_cells if index_valid_cells else None,
        },
        "noDataPolicy": ("propagate any source NoData/non-finite value or unmatched factor rating"),
        "interpretation": (
            "DRASTIC is a relative intrinsic groundwater vulnerability index under "
            "the supplied ratings and weights; it is not a pollution probability."
        ),
        "limitations": [
            (
                "All seven rasters must already be exactly aligned; this operator "
                "performs no implicit resampling or reprojection."
            ),
            (
                "Ratings, weights and index classes are applied as supplied and "
                "verified for provenance, not independently scientifically validated "
                "by the platform."
            ),
            (
                "The index does not model contaminant source, release likelihood, "
                "concentration, travel time, exposure or consequence."
            ),
        ],
    }
    return {
        "index": _artifact(index_path),
        "classification": _artifact(classification_path),
        "summary": _value(summary),
    }
