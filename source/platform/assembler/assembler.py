from __future__ import annotations

import hashlib
import json
import os  # noqa: F401 - retained for the stable assembler test/patch surface
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from platform_workflow_compiler import compile_workflow as _compile_workflow  # noqa: F401

from assembler_common import (
    AssemblyIR, CURRENT_MODULE_API_VERSION, DomainPackage, ProductContract,
    _canonical, _checksum,
)
from assembler_domain import (
    _compile_domain, _compile_services, _load_operator_catalog,
)
from assembler_gateway import _compile_routes, _nginx_config
from assembler_manifests import (
    _load_manifest, _load_modules, _selected_domain,
    available_products as available_products,
    load_domain,
)
from assembler_publication import (
    _bundle_pointer_identity,
    _publish_bundle,
    bundle_pointer_path,
    generated_root,
)
from assembler_runtime import _compose, _worker_runtime_config
from executor_profile import ExecutorOperationalProfile
from assembler_ui import load_product_ui

def compile_product(
    root: Path,
    manifest_path: Path | str,
    *,
    executor_profile: ExecutorOperationalProfile | None = None,
) -> Path:
    root = root.resolve()
    product_manifest_path, manifest = _load_manifest(root, manifest_path)
    product = ProductContract.from_document(manifest)
    generated = generated_root(root)
    baseline = _bundle_pointer_identity(bundle_pointer_path(generated, product.product_id))
    domain = _selected_domain(root, product.domain_package)
    projects = list(product.knowledge_projects)
    if bool(domain.knowledge_documents) != bool(projects):
        raise ValueError("KNOWLEDGE_PROJECTS_INVALID")
    modules = _load_modules(root)
    services, selected_modules = _compile_services(product.service_names, modules)
    operators, catalog_digest = _load_operator_catalog(root)
    tools, workflows, models = _compile_domain(domain, services, selected_modules, operators)
    remote_operations = {
        item["serviceId"]: set(item["allowedOperations"])
        for item in product.remote_services
    }
    for workflow in workflows:
        for node in workflow["nodes"]:
            if node["runtime"] != "remote_http":
                continue
            binding = node["remoteBinding"]
            allowed = remote_operations.get(binding["serviceId"])
            if allowed is None or binding["operationType"] not in allowed:
                raise ValueError("REMOTE_OPERATION_DISABLED")
    enabled_operators = [
        f"{operator['id']}@{operator['version']}"
        for key in sorted(operators)
        if (
            (operator := operators[key])["runtime"] != "remote_http"
            or (
                operator["remoteBinding"]["serviceId"] in remote_operations
                and operator["remoteBinding"]["operationType"]
                in remote_operations[operator["remoteBinding"]["serviceId"]]
            )
        )
    ]
    product_ui, product_icon, product_ui_identity = load_product_ui(
        product_manifest_path.parent,
        product.ui_profile,
        product.product_id,
        models,
        domain.execution_cases,
    )
    worker_runtimes = {node["runtime"] for workflow in workflows for node in workflow["nodes"]}
    # Assembly is the sole non-candidate entry point that maps legacy
    # EXECUTOR_* environment inputs into the immutable operational profile.
    # Everything below receives the profile, never independent capacity values.
    executor_profile = executor_profile or ExecutorOperationalProfile.from_environment()
    routes = _compile_routes(product.public_routes, services, selected_modules)
    gateway_nginx = _nginx_config(product.product_id, routes, services)
    ir = AssemblyIR(product, domain, selected_modules, services, tuple(routes))
    service_wire = ir.service_wire()
    manifest_checksum = _checksum(manifest)
    kernel_config = {
        "schemaVersion": "kernel-config.v8",
        "productId": product.product_id,
        "publicApiVersion": product.public_api_version,
        "systemPrompt": domain.instructions,
        "services": service_wire,
        "observationTools": tools,
        "models": [
            {**{key: model[key] for key in (
                "modelId", "version", "modelChecksum", "workflowRef", "tool",
                "defaultParameters", "executionService", "executorId",
                "deadlineSeconds", "finalOutputs", "inputSchemaChecksum",
            )}, "dataInputParameters": sorted(set(model["inputBindings"].values()))}
            for model in models
        ],
        "executionCases": list(domain.execution_cases),
        "workflowAuthoring": {
            "operatorCatalogDigest": catalog_digest,
            "operators": [operators[key] for key in sorted(operators)],
            "enabledOperators": enabled_operators,
        },
        "manifestChecksum": manifest_checksum,
    }
    executor_config = {
        "schemaVersion": "executor-config.v8",
        "productId": product.product_id,
        "operatorCatalogDigest": catalog_digest,
        "workerRuntimes": _worker_runtime_config(worker_runtimes, executor_profile),
        "leaseHeartbeatSeconds": 10,
        "leaseSeconds": 45,
        "remoteServices": {
            item["serviceId"]: item for item in product.remote_services
        },
        "models": models,
    }
    logical_dir = generated / ".releases" / manifest["productId"] / "_CONTENT_"
    compose = _compose(
        root,
        logical_dir,
        product.product_id,
        selected_modules,
        modules["kernel"],
        domain,
        projects,
        product.project_id,
        worker_runtimes,
        executor_profile,
        product.remote_services,
    )
    lock_modules = {"kernel": modules["kernel"], **selected_modules}
    lock = {
        "schemaVersion": "assembly-lock.v8",
        "productId": product.product_id,
        "domainPackage": {**domain.identity, "files": domain.file_checksums},
        "knowledgeBootstrap": {
            "projects": sorted(projects),
            "files": sorted(domain.manifest["knowledgeDocuments"]),
            "documents": sorted(item["knowledgeId"] for item in domain.knowledge_documents),
            "documentCount": len(domain.knowledge_documents),
            "chunkCount": sum(
                len(item["chunks"]) for item in domain.knowledge_documents
            ),
        },
        "productManifestChecksum": manifest_checksum,
        "productUi": product_ui_identity,
        "managementGateway": {
            "managementServices": [
                name
                for name in ("data", "knowledge-next")
                if name in selected_modules
            ],
            "configChecksum": "sha256:"
            + hashlib.sha256(gateway_nginx.encode("utf-8")).hexdigest(),
        },
        "kernelConfigChecksum": _checksum(kernel_config),
        "executorConfigChecksum": _checksum(executor_config),
        "executorOperationalProfile": _worker_runtime_config(
            worker_runtimes, executor_profile
        ),
        "operatorCatalogDigest": catalog_digest,
        "workflows": {
            f"{item['workflowId']}@{item['version']}": item["definitionChecksum"]
            for item in workflows
        },
        "models": {
            f"{item['modelId']}@{item['version']}": item["modelChecksum"]
            for item in models
        },
        "modules": {
            name: {
                "moduleVersion": module.version,
                "distribution": {
                    "name": module.distribution_name,
                    "version": module.distribution_version,
                },
                "manifestChecksum": "sha256:"
                + hashlib.sha256(module.path.read_bytes()).hexdigest(),
                "httpContracts": {
                    contract_id: contract.checksum
                    for contract_id, contract in sorted(
                        module.http_contracts.items()
                    )
                },
                "observationToolContracts": {
                    contract_id: contract.checksum
                    for contract_id, contract in sorted(
                        module.observation_tool_contracts.items()
                    )
                },
            }
            for name, module in sorted(lock_modules.items())
        },
    }
    outputs = {
        "kernel-config.json": json.dumps(kernel_config, ensure_ascii=False, indent=2)
        + "\n",
        "executor-config.json": json.dumps(
            executor_config, ensure_ascii=False, indent=2
        )
        + "\n",
        "compose.json": json.dumps(compose, ensure_ascii=False, indent=2) + "\n",
        "nginx.conf": gateway_nginx,
        "assembly-lock.json": json.dumps(lock, ensure_ascii=False, indent=2) + "\n",
        "product-ui.json": product_ui,
        "product-icon.svg": product_icon,
    }
    digest = hashlib.sha256(_canonical(outputs)).hexdigest()
    release_dir = generated / ".releases" / product.product_id / digest
    outputs["compose.json"] = json.dumps(
        _compose(
            root, release_dir, product.product_id, selected_modules,
            modules["kernel"], domain,
            projects, product.project_id, worker_runtimes, executor_profile,
            product.remote_services,
        ),
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    return _publish_bundle(root, product.product_id, digest, outputs, baseline)

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    print(compile_product(args.root, args.manifest))

def domain_main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Validate or release an immutable domain package")
    parser.add_argument("command", choices=("check", "pack"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if (args.command == "pack") != (args.output is not None):
        parser.error("pack requires --output; check does not accept it")
    domain = load_domain(args.directory)
    modules = _load_modules(args.root)
    names = [
        name
        for name, module in modules.items()
        if name != "kernel" and module.api_version == CURRENT_MODULE_API_VERSION
    ]
    services, selected = _compile_services(names, modules)
    operators, _ = _load_operator_catalog(args.root)
    _compile_domain(domain, services, selected, operators)
    if args.command == "pack":
        pack_domain(domain, args.output)
    print(json.dumps(domain.identity, ensure_ascii=False, sort_keys=True))

def pack_domain(domain: DomainPackage, output: Path) -> None:
    """Release exactly the bytes used to calculate the package identity."""
    with ZipFile(output, "x", compression=ZIP_STORED) as archive:
        for name, content in sorted(domain.files.items()):
            entry = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, content)


if __name__ == "__main__":
    main()
