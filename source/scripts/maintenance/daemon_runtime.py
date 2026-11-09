"""Boundary checks for the project-specific rootless Docker daemon."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any

from .xdg_layout import LifecyclePaths


class DaemonRefusalError(RuntimeError):
    """The selected Docker endpoint cannot be proven to belong to this user."""


DaemonRefusal = DaemonRefusalError


def docker_environment(paths: LifecyclePaths) -> dict[str, str]:
    environment = dict(os.environ)
    for name in ("DOCKER_CONTEXT", "DOCKER_TLS", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
        environment.pop(name, None)
    environment["DOCKER_HOST"] = f"unix://{paths.socket}"
    return environment


def verify_daemon(paths: LifecyclePaths, docker: Path) -> dict[str, Any]:
    try:
        metadata = paths.socket.stat()
    except OSError as error:
        raise DaemonRefusal("PROJECT_DOCKER_SOCKET_MISSING") from error
    if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise DaemonRefusal("PROJECT_DOCKER_SOCKET_INVALID")
    completed = subprocess.run(
        [str(docker), "info", "--format", "{{json .}}"],
        env=docker_environment(paths),
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    if completed.returncode:
        raise DaemonRefusal("PROJECT_DOCKER_UNAVAILABLE")
    try:
        info = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise DaemonRefusal("PROJECT_DOCKER_INFO_INVALID") from error
    root = Path(info.get("DockerRootDir", "")).absolute()
    if root != paths.docker.absolute():
        raise DaemonRefusal("FOREIGN_DOCKER_ROOT_REJECTED")
    security = info.get("SecurityOptions", [])
    if not isinstance(security, list) or not any("rootless" in str(item) for item in security):
        raise DaemonRefusal("NON_ROOTLESS_DOCKER_REJECTED")
    return {
        "status": "PASS",
        "socket": str(paths.socket),
        "dataRoot": str(root),
        "serverVersion": info.get("ServerVersion"),
        "rootless": True,
    }


def compose_command(
    docker: Path,
    paths: LifecyclePaths,
    compose: Path,
    env_file: Path,
    project: str,
    arguments: list[str],
    *,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    verify_daemon(paths, docker)
    child_environment = docker_environment(paths)
    child_environment.update(environment or {})
    return subprocess.run(
        [
            str(docker),
            "compose",
            "--env-file",
            str(env_file),
            "--project-name",
            project,
            "--file",
            str(compose),
            *arguments,
        ],
        env=child_environment,
        capture_output=True,
        text=True,
        check=False,
    )
