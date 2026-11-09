from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from zipfile import ZipFile

import pytest

from assembler import _canonical, available_products, compile_product, load_domain, pack_domain
from platform_kernel.agent_runtime.prompts import build_instructions
from platform_knowledge_next.package_conversion import convert_domain_package


ROOT = Path(__file__).resolve().parents[2]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


@pytest.fixture
def source(tmp_path):
    for path in (ROOT / "kernel/module.yaml", *sorted((ROOT / "services").glob("*/module.yaml"))):
        destination = tmp_path / path.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        contracts = path.parent / "contracts"
        if contracts.is_dir():
            shutil.copytree(contracts, destination.parent / "contracts")
        packaged_contracts = path.parent / "src" / "platform_knowledge_next" / "contracts"
        if packaged_contracts.is_dir():
            shutil.copytree(
                packaged_contracts,
                destination.parent / "src" / "platform_knowledge_next" / "contracts",
            )
    for name in ("domains", "products", "packages/operator-pack/catalog", "packages/operator-pack/src"):
        shutil.copytree(ROOT / name, tmp_path / name, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(ROOT / "system-deps.lock.json", tmp_path / "system-deps.lock.json")
    return tmp_path


def pin(source, name):
    path = source / f"products/{name}/product-manifest.json"
    product = json.loads(path.read_text())
    ref = product["domainPackage"]
    product["domainPackage"] = load_domain(source / "domains" / ref["domainId"] / ref["version"]).identity
    write(path, product)
    return path


def test_release_is_deterministic_portable_and_contains_only_declared_files(source, tmp_path):
    directory = source / "domains/groundwater/6.0.0"
    (directory / "unlisted.py").write_text("raise RuntimeError('never packaged')")
    domain = load_domain(directory)
    first, second = tmp_path / "first.zip", tmp_path / "second.zip"
    pack_domain(domain, first)
    pack_domain(domain, second)
    assert first.read_bytes() == second.read_bytes()
    with ZipFile(first) as archive:
        assert archive.namelist() == sorted(domain.files)
        assert "unlisted.py" not in archive.namelist()
        archive.extractall(tmp_path / "release")
    assert load_domain(tmp_path / "release").identity == domain.identity
    shutil.rmtree(directory)
    shutil.copytree(tmp_path / "release", directory)
    compile_product(source, "groundwater/product-manifest.json")


def test_two_products_reuse_one_package_without_copying_definitions(source):
    original = source / "products/groundwater/product-manifest.json"
    product = json.loads(original.read_text())
    product["productId"] = "second-product"
    target = source / "products/second-product"
    shutil.copytree(source / "products/groundwater/ui", target / "ui")
    ui_path = target / "ui/product-ui.json"
    ui = json.loads(ui_path.read_text())
    ui["productId"] = "second-product"
    write(ui_path, ui)
    product["uiProfile"]["checksum"] = "sha256:" + hashlib.sha256(ui_path.read_bytes()).hexdigest()
    write(target / "product-manifest.json", product)
    assert available_products(source) == ("groundwater", "second-product")
    outputs = [compile_product(source, f"{name}/product-manifest.json") for name in ("groundwater", "second-product")]
    configs = [json.loads((out / "kernel-config.json").read_text()) for out in outputs]
    locks = [json.loads((out / "assembly-lock.json").read_text()) for out in outputs]
    assert configs[0]["productId"] != configs[1]["productId"]
    for key in ("systemPrompt", "models", "observationTools"):
        assert configs[0][key] == configs[1][key]
    assert locks[0]["domainPackage"] == locks[1]["domainPackage"]
    assert outputs[0] != outputs[1]
    assert {path.name for path in target.iterdir()} == {"product-manifest.json", "ui"}


@pytest.mark.parametrize("file", ["instructions.md", "models/summary.model.json", "workflows/summary.workflow.json"])
def test_pinned_package_rejects_any_changed_file_before_output(source, file):
    path = source / "domains/groundwater/6.0.0" / file
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="DOMAIN_IDENTITY_MISMATCH"):
        compile_product(source, "groundwater/product-manifest.json")
    assert not (source / ".generated").exists()


