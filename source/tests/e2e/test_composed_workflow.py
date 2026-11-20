"""Natural-language authoring, confirmation, native execution and Data publication."""

from __future__ import annotations

from ast import literal_eval
import asyncio
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import io
import json
import os
from pathlib import Path
from threading import Event, Thread
import time

from agents.testing import ModelStep, ScriptedModel, assistant_message, function_call
from fastapi.testclient import TestClient
import httpx
import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as parquet
import pytest
from rasterio.io import MemoryFile
from rasterio.transform import from_bounds, from_origin
from rasterio.warp import transform

from kernel.tests.conftest import kernel_settings as kernel_settings  # noqa: F401
from kernel.tests.reply_testing import submit_reply
from kernel.tests.test_workflow_drafts import _operator, _reply
from platform_data_service.api.app import create_app as data_app
from platform_data_service.operations.worker import Worker as DataWorker
from platform_executor_service.app import create_app as executor_app
from platform_executor_service.composition import ExecutorApplication
from platform_executor_service.contracts import JobRequestV6
from platform_executor_service.database import connect_database as executor_database
from platform_executor_service.remote_http import RemoteOperationRunner
from platform_executor_service.worker import Worker as ExecutorWorker
from platform_knowledge_next.models import EvidenceBundleV1
from platform_knowledge_next.wire import model_to_wire
from platform_kernel.agent_runtime.evidence import _data_reference
from platform_kernel.api import create_app as kernel_app
from platform_kernel.composition import PlatformApplication
from platform_kernel.settings import KernelSettings
from platform_protocol_contracts import DataVersionRefV2
from platform_workflow_compiler.composition import definition_digest
from services.data.tests.conftest import postgres as postgres, settings as data_settings  # noqa: F401
from services.data.tests.test_service import ready, start, upload

ROOT = Path(__file__).resolve().parents[2]
OPERATORS = ("table.filter", "table.sort", "table.profile")
RASTER_OPERATORS = ("raster.cell_statistics", "raster.reclassify")
TABLE_QUALITY_STEPS = (
    ("project", "table.project", "data", "projection"),
    ("sort", "table.sort", "data", "ordering"),
    ("deduplicate", "table.deduplicate", "data", "keys"),
    ("validate", "table.validate", "unique", "checks"),
    ("profile", "table.profile", "valid", None),
)
TABLE_QUALITY_OUTPUTS = (
    ("deduplicate", "duplicates"),
    ("validate", "valid"),
    ("validate", "invalid"),
    ("validate", "violations"),
    ("profile", "profiles"),
)
TABLE_QUALITY_SCENARIOS = {"table-quality", "table-quality-missing-rules", "table-quality-invalid-rules"}


def _upload(client, worker, title, fmt, content, *, semantics=None):
    policy = "FILE_QUERYABLE" if fmt == "parquet" else "FILE_ONLY"
    output = {"name": "input", "title": title, "format": fmt, "policy": policy}
    if semantics is not None:
        output["semantics"] = semantics
    pid = start(client, [output])
    upload(client, pid, name="input", content=content)
    ready(client, worker, pid)
    result = client.post(f"/data/v3/publications/{pid}/commit")
    assert result.status_code == 200, result.text
    version = result.json()["outputs"][0]["versionId"]
    resource = client.get(f"/data/v3/versions/{version}").json()
    raw = next(obj for obj in resource["objects"] if obj["role"] == "raw")
    return {
        "kind": "data_version", "service": "data-primary",
        "dataset_id": resource["datasetId"], "version_id": version, "semantic_id": resource["semanticId"],
        "object_id": raw["objectId"], "media_type": raw["mediaType"], "format": raw["format"],
        "size_bytes": raw["size"], "checksum": raw["checksum"],
    }


def _join_case(client, worker, catalog):
    references = {}
    bodies = {
        "left": pa.table({"id": [1, 2], "measurement": [4.0, 9.0]}),
        "right": pa.table({"id": [1, 3], "label": ["A", "C"]}),
    }
    for name, table in bodies.items():
        stream = io.BytesIO()
        parquet.write_table(table, stream)
        references[name] = _upload(client, worker, name, "parquet", stream.getvalue())
    rules = json.loads((catalog / "fixtures/data/table-join-rules.json").read_bytes())
    values = {"joinType": "left", "cardinality": "oneToOne", "maxOutputRows": 100}
    draft = {
        "title": "按编号连接两张表",
        "summary": "保留左表全部行并按编号补齐标签。",
        "graph": {
            "inputs": [
                {"name": name, "cardinality": "one", "mediaTypes": [ref["media_type"]], "formats": [ref["format"]]}
                for name, ref in references.items()
            ] + [{"name": "rules", "cardinality": "one", "mediaTypes": ["application/json"], "formats": ["json"]}],
            "parameters": list(values),
            "nodes": [{
                "nodeId": "join", "operatorId": "table.join", "operatorVersion": "1.0.0",
                "inputs": {name: {"workflowInput": name} for name in (*references, "rules")},
                "parameters": {name: {"workflowParameter": name} for name in values},
            }],
            "outputs": [
                {"name": "joined", "nodeOutput": {"nodeId": "join", "port": "data"}},
                {"name": "join-summary", "nodeOutput": {"nodeId": "join", "port": "summary"}},
            ],
        },
        "data_bindings": [{"input_name": name, "data_ref": ref} for name, ref in references.items()],
        "parameter_values": values,
        "parameter_sources": [
            {"name": name, "source": "USER", "explanation": "本轮用户指定"}
            for name in values
        ],
        "knowledge_refs": [], "unresolved_items": [], "limitations": [],
    }
    return references, draft, rules


def _draft(references):
    return {
        "title": "筛选、稳定排序与字段画像",
        "summary": "按用户提供的规则筛选后稳定排序，再统计输出字段的结构和缺失情况。",
        "graph": {
            "inputs": [
                {"name": name, "cardinality": "one", "mediaTypes": [ref["media_type"]], "formats": [ref["format"]]}
                for name, ref in references.items()
            ],
            "parameters": [],
            "nodes": [
                {
                    "nodeId": "filter", "operatorId": "table.filter", "operatorVersion": "1.0.0",
                    "inputs": {"data": {"workflowInput": "source"}, "rules": {"workflowInput": "filter-rules"}},
                    "parameters": {},
                },
                {
                    "nodeId": "sort", "operatorId": "table.sort", "operatorVersion": "1.0.0",
                    "inputs": {
                        "data": {"nodeOutput": {"nodeId": "filter", "port": "matched"}},
                        "rules": {"workflowInput": "sort-rules"},
                    },
                    "parameters": {},
                },
                {
                    "nodeId": "profile", "operatorId": "table.profile", "operatorVersion": "1.0.0",
                    "inputs": {"data": {"nodeOutput": {"nodeId": "sort", "port": "data"}}},
                    "parameters": {},
                },
            ],
            "outputs": [
                {"name": "sorted-table", "nodeOutput": {"nodeId": "sort", "port": "data"}},
                {"name": "profiles", "nodeOutput": {"nodeId": "profile", "port": "profiles"}},
            ],
        },
        "data_bindings": [{"input_name": name, "data_ref": ref} for name, ref in references.items()],
        "parameter_values": {}, "parameter_sources": [], "knowledge_refs": [],
        "unresolved_items": [], "limitations": [],
    }


def _raster_bytes(pixels, *, crs="EPSG:32650", x=500000, affine=None):
    values = np.array(pixels, dtype="int16")
    height, width = values.shape
    with MemoryFile() as file:
        with file.open(
            driver="GTiff", width=width, height=height, count=1, dtype="int16", nodata=-9999,
            crs=crs, transform=affine if affine is not None else from_origin(x, 4000000, 30, 30),
        ) as raster:
            raster.write(values, 1)
        return file.read()


HYDROLOGY = ("fill_sinks", "flow_direction", "flow_accumulation", "stream_extract")


def _hydrology_case(client, worker):
    dem = _raster_bytes([[100 - 3 * y - x for x in range(7)] for y in range(7)])
    terrain = _upload(client, worker, "DEM", "geotiff", dem, semantics={"valueKind": "dem"})
    steps = [
        ("fill_sinks", "dem", {"workflowInput": "terrain"}),
        ("flow_direction", "dem", {"nodeOutput": {"nodeId": "fill_sinks", "port": "dem"}}),
        ("flow_accumulation", "flowDirection", {"nodeOutput": {"nodeId": "flow_direction", "port": "flowDirection"}}),
        ("stream_extract", "accumulation", {"nodeOutput": {"nodeId": "flow_accumulation", "port": "accumulation"}}),
    ]
    count = "minimumContributingCells"
    draft = {
        "title": "提取地表汇流河网",
        "summary": "先填洼，再计算流向和汇流累积，最后以三格汇水面积为门槛提取河网。",
        "graph": {
            "inputs": [{"name": "terrain", "cardinality": "one", "formats": ["geotiff"], "mediaTypes": [terrain["media_type"]]}],
            "parameters": [count],
            "nodes": [
                {
                    "nodeId": name, "operatorId": f"hydrology.{name}", "operatorVersion": "1.0.0",
                    "inputs": {port: binding},
                    "parameters": {count: {"workflowParameter": count}} if name == "stream_extract" else {},
                }
                for name, port, binding in steps
            ],
            "outputs": [
                {"name": "accumulation", "nodeOutput": {"nodeId": "flow_accumulation", "port": "accumulation"}},
                {"name": "streams", "nodeOutput": {"nodeId": "stream_extract", "port": "streams"}},
            ],
        },
        "data_bindings": [{"input_name": "terrain", "data_ref": terrain}],
        "parameter_values": {count: 3},
        "parameter_sources": [{"name": count, "source": "USER", "explanation": "本轮用户指定汇水格数"}],
        "knowledge_refs": [], "unresolved_items": [], "limitations": [],
    }
    return {"terrain": terrain}, draft


def _worker_child(monkeypatch, image, runtime):
    from scripts.maintenance.project_environment import docker_executable

    spawn = asyncio.create_subprocess_exec

    async def launch(*args, **kwargs):
        if len(args) >= 7 and args[2] == "platform_executor_service.operator_child":
            payload = json.loads(Path(args[-2]).read_bytes())
            if payload["runtime"] == runtime:
                workspace = Path(args[-2]).parent
                package = ROOT / "packages/operator-pack"
                # The Docker launcher uses its regular assembly root. The
                # test Executor reads its own config before spawning the child.
                kwargs["env"] = {key: value for key, value in kwargs["env"].items() if key != "PLATFORM_GENERATED_ROOT"}
                pythonpath = "/operator-pack/src"
                if runtime == "qgis":
                    pythonpath += ":/usr/share/qgis/python:/usr/share/qgis/python/plugins"
                return await spawn(
                    docker_executable(), "run", "--rm", "--pull=never", "--network=none", "--read-only",
                    "--tmpfs", "/tmp:rw,nosuid,nodev,size=2g", "--user", "0:0",
                    "--mount", f"type=bind,src={workspace},dst={workspace}",
                    "--mount", f"type=bind,src={package},dst=/operator-pack,readonly",
                    "--workdir", str(workspace),
                    "--env", "XDG_DATA_HOME=/tmp/qgis-data",
                    "--env", "XDG_CONFIG_HOME=/tmp/qgis-config",
                    "--env", "XDG_CACHE_HOME=/tmp/qgis-cache",
                    "--env", f"PYTHONPATH={pythonpath}", "--env", "GDAL_DRIVER_PATH=disable",
                    image, "/usr/bin/python3", *args[1:], **kwargs,
                )
        return await spawn(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)


