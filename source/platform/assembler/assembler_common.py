from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
import re

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError


MANIFEST_FIELDS = {
    "schemaVersion",
    "productId",
    "projectId",
    "publicApiVersion",
    "domainPackage",
    "knowledgeProjects",
    "services",
    "publicRoutes",
    "uiProfile",
    "remoteServices",
}
DOMAIN_FIELDS_V1 = {
    "schemaVersion", "domainId", "version", "instructions", "observationTools",
    "workflowDefinitions", "executableModels", "knowledgeDocuments",
}
DOMAIN_FIELDS_V2 = DOMAIN_FIELDS_V1 | {"executionCases"}
DOMAIN_FIELDS_V3 = DOMAIN_FIELDS_V2
TOOL_FIELDS = {"service", "contract", "tool"}
MODEL_FIELDS_V4 = {
    "schemaVersion",
    "modelId",
    "version",
    "workflowRef",
    "tool",
    "inputBindings",
    "defaultParameters",
    "parameterBindings",
    "deadlineSeconds",
    "maxParallelNodes",
    "finalOutputs",
    "outputPublications",
    "executionService",
    "executorId",
}
MODEL_FIELDS_V5 = MODEL_FIELDS_V4 | {"fieldRequirements"}
MODEL_FIELDS = MODEL_FIELDS_V5 | {"semanticRequirements"}
ROUTE_FIELDS = {"prefix", "service", "contract"}
MODULE_FIELDS = {"apiVersion", "kind", "metadata", "spec"}
CURRENT_MODULE_API_VERSION = "platform.module/v9"
MODULE_SPEC_FIELDS = {
    "owner",
    "process",
    "provides",
    "requires",
    "httpContracts",
    "observationToolContracts",
    "executors",
    "health",
    "storage",
}
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]*$")
TOOL_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
CHECKSUM_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
SOURCE_ID_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,126}[a-z0-9])?$")
LANGUAGE_PATTERN = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
ROUTE_PREFIX_PATTERN = re.compile(r"^/api/v12/[a-z0-9][a-z0-9._~/-]*$")
EXTERNAL_CAPABILITIES = {"embedding-provider-v1"}
PROJECT_UPLOAD_BYTES = 1024 ** 3
KERNEL_ROUTE_PREFIXES = {
    "/api/v12/sessions",
    "/api/v12/agent-runs",
    "/api/v12/workflow-runs",
    "/api/v12/health",
    "/api/v12/capabilities",
    "/api/v12/ready",
    "/api/v12/audit",
}

@dataclass(frozen=True)
class HttpContract:
    contract_id: str
    base_path: str
    snapshot_path: Path
    checksum: str
    document: dict


@dataclass(frozen=True)
class ObservationToolContract:
    contract_id: str
    path: Path
    checksum: str
    tools: dict[str, dict]


@dataclass(frozen=True)
class ModuleContract:
    name: str
    version: str
    path: Path
    api_version: str
    distribution_name: str
    distribution_version: str
    provides: frozenset[str]
    requires: frozenset[str]
    http_contracts: dict[str, HttpContract]
    observation_tool_contracts: dict[str, ObservationToolContract]
    executors: frozenset[str]
    command: tuple[str, ...]
    ready_path: str
    storage_paths: tuple[str, ...]
    temporary_paths: tuple[str, ...]


@dataclass(frozen=True)
class ServiceBinding:
    name: str
    internal_base_url: str

    def as_wire(self) -> dict[str, str]:
        return {"internalBaseUrl": self.internal_base_url}


@dataclass(frozen=True)
class RouteBinding:
    prefix: str
    service: str
    contract: str
    base_path: str

    def as_wire(self) -> dict[str, str]:
        return {
            "prefix": self.prefix,
            "service": self.service,
            "contract": self.contract,
            "basePath": self.base_path,
        }


@dataclass(frozen=True)
class ProductContract:
    product_id: str
    project_id: str
    public_api_version: str
    domain_package: dict[str, str]
    knowledge_projects: tuple[str, ...]
    service_names: tuple[str, ...]
    public_routes: tuple[dict[str, str], ...]
    ui_profile: dict[str, str]
    remote_services: tuple[dict, ...]
    document: dict

    @classmethod
    def from_document(cls, value: dict) -> "ProductContract":
        return cls(
            product_id=value["productId"],
            project_id=value["projectId"],
            public_api_version=value["publicApiVersion"],
            domain_package=value["domainPackage"],
            knowledge_projects=tuple(value["knowledgeProjects"]),
            service_names=tuple(value["services"]),
            public_routes=tuple(value["publicRoutes"]),
            ui_profile=value["uiProfile"],
            remote_services=tuple(value["remoteServices"]),
            document=value,
        )


@dataclass(frozen=True)
class AssemblyIR:
    product: ProductContract
    domain: "DomainPackage"
    modules: dict[str, ModuleContract]
    services: dict[str, ServiceBinding]
    routes: tuple[RouteBinding, ...]

    def service_wire(self) -> dict[str, dict[str, str]]:
        return {
            name: binding.as_wire()
            for name, binding in sorted(self.services.items())
        }

@dataclass(frozen=True)
class DomainPackage:
    root: Path
    manifest: dict
    files: dict[str, bytes]
    definitions: dict[str, dict]
    instructions: str
    knowledge_documents: tuple[dict, ...]
    execution_cases: tuple[dict, ...]

    @property
    def file_checksums(self) -> dict[str, str]:
        return {
            name: "sha256:" + hashlib.sha256(content).hexdigest()
            for name, content in sorted(self.files.items())
        }

    @property
    def identity(self) -> dict[str, str]:
        return {
            "domainId": self.manifest["domainId"],
            "version": self.manifest["version"],
            "checksum": _checksum(self.file_checksums),
        }

def load_json(path: Path, error: str = "PRODUCT_MANIFEST_INVALID") -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as cause:
        raise ValueError(error) from cause
    if not isinstance(value, dict):
        raise ValueError(error)
    return value

def require_fields(value: dict, fields: set[str], error: str) -> None:
    if set(value) != fields:
        raise ValueError(error)

def require_string(value: object, error: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(error)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as cause:
        raise ValueError(error) from cause
    return value

def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")

def _checksum(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()

def resolve_inside(base: Path, value: Path | str, error: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(error)
    resolved = (base / candidate).resolve()
    try:
        resolved.relative_to(base.resolve())
    except ValueError as cause:
        raise ValueError(error) from cause
    if not resolved.is_file():
        raise ValueError(error)
    return resolved

def _validate_schema(schema: object, error: str) -> None:
    if not isinstance(schema, dict):
        raise ValueError(error)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as cause:
        raise ValueError(error) from cause
    def require_local_references(value: object) -> None:
        if isinstance(value, dict):
            reference = value.get("$ref")
            if reference is not None and (
                not isinstance(reference, str) or not reference.startswith("#/$defs/")
            ):
                raise ValueError(error)
            for child in value.values():
                require_local_references(child)
        elif isinstance(value, list):
            for child in value:
                require_local_references(child)

    require_local_references(schema)

def _validate_value(schema: dict, value: object, error: str) -> None:
    try:
        Draft202012Validator(schema).validate(value)
    except ValidationError as cause:
        raise ValueError(error) from cause

def _string_set(value: object, error: str) -> frozenset[str]:
    if (
        not isinstance(value, (list, tuple))
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(value))
    ):
        raise ValueError(error)
    return frozenset(value)

def _path_list(value: object, error: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(value))
    ):
        raise ValueError(error)
    return tuple(value)
