"""Additional versioned domain declarations for the full porous candidate."""

import hashlib
import json
from pathlib import Path

from .porous_candidate import FORMATS, MODEL, TOOL, _node, _publication, _write

SOURCE = "groundwater-porous-linux-v2-6-2"


def _input(name, parameter, title, format_):
    return {"port": name, "parameter": parameter, "title": title, "format": format_}


def _output(name, port, title, format_, node="execute"):
    return (name, node, port, title, format_)


def _simple(operation, *, model, tool, title, detail, inputs, outputs, timeout=7200):
    node = _node("execute", operation, inputs[0]["port"], {})
    node["inputs"] = {item["port"]: {"workflowInput": item["port"]} for item in inputs}
    return {
        "slug": model, "tool": tool, "title": title, "detail": detail, "inputs": inputs,
        "nodes": [node], "timeout": timeout,
        "outputs": [*outputs, _output(f"porous-{model}-receipt", "run_receipt", "运行凭据", "json")],
    }


PROJECT = _input("project_package", "projectPackageVersionId", "模型项目 ZIP", "zip")
PEST = {
    "slug": "calibration", "tool": "run_porous_pest_calibration", "title": "孔隙型 PEST 水头率定",
    "detail": "用原生 PEST 拟合给定水头观测和全局参数倍率，随后用率定模型重新执行仿真。",
    "fixedParameters": {"includeCalibratedModel": True},
    "inputs": [PROJECT, _input("observations", "calibrationObservationsVersionId", "率定观测 JSON", "json")],
    "nodes": [
        _node("compile", "model.compile", "project_package", {"workflowInput": "project_package"}),
        {
            "nodeId": "calibrate", "operatorId": "groundwater.porous.pest_calibrate",
            "operatorVersion": "1.1.0",
            "parameters": {"includeCalibratedModel": {"workflowParameter": "includeCalibratedModel"}},
            "inputs": {
                "model_package": {"nodeOutput": {"nodeId": "compile", "port": "model_package"}},
                "observations": {"workflowInput": "observations"},
            },
        },
        _node("simulate", "flow_transport.simulate", "model_package", {
            "nodeOutput": {"nodeId": "calibrate", "port": "calibrated_model_package"},
        }),
    ],
    "outputs": [
        _output("porous-calibrated-parameters", "calibrated_parameters", "率定参数倍率", "json", "calibrate"),
        _output("porous-calibration-residuals", "residual_diagnostics", "率定残差", "json", "calibrate"),
        _output("porous-calibration-package", "calibration_package", "PEST 率定报告", "zip", "calibrate"),
        _output("porous-calibrated-model", "calibrated_model_package", "可重算的已率定模型包", "zip", "calibrate"),
        _output("porous-calibrated-summary", "calculation_summary", "率定后仿真摘要", "json", "simulate"),
        _output("porous-calibrated-result", "result_package", "率定后仿真结果包", "zip", "simulate"),
        _output("porous-pest-compile-receipt", "run_receipt", "率定前编译凭据", "json", "compile"),
        _output("porous-pest-receipt", "run_receipt", "PEST 率定凭据", "json", "calibrate"),
        _output("porous-pest-simulation-receipt", "run_receipt", "率定后仿真凭据", "json", "simulate"),
    ],
    "timeout": 7200,
}

