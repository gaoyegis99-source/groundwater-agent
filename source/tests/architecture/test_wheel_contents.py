from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
from zipfile import ZipFile

import pytest

from scripts.maintenance.dependency_materializer import root as dependency_root
from scripts.maintenance.xdg_layout import resolve_lifecycle_paths


ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts" / "release" / "check_wheel_contents.py"
SPEC = importlib.util.spec_from_file_location("check_wheel_contents", CHECKER)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

LEGACY_ENTRIES = {
    "platform-workflow-compiler": "platform_core/__init__.py",
    "platform-assembler": "platform_core.py",
    "platform-executor-service": "platform_worker/__init__.py",
    "platform-kernel-services": "platform_core/__init__.py",
    "platform-embedding-client": "platform_core/__init__.py",
    "platform-data-service": "platform_resource_service/__init__.py",
    "platform-data-client": "platform_resource_client/__init__.py",
    "platform-knowledge-next": "platform_knowledge_service/__init__.py",
    "platform-knowledge-client": "platform_knowledge_service/__init__.py",
    "platform-protocol-contracts": "platform_core/__init__.py",
    "platform-executor-client": "platform_worker/__init__.py",
    "groundwater-api-client": "platform_core/__init__.py",
    "platform-operator-pack": "moperators/api/__init__.py",
}
PROJECT_ROOTS = {
    name: (
        definition.source_root
        if definition.package is None
        else definition.source_root.parent
    )
    for name, definition in MODULE.DISTRIBUTIONS.items()
}


def _source_files(distribution: str) -> dict[str, bytes]:
    return {
        name: path.read_bytes()
        for name, path in MODULE._source_files(distribution).items()
    }


def _write_wheel(
    path: Path,
    distribution: str,
    files: dict[str, bytes],
    *,
    metadata_name: str | None = None,
    metadata_version: str | None = None,
) -> None:
    metadata_version = metadata_version or MODULE.VERSIONS.get(
        distribution, MODULE.DEFAULT_VERSION
    )
    dist_info = distribution.replace("-", "_") + f"-{metadata_version}.dist-info"
    with ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
        archive.writestr(
            f"{dist_info}/METADATA",
            (
                "Metadata-Version: 2.4\n"
                f"Name: {metadata_name or distribution}\n"
                f"Version: {metadata_version}\n"
            ),
        )


def test_assembler_packaging_declares_every_top_level_runtime_module() -> None:
    project = ROOT / "platform" / "assembler"
    configuration = tomllib.loads(
        (project / "pyproject.toml").read_text(encoding="utf-8")
    )
    declared = set(configuration["tool"]["setuptools"]["py-modules"])
    sources = {path.stem for path in project.glob("*.py") if path.is_file()}

    assert declared == sources


@pytest.mark.parametrize("distribution", sorted(MODULE.DISTRIBUTIONS))
def test_wheel_checker_accepts_exact_distribution_source(
    tmp_path: Path, distribution: str
) -> None:
    wheel = tmp_path / f"{distribution}-current-py3-none-any.whl"
    _write_wheel(wheel, distribution, _source_files(distribution))

    assert MODULE.check_wheel(distribution, wheel) == []


@pytest.mark.parametrize("distribution", sorted(MODULE.DISTRIBUTIONS))
def test_wheel_checker_rejects_legacy_sibling_content(
    tmp_path: Path, distribution: str
) -> None:
    wheel = tmp_path / f"{distribution}-current-py3-none-any.whl"
    legacy = LEGACY_ENTRIES[distribution]
    _write_wheel(
        wheel,
        distribution,
        {**_source_files(distribution), legacy: b""},
    )

    assert f"extra: {legacy}" in MODULE.check_wheel(distribution, wheel)


@pytest.mark.parametrize(
    "extra",
    [
        "platform_core/__init__.pyi",
        "platform_core/metadata.json",
        "platform_kernel/stale.pyi",
        "platform_kernel/stale.json",
    ],
)
def test_wheel_checker_rejects_non_python_and_package_data_extras(
    tmp_path: Path, extra: str
) -> None:
    distribution = "platform-kernel-services"
    wheel = tmp_path / "platform_kernel-current-py3-none-any.whl"
    _write_wheel(
        wheel,
        distribution,
        {**_source_files(distribution), extra: b"stale"},
    )

    assert f"extra: {extra}" in MODULE.check_wheel(distribution, wheel)


