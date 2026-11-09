"""Run and finalize the frozen 24-run structured-reply quality gate.

Run this once against an isolated deployment configured for MiniMax and once
against an isolated deployment configured for DeepSeek. The run phase records
only reviewable presentation text and source labels. The finalize phase binds
two independent human reviews to those exact runs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import httpx

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.lib.e2e_http import public_api_headers
from scripts.evaluation.evaluate_agent import request
from scripts.evaluation.reply_quality import definitions, identity, reports, runner
from scripts.lib.assembly_paths import compiled_bundle


def _write_report(path: Path, report: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode()
    path.write_bytes(encoded)
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _require_external_output(parser: argparse.ArgumentParser, path: Path) -> None:
    if path.resolve().is_relative_to(definitions.ROOT):
        parser.error("report output must be outside the repository")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--base-url")
    run_parser.add_argument("--knowledge-url")
    run_parser.add_argument(
        "--fail-fast",
        action="store_true",
        help=(
            "Stop after the first failed run. The report remains failed and "
            "unexecuted runs remain unassessed; 24/24 is still required."
        ),
    )
    run_parser.add_argument(
        "--kernel-config",
        type=Path,
        default=None,
    )
    run_parser.add_argument("--output", type=Path, required=True)
    finalize_parser = subparsers.add_parser("finalize")
    finalize_parser.add_argument("--input", type=Path, required=True)
    finalize_parser.add_argument("--reviews", type=Path, required=True)
    finalize_parser.add_argument("--output", type=Path, required=True)
    blind_parser = subparsers.add_parser("blind")
    blind_parser.add_argument("--input", type=Path, required=True)
    blind_parser.add_argument("--output", type=Path, required=True)
    combine_parser = subparsers.add_parser("combine")
    combine_parser.add_argument(
        "--inputs", type=Path, nargs=2, required=True,
    )
    combine_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "run" and args.kernel_config is None:
        args.kernel_config = compiled_bundle(definitions.ROOT) / "kernel-config.json"
    _require_external_output(parser, args.output)
    if args.command == "combine":
        input_reports = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in args.inputs
        ]
        combined = reports.combine_reports(input_reports)
        combined["input_reports"] = [
            {
                "provider": report["configuration"]["provider"],
                "checksum": definitions._sha256(path.read_bytes()),
            }
            for path, report in zip(args.inputs, input_reports, strict=True)
        ]
        checksum = _write_report(args.output, combined)
        print(json.dumps({
            "status": combined["status"], "report_checksum": checksum,
        }))
        return
    if args.command == "blind":
        report = json.loads(args.input.read_text(encoding="utf-8"))
        bundle = reports.blind_review_bundle(report)
        checksum = _write_report(args.output, bundle)
        print(json.dumps({
            "status": "READY_FOR_BLIND_REVIEW",
            "response_count": len(bundle["responses"]),
            "bundle_checksum": checksum,
        }))
        return
    if args.command == "finalize":
        report = json.loads(args.input.read_text(encoding="utf-8"))
        reviews = json.loads(args.reviews.read_text(encoding="utf-8"))
        final = reports.finalize_report(report, reviews)
        checksum = _write_report(args.output, final)
        print(json.dumps({"status": final["status"], "report_checksum": checksum}))
        raise SystemExit(0 if final["status"] == "PASS" else 1)
    cases = identity.load_cases()
    release_identity = identity.load_release_identity(args.kernel_config)
    frozen_data_checksum = identity.data_fixture_checksum()
    problem = identity.configuration_problem()
    if problem is not None or not args.base_url or not args.knowledge_url:
        reason, fields = problem or (
            "MISSING_ISOLATED_DEPLOYMENT_CONFIGURATION",
            [name for name, value in {
                "base_url": args.base_url,
                "knowledge_url": args.knowledge_url,
            }.items() if not value],
        )
        report = {
            "schemaVersion": "reply-quality-evaluation-report.v2",
            "status": "BLOCKED[real-reply-quality]",
            "reason": reason,
            "fields": fields,
            "fixture_checksum": identity.fixture_checksum(),
            "data_fixture_checksum": frozen_data_checksum,
            "release_identity": release_identity,
            "planned_run_count": len(cases) * definitions.REPETITIONS,
        }
        checksum = _write_report(args.output, report)
        print(json.dumps({"status": report["status"], "report_checksum": checksum}))
        raise SystemExit(2)
    records = []
    with (
        httpx.Client(
            base_url=args.base_url,
            timeout=600,
            trust_env=False,
            headers=public_api_headers(),
        ) as client,
        httpx.Client(
            base_url=args.knowledge_url,
            timeout=120,
            trust_env=False,
            headers=public_api_headers(),
        ) as knowledge,
    ):
        identity.verify_capabilities(
            request(client, "GET", "/api/v12/capabilities"),
            release_identity,
        )
        stopped = False
        for case in cases:
            for iteration in range(1, definitions.REPETITIONS + 1):
                record = runner.run_record(
                    client, knowledge, case, iteration, release_identity,
                )
                records.append(record)
                print(json.dumps({
                    "case_id": record["case_id"],
                    "iteration": iteration,
                    "status": record["status"],
                    "failure": record.get("failure"),
                }, ensure_ascii=False), flush=True)
                if args.fail_fast and record["status"] != "PASS":
                    stopped = True
                    break
            if stopped:
                break
    report = reports.summarize(
        records, cases=cases, release_identity=release_identity,
    )
    checksum = _write_report(args.output, report)
    print(json.dumps({
        "status": report["status"],
        "pass_count": report["pass_count"],
        "run_count": report["run_count"],
        "report_checksum": checksum,
    }))
    raise SystemExit(0 if report["status"] == "AWAITING_HUMAN_REVIEW" else 1)


if __name__ == "__main__":
    main()
