"""Run the V6 developer delivery gate and emit inspectable JSON evidence.

This is intentionally a thin orchestration script over the repository's real
tools: uv resolves/builds, pytest exercises the public product surface, and
the delivery baseline performs the clean wheel installation.  It
does not create a second workflow runtime or invent a test framework.

    uv run --group development python scripts/verify_developer_delivery.py

Live model evaluation remains opt-in and is deliberately not triggered here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from source_inputs import copy_inputs, input_manifest  # noqa: E402


def _tree_digest(root: Path) -> str:
    return str(input_manifest(root)["sha256"])


def _git(root: Path, *args: str) -> str | None:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    value = completed.stdout.strip()
    return value if completed.returncode == 0 and value else None


def _run(
    name: str,
    command: list[str],
    *,
    cwd: Path = ROOT,
    pythonpath: str | None = None,
    extra_environment: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run a check with an explicit import boundary.

    Source-tree tests deliberately use the package's ``src`` directory.  The
    clean-wheel probe, by contrast, must not inherit it: that is what proves
    the installed wheel is self-contained rather than accidentally importing
    the developer checkout.
    """
    environment = {
        **os.environ,
        "PYTHONPATH": pythonpath or "",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_ADDOPTS": "",
        "OPENAI_AGENTS_DISABLE_TRACING": "1",
    }
    if extra_environment:
        environment.update(extra_environment)
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=900,
    )
    return {
        "name": name,
        "command": command,
        "result": "passed" if completed.returncode == 0 else "failed",
        "exit_code": completed.returncode,
        "stdout_tail": completed.stdout[-2_000:],
        "stderr_tail": completed.stderr[-2_000:],
    }


def _csv_capability_identity(root: Path) -> dict[str, Any] | None:
    pack = root / "examples/domain-packs/csv-statistics"
    artifacts = pack / "artifacts"
    wheels = sorted(artifacts.glob("*.whl"))
    sidecars = sorted(artifacts.glob("tool-manifest-*.json"))
    if len(wheels) != 1 or len(sidecars) != 3:
        return None
    wheel = wheels[0]
    wheel_digest = "sha256:" + hashlib.sha256(wheel.read_bytes()).hexdigest()
    try:
        deliveries = json.loads(
            (pack / "release-deliveries.json").read_text(encoding="utf-8")
        )
        declared = {
            item["tool"]["package_digest"] for item in deliveries.values()
        }
        sidecar_digests = {
            json.loads(path.read_text(encoding="utf-8"))["package_digest"]
            for path in sidecars
        }
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return None
    if declared != {wheel_digest} or sidecar_digests != {wheel_digest}:
        return None
    return {
        "wheel": wheel.relative_to(root).as_posix(),
        "wheel_sha256": wheel_digest,
        "sidecars": [path.relative_to(root).as_posix() for path in sidecars],
    }


