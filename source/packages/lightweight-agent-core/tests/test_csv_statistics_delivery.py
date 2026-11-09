from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PACK_ROOT = PACKAGE_ROOT / "examples" / "domain-packs" / "csv-statistics"


def _run(command: list[str], *, cwd: Path, environment: dict[str, str]) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    assert completed.returncode == 0, (
        f"command failed: {' '.join(command)}\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    return completed.stdout


def _write_runner(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is required for wheel gate")
def test_csv_capability_clean_install_restart_and_bundle_round_trip(
    tmp_path: Path,
) -> None:
    """The example is a shipped capability, not an in-tree handler fixture."""

    wheel = next((PACK_ROOT / "artifacts").glob("*.whl"))
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)

    source_copy = tmp_path / "core-source"
    shutil.copytree(
        PACKAGE_ROOT,
        source_copy,
        ignore=shutil.ignore_patterns(
            ".venv",
            ".hypothesis",
            "build",
            "dist",
            "*.egg-info",
            "__pycache__",
        ),
    )
    dist = tmp_path / "dist"
    dist.mkdir()
    uv = shutil.which("uv")
    assert uv is not None
    capability_source = tmp_path / "capability-source"
    shutil.copytree(
        PACK_ROOT / "capability",
        capability_source,
        ignore=shutil.ignore_patterns(
            ".venv", "build", "dist", "*.egg-info", "__pycache__"
        ),
    )
    _run([uv, "lock", "--check"], cwd=capability_source, environment=environment)
    rebuilt_capability = tmp_path / "capability-dist"
    rebuilt_capability.mkdir()
    reproducible_environment = {**environment, "SOURCE_DATE_EPOCH": "0"}
    _run(
        [uv, "build", "--wheel", "--out-dir", str(rebuilt_capability)],
        cwd=capability_source,
        environment=reproducible_environment,
    )
    rebuilt_wheel = next(rebuilt_capability.glob("*.whl"))
    assert rebuilt_wheel.read_bytes() == wheel.read_bytes()

    _run([uv, "build", "--wheel", "--out-dir", str(dist)], cwd=source_copy, environment=environment)
    core_wheel = next(dist.glob("platform_lightweight_agent_core-*.whl"))

    venv = tmp_path / "venv"
    _run([sys.executable, "-m", "venv", str(venv)], cwd=tmp_path, environment=environment)
    python = venv / "bin" / "python"
    _run(
        [
            uv,
            "pip",
            "install",
            "--python",
            str(python),
            f"{core_wheel}[authoring,workflow]",
            str(wheel),
        ],
        cwd=tmp_path,
        environment=environment,
    )

    state = tmp_path / "state"
    state.mkdir()
    first = tmp_path / "first.py"
    _write_runner(
        first,
        r'''
import json
import sys
from pathlib import Path

import anyio
from platform_lightweight_agent import (
    FileWorkspace,
    PortableTarget,
    SQLiteRunJournal,
    ToolContext,
    ToolDelivery,
    ToolReference,
    compile_domain_pack,
    load_project_runtime,
)

pack_root = Path(sys.argv[1]).resolve()
state = Path(sys.argv[2]).resolve()
compiled_root = state / "compiled"
compiled = compile_domain_pack(
    pack_root,
    compiled_root,
    parameter_profile="small",
)
project = compiled.projects["analysis"]
manifests = json.loads((pack_root / "tools.json").read_text())
tools = tuple(
    ToolReference.from_mapping(
        {key: item[key] for key in ("tool_id", "version", "package_digest")}
    )
    for item in manifests
)
raw_deliveries = json.loads((pack_root / "release-deliveries.json").read_text())
deliveries = []
for name in ("validate", "calculate", "blocked"):
    raw = dict(raw_deliveries[name])
    raw["wheel_path"] = str(pack_root / raw["wheel_path"])
    deliveries.append(ToolDelivery.from_mapping(raw))
target = PortableTarget(
    trusted_tools=tools,
    allowed_permissions=frozenset({"files.read", "files.write"}),
    python_executable=sys.executable,
    tool_deliveries=tuple(deliveries),
)

workspace = FileWorkspace.under(state / "workspace")
valid_source = state / "valid.csv"
valid_source.write_text("level\n1\n2\n3\n", encoding="utf-8")
bad_source = state / "bad.csv"
bad_source.write_text("level\n1\nnot-a-number\n", encoding="utf-8")
valid_ref = workspace.stage_input(valid_source, "inputs/valid.csv")
bad_ref = workspace.stage_input(bad_source, "inputs/bad.csv")
(state / "references.json").write_text(
    json.dumps({"valid": valid_ref.to_mapping(), "bad": bad_ref.to_mapping()}),
    encoding="utf-8",
)

def context_factory(request, cancellation):
    return ToolContext(request.action_id, cancellation, workspace)

journal = SQLiteRunJournal(state / "runs.sqlite")
runtime = load_project_runtime(
    project,
    target,
    journal,
    tool_context_factory=context_factory,
)

async def main():
    waiting = await runtime.engine.run_async(
        runtime.workflow,
        {"file": valid_ref.to_mapping(), "column": "level"},
        run_id="csv-valid",
    )
    blocked = await runtime.engine.run_async(
        runtime.workflow,
        {"file": bad_ref.to_mapping(), "column": "level"},
        run_id="csv-invalid",
    )
    assert waiting.status == "WAITING_APPROVAL"
    assert blocked.status == "SUCCEEDED"
    assert blocked.state["v3"]["output"]["business_status"] == "BLOCKED"
    assert blocked.state["v3"]["output"]["result_file"] is None
    approval_id = next(iter(waiting.state["v3"]["pending_approvals"]))
    print(json.dumps({"approval_id": approval_id, "blocked": blocked.status}))

anyio.run(main)
''',
    )
    first_result = json.loads(
        _run(
            [str(python), str(first), str(PACK_ROOT), str(state)],
            cwd=tmp_path,
            environment=environment,
        )
    )
    assert first_result["blocked"] == "SUCCEEDED"

    second = tmp_path / "second.py"
    _write_runner(
        second,
        r'''
import json
import sys
from pathlib import Path

import anyio
from platform_lightweight_agent import (
    ApprovalDecision,
    FileWorkspace,
    PortableTarget,
    SQLiteRunJournal,
    ToolContext,
    ToolDelivery,
    ToolReference,
    export_project_bundle,
    import_bundle_project,
    load_project_runtime,
    load_resume_runtime,
)

pack_root = Path(sys.argv[1]).resolve()
state = Path(sys.argv[2]).resolve()
approval_id = sys.argv[3]
project = state / "compiled" / "entries" / "analysis"
manifests = json.loads((pack_root / "tools.json").read_text())
tools = tuple(
    ToolReference.from_mapping(
        {key: item[key] for key in ("tool_id", "version", "package_digest")}
    )
    for item in manifests
)
raw_deliveries = json.loads((pack_root / "release-deliveries.json").read_text())
deliveries = []
for name in ("validate", "calculate", "blocked"):
    raw = dict(raw_deliveries[name])
    raw["wheel_path"] = str(pack_root / raw["wheel_path"])
    deliveries.append(ToolDelivery.from_mapping(raw))
target = PortableTarget(
    trusted_tools=tools,
    allowed_permissions=frozenset({"files.read", "files.write"}),
    python_executable=sys.executable,
    tool_deliveries=tuple(deliveries),
)
workspace = FileWorkspace.under(state / "workspace")

def context_factory(request, cancellation):
    return ToolContext(request.action_id, cancellation, workspace)

journal = SQLiteRunJournal(state / "runs.sqlite")
runtime = load_resume_runtime(
    project,
    target,
    journal,
    "csv-valid",
    tool_context_factory=context_factory,
)

async def main():
    completed = await runtime.engine.resume_async(
        "csv-valid",
        approvals=(ApprovalDecision(approval_id, True),),
    )
    assert completed.status == "SUCCEEDED"
    output = completed.state["v3"]["output"]
    result_path = workspace.resolve_input(output["result_file"])
    assert json.loads(result_path.read_text()) == {
        "count": 3,
        "maximum": 3.0,
        "mean": 2.0,
        "minimum": 1.0,
    }

    archive = state / "csv-statistics.v6bundle"
    export_project_bundle(project, archive)
    imported = import_bundle_project(
        archive,
        state / "imported",
        target,
        check_dependencies=False,
    )
    fresh_journal = SQLiteRunJournal(state / "imported.sqlite")
    imported_runtime = load_project_runtime(
        imported,
        target,
        fresh_journal,
        tool_context_factory=context_factory,
    )
    references = json.loads((state / "references.json").read_text())
    waiting = await imported_runtime.engine.run_async(
        imported_runtime.workflow,
        {"file": references["valid"], "column": "level"},
        run_id="csv-imported",
    )
    imported_approval = next(iter(waiting.state["v3"]["pending_approvals"]))
    imported_completed = await imported_runtime.engine.resume_async(
        "csv-imported",
        approvals=(ApprovalDecision(imported_approval, True),),
    )
    assert imported_completed.status == "SUCCEEDED"
    assert imported_completed.state["v3"]["output"]["summary"]["mean"] == 2.0
    print(json.dumps({
        "status": completed.status,
        "imported_status": imported_completed.status,
        "result": output["result_file"],
    }))

anyio.run(main)
''',
    )
    result = json.loads(
        _run(
            [
                str(python),
                str(second),
                str(PACK_ROOT),
                str(state),
                first_result["approval_id"],
            ],
            cwd=tmp_path,
            environment=environment,
        )
    )
    assert result["status"] == "SUCCEEDED"
    assert result["imported_status"] == "SUCCEEDED"
    assert result["result"]["logical_id"] == "reports/csv-statistics.json"
