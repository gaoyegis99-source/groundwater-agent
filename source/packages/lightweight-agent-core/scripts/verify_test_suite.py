"""A fail-closed evidence recorder around pytest, not a second test runner."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path
from typing import Any

import pytest


class SuiteEvidence:
    def __init__(self, root: Path, required_suites: list[str]) -> None:
        self.root = root.resolve()
        self.required_suites = required_suites
        self.collected: list[str] = []
        self.phases: dict[str, dict[str, str]] = {}
        self.collection_errors: list[str] = []

    def nodeid(self, value: str) -> str:
        # pytest's rootdir may be the containing monorepo. Normalize only its
        # path prefix, never a test's identity or parametrization.
        filename, *parts = value.split("::")
        marker = "tests/"
        if marker in filename:
            filename = marker + filename.rsplit(marker, 1)[1]
        return "::".join([filename, *parts])

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self.collected = [self.nodeid(item.nodeid) for item in session.items]

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        if report.failed or report.skipped:
            self.collection_errors.append(f"{report.nodeid}: {report.outcome}")

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        outcome = "xfail" if hasattr(report, "wasxfail") else report.outcome
        self.phases.setdefault(self.nodeid(report.nodeid), {})[report.when] = outcome

    def result(self, exit_code: int) -> dict[str, Any]:
        missing = [
            suite
            for suite in self.required_suites
            if not any(
                node == suite or node.startswith(suite + "::")
                for node in self.collected
            )
        ]
        incomplete = [
            node
            for node in self.collected
            if self.phases.get(node)
            != {"setup": "passed", "call": "passed", "teardown": "passed"}
        ]
        passed = bool(self.collected) and not (
            exit_code or missing or incomplete or self.collection_errors
        )
        return {
            "schema_version": "v6-pytest-suite-evidence-v1",
            "result": "PASS" if passed else "FAIL",
            "pytest_exit_code": exit_code,
            "required_suites": self.required_suites,
            "missing_suites": missing,
            "collection_errors": self.collection_errors,
            "collected": self.collected,
            "incomplete_tests": incomplete,
            "phases": self.phases,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("selectors", nargs="*", default=["tests"])
    args = parser.parse_args(argv)
    root = args.root.resolve()
    profile = json.loads(
        (args.profile or root / "verification-profile.json").read_text(encoding="utf-8")
    )
    if profile.get("schema_version") != "v6-developer-verification-profile-v1":
        parser.error("unsupported verification profile")
    recorder = SuiteEvidence(root, profile["required_suites"])
    missing = []
    for distribution, expected in profile["required_distributions"].items():
        try:
            actual = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            actual = None
        if actual != expected:
            missing.append(
                {"distribution": distribution, "expected": expected, "actual": actual}
            )
    exit_code = (
        4
        if missing
        else int(
            pytest.main(
                ["-q", "-rs", "-p", "no:cacheprovider", *args.selectors],
                plugins=[recorder],
            )
        )
    )
    report = recorder.result(exit_code)
    report["dependency_errors"] = missing
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
