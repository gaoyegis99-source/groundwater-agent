"""Natural-language authoring, confirmation, native execution and Data publication."""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
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
import pyarrow.parquet as parquet
import pytest
from rasterio.io import MemoryFile
from rasterio.transform import from_bounds, from_origin
from rasterio.warp import transform

from kernel.tests.conftest import kernel_settings as kernel_settings  # noqa: F401
from kernel.tests.test_workflow_drafts import _operator, _reply
from platform_data_service.api.app import create_app as data_app
from platform_data_service.operations.worker import Worker as DataWorker
from platform_executor_service.app import create_app as executor_app
from platform_executor_service.composition import ExecutorApplication
from platform_executor_service.contracts import JobRequestV6
from platform_executor_service.database import connect_database as executor_database
from platform_executor_service.worker import Worker as ExecutorWorker
from platform_knowledge_next.models import EvidenceBundleV1
from platform_knowledge_next.wire import model_to_wire
from platform_kernel.api import create_app as kernel_app
from platform_kernel.composition import PlatformApplication
from platform_kernel.settings import KernelSettings
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


def _upload(client, worker, title, fmt, content):
    policy = "FILE_QUERYABLE" if fmt == "parquet" else "FILE_ONLY"
    pid = start(client, [{"name": "input", "title": title, "format": fmt, "policy": policy}])
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


@pytest.fixture
def assembly(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "platform/assembler"))
    monkeypatch.setenv("PLATFORM_GENERATED_ROOT", str(tmp_path / "assembly"))
    from assembler import compile_product

    return compile_product(ROOT, "groundwater/product-manifest.json")


@pytest.fixture(scope="module")
def native_site():
    from scripts.development.dev import _native_runtime_python

    with _native_runtime_python() as (native_python, _):
        yield next(native_python.parent.parent.glob("lib/python*/site-packages"))


@pytest.mark.parametrize("scenario", [
    "table", "raster", "crs", "grid", "nodata", "missing-crs", "reference-grid",
    *sorted(TABLE_QUALITY_SCENARIOS),
])
def test_agent_composes_confirms_executes_and_publishes(
    assembly, kernel_settings, request, native_site, tmp_path, monkeypatch, scenario,
):
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
        if scenario == "table":
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
            original = kernel_settings()
            live = (
                scenario in {"table", "raster", "reference-grid"} | TABLE_QUALITY_SCENARIOS
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
            settings = KernelSettings.from_config(
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

            agent = ScriptedModel([
                *[_operator(op, version="1.1.0" if op == "raster.reproject" else "1.0.0") for op in operators],
                *[
                    ModelStep(output=[function_call("inspect_data_version", {
                        "version_id": ref["version_id"],
                        "progress_intent": {
                            "capability": inspect.progress.capability,
                            "template_key": inspect.progress.template_key,
                            "objective": "核验本轮输入版本",
                        },
                    }, call_id=name)])
                    for name, ref in references.items()
                ],
                _reply(authored, cited=False),
            ])
            router = ScriptedModel([ModelStep(output=[assistant_message("CREATE_WORKFLOW|NONE|NONE")])])
            kernel = PlatformApplication(
                settings=settings, model=None if live else agent, intent_router_model=None if live else router,
                client=httpx.AsyncClient(transport=httpx.MockTransport(route)),
            )
            api = stack.enter_context(TestClient(kernel_app(application=kernel)))
            sid = api.post("/api/v12/sessions", json={"projectId": "p"}).json()["sessionId"]
            message = (
                "请编排可复用工作流，校验通过后提交我确认。" + task
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
                capacity = config["workerRuntimes"]["native"]
                worker = ExecutorWorker("native", capacity["slots"], capacity["memoryBytes"])
                await worker.client.aclose()
                worker.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: _bridge(executor_http, req)))
                try:
                    executor.control.heartbeat({
                        "worker_id": worker.worker_id, "runtime": worker.runtime,
                        "catalog_digest": worker.catalog_digest, "slots": worker.slots,
                        "memory_bytes": worker.memory_bytes,
                    })
                    remaining = set(operators)
                    for index, expected_operator in enumerate(operators[:-1] if invalid_rules else operators):
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
                            raise AssertionError(f"Expected a native lease, job state {state}")
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
            assert len(status.outputs) == (len(TABLE_QUALITY_OUTPUTS) if scenario == "table-quality" else 2)
            publication = executor.control.publication(job_id)
            assert publication["state"] == "PUBLISHED"
            receipt = data.get(f"/data/v3/publications/{publication['publicationId']}").json()
            assert set(receipt["manifest"]["inputVersions"]) == {ref["version_id"] for ref in references.values()}
            assert receipt["manifest"]["provenance"]["workflow_checksum"] == confirmation["actions"][0]["workflowChecksum"]
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
