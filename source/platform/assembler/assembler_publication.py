from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil


POINTER_SCHEMA = "assembly-current-pointer.v1"


def generated_root(root: Path) -> Path:
    """Return an external cache root; assembly never defaults into source."""
    configured = os.environ.get("PLATFORM_GENERATED_ROOT")
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("GENERATED_ROOT_NOT_ABSOLUTE")
        return path.absolute()
    cache_home = Path(
        os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
    ).expanduser()
    identity = hashlib.sha256(str(root.absolute()).encode()).hexdigest()[:16]
    return (
        cache_home
        / "platform-assembler"
        / "build"
        / "assemblies"
        / "worktrees"
        / identity
    ).absolute()


def bundle_pointer_path(generated: Path, product_id: str) -> Path:
    """Return the regular JSON pointer path for one assembled product."""
    if not product_id or Path(product_id).name != product_id:
        raise ValueError("PRODUCT_ID_INVALID")
    return generated / f"{product_id}.current.json"


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _bundle_pointer_identity(path: Path) -> tuple[str, Path] | None:
    """Return the immutable release selected by a regular JSON pointer."""
    if path.is_symlink():
        raise ValueError("OUTPUT_SYMLINK_POINTER_REJECTED")
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError("OUTPUT_PATH_INVALID")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("OUTPUT_POINTER_INVALID") from error
    suffix = ".current.json"
    if not path.name.endswith(suffix):
        raise ValueError("OUTPUT_POINTER_INVALID")
    product_id = path.name.removesuffix(suffix)
    if (
        not isinstance(payload, dict)
        or payload.get("schemaVersion") != POINTER_SCHEMA
        or payload.get("productId") != product_id
        or not isinstance(payload.get("releaseDirectory"), str)
        or not isinstance(payload.get("releaseDigest"), str)
    ):
        raise ValueError("OUTPUT_POINTER_INVALID")
    relative = Path(payload["releaseDirectory"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("OUTPUT_POINTER_INVALID")
    releases = (path.parent / ".releases" / product_id).resolve()
    release = (path.parent / relative).resolve()
    try:
        release.relative_to(releases)
    except ValueError as error:
        raise ValueError("OUTPUT_POINTER_ESCAPE") from error
    if release.name != payload["releaseDigest"] or not release.is_dir():
        raise ValueError("OUTPUT_POINTER_INVALID")
    return ("pointer", release)


def current_bundle(generated: Path, product_id: str) -> Path:
    """Resolve the current bundle without following a link or accepting a directory."""
    pointer = bundle_pointer_path(generated, product_id)
    identity = _bundle_pointer_identity(pointer)
    if identity is None:
        raise ValueError("OUTPUT_POINTER_MISSING")
    return identity[1]


def _write_pointer(generated: Path, product_id: str, release_dir: Path) -> None:
    pointer = bundle_pointer_path(generated, product_id)
    relative = release_dir.relative_to(generated).as_posix()
    payload = {
        "schemaVersion": POINTER_SCHEMA,
        "productId": product_id,
        "releaseDirectory": relative,
        "releaseDigest": release_dir.name,
    }
    temporary = generated / f".{product_id}-{secrets.token_hex(12)}.current.json"
    try:
        with temporary.open("x", encoding="utf-8") as output:
            output.write(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
            )
            os.fchmod(output.fileno(), 0o444)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, pointer)
        _sync_directory(generated)
    finally:
        if temporary.exists():
            temporary.unlink()


def _publish_bundle(
    root: Path,
    product_id: str,
    digest: str,
    outputs: dict[str, str],
    baseline: tuple | None,
) -> Path:
    generated = generated_root(root)
    releases = generated / ".releases"
    release_parent = releases / product_id
    release_dir = release_parent / digest
    pointer = bundle_pointer_path(generated, product_id)
    # XDG build roots are intentionally outside the repository and may not
    # have been created before the first compile after a factory reset.
    generated.mkdir(parents=True, exist_ok=True)
    _sync_directory(root)
    releases.mkdir(exist_ok=True)
    _sync_directory(generated)
    release_parent.mkdir(exist_ok=True)
    _sync_directory(releases)
    with (generated / f".{product_id}.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = _bundle_pointer_identity(pointer)
        release_identity = ("pointer", release_dir.resolve())
        if current != baseline and current != release_identity:
            raise ValueError("OUTPUT_CONCURRENT_REPLACEMENT")
        if release_dir.exists():
            actual = {
                item.name: item.read_text(encoding="utf-8")
                for item in release_dir.iterdir()
            }
            if actual != outputs:
                raise ValueError("OUTPUT_RELEASE_COLLISION")
            for item in release_dir.iterdir():
                # The byte-identical release may have been compiled under a
                # restrictive umask before mode normalization was introduced.
                item.chmod(0o444)
        else:
            if current != baseline:
                raise ValueError("OUTPUT_RELEASE_MISSING")
            staging = release_parent / f".tmp-{secrets.token_hex(12)}"
            staging.mkdir()
            try:
                for name, content in outputs.items():
                    with (staging / name).open("x", encoding="utf-8") as stream:
                        stream.write(content)
                        # Compose mounts these immutable files into service UIDs
                        # distinct from the compiler, even with umask 077.
                        os.fchmod(stream.fileno(), 0o444)
                        stream.flush()
                        os.fsync(stream.fileno())
                _sync_directory(staging)
                staging.replace(release_dir)
                _sync_directory(release_parent)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
        if current != baseline or current == release_identity:
            return release_dir
        _write_pointer(generated, product_id, release_dir)
        return release_dir
