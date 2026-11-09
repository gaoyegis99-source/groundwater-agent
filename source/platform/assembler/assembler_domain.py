from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from platform_workflow_compiler import compile_workflow as _compile_workflow

from assembler_common import (
    CHECKSUM_PATTERN, CURRENT_MODULE_API_VERSION, EXTERNAL_CAPABILITIES,
    MODEL_FIELDS, NAME_PATTERN, TOOL_FIELDS, TOOL_PATTERN, VERSION_PATTERN,
    DomainPackage, ModuleContract, ServiceBinding, _canonical, _checksum, _string_set,
    _validate_schema, _validate_value, load_json, require_fields, require_string,
)

def _compile_services(
    value: object, modules: dict[str, ModuleContract]
) -> tuple[dict[str, ServiceBinding], dict[str, ModuleContract]]:
    names = _string_set(value, "PRODUCT_SERVICES_INVALID")
    if not names or "kernel" in names or "auth" in names:
        raise ValueError("PRODUCT_SERVICES_INVALID")
    if "data" not in names:
        raise ValueError("PRODUCT_DATA_SERVICE_REQUIRED")
    try:
        selected = {name: modules[name] for name in sorted(names)}
    except KeyError as cause:
        raise ValueError("PRODUCT_SERVICE_UNKNOWN") from cause
    if any(
        module.api_version != CURRENT_MODULE_API_VERSION
        for module in selected.values()
    ):
        raise ValueError("PRODUCT_SERVICE_MODULE_VERSION_INVALID")
    providers = {
        capability: module.name
        for module in selected.values()
        for capability in module.provides
    }
    for module in selected.values():
        for requirement in module.requires:
            if (
                requirement not in providers
                and requirement not in EXTERNAL_CAPABILITIES
            ):
                raise ValueError("PRODUCT_SERVICE_DEPENDENCY_MISSING")
    bindings = {
        name: ServiceBinding(
            name=name,
            internal_base_url="http://executor-control:8000"
            if name == "executor"
            else f"http://{name}-service:8000",
        )
        for name in selected
    }
    return bindings, selected

def _compile_tools(
    value: object,
    services: dict[str, ServiceBinding],
    modules: dict[str, ModuleContract],
) -> list[dict]:
    if not isinstance(value, list):
        raise ValueError("PRODUCT_TOOLS_INVALID")
    tools: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("PRODUCT_TOOLS_INVALID")
        require_fields(item, TOOL_FIELDS, "PRODUCT_TOOLS_INVALID")
        service = item["service"]
        contract_id = item["contract"]
        name = require_string(item["tool"], "PRODUCT_TOOLS_INVALID")
        if (
            not TOOL_PATTERN.fullmatch(name)
            or service not in services
            or contract_id
            not in modules[service].observation_tool_contracts
            or name
            not in modules[service].observation_tool_contracts[contract_id].tools
        ):
            raise ValueError("PRODUCT_TOOL_UNSUPPORTED")
        owned = modules[service].observation_tool_contracts[contract_id].tools[name]
        tools.append(
            {
                **owned,
                "service": service,
                "contract": contract_id,
                "inputSchemaChecksum": _checksum(owned["inputSchema"]),
                "outputSchemaChecksum": _checksum(owned["outputSchema"]),
            }
        )
    names = [item["name"] for item in tools]
    if len(names) != len(set(names)):
        raise ValueError("PRODUCT_TOOL_NAMES_INVALID")
    return tools

def _catalog_root(root: Path) -> Path:
    path = root / "packages" / "operator-pack" / "catalog"
    if not path.is_dir():
        raise ValueError("OPERATOR_CATALOG_MISSING")
    return path

