from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "platform" / "assembler"))
from assembler import (  # noqa: E402
    _checksum,
    _load_modules,
    available_products,
    compile_product,
    domain_main,
    load_domain,
)
from assembler_publication import bundle_pointer_path, current_bundle  # noqa: E402
from base_images import locked_base_image_contexts  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_assembly_output(tmp_path, monkeypatch):
    # Keep both real-source compiles and copied-source fixtures in this test's
    # workspace. A caller's shared generated root must not override fixtures.
    monkeypatch.setenv("PLATFORM_GENERATED_ROOT", str(tmp_path / ".generated"))


def _manifest(product: str) -> Path:
    return ROOT / "products" / product / "product-manifest.json"


def test_domain_cli_checks_current_package_without_loading_legacy_services(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    domain = ROOT / "domains" / "groundwater" / "6.0.0"
    monkeypatch.setattr(
        sys,
        "argv",
        ["platform-domain", "check", str(domain), "--root", str(ROOT)],
    )

    domain_main()

    assert json.loads(capsys.readouterr().out) == load_domain(domain).identity


def test_default_module_scan_only_returns_current_contracts() -> None:
    modules = _load_modules(ROOT)

    assert set(modules) == {"kernel", "data", "knowledge-next", "executor"}
    assert {module.api_version for module in modules.values()} == {"platform.module/v9"}


def test_retired_infrastructure_dependency_is_not_implicitly_provided():
    from assembler_domain import _compile_services

    modules = _load_modules(ROOT)
    modules["knowledge-next"] = replace(
        modules["knowledge-next"], requires=("qdrant-v1",)
    )
    with pytest.raises(ValueError, match="PRODUCT_SERVICE_DEPENDENCY_MISSING"):
        _compile_services(["data", "knowledge-next"], modules)


def test_data_route_compiles_research_streaming_limits_at_gateway():
    generated = compile_product(ROOT, _manifest("groundwater"))
    lines = (generated / "nginx.conf").read_text().splitlines()
    data_lines = [
        line
        for line in lines
        if "location /api/v12/data" in line and "proxy_pass $upstream_data;" in line
    ]
    identity_lines = [
        line for line in lines if "location = /_platform_identity" in line
    ]
    assert len(data_lines) == 1
    assert len(identity_lines) == 1
    for line in data_lines:
        assert f"client_max_body_size {1024**3};" in line
        assert "proxy_request_buffering off;" in line
        assert "proxy_buffering off;" in line
        assert "proxy_http_version 1.1;" in line
        assert "proxy_read_timeout 1h;" in line
        assert "client_body_timeout 60s;" in line
    assert any("auth_request /_platform_identity" in line for line in lines)
    assert any(
        "proxy_pass $upstream_kernel/internal/identity/verify" in line for line in lines
    )
    assert f"client_max_body_size {1024**3};" in identity_lines[0]
    other_public_api_lines = [
        line
        for line in lines
        if "location /api/v12/" in line and line not in data_lines
    ]
    assert all("client_max_body_size" not in line for line in other_public_api_lines)


@pytest.mark.parametrize(
    "prefix",
    [
        "/api/v11/ready",
        "/api/v11/audit",
        "/",
        "/assets/",
        "/product-ui.json",
        "/api/v11/data/",
        "/api/v11/data//admin",
        "/api/v11/data/../admin",
        "/api/v11/data { return 200; }",
    ],
)
def test_product_route_prefix_is_a_strict_trust_boundary(tmp_path, prefix):
    _copy_inputs(tmp_path)
    manifest_path = tmp_path / "products/groundwater/product-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["publicRoutes"][0]["prefix"] = prefix
    manifest_path.write_text(json.dumps(manifest))

    error = (
        "PRODUCT_ROUTE_CONFLICT"
        if prefix in {"/api/v12/ready", "/api/v12/audit"}
        else "PRODUCT_ROUTE_PREFIX_INVALID"
    )
    with pytest.raises(ValueError, match=error):
        compile_product(tmp_path, manifest_path)


def test_data_is_a_required_v11_product_service(tmp_path):
    _copy_inputs(tmp_path)
    manifest_path = tmp_path / "products/groundwater/product-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["services"].remove("data")
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="PRODUCT_DATA_SERVICE_REQUIRED"):
        compile_product(tmp_path, manifest_path)


def _copy_inputs(target: Path) -> None:
    ignore = shutil.ignore_patterns(
        ".generated",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
    )
    for name in ("kernel", "services", "products", "packages", "domains"):
        shutil.copytree(ROOT / name, target / name, ignore=ignore)
    shutil.copy2(ROOT / "system-deps.lock.json", target / "system-deps.lock.json")


def test_isolated_assembly_inputs_exclude_local_generated_and_dependency_trees(
    tmp_path: Path,
) -> None:
    _copy_inputs(tmp_path)

    excluded_names = {".generated", ".venv", "build", "dist", "node_modules"}
    assert not [path for path in tmp_path.rglob("*") if path.name in excluded_names]
    assert (tmp_path / "system-deps.lock.json").is_file()


def _selected_groundwater_domain(target: Path) -> Path:
    manifest = json.loads((target / "products/groundwater/product-manifest.json").read_bytes())
    return target / "domains" / "groundwater" / manifest["domainPackage"]["version"]


def _pin_domain(target: Path, product_name: str, domain: Path) -> Path:
    manifest_path = target / "products" / product_name / "product-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["domainPackage"] = load_domain(domain).identity
    manifest_path.write_text(json.dumps(manifest))
    return manifest_path


def test_remote_workflow_operation_must_be_enabled_by_product_binding(
    tmp_path: Path,
) -> None:
    _copy_inputs(tmp_path)
    manifest_path = tmp_path / "products/groundwater/product-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["remoteServices"][0]["allowedOperations"] = ["other.evaluate"]
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="REMOTE_OPERATION_DISABLED"):
        compile_product(tmp_path, manifest_path)


