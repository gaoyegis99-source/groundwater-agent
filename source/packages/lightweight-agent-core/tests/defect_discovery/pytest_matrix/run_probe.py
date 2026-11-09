"""Run mapped offline contracts through pytest and emit neutral evidence.

The mapping distinguishes a passing sub-scope from complete assessment.  A
partial mapping stays NOT_ASSESSED even when every selected test passes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

import pytest


PACKAGE_ROOT = Path(__file__).resolve().parents[3]


class Recorder:
    def __init__(self) -> None:
        self.reports: dict[str, list[dict[str, object]]] = {}

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        self.reports.setdefault(report.nodeid, []).append(
            {
                "phase": report.when,
                "outcome": report.outcome,
                "longrepr": (
                    str(report.longrepr)[-4_000:]
                    if report.failed or report.skipped
                    else None
                ),
            }
        )


def _selector_reports(
    selector: str, reports: dict[str, list[dict[str, object]]]
) -> dict[str, list[dict[str, object]]]:
    def selected(nodeid: str) -> bool:
        # The repository-level pytest root prefixes package nodeids with
        # ``packages/lightweight-agent-core/`` even when this runner is
        # launched from the package directory.
        path = nodeid.split("::", 1)[0]
        if selector.endswith(".py"):
            return path == selector or path.endswith("/" + selector)
        return (
            nodeid == selector
            or nodeid.startswith(selector + "[")
            or nodeid.endswith("/" + selector)
            or ("/" + selector + "[") in nodeid
        )

    return {
        nodeid: phases
        for nodeid, phases in reports.items()
        if selected(nodeid)
    }


def _test_result(reports: dict[str, list[dict[str, object]]]) -> str:
    if not reports:
        return "ERROR"
    phases = [phase for values in reports.values() for phase in values]
    if any(
        phase["outcome"] == "failed" and phase["phase"] != "call"
        for phase in phases
    ):
        return "ERROR"
    if any(
        phase["outcome"] == "failed" and phase["phase"] == "call"
        for phase in phases
    ):
        return "FAIL"
    if any(phase["outcome"] == "skipped" for phase in phases):
        return "NOT_ASSESSED"
    if not any(
        phase["phase"] == "call" and phase["outcome"] == "passed"
        for phase in phases
    ):
        return "ERROR"
    return "PASS"


def run(mapping_path: Path) -> tuple[dict[str, Any], int]:
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    cases = mapping.get("cases")
    if not isinstance(cases, list):
        raise ValueError("mapping cases must be an array")
    selectors = sorted(
        {
            selector
            for case in cases
            for selector in case.get("selectors", [])
        }
    )
    recorder = Recorder()
    exit_code = 0
    if selectors:
        exit_code = int(
            pytest.main(
                ["-q", "-rs", "-p", "no:cacheprovider", *selectors],
                plugins=[recorder],
            )
        )

    results: list[dict[str, Any]] = []
    for case in cases:
        case_selectors = case.get("selectors", [])
        tests: list[dict[str, Any]] = []
        observed: list[str] = []
        for selector in case_selectors:
            selected = _selector_reports(selector, recorder.reports)
            result = _test_result(selected)
            observed.append(result)
            tests.append(
                {
                    "selector": selector,
                    "contract_result": result,
                    "nodeids": selected,
                }
            )
        if "ERROR" in observed:
            contract_result = "ERROR"
        elif "FAIL" in observed:
            contract_result = "FAIL"
        elif "NOT_ASSESSED" in observed:
            contract_result = "NOT_ASSESSED"
        elif case.get("complete", False) and observed:
            contract_result = "PASS"
        else:
            contract_result = "NOT_ASSESSED"
        results.append(
            {
                "case": case["case"],
                "variant": "offline_pytest_contracts",
                "contract_result": contract_result,
                "classification": (
                    None
                    if case.get("complete", False)
                    else "COVERAGE_GAP"
                ),
                "assessed_scope": case.get("assessed_scope"),
                "missing_scope": case.get("missing_scope", []),
                "tests": tests,
            }
        )
    infrastructure_error = exit_code not in {0, 1}
    report = {
        "schema_version": "v6-pytest-contract-matrix-v1",
        "result": (
            "ERROR"
            if infrastructure_error
            or any(item["contract_result"] == "ERROR" for item in results)
            else "FAIL"
            if any(item["contract_result"] == "FAIL" for item in results)
            else "NOT_ASSESSED"
            if any(item["contract_result"] == "NOT_ASSESSED" for item in results)
            else "PASS"
        ),
        "pytest_exit_code": exit_code,
        "python": sys.version,
        "mapping": str(mapping_path),
        "results": results,
    }
    evidence_error = any(
        item["contract_result"] == "ERROR" for item in results
    )
    return report, 1 if infrastructure_error or evidence_error else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mapping",
        type=Path,
        default=Path(__file__).with_name("matrix.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report, exit_code = run(args.mapping)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