def _load_operator_catalog(root: Path) -> tuple[dict[tuple[str, str], dict], str]:
    catalog_root = _catalog_root(root)
    pack_root = catalog_root.parent
    lock = load_json(catalog_root / "implementation-lock.json", "OPERATOR_LOCK_INVALID")
    if lock.get("schemaVersion") != "operator-implementation-lock.v1" or not isinstance(
        lock.get("operators"), dict
    ):
        raise ValueError("OPERATOR_LOCK_INVALID")
    operators: dict[tuple[str, str], dict] = {}
    for path in sorted((catalog_root / "operators").glob("*/*/manifest.json")):
        raw = load_json(path, "OPERATOR_MANIFEST_INVALID")
        key = (
            require_string(raw.get("id"), "OPERATOR_MANIFEST_INVALID"),
            require_string(raw.get("version"), "OPERATOR_MANIFEST_INVALID"),
        )
        if key in operators:
            raise ValueError("OPERATOR_IDENTITY_DUPLICATE")
        locked = lock["operators"].get(f"{key[0]}@{key[1]}")
        if not isinstance(locked, dict) or not CHECKSUM_PATTERN.fullmatch(
            str(locked.get("implementationRevision", ""))
        ):
            raise ValueError("OPERATOR_IMPLEMENTATION_MISSING")
        if raw.get("runtime") not in {"native", "qgis", "remote_http"}:
            raise ValueError("OPERATOR_RUNTIME_INVALID")
        remote_binding = raw.get("remoteBinding")
        if (raw.get("runtime") == "remote_http") != (
            isinstance(remote_binding, dict)
            and set(remote_binding) == {"serviceId", "operationType"}
            and all(
                isinstance(value, str) and value
                for value in remote_binding.values()
            )
        ):
            raise ValueError("OPERATOR_REMOTE_BINDING_INVALID")
        source_files = locked.get("sourceFiles")
        source_digest = locked.get("sourceDigest")
        if (
            not isinstance(source_files, list)
            or not source_files
            or not CHECKSUM_PATTERN.fullmatch(str(source_digest or ""))
        ):
            raise ValueError("OPERATOR_IMPLEMENTATION_LOCK_INVALID")
        digest_builder = hashlib.sha256()
        for relative in source_files:
            if not isinstance(relative, str):
                raise ValueError("OPERATOR_IMPLEMENTATION_LOCK_INVALID")
            source = (pack_root / relative).resolve()
            if not source.is_relative_to(pack_root.resolve()) or not source.is_file():
                raise ValueError("OPERATOR_IMPLEMENTATION_LOCK_INVALID")
            digest_builder.update(relative.encode("utf-8"))
            digest_builder.update(b"\0")
            digest_builder.update(source.read_bytes())
        if "sha256:" + digest_builder.hexdigest() != source_digest:
            raise ValueError("OPERATOR_IMPLEMENTATION_DRIFT")
        revision = "sha256:" + hashlib.sha256(
            _canonical(raw) + source_digest.encode("ascii")
        ).hexdigest()
        if locked["implementationRevision"] != revision:
            raise ValueError("OPERATOR_IMPLEMENTATION_DRIFT")
        operators[key] = {
            **raw,
            "implementationRevision": locked["implementationRevision"],
        }
    if len(operators) != 106:
        raise ValueError("OPERATOR_CATALOG_SIZE_INVALID")
    mapping_path = pack_root / "src" / "moperators" / "static_implementations.py"
    tree = ast.parse(
        mapping_path.read_text(encoding="utf-8"), filename=str(mapping_path)
    )
    mapping = next(
        (
            node.value
            for node in tree.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "IMPLEMENTATIONS"
        ),
        None,
    )
    if not isinstance(mapping, ast.Dict):
        raise ValueError("OPERATOR_STATIC_MAPPING_INVALID")
    try:
        mapped = {ast.literal_eval(key) for key in mapping.keys}
    except (TypeError, ValueError) as cause:
        raise ValueError("OPERATOR_STATIC_MAPPING_INVALID") from cause
    if mapped != set(operators):
        raise ValueError("OPERATOR_STATIC_MAPPING_INVALID")
    digest = {
        "operators": {
            f"{key[0]}@{key[1]}": {
                "manifest": value,
                "implementationRevision": value["implementationRevision"],
            }
            for key, value in sorted(operators.items())
        },
        "staticMappingChecksum": "sha256:"
        + hashlib.sha256(mapping_path.read_bytes()).hexdigest(),
    }
    return operators, _checksum(digest)

