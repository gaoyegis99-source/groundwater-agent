from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
import re
from string import Formatter
from urllib.parse import urlsplit

import yaml

from assembler_common import (
    CHECKSUM_PATTERN, CURRENT_MODULE_API_VERSION,
    DOMAIN_FIELDS_V3, LANGUAGE_PATTERN, MANIFEST_FIELDS,
    MODULE_FIELDS, MODULE_SPEC_FIELDS,
    NAME_PATTERN, SOURCE_ID_PATTERN, TOOL_PATTERN, VERSION_PATTERN,
    DomainPackage, HttpContract, ModuleContract, ObservationToolContract,
    _path_list, _string_set, _validate_schema,
    load_json, require_fields, require_string, resolve_inside,
)

def _load_manifest(root: Path, manifest_path: Path | str) -> tuple[Path, dict]:
    products = (root / "products").resolve()
    candidate = Path(manifest_path)
    if candidate.is_absolute():
        try:
            relative = candidate.resolve().relative_to(products)
        except ValueError as cause:
            raise ValueError("PRODUCT_MANIFEST_INVALID") from cause
    else:
        relative = candidate
    path = resolve_inside(products, relative, "PRODUCT_MANIFEST_INVALID")
    manifest = load_json(path)
    require_fields(manifest, MANIFEST_FIELDS | ({"compositionOperators"} & manifest.keys()), "PRODUCT_MANIFEST_FIELDS_INVALID")
    if "compositionOperators" in manifest:
        _string_set(manifest["compositionOperators"], "PRODUCT_COMPOSITION_OPERATORS_INVALID")
    if manifest["schemaVersion"] != "product-manifest.v8":
        raise ValueError("PRODUCT_MANIFEST_VERSION_INVALID")
    if manifest["publicApiVersion"] != "v12":
        raise ValueError("PRODUCT_API_VERSION_INVALID")
    product_id = require_string(manifest["productId"], "PRODUCT_ID_INVALID")
    if not NAME_PATTERN.fullmatch(product_id) or path.parent.name != product_id:
        raise ValueError("PRODUCT_ID_INVALID")
    project_id = require_string(manifest["projectId"], "PRODUCT_PROJECT_ID_INVALID")
    if not NAME_PATTERN.fullmatch(project_id):
        raise ValueError("PRODUCT_PROJECT_ID_INVALID")
    reference = manifest["domainPackage"]
    if not isinstance(reference, dict):
        raise ValueError("DOMAIN_REFERENCE_INVALID")
    require_fields(reference, {"domainId", "version", "checksum"}, "DOMAIN_REFERENCE_INVALID")
    for key, pattern in (("domainId", NAME_PATTERN), ("version", VERSION_PATTERN), ("checksum", CHECKSUM_PATTERN)):
        if not pattern.fullmatch(require_string(reference[key], "DOMAIN_REFERENCE_INVALID")):
            raise ValueError("DOMAIN_REFERENCE_INVALID")
    _string_set(manifest["knowledgeProjects"], "KNOWLEDGE_PROJECTS_INVALID")
    for project in manifest["knowledgeProjects"]:
        if not require_string(project, "KNOWLEDGE_PROJECTS_INVALID").strip():
            raise ValueError("KNOWLEDGE_PROJECTS_INVALID")
    if manifest["knowledgeProjects"] and manifest["knowledgeProjects"] != [project_id]:
        raise ValueError("KNOWLEDGE_PROJECTS_INVALID")
    ui_profile = manifest["uiProfile"]
    if not isinstance(ui_profile, dict):
        raise ValueError("PRODUCT_UI_REFERENCE_INVALID")
    require_fields(ui_profile, {"path", "checksum"}, "PRODUCT_UI_REFERENCE_INVALID")
    ui_path = require_string(ui_profile["path"], "PRODUCT_UI_REFERENCE_INVALID")
    ui_checksum = require_string(ui_profile["checksum"], "PRODUCT_UI_REFERENCE_INVALID")
    if (
        not ui_path
        or "\\" in ui_path
        or Path(ui_path).as_posix() != ui_path
        or CHECKSUM_PATTERN.fullmatch(ui_checksum) is None
    ):
        raise ValueError("PRODUCT_UI_REFERENCE_INVALID")
    resolve_inside(path.parent, ui_path, "PRODUCT_UI_REFERENCE_INVALID")
    _validate_remote_services(manifest["remoteServices"])
    return path, manifest