def test_groundwater_compiles_v12_research_outputs() -> None:
    output = compile_product(ROOT, _manifest("groundwater"))
    assert {path.name for path in output.iterdir()} == {
        "kernel-config.json",
        "executor-config.json",
        "compose.json",
        "nginx.conf",
        "assembly-lock.json",
        "product-ui.json",
        "product-icon.svg",
    }
    kernel = json.loads((output / "kernel-config.json").read_text())
    executor = json.loads((output / "executor-config.json").read_text())
    compose = json.loads((output / "compose.json").read_text())
    assert kernel["schemaVersion"] == "kernel-config.v8"
    authoring = kernel["workflowAuthoring"]
    assert authoring["operatorCatalogDigest"] == executor["operatorCatalogDigest"]
    assert len(authoring["operators"]) == 111
    assert all("implementationRevision" in item for item in authoring["operators"])
    bindings = {
        row["serviceId"]: row["allowedOperations"]
        for row in json.loads(_manifest("groundwater").read_bytes())["remoteServices"]
    }
    assert set(authoring["enabledOperators"]) == {
        f"{item['id']}@{item['version']}"
        for item in authoring["operators"]
        if (
            item["runtime"] != "remote_http"
            or item["remoteBinding"]["operationType"] in bindings.get(item["remoteBinding"]["serviceId"], ())
        )
    }
    assert len(kernel["models"]) == (18 if "porous-groundwater" in bindings else 10)
    assert {"retrieve_evidence", "read_evidence", "lookup_knowledge"} <= {
        item["name"] for item in kernel["observationTools"]
    }
    assert executor["schemaVersion"] == "executor-config.v8"
    assert executor["leaseSeconds"] == 45
    assert set(compose["services"]) == {
        "gateway",
        "kernel-service",
        "data-db",
        "data-migrate",
        "data-service",
        "data-worker",
        "executor-control",
        "executor-native-worker",
        "executor-qgis-worker",
        "executor-remote_http-worker",
        "knowledge-next-db",
        "knowledge-next-migrate",
        "knowledge-next-service",
        "knowledge-next-worker",
        "knowledge-bootstrap",
    }
    assert set(executor["workerRuntimes"]) == {"native", "qgis", "remote_http"}
    assert set(executor["remoteServices"]) == set(bindings)
    assert executor["remoteServices"]["groundwater-sustainability"][
        "allowedOperations"
    ] == ["sustainability.evaluate"]
    assert "cacheMaxBytes" not in executor
    assert "PLATFORM_EMBEDDING_BASE_URL" in json.dumps(compose)
    assert set(kernel["models"][0]) == {
        "modelId",
        "version",
        "modelChecksum",
        "workflowRef",
        "tool",
        "defaultParameters",
        "executionService",
        "executorId",
        "deadlineSeconds",
        "finalOutputs",
        "inputSchemaChecksum",
        "dataInputParameters",
        "fieldRequirements",
        "semanticRequirements",
    }
    trend = next(
        model
        for model in kernel["models"]
        if model["modelId"] == "groundwater.monitoring-trend"
    )
    assert trend["fieldRequirements"] == [
        {
            "parameter": "groupField",
            "inputParameter": "versionId",
            "identifier": "name",
            "allowedTypes": ["string", "number"],
        },
        {
            "parameter": "timeField",
            "inputParameter": "versionId",
            "identifier": "name",
            "allowedTypes": ["string", "date-time"],
        },
        {
            "parameter": "valueField",
            "inputParameter": "versionId",
            "identifier": "name",
            "allowedTypes": ["string", "number"],
        },
    ]
    assert trend["semanticRequirements"] == [
        {
            "kind": "fieldUnit",
            "inputParameter": "versionId",
            "fieldParameter": "valueField",
        },
        {
            "kind": "timeField",
            "inputParameter": "versionId",
            "fieldParameter": "timeField",
        },
        {
            "kind": "timeRange",
            "inputParameter": "versionId",
            "fieldParameter": "timeField",
            "startParameter": "expectedStart",
            "endParameter": "expectedEnd",
        },
        {
            "kind": "timeRange",
            "inputParameter": "versionId",
            "fieldParameter": "timeField",
            "startParameter": "baselineStart",
            "endParameter": "baselineEnd",
        },
        {
            "kind": "timeRange",
            "inputParameter": "versionId",
            "fieldParameter": "timeField",
            "startParameter": "comparisonStart",
            "endParameter": "comparisonEnd",
        },
    ]
    flow = next(
        model
        for model in kernel["models"]
        if model["modelId"] == "groundwater.flow-field"
    )
    assert {
        "kind": "crsParameter",
        "parameter": "targetCrs",
        "requirement": "projectedMetric",
    } in flow["semanticRequirements"]
    assert {
        "kind": "verticalBasis",
        "inputParameter": "versionId",
        "referenceFieldParameter": "referenceElevationField",
        "depthFieldParameter": "depthToWaterField",
        "referenceMeanings": [
            "reference_elevation",
            "land_surface_elevation",
        ],
        "depthMeanings": [
            "depth_to_water",
            "depth_below_reference_surface",
        ],
    } in flow["semanticRequirements"]
    assert next(
        model for model in kernel["models"] if model["tool"]["name"] == "render_map"
    )["dataInputParameters"] == ["sourceVersionId"]
    assert "executor-service" not in compose["services"]
    assert compose["services"]["gateway"]["ports"] == [
        "${PLATFORM_GATEWAY_BIND_IP:-127.0.0.1}:${PLATFORM_GATEWAY_PORT:-28186}:8080"
    ]
    assert "depends_on" not in compose["services"]["gateway"]
    assert not any(name.startswith("mapfactory-") for name in compose["services"])
    assert "volumes" not in compose
    assert all(
        service["restart"] == "unless-stopped"
        for name, service in compose["services"].items()
        if name not in {"data-migrate", "knowledge-next-migrate", "knowledge-bootstrap"}
    )
    assert (
        compose["services"]["executor-native-worker"]["command"][0]
        == "/usr/bin/python3"
    )
    kernel_environment = compose["services"]["kernel-service"]["environment"]
    assert kernel_environment["PLATFORM_AGENT_CONTEXT_WINDOW_TOKENS"] == (
        "${PLATFORM_AGENT_CONTEXT_WINDOW_TOKENS:?required}"
    )
    assert kernel_environment["PLATFORM_AGENT_MAX_OUTPUT_TOKENS"] == (
        "${PLATFORM_AGENT_MAX_OUTPUT_TOKENS:?required}"
    )
    assert "PLATFORM_AGENT_CONTEXT_POLICY" not in kernel_environment
    assert kernel_environment["PLATFORM_AGENT_STANDBY_BASE_URL"] == (
        "${PLATFORM_AGENT_STANDBY_BASE_URL:-}"
    )
    assert kernel_environment["PLATFORM_AGENT_HEALTH_PATH"] == (
        "${PLATFORM_AGENT_HEALTH_PATH:-/models}"
    )
    assert kernel_environment["PLATFORM_INTENT_ROUTER_STANDBY_BASE_URL"] == (
        "${PLATFORM_INTENT_ROUTER_STANDBY_BASE_URL:-}"
    )
    assert kernel_environment["PLATFORM_INTENT_ROUTER_HEALTH_PATH"] == (
        "${PLATFORM_INTENT_ROUTER_HEALTH_PATH:-/models}"
    )
    assert kernel_environment["PLATFORM_READINESS_TIMEOUT_SECONDS"] == (
        "${PLATFORM_READINESS_TIMEOUT_SECONDS:-5}"
    )
    assert kernel_environment["PLATFORM_INTENT_ROUTER_CONTEXT_WINDOW_TOKENS"] == (
        "${PLATFORM_INTENT_ROUTER_CONTEXT_WINDOW_TOKENS:?required}"
    )
    assert kernel_environment["PLATFORM_INTENT_ROUTER_MAX_OUTPUT_TOKENS"] == (
        "${PLATFORM_INTENT_ROUTER_MAX_OUTPUT_TOKENS:?required}"
    )
    assert kernel_environment["PLATFORM_PROJECT_ID"] == "groundwater-demo"
    for name in (
        "PLATFORM_INTENT_ROUTER_PROTOCOL",
        "PLATFORM_AGENT_REQUEST_TIMEOUT_SECONDS",
        "PLATFORM_AGENT_SEGMENT_TIMEOUT_SECONDS",
        "PLATFORM_INTENT_ROUTER_REQUEST_TIMEOUT_SECONDS",
        "PLATFORM_INTENT_ROUTER_SEGMENT_TIMEOUT_SECONDS",
    ):
        assert kernel_environment[name] == "${" + name + ":?required}"
    nginx = (output / "nginx.conf").read_text()
    assert "auth_request /_platform_identity;" in nginx
    assert "location = /_platform_identity { internal; auth_request off;" in nginx
    assert (
        "X-Platform-Identity-Assertion $http_x_platform_identity_assertion" not in nginx
    )
    assert "proxy_set_header Authorization $http_authorization;" in nginx
    assert "proxy_set_header Cookie $http_cookie;" in nginx
    # The auth subrequest validates credentials and Kernel validates them again.
    # Raw credentials stay on Kernel routes and are never forwarded to the
    # Data or Knowledge services.
    assert nginx.count("proxy_set_header Authorization $http_authorization;") == 3
    assert nginx.count("proxy_set_header Cookie $http_cookie;") == 3
    assert nginx.count("proxy_set_header X-CSRF-Token $http_x_csrf_token;") == 3
    assert "add_header X-Request-ID $request_id always;" in nginx
    assert "proxy_hide_header X-Request-ID;" in nginx
    assert "proxy_set_header X-Request-ID $request_id;" in nginx
    assert "proxy_set_header X-Trace-ID $request_id;" in nginx
    assert "proxy_set_header X-Client-Type web;" in nginx
    assert "proxy_set_header X-Platform-Subject $verified_subject;" in nginx
    assert "proxy_set_header X-Platform-Display-Name $verified_display_name;" in nginx
    assert "proxy_set_header X-Platform-Roles $verified_roles;" in nginx
    assert "proxy_set_header X-Platform-Subject $http_x_platform_subject;" not in nginx
    assert "proxy_set_header X-Content-SHA256 $http_x_content_sha256;" in nginx
    assert (
        "log_format platform '$request_id $status $request_method $uri request_time="
        in nginx
    )
    assert "add_header Content-Security-Policy" in nginx
    assert "frame-ancestors 'none'" in nginx
    assert "add_header X-Content-Type-Options nosniff always;" in nginx
    assert "add_header Referrer-Policy no-referrer always;" in nginx
    assert "add_header Strict-Transport-Security" in nginx
    assert "location /api/v11/ {" in nginx
    assert "include /etc/nginx/mime.types;" in nginx
    assert "resolver 127.0.0.11 valid=5s ipv6=off;" in nginx
    assert "set $upstream_kernel http://kernel-service:8000;" in nginx
    assert "root /usr/share/nginx/html;" in nginx
    assert "location = / { auth_request off; try_files /index.html =404; }" in nginx
    assert "location /assets/ { auth_request off; try_files $uri =404; }" in nginx
    assert "location / { auth_request off; return 404; }" in nginx
    assert compose["services"]["gateway"]["build"] == {
        "context": str(ROOT),
        "dockerfile": "services/gateway/Dockerfile",
        "additional_contexts": [
            "platform-deps=${PLATFORM_DEPENDENCY_ROOT:?required}",
            *locked_base_image_contexts(ROOT),
        ],
    }
    assert "X-Platform-Identity-Proof" not in nginx
    assert "proxy_read_timeout 1h;" in nginx
    assert "auth-service" not in compose["services"]
    assert "volumes" not in compose
    assert all(
        "ports" not in service
        for name, service in compose["services"].items()
        if name != "gateway"
    )
    assert compose["secrets"] == {
        "agent-api-key": {"file": "${PLATFORM_SECRETS_DIR:?required}/agent-api-key"},
        "intent-router-api-key": {
            "file": "${PLATFORM_SECRETS_DIR:?required}/intent-router-api-key"
        },
        "data-db-password": {
            "file": "${PLATFORM_SECRETS_DIR:?required}/data-db-password"
        },
        "knowledge-db-password": {
            "file": "${PLATFORM_SECRETS_DIR:?required}/knowledge-db-password"
        },
        **{
            f"{service}-{suffix}": {
                "file": f"${{PLATFORM_SECRETS_DIR:?required}}/{service}-{suffix}"
            }
            for service in bindings
            for suffix in ("api-key", "ca-bundle")
        },
    }
    assert compose["services"]["kernel-service"]["secrets"] == [
        "agent-api-key",
        "intent-router-api-key",
    ]
    assert (
        "PLATFORM_IDENTITY_ASSERTION_KEY_FILE"
        not in compose["services"]["kernel-service"]["environment"]
    )
    assert compose["services"]["data-db"]["environment"] == {
        "POSTGRES_USER": "data",
        "POSTGRES_PASSWORD_FILE": "/run/secrets/data-db-password",
        "POSTGRES_DB": "data",
    }
    assert (
        compose["services"]["data-service"]["environment"]["DATA_DATABASE_URL"]
        == "postgresql://data@data-db:5432/data"
    )
    assert compose["services"]["knowledge-next-db"]["environment"] == {
        "POSTGRES_USER": "knowledge",
        "POSTGRES_PASSWORD_FILE": "/run/secrets/knowledge-db-password",
        "POSTGRES_DB": "knowledge",
    }
    assert "knowledge-local" not in json.dumps(compose)
    assert "data-local" not in json.dumps(compose)
    persistent_mounts = [
        volume
        for service in compose["services"].values()
        for volume in service.get("volumes", [])
        if isinstance(volume, dict)
    ]
    assert persistent_mounts
    assert all(volume["type"] == "bind" for volume in persistent_mounts)
    assert all(
        volume["source"].startswith("${PLATFORM_INSTANCE_DATA_ROOT:?required}/")
        for volume in persistent_mounts
    )
    assert all(
        volume["bind"] == {"create_host_path": False} for volume in persistent_mounts
    )
    gateway = (output / "nginx.conf").read_text()
    assert "auth_request /_platform_identity;" in gateway
    assert (
        f"location = /_platform_identity {{ internal; auth_request off; "
        f"client_max_body_size {1024**3};"
    ) in gateway
    assert "X-Platform-Required-Role admin" not in gateway
    assert "location ^~ /data/v3/" in gateway
    assert "location ^~ /knowledge/v3/" in gateway
    assert "proxy_pass $upstream_data;" in gateway
    assert "proxy_pass $upstream_knowledge;" in gateway
    assert "location ^~ /data/internal { return 404; }" in gateway
    assert "gateway-auth-key" not in json.dumps(compose)
    assert "service-trust.json" not in json.dumps(compose)


