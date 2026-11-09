"""Isolated paths, ports, Compose commands, and readiness helpers."""
from __future__ import annotations
import ipaddress
import json
import socket
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
import httpx
from scripts.maintenance.dependency_materializer import (
    root as dependency_material_root,
)
from scripts.maintenance.project_environment import docker_executable
from scripts.maintenance.xdg_layout import resolve_lifecycle_paths
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "platform/assembler") not in sys.path:
    sys.path.insert(0, str(ROOT / "platform/assembler"))
from base_images import locked_base_image_contexts  # noqa: E402
DATA_COMPOSE = ROOT / "services/data/compose.yaml"
KNOWLEDGE_COMPOSE = ROOT / "services/knowledge-next/compose.yaml"
ASSEMBLER = ROOT / "platform/assembler"
PGVECTOR_IMAGE = ("pgvector/pgvector:0.8.7-pg16-bookworm@" "sha256:7b822b0aac60967beb1ea5e576b8602c94c300a157d187f385ae3e0da199b90a")
from .context import Recorder  # noqa: E402


def _dependency_build_arguments(dependency_root: Path | None = None) -> list[str]:
    """Return the same frozen BuildKit contexts used by assembled releases."""

    material_root = dependency_root or dependency_material_root(
        ROOT, resolve_lifecycle_paths()
    )
    contexts = (
        f"platform-deps={material_root}",
        *locked_base_image_contexts(ROOT),
    )
    return [
        argument
        for context in contexts
        for argument in (
            "--build-context",
            context,
        )
    ]


def _frozen_image_build_command(
    image: str,
    dockerfile: str,
    context: str,
    *,
    dependency_root: Path | None = None,
) -> list[str]:
    """Build offline with the OCI-layout-capable project builder and load it."""

    return [
        docker_executable(),
        "buildx",
        "build",
        "--load",
        "--platform",
        "linux/amd64",
        "--network=none",
        *_dependency_build_arguments(dependency_root),
        "-t",
        image,
        "-f",
        dockerfile,
        context,
    ]


def _prepare_instance_data(work: Path) -> tuple[Path, Path]:
    """Create disjoint writable bind roots for source and restored services."""

    source = work / "instance-source"
    restored = work / "instance-restored"
    for root, relatives in (
        (source, ("data/database", "data/objects", "knowledge/database")),
        (restored, ("knowledge/database",)),
    ):
        for relative in relatives:
            target = root / relative
            target.mkdir(parents=True)
            # The test parent is run-scoped. Only bind leaves need to be
            # writable by image UIDs mapped through the rootless daemon.
            target.chmod(0o777)
    return source, restored


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _compose(project: str, base: Path, override: Path) -> list[str]:
    return [
        docker_executable(),
        "compose",
        "-p",
        project,
        "-f",
        str(base),
        "-f",
        str(override),
    ]


def _docker_port(recorder: Recorder, container: str, port: str) -> int:
    result = recorder.run(
        [docker_executable(), "port", container, port], capture=True
    ).stdout.strip()
    return int(result.rsplit(":", 1)[1])


def _compose_port(
    recorder: Recorder,
    command: list[str],
    env: dict[str, str],
) -> int:
    result = recorder.run(
        [*command, "port", "api", "8000"], env=env, capture=True
    ).stdout.strip()
    return int(result.rsplit(":", 1)[1])


def _free_ports(count: int) -> tuple[int, ...]:
    listeners: list[socket.socket] = []
    try:
        for _ in range(count):
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listeners.append(listener)
        return tuple(int(item.getsockname()[1]) for item in listeners)
    finally:
        for listener in listeners:
            listener.close()


def _secondary_subnet(value: str | None) -> str:
    """Return a deterministic, adjacent /24 for Compose's implicit network."""
    if value:
        network = ipaddress.ip_network(value, strict=True)
        candidate = ipaddress.ip_network(
            (int(network.network_address) + network.num_addresses, network.prefixlen),
            strict=False,
        )
        if not candidate.is_private:
            raise ValueError("NETWORK_SUBNET_SECONDARY_NOT_PRIVATE")
        return str(candidate)
    return "10.254.249.0/24"


