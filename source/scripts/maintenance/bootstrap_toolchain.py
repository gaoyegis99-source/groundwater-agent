"""Provision the digest-addressed development toolchain outside the source tree."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import shutil
import subprocess
from pathlib import Path
from typing import Any

try:
    from .toolchain_contract import toolchain_digest, toolchain_name
    from .xdg_layout import LifecyclePaths, ensure_private_roots, owned_path
except ImportError:  # pragma: no cover - direct launcher execution
    from toolchain_contract import toolchain_digest, toolchain_name
    from xdg_layout import LifecyclePaths, ensure_private_roots, owned_path


class BootstrapRefusalError(RuntimeError):
    """A bootstrap prerequisite or downloaded artifact failed validation."""


BootstrapRefusal = BootstrapRefusalError
MARKER = ".groundwater-toolchain.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def environment_path(source_root: Path, paths: LifecyclePaths) -> Path:
    return owned_path(toolchain_name(source_root), paths.toolchains)


def _load_lock(source_root: Path) -> dict[str, Any]:
    try:
        lock = json.loads((source_root / "toolchain.lock.json").read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise BootstrapRefusal("TOOLCHAIN_LOCK_INVALID") from error
    if lock.get("schema") != "groundwater-toolchain-lock.v1":
        raise BootstrapRefusal("TOOLCHAIN_LOCK_INVALID")
    return lock


def status(source_root: Path, paths: LifecyclePaths) -> dict[str, Any]:
    lock = _load_lock(source_root)
    target = environment_path(source_root, paths)
    python = target / "bin/python"
    version = None
    if python.is_file() or python.is_symlink():
        completed = subprocess.run(
            [str(python), "-c", "import platform; print(platform.python_version())"],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0:
            version = completed.stdout.strip()
    expected = lock["python"]["version"]
    expected_sha256 = lock["python"]["installed_binary_sha256"]
    actual_sha256 = None
    try:
        executable = python.resolve(strict=True)
        if executable.is_file():
            actual_sha256 = _sha256(executable)
    except OSError:
        pass
    marker_status = "MISSING"
    try:
        marker = json.loads((target / MARKER).read_text())
        if marker == {
            "schemaVersion": "groundwater-toolchain.v1",
            "digest": toolchain_digest(source_root),
            "path": str(target),
        }:
            marker_status = "PASS"
    except (OSError, json.JSONDecodeError):
        pass
    relocation_status = "PASS"
    bin_root = target / "bin"
    if bin_root.is_dir():
        for candidate in bin_root.iterdir():
            if candidate.is_symlink() or not candidate.is_file():
                continue
            if candidate.stat().st_size > 2 * 1024 * 1024:
                continue
            try:
                content = candidate.read_bytes()
            except OSError:
                relocation_status = "INVALID"
                break
            if b"/toolchains/.tmp-v6-" in content:
                relocation_status = "INVALID"
                break
            first = content.split(b"\n", 1)[0]
            if first.startswith(b"#!") and b"python" in first:
                expected_shebang = f"#!{target}/bin/python".encode()
                if first != expected_shebang:
                    relocation_status = "INVALID"
                    break
    return {
        "status": (
            "PASS"
            if version == expected
            and actual_sha256 == expected_sha256
            and marker_status == "PASS"
            and relocation_status == "PASS"
            else "MISSING"
        ),
        "digest": toolchain_digest(source_root),
        "path": str(target),
        "python": str(python),
        "expected_python": expected,
        "actual_python": version,
        "expected_python_sha256": expected_sha256,
        "actual_python_sha256": actual_sha256,
        "marker": marker_status,
        "relocation": relocation_status,
    }


def _rewrite_staged_environment(staging: Path, target: Path, source_root: Path) -> None:
    old = str(staging).encode()
    new = str(target).encode()
    bin_root = staging / "bin"
    if not bin_root.is_dir():
        raise BootstrapRefusal("TOOLCHAIN_BIN_MISSING")
    for candidate in bin_root.iterdir():
        if candidate.is_symlink() or not candidate.is_file():
            continue
        if candidate.stat().st_size > 2 * 1024 * 1024:
            continue
        content = candidate.read_bytes()
        if old not in content:
            continue
        mode = candidate.stat().st_mode & 0o777
        replacement = content.replace(old, new)
        temporary = candidate.with_name(f".{candidate.name}.rewrite")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(descriptor, "wb") as output:
            output.write(replacement)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(candidate)
    if any(
        old in candidate.read_bytes()
        for candidate in bin_root.iterdir()
        if not candidate.is_symlink()
        and candidate.is_file()
        and candidate.stat().st_size <= 2 * 1024 * 1024
    ):
        raise BootstrapRefusal("TOOLCHAIN_RELOCATION_FAILED")
    marker = staging / MARKER
    with marker.open("x", encoding="utf-8") as output:
        json.dump(
            {
                "schemaVersion": "groundwater-toolchain.v1",
                "digest": toolchain_digest(source_root),
                "path": str(target),
            },
            output,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())


def sync(source_root: Path, paths: LifecyclePaths) -> dict[str, Any]:
    ensure_private_roots(paths, ("cache",))
    lock = _load_lock(source_root)
    tools: list[dict] = []
    uv = shutil.which("uv")
    if uv is None:
        raise BootstrapRefusal("UV_MISSING")
    target = environment_path(source_root, paths)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = owned_path(".bootstrap.lock", paths.toolchains)
    with lock_path.open("a+b") as bootstrap_lock:
        fcntl.flock(bootstrap_lock, fcntl.LOCK_EX)
        current = status(source_root, paths)
        if current["status"] == "PASS":
            return current | {"tools": tools}
        temporary = owned_path(f".tmp-v6-{secrets.token_hex(8)}", paths.toolchains)
        obsolete = owned_path(f".old-v6-{secrets.token_hex(8)}", paths.toolchains)
        if target.is_symlink() or (target.exists() and not target.is_dir()):
            raise BootstrapRefusal("TOOLCHAIN_DESTINATION_INVALID")
        if target.exists():
            target.replace(obsolete)
        environment = dict(os.environ)
        environment.update(
            {
                "UV_PROJECT_ENVIRONMENT": str(temporary),
                "UV_CACHE_DIR": str(owned_path("uv", paths.cache)),
                "UV_LINK_MODE": "copy",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        try:
            completed = subprocess.run(
                [
                    uv,
                    "sync",
                    "--locked",
                    "--all-packages",
                    "--all-groups",
                    "--python",
                    lock["python"]["version"],
                    "--project",
                    str(source_root),
                ],
                env=environment,
                cwd=source_root,
                check=False,
            )
            if completed.returncode:
                raise BootstrapRefusal("UV_SYNC_FAILED")
            _rewrite_staged_environment(temporary, target, source_root)
            temporary.replace(target)
        except Exception:
            if not target.exists() and obsolete.exists():
                obsolete.replace(target)
            raise
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        if obsolete.exists():
            shutil.rmtree(obsolete)
    result = status(source_root, paths)
    if result["status"] != "PASS":
        raise BootstrapRefusal("TOOLCHAIN_VERSION_MISMATCH")
    return result | {"tools": tools}
