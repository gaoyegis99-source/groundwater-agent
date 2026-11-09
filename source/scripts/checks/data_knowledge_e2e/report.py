"""Machine-readable report and pytest evidence summaries."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

def _write_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _pytest_junit_summary(path: Path) -> dict[str, int]:
    root = ET.parse(path).getroot()
    if root.tag == "testsuite":
        suites = [root]
    elif root.tag == "testsuites":
        suites = [item for item in root if item.tag == "testsuite"]
    else:
        raise ValueError("PYTEST_JUNIT_ROOT_INVALID")
    if not suites:
        raise ValueError("PYTEST_JUNIT_EMPTY")
    counts = {
        name: sum(int(suite.attrib.get(name, "0")) for suite in suites)
        for name in ("tests", "failures", "errors", "skipped")
    }
    if counts["tests"] <= 0:
        raise ValueError("PYTEST_JUNIT_EMPTY")
    if counts["failures"] + counts["errors"] + counts["skipped"] > counts["tests"]:
        raise ValueError("PYTEST_JUNIT_COUNTS_INVALID")
    counts["passed"] = (
        counts["tests"] - counts["failures"] - counts["errors"] - counts["skipped"]
    )
    return counts


def _pytest_report(path: Path, return_code: int) -> dict[str, object]:
    counts = _pytest_junit_summary(path)
    return {
        "status": "PASS"
        if return_code == 0
        and counts["passed"] == counts["tests"]
        and counts["skipped"] == 0
        else "FAIL",
        **counts,
        "returnCode": return_code,
        "junitReport": path.name,
        "junitSha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _playwright_passed(path: Path) -> int:
    report = json.loads(path.read_text(encoding="utf-8"))
    stats = report["stats"]
    if (
        report.get("errors")
        or stats["expected"] <= 0
        or any(stats[name] for name in ("unexpected", "flaky", "skipped"))
    ):
        raise RuntimeError(f"PLAYWRIGHT_TESTS_INCOMPLETE:{path}")
    return int(stats["expected"])