def _prepare_csv_capability(uv: str) -> list[dict[str, Any]]:
    """Build and freeze the ignored example wheel before source capture."""
    pack = ROOT / "examples/domain-packs/csv-statistics"
    with tempfile.TemporaryDirectory(prefix="v6-csv-capability-") as raw:
        output = Path(raw)
        build = _run(
            "csv_capability_build",
            [
                uv,
                "build",
                "--wheel",
                "--out-dir",
                str(output),
                str(pack / "capability"),
            ],
            extra_environment={"SOURCE_DATE_EPOCH": "0"},
        )
        checks = [build]
        wheels = sorted(output.glob("*.whl"))
        if build["result"] != "passed" or len(wheels) != 1:
            build["result"] = "failed"
            return checks
        checks.append(
            _run(
                "csv_capability_freeze",
                [
                    sys.executable,
                    str(pack / "generate_release.py"),
                    "--wheel",
                    str(wheels[0]),
                ],
                pythonpath=str(ROOT / "src"),
            )
        )
    identity = _csv_capability_identity(ROOT)
    checks.append(
        {
            "name": "csv_capability_release_identity",
            "result": "passed" if identity is not None else "failed",
            "identity": identity,
        }
    )
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
        / "groundwater-v6/verification/developer-acceptance-report.json",
    )
    parser.add_argument("--skip-development-tests", action="store_true")
    parser.add_argument("--skip-clean-wheel", action="store_true")
    parser.add_argument("--baseline-samples", type=int, default=1)
    parser.add_argument("--archive-artifacts", type=Path)
    args = parser.parse_args(argv)
    if args.baseline_samples < 1:
        raise SystemExit("--baseline-samples must be positive")
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("V6 developer delivery verification requires uv")

    preparation_checks = _prepare_csv_capability(uv)
    args.output = args.output.resolve()
    lock = ROOT / "uv.lock"
    worktree_status = _git(ROOT, "status", "--porcelain") or ""
    report: dict[str, Any] = {
        "schema_version": "v6-developer-delivery-report-v2",
        "source": {
            "package_root": str(ROOT),
            "tree_sha256": _tree_digest(ROOT),
            "git_revision": _git(ROOT, "rev-parse", "HEAD"),
            "git_dirty": bool(worktree_status),
            "git_status_sha256": "sha256:"
            + hashlib.sha256(worktree_status.encode("utf-8")).hexdigest(),
            "git_changed_path_count": len(worktree_status.splitlines()),
        },
        "environment": {
            "python": platform.python_version(),
            "python_executable": sys.executable,
            "platform": platform.platform(),
            "installation_combo": "captured source checks + clean wheel [workflow]",
        },
        "lock": {
            "path": str(lock),
            "present": lock.is_file(),
            "sha256": (
                "sha256:" + hashlib.sha256(lock.read_bytes()).hexdigest()
                if lock.is_file()
                else None
            ),
        },
        "checks": preparation_checks,
        "skipped": [],
        "unassessed": [
            "real_model_evaluation_requires_platform-agent-workflow evaluate --allow-model-requests",
            "browser_playwright_trace_requires_an_explicit_browser_environment",
        ],
    }
    checks: list[dict[str, Any]] = report["checks"]
    with tempfile.TemporaryDirectory(prefix="v6-verified-inputs-") as raw:
        captured = Path(raw) / "source"
        inputs = copy_inputs(ROOT, captured)
        report["source"]["inputs"] = inputs
        report["source"]["tree_sha256"] = inputs["sha256"]
        profile = json.loads(
            (captured / "verification-profile.json").read_text(encoding="utf-8")
        )
        prefix = [
            uv,
            "run",
            "--isolated",
            "--locked",
            "--group",
            profile["dependency_group"],
        ]
        checks.append(_run("uv_lock", [uv, "lock", "--check"], cwd=captured))
        checks.append(
            _run(
                "ruff",
                [
                    *prefix,
                    "ruff",
                    "check",
                    "src",
                    "tests",
                    "scripts",
                    "examples/domain-packs/csv-statistics/generate_release.py",
                ],
                cwd=captured,
            )
        )
        checks.append(
            _run(
                "architecture_imports",
                [*prefix, "lint-imports", "--no-logo"],
                cwd=captured,
            )
        )
        if args.skip_development_tests:
            report["skipped"].append("development_pytest")
        else:
            evidence = Path(raw) / "pytest-evidence.json"
            checks.append(
                _run(
                    "development_pytest",
                    [
                        *prefix,
                        "--all-extras",
                        "python",
                        "scripts/verify_test_suite.py",
                        "--output",
                        str(evidence),
                    ],
                    cwd=captured,
                    pythonpath=str(captured / "src"),
                )
            )
            if evidence.is_file():
                report["pytest_evidence"] = json.loads(
                    evidence.read_text(encoding="utf-8")
                )
            if report.get("pytest_evidence", {}).get("result") != "PASS":
                checks[-1]["result"] = "failed"
        if args.skip_clean_wheel:
            report["skipped"].append("clean_wheel_delivery")
        else:
            baseline = Path(raw) / "delivery-baseline.json"
            archive_args = (
                ["--archive-artifacts", str(args.archive_artifacts.resolve())]
                if args.archive_artifacts
                else []
            )
            checks.append(
                _run(
                    "clean_wheel_execution_and_recovery",
                    [
                        sys.executable,
                        str(captured / "scripts/delivery_baseline/measure.py"),
                        "--samples",
                        str(args.baseline_samples),
                        "--output",
                        str(baseline),
                        *archive_args,
                    ],
                    cwd=captured,
                )
            )
            if baseline.is_file():
                report["clean_wheel_baseline"] = json.loads(
                    baseline.read_text(encoding="utf-8")
                )
            if not clean_wheel_passed(
                report.get("clean_wheel_baseline", {}), inputs["sha256"]
            ):
                checks[-1]["result"] = "failed"
        unchanged = input_manifest(captured) == inputs
        checks.append(
            {
                "name": "verified_inputs_unchanged",
                "result": "passed" if unchanged else "failed",
            }
        )
    report["result"] = delivery_result(checks, report["skipped"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "result": report["result"],
                "report": str(args.output),
                "checks": [
                    {"name": item["name"], "result": item["result"]} for item in checks
                ],
            },
            indent=2,
        )
    )
    return 0 if report["result"] == "passed" else 1


def delivery_result(checks: list[dict[str, Any]], skipped: list[str]) -> str:
    if not checks or any(item["result"] != "passed" for item in checks):
        return "failed"
    return "incomplete" if skipped else "passed"


def clean_wheel_passed(report: dict[str, Any], source_digest: str) -> bool:
    required = (
        "clean_wheel_install",
        "cli_contract_check",
        "execution",
        "run_query",
        "execution_recovery",
        "resource_cleanup",
    )
    acceptance = report.get("acceptance", {})
    return (
        report.get("schema_version") == "v6-delivery-baseline-v2"
        and report.get("source_baseline", {}).get("source_tree_digest") == source_digest
        and isinstance(acceptance, dict)
        and all(acceptance.get(name) is True for name in required)
        and acceptance.get("skipped") == []
    )


if __name__ == "__main__":
    raise SystemExit(main())
