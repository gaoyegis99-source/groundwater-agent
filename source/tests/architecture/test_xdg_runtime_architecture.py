from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.maintenance import bootstrap_toolchain
from scripts.maintenance.xdg_layout import (
    LayoutRefusal,
    resolve_lifecycle_paths,
)

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT.parent


def _environment(tmp_path: Path) -> dict[str, str]:
    return {
        "HOME": str(tmp_path / "home"),
        "XDG_CONFIG_HOME": str(tmp_path / "config-home"),
        "XDG_DATA_HOME": str(tmp_path / "data-home"),
        "XDG_STATE_HOME": str(tmp_path / "state-home"),
        "XDG_CACHE_HOME": str(tmp_path / "cache-home"),
        "XDG_RUNTIME_DIR": str(tmp_path / "runtime-home"),
    }


def test_xdg_roles_are_disjoint_and_resolution_has_no_side_effects(
    tmp_path: Path,
) -> None:
    paths = resolve_lifecycle_paths(_environment(tmp_path), uid=os.getuid())

    assert paths.config == tmp_path / "config-home/groundwater-v6"
    assert paths.instances == tmp_path / "data-home/groundwater-v6/instances"
    assert paths.audit == tmp_path / "state-home/groundwater-v6/audit"
    assert paths.docker == tmp_path / "cache-home/groundwater-v6/docker"
    assert paths.socket == tmp_path / "runtime-home/groundwater-v6/docker.sock"
    assert not any(path.exists() for path in (paths.config, paths.data, paths.cache))
    assert not paths.docker.is_relative_to(paths.instances)


def test_instance_identifiers_cannot_escape(tmp_path: Path) -> None:
    paths = resolve_lifecycle_paths(_environment(tmp_path))
    assert paths.instance_data("project-01") == paths.instances / "project-01"
    with pytest.raises(LayoutRefusal, match="INSTANCE_ID_INVALID"):
        paths.instance_data("../project")


def test_layout_schema_four_has_no_repository_runtime_role() -> None:
    layout = json.loads((PROJECT / "project-layout.json").read_text())
    assert layout["schema_version"] == 4
    assert set(layout["lifecycle"]) == {
        "config",
        "data",
        "state",
        "cache",
        "runtime",
    }
    assert "runtime" not in layout
    assert layout["docker"]["authoritative_volumes"] is False


def test_doctor_runs_without_runtime_or_project_environment(tmp_path: Path) -> None:
    environment = {**os.environ, **_environment(tmp_path)}
    completed = subprocess.run(
        [sys.executable, "-B", str(ROOT / "scripts/maintenance/gw.py"), "doctor"],
        cwd=PROJECT,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["status"] == "NOT_READY"
    assert payload["docker_contacted"] is False
    assert payload["paths"]["cache"].startswith(str(tmp_path))
    assert not (PROJECT / "runtime").exists()


def test_toolchain_location_is_bound_to_lock_digest(tmp_path: Path) -> None:
    paths = resolve_lifecycle_paths(_environment(tmp_path))
    first = bootstrap_toolchain.environment_path(ROOT, paths)
    assert first.parent == paths.toolchains
    assert first.name.startswith("v6-")
    assert bootstrap_toolchain.status(ROOT, paths)["status"] == "MISSING"


def test_staged_toolchain_rewrites_absolute_console_script_paths(
    tmp_path: Path,
) -> None:
    paths = resolve_lifecycle_paths(_environment(tmp_path))
    staging = paths.toolchains / ".tmp-v6-fixture"
    target = bootstrap_toolchain.environment_path(ROOT, paths)
    (staging / "bin").mkdir(parents=True)
    script = staging / "bin/pytest"
    script.write_text(f"#!{staging}/bin/python\nprint('ok')\n")
    script.chmod(0o755)

    bootstrap_toolchain._rewrite_staged_environment(staging, target, ROOT)

    assert script.read_text().splitlines()[0] == f"#!{target}/bin/python"
    marker = json.loads((staging / bootstrap_toolchain.MARKER).read_text())
    assert marker["path"] == str(target)
    assert marker["digest"] == bootstrap_toolchain.toolchain_digest(ROOT)