def _compile_workflows(
    domain: DomainPackage, operators: dict[tuple[str, str], dict]
) -> list[dict]:
    workflows: list[dict] = []
    identities: set[tuple[str, str]] = set()
    for relative in domain.manifest["workflowDefinitions"]:
        raw = domain.definitions[relative]
        compiled = _compile_workflow(raw, operators)
        identity = compiled["workflowId"], compiled["version"]
        if identity in identities:
            raise ValueError("WORKFLOW_IDS_INVALID")
        identities.add(identity)
        workflows.append(compiled)
    return workflows

def _compile_models(
    domain: DomainPackage,
    workflows: dict[tuple[str, str], dict],
    services: dict[str, dict[str, str]],
    modules: dict[str, ModuleContract],
    occupied_tools: set[str],
) -> list[dict]:
    models: list[dict] = []
    identities: set[tuple[str, str]] = set()
    for relative in domain.manifest["executableModels"]:
        raw = domain.definitions[relative]
        require_fields(raw, MODEL_FIELDS, "EXECUTABLE_MODEL_FIELDS_INVALID")
        if raw["schemaVersion"] != "executable-model.v4":
            raise ValueError("EXECUTABLE_MODEL_VERSION_INVALID")
        identity = (
            require_string(raw["modelId"], "EXECUTABLE_MODEL_ID_INVALID"),
            require_string(raw["version"], "EXECUTABLE_MODEL_ID_INVALID"),
        )
        if (
            not NAME_PATTERN.fullmatch(identity[0])
            or not VERSION_PATTERN.fullmatch(identity[1])
            or identity in identities
        ):
            raise ValueError("EXECUTABLE_MODEL_ID_INVALID")
        identities.add(identity)
        reference = raw["workflowRef"]
        if not isinstance(reference, dict) or set(reference) != {
            "workflowId",
            "version",
            "checksum",
        }:
            raise ValueError("MODEL_WORKFLOW_REF_INVALID")
        workflow = workflows.get((reference["workflowId"], reference["version"]))
        if workflow is None or reference["checksum"] != workflow["definitionChecksum"]:
            raise ValueError("MODEL_WORKFLOW_CHECKSUM_MISMATCH")
        tool = raw["tool"]
        if not isinstance(tool, dict) or set(tool) != {
            "name",
            "description",
            "inputSchema",
        }:
            raise ValueError("MODEL_TOOL_INVALID")
        tool_name = require_string(tool["name"], "MODEL_TOOL_INVALID")
        if not TOOL_PATTERN.fullmatch(tool_name) or tool_name in occupied_tools:
            raise ValueError("MODEL_TOOL_INVALID")
        occupied_tools.add(tool_name)
        _validate_schema(tool["inputSchema"], "MODEL_INPUT_SCHEMA_INVALID")
        input_bindings = raw["inputBindings"]
        workflow_inputs = {item["name"] for item in workflow["inputs"]}
        if (
            not isinstance(input_bindings, dict)
            or set(input_bindings) != workflow_inputs
            or any(not isinstance(value, str) for value in input_bindings.values())
            or not set(input_bindings.values())
            <= set(tool["inputSchema"]["properties"])
        ):
            raise ValueError("MODEL_INPUT_BINDING_INVALID")
        defaults, bindings = raw["defaultParameters"], raw["parameterBindings"]
        if (
            not isinstance(defaults, dict)
            or not isinstance(bindings, dict)
            or set(bindings) != set(workflow["parameters"])
            or any(not isinstance(value, str) for value in bindings.values())
        ):
            raise ValueError("MODEL_PARAMETER_BINDING_INVALID")
        schema_properties = tool["inputSchema"]["properties"]
        if not set(defaults) <= set(schema_properties) or not set(
            bindings.values()
        ) <= set(schema_properties):
            raise ValueError("MODEL_PARAMETER_BINDING_INVALID")
        for name, value in defaults.items():
            _validate_value(
                schema_properties[name], value, "MODEL_PARAMETER_DEFAULT_INVALID"
            )
        for node in workflow["nodes"]:
            for parameter, binding in node["parameters"].items():
                argument = bindings[binding["workflowParameter"]]
                if argument in defaults:
                    _validate_value(
                        node["parameterSchema"]["properties"][parameter],
                        defaults[argument],
                        "MODEL_PARAMETER_DEFAULT_INVALID",
                    )
        available_arguments = set(tool["inputSchema"]["required"]) | set(defaults)
        if not set(input_bindings.values()) <= available_arguments:
            raise ValueError("MODEL_INPUT_BINDING_INVALID")
        required_workflow_parameters = {
            binding["workflowParameter"]
            for node in workflow["nodes"]
            for parameter, binding in node["parameters"].items()
            if parameter in node["parameterSchema"].get("required", [])
        }
        if not {
            bindings[name] for name in required_workflow_parameters
        } <= available_arguments:
            raise ValueError("MODEL_PARAMETER_BINDING_INVALID")
        deadline, parallel = raw["deadlineSeconds"], raw["maxParallelNodes"]
        if (
            isinstance(deadline, bool)
            or not isinstance(deadline, int)
            or deadline <= 0
            or isinstance(parallel, bool)
            or not isinstance(parallel, int)
            or parallel <= 0
        ):
            raise ValueError("MODEL_EXECUTION_POLICY_INVALID")
        workflow_outputs = {item["name"] for item in workflow["outputs"]}
        final_outputs = raw["finalOutputs"]
        if (
            not isinstance(final_outputs, list)
            or not final_outputs
            or len(final_outputs) != len(set(final_outputs))
            or not set(final_outputs) <= workflow_outputs
        ):
            raise ValueError("MODEL_FINAL_OUTPUT_INVALID")
        publications = raw["outputPublications"]
        if (
            not isinstance(publications, dict)
            or set(publications) != set(final_outputs)
            or any(
                not isinstance(item, dict)
                or set(item) != {
                    "title", "mediaType", "format", "policy", "semantics"
                }
                or not isinstance(item["title"], str)
                or not item["title"]
                or not isinstance(item["mediaType"], str)
                or not item["mediaType"]
                or item["policy"] not in {
                    "FILE_ONLY", "FILE_QUERYABLE", "FILE_AND_METRICS"
                }
                or not isinstance(item["semantics"], dict)
                for item in publications.values()
            )
        ):
            raise ValueError("MODEL_OUTPUT_PUBLICATION_INVALID")
        service = raw["executionService"]
        if (
            service not in services
            or "execution-http-v6" not in modules[service].provides
            or raw["executorId"] not in modules[service].executors
        ):
            raise ValueError("MODEL_EXECUTOR_INVALID")
        models.append(
            {
                **raw,
                "modelChecksum": _checksum(raw),
                "inputSchemaChecksum": _checksum(tool["inputSchema"]),
                "workflow": workflow,
            }
        )
    return models

