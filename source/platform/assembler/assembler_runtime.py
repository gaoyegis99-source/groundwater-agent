from __future__ import annotations

import os
from pathlib import Path

from assembler_common import DomainPackage, ModuleContract, PROJECT_UPLOAD_BYTES
from base_images import locked_base_image_contexts
from deployment_topology import apply_assembled_topology
from executor_profile import ExecutorOperationalProfile


SECRETS_ROOT = "${PLATFORM_SECRETS_DIR:?required}"
INSTANCE_DATA_ROOT = "${PLATFORM_INSTANCE_DATA_ROOT:?required}"
DEPENDENCY_ROOT = "${PLATFORM_DEPENDENCY_ROOT:?required}"
LOCAL_IDENTITY_ENVIRONMENT: dict[str, str] = {}
TELEMETRY_ENVIRONMENT = {
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "${OTEL_EXPORTER_OTLP_TRACES_ENDPOINT:-}",
}


def _worker_runtime_config(
    worker_runtimes: set[str], profile: ExecutorOperationalProfile | None = None
) -> dict[str, dict[str, int]]:
    return (profile or ExecutorOperationalProfile()).worker_runtimes(worker_runtimes)


def _service_healthcheck(path: str) -> dict:
    return {
        "test": [
            "CMD",
            "python",
            "-c",
            f"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000{path}', timeout=2)",
        ],
        "interval": "5s",
        "timeout": "3s",
        "retries": 20,
    }


def _persistent_mount(relative: str, target: str) -> dict:
    """Declare authoritative data outside Docker's disposable data-root."""

    return {
        "type": "bind",
        "source": f"{INSTANCE_DATA_ROOT}/{relative}",
        "target": target,
        "bind": {"create_host_path": False},
    }


def _build(root: Path, dockerfile: str) -> dict:
    return {
        "context": str(root),
        "dockerfile": dockerfile,
        "additional_contexts": [
            f"platform-deps={DEPENDENCY_ROOT}",
            *locked_base_image_contexts(root),
        ],
    }


def _ordinary_service(
    root: Path, module: ModuleContract, dependencies: dict, project_id: str
) -> dict:
    name = module.name
    mount = os.path.commonpath(
        [str(Path(path).parent) for path in module.storage_paths]
    )
    value: dict = {
        "build": _build(root, f"services/{name}/Dockerfile"),
        "command": list(module.command),
        "healthcheck": _service_healthcheck(module.ready_path),
        "volumes": [_persistent_mount(f"services/{name}", mount)],
        "environment": {
            "PLATFORM_PROJECT_ID": project_id,
            f"{name.upper()}_DATA_ROOT": "/data",
            **TELEMETRY_ENVIRONMENT,
        },
    }
    if dependencies:
        value["depends_on"] = dependencies
    return value