def test_groundwater_compiles_native_png_model_and_expected_parallelism() -> None:
    output = compile_product(ROOT, _manifest("groundwater"))
    kernel = json.loads((output / "kernel-config.json").read_text())
    executor = json.loads((output / "executor-config.json").read_text())
    tools = {item["name"]: item for item in kernel["observationTools"]}
    assert set(tools) == {
        "discover_datasets",
        "inspect_data_version",
        "inspect_data_table",
        "get_data_inventory",
        "discover_datasets",
        "retrieve_evidence",
        "read_evidence",
        "lookup_knowledge",
    }
    assert tools["retrieve_evidence"]["service"] == "knowledge-next"
    assert tools["retrieve_evidence"]["path"] == "/internal/tools/v5/retrieve_evidence"
    assert tools["retrieve_evidence"]["inputSchemaChecksum"].startswith("sha256:")
    assert tools["retrieve_evidence"]["outputSchemaChecksum"].startswith("sha256:")
    assert "url" not in tools["retrieve_evidence"]
    models = {item["modelId"]: item for item in executor["models"]}
    expected_models = {
        "groundwater.monitoring-trend",
        "groundwater.flow-field",
        "groundwater.drastic",
        "groundwater.kriging",
        "groundwater.transfer-learning-analysis",
        "groundwater.summary",
        "groundwater.monitoring-coverage",
        "groundwater.render-map",
        "groundwater.rule-classification",
        "groundwater.source-sustainability",
    }
    if "porous-groundwater" in executor["remoteServices"]:
        expected_models.update({
            "groundwater.porous-simulation",
            "groundwater.porous-calibration",
            "groundwater.porous-scenario-comparison",
            "groundwater.porous-map",
            "groundwater.porous-inversion-training",
            "groundwater.porous-inversion-sampling",
            "groundwater.porous-agricultural-inversion",
            "groundwater.sustainability-input-preparation",
        })
    assert set(models) == expected_models
    summary = models["groundwater.summary"]
    assert summary["version"] == "3.1.0"
    assert summary["workflowRef"]["workflowId"] == "groundwater.summary"
    assert summary["workflowRef"]["version"] == "1.1.0"
    assert summary["defaultParameters"] == {"axis": "columns", "unit": ""}
    assert summary["finalOutputs"] == ["summary", "summary-details"]
    assert models["groundwater.monitoring-trend"]["maxParallelNodes"] == 4
    assert len(models["groundwater.monitoring-trend"]["workflow"]["nodes"]) == 10
    assert models["groundwater.flow-field"]["maxParallelNodes"] == 1
    transfer = models["groundwater.transfer-learning-analysis"]
    assert transfer["version"] == "3.1.0"
    assert transfer["workflowRef"]["version"] == "1.0.0"
    assert len(transfer["workflow"]["nodes"]) == 58
    assert len(transfer["finalOutputs"]) == 25
    assert (
        "single-step"
        in transfer["outputPublications"]["predictions"]["semantics"]["description"]
    )
    assert (
        "r2_reason"
        in transfer["outputPublications"]["metrics"]["semantics"]["description"]
    )
    render = models["groundwater.render-map"]
    assert render["version"] == "3.1.0"
    assert render["workflowRef"]["version"] == "1.1.0"
    assert render["tool"]["name"] == "render_map"
    assert render["inputBindings"] == {"data": "sourceVersionId"}
    assert render["defaultParameters"] == {
        "title": "",
        "valueField": "",
        "palette": "viridis",
        "width": 1024,
        "height": 768,
        "unit": "",
    }
    assert render["finalOutputs"] == ["map-image", "map-summary"]
    assert len(render["workflow"]["nodes"]) == 1
    node = render["workflow"]["nodes"][0]
    assert (node["operatorId"], node["operatorVersion"], node["runtime"]) == (
        "cartography.render-png",
        "1.0.0",
        "native",
    )
    sustainability = models["groundwater.source-sustainability"]
    assert sustainability["version"] == "1.1.0"
    assert sustainability["workflowRef"]["version"] == "1.0.0"
    assert sustainability["finalOutputs"] == [
        "sustainability-result",
        "sustainability-result-file",
        "sustainability-run-receipt",
    ]
    remote_node = sustainability["workflow"]["nodes"][0]
    assert remote_node["runtime"] == "remote_http"
    assert remote_node["remoteBinding"] == {
        "serviceId": "groundwater-sustainability",
        "operationType": "sustainability.evaluate",
    }
    assert all(
        "mapFactoryAction" not in model and "outputPolicy" not in model
        for model in models.values()
    )
    compose = json.loads((output / "compose.json").read_text())
    lock = json.loads((output / "assembly-lock.json").read_text())
    assert set(compose["services"]) == {
        "gateway",
        "kernel-service",
        "data-db",
        "data-migrate",
        "data-service",
        "data-worker",
        "executor-control",
        "executor-native-worker",
        "executor-qgis-worker",
        "executor-remote_http-worker",
        "knowledge-next-db",
        "knowledge-next-migrate",
        "knowledge-next-service",
        "knowledge-next-worker",
        "knowledge-bootstrap",
    }
    assert all(
        "ports" not in service
        for name, service in compose["services"].items()
        if name != "gateway"
    )
    assert "volumes" not in compose
    assert (
        "MAPFACTORY_SERVICE_URL"
        not in compose["services"]["executor-control"]["environment"]
    )
    assert "location /api/v11/" in (output / "nginx.conf").read_text()
    assert all(
        service["restart"] == "unless-stopped"
        for name, service in compose["services"].items()
        if name not in {"data-migrate", "knowledge-next-migrate", "knowledge-bootstrap"}
    )
    assert compose["services"]["knowledge-bootstrap"]["restart"] == "no"
    assert compose["services"]["knowledge-bootstrap"]["profiles"] == ["bootstrap"]
    domain_ref = json.loads(_manifest("groundwater").read_bytes())["domainPackage"]
    assert lock["domainPackage"]["version"] == domain_ref["version"]
    additions = 8 if domain_ref["version"] == "6.1.0" else 0
    assert lock["knowledgeBootstrap"]["projects"] == ["groundwater-demo"]
    assert lock["knowledgeBootstrap"]["documentCount"] == 103 + additions
    assert lock["knowledgeBootstrap"]["chunkCount"] == 303 + additions
    assert len(lock["knowledgeBootstrap"]["files"]) == (8 if additions else 7)
    bootstrap = compose["services"]["knowledge-bootstrap"]
    assert bootstrap["command"][-2:] == ["--project", "groundwater-demo"]
    for name in (
        "kernel-service",
        "executor-control",
        "executor-native-worker",
        "executor-qgis-worker",
        "knowledge-bootstrap",
    ):
        assert (
            compose["services"][name]["environment"]["PLATFORM_PROJECT_ID"]
            == "groundwater-demo"
        )
    assert (
        compose["services"]["data-service"]["environment"]["PLATFORM_PRODUCT_ID"]
        == "groundwater"
    )
    assert (
        compose["services"]["knowledge-next-service"]["environment"][
            "PLATFORM_PRODUCT_ID"
        ]
        == "groundwater"
    )
    traced_services = {
        "kernel-service",
        "data-service",
        "data-worker",
        "knowledge-next-service",
        "knowledge-next-worker",
        "executor-control",
        "executor-native-worker",
        "executor-qgis-worker",
    }
    assert all(
        compose["services"][name]["environment"]["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"]
        == "${OTEL_EXPORTER_OTLP_TRACES_ENDPOINT:-}"
        for name in traced_services
    )
    data_environment = compose["services"]["data-worker"]["environment"]
    assert data_environment["OPENLINEAGE_URL"] == "${OPENLINEAGE_URL:-}"
    assert data_environment["DATA_QUALITY_WEBHOOK_URL"] == (
        "${DATA_QUALITY_WEBHOOK_URL:-}"
    )
    assert data_environment["MLFLOW_ADAPTER_URL"] == "${MLFLOW_ADAPTER_URL:-}"
    assert "knowledge-bootstrap" not in compose["services"]["kernel-service"].get(
        "depends_on", {}
    )


