"""Assemble the porous simulation workflow in an isolated source copy."""

import hashlib
import json
from pathlib import Path
import shutil

from assembler_manifests import load_domain

SERVICE = "porous-groundwater"
HOST = "porous-groundwater.local"
MODEL = "groundwater.porous-simulation"
TOOL = "run_porous_simulation"
INPUT = "projectPackageVersionId"
OPERATIONS = (
    "model.compile",
    "flow_transport.simulate",
    "result.analyze",
    "result.render",
)
EXTRA_OPERATIONS = (
    "pest.calibrate", "scenario.compare", "map.prepare",
    "inversion.train", "inversion.sample", "agri.invert",
)
FORMATS = {
    "zip": ("application/zip", "metadata"), "json": ("application/json", "json"),
    "png": ("image/png", "image"), "geotiff": ("image/tiff", "map"),
    "binary": ("application/octet-stream", "metadata"), "npy": ("application/x-npy", "metadata"),
}
OUTPUTS = (
    ("porous-model-package", "compile", "model_package", "已编译的模型包", "zip"),
    ("porous-model-validation", "compile", "validation_report", "模型校验报告", "json"),
    ("porous-calculation-summary", "simulate", "calculation_summary", "仿真摘要", "json"),
    ("porous-effective-parameters", "simulate", "effective_parameters", "有效模型参数", "json"),
    ("porous-result-package", "simulate", "result_package", "水头与浓度结果包", "zip"),
    ("porous-analysis", "analyze", "analysis_table", "按时段和层统计的结果", "json"),
    ("porous-figure", "render", "rendered_figure", "最终时刻结果图", "png"),
    ("porous-vtk-package", "render", "vtu_pvd_package", "VTU 和 PVD 文件包", "zip"),
    ("porous-compile-receipt", "compile", "run_receipt", "模型编译运行凭据", "json"),
    ("porous-simulation-receipt", "simulate", "run_receipt", "仿真运行凭据", "json"),
    ("porous-analysis-receipt", "analyze", "run_receipt", "分析运行凭据", "json"),
    ("porous-render-receipt", "render", "run_receipt", "制图运行凭据", "json"),
)


def _write(path: Path, value: dict) -> bytes:
    content = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    path.write_bytes(content)
    path.chmod(0o644)
    return content


def _node(name: str, operation: str, port: str, binding: dict) -> dict:
    return {
        "nodeId": name,
        "operatorId": "groundwater.porous." + operation.replace(".", "_"),
        "operatorVersion": "1.0.0",
        "inputs": {port: binding},
        "parameters": {},
    }


def _publication(title: str, format_: str) -> dict:
    return {
        "title": title,
        "format": format_,
        "mediaType": FORMATS[format_][0],
        "policy": "FILE_ONLY",
        "semantics": {
            "description": title,
            "fields": {},
            "grain": None,
            "time_range": None,
            "timezone": None,
            "crs": None,
            "vertical_datum": None,
            "limitations": ["现场科学等价待验证。"],
            "source": "porous-groundwater Linux v2.6.2 工作流",
        },
    }


def _bind(product: dict, domain: Path, product_path: Path, ui_file: Path, *, full: bool) -> None:
    product["domainPackage"] = load_domain(domain).identity
    product["remoteServices"].append({
        "serviceId": SERVICE,
        "baseUrlEnvironment": "POROUS_GROUNDWATER_BASE_URL",
        "allowedHostEnvironment": "POROUS_GROUNDWATER_ALLOWED_HOST",
        "credentialSecret": f"{SERVICE}-api-key",
        "caBundleSecret": f"{SERVICE}-ca-bundle",
        "connectTimeoutSeconds": 10,
        "pollIntervalSeconds": 2,
        "apiVersion": "remote-operation.v1",
        "allowedServiceVersions": ["1.1.1"],
        "allowedModelVersions": ["porous-groundwater-v2.6.2-linux.v2"],
        "allowedOperations": list(OPERATIONS + (EXTRA_OPERATIONS if full else ())),
    })
    product["uiProfile"] = {
        "path": ui_file.relative_to(product_path.parent).as_posix(),
        "checksum": f"sha256:{hashlib.sha256(ui_file.read_bytes()).hexdigest()}",
    }
    _write(product_path, product)


