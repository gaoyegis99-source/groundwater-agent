"""CLI parsing for the Kernel sandbox pressure report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.maintenance.project_environment import validation_state_root

from .environment import ROOT
from .report import build_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=validation_state_root(ROOT)
        / "current/kernel-sandbox-pressure-report.json",
    )
    parser.add_argument("--seed", type=int, default=20261018)
    args = parser.parse_args()
    report = build_report(root=ROOT, seed=args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "output": str(args.output),
                "eventCount": report["probeSummary"].get("eventCount"),
                "failureCount": report["probeSummary"]["failureCount"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "PASS" else 1
