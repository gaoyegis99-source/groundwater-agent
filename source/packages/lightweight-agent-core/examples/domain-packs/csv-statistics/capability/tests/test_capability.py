from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from csv_statistics_capability import blocked, calculate, validate


@dataclass
class Reference:
    logical_id: str
    sha256: str
    size_bytes: int

    def to_mapping(self) -> dict[str, object]:
        return {
            "logical_id": self.logical_id,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root

    def resolve_input(self, reference):  # type: ignore[no-untyped-def]
        return self.root / reference["logical_id"]

    def action_output_path(self, action_id, logical_id):  # type: ignore[no-untyped-def]
        path = self.root / "staging" / action_id / logical_id
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def publish_output(self, logical_id, *, action_id):  # type: ignore[no-untyped-def]
        path = self.action_output_path(action_id, logical_id)
        return Reference(
            logical_id,
            "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_size,
        )


@dataclass
class Context:
    files: Workspace
    action_id: str = "csv-action"


def test_validate_and_calculate_publish_a_result(tmp_path: Path) -> None:
    source = tmp_path / "input.csv"
    source.write_text("level\n1\n2\n3\n", encoding="utf-8")
    file_reference = Reference(
        "input.csv",
        "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest(),
        source.stat().st_size,
    ).to_mapping()
    arguments = {
        "file": file_reference,
        "column": "level",
        "profile": {
            "max_input_bytes": 1048576,
            "max_rows": 1000,
            "round_digits": 3,
        },
    }
    context = Context(Workspace(tmp_path))
    report = validate(arguments, context)
    result = calculate({**arguments, "validation": report}, context)

    assert report["status"] == "PASS"
    assert result["business_status"] == "APPROVED"
    assert result["summary"] == {
        "count": 3,
        "minimum": 1.0,
        "maximum": 3.0,
        "mean": 2.0,
    }
    assert result["result_file"]["logical_id"] == "reports/csv-statistics.json"


def test_invalid_numeric_value_produces_a_rule_bound_blocked_result(
    tmp_path: Path,
) -> None:
    source = tmp_path / "invalid.csv"
    source.write_text("level\n1\nnot-a-number\n", encoding="utf-8")
    arguments = {
        "file": Reference(
            "invalid.csv",
            "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest(),
            source.stat().st_size,
        ).to_mapping(),
        "column": "level",
        "profile": {
            "max_input_bytes": 1_048_576,
            "max_rows": 1_000,
            "round_digits": 3,
        },
    }
    report = validate(arguments, Context(Workspace(tmp_path)))
    result = blocked({"validation": report})

    assert report["status"] == "FAIL"
    assert report["rule_ref"] == "csv-statistics.input-v1"
    assert result["business_status"] == "BLOCKED"
    assert result["reason_code"] == "CSV_VALUE_NOT_NUMBER"