def _executor_services(
    root: Path,
    output_dir: Path,
    dependencies: dict,
    project_id: str,
    worker_runtimes: set[str],
    profile: ExecutorOperationalProfile | None = None,
    remote_services: tuple[dict, ...] = (),
) -> dict[str, dict]:
    common_environment = {
        "EXECUTOR_CONTROL_URL": "http://executor-control:8000",
        "EXECUTOR_CONFIG_PATH": "/config/executor-config.json",
        "EXECUTOR_WORK_ROOT": "/work",
        "PLATFORM_PROJECT_ID": project_id,
        **TELEMETRY_ENVIRONMENT,
    }
    common_volumes = [
        _persistent_mount("executor/work", "/work"),
        f"{output_dir / 'executor-config.json'}:/config/executor-config.json:ro",
    ]
    control_environment = {
        "EXECUTOR_STATE_ROOT": "/data/state",
        "EXECUTOR_OBJECT_ROOT": "/data/objects",
        "EXECUTOR_WORK_ROOT": "/work",
        "EXECUTOR_CONFIG_PATH": "/config/executor-config.json",
        "DATA_SERVICE_URL": "http://data-service:8000",
        "PLATFORM_PROJECT_ID": project_id,
        **TELEMETRY_ENVIRONMENT,
    }
    deployment = (profile or ExecutorOperationalProfile()).worker_runtimes(
        {"native", "qgis", "remote_http"}
    )
    remote_environment = {
        name: f"${{{name}:?required}}"
        for binding in remote_services
        for name in (
            binding["baseUrlEnvironment"],
            binding["allowedHostEnvironment"],
        )
    }
    remote_secrets = sorted({
        binding[name]
        for binding in remote_services
        for name in ("credentialSecret", "caBundleSecret")
    })
    services = {
        "executor-control": {
            "build": _build(root, "services/executor/Dockerfile"),
            "command": [
                "uvicorn",
                "platform_executor_service.main:app",
                "--host",
                "0.0.0.0",
                "--port",
                "8000",
            ],
            "environment": control_environment,
            "volumes": [
                _persistent_mount("executor/state", "/data"),
                *common_volumes,
            ],
            "healthcheck": _service_healthcheck("/internal/health/ready"),
            **({"depends_on": dependencies} if dependencies else {}),
        },
        "executor-native-worker": {
            "init": True,
            "build": _build(root, "services/executor/Dockerfile.native"),
            "command": [
                "/usr/bin/python3",
                "-m",
                "platform_executor_service.worker",
                "--runtime",
                "native",
                "--slots",
                str(deployment["native"]["slots"]),
                "--memory-bytes",
                str(deployment["native"]["memoryBytes"]),
            ],
            "environment": common_environment,
            "volumes": common_volumes,
            "mem_limit": deployment["native"]["memoryBytes"],
            "depends_on": {"executor-control": {"condition": "service_started"}},
        },
        "executor-qgis-worker": {
            "init": True,
            "build": _build(root, "services/executor/Dockerfile.qgis"),
            "command": [
                "/usr/bin/python3",
                "-m",
                "platform_executor_service.worker",
                "--runtime",
                "qgis",
                "--slots",
                str(deployment["qgis"]["slots"]),
                "--memory-bytes",
                str(deployment["qgis"]["memoryBytes"]),
            ],
            "environment": common_environment,
            "volumes": common_volumes,
            "mem_limit": deployment["qgis"]["memoryBytes"],
            "depends_on": {"executor-control": {"condition": "service_started"}},
        },
        "executor-remote_http-worker": {
            "init": True,
            "build": _build(root, "services/executor/Dockerfile.remote-http"),
            "command": [
                "python",
                "-m",
                "platform_executor_service.worker",
                "--runtime",
                "remote_http",
                "--slots",
                str(deployment["remote_http"]["slots"]),
                "--memory-bytes",
                str(deployment["remote_http"]["memoryBytes"]),
            ],
            "environment": {**common_environment, **remote_environment},
            "secrets": remote_secrets,
            "volumes": common_volumes,
            "mem_limit": deployment["remote_http"]["memoryBytes"],
            "depends_on": {"executor-control": {"condition": "service_started"}},
        },
    }
    return {
        name: service
        for name, service in services.items()
        if name == "executor-control"
        or name.removeprefix("executor-").removesuffix("-worker") in worker_runtimes
    }


def _data_services(root: Path, product_id: str, project_id: str) -> dict[str, dict]:
    common = {
        "build": _build(root, "services/data/Dockerfile"),
        "secrets": ["data-db-password"],
        "environment": {
            "DATA_DATABASE_URL": "postgresql://data@data-db:5432/data",
            "DATA_DATABASE_PASSWORD_FILE": "/run/secrets/data-db-password",
            "DATA_PROJECT": project_id,
            "PLATFORM_PRODUCT_ID": product_id,
            "DATA_ROOT": "/data",
            "DATA_UPLOAD_BYTES": str(PROJECT_UPLOAD_BYTES),
            "DATA_UPLOAD_SLOTS": "${DATA_UPLOAD_SLOTS:-4}",
            "DATA_WORKER_SLOTS": "${DATA_WORKER_SLOTS:-2}",
            "PLATFORM_IDENTITY_MODE": "gateway",
            "OPENLINEAGE_URL": "${OPENLINEAGE_URL:-}",
            "DATA_QUALITY_WEBHOOK_URL": "${DATA_QUALITY_WEBHOOK_URL:-}",
            "MLFLOW_ADAPTER_URL": "${MLFLOW_ADAPTER_URL:-}",
            **TELEMETRY_ENVIRONMENT,
            **LOCAL_IDENTITY_ENVIRONMENT,
        },
    }
    return {
        "data-db": {
            "image": "postgres:16.10-bookworm@sha256:38471f330eb885e04de130b768d6db4e10469e2311879c7e5c699f6d2d8a1c74",
            "environment": {
                "POSTGRES_USER": "data",
                "POSTGRES_PASSWORD_FILE": "/run/secrets/data-db-password",
                "POSTGRES_DB": "data",
            },
            "secrets": ["data-db-password"],
            "volumes": [_persistent_mount("data/database", "/var/lib/postgresql/data")],
            "healthcheck": {
                "test": ["CMD-SHELL", "pg_isready -U data -d data"],
                "interval": "2s",
                "timeout": "3s",
                "retries": 30,
            },
        },
        "data-migrate": {
            **common,
            "command": ["platform-data", "migrate"],
            "volumes": [_persistent_mount("data/objects", "/data")],
            "depends_on": {"data-db": {"condition": "service_healthy"}},
            "restart": "no",
        },
        "data-service": {
            **common,
            "command": [
                "uvicorn",
                "platform_data_service.main:app",
                "--host",
                "0.0.0.0",
                "--port",
                "8000",
            ],
            "volumes": [_persistent_mount("data/objects", "/data")],
            "depends_on": {
                "data-migrate": {"condition": "service_completed_successfully"}
            },
            "healthcheck": _service_healthcheck("/internal/health/ready"),
        },
        "data-worker": {
            **common,
            "init": True,
            "command": ["platform-data", "worker"],
            "environment": {
                **common["environment"],
                "DATA_SECCOMP_PROFILE": "/app/seccomp.json",
            },
            "volumes": [_persistent_mount("data/objects", "/data")],
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true", "seccomp=unconfined"],
            "depends_on": {
                "data-migrate": {"condition": "service_completed_successfully"}
            },
        },
    }


