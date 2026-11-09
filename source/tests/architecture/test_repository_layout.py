from __future__ import annotations

import ast
import configparser
import json
from pathlib import Path
import re
import tomllib

import yaml


ROOT = Path(__file__).resolve().parents[2]
CAPSULES = (
    ROOT / "kernel",
    ROOT / "services" / "data",
    ROOT / "services" / "knowledge-next",
    ROOT / "services" / "executor",
)
SERVICE_PACKAGES = {
    "platform_data_service",
    "platform_knowledge_next",
    "platform_executor_service",
}


def test_ordinary_pytest_collection_excludes_native_only_suites() -> None:
    config = configparser.ConfigParser()
    config.read(ROOT / "pytest.ini", encoding="utf-8")
    paths = set(config["pytest"]["testpaths"].split())
    assert "packages/operator-pack/tests" not in paths
    assert "services/executor/native_tests" not in paths


def test_runtime_capsules_have_independent_project_boundaries() -> None:
    required = {
        "module.yaml",
        "pyproject.toml",
        "Dockerfile",
        "README.md",
        "src",
        "tests",
    }

    for capsule in CAPSULES:
        assert capsule.is_dir(), capsule.relative_to(ROOT)
        assert required <= {path.name for path in capsule.iterdir()}, (
            capsule.relative_to(ROOT)
        )


def test_only_effective_build_contexts_have_dockerignore() -> None:
    assert (ROOT / ".dockerignore").is_file()
    assert [
        capsule.relative_to(ROOT)
        for capsule in CAPSULES
        if (capsule / ".dockerignore").exists()
    ] == []