def _raster_draft(references, nodata=255):
    values = {"statistic": "mean", "minimum": 2, "type": "uint8", "nodata": nodata}
    return {
        "title": "逐像元平均与重分类",
        "summary": "计算两幅已对齐栅格共同有效像元的平均值，再按规则重分类。",
        "graph": {
            "inputs": [
                {"name": name, "cardinality": "one", "mediaTypes": [ref["media_type"]], "formats": [ref["format"]]}
                for name, ref in references.items()
            ],
            "parameters": list(values),
            "nodes": [
                {
                    "nodeId": "mean", "operatorId": "raster.cell_statistics", "operatorVersion": "1.0.0",
                    "inputs": {"rasters": {"collection": [{"workflowInput": "left"}, {"workflowInput": "right"}]}},
                    "parameters": {"statistic": {"workflowParameter": "statistic"},
                                   "minimumValidInputs": {"workflowParameter": "minimum"}},
                },
                {
                    "nodeId": "classes", "operatorId": "raster.reclassify", "operatorVersion": "1.0.0",
                    "inputs": {"raster": {"nodeOutput": {"nodeId": "mean", "port": "raster"}},
                               "rules": {"workflowInput": "rules"}},
                    "parameters": {"outputType": {"workflowParameter": "type"}, "noData": {"workflowParameter": "nodata"}},
                },
            ],
            "outputs": [
                {"name": "mean", "nodeOutput": {"nodeId": "mean", "port": "raster"}},
                {"name": "classes", "nodeOutput": {"nodeId": "classes", "port": "raster"}},
            ],
        },
        "data_bindings": [{"input_name": name, "data_ref": ref} for name, ref in references.items()],
        "parameter_values": values,
        "parameter_sources": [
            {"name": name, "source": "USER", "explanation": "来自本轮请求"} for name in values
        ],
        "knowledge_refs": [], "unresolved_items": [], "limitations": [],
    }


def _bridge(client, request):
    response = client.request(
        request.method, request.url.raw_path.decode(), headers=request.headers, content=request.content,
    )
    return httpx.Response(response.status_code, headers=response.headers, content=response.content)


def _table_quality_case(data, worker, scenario):
    table = io.BytesIO()
    parquet.write_table(pa.table({
        "raw_id": ["001", "001", "001", "002", "003", "004", "005", None, None],
        "rank": [1, 3, 3, 2, 2, 2, 2, 2, 1],
        "amount": ["3", "8", "9", "22", None, "10", "0", "4", "7"],
        "flag": ["ok", "ok", "ok", "hold", "ok", "ok", "ok", "ok", "ok"],
        "extra": [1] * 9,
    }), table)
    rules = {
        "projection": {
            "schemaVersion": "table-project-rules.v1",
            "columns": [
                {"source": source, "target": target, "type": dtype}
                for source, target, dtype in (
                    ("raw_id", "station_id", "string"), ("rank", "rank", "int64"),
                    ("amount", "value", "float64"), ("flag", "flag", "string"),
                )
            ],
        },
        "ordering": {
            "schemaVersion": "table-sort-rules.v1",
            "keys": [{"field": "rank", "direction": "desc", "nulls": "last"}],
        },
        "keys": {"schemaVersion": "table-deduplicate-rules.v1", "keys": ["station_id"]},
        "checks": {
            "schemaVersion": "table-validation-rules.v1",
            "constraints": [
                {"id": "id-required", "kind": "notNull", "field": "station_id"},
                {"id": "value-required", "kind": "notNull", "field": "value"},
                {"id": "value-range", "kind": "range", "field": "value", "minimum": 0, "maximum": 10},
                {"id": "allowed-flag", "kind": "enum", "field": "flag", "values": ["ok"]},
            ],
        },
    }
    if scenario == "table-quality-invalid-rules":
        rules["checks"] = {"schemaVersion": "table-validation-rules.v1", "constraints": []}
    missing = scenario == "table-quality-missing-rules"
    references = {"source": _upload(data, worker, "合成原始表", "parquet", table.getvalue())}
    references.update({
        name: _upload(data, worker, f"{name} 规则", "json", json.dumps(rule).encode())
        for name, rule in rules.items() if not (missing and name == "checks")
    })
    inputs = [
        {
            "name": name, "cardinality": "one",
            "mediaTypes": [references[name]["media_type"] if name in references else "application/json"],
            "formats": [references[name]["format"] if name in references else "json"],
        }
        for name in ("source", *rules)
    ]
    nodes = []
    for node, operator, upstream_port, rules_name in TABLE_QUALITY_STEPS:
        bound = {"workflowInput": "source"} if not nodes else {
            "nodeOutput": {"nodeId": nodes[-1]["nodeId"], "port": upstream_port},
        }
        nodes.append({
            "nodeId": node, "operatorId": operator, "operatorVersion": "1.0.0",
            "inputs": {"data": bound, **({"rules": {"workflowInput": rules_name}} if rules_name else {})},
            "parameters": {"keep": {"workflowParameter": "keep"}} if node == "deduplicate" else {},
        })
    draft = {
        "title": "表格整理、逐行校验与有效数据画像",
        "summary": "按指定规则整理并校验表格，对有效行生成字段画像。",
        "graph": {
            "inputs": inputs, "parameters": ["keep"], "nodes": nodes,
            "outputs": [
                {"name": port, "nodeOutput": {"nodeId": node, "port": port}}
                for node, port in TABLE_QUALITY_OUTPUTS
            ],
        },
        "data_bindings": [{"input_name": name, "data_ref": ref} for name, ref in references.items()],
        "parameter_values": {"keep": "first"},
        "parameter_sources": [{"name": "keep", "source": "USER", "explanation": "保留排序后第一条"}],
        "knowledge_refs": [], "limitations": [],
        "unresolved_items": ["缺少行级校验规则 checks 的 JSON 数据版本"] if missing else [],
    }
    task = (
        "对合成表按指定顺序处理。"
        "第一步按 projection 规则选择列、重命名并严格转换类型（project）。"
        "第二步按 ordering 规则稳定排序（sort）。"
        "第三步用 keys 规则对排序结果去重（deduplicate），保留每组第一条（first）。"
        "第四步用 checks 规则校验去重后的唯一行（validate）。"
        "第五步只对校验有效行生成字段画像（profile）。"
        "请发布去掉的重复行、校验有效行、校验违规行、违规明细、有效行的字段画像五份 Parquet。"
        "规则须绑定原始 JSON 文件的精确版本。"
    )
    if missing:
        task += "行级校验规则 checks 尚未上传。请保留这个必填输入和全部步骤，记录缺项。"
    return references, draft, task


def _reference_grid_case(data, worker):
    x, y = transform("EPSG:3857", "EPSG:4326", [0, 2000], [0, 3000])
    geographic = _raster_bytes(
        [[0, 30], [40, -9999], [70, 80]], crs="EPSG:4326",
        affine=from_bounds(x[0], y[0], x[1], y[1], 2, 3),
    )
    shifted = _raster_bytes(
        [[0, 10, 20, 30], [10, 20, 30, 40], [20, 30, 40, 50], [30, 40, 50, 60]],
        crs="EPSG:3857", affine=from_origin(-500, 3500, 1000, 1000),
    )
    template = _raster_bytes(
        [[-9999] * 3] * 3, crs="EPSG:3857", affine=from_origin(0, 3000, 1000, 1000),
    )
    rules = b'[{"minInclusive":null,"maxExclusive":25,"value":1},{"minInclusive":25,"maxExclusive":null,"value":2}]'
    references = {
        "geographic": _upload(data, worker, "跨 CRS 栅格", "geotiff", geographic),
        "shifted": _upload(data, worker, "偏移栅格", "geotiff", shifted),
        "reference": _upload(data, worker, "参考栅格", "geotiff", template),
        "rules": _upload(data, worker, "重分类规则", "json", rules),
    }
    authored = _raster_draft(references)
    authored["summary"] = "对齐到指定参考网格，计算共同有效像元的均值，再按给定规则重分类。"
    graph = authored["graph"]
    steps = (
        ("project", "raster.reproject", "1.1.0", "geographic", "nearest"),
        ("align", "raster.align_grid", "1.0.0", "shifted", "bilinear"),
    )
    for node, operator, version, source, method in steps:
        parameter = f"{node}-method"
        graph["parameters"].append(parameter)
        authored["parameter_values"][parameter] = method
        authored["parameter_sources"].append({
            "name": parameter, "source": "USER", "explanation": "来自本轮请求",
        })
        graph["nodes"].insert(0, {
            "nodeId": node, "operatorId": operator, "operatorVersion": version,
            "inputs": {"raster": {"workflowInput": source}, "reference": {"workflowInput": "reference"}},
            "parameters": {"resampling": {"workflowParameter": parameter}},
        })
    graph["nodes"][2]["inputs"]["rasters"]["collection"] = [
        {"nodeOutput": {"nodeId": node, "port": "raster"}} for node, *_ in steps
    ]
    task = (
        "根据下列精确版本，以 reference 为唯一参考网格。参考栅格仅定义目标网格。"
        "先核对 geographic 和 shifted 是否与参考栅格匹配，自动编排所需的对齐步骤。"
        "两份栅格都处理第 1 波段，geographic 使用最近邻（nearest），shifted 使用双线性（bilinear）。"
        "对这两份处理结果各自的第 1 波段计算逐像元均值（cell statistics），"
        "每个位置必须有两个有效像元，否则保留 NoData。再按 rules 重分类（reclassify），"
        f"类型为 uint8、NoData 为 255。只发布均值和分类两个 GeoTIFF。rules 内容为 {rules.decode()}。"
    )
    return references, authored, task


