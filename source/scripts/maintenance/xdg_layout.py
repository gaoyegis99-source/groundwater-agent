"""Groundwater V6 lifecycle paths built on the XDG base-directory contract.

This module is deliberately standard-library only. It is imported by the
bootstrap, doctor and Docker boundary before a project Python environment
exists. Resolving paths never creates them.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

PROJECT = "groundwater-v6"
_INSTANCE_ID = re.compile(r"[a-z0-9][a-z0-9.-]{0,62}\Z")


class LayoutRefusalError(RuntimeError):
    """A configured lifecycle path does not belong to its declared root."""


LayoutRefusal = LayoutRefusalError


def _absolute(value: str | Path, code: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise LayoutRefusal(code)
    return path.absolute()


def _existing_chain_has_symlink(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            return True
        if not current.exists():
            break
    return False


def owned_path(value: str | Path, allowed: Path) -> Path:
    """Return an absolute path below ``allowed`` without following links."""

    root = _absolute(allowed, "OWNED_ROOT_NOT_ABSOLUTE")
    raw = Path(value).expanduser()
    path = raw if raw.is_absolute() else root / raw
    if ".." in path.parts:
        raise LayoutRefusal("PATH_ESCAPE_REJECTED")
    path = path.absolute()
    if not path.is_relative_to(root):
        raise LayoutRefusal("PATH_OUTSIDE_OWNED_ROOT")
    if _existing_chain_has_symlink(path):
        raise LayoutRefusal("WRITABLE_PATH_SYMLINK_REJECTED")
    return path


def validate_instance_id(value: str) -> str:
    if not _INSTANCE_ID.fullmatch(value):
        raise LayoutRefusal("INSTANCE_ID_INVALID")
    return value


@dataclass(frozen=True)
class LifecyclePaths:
    """Resolved path roles for exactly one operating-system user."""

    home: Path
    config_home: Path
    data_home: Path
    state_home: Path
    cache_home: Path
    runtime_home: Path
    config: Path
    data: Path
    state: Path
    cache: Path
    runtime: Path
    instances: Path
    audit: Path
    logs: Path
    toolchains: Path
    build: Path
    docker: Path
    socket: Path

    def instance_data(self, instance_id: str) -> Path:
        return owned_path(validate_instance_id(instance_id), self.instances)

    def instance_state(self, instance_id: str) -> Path:
        return owned_path(
            Path("instances") / validate_instance_id(instance_id), self.state
        )

def resolve_lifecycle_paths(
    inherited: Mapping[str, str] | None = None,
    *,
    uid: int | None = None,
) -> LifecyclePaths:
    """Resolve XDG defaults without creating or resolving through any path."""

    env = os.environ if inherited is None else inherited
    effective_uid = os.getuid() if uid is None else uid
    home = _absolute(env.get("HOME", str(Path.home())), "HOME_NOT_ABSOLUTE")
    config_home = _absolute(
        env.get("XDG_CONFIG_HOME", str(home / ".config")),
        "XDG_CONFIG_HOME_NOT_ABSOLUTE",
    )
    data_home = _absolute(
        env.get("XDG_DATA_HOME", str(home / ".local/share")),
        "XDG_DATA_HOME_NOT_ABSOLUTE",
    )
    state_home = _absolute(
        env.get("XDG_STATE_HOME", str(home / ".local/state")),
        "XDG_STATE_HOME_NOT_ABSOLUTE",
    )
    cache_home = _absolute(
        env.get("XDG_CACHE_HOME", str(home / ".cache")),
        "XDG_CACHE_HOME_NOT_ABSOLUTE",
    )
    runtime_home = _absolute(
        env.get("XDG_RUNTIME_DIR", f"/run/user/{effective_uid}"),
        "XDG_RUNTIME_DIR_NOT_ABSOLUTE",
    )

    config = owned_path(PROJECT, config_home)
    data = owned_path(PROJECT, data_home)
    state = owned_path(PROJECT, state_home)
    cache = owned_path(PROJECT, cache_home)
    runtime = owned_path(PROJECT, runtime_home)
    return LifecyclePaths(
        home=home,
        config_home=config_home,
        data_home=data_home,
        state_home=state_home,
        cache_home=cache_home,
        runtime_home=runtime_home,
        config=config,
        data=data,
        state=state,
        cache=cache,
        runtime=runtime,
        instances=owned_path("instances", data),
        audit=owned_path("audit", state),
        logs=owned_path("logs", state),
        toolchains=owned_path("toolchains", cache),
        build=owned_path("build", cache),
        docker=owned_path("docker", cache),
        socket=owned_path("docker.sock", runtime),
    )


def private_root_status(paths: LifecyclePaths) -> dict[str, str]:
    """Inspect persistent project roots without creating or changing them."""

    result: dict[str, str] = {}
    for name in ("config", "data", "state", "cache"):
        path = getattr(paths, name)
        if path.is_symlink():
            result[name] = "SYMLINK"
        elif not path.exists():
            result[name] = "MISSING"
        elif not path.is_dir():
            result[name] = "INVALID"
        else:
            metadata = path.stat()
            if metadata.st_uid != os.getuid():
                result[name] = "FOREIGN_OWNER"
            elif metadata.st_mode & 0o077:
                result[name] = "INSECURE_MODE"
            else:
                result[name] = "PASS"
    return result


def ensure_private_roots(
    paths: LifecyclePaths, names: tuple[str, ...] = ("config", "data", "state", "cache")
) -> None:
    """Create selected XDG project roots and enforce owner-only access."""

    allowed = {"config", "data", "state", "cache", "runtime"}
    if not names or any(name not in allowed for name in names):
        raise LayoutRefusal("PRIVATE_ROOT_SELECTION_INVALID")
    for name in names:
        path = getattr(paths, name)
        if _existing_chain_has_symlink(path):
            raise LayoutRefusal("PRIVATE_ROOT_SYMLINK_REJECTED")
        if path.exists():
            metadata = path.stat()
            if not path.is_dir() or metadata.st_uid != os.getuid():
                raise LayoutRefusal("PRIVATE_ROOT_OWNERSHIP_INVALID")
        else:
            path.mkdir(parents=True, mode=0o700)
        path.chmod(0o700)