def _validate_remote_services(value: object) -> None:
    fields = {
        "serviceId", "baseUrlEnvironment", "allowedHostEnvironment",
        "credentialSecret", "caBundleSecret", "connectTimeoutSeconds",
        "pollIntervalSeconds", "apiVersion", "allowedServiceVersions",
        "allowedModelVersions", "allowedOperations",
    }
    environment = re.compile(r"^[A-Z][A-Z0-9_]*$")
    identities: set[str] = set()
    if not isinstance(value, list):
        raise ValueError("REMOTE_SERVICES_INVALID")
    for item in value:
        if not isinstance(item, dict) or set(item) != fields:
            raise ValueError("REMOTE_SERVICES_INVALID")
        service_id = item["serviceId"]
        versions = item["allowedServiceVersions"]
        model_versions = item["allowedModelVersions"]
        operations = item["allowedOperations"]
        if (
            not isinstance(service_id, str)
            or NAME_PATTERN.fullmatch(service_id) is None
            or service_id in identities
            or any(
                not isinstance(item[name], str) or not item[name]
                for name in (
                    "baseUrlEnvironment", "allowedHostEnvironment",
                    "credentialSecret", "caBundleSecret", "apiVersion",
                )
            )
            or environment.fullmatch(item["baseUrlEnvironment"]) is None
            or environment.fullmatch(item["allowedHostEnvironment"]) is None
            or type(item["connectTimeoutSeconds"]) is not int
            or not 1 <= item["connectTimeoutSeconds"] <= 120
            or type(item["pollIntervalSeconds"]) is not int
            or not 1 <= item["pollIntervalSeconds"] <= 60
            or not isinstance(versions, list)
            or not versions
            or len(versions) != len(set(versions))
            or any(
                not isinstance(version, str)
                or VERSION_PATTERN.fullmatch(version) is None
                for version in versions
            )
            or not isinstance(model_versions, list)
            or not model_versions
            or len(model_versions) != len(set(model_versions))
            or any(
                not isinstance(version, str) or not version
                for version in model_versions
            )
            or not isinstance(operations, list)
            or not operations
            or len(operations) != len(set(operations))
            or any(
                not isinstance(operation, str)
                or NAME_PATTERN.fullmatch(operation) is None
                for operation in operations
            )
        ):
            raise ValueError("REMOTE_SERVICES_INVALID")
        identities.add(service_id)