def stage_candidate(source: Path, *, full: bool = False) -> None:
    product_path = source / "products/groundwater/product-manifest.json"
    product = json.loads(product_path.read_bytes())
    base_version = product["domainPackage"]["version"]
    if base_version == "6.1.0" and any(
        binding["serviceId"] == SERVICE
        and set(binding["allowedOperations"]) == set(OPERATIONS + EXTRA_OPERATIONS)
        for binding in product["remoteServices"]
    ):
        return
    if base_version != "6.0.0":
        raise ValueError("POROUS_CANDIDATE_BASE_VERSION_UNEXPECTED")
    domain_root = source / "domains/groundwater"
    domain = domain_root / "6.1.0"
    base_ui = product_path.parent / product["uiProfile"]["path"]
    ui_file = base_ui.with_name("product-ui-porous.v1.json")
    if domain.exists():
        if not full or not (domain / "models/porous-agricultural-inversion.model.json").is_file():
            raise ValueError("POROUS_DOMAIN_VERSION_CONFLICT")
        _bind(product, domain, product_path, ui_file, full=True)
        return
    shutil.copytree(domain_root / base_version, domain)

    workflow = {
        "schemaVersion": "workflow.v6",
        "workflowId": MODEL,
        "version": "1.0.0",
        "inputs": [{
            "name": "project_package", "cardinality": "one",
            "mediaTypes": ["application/zip"], "formats": ["zip"],
        }],
        "parameters": [],
        "nodes": [
            _node("compile", OPERATIONS[0], "project_package", {"workflowInput": "project_package"}),
            _node("simulate", OPERATIONS[1], "model_package", {"nodeOutput": {
                "nodeId": "compile", "port": "model_package",
            }}),
            _node("analyze", OPERATIONS[2], "result_package", {"nodeOutput": {
                "nodeId": "simulate", "port": "result_package",
            }}),
            _node("render", OPERATIONS[3], "result_package", {"nodeOutput": {
                "nodeId": "simulate", "port": "result_package",
            }}),
        ],
        "outputs": [
            {"name": name, "nodeOutput": {"nodeId": node, "port": port}}
            for name, node, port, _, _ in OUTPUTS
        ],
    }
    description = "编译孔隙型地下水模型，计算地下水流与溶质运移，汇总结果并生成图件。"
    checksum = hashlib.sha256(
        json.dumps(workflow, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    model = {
        "schemaVersion": "executable-model.v6",
        "modelId": MODEL,
        "version": "1.0.0",
        "workflowRef": {"workflowId": MODEL, "version": "1.0.0", "checksum": f"sha256:{checksum}"},
        "tool": {
            "name": TOOL,
            "description": description,
            "inputSchema": {
                "type": "object",
                "properties": {INPUT: {"type": "string", "minLength": 1}},
                "required": [INPUT],
                "additionalProperties": False,
            },
        },
        "inputBindings": {"project_package": INPUT},
        "fieldRequirements": [],
        "semanticRequirements": [],
        "defaultParameters": {},
        "parameterBindings": {},
        "deadlineSeconds": 600,
        "maxParallelNodes": 2,
        "finalOutputs": [item[0] for item in OUTPUTS],
        "executionService": "executor",
        "executorId": "workflow-dag",
        "outputPublications": {
            name: _publication(title, format_) for name, _, _, title, format_ in OUTPUTS
        },
    }
    workflow_file = "workflows/porous-simulation.workflow.json"
    model_file = "models/porous-simulation.model.json"
    _write(domain / workflow_file, workflow)
    _write(domain / model_file, model)
    domain_manifest_path = domain / "domain-manifest.json"
    domain_manifest = json.loads(domain_manifest_path.read_bytes())
    domain_manifest["version"] = domain.name
    domain_manifest["workflowDefinitions"].append(workflow_file)
    domain_manifest["executableModels"].append(model_file)
    _write(domain_manifest_path, domain_manifest)
    ui = json.loads(base_ui.read_bytes())
    ui["parameterNames"][INPUT] = {"label": "项目包版本", "group": "模型输入"}
    ui["models"][MODEL] = {
        "displayName": "孔隙型地下水流与溶质运移",
        "description": description,
        "applicability": "用于核对合成算例与候选 Linux 服务。现场科学等价待验证。",
        "inputs": {INPUT: {
            "label": "孔隙型模型项目 ZIP",
            "description": "包含声明式模型定义的已发布 ZIP 数据版本。",
            "formats": ["zip"],
        }},
        "parameterOverrides": {},
        "confirmationSummary": [],
    }
    for name, _, _, title, format_ in OUTPUTS:
        _, viewer = FORMATS[format_]
        ui["outputs"][name] = {"label": title, "viewer": viewer}
    if full:
        from products.groundwater.scripts.porous_declarations import append_workflows

        append_workflows(domain, domain_manifest, ui)
        _write(domain_manifest_path, domain_manifest)
    _write(ui_file, ui)
    _bind(product, domain, product_path, ui_file, full=full)
