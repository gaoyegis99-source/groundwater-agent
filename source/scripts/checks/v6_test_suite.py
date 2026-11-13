"""Declared, non-overlapping test slices for the assembled V6 product.

The normal project check and the auditable full-project runner both consume this
module. Keeping the inventory here makes a missing product test visible instead
of relying on a broad pytest discovery command whose omissions are hard to see.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import shlex
from tempfile import TemporaryDirectory


@dataclass(frozen=True)
class V6PytestSlice:
    key: str
    title: str
    paths: tuple[str, ...]
    requires_knowledge_database: bool = False
    pytest_arguments: tuple[str, ...] = ()


V6_PYTEST_SLICES: tuple[V6PytestSlice, ...] = (
    V6PytestSlice(
        key="foundation",
        title="Shared protocol and compiler tests",
        paths=(
            "packages/embedding-client/tests",
            "packages/protocol-contracts/tests",
            "packages/workflow-compiler/tests",
        ),
    ),
    V6PytestSlice(
        key="kernel",
        title="Kernel tests",
        paths=("kernel/tests",),
    ),
    V6PytestSlice(
        key="data",
        title="Data service tests",
        paths=("services/data/tests",),
        # Image-owned checks run in check_data_knowledge_e2e after the exact
        # service image has been built.  Keeping them out of this Python-only
        # slice avoids reporting a knowingly unexecuted check as a skip.
        pytest_arguments=("-m", "not service_image"),
    ),
    V6PytestSlice(
        key="knowledge",
        title="Knowledge service tests",
        paths=("services/knowledge-next/tests",),
        requires_knowledge_database=True,
    ),
    V6PytestSlice(
        key="executor",
        title="Executor service tests",
        paths=("services/executor/tests",),
    ),
    V6PytestSlice(
        key="contracts",
        title="Cross-service contract tests",
        paths=("tests/contracts",),
    ),
    V6PytestSlice(
        key="platform_product",
        title="Platform, CLI and groundwater product tests",
        paths=(
            "tests/architecture",
            "tests/assembler",
            "tests/cli",
            "tests/persistence",
            "tests/test_v6_state_layout_experiment.py",
            "products/groundwater/tests",
        ),
    ),
)


# These are the roots owned by the normal workspace pytest configuration. The
# native operator suite and standalone lightweight-agent delivery use their own
# locked environments and remain separate explicit checks.
V6_PYTEST_ROOTS: tuple[str, ...] = (
    "kernel/tests",
    "packages/embedding-client/tests",
    "packages/protocol-contracts/tests",
    "packages/workflow-compiler/tests",
    "services/data/tests",
    "services/executor/tests",
    "services/knowledge-next/tests",
    "tests",
    "products/groundwater/tests",
)


V6_UI_WORKSPACES: tuple[str, ...] = (
    "web",
    "services/data/web",
    "services/knowledge-next/ui",
)


def pytest_command(
    test_slice: V6PytestSlice, *, report_skips: bool = False
) -> tuple[str, ...]:
    options = ("-q", "-rs") if report_skips else ("-q",)
    return (
        "-m", "pytest", *options, *test_slice.pytest_arguments, *test_slice.paths
    )


@contextmanager
def isolated_pytest_command(command: Sequence[str]) -> Iterator[Sequence[str]]:
    """Keep each validation attempt out of pytest's shared temporary tree."""
    if list(command[1:3]) != ["-m", "pytest"]:
        yield command
        return
    options = [*command[3:], *shlex.split(os.getenv("PYTEST_ADDOPTS", ""))]
    if any(option == "--basetemp" or option.startswith("--basetemp=") for option in options):
        yield command
        return
    with TemporaryDirectory(prefix="groundwater-v6-pytest-") as directory:
        yield (*command, "--basetemp", str(Path(directory) / "tests"))