def load_domain(directory: Path) -> DomainPackage:
    """Read one immutable declaration package, retaining the exact checked bytes."""
    directory = directory.resolve()
    manifest_path = resolve_inside(directory, "domain-manifest.json", "DOMAIN_PATH_INVALID")
    files = {"domain-manifest.json": manifest_path.read_bytes()}
    try:
        manifest = json.loads(files["domain-manifest.json"])
    except (UnicodeDecodeError, json.JSONDecodeError) as cause:
        raise ValueError("DOMAIN_MANIFEST_INVALID") from cause
    if not isinstance(manifest, dict):
        raise ValueError("DOMAIN_MANIFEST_INVALID")
    version = manifest.get("schemaVersion")
    if version != "domain-manifest.v3":
        raise ValueError("DOMAIN_MANIFEST_VERSION_INVALID")
    fields = DOMAIN_FIELDS_V3
    require_fields(manifest, fields, "DOMAIN_MANIFEST_FIELDS_INVALID")
    for key, pattern in (("domainId", NAME_PATTERN), ("version", VERSION_PATTERN)):
        if not pattern.fullmatch(require_string(manifest[key], "DOMAIN_IDENTITY_INVALID")):
            raise ValueError("DOMAIN_IDENTITY_INVALID")
    instructions = require_string(manifest["instructions"], "DOMAIN_INSTRUCTIONS_INVALID")
    paths = [instructions]
    for key in ("workflowDefinitions", "executableModels", "knowledgeDocuments"):
        paths.extend(_path_list(manifest[key], "DOMAIN_PATH_INVALID"))
    paths.extend(_path_list(manifest["executionCases"], "DOMAIN_PATH_INVALID"))
    resolved_paths = {manifest_path}
    for relative in paths:
        require_string(relative, "DOMAIN_PATH_INVALID")
        path = resolve_inside(directory, relative, "DOMAIN_PATH_INVALID")
        if "\\" in relative or Path(relative).as_posix() != relative or path in resolved_paths:
            raise ValueError("DOMAIN_PATH_INVALID")
        resolved_paths.add(path)
        files[relative] = path.read_bytes()
    try:
        prompt = files[instructions].decode("utf-8")
        definitions = {
            path: json.loads(files[path]) for path in paths if path != instructions
        }
    except (UnicodeDecodeError, json.JSONDecodeError) as cause:
        raise ValueError("DOMAIN_CONTENT_INVALID") from cause
    if not prompt.strip() or any(not isinstance(value, dict) for value in definitions.values()):
        raise ValueError("DOMAIN_CONTENT_INVALID")
    documents = []
    ids: set[str] = set()
    all_sources: dict[str, dict] = {}
    for path in manifest["knowledgeDocuments"]:
        bundle = definitions[path]
        require_fields(
            bundle,
            {"schemaVersion", "contentVersion", "language", "sources", "documents"},
            "DOMAIN_KNOWLEDGE_INVALID",
        )
        if (
            bundle["schemaVersion"] != "domain-knowledge.v2"
            or VERSION_PATTERN.fullmatch(
                require_string(
                    bundle["contentVersion"], "DOMAIN_KNOWLEDGE_INVALID"
                )
            )
            is None
            or LANGUAGE_PATTERN.fullmatch(
                require_string(bundle["language"], "DOMAIN_KNOWLEDGE_INVALID")
            )
            is None
            or not isinstance(bundle["sources"], list)
            or not isinstance(bundle["documents"], list)
        ):
            raise ValueError("DOMAIN_KNOWLEDGE_INVALID")
        local_sources: dict[str, dict] = {}
        for source in bundle["sources"]:
            _validate_knowledge_source(source)
            source_id = source["sourceId"]
            if source_id in local_sources:
                raise ValueError("DOMAIN_KNOWLEDGE_SOURCE_DUPLICATE")
            if source_id in all_sources and all_sources[source_id] != source:
                raise ValueError("DOMAIN_KNOWLEDGE_SOURCE_CONFLICT")
            local_sources[source_id] = source
            all_sources[source_id] = source
        used_sources: set[str] = set()
        for document in bundle["documents"]:
            if not isinstance(document, dict):
                raise ValueError("DOMAIN_KNOWLEDGE_INVALID")
            required = {"knowledgeId", "title", "summary", "chunks"}
            if not required <= document.keys() <= required | {"kind", "aliases", "modelRef"}:
                raise ValueError("DOMAIN_KNOWLEDGE_INVALID")
            if document.get("kind", "knowledge") not in ("knowledge", "definition", "method", "case", "rule"):
                raise ValueError("DOMAIN_KNOWLEDGE_INVALID")
            aliases = document.get("aliases", [])
            if not isinstance(aliases, list) or len(aliases) > 32 or any(
                not isinstance(alias, str) or not alias.strip() or len(alias) > 160
                for alias in aliases
            ):
                raise ValueError("DOMAIN_KNOWLEDGE_INVALID")
            if "modelRef" in document:
                ref = document["modelRef"]
                if not isinstance(ref, dict) or ref.keys() != {"modelId", "version", "checksum"}:
                    raise ValueError("DOMAIN_KNOWLEDGE_MODEL_IDENTITY_INVALID")
                for key, pattern in (
                    ("modelId", NAME_PATTERN), ("version", VERSION_PATTERN), ("checksum", CHECKSUM_PATTERN)
                ):
                    if not isinstance(ref[key], str) or not pattern.fullmatch(ref[key]):
                        raise ValueError("DOMAIN_KNOWLEDGE_MODEL_IDENTITY_INVALID")
            knowledge_id = require_string(document["knowledgeId"], "DOMAIN_KNOWLEDGE_INVALID")
            if (
                len(knowledge_id) > 128
                or not NAME_PATTERN.fullmatch(knowledge_id)
                or knowledge_id in ids
            ):
                raise ValueError("DOMAIN_KNOWLEDGE_ID_INVALID")
            ids.add(knowledge_id)
            for key in ("title", "summary"):
                if not require_string(document[key], "DOMAIN_KNOWLEDGE_INVALID").strip():
                    raise ValueError("DOMAIN_KNOWLEDGE_INVALID")
            if len(document["title"]) > 200 or len(document["summary"]) > 500:
                raise ValueError("DOMAIN_KNOWLEDGE_INVALID")
            chunks = document["chunks"]
            if not isinstance(chunks, list) or not 1 <= len(chunks) <= 64:
                raise ValueError("DOMAIN_KNOWLEDGE_INVALID")
            document_sources: set[str] = set()
            text_size = 0
            for chunk in chunks:
                if not isinstance(chunk, dict):
                    raise ValueError("DOMAIN_KNOWLEDGE_INVALID")
                required = {"content", "sourceRefs"}
                if not required <= chunk.keys() <= required | {"locator", "requiredChunks"}:
                    raise ValueError("DOMAIN_KNOWLEDGE_INVALID")
                locator = chunk.get("locator", {})
                context = chunk.get("requiredChunks", [])
                if (
                    not isinstance(locator, dict) or len(locator) > 8
                    or any(type(value) not in (str, int) for value in locator.values())
                    or not isinstance(context, list) or len(context) > 16
                    or any(type(index) is not int or not 0 <= index < len(chunks) for index in context)
                    or len(context) != len(set(context))
                ):
                    raise ValueError("DOMAIN_KNOWLEDGE_INVALID")
                content_value = require_string(
                    chunk["content"], "DOMAIN_KNOWLEDGE_INVALID"
                )
                refs = chunk["sourceRefs"]
                if (
                    len(content_value) > 4000
                    or not isinstance(refs, list)
                    or not 1 <= len(refs) <= 8
                    or len(refs) != len(set(refs))
                    or any(
                        not isinstance(source_id, str)
                        or source_id not in local_sources
                        for source_id in refs
                    )
                ):
                    raise ValueError("DOMAIN_KNOWLEDGE_INVALID")
                text_size += len(content_value)
                document_sources.update(refs)
            if text_size > 256_000 or not 1 <= len(document_sources) <= 16:
                raise ValueError("DOMAIN_KNOWLEDGE_INVALID")
            used_sources.update(document_sources)
            documents.append(document)
        if used_sources != set(local_sources):
            raise ValueError("DOMAIN_KNOWLEDGE_INVALID")
    execution_cases = tuple(
        definitions[path] for path in manifest.get("executionCases", [])
    )
    _validate_execution_cases(execution_cases)
    return DomainPackage(
        directory, manifest, files, definitions, prompt, tuple(documents),
        execution_cases,
    )