def test_wheel_checker_rejects_missing_changed_and_duplicate_content(
    tmp_path: Path,
) -> None:
    distribution = "platform-kernel-services"
    wheel = tmp_path / "platform_kernel-current-py3-none-any.whl"
    files = _source_files(distribution)
    missing = "platform_kernel/__init__.py"
    changed = "platform_kernel/errors.py"
    duplicate = "platform_kernel/settings.py"
    del files[missing]
    files[changed] = b"# stale source\n"
    current = files.pop(duplicate)
    version = MODULE.VERSIONS[distribution]
    dist_info = f"platform_kernel-{version}.dist-info"
    with ZipFile(wheel, "w") as archive:
        archive.writestr(duplicate, b"# stale source\n")
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr(duplicate, current)
        for name, content in files.items():
            archive.writestr(name, content)
        archive.writestr(
            f"{dist_info}/METADATA",
            f"Metadata-Version: 2.4\nName: platform-kernel-services\nVersion: {version}\n",
        )

    problems = MODULE.check_wheel(distribution, wheel)
    assert f"missing: {missing}" in problems
    assert f"changed: {changed}" in problems
    assert f"duplicate: {duplicate}" in problems


@pytest.mark.parametrize(
    ("name", "version", "message"),
    [
        (
            "other",
            "7.0.0",
            "distribution: expected platform-kernel-services, found other",
        ),
        (
            "platform-kernel-services",
            "9.9.9",
            "version: expected 7.0.0, found 9.9.9",
        ),
    ],
)
def test_wheel_checker_rejects_invalid_metadata(
    tmp_path: Path, name: str, version: str, message: str
) -> None:
    distribution = "platform-kernel-services"
    wheel = tmp_path / "platform_kernel-current-py3-none-any.whl"
    _write_wheel(
        wheel,
        distribution,
        _source_files(distribution),
        metadata_name=name,
        metadata_version=version,
    )

    assert message in MODULE.check_wheel(distribution, wheel)


@pytest.mark.parametrize("distribution", sorted(MODULE.DISTRIBUTIONS))
def test_wheel_checker_rejects_legacy_build_input(
    tmp_path: Path, distribution: str
) -> None:
    project = tmp_path / distribution
    shutil.copytree(
        PROJECT_ROOTS[distribution],
        project,
        ignore=shutil.ignore_patterns(
            ".pytest_cache",
            ".ruff_cache",
            ".venv",
            "__pycache__",
            "build",
            "dist",
            "node_modules",
        ),
    )
    # Hatchling packages src directly and never consumes setuptools' build/lib.
    build_input = (
        project / "src"
        if distribution == "platform-operator-pack"
        else project / "build" / "lib"
    )
    stale = build_input / LEGACY_ENTRIES[distribution]
    stale.parent.mkdir(parents=True)
    stale.write_text("", encoding="utf-8")
    wheels = tmp_path / "wheels"
    wheels.mkdir()

    uv = shutil.which("uv")
    if uv is None:
        pytest.fail("UV_MISSING: run bin/bootstrap --sync")
    build_wheelhouse = (
        dependency_root(ROOT, resolve_lifecycle_paths()) / "python" / "build"
    )
    if not build_wheelhouse.is_dir():
        pytest.skip("BUILD_WHEELHOUSE_MISSING: run gw deps materialize --scope python")
    subprocess.run(
        [
            uv,
            "build",
            "--wheel",
            "--out-dir",
            str(wheels),
            "--build-constraints",
            str(ROOT / "environments" / "build" / "requirements.lock"),
            "--require-hashes",
            "--no-index",
            "--find-links",
            str(build_wheelhouse),
            "--offline",
            "--no-cache",
            "--no-config",
            "--python",
            sys.executable,
            "--no-python-downloads",
            str(project),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(wheels.glob("*.whl"))

    assert f"extra: {LEGACY_ENTRIES[distribution]}" in MODULE.check_wheel(
        distribution, wheel
    )


def test_wheel_checker_cli_reports_success(tmp_path: Path) -> None:
    distribution = "platform-embedding-client"
    wheel = tmp_path / "platform_embedding_client-current-py3-none-any.whl"
    _write_wheel(wheel, distribution, _source_files(distribution))

    result = subprocess.run(
        [sys.executable, str(CHECKER), distribution, str(wheel)],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0
    assert "Wheel content PASS: platform-embedding-client" in result.stdout