def test_compilation_is_deterministic() -> None:
    first = compile_product(ROOT, _manifest("groundwater"))
    expected = {path.name: path.read_bytes() for path in first.iterdir()}
    second = compile_product(ROOT, _manifest("groundwater"))
    assert {path.name: path.read_bytes() for path in second.iterdir()} == expected


def test_compose_binds_immutable_bundle(tmp_path) -> None:
    _copy_inputs(tmp_path)
    output = compile_product(
        tmp_path, tmp_path / "products/groundwater/product-manifest.json"
    )
    assert output.is_dir()
    pointer = bundle_pointer_path(tmp_path / ".generated", "groundwater")
    assert pointer.is_file()
    assert not pointer.is_symlink()
    assert current_bundle(tmp_path / ".generated", "groundwater") == output
    compose = json.loads((output / "compose.json").read_text())
    assert any(
        str(output / "kernel-config.json") in item
        for item in compose["services"]["kernel-service"]["volumes"]
    )
    assert (
        str(tmp_path / ".generated/groundwater")
        not in (output / "compose.json").read_text()
    )


def test_compiled_bundle_is_mount_readable_with_private_umask(tmp_path) -> None:
    _copy_inputs(tmp_path)
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import os, sys; from pathlib import Path; "
            "os.umask(0o077); sys.path.insert(0, sys.argv[1]); "
            "sys.path.insert(0, sys.argv[3]); "
            "from assembler import compile_product; "
            "root = Path(sys.argv[2]); "
            "compile_product(root, root / 'products/groundwater/product-manifest.json')",
            str(ROOT / "platform" / "assembler"),
            str(tmp_path),
            str(ROOT / "packages/workflow-compiler/src"),
        ],
        check=True,
    )
    output = current_bundle(tmp_path / ".generated", "groundwater")
    assert {stat.S_IMODE(os.stat(path).st_mode) for path in output.iterdir()} == {0o444}

    # A repeated compile also repairs a byte-identical release made before
    # permissions were normalized, without changing its bound path or bytes.
    config = output / "kernel-config.json"
    original = config.read_bytes()
    config.chmod(0o600)
    assert (
        compile_product(
            tmp_path, tmp_path / "products/groundwater/product-manifest.json"
        )
        == output
    )
    assert config.read_bytes() == original
    assert stat.S_IMODE(os.stat(config).st_mode) == 0o444