def _validate_execution_cases(cases: tuple[dict, ...]) -> None:
    identities: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("DOMAIN_EXECUTION_CASE_INVALID")
        require_fields(case, {
            "schemaVersion", "caseId", "aliases", "modelRef", "datasetTag",
            "inputRoles", "defaultProfile", "profiles",
        }, "DOMAIN_EXECUTION_CASE_INVALID")
        case_id = require_string(case["caseId"], "DOMAIN_EXECUTION_CASE_INVALID")
        if (
            case["schemaVersion"] != "execution-case.v1"
            or NAME_PATTERN.fullmatch(case_id) is None
            or case_id in identities
        ):
            raise ValueError("DOMAIN_EXECUTION_CASE_INVALID")
        identities.add(case_id)
        aliases = case["aliases"]
        if (
            not isinstance(aliases, list) or not aliases
            or any(not isinstance(alias, str) or not alias.strip() for alias in aliases)
            or len(set(aliases)) != len(aliases)
        ):
            raise ValueError("DOMAIN_EXECUTION_CASE_INVALID")
        model = case["modelRef"]
        if not isinstance(model, dict):
            raise ValueError("DOMAIN_EXECUTION_CASE_INVALID")
        require_fields(model, {"modelId", "version"}, "DOMAIN_EXECUTION_CASE_INVALID")
        if (
            NAME_PATTERN.fullmatch(require_string(model["modelId"], "DOMAIN_EXECUTION_CASE_INVALID")) is None
            or VERSION_PATTERN.fullmatch(require_string(model["version"], "DOMAIN_EXECUTION_CASE_INVALID")) is None
        ):
            raise ValueError("DOMAIN_EXECUTION_CASE_INVALID")
        require_string(case["datasetTag"], "DOMAIN_EXECUTION_CASE_INVALID")
        roles = case["inputRoles"]
        if not isinstance(roles, list) or not roles:
            raise ValueError("DOMAIN_EXECUTION_CASE_INVALID")
        names: set[str] = set()
        parameters: set[str] = set()
        for role in roles:
            if not isinstance(role, dict):
                raise ValueError("DOMAIN_EXECUTION_CASE_INVALID")
            require_fields(role, {"role", "parameter"}, "DOMAIN_EXECUTION_CASE_INVALID")
            name = require_string(role["role"], "DOMAIN_EXECUTION_CASE_INVALID")
            parameter = require_string(role["parameter"], "DOMAIN_EXECUTION_CASE_INVALID")
            if name in names or parameter in parameters:
                raise ValueError("DOMAIN_EXECUTION_CASE_INVALID")
            names.add(name)
            parameters.add(parameter)
        profiles = case["profiles"]
        if (
            not isinstance(profiles, dict) or not profiles
            or case["defaultProfile"] not in profiles
            or any(not isinstance(value, dict) for value in profiles.values())
        ):
            raise ValueError("DOMAIN_EXECUTION_CASE_INVALID")

