"""Compile defect-discovery evidence without converting gaps into passes."""

from __future__ import annotations

import argparse
import hashlib
import json
from itertools import product
import platform
from pathlib import Path
import subprocess
import sys
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
REPOSITORY_ROOT = PACKAGE_ROOT.parents[1]
CASE_IDS = tuple(
    [f"B{number:02d}" for number in range(1, 5)]
    + [f"W{number:02d}" for number in range(1, 6)]
    + [f"R{number:02d}" for number in range(1, 9)]
    + [f"A{number:02d}" for number in range(1, 5)]
    + [f"D{number:02d}" for number in range(1, 7)]
    + [f"M{number:02d}" for number in range(1, 5)]
    + [f"X{number:02d}" for number in range(1, 5)]
    + [f"P{number:02d}" for number in range(1, 7)]
    + [f"U{number:02d}" for number in range(1, 5)]
    + [f"S{number:02d}" for number in range(1, 4)]
    + [f"O{number:02d}" for number in range(1, 5)]
)
ALLOWED_RESULTS = {"PASS", "FAIL", "ERROR", "NOT_RUN", "NOT_ASSESSED"}
RESULT_PRECEDENCE = {
    "NOT_RUN": 0,
    "PASS": 1,
    "NOT_ASSESSED": 2,
    "FAIL": 3,
    "ERROR": 4,
}


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _git(*arguments: str) -> str | None:
    completed = subprocess.run(
        ["git", "-C", str(REPOSITORY_ROOT), *arguments],
        text=True,
        capture_output=True,
        check=False,
    )
    value = completed.stdout.strip()
    return value or None


def _evidence_entries(report: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for item in report.get("results", []):
        if not isinstance(item, dict) or "case" not in item:
            continue
        cases = str(item["case"]).split("/")
        for case_id in cases:
            copied = dict(item)
            copied["case"] = case_id
            entries.append(copied)
            # Partial matrix cases carry useful, independently assessed tests.
            # Their PASS proves only this selector, never an external scenario.
            for test in item.get("tests", []):
                entries.append(
                    {
                        "case": case_id,
                        "variant": "pytest:" + test["selector"],
                        "contract_result": test["contract_result"],
                    }
                )
    cases = report.get("cases", {})
    if isinstance(cases, dict):
        for case_id, items in cases.items():
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict):
                    copied = dict(item)
                    copied["case"] = case_id
                    entries.append(copied)
    return entries


def _case_result(entries: list[dict[str, Any]]) -> str:
    results = [str(item.get("contract_result", "ERROR")) for item in entries]
    unknown = sorted(set(results) - ALLOWED_RESULTS)
    if unknown:
        raise ValueError(f"unsupported evidence result(s): {unknown}")
    return max(results, key=RESULT_PRECEDENCE.__getitem__)


def _assessments(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict) or parsed.get("schema_version") != (
        "v6-defect-discovery-assessments-v2"
    ):
        raise ValueError("assessment manifest has an unsupported schema")
    cases = parsed.get("cases")
    if not isinstance(cases, dict) or set(cases) != set(CASE_IDS):
        actual = set(cases) if isinstance(cases, dict) else set()
        missing = sorted(set(CASE_IDS) - actual)
        extra = sorted(actual - set(CASE_IDS))
        raise ValueError(
            f"assessment manifest must cover every case; missing={missing}, extra={extra}"
        )
    normalized: dict[str, dict[str, Any]] = {}
    for case_id, value in cases.items():
        if not isinstance(value, dict):
            raise ValueError(f"assessment for {case_id} must be an object")
        disposition = value.get("disposition")
        if disposition not in {"complete", "not_assessed"}:
            raise ValueError(f"assessment for {case_id} has invalid disposition")
        required = value.get("required_evidence")
        if not isinstance(required, list) or not required:
            raise ValueError(
                f"assessment for {case_id} requires executable evidence facets"
            )
        for facet in required:
            if not isinstance(facet, dict) or not isinstance(facet.get("schema"), str):
                raise ValueError(f"invalid evidence facet for {case_id}")
            if not isinstance(facet.get("match"), dict) or not facet["match"]:
                raise ValueError(
                    f"evidence facet for {case_id} requires exact dimensions"
                )
            for values in facet.get("dimensions", {}).values():
                if (
                    not isinstance(values, list)
                    or not values
                    or len({json.dumps(v) for v in values}) != len(values)
                ):
                    raise ValueError(f"invalid evidence dimensions for {case_id}")
        if disposition == "not_assessed" and (
            not isinstance(value.get("reason"), str)
            or not value["reason"]
            or not isinstance(value.get("required_environment"), list)
            or not value["required_environment"]
        ):
            raise ValueError(
                f"not-assessed case {case_id} requires reason and required_environment"
            )
        normalized[case_id] = dict(value)
    source = {
        "path": str(path),
        "sha256": _sha256(path),
        "schema_version": parsed["schema_version"],
    }
    return normalized, source


