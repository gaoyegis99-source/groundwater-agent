"""Render the active Groundwater workflow inventory with the production assembler."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

SOURCE = Path(__file__).resolve().parents[2] / "source"
sys.path.insert(0, str(SOURCE))

from scripts.development.capability_inventory import _assembler_sources  # noqa: E402

from assembler_manifests import load_domain  # noqa: E402

PLAN = Path(__file__).resolve().parent
PRODUCT = SOURCE / "products/groundwater/product-manifest.json"
INVENTORY = PLAN / "02_WORKFLOW_INVENTORY.md"
START = "<!-- BEGIN GENERATED WORKFLOWS -->"
END = "<!-- END GENERATED WORKFLOWS -->"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def code(value: object) -> str:
    return f"`{str(value).replace('|', r'\|').replace('`', '&#96;')}`"


def inline_json(value: object) -> str:
    return code(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def join(values) -> str:
    return "<br>".join(values) or "—"


def relative_link(path: Path, label: str) -> str:
    return f"[{label}](../../source/{path.relative_to(SOURCE).as_posix()})"


def short_ref(document: dict, identity_key: str) -> str:
    return f"{document[identity_key]}@{document['version']}"


def operator_ref(node: dict) -> str:
    return f"{node['operatorId']}@{node['operatorVersion']}"


def port_formats(port: dict) -> str:
    return join(
        code(f"{pair['format']} ({pair['mediaType']})") for pair in port["formatPairs"]
    )


def binding_ref(binding: dict) -> str:
    if "workflowInput" in binding:
        return f"input:{binding['workflowInput']}"
    if "collection" in binding:
        return (
            "[" + ", ".join(binding_ref(item) for item in binding["collection"]) + "]"
        )
    upstream = binding["nodeOutput"]
    return f"{upstream['nodeId']}.{upstream['port']}"


def parameter_rows(workflow: dict, model: dict):
    schema = model["tool"]["inputSchema"]
    defaults = model["defaultParameters"]
    for parameter in workflow["parameters"]:
        argument = model["parameterBindings"][parameter]
        if argument in defaults:
            origin = f"Model default {inline_json(defaults[argument])}，可显式覆盖"
        elif argument in schema.get("required", []):
            origin = "调用方必填"
        else:
            origin = "调用方选填或条件必填，见 Model Schema"
        yield f"| {code(parameter)} | {code(argument)} | {origin} |"


def node_row(node: dict, composition: set[str]) -> str:
    identity = operator_ref(node)
    inputs = join(
        code(f"{port} ← {binding_ref(binding)}")
        for port, binding in node["inputs"].items()
    )
    parameters = join(
        code(
            name
            if name == binding["workflowParameter"]
            else f"{name} ← {binding['workflowParameter']}"
        )
        for name, binding in node["parameters"].items()
    )
    schema_defaults = join(
        f"{code(name)} = {inline_json(schema['default'])}"
        + ("（已显式绑定）" if name in node["parameters"] else "（未绑定）")
        for name, schema in node["parameterSchema"].get("properties", {}).items()
        if "default" in schema
    )
    runtime = code(node["runtime"])
    if remote := node.get("remoteBinding"):
        runtime += "<br>" + code(f"{remote['serviceId']}/{remote['operationType']}")
    member = "是" if identity in composition else "否"
    return (
        f"| {code(node['nodeId'])} | {code(identity)} · {runtime} | "
        f"{member} | {inputs} | {parameters} | {schema_defaults} |"
    )


def output_row(output: dict, nodes: dict, model: dict, ui: dict) -> str:
    name = output["name"]
    upstream = output["nodeOutput"]
    contracts = nodes[upstream["nodeId"]]["outputContracts"]
    port = next(item for item in contracts if item["name"] == upstream["port"])
    contract = (
        port_formats(port)
        if port["kind"] == "artifact"
        else inline_json(port["schema"])
    )
    declaration = model["outputPublications"].get(name)
    if declaration:
        pair = f"{declaration['format']} ({declaration['mediaType']})"
        publication = f"{code(pair)} · {code(declaration['policy'])}"
    else:
        publication = "未声明"
    presentation = ui["outputs"][name]
    return (
        f"| {code(name)} | {code(binding_ref(output))} | {code(port['kind'])}<br>{contract} | "
        f"{publication} | {presentation['label']} · {code(presentation['viewer'])} |"
    )


def case_bindings(
    product_path: Path, domain, models: list[dict], workflows: list[dict]
) -> dict:
    directory = product_path.parent / "development"
    golden_source = directory / "workflow-golden-cases.v2.json"
    golden_baselines = read_json(directory / "workflow-golden-baselines.v1.json")[
        "cases"
    ]
    real_source = directory / "real-data-cases.v2.json"
    workflow_by_id = {item["workflowId"]: item for item in workflows}
    model_by_ref = {short_ref(item, "modelId"): item for item in models}
    cases: dict[str, list[str]] = {identity: [] for identity in workflow_by_id}
    for name, item in read_json(golden_source)["cases"].items():
        workflow = workflow_by_id[item["workflowId"]]
        baseline_path = directory / golden_baselines[name]
        baseline = read_json(baseline_path)["workflow"]
        expected = (
            workflow["workflowId"],
            workflow["version"],
            workflow["definitionChecksum"],
        )
        if (
            baseline["id"],
            baseline["version"],
            baseline["definitionChecksum"],
        ) != expected:
            raise ValueError(f"Golden baseline has a different identity: {name}")
        inputs = ", ".join(
            f"{port}={fixture}" for port, fixture in item["inputs"].items()
        )
        outputs = ", ".join(item["keyOutputs"])
        cases[item["workflowId"]].append(
            f"{relative_link(golden_source, 'Pack Golden')} {code(name)}，夹具 {code(inputs)}，"
            f"重点输出 {code(outputs)}，{relative_link(baseline_path, '同身份基线')}"
        )
    for name, item in read_json(real_source)["cases"].items():
        cases[item["workflowId"]].append(
            f"{relative_link(real_source, '外部来源样本声明')} {code(name)}，"
            f"来源组 {code(item['sourceGroup'])}，输入角色 {code(', '.join(item['inputs']))}"
        )
    for relative, item in zip(
        domain.manifest["executionCases"], domain.execution_cases, strict=True
    ):
        model = model_by_ref[short_ref(item["modelRef"], "modelId")]
        cases[model["workflowRef"]["workflowId"]].append(
            f"{relative_link(domain.root / relative, 'Domain Execution Case')} {code(item['caseId'])}，"
            f"数据标签 {code(item['datasetTag'])}，"
            f"默认 {code(item['defaultProfile'])}，选项 {code(', '.join(item['profiles']))}"
        )
    return cases


def render() -> str:
    product_path, manifest, domain, workflows, models, ui, _, _ = _assembler_sources(
        SOURCE, PRODUCT
    )
    domain_id = short_ref(domain.identity, "domainId")
    composition = set(manifest["compositionOperators"])
    cases = case_bindings(product_path, domain, models, workflows)
    raw_workflows = {
        short_ref(domain.definitions[relative], "workflowId"): relative
        for relative in domain.manifest["workflowDefinitions"]
    }
    raw_models = {
        short_ref(domain.definitions[relative], "modelId"): relative
        for relative in domain.manifest["executableModels"]
    }
    historical = load_domain(SOURCE / "domains/groundwater/6.1.0")
    historical_counts = []
    for category in ("workflowDefinitions", "executableModels", "executionCases"):
        same = sum(
            relative in historical.manifest[category]
            and domain.definitions[relative] == historical.definitions[relative]
            for relative in domain.manifest[category]
        )
        historical_counts.append(
            f"{code(category)} {same}/{len(historical.manifest[category])}"
        )
    lines = [
        f"产品选择 {code(domain_id)}，包摘要 {code(domain.identity['checksum'])}。"
        f"共 {len(workflows)} 个 Workflow、{len(models)} 个 Executable Model、"
        f"{sum(len(workflow['nodes']) for workflow in workflows)} 个节点及 "
        f"{sum(len(workflow['outputs']) for workflow in workflows)} 个命名输出。",
        "",
        f"保留的历史领域包 {code(short_ref(historical.identity, 'domainId'))} 使用独立包摘要 "
        f"{code(historical.identity['checksum'])}。两个包 JSON 定义内容相同的同名文件数为 "
        f"{'、'.join(historical_counts)}。",
        "",
        f"动态组合白名单有 {len(composition)} 个精确算子版本。按 Workflow ID 排序，节点与输出保留来源顺序。",
        "",
    ]
    for workflow in sorted(workflows, key=lambda item: item["workflowId"]):
        workflow_id = workflow["workflowId"]
        workflow_reference = short_ref(workflow, "workflowId")
        nodes = {node["nodeId"]: node for node in workflow["nodes"]}
        operators = {operator_ref(node) for node in nodes.values()}
        members = sorted(operators & composition)
        lines += [
            f"### {workflow_reference}",
            "",
            f"- Workflow 定义摘要 {code(workflow['definitionChecksum'])}。 "
            f"{relative_link(domain.root / raw_workflows[workflow_reference], 'Workflow JSON')}",
            f"- 组合算子成员 {join(code(identity) for identity in members)}。"
            + (
                "节点版本均在白名单内，动态执行还需通过绑定、语义与预算检查。"
                if len(members) == len(operators)
                else "本图包含未准入的精确版本，动态组合草稿的准入检查返回 "
                "`COMPOSITION_OPERATOR_UNSUPPORTED`。"
            ),
        ]
        linked = [
            item
            for item in models
            if item["workflowRef"]["workflowId"] == workflow_id
            and item["workflowRef"]["version"] == workflow["version"]
        ]
        if len(linked) != 1:
            raise ValueError(f"Expected one active Model for {workflow_reference}")
        (model,) = linked
        model_ref = short_ref(model, "modelId")
        lines += [
            f"- Model {code(model_ref)}，摘要 {code(model['modelChecksum'])}。"
            f"产品入口为预设「{ui['models'][model['modelId']]['displayName']}」，"
            f"Agent 工具为 {code(model['tool']['name'])}。 "
            f"{relative_link(domain.root / raw_models[model_ref], 'Model JSON')}",
            "- 声明绑定的验证用例 "
            + ("，".join(cases[workflow_id]) or "未找到结构化用例声明"),
            "",
            "| 输入角色 | 基数 | 编译后的格式 / 媒体类型 | Model 调用参数 |",
            "| --- | --- | --- | --- |",
        ]
        lines += [
            f"| {code(item['name'])} | {code(item['cardinality'])} | {port_formats(item)} | "
            f"{code(model['inputBindings'][item['name']])} |"
            for item in workflow["inputs"]
        ]
        lines += [
            "",
            "| 节点 | 精确 Operator · Runtime | 组合成员 | 端口输入绑定 | 节点参数 ← Workflow 参数 | Manifest Schema 默认声明 |",
            "| --- | --- | --- | --- | --- | --- |",
            *(node_row(node, composition) for node in nodes.values()),
            "",
        ]
        if workflow["parameters"]:
            lines += [
                "<details><summary>Workflow 参数来源</summary>",
                "",
                "| Workflow 参数 | Model 调用参数 | 来源 |",
                "| --- | --- | --- |",
                *parameter_rows(workflow, model),
                "",
                "</details>",
                "",
            ]
        else:
            lines += [
                "Workflow 参数集合为空。输入文件仍可承载规则、服务或算法配置。",
                "",
            ]
        lines += [
            "| 命名输出 | 节点端口 | 编译后的底层合同 | Model 发布声明 | UI 标签 · 查看器 |",
            "| --- | --- | --- | --- | --- |",
            *(output_row(output, nodes, model, ui) for output in workflow["outputs"]),
            "",
        ]
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check the generated inventory block on disk",
    )
    args = parser.parse_args()
    generated = render()
    if args.check:
        document = INVENTORY.read_text(encoding="utf-8")
        actual = document.split(START + "\n", 1)[1].split(END, 1)[0]
        if actual != generated:
            raise SystemExit(f"Workflow inventory is stale: {INVENTORY.name}")
        print(f"PASS {INVENTORY.name}")
    else:
        sys.stdout.write(generated)


if __name__ == "__main__":
    main()