def _validate_knowledge_source(source: object) -> None:
    if not isinstance(source, dict):
        raise ValueError("DOMAIN_KNOWLEDGE_INVALID")
    require_fields(
        source,
        {
            "sourceId", "kind", "title", "publisher", "url", "version",
            "publishedAt", "accessedAt", "reviewAfter", "validThrough",
            "lifecycleStatus",
        },
        "DOMAIN_KNOWLEDGE_INVALID",
    )
    source_id = require_string(source["sourceId"], "DOMAIN_KNOWLEDGE_INVALID")
    title = require_string(source["title"], "DOMAIN_KNOWLEDGE_INVALID")
    publisher = require_string(source["publisher"], "DOMAIN_KNOWLEDGE_INVALID")
    if (
        SOURCE_ID_PATTERN.fullmatch(source_id) is None
        or len(title) > 300
        or len(publisher) > 200
        or source["kind"] not in {
            "OFFICIAL_STANDARD", "OFFICIAL_GUIDANCE", "OFFICIAL_POLICY",
            "DOMAIN_DEFINITION", "DEMO_ASSUMPTION", "PROJECT_TEMPLATE", "RESEARCH_PAPER",
        }
        or source["lifecycleStatus"] not in {"ACTIVE", "WITHDRAWN"}
    ):
        raise ValueError("DOMAIN_KNOWLEDGE_INVALID")
    version = source["version"]
    if version is not None and (
        not isinstance(version, str) or not version or len(version) > 100
    ):
        raise ValueError("DOMAIN_KNOWLEDGE_INVALID")
    external = source["kind"].startswith("OFFICIAL_") or source["kind"] == "RESEARCH_PAPER"
    url = source["url"]
    accessed = source["accessedAt"]
    if external:
        if (
            not isinstance(url, str)
            or len(url) > 2048
            or urlsplit(url).scheme != "https"
            or not isinstance(accessed, str)
        ):
            raise ValueError("DOMAIN_KNOWLEDGE_INVALID")
    elif url is not None:
        raise ValueError("DOMAIN_KNOWLEDGE_INVALID")
    parsed = {}
    for field in ("publishedAt", "accessedAt", "reviewAfter", "validThrough"):
        value = source[field]
        if value is None:
            parsed[field] = None
            continue
        if not isinstance(value, str):
            raise ValueError("DOMAIN_KNOWLEDGE_INVALID")
        try:
            parsed[field] = date.fromisoformat(value)
        except ValueError as cause:
            raise ValueError("DOMAIN_KNOWLEDGE_INVALID") from cause
    if parsed["reviewAfter"] is None:
        raise ValueError("DOMAIN_KNOWLEDGE_INVALID")
    if (
        parsed["publishedAt"] is not None
        and parsed["accessedAt"] is not None
        and parsed["publishedAt"] > parsed["accessedAt"]
    ):
        raise ValueError("DOMAIN_KNOWLEDGE_INVALID")
    if (
        source["lifecycleStatus"] == "ACTIVE"
        and parsed["validThrough"] is not None
        and parsed["accessedAt"] is not None
        and parsed["validThrough"] < parsed["accessedAt"]
    ):
        raise ValueError("DOMAIN_KNOWLEDGE_INVALID")

def _selected_domain(root: Path, reference: dict) -> DomainPackage:
    path = resolve_inside(
        root / "domains",
        f"{reference['domainId']}/{reference['version']}/domain-manifest.json",
        "DOMAIN_PATH_INVALID",
    )
    domain = load_domain(path.parent)
    if domain.identity != reference:
        raise ValueError("DOMAIN_IDENTITY_MISMATCH")
    return domain

