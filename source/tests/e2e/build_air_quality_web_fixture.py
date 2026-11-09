"""Build the browser-only product fixture through the real product assembler.

The source product is created in an isolated temporary tree.  Only immutable
assembler outputs needed by Playwright are copied to the requested directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "platform/assembler"))
sys.path.insert(0, str(ROOT / "packages/workflow-compiler/src"))

from assembler import _checksum, compile_product, load_domain  # noqa: E402


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _copy_assembly_inputs(target: Path) -> None:
    shutil.copy2(ROOT / "system-deps.lock.json", target / "system-deps.lock.json")
    for path in (
        ROOT / "kernel/module.yaml",
        *sorted((ROOT / "services").glob("*/module.yaml")),
    ):
        copied = target / path.relative_to(ROOT)
        copied.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, copied)
        contracts = path.parent / "contracts"
        if contracts.is_dir():
            shutil.copytree(contracts, copied.parent / "contracts")
        packaged = path.parent / "src/platform_knowledge_next/contracts"
        if packaged.is_dir():
            shutil.copytree(
                packaged,
                copied.parent / "src/platform_knowledge_next/contracts",
            )
    for name in ("catalog", "src"):
        shutil.copytree(
            ROOT / "packages/operator-pack" / name,
            target / "packages/operator-pack" / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    shutil.copytree(ROOT / "products", target / "products")
    shutil.copytree(ROOT / "domains", target / "domains")


def _create_air_quality_product(root: Path) -> Path:
    groundwater = root / "domains/groundwater/6.0.0"
    domain = root / "domains/air-quality/1.0.0"
    (domain / "models").mkdir(parents=True)
    (domain / "workflows").mkdir()
    (domain / "cases").mkdir()
    groundwater_domain = _read(groundwater / "domain-manifest.json")
    _write(
        domain / "domain-manifest.json",
        {
            "schemaVersion": "domain-manifest.v3",
            "domainId": "air-quality",
            "version": "1.0.0",
            "instructions": "instructions.md",
            "observationTools": [
                tool
                for tool in groundwater_domain["observationTools"]
                if tool["service"] == "data"
            ],
            "workflowDefinitions": ["workflows/summary.workflow.json"],
            "executableModels": ["models/summary.model.json"],
            "knowledgeDocuments": [],
            "executionCases": ["cases/city-stations.json"],
        },
    )
    (domain / "instructions.md").write_text(
        "你是空气质量监测助手。只汇总颗粒物观测，不推断空气质量等级。",
        encoding="utf-8",
    )

    workflow = _read(groundwater / "workflows/summary.workflow.json")
    workflow["workflowId"] = "air-quality.summary"
    _write(domain / "workflows/summary.workflow.json", workflow)
    model = _read(groundwater / "models/summary.model.json")
    model["modelId"] = "air-quality.summary"
    model["workflowRef"].update(
        workflowId="air-quality.summary", checksum=_checksum(workflow)
    )
    model["tool"]["name"] = "run_air_quality_summary"
    model["tool"]["description"] = "汇总指定数据版本中的 PM2.5 或 PM10 观测统计。"
    model["tool"]["inputSchema"]["properties"]["column"]["enum"] = [
        "pm25",
        "pm10",
    ]
    custom_parameters = [f"calibrationOption{index}" for index in range(25)]
    model["tool"]["inputSchema"]["properties"].update(
        {name: {"type": "string", "minLength": 1} for name in custom_parameters}
    )
    model["defaultParameters"]["column"] = "pm25"
    _write(domain / "models/summary.model.json", model)
    _write(
        domain / "cases/city-stations.json",
        {
            "schemaVersion": "execution-case.v1",
            "caseId": "city-stations",
            "aliases": ["城市站点对比"],
            "modelRef": {
                "modelId": "air-quality.summary",
                "version": model["version"],
            },
            "datasetTag": "case:city-stations",
            "inputRoles": [{"role": "observations", "parameter": "versionId"}],
            "defaultProfile": "standard",
            "profiles": {"standard": {}},
        },
    )

    product_dir = root / "products/air-quality"
    product_dir.mkdir()
    shutil.copytree(root / "products/groundwater/ui", product_dir / "ui")
    profile_path = product_dir / "ui/product-ui.json"
    profile = _read(profile_path)
    profile["productId"] = "air-quality"
    profile["brand"].update(
        productName="空气质量分析",
        workspaceName="空气质量工作区",
        assistantName="空气质量助手",
        welcome="想了解哪些空气质量数据？",
    )
    profile["examples"] = [
        {
            "id": "summarize-particles",
            "title": "概览颗粒物",
            "prompt": "请汇总 PM2.5 观测。",
            "icon": "chart",
            "featured": True,
        }
    ]
    summary = profile["models"]["groundwater.summary"]
    summary.update(
        displayName="颗粒物概览",
        description="汇总 PM2.5 或 PM10 观测的描述统计。",
        applicability="适用于表格形式的颗粒物观测。",
    )
    summary["inputs"]["versionId"].update(
        label="空气质量观测表",
        description="一个精确且可查询的站点观测版本。",
    )
    profile["models"] = {"air-quality.summary": summary}
    profile["parameterNames"]["column"].update(
        label="颗粒物字段", values={"pm25": "PM2.5", "pm10": "PM10"}
    )
    profile["parameterNames"] = {
        name: profile["parameterNames"][name]
        for name in ("versionId", "axis", "column")
    }
    profile["parameterNames"].update(
        {
            name: {"label": f"校准参数 {index + 1}", "group": "气象校准"}
            for index, name in enumerate(custom_parameters)
        }
    )
    profile["parameterStages"] = {}
    profile["outputs"] = {"summary": {"label": "颗粒物统计摘要", "viewer": "table"}}
    profile["cases"] = {
        "city-stations": {
            "label": "城市站点对比",
            "profiles": {"standard": "标准统计"},
        }
    }
    _write(profile_path, profile)

    product = _read(root / "products/groundwater/product-manifest.json")
    product.update(productId="air-quality", projectId="air-demo")
    product["services"] = ["data", "executor"]
    product["publicRoutes"] = [
        route
        for route in product["publicRoutes"]
        if route["service"] in product["services"]
    ]
    product["knowledgeProjects"] = []
    product["domainPackage"] = load_domain(domain).identity
    product["uiProfile"]["checksum"] = (
        "sha256:" + hashlib.sha256(profile_path.read_bytes()).hexdigest()
    )
    manifest = product_dir / "product-manifest.json"
    _write(manifest, product)
    return manifest


def build_fixture(output: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="platform-air-quality-") as temporary:
        root = Path(temporary)
        _copy_assembly_inputs(root)
        assembled = compile_product(root, _create_air_quality_product(root))
        output.mkdir(parents=True, exist_ok=True)
        for name in ("product-ui.json", "product-icon.svg", "assembly-lock.json"):
            destination = output / name
            destination.unlink(missing_ok=True)
            shutil.copy2(assembled / name, destination)
        kernel = _read(assembled / "kernel-config.json")
        capabilities = {
            "productId": kernel["productId"],
            "projectId": "air-demo",
            "services": sorted(kernel["services"]),
            "orchestrationOwner": "AGENT",
            "observationTools": [
                {
                    "name": item["name"],
                    "description": item["description"],
                    "inputSchema": item["inputSchema"],
                    "outputSchema": item["outputSchema"],
                }
                for item in kernel["observationTools"]
            ],
            "models": [
                {
                    "modelId": item["modelId"],
                    "modelVersion": item["version"],
                    "modelChecksum": item["modelChecksum"],
                    "workflowId": item["workflowRef"]["workflowId"],
                    "workflowVersion": item["workflowRef"]["version"],
                    "workflowChecksum": item["workflowRef"]["checksum"],
                    "description": item["tool"]["description"],
                    "tool": item["tool"]["name"],
                    "inputSchema": item["tool"]["inputSchema"],
                    "dataInputParameters": item["dataInputParameters"],
                    "requiredInputs": [
                        name
                        for name in item["tool"]["inputSchema"].get("required", [])
                        if name not in item["defaultParameters"]
                    ],
                    "defaultParameters": item["defaultParameters"],
                    "finalOutputs": item["finalOutputs"],
                    "applicabilityStatus": "AVAILABLE",
                    "deadlineSeconds": item["deadlineSeconds"],
                }
                for item in kernel["models"]
            ],
        }
        capabilities_path = output / "capabilities.json"
        capabilities_path.unlink(missing_ok=True)
        _write(capabilities_path, capabilities)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build_fixture(args.output.resolve())


if __name__ == "__main__":
    main()
