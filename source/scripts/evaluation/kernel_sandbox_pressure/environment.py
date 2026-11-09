"""Deterministic environment, redaction, and metric helpers for pressure probes."""

from __future__ import annotations
import hashlib
import importlib.metadata
import json
from datetime import UTC, datetime
from pathlib import Path
import platform
import re
import subprocess
import sys
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[3]
for relative in (
    "kernel/src",
    "packages/protocol-contracts/src",
    "packages/workflow-compiler/src",
    "packages/operator-pack/src",
    "packages/groundwater-api-client/src",
    "services/executor/src",
    "services/executor/client/src",
    "services/data/src",
    "services/data/client/src",
    "services/knowledge-next/src",
    "services/knowledge-next/client/src",
    "platform/assembler",
):
    value = str(ROOT / relative)
    if value not in sys.path:
        sys.path.insert(0, value)
import httpx  # noqa: E402
from assembler_publication import current_bundle, generated_root  # noqa: E402

SCHEMA = "kernel-sandbox-pressure.v1"
PROJECT = "sandbox-project"
VERSION = "sandbox-version"
CHECKSUM = "sha256:" + "a" * 64
_IDENTITY_KEYS = {
    "callId",
    "requestId",
    "jobId",
    "sessionId",
    "agentRunId",
    "workflowRunId",
    "confirmationId",
    "traceId",
    "subject",
    "datasetId",
    "fileName",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _digest(value: object) -> str:
    encoded = json.dumps(
        _redact_digest_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _redact_digest_value(value: object) -> object:
    """Keep probe digests useful while making them independent of run IDs."""
    if isinstance(value, dict):
        return {
            str(key): (
                "{id}"
                if str(key) in _IDENTITY_KEYS
                or str(key).replace("_", "").lower().endswith("id")
                else _redact_digest_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_digest_value(item) for item in value]
    return value


def _jsonable(value: object) -> object:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return _jsonable(dump(exclude_none=True))
    return repr(value)


def _contains(value: object, marker: str) -> bool:
    return marker in json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True)


def _seen_tool(value: object, name: str) -> bool:
    encoded = _jsonable(value)
    items = encoded if isinstance(encoded, list) else [encoded]
    return any(
        isinstance(item, dict)
        and item.get("type") == "function_call"
        and item.get("name") == name
        for item in items
    )


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return ordered[index]


def latency_stats(values: list[float]) -> dict[str, float | int]:
    return {
        "count": len(values),
        "p50": round(percentile(values, 0.50), 4),
        "p95": round(percentile(values, 0.95), 4),
        "max": round(max(values, default=0.0), 4),
    }


def _safe_route(route: str) -> str:
    route = re.sub(r"/sessions/[^/?]+", "/sessions/{id}", route)
    route = re.sub(r"/jobs/[^/?]+", "/jobs/{id}", route)
    return re.sub(
        r"[0-9a-f]{8}-[0-9a-f-]{27,}",
        "{id}",
        route,
        flags=re.IGNORECASE,
    )


def _command_version(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=3, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return "UNAVAILABLE"
    text = (completed.stdout or completed.stderr).strip().splitlines()
    return text[0][:160] if text else "UNAVAILABLE"


def _environment() -> dict[str, object]:
    dependency_versions: dict[str, str] = {}
    for package in ("httpx", "fastapi", "pydantic", "jsonschema", "openai-agents"):
        try:
            dependency_versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            dependency_versions[package] = "UNAVAILABLE"
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "httpx": getattr(httpx, "__version__", "unknown"),
        "node": _command_version(["node", "--version"]),
        "docker": "NOT_USED_BY_SANDBOX",
        "dependencies": dependency_versions,
    }


def _kernel_config_path(root: Path) -> Path:
    return current_bundle(generated_root(root), "groundwater") / "kernel-config.json"


def _configuration(root: Path) -> dict[str, object]:
    try:
        path = _kernel_config_path(root)
    except ValueError:
        return {"status": "UNAVAILABLE", "path": "XDG assembly pointer"}
    if not path.exists():
        return {
            "status": "UNAVAILABLE",
            "path": "generated/groundwater/kernel-config.json",
        }
    raw = path.read_bytes()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {"status": "INVALID", "digest": _digest(raw)}
    services = payload.get("services")
    service_names = sorted(services) if isinstance(services, dict) else []
    return {
        "status": "AVAILABLE",
        "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "manifestChecksum": payload.get("manifestChecksum"),
        "serviceNames": service_names,
        "modelCount": len(payload.get("models", []))
        if isinstance(payload.get("models"), list)
        else 0,
    }


class _SeededUUID4:
    """Deterministic UUID source used only while the sandbox report runs."""

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self.counter = 0

    def __call__(self) -> UUID:
        self.counter += 1
        digest = hashlib.sha256(
            f"kernel-sandbox:{self.seed}:{self.counter}".encode()
        ).digest()
        return UUID(bytes=digest[:16], version=4)


def _install_seeded_uuid4(seed: int):
    factory = _SeededUUID4(seed)
    original = uuid4
    patches: list[tuple[dict[str, object], str, object]] = []
    for module in list(sys.modules.values()):
        namespace = getattr(module, "__dict__", None)
        if isinstance(namespace, dict) and namespace.get("uuid4") is original:
            patches.append((namespace, "uuid4", original))
            namespace["uuid4"] = factory

    def restore() -> None:
        for namespace, name, value in patches:
            namespace[name] = value

    return restore