def _validate_process(value: object) -> tuple[str, ...]:
    if not isinstance(value, dict) or set(value) != {"command"}:
        raise ValueError("MODULE_CONTRACT_INVALID")
    command = value["command"]
    if (
        not isinstance(command, list)
        or not command
        or any(not isinstance(item, str) or not item for item in command)
    ):
        raise ValueError("MODULE_CONTRACT_INVALID")
    return tuple(command)

def _validate_health(value: object) -> str:
    if not isinstance(value, dict) or set(value) != {"live", "ready"}:
        raise ValueError("MODULE_CONTRACT_INVALID")
    if any(
        not isinstance(path, str) or not path.startswith("/internal/health/")
        for path in value.values()
    ):
        raise ValueError("MODULE_CONTRACT_INVALID")
    return value["ready"]

def _validate_paths(value: object) -> tuple[str, ...]:
    if not isinstance(value, dict) or set(value) - {"authority", "paths"}:
        raise ValueError("MODULE_CONTRACT_INVALID")
    paths = value.get("paths")
    if (
        not isinstance(paths, list)
        or not paths
        or any(not isinstance(path, str) or not path.startswith("/") for path in paths)
    ):
        raise ValueError("MODULE_CONTRACT_INVALID")
    return tuple(paths)

def _load_module(path: Path) -> ModuleContract:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as cause:
        raise ValueError("MODULE_CONTRACT_INVALID") from cause
    if not isinstance(raw, dict):
        raise ValueError("MODULE_CONTRACT_INVALID")
    require_fields(raw, MODULE_FIELDS, "MODULE_CONTRACT_INVALID")
    if raw["apiVersion"] != CURRENT_MODULE_API_VERSION or raw["kind"] != "Service":
        raise ValueError("MODULE_CONTRACT_INVALID")
    metadata, spec = raw["metadata"], raw["spec"]
    if not isinstance(metadata, dict) or set(metadata) != {
        "name", "version", "distribution"
    }:
        raise ValueError("MODULE_CONTRACT_INVALID")
    if not isinstance(spec, dict) or set(spec) not in (
        MODULE_SPEC_FIELDS,
        MODULE_SPEC_FIELDS | {"temporary"},
    ):
        raise ValueError("MODULE_CONTRACT_INVALID")
    name = require_string(metadata["name"], "MODULE_CONTRACT_INVALID")
    if (
        not NAME_PATTERN.fullmatch(name)
        or VERSION_PATTERN.fullmatch(
            require_string(metadata["version"], "MODULE_CONTRACT_INVALID")
        ) is None
    ):
        raise ValueError("MODULE_CONTRACT_INVALID")
    distribution = metadata["distribution"]
    if not isinstance(distribution, dict) or set(distribution) != {"name", "version"}:
        raise ValueError("MODULE_CONTRACT_INVALID")
    distribution_name = require_string(
        distribution["name"], "MODULE_CONTRACT_INVALID"
    )
    distribution_version = require_string(
        distribution["version"], "MODULE_CONTRACT_INVALID"
    )
    if (
        NAME_PATTERN.fullmatch(distribution_name) is None
        or VERSION_PATTERN.fullmatch(distribution_version) is None
    ):
        raise ValueError("MODULE_CONTRACT_INVALID")
    http_contracts = _load_http_contracts(path, spec["httpContracts"])
    tool_contracts = _load_observation_tool_contracts(
        path, spec["observationToolContracts"]
    )
    declared_contracts = set(http_contracts) | set(tool_contracts)
    provides = _string_set(spec["provides"], "MODULE_CONTRACT_INVALID")
    if not declared_contracts <= provides:
        raise ValueError("MODULE_CONTRACT_INVALID")
    storage = spec["storage"]
    if not isinstance(storage, dict) or set(storage) != {"authority", "paths"}:
        raise ValueError("MODULE_CONTRACT_INVALID")
    require_string(storage["authority"], "MODULE_CONTRACT_INVALID")
    return ModuleContract(
        name=name,
        version=metadata["version"],
        path=path,
        api_version=raw["apiVersion"],
        distribution_name=distribution_name,
        distribution_version=distribution_version,
        provides=provides,
        requires=_string_set(spec["requires"], "MODULE_CONTRACT_INVALID"),
        http_contracts=http_contracts,
        observation_tool_contracts=tool_contracts,
        executors=_string_set(spec["executors"], "MODULE_CONTRACT_INVALID"),
        command=_validate_process(spec["process"]),
        ready_path=_validate_health(spec["health"]),
        storage_paths=_validate_paths(storage),
        temporary_paths=_validate_paths(spec["temporary"])
        if "temporary" in spec
        else (),
    )


