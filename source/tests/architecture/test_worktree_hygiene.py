from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "checks" / "check_worktree_hygiene.py"
SPEC = importlib.util.spec_from_file_location("check_worktree_hygiene", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_reusable_source_and_case_manifest_are_trackable() -> None:
    assert (
        MODULE.check_paths(
            [
                "kernel/src/platform_kernel/main.py",
                "migration-assets/yaozhanbao-transfer-case/relocation.v1.json",
            ]
        )
        == []
    )


def test_historical_and_candidate_roots_cannot_be_tracked() -> None:
    assert MODULE.check_paths(
        [
            "archive/docs/old.md",
            "docs/verification/old-report.md",
            "evidence/current/acceptance-summary.json",
            "golden/corpus-v1.json",
            "release/v6.3/export-manifest.json",
        ]
    ) == [
        "archive/docs/old.md: TRACKED_RETRIEVAL_POLLUTION_ROOT:archive",
        "docs/verification/old-report.md: TRACKED_HISTORICAL_VERIFICATION",
        "evidence/current/acceptance-summary.json: "
        "TRACKED_RETRIEVAL_POLLUTION_ROOT:evidence",
        "golden/corpus-v1.json: TRACKED_RETRIEVAL_POLLUTION_ROOT:golden",
        "release/v6.3/export-manifest.json: "
        "TRACKED_RETRIEVAL_POLLUTION_ROOT:release",
    ]


def test_case_payload_and_local_state_cannot_be_tracked() -> None:
    assert MODULE.check_paths(
        [
            "migration-assets/yaozhanbao-transfer-case/payload/resources/workbook.xlsx",
            "services/data/.venv/bin/python",
            ".generated/groundwater/compose.json",
            ".env.production",
            ".v6-agent-runs.sqlite",
            "packages/lightweight-agent-core/.v6-agent-runs.sqlite-wal",
        ]
    ) == [
        ".env.production: TRACKED_SECRET_CONFIGURATION",
        ".generated/groundwater/compose.json: TRACKED_EPHEMERAL_PATH:.generated",
        ".v6-agent-runs.sqlite: TRACKED_EPHEMERAL_JOURNAL",
        "migration-assets/yaozhanbao-transfer-case/payload/resources/workbook.xlsx: "
        "TRACKED_CASE_PAYLOAD",
        "packages/lightweight-agent-core/.v6-agent-runs.sqlite-wal: "
        "TRACKED_EPHEMERAL_JOURNAL",
        "services/data/.venv/bin/python: TRACKED_EPHEMERAL_PATH:.venv",
    ]


def test_workspace_scan_allows_the_enclosing_project_but_rejects_a_sibling(
    tmp_path: Path,
) -> None:
    source = tmp_path / "groundwater-v6" / "source"
    source.mkdir(parents=True)
    (source / "own.py").write_text(
        'path = "/home/mtd/liweigis/workflow/groundwater-v6/runtime/work/v6"\n',
        encoding="utf-8",
    )
    (source / "sibling.py").write_text(
        'path = "/home/mtd/liweigis/workflow/groundwater-v7/runtime/work/p3"\n',
        encoding="utf-8",
    )

    assert MODULE._workspace_dependency_violations(source) == [
        "sibling.py: WORKSPACE_SIBLING_DEPENDENCY:groundwater-v7"
    ]


def test_workspace_scan_rejects_a_link_outside_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("pass\n", encoding="utf-8")
    (source / "escaped.py").symlink_to(outside)

    assert MODULE._symlink_violations(source) == [
        f"escaped.py: SYMLINK_OUTSIDE_SOURCE:{outside}"
    ]


def test_workspace_scan_rejects_a_host_docker_socket(tmp_path: Path) -> None:
    source = tmp_path / "groundwater-v6" / "source"
    source.mkdir(parents=True)
    (source / "compose.yaml").write_text(
        "services:\n  worker:\n    volumes:\n      - /var/run/docker.sock:/var/run/docker.sock\n",
        encoding="utf-8",
    )
    assert MODULE._workspace_dependency_violations(source) == [
        "compose.yaml: HOST_DOCKER_SOCKET_SOURCE"
    ]


def test_workspace_scan_rejects_an_explicit_v7_python_import(tmp_path: Path) -> None:
    source = tmp_path / "groundwater-v6" / "source"
    source.mkdir(parents=True)
    (source / "bad.py").write_text(
        "from groundwater_v7.runtime import client\n", encoding="utf-8"
    )
    assert MODULE._workspace_dependency_violations(source) == [
        "bad.py: CROSS_PROJECT_IMPORT:groundwater-v7"
    ]


def test_layout_rejects_shared_or_non_xdg_declarations(tmp_path: Path) -> None:
    project = tmp_path / "groundwater-v6"
    source = project / "source"
    source.mkdir(parents=True)
    (project / "project-layout.json").write_text(
        json.dumps(
            {
                "schema_version": 4,
                "project": "groundwater-v6",
                "source": "source",
                "lifecycle": {
                    "config": "xdg-config",
                    "data": "xdg-data",
                    "state": "xdg-state",
                    "cache": "runtime",
                    "runtime": "xdg-runtime",
                },
                "docker": {
                    "data_root": "runtime/docker",
                    "socket": "/var/run/docker.sock",
                    "default_address_pool": "10.207.0.0/16",
                    "network_size": 25,
                    "authoritative_volumes": True,
                },
                "ports": {"research": {"gateway": 28187}},
                "accounts": {"development": "developer"},
                "shared_runtime_paths": ["../groundwater-v7/runtime"],
                "shared_docker_resources": [],
            }
        ),
        encoding="utf-8",
    )
    assert MODULE._layout_violations(source) == [
        "../project-layout.json: AUTHORITATIVE_VOLUME_DECLARED",
        "../project-layout.json: DOCKER_ADDRESS_POOL_INVALID",
        "../project-layout.json: DOCKER_DATA_ROOT_INVALID",
        "../project-layout.json: DOCKER_NETWORK_SIZE_INVALID",
        "../project-layout.json: DOCKER_SOCKET_INVALID",
        "../project-layout.json: RESEARCH_ACCOUNT_INVALID",
        "../project-layout.json: RESEARCH_PORTS_INVALID",
        "../project-layout.json: SHARED_RESOURCE_DECLARED:shared_runtime_paths",
        "../project-layout.json: XDG_LIFECYCLE_INVALID",
    ]


def test_project_bin_cannot_reference_a_sibling_runtime(tmp_path: Path) -> None:
    source = tmp_path / "groundwater-v6" / "source"
    source.mkdir(parents=True)
    command = source.parent / "bin" / "bad"
    command.parent.mkdir()
    command.write_text(
        "state=/home/mtd/liweigis/workflow/groundwater-v7/runtime/p8\n",
        encoding="utf-8",
    )
    assert MODULE._project_bin_violations(source) == [
        "../bin/bad: WORKSPACE_SIBLING_DEPENDENCY:groundwater-v7"
    ]
