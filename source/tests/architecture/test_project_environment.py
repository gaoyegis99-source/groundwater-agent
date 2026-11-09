from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.maintenance import project_environment as environment

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "scripts/maintenance/bootstrap-assets"


@pytest.fixture
def project(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    for name in (
        "pyproject.toml",
        "uv.lock",
        "toolchain.lock.json",
        "package-lock.json",
        "system-deps.lock.json",
    ):
        shutil.copy2(ROOT / name, source / name)
    for name in ("common", "native", "qgis", "build", "testing"):
        target = source / "environments" / name
        target.mkdir(parents=True)
        for lock in (ROOT / "environments" / name).glob("*.lock"):
            shutil.copy2(lock, target / lock.name)
    content = (
        (ASSETS / "project-layout.json")
        .read_text()
        .replace("__UID__", str(os.getuid()))
    )
    (tmp_path / "project-layout.json").write_text(content)
    monkeypatch.setattr(environment, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(environment, "SOURCE_ROOT", source)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg/config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg/data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg/state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg/cache"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "xdg/runtime"))
    for key in tuple(os.environ):
        if key.startswith(("V6_", "V7_")) or key in {
            "PLATFORM_GENERATED_ROOT",
            "UV_PROJECT_ENVIRONMENT",
        }:
            monkeypatch.delenv(key)
    return tmp_path


def test_owned_paths_reject_sibling_traversal_and_symlink(project):
    allowed = project / "xdg/cache/groundwater-v6/build"
    with pytest.raises(environment.EnvironmentRefusal):
        environment.owned_path(project.parent / "sibling/runtime/work", allowed)
    with pytest.raises(environment.EnvironmentRefusal):
        environment.owned_path(allowed / "../active", allowed)
    allowed.mkdir(parents=True)
    (allowed / "linked").symlink_to(project.parent)
    with pytest.raises(environment.EnvironmentRefusal):
        environment.owned_path(allowed / "linked/cache", allowed)


def test_validation_outputs_are_worktree_scoped_outside_source(project):
    first = environment.validation_cache_root(environment.SOURCE_ROOT)
    second = environment.validation_cache_root(project / "other-source")
    receipts = environment.validation_state_root(environment.SOURCE_ROOT)

    assert first.parent.parent.parent == project / "xdg/cache/groundwater-v6/build"
    assert receipts.parent.parent.parent == project / "xdg/state/groundwater-v6/audit"
    assert first.name != second.name
    assert not first.is_relative_to(environment.SOURCE_ROOT)
    assert not receipts.is_relative_to(environment.SOURCE_ROOT)


def test_testing_environment_scrubs_credentials_after_saving_valid_paths(project):
    prefix = "V6" if environment.PROJECT.endswith("v6") else "V7"
    key = "PLATFORM_GENERATED_ROOT"
    xdg = {
        "HOME": str(project / "home"),
        "XDG_CONFIG_HOME": str(project / "xdg/config"),
        "XDG_DATA_HOME": str(project / "xdg/data"),
        "XDG_STATE_HOME": str(project / "xdg/state"),
        "XDG_CACHE_HOME": str(project / "xdg/cache"),
        "XDG_RUNTIME_DIR": str(project / "xdg/runtime"),
    }
    result = environment.child_environment(
        testing=True,
        inherited={
            **xdg,
            "PATH": os.defpath,
            key: str(project / "xdg/cache/groundwater-v6/build/fixture"),
            f"{prefix}_OWNER_TOKEN": "dummy-never-log",
            f"{prefix}_DATABASE_URL": "dummy",
            "DOCKER_HOST": "unix:///foreign.sock",
            "DOCKER_CONTEXT": "foreign",
            "DOCKER_CERT_PATH": "/foreign",
            "DOCKER_TLS_VERIFY": "1",
            "DOCKER_TLS": "1",
            "PYTHONPATH": "/foreign/source",
            "PYTHONHOME": "/foreign/python",
            "VIRTUAL_ENV": "/foreign/venv",
            "UV_PROJECT": "/foreign/source",
            "UV_ACTIVE": "1",
        },
    )
    assert result[key] == str(project / "xdg/cache/groundwater-v6/build/fixture")
    assert f"{prefix}_OWNER_TOKEN" not in result
    assert f"{prefix}_DATABASE_URL" not in result
    assert (
        result["DOCKER_HOST"]
        == f"unix://{project}/xdg/runtime/{environment.PROJECT}/docker.sock"
    )
    assert not {"DOCKER_CONTEXT", "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY"}.intersection(
        result
    )
    assert result["PYTHONDONTWRITEBYTECODE"] == "1"
    assert result["PYTHONPATH"] == str(project / "source")
    assert result["VIRTUAL_ENV"] == result["UV_PROJECT_ENVIRONMENT"]
    assert not {"DOCKER_TLS", "PYTHONHOME", "UV_PROJECT", "UV_ACTIVE"}.intersection(
        result
    )


@pytest.mark.parametrize(
    "arguments", [["--context", "foreign"], ["--host=unix:///foreign"], ["-Hforeign"]]
)
def test_docker_cli_overrides_are_rejected(arguments):
    with pytest.raises(environment.EnvironmentRefusal, match="OVERRIDE_REJECTED"):
        environment.docker_arguments(arguments)


def test_foreign_socket_in_layout_is_rejected(project):
    file = project / "project-layout.json"
    value = json.loads(file.read_text())
    value["docker"]["socket"] = "other-docker.sock"
    file.write_text(json.dumps(value))
    with pytest.raises(environment.EnvironmentRefusal, match="OUTSIDE_PROJECT"):
        environment.socket_path()


def test_docker_context_short_flag_is_rejected():
    for arguments in (["-c", "foreign"], ["-cforeign"], ["-c=foreign"]):
        with pytest.raises(environment.EnvironmentRefusal, match="OVERRIDE_REJECTED"):
            environment.docker_arguments(arguments)


@pytest.mark.parametrize("command", ["run", "exec"])
@pytest.mark.parametrize("shell", ["sh", "/bin/sh", "bash", "/bin/bash"])
def test_container_shell_command_flag_does_not_override_docker_target(command, shell):
    arguments = [command, "isolated-container", shell, "-c", "printf ready"]
    assert environment.docker_arguments(arguments) == ["/usr/bin/docker", *arguments]
    with pytest.raises(environment.EnvironmentRefusal, match="OVERRIDE_REJECTED"):
        environment.docker_arguments(["-c", "foreign", *arguments])


@pytest.mark.parametrize("compose_command", ["run", "exec"])
@pytest.mark.parametrize("interpreter", ["python", "/usr/bin/python3", "/bin/sh"])
def test_compose_container_interpreter_flag_does_not_override_docker_target(
    compose_command, interpreter
):
    arguments = [
        "compose",
        "-p",
        "isolated",
        compose_command,
        "-T",
        "service",
        interpreter,
        "-c",
        "print('ready')",
    ]
    assert environment.docker_arguments(arguments) == ["/usr/bin/docker", *arguments]
    with pytest.raises(environment.EnvironmentRefusal, match="OVERRIDE_REJECTED"):
        environment.docker_arguments(["-c", "foreign", *arguments])


def test_compose_run_entrypoint_interpreter_consumes_command_flag():
    arguments = [
        "compose",
        "-p",
        "isolated",
        "run",
        "--rm",
        "--entrypoint",
        "/usr/bin/python3",
        "service",
        "-c",
        "print('ready')",
    ]
    assert environment.docker_arguments(arguments) == ["/usr/bin/docker", *arguments]
    with pytest.raises(environment.EnvironmentRefusal, match="OVERRIDE_REJECTED"):
        environment.docker_arguments(["-c", "foreign", *arguments])


def test_docker_run_entrypoint_shell_consumes_command_flag():
    arguments = [
        "run",
        "--rm",
        "--network",
        "none",
        "--entrypoint",
        "/bin/sh",
        "example/image:tag",
        "-c",
        "set -eu; printf ready",
    ]
    assert environment.docker_arguments(arguments) == ["/usr/bin/docker", *arguments]


def test_bootstrap_uses_only_tracked_assets_and_refuses_overwrite(tmp_path):
    maintenance = tmp_path / "source" / "scripts" / "maintenance"
    maintenance.mkdir(parents=True)
    for name in ("bootstrap_project.py", "project_environment.py"):
        shutil.copy2(ROOT / "scripts" / "maintenance" / name, maintenance / name)
    shutil.copytree(ASSETS, maintenance / "bootstrap-assets")
    script = maintenance / "bootstrap_project.py"

    def run(*args):
        return subprocess.run(
            [sys.executable, "-B", str(script), *args], capture_output=True, text=True
        )

    assert json.loads(run().stdout)["status"] == "PLANNED"
    assert not (tmp_path / "bin").exists()
    assert run("--execute").returncode == 0
    assert run("--execute").returncode == 0
    assert run("--check").returncode == 0
    assert not (tmp_path / "runtime").exists()
    layout = json.loads((tmp_path / "project-layout.json").read_text())
    assert layout["schema_version"] == 4
    assert "runtime" not in layout
    assert "worktrees" not in layout
    assert "observed_active_release" not in layout.get("runtime_status", {})
    launcher = tmp_path / "bin/gw"
    launcher.write_text("custom entrypoint")
    assert run("--execute").returncode == 1
    assert launcher.read_text() == "custom entrypoint"


def test_direct_docker_launcher_rejects_target_override_without_socket(tmp_path):
    maintenance = tmp_path / "source" / "scripts" / "maintenance"
    maintenance.mkdir(parents=True)
    for name in (
        "project_environment.py",
        "xdg_layout.py",
        "toolchain_contract.py",
        "dependency_contract.py",
    ):
        shutil.copy2(ROOT / "scripts/maintenance" / name, maintenance / name)
    launcher = tmp_path / "bin/docker"
    launcher.parent.mkdir()
    shutil.copy2(ASSETS / "bin/docker", launcher)
    result = subprocess.run(
        ["bash", str(launcher), "--host", "unix:///foreign"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 75
    assert "OVERRIDE_REJECTED" in result.stderr


def test_public_runtime_override_rejects_a_sibling_project(project, monkeypatch):
    monkeypatch.setenv(
        "PLATFORM_GENERATED_ROOT", str(project.parent / "sibling/runtime/work")
    )
    with pytest.raises(environment.EnvironmentRefusal, match="OUTSIDE"):
        environment.paths()


def test_direct_dev_rejects_foreign_output_before_assembly(tmp_path):
    foreign = ROOT.parent.parent / ("refused-output-" + tmp_path.name)
    env = dict(os.environ, PLATFORM_GENERATED_ROOT=str(foreign))
    completed = subprocess.run(
        [sys.executable, "-B", str(ROOT / "scripts/development/dev.py"), "compile"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 75
    assert "OUTSIDE_OWNED_ROOT" in completed.stderr
    assert not foreign.exists()
