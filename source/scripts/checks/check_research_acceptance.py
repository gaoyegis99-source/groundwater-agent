#!/usr/bin/env python3
"""Validate the three independent V12 research acceptance dimensions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


STATUSES = {"PASS", "FAIL", "NOT_ASSESSED"}


def evaluate(policy: dict, assessment: dict, level: str) -> dict:
    if policy.get("schemaVersion") != "v12-research-acceptance-policy.v1":
        raise ValueError("RESEARCH_POLICY_UNSUPPORTED")
    if assessment.get("schemaVersion") != "v12-research-acceptance.v1":
        raise ValueError("RESEARCH_ASSESSMENT_UNSUPPORTED")
    dimensions = policy.get("dimensions")
    results: dict[str, str] = {}
    for dimension, gates in dimensions.items():
        value = assessment.get(dimension)
        if not isinstance(value, dict) or set(value) != set(gates):
            raise ValueError(f"RESEARCH_GATE_MISMATCH:{dimension}")
        statuses = [value[gate].get("status") for gate in gates]
        if any(status not in STATUSES for status in statuses):
            raise ValueError(f"RESEARCH_STATUS_INVALID:{dimension}")
        results[dimension] = (
            "FAIL" if "FAIL" in statuses else
            "NOT_ASSESSED" if "NOT_ASSESSED" in statuses else "PASS"
        )
    required = policy["archivePolicy"].get(level)
    if required is None:
        raise ValueError("RESEARCH_ARCHIVE_LEVEL_INVALID")
    archive_status = "PASS" if all(results[item] == "PASS" for item in required) else "FAIL"
    return {"level": level, "archiveStatus": archive_status, **results}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--assessment", type=Path, required=True)
    parser.add_argument("--level", choices=("experiment", "benchmark"), required=True)
    args = parser.parse_args()
    try:
        result = evaluate(
            json.loads(args.policy.read_text()), json.loads(args.assessment.read_text()), args.level
        )
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(json.dumps({"status": "FAILED", "code": str(error)}))
        return 3
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["archiveStatus"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