def _catalog_scenarios():
    requested = os.getenv("COMPOSITION_E2E_OPERATOR_SCOPE")
    if not requested:
        return {}
    product = json.loads((ROOT / "products/groundwater/product-manifest.json").read_bytes())
    domain_ref = product["domainPackage"]
    domain = ROOT / "domains" / domain_ref["domainId"] / domain_ref["version"]
    manifest = json.loads((domain / "domain-manifest.json").read_bytes())
    pinned = {
        f"{node['operatorId']}@{node['operatorVersion']}"
        for path in manifest["workflowDefinitions"]
        for node in json.loads((domain / path).read_bytes())["nodes"]
    }
    catalog = ROOT / "packages/operator-pack/catalog/operators"
    selected = {}
    for identity in sorted(set(product["compositionOperators"]) - pinned):
        identifier, version = identity.split("@")
        operator = json.loads((catalog / identifier / version / "manifest.json").read_bytes())
        if requested in {"all", operator["runtime"], identity}:
            selected[identity] = operator
    if not selected:
        raise ValueError(f"Empty composition acceptance scope {requested!r}")
    return selected


CATALOG_SCENARIOS = _catalog_scenarios()


def _catalog_case(client, worker, catalog, operator):
    identity = operator["id"], operator["version"]
    smoke = json.loads((catalog / "operators" / identity[0] / identity[1] / "smoke.json").read_bytes())
    fixtures = json.loads((catalog / "fixtures/index.json").read_bytes())["fixtures"]
    references, ports = {}, {}
    roles = operator.get("composition", {}).get("portSemantics", {})
    for port in operator["inputs"]:
        name = port["name"]
        members = smoke["inputs"][name]
        members = members if isinstance(members, list) else [members]
        bindings = []
        for member in members:
            if operator["runtime"] == "remote_http":
                directory = Path(os.environ["COMPOSITION_E2E_PEST_FIXTURES"])
                format_id = port["formats"][0]
                content = (directory / f"{name}.{format_id}").read_bytes()
            else:
                fixture = fixtures[member["fixtureId"]]
                format_id = fixture["format"]
                content = (catalog / "fixtures" / fixture["path"]).read_bytes()
            data_name = f"input-{len(references)}"
            role = roles.get(name, {}).get("requires", {}).get("valueKind")
            references[data_name] = _upload(
                client, worker, data_name, format_id, content,
                semantics={"valueKind": role} if role else None,
            )
            bindings.append({"workflowInput": data_name})
        ports[name] = {"collection": bindings} if port["cardinality"] == "many" else bindings[0]
    values = smoke["parameters"]
    expected = {port: item.get("format", "json") for port, item in smoke["expectedOutputs"].items()}
    draft = {
        "title": operator["title"],
        "summary": "按已确认的输入和显式参数运行单算子并发布结果。",
        "graph": {
            "inputs": [
                {"name": name, "cardinality": "one", "mediaTypes": [ref["media_type"]], "formats": [ref["format"]]}
                for name, ref in references.items()
            ],
            "parameters": list(values),
            "nodes": [{
                "nodeId": "analyze", "operatorId": identity[0], "operatorVersion": identity[1],
                "inputs": ports, "parameters": {name: {"workflowParameter": name} for name in values},
            }],
            "outputs": [
                {"name": port, "nodeOutput": {"nodeId": "analyze", "port": port}} for port in expected
            ],
        },
        "data_bindings": [{"input_name": name, "data_ref": ref} for name, ref in references.items()],
        "parameter_values": values,
        "parameter_sources": [
            {"name": name, "source": "USER", "explanation": "本轮合成验收指定"} for name in values
        ],
        "knowledge_refs": [], "unresolved_items": [], "limitations": [],
    }
    return references, draft, expected


@pytest.fixture
def assembly(tmp_path, monkeypatch):
    with monkeypatch.context() as assembly_environment:
        assembly_environment.syspath_prepend(str(ROOT / "platform/assembler"))
        assembly_environment.setenv("PLATFORM_GENERATED_ROOT", str(tmp_path / "assembly"))
        from assembler import compile_product

        return compile_product(ROOT, "groundwater/product-manifest.json")


@pytest.fixture(scope="module")
def native_site():
    from scripts.development.dev import _native_runtime_python

    with _native_runtime_python() as (native_python, _):
        yield next(native_python.parent.parent.glob("lib/python*/site-packages"))


def _assembled_kernel_settings(assembly, original):
    return KernelSettings.from_config(
        assembly / "kernel-config.json",
        data_root=original.data_root, project_id="p",
        agent_provider=original.agent_provider, agent_protocol=original.agent_protocol,
        agent_base_url=original.agent_base_url, agent_model=original.agent_model,
        agent_api_key=original.agent_api_key,
        agent_context_window_tokens=original.agent_context_window_tokens,
        agent_max_output_tokens=original.agent_max_output_tokens,
        agent_request_timeout_seconds=original.agent_request_timeout_seconds,
        agent_segment_timeout_seconds=original.agent_segment_timeout_seconds,
        intent_router=original.intent_router,
    )


_MONITORING_TREND_REQUEST = (
    "请对附件运行监测趋势，分析季节趋势和阈值事件。"
    "分组字段=station，时间字段=time，数值字段=value，频率=月，季节周期=月，期间统计=平均值，"
    "基准期=2020-01-01 至 2022-12-31，对比期=2023-01-01 至 2025-12-31，"
)


def _publish_monitoring_observations(data, worker, *, typed_time=False):
    path = ROOT / "packages/operator-pack/catalog/fixtures/data/monitoring-timeseries.parquet"
    content = path.read_bytes()
    if typed_time:
        # The golden fixture uses ISO strings. The same observations with a
        # typed UTC time column can satisfy the capability's Data precondition.
        table = parquet.read_table(io.BytesIO(content))
        times = pc.cast(table["time"], pa.timestamp("ms", tz="UTC"))
        table = table.set_column(table.schema.get_field_index("time"), "time", times)
        encoded = io.BytesIO()
        parquet.write_table(table, encoded)
        content = encoded.getvalue()
    reference = _upload(data, worker, "合成监测序列", "parquet", content)
    version_path = f"/data/v3/versions/{reference['version_id']}"
    version = data.get(version_path).raise_for_status().json()
    value_field = next(field["fieldId"] for field in version["descriptor"]["fields"] if field["name"] == "value")
    revision = data.post(version_path + "/semantics", json={
        "expectedSemanticId": reference["semantic_id"],
        "semantics": {
            "fields": {value_field: {"unit": "m", "meaning": "groundwater monitoring level"}},
            "timeRange": "2020-01-01/2025-12-31", "timezone": "UTC",
        },
    }).raise_for_status().json()
    reference["semantic_id"] = revision["semanticId"]
    return DataVersionRefV2.from_storage_dict(reference).as_dict()


def _monitoring_preparation_result(call, call_id="prepare"):
    return literal_eval(next(
        item["output"] for item in call.input
        if item.get("type") == "function_call_output" and item.get("call_id") == call_id
    ))


def _assert_monitoring_capability_tools(agent, settings):
    model_tools = {model.tool_name for model in settings.models}
    exposed = [{tool.name for tool in call.tools} for call in agent.calls]
    assert exposed[0] & model_tools == {"run_monitoring_trend"}
    assert all(not names & {"list_operators", "get_operator", "validate_workflow"} for names in exposed)
    assert all(names & model_tools <= {"run_monitoring_trend"} for names in exposed)


def _monitoring_trend_steps(arguments, reference, *, selected_by_capability=False, preparation_call_id="prepare"):
    def plan(call):
        prepared = _monitoring_preparation_result(call, preparation_call_id)
        assert prepared["status"] == "READY", prepared
        assert prepared["selection_policy"] == "PINNED"
        exact = DataVersionRefV2.from_storage_dict(prepared["data_references"]["versionId"])
        assert exact.as_dict() == reference
        assert prepared["parameters"] == {key: value for key, value in arguments.items() if key != "versionId"}
        assert [check["status"] for check in prepared["checks"] if check["kind"] == "FIELD_UNIT"] == ["PASS"]
        assert [check["status"] for check in prepared["checks"] if check["kind"] == "TIME_RANGE"] == ["PASS"] * 3
        if selected_by_capability:
            sources = prepared["parameter_sources"]
            assert sources["expectedStart"] == sources["expectedEnd"] == "DATA"
            assert sources["eventThreshold"] == "USER"
            assert sources["seasonalTrendEnabled"] == "WORKFLOW_PROFILE"
            assert sources["alpha"] == "MODEL_DEFAULT"
        return [function_call("record_plan", {
            "goal": "分析监测序列",
            "steps": [{"step_id": "trend", "tool": "run_monitoring_trend", "purpose": "计算趋势和诊断"}],
            "reason": "输入版本和计算参数已确定",
        }, call_id="plan")]

    def reply(call):
        result = json.loads(next(
            item["output"] for item in call.input
            if item.get("type") == "function_call_output" and item.get("call_id") == "trend"
        ))
        assert result["status"] == "COMPLETE", result
        return [submit_reply("监测趋势计算已完成。", refs=[result["workflow_run_id"]], status=True)]

    return [
        ModelStep(output=[function_call("prepare_task_inputs", {
            "target_tool": "run_monitoring_trend", "arguments": {} if selected_by_capability else arguments,
            "selection_policy": "PINNED",
        }, call_id=preparation_call_id)]),
        ModelStep.respond(plan),
        ModelStep(output=[function_call("run_monitoring_trend", arguments, call_id="trend")]),
        ModelStep.respond(reply),
    ]


def _wait_for_session(api, session_path, expected):
    for _ in range(300):
        session = api.get(session_path).raise_for_status().json()
        states = session["agentSteps"]
        if expected in states or "FAILED" in states:
            assert states[-1] == expected, session
            return session
        time.sleep(0.05)
    raise AssertionError(session)


async def _run_native_workflow_worker(executor_http, data_worker, kernel_http, run_path, capacity, *, session_path=None):
    worker = ExecutorWorker("native", capacity["slots"], capacity["memoryBytes"])
    await worker.client.aclose()
    worker.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: _bridge(executor_http, req)))
    try:
        (await worker.client.post("http://executor/internal/workers/v3/heartbeat", json=worker.identity)).raise_for_status()
        for _ in range(600):
            response = await worker.client.post("http://executor/internal/workers/v3/nodes:claim", json=worker.identity)
            lease = response.raise_for_status().json()["lease"]
            if lease is not None:
                await worker._execute(lease)
            data_worker.once()
            run = kernel_http.get(run_path, params={"projectId": "p"}).raise_for_status().json()
            assert run["status"] != "FAILED", run
            if run["status"] == "SUCCEEDED":
                if session_path is None:
                    return run
                session = kernel_http.get(session_path).raise_for_status().json()
                assert "FAILED" not in session["agentSteps"], session
                if session["agentSteps"][-1:] == ["COMPLETE"]:
                    return run
            await asyncio.sleep(0.025)
        raise AssertionError(run)
    finally:
        await worker.client.aclose()