def _compile_domain(domain: DomainPackage, services: dict, modules: dict, operators: dict) -> tuple[list, list, list]:
    if domain.knowledge_documents and "knowledge-next" not in modules:
        raise ValueError("DOMAIN_KNOWLEDGE_SERVICE_MISSING")
    tools = _compile_tools(domain.manifest["observationTools"], services, modules)
    workflows = _compile_workflows(domain, operators)
    workflow_index = {(item["workflowId"], item["version"]): item for item in workflows}
    models = _compile_models(
        domain,
        workflow_index,
        services,
        modules,
        {item["name"] for item in tools},
    )
    model_index = {(item["modelId"], item["version"]): item for item in models}
    for case in domain.execution_cases:
        model_ref = case["modelRef"]
        model = model_index.get((model_ref["modelId"], model_ref["version"]))
        if model is None:
            raise ValueError("DOMAIN_EXECUTION_CASE_MODEL_INVALID")
        expected = set(model["inputBindings"].values())
        actual = {item["parameter"] for item in case["inputRoles"]}
        if actual != expected:
            raise ValueError("DOMAIN_EXECUTION_CASE_INPUTS_INVALID")
        for profile in case["profiles"].values():
            if set(profile) - set(model["tool"]["inputSchema"]["properties"]):
                raise ValueError("DOMAIN_EXECUTION_CASE_PROFILE_INVALID")
            _validate_value(model["tool"]["inputSchema"], {
                **model["defaultParameters"],
                **profile,
                **{parameter: "version-placeholder" for parameter in expected},
            }, "DOMAIN_EXECUTION_CASE_PROFILE_INVALID")
    if workflows and (
        "executor" not in modules or "data" not in modules
    ):
        raise ValueError("MODEL_SERVICE_DEPENDENCY_MISSING")
    return tools, workflows, models
