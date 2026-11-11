from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages/workflow-compiler/src"))
sys.path.insert(0, str(ROOT / "platform" / "assembler"))
sys.path.insert(0, str(ROOT / "packages/operator-pack/src"))

from assembler import available_products, compile_product  # noqa: E402
from assembler_publication import (  # noqa: E402
    bundle_pointer_path,
    current_bundle,
    generated_root,
)
from moperators.smoke import SmokeRunRefusal, run_operator_smoke  # noqa: E402
from scripts.development.capability_inventory import (  # noqa: E402
    CapabilityInventoryRefusal,
    generate_capability_inventory,
)
from scripts.development.real_case import (  # noqa: E402
    RealCaseRefusal,
    run_real_cases,
)
from scripts.development.workflow_golden import (  # noqa: E402
    WorkflowGoldenRefusal,
    run_workflow_golden,
)

from scripts.maintenance.project_environment import (  # noqa: E402
    EnvironmentRefusal,
    activate,
    docker_arguments,
    lifecycle,
    owned_path,
)
from scripts.maintenance import dependency_materializer  # noqa: E402
from scripts.maintenance.xdg_layout import ensure_private_roots  # noqa: E402

_CACHE_DIRECTORIES = frozenset(
    {
        ".pytest_cache",
        ".ruff_cache",
        ".retired-build",
        "__pycache__",
        "build",
        "dist",
        "playwright-report",
        "test-results",
    }
)
_PROTECTED_DIRECTORIES = frozenset(
    {
        ".git",
        ".generated",
        ".venv",
        "node_modules",
        "release",
        "verification",
    }
)

_RESEARCH_DEPENDENCY_INPUTS = (
    "package-lock.json",
    "system-deps.lock.json",
    "environments/build/requirements.lock",
    "environments/common/requirements.lock",
    "environments/native/requirements.lock",
    "environments/qgis/requirements.lock",
    "environments/testing/requirements.lock",
)
_RESEARCH_DEPENDENCY_SCOPES = ("npm", "python", "system")


def _git_paths(root: Path, *arguments: str) -> list[str]:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _worktree_identity(root: Path) -> tuple[str, bool, str]:
    """Return the revision, dirty state, and digest of mounted source inputs.

    ``git diff`` covers tracked index and worktree changes. Untracked, non-ignored
    files are hashed separately because local builds and source mounts can consume
    them even though they do not appear in a diff against ``HEAD``.
    """
    revision = _git_paths(root, "rev-parse", "HEAD")[0]
    diff = subprocess.run(
        [
            "git",
            "diff",
            "--binary",
            "--no-ext-diff",
            "--no-textconv",
            "HEAD",
        ],
        cwd=root,
        capture_output=True,
        check=True,
    ).stdout
    untracked_output = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=root,
        capture_output=True,
        check=True,
    ).stdout
    untracked = sorted(path for path in untracked_output.split(b"\0") if path)

    digest = hashlib.sha256()

    def add_record(name: bytes, payload: bytes) -> None:
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)

    add_record(b"identity-version", b"worktree.v1")
    add_record(b"revision", revision.encode("utf-8"))
    add_record(b"tracked-diff", diff)
    for relative_bytes in untracked:
        relative = Path(os.fsdecode(relative_bytes))
        path = root / relative
        metadata = path.lstat()
        mode = stat.S_IMODE(metadata.st_mode).to_bytes(4, "big")
        if stat.S_ISREG(metadata.st_mode):
            kind = b"file"
            content = path.read_bytes()
        elif stat.S_ISLNK(metadata.st_mode):
            kind = b"symlink"
            content = os.fsencode(os.readlink(path))
        else:
            kind = b"special"
            content = b""
        add_record(b"untracked-path", relative_bytes)
        add_record(b"untracked-kind", kind)
        add_record(b"untracked-mode", mode)
        add_record(b"untracked-content", content)
    return revision, bool(diff or untracked), digest.hexdigest()


