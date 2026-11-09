"""Strict, dependency-free validators for lifecycle control-plane manifests."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
RELEASE_ID = re.compile(r"sha256-[0-9a-f]{64}\Z")
INSTANCE_ID = re.compile(r"[a-z0-9][a-z0-9.-]{0,62}\Z")


class ContractRefusalError(RuntimeError):
    """A control-plane document is incomplete, ambiguous, or from the future."""


ContractRefusal = ContractRefusalError


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8") + b"\n"


def sha256_digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _object(
    value: Any,
    *,
    required: set[str],
    optional: set[str] = frozenset(),
    code: str,
) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or not required.issubset(value)
        or not set(value).issubset(required | optional)
    ):
        raise ContractRefusal(code)
    return value


def validate_layout_v4(value: Any) -> dict[str, Any]:
    required = {
        "schema_version", "project", "source", "lifecycle", "docker", "ports",
        "accounts", "shared_runtime_paths", "shared_docker_resources",
    }
    result = _object(
        value, required=required, optional={"worktrees"}, code="LAYOUT_V4_INVALID"
    )
    expected_lifecycle = {
        "config": "xdg-config", "data": "xdg-data", "state": "xdg-state",
        "cache": "xdg-cache", "runtime": "xdg-runtime",
    }
    if (
        result["schema_version"] != 4
        or result["project"] != "groundwater-v6"
        or result["source"] != "source"
        or result["lifecycle"] != expected_lifecycle
        or result["docker"]
        != {
            "data_root": "xdg-cache/docker", "socket": "docker.sock",
            "default_address_pool": "10.206.0.0/16", "network_size": 24,
            "authoritative_volumes": False,
        }
        or result["ports"] != {"research": {"gateway": 28186}}
        or set(result["accounts"]) != {"research"}
        or not isinstance(result["accounts"]["research"], str)
        or not result["accounts"]["research"]
        or result["shared_runtime_paths"] != []
        or result["shared_docker_resources"] != []
        or not isinstance(result.get("worktrees", {}), dict)
    ):
        raise ContractRefusal("LAYOUT_V4_INVALID")
    return result
def validate_instance_manifest_v3(value: Any) -> dict[str, Any]:
    result = _object(
        value,
        required={
            "schemaVersion",
            "instanceId",
            "releaseId",
            "profile",
            "composeProject",
            "dataRoot",
            "stateRoot",
            "ports",
            "backupRoot",
            "createdAt",
            "status",
        },
        code="INSTANCE_MANIFEST_V3_INVALID",
    )
    instance_id = result["instanceId"]
    if (
        result["schemaVersion"] != "instance-manifest.v3"
        or not isinstance(instance_id, str)
        or INSTANCE_ID.fullmatch(instance_id) is None
        or not isinstance(result["releaseId"], str)
        or RELEASE_ID.fullmatch(result["releaseId"]) is None
        or result["profile"] not in {"development", "pilot"}
        or result["composeProject"] != f"groundwater-v6-{instance_id.replace('.', '-')}"
        or not all(
            isinstance(result[key], str) and Path(result[key]).is_absolute()
            for key in ("dataRoot", "stateRoot")
        )
        or result["status"]
        not in {"created", "active", "stopped", "restored", "destroyed"}
        or not isinstance(result["createdAt"], str)
        or (
            result["backupRoot"] is not None
            and (
                not isinstance(result["backupRoot"], str)
                or not Path(result["backupRoot"]).is_absolute()
            )
        )
        or (result["profile"] == "pilot" and result["backupRoot"] is None)
    ):
        raise ContractRefusal("INSTANCE_MANIFEST_V3_INVALID")
    ports = _object(
        result["ports"],
        required={"gateway", "operator"},
        code="INSTANCE_MANIFEST_V3_INVALID",
    )
    if any(
        not isinstance(port, int) or not 1024 <= port <= 65535
        for port in ports.values()
    ) or ports["gateway"] == ports["operator"]:
        raise ContractRefusal("INSTANCE_MANIFEST_V3_INVALID")
    return result


def validate_instance_manifest_v4(value: Any) -> dict[str, Any]:
    result = _object(
        value,
        required={
            "schemaVersion", "instanceId", "profile",
            "composeProject", "dataRoot", "stateRoot", "ports",
            "assemblyDigest", "createdAt", "status",
        },
        optional={"legacyReleaseId"},
        code="INSTANCE_MANIFEST_V4_INVALID",
    )
    instance_id = result["instanceId"]
    if (
        result["schemaVersion"] != "instance-manifest.v4"
        or not isinstance(instance_id, str)
        or INSTANCE_ID.fullmatch(instance_id) is None
        or result["profile"] != "research"
        or result["composeProject"] != f"groundwater-v6-{instance_id.replace('.', '-')}"
        or not all(isinstance(result[key], str) and Path(result[key]).is_absolute() for key in ("dataRoot", "stateRoot"))
        or result["status"] not in {"created", "active", "stopped", "restored", "destroyed"}
        or not isinstance(result["createdAt"], str)
        or not isinstance(result["assemblyDigest"], str)
        or DIGEST.fullmatch(result["assemblyDigest"]) is None
        or (
            "legacyReleaseId" in result
            and (
                not isinstance(result["legacyReleaseId"], str)
                or RELEASE_ID.fullmatch(result["legacyReleaseId"]) is None
            )
        )
    ):
        raise ContractRefusal("INSTANCE_MANIFEST_V4_INVALID")
    ports = _object(
        result["ports"], required={"gateway"}, code="INSTANCE_MANIFEST_V4_INVALID"
    )
    if not isinstance(ports["gateway"], int) or not 1024 <= ports["gateway"] <= 65535:
        raise ContractRefusal("INSTANCE_MANIFEST_V4_INVALID")
    return result