ENTRIES = [
    PEST,
    _simple(
        "scenario.compare", model="scenario-comparison", tool="run_porous_scenario_compare",
        title="孔隙型情景对比", detail="以固定模型为基准，计算声明的各个参数情景并汇总差异。",
        inputs=[_input("scenario_package", "scenarioPackageVersionId", "情景 ZIP", "zip")],
        outputs=[
            _output("porous-scenario-table", "scenario_table", "逐情景对比表", "json"),
            _output("porous-scenario-summary", "comparison_summary", "情景对比摘要", "json"),
            _output("porous-scenario-results", "result_package", "逐情景仿真结果包", "zip"),
        ],
    ),
    _simple(
        "map.prepare", model="map", tool="run_porous_map_prepare",
        title="孔隙型栅格地图准备", detail="校验并整理本地 GeoTIFF，发布栅格与坐标参考系信息。",
        inputs=[_input("map_input", "geoTiffVersionId", "本地 GeoTIFF", "geotiff")],
        outputs=[
            _output("porous-map-raster", "geospatial_output", "可查看的 GeoTIFF", "geotiff"),
            _output("porous-map-crs", "crs_attribution_metadata", "坐标系、范围与波段信息", "json"),
        ],
    ),
    _simple(
        "inversion.train", model="inversion-training", tool="run_porous_inversion_train",
        title="孔隙型反演训练", detail="用同批水头、地表形变与参数场训练条件扩散反演模型。",
        inputs=[_input("training_data", "inversionTrainingVersionId", "反演训练 ZIP", "zip")],
        outputs=[
            _output("porous-inversion-weights", "weights", "反演训练权重", "binary"),
            _output("porous-training-metrics", "metrics", "训练与验证指标", "json"),
            _output("porous-training-report", "report_package", "训练报告与缩放参数", "zip"),
        ], timeout=21600,
    ),
    _simple(
        "inversion.sample", model="inversion-sampling", tool="run_porous_inversion_sample",
        title="孔隙型反演采样", detail="用已训练权重和明确观测条件采样参数场后验。",
        inputs=[
            _input("weights", "inversionWeightsVersionId", "反演训练权重", "binary"),
            _input("sample_config", "inversionSamplingVersionId", "采样配置 JSON", "json"),
        ],
        outputs=[
            _output("porous-posterior-samples", "posterior_samples", "后验样本数组", "npy"),
            _output("porous-sampling-metrics", "metrics", "采样指标", "json"),
            _output("porous-sampling-report", "report_package", "后验均值与标准差", "zip"),
        ],
    ),
    _simple(
        "agri.invert", model="agricultural-inversion", tool="run_porous_agri_inversion",
        title="孔隙型农业抽水反演", detail="以明确的农业抽水记录和监测水头计算响应矩阵，分期率定并独立验证。",
        inputs=[_input("agricultural_data", "agriculturalDataVersionId", "农业反演 ZIP", "zip")],
        outputs=[
            _output("porous-agricultural-arrays", "posterior_arrays", "基准、线性估计和重算水头", "npy"),
            _output("porous-agricultural-metrics", "metrics", "农业抽水反演指标", "json"),
            _output("porous-agricultural-report", "report_package", "农业反演分组及报告", "zip"),
        ],
    ),
]
ASSESSMENT = {
    "slug": "sustainability-input-preparation", "modelId": "groundwater.sustainability-input-preparation",
    "tool": "prepare_source_sustainability",
    "title": "水源地评价输入准备",
    "detail": "汇总模拟、监测及管理资料，核对同一区域、时段和指标口径，生成候选评价输入与字段来源报告。",
    "inputs": [
        _input("simulation", "simulationEvidenceVersionId", "模拟成果 JSON", "json"),
        _input("monitoring", "monitoringEvidenceVersionId", "监测证据 JSON", "json"),
        _input("management", "managementEvidenceVersionId", "管理与供需证据 JSON", "json"),
    ],
    "nodes": [{
        "nodeId": "execute", "operatorId": "groundwater.source_sustainability.prepare",
        "operatorVersion": "1.0.0", "parameters": {},
        "inputs": {role: {"workflowInput": role} for role in ("simulation", "monitoring", "management")},
    }],
    "outputs": [
        _output("assessment-input-candidate", "assessment_input", "候选评价 JSON 或阻止标记", "json"),
        _output("assessment-input-sources", "field_sources", "指标来源与缺项诊断", "json"),
    ],
    "timeout": 120,
}


