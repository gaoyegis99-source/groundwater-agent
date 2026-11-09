from __future__ import annotations

import configparser
import json
from pathlib import Path

from scripts.checks.v6_test_suite import (
    V6_PYTEST_ROOTS,
    V6_PYTEST_SLICES,
    V6_UI_WORKSPACES,
    pytest_command,
)


ROOT = Path(__file__).resolve().parents[2]


def _test_files(paths: tuple[str, ...]) -> set[str]:
    files: set[str] = set()
    for value in paths:
        path = ROOT / value
        candidates = (path,) if path.is_file() else path.rglob("test_*.py")
        files.update(
            candidate.relative_to(ROOT).as_posix()
            for candidate in candidates
            if candidate.is_file() and candidate.name.startswith("test_")
        )
    return files


def test_v6_pytest_roots_match_workspace_configuration() -> None:
    configuration = configparser.ConfigParser()
    configuration.read(ROOT / "pytest.ini", encoding="utf-8")
    roots = tuple(configuration["pytest"]["testpaths"].strip().splitlines())
    assert roots == V6_PYTEST_ROOTS


def test_every_workspace_pytest_file_belongs_to_exactly_one_v6_slice() -> None:
    expected = _test_files(V6_PYTEST_ROOTS)
    owners: dict[str, list[str]] = {}
    for test_slice in V6_PYTEST_SLICES:
        for path in _test_files(test_slice.paths):
            owners.setdefault(path, []).append(test_slice.key)

    assert set(owners) == expected
    assert {
        path: assigned for path, assigned in owners.items() if len(assigned) != 1
    } == {}


def test_every_declared_test_path_exists() -> None:
    assert {
        value
        for test_slice in V6_PYTEST_SLICES
        for value in test_slice.paths
        if not (ROOT / value).exists()
    } == set()


def test_groundwater_product_tests_are_in_the_platform_product_slice() -> None:
    product = next(
        test_slice
        for test_slice in V6_PYTEST_SLICES
        if test_slice.key == "platform_product"
    )
    assert "products/groundwater/tests" in product.paths


def test_data_python_slice_defers_built_image_checks_without_skipping_them() -> None:
    data = next(item for item in V6_PYTEST_SLICES if item.key == "data")

    assert pytest_command(data, report_skips=True) == (
        "-m", "pytest", "-q", "-rs", "-m", "not service_image",
        "services/data/tests",
    )
    source = (ROOT / "services/data/tests/test_failures.py").read_text()
    assert source.count("@pytest.mark.service_image") == 2
    assert "pytest.skip" not in source


def test_each_v6_ui_exposes_test_typecheck_and_build_commands() -> None:
    for workspace in V6_UI_WORKSPACES:
        package = json.loads((ROOT / workspace / "package.json").read_text())
        assert {"test", "typecheck", "build"} <= set(package["scripts"])