def _artifact_path(module_path: Path, value: object, error: str) -> Path:
    return resolve_inside(module_path.parent, require_string(value, error), error)


def _artifact_checksum(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _load_http_contracts(
    module_path: Path, value: object
) -> dict[str, HttpContract]:
    if not isinstance(value, dict):
        raise ValueError("MODULE_CONTRACT_INVALID")
    contracts: dict[str, HttpContract] = {}
    for contract_id, declaration in value.items():
        if (
            not isinstance(contract_id, str)
            or NAME_PATTERN.fullmatch(contract_id) is None
            or not isinstance(declaration, dict)
            or set(declaration) != {"basePath", "openapi"}
        ):
            raise ValueError("MODULE_CONTRACT_INVALID")
        base_path = require_string(
            declaration["basePath"], "MODULE_CONTRACT_INVALID"
        )
        if not base_path.startswith("/") or base_path.endswith("/"):
            raise ValueError("MODULE_CONTRACT_INVALID")
        snapshot_path = _artifact_path(
            module_path, declaration["openapi"], "MODULE_HTTP_CONTRACT_INVALID"
        )
        document = load_json(snapshot_path, "MODULE_HTTP_CONTRACT_INVALID")
        if (
            not isinstance(document.get("openapi"), str)
            or not isinstance(document.get("paths"), dict)
            or not any(
                route == base_path or route.startswith(base_path + "/")
                for route in document["paths"]
            )
        ):
            raise ValueError("MODULE_HTTP_CONTRACT_INVALID")
        contracts[contract_id] = HttpContract(
            contract_id, base_path, snapshot_path,
            _artifact_checksum(snapshot_path), document,
        )
    return contracts


def _load_observation_tool_contracts(
    module_path: Path, value: object
) -> dict[str, ObservationToolContract]:
    if not isinstance(value, dict):
        raise ValueError("MODULE_CONTRACT_INVALID")
    contracts: dict[str, ObservationToolContract] = {}
    for contract_id, declaration in value.items():
        if (
            not isinstance(contract_id, str)
            or NAME_PATTERN.fullmatch(contract_id) is None
            or not isinstance(declaration, dict)
            or set(declaration) != {"file"}
        ):
            raise ValueError("MODULE_CONTRACT_INVALID")
        contract_path = _artifact_path(
            module_path, declaration["file"], "MODULE_TOOL_CONTRACT_INVALID"
        )
        document = load_json(contract_path, "MODULE_TOOL_CONTRACT_INVALID")
        if set(document) != {"schemaVersion", "contract", "tools"} or document.get(
            "contract"
        ) != contract_id or not isinstance(document.get("tools"), list):
            raise ValueError("MODULE_TOOL_CONTRACT_INVALID")
        tools: dict[str, dict] = {}
        for tool in document["tools"]:
            if not isinstance(tool, dict) or set(tool) != {
                "name", "path", "description", "inputSchema", "outputSchema",
                "timeoutSeconds", "progress",
            }:
                raise ValueError("MODULE_TOOL_CONTRACT_INVALID")
            name = require_string(tool["name"], "MODULE_TOOL_CONTRACT_INVALID")
            route = require_string(tool["path"], "MODULE_TOOL_CONTRACT_INVALID")
            timeout = tool["timeoutSeconds"]
            if (
                TOOL_PATTERN.fullmatch(name) is None
                or re.fullmatch(
                    rf"/internal/tools/v[0-9]+/{re.escape(name)}", route
                ) is None
                or name in tools
                or isinstance(timeout, bool)
                or not isinstance(timeout, int)
                or timeout <= 0
            ):
                raise ValueError("MODULE_TOOL_CONTRACT_INVALID")
            require_string(tool["description"], "MODULE_TOOL_CONTRACT_INVALID")
            _validate_schema(tool["inputSchema"], "MODULE_TOOL_CONTRACT_INVALID")
            _validate_schema(tool["outputSchema"], "MODULE_TOOL_CONTRACT_INVALID")
            _validate_tool_progress(tool["progress"])
            tools[name] = tool
        contracts[contract_id] = ObservationToolContract(
            contract_id, contract_path, _artifact_checksum(contract_path), tools
        )
    return contracts


def _validate_tool_progress(value: object) -> None:
    if not isinstance(value, dict):
        raise ValueError("MODULE_TOOL_PROGRESS_INVALID")
    require_fields(value, {
        "capability", "semanticRole", "templateKey", "title",
        "runningSummaryTemplate", "processingTitle",
        "processingSummaryTemplate", "outcomes",
    }, "MODULE_TOOL_PROGRESS_INVALID")
    for key in ("capability", "templateKey"):
        if NAME_PATTERN.fullmatch(
            require_string(value[key], "MODULE_TOOL_PROGRESS_INVALID")
        ) is None:
            raise ValueError("MODULE_TOOL_PROGRESS_INVALID")
    role = require_string(value["semanticRole"], "MODULE_TOOL_PROGRESS_INVALID")
    title = require_string(value["title"], "MODULE_TOOL_PROGRESS_INVALID")
    running = require_string(
        value["runningSummaryTemplate"], "MODULE_TOOL_PROGRESS_INVALID"
    )
    processing_title = require_string(
        value["processingTitle"], "MODULE_TOOL_PROGRESS_INVALID"
    )
    processing = require_string(
        value["processingSummaryTemplate"], "MODULE_TOOL_PROGRESS_INVALID"
    )
    if (
        re.fullmatch(r"[A-Z][A-Z0-9_]*", role) is None
        or not title.strip()
        or not running.strip()
        or not processing_title.strip()
        or "{" in processing_title
        or "}" in processing_title
        or _template_fields(running) != ("objective",)
        or _template_fields(processing) != ("objective",)
    ):
        raise ValueError("MODULE_TOOL_PROGRESS_INVALID")
    outcomes = value["outcomes"]
    if not isinstance(outcomes, dict) or not outcomes:
        raise ValueError("MODULE_TOOL_PROGRESS_INVALID")
    for code, outcome in outcomes.items():
        if (
            not isinstance(code, str)
            or NAME_PATTERN.fullmatch(code) is None
            or not isinstance(outcome, dict)
        ):
            raise ValueError("MODULE_TOOL_PROGRESS_INVALID")
        require_fields(
            outcome, {"summary", "factsSchema"}, "MODULE_TOOL_PROGRESS_INVALID"
        )
        summary = require_string(outcome["summary"], "MODULE_TOOL_PROGRESS_INVALID")
        schema = outcome["factsSchema"]
        _validate_schema(schema, "MODULE_TOOL_PROGRESS_INVALID")
        properties = schema.get("properties") if isinstance(schema, dict) else None
        required = schema.get("required") if isinstance(schema, dict) else None
        if (
            not isinstance(properties, dict)
            or not isinstance(required, list)
            or schema.get("type") != "object"
            or schema.get("additionalProperties") is not False
            or set(properties) != set(required)
            or set(_template_fields(summary)) != set(properties)
        ):
            raise ValueError("MODULE_TOOL_PROGRESS_INVALID")


def _template_fields(template: str) -> tuple[str, ...]:
    fields: list[str] = []
    try:
        parsed = Formatter().parse(template)
        for _literal, name, format_spec, conversion in parsed:
            if name is None:
                continue
            if not name or "." in name or "[" in name or format_spec or conversion:
                raise ValueError("MODULE_TOOL_PROGRESS_INVALID")
            fields.append(name)
    except ValueError as error:
        raise ValueError("MODULE_TOOL_PROGRESS_INVALID") from error
    return tuple(fields)

def _load_modules(
    root: Path, *, api_version: str | None = CURRENT_MODULE_API_VERSION
) -> dict[str, ModuleContract]:
    paths = [
        root / "kernel" / "module.yaml",
        *sorted((root / "services").glob("*/module.yaml")),
    ]
    modules: dict[str, ModuleContract] = {}
    providers: dict[str, str] = {}
    for path in paths:
        module = _load_module(path)
        if api_version is not None and module.api_version != api_version:
            continue
        if module.name in modules:
            raise ValueError("MODULE_NAME_CONFLICT")
        for capability in module.provides:
            if capability in providers:
                raise ValueError("MODULE_CAPABILITY_PROVIDER_CONFLICT")
            providers[capability] = module.name
        modules[module.name] = module
    if "kernel" not in modules:
        raise ValueError("KERNEL_MODULE_MISSING")
    return modules

def available_products(root: Path) -> tuple[str, ...]:
    return tuple(
        sorted(
            path.parent.name
            for path in (root / "products").glob("*/product-manifest.json")
        )
    )