def test_failed_pointer_switch_keeps_previous_eight_files(
    tmp_path, monkeypatch
) -> None:
    import assembler

    _copy_inputs(tmp_path)
    manifest_path = tmp_path / "products/groundwater/product-manifest.json"
    first = compile_product(tmp_path, manifest_path)
    previous = {path.name: path.read_bytes() for path in first.iterdir()}
    pointer = bundle_pointer_path(tmp_path / ".generated", "groundwater")
    manifest = json.loads(manifest_path.read_text())
    manifest["publicRoutes"].reverse()
    manifest_path.write_text(json.dumps(manifest))
    actual_replace = assembler.os.replace

    def fail_pointer_switch(source, target):
        if target == pointer:
            raise OSError("injected pointer failure")
        return actual_replace(source, target)

    monkeypatch.setattr(assembler.os, "replace", fail_pointer_switch)
    with pytest.raises(OSError, match="injected pointer failure"):
        compile_product(tmp_path, manifest_path)
    assert current_bundle(tmp_path / ".generated", "groundwater") == first
    assert {path.name: path.read_bytes() for path in first.iterdir()} == previous


def test_failed_release_rename_keeps_previous_eight_files(
    tmp_path, monkeypatch
) -> None:
    _copy_inputs(tmp_path)
    manifest_path = tmp_path / "products/groundwater/product-manifest.json"
    first = compile_product(tmp_path, manifest_path)
    previous = {path.name: path.read_bytes() for path in first.iterdir()}
    manifest = json.loads(manifest_path.read_text())
    manifest["publicRoutes"].reverse()
    manifest_path.write_text(json.dumps(manifest))
    original_replace = Path.replace

    def fail_staging_rename(source, target):
        if source.name.startswith(".tmp-"):
            raise OSError("injected release rename failure")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_staging_rename)
    with pytest.raises(OSError, match="injected release rename failure"):
        compile_product(tmp_path, manifest_path)
    assert current_bundle(tmp_path / ".generated", "groundwater") == first
    assert {path.name: path.read_bytes() for path in first.iterdir()} == previous


