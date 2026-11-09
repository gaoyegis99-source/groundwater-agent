"""The V6 Compose topology shared by assembled and service deployments.

This module deliberately contains declarations only.  It does not talk to
Docker and it does not infer privileges from a service name at runtime.  The
assembler uses the same roles in every product candidate; the standalone Data
and Knowledge Compose files use the corresponding names documented here.
"""

from __future__ import annotations

from collections.abc import Mapping


ASSEMBLY_VERSION = "v6-topology.v1"

# All east/west networks are internal.  ``v6-ingress`` and ``v6-egress`` are
# narrow north/south exceptions: only the two gateways join ingress, while only
# model and embedding clients join egress.
ASSEMBLED_NETWORKS: dict[str, dict[str, bool]] = {
    "v6-ingress": {},
    "v6-service": {"internal": True},
    "v6-data-store": {"internal": True},
    "v6-knowledge-store": {"internal": True},
    "v6-executor": {"internal": True},
    "v6-egress": {},
}

SERVICE_NETWORKS: Mapping[str, tuple[str, ...]] = {
    "gateway": ("v6-ingress", "v6-service"),
    "kernel-service": ("v6-service", "v6-egress"),
    "data-db": ("v6-data-store",),
    "data-migrate": ("v6-data-store",),
    "data-service": ("v6-service", "v6-data-store"),
    "data-worker": ("v6-data-store",),
    "knowledge-next-db": ("v6-knowledge-store",),
    "knowledge-next-migrate": ("v6-knowledge-store",),
    "knowledge-next-service": (
        "v6-service",
        "v6-knowledge-store",
        "v6-egress",
    ),
    "knowledge-next-worker": (
        "v6-service",
        "v6-knowledge-store",
        "v6-egress",
    ),
    "knowledge-bootstrap": ("v6-service", "v6-knowledge-store"),
    "executor-control": ("v6-service", "v6-executor"),
    "executor-native-worker": ("v6-executor",),
    "executor-qgis-worker": ("v6-executor",),
    "executor-remote_http-worker": ("v6-executor", "v6-egress"),
}

# PostgreSQL needs its durable data directory, migrations deliberately alter
# schemas, and QGIS creates runtime files outside the generic Python paths.
# These are narrow, machine-readable exceptions rather than silent omissions.
HARDENING_EXCEPTIONS: Mapping[str, str] = {
    "data-db": "database",
    "knowledge-next-db": "database",
    "data-migrate": "migration",
    "knowledge-next-migrate": "migration",
    "executor-qgis-worker": "qgis-runtime",
}


def _network_bindings(names: tuple[str, ...]) -> dict[str, dict[str, str]]:
    return {name: {} for name in names}


def _harden(service: dict, *, role: str, project_id: str) -> None:
    """Apply the portable least-privilege baseline to one Compose service."""

    labels = dict(service.get("labels", {}))
    labels.update(
        {
            "io.platform.project": project_id,
            "io.platform.candidate": "${PLATFORM_CANDIDATE_ID:-assembled}",
            "io.platform.role": role,
            "io.platform.assembly-version": ASSEMBLY_VERSION,
        }
    )
    exception = HARDENING_EXCEPTIONS.get(role)
    if exception is not None:
        labels["io.platform.hardening-exception"] = exception
        service["labels"] = labels
        return

    service["labels"] = labels
    service["read_only"] = True
    service["cap_drop"] = ["ALL"]
    security_options = list(service.get("security_opt", []))
    if "no-new-privileges:true" not in security_options:
        security_options.append("no-new-privileges:true")
    service["security_opt"] = security_options
    # Every writable runtime path is explicit. Authoritative application data
    # is supplied as an instance-root bind mount by the assembler.
    service["tmpfs"] = ["/tmp:rw,noexec,nosuid,size=64m"]
    if role == "gateway":
        service["user"] = "101:101"
        service["tmpfs"] += [
            "/var/cache/nginx:rw,noexec,nosuid,uid=101,gid=101,mode=0700,size=16m",
            "/var/run:rw,noexec,nosuid,uid=101,gid=101,mode=0700,size=4m",
        ]


def apply_assembled_topology(services: dict[str, dict], project_id: str) -> dict:
    """Attach declared networks, labels and hardening to an assembled stack."""

    for role, service in services.items():
        networks = SERVICE_NETWORKS.get(role, ("v6-service",))
        service["networks"] = _network_bindings(networks)
        _harden(service, role=role, project_id=project_id)
    return {
        "services": services,
        "networks": {name: dict(value) for name, value in ASSEMBLED_NETWORKS.items()},
    }