def _wait_postgres(recorder: Recorder, container: str) -> None:
    for _ in range(120):
        result = recorder.run(
            [docker_executable(), "exec", container, "pg_isready", "-U", "knowledge"],
            capture=True,
            check=False,
            phase="startup_wait",
            expected_return_codes={1, 2},
        )
        if result.returncode == 0:
            return
        time.sleep(0.25)
    raise RuntimeError("KNOWLEDGE_TEST_DATABASE_NOT_READY")


def _wait_stub(recorder: Recorder, container: str) -> None:
    probe = (
        "import urllib.request;"
        "urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2)"
    )
    for _ in range(120):
        result = recorder.run(
            [docker_executable(), "exec", container, "python", "-c", probe],
            capture=True,
            check=False,
            phase="startup_wait",
            expected_return_codes={1},
        )
        if result.returncode == 0:
            return
        time.sleep(0.25)
    raise RuntimeError("EMBEDDING_STUB_NOT_READY")


def _write_overrides(
    work: Path,
    *,
    data_image: str,
    knowledge_image: str,
    network: str,
    default_network: str,
    project_id: str,
) -> tuple[Path, Path]:
    external_network = {"external": True, "name": network}
    data_services: dict[str, object] = {}
    for service in ("migrate", "api", "worker"):
        # Keep the shared integration network explicit. The private database
        # network is added below and must not be mapped to the same Docker
        # network, otherwise the two stacks expose colliding ``postgres``
        # DNS aliases.
        networks: dict[str, object] = {"joint": {}}
        if service == "api":
            networks["joint"] = {"aliases": ["data-api", "data-service"]}
        data_services[service] = {
            "image": data_image,
            "build": None,
            "environment": {
                "DATA_PROJECT": project_id,
                # The database hostname is unique in the shared Compose network.
                "DATA_DATABASE_URL": "postgresql://data:data-local@data-db:5432/data",
            },
            "networks": networks,
        }
    # The base service is named ``data-db`` so it cannot collide with the
    # Knowledge database when both stacks join the integration network.
    data_services["data-db"] = {"networks": {"joint": {}}}
    knowledge_services: dict[str, dict[str, Any]] = {
        service: {
            "image": knowledge_image,
            "build": None,
            "environment": {
                "KNOWLEDGE_PROJECT": project_id,
                "KNOWLEDGE_DATA_URL": "http://data-api:8000",
                "PLATFORM_EMBEDDING_BASE_URL": "http://embedding-stub:8000",
                # Both the source and restored stacks use the shared joint
                # network during the restore check.  A project-specific DNS
                # alias prevents a restored worker from accidentally claiming
                # a connection to the source database.
                "KNOWLEDGE_DATABASE_URL": (
                    "postgresql://knowledge:knowledge-local@"
                    f"knowledge-db-{project_id}:5432/knowledge"
                ),
            },
            "networks": {"joint": {}},
        }
        for service in ("migrate", "api", "worker")
    }
    knowledge_services["api"]["networks"]["joint"] = {
        "aliases": ["knowledge-next-service"]
    }
    knowledge_services["knowledge-db"] = {
        "networks": {
            "default": None,
            "joint": {"aliases": [f"knowledge-db-{project_id}"]},
        }
    }
    data_override = work / "data.override.json"
    knowledge_override = work / "knowledge.override.json"
    data_override.write_text(
        json.dumps(
            {
                "services": data_services,
                "networks": {"joint": external_network},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    knowledge_override.write_text(
        json.dumps(
            {
                "services": knowledge_services,
                # Compose still materializes the implicit default network from
                # the base file even when every service is attached to joint.
                # Make it an explicitly-created user network so it consumes no
                # implicit address-pool subnet and remains safe if Compose
                # attaches a database service to its default network.
                "networks": {
                    "default": {
                        "external": True,
                        "name": default_network,
                    },
                    "joint": external_network,
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return data_override, knowledge_override


def _wait_http_ready(
    client: httpx.Client,
    path: str = "/internal/health/ready",
    *,
    timeout: float = 60,
) -> dict:
    deadline = time.monotonic() + timeout
    last = "not requested"
    while time.monotonic() < deadline:
        try:
            response = client.get(path)
            last = f"HTTP {response.status_code}: {response.text[:500]}"
            if response.status_code == 200:
                return response.json()
        except httpx.RequestError as exc:
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(0.25)
    raise AssertionError(f"SERVICE_READINESS_TIMEOUT:{path}:{last}")