@pytest.mark.parametrize("route_kind", ["profile", "unprofiled", "capability"])
def test_monitoring_trend_publishes_exact_results(
    assembly, kernel_settings, request, tmp_path, monkeypatch, route_kind,
):
    selected_profile = route_kind != "unprofiled"
    if selected_profile:
        from scripts.development.workflow_profile import describe_workflow_result, select_workflow_profile

        platform = json.loads((assembly / "workflow-platform.json").read_text())
        profile = next(item for item in platform["profiles"] if item["ref"]["id"] == "groundwater.monitoring-trend.seasonal-and-threshold")
        profile_ref = profile["ref"]
        assert profile_ref["id"] == "groundwater.monitoring-trend.seasonal-and-threshold"
        assert profile_ref["version"] == "1.0.0"
    else:
        historical_model = json.loads((
            ROOT / "domains/groundwater/6.1.0/models/monitoring-trend.model.json"
        ).read_text())
    development = ROOT / "products/groundwater/development"
    golden = json.loads((development / "workflow-goldens/monitoring-trend.json").read_text())
    parameters = json.loads((development / "workflow-golden-cases.v2.json").read_text())["cases"]["monitoring-trend"]["parameters"]
    data_config = replace(request.getfixturevalue("data_settings"), project="p")
    data_worker = DataWorker(data_config)
    executor_config = assembly / "executor-config.json"
    native_site = request.getfixturevalue("native_site")
    monkeypatch.setenv("EXECUTOR_CONFIG_PATH", str(executor_config))
    monkeypatch.setenv("EXECUTOR_CONTROL_URL", "http://executor")
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join((
        str(ROOT / "packages/operator-pack/src"), str(ROOT / "services/executor/src"),
        str(ROOT / "packages/protocol-contracts/src"), str(native_site),
    )))
    for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        monkeypatch.setenv(key, "1")

    with ExitStack() as stack:
        data = stack.enter_context(TestClient(data_app(data_config)))
        input_ref = _publish_monitoring_observations(data, data_worker, typed_time=route_kind == "capability")
        arguments = {**parameters, "versionId": input_ref["versionId"]}
        if route_kind == "profile":
            user_parameters = {
                key: value for key, value in parameters.items()
                if key not in {"alpha", "threshold", "seasonalTrendEnabled", "thresholdEventsEnabled"}
            }
            selection = select_workflow_profile(
                assembly, profile_ref, role="researcher", inputs={"observations": input_ref}, parameters=user_parameters,
            )
            assert selection == select_workflow_profile(
                assembly, profile_ref, role="researcher", inputs={"observations": input_ref}, parameters=user_parameters,
            )
            assert selection["inputReferences"] == [input_ref]
            assert selection["workflowSelection"]["inputBindings"] == {"versionId": 0}
            assert selection["workflowSelection"]["parameters"] == parameters
            expected_workflow_checksum = selection["workflowSelection"]["workflowChecksum"]
            message = {
                "requestedMode": "DIALOGUE", "message": "请准备此监测趋势档位，等我确认后运行。", **selection,
            }
        elif route_kind == "capability":
            expected_workflow_checksum = profile["workflowRef"]["checksum"]
            message = {
                "requestedMode": "AUTO", "inputReferences": [input_ref],
                "message": _MONITORING_TREND_REQUEST + "识别高于 10 m 的阈值事件。",
            }
        else:
            expected_workflow_checksum = historical_model["workflowRef"]["checksum"]
            execution = json.dumps({"tool": "run_monitoring_trend", "arguments": arguments})
            message = {
                "requestedMode": "AUTO",
                "message": f"请使用工具 run_monitoring_trend，等我确认后运行。[Model execution] {execution}",
            }
        data_transport = stack.enter_context(httpx.Client(transport=httpx.MockTransport(lambda req: _bridge(data, req))))
        executor = ExecutorApplication(
            executor_config, tmp_path / "executor", tmp_path / "objects", tmp_path / "work", "http://data", "p",
            client=data_transport,
        )
        executor_http = stack.enter_context(TestClient(executor_app(executor)))
        capacity = executor.control.config["workerRuntimes"]["native"]
        settings = _assembled_kernel_settings(assembly, kernel_settings())
        assert executor.definition_set_digest == settings.definition_set_digest

        def route(request):
            if request.url.host == "executor-control":
                return _bridge(executor_http, request)
            if request.url.host == "data-service":
                return _bridge(data, request)
            raise AssertionError(f"Unexpected request {request.url}")

        def application(agent):
            router = ScriptedModel([ModelStep(output=[assistant_message("EXECUTE_ON_DATA|NONE|NONE")])])
            return PlatformApplication(
                settings=settings, model=agent, intent_router_model=router,
                client=httpx.AsyncClient(transport=httpx.MockTransport(route)),
            )

        agent = ScriptedModel(_monitoring_trend_steps(
            arguments, input_ref, selected_by_capability=route_kind == "capability",
        ))
        with TestClient(kernel_app(application=application(agent))) as api:
            session_id = api.post("/api/v12/sessions", json={"projectId": "p"}).raise_for_status().json()["sessionId"]
            session_path = f"/api/v12/sessions/{session_id}"
            request_body = {"requestId": "monitoring-trend", **message}
            response = api.post(session_path + "/messages", json=request_body)
            assert response.status_code == 202, response.text
            waiting = _wait_for_session(api, session_path, "WAITING_CONFIRMATION")
            confirmation = waiting["confirmation"]
            assert confirmation["status"] == "PENDING"
            action, = confirmation["actions"]
            assert action["workflowChecksum"] == expected_workflow_checksum
            assert action["arguments"] == arguments
            assert not waiting["workflowRunIds"]
            if route_kind == "capability":
                assert action["modelChecksum"] == profile["modelRef"]["checksum"]
                assert action["inputReferences"] == {"versionId": input_ref}
                _assert_monitoring_capability_tools(agent, settings)
                call_count = len(agent.calls)
                same_turn = api.post(session_path + "/messages", json=request_body).raise_for_status().json()
                assert same_turn["agentRunId"] == response.json()["agentRunId"]
                assert same_turn["goalRevision"] == response.json()["goalRevision"]
                assert api.get(session_path).raise_for_status().json()["confirmation"] == confirmation
                assert len(agent.calls) == call_count
                trace_path = f"/api/v12/agent-runs/{same_turn['agentRunId']}/trace"
                trace = api.get(trace_path, params={"projectId": "p"}).raise_for_status().json()["trace"]
                capability = trace["businessCapability"]
                capability_ref = capability["capabilityRef"]
                assert capability_ref["id"] == "groundwater.monitoring-trend"
                assert capability_ref["version"] == "1.0.0"
                installed = json.loads((assembly / "kernel-config.json").read_text())["businessCapabilities"]
                assert capability_ref in [item["ref"] for item in installed]
                assert capability["profileRef"] == profile_ref
                assert capability["modelRef"] == profile["modelRef"]
                assert capability["workflowRef"] == profile["workflowRef"]
                assert capability["resultContractRef"] == profile["resultContractRef"]
                changed = api.post(session_path + f"/confirmations/{confirmation['confirmationId']}/revisions", json={
                    "requestId": "alter-threshold", "expectedGoalRevision": waiting["goalRevision"],
                    "actions": [{"toolCallId": action["toolCallId"], "arguments": {**arguments, "eventThreshold": 11.0}}],
                })
                assert changed.status_code == 409, changed.text
                assert changed.json()["code"] == "BUSINESS_CAPABILITY_REPREPARATION_REQUIRED"
                assert api.get(session_path).raise_for_status().json()["confirmation"] == confirmation
            with executor_database(executor.database) as connection:
                assert connection.execute("SELECT COUNT(*) FROM executor_jobs").fetchone()[0] == 0

            accepted = api.post(session_path + "/confirm", json={"confirmationId": confirmation["confirmationId"]})
            assert accepted.status_code == 202, accepted.text
            run_id, = api.get(session_path).raise_for_status().json()["workflowRunIds"]
            run_path = f"/api/v12/workflow-runs/{run_id}"
            run = asyncio.run(_run_native_workflow_worker(executor_http, data_worker, api, run_path, capacity))
            _wait_for_session(api, session_path, "COMPLETE")
            if route_kind == "capability":
                _assert_monitoring_capability_tools(agent, settings)
            nodes = api.get(run_path + "/nodes", params={"projectId": "p"}).raise_for_status().json()
            assert nodes["availability"] == "AVAILABLE" and len(nodes["nodes"]) == 10
            assert {node["status"] for node in nodes["nodes"]} == {"SUCCEEDED"}

        with TestClient(kernel_app(application=application(ScriptedModel([])))) as restarted:
            assert restarted.get(run_path, params={"projectId": "p"}).raise_for_status().json() == run
            publication = restarted.get(run_path + "/publication", params={"projectId": "p"}).raise_for_status().json()
            if route_kind == "capability":
                trace = restarted.get(trace_path, params={"projectId": "p"}).raise_for_status().json()["trace"]
                assert trace["businessCapability"] == capability
        assert publication["state"] == "PUBLISHED"
        publication_id = publication["publicationId"]
        receipt = data.get(f"/data/v3/publications/{publication_id}").raise_for_status().json()
        assert receipt["manifest"]["inputVersions"] == [input_ref["versionId"]]
        provenance = receipt["manifest"]["provenance"]
        assert provenance["workflow_checksum"] == expected_workflow_checksum
        assert provenance["model_checksum"] == run["modelChecksum"]
        by_name = {output["outputName"]: output for output in run["outputs"]}
        if selected_profile:
            result = describe_workflow_result(assembly, profile_ref, run, publication)
            assert result["runId"] == run_id
            assert result["profileRef"] == profile_ref
            assert result["contractRef"] == profile["resultContractRef"]
            assert result["executionStatus"] == "SUCCEEDED"
            assert result["publicationState"] == "PUBLISHED"
            assert result["resultStatus"] == "COMPLETE"
            assert result["businessAcceptance"] == "NOT_ASSESSED"
            assert by_name == {output["name"]: output["dataRef"] for output in result["outputs"]}
            assert len(by_name) == len(result["outputs"])
            assert {output["role"] for output in result["outputs"]} == {"PRIMARY_RESULT", "DIAGNOSTIC", "SUMMARY"}
        else:
            assert run["modelVersion"] == historical_model["version"]
            assert run["workflowVersion"] == historical_model["workflowRef"]["version"]
            assert run["workflowChecksum"] == expected_workflow_checksum
        assert len(by_name) == len(run["outputs"]) == 21
        assert set(by_name) == set(golden["outputs"])
        for name, ref in by_name.items():
            content = data.get(f"/data/v3/versions/{ref['versionId']}/objects/{ref['objectId']}/content").raise_for_status().content
            assert len(content) == ref["sizeBytes"]
            assert "sha256:" + sha256(content).hexdigest() == ref["checksum"]
            if name in golden["keyValues"]:
                assert json.loads(content) == golden["keyValues"][name]


