from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def _run(
    command: list[str], *, cwd: Path, environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, (
        f"command failed: {' '.join(command)}\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    return completed


def _snapshot(command: list[str], *, cwd: Path, environment: dict[str, str]) -> dict:
    return json.loads(_run(command, cwd=cwd, environment=environment).stdout)


def _approval_workflow() -> dict:
    empty = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    approved = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"approved": {"type": "boolean"}},
        "required": ["approved"],
        "additionalProperties": False,
    }
    return {
        "schema_version": "v6-light-workflow-v3",
        "workflow_id": "installed-approval",
        "version": "1",
        "input_schema": empty,
        "output_schema": approved,
        "output_bindings": {
            "/approved": {
                "source": "step_output",
                "step_id": "review",
                "pointer": "/approved",
            }
        },
        "execution_policy": {"max_concurrency": 1, "run_timeout_seconds": None},
        "steps": [
            {
                "id": "review",
                "kind": "approval",
                "config": {"prompt": "Approve installed wheel?"},
                "contract": {
                    "input_schema": empty,
                    "output_schema": approved,
                    "bindings": {},
                    "timeout_seconds": None,
                },
            }
        ],
    }


def _complete_approval(
    cli: Path,
    workflow: Path,
    journal: Path,
    run_id: str,
    *,
    cwd: Path,
    environment: dict[str, str],
) -> dict:
    waiting = _snapshot(
        [
            str(cli),
            "run",
            str(workflow),
            "--journal",
            str(journal),
            "--run-id",
            run_id,
        ],
        cwd=cwd,
        environment=environment,
    )
    assert waiting["status"] == "WAITING_APPROVAL"
    approval_id = next(iter(waiting["state"]["v3"]["pending_approvals"]))
    # Exercise the shipped CLI's error path before the valid decision.  An
    # invalid control request must not turn the persisted run into a
    # cancellation or otherwise make the pending approval unrecoverable.
    invalid = subprocess.run(
        [
            str(cli),
            "resume",
            run_id,
            "--journal",
            str(journal),
            "--approval-id",
            "approval-not-pending",
            "--approve",
        ],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert invalid.returncode != 0
    assert json.loads(invalid.stderr)["code"] == "WORKFLOW_RUN_FAILED"
    # This is a fresh process, so the SQLite snapshot and installed wheel—not
    # source imports or process-local state—supply the restart behavior.
    return _snapshot(
        [
            str(cli),
            "resume",
            run_id,
            "--journal",
            str(journal),
            "--approval-id",
            approval_id,
            "--approve",
        ],
        cwd=cwd,
        environment=environment,
    )


def test_base_wheel_runs_offline_without_optional_dependencies(tmp_path: Path) -> None:
    """The base CLI can resume a legacy approval without an index or extras."""

    uv = shutil.which("uv")
    assert uv is not None, "the release gate requires uv"
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    source_copy = tmp_path / "wheel-source"
    shutil.copytree(
        PACKAGE_ROOT,
        source_copy,
        ignore=shutil.ignore_patterns(
            ".venv", ".hypothesis", "build", "dist", "*.egg-info", "__pycache__"
        ),
    )
    dist = tmp_path / "dist"
    dist.mkdir()
    _run(
        [uv, "build", "--wheel", "--out-dir", str(dist)],
        cwd=source_copy,
        environment=environment,
    )
    wheels = tuple(dist.glob("platform_lightweight_agent_core-*.whl"))
    assert len(wheels) == 1
    venv = tmp_path / "venv"
    _run(
        [sys.executable, "-m", "venv", str(venv)],
        cwd=tmp_path,
        environment=environment,
    )
    python = venv / "bin" / "python"
    cli = venv / "bin" / "platform-agent-workflow"
    offline = environment | {
        "PYTHONNOUSERSITE": "1",
        "UV_OFFLINE": "1",
        "PIP_NO_INDEX": "1",
    }
    _run(
        [
            uv,
            "pip",
            "install",
            "--python",
            str(python),
            "--offline",
            "--no-index",
            "--no-cache",
            str(wheels[0]),
        ],
        cwd=tmp_path,
        environment=offline,
    )
    imported = _snapshot(
        [
            str(python),
            "-c",
            (
                "import importlib.metadata as m, json, sys; "
                "import platform_lightweight_agent as p; "
                "import platform_lightweight_agent.api as a; "
                "from platform_lightweight_agent.drivers import "
                "OpenAIAgentsDriver, PydanticAIDriver; "
                "blocked = ('anyio', 'jsonschema', 'pydantic', 'agents', "
                "'copier', 'dbos', 'sqlalchemy', 'psycopg'); "
                "loaded = [name for name in blocked if name in sys.modules]; "
                "requires = m.metadata('platform-lightweight-agent-core')"
                ".get_all('Requires-Dist') or []; "
                "mandatory = [item for item in requires if 'extra ==' not in item]; "
                "print(json.dumps({'path': p.__file__, 'api_path': a.__file__, "
                "'api_names': len(a.__all__), 'loaded': loaded, "
                "'driver_modules': [OpenAIAgentsDriver.__module__, "
                "PydanticAIDriver.__module__], "
                "'mandatory': mandatory}))"
            ),
        ],
        cwd=tmp_path,
        environment=offline,
    )
    assert "site-packages" in imported["path"]
    assert "site-packages" in imported["api_path"]
    assert imported["api_names"] == 41
    assert imported["driver_modules"] == [
        "platform_lightweight_agent.driver_openai_agents",
        "platform_lightweight_agent.driver_pydantic_ai",
    ]
    assert str(PACKAGE_ROOT) not in imported["path"]
    assert imported["loaded"] == []
    assert imported["mandatory"] == []

    definition = tmp_path / "approval.json"
    definition.write_text(
        json.dumps(
            {
                "schema_version": "v6-light-workflow-v1",
                "workflow_id": "base-wheel-approval",
                "version": "1",
                "steps": [
                    {
                        "id": "review",
                        "kind": "approval",
                        "config": {"prompt": "Approve offline wheel?"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    journal = tmp_path / "runs.sqlite"
    waiting = _snapshot(
        [
            str(cli),
            "run",
            str(definition),
            "--journal",
            str(journal),
            "--run-id",
            "base-wheel-run",
        ],
        cwd=tmp_path,
        environment=offline,
    )
    assert waiting["status"] == "WAITING_APPROVAL"
    completed = _snapshot(
        [str(cli), "resume", "base-wheel-run", "--journal", str(journal), "--approve"],
        cwd=tmp_path,
        environment=offline,
    )
    assert completed["status"] == "SUCCEEDED"


def test_built_wheel_delivers_templates_and_restartable_cli(tmp_path: Path) -> None:
    """Exercise the shipped artifact without allowing imports from this tree."""

    uv = shutil.which("uv")
    assert uv is not None, "the release gate requires uv"
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    # Setuptools writes transient ``build/`` and ``*.egg-info`` directories
    # next to the project it builds. Build a faithful disposable source copy
    # so this release gate never leaves artifacts in the developer checkout.
    source_copy = tmp_path / "wheel-source"
    shutil.copytree(
        PACKAGE_ROOT,
        source_copy,
        ignore=shutil.ignore_patterns(
            ".venv", ".hypothesis", "build", "dist", "*.egg-info", "__pycache__"
        ),
    )
    dist = tmp_path / "dist"
    dist.mkdir()
    _run(
        [uv, "build", "--wheel", "--out-dir", str(dist)],
        cwd=source_copy,
        environment=environment,
    )
    wheels = tuple(dist.glob("platform_lightweight_agent_core-*.whl"))
    assert len(wheels) == 1
    wheel = wheels[0]
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    assert "platform_lightweight_agent/console/index.html" in names
    assert "platform_lightweight_agent/console/app.js" in names
    assert "platform_lightweight_agent/console/style.css" in names
    venv = tmp_path / "venv"
    _run(
        [sys.executable, "-m", "venv", str(venv)], cwd=tmp_path, environment=environment
    )
    python = venv / "bin" / "python"
    cli = venv / "bin" / "platform-agent-workflow"
    _run(
        [uv, "pip", "install", "--python", str(python), f"{wheel}[authoring,workflow]"],
        cwd=tmp_path,
        environment=environment,
    )
    installed = _run(
        [
            str(python),
            "-c",
            "import platform_lightweight_agent as p; print(p.__file__)",
        ],
        cwd=tmp_path,
        environment=environment,
    ).stdout.strip()
    assert "site-packages" in installed
    assert str(PACKAGE_ROOT) not in installed

    workflow_project = tmp_path / "workflow-template"
    tool_project = tmp_path / "tool-template"
    _run(
        [str(cli), "init", "workflow", str(workflow_project), "--name", "wheel-flow"],
        cwd=tmp_path,
        environment=environment,
    )
    _run(
        [str(cli), "check", str(workflow_project)],
        cwd=tmp_path,
        environment=environment,
    )
    scripted_path = workflow_project / "scripted-decisions.json"
    assert scripted_path.is_file()
    offline_journal = workflow_project / "offline.sqlite"
    waiting_offline = _snapshot(
        [
            str(cli),
            "run",
            str(workflow_project / "workflow.json"),
            "--journal",
            str(offline_journal),
            "--run-id",
            "installed-offline",
            "--tool-registry",
            "runtime_tools:register",
            "--driver-name",
            "primary",
            "--scripted-decisions",
            str(scripted_path),
            "--input",
            '{"request":"Find the aquifer example."}',
        ],
        cwd=workflow_project,
        environment=environment,
    )
    assert waiting_offline["status"] == "WAITING_APPROVAL"
    assert waiting_offline["execution_mode"] == "simulated"
    offline_approval = next(iter(waiting_offline["state"]["v3"]["pending_approvals"]))
    completed_offline = _snapshot(
        [
            str(cli),
            "resume",
            "installed-offline",
            "--journal",
            str(offline_journal),
            "--approval-id",
            offline_approval,
            "--approve",
            "--tool-registry",
            "runtime_tools:register",
            "--driver-name",
            "primary",
            "--scripted-decisions",
            str(scripted_path),
        ],
        cwd=workflow_project,
        environment=environment,
    )
    assert completed_offline["status"] == "SUCCEEDED"
    assert completed_offline["state"]["v3"]["output"] == {"message": "example:aquifer"}
    _run(
        [str(cli), "init", "tool", str(tool_project), "--name", "wheel-tool"],
        cwd=tmp_path,
        environment=environment,
    )
    _run([str(cli), "check", str(tool_project)], cwd=tmp_path, environment=environment)
    built_tool = _snapshot(
        [str(cli), "build-tool", str(tool_project)],
        cwd=tmp_path,
        environment=environment,
    )
    tool_wheel = next((tool_project / "dist").glob("*.whl"))
    _run(
        [uv, "pip", "install", "--python", str(python), str(tool_wheel)],
        cwd=tmp_path,
        environment=environment,
    )
    # The generated tool has no self-referential wheel hash in its source.
    # Its host verifies the build sidecar and then asks the explicitly named
    # PyPA entry point to bind the host's exact immutable reference.
    bound_tool = _snapshot(
        [
            str(python),
            "-c",
            (
                "from pathlib import Path; import json, sys; "
                "from platform_lightweight_agent import ("
                "FunctionToolExecutor, ToolDelivery, ToolExecutionRequest, "
                "ToolReference, bind_release_tool_delivery); "
                "manifest = json.loads(Path(sys.argv[1]).read_text()); "
                "ref = ToolReference.from_mapping({key: manifest[key] for key in "
                "('tool_id', 'version', 'package_digest')}); "
                "delivery = ToolDelivery("
                "ref, 'release', distribution='wheel-tool', "
                "entry_point='platform_lightweight_agent.tools:example.lookup', "
                "wheel_path=sys.argv[2]); "
                "executor = FunctionToolExecutor(); "
                "bind_release_tool_delivery(executor, delivery); "
                "result = executor.execute(ToolExecutionRequest("
                "'wheel-action', ref, {'query': 'aquifer'})); "
                "print(json.dumps(result.to_mapping(), sort_keys=True))"
            ),
            str(tool_project / "dist" / "tool-manifest.json"),
            str(tool_wheel),
        ],
        cwd=tmp_path,
        environment=environment,
    )
    source_descriptor = json.loads(
        (tool_project / "tool-manifest.json").read_text(encoding="utf-8")
    )
    assert "package_digest" not in source_descriptor
    assert built_tool["package_digest"] != "sha256:" + "0" * 64
    assert bound_tool == {
        "action_id": "wheel-action",
        "error": None,
        "output": {"answer": "example:aquifer"},
        "succeeded": True,
    }

    source_workflow = tmp_path / "approval.json"
    source_workflow.write_text(json.dumps(_approval_workflow()), encoding="utf-8")
    _run(
        [str(cli), "check", str(source_workflow)], cwd=tmp_path, environment=environment
    )
    completed = _complete_approval(
        cli,
        source_workflow,
        tmp_path / "source.sqlite",
        "installed-source",
        cwd=tmp_path,
        environment=environment,
    )
    assert completed["status"] == "SUCCEEDED"

    bundle = tmp_path / "approval.v6bundle"
    _run(
        [str(cli), "export", str(source_workflow), str(bundle)],
        cwd=tmp_path,
        environment=environment,
    )
    # A base-only installation can inspect contracts, but must not claim an
    # async v3 workflow is runnable when its optional runtime is absent.
    base_venv = tmp_path / "base-venv"
    _run(
        [sys.executable, "-m", "venv", str(base_venv)],
        cwd=tmp_path,
        environment=environment,
    )
    base_python = base_venv / "bin" / "python"
    _run(
        [uv, "pip", "install", "--python", str(base_python), str(wheel)],
        cwd=tmp_path,
        environment=environment,
    )
    readiness = json.loads(
        _run(
            [
                str(base_python),
                "-c",
                (
                    "import json, sys; "
                    "from platform_lightweight_agent import PortableTarget, "
                    "inspect_target_environment; "
                    "report = inspect_target_environment("
                    "PortableTarget(python_executable=sys.executable), "
                    "bundle=sys.argv[1]); "
                    "print(json.dumps(report.to_mapping()))"
                ),
                str(bundle),
            ],
            cwd=tmp_path,
            environment=environment,
        ).stdout
    )
    assert readiness["status"] == "incompatible"
    assert "WORKFLOW_RUNTIME_DEPENDENCY_MISSING" in {
        item["code"] for item in readiness["diagnostics"]
    }
    imported = tmp_path / "imported"
    _run(
        [
            str(cli),
            "import",
            str(bundle),
            str(imported),
            "--skip-dependency-check",
        ],
        cwd=tmp_path,
        environment=environment,
    )
    imported_completed = _complete_approval(
        cli,
        imported / "workflow.json",
        tmp_path / "imported.sqlite",
        "installed-imported",
        cwd=tmp_path,
        environment=environment,
    )
    assert imported_completed["status"] == "SUCCEEDED"

    # The local console is shipped as wheel data and remains an optional edge:
    # a clean web/workflow install can construct its app without importing the
    # source tree or a Node/CDN asset pipeline.
    web_venv = tmp_path / "web-venv"
    _run(
        [sys.executable, "-m", "venv", str(web_venv)],
        cwd=tmp_path,
        environment=environment,
    )
    web_python = web_venv / "bin" / "python"
    _run(
        [uv, "pip", "install", "--python", str(web_python), f"{wheel}[workflow,web]"],
        cwd=tmp_path,
        environment=environment,
    )
    web_probe = _run(
        [
            str(web_python),
            "-c",
            (
                "from importlib.resources import files; "
                "from platform_lightweight_agent import (InMemoryRunJournal, "
                "LocalToolExecutor, LocalWebSecurity, WorkflowEngine, create_web_app); "
                "assert 'V6 local run console' in files('platform_lightweight_agent').joinpath('console/index.html').read_text(); "
                "app=create_web_app(WorkflowEngine(InMemoryRunJournal(), LocalToolExecutor(), {}), "
                "security=LocalWebSecurity(session_token='wheel-web-token')); "
                "assert any(getattr(route, 'path', None)=='/console' for route in app.routes); "
                "print('ok')"
            ),
        ],
        cwd=tmp_path,
        environment=environment,
    )
    assert web_probe.stdout.strip() == "ok"


def test_provider_specific_extra_matches_runtime_readiness(tmp_path: Path) -> None:
    """A clean wheel install must not report a provider it cannot construct."""

    uv = shutil.which("uv")
    assert uv is not None
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    source_copy = tmp_path / "provider-source"
    shutil.copytree(
        PACKAGE_ROOT,
        source_copy,
        ignore=shutil.ignore_patterns(
            ".venv", ".hypothesis", "build", "dist", "*.egg-info", "__pycache__"
        ),
    )
    dist = tmp_path / "dist"
    dist.mkdir()
    _run(
        [uv, "build", "--wheel", "--out-dir", str(dist)],
        cwd=source_copy,
        environment=environment,
    )
    wheel = next(dist.glob("platform_lightweight_agent_core-*.whl"))
    venv = tmp_path / "provider-venv"
    _run([uv, "venv", str(venv)], cwd=tmp_path, environment=environment)
    python = venv / "bin" / "python"
    _run(
        [uv, "pip", "install", "--python", str(python), f"{wheel}[pydantic-ai]"],
        cwd=tmp_path,
        environment=environment,
    )
    probe = (
        "import json,sys; from platform_lightweight_agent import ("
        "DriverBinding,PortableTarget,inspect_target_environment); "
        "binding=DriverBinding('agent','pydantic-ai',"
        "'pydantic-ai-native-v3@1.107.9','anthropic:claude-sonnet-4-5',"
        "distribution='pydantic-ai-slim'); "
        "report=inspect_target_environment(PortableTarget("
        "driver_bindings=(binding,),python_executable=sys.executable)); "
        "print(json.dumps(report.to_mapping(),sort_keys=True))"
    )
    slim = _snapshot([str(python), "-c", probe], cwd=tmp_path, environment=environment)
    assert slim["status"] == "incompatible"
    assert slim["checks"]["provider_imports"] == "failed"

    _run(
        [
            uv,
            "pip",
            "install",
            "--python",
            str(python),
            f"{wheel}[pydantic-ai-anthropic]",
        ],
        cwd=tmp_path,
        environment=environment,
    )
    provider_environment = dict(environment)
    provider_environment["ANTHROPIC_API_KEY"] = "test-only-not-sent"
    for name in (
        "ALL_PROXY",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "all_proxy",
        "https_proxy",
        "http_proxy",
    ):
        provider_environment.pop(name, None)
    ready = _snapshot(
        [str(python), "-c", probe], cwd=tmp_path, environment=provider_environment
    )
    assert ready["status"] == "ready"
    assert ready["checks"] == {
        "model_network": "not_checked",
        "package_metadata": "passed",
        "provider_imports": "passed",
        "runtime_construction": "passed",
        "development_top_level_modules": "not_checked",
        "development_full_module_locator": "not_checked",
        "development_entrypoints": "not_checked",
        "tool_registration": "not_checked",
        "tool_business_invocation": "not_checked",
    }
    constructed = _run(
        [
            str(python),
            "-c",
            (
                "from platform_lightweight_agent import PydanticAIDriver; "
                "PydanticAIDriver(model='anthropic:claude-sonnet-4-5'); print('ok')"
            ),
        ],
        cwd=tmp_path,
        environment=provider_environment,
    )
    assert constructed.stdout.strip() == "ok"
    unrelated = _run(
        [
            str(python),
            "-c",
            "import importlib.util; print(importlib.util.find_spec('openai'))",
        ],
        cwd=tmp_path,
        environment=environment,
    )
    assert unrelated.stdout.strip() == "None"


def test_native_drivers_run_from_independent_clean_wheels(tmp_path: Path) -> None:
    """Each SDK wheel must handle typed output and an approval restart offline."""

    uv = shutil.which("uv")
    assert uv is not None
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    for name in (
        "ALL_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "all_proxy", "https_proxy", "http_proxy"
    ):
        environment.pop(name, None)
    source_copy = tmp_path / "sdk-source"
    shutil.copytree(
        PACKAGE_ROOT,
        source_copy,
        ignore=shutil.ignore_patterns(
            ".venv", ".hypothesis", "build", "dist", "*.egg-info", "__pycache__"
        ),
    )
    dist = tmp_path / "dist"
    dist.mkdir()
    _run(
        [uv, "build", "--wheel", "--out-dir", str(dist)],
        cwd=source_copy,
        environment=environment,
    )
    wheel = next(dist.glob("platform_lightweight_agent_core-*.whl"))
    probe = """
import asyncio
import socket
import sys

from platform_lightweight_agent import OpenAIAgentsDriver, PydanticAIDriver
from platform_lightweight_agent.engine import AgentContext
from platform_lightweight_agent.spec import AgentBudget

schema = {
    '$schema': 'https://json-schema.org/draft/2020-12/schema',
    'type': 'object',
    'properties': {'answer': {'type': 'string'}},
    'required': ['answer'],
    'additionalProperties': False,
}
context = AgentContext(
    run_id='wheel-run', workflow_id='wheel-workflow', workflow_version='1',
    step_id='answer', turn=1, inputs={}, step_input={}, values={},
    prior_tool_results=(), workflow_schema_version='v6-light-workflow-v4',
    output_schema=schema, budget=AgentBudget(),
)

def deny_network(*_args, **_kwargs):
    raise AssertionError('SDK attempted network I/O in offline wheel test')

socket.socket.connect = deny_network
if sys.argv[1] == 'pydantic-ai':
    import pydantic_ai
    from pydantic_ai.models.test import TestModel

    driver = PydanticAIDriver(
        agent=pydantic_ai.Agent(TestModel(call_tools=[], custom_output_args={'answer': 'ok'}))
    )
else:
    import agents
    from agents.testing import ModelStep, ScriptedModel, assistant_message

    agents.set_tracing_disabled(True)
    model = ScriptedModel([
        ModelStep(output=(assistant_message('{"answer":"ok"}'),))
    ])
    driver = OpenAIAgentsDriver(agent=agents.Agent(name='wheel', model=model))

decision = asyncio.run(driver.decide_async(context))
assert decision.output == {'answer': 'ok'}
assert decision.tool_calls == ()
assert decision.usage is not None
print('ok')
"""
    for extra in ("pydantic-ai", "openai-agents"):
        venv = tmp_path / f"{extra}-venv"
        _run([uv, "venv", str(venv)], cwd=tmp_path, environment=environment)
        python = venv / "bin" / "python"
        _run(
            [uv, "pip", "install", "--python", str(python), f"{wheel}[{extra}]"],
            cwd=tmp_path,
            environment=environment,
        )
        completed = _run(
            [str(python), "-c", probe, extra],
            cwd=tmp_path,
            environment=environment,
        )
        assert completed.stdout.strip() == "ok"
        missing_runtime_path = tmp_path / f"{extra}-missing-workflow.sqlite"
        missing_runtime = subprocess.run(
            [
                str(python),
                str(source_copy / "tests" / "wheel_native_roundtrip_probe.py"),
                extra,
                str(missing_runtime_path),
            ],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert missing_runtime.returncode != 0
        assert "Install platform-lightweight-agent-core[workflow]" in missing_runtime.stderr
        assert "ExceptionGroup" not in missing_runtime.stderr
        with sqlite3.connect(missing_runtime_path) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM lightweight_agent_runs"
            ).fetchone()[0] == 0
        # The SDK extras expose a driver, while v4 execution additionally
        # requires the independently selectable workflow runtime.
        _run(
            [uv, "pip", "install", "--python", str(python), f"{wheel}[workflow]"],
            cwd=tmp_path,
            environment=environment,
        )
        roundtrip = _run(
            [
                str(python),
                str(source_copy / "tests" / "wheel_native_roundtrip_probe.py"),
                extra,
                str(tmp_path / f"{extra}-roundtrip.sqlite"),
            ],
            cwd=tmp_path,
            environment=environment,
        )
        assert roundtrip.stdout.strip() == "ok"
