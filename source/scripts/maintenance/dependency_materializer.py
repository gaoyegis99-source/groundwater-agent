"""Materialize locked wheels, npm tarballs and system packages."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import urllib.request
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .contracts import canonical_bytes, sha256_digest
from .daemon_runtime import docker_environment, verify_daemon
from .dependency_contract import DEPENDENCY_INPUTS, dependency_digest, dependency_name
from .xdg_layout import LifecyclePaths, ensure_private_roots, owned_path


SCOPES = frozenset({"python", "npm", "system"})
SYSTEM_DOWNLOAD_WORKERS = 8
SYSTEM_DOWNLOAD_ATTEMPTS = 3
SCOPE_REUSE_INPUTS = {
    "npm": ("package-lock.json", "toolchain.lock.json"),
    "python": (
        "environments/build/requirements.lock",
        "environments/common/requirements.lock",
        "environments/native/requirements.lock",
        "environments/qgis/requirements.lock",
        "environments/testing/requirements.lock",
        "toolchain.lock.json",
    ),
    "system": ("system-deps.lock.json",),
}
PROXY_ENVIRONMENT_NAMES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
)
QGIS_ARCHIVE_KEY_FINGERPRINT = "2D7E3441A707FDB3E7059441D155B8E6A419C5BE"
QGIS_ARCHIVE_KEY_URL = (
    "https://download.qgis.org/downloads/qgis-archive-keyring.gpg"
)
QGIS_LTR_REPOSITORY = "https://qgis.org/ubuntu-ltr"


class DependencyRefusalError(RuntimeError):
    """Locked dependencies cannot be safely or completely materialized."""


DependencyRefusal = DependencyRefusalError


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def root(source_root: Path, paths: LifecyclePaths) -> Path:
    return owned_path(dependency_name(source_root), owned_path("deps", paths.cache))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _npm_volatile(relative: str) -> bool:
    return (
        relative.startswith("cache/_logs/")
        or relative == "cache/_update-notifier-last-checked"
        or relative == "cache/_cacache/_lastverified"
        or relative.startswith("cache/_cacache/tmp/")
    )


def _files(path: Path, *, scope: str | None = None) -> list[dict[str, Any]]:
    return [
        {
            "path": item.relative_to(path).as_posix(),
            "sha256": "sha256:" + _sha256(item),
            "sizeBytes": item.stat().st_size,
        }
        for item in sorted(path.rglob("*"), key=lambda value: value.as_posix())
        if item.is_file()
        and not item.is_symlink()
        and not (scope == "npm" and _npm_volatile(item.relative_to(path).as_posix()))
    ]


def _manifest_inputs_match(
    source_root: Path, manifest: dict[str, Any], relatives: tuple[str, ...]
) -> bool:
    inputs = manifest.get("inputs")
    if not isinstance(inputs, dict):
        return False
    for relative in relatives:
        path = source_root / relative
        if path.is_symlink() or not path.is_file():
            return False
        if inputs.get(relative) != sha256_digest(path.read_bytes()):
            return False
    return True


def _candidate_scope(
    source_root: Path, candidate: Path, scope: str
) -> tuple[list[dict[str, Any]], str] | None:
    digest = candidate.name.removeprefix("sha256-")
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        return None
    try:
        receipt = json.loads((candidate / f"{scope}.receipt.json").read_text())
        manifest = json.loads(
            (candidate / "materialization-manifest.json").read_text()
        )
    except (OSError, json.JSONDecodeError):
        return None
    if (
        receipt.get("status") != "PASS"
        or receipt.get("scope") != scope
        or receipt.get("dependencyDigest") != digest
        or manifest.get("status") != "PASS"
        or manifest.get("dependencyDigest") != digest
    ):
        return None
    directory = candidate / scope
    if directory.is_symlink() or not directory.is_dir():
        return None
    relatives = SCOPE_REUSE_INPUTS.get(scope)
    if relatives is None or not _manifest_inputs_match(
        source_root, manifest, relatives
    ):
        return None
    actual = _files(directory, scope=scope)
    if actual != receipt.get("files"):
        return None
    return actual, digest


def _reuse_scope(
    source_root: Path,
    paths: LifecyclePaths,
    target: Path,
    destination: Path,
    scope: str,
) -> str | None:
    dependency_root = owned_path("deps", paths.cache)
    if dependency_root.is_symlink() or not dependency_root.is_dir():
        return None
    for candidate in sorted(dependency_root.iterdir(), key=lambda path: path.name):
        if (
            candidate == target
            or candidate.is_symlink()
            or not candidate.is_dir()
            or not candidate.name.startswith("sha256-")
        ):
            continue
        reusable = _candidate_scope(source_root, candidate, scope)
        if reusable is None:
            continue
        files, digest = reusable
        if scope == "npm":
            try:
                verify_npm_offline(source_root, candidate / scope / "cache")
            except (DependencyRefusal, OSError):
                continue
        shutil.copytree(candidate / scope, destination, dirs_exist_ok=True)
        if _files(destination, scope=scope) != files:
            raise DependencyRefusal("DEPENDENCY_REUSE_COPY_MISMATCH")
        return digest
    return None


def _run(command: list[str], *, cwd: Path | None = None) -> None:
    completed = subprocess.run(command, cwd=cwd, check=False)
    if completed.returncode:
        raise DependencyRefusal("DEPENDENCY_COMMAND_FAILED:" + command[0])


def _pip(source_root: Path, destination: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        raise DependencyRefusal("UV_MISSING")
    environment = destination / ".download-environment"
    _run([uv, "venv", "--python", "3.12", "--seed", str(environment)])
    try:
        for name in ("build", "common", "native", "qgis", "testing"):
            wheelhouse = destination / name
            wheelhouse.mkdir(parents=True)
            lock = source_root / "environments" / name / "requirements.lock"
            _run(
                [
                    str(environment / "bin/python"),
                    "-m",
                    "pip",
                    "download",
                    "--require-hashes",
                    "--only-binary=:all:",
                    "--dest",
                    str(wheelhouse),
                    "--requirement",
                    str(lock),
                ]
            )
    finally:
        shutil.rmtree(environment, ignore_errors=True)


def _npm_workspace(source_root: Path, workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    for relative in (
        "package.json",
        "package-lock.json",
        ".npmrc",
        "packages/ui-boundary/package.json",
        "web/package.json",
        "services/data/web/package.json",
        "services/knowledge-next/ui/package.json",
    ):
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_root / relative, target)


def verify_npm_offline(source_root: Path, cache: Path) -> dict[str, Any]:
    npm = shutil.which("npm")
    if npm is None:
        raise DependencyRefusal("NPM_MISSING")
    if cache.is_symlink() or not cache.is_dir():
        raise DependencyRefusal("NPM_CACHE_MISSING")
    with tempfile.TemporaryDirectory(prefix="groundwater-v6-npm-verify-") as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        scratch_cache = root / "cache"
        shutil.copytree(cache, scratch_cache)
        _npm_workspace(source_root, workspace)
        _run(
            [
                npm,
                "ci",
                "--offline",
                "--ignore-scripts",
                "--cache",
                str(scratch_cache),
            ],
            cwd=workspace,
        )
    return {"status": "PASS", "scope": "npm", "mode": "offline"}


def _npm(source_root: Path, destination: Path) -> None:
    npm = shutil.which("npm")
    if npm is None:
        raise DependencyRefusal("NPM_MISSING")
    workspace = destination / ".workspace"
    _npm_workspace(source_root, workspace)
    cache = destination / "cache"
    try:
        _run(
            [
                npm,
                "ci",
                "--ignore-scripts",
                "--cache",
                str(cache),
                "--prefer-online",
            ],
            cwd=workspace,
        )
        _run([npm, "cache", "verify", "--cache", str(cache)], cwd=workspace)
        shutil.rmtree(workspace / "node_modules", ignore_errors=True)
        _run(
            [
                npm,
                "ci",
                "--offline",
                "--ignore-scripts",
                "--cache",
                str(cache),
            ],
            cwd=workspace,
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _system_lock(source_root: Path) -> dict[str, Any]:
    try:
        value = json.loads((source_root / "system-deps.lock.json").read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise DependencyRefusal("SYSTEM_DEPS_LOCK_INVALID") from error
    if value.get("schemaVersion") != "system-deps-lock.v1":
        raise DependencyRefusal("SYSTEM_DEPS_LOCK_INVALID")
    return value


def _apt_uris(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line.startswith("'") or "'" not in line[1:]:
            continue
        url, _, remainder = line[1:].partition("'")
        fields = remainder.split()
        if not url.startswith(("http://", "https://")) or not fields:
            continue
        # APT's second column is the local archive name. It need not match a
        # decoded URL basename (notably for versions with an epoch, ``%3a``).
        filename = fields[0]
        if filename.endswith(".deb") and Path(filename).name == filename:
            result[filename] = url
    return result


def _resolved_packages(directory: Path) -> list[dict[str, Any]]:
    uri_file = directory / "uris.txt"
    metadata_file = directory / "metadata.tsv"
    if not uri_file.is_file() or not metadata_file.is_file():
        raise DependencyRefusal("SYSTEM_RESOLUTION_URI_LIST_MISSING")
    urls = _apt_uris(uri_file)
    metadata: dict[str, tuple[str, str]] = {}
    for line in metadata_file.read_text(errors="replace").splitlines():
        fields = line.split("\t")
        if len(fields) != 3:
            raise DependencyRefusal("SYSTEM_RESOLUTION_PACKAGE_INVALID")
        metadata[fields[0]] = (fields[1], fields[2])
    packages = []
    for path in sorted(directory.glob("*.deb"), key=lambda value: value.name):
        fields = metadata.get(path.name)
        if fields is None or path.name not in urls:
            raise DependencyRefusal("SYSTEM_RESOLUTION_PACKAGE_INVALID")
        packages.append(
            {
                "name": fields[0],
                "version": fields[1],
                "url": urls[path.name],
                "sha256": "sha256:" + _sha256(path),
                "sizeBytes": path.stat().st_size,
            }
        )
    return packages


def _ubuntu_repository_setup() -> str:
    return (
        "rm -f /etc/apt/sources.list /etc/apt/sources.list.d/*.list "
        "/etc/apt/sources.list.d/*.sources; "
        "printf '%s\\n' 'Types: deb' "
        "'URIs: http://archive.ubuntu.com/ubuntu' "
        "'Suites: noble noble-updates' 'Architectures: amd64' "
        "'Components: main universe' "
        "'Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg' "
        "'' 'Types: deb' 'URIs: http://security.ubuntu.com/ubuntu' "
        "'Suites: noble-security' 'Architectures: amd64' "
        "'Components: main universe' "
        "'Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg' "
        "> /etc/apt/sources.list.d/ubuntu.sources; "
    )


def _repository_setup(profile: str, repositories: Any) -> str:
    if profile == "native":
        if repositories != ["base-image"]:
            raise DependencyRefusal("SYSTEM_NATIVE_REPOSITORY_CONTRACT_INVALID")
        return _ubuntu_repository_setup()
    if profile != "qgis":
        return ""
    if repositories != ["ubuntu-noble", "qgis-ltr-noble"]:
        raise DependencyRefusal("SYSTEM_QGIS_REPOSITORY_CONTRACT_INVALID")
    return (
        _ubuntu_repository_setup()
        +
        "apt-get update >/dev/null; "
        "mkdir -p /tmp/apt-downloads; "
        "apt-get --yes --download-only --print-uris "
        "--no-install-recommends install "
        "ca-certificates gnupg wget >> /out/uris.txt; "
        + _parallel_apt_download(
            "ca-certificates gnupg wget", "bootstrap-selections"
        )
        + "set -- /tmp/apt-downloads/*.deb; "
        "if [ -e \"$1\" ]; then "
        "dpkg --unpack \"$@\" >/dev/null; "
        "DEBIAN_FRONTEND=noninteractive dpkg --configure -a >/dev/null; fi; "
        "install -d -m 0755 /etc/apt/keyrings; "
        f"wget --https-only --quiet --output-document="
        f"/etc/apt/keyrings/qgis-archive-keyring.gpg {QGIS_ARCHIVE_KEY_URL}; "
        "gpg --batch --show-keys --with-colons "
        "/etc/apt/keyrings/qgis-archive-keyring.gpg | "
        f"grep -Fqx 'fpr:::::::::{QGIS_ARCHIVE_KEY_FINGERPRINT}:'; "
        "printf '%s\\n' 'Types: deb' "
        f"'URIs: {QGIS_LTR_REPOSITORY}' "
        "'Suites: noble' 'Architectures: amd64' 'Components: main' "
        "'Signed-By: /etc/apt/keyrings/qgis-archive-keyring.gpg' "
        "> /etc/apt/sources.list.d/qgis.sources; "
    )


def _parallel_apt_download(packages: str, selection_name: str) -> str:
    if not packages or not selection_name.replace("-", "").isalnum():
        raise DependencyRefusal("SYSTEM_RESOLUTION_PACKAGE_INVALID")
    return (
        "apt-get --simulate --no-install-recommends install "
        + packages
        + " | sed -n 's/^Inst \\([^ ]*\\) "
        + "\\(\\[[^]]*\\] \\)\\{0,1\\}(\\([^ ]*\\).*/\\1=\\3/p' "
        + f"> /tmp/{selection_name}; "
        + "(cd /tmp/apt-downloads && "
        + f"xargs -r -n 1 -P {SYSTEM_DOWNLOAD_WORKERS} "
        + "/bin/sh -c 'selection=$1; attempt=1; "
        + f"while [ \"$attempt\" -le {SYSTEM_DOWNLOAD_ATTEMPTS} ]; do "
        + "if apt-get -o Acquire::Retries=2 "
        + "-o Acquire::http::Timeout=120 "
        + "-o Acquire::https::Timeout=120 "
        + "download -- \"$selection\" >/dev/null; then exit 0; fi; "
        + "attempt=$((attempt + 1)); done; "
        + "printf \"SYSTEM_PACKAGE_RESOLUTION_DOWNLOAD_FAILED:%s\\n\" "
        + "\"$selection\" >&2; exit 1' resolver "
        + f"< /tmp/{selection_name}); "
    )


def _docker_proxy_arguments(
    inherited: Mapping[str, str] | None = None,
) -> list[str]:
    environment = os.environ if inherited is None else inherited
    arguments: list[str] = []
    for name in PROXY_ENVIRONMENT_NAMES:
        if environment.get(name):
            arguments.extend(("--env", name))
    return arguments


def resolve_system(
    source_root: Path,
    paths: LifecyclePaths,
    *,
    docker: Path,
) -> dict[str, Any]:
    """Resolve apt closure in digest-pinned bases using only the owned daemon."""

    ensure_private_roots(paths, ("cache",))
    verify_daemon(paths, docker)
    lock = _system_lock(source_root)
    profiles = lock.get("aptProfiles")
    images = lock.get("baseImages")
    if not isinstance(profiles, dict) or not isinstance(images, dict):
        raise DependencyRefusal("SYSTEM_DEPS_LOCK_INVALID")
    resolution_root = owned_path("system-resolution", paths.cache)
    resolution_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(
        prefix="resolve-", dir=resolution_root
    ) as temporary_name:
        temporary = Path(temporary_name)
        resolved: dict[str, list[dict[str, Any]]] = {}
        for profile, specification in sorted(profiles.items()):
            if not isinstance(specification, dict):
                raise DependencyRefusal("SYSTEM_DEPS_LOCK_INVALID")
            base_name = specification.get("baseImage")
            requested = specification.get("requested")
            image = images.get(base_name)
            if (
                not isinstance(image, str)
                or "@sha256:" not in image
                or not isinstance(requested, list)
                or not requested
                or not all(isinstance(name, str) and name for name in requested)
            ):
                raise DependencyRefusal("SYSTEM_DEPS_LOCK_INVALID")
            output = temporary / profile
            output.mkdir(mode=0o700)
            packages = " ".join(requested)
            repository_setup = _repository_setup(
                profile, specification.get("repositories")
            )
            script = (
                "set -eu; "
                + ": > /out/uris.txt; "
                + repository_setup
                + "apt-get update >/dev/null; "
                + "apt-get --yes --download-only --print-uris "
                + "--no-install-recommends install "
                + packages
                + " >> /out/uris.txt; "
                + "mkdir -p /tmp/apt-downloads; "
                + _parallel_apt_download(packages, "requested-selections")
                + 'for f in /tmp/apt-downloads/*.deb; do [ -e "$f" ] || continue; '
                + 'cp "$f" /out/; done; '
                + ": > /out/metadata.tsv; "
                + 'for f in /out/*.deb; do [ -e "$f" ] || continue; '
                + 'printf \'%s\\t%s\\t%s\\n\' "$(basename "$f")" '
                + '"$(dpkg-deb -f "$f" Package)" '
                + '"$(dpkg-deb -f "$f" Version)" >> /out/metadata.tsv; done'
            )
            completed = subprocess.run(
                [
                    str(docker),
                    "run",
                    "--rm",
                    "--network",
                    "bridge",
                    *_docker_proxy_arguments(),
                    "--mount",
                    f"type=bind,src={output},dst=/out",
                    image,
                    "/bin/sh",
                    "-c",
                    script,
                ],
                env=docker_environment(paths),
                capture_output=True,
                text=True,
                check=False,
                timeout=3600,
            )
            if completed.returncode:
                raise DependencyRefusal(f"SYSTEM_RESOLUTION_FAILED:{profile}")
            resolved[profile] = _resolved_packages(output)
        updated = json.loads(json.dumps(lock))
        for profile, packages in resolved.items():
            updated["aptProfiles"][profile]["packages"] = packages
        updated["resolution"] = {
            "status": "PASS",
            "resolver": "owned-rootless-docker/apt.v1",
            "requiredPackageFields": [
                "name",
                "version",
                "url",
                "sha256",
                "sizeBytes",
            ],
            "packageCount": sum(len(value) for value in resolved.values()),
        }
        lock_path = source_root / "system-deps.lock.json"
        temporary_lock = lock_path.with_name(".system-deps.lock.json.tmp")
        temporary_lock.write_bytes(canonical_bytes(updated))
        temporary_lock.replace(lock_path)
    shutil.rmtree(resolution_root, ignore_errors=True)
    return {
        "status": "PASS",
        "lock": str(source_root / "system-deps.lock.json"),
        "profiles": {name: len(value) for name, value in sorted(resolved.items())},
        "dockerRoot": str(paths.docker),
    }


def _download(url: str, target: Path, expected: str, size: int) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "groundwater-v6-deps"})
    last_error: Exception | None = None
    for _attempt in range(SYSTEM_DOWNLOAD_ATTEMPTS):
        target.unlink(missing_ok=True)
        try:
            with (
                urllib.request.urlopen(request, timeout=120) as response,
                target.open("xb") as stream,
            ):
                shutil.copyfileobj(response, stream)
            if target.stat().st_size == size and _sha256(
                target
            ) == expected.removeprefix("sha256:"):
                return
            last_error = DependencyRefusal("SYSTEM_PACKAGE_DIGEST_MISMATCH")
        except Exception as error:
            last_error = error
    target.unlink(missing_ok=True)
    raise DependencyRefusal(
        f"SYSTEM_PACKAGE_DOWNLOAD_FAILED:{target.name}"
    ) from last_error


def _system(source_root: Path, destination: Path) -> None:
    lock = _system_lock(source_root)
    if lock.get("resolution", {}).get("status") != "PASS":
        raise DependencyRefusal("SYSTEM_DEPS_NOT_RESOLVED")
    profiles = lock.get("aptProfiles")
    if not isinstance(profiles, dict):
        raise DependencyRefusal("SYSTEM_DEPS_LOCK_INVALID")
    downloads: list[tuple[str, Path, str, int]] = []
    for profile, value in sorted(profiles.items()):
        target = destination / profile
        target.mkdir(parents=True)
        packages = value.get("packages") if isinstance(value, dict) else None
        if not isinstance(packages, list):
            raise DependencyRefusal("SYSTEM_DEPS_LOCK_INVALID")
        filenames: set[str] = set()
        for package in packages:
            if set(package) != {"name", "version", "url", "sha256", "sizeBytes"}:
                raise DependencyRefusal("SYSTEM_DEPS_LOCK_INVALID")
            filename = Path(package["url"]).name
            if (
                not filename.endswith(".deb")
                or "/" in filename
                or filename in filenames
            ):
                raise DependencyRefusal("SYSTEM_DEPS_LOCK_INVALID")
            filenames.add(filename)
            downloads.append(
                (
                    package["url"],
                    target / filename,
                    package["sha256"],
                    package["sizeBytes"],
                )
            )
    if not downloads:
        raise DependencyRefusal("SYSTEM_DEPS_LOCK_INVALID")
    workers = min(SYSTEM_DOWNLOAD_WORKERS, len(downloads))
    with ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix="groundwater-system-deps"
    ) as executor:
        futures = [executor.submit(_download, *download) for download in downloads]
        try:
            for future in as_completed(futures):
                future.result()
        except BaseException:
            for future in futures:
                future.cancel()
            raise


def status(source_root: Path, paths: LifecyclePaths) -> dict[str, Any]:
    target = root(source_root, paths)
    receipts = {}
    for scope in sorted(SCOPES):
        receipt = target / f"{scope}.receipt.json"
        if receipt.is_file() and not receipt.is_symlink():
            try:
                value = json.loads(receipt.read_text())
            except json.JSONDecodeError:
                value = {"status": "INVALID"}
            receipts[scope] = value.get("status", "INVALID")
        else:
            receipts[scope] = "MISSING"
    complete = all(value == "PASS" for value in receipts.values())
    return {
        "status": "PASS" if complete else "NOT_READY",
        "digest": dependency_digest(source_root),
        "path": str(target),
        "scopes": receipts,
        "systemLock": _system_lock(source_root)["resolution"]["status"],
    }


def materialize(
    source_root: Path, paths: LifecyclePaths, requested: set[str]
) -> dict[str, Any]:
    if not requested or not requested <= SCOPES:
        raise DependencyRefusal("DEPENDENCY_SCOPE_INVALID")
    ensure_private_roots(paths, ("cache",))
    target = root(source_root, paths)
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = target / ".materialize.lock"
    descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    try:
        implementations = {
            "python": _pip,
            "npm": _npm,
            "system": _system,
        }
        for scope in sorted(requested):
            receipt = target / f"{scope}.receipt.json"
            if receipt.is_file():
                continue
            destination = target / f".{scope}.tmp-{secrets.token_hex(8)}"
            destination.mkdir(mode=0o700)
            try:
                reused_from = _reuse_scope(
                    source_root, paths, target, destination, scope
                )
                if reused_from is None:
                    implementations[scope](source_root, destination)
                final = target / scope
                if final.exists() or final.is_symlink():
                    raise DependencyRefusal("DEPENDENCY_DESTINATION_COLLISION")
                destination.replace(final)
                payload = {
                    "schemaVersion": "dependency-materialization-receipt.v1",
                    "status": "PASS",
                    "scope": scope,
                    "dependencyDigest": dependency_digest(source_root),
                    "completedAt": _now(),
                    "files": _files(final, scope=scope),
                }
                if reused_from is not None:
                    payload["reusedFromDependencyDigest"] = reused_from
                receipt.write_bytes(canonical_bytes(payload))
            finally:
                if destination.exists():
                    shutil.rmtree(destination)
        result = status(source_root, paths)
        if result["status"] == "PASS":
            manifest = {
                "schemaVersion": "dependency-materialization.v1",
                "status": "PASS",
                "dependencyDigest": dependency_digest(source_root),
                "inputs": {
                    relative: sha256_digest((source_root / relative).read_bytes())
                    for relative in DEPENDENCY_INPUTS
                },
            }
            (target / "materialization-manifest.json").write_bytes(
                canonical_bytes(manifest)
            )
        return result
    finally:
        lock_path.unlink(missing_ok=True)


def verify_scope(
    source_root: Path, paths: LifecyclePaths, scope: str
) -> dict[str, Any]:
    if scope not in SCOPES:
        raise DependencyRefusal("DEPENDENCY_SCOPE_INVALID")
    current = status(source_root, paths)
    if current["scopes"][scope] != "PASS":
        raise DependencyRefusal("DEPENDENCY_SCOPE_NOT_MATERIALIZED")
    target = root(source_root, paths)
    receipt = json.loads((target / f"{scope}.receipt.json").read_text())
    actual = _files(target / scope, scope=scope)
    recorded = receipt.get("files")
    if scope == "npm" and isinstance(recorded, list):
        recorded = [
            item
            for item in recorded
            if isinstance(item, dict)
            and isinstance(item.get("path"), str)
            and not _npm_volatile(item["path"])
        ]
    if actual != recorded:
        raise DependencyRefusal("DEPENDENCY_MATERIAL_CHANGED")
    if scope == "npm":
        verify_npm_offline(source_root, target / "npm/cache")
    return {
        "status": "PASS",
        "scope": scope,
        "dependencyDigest": current["digest"],
        "files": len(actual),
    }


def locate_compatible_scope(
    source_root: Path, paths: LifecyclePaths, scope: str
) -> dict[str, Any]:
    """Locate a verified scope without copying it into the current digest root.

    Quick, isolated developer runs may consume a prior materialization only when
    every lock input owned by that scope still matches. This lookup is read-only;
    normal builds continue to require the current digest via ``verify_scope``.
    """

    if scope not in SCOPES:
        raise DependencyRefusal("DEPENDENCY_SCOPE_INVALID")
    target = root(source_root, paths)
    try:
        verified = verify_scope(source_root, paths, scope)
    except DependencyRefusal:
        pass
    else:
        return {
            **verified,
            "path": str(target / scope),
            "reused": False,
        }
    dependency_root = owned_path("deps", paths.cache)
    if dependency_root.is_symlink() or not dependency_root.is_dir():
        raise DependencyRefusal("DEPENDENCY_SCOPE_NOT_MATERIALIZED")
    for candidate in sorted(dependency_root.iterdir(), key=lambda path: path.name):
        if (
            candidate == target
            or candidate.is_symlink()
            or not candidate.is_dir()
            or not candidate.name.startswith("sha256-")
        ):
            continue
        reusable = _candidate_scope(source_root, candidate, scope)
        if reusable is None:
            continue
        files, digest = reusable
        if scope == "npm":
            verify_npm_offline(source_root, candidate / scope / "cache")
        return {
            "status": "PASS",
            "scope": scope,
            "dependencyDigest": digest,
            "path": str(candidate / scope),
            "files": len(files),
            "reused": True,
        }
    raise DependencyRefusal("DEPENDENCY_SCOPE_NOT_MATERIALIZED")