@pytest.mark.parametrize("alpha", [0, 1])
def test_model_default_respects_exclusive_schema_bounds_before_output(source, alpha):
    path = source / "domains/groundwater/6.0.0/models/monitoring-trend.model.json"
    model = json.loads(path.read_text())
    model["defaultParameters"]["alpha"] = alpha
    write(path, model)
    pin(source, "groundwater")
    with pytest.raises(ValueError, match="MODEL_PARAMETER_DEFAULT_INVALID"):
        compile_product(source, "groundwater/product-manifest.json")
    assert not (source / ".generated").exists()


def test_model_default_respects_pattern_before_output(source):
    path = source / "domains/groundwater/6.0.0/models/transfer-learning-analysis.model.json"
    model = json.loads(path.read_text())
    model["defaultParameters"]["boundary-projected.targetCrs"] = "EPSG 32650"
    write(path, model)
    pin(source, "groundwater")
    with pytest.raises(ValueError, match="MODEL_PARAMETER_DEFAULT_INVALID"):
        compile_product(source, "groundwater/product-manifest.json")
    assert not (source / ".generated").exists()


@pytest.mark.parametrize("value,schema", [
    (7, {"type": "integer"}),
    ("unsupported", {"type": "string"}),
])
def test_model_default_must_satisfy_bound_operator_schema(source, value, schema):
    path = source / "domains/groundwater/6.0.0/models/summary.model.json"
    model = json.loads(path.read_text())
    model["tool"]["inputSchema"]["properties"]["axis"] = schema
    model["defaultParameters"]["axis"] = value
    write(path, model)
    pin(source, "groundwater")

    with pytest.raises(ValueError, match="MODEL_PARAMETER_DEFAULT_INVALID"):
        compile_product(source, "groundwater/product-manifest.json")
    assert not (source / ".generated").exists()


def test_model_default_must_satisfy_every_downstream_parameter(source):
    directory = source / "domains/groundwater/6.0.0"
    workflow_path = directory / "workflows/summary.workflow.json"
    workflow = json.loads(workflow_path.read_text())
    workflow["nodes"][0]["parameters"]["axis"] = {"workflowParameter": "column"}
    write(workflow_path, workflow)
    model_path = directory / "models/summary.model.json"
    model = json.loads(model_path.read_text())
    model["defaultParameters"]["column"] = "measurement"
    model["workflowRef"]["checksum"] = "sha256:" + hashlib.sha256(
        _canonical(workflow)
    ).hexdigest()
    write(model_path, model)
    pin(source, "groundwater")

    with pytest.raises(ValueError, match="MODEL_PARAMETER_DEFAULT_INVALID"):
        compile_product(source, "groundwater/product-manifest.json")
    assert not (source / ".generated").exists()


def test_invalid_model_json_schema_fails_before_output(source):
    path = source / "domains/groundwater/6.0.0/models/monitoring-trend.model.json"
    model = json.loads(path.read_text())
    model["tool"]["inputSchema"]["properties"]["alpha"]["exclusiveMinimum"] = "zero"
    write(path, model)
    pin(source, "groundwater")
    with pytest.raises(ValueError, match="MODEL_INPUT_SCHEMA_INVALID"):
        compile_product(source, "groundwater/product-manifest.json")
    assert not (source / ".generated").exists()


def test_operator_revision_covers_manifest_and_source_digest(source):
    path = source / "packages/operator-pack/catalog/operators/statistics.describe/1.0.0/manifest.json"
    manifest = json.loads(path.read_text())
    manifest["timeoutSeconds"] += 1
    write(path, manifest)
    with pytest.raises(ValueError, match="OPERATOR_IMPLEMENTATION_DRIFT"):
        compile_product(source, "groundwater/product-manifest.json")
    assert not (source / ".generated").exists()

    lock_path = source / "packages/operator-pack/catalog/implementation-lock.json"
    lock = json.loads(lock_path.read_text())
    entry = lock["operators"]["statistics.describe@1.0.0"]
    entry["implementationRevision"] = "sha256:" + hashlib.sha256(
        _canonical(manifest) + entry["sourceDigest"].encode("ascii")
    ).hexdigest()
    write(lock_path, lock)
    compile_product(source, "groundwater/product-manifest.json")