def test_monitoring_trend_capability_requires_user_threshold(assembly, kernel_settings, request):
    data_config = replace(request.getfixturevalue("data_settings"), project="p")
    settings = _assembled_kernel_settings(assembly, kernel_settings())
    cases = ROOT / "products/groundwater/development/workflow-golden-cases.v2.json"
    parameters = json.loads(cases.read_text())["cases"]["monitoring-trend"]["parameters"]

    def clarify(call):
        prepared = _monitoring_preparation_result(call)
        assert prepared["status"] == "BLOCKED", prepared
        assert "eventThreshold" not in prepared["parameters"]
        return [submit_reply(
            "需要用户给定的阈值才能识别事件。",
            unresolved=[("您要使用多少米作为阈值？", ())],
        )]

    router = ScriptedModel([ModelStep(output=[assistant_message("EXECUTE_ON_DATA|NONE|NONE")]) for _ in range(2)])
    with TestClient(data_app(data_config)) as data:
        input_ref = _publish_monitoring_observations(data, DataWorker(data_config), typed_time=True)
        arguments = {**parameters, "versionId": input_ref["versionId"]}
        agent = ScriptedModel([
            ModelStep(output=[function_call("prepare_task_inputs", {
                "target_tool": "run_monitoring_trend",
                "arguments": arguments, "selection_policy": "PINNED",
            }, call_id="prepare")]),
            ModelStep.respond(clarify),
            *_monitoring_trend_steps(
                arguments, input_ref, selected_by_capability=True, preparation_call_id="follow-up-preparation",
            )[:3],
        ])

        def observe(request):
            assert request.url.host == "data-service", f"Unexpected request {request.url}"
            return _bridge(data, request)

        kernel = PlatformApplication(
            settings=settings, model=agent, intent_router_model=router,
            client=httpx.AsyncClient(transport=httpx.MockTransport(observe)),
        )
        with TestClient(kernel_app(application=kernel)) as api:
            session_id = api.post("/api/v12/sessions", json={"projectId": "p"}).raise_for_status().json()["sessionId"]
            path = f"/api/v12/sessions/{session_id}"
            api.post(path + "/messages", json={
                "requestId": "monitoring-trend-missing-threshold", "requestedMode": "AUTO", "inputReferences": [input_ref],
                "message": _MONITORING_TREND_REQUEST + "阈值比较=高于，识别超过该阈值的事件。",
            }).raise_for_status()
            session = _wait_for_session(api, path, "COMPLETE")
            preparation = api.get(path + "/preparation").raise_for_status().json()
            assert preparation["status"] == "BLOCKED"
            entry, = preparation["entries"]
            assert "eventThreshold" not in entry["parameters"]
            issue = entry["blockingIssues"][0]
            assert (issue["code"], issue["kind"], issue["parameter"]) == (
                "CAPABILITY_PARAMETER_REQUIRED", "MISSING_PARAMETER", "eventThreshold",
            )
            assert "阈值" in session["agentOutput"]["message"]
            assert session["confirmation"] is None
            assert session["workflowRunIds"] == []

            api.post(path + "/messages", json={
                "requestId": "monitoring-trend-threshold-supplied", "requestedMode": "AUTO",
                "message": "高于 10 m",
            }).raise_for_status()
            waiting = _wait_for_session(api, path, "WAITING_CONFIRMATION")
            assert waiting["goalRevision"] == session["goalRevision"] + 1
            assert waiting["workflowRunIds"] == []
            confirmation = waiting["confirmation"]
            assert confirmation["status"] == "PENDING"
            action, = confirmation["actions"]
            assert action["arguments"] == arguments
            assert action["inputReferences"] == {"versionId": input_ref}
            assert api.get(path + "/preparation").raise_for_status().json()["status"] == "READY"
    _assert_monitoring_capability_tools(agent, settings)