def test_old_mutable_output_directory_is_rejected(tmp_path) -> None:
    _copy_inputs(tmp_path)
    pointer = bundle_pointer_path(tmp_path / ".generated", "groundwater")
    pointer.mkdir(parents=True)
    (pointer / "kernel-config.json").write_text("old deployment")
    with pytest.raises(ValueError, match="OUTPUT_PATH_INVALID"):
        compile_product(
            tmp_path, tmp_path / "products/groundwater/product-manifest.json"
        )
    assert (pointer / "kernel-config.json").read_text() == "old deployment"
    assert not (tmp_path / ".generated/.legacy").exists()


def test_path_escape_and_checksum_drift_fail_before_output(tmp_path) -> None:
    _copy_inputs(tmp_path)
    manifest_path = tmp_path / "products/groundwater/product-manifest.json"
    domain = _selected_groundwater_domain(tmp_path)
    domain_path = domain / "domain-manifest.json"
    manifest = json.loads(domain_path.read_text())
    original = domain_path.read_bytes()
    manifest["workflowDefinitions"] = ["../outside.json"]
    domain_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="DOMAIN_PATH_INVALID"):
        compile_product(tmp_path, manifest_path)
    assert not (tmp_path / ".generated").exists()

    domain_path.write_bytes(original)
    model_path = domain / "models/summary.model.json"
    model = json.loads(model_path.read_text())
    model["workflowRef"]["checksum"] = "sha256:" + "0" * 64
    model_path.write_text(json.dumps(model))
    product = json.loads(manifest_path.read_text())
    product["domainPackage"] = load_domain(domain).identity
    manifest_path.write_text(json.dumps(product))
    with pytest.raises(ValueError, match="MODEL_WORKFLOW_CHECKSUM_MISMATCH"):
        compile_product(tmp_path, manifest_path)
    assert not (tmp_path / ".generated").exists()


