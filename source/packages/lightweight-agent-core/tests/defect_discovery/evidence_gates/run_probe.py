"""Audit whether the developer gate itself can silently omit evidence."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
ARCHITECTURE = (
    PACKAGE_ROOT.parents[1] / "docs" / "architecture" / "v6-lightweight-agent-kernel.md"
)


def _run(command: list[str], *, cwd: Path, timeout: int = 300) -> dict[str, Any]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in ("TOKEN", "SECRET", "PASSWORD", "API_KEY"))
    }
    environment.update(
        {
            "PYTHONPATH": "src",
            "PYTHONDONTWRITEBYTECODE": "1",
            "OPENAI_AGENTS_DISABLE_TRACING": "1",
        }
    )
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return {
        "command": command,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _tree_digest(root: Path) -> str:
    source = root / "scripts" / "verify_developer_delivery.py"
    spec = importlib.util.spec_from_file_location("delivery_verifier", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._tree_digest(root)  # type: ignore[no-any-return,attr-defined]


def _copy_source(destination: Path) -> None:
    shutil.copytree(
        PACKAGE_ROOT,
        destination,
        ignore=shutil.ignore_patterns(
            ".venv",
            ".hypothesis",
            ".import_linter_cache",
            "__pycache__",
            "build",
            "dist",
            "*.egg-info",
        ),
    )


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def run(evidence_root: Path) -> dict[str, object]:
    uv = shutil.which("uv")
    if uv is None:
        return {
            "schema_version": "v6-evidence-gate-audit-v1",
            "result": "NOT_ASSESSED",
            "reason": "uv is unavailable",
        }
    source_copy = evidence_root / "source-copy"
    _copy_source(source_copy)
    profile = evidence_root / "invariant-profile.json"
    profile.write_text(json.dumps({
        "schema_version": "v6-developer-verification-profile-v1",
        "required_distributions": {"pytest": "8.3.4", "hypothesis": "6.170.9"},
        "required_suites": ["tests/test_state_invariants.py"],
    }), encoding="utf-8")

    omitted = _run(
        [
            uv,
            "run",
            "--isolated",
            "--locked",
            "--all-extras",
            "--group",
            "development",
            "python",
            "scripts/verify_test_suite.py",
            "--profile", str(profile),
            "--output", str(evidence_root / "omitted.json"),
            "tests/test_state_invariants.py",
        ],
        cwd=source_copy,
        timeout=600,
    )
    explicit = _run(
        [
            uv,
            "run",
            "--isolated",
            "--locked",
            "--all-extras",
            "--group",
            "verification",
            "python",
            "scripts/verify_test_suite.py",
            "--profile", str(profile),
            "--output", str(evidence_root / "explicit.json"),
            "tests/test_state_invariants.py",
        ],
        cwd=source_copy,
    )
    before = _tree_digest(source_copy)
    import_linter = _run(
        [
            uv,
            "run",
            "--isolated",
            "--frozen",
            "--group",
            "architecture",
            "lint-imports",
            "--no-logo",
        ],
        cwd=source_copy,
    )
    after = _tree_digest(source_copy)
    cache_files = {
        path.relative_to(source_copy).as_posix(): _sha(path)
        for path in sorted((source_copy / ".import_linter_cache").rglob("*"))
        if path.is_file()
    }
    architecture = ARCHITECTURE.read_text(encoding="utf-8")
    omissions = {
        "missing_dependency_rejected": omitted["exit_code"] != 0
        and json.loads((evidence_root / "omitted.json").read_text())["result"] == "FAIL",
        "explicit_invariant_control_passes": (
            explicit["exit_code"] == 0
            and json.loads((evidence_root / "explicit.json").read_text())["result"] == "PASS"
        ),
        "import_linter_passes": import_linter["exit_code"] == 0,
        "import_cache_changes_tree_digest": before != after,
        "stale_csv_placeholder_statement": (
            "placeholder capability identities, not production executable tools"
            in architecture
        ),
    }
    results = [
        {
            "case": "O04",
            "variant": "delivery_gate_omits_state_invariants",
            "contract_result": (
                "PASS"
                if omissions["missing_dependency_rejected"]
                and omissions["explicit_invariant_control_passes"]
                else "FAIL"
            ),
            "omitted_gate": {
                "exit_code": omitted["exit_code"],
                "stdout_tail": str(omitted["stdout"])[-4000:],
                "stderr_tail": str(omitted["stderr"])[-2000:],
            },
            "explicit_control": {
                "exit_code": explicit["exit_code"],
                "stdout_tail": str(explicit["stdout"])[-2000:],
            },
        },
        {
            "case": "O04",
            "variant": "import_linter_cache_pollutes_source_fingerprint",
            "contract_result": (
                "PASS"
                if omissions["import_linter_passes"]
                and not omissions["import_cache_changes_tree_digest"]
                else "FAIL"
            ),
            "tree_digest_before": before,
            "tree_digest_after": after,
            "cache_files": cache_files,
            "import_linter_exit_code": import_linter["exit_code"],
        },
        {
            "case": "O04",
            "variant": "csv_documentation_contradicts_shipped_wheel",
            "contract_result": (
                "FAIL" if omissions["stale_csv_placeholder_statement"] else "PASS"
            ),
            "architecture_path": str(ARCHITECTURE),
            "capability_wheel": str(
                next((PACKAGE_ROOT / "examples/domain-packs/csv-statistics/artifacts").glob("*.whl"))
            ),
        },
    ]
    return {
        "schema_version": "v6-evidence-gate-audit-v1",
        "result": (
            "ERROR"
            if any(item["contract_result"] == "ERROR" for item in results)
            else "FAIL"
            if any(item["contract_result"] == "FAIL" for item in results)
            else "PASS"
        ),
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "source_copy": str(source_copy),
        },
        "observations": omissions,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.evidence_root.mkdir(parents=True, exist_ok=True)
    report = run(args.evidence_root)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
