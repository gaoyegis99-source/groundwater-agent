from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from pathlib import Path
from typing import Any

import pyarrow as pa
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from moperators.operators.table_document_common_v1 import (
    MAX_RULE_BYTES,
    one,
    read_table,
    require_fields,
    summary,
    write_table,
)

_TEXT = {"type": "string", "minLength": 1, "maxLength": 256}
_BOUND = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "minimum": {"type": "number"},
        "maximum": {"type": "number"},
        "minimumInclusive": {"type": "boolean"},
        "maximumInclusive": {"type": "boolean"},
    },
    "required": ["minimumInclusive", "maximumInclusive"],
    "anyOf": [{"required": ["minimum"]}, {"required": ["maximum"]}],
}
_RULE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "table-classification-rules.v1",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "schemaVersion": {"const": "table-classification-rules.v1"},
        "ruleSetId": _TEXT,
        "title": _TEXT,
        "version": _TEXT,
        "overallMethod": {"const": "worst-class-order"},
        "classes": {
            "type": "array",
            "minItems": 1,
            "maxItems": 64,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "code": _TEXT,
                    "label": _TEXT,
                    "order": {"type": "integer", "minimum": 1},
                },
                "required": ["code", "label", "order"],
            },
        },
        "indicators": {
            "type": "array",
            "minItems": 1,
            "maxItems": 64,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "indicator": _TEXT,
                    "unit": _TEXT,
                    "basis": _TEXT,
                    "verificationStatus": {
                        "enum": ["VERIFIED", "UNVERIFIED", "WITHDRAWN", "SUPERSEDED"]
                    },
                    "source": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "id": _TEXT,
                            "title": _TEXT,
                            "version": _TEXT,
                            "locator": _TEXT,
                        },
                        "required": ["id", "title", "version", "locator"],
                    },
                    "classifications": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 64,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "classCode": _TEXT,
                                "bounds": {
                                    "type": "array",
                                    "minItems": 1,
                                    "maxItems": 32,
                                    "items": _BOUND,
                                },
                            },
                            "required": ["classCode", "bounds"],
                        },
                    },
                },
                "required": [
                    "indicator",
                    "unit",
                    "basis",
                    "source",
                    "verificationStatus",
                    "classifications",
                ],
            },
        },
    },
    "required": [
        "schemaVersion",
        "ruleSetId",
        "title",
        "version",
        "overallMethod",
        "classes",
        "indicators",
    ],
}
_BINDING_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "table-classification-bindings.v1",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "schemaVersion": {"const": "table-classification-bindings.v1"},
        "bindings": {
            "type": "array",
            "minItems": 1,
            "maxItems": 64,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "indicator": _TEXT,
                    "field": _TEXT,
                    "unit": _TEXT,
                    "basis": _TEXT,
                },
                "required": ["indicator", "field", "unit", "basis"],
            },
        },
    },
    "required": ["schemaVersion", "bindings"],
}


@dataclass(frozen=True)
class Classification:
    code: str
    label: str
    order: int


@dataclass(frozen=True)
class Bound:
    classification: Classification
    minimum: Decimal | None
    maximum: Decimal | None
    minimum_inclusive: bool
    maximum_inclusive: bool

    def matches(self, value: Decimal) -> bool:
        lower = (
            self.minimum is None
            or value > self.minimum
            or (self.minimum_inclusive and value == self.minimum)
        )
        upper = (
            self.maximum is None
            or value < self.maximum
            or (self.maximum_inclusive and value == self.maximum)
        )
        return lower and upper


@dataclass(frozen=True)
class IndicatorRule:
    indicator: str
    unit: str
    basis: str
    verification_status: str
    source: dict[str, str]
    bounds: tuple[Bound, ...]


@dataclass(frozen=True)
class Binding:
    indicator: str
    field: str
    unit: str
    basis: str


def _load_document(source: Any, schema: dict[str, Any]) -> dict[str, Any]:
    if source.format_id != "json" or source.path.stat().st_size > MAX_RULE_BYTES:
        raise ValueError("RULESET_INVALID")
    try:
        value = json.loads(source.path.read_text(encoding="utf-8"), parse_float=Decimal)
        Draft202012Validator(schema).validate(value)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        raise ValueError("RULESET_INVALID") from exc
    return value


