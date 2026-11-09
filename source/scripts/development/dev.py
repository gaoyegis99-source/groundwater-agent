from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages/workflow-compiler/src"))
sys.path.insert(0, str(ROOT / "platform" / "assembler"))

from assembler import available_products, compile_product  # noqa: E402
from assembler_publication import (  # noqa: E402
    bundle_pointer_path,
    current_bundle,
    generated_root,
)

sys.path.insert(0, str(ROOT))
from scripts.maintenance.project_environment import (  # noqa: E402
    EnvironmentRefusal,
    activate,
    docker_arguments,
    lifecycle,
)
from scripts.maintenance import dependency_materializer  # noqa: E402

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
    missing = {
        scope
        for scope, status in current["scopes"].items()
        if status != "PASS"
    }
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
        "kernel", "data/database", "data/objects", "knowledge/database",
        "executor/state", "executor/work",
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
        "kernel-service", "data-migrate", "data-service", "data-worker",
        "knowledge-next-migrate", "knowledge-next-service", "knowledge-next-worker",
        "executor-control", "executor-native-worker", "executor-qgis-worker",
    )
    available = json.loads((output / "compose.json").read_text())["services"]
    override = state / "compose.source.json"
    override.write_text(
        json.dumps(
            {
                "services": {
                    name: {
                        "volumes": [{
                            "type": "bind", "source": str(ROOT),
                            "target": "/workspace", "read_only": True,
                        }],
                        "environment": {"PYTHONPATH": python_path},
                    }
                    for name in source_services if name in available
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    override.chmod(0o600)
    return (
        output / "compose.json", override, env_file, data,
        _compose_project(ROOT, product_id),
    )


def _run_compose(
    product_id: str,
    arguments: list[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    compose, override, env_file, _data, project = _research_runtime(product_id)
    command = docker_arguments([
        "compose", "--env-file", str(env_file),
        "--project-name", project, "--file", str(compose),
        "--file", str(override), *arguments,
    ])
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
        matches = re.findall(
            r"KNOWLEDGE_BOOTSTRAP_[A-Z_]+(?::[A-Z0-9_]+)?", combined
        )
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
        if (
            isinstance(status, dict)
            and status.get("assemblyDigest") == expected_digest
        ):
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
            return _unavailable_status(
                revision, source_dirty, source_digest, code
            )
        raise
    rows = _compose_json_rows(completed.stdout)
    services = {
        str(row.get("Service")): {
            "state": row.get("State"), "health": row.get("Health"),
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
                    "exec", "-T", service, "python", "-c", probe,
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
        service = labels.get("com.docker.compose.service") if isinstance(labels, dict) else None
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
                    bootstrap_status.get(
                        "errorCode", "KNOWLEDGE_BOOTSTRAP_NOT_RUN"
                    )
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("compile", "up", "down", "status", "clean", "check"))
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
    args = parser.parse_args()
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