def _is_ignored(root: Path, path: Path) -> bool:
    relative = path.relative_to(root).as_posix()
    result = subprocess.run(
        ["git", "check-ignore", "-v", "--no-index", "--", relative],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout:
        return False
    metadata, separator, _matched_path = result.stdout.rstrip("\n").partition("\t")
    fields = metadata.split(":", 2)
    if not separator or len(fields) != 3:
        return False
    pattern = fields[2]
    return not pattern.startswith("!") or pattern.startswith(r"\!")


def _assert_removable(root: Path, path: Path) -> None:
    generated = generated_root(root)
    try:
        relative_generated = path.absolute().relative_to(generated.absolute())
    except ValueError:
        relative_generated = None
    if relative_generated is not None:
        if not relative_generated.parts:
            raise ValueError(f"CLEAN_GENERATED_ROOT_PROTECTED:{path}")
        return
    try:
        relative = path.absolute().relative_to(root.absolute())
    except ValueError as error:
        raise ValueError(f"CLEAN_PATH_OUTSIDE_REPOSITORY:{path}") from error
    if relative.parts and relative.parts[0] in {
        ".env",
        ".git",
        ".venv",
        "node_modules",
        "release",
        "verification",
    }:
        raise ValueError(f"CLEAN_PATH_PROTECTED:{relative.as_posix()}")
    tracked = _git_paths(root, "ls-files", "--", relative.as_posix())
    if tracked:
        raise ValueError(f"CLEAN_TRACKED_PATH:{tracked[0]}")
    if not _is_ignored(root, path):
        raise ValueError(f"CLEAN_PATH_NOT_IGNORED:{relative.as_posix()}")


def _cache_candidates(root: Path) -> set[Path]:
    candidates: set[Path] = set()
    legacy_generated = root / ".generated"
    if legacy_generated.exists() and not legacy_generated.is_symlink():
        candidates.add(legacy_generated)
    for current, directories, _files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        retained = []
        for name in directories:
            path = current_path / name
            relative = path.relative_to(root)
            protected = name in {".git", ".venv", "node_modules"} or relative.parts[
                0
            ] in {".generated", "release", "verification"}
            if protected:
                continue
            if name in _CACHE_DIRECTORIES or name.endswith(".egg-info"):
                if _is_ignored(root, path):
                    candidates.add(path)
                else:
                    retained.append(name)
            else:
                retained.append(name)
        directories[:] = retained
    return candidates


def _assembly_product_ids(generated: Path) -> set[str]:
    releases = generated / ".releases"
    product_ids = (
        {path.name for path in releases.iterdir() if path.is_dir()}
        if releases.is_dir()
        else set()
    )
    product_ids.update(
        path.name[1:-5]
        for path in generated.glob(".*.lock")
        if len(path.name) > len("..lock")
    )
    product_ids.update(
        path.name.removesuffix(".current.json")
        for path in generated.glob("*.current.json")
        if path.name != ".current.json"
    )
    return product_ids


def _stale_assemblies(root: Path) -> set[Path]:
    generated = generated_root(root)
    stale: set[Path] = set()
    for product_id in _assembly_product_ids(generated):
        pointer = bundle_pointer_path(generated, product_id)
        active = current_bundle(generated, product_id) if pointer.exists() else None
        releases = generated / ".releases" / product_id
        if releases.is_dir():
            stale.update(
                path
                for path in releases.iterdir()
                if path.is_dir() and (active is None or path.resolve() != active)
            )
    return stale


def _generated_outputs(root: Path) -> set[Path]:
    """Return rebuildable generated outputs outside the current assembly store."""
    generated = generated_root(root)
    if not generated.is_dir():
        return set()
    product_ids = _assembly_product_ids(generated)
    retained = {
        ".releases",
        *(f".{product_id}.lock" for product_id in product_ids),
        *(f"{product_id}.current.json" for product_id in product_ids),
    }
    return {path for path in generated.iterdir() if path.name not in retained}


def _display_path(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        generated = generated_root(root)
        return "@build/" + path.relative_to(generated).as_posix()


def _select_product(root: Path, requested: str | None) -> str:
    products = available_products(root)
    if requested is not None:
        if requested not in products:
            raise ValueError(f"PRODUCT_NOT_FOUND:{requested}")
        return requested
    if len(products) != 1:
        choices = ",".join(products) or "none"
        raise ValueError(f"PRODUCT_SELECTION_REQUIRED:{choices}")
    return products[0]


def _compose_project(root: Path, product_id: str) -> str:
    repository = root.name.lower().replace("_", "-")
    return f"{repository}-{product_id}"


def _research_dependency_root(configured: Path, root: Path = ROOT) -> Path:
    """Resolve a complete cache whose actual dependency inputs still match.

    Removing enterprise-only tools changes the aggregate legacy cache digest even
    when every wheel, npm lock, system package and base image used by research
    builds is unchanged. Research builds may reuse such a cache, but only after
    checking all build-relevant input hashes and all required scopes.
    """

    def complete(candidate: Path) -> bool:
        return candidate.is_dir() and all(
            (candidate / scope).is_dir() for scope in _RESEARCH_DEPENDENCY_SCOPES
        )

    if complete(configured):
        return configured
    expected = {
        relative: "sha256:" + hashlib.sha256((root / relative).read_bytes()).hexdigest()
        for relative in _RESEARCH_DEPENDENCY_INPUTS
    }
    candidates: list[Path] = []
    for manifest in configured.parent.glob("sha256-*/materialization-manifest.json"):
        candidate = manifest.parent
        if candidate.is_symlink() or not complete(candidate):
            continue
        try:
            inputs = json.loads(manifest.read_text(encoding="utf-8")).get("inputs", {})
        except (OSError, json.JSONDecodeError):
            continue
        if all(inputs.get(name) == digest for name, digest in expected.items()):
            candidates.append(candidate)
    if not candidates:
        raise ValueError("RESEARCH_DEPENDENCIES_NOT_READY")
    return max(candidates, key=lambda candidate: candidate.stat().st_mtime_ns)


def _ensure_research_dependencies(root: Path = ROOT) -> Path:
    """Materialize only missing locked inputs needed by a local Compose build."""

    paths = lifecycle()
    current = dependency_materializer.status(root, paths)
    missing = {scope for scope, status in current["scopes"].items() if status != "PASS"}
    if missing:
        current = dependency_materializer.materialize(root, paths, missing)
    if current["status"] != "PASS":
        raise ValueError("RESEARCH_DEPENDENCIES_NOT_READY")
    return Path(str(current["path"]))


def _research_runtime(product_id: str) -> tuple[Path, Path, Path, Path, str]:
    paths = lifecycle()
    output = current_bundle(generated_root(ROOT), product_id)
    if output is None:
        output = compile_product(
            ROOT, ROOT / "products" / product_id / "product-manifest.json"
        )
    state = paths.state / "research-dev" / product_id
    data = paths.data / "research-dev" / product_id
    for relative in (
        "kernel",
        "data/database",
        "data/objects",
        "knowledge/database",
        "executor/state",
        "executor/work",
    ):
        bind_root = data / relative
        bind_root.mkdir(parents=True, exist_ok=True)
        # Rootless container UIDs map to subordinate host UIDs and cannot write
        # a 0755 directory created by the login user. The enclosing XDG data
        # tree remains 0700, so only these non-traversable bind leaves need the
        # cooperative write bit used during first-time service initialization.
        try:
            bind_root.chmod(0o777)
        except PermissionError:
            # PostgreSQL takes ownership with its mapped rootless UID after
            # initialization. At that point the directory is already writable
            # by its intended container user and must not be reclaimed.
            pass
    state.mkdir(parents=True, exist_ok=True)
    profile = paths.config / "profiles/research.env"
    secrets = paths.config / "secrets/research"
    if not profile.is_file() or not secrets.is_dir():
        raise ValueError("RESEARCH_PROFILE_NOT_READY")
    env_file = state / "compose.env"
    retained = profile.read_text().rstrip()
    dependency_root = _research_dependency_root(
        Path(os.environ["PLATFORM_DEPENDENCY_ROOT"])
    )
    env_file.write_text(
        retained + "\n"
        f"PLATFORM_INSTANCE_DATA_ROOT={data}\n"
        f"PLATFORM_SECRETS_DIR={secrets}\n"
        f"PLATFORM_DEPENDENCY_ROOT={dependency_root}\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    python_path = ":".join(
        f"/workspace/{relative}"
        for relative in (
            "kernel/src",
            "services/data/src",
            "services/knowledge-next/src",
            "services/executor/src",
            "packages/protocol-contracts/src",
            "packages/workflow-compiler/src",
            "services/executor/client/src",
        )
    )
    source_services = (
        "kernel-service",
        "data-migrate",
        "data-service",
        "data-worker",
        "knowledge-next-migrate",
        "knowledge-next-service",
        "knowledge-next-worker",
        "executor-control",
        "executor-native-worker",
        "executor-qgis-worker",
    )
    available = json.loads((output / "compose.json").read_text())["services"]
    override = state / "compose.source.json"
    override.write_text(
        json.dumps(
            {
                "services": {
                    name: {
                        "volumes": [
                            {
                                "type": "bind",
                                "source": str(ROOT),
                                "target": "/workspace",
                                "read_only": True,
                            }
                        ],
                        "environment": {"PYTHONPATH": python_path},
                    }
                    for name in source_services
                    if name in available
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    override.chmod(0o600)
    return (
        output / "compose.json",
        override,
        env_file,
        data,
        _compose_project(ROOT, product_id),
    )


def _run_compose(
    product_id: str,
    arguments: list[str],
    *,
    extra_files: tuple[Path, ...] = (),
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    compose, override, env_file, _data, project = _research_runtime(product_id)
    state_root = lifecycle().state.resolve()
    extra_arguments: list[str] = []
    for path in extra_files:
        resolved = path.resolve()
        if not resolved.is_file() or not resolved.is_relative_to(state_root):
            raise ValueError(f"RESEARCH_COMPOSE_OVERRIDE_INVALID:{path}")
        extra_arguments.extend(("--file", str(resolved)))
    command = docker_arguments(
        [
            "compose",
            "--env-file",
            str(env_file),
            "--project-name",
            project,
            "--file",
            str(compose),
            "--file",
            str(override),
            *extra_arguments,
            *arguments,
        ]
    )
    environment = dict(os.environ)
    for line in env_file.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition("=")
        if separator and name == "PLATFORM_DEPENDENCY_ROOT":
            # Shell variables have higher Compose precedence than --env-file.
            # Pin the already validated research cache explicitly so activation's
            # aggregate legacy digest cannot override it.
            environment[name] = value
            break
    completed = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )
    if completed.returncode and check:
        diagnostics = (completed.stdout + "\n" + completed.stderr).strip()
        raise ValueError("RESEARCH_COMPOSE_FAILED:" + diagnostics[-4000:])
    return completed


def _bootstrap_status_path(product_id: str) -> Path:
    return lifecycle().state / "research-dev" / product_id / "bootstrap-status.json"


def _record_bootstrap_status(
    product_id: str,
    completed: subprocess.CompletedProcess[str],
) -> dict[str, object]:
    output = current_bundle(generated_root(ROOT), product_id)
    assembly_digest = output.name if output is not None else None
    combined = completed.stdout + "\n" + completed.stderr
    payload = None
    for line in reversed(completed.stdout.splitlines()):
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and "bootstrapState" in candidate:
            payload = candidate
            break
    if completed.returncode == 0 and payload is not None:
        status: dict[str, object] = {
            "status": "PASS",
            "state": payload["bootstrapState"],
            "documentCount": payload.get("documentCount"),
            "changedDocumentCount": payload.get("changedDocumentCount", 0),
            "assemblyDigest": assembly_digest,
        }
    else:
        matches = re.findall(r"KNOWLEDGE_BOOTSTRAP_[A-Z_]+(?::[A-Z0-9_]+)?", combined)
        status = {
            "status": "FAILED",
            "errorCode": matches[-1] if matches else "KNOWLEDGE_BOOTSTRAP_FAILED",
            "assemblyDigest": assembly_digest,
        }
    path = _bootstrap_status_path(product_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)
    return status


def _load_bootstrap_status(product_id: str) -> dict[str, object]:
    output = current_bundle(generated_root(ROOT), product_id)
    expected_digest = output.name if output is not None else None
    path = _bootstrap_status_path(product_id)
    if path.is_file():
        try:
            status = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            status = None
        if isinstance(status, dict) and status.get("assemblyDigest") == expected_digest:
            return status
    return {
        "status": "NOT_RUN",
        "assemblyDigest": expected_digest,
    }


def _compose_json_rows(raw: str) -> list[dict[str, object]]:
    """Accept both Compose JSON arrays and newline-delimited JSON objects."""
    stripped = raw.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        value = json.loads(stripped)
        return value if isinstance(value, list) else [value]
    return [json.loads(line) for line in stripped.splitlines() if line.strip()]


def _unavailable_status(
    revision: str, source_dirty: bool, source_digest: str, code: str
) -> dict[str, object]:
    summaries = {
        "RESEARCH_DEPENDENCIES_NOT_READY": "本地依赖缓存尚未准备。",
        "RESEARCH_PROFILE_NOT_READY": "本地运行配置或密钥尚未准备。",
        "RESEARCH_COMPOSE_FAILED": "Compose 运行状态不可用。",
    }
    return {
        "status": "NOT_READY",
        "issues": [{"code": code, "summary": summaries[code]}],
        "sourceRevision": revision,
        "sourceDirty": source_dirty,
        "sourceDigest": f"sha256:{source_digest}",
        "services": {},
        "readiness": {},
        "runtimeCapabilities": {},
        "images": {},
        "initialization": {},
    }


def _status(product_id: str) -> dict[str, object]:
    revision, source_dirty, source_digest = _worktree_identity(ROOT)
    try:
        compose, _override, _env, _data, _project = _research_runtime(product_id)
        completed = _run_compose(product_id, ["ps", "--format", "json"])
    except ValueError as error:
        code = str(error).partition(":")[0]
        if code in {
            "RESEARCH_DEPENDENCIES_NOT_READY",
            "RESEARCH_PROFILE_NOT_READY",
            "RESEARCH_COMPOSE_FAILED",
        }:
            return _unavailable_status(revision, source_dirty, source_digest, code)
        raise
    rows = _compose_json_rows(completed.stdout)
    services = {
        str(row.get("Service")): {
            "state": row.get("State"),
            "health": row.get("Health"),
            "image": row.get("Image"),
        }
        for row in rows
    }
    required = set(json.loads(compose.read_text())["services"])
    containers_healthy = all(
        name in services
        and services[name]["state"] == "running"
        and services[name]["health"] not in {"unhealthy", "starting"}
        for name in required
        if not name.endswith("-migrate") and name != "knowledge-bootstrap"
    )
    readiness: dict[str, object] = {}
    readiness_paths = {
        "kernel-service": "/internal/health/ready",
        "data-service": "/internal/health/ready",
        "knowledge-next-service": "/internal/health/ready",
        "executor-control": "/internal/health/ready",
    }
    probe = (
        "import json,sys,urllib.request;"
        "print(urllib.request.urlopen(sys.argv[1],timeout=3).read().decode())"
    )
    for service, path in readiness_paths.items():
        if services.get(service, {}).get("state") != "running":
            readiness[service] = {"status": "UNAVAILABLE"}
            continue
        try:
            result = _run_compose(
                product_id,
                [
                    "exec",
                    "-T",
                    service,
                    "python",
                    "-c",
                    probe,
                    f"http://127.0.0.1:8000{path}",
                ],
            )
            readiness[service] = json.loads(result.stdout)
        except (ValueError, json.JSONDecodeError):
            readiness[service] = {"status": "UNAVAILABLE"}
    container_names = [str(row["Name"]) for row in rows if row.get("Name")]
    inspected: list[dict[str, object]] = []
    if container_names:
        result = subprocess.run(
            docker_arguments(["inspect", *container_names]),
            text=True,
            capture_output=True,
            check=True,
        )
        value = json.loads(result.stdout)
        if isinstance(value, list):
            inspected = value
    images = {}
    for item in inspected:
        config = item.get("Config", {})
        labels = config.get("Labels", {}) if isinstance(config, dict) else {}
        service = (
            labels.get("com.docker.compose.service")
            if isinstance(labels, dict)
            else None
        )
        if isinstance(service, str):
            images[service] = {
                "reference": config.get("Image") if isinstance(config, dict) else None,
                "imageId": item.get("Image"),
            }
    executor_readiness = readiness.get("executor-control", {})
    runtime_capabilities = (
        executor_readiness.get("components", {})
        if isinstance(executor_readiness, dict)
        else {}
    )
    all_runtimes_ready = isinstance(runtime_capabilities, dict) and all(
        runtime_capabilities.get(runtime) == "UP" for runtime in ("native", "qgis")
    )
    base_ready = containers_healthy and all(
        str(value.get("status", "")).upper() in {"UP", "READY"}
        for value in readiness.values()
        if isinstance(value, dict)
    )
    some_runtime_ready = isinstance(runtime_capabilities, dict) and any(
        runtime_capabilities.get(runtime) == "UP" for runtime in ("native", "qgis")
    )
    status = (
        "PASS"
        if base_ready and all_runtimes_ready
        else "DEGRADED"
        if base_ready and some_runtime_ready
        else "NOT_READY"
    )
    initialization = {
        "knowledgeBootstrap": _load_bootstrap_status(product_id),
    }
    issues: list[dict[str, str]] = []
    bootstrap_status = initialization["knowledgeBootstrap"]
    if bootstrap_status.get("status") != "PASS":
        if status == "PASS":
            status = "DEGRADED"
        issues.append(
            {
                "code": str(
                    bootstrap_status.get("errorCode", "KNOWLEDGE_BOOTSTRAP_NOT_RUN")
                ),
                "summary": (
                    "知识目录初始化失败；常驻服务仍可诊断。"
                    if bootstrap_status.get("status") == "FAILED"
                    else "当前装配尚未执行知识目录初始化。"
                ),
            }
        )
    return {
        "status": status,
        "issues": issues,
        "sourceRevision": revision,
        "sourceDirty": source_dirty,
        "sourceDigest": f"sha256:{source_digest}",
        "services": services,
        "readiness": readiness,
        "runtimeCapabilities": runtime_capabilities,
        "images": images,
        "initialization": initialization,
    }


def clean_workspace(root: Path = ROOT, *, dry_run: bool = False) -> list[str]:
    """Remove ignored, reproducible outputs while preserving the current assembly."""
    root = root.resolve()
    candidates = (
        _cache_candidates(root) | _stale_assemblies(root) | _generated_outputs(root)
    )
    ordered = sorted(candidates, key=lambda item: _display_path(root, item))
    for path in ordered:
        _assert_removable(root, path)
    removed = [_display_path(root, path) for path in ordered]
    if not dry_run:
        for path in ordered:
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.exists():
                shutil.rmtree(path)
    action = "would remove" if dry_run else "removed"
    for relative in removed:
        print(f"{action} {relative}")
    return removed


def _parameter_overrides(values: list[str]) -> dict[str, object]:
    result: dict[str, object] = {}
    for assignment in values:
        name, separator, encoded = assignment.partition("=")
        if (
            not separator
            or not name
            or name.strip() != name
            or name in result
            or not encoded
        ):
            raise SmokeRunRefusal("OPERATOR_PARAMETER_OVERRIDE_INVALID")
        try:
            result[name] = json.loads(encoded)
        except json.JSONDecodeError as error:
            raise SmokeRunRefusal("OPERATOR_PARAMETER_OVERRIDE_INVALID") from error
    return result


def _fixture_overrides(values: list[str]) -> dict[str, tuple[str, ...]]:
    collected: dict[str, list[str]] = {}
    for assignment in values:
        port, separator, fixture_id = assignment.partition("=")
        if (
            not separator
            or not port
            or port.strip() != port
            or not fixture_id
            or fixture_id.strip() != fixture_id
        ):
            raise SmokeRunRefusal("OPERATOR_FIXTURE_OVERRIDE_INVALID")
        collected.setdefault(port, []).append(fixture_id)
    return {port: tuple(fixture_ids) for port, fixture_ids in collected.items()}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _operator_native_lock() -> dict[str, str]:
    try:
        lock = json.loads((ROOT / "toolchain.lock.json").read_text(encoding="utf-8"))
        python = lock["python"]
        uv = lock["tools"]["uv"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise SmokeRunRefusal("OPERATOR_NATIVE_LOCK_INVALID") from error
    values = {
        "platform": lock.get("platform"),
        "pythonVersion": python.get("version"),
        "pythonSha256": python.get("installed_binary_sha256"),
        "uvVersion": uv.get("version"),
        "uvSha256": uv.get("installed_binary_sha256"),
    }
    schema = lock.get("schema")
    if (
        not isinstance(schema, str)
        or re.fullmatch(r"[a-z][a-z0-9-]*-toolchain-lock\.v1", schema) is None
        or values["platform"] != "linux-amd64"
        or sys.platform != "linux"
        or os.uname().machine not in {"x86_64", "amd64"}
        or not all(isinstance(value, str) and value for value in values.values())
        or not values["pythonVersion"].startswith("3.12.")
        or any(
            len(values[name]) != 64
            or any(character not in "0123456789abcdef" for character in values[name])
            for name in ("pythonSha256", "uvSha256")
        )
    ):
        raise SmokeRunRefusal("OPERATOR_NATIVE_LOCK_INVALID")
    return values


def _verified_uv(lock: dict[str, str]) -> str:
    configured = shutil.which("uv")
    if configured is None:
        raise SmokeRunRefusal("OPERATOR_NATIVE_UV_MISSING")
    try:
        executable = Path(configured).resolve(strict=True)
        digest = _file_sha256(executable)
        completed = subprocess.run(
            [str(executable), "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SmokeRunRefusal("OPERATOR_NATIVE_UV_INVALID") from error
    if (
        not executable.is_file()
        or digest != lock["uvSha256"]
        or completed.stdout.strip() != f"uv {lock['uvVersion']}"
    ):
        raise SmokeRunRefusal("OPERATOR_NATIVE_UV_INVALID")
    return str(executable)


def _native_environment_commands(
    uv: str,
    environment: Path,
    wheelhouse: Path,
    python_version: str,
) -> tuple[list[str], list[str], list[str]]:
    python = environment / "bin/python"
    return (
        [
            uv,
            "venv",
            "--python",
            python_version,
            "--offline",
            "--no-python-downloads",
            str(environment),
        ],
        [
            uv,
            "pip",
            "install",
            "--python",
            str(python),
            "--require-hashes",
            "--offline",
            "--no-index",
            "--find-links",
            str(wheelhouse / "native"),
            "--link-mode",
            "copy",
            "--strict",
            "-r",
            str(ROOT / "environments/native/requirements.lock"),
        ],
        [
            uv,
            "pip",
            "check",
            "--python",
            str(python),
            "--offline",
        ],
    )


@contextmanager
def _native_runtime_python() -> Iterator[tuple[Path, dict[str, object]]]:
    """Yield an ephemeral locked Native interpreter without changing defaults."""

    paths = lifecycle()
    ensure_private_roots(paths, ("cache",))
    lock = _operator_native_lock()
    uv = _verified_uv(lock)
    try:
        dependency = dependency_materializer.locate_compatible_scope(
            ROOT, paths, "python"
        )
    except dependency_materializer.DependencyRefusal as error:
        raise SmokeRunRefusal("OPERATOR_NATIVE_DEPENDENCIES_NOT_READY") from error
    wheelhouse = Path(str(dependency["path"]))
    native_wheelhouse = wheelhouse / "native"
    requirements = ROOT / "environments/native/requirements.lock"
    if (
        wheelhouse.is_symlink()
        or not native_wheelhouse.is_dir()
        or native_wheelhouse.is_symlink()
        or not requirements.is_file()
        or requirements.is_symlink()
    ):
        raise SmokeRunRefusal("OPERATOR_NATIVE_WHEELHOUSE_INVALID")
    runtime_root = owned_path("operator-smoke-runtime", paths.build)
    runtime_root.mkdir(parents=True, mode=0o700, exist_ok=True)
    runtime_root.chmod(0o700)
    with tempfile.TemporaryDirectory(
        prefix="native-", dir=runtime_root
    ) as temporary_name:
        temporary = Path(temporary_name)
        environment = temporary / "venv"
        python = environment / "bin/python"
        command_environment = {
            **os.environ,
            "PYTHONPATH": "",
            "PYTHONDONTWRITEBYTECODE": "1",
            "UV_CACHE_DIR": str(temporary / "uv-cache"),
            "UV_LINK_MODE": "copy",
            "UV_NO_CONFIG": "1",
            "UV_NO_INDEX": "1",
            "UV_OFFLINE": "1",
            "UV_PYTHON_DOWNLOADS": "never",
            "UV_PROJECT_ENVIRONMENT": str(environment),
            "VIRTUAL_ENV": str(environment),
        }
        try:
            for command in _native_environment_commands(
                uv, environment, wheelhouse, lock["pythonVersion"]
            ):
                subprocess.run(
                    command,
                    cwd=ROOT,
                    env=command_environment,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=600,
                )
            completed = subprocess.run(
                [
                    str(python),
                    "-c",
                    "import platform; print(platform.python_version())",
                ],
                cwd=ROOT,
                env=command_environment,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            python_executable = python.resolve(strict=True)
            python_sha256 = _file_sha256(python_executable)
        except (OSError, subprocess.SubprocessError) as error:
            raise SmokeRunRefusal("OPERATOR_NATIVE_ENVIRONMENT_FAILED") from error
        if (
            completed.stdout.strip() != lock["pythonVersion"]
            or python_sha256 != lock["pythonSha256"]
        ):
            raise SmokeRunRefusal("OPERATOR_NATIVE_PYTHON_INVALID")
        dependency_digest = str(dependency["dependencyDigest"])
        if not dependency_digest.startswith("sha256:"):
            dependency_digest = "sha256:" + dependency_digest
        evidence: dict[str, object] = {
            "schemaVersion": "operator-native-runtime.v1",
            "isolation": "EPHEMERAL_XDG_BUILD",
            "network": "OFFLINE",
            "pythonVersion": lock["pythonVersion"],
            "pythonSha256": "sha256:" + python_sha256,
            "uvVersion": lock["uvVersion"],
            "uvSha256": "sha256:" + lock["uvSha256"],
            "dependencyScope": "python",
            "dependencyDigest": dependency_digest,
            "dependencyReused": bool(dependency.get("reused")),
            "requirementsPath": "environments/native/requirements.lock",
            "requirementsSha256": "sha256:" + _file_sha256(requirements),
            "defaultRuntimeMutation": "NONE",
        }
        yield python, evidence


def _operator_run_directory(configured: Path | None) -> Path:
    paths = lifecycle()
    ensure_private_roots(paths, ("cache",))
    root = owned_path("operator-runs", paths.cache)
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    root.chmod(0o700)
    try:
        output = (
            owned_path(configured, root)
            if configured
            else owned_path(uuid.uuid4().hex, root)
        )
    except EnvironmentRefusal as error:
        raise SmokeRunRefusal("OPERATOR_RUN_OUTPUT_OUTSIDE_CACHE") from error
    if output == root:
        raise SmokeRunRefusal("OPERATOR_RUN_OUTPUT_INVALID")
    output.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    return output


def _run_operator_smoke(args: argparse.Namespace) -> dict[str, object]:
    fixtures = _fixture_overrides(args.fixture)
    parameters = _parameter_overrides(args.parameter)
    output = _operator_run_directory(args.output_dir)
    with _native_runtime_python() as (python, runtime_evidence):
        return run_operator_smoke(
            ROOT / "packages/operator-pack/catalog/operators",
            args.operator_id,
            args.operator_version,
            output,
            fixture_overrides=fixtures,
            parameter_overrides=parameters,
            python_executable=python,
            runtime_evidence=runtime_evidence,
        )


def _agent_replay_run_directory(configured: Path | None) -> Path:
    from scripts.development.agent_replay import AgentReplayRefusal

    paths = lifecycle()
    ensure_private_roots(paths, ("cache",))
    root = owned_path("agent-replay-runs", paths.cache)
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    root.chmod(0o700)
    try:
        output = (
            owned_path(configured, root)
            if configured
            else owned_path(uuid.uuid4().hex, root)
        )
    except EnvironmentRefusal as error:
        raise AgentReplayRefusal("AGENT_REPLAY_OUTPUT_OUTSIDE_CACHE") from error
    if output == root:
        raise AgentReplayRefusal("AGENT_REPLAY_OUTPUT_INVALID")
    output.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    return output


def run_agent_replay(**kwargs: object) -> dict[str, object]:
    """Load the Agent replay runtime only when that development command runs."""

    from scripts.development.agent_replay import run_agent_replay as execute

    return execute(**kwargs)


def _run_agent_replay(args: argparse.Namespace) -> dict[str, object]:
    output = _agent_replay_run_directory(args.output_dir)
    repetitions = 3 if args.repetitions is None else args.repetitions
    return run_agent_replay(
        repository_root=ROOT,
        case_file=ROOT / "scripts/development/agent-replay-cases.v1.json",
        run_directory=output,
        case_ids=None if args.case == "all" else (args.case,),
        repetitions=repetitions,
    )


def _workflow_golden_run_directory(configured: Path | None) -> Path:
    paths = lifecycle()
    ensure_private_roots(paths, ("cache",))
    root = owned_path("workflow-golden-runs", paths.cache)
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    root.chmod(0o700)
    try:
        output = (
            owned_path(configured, root)
            if configured
            else owned_path(uuid.uuid4().hex, root)
        )
    except EnvironmentRefusal as error:
        raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_OUTPUT_OUTSIDE_CACHE") from error
    if output == root:
        raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_OUTPUT_INVALID")
    output.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    return output


def _workflow_native_system_lock() -> tuple[str, str]:
    try:
        lock = json.loads((ROOT / "system-deps.lock.json").read_text(encoding="utf-8"))
        native = lock["aptProfiles"]["native"]
        package = next(
            item for item in native["packages"] if item["name"] == "gdal-bin"
        )
        version = package["version"]
    except (OSError, KeyError, StopIteration, TypeError, json.JSONDecodeError) as error:
        raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_SYSTEM_LOCK_INVALID") from error
    if (
        lock.get("schemaVersion") != "system-deps-lock.v1"
        or native.get("baseImage") != "pdal"
        or not isinstance(version, str)
        or re.fullmatch(r"[0-9][A-Za-z0-9.+:~_-]*", version) is None
    ):
        raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_SYSTEM_LOCK_INVALID")
    return version, version.split("+", 1)[0].split(":", 1)[-1]


def _verified_workflow_runtime_image(
    image_reference: str,
) -> tuple[str, dict[str, object]]:
    if not image_reference or image_reference.strip() != image_reference:
        raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_RUNTIME_IMAGE_INVALID")
    expected_package, expected_gdal = _workflow_native_system_lock()
    try:
        docker_info = subprocess.run(
            docker_arguments(
                ["info", "--format", "{{json .SecurityOptions}}|{{.DockerRootDir}}"]
            ),
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        security_encoded, separator, docker_root_encoded = (
            docker_info.stdout.strip().partition("|")
        )
        security = json.loads(security_encoded)
        expected_docker_root = lifecycle().docker.resolve()
        if (
            not separator
            or not isinstance(security, list)
            or "name=rootless" not in security
            or Path(docker_root_encoded).resolve() != expected_docker_root
        ):
            raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_RUNTIME_IMAGE_INVALID")
        inspected = subprocess.run(
            docker_arguments(
                [
                    "image",
                    "inspect",
                    "--format",
                    "{{.Id}} {{.Os}} {{.Architecture}}",
                    image_reference,
                ]
            ),
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        fields = inspected.stdout.strip().split()
        if (
            len(fields) != 3
            or re.fullmatch(r"sha256:[0-9a-f]{64}", fields[0]) is None
            or fields[1:] != ["linux", "amd64"]
        ):
            raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_RUNTIME_IMAGE_INVALID")
        image_id = fields[0]
        probe = subprocess.run(
            docker_arguments(
                [
                    "run",
                    "--rm",
                    "--pull",
                    "never",
                    "--network",
                    "none",
                    "--entrypoint",
                    "/bin/sh",
                    image_id,
                    "-c",
                    (
                        "set -eu; uname -m; dpkg-query -W gdal-bin; "
                        "ogr2ogr --version; gdal_grid --version"
                    ),
                ]
            ),
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except WorkflowGoldenRefusal:
        raise
    except (
        EnvironmentRefusal,
        OSError,
        subprocess.SubprocessError,
        json.JSONDecodeError,
    ) as error:
        raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_RUNTIME_IMAGE_INVALID") from error
    lines = probe.stdout.splitlines()
    expected_version_line = f"GDAL {expected_gdal},"
    if (
        len(lines) != 4
        or lines[0] != "x86_64"
        or lines[1] != f"gdal-bin\t{expected_package}"
        or not lines[2].startswith(expected_version_line)
        or not lines[3].startswith(expected_version_line)
    ):
        raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_RUNTIME_IMAGE_INVALID")
    return image_id, {
        "schemaVersion": "workflow-native-system-runtime.v1",
        "isolation": "EPHEMERAL_READ_ONLY_CONTAINER",
        "network": "OFF",
        "imageReference": image_reference,
        "imageId": image_id,
        "platform": "linux/amd64",
        "daemonMode": "ROOTLESS_PROJECT_SCOPED",
        "gdalBinVersion": expected_package,
        "gdalVersion": expected_gdal,
        "purpose": "LOCKED_SYSTEM_TOOLS_ONLY",
        "defaultRuntimeMutation": "NONE",
    }


def _workflow_container_task_command(
    image_id: str,
    python: Path,
    output_parent: Path,
) -> list[str]:
    runtime_root = python.parents[2]
    python_distribution = python.resolve(strict=True).parent.parent
    roots = (ROOT, runtime_root, python_distribution, output_parent)
    if any(
        not path.is_dir()
        or path.is_symlink()
        or any(character in str(path) for character in (",", "\n", "\r"))
        for path in roots
    ):
        raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_CONTAINER_MOUNT_INVALID")
    pythonpath = os.pathsep.join(
        (
            str(ROOT / "packages/operator-pack/src"),
            str(ROOT / "packages/workflow-compiler/src"),
            str(ROOT),
        )
    )
    try:
        return docker_arguments(
            [
                "run",
                "--rm",
                "--pull",
                "never",
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                "512",
                "--memory",
                "8589934592",
                "--memory-swap",
                "8589934592",
                "--cpus",
                "2",
                "--user",
                "0:0",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,noexec,size=536870912",
                "--workdir",
                str(ROOT),
                "--mount",
                f"type=bind,src={ROOT},dst={ROOT},readonly",
                "--mount",
                f"type=bind,src={runtime_root},dst={runtime_root},readonly",
                "--mount",
                (
                    f"type=bind,src={python_distribution},"
                    f"dst={python_distribution},readonly"
                ),
                "--mount",
                f"type=bind,src={output_parent},dst={output_parent}",
                "--env",
                f"PYTHONPATH={pythonpath}",
                "--env",
                "PYTHONDONTWRITEBYTECODE=1",
                "--env",
                "HOME=/tmp",
                "--env",
                "MPLCONFIGDIR=/tmp/matplotlib",
                "--env",
                "XDG_CACHE_HOME=/tmp/xdg-cache",
                image_id,
                str(python),
            ]
        )
    except EnvironmentRefusal as error:
        raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_RUNTIME_IMAGE_INVALID") from error


def _run_workflow_golden(
    args: argparse.Namespace,
    product_id: str,
) -> dict[str, object]:
    output = _workflow_golden_run_directory(args.output_dir)
    product_development = ROOT / "products" / product_id / "development"
    verified_image: tuple[str, dict[str, object]] | None = None
    if args.runtime_image is not None:
        verified_image = _verified_workflow_runtime_image(args.runtime_image)
    with _native_runtime_python() as (python, runtime_evidence):
        task_command = None
        if verified_image is not None:
            image_id, system_evidence = verified_image
            task_command = _workflow_container_task_command(
                image_id, python, output.parent
            )
            runtime_evidence = {
                **runtime_evidence,
                "systemRuntime": system_evidence,
            }
        return run_workflow_golden(
            repository_root=ROOT,
            catalog_directory=ROOT / "packages/operator-pack/catalog/operators",
            case_file=product_development / "workflow-golden-cases.v1.json",
            baseline_file=product_development / "workflow-golden-baselines.v1.json",
            case_id=args.case,
            run_directory=output,
            python_executable=python,
            runtime_evidence=runtime_evidence,
            task_command=task_command,
        )


def _real_case_run_directory(configured: Path | None) -> tuple[Path, Path]:
    paths = lifecycle()
    ensure_private_roots(paths, ("cache",))
    real_case_root = owned_path("real-cases", paths.cache)
    source_cache = owned_path("sources", real_case_root)
    runs = owned_path("runs", real_case_root)
    for path in (real_case_root, source_cache, runs):
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
        path.chmod(0o700)
    try:
        output = (
            owned_path(configured, runs)
            if configured
            else owned_path(uuid.uuid4().hex, runs)
        )
    except EnvironmentRefusal as error:
        raise RealCaseRefusal("REAL_CASE_OUTPUT_OUTSIDE_CACHE") from error
    if output == runs:
        raise RealCaseRefusal("REAL_CASE_OUTPUT_INVALID")
    output.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    return output, source_cache


def _run_real_case(args: argparse.Namespace, product_id: str) -> dict[str, object]:
    output, source_cache = _real_case_run_directory(args.output_dir)
    image_id, system_evidence = _verified_workflow_runtime_image(args.runtime_image)
    with _native_runtime_python() as (python, runtime_evidence):
        task_command = _workflow_container_task_command(image_id, python, output.parent)
        runtime_evidence = {
            **runtime_evidence,
            "systemRuntime": system_evidence,
        }
        return run_real_cases(
            repository_root=ROOT,
            catalog_directory=ROOT / "packages/operator-pack/catalog/operators",
            manifest_path=(
                ROOT
                / "products"
                / product_id
                / "development"
                / "real-data-cases.v1.json"
            ),
            case_id=args.case,
            source_root=args.source_root,
            source_cache=source_cache,
            run_directory=output,
            python_executable=python,
            image_id=image_id,
            runtime_evidence=runtime_evidence,
            task_command=task_command,
        )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=(
            "compile",
            "up",
            "down",
            "status",
            "clean",
            "check",
            "operator-smoke",
            "workflow-golden",
            "real-case",
            "agent-replay",
            "capability-inventory",
            "interactive",
            "interactive-status",
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--product")
    parser.add_argument(
        "--scope",
        choices=(
            "kernel",
            "data",
            "knowledge",
            "executor",
            "assembler",
            "contracts",
            "web",
            "integration",
            "all",
        ),
    )
    parser.add_argument("--operator-id")
    parser.add_argument("--operator-version")
    parser.add_argument("--fixture", action="append", default=[])
    parser.add_argument("--parameter", action="append", default=[])
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--case")
    parser.add_argument("--repetitions", type=int)
    parser.add_argument("--runtime-image")
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--dashboard-port", type=int, default=28185)
    parser.add_argument("--poll-interval", type=float, default=0.75)
    parser.add_argument("--debounce-ms", type=int, default=350)
    parser.add_argument("--no-ui", action="store_true")
    parser.add_argument("--no-validate", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    knowledge_test_database_url = (
        os.environ.get("TEST_KNOWLEDGE_DATABASE_URL")
        if args.command == "check" and args.scope in {"knowledge", "all"}
        else None
    )
    try:
        activate(testing=args.command == "check")
    except EnvironmentRefusal as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(75) from error
    operator_specific_arguments = (
        args.operator_id is not None
        or args.operator_version is not None
        or bool(args.fixture)
        or bool(args.parameter)
    )
    if args.command == "capability-inventory":
        if (
            operator_specific_arguments
            or args.case is not None
            or args.repetitions is not None
            or args.runtime_image is not None
            or args.source_root is not None
            or args.output_dir is not None
            or args.scope is not None
            or args.dry_run
        ):
            parser.error(
                "capability-inventory accepts only --product and the optional "
                "--verify flag"
            )
        product_id = _select_product(ROOT, args.product)
        try:
            result = generate_capability_inventory(
                repository_root=ROOT,
                product_manifest_path=(
                    ROOT / "products" / product_id / "product-manifest.json"
                ),
                output_path=ROOT / "docs/development/current-capability-inventory.md",
                check=args.verify,
            )
        except CapabilityInventoryRefusal as error:
            print(
                json.dumps(
                    {"status": "FAILED", "code": error.code},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            raise SystemExit(2) from error
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return
    if args.command == "operator-smoke":
        if (
            args.operator_id is None
            or args.operator_version is None
            or args.case is not None
            or args.repetitions is not None
            or args.runtime_image is not None
            or args.verify
            or args.scope is not None
            or args.product is not None
            or args.source_root is not None
            or args.dry_run
        ):
            parser.error(
                "operator-smoke requires --operator-id and --operator-version "
                "and does not accept --scope, --product, or --dry-run"
            )
        try:
            result = _run_operator_smoke(args)
        except SmokeRunRefusal as error:
            print(
                json.dumps(
                    {"status": "FAILED", "code": error.code},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            raise SystemExit(2) from error
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return
    if args.command == "workflow-golden":
        if (
            args.case is None
            or operator_specific_arguments
            or args.repetitions is not None
            or args.scope is not None
            or args.dry_run
            or args.verify
            or args.source_root is not None
        ):
            parser.error(
                "workflow-golden requires --case and does not accept operator, "
                "--scope, or --dry-run arguments"
            )
        product_id = _select_product(ROOT, args.product)
        try:
            result = _run_workflow_golden(args, product_id)
        except WorkflowGoldenRefusal as error:
            print(
                json.dumps(
                    {"status": "FAILED", "code": error.code},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            raise SystemExit(2) from error
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        if result.get("status") != "PASS":
            raise SystemExit(1)
        return
    if args.command == "real-case":
        if (
            args.case is None
            or args.source_root is None
            or args.runtime_image is None
            or operator_specific_arguments
            or args.repetitions is not None
            or args.scope is not None
            or args.dry_run
            or args.verify
        ):
            parser.error(
                "real-case requires --case, --source-root, and --runtime-image "
                "and does not accept operator, --scope, or --dry-run arguments"
            )
        product_id = _select_product(ROOT, args.product)
        try:
            result = _run_real_case(args, product_id)
        except (RealCaseRefusal, WorkflowGoldenRefusal) as error:
            print(
                json.dumps(
                    {"status": "FAILED", "code": error.code},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            raise SystemExit(2) from error
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        if result.get("status") != "PASS":
            raise SystemExit(1)
        return
    if args.command == "agent-replay":
        from scripts.development.agent_replay import AgentReplayRefusal

        if (
            args.case is None
            or operator_specific_arguments
            or args.runtime_image is not None
            or args.scope is not None
            or args.product is not None
            or args.dry_run
            or args.verify
            or args.source_root is not None
        ):
            parser.error(
                "agent-replay requires --case and does not accept operator, "
                "--runtime-image, --scope, --product, or --dry-run arguments"
            )
        try:
            result = _run_agent_replay(args)
        except AgentReplayRefusal as error:
            print(
                json.dumps(
                    {"status": "FAILED", "code": error.code},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            raise SystemExit(2) from error
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        if result.get("status") != "PASS":
            raise SystemExit(1)
        return
    if (
        operator_specific_arguments
        or args.case is not None
        or args.repetitions is not None
        or args.output_dir is not None
        or args.runtime_image is not None
        or args.source_root is not None
        or args.verify
    ):
        parser.error(
            "operator arguments, --case, --repetitions, and --output-dir are "
            "only valid with operator-smoke, workflow-golden, real-case, or "
            "agent-replay"
        )
    interactive_options_changed = (
        args.dashboard_port != 28185
        or args.poll_interval != 0.75
        or args.debounce_ms != 350
        or args.no_ui
        or args.no_validate
        or args.once
    )
    if args.command in {"interactive", "interactive-status"}:
        if args.scope is not None:
            parser.error("interactive commands do not accept --scope")
        if args.command == "interactive-status" and (
            args.dry_run or interactive_options_changed
        ):
            parser.error("interactive-status accepts only --product")
        if not 0 <= args.dashboard_port <= 65535:
            parser.error("--dashboard-port must be between 0 and 65535")
        if args.poll_interval <= 0:
            parser.error("--poll-interval must be greater than zero")
        if args.debounce_ms < 0:
            parser.error("--debounce-ms must be non-negative")
        product_id = _select_product(ROOT, args.product)
        from scripts.development.interactive.command import (
            InteractiveCommand,
            InteractiveHooks,
            read_interactive_status,
        )

        state_directory = owned_path(
            Path("research-dev") / product_id / "interactive",
            lifecycle().state,
        )
        if args.command == "interactive-status":
            print(
                json.dumps(
                    read_interactive_status(state_directory),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        command = InteractiveCommand(
            root=ROOT,
            state_directory=state_directory,
            product_id=product_id,
            hooks=InteractiveHooks(
                ensure_dependencies=_ensure_research_dependencies,
                compile_product=lambda: compile_product(
                    ROOT,
                    ROOT / "products" / product_id / "product-manifest.json",
                ),
                run_compose=lambda arguments, extra_files, check: _run_compose(
                    product_id,
                    arguments,
                    extra_files=extra_files,
                    check=check,
                ),
                runtime_status=lambda: _status(product_id),
                record_bootstrap=lambda completed: _record_bootstrap_status(
                    product_id, completed
                ),
            ),
        )
        if args.dry_run:
            result = command.describe()
        else:
            result = command.run(
                dashboard_port=args.dashboard_port,
                poll_interval=args.poll_interval,
                debounce_seconds=args.debounce_ms / 1000,
                ui_enabled=not args.no_ui,
                validation_enabled=not args.no_validate,
                once=args.once,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if interactive_options_changed:
        parser.error(
            "interactive options are only valid with the interactive command"
        )
    if args.command == "check":
        if args.scope is None or args.dry_run or args.product is not None:
            parser.error(
                "check requires --scope and does not accept --dry-run or --product"
            )
        from scripts.checks.check_change import run_checks

        if knowledge_test_database_url is None:
            run_checks(args.scope)
        else:
            run_checks(
                args.scope, knowledge_test_database_url=knowledge_test_database_url
            )
        return
    if args.scope is not None:
        parser.error("--scope is only valid with check")
    if args.command == "clean":
        if args.product is not None:
            parser.error("clean does not accept --product")
        clean_workspace(dry_run=args.dry_run)
        return
    if args.dry_run:
        parser.error("--dry-run is only valid with clean")
    product_id = _select_product(ROOT, args.product)
    if args.command == "up":
        _ensure_research_dependencies()
        compile_product(ROOT, ROOT / "products" / product_id / "product-manifest.json")
        _run_compose(
            product_id,
            ["up", "--build", "--pull", "missing", "-d", "--wait"],
        )
        bootstrap = _run_compose(
            product_id,
            ["run", "--rm", "--no-deps", "knowledge-bootstrap"],
            check=False,
        )
        _record_bootstrap_status(product_id, bootstrap)
        print(json.dumps(_status(product_id), ensure_ascii=False, indent=2))
        if bootstrap.returncode:
            raise SystemExit(1)
        return
    if args.command == "down":
        _run_compose(product_id, ["down"])
        return
    if args.command == "status":
        print(json.dumps(_status(product_id), ensure_ascii=False, indent=2))
        return
    output = compile_product(
        ROOT, ROOT / "products" / product_id / "product-manifest.json"
    )
    print(output)


if __name__ == "__main__":
    main()
