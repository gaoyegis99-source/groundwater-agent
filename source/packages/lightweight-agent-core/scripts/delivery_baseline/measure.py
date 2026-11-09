"""Build a wheel and record a reproducible clean-environment delivery baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
PACKAGE_ROOT = HERE.parents[1]
PROBE = HERE / "probe.py"
sys.path.insert(0, str(PACKAGE_ROOT / "scripts"))
from source_inputs import copy_inputs, input_manifest  # noqa: E402


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _tree_digest(root: Path) -> str:
    """Identify exactly the copied source baseline used for the wheel."""
    return str(input_manifest(root)["sha256"])


def _baseline_id(components: dict[str, str]) -> str:
    payload = json.dumps(
        components, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _git_revision(root: Path) -> str | None:
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    value = completed.stdout.strip()
    return value if completed.returncode == 0 and value else None


def _run(command: list[str], *, cwd: Path, timeout: float = 180) -> str:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            + completed.stderr[-4000:]
        )
    return completed.stdout


def _summary(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    p95_index = min(len(ordered) - 1, max(0, int(len(ordered) * 0.95)))
    return {
        "p50": round(statistics.median(ordered), 3),
        "p95": round(ordered[p95_index], 3),
    }


def _probe(python: Path, mode: str) -> dict[str, Any]:
    return json.loads(_run([str(python), str(PROBE), "--mode", mode], cwd=HERE))


def measure(
    output: Path, *, samples: int, archive: Path | None = None
) -> dict[str, Any]:
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("delivery baseline requires uv")
    with tempfile.TemporaryDirectory(prefix="v6-delivery-baseline-") as raw:
        root = Path(raw)
        source = root / "source"
        copy_inputs(PACKAGE_ROOT, source)
        source_digest = _tree_digest(source)
        lock = source / "uv.lock"
        lock_summary = {
            "path": "uv.lock",
            "present": lock.is_file(),
            "sha256": _sha256(lock) if lock.is_file() else None,
            "bytes": lock.stat().st_size if lock.is_file() else None,
        }
        if lock.is_file():
            _run([uv, "lock", "--check"], cwd=source)
        dist = root / "dist"
        dist.mkdir()
        _run([uv, "build", "--wheel", "--out-dir", str(dist)], cwd=source)
        wheel = next(dist.glob("platform_lightweight_agent_core-*.whl"))
        environment = root / "venv"
        _run([uv, "venv", "--python", sys.executable, str(environment)], cwd=root)
        python = environment / "bin" / "python"
        cli = environment / "bin" / "platform-agent-workflow"
        _run(
            [uv, "pip", "install", "--python", str(python), f"{wheel}[workflow]"],
            cwd=root,
        )
        inventory_program = (
            "import json,pathlib,sysconfig; root=pathlib.Path(sysconfig.get_paths()"
            "['purelib']); print(json.dumps({'bytes':sum(p.stat().st_size for p in "
            "root.rglob('*') if p.is_file())}))"
        )
        install_size = json.loads(
            _run([str(python), "-c", inventory_program], cwd=root)
        )["bytes"]
        workflow = root / "workflow.json"
        workflow.write_text(
            json.dumps(
                {
                    "schema_version": "v6-light-workflow-v1",
                    "workflow_id": "cli-baseline",
                    "version": "1",
                    "steps": [
                        {
                            "id": "review",
                            "kind": "approval",
                            "config": {"prompt": "continue?"},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        cli_samples: list[float] = []
        for _index in range(samples):
            started = time.perf_counter()
            _run([str(cli), "check", str(workflow)], cwd=root)
            cli_samples.append((time.perf_counter() - started) * 1000)
        imports = [_probe(python, "import") for _index in range(samples)]
        executions = [_probe(python, "execution") for _index in range(samples)]
        queries = [_probe(python, "query") for _index in range(samples)]
        recoveries = [_probe(python, "recovery") for _index in range(samples)]
        cleanups = [_probe(python, "cleanup") for _index in range(samples)]
        archived = None
        if archive is not None:
            archive.mkdir(parents=True, exist_ok=False)
            shutil.copy2(wheel, archive / wheel.name)
            shutil.copy2(lock, archive / "uv.lock")
            (archive / "source-inputs.json").write_text(
                json.dumps(input_manifest(source), indent=2) + "\n", encoding="utf-8"
            )
            goldens = json.loads(
                _run(
                    [
                        str(python),
                        str(HERE / "capture_goldens.py"),
                        "--output",
                        str(archive / "goldens"),
                    ],
                    cwd=root,
                )
            )
            components = {
                "source_sha256": source_digest,
                "wheel_sha256": _sha256(wheel),
                "lock_sha256": _sha256(lock),
                "goldens_sha256": _sha256(archive / "goldens/goldens.json"),
            }
            archived = {
                "schema_version": "v6-compatibility-baseline-v1",
                "directory": str(archive),
                "baseline_id": _baseline_id(components),
                "wheel_sha256": _sha256(wheel),
                "source_sha256": source_digest,
                "identity_components": components,
                "producer": {
                    "package_version": goldens["producer_version"],
                    "wheel_name": wheel.name,
                    **components,
                },
                "goldens": goldens,
            }
            (archive / "provenance.json").write_text(
                json.dumps(archived, indent=2) + "\n", encoding="utf-8"
            )
            compatibility_output = archive / "compatibility-verification.json"
            compatibility_matrix = archive / "compatibility-matrix.json"
            _run(
                [
                    str(python),
                    str(HERE / "verify_compatibility.py"),
                    "--baseline",
                    str(archive),
                    "--output",
                    str(compatibility_output),
                    "--matrix-output",
                    str(compatibility_matrix),
                    "--consumer-source-sha256",
                    source_digest,
                    "--consumer-wheel",
                    str(wheel),
                ],
                cwd=root,
            )
            compatibility = json.loads(
                compatibility_output.read_text(encoding="utf-8")
            )
            if compatibility.get("result") != "BASELINE_ESTABLISHED":
                raise RuntimeError("current wheel did not establish its baseline")
            archived["compatibility_verification"] = {
                "path": compatibility_output.name,
                "result": compatibility["result"],
                "matrix": compatibility_matrix.name,
            }
            (archive / "provenance.json").write_text(
                json.dumps(archived, indent=2) + "\n", encoding="utf-8"
            )
        report = {
            "schema_version": "v6-delivery-baseline-v2",
            "source_baseline": {
                "source_tree_digest": source_digest,
                "source_revision": _git_revision(PACKAGE_ROOT),
                "copy_mode": "fresh_explicit_source_copy",
                "development_path_imports_allowed": False,
            },
            "wheel": {"name": wheel.name, "sha256": _sha256(wheel)},
            "archived_artifacts": archived,
            "lock": lock_summary,
            "measurement": {
                "samples": samples,
                "python": platform.python_version(),
                "python_executable": str(python),
                "platform": platform.platform(),
                "machine": platform.machine(),
                "installation": {"extra": "workflow", "isolated_venv": True},
            },
            "acceptance": {
                "clean_wheel_install": True,
                "cli_contract_check": True,
                "execution": all(item["accepted"] is True for item in executions),
                "run_query": all(item["accepted"] is True for item in queries),
                "execution_recovery": all(
                    item["accepted"] is True for item in recoveries
                ),
                "resource_cleanup": all(item["accepted"] is True for item in cleanups),
                "skipped": [],
                "unassessed": [
                    "real_model_evaluation_requires_explicit_opt_in",
                    "browser_playwright_trace_requires_explicit_browser_gate",
                ],
            },
            "install_size_bytes": install_size,
            "cold_import_ms": _summary([item["import_ms"] for item in imports]),
            "cli_check_ms": _summary(cli_samples),
            "execution_ms": _summary([item["execution_ms"] for item in executions]),
            "execution_rss_bytes": {
                "p50": int(statistics.median(item["rss_bytes"] for item in executions)),
                "max": max(item["rss_bytes"] for item in executions),
            },
            "run_query_ms": _summary([item["query_ms"] for item in queries]),
            "run_query_rss_bytes": {
                "p50": int(statistics.median(item["rss_bytes"] for item in queries)),
                "max": max(item["rss_bytes"] for item in queries),
            },
            "idle_rss_bytes": {
                "p50": int(statistics.median(item["rss_bytes"] for item in imports)),
                "max": max(item["rss_bytes"] for item in imports),
            },
            "recovery_ms": _summary([item["recovery_ms"] for item in recoveries]),
            "recovery_rss_bytes": {
                "p50": int(statistics.median(item["rss_bytes"] for item in recoveries)),
                "max": max(item["rss_bytes"] for item in recoveries),
            },
            "resource_cleanup_ms": _summary([item["cleanup_ms"] for item in cleanups]),
            "resource_cleanup_rss_bytes": {
                "p50": int(statistics.median(item["rss_bytes"] for item in cleanups)),
                "max": max(item["rss_bytes"] for item in cleanups),
            },
            "thresholds": None,
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, output)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--archive-artifacts", type=Path)
    args = parser.parse_args()
    if args.samples < 1:
        raise SystemExit("--samples must be positive")
    report = measure(args.output, samples=args.samples, archive=args.archive_artifacts)
    print(json.dumps(report, indent=2, sort_keys=True))
    acceptance = report["acceptance"]
    required = (
        "clean_wheel_install",
        "cli_contract_check",
        "execution",
        "run_query",
        "execution_recovery",
        "resource_cleanup",
    )
    return (
        0
        if all(acceptance.get(name) is True for name in required)
        and acceptance.get("skipped") == []
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