def _final_case_result(
    raw_results: list[str],
    assessment: dict[str, Any],
    missing: list[dict[str, Any]],
) -> tuple[str, str | None]:
    unknown = sorted(set(raw_results) - ALLOWED_RESULTS)
    if unknown:
        raise ValueError(f"unsupported evidence result(s): {unknown}")
    if "ERROR" in raw_results:
        return "ERROR", "final evidence contains an infrastructure error"
    if "FAIL" in raw_results:
        return "FAIL", None
    if assessment["disposition"] == "not_assessed":
        return "NOT_ASSESSED", str(assessment["reason"])
    if missing:
        return "NOT_RUN", "required executable evidence is missing or incomplete"
    if "PASS" not in raw_results:
        return "NOT_RUN", "no passing executable evidence"
    return "PASS", None


def missing_evidence(
    entries: list[dict[str, Any]], required: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """All declared dimensions must pass; duplicates never fill another cell."""
    missing = []
    for facet in required:
        dimensions = facet.get("dimensions", {})
        for values in product(*dimensions.values()):
            expected = {**facet["match"], **dict(zip(dimensions, values, strict=True))}
            matches = [
                entry
                for entry in entries
                if entry["evidence_schema"] == facet["schema"]
                and all(entry.get(key) == value for key, value in expected.items())
            ]
            if not matches or any(
                item["contract_result"] != "PASS" for item in matches
            ):
                missing.append({"schema": facet["schema"], "match": expected})
    return missing


def compile_report(
    evidence_paths: list[Path],
    assessments_path: Path,
) -> dict[str, Any]:
    assessments, assessment_source = _assessments(assessments_path)
    coverage: dict[str, dict[str, Any]] = {
        case_id: {
            "case": case_id,
            "contract_result": "NOT_RUN",
            "variants": [],
            "evidence": [],
            "assessment": assessments[case_id],
            "_raw_results": [],
            "_entries": [],
        }
        for case_id in CASE_IDS
    }
    sources: list[dict[str, Any]] = []
    source_errors: list[dict[str, Any]] = []
    for path in evidence_paths:
        report = json.loads(path.read_text(encoding="utf-8"))
        source = {
            "path": str(path),
            "sha256": _sha256(path),
            "schema_version": report.get("schema_version"),
            "reported_result": report.get("result"),
        }
        sources.append(source)
        entries = _evidence_entries(report)
        reported = report.get("result")
        if reported == "ERROR" or (
            reported == "FAIL"
            and not any(
                item.get("contract_result") in {"FAIL", "ERROR"} for item in entries
            )
        ):
            source_errors.append(source)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in entries:
            item["evidence_schema"] = report.get("schema_version")
            case_id = str(item["case"])
            if case_id not in coverage:
                raise ValueError(f"unknown case id in {path}: {case_id}")
            grouped.setdefault(case_id, []).append(item)
        for case_id, items in grouped.items():
            current = coverage[case_id]
            current["_entries"].extend(items)
            current["variants"].extend(
                {
                    key: item[key]
                    for key in (
                        "variant",
                        "repetition",
                        "window",
                        "contract_result",
                        "classification",
                        "reason",
                        "evidence_schema",
                    )
                    if key in item
                }
                for item in items
            )
            current["evidence"].append(source["sha256"])
            _case_result(items)  # validate result vocabulary before aggregation
            current["_raw_results"].extend(item["contract_result"] for item in items)

    counts = {result: 0 for result in ALLOWED_RESULTS}
    for item in coverage.values():
        raw_results = item.pop("_raw_results")
        missing = missing_evidence(
            item.pop("_entries"), item["assessment"]["required_evidence"]
        )
        item["missing_evidence"] = missing
        result, reason = _final_case_result(raw_results, item["assessment"], missing)
        item["contract_result"] = result
        item["raw_evidence_results"] = sorted(
            set(raw_results), key=RESULT_PRECEDENCE.__getitem__
        )
        if reason is not None:
            item["reason"] = reason
        counts[item["contract_result"]] += 1

    def live_status(schema: str) -> str:
        results = [
            source["reported_result"]
            for source in sources
            if source["schema_version"] == schema
        ]
        if not results:
            return "NOT_ASSESSED"
        return max(
            (value if value in ALLOWED_RESULTS else "ERROR" for value in results),
            key=RESULT_PRECEDENCE.__getitem__,
        )

    return {
        "schema_version": "v6-defect-discovery-coverage-report-v2",
        "result": "ERROR"
        if source_errors
        else "PASS"
        if counts["PASS"] == len(CASE_IDS)
        else "FAIL",
        "source_errors": source_errors,
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "repository_head": _git("rev-parse", "HEAD"),
            "worktree_dirty": bool(_git("status", "--porcelain")),
        },
        "scope": {
            "case_count": len(CASE_IDS),
            "allowed_results": sorted(ALLOWED_RESULTS),
            "real_model_requests": live_status("v6-real-provider-evidence-v1"),
            "real_container_execution": live_status("v6-real-container-evidence-v1"),
            "remote_service_calls": "NOT_ASSESSED",
        },
        "summary": counts,
        "coverage": [coverage[case_id] for case_id in CASE_IDS],
        "evidence_sources": sources,
        "assessment_source": assessment_source,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, action="append", default=[])
    parser.add_argument("--assessments", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = compile_report(args.evidence, args.assessments)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    # Compiling a report is not itself an acceptance pass.
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
