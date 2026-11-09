"""Exercise CSV edge cases through the shipped capability wheel."""

from __future__ import annotations

import argparse
import importlib
import json
import math
from pathlib import Path
import sys
import tempfile
from typing import Any

from platform_lightweight_agent import CancellationToken, FileWorkspace, ToolContext


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
WHEEL = next(
    (PACKAGE_ROOT / "examples/domain-packs/csv-statistics/artifacts").glob("*.whl")
)


def _load_capability() -> Any:
    sys.path.insert(0, str(WHEEL))
    try:
        module = importlib.import_module("csv_statistics_capability")
    finally:
        sys.path.remove(str(WHEEL))
    return module


def _run_case(
    capability: Any,
    root: Path,
    *,
    name: str,
    content: bytes,
    column: str = "level",
    max_rows: int = 1_000,
    max_input_bytes: int | None = None,
    expected_status: str,
    expected_reason: str | None,
    expected_summary: dict[str, object] | None = None,
) -> dict[str, object]:
    workspace = FileWorkspace.under(root / name / "workspace")
    source = root / name / "source.csv"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(content)
    reference = workspace.stage_input(source, f"inputs/{name}.csv")
    arguments = {
        "file": reference.to_mapping(),
        "column": column,
        "profile": {
            "max_input_bytes": (
                reference.size_bytes
                if max_input_bytes is None
                else max_input_bytes
            ),
            "max_rows": max_rows,
            "round_digits": 3,
        },
    }
    context = ToolContext(
        f"csv-edge-{name}", CancellationToken(), workspace
    )
    report = capability.validate(arguments, context)
    if report["status"] == "PASS":
        result = capability.calculate(
            {**arguments, "validation": report}, context
        )
        reason = result["reason_code"]
        summary = result["summary"]
        result_file = result["result_file"]
        published_ok = result_file is not None and workspace.resolve_published(
            result_file
        ).is_file()
    else:
        result = capability.blocked({"validation": report})
        reason = result["reason_code"]
        summary = result["summary"]
        published_ok = result["result_file"] is None
    requirements = {
        "expected_business_status": result["business_status"] == expected_status,
        "expected_reason": reason == expected_reason,
        "expected_summary": expected_summary is None or summary == expected_summary,
        "result_publication_consistent": published_ok,
        "validation_is_rule_bound": report["rule_ref"]
        == "csv-statistics.input-v1",
    }
    return {
        "case": "D05",
        "variant": name,
        "contract_result": "PASS" if all(requirements.values()) else "FAIL",
        "requirements": requirements,
        "input_size_bytes": reference.size_bytes,
        "validation": report,
        "business_status": result["business_status"],
        "reason_code": reason,
        "summary": summary,
    }


def collect() -> dict[str, object]:
    capability = _load_capability()
    with tempfile.TemporaryDirectory(prefix="v6-csv-edges-") as raw:
        root = Path(raw)
        valid = b"level\n1\n2\n3\n"
        independent = {
            "count": 3,
            "minimum": 1.0,
            "maximum": 3.0,
            "mean": round(math.fsum((1.0, 2.0, 3.0)) / 3, 3),
        }
        cases = [
            _run_case(
                capability,
                root,
                name="valid_reference",
                content=valid,
                expected_status="APPROVED",
                expected_reason=None,
                expected_summary=independent,
            ),
            _run_case(
                capability,
                root,
                name="empty_file",
                content=b"",
                expected_status="BLOCKED",
                expected_reason="CSV_COLUMN_MISSING",
            ),
            _run_case(
                capability,
                root,
                name="header_without_rows",
                content=b"level\n",
                expected_status="BLOCKED",
                expected_reason="CSV_NO_DATA",
            ),
            _run_case(
                capability,
                root,
                name="utf8_bom",
                content="\ufefflevel\n1\n".encode(),
                expected_status="BLOCKED",
                expected_reason="CSV_COLUMN_MISSING",
            ),
            _run_case(
                capability,
                root,
                name="duplicate_column",
                content=b"level,level\n1,2\n",
                expected_status="BLOCKED",
                expected_reason="CSV_COLUMN_MISSING",
            ),
            _run_case(
                capability,
                root,
                name="missing_column",
                content=b"other\n1\n",
                expected_status="BLOCKED",
                expected_reason="CSV_COLUMN_MISSING",
            ),
            _run_case(
                capability,
                root,
                name="invalid_utf8",
                content=b"level\n\xff\n",
                expected_status="BLOCKED",
                expected_reason="CSV_PARSE_ERROR",
            ),
            _run_case(
                capability,
                root,
                name="malformed_csv",
                content=b'level\n"unterminated\n',
                expected_status="BLOCKED",
                expected_reason="CSV_PARSE_ERROR",
            ),
            _run_case(
                capability,
                root,
                name="not_a_number",
                content=b"level\nnot-a-number\n",
                expected_status="BLOCKED",
                expected_reason="CSV_VALUE_NOT_NUMBER",
            ),
            _run_case(
                capability,
                root,
                name="nan",
                content=b"level\nNaN\n",
                expected_status="BLOCKED",
                expected_reason="CSV_VALUE_NOT_NUMBER",
            ),
            _run_case(
                capability,
                root,
                name="infinity",
                content=b"level\nInfinity\n",
                expected_status="BLOCKED",
                expected_reason="CSV_VALUE_NOT_NUMBER",
            ),
            _run_case(
                capability,
                root,
                name="large_finite",
                content=b"level\n1e308\n-1e308\n",
                expected_status="APPROVED",
                expected_reason=None,
                expected_summary={
                    "count": 2,
                    "minimum": -1e308,
                    "maximum": 1e308,
                    "mean": 0.0,
                },
            ),
            _run_case(
                capability,
                root,
                name="row_limit_equal",
                content=b"level\n1\n2\n",
                max_rows=2,
                expected_status="APPROVED",
                expected_reason=None,
            ),
            _run_case(
                capability,
                root,
                name="row_limit_plus_one",
                content=b"level\n1\n2\n3\n",
                max_rows=2,
                expected_status="BLOCKED",
                expected_reason="CSV_ROW_LIMIT",
            ),
            _run_case(
                capability,
                root,
                name="size_limit_equal",
                content=valid,
                max_input_bytes=len(valid),
                expected_status="APPROVED",
                expected_reason=None,
            ),
            _run_case(
                capability,
                root,
                name="size_limit_plus_one",
                content=valid,
                max_input_bytes=len(valid) - 1,
                expected_status="BLOCKED",
                expected_reason="CSV_FILE_TOO_LARGE",
            ),
        ]
    return {
        "schema_version": "v6-csv-wheel-edge-evidence-v1",
        "result": (
            "FAIL"
            if any(item["contract_result"] == "FAIL" for item in cases)
            else "PASS"
        ),
        "wheel": {
            "path": str(WHEEL),
            "module_file": str(capability.__file__),
        },
        "results": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = collect()
        exit_code = 0
    except Exception as error:  # noqa: BLE001 - retain unexpected wheel behavior
        report = {
            "schema_version": "v6-csv-wheel-edge-evidence-v1",
            "result": "ERROR",
            "results": [
                {
                    "case": "D05",
                    "variant": "wheel_edge_matrix",
                    "contract_result": "ERROR",
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            ],
        }
        exit_code = 1
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
