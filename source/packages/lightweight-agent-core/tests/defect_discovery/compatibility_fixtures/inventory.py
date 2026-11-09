"""Inventory genuine historical V6 artifacts without synthesizing compatibility."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
REPOSITORY_ROOT = PACKAGE_ROOT.parents[1]


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(REPOSITORY_ROOT), *args],
        text=True,
        capture_output=True,
        check=False,
    )


def run() -> dict[str, object]:
    history = _git(
        "log",
        "--all",
        "--format=%H%x09%aI%x09%s",
        "--",
        "packages/lightweight-agent-core",
    )
    tracked = _git(
        "ls-tree",
        "-r",
        "HEAD",
        "--",
        "packages/lightweight-agent-core",
    )
    candidates: list[dict[str, object]] = []
    # The compatibility claim belongs to this package.  Unrelated repository
    # manifests (for example v11 operator catalog entries) are not historical
    # V6 artifacts and would only make the provenance inventory ambiguous.
    for path in sorted(PACKAGE_ROOT.rglob("*")):
        if not path.is_file() or not (
            path.suffix in {".whl", ".sqlite", ".v6bundle"}
            or path.name
            in {
                "domain-lock.json",
                "manifest.json",
                "portability.json",
            }
        ):
            continue
        relative = path.relative_to(REPOSITORY_ROOT).as_posix()
        candidates.append(
            {
                "path": relative,
                "sha256": _sha(path),
                "size_bytes": path.stat().st_size,
                "historical_provenance": None,
                "classification": "current_generated_artifact",
            }
        )
    has_history = bool(history.stdout.strip())
    has_tracked_package = bool(tracked.stdout.strip())
    axes = [
        "python_package_and_driver_version",
        "domain_lock_and_context_policy",
        "execution_manifest",
        "sqlite_storage",
        "bundle_and_local_recovery",
    ]
    results = [
        {
            "case": "P06",
            "variant": axis,
            "contract_result": "NOT_ASSESSED",
            "classification": "CONTRACT_GAP",
            "reason": (
                "No genuine prior-version artifact with immutable provenance is "
                "available in repository history; current files and synthetic "
                "field mutations cannot prove historical compatibility."
            ),
        }
        for axis in axes
    ]
    return {
        "schema_version": "v6-historical-artifact-inventory-v1",
        "result": "NOT_ASSESSED",
        "repository_head": (_git("rev-parse", "HEAD").stdout.strip() or None),
        "package_has_git_history": has_history,
        "package_tracked_at_head": has_tracked_package,
        "git_history_stdout": history.stdout,
        "candidates": candidates,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run()
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