def test_knowledge_image_specific_ignore_keeps_its_build_lock() -> None:
    ignore = (
        (ROOT / "services/knowledge-next/Dockerfile.dockerignore")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert ignore[0] == "**"
    assert "!environments/build/requirements.lock" in ignore
    assert "COPY environments/build/requirements.lock" in (
        ROOT / "services/knowledge-next/Dockerfile"
    ).read_text(encoding="utf-8")


def test_python_lock_domains_are_hashed_and_used_by_images() -> None:
    requirement = re.compile(r"^[a-z0-9][a-z0-9._-]*==", re.MULTILINE)
    locks = [
        ROOT / "environments" / name / "requirements.lock"
        for name in ("build", "common", "native", "qgis", "testing")
    ]
    for path in locks:
        lock = path.read_text(encoding="utf-8")
        assert requirement.search(lock), path.relative_to(ROOT)
        assert "--hash=sha256:" in lock, path.relative_to(ROOT)
    for capsule in CAPSULES:
        dockerfile = (capsule / "Dockerfile").read_text(encoding="utf-8")
        assert "COPY --from=platform-deps" in dockerfile
        assert "--network=none" in dockerfile
        assert "apt-get update" not in dockerfile
        assert "--extra-index-url" not in dockerfile
        if capsule.name in {"data", "knowledge-next"}:
            assert "pip install" in dockerfile
            assert "--no-index --find-links=/wheels" in dockerfile


def test_native_test_lock_matches_root_pytest_without_entering_worker_images() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pytest_pin = next(
        item
        for item in project["dependency-groups"]["dev"]
        if item.startswith("pytest==")
    )
    test_root = ROOT / "environments/testing"
    assert (test_root / "requirements.in").read_text(
        encoding="utf-8"
    ).strip() == pytest_pin
    assert re.search(
        rf"^{re.escape(pytest_pin)}\s",
        (test_root / "requirements.lock").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    for runtime in ("native", "qgis"):
        dockerfile = (ROOT / "services/executor" / f"Dockerfile.{runtime}").read_text(
            encoding="utf-8"
        )
        assert "python/testing" not in dockerfile


def test_worker_runtime_locks_include_executor_service_dependencies() -> None:
    """Worker images install the Executor wheel with --no-deps."""

    executor = tomllib.loads(
        (ROOT / "services/executor/pyproject.toml").read_text(encoding="utf-8")
    )["project"]
    identity = f"{executor['name']}=={executor['version']}"
    for runtime in ("native", "qgis"):
        project = tomllib.loads(
            (ROOT / "environments" / runtime / "pyproject.toml").read_text(
                encoding="utf-8"
            )
        )
        assert identity in project["project"]["dependencies"]
        assert project["tool"]["uv"]["sources"][executor["name"]] == {
            "path": "../../services/executor"
        }
        lock = (ROOT / "environments" / runtime / "requirements.lock").read_text(
            encoding="utf-8"
        )
        for dependency in executor["dependencies"]:
            if dependency.startswith("platform-"):
                continue
            assert re.search(rf"^{re.escape(dependency)}\s", lock, re.MULTILINE)
        # The transitive protocol/client pin must not drift to Operator Pack's
        # independently resolved Pydantic version.
        assert re.search(r"^pydantic==2\.13\.5\s", lock, re.MULTILINE)
        dockerfile = (ROOT / "services/executor" / f"Dockerfile.{runtime}").read_text(
            encoding="utf-8"
        )
        assert "--no-deps --no-build-isolation" in dockerfile
        assert "-r /locks/runtime.lock" in dockerfile


def test_all_base_images_are_local_oci_contexts_with_digest_locked_sources() -> None:
    lock = json.loads((ROOT / "system-deps.lock.json").read_text())
    assert lock["baseImages"]
    assert all("@sha256:" in image for image in lock["baseImages"].values())
    dockerfiles = [
        ROOT / "kernel/Dockerfile",
        ROOT / "services/data/Dockerfile",
        ROOT / "services/executor/Dockerfile",
        ROOT / "services/executor/Dockerfile.native",
        ROOT / "services/executor/Dockerfile.qgis",
        ROOT / "services/knowledge-next/Dockerfile",
        ROOT / "services/gateway/Dockerfile",
    ]
    bases = {
        line.split()[1]
        for path in dockerfiles
        for line in path.read_text().splitlines()
        if line.startswith("FROM ")
    }
    assert bases
    assert all(name.startswith("platform-base-") for name in bases)
    assert {name.removeprefix("platform-base-") for name in bases} <= set(
        lock["baseImages"]
    )
    assert set(lock["baseImages"]) == {
        "nginx", "node", "pdal", "pgvector", "postgres", "python", "ubuntu",
    }
    assert all("# syntax=" not in path.read_text() for path in dockerfiles)


def test_active_validation_defaults_do_not_write_source_generated_tree() -> None:
    active = (
        ROOT / "scripts/checks/compose_e2e/command.py",
        ROOT / "scripts/checks/compose_e2e/scenarios.py",
        ROOT / "scripts/checks/data_knowledge_e2e/scenarios.py",
        ROOT / "scripts/evaluation/kernel_sandbox_pressure/command.py",
        ROOT / "scripts/evaluation/kernel_sandbox_pressure/environment.py",
        ROOT / "scripts/evaluation/kernel_sandbox_pressure/probe.py",
        ROOT / "scripts/operations/test_full_project.py",
        ROOT / "web/playwright.config.ts",
        ROOT / "web/e2e/build-global-setup.ts",
    )
    assert [
        path.relative_to(ROOT) for path in active if ".generated" in path.read_text()
    ] == []


def test_retired_runtime_and_release_mutators_are_absent() -> None:
    retired = (
        ROOT / "scripts/maintenance/cleanup_core.py",
        ROOT / "scripts/maintenance/cleanup_policy.py",
        ROOT / "scripts/maintenance/cleanup_protocol.py",
        ROOT / "scripts/maintenance/cleanup_rebuild.py",
        ROOT / "scripts/maintenance/retire_legacy_candidates.py",
        ROOT / "scripts/release/freeze_release_bundle.py",
        ROOT / "scripts/release/prepare_release_images.py",
    )
    assert [path.relative_to(ROOT) for path in retired if path.exists()] == []


def test_retired_layout_is_absent() -> None:
    retired = (
        ROOT / "core",
        ROOT / "kernel" / "execution-sdk",
        ROOT / "kernel" / "kernel-services",
        ROOT / "services" / "asset-service",
        ROOT / "services" / "knowledge-service",
        ROOT / "services" / "executor-service",
        ROOT / "services" / "result-service",
        ROOT / "services" / "map-service",
        ROOT / "services" / "map",
        ROOT / "services" / "asset",
        ROOT / "services" / "result",
        ROOT / "services" / "embedding-client",
        ROOT / "services" / "auth",
        ROOT / "packages" / "auth-contract",
        ROOT / "integration" / "profiles",
    )

    assert [path.relative_to(ROOT) for path in retired if path.exists()] == []


def test_services_do_not_import_other_service_packages() -> None:
    violations: list[str] = []
    for capsule in CAPSULES:
        for path in sorted((capsule / "src").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            own_packages = {
                part for part in path.relative_to(capsule / "src").parts[:1]
            }
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = {alias.name.split(".", 1)[0] for alias in node.names}
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    imported = {(node.module or "").split(".", 1)[0]}
                else:
                    continue
                forbidden = (imported & SERVICE_PACKAGES) - own_packages
                violations.extend(
                    f"{path.relative_to(ROOT)} -> {name}" for name in sorted(forbidden)
                )

    assert violations == []


def test_capsule_images_build_only_from_local_context() -> None:
    for capsule in CAPSULES:
        dockerfile = (capsule / "Dockerfile").read_text(encoding="utf-8")
        copies = [
            match.group(1)
            for line in dockerfile.splitlines()
            if "--from=" not in line and (match := re.match(r"^COPY\s+([^\s]+)", line))
        ]
        assert " AS wheels" in dockerfile
        assert sum(line.startswith("FROM ") for line in dockerfile.splitlines()) >= 2
        assert "10001" in dockerfile
        assert "VOLUME " not in dockerfile
        assert any(
            line.startswith("USER ") and line != "USER root"
            for line in dockerfile.splitlines()
        )
        if capsule in {ROOT / "kernel", ROOT / "services" / "executor"}:
            assert "HEALTHCHECK" in dockerfile
        assert copies
        assert all(not source.startswith(("/", "..")) for source in copies)
        allowed_roots = {capsule.relative_to(ROOT).as_posix()}
        allowed_roots.add("packages/protocol-contracts")
        allowed_roots.add("environments/build")
        if capsule == ROOT / "kernel":
            allowed_roots.add("packages/workflow-compiler")
            allowed_roots.add("services/executor/client")
        if capsule == ROOT / "services" / "knowledge-next":
            allowed_roots.add("packages/embedding-client")
            allowed_roots.add("services/data/client")
        if capsule == ROOT / "services" / "executor":
            allowed_roots.add("services/data/client")
        assert all(
            source.rstrip("/") in allowed_roots
            or any(source.startswith(root + "/") for root in allowed_roots)
            for source in copies
        )


def test_executor_worker_images_use_system_python() -> None:
    for runtime in ("native", "qgis"):
        dockerfile = (
            ROOT / "services" / "executor" / f"Dockerfile.{runtime}"
        ).read_text(encoding="utf-8")
        installs = [
            line
            for line in dockerfile.splitlines()
            if line.startswith("RUN ") and "-m pip install" in line
        ]

        assert installs
        assert all(
            line.startswith("RUN --network=none /usr/bin/python3 -m pip install")
            for line in installs
        )
        assert 'CMD ["/usr/bin/python3"' in dockerfile


def test_module_manifests_declare_owner_process_and_storage() -> None:
    for capsule in CAPSULES:
        manifest = yaml.safe_load((capsule / "module.yaml").read_text(encoding="utf-8"))
        assert manifest["apiVersion"] == "platform.module/v9"
        assert manifest["kind"] == "Service"
        expected_versions = {
            ROOT / "kernel": "10.0.0",
            ROOT / "services" / "data": "5.0.0",
            ROOT / "services" / "knowledge-next": "4.0.0",
            ROOT / "services" / "executor": "10.0.0",
        }
        assert manifest["metadata"]["version"] == expected_versions[capsule]
        distribution = manifest["metadata"]["distribution"]
        assert distribution["name"] and distribution["version"]
        assert manifest["spec"]["owner"]
        assert manifest["spec"]["process"]["command"]
        assert isinstance(manifest["spec"]["provides"], list)
        assert isinstance(manifest["spec"]["requires"], list)
        assert isinstance(manifest["spec"]["httpContracts"], dict)
        assert isinstance(manifest["spec"]["observationToolContracts"], dict)
        assert isinstance(manifest["spec"]["executors"], list)
        expected_health = {
            "live": "/internal/health/live",
            "ready": "/internal/health/ready",
        }
        assert manifest["spec"]["health"] == expected_health
        assert manifest["spec"]["storage"]["authority"]


def test_retired_services_are_absent_from_the_current_service_tree() -> None:
    assert not (ROOT / "services" / "resource").exists()
    assert not (ROOT / "services" / "knowledge").exists()


def test_v11_executor_distribution_has_no_resource_v6_adapter() -> None:
    executor_source = ROOT / "services" / "executor" / "src"
    assert not any(
        "/internal/resources/v6" in path.read_text(encoding="utf-8")
        for path in executor_source.rglob("*.py")
    )