def _append(entry: dict, domain: Path, manifest: dict, ui: dict) -> None:
    model_id = entry.get("modelId", "groundwater.porous-" + entry["slug"])
    slug = model_id.removeprefix("groundwater.")
    inputs, outputs = entry["inputs"], entry["outputs"]
    input_bindings = {item["port"]: item["parameter"] for item in inputs}
    defaults = entry.get("fixedParameters", {})
    workflow = {
        "schemaVersion": "workflow.v6", "workflowId": model_id, "version": "1.0.0",
        "inputs": [
            {"name": item["port"], "cardinality": "one", "mediaTypes": [FORMATS[item["format"]][0]],
             "formats": [item["format"]]} for item in inputs
        ],
        "parameters": list(defaults), "nodes": entry["nodes"],
        "outputs": [{"name": name, "nodeOutput": {"nodeId": node, "port": port}}
                    for name, node, port, _, _ in outputs],
    }
    checksum = hashlib.sha256(
        json.dumps(workflow, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    model = {
        "schemaVersion": "executable-model.v6", "modelId": model_id, "version": "1.0.0",
        "workflowRef": {"workflowId": model_id, "version": "1.0.0", "checksum": f"sha256:{checksum}"},
        "tool": {
            "name": entry["tool"], "description": entry["detail"],
            "inputSchema": {
                "type": "object",
                "properties": {
                    **{name: {"type": "string", "minLength": 1} for name in input_bindings.values()},
                    **{name: {"type": "boolean", "const": value} for name, value in defaults.items()},
                },
                "required": list(input_bindings.values()), "additionalProperties": False,
            },
        },
        "inputBindings": input_bindings, "fieldRequirements": [], "semanticRequirements": [],
        "defaultParameters": defaults, "parameterBindings": {name: name for name in defaults},
        "deadlineSeconds": entry["timeout"],
        "maxParallelNodes": 1, "finalOutputs": [name for name, *_ in outputs],
        "executionService": "executor", "executorId": "workflow-dag",
        "outputPublications": {name: _publication(title, format_) for name, _, _, title, format_ in outputs},
    }
    for key, path, document in (
        ("workflowDefinitions", f"workflows/{slug}.workflow.json", workflow),
        ("executableModels", f"models/{slug}.model.json", model),
    ):
        _write(domain / path, document)
        manifest[key].append(path)
    ui_inputs = {}
    if defaults:
        ui["parameterNames"]["includeCalibratedModel"] = {"label": "发布可重算模型包", "group": "成果"}
    for item in inputs:
        parameter, label, format_ = (item[key] for key in ("parameter", "title", "format"))
        ui["parameterNames"].setdefault(parameter, {"label": label, "group": "模型输入"})
        ui_inputs[parameter] = {"label": label, "description": f"已发布的{label}数据版本。", "formats": [format_]}
    ui["models"][model_id] = {
        "displayName": entry["title"], "description": entry["detail"],
        "applicability": "用于本机科研。现场科学等价待验证。",
        "inputs": ui_inputs, "parameterOverrides": {}, "confirmationSummary": [],
    }
    for name, _, _, title, format_ in outputs:
        ui["outputs"][name] = {"label": title, "viewer": FORMATS[format_][1]}


def append_workflows(domain: Path, manifest: dict, ui: dict) -> None:
    entries = [*ENTRIES, ASSESSMENT]
    for entry in entries:
        _append(entry, domain, manifest, ui)
    instructions = domain / manifest["instructions"]
    prompt = instructions.read_text(encoding="utf-8")
    prompt = prompt.replace("十类已安装能力为", "基础能力包括", 1)
    instructions.write_text(
        prompt.rstrip() + "\n\n## 孔隙型模型与评价输入准备\n\n"
        "孔隙型工具支持流与运移仿真、PEST 水头率定、情景对比、GeoTIFF 地图准备、条件扩散训练和采样以及农业抽水反演。"
        "从 Data 核实项目包、观测和训练权重的精确版本，确认后执行。现场科学等价待验证。\n\n"
        "水源地评价先核对模拟预算、监测统计和管理认定，运行评价输入准备。"
        "对候选 JSON 和字段来源报告进行人工复核后，单独发起并确认综合评价。"
        "出现 BLOCKED 或待核查字段时，应报告诊断并等待补齐。\n",
        encoding="utf-8",
    )
    cards = []
    for model, tool, title, detail in [
        (MODEL, TOOL, "孔隙型流与运移仿真", "计算水头、浓度并发布分析与图件。"),
        *((entry.get("modelId", f"groundwater.porous-{entry['slug']}"),
           entry["tool"], entry["title"], entry["detail"]) for entry in entries),
    ]:
        cards.append({
            "knowledgeId": model.replace("groundwater.", "gw-model-card-"),
            "title": title, "summary": detail,
            "chunks": [{
                "content": f"{model}@1.0.0，工具 {tool}。{detail}"
                           "选择已发布的 Data 输入版本，经用户确认执行。"
                           "运行凭据记录服务版本、任务标识及成果校验值。现场科学等价待验证。",
                "sourceRefs": [SOURCE],
            }],
        })
    path = "knowledge/porous-model-cards.v1.json"
    _write(domain / path, {
        "schemaVersion": "domain-knowledge.v2", "contentVersion": "1.0.0", "language": "zh-CN",
        "sources": [{
            "sourceId": SOURCE, "kind": "DOMAIN_DEFINITION", "title": "Groundwater 孔隙型 Linux 模型",
            "publisher": "Groundwater Platform", "url": None, "version": "6.1.0",
            "publishedAt": "2026-11-11", "accessedAt": None, "reviewAfter": "2027-11-11",
            "validThrough": None, "lifecycleStatus": "ACTIVE",
        }],
        "documents": cards,
    })
    manifest["knowledgeDocuments"].append(path)