@pytest.mark.parametrize("scenario", [
    "table", "join", "hydrology", "raster", "crs", "grid", "nodata", "missing-crs", "reference-grid",
    *sorted(TABLE_QUALITY_SCENARIOS), *CATALOG_SCENARIOS,
])
def test_agent_composes_confirms_executes_and_publishes(
    assembly, kernel_settings, request, native_site, tmp_path, monkeypatch, scenario,
):
    single = CATALOG_SCENARIOS.get(scenario)
    qgis = scenario == "hydrology" or single is not None and single["runtime"] == "qgis"
    if qgis:
        qgis_image = os.getenv("COMPOSITION_E2E_QGIS_IMAGE")
        if qgis_image is None:
            pytest.skip("Set COMPOSITION_E2E_QGIS_IMAGE to the verified QGIS worker image digest")
        _worker_child(monkeypatch, qgis_image, "qgis")
    if single is not None and single["runtime"] == "native":
        if native_image := os.getenv("COMPOSITION_E2E_NATIVE_IMAGE"):
            _worker_child(monkeypatch, native_image, "native")
    direct = scenario in {"join", "hydrology"} or single is not None
    lost_receipts = []
    if single is not None and single["runtime"] == "remote_http":
        if not os.getenv("COMPOSITION_E2E_PEST_FIXTURES") or not os.getenv("POROUS_GROUNDWATER_BASE_URL"):
            pytest.skip("Set the isolated PEST fixture directory and remote binding")
        secrets = Path(os.environ["COMPOSITION_E2E_PEST_FIXTURES"])
        monkeypatch.setattr(RemoteOperationRunner, "_secret_path", staticmethod(lambda name: secrets / name))
        real_client = RemoteOperationRunner._client

        async def lose_first_receipt(response):
            if response.request.method == "POST" and response.request.url.path == "/api/v1/operations":
                if not lost_receipts:
                    await response.aread()
                    response.raise_for_status()
                    lost_receipts.append(response.json()["operationId"])
                    raise httpx.ReadTimeout("synthetic lost submit response")

        def client_with_lost_receipt(runner):
            client = real_client(runner)
            client.event_hooks["response"].append(lose_first_receipt)
            return client

        monkeypatch.setattr(RemoteOperationRunner, "_client", client_with_lost_receipt)
    executor_config = assembly / "executor-config.json"
    data_config = replace(request.getfixturevalue("data_settings"), project="p")
    data_worker = DataWorker(data_config)
    catalog = ROOT / "packages/operator-pack/catalog"
    with ExitStack() as stack:
        data = stack.enter_context(TestClient(data_app(data_config)))
        # The control environment and the operator environment are deliberately
        # distinct in deployment. Add only the missing Native dependency set.
        monkeypatch.setenv("PYTHONPATH", os.pathsep.join((
            str(ROOT / "packages/operator-pack/src"), str(native_site),
        )))
        for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
            monkeypatch.setenv(key, "1")
        if single is not None:
            operators = (single["id"],)
            references, authored, expected = _catalog_case(data, data_worker, catalog, single)
            task = single["tags"][-1] + "。按指定参数执行并发布成果。" + json.dumps(authored["parameter_values"])
            if single["id"] == "vector.spatial_join":
                ports = authored["graph"]["nodes"][0]["inputs"]
                task += "\n" + "\n".join(
                    f"输入角色 {port} = {references[ports[port]['workflowInput']]['version_id']}"
                    for port in ("target", "join")
                ) + "\n"
        elif scenario == "table":
            operators = OPERATORS
            table = io.BytesIO()
            parquet.write_table(pa.table({"site": ["A", "B", "C"], "value": [2, 1, 4]}), table)
            filter_rules = (catalog / "fixtures/data/table-filter-rules.json").read_bytes()
            sort_rules = (catalog / "fixtures/data/table-sort-rules.json").read_bytes()
            references = {
                "source": _upload(data, data_worker, "source", "parquet", table.getvalue()),
                "filter-rules": _upload(data, data_worker, "filter rules", "json", filter_rules),
                "sort-rules": _upload(data, data_worker, "sort rules", "json", sort_rules),
            }
            authored = _draft(references)
            task = (
                "对合成测试表按已给的规则筛选（filter），再稳定排序，最后对排序表生成字段画像（profile）。"
                "保留 Parquet 排序表和字段画像。以下两份规则已经核对，请绑定原文件版本。\n"
                f"筛选规则\n{filter_rules.decode()}\n排序规则\n{sort_rules.decode()}"
            )
        elif scenario == "hydrology":
            operators = tuple(f"hydrology.{name}" for name in HYDROLOGY)
            references, authored = _hydrology_case(data, data_worker)
            task = "对指定高程做填洼、流向和汇流累积，再按汇水格数至少为 3 提取河网。输出累积格数与河网。"
        elif scenario == "join":
            operators = ("table.join",)
            references, authored, join_rules = _join_case(data, data_worker, catalog)
            task = (
                "用两张表的 id 字段做一对一左连接，最多输出 100 行。请把指定的连接键保存为规则文件，"
                "再执行连接并输出连接表和摘要。左表是 left，右表是 right。"
            )
        elif scenario == "reference-grid":
            operators = ("raster.reproject", "raster.align_grid", *RASTER_OPERATORS)
            references, authored, task = _reference_grid_case(data, data_worker)
        elif scenario in TABLE_QUALITY_SCENARIOS:
            operators = tuple(operator for _, operator, _, _ in TABLE_QUALITY_STEPS)
            references, authored, task = _table_quality_case(data, data_worker, scenario)
        else:
            operators = RASTER_OPERATORS
            rules = b'[{"minInclusive":null,"maxExclusive":5,"value":1},{"minInclusive":5,"maxExclusive":null,"value":2}]'
            nodata = 256 if scenario == "nodata" else 255
            crs = "EPSG:32651" if scenario == "crs" else None if scenario == "missing-crs" else "EPSG:32650"
            references = {
                "left": _upload(data, data_worker, "左栅格", "geotiff", _raster_bytes([[2, -9999], [7, 4]])),
                "right": _upload(data, data_worker, "右栅格", "geotiff", _raster_bytes(
                    [[4, 8], [9, -9999]], crs=crs, x=500015 if scenario == "grid" else 500000,
                )),
                "rules": _upload(data, data_worker, "重分类规则", "json", rules),
            }
            authored = _raster_draft(references, nodata=nodata)
            task = (
                "先对两份合成栅格的第 1 波段计算逐像元平均值（cell statistics），每个位置必须有两个有效像元，"
                "否则保留 NoData，再对均值栅格按已核对的 JSON 规则重分类（reclassify）。"
                f"分类输出用 uint8，NoData={nodata}，保留均值和分类两个 GeoTIFF。"
                f"绑定原始规则文件版本，规则内容为 {rules.decode()}。"
            )
        missing_rules = scenario == "table-quality-missing-rules"
        invalid_rules = scenario == "table-quality-invalid-rules"
        publication_calls = []

        def route_executor_data(request):
            if invalid_rules and "publications" in request.url.path:
                publication_calls.append(request.url.path)
            return _bridge(data, request)

        with httpx.Client(transport=httpx.MockTransport(route_executor_data)) as data_transport:
            executor = ExecutorApplication(
                executor_config, tmp_path / "executor", tmp_path / "objects", tmp_path / "work",
                "http://data", "p", client=data_transport,
            )
            executor_http = stack.enter_context(TestClient(executor_app(executor)))
            config = executor.control.config
            runtimes = (single["runtime"],) if single else ("native", "qgis") if qgis else ("native",)
            if direct:
                for runtime in runtimes:
                    capacity = config["workerRuntimes"][runtime]
                    executor.control.heartbeat({
                        "worker_id": f"acceptance-{runtime}", "runtime": runtime,
                        "catalog_digest": config["operatorCatalogDigest"],
                        "slots": capacity["slots"], "memory_bytes": capacity["memoryBytes"],
                    })
            original = kernel_settings()
            live = (
                scenario in {"table", "raster", "reference-grid", "join", "hydrology"} | TABLE_QUALITY_SCENARIOS
                and os.getenv("COMPOSITION_E2E_LIVE") == "1"
            )
            if live:
                endpoint = {
                    "base_url": "https://api.deepseek.com",
                    "model": os.getenv("COMPOSITION_E2E_MODEL", "deepseek-flash"),
                    "api_key": os.environ["DEEPSEEK_API_KEY"],
                }
                original = replace(
                    original, **{f"agent_{key}": value for key, value in endpoint.items()},
                    intent_router=replace(original.intent_router, **(endpoint | {"model": "deepseek-flash"})),
                )
            settings = _assembled_kernel_settings(assembly, original)
            assert executor.definition_set_digest == settings.definition_set_digest
            inspect = next(tool for tool in settings.observation_tools if tool.name == "inspect_data_version")
            assert inspect.progress is not None

            def inspect_table(request):
                # Data's synchronous inspection waits for its normal query worker.
                stop_worker = Event()

                def drain_queries():
                    while not stop_worker.is_set():
                        data_worker.once()
                        stop_worker.wait(0.025)

                worker = Thread(target=drain_queries)
                worker.start()
                try:
                    return _bridge(data, request)
                finally:
                    stop_worker.set()
                    worker.join()

            def route(request):
                if request.url.path.startswith("/internal/execution/") or request.url.path.startswith("/internal/health/"):
                    return _bridge(executor_http, request)
                if request.url.path.endswith("/inspect_data_table"):
                    return inspect_table(request)
                if request.url.path.startswith("/internal/tools/v4/"):
                    return _bridge(data, request)
                if scenario == "join" and request.url.path.startswith("/data/v3/publications"):
                    if request.method == "GET":
                        data_worker.once()
                    return _bridge(data, request)
                if live and request.url.path.startswith("/internal/tools/v5/"):
                    no_match = EvidenceBundleV1(
                        status="NO_MATCH", receipt_id="receipt-test", knowledge_revision=0,
                        index_generation="test", observed_at="2026-01-01T00:00:00Z", items=[],
                        coverage={}, budget={"returned_units": 0, "returned_chars": 0}, timings_ms={},
                    )
                    return httpx.Response(200, json={
                        "data": model_to_wire(no_match),
                        "outcome": {"outcomeCode": "evidence.no_match", "facts": {}},
                    })
                raise AssertionError(f"Unexpected request {request.url}")

            discovery = [ModelStep(output=[function_call("list_operators", {
                "progress_intent": {
                    "capability": "workflow.operator.discovery",
                    "template_key": "workflow.operator.discovery",
                    "objective": "找出符合两表连接任务的算子",
                },
                "query": single["tags"][-1] if single else "按键连接两张表",
                "capability": None, "domain": None, "after": None, "limit": 8,
            }, call_id="find-operator")])] if scenario == "join" or single else []
            def inspect_call(name, version_id):
                return function_call("inspect_data_version", {
                    "version_id": version_id,
                    "progress_intent": {
                        "capability": inspect.progress.capability,
                        "template_key": inspect.progress.template_key,
                        "objective": "核验本轮输入版本",
                    },
                }, call_id=name)

            def tool_result(call, call_id):
                return next(
                    literal_eval(item["output"])
                    for item in call.input
                    if item.get("type") == "function_call_output" and item.get("call_id") == call_id
                )

            def inspect_new_rules(call):
                result = tool_result(call, "prepare-join-rules")
                assert result["status"] == "RULES_READY", result
                return [inspect_call("join-rules", result["version_id"])]

            def validate_join(call):
                rules_ref = _data_reference(tool_result(call, "join-rules")["data"]).as_storage_dict()
                references["rules"] = rules_ref
                authored["data_bindings"].append({"input_name": "rules", "data_ref": rules_ref})
                return [function_call("validate_workflow", authored, call_id="validate-composition")]

            if scenario == "join":
                rule_request = {
                    "operator_id": "table.join", "operator_version": "1.0.0", "rules_port": "rules",
                    "rules": join_rules,
                    "data_inputs": {name: ref["version_id"] for name, ref in references.items()},
                }
                invalid_rule = {
                    **rule_request,
                    "rules": {**join_rules, "keys": [{"left": "missing", "right": "id"}]},
                }

                def prepare_valid_rules(call):
                    blocked = tool_result(call, "invalid-join-rules")
                    assert blocked == {
                        "status": "RULES_BLOCKED", "code": "RULE_FIELD_NOT_FOUND",
                        "port": "left", "fields": ["missing"],
                    }
                    return [function_call("prepare_task_inputs", rule_request, call_id="prepare-join-rules")]

                draft_steps = [
                    ModelStep(output=[function_call("prepare_task_inputs", invalid_rule, call_id="invalid-join-rules")]),
                    ModelStep.respond(prepare_valid_rules),
                    ModelStep.respond(inspect_new_rules),
                    ModelStep.respond(validate_join),
                    ModelStep.respond(lambda _: _reply(authored, cited=False).output),
                ]
            elif scenario == "hydrology":
                invalid_graph = deepcopy(authored)
                invalid_graph["graph"]["nodes"][-1]["inputs"]["accumulation"] = {
                    "nodeOutput": {"nodeId": "flow_direction", "port": "flowDirection"},
                }

                def validate_corrected_graph(call):
                    issue = tool_result(call, "invalid-hydrology")
                    assert issue["status"] == "INVALID"
                    assert issue["code"] == "COMPOSITION_FACT_MISMATCH"
                    assert "accumulation" in issue["diagnostic"]["path"]
                    assert issue["diagnostic"]["expected"] == "contributing-cells"
                    assert issue["diagnostic"]["actual"] == "d8-direction"
                    return [function_call("validate_workflow", authored, call_id="validate-composition")]

                draft_steps = [
                    ModelStep(output=[function_call("validate_workflow", invalid_graph, call_id="invalid-hydrology")]),
                    ModelStep.respond(validate_corrected_graph),
                    _reply(authored, cited=False),
                ]
            elif single and single["id"] == "vector.spatial_join":
                swapped = deepcopy(authored)
                ports = swapped["graph"]["nodes"][0]["inputs"]
                ports["target"], ports["join"] = ports["join"], ports["target"]

                def validate_corrected_spatial_join(call):
                    issue = tool_result(call, "swapped-spatial-inputs")
                    assert issue["status"] == "INVALID"
                    assert issue["code"] == "WORKFLOW_INPUT_ROLE_MISMATCH"
                    assert issue["path"].endswith("/inputs/target")
                    return [function_call("validate_workflow", authored, call_id="validate-composition")]

                draft_steps = [
                    ModelStep(output=[function_call("validate_workflow", swapped, call_id="swapped-spatial-inputs")]),
                    ModelStep.respond(validate_corrected_spatial_join),
                    _reply(authored, cited=False),
                ]
            elif single:
                draft_steps = [
                    ModelStep(output=[function_call("validate_workflow", authored, call_id="validate-composition")]),
                    _reply(authored, cited=False),
                ]
            else:
                draft_steps = [_reply(authored, cited=False)]
            agent = ScriptedModel([
                *discovery,
                *[
                    _operator(op, version=single["version"] if single else "1.1.0" if op == "raster.reproject" else "1.0.0")
                    for op in operators
                ],
                *[
                    ModelStep(output=[inspect_call(name, ref["version_id"])])
                    for name, ref in references.items()
                ],
                *draft_steps,
            ])
            route_intent = "EXECUTE_ON_DATA" if direct else "CREATE_WORKFLOW"
            router = ScriptedModel([ModelStep(output=[assistant_message(f"{route_intent}|NONE|NONE")])])
            kernel = PlatformApplication(
                settings=settings, model=None if live else agent, intent_router_model=None if live else router,
                client=httpx.AsyncClient(transport=httpx.MockTransport(route)),
            )
            api = stack.enter_context(TestClient(kernel_app(application=kernel)))
            sid = api.post("/api/v12/sessions", json={"projectId": "p"}).json()["sessionId"]
            message = (
                ("请处理这些数据。" if direct else "请编排可复用工作流，校验通过后提交我确认。")
                + task
                + " 本轮使用的版本为" + "，".join(f"{name}={ref['version_id']}" for name, ref in references.items())
                + "。"
                + ("" if missing_rules else "我已确定全部输入和处理规则。正常的运行时规则检查属于执行说明，校验通过即可提交确认。")
            )
            turn = api.post(f"/api/v12/sessions/{sid}/messages", json={
                "requestId": "compose", "requestedMode": "AUTO",
                "message": message,
            })
            assert turn.status_code == 202, turn.text
            for _ in range(900 if live else 100):
                session = api.get(f"/api/v12/sessions/{sid}").json()
                if session["confirmation"] is not None or (
                    session["workflowDraftId"] and set(session["agentSteps"]) & {"COMPLETE", "FAILED"}
                ):
                    break
                time.sleep(0.2 if live else 0.05)
            session = api.get(f"/api/v12/sessions/{sid}").json()
            draft_id = session["workflowDraftId"]
            assert draft_id, session
            if direct and not live:
                received = {
                    item["call_id"]: literal_eval(item["output"])
                    for item in agent.calls[-1].input
                    if item.get("type") == "function_call_output"
                    and item.get("call_id") in {"find-operator", "validate-composition", *operators}
                }
                assert received["validate-composition"]["status"] == "VALIDATED", received
                if scenario == "join":
                    hits = received["find-operator"]["items"]
                    assert hits[0]["operator_id"] == "table.join"
                    assert hits[0]["confirmation_supported"]
                    assert received["table.join"]["worker_status"] == "UP"
                elif single:
                    assert any(
                        hit["operator_id"] == single["id"] and hit["operator_version"] == single["version"]
                        and hit["confirmation_supported"] for hit in received["find-operator"]["items"]
                    )
            draft = api.get(f"/api/v12/sessions/{sid}/workflow-drafts/{draft_id}").json()
            assert draft["payload"] is not None, draft
            if missing_rules:
                _assert_missing_table_rules(draft["payload"], references)
                assert session["confirmation"] is None
                rejected = api.post(f"/api/v12/sessions/{sid}/confirm", json={"confirmationId": draft_id})
                assert rejected.status_code == 409 and rejected.json()["code"] == "CONFIRMATION_NOT_CURRENT"
                with executor_database(executor.database) as connection:
                    assert connection.execute("SELECT COUNT(*) FROM executor_jobs").fetchone()[0] == 0
                return
            expected_blocker = {
                "crs": "CRS_MISMATCH", "grid": "GRID_MISMATCH",
                "nodata": "NODATA_VALUE_INVALID", "missing-crs": "COMPOSITION_FACT_REQUIRED",
            }.get(scenario)
            if expected_blocker:
                assert not draft["payload"]["executionAllowed"], draft
                assert draft["payload"]["compositionCheck"]["code"] == expected_blocker, draft
                assert session["confirmation"] is None
                with executor_database(executor.database) as connection:
                    assert connection.execute("SELECT COUNT(*) FROM executor_jobs").fetchone()[0] == 0
                return
            assert draft["payload"]["executionAllowed"], draft
            assert draft["payload"]["compositionCheck"]["status"] == "VALIDATED"
            if scenario in {"raster", "reference-grid"}:
                required_checks = {"same-crs", "same-grid", "input-nodata", "output-nodata"}
                assert required_checks <= {
                    check["id"] for check in draft["payload"]["compositionCheck"]["checks"] if check["state"] == "PASS"
                }
            if scenario == "reference-grid":
                _assert_reference_grid_definition(draft["payload"]["composition"], references)
            if scenario in TABLE_QUALITY_SCENARIOS:
                _assert_table_quality_definition(draft["payload"]["composition"], references)
            confirmation = session["confirmation"]
            assert confirmation["status"] == "PENDING"
            assert confirmation["actions"][0]["workflowChecksum"] == draft["payload"]["composition"]["definitionDigest"]
            with executor_database(executor.database) as connection:
                assert connection.execute("SELECT COUNT(*) FROM executor_jobs").fetchone()[0] == 0
            response = api.post(f"/api/v12/sessions/{sid}/confirm", json={
                "confirmationId": confirmation["confirmationId"],
            })
            assert response.status_code == 202, response.text

            monkeypatch.setenv("EXECUTOR_CONFIG_PATH", str(executor_config))
            monkeypatch.setenv("EXECUTOR_CONTROL_URL", "http://executor")

            async def drive():
                workers = {}
                try:
                    for runtime in runtimes:
                        capacity = config["workerRuntimes"][runtime]
                        worker = ExecutorWorker(runtime, capacity["slots"], capacity["memoryBytes"])
                        await worker.client.aclose()
                        worker.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: _bridge(executor_http, req)))
                        workers[runtime] = worker
                        executor.control.heartbeat({
                            "worker_id": worker.worker_id, "runtime": worker.runtime,
                            "catalog_digest": worker.catalog_digest, "slots": worker.slots,
                            "memory_bytes": worker.memory_bytes,
                        })
                    remaining = set(operators)
                    for index, expected_operator in enumerate(operators[:-1] if invalid_rules else operators):
                        runtime = single["runtime"] if single else "qgis" if expected_operator == "hydrology.fill_sinks" else "native"
                        worker = workers[runtime]
                        for _ in range(200):
                            response = await worker.client.post(
                                "http://executor/internal/workers/v3/nodes:claim", json=worker.identity,
                            )
                            assert response.status_code == 200, response.text
                            if lease := response.json()["lease"]:
                                break
                            await asyncio.sleep(0.025)
                        else:
                            with executor_database(executor.database) as connection:
                                state = [tuple(row) for row in connection.execute("SELECT status,error FROM executor_jobs")]
                            raise AssertionError(f"Expected a {runtime} lease for {expected_operator}, job state {state}")
                        claimed_operator = lease["operatorId"]
                        if scenario == "reference-grid" and index < 2:
                            assert claimed_operator in remaining & {"raster.reproject", "raster.align_grid"}
                            method = "nearest" if claimed_operator == "raster.reproject" else "bilinear"
                            assert lease["parameters"]["resampling"] == method
                        else:
                            assert claimed_operator == expected_operator
                        remaining.remove(claimed_operator)
                        if expected_operator == "raster.cell_statistics":
                            assert len(lease["inputs"]["rasters"]) == 2
                        await worker._execute(lease)
                    for _ in range(300):
                        data_worker.once()
                        with executor_database(executor.database) as connection:
                            job = connection.execute("SELECT job_id, request_id, status, error FROM executor_jobs").fetchone()
                        run = kernel.workflow_execution.get("p", job["request_id"])
                        expected_status = "FAILED" if invalid_rules else "SUCCEEDED"
                        assert job["status"] not in {"SUCCEEDED", "FAILED"} or job["status"] == expected_status, dict(job)
                        if job["status"] == expected_status and run.status == expected_status:
                            break
                        await asyncio.sleep(0.025)
                    else:
                        raise AssertionError(dict(job))
                    return job["job_id"], job["request_id"]
                finally:
                    for worker in workers.values():
                        await worker.client.aclose()

            job_id, run_id = asyncio.run(drive())
            status = executor.control.get(job_id)
            if invalid_rules:
                assert status.status == "FAILED"
                assert not status.outputs and not kernel.workflow_execution.get("p", run_id).outputs
                assert "publicationId" not in executor.control.publication(job_id)
                assert not publication_calls
                with executor_database(executor.database) as connection:
                    failed = connection.execute("SELECT error FROM job_nodes WHERE status='FAILED'").fetchall()
                    assert [row["error"] for row in failed] == ["RULESET_INVALID"]
                    assert connection.execute("SELECT count(*) FROM job_nodes WHERE status='SUCCEEDED'").fetchone()[0] == 3
                return
            if single:
                assert {item.output_name: item.format_id for item in status.outputs} == expected
            else:
                assert len(status.outputs) == (len(TABLE_QUALITY_OUTPUTS) if scenario == "table-quality" else 2)
            publication = executor.control.publication(job_id)
            assert publication["state"] == "PUBLISHED"
            receipt = data.get(f"/data/v3/publications/{publication['publicationId']}").json()
            bound_versions = set(receipt["manifest"]["inputVersions"])
            if scenario == "join":
                source_versions = {references[name]["version_id"] for name in ("left", "right")}
                rule_id, = bound_versions - source_versions
                rule_version = data.get(f"/data/v3/versions/{rule_id}").json()
                assert rule_version["semantics"]["source"] == "agent_operator_rules"
                rule_raw = next(obj for obj in rule_version["objects"] if obj["role"] == "raw")
                saved = data.get(f"/data/v3/versions/{rule_id}/objects/{rule_raw['objectId']}/content")
                assert saved.json() == join_rules
                assert source_versions < bound_versions
            else:
                assert bound_versions == {ref["version_id"] for ref in references.values()}
            assert receipt["manifest"]["provenance"]["workflow_checksum"] == confirmation["actions"][0]["workflowChecksum"]
            if single:
                if single["runtime"] == "remote_http":
                    with executor_database(executor.database) as connection:
                        remote = connection.execute("SELECT remote_state_json FROM job_nodes").fetchone()[0]
                    assert [json.loads(remote)["remote_task_id"]] == lost_receipts
                for output in status.outputs:
                    content = data.get(f"/data/v3/versions/{output.version_id}/objects/{output.object_id}/content")
                    assert content.status_code == 200 and content.content
                    assert "sha256:" + sha256(content.content).hexdigest() == output.checksum
                return
            if scenario == "hydrology":
                rasters = {}
                for output in status.outputs:
                    result = data.get(f"/data/v3/versions/{output.version_id}/objects/{output.object_id}/content")
                    assert result.status_code == 200, result.text
                    meta = data.get(f"/data/v3/versions/{output.version_id}").json()["semantics"]
                    with MemoryFile(result.content) as file, file.open() as raster:
                        rasters[meta["valueKind"]] = raster.read(1)
                accumulation = rasters["contributing-cells"]
                assert accumulation.max() == 49
                np.testing.assert_array_equal(rasters["stream-mask"], accumulation >= 3)
                return
            if scenario == "join":
                outputs = {}
                for output in status.outputs:
                    result = data.get(f"/data/v3/versions/{output.version_id}/objects/{output.object_id}/content")
                    assert result.status_code == 200, result.text
                    outputs[output.format_id] = result.content
                assert parquet.read_table(io.BytesIO(outputs["parquet"])).to_pylist() == [
                    {"id": 1, "measurement": 4.0, "label": "A"},
                    {"id": 2, "measurement": 9.0, "label": None},
                ]
                assert json.loads(outputs["json"]) == {"rowCount": 2}
                assert kernel.workflow_execution.get("p", run_id).status == "SUCCEEDED"
                return
            if scenario == "table-quality":
                definition = draft["payload"]["composition"]
                provenance = receipt["manifest"]["provenance"]
                assert provenance["composition_environment_digest"] == definition["environmentRef"]["digest"]
                assert provenance["composition_validator_digest"] == definition["validatorRef"]["digest"]
                assert provenance["operator_catalog_digest"] == settings.operator_catalog_digest
                _assert_table_quality_outputs(data, status.outputs, definition)
                assert {item.output_name for item in kernel.workflow_execution.get("p", run_id).outputs} == {
                    item.output_name for item in status.outputs
                }
                with executor_database(executor.database) as connection:
                    stored = connection.execute("SELECT request_json FROM executor_jobs").fetchone()[0]
                replay = executor_http.post("/internal/execution/v6/jobs", json=JobRequestV6.from_storage_dict(json.loads(stored)).as_dict())
                assert replay.status_code == 202 and replay.json()["jobId"] == job_id
                return
            if scenario == "reference-grid":
                _assert_reference_grid_outputs(data, status.outputs)
                assert {item.output_name for item in kernel.workflow_execution.get("p", run_id).outputs} == {
                    item.output_name for item in status.outputs
                }
                return
            if scenario == "raster":
                _assert_raster_outputs(data, status.outputs)
                assert {item.output_name for item in kernel.workflow_execution.get("p", run_id).outputs} == {
                    item.output_name for item in status.outputs
                }
                with executor_database(executor.database) as connection:
                    stored = connection.execute("SELECT request_json FROM executor_jobs").fetchone()[0]
                submitted = JobRequestV6.from_storage_dict(json.loads(stored)).as_dict()
                submitted["requestId"] = "tampered-raster-grid"
                definition = submitted["composition"]
                for item in definition["inputs"].values():
                    for fact in item["facts"]:
                        if fact["property"] == "grid":
                            # Shift both rasters equally so their mutual grid
                            # check still passes. Data's exact facts must win.
                            fact["value"]["transform"][0] += 15
                digest = definition_digest(definition)
                definition["definitionDigest"] = submitted["modelChecksum"] = submitted["workflowChecksum"] = digest
                rejected = executor_http.post("/internal/execution/v6/jobs", json=submitted)
                assert rejected.status_code == 422
                assert rejected.json()["code"] == "RASTER_INPUT_FACTS_MISMATCH"
                with executor_database(executor.database) as connection:
                    assert connection.execute("SELECT COUNT(*) FROM executor_jobs").fetchone()[0] == 1
                return
            tables = {}
            for result in status.outputs:
                content = data.get(f"/data/v3/versions/{result.version_id}/objects/{result.object_id}/content")
                assert content.status_code == 200, content.text
                tables[result.output_name] = parquet.read_table(io.BytesIO(content.content))
            sorted_table = next(table for table in tables.values() if "value" in table.column_names)
            assert sorted_table.column("value").to_pylist() == [4, 2]
            profile_table = next(table for table in tables.values() if "field_name" in table.column_names)
            profiles = {row["field_name"]: row for row in profile_table.to_pylist()}
            assert set(profiles) == {"site", "value"}
            assert profiles["value"]["row_count"] == 2
            assert profiles["value"]["mean"] == 3
            run = kernel.workflow_execution.get("p", run_id)
            assert run.status == "SUCCEEDED"
            assert {item.output_name for item in run.outputs} == set(tables)
            with executor_database(executor.database) as connection:
                stored = connection.execute("SELECT request_json FROM executor_jobs").fetchone()[0]
            submitted = JobRequestV6.from_storage_dict(json.loads(stored)).as_dict()
            replay = executor_http.post("/internal/execution/v6/jobs", json=submitted)
            assert replay.status_code == 202 and replay.json()["jobId"] == job_id
            submitted["requestId"] = "tampered-composition"
            composition = submitted["composition"]
            first_input = next(iter(composition["inputs"].values()))
            first_input["dataRef"]["semanticChecksum"] = "sha256:" + "0" * 64
            rejected = executor_http.post("/internal/execution/v6/jobs", json=submitted)
            assert rejected.status_code == 422
            assert rejected.json()["code"] == "COMPOSITION_DEFINITION_DIGEST_MISMATCH"
            forged_digest = definition_digest(composition)
            composition["definitionDigest"] = submitted["modelChecksum"] = submitted["workflowChecksum"] = forged_digest
            rejected = executor_http.post("/internal/execution/v6/jobs", json=submitted)
            assert rejected.status_code == 422
            assert rejected.json()["code"] == "COMPOSITION_SEMANTIC_REVISION_MISMATCH"
            with executor_database(executor.database) as connection:
                assert connection.execute("SELECT COUNT(*) FROM executor_jobs").fetchone()[0] == 1