def _number(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("RULESET_INVALID")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    else:
        if not math.isfinite(value):
            raise ValueError("RULESET_INVALID")
        result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("RULESET_INVALID")
    return result


def _bound(raw: dict[str, Any], classification: Classification) -> Bound:
    minimum = _number(raw["minimum"]) if "minimum" in raw else None
    maximum = _number(raw["maximum"]) if "maximum" in raw else None
    minimum_inclusive = raw["minimumInclusive"]
    maximum_inclusive = raw["maximumInclusive"]
    if minimum is None and minimum_inclusive:
        raise ValueError("RULESET_INVALID")
    if maximum is None and maximum_inclusive:
        raise ValueError("RULESET_INVALID")
    if (
        minimum is not None
        and maximum is not None
        and (
            minimum > maximum
            or (minimum == maximum and not (minimum_inclusive and maximum_inclusive))
        )
    ):
        raise ValueError("RULESET_INVALID")
    return Bound(
        classification=classification,
        minimum=minimum,
        maximum=maximum,
        minimum_inclusive=minimum_inclusive,
        maximum_inclusive=maximum_inclusive,
    )


def _sort_key(bound: Bound) -> tuple[Decimal, int, Decimal]:
    return (
        Decimal("-Infinity") if bound.minimum is None else bound.minimum,
        0 if bound.minimum_inclusive else 1,
        Decimal("Infinity") if bound.maximum is None else bound.maximum,
    )


def _validate_no_overlap(bounds: tuple[Bound, ...]) -> None:
    ordered = sorted(bounds, key=_sort_key)
    for left, right in pairwise(ordered):
        if left.maximum is None or right.minimum is None:
            raise ValueError("RULESET_INVALID")
        if left.maximum > right.minimum or (
            left.maximum == right.minimum and left.maximum_inclusive and right.minimum_inclusive
        ):
            raise ValueError("RULESET_INVALID")


def _parse_rules(
    raw: dict[str, Any],
) -> tuple[dict[str, Classification], tuple[IndicatorRule, ...]]:
    classes = [
        Classification(item["code"], item["label"], item["order"]) for item in raw["classes"]
    ]
    if len({item.code for item in classes}) != len(classes) or len(
        {item.order for item in classes}
    ) != len(classes):
        raise ValueError("RULESET_INVALID")
    classes_by_code = {item.code: item for item in classes}
    indicator_names: set[str] = set()
    indicators: list[IndicatorRule] = []
    for item in raw["indicators"]:
        name = item["indicator"]
        if name in indicator_names:
            raise ValueError("RULESET_INVALID")
        indicator_names.add(name)
        classification_codes: set[str] = set()
        bounds: list[Bound] = []
        for classification in item["classifications"]:
            code = classification["classCode"]
            if code in classification_codes or code not in classes_by_code:
                raise ValueError("RULESET_INVALID")
            classification_codes.add(code)
            bounds.extend(
                _bound(bound, classes_by_code[code]) for bound in classification["bounds"]
            )
        frozen_bounds = tuple(sorted(bounds, key=_sort_key))
        _validate_no_overlap(frozen_bounds)
        indicators.append(
            IndicatorRule(
                indicator=name,
                unit=item["unit"],
                basis=item["basis"],
                verification_status=item["verificationStatus"],
                source=item["source"],
                bounds=frozen_bounds,
            )
        )
    return classes_by_code, tuple(indicators)


def _parse_bindings(raw: dict[str, Any]) -> dict[str, Binding]:
    values = raw["bindings"]
    result: dict[str, Binding] = {}
    for item in values:
        binding = Binding(**item)
        if binding.indicator in result:
            raise ValueError("RULESET_INVALID")
        result[binding.indicator] = binding
    return result


def _declared_nulls(source: Any) -> frozenset[str]:
    metadata = source.metadata
    if not isinstance(metadata, dict):
        raise ValueError("INPUT_METADATA_INVALID")
    contract = metadata.get("dataContract", {})
    if not isinstance(contract, dict):
        raise ValueError("INPUT_METADATA_INVALID")
    values = contract.get("nullValues", [])
    if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
        raise ValueError("INPUT_METADATA_INVALID")
    return frozenset(values)


def _observed_number(
    value: Any, null_values: frozenset[str]
) -> tuple[Decimal | None, str | None, str | None]:
    text = None if value is None else str(value)
    if value is None or (
        isinstance(value, str)
        and (not value.strip() or value in null_values or value.strip() in null_values)
    ):
        return None, "MISSING_VALUE", text
    try:
        if isinstance(value, bool):
            raise ValueError
        if isinstance(value, Decimal):
            number = value
        elif isinstance(value, int):
            number = Decimal(value)
        elif isinstance(value, float):
            if not math.isfinite(value):
                return None, "NON_FINITE_VALUE", text
            number = Decimal(str(value))
        elif isinstance(value, str):
            number = Decimal(value.strip())
        else:
            raise ValueError
    except (InvalidOperation, TypeError, ValueError):
        return None, "NON_NUMERIC_VALUE", text
    if not number.is_finite():
        return None, "NON_FINITE_VALUE", text
    return number, None, text


def _outside_rule_range(value: Decimal, bounds: tuple[Bound, ...]) -> bool:
    first, last = bounds[0], bounds[-1]
    below = first.minimum is not None and (
        value < first.minimum or (value == first.minimum and not first.minimum_inclusive)
    )
    above = last.maximum is not None and (
        value > last.maximum or (value == last.maximum and not last.maximum_inclusive)
    )
    return below or above


def _lossless_float(value: Decimal | None) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(result) or Decimal(str(result)) != value:
        return None
    return result


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _evaluate(
    value: Any,
    rule: IndicatorRule,
    null_values: frozenset[str],
) -> tuple[float | None, str | None, Classification | None, str | None]:
    if rule.verification_status != "VERIFIED":
        text = None if value is None else str(value)
        return None, text, None, "RULE_NOT_VERIFIED"
    number, reason, text = _observed_number(value, null_values)
    if reason is not None or number is None:
        return None, text, None, reason
    matches = [bound.classification for bound in rule.bounds if bound.matches(number)]
    if len(matches) == 1:
        return _lossless_float(number), text, matches[0], None
    if matches:
        raise ValueError("RULESET_INVALID")
    reason = "OUT_OF_RANGE" if _outside_rule_range(number, rule.bounds) else "NO_MATCHING_CLASS"
    return _lossless_float(number), text, None, reason


_INDICATOR_SCHEMA = pa.schema(
    [
        ("row_number", pa.int64()),
        ("indicator", pa.string()),
        ("field", pa.string()),
        ("unit", pa.string()),
        ("basis", pa.string()),
        ("observed_value", pa.float64()),
        ("observed_value_text", pa.string()),
        ("status", pa.string()),
        ("class_code", pa.string()),
        ("class_label", pa.string()),
        ("class_order", pa.int64()),
        ("unevaluated_reason", pa.string()),
        ("verification_status", pa.string()),
        ("source_id", pa.string()),
        ("source_version", pa.string()),
        ("source_locator", pa.string()),
    ]
)

_RULE_SUMMARY_SCHEMA = pa.schema(
    [
        ("indicator", pa.string()),
        ("field", pa.string()),
        ("unit", pa.string()),
        ("basis", pa.string()),
        ("verification_status", pa.string()),
        ("source_id", pa.string()),
        ("source_title", pa.string()),
        ("source_version", pa.string()),
        ("source_locator", pa.string()),
        ("class_code", pa.string()),
        ("class_label", pa.string()),
        ("class_order", pa.int64()),
        ("bound_index", pa.int64()),
        ("minimum", pa.float64()),
        ("minimum_text", pa.string()),
        ("minimum_inclusive", pa.bool_()),
        ("maximum", pa.float64()),
        ("maximum_text", pa.string()),
        ("maximum_inclusive", pa.bool_()),
    ]
)

_OUTPUT_FIELDS = (
    "classification_row_number",
    "overall_class_code",
    "overall_class_label",
    "overall_class_order",
    "overall_status",
    "overall_unevaluated_reason",
    "overall_unevaluated_details",
)


def _rule_summary_rows(
    indicators: tuple[IndicatorRule, ...], bindings: dict[str, Binding]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rule in indicators:
        binding = bindings[rule.indicator]
        for index, bound in enumerate(rule.bounds, start=1):
            rows.append(
                {
                    "indicator": rule.indicator,
                    "field": binding.field,
                    "unit": rule.unit,
                    "basis": rule.basis,
                    "verification_status": rule.verification_status,
                    "source_id": rule.source["id"],
                    "source_title": rule.source["title"],
                    "source_version": rule.source["version"],
                    "source_locator": rule.source["locator"],
                    "class_code": bound.classification.code,
                    "class_label": bound.classification.label,
                    "class_order": bound.classification.order,
                    "bound_index": index,
                    "minimum": _lossless_float(bound.minimum),
                    "minimum_text": _decimal_text(bound.minimum),
                    "minimum_inclusive": bound.minimum_inclusive,
                    "maximum": _lossless_float(bound.maximum),
                    "maximum_text": _decimal_text(bound.maximum),
                    "maximum_inclusive": bound.maximum_inclusive,
                }
            )
    return rows


def run(
    inputs: dict[str, list[Any]], parameters: dict[str, Any], output_dir: Path
) -> dict[str, Any]:
    if parameters:
        raise ValueError("PARAMETER_INVALID")
    data_source = one(inputs, "data")
    rules_raw = _load_document(one(inputs, "rules"), _RULE_SCHEMA)
    classes_by_code, indicators = _parse_rules(rules_raw)
    bindings = _parse_bindings(_load_document(one(inputs, "bindings"), _BINDING_SCHEMA))
    expected = {rule.indicator for rule in indicators}
    if set(bindings) != expected:
        raise ValueError("INDICATOR_BINDING_MISMATCH")
    for rule in indicators:
        binding = bindings[rule.indicator]
        if binding.unit != rule.unit:
            raise ValueError("UNIT_MISMATCH")
        if binding.basis != rule.basis:
            raise ValueError("BASIS_MISMATCH")

    table = read_table(data_source)
    require_fields(table, [bindings[rule.indicator].field for rule in indicators])
    if set(_OUTPUT_FIELDS).intersection(table.column_names):
        raise ValueError("OUTPUT_FIELD_CONFLICT")
    null_values = _declared_nulls(data_source)

    indicator_rows: list[dict[str, Any]] = []
    overall_codes: list[str | None] = []
    overall_labels: list[str | None] = []
    overall_orders: list[int | None] = []
    overall_statuses: list[str] = []
    overall_reasons: list[str | None] = []
    overall_details: list[str | None] = []
    overall_counts: Counter[str] = Counter()
    indicator_counts = {
        rule.indicator: {
            "evaluatedCount": 0,
            "unevaluatedCount": 0,
            "classCounts": Counter(),
            "unevaluatedReasons": Counter(),
        }
        for rule in indicators
    }

    for row_number, row in enumerate(table.to_pylist(), start=1):
        row_classes: list[Classification] = []
        unevaluated: list[dict[str, str]] = []
        for rule in indicators:
            binding = bindings[rule.indicator]
            number, text, classification, reason = _evaluate(row[binding.field], rule, null_values)
            status = "EVALUATED" if classification is not None else "NOT_EVALUATED"
            counts = indicator_counts[rule.indicator]
            if classification is None:
                counts["unevaluatedCount"] += 1
                counts["unevaluatedReasons"][reason] += 1
                unevaluated.append({"indicator": rule.indicator, "reason": str(reason)})
            else:
                counts["evaluatedCount"] += 1
                counts["classCounts"][classification.code] += 1
                row_classes.append(classification)
            indicator_rows.append(
                {
                    "row_number": row_number,
                    "indicator": rule.indicator,
                    "field": binding.field,
                    "unit": rule.unit,
                    "basis": rule.basis,
                    "observed_value": number,
                    "observed_value_text": text,
                    "status": status,
                    "class_code": classification.code if classification else None,
                    "class_label": classification.label if classification else None,
                    "class_order": classification.order if classification else None,
                    "unevaluated_reason": reason,
                    "verification_status": rule.verification_status,
                    "source_id": rule.source["id"],
                    "source_version": rule.source["version"],
                    "source_locator": rule.source["locator"],
                }
            )
        if unevaluated:
            overall_codes.append(None)
            overall_labels.append(None)
            overall_orders.append(None)
            overall_statuses.append("NOT_EVALUATED")
            overall_reasons.append("REQUIRED_INDICATOR_NOT_EVALUATED")
            overall_details.append(
                json.dumps(
                    unevaluated,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        else:
            worst = max(row_classes, key=lambda item: item.order)
            overall_codes.append(worst.code)
            overall_labels.append(worst.label)
            overall_orders.append(worst.order)
            overall_statuses.append("EVALUATED")
            overall_reasons.append(None)
            overall_details.append(None)
            overall_counts[worst.code] += 1

    classified = table
    additions: tuple[tuple[str, list[Any], pa.DataType], ...] = (
        ("classification_row_number", list(range(1, table.num_rows + 1)), pa.int64()),
        ("overall_class_code", overall_codes, pa.string()),
        ("overall_class_label", overall_labels, pa.string()),
        ("overall_class_order", overall_orders, pa.int64()),
        ("overall_status", overall_statuses, pa.string()),
        ("overall_unevaluated_reason", overall_reasons, pa.string()),
        ("overall_unevaluated_details", overall_details, pa.string()),
    )
    for name, values, data_type in additions:
        classified = classified.append_column(name, pa.array(values, type=data_type))

    class_codes = [
        item.code for item in sorted(classes_by_code.values(), key=lambda item: item.order)
    ]
    normalized_indicator_counts = {
        name: {
            "evaluatedCount": value["evaluatedCount"],
            "unevaluatedCount": value["unevaluatedCount"],
            "classCounts": {code: value["classCounts"].get(code, 0) for code in class_codes},
            "unevaluatedReasons": dict(sorted(value["unevaluatedReasons"].items())),
        }
        for name, value in indicator_counts.items()
    }
    evaluated_rows = sum(overall_counts.values())
    summary_value = {
        "schemaVersion": "table-classification-summary.v1",
        "scope": {"basis": "FULL_STAGED_INPUT", "inputFormat": data_source.format_id},
        "ruleSet": {
            "id": rules_raw["ruleSetId"],
            "title": rules_raw["title"],
            "version": rules_raw["version"],
            "overallMethod": rules_raw["overallMethod"],
        },
        "rowCount": table.num_rows,
        "indicatorCount": len(indicators),
        "boundCount": sum(len(rule.bounds) for rule in indicators),
        "evaluatedRowCount": evaluated_rows,
        "unevaluatedRowCount": table.num_rows - evaluated_rows,
        "overallClassCounts": {code: overall_counts.get(code, 0) for code in class_codes},
        "indicatorCounts": normalized_indicator_counts,
        "verificationStatusCounts": dict(
            sorted(Counter(rule.verification_status for rule in indicators).items())
        ),
        "conventions": {
            "intervals": "explicit-open-closed-multi-interval",
            "missingValues": "not-evaluated",
            "nonNumericAndNonFiniteValues": "not-evaluated",
            "outOfRangeAndRuleGaps": "not-evaluated",
            "unverifiedRules": "not-evaluated",
            "units": "exact-match-no-conversion",
            "basis": "exact-match-no-inference",
            "overall": "worst-class-order-requires-all-indicators-evaluated",
        },
        "interpretation": (
            "Each input row is classified independently with the supplied versioned rules; "
            "the overall class is the greatest class order only when every indicator is evaluated."
        ),
        "limitations": [
            "Applying a verified rule artifact is not scientific validation of the rule source, "
            "sampling design, measurements, or suitability for a decision.",
            "Units and measurement basis are checked exactly; this operator performs no unit "
            "conversion or semantic inference.",
            "A worst-class-order aggregate is a declared deterministic convention, not an "
            "inferred weighting or regulatory decision.",
        ],
    }
    rule_rows = _rule_summary_rows(indicators, bindings)
    return {
        "classified": write_table(classified, output_dir / "classified.parquet"),
        "indicatorClassifications": write_table(
            pa.Table.from_pylist(indicator_rows, schema=_INDICATOR_SCHEMA),
            output_dir / "indicator-classifications.parquet",
        ),
        "ruleSummary": write_table(
            pa.Table.from_pylist(rule_rows, schema=_RULE_SUMMARY_SCHEMA),
            output_dir / "rule-summary.parquet",
        ),
        "summary": summary(summary_value),
    }