def test_products_are_discovered_without_allowlist() -> None:
    assert available_products(ROOT) == ("groundwater",)
    assert not (ROOT / "products/reference").exists()
    assert not (ROOT / "products/demo").exists()
    assert not (ROOT / "domains/reference").exists()


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"projectId": "../other"}, "PRODUCT_PROJECT_ID_INVALID"),
        ({"knowledgeProjects": ["other"]}, "KNOWLEDGE_PROJECTS_INVALID"),
        (
            {"services": ["auth", "resource", "executor", "knowledge"]},
            "PRODUCT_SERVICES_INVALID",
        ),
    ],
)
def test_product_cannot_select_another_project_or_auth(tmp_path, change, error):
    _copy_inputs(tmp_path)
    manifest_path = tmp_path / "products/groundwater/product-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.update(change)
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match=error):
        compile_product(tmp_path, manifest_path)
    assert not (tmp_path / ".generated").exists()


def test_many_input_is_rejected_before_assembly_output(tmp_path):
    from assembler import _checksum

    _copy_inputs(tmp_path)
    domain = _selected_groundwater_domain(tmp_path)
    workflow_path = domain / "workflows/summary.workflow.json"
    workflow = json.loads(workflow_path.read_text())
    workflow["inputs"][0]["cardinality"] = "many"
    workflow_path.write_text(json.dumps(workflow))
    model_path = domain / "models/summary.model.json"
    model = json.loads(model_path.read_text())
    model["workflowRef"]["checksum"] = _checksum(workflow)
    model_path.write_text(json.dumps(model))
    product_path = tmp_path / "products/groundwater/product-manifest.json"
    product = json.loads(product_path.read_text())
    product["domainPackage"] = load_domain(domain).identity
    product_path.write_text(json.dumps(product))
    with pytest.raises(ValueError, match="WORKFLOW_CARDINALITY_UNSUPPORTED"):
        compile_product(tmp_path, product_path)
    assert not (tmp_path / ".generated").exists()


def test_single_binding_cannot_feed_many_operator_port_before_output(
    tmp_path, monkeypatch
):
    import assembler

    _copy_inputs(tmp_path)
    load_catalog = assembler._load_operator_catalog

    def many_port(root):
        operators, digest = load_catalog(root)
        workflow = json.loads(
            (
                root / "domains/groundwater/6.0.0/workflows/summary.workflow.json"
            ).read_text()
        )
        node = workflow["nodes"][0]
        operator = operators[(node["operatorId"], node["operatorVersion"])]
        operator["inputs"][0]["cardinality"] = "many"
        return operators, digest

    monkeypatch.setattr(assembler, "_load_operator_catalog", many_port)
    with pytest.raises(ValueError, match="WORKFLOW_CARDINALITY_UNSUPPORTED"):
        compile_product(
            tmp_path, tmp_path / "products/groundwater/product-manifest.json"
        )
    assert not (tmp_path / ".generated").exists()


def test_port_requires_every_possible_media_format_pair() -> None:
    from copy import deepcopy

    from assembler import _compile_workflow, _load_operator_catalog

    operators, _ = _load_operator_catalog(ROOT)
    workflow = json.loads(
        (ROOT / "domains/groundwater/6.0.0/workflows/summary.workflow.json").read_text()
    )
    key = (workflow["nodes"][0]["operatorId"], workflow["nodes"][0]["operatorVersion"])
    narrowed = deepcopy(operators[key])
    narrowed["inputs"][0]["mediaTypes"] = ["application/vnd.apache.parquet"]
    narrowed["inputs"][0]["formats"] = ["parquet"]
    with pytest.raises(ValueError, match="WORKFLOW_PORT_INCOMPATIBLE"):
        _compile_workflow(workflow, {key: narrowed})


def test_reproject_output_contract_is_narrowed_from_bound_vector_input() -> None:
    output = compile_product(ROOT, _manifest("groundwater"))
    executor = json.loads((output / "executor-config.json").read_text())
    transfer = next(
        item
        for item in executor["models"]
        if item["modelId"] == "groundwater.transfer-learning-analysis"
    )
    node = next(
        item
        for item in transfer["workflow"]["nodes"]
        if item["nodeId"] == "boundary-projected"
    )
    assert node["outputContracts"][0]["formatPairs"] == [
        {"mediaType": "application/geopackage+sqlite3", "format": "geopackage"}
    ]


