"""Run the installed-client handoff gate while the isolated Compose stack lives."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import httpx

from scripts.lib.e2e_http import TOKEN_ENVIRONMENT, public_api_headers
from scripts.maintenance.dependency_materializer import root as dependency_material_root
from scripts.maintenance.project_environment import lifecycle
from scripts.operations.test_full_project import (
    _offline_wheel_build_command,
    _prepare_wheel_build_input,
)

from .environment import ROOT, _run


SPECS = ("v12-interaction-isolated.spec.ts", "v12-interaction-recovery.spec.ts")
EXPECTED_TESTS = 7


def _install_clients(directory: Path) -> tuple[Path, dict[str, str]]:
    wheels = directory / "wheels"
    wheels.mkdir()
    for package in ("protocol-contracts", "groundwater-api-client"):
        source = _prepare_wheel_build_input(ROOT / "packages" / package, directory / package)
        _run(_offline_wheel_build_command(source, wheels))
    wheel_files = sorted(wheels.glob("*.whl"))
    if len(wheel_files) != 2:
        raise RuntimeError("INTERACTION_WHEELS_INCOMPLETE")
    venv = directory / "client-venv"
    python = venv / "bin/python"
    common = dependency_material_root(ROOT, lifecycle()) / "python/common"
    _run(["uv", "venv", "--offline", "--no-python-downloads", "--python", sys.executable, str(venv)])
    _run([
        "uv", "pip", "install", "--offline", "--no-index", "--find-links", str(common),
        "--python", str(python), *map(str, wheel_files),
    ])
    _run(["uv", "pip", "check", "--python", str(python)])
    _run([str(python), "-I", "-c",
          "import importlib.util as u; import groundwater_api_client; "
          "assert u.find_spec('platform_kernel') is None"])
    return venv / "bin", {
        wheel.name: hashlib.sha256(wheel.read_bytes()).hexdigest() for wheel in wheel_files
    }


def run_interaction_gate(
    base_url: str, report_root: Path, credentials: Path, command: list[str],
    compose_project: str, project_id: str,
) -> None:
    from webui_seed import seed_browser_data

    report = (report_root / compose_project).resolve()
    report.mkdir(parents=True)
    print(f"Interaction evidence: {report}", flush=True)
    seed_browser_data(base_url)
    with httpx.Client(
        base_url=base_url, headers=public_api_headers(), trust_env=False, timeout=15,
    ) as client:
        rows = client.get(
            "/api/v12/data/datasets", params={"query": "六模型观测表", "limit": 20},
        ).raise_for_status().json()["items"]
    (dataset,) = [row for row in rows if row["title"] == "六模型观测表"]
    programs, wheel_hashes = _install_clients(credentials.parent)
    worker = _run(
        [*command, "ps", "-q", "executor-native-worker"], capture_output=True,
    ).stdout.strip()
    if not worker or "\n" in worker:
        raise RuntimeError("INTERACTION_NATIVE_WORKER_NOT_UNIQUE")
    data_worker = _run(
        [*command, "ps", "-q", "data-worker"], capture_output=True,
    ).stdout.strip()
    if not data_worker or "\n" in data_worker:
        raise RuntimeError("INTERACTION_DATA_WORKER_NOT_UNIQUE")
    env = {
        **os.environ,
        "PLATFORM_INTERACTION_GATE": "1",
        "PLATFORM_BASE_URL": base_url,
        "PLATFORM_WEBUI_REPORT": str(report),
        "PLATFORM_CASE_CREDENTIALS_FILE": str(credentials),
        "PLATFORM_INTERACTION_INPUT_VERSION": dataset["currentVersion"],
        "PLATFORM_TLI_EXECUTABLE": str(programs / "groundwater-tli"),
        "PLATFORM_API_PYTHON": str(programs / "python"),
        "PLATFORM_RECOVERY_DOCKER": command[0],
        "PLATFORM_RECOVERY_NATIVE_WORKER": worker,
        "PLATFORM_PUBLICATION_DATA_WORKER": data_worker,
        "GROUNDWATER_API_TOKEN": os.environ[TOKEN_ENVIRONMENT],
    }
    for name in (TOKEN_ENVIRONMENT, "NO_COLOR", "PYTHONPATH"):
        env.pop(name, None)
    outcome = subprocess.run(
        ["npm", "exec", "--", "playwright", "test", *[f"e2e/{spec}" for spec in SPECS]],
        cwd=ROOT / "web", env=env, check=False,
    )
    path = report / "results.json"
    stats = json.loads(path.read_text())["stats"] if path.exists() else {}
    passed = outcome.returncode == 0 and stats.get("expected") == EXPECTED_TESTS and not any(
        stats.get(key) for key in ("unexpected", "flaky", "skipped")
    )
    (report / "gate.json").write_text(
        json.dumps(
            {
                "status": "PASS" if passed else "FAIL",
                "composeProject": compose_project,
                "projectId": project_id,
                "gateway": base_url,
                "inputVersion": dataset["currentVersion"],
                "testProvider": "local fixed fixture",
                "wheelSha256": wheel_hashes,
                "specs": SPECS,
                "stats": stats,
                "playwrightExitCode": outcome.returncode,
            },
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    if not passed:
        raise AssertionError(f"INTERACTION_GATE_FAILED:{report}")