def _knowledge_next_services(
    root: Path, product_id: str, project_id: str
) -> dict[str, dict]:
    common = {
        "build": _build(root, "services/knowledge-next/Dockerfile"),
        "secrets": ["knowledge-db-password"],
        "environment": {
            "KNOWLEDGE_DATABASE_URL": "postgresql://knowledge@knowledge-next-db:5432/knowledge",
            "KNOWLEDGE_DATABASE_PASSWORD_FILE": "/run/secrets/knowledge-db-password",
            "KNOWLEDGE_PROJECT": project_id,
            "PLATFORM_PRODUCT_ID": product_id,
            "KNOWLEDGE_DATA_URL": "http://data-service:8000",
            "PLATFORM_EMBEDDING_BASE_URL": "${PLATFORM_EMBEDDING_BASE_URL:?required}",
            "PLATFORM_EMBEDDING_HEALTH_PATH": "${PLATFORM_EMBEDDING_HEALTH_PATH:-/health}",
            "KNOWLEDGE_VECTOR_SEARCH": "${KNOWLEDGE_VECTOR_SEARCH:-exact}",
            "PLATFORM_IDENTITY_MODE": "gateway",
            **TELEMETRY_ENVIRONMENT,
            **LOCAL_IDENTITY_ENVIRONMENT,
        },
    }
    return {
        "knowledge-next-db": {
            "image": "pgvector/pgvector:0.8.7-pg16-bookworm@sha256:7b822b0aac60967beb1ea5e576b8602c94c300a157d187f385ae3e0da199b90a",
            "environment": {
                "POSTGRES_USER": "knowledge",
                "POSTGRES_PASSWORD_FILE": "/run/secrets/knowledge-db-password",
                "POSTGRES_DB": "knowledge",
            },
            "secrets": ["knowledge-db-password"],
            "volumes": [
                _persistent_mount("knowledge/database", "/var/lib/postgresql/data")
            ],
            "healthcheck": {
                "test": ["CMD-SHELL", "pg_isready -U knowledge -d knowledge"],
                "interval": "2s",
                "timeout": "3s",
                "retries": 30,
            },
        },
        "knowledge-next-migrate": {
            **common,
            "command": ["platform-knowledge", "migrate"],
            "depends_on": {"knowledge-next-db": {"condition": "service_healthy"}},
            "restart": "no",
        },
        "knowledge-next-service": {
            **common,
            "command": ["platform-knowledge", "serve"],
            "depends_on": {
                "knowledge-next-migrate": {
                    "condition": "service_completed_successfully"
                },
                "data-service": {"condition": "service_healthy"},
            },
            "healthcheck": _service_healthcheck("/internal/health/ready"),
        },
        "knowledge-next-worker": {
            **common,
            "command": ["platform-knowledge", "worker"],
            "depends_on": {
                "knowledge-next-migrate": {
                    "condition": "service_completed_successfully"
                },
                "data-service": {"condition": "service_healthy"},
            },
        },
    }