def test_reproject_pointcloud_branch_and_mixed_downstream_boundary() -> None:
    from copy import deepcopy

    from assembler import _compile_workflow, _load_operator_catalog

    operators, _ = _load_operator_catalog(ROOT)
    workflow = {
        "schemaVersion": "workflow.v6",
        "workflowId": "test.reproject",
        "version": "1.0.0",
        "inputs": [
            {
                "name": "data",
                "cardinality": "one",
                "mediaTypes": ["application/vnd.laz"],
                "formats": ["copc"],
            }
        ],
        "parameters": ["targetCrs"],
        "nodes": [
            {
                "nodeId": "project",
                "operatorId": "crs.reproject",
                "operatorVersion": "1.0.0",
                "inputs": {"data": {"workflowInput": "data"}},
                "parameters": {"targetCrs": {"workflowParameter": "targetCrs"}},
            }
        ],
        "outputs": [
            {"name": "data", "nodeOutput": {"nodeId": "project", "port": "data"}}
        ],
    }
    compiled = _compile_workflow(workflow, operators)
    assert compiled["nodes"][0]["outputContracts"][0]["formatPairs"] == [
        {"mediaType": "application/vnd.laz", "format": "copc"}
    ]

    mixed = deepcopy(workflow)
    mixed["inputs"][0]["mediaTypes"] = [
        "application/geopackage+sqlite3",
        "application/vnd.laz",
    ]
    mixed["inputs"][0]["formats"] = ["geopackage", "copc"]
    narrow = deepcopy(operators[("crs.reproject", "1.0.0")])
    narrow["inputs"][0]["mediaTypes"] = ["application/geopackage+sqlite3"]
    narrow["inputs"][0]["formats"] = ["geopackage"]
    mixed["nodes"].append(
        {
            "nodeId": "narrow",
            "operatorId": "test.narrow",
            "operatorVersion": "1.0.0",
            "inputs": {"data": {"nodeOutput": {"nodeId": "project", "port": "data"}}},
            "parameters": {"targetCrs": {"workflowParameter": "targetCrs"}},
        }
    )
    mixed["outputs"] = [
        {"name": "data", "nodeOutput": {"nodeId": "narrow", "port": "data"}}
    ]
    with pytest.raises(ValueError, match="WORKFLOW_PORT_INCOMPATIBLE"):
        _compile_workflow(mixed, {**operators, ("test.narrow", "1.0.0"): narrow})


def test_unreachable_workflow_node_fails_before_output(tmp_path):
    _copy_inputs(tmp_path)
    domain = _selected_groundwater_domain(tmp_path)
    workflow_path = domain / "workflows/summary.workflow.json"
    workflow = json.loads(workflow_path.read_text())
    orphan = {**workflow["nodes"][0], "nodeId": "orphan"}
    workflow["nodes"].append(orphan)
    workflow_path.write_text(json.dumps(workflow))
    manifest_path = _pin_domain(tmp_path, "groundwater", domain)

    with pytest.raises(ValueError, match="WORKFLOW_NODE_UNREACHABLE"):
        compile_product(tmp_path, manifest_path)
    assert not (tmp_path / ".generated").exists()


def test_workflow_node_order_does_not_change_valid_graph(tmp_path):
    _copy_inputs(tmp_path)
    domain = _selected_groundwater_domain(tmp_path)
    workflow_path = domain / "workflows/flow-field.workflow.json"
    workflow = json.loads(workflow_path.read_text())
    workflow["nodes"].reverse()
    workflow_path.write_text(json.dumps(workflow))
    model_path = domain / "models/flow-field.model.json"
    model = json.loads(model_path.read_text())
    model["workflowRef"]["checksum"] = _checksum(workflow)
    model_path.write_text(json.dumps(model))
    manifest_path = _pin_domain(tmp_path, "groundwater", domain)

    output = compile_product(tmp_path, manifest_path)
    executor = json.loads((output / "executor-config.json").read_text())
    flow = next(
        item
        for item in executor["models"]
        if item["modelId"] == "groundwater.flow-field"
    )
    assert [node["nodeId"] for node in flow["workflow"]["nodes"]] == [
        node["nodeId"] for node in workflow["nodes"]
    ]


def test_cycle_with_forward_reference_fails_before_output(tmp_path):
    _copy_inputs(tmp_path)
    domain = _selected_groundwater_domain(tmp_path)
    workflow_path = domain / "workflows/flow-field.workflow.json"
    workflow = json.loads(workflow_path.read_text())
    workflow["nodes"][0]["inputs"]["observations"] = {
        "nodeOutput": {"nodeId": "snapshot", "port": "snapshot"}
    }
    workflow_path.write_text(json.dumps(workflow))
    manifest_path = _pin_domain(tmp_path, "groundwater", domain)

    with pytest.raises(ValueError, match="WORKFLOW_CYCLE"):
        compile_product(tmp_path, manifest_path)
    assert not (tmp_path / ".generated").exists()


@pytest.mark.parametrize(
    ("missing", "error"),
    [
        ("input", "MODEL_INPUT_BINDING_INVALID"),
        ("parameter_requirement", "MODEL_PARAMETER_BINDING_INVALID"),
        ("parameter_default", "MODEL_PARAMETER_BINDING_INVALID"),
    ],
)
def test_model_binding_needs_required_argument_or_default(tmp_path, missing, error):
    _copy_inputs(tmp_path)
    model_name = "summary" if missing == "input" else "flow-field"
    domain = _selected_groundwater_domain(tmp_path)
    model_path = domain / "models" / f"{model_name}.model.json"
    model = json.loads(model_path.read_text())
    if missing == "input":
        model["tool"]["inputSchema"]["required"].remove("versionId")
    elif missing == "parameter_requirement":
        model["tool"]["inputSchema"]["required"].remove("pixelSize")
    else:
        del model["defaultParameters"]["depthToWaterUnit"]
    model_path.write_text(json.dumps(model))
    manifest_path = _pin_domain(tmp_path, "groundwater", domain)

    with pytest.raises(ValueError, match=error):
        compile_product(tmp_path, manifest_path)
    assert not (tmp_path / ".generated").exists()


def test_model_input_default_covers_optional_bound_argument(tmp_path):
    _copy_inputs(tmp_path)
    domain = _selected_groundwater_domain(tmp_path)
    model_path = domain / "models/summary.model.json"
    model = json.loads(model_path.read_text())
    model["tool"]["inputSchema"]["required"].remove("versionId")
    model["defaultParameters"]["versionId"] = "fixed-version-id"
    model_path.write_text(json.dumps(model))
    manifest_path = _pin_domain(tmp_path, "groundwater", domain)

    assert (compile_product(tmp_path, manifest_path) / "executor-config.json").exists()