def _assert_table_quality_definition(definition, references):
    nodes = _assert_table_quality_graph(definition["nodes"], definition["outputs"], frozen=True)
    for _, operator, _, rules_name in TABLE_QUALITY_STEPS:
        if rules_name:
            bound = nodes[operator]["inputs"]["rules"]["workflowInput"]
            assert definition["inputs"][bound]["dataRef"]["versionId"] == references[rules_name]["version_id"]
    source = nodes["table.project"]["inputs"]["data"]["workflowInput"]
    assert definition["inputs"][source]["dataRef"]["versionId"] == references["source"]["version_id"]
    keep = nodes["table.deduplicate"]["parameters"]["keep"]["workflowParameter"]
    parameter = definition["parameters"][keep]
    assert (parameter["value"], parameter["source"]["kind"]) == ("first", "USER")


def _assert_table_quality_graph(nodes, outputs, *, frozen):
    by_operator = {
        node["operator"]["id"] if frozen else node["operatorId"]: node for node in nodes
    }
    assert len(by_operator) == len(nodes) == len(TABLE_QUALITY_STEPS)
    node_ids = {}
    upstream = None
    for alias, operator, upstream_port, _ in TABLE_QUALITY_STEPS:
        node = by_operator[operator]
        node_ids[alias] = node["nodeId"]
        assert (node["operator"]["version"] if frozen else node["operatorVersion"]) == "1.0.0"
        if upstream is not None:
            assert node["inputs"]["data"] == {"nodeOutput": {"nodeId": upstream, "port": upstream_port}}
        upstream = node["nodeId"]
    ports = outputs.values() if frozen else outputs
    assert {(item["nodeOutput"]["nodeId"], item["nodeOutput"]["port"]) for item in ports} == {
        (node_ids[node], port) for node, port in TABLE_QUALITY_OUTPUTS
    }
    return by_operator