@pytest.mark.parametrize("path", ["../outside.json", "/tmp/outside.json", "./instructions.md", "instructions.md"])
def test_domain_paths_cannot_escape_or_alias(source, path):
    manifest_path = source / "domains/groundwater/6.0.0/domain-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["workflowDefinitions"] = [path]
    write(manifest_path, manifest)
    with pytest.raises(ValueError, match="DOMAIN_PATH_INVALID"):
        compile_product(source, "groundwater/product-manifest.json")
    assert not (source / ".generated").exists()


def test_symlink_escape_is_rejected(source):
    directory = source / "domains/groundwater/6.0.0"
    external = source / "outside.md"
    external.write_text("not in the package")
    (directory / "instructions.md").unlink()
    (directory / "instructions.md").symlink_to(external)
    with pytest.raises(ValueError, match="DOMAIN_PATH_INVALID"):
        compile_product(source, "groundwater/product-manifest.json")


@pytest.mark.parametrize("mutation,error", [
    ("old_product", "PRODUCT_MANIFEST_VERSION_INVALID"),
    ("old_domain", "DOMAIN_MANIFEST_VERSION_INVALID"),
    ("projects", "KNOWLEDGE_PROJECTS_INVALID"),
    ("blank_project", "KNOWLEDGE_PROJECTS_INVALID"),
    ("knowledge_service", "DOMAIN_KNOWLEDGE_SERVICE_MISSING"),
    ("retired_model_field", "EXECUTABLE_MODEL_FIELDS_INVALID"),
    ("duplicate_model", "EXECUTABLE_MODEL_ID_INVALID"),
])
def test_contract_failures_never_publish_output(source, mutation, error):
    product_path = source / "products/groundwater/product-manifest.json"
    product = json.loads(product_path.read_text())
    directory = source / "domains/groundwater" / product["domainPackage"]["version"]
    domain_path = directory / "domain-manifest.json"
    manifest = json.loads(domain_path.read_text())
    if mutation == "old_product":
        product["schemaVersion"] = "product-manifest.v3"
    elif mutation == "old_domain":
        manifest["schemaVersion"] = "domain-manifest.v0"
    elif mutation == "projects":
        product["knowledgeProjects"] = []
    elif mutation == "blank_project":
        product["knowledgeProjects"] = [" "]
    elif mutation == "knowledge_service":
        product["services"].remove("knowledge-next")
    elif mutation == "retired_model_field":
        model_path = directory / manifest["executableModels"][0]
        model = json.loads(model_path.read_text())
        model["mapFactoryAction"] = None
        write(model_path, model)
    else:
        shutil.copy2(directory / manifest["executableModels"][0], directory / "duplicate.model.json")
        manifest["executableModels"].append("duplicate.model.json")
    write(product_path, product)
    write(domain_path, manifest)
    if mutation != "old_domain":
        pin(source, "groundwater")
    with pytest.raises(ValueError, match=error):
        compile_product(source, product_path)
    assert not (source / ".generated").exists()


def test_bootstrap_is_independent_and_does_not_expose_domain_to_runtime(source):
    out = compile_product(source, "groundwater/product-manifest.json")
    compose = json.loads((out / "compose.json").read_text())["services"]
    lock = json.loads((out / "assembly-lock.json").read_text())
    assert lock["schemaVersion"] == "assembly-lock.v8"
    assert len(lock["knowledgeBootstrap"]["documents"]) == 99
    domain_version = lock["domainPackage"]["version"]
    domain_root = source / "domains/groundwater" / domain_version
    manifest = json.loads((domain_root / "domain-manifest.json").read_text())
    documents = []
    for index, relative in enumerate(manifest["knowledgeDocuments"]):
        package = convert_domain_package(
            json.loads((domain_root / relative).read_text()),
            project="groundwater-demo", idempotency_key=f"part-{index}",
        )
        documents.extend(package.documents)
    assert len(documents) == lock["knowledgeBootstrap"]["documentCount"] == 99
    assert sum(len(document.units) for document in documents) == 291
    assert lock["knowledgeBootstrap"]["chunkCount"] == 291
    assert compose["knowledge-bootstrap"]["restart"] == "no"
    assert compose["knowledge-bootstrap"]["profiles"] == ["bootstrap"]
    assert all(volume.endswith(":ro") for volume in compose["knowledge-bootstrap"]["volumes"])
    assert "knowledge-bootstrap" not in compose["kernel-service"].get(
        "depends_on", {}
    )
    assert compose["knowledge-bootstrap"]["depends_on"] == {
        "knowledge-next-service": {"condition": "service_healthy"},
        "knowledge-next-worker": {"condition": "service_started"},
    }
    for name in ("kernel-service", "executor-control", "executor-native-worker", "executor-qgis-worker"):
        assert all("/domain" not in volume for volume in compose[name]["volumes"])


