from __future__ import annotations

import csv
import hashlib
import json
import math
from collections.abc import Mapping
from pathlib import Path
from statistics import fmean

TOOL_DESCRIPTORS = {
    "csv-statistics.validate": "1.0.0",
    "csv-statistics.calculate": "1.0.0",
    "csv-statistics.blocked": "1.0.0",
}
RULE_REF = "csv-statistics.input-v1"


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _checked_values(arguments: Mapping[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    inputs = {"file": arguments["file"], "column": arguments["column"]}
    checked_arguments = {**inputs, "profile": arguments["profile"]}
    return inputs, checked_arguments


def _report(
    status: str,
    arguments: Mapping[str, object],
    *,
    issue: tuple[str, str, str] | None = None,
) -> dict[str, object]:
    inputs, checked_arguments = _checked_values(arguments)
    issues = []
    if issue is not None:
        path, code, message = issue
        issues.append({"path": path, "code": code, "message": message})
    return {
        "schema_version": "v6-validation-report-v1",
        "status": status,
        "rule_ref": RULE_REF,
        "input_digest": _digest(inputs),
        "arguments_digest": _digest(checked_arguments),
        "issues": issues,
        "evidence_refs": [],
    }


def _workspace(context: object) -> object:
    workspace = getattr(context, "files", None)
    if workspace is None:
        raise ValueError("CSV_FILE_WORKSPACE_REQUIRED")
    return workspace


def _read_values(
    arguments: Mapping[str, object], context: object
) -> tuple[list[float] | None, tuple[str, str, str] | None]:
    file_value = arguments.get("file")
    column = arguments.get("column")
    profile = arguments.get("profile")
    if not isinstance(file_value, Mapping) or not isinstance(column, str) or not column:
        return None, ("/file", "CSV_INPUT_INVALID", "File reference and column are required.")
    if not isinstance(profile, Mapping):
        return None, ("/profile", "CSV_PROFILE_INVALID", "A frozen parameter profile is required.")
    size = file_value.get("size_bytes")
    max_bytes = profile.get("max_input_bytes")
    max_rows = profile.get("max_rows")
    if (
        not isinstance(size, int)
        or not isinstance(max_bytes, int)
        or not isinstance(max_rows, int)
        or size > max_bytes
    ):
        return None, ("/file", "CSV_FILE_TOO_LARGE", "CSV input exceeds the selected profile limit.")
    try:
        path = _workspace(context).resolve_input(file_value)
    except Exception:
        return None, ("/file", "CSV_FILE_UNAVAILABLE", "The staged CSV input is unavailable or changed.")
    if not isinstance(path, Path):
        path = Path(path)
    try:
        with path.open("r", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream, strict=True)
            fields = reader.fieldnames
            if not fields or len(fields) != len(set(fields)) or column not in fields:
                return None, ("/column", "CSV_COLUMN_MISSING", "The required column is absent or duplicated.")
            values: list[float] = []
            for index, row in enumerate(reader, start=1):
                if index > max_rows:
                    return None, ("/file", "CSV_ROW_LIMIT", "CSV input exceeds the selected row limit.")
                raw = row.get(column)
                try:
                    number = float(raw) if raw is not None and raw.strip() else math.nan
                except (AttributeError, ValueError):
                    number = math.nan
                if not math.isfinite(number):
                    return None, (
                        f"/rows/{index}/{column}",
                        "CSV_VALUE_NOT_NUMBER",
                        "Every selected value must be a finite number.",
                    )
                values.append(number)
    except (OSError, UnicodeError, csv.Error):
        return None, ("/file", "CSV_PARSE_ERROR", "CSV input could not be decoded safely.")
    if not values:
        return None, ("/file", "CSV_NO_DATA", "CSV input contains no data rows.")
    return values, None


def validate(arguments: Mapping[str, object], context: object) -> Mapping[str, object]:
    _values, issue = _read_values(arguments, context)
    return _report("PASS" if issue is None else "FAIL", arguments, issue=issue)


def _receipt_matches(arguments: Mapping[str, object]) -> bool:
    report = arguments.get("validation")
    if not isinstance(report, Mapping):
        return False
    inputs, checked_arguments = _checked_values(arguments)
    return (
        report.get("schema_version") == "v6-validation-report-v1"
        and report.get("status") == "PASS"
        and report.get("rule_ref") == RULE_REF
        and report.get("input_digest") == _digest(inputs)
        and report.get("arguments_digest") == _digest(checked_arguments)
    )


def _blocked(validation: object, reason_code: str) -> dict[str, object]:
    return {
        "business_status": "BLOCKED",
        "reason_code": reason_code,
        "result_file": None,
        "summary": None,
        "validation": validation,
    }


def calculate(arguments: Mapping[str, object], context: object) -> Mapping[str, object]:
    validation = arguments.get("validation")
    if not _receipt_matches(arguments):
        return _blocked(validation, "VALIDATION_RECEIPT_MISMATCH")
    values, issue = _read_values(arguments, context)
    if issue is not None or values is None:
        return _blocked(validation, issue[1] if issue is not None else "CSV_INPUT_INVALID")
    profile = arguments["profile"]
    assert isinstance(profile, Mapping)
    digits = profile.get("round_digits")
    if not isinstance(digits, int) or isinstance(digits, bool) or not 0 <= digits <= 12:
        return _blocked(validation, "CSV_PROFILE_INVALID")
    summary = {
        "count": len(values),
        "minimum": round(min(values), digits),
        "maximum": round(max(values), digits),
        "mean": round(fmean(values), digits),
    }
    workspace = _workspace(context)
    action_id = getattr(context, "action_id", None)
    if not isinstance(action_id, str) or not action_id:
        raise ValueError("CSV_ACTION_ID_REQUIRED")
    logical_id = "reports/csv-statistics.json"
    output = workspace.action_output_path(action_id, logical_id)
    output.write_text(_canonical(summary) + "\n", encoding="utf-8")
    reference = workspace.publish_output(logical_id, action_id=action_id)
    return {
        "business_status": "APPROVED",
        "reason_code": None,
        "result_file": reference.to_mapping(),
        "summary": summary,
        "validation": validation,
    }


def blocked(arguments: Mapping[str, object]) -> Mapping[str, object]:
    validation = arguments.get("validation")
    reason = "VALIDATION_FAILED"
    if isinstance(validation, Mapping):
        issues = validation.get("issues")
        if isinstance(issues, list) and issues and isinstance(issues[0], Mapping):
            candidate = issues[0].get("code")
            if isinstance(candidate, str):
                reason = candidate
    return _blocked(validation, reason)


def _register(executor: object, reference: object, handler: object, tool_id: str) -> None:
    if (
        getattr(reference, "tool_id", None) != tool_id
        or getattr(reference, "version", None) != TOOL_DESCRIPTORS[tool_id]
    ):
        raise ValueError("host ToolReference does not match this capability")
    register = getattr(executor, "register", None)
    if not callable(register):
        raise TypeError("executor does not support trusted tool registration")
    register(reference, handler)


def register_validate(executor: object, reference: object) -> None:
    _register(executor, reference, validate, "csv-statistics.validate")


def register_calculate(executor: object, reference: object) -> None:
    _register(executor, reference, calculate, "csv-statistics.calculate")


def register_blocked(executor: object, reference: object) -> None:
    _register(executor, reference, blocked, "csv-statistics.blocked")


__all__ = [
    "TOOL_DESCRIPTORS",
    "blocked",
    "calculate",
    "register_blocked",
    "register_calculate",
    "register_validate",
    "validate",
]
