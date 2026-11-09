"""Build and exercise the dependency-free wheel in an offline environment."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile


PACKAGE_ROOT = Path(__file__).resolve().parents[3]


EMBEDDED_PROGRAM = r"""
import json
import pathlib
import sys
import threading

import platform_lightweight_agent as v6

tool = v6.ToolReference("offline.echo", "1", "sha256:" + "2" * 64)
executor = v6.LocalToolExecutor()
calls = []
executor.register(tool, lambda arguments: calls.append(dict(arguments)) or {"value": 7})
engine = v6.WorkflowEngine(v6.InMemoryRunJournal(), executor, {})
spec = v6.workflow(
    "offline-base",
    "1",
    [v6.tool_step("effect", tool, {}, approval_required=True)],
    schema_version="v6-light-workflow-v1",
)
waiting = engine.run(spec, run_id="offline-base")
complete = engine.resume(waiting.run_id, approved=True)
optional = [
    name for name in ("anyio", "jsonschema", "pydantic", "agents", "dbos", "sqlalchemy")
    if name in sys.modules
]
print(json.dumps({
    "status_before": waiting.status,
    "status_after": complete.status,
    "calls": calls,
    "import_path": str(pathlib.Path(v6.__file__).resolve()),
    "optional_modules_loaded": optional,
    "thread_count": threading.active_count(),
}))
"""


def _run(
    command: list[str], *, cwd: Path, environment: dict[str, str], timeout: int = 180
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def collect() -> dict[str, object]:
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required for the installation probe")
    environment = {
        name: value
        for name, value in os.environ.items()
        if "API_KEY" not in name and name not in {"PYTHONPATH", "PYTHONHOME"}
    }
    environment.update(
        {
            "UV_OFFLINE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "OPENAI_AGENTS_DISABLE_TRACING": "1",
        }
    )
    with tempfile.TemporaryDirectory(prefix="v6-base-wheel-") as raw:
        root = Path(raw)
        source = root / "source"
        shutil.copytree(
            PACKAGE_ROOT,
            source,
            ignore=shutil.ignore_patterns(
                ".venv",
                ".hypothesis",
                ".import_linter_cache",
                "__pycache__",
                "*.egg-info",
                "build",
                "dist",
            ),
        )
        dist = root / "dist"
        built = _run(
            [uv, "build", "--wheel", "--out-dir", str(dist)],
            cwd=source,
            environment=environment,
        )
        if built.returncode != 0:
            raise RuntimeError(f"offline wheel build failed: {built.stderr[-3000:]}")
        wheel = next(dist.glob("platform_lightweight_agent_core-*.whl"))
        venv = root / "venv"
        created = _run(
            [uv, "venv", "--python", sys.executable, str(venv)],
            cwd=root,
            environment=environment,
        )
        if created.returncode != 0:
            raise RuntimeError(f"offline venv creation failed: {created.stderr[-3000:]}")
        python = venv / "bin" / "python"
        installed = _run(
            [
                uv,
                "pip",
                "install",
                "--offline",
                "--no-deps",
                "--python",
                str(python),
                str(wheel),
            ],
            cwd=root,
            environment=environment,
        )
        if installed.returncode != 0:
            raise RuntimeError(f"offline wheel install failed: {installed.stderr[-3000:]}")
        hidden = root / "source-hidden"
        source.rename(hidden)
        arbitrary = root / "arbitrary" / "nested"
        arbitrary.mkdir(parents=True)
        embedded = _run(
            [str(python), "-I", "-c", EMBEDDED_PROGRAM],
            cwd=arbitrary,
            environment=environment,
        )
        if embedded.returncode != 0:
            raise RuntimeError(f"embedded base run failed: {embedded.stderr[-3000:]}")
        embedded_result = json.loads(embedded.stdout)
        workflow_path = arbitrary / "workflow.json"
        workflow_path.write_text(
            json.dumps(
                {
                    "schema_version": "v6-light-workflow-v1",
                    "workflow_id": "offline-cli",
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
        cli = _run(
            [str(venv / "bin" / "platform-agent-workflow"), "check", str(workflow_path)],
            cwd=arbitrary,
            environment=environment,
        )
        with zipfile.ZipFile(wheel) as archive:
            metadata_name = next(
                name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
            )
            metadata = archive.read(metadata_name).decode("utf-8")
        unconditional_requirements = [
            line
            for line in metadata.splitlines()
            if line.startswith("Requires-Dist:") and "extra ==" not in line
        ]
        requirements = {
            "wheel_builds_offline": built.returncode == 0,
            "base_installs_without_dependencies": installed.returncode == 0
            and not unconditional_requirements,
            "approval_and_tool_complete": embedded_result["status_before"]
            == "WAITING_APPROVAL"
            and embedded_result["status_after"] == "SUCCEEDED"
            and embedded_result["calls"] == [{}],
            "optional_frameworks_not_loaded": not embedded_result[
                "optional_modules_loaded"
            ],
            "import_comes_from_installed_environment": str(venv)
            in embedded_result["import_path"]
            and str(hidden) not in embedded_result["import_path"],
            "cli_works_from_arbitrary_directory": cli.returncode == 0,
            "no_background_thread_started": embedded_result["thread_count"] == 1,
        }
        evidence = {
            "wheel": {
                "name": wheel.name,
                "sha256": _sha(wheel),
                "size_bytes": wheel.stat().st_size,
                "unconditional_requirements": unconditional_requirements,
            },
            "embedded": embedded_result,
            "cli": {
                "returncode": cli.returncode,
                "stdout": cli.stdout[-2_000:],
                "stderr": cli.stderr[-2_000:],
            },
            "network_mode": "uv offline; runtime program uses no network adapter",
            "source_copy_hidden_before_runtime": True,
        }
    return {
        "schema_version": "v6-base-wheel-offline-evidence-v1",
        "result": "PASS" if all(requirements.values()) else "FAIL",
        "results": [
            {
                "case": "B01",
                "variant": "base_wheel_offline_v1_approval",
                "contract_result": (
                    "PASS" if all(requirements.values()) else "FAIL"
                ),
                "requirements": requirements,
                **evidence,
            }
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = collect()
        exit_code = 0
    except Exception as error:  # noqa: BLE001 - preserve infrastructure failure
        report = {
            "schema_version": "v6-base-wheel-offline-evidence-v1",
            "result": "ERROR",
            "results": [
                {
                    "case": "B01",
                    "variant": "base_wheel_offline_v1_approval",
                    "contract_result": "ERROR",
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            ],
        }
        exit_code = 1
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
