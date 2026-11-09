"""Install each optional capability separately with uv's locked offline cache."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
EXTRAS = {
    "workflow": ("anyio", "jsonschema"),
    "pydantic-ai": ("pydantic_ai", "jsonschema"),
    "openai-agents": ("agents", "jsonschema", "socksio"),
    "authoring": ("copier", "jsonschema"),
    "web": ("fastapi", "sse_starlette", "uvicorn"),
}


def _run(
    command: list[str], *, cwd: Path, environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=True,
        timeout=240,
        check=False,
    )


def collect() -> dict[str, object]:
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required for the extras probe")
    environment = {
        name: value
        for name, value in os.environ.items()
        if "API_KEY" not in name and name not in {"PYTHONPATH", "PYTHONHOME"}
    }
    environment.update({"UV_OFFLINE": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    with tempfile.TemporaryDirectory(prefix="v6-extras-") as raw:
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
        results: list[dict[str, object]] = []
        for extra, modules in EXTRAS.items():
            venv = root / f"venv-{extra}"
            created = _run(
                [uv, "venv", "--python", sys.executable, str(venv)],
                cwd=root,
                environment=environment,
            )
            installed = _run(
                [
                    uv,
                    "pip",
                    "install",
                    "--offline",
                    "--python",
                    str(venv / "bin" / "python"),
                    f"{wheel}[{extra}]",
                ],
                cwd=root,
                environment=environment,
            )
            program = (
                "import importlib,json,platform_lightweight_agent;"
                f"mods={list(modules)!r};"
                "loaded=[];"
                "[(importlib.import_module(name),loaded.append(name)) for name in mods];"
                "print(json.dumps({'loaded':loaded}))"
            )
            imported = _run(
                [str(venv / "bin" / "python"), "-I", "-c", program],
                cwd=root,
                environment=environment,
            )
            requirements = {
                "venv_created": created.returncode == 0,
                "extra_installed_offline": installed.returncode == 0,
                "declared_modules_import": imported.returncode == 0
                and json.loads(imported.stdout)["loaded"] == list(modules),
            }
            results.append(
                {
                    "extra": extra,
                    "contract_result": (
                        "PASS" if all(requirements.values()) else "FAIL"
                    ),
                    "requirements": requirements,
                    "install_stderr": installed.stderr[-2_000:],
                    "import_stderr": imported.stderr[-2_000:],
                }
            )

        base = root / "venv-base"
        _run(
            [uv, "venv", "--python", sys.executable, str(base)],
            cwd=root,
            environment=environment,
        )
        base_install = _run(
            [
                uv,
                "pip",
                "install",
                "--offline",
                "--no-deps",
                "--python",
                str(base / "bin" / "python"),
                str(wheel),
            ],
            cwd=root,
            environment=environment,
        )
        missing_program = (
            "import importlib.util,json,platform_lightweight_agent;"
            "print(json.dumps({'anyio':importlib.util.find_spec('anyio') is not None,"
            "'jsonschema':importlib.util.find_spec('jsonschema') is not None}))"
        )
        missing = _run(
            [str(base / "bin" / "python"), "-I", "-c", missing_program],
            cwd=root,
            environment=environment,
        )
        missing_result = json.loads(missing.stdout) if missing.returncode == 0 else {}
        missing_runtime_program = "\n".join(
            (
                "import json",
                "from platform_lightweight_agent import (",
                "    InMemoryRunJournal, LocalToolExecutor, NodeContract,",
                "    ValueBinding, WorkflowEngine, approval_step, workflow,",
                ")",
                "empty = {'$schema': 'https://json-schema.org/draft/2020-12/schema', "
                "'type': 'object', 'properties': {}, 'required': [], "
                "'additionalProperties': False}",
                "approved = {'$schema': 'https://json-schema.org/draft/2020-12/schema', "
                "'type': 'object', 'properties': {'approved': {'type': 'boolean'}}, "
                "'required': ['approved'], 'additionalProperties': False}",
                "spec = workflow('missing-extra', '1', (approval_step('review', "
                "'approve?', contract=NodeContract(empty, approved, {})),), "
                "schema_version='v6-light-workflow-v3', input_schema=empty, "
                "output_schema=approved, output_bindings={'/approved': "
                "ValueBinding('step_output', '/approved', 'review')})",
                "try:",
                "    WorkflowEngine(InMemoryRunJournal(), LocalToolExecutor(), {}).run(spec, {})",
                "except RuntimeError as error:",
                "    print(json.dumps({'caught': True, 'message': str(error)}))",
                "else:",
                "    print(json.dumps({'caught': False, 'message': ''}))",
            )
        )
        missing_runtime = _run(
            [
                str(base / "bin" / "python"),
                "-I",
                "-c",
                missing_runtime_program,
            ],
            cwd=root,
            environment=environment,
        )
        missing_runtime_result = (
            json.loads(missing_runtime.stdout)
            if missing_runtime.returncode == 0
            else {}
        )
        base_requirements = {
            "base_installed": base_install.returncode == 0,
            "workflow_dependencies_absent": missing_result
            == {"anyio": False, "jsonschema": False},
            "missing_workflow_extra_refuses_execution": missing_runtime_result.get(
                "caught"
            )
            is True
            and missing_runtime_result.get("message")
            == (
                "Install platform-lightweight-agent-core[workflow] for v3/v4 "
                "workflows"
            ),
        }
    requirements = {
        "all_extras_install_separately": all(
            item["contract_result"] == "PASS" for item in results
        ),
        "missing_extra_stays_absent": all(base_requirements.values()),
    }
    return {
        "schema_version": "v6-separate-extras-install-evidence-v1",
        "result": "PASS" if all(requirements.values()) else "FAIL",
        "results": [
            {
                "case": "B03",
                "variant": "separate_offline_extra_installations",
                "contract_result": (
                    "PASS" if all(requirements.values()) else "FAIL"
                ),
                "requirements": requirements,
                "extras": results,
                "base_without_workflow": base_requirements,
                "missing_runtime_result": missing_runtime_result,
                "missing_runtime_stderr": missing_runtime.stderr[-2_000:],
                "network_mode": "UV_OFFLINE=1",
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
    except Exception as error:  # noqa: BLE001 - preserve install infrastructure error
        report = {
            "schema_version": "v6-separate-extras-install-evidence-v1",
            "result": "ERROR",
            "results": [
                {
                    "case": "B03",
                    "variant": "separate_offline_extra_installations",
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