def test_native_only_declarations_select_only_required_services(source):
    directory = source / "domains/groundwater/6.0.0"
    domain_path = directory / "domain-manifest.json"
    domain = json.loads(domain_path.read_text())
    domain["workflowDefinitions"] = ["workflows/summary.workflow.json"]
    domain["executableModels"] = ["models/summary.model.json"]
    domain["observationTools"] = []
    domain["knowledgeDocuments"] = []
    domain["executionCases"] = []
    write(domain_path, domain)
    product_path = source / "products/groundwater/product-manifest.json"
    product = json.loads(product_path.read_text())
    product["services"] = ["data", "executor"]
    product["publicRoutes"] = [route for route in product["publicRoutes"] if route["service"] in product["services"]]
    product["knowledgeProjects"] = []
    ui_path = source / "products/groundwater/ui/product-ui.json"
    ui = json.loads(ui_path.read_text())
    ui["models"] = {"groundwater.summary": ui["models"]["groundwater.summary"]}
    ui["parameterNames"] = {
        name: ui["parameterNames"][name]
        for name in ("versionId", "axis", "column")
    }
    ui["parameterStages"] = {}
    summary_model = json.loads((directory / "models/summary.model.json").read_text())
    ui["outputs"] = {name: ui["outputs"][name] for name in summary_model["finalOutputs"]}
    ui["cases"] = {}
    write(ui_path, ui)
    product["uiProfile"]["checksum"] = "sha256:" + hashlib.sha256(ui_path.read_bytes()).hexdigest()
    write(product_path, product)
    pin(source, "groundwater")

    out = compile_product(source, product_path)
    compose = json.loads((out / "compose.json").read_text())
    executor = json.loads((out / "executor-config.json").read_text())
    assert set(compose["services"]) == {
        "gateway", "kernel-service", "data-db", "data-migrate", "data-service",
        "data-worker",
        "executor-control", "executor-native-worker",
    }
    assert set(executor["workerRuntimes"]) == {"native"}
    assert "PLATFORM_EMBEDDING_BASE_URL" not in json.dumps(compose)


def test_kernel_prompt_has_no_capability_specific_policy():
    prompt = build_instructions("domain instructions", (), (), "{}")
    for phrase in ("retrieve_evidence", "HEAD_SAMPLE", "field mappings"):
        assert phrase not in prompt
    assert "record_plan" in prompt and "untrusted evidence" in prompt


def test_retired_domain_profile_field_is_rejected(source):
    directory = source / "domains/groundwater/6.0.0"
    path = directory / "domain-manifest.json"
    manifest = json.loads(path.read_text())
    manifest["mapFactoryProfile"] = None
    write(path, manifest)
    with pytest.raises(ValueError, match="DOMAIN_MANIFEST_FIELDS_INVALID"):
        compile_product(source, "groundwater/product-manifest.json")
    assert not (source / ".generated").exists()


@pytest.mark.parametrize("field,value", [("mapFactoryAction", None), ("outputPolicy", "namedResources")])
def test_retired_model_fields_are_rejected(source, field, value):
    path = source / "domains/groundwater/6.0.0/models/summary.model.json"
    model = json.loads(path.read_text())
    model[field] = value
    write(path, model)
    pin(source, "groundwater")
    with pytest.raises(ValueError, match="EXECUTABLE_MODEL_FIELDS_INVALID"):
        compile_product(source, "groundwater/product-manifest.json")
    assert not (source / ".generated").exists()
