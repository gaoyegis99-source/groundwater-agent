"""Generate the current product capability reference from owned manifests."""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
for _source_directory in (
    REPOSITORY_ROOT / "platform/assembler",
    REPOSITORY_ROOT / "packages/workflow-compiler/src",
    REPOSITORY_ROOT / "packages/operator-pack/src",
):
    if str(_source_directory) not in sys.path:
        sys.path.insert(0, str(_source_directory))

from assembler_domain import (  # noqa: E402
    _compile_domain,
    _compile_services,
    _load_operator_catalog,
)
from assembler_manifests import (  # noqa: E402
    _load_manifest,
    _load_modules,
    _selected_domain,
)
from assembler_ui import load_product_ui  # noqa: E402
from moperators.catalog import OperatorCatalog  # noqa: E402


_CHECKSUM = re.compile(r"^sha256:[0-9a-f]{64}$")
_INDEX_FIELDS = {"schemaVersion", "catalogDigest", "operators"}
_INDEX_OPERATOR_FIELDS = {
    "id",
    "version",
    "domain",
    "runtime",
    "manifestDigest",
    "smokeDigest",
}


class CapabilityInventoryRefusal(ValueError):  # noqa: N818
    """Stable refusal family for generation and verification commands."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    try:
        return _sha256_bytes(path.read_bytes())
    except OSError as error:
        raise CapabilityInventoryRefusal(
            "CAPABILITY_INVENTORY_SOURCE_INVALID", str(path)
        ) from error


def _load_json(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        encoded = path.read_bytes()
        value = json.loads(encoded)
    except (OSError, UnicodeError, ValueError) as error:
        raise CapabilityInventoryRefusal(
            "CAPABILITY_INVENTORY_SOURCE_INVALID", str(path)
        ) from error
    if not isinstance(value, dict):
        raise CapabilityInventoryRefusal(
            "CAPABILITY_INVENTORY_SOURCE_INVALID", str(path)
        )
    return value, encoded


def _relative(repository: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repository).as_posix()
    except ValueError as error:
        raise CapabilityInventoryRefusal(
            "CAPABILITY_INVENTORY_SOURCE_OUTSIDE_REPOSITORY", str(path)
        ) from error


def _load_catalog_index(
    repository: Path,
) -> tuple[dict[str, Any], str, OperatorCatalog]:
    catalog_root = repository / "packages/operator-pack/catalog"
    index_path = catalog_root / "index.json"
    index, encoded = _load_json(index_path)
    rows = index.get("operators")
    if (
        set(index) != _INDEX_FIELDS
        or index.get("schemaVersion") != "operator-catalog-index.v1"
        or _CHECKSUM.fullmatch(str(index.get("catalogDigest", ""))) is None
        or not isinstance(rows, list)
        or not rows
    ):
        raise CapabilityInventoryRefusal("CAPABILITY_INVENTORY_CATALOG_INDEX_INVALID")

    by_identity: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != _INDEX_OPERATOR_FIELDS
            or any(
                not isinstance(row.get(key), str) or not row[key]
                for key in ("id", "version", "domain", "runtime")
            )
            or row["runtime"] not in {"native", "qgis", "remote_http"}
            or _CHECKSUM.fullmatch(str(row.get("manifestDigest", ""))) is None
            or _CHECKSUM.fullmatch(str(row.get("smokeDigest", ""))) is None
        ):
            raise CapabilityInventoryRefusal(
                "CAPABILITY_INVENTORY_CATALOG_INDEX_INVALID"
            )
        identity = row["id"], row["version"]
        if identity in by_identity:
            raise CapabilityInventoryRefusal(
                "CAPABILITY_INVENTORY_CATALOG_INDEX_INVALID"
            )
        by_identity[identity] = row

    try:
        catalog = OperatorCatalog.load(catalog_root / "operators")
    except (OSError, TypeError, ValueError) as error:
        raise CapabilityInventoryRefusal(
            "CAPABILITY_INVENTORY_CATALOG_INVALID"
        ) from error
    if index["catalogDigest"] != catalog.digest:
        raise CapabilityInventoryRefusal("CAPABILITY_INVENTORY_CATALOG_INDEX_DRIFT")

    current_identities = {
        (operator.id, operator.version) for operator in catalog.operators
    }
    if set(by_identity) != current_identities:
        raise CapabilityInventoryRefusal("CAPABILITY_INVENTORY_CATALOG_INDEX_DRIFT")
    for operator in catalog.operators:
        identity = operator.id, operator.version
        row = by_identity[identity]
        smoke_path = (
            catalog_root / "operators" / operator.id / operator.version / "smoke.json"
        )
        smoke, _encoded = _load_json(smoke_path)
        expected_smoke = _sha256_bytes(_canonical_json(smoke))
        if (
            row["domain"] != operator.domain
            or row["runtime"] != operator.runtime
            or row["manifestDigest"] != catalog.manifest_digests[identity]
            or row["smokeDigest"] != expected_smoke
        ):
            raise CapabilityInventoryRefusal("CAPABILITY_INVENTORY_CATALOG_INDEX_DRIFT")
    return index, _sha256_bytes(encoded), catalog


def _counter(value: Counter[str]) -> dict[str, int]:
    return {key: value[key] for key in sorted(value)}


def _assembler_sources(
    repository: Path,
    product_manifest_path: Path,
) -> tuple[
    Path,
    dict[str, Any],
    Any,
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
    dict[str, Any],
    str,
]:
    product_path = product_manifest_path.resolve()
    if not product_path.is_relative_to(repository):
        raise CapabilityInventoryRefusal("CAPABILITY_INVENTORY_PRODUCT_INVALID")
    try:
        product_path, product = _load_manifest(repository, product_path)
        domain = _selected_domain(repository, product["domainPackage"])
        modules = _load_modules(repository)
        services, selected_modules = _compile_services(
            tuple(product["services"]), modules
        )
        operator_definitions, execution_catalog_digest = _load_operator_catalog(
            repository
        )
        _tools, workflows, models = _compile_domain(
            domain,
            services,
            selected_modules,
            operator_definitions,
        )
        product_ui_text, _icon, product_ui_identity = load_product_ui(
            product_path.parent,
            product["uiProfile"],
            product["productId"],
            models,
            domain.execution_cases,
        )
        product_ui = json.loads(product_ui_text)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise CapabilityInventoryRefusal(
            "CAPABILITY_INVENTORY_PRODUCT_INVALID", str(error)
        ) from error
    return (
        product_path,
        product,
        domain,
        workflows,
        models,
        product_ui,
        product_ui_identity,
        execution_catalog_digest,
    )


def build_capability_inventory(
    repository_root: Path,
    product_manifest_path: Path,
) -> dict[str, Any]:
    """Build a validated, timestamp-free projection of current capabilities."""

    repository = repository_root.resolve()
    if not repository.is_dir():
        raise CapabilityInventoryRefusal("CAPABILITY_INVENTORY_REPOSITORY_INVALID")

    index, index_sha256, catalog = _load_catalog_index(repository)
    (
        product_path,
        product,
        domain,
        workflows,
        models,
        product_ui,
        product_ui_identity,
        execution_catalog_digest,
    ) = _assembler_sources(repository, product_manifest_path)

    index_by_identity = {(row["id"], row["version"]): row for row in index["operators"]}
    compiled_identities = {
        (node["operatorId"], node["operatorVersion"])
        for workflow in workflows
        for node in workflow["nodes"]
    }
    if not compiled_identities <= set(index_by_identity):
        raise CapabilityInventoryRefusal(
            "CAPABILITY_INVENTORY_WORKFLOW_OPERATOR_MISSING"
        )

    workflow_by_identity = {
        (workflow["workflowId"], workflow["version"]): workflow
        for workflow in workflows
    }
    ui_models = product_ui["models"]
    ui_outputs = product_ui["outputs"]
    usage_workflows: dict[tuple[str, str], set[str]] = defaultdict(set)
    usage_nodes: Counter[tuple[str, str]] = Counter()
    workflow_rows: list[dict[str, Any]] = []
    for workflow in sorted(workflows, key=lambda item: item["workflowId"]):
        operators: list[str] = []
        seen: set[tuple[str, str]] = set()
        runtime_counts: Counter[str] = Counter()
        for node in workflow["nodes"]:
            identity = node["operatorId"], node["operatorVersion"]
            usage_workflows[identity].add(workflow["workflowId"])
            usage_nodes[identity] += 1
            runtime_counts[node["runtime"]] += 1
            if identity not in seen:
                operators.append(f"{identity[0]}@{identity[1]}")
                seen.add(identity)
        outputs = [item["name"] for item in workflow["outputs"]]
        if any(name not in ui_outputs for name in outputs):
            raise CapabilityInventoryRefusal("CAPABILITY_INVENTORY_UI_OUTPUT_MISSING")
        workflow_rows.append(
            {
                "workflowId": workflow["workflowId"],
                "version": workflow["version"],
                "nodeCount": len(workflow["nodes"]),
                "operatorCount": len(operators),
                "operators": operators,
                "runtimeCounts": _counter(runtime_counts),
                "outputCount": len(outputs),
                "outputs": outputs,
                "outputDetails": [
                    {
                        "name": name,
                        "label": ui_outputs[name]["label"],
                        "viewer": ui_outputs[name]["viewer"],
                    }
                    for name in outputs
                ],
                "definitionChecksum": workflow["definitionChecksum"],
            }
        )

    model_rows: list[dict[str, Any]] = []
    for model in sorted(models, key=lambda item: item["modelId"]):
        reference = model["workflowRef"]
        workflow = workflow_by_identity.get(
            (reference["workflowId"], reference["version"])
        )
        presentation = ui_models.get(model["modelId"])
        if workflow is None or not isinstance(presentation, dict):
            raise CapabilityInventoryRefusal(
                "CAPABILITY_INVENTORY_MODEL_REFERENCE_INVALID"
            )
        data_inputs = [
            {
                "workflowInput": workflow_input,
                "parameter": parameter,
            }
            for workflow_input, parameter in sorted(model["inputBindings"].items())
        ]
        outputs = [item["name"] for item in workflow["outputs"]]
        viewers = Counter(ui_outputs[name]["viewer"] for name in outputs)
        model_rows.append(
            {
                "modelId": model["modelId"],
                "version": model["version"],
                "displayName": presentation["displayName"],
                "description": presentation["description"],
                "applicability": presentation["applicability"],
                "toolName": model["tool"]["name"],
                "workflowId": workflow["workflowId"],
                "workflowVersion": workflow["version"],
                "dataInputs": data_inputs,
                "requiredParameters": list(
                    model["tool"]["inputSchema"].get("required", [])
                ),
                "nodeCount": len(workflow["nodes"]),
                "outputCount": len(outputs),
                "outputViewers": _counter(viewers),
                "modelChecksum": model["modelChecksum"],
                "workflowChecksum": workflow["definitionChecksum"],
            }
        )

    operator_rows = [
        {
            "identity": f"{row['id']}@{row['version']}",
            "id": row["id"],
            "version": row["version"],
            "domain": row["domain"],
            "runtime": row["runtime"],
            "manifestDigest": row["manifestDigest"],
            "smokeDigest": row["smokeDigest"],
            "workflowUsageCount": len(usage_workflows[(row["id"], row["version"])]),
            "nodeUsageCount": usage_nodes[(row["id"], row["version"])],
            "workflows": sorted(usage_workflows[(row["id"], row["version"])]),
        }
        for row in sorted(
            index["operators"], key=lambda item: (item["id"], item["version"])
        )
    ]
    examples = [
        {
            "order": position,
            "id": item["id"],
            "title": item["title"],
            "prompt": item["prompt"],
            "icon": item["icon"],
            "featured": item["featured"],
        }
        for position, item in enumerate(product_ui["examples"], start=1)
    ]
    viewer_counts = Counter(definition["viewer"] for definition in ui_outputs.values())
    runtime_counts = Counter(row["runtime"] for row in index["operators"])
    domain_counts = Counter(row["domain"] for row in index["operators"])
    used_operator_count = len(compiled_identities)

    domain_manifest_path = domain.root / "domain-manifest.json"
    product_ui_path = product_path.parent / product["uiProfile"]["path"]
    return {
        "schemaVersion": "capability-inventory.v1",
        "product": {
            "productId": product["productId"],
            "projectId": product["projectId"],
            "publicApiVersion": product["publicApiVersion"],
            "domainId": domain.manifest["domainId"],
            "domainVersion": domain.manifest["version"],
            "locale": product_ui["locale"],
        },
        "sources": {
            "productManifest": {
                "path": _relative(repository, product_path),
                "sha256": _sha256_file(product_path),
            },
            "domainManifest": {
                "path": _relative(repository, domain_manifest_path),
                "sha256": _sha256_file(domain_manifest_path),
                "packageChecksum": domain.identity["checksum"],
            },
            "operatorCatalog": {
                "path": "packages/operator-pack/catalog/index.json",
                "sha256": index_sha256,
                "catalogDigest": catalog.digest,
                "executionCatalogDigest": execution_catalog_digest,
            },
            "productUi": {
                "path": _relative(repository, product_ui_path),
                "sha256": _sha256_file(product_ui_path),
                "compiledChecksum": product_ui_identity["profileOutputChecksum"],
            },
        },
        "summary": {
            "modelCount": len(model_rows),
            "workflowCount": len(workflow_rows),
            "operatorCount": len(operator_rows),
            "workflowOperatorCount": used_operator_count,
            "uiModelCount": len(ui_models),
            "exampleCount": len(examples),
            "featuredExampleCount": sum(item["featured"] for item in examples),
            "observationToolCount": len(domain.manifest["observationTools"]),
            "knowledgeDocumentCount": len(domain.manifest["knowledgeDocuments"]),
            "executionCaseCount": len(domain.manifest["executionCases"]),
            "runtimeCounts": _counter(runtime_counts),
            "operatorDomainCounts": _counter(domain_counts),
            "viewerCounts": _counter(viewer_counts),
        },
        "models": model_rows,
        "workflows": workflow_rows,
        "operators": operator_rows,
        "examples": examples,
    }


def _markdown(value: object) -> str:
    return " ".join(str(value).split()).replace("|", "\\|")


def _code(value: object) -> str:
    return f"`{_markdown(value).replace('`', '&#96;')}`"


def _breaks(values: list[str]) -> str:
    return "<br>".join(_code(value) for value in values) or "—"


def _counts(value: dict[str, int]) -> str:
    return "、".join(f"{_code(key)} {count}" for key, count in value.items()) or "—"


def render_capability_inventory(inventory: dict[str, Any]) -> str:
    """Render one stable Markdown reference from a built inventory."""

    product = inventory["product"]
    sources = inventory["sources"]
    summary = inventory["summary"]
    lines = [
        "<!-- Generated by scripts/development/capability_inventory.py; 不要手工编辑。 -->",
        "# 当前功能清单",
        "",
        (
            f"本表从当前 `{product['productId']}` 产品选择的领域包、Operator Catalog index 和 "
            "Product UI 确定性生成。它描述默认产品装配声明，不代表远程服务在线、现场科学适用性或发布门禁通过。"
        ),
        "",
        "## 来源身份",
        "",
        "| 来源 | 路径 | 身份 |",
        "|---|---|---|",
        (
            f"| Product manifest | {_code(sources['productManifest']['path'])} | "
            f"{_code(sources['productManifest']['sha256'])} |"
        ),
        (
            f"| Domain manifest | {_code(sources['domainManifest']['path'])} | "
            f"package {_code(sources['domainManifest']['packageChecksum'])} |"
        ),
        (
            f"| Operator Catalog index | {_code(sources['operatorCatalog']['path'])} | "
            f"manifest {_code(sources['operatorCatalog']['catalogDigest'])}<br>"
            f"execution {_code(sources['operatorCatalog']['executionCatalogDigest'])} |"
        ),
        (
            f"| Product UI | {_code(sources['productUi']['path'])} | "
            f"source {_code(sources['productUi']['sha256'])}<br>"
            f"compiled {_code(sources['productUi']['compiledChecksum'])} |"
        ),
        "",
        "## 摘要",
        "",
        "| 项目 | 当前值 |",
        "|---|---:|",
        f"| 可执行模型 | {summary['modelCount']} |",
        f"| 工作流 | {summary['workflowCount']} |",
        f"| Catalog 算子 | {summary['operatorCount']} |",
        f"| 被当前工作流引用的唯一算子 | {summary['workflowOperatorCount']} |",
        f"| UI 模型配置 | {summary['uiModelCount']} |",
        f"| UI 示例 / featured | {summary['exampleCount']} / {summary['featuredExampleCount']} |",
        f"| Agent observation tools | {summary['observationToolCount']} |",
        f"| Knowledge documents | {summary['knowledgeDocumentCount']} |",
        f"| Execution cases | {summary['executionCaseCount']} |",
        "",
        f"算子 runtime：{_counts(summary['runtimeCounts'])}。",
        "",
        f"结果 viewer：{_counts(summary['viewerCounts'])}。",
        "",
        "## 可执行模型",
        "",
        "| 模型 | UI 名称 | 版本 | Tool | Workflow | 数据输入 | 节点 / 输出 | Viewer |",
        "|---|---|---|---|---|---|---:|---|",
    ]
    for model in inventory["models"]:
        data_inputs = [
            f"{item['workflowInput']}←{item['parameter']}"
            for item in model["dataInputs"]
        ]
        lines.append(
            f"| {_code(model['modelId'])} | {_markdown(model['displayName'])} | "
            f"{_code(model['version'])} | {_code(model['toolName'])} | "
            f"{_code(model['workflowId'] + '@' + model['workflowVersion'])} | "
            f"{_breaks(data_inputs)} | {model['nodeCount']} / {model['outputCount']} | "
            f"{_counts(model['outputViewers'])} |"
        )

    lines.extend(
        [
            "",
            "## 模型说明与适用性",
            "",
            "| 模型 | 能力说明 | 适用性与前置条件 |",
            "|---|---|---|",
        ]
    )
    for model in inventory["models"]:
        lines.append(
            f"| {_code(model['modelId'])} | {_markdown(model['description'])} | "
            f"{_markdown(model['applicability'])} |"
        )

    lines.extend(
        [
            "",
            "## 工作流与算子组合",
            "",
            "| Workflow | 节点 | 唯一算子 | Runtime 节点 | 输出 |",
            "|---|---:|---|---|---:|",
        ]
    )
    for workflow in inventory["workflows"]:
        lines.append(
            f"| {_code(workflow['workflowId'] + '@' + workflow['version'])} | "
            f"{workflow['nodeCount']} | {_breaks(workflow['operators'])} | "
            f"{_counts(workflow['runtimeCounts'])} | {workflow['outputCount']} |"
        )

    lines.extend(
        [
            "",
            "## 工作流成果与 Viewer",
            "",
            "| Workflow | 输出 |",
            "|---|---|",
        ]
    )
    for workflow in inventory["workflows"]:
        outputs = [
            f"{item['name']} — {item['label']} [{item['viewer']}]"
            for item in workflow["outputDetails"]
        ]
        lines.append(
            f"| {_code(workflow['workflowId'] + '@' + workflow['version'])} | "
            f"{_breaks(outputs)} |"
        )

    lines.extend(
        [
            "",
            "## Operator Catalog 摘要",
            "",
            f"领域分布：{_counts(summary['operatorDomainCounts'])}。",
            "",
            (
                "Catalog 中存在不等于当前领域工作流已暴露；下表的“工作流”与“节点使用”来自当前 "
                f"{summary['workflowCount']} 个工作流的精确引用。"
            ),
            "",
            "| Operator | Domain | Runtime | 工作流 | 节点使用 |",
            "|---|---|---|---:|---:|",
        ]
    )
    for operator in inventory["operators"]:
        lines.append(
            f"| {_code(operator['identity'])} | {_code(operator['domain'])} | "
            f"{_code(operator['runtime'])} | {operator['workflowUsageCount']} | "
            f"{operator['nodeUsageCount']} |"
        )

    lines.extend(
        [
            "",
            "## Product UI 示例",
            "",
            "| 顺序 | ID | 标题 | Featured | Prompt |",
            "|---:|---|---|---|---|",
        ]
    )
    for example in inventory["examples"]:
        lines.append(
            f"| {example['order']} | {_code(example['id'])} | "
            f"{_markdown(example['title'])} | "
            f"{'是' if example['featured'] else '否'} | "
            f"{_markdown(example['prompt'])} |"
        )

    lines.extend(
        [
            "",
            "## 边界",
            "",
            "- 本表只投影默认产品声明；独立 Pastas/MODFLOW 6 spike 未进入默认 domain manifest、Catalog 或 Product UI，因此不会出现在可执行模型表中。",
            "- `remote_http` 只表示执行绑定；安装声明、UI 展示或 Catalog 收录都不能证明第三方服务当前可用。",
            "- Catalog 算子总数不等于当前工作流使用数；判断产品可调用能力应以模型、工作流和 UI 三方一致性为准。",
            "- 重新生成使用 `../bin/dev capability-inventory`；只读漂移检查使用 `../bin/dev capability-inventory --verify`。",
            "",
        ]
    )
    return "\n".join(lines)


def generate_capability_inventory(
    *,
    repository_root: Path,
    product_manifest_path: Path,
    output_path: Path,
    check: bool,
) -> dict[str, Any]:
    """Generate or verify the owned Markdown capability reference."""

    repository = repository_root.resolve()
    if not output_path.is_absolute():
        raise CapabilityInventoryRefusal("CAPABILITY_INVENTORY_OUTPUT_INVALID")
    output = output_path.resolve()
    if not output.parent.is_dir() or output.is_symlink() or output.is_dir():
        raise CapabilityInventoryRefusal("CAPABILITY_INVENTORY_OUTPUT_PARENT_INVALID")
    inventory = build_capability_inventory(repository, product_manifest_path)
    rendered = render_capability_inventory(inventory).encode("utf-8")
    current = output.read_bytes() if output.is_file() else None
    if check:
        if current != rendered:
            raise CapabilityInventoryRefusal("CAPABILITY_INVENTORY_OUT_OF_DATE")
        status = "PASS"
    elif current == rendered:
        status = "UNCHANGED"
    else:
        temporary: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
            )
            temporary = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(rendered)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(0o644)
            temporary.replace(output)
        except OSError as error:
            try:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise CapabilityInventoryRefusal(
                "CAPABILITY_INVENTORY_WRITE_FAILED"
            ) from error
        status = "UPDATED"
    return {
        "schemaVersion": "capability-inventory-generation.v1",
        "status": status,
        "outputPath": str(output),
        "outputSha256": _sha256_bytes(rendered),
        "summary": inventory["summary"],
        "sources": inventory["sources"],
    }


__all__ = (
    "CapabilityInventoryRefusal",
    "build_capability_inventory",
    "generate_capability_inventory",
    "render_capability_inventory",
)
