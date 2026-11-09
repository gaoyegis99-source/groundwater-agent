#!/usr/bin/env python3
"""Compare normalized Windows/Linux scientific regression JSON documents."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def close(actual: float, expected: float, rtol: float, atol: float) -> bool:
    return math.isclose(actual, expected, rel_tol=rtol, abs_tol=atol)


def compare_series(label: str, actual: list, expected: list, rtol: float, atol: float) -> list[str]:
    failures: list[str] = []
    if len(actual) != len(expected):
        return [f"{label}: length {len(actual)} != {len(expected)}"]
    for index, (left, right) in enumerate(zip(actual, expected, strict=True)):
        if left is None or right is None:
            if left != right:
                failures.append(f"{label}[{index}]: valid-value mask mismatch")
        elif not close(float(left), float(right), rtol, atol):
            failures.append(f"{label}[{index}]: {left} != {right}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("expected", type=Path)
    parser.add_argument("actual", type=Path)
    arguments = parser.parse_args()
    expected = json.loads(arguments.expected.read_text(encoding="utf-8"))
    actual = json.loads(arguments.actual.read_text(encoding="utf-8"))
    failures: list[str] = []
    for key in ("structure", "timeAxis", "converged"):
        if actual.get(key) != expected.get(key):
            failures.append(f"{key}: mismatch")
    for key in ("heads", "concentrations"):
        failures += compare_series(key, actual.get(key, []), expected.get(key, []), 1e-5, 1e-7)
    for key in ("waterBalancePercent", "massBalancePercent"):
        if not close(float(actual[key]), float(expected[key]), 0.0, 0.01):
            failures.append(f"{key}: differs by more than 0.01 percentage point")
    failures += compare_series(
        "pestParameters",
        actual.get("pestParameters", []),
        expected.get("pestParameters", []),
        1e-3,
        0.0,
    )
    if not close(
        float(actual.get("pestObjective", 0.0)),
        float(expected.get("pestObjective", 0.0)),
        1e-3,
        0.0,
    ):
        failures.append("pestObjective: outside rtol=1e-3")
    report = {"passed": not failures, "failures": failures}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