def _assert_missing_table_rules(payload, references):
    assert not payload["executionAllowed"] and not payload.get("composition")
    graph = payload["workflow"]
    nodes = _assert_table_quality_graph(graph["nodes"], graph["outputs"], frozen=False)
    validation = nodes["table.validate"]
    name = validation["inputs"]["rules"]["workflowInput"]
    assert payload["missingInputs"] == [{
        "kind": "DATA", "name": name, "requiredBy": [{"nodeId": validation["nodeId"], "port": "rules"}],
    }]
    assert payload["unresolvedItems"]
    assert {binding["dataRef"]["versionId"] for binding in payload["dataBindings"]} == {
        ref["version_id"] for ref in references.values()
    }


def _assert_table_quality_outputs(data, outputs, definition):
    tables = {}
    for result in outputs:
        assert result.format_id == "parquet"
        version = data.get(f"/data/v3/versions/{result.version_id}").json()
        raw = next(obj for obj in version["objects"] if obj["role"] == "raw")
        assert raw["objectId"] == result.object_id and raw["checksum"] == result.checksum
        content = data.get(f"/data/v3/versions/{result.version_id}/objects/{result.object_id}/content")
        assert content.status_code == 200, content.text
        assert "sha256:" + sha256(content.content).hexdigest() == result.checksum
        port = definition["outputs"][result.output_name]["nodeOutput"]["port"]
        tables[port] = parquet.read_table(io.BytesIO(content.content))
    data_schema = pa.schema([
        ("station_id", pa.string()), ("rank", pa.int64()), ("value", pa.float64()), ("flag", pa.string()),
    ])
    for port, ids, values in (
        ("valid", ["001", "004", "005"], [8, 10, 0]),
        ("invalid", ["002", "003", None], [22, None, 4]),
        ("duplicates", ["001", "001", None], [9, 3, 7]),
    ):
        assert tables[port].schema.equals(data_schema)
        assert tables[port]["station_id"].to_pylist() == ids
        assert tables[port]["value"].to_pylist() == values
    assert [(row["row_number"], row["constraint_id"]) for row in tables["violations"].to_pylist()] == [
        (2, "allowed-flag"), (2, "value-range"), (3, "value-required"), (6, "id-required"),
    ]
    profiles = {row["field_name"]: row for row in tables["profiles"].to_pylist()}
    assert set(profiles) == set(data_schema.names)
    assert all(row["row_count"] == 3 and row["null_count"] == 0 for row in profiles.values())
    assert profiles["value"]["mean"] == pytest.approx(6.0)
    assert profiles["value"]["minimum"] == 0 and profiles["value"]["maximum"] == 10


def _assert_raster_outputs(data, outputs):
    by_type = {}
    for result in outputs:
        version = data.get(f"/data/v3/versions/{result.version_id}").json()
        descriptor = version["descriptor"]["raster"]
        assert descriptor["sourceChecksum"] == result.checksum
        assert descriptor["crs"] == "EPSG:32650"
        assert descriptor["grid"]["transform"] == [500000, 30, 0, 4000000, 0, -30]
        content = data.get(f"/data/v3/versions/{result.version_id}/objects/{result.object_id}/content")
        assert content.status_code == 200, content.text
        with MemoryFile(content.content) as file, file.open() as raster:
            values = raster.read(1, masked=True)
            assert values.mask.tolist() == [[False, True], [False, True]]
            assert descriptor["bands"][0]["maskFlags"] == ["nodata"]
            by_type[raster.dtypes[0]] = (values.compressed().tolist(), descriptor["bands"][0]["nodata"])
    assert by_type == {"float32": ([3.0, 8.0], "nan"), "uint8": ([1, 2], 255)}


def _assert_reference_grid_definition(definition, references):
    by_operator = {node["operator"]["id"]: node for node in definition["nodes"]}
    for operator, source, version, method in (
        ("raster.reproject", "geographic", "1.1.0", "nearest"),
        ("raster.align_grid", "shifted", "1.0.0", "bilinear"),
    ):
        node = by_operator[operator]
        assert node["operator"]["version"] == version
        for port, key in (("raster", source), ("reference", "reference")):
            binding = node["inputs"][port]["workflowInput"]
            assert definition["inputs"][binding]["dataRef"]["versionId"] == references[key]["version_id"]
        parameter = definition["parameters"][node["parameters"]["resampling"]["workflowParameter"]]
        assert (parameter["value"], parameter["source"]["kind"]) == (method, "USER")
    bindings = by_operator["raster.cell_statistics"]["inputs"]["rasters"]["collection"]
    assert {item["nodeOutput"]["nodeId"] for item in bindings} == {
        by_operator[name]["nodeId"] for name in ("raster.reproject", "raster.align_grid")
    }


def _assert_reference_grid_outputs(data, outputs):
    expected = {"float32": ([5, 25, 30, 50, 60], "nan"), "uint8": ([1, 2, 2, 2, 2], 255)}
    observed = set()
    for output in outputs:
        version = data.get(f"/data/v3/versions/{output.version_id}").json()
        descriptor = version["descriptor"]["raster"]
        assert descriptor["sourceChecksum"] == output.checksum
        assert descriptor["crs"] == "EPSG:3857"
        assert descriptor["grid"]["transform"] == [0, 1000, 0, 3000, 0, -1000]
        content = data.get(f"/data/v3/versions/{output.version_id}/objects/{output.object_id}/content")
        assert content.status_code == 200, content.text
        with MemoryFile(content.content) as file, file.open() as raster:
            assert raster.shape == (3, 3)
            values = raster.read(1, masked=True)
            assert values.mask.tolist() == [[False, False, True], [False, True, True], [False, False, True]]
            dtype = raster.dtypes[0]
            pixels, nodata = expected[dtype]
            np.testing.assert_allclose(values.compressed(), pixels, atol=1e-5)
            assert descriptor["bands"][0]["nodata"] == nodata
            assert descriptor["bands"][0]["maskFlags"] == ["nodata"]
            observed.add(dtype)
    assert observed == expected.keys()