def _compose(
    root: Path,
    output_dir: Path,
    product_id: str,
    modules: dict[str, ModuleContract],
    kernel: ModuleContract,
    domain: DomainPackage,
    knowledge_projects: list[str],
    project_id: str,
    worker_runtimes: set[str],
    profile: ExecutorOperationalProfile | None = None,
    remote_services: tuple[dict, ...] = (),
) -> dict:
    providers = {
        capability: module.name
        for module in modules.values()
        for capability in module.provides
    }
    services: dict[str, dict] = {
        "gateway": {
            "build": _build(root, "services/gateway/Dockerfile"),
            "ports": [
                "${PLATFORM_GATEWAY_BIND_IP:-127.0.0.1}:${PLATFORM_GATEWAY_PORT:-28186}:8080"
            ],
            "volumes": [
                f"{output_dir / 'nginx.conf'}:/etc/nginx/nginx.conf:ro",
                f"{output_dir / 'product-ui.json'}:/usr/share/nginx/html/product-ui.json:ro",
                f"{output_dir / 'product-icon.svg'}:/usr/share/nginx/html/product-icon.svg:ro",
            ],
            "healthcheck": {
                "test": ["CMD", "wget", "-qO-", "http://127.0.0.1:8080/api/v12/health"],
                "interval": "5s",
                "timeout": "3s",
                "retries": 20,
            },
        },
        "kernel-service": {
            "build": _build(root, "kernel/Dockerfile"),
            "volumes": [
                _persistent_mount("kernel", "/data"),
                f"{output_dir / 'kernel-config.json'}:/config/kernel-config.json:ro",
            ],
            "secrets": [
                "agent-api-key",
                "intent-router-api-key",
            ],
            "environment": {
                "KERNEL_DATA_ROOT": "/data",
                "KERNEL_CONFIG_PATH": "/config/kernel-config.json",
                "PLATFORM_PROJECT_ID": project_id,
                "PLATFORM_RESEARCH_AUTH_REQUIRED": "true",
                **TELEMETRY_ENVIRONMENT,
                **LOCAL_IDENTITY_ENVIRONMENT,
                "PLATFORM_AGENT_PROVIDER": "${PLATFORM_AGENT_PROVIDER:?required}",
                "PLATFORM_AGENT_PROTOCOL": "${PLATFORM_AGENT_PROTOCOL:?required}",
                "PLATFORM_AGENT_BASE_URL": "${PLATFORM_AGENT_BASE_URL:?required}",
                "PLATFORM_AGENT_STANDBY_BASE_URL": "${PLATFORM_AGENT_STANDBY_BASE_URL:-}",
                "PLATFORM_AGENT_HEALTH_PATH": "${PLATFORM_AGENT_HEALTH_PATH:-/models}",
                "PLATFORM_AGENT_MODEL": "${PLATFORM_AGENT_MODEL:?required}",
                "PLATFORM_AGENT_API_KEY_FILE": "/run/secrets/agent-api-key",
                "PLATFORM_AGENT_CONTEXT_WINDOW_TOKENS": "${PLATFORM_AGENT_CONTEXT_WINDOW_TOKENS:?required}",
                "PLATFORM_AGENT_MAX_OUTPUT_TOKENS": "${PLATFORM_AGENT_MAX_OUTPUT_TOKENS:?required}",
                "PLATFORM_AGENT_REQUEST_TIMEOUT_SECONDS": "${PLATFORM_AGENT_REQUEST_TIMEOUT_SECONDS:?required}",
                "PLATFORM_AGENT_SEGMENT_TIMEOUT_SECONDS": "${PLATFORM_AGENT_SEGMENT_TIMEOUT_SECONDS:?required}",
                "PLATFORM_AGENT_PREVIEW_ENABLED": "${PLATFORM_AGENT_PREVIEW_ENABLED:-true}",
                "PLATFORM_AGENT_CONCURRENCY": "${PLATFORM_AGENT_CONCURRENCY:-8}",
                "PLATFORM_AGENT_QUEUE_CAPACITY": "${PLATFORM_AGENT_QUEUE_CAPACITY:-100}",
                "PLATFORM_INTENT_ROUTER_PROVIDER": "${PLATFORM_INTENT_ROUTER_PROVIDER:?required}",
                "PLATFORM_INTENT_ROUTER_PROTOCOL": "${PLATFORM_INTENT_ROUTER_PROTOCOL:?required}",
                "PLATFORM_INTENT_ROUTER_BASE_URL": "${PLATFORM_INTENT_ROUTER_BASE_URL:?required}",
                "PLATFORM_INTENT_ROUTER_STANDBY_BASE_URL": "${PLATFORM_INTENT_ROUTER_STANDBY_BASE_URL:-}",
                "PLATFORM_INTENT_ROUTER_HEALTH_PATH": "${PLATFORM_INTENT_ROUTER_HEALTH_PATH:-/models}",
                "PLATFORM_INTENT_ROUTER_MODEL": "${PLATFORM_INTENT_ROUTER_MODEL:?required}",
                "PLATFORM_INTENT_ROUTER_API_KEY_FILE": "/run/secrets/intent-router-api-key",
                "PLATFORM_INTENT_ROUTER_CONTEXT_WINDOW_TOKENS": "${PLATFORM_INTENT_ROUTER_CONTEXT_WINDOW_TOKENS:?required}",
                "PLATFORM_INTENT_ROUTER_MAX_OUTPUT_TOKENS": "${PLATFORM_INTENT_ROUTER_MAX_OUTPUT_TOKENS:?required}",
                "PLATFORM_INTENT_ROUTER_REQUEST_TIMEOUT_SECONDS": "${PLATFORM_INTENT_ROUTER_REQUEST_TIMEOUT_SECONDS:?required}",
                "PLATFORM_INTENT_ROUTER_SEGMENT_TIMEOUT_SECONDS": "${PLATFORM_INTENT_ROUTER_SEGMENT_TIMEOUT_SECONDS:?required}",
                "PLATFORM_READINESS_TIMEOUT_SECONDS": "${PLATFORM_READINESS_TIMEOUT_SECONDS:-5}",
                "PLATFORM_READINESS_SUCCESS_CACHE_SECONDS": "${PLATFORM_READINESS_SUCCESS_CACHE_SECONDS:-15}",
                "PLATFORM_READINESS_FAILURE_CACHE_SECONDS": "${PLATFORM_READINESS_FAILURE_CACHE_SECONDS:-5}",
            },
            "command": list(kernel.command),
            "healthcheck": _service_healthcheck(kernel.ready_path),
        },
    }
    for name, module in modules.items():
        dependencies = {
            (
                "executor-control"
                if providers[requirement] == "executor"
                else f"{providers[requirement]}-service"
            ): {"condition": "service_healthy"}
            for requirement in module.requires
            if requirement in providers
        }
        if name == "data":
            services.update(_data_services(root, product_id, project_id))
        elif name == "knowledge-next":
            services.update(_knowledge_next_services(root, product_id, project_id))
        elif name == "executor":
            services.update(
                _executor_services(
                    root,
                    output_dir,
                    dependencies,
                    project_id,
                    worker_runtimes,
                    profile,
                    remote_services,
                )
            )
        else:
            services[f"{name}-service"] = _ordinary_service(
                root, module, dependencies, project_id
            )
    if domain.knowledge_documents:
        services["knowledge-bootstrap"] = {
            "build": services["knowledge-next-service"]["build"],
            "command": [
                "platform-knowledge",
                "bootstrap",
                "--lock",
                "/config/assembly-lock.json",
                "--domain",
                "/domain",
                "--url",
                "http://knowledge-next-service:8000",
                *[
                    value
                    for project_id in sorted(knowledge_projects)
                    for value in ("--project", project_id)
                ],
            ],
            "volumes": [
                f"{domain.root}:/domain:ro",
                f"{output_dir / 'assembly-lock.json'}:/config/assembly-lock.json:ro",
            ],
            "environment": {"PLATFORM_PROJECT_ID": project_id},
            "depends_on": {
                "knowledge-next-service": {"condition": "service_healthy"},
                "knowledge-next-worker": {"condition": "service_started"},
            },
            "restart": "no",
            "profiles": ["bootstrap"],
            "healthcheck": {"disable": True},
        }
        # Domain seeding is an observable one-shot initialization task, not a
        # liveness dependency of Kernel.  A failed seed must leave authentication,
        # status, Data and Executor APIs available for diagnosis and retry.
    for name, service in services.items():
        if name not in {
            "knowledge-bootstrap",
            "data-migrate",
            "knowledge-next-migrate",
        }:
            service["restart"] = "unless-stopped"
    topology = apply_assembled_topology(services, project_id)
    topology["secrets"] = {
        "agent-api-key": {"file": f"{SECRETS_ROOT}/agent-api-key"},
        "intent-router-api-key": {"file": f"{SECRETS_ROOT}/intent-router-api-key"},
        "data-db-password": {"file": f"{SECRETS_ROOT}/data-db-password"},
        "knowledge-db-password": {"file": f"{SECRETS_ROOT}/knowledge-db-password"},
        **{
            binding[name]: {"file": f"{SECRETS_ROOT}/{binding[name]}"}
            for binding in remote_services
            for name in ("credentialSecret", "caBundleSecret")
        },
    }
    return topology
