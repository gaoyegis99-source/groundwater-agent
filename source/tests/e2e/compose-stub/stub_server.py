from __future__ import annotations

import ast
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
from threading import Lock
import time


DIMENSIONS = 1024
EVIDENCE: dict[str, list[dict]] = {}
EVIDENCE_LOCK = Lock()
CHAT_DELAY_SECONDS = 0
STREAM_DELAY_SECONDS = int(os.getenv("PLATFORM_STUB_STREAM_DELAY_MS", "0")) / 1000


# The development candidate contains two immutable generations of the same
# fixed-seed transfer-learning fixture.  Natural-language requests select the
# newest version of every named input and then inspect those exact versions
# before the approval-required model tool is proposed.
YAOZHANBAO_STANDARD_DATASET_PREFIX = "腰站堡标准化迁移输入 "
YAOZHANBAO_SYNTHETIC_DATASET_PREFIX = "六模型迁移输入 "
YAOZHANBAO_INPUT_BINDINGS = {
    "workbook": "workbookVersionId",
    "monthMapping": "monthMappingVersionId",
    "sourceFilter": "sourceFilterVersionId",
    "targetFilter": "targetFilterVersionId",
    "boundary": "boundaryVersionId",
    "imagery": "imageryVersionId",
    "counties": "countiesVersionId",
    "townships": "townshipsVersionId",
    "rivers": "riversVersionId",
    "groundwater_systems": "groundwater_systemsVersionId",
    "railways": "railwaysVersionId",
    "basin_boundary": "basin_boundaryVersionId",
    "source-scaler-rules": "source-scaler-rulesVersionId",
    "target-scaler-rules": "target-scaler-rulesVersionId",
    "source-windows-rules": "source-windows-rulesVersionId",
    "target-windows-rules": "target-windows-rulesVersionId",
    "holdout-windows-rules": "holdout-windows-rulesVersionId",
    "inverse-rules": "inverse-rulesVersionId",
    "imagery-rules": "imagery-rulesVersionId",
}
YAOZHANBAO_DEVELOPMENT_PARAMETERS = {
    "source-split.inputWindow": 3,
    "source-windows.inputWindow": 3,
    "target-split.inputWindow": 3,
    "target-windows.inputWindow": 3,
    "holdout-windows.inputWindow": 3,
    "pretrain.hiddenSize": 8,
    "pretrain.numLayers": 1,
    "pretrain.numHeads": 2,
    "pretrain.dropout": 0,
    "pretrain.epochs": 2,
    "finetune.epochs": 2,
}


def verify_evidence(key: str, expected: dict) -> dict[str, str]:
    """Assert V12 observations received by the model without returning bodies."""
    with EVIDENCE_LOCK:
        items = list(EVIDENCE[key])
    calls: dict[str, str] = {}
    call_arguments: dict[str, dict] = {}
    arguments: dict[str, dict] = {}
    results: list[tuple[str, dict]] = []
    for item in items:
        if item.get("type") == "function_call":
            calls[item["call_id"]] = item["name"]
            value = json.loads(item["arguments"])
            call_arguments[item["call_id"]] = value
            arguments[item["name"]] = value
        for call in item.get("tool_calls", []):
            name = call["function"]["name"]
            calls[call["id"]] = name
            value = json.loads(call["function"]["arguments"])
            call_arguments[call["id"]] = value
            arguments[name] = value
        if item.get("type") == "function_call_output":
            name = calls[item["call_id"]]
            value = _parse_tool_output(
                item["output"], rejection=name.startswith("run_")
            )
            results.append((name, value))
        elif item.get("role") == "tool":
            name = calls[item["tool_call_id"]]
            value = _parse_tool_output(
                item["content"], rejection=name.startswith("run_")
            )
            results.append((name, value))

    if expected.get("kind") == "demo":
        assert set(expected["observations"]) <= set(arguments), (
            "DEMO_OBSERVATION_MISSING"
        )
        if "read_evidence" in expected["observations"]:
            bundle = next(
                result["data"] for name, result in results if name == "read_evidence"
            )
            value = (
                bundle["items"][0] if isinstance(bundle.get("items"), list) else bundle
            )
            assert (
                value["status"] == "OK"
                and value["content"] == expected["knowledge_content"]
            )
            assert _business_arguments(arguments["read_evidence"]) == {
                "refs": [
                    {
                        "version_id": value["versionId"],
                        "unit_id": value["unitId"],
                        "checksum": value["checksum"],
                    }
                ]
            }
        if expected.get("tool"):
            replay = next(
                result for name, result in results if name == expected["tool"]
            )
            assert (
                replay["status"] == "COMPLETE"
                and replay["outputs"] == expected["outputs"]
            )
            assert replay["workflow_run_id"] == expected["workflow_run_id"]
            assert arguments[expected["tool"]] == expected["arguments"]
        return {"observed_inputs": "PASS", "replay_references": "PASS"}

    if expected.get("kind") == "execution":
        replay = next(
            (
                result
                for name, result in results
                if name == expected["tool"]
                and result["workflow_run_id"] == expected["workflow_run_id"]
            ),
            None,
        )
        assert replay is not None, "WRONG_REPLAY_WORKFLOW"
        assert replay["status"] == "COMPLETE", "EXECUTION_REPLAY_FAILED"
        assert replay["outputs"] == expected["outputs"], (
            "REPLAY_DATA_VERSION_REFERENCE_MISMATCH"
        )
        return {
            "execution_replay_identity": "PASS",
            "execution_replay_references": "PASS",
        }

    searches = [
        result["data"] for name, result in results if name == "discover_datasets"
    ]
    assert searches, "DATA_SEARCH_NOT_RECEIVED"
    seen = {item["currentVersion"] for search in searches for item in search["items"]}
    allowed = set(expected["allowed_ids"])
    assert seen <= allowed, "DATA_SEARCH_PROJECT_ISOLATION_FAILED"
    if expected["empty"]:
        assert not seen, "EXPECTED_EMPTY_DATA_SEARCH"
    elif expected["ambiguous"]:
        assert seen == allowed, "AMBIGUOUS_DATA_CANDIDATES_MISSING"
    else:
        assert expected["primary_id"] in seen, "PRIMARY_DATA_NOT_IN_SEARCH"
    if expected["inspect"]:
        assert _business_arguments(arguments["inspect_data_version"]) == {
            "version_id": expected["primary_id"]
        }, "EXACT_DATA_VERSION_NOT_INSPECTED"
        inspected = [
            result["data"] for name, result in results if name == "inspect_data_version"
        ]
        assert inspected[-1]["versionId"] == expected["primary_id"]
    return {
        "search_project_isolation": "PASS",
        "search_results": "PASS",
        "exact_data_version_inspected"
        if expected["inspect"]
        else "no_inspection_required": "PASS",
    }


def _business_arguments(value: dict) -> dict:
    """Remove Agent-control metadata before comparing service business inputs."""

    intent = value.get("progress_intent")
    if intent is not None:
        assert isinstance(intent, dict), "PROGRESS_INTENT_NOT_STRUCTURED"
        assert set(intent) == {
            "capability",
            "objective",
            "template_key",
        }, "PROGRESS_INTENT_FIELDS_INVALID"
        assert all(
            isinstance(intent[field], str) and intent[field]
            for field in ("capability", "objective", "template_key")
        ), "PROGRESS_INTENT_VALUE_INVALID"
    return {key: item for key, item in value.items() if key != "progress_intent"}


def _yaozhanbao_transfer_action(
    results: dict,
    result_events: list[tuple[str, dict]],
    finish,
) -> tuple[str | None, dict]:
    tool = "run_transfer_learning_analysis"
    search_pages = [
        response["data"]
        for name, response in result_events
        if name == "discover_datasets"
    ]
    used_standardized = len(
        {
            item.get("title")
            for page in search_pages
            for item in page["items"]
            if item.get("title", "").startswith(YAOZHANBAO_STANDARD_DATASET_PREFIX)
            and "yaozhanbao-transfer-case" in item.get("tags", [])
        }
    ) == len(YAOZHANBAO_INPUT_BINDINGS)
    if tool in results:
        result = results[tool]
        if result["status"] == "REJECTED":
            return finish("已取消腰站堡迁移学习案例，未创建执行任务。", [])
        output_count = len(result.get("outputs", []))
        _, final = finish(
            (
                "腰站堡迁移学习标准化资料案例已完成。"
                if result["status"] == "COMPLETE"
                else "腰站堡迁移学习案例执行未成功。"
            ),
            [],
            [
                "**执行范围**：已完成源域预训练、目标域微调、留出回测和空间成果整理。",
                f"**执行结果**：本次工作流发布了 {output_count} 项可核验成果，可在右侧任务工作台的“成果”页逐项查看或下载。",
                (
                    "**使用限制**：输入来自历史归档和标准化处理，仅用于开发调试，"
                    "不代表腰站堡当前真实水文状况。"
                    if used_standardized
                    else "**使用限制**：本次使用固定模拟输入，仅用于开发调试，"
                    "不代表腰站堡的真实水文结论。"
                ),
            ],
        )
        if result.get("workflow_run_id"):
            final["workflow_runs"] = [result["workflow_run_id"]]
        return None, final

    if not search_pages:
        return "discover_datasets", {
            "query": "迁移输入",
            "tags": [],
            "after": None,
            "limit": 50,
        }

    standardized: dict[str, dict] = {}
    synthetic: dict[str, dict] = {}
    standardized_titles = {
        YAOZHANBAO_STANDARD_DATASET_PREFIX + logical_name: logical_name
        for logical_name in YAOZHANBAO_INPUT_BINDINGS
    }
    synthetic_titles = {
        YAOZHANBAO_SYNTHETIC_DATASET_PREFIX + logical_name: logical_name
        for logical_name in YAOZHANBAO_INPUT_BINDINGS
    }
    for page in search_pages:
        for item in page["items"]:
            logical_name = standardized_titles.get(item.get("title"))
            target = standardized
            required_tag = "yaozhanbao-transfer-case"
            if logical_name is None:
                logical_name = synthetic_titles.get(item.get("title"))
                target = synthetic
                required_tag = "SYNTHETIC_TEST_ONLY"
            if logical_name is not None and required_tag in item.get("tags", []):
                current = target.get(logical_name)
                if current is None or item.get("createdAt", "") > current.get(
                    "createdAt", ""
                ):
                    target[logical_name] = item

    next_cursor = search_pages[-1].get("nextCursor")
    standardized_missing = [
        name for name in YAOZHANBAO_INPUT_BINDINGS if name not in standardized
    ]
    # A complete synthetic case on an early page must not suppress the stated
    # preference for a complete standardized case on a later page.
    if standardized_missing and next_cursor is not None:
        return "discover_datasets", {
            "query": "迁移输入",
            "tags": [],
            "after": next_cursor,
            "limit": 50,
        }
    candidates = standardized if not standardized_missing else synthetic
    missing_names = [
        name for name in YAOZHANBAO_INPUT_BINDINGS if name not in candidates
    ]
    if missing_names:
        return finish(
            "腰站堡迁移学习案例的输入尚不完整。",
            ["缺少案例输入：" + "、".join(missing_names)],
        )

    selected_versions = {
        YAOZHANBAO_INPUT_BINDINGS[name]: candidates[name]["currentVersion"]
        for name in YAOZHANBAO_INPUT_BINDINGS
    }
    inspected = {
        response["data"].get("versionId")
        for name, response in result_events
        if name == "inspect_data_version"
        and response.get("data", {}).get("status") == "OK"
    }
    missing_versions = [
        version_id
        for version_id in selected_versions.values()
        if version_id not in inspected
    ]
    if missing_versions:
        calls = [
            ("inspect_data_version", {"version_id": version_id})
            for version_id in missing_versions
        ]
        return ("__batch__", calls) if len(calls) > 1 else calls[0]

    if "record_plan" not in results:
        standardized_case = candidates is standardized
        return "record_plan", {
            "goal": (
                "使用标准化资料运行腰站堡迁移学习开发案例"
                if standardized_case
                else "使用固定模拟输入运行腰站堡迁移学习开发案例"
            ),
            "steps": [
                {
                    "step_id": "transfer-learning",
                    "tool": tool,
                    "purpose": "完成迁移学习、回测和空间成果发布",
                }
            ],
            "reason": (
                "已识别腰站堡案例配置并核验 19 项精确 Data 版本；"
                + (
                    "优先采用历史归档的标准化案例输入，等待用户确认执行"
                    if standardized_case
                    else "标准化输入不完整，回退固定 seed 20260928 模拟输入，等待用户确认执行"
                )
            ),
        }

    return tool, {
        **selected_versions,
        **YAOZHANBAO_DEVELOPMENT_PARAMETERS,
        "extent.paddingMeters": 1000 if candidates is standardized else 10,
        "prediction-points.sourceCrs": (
            "EPSG:4490" if candidates is standardized else "EPSG:32650"
        ),
        "map-points.sourceCrs": (
            "EPSG:4490" if candidates is standardized else "EPSG:32650"
        ),
        "map-month.targetTime": (
            "2024-12-01" if candidates is standardized else "2021-04-01"
        ),
    }


def next_action(items: list[dict], tool_names: set[str]) -> tuple[str | None, dict]:
    """Deterministic V12 model policy; identities come only from tool results."""
    start = max(i for i, item in enumerate(items) if item.get("role") == "user")
    current = items[start:]
    content = current[0]["content"]
    plain_text = (
        content
        if isinstance(content, str)
        else "".join(part.get("text", "") for part in content)
    )
    marker = re.search(r"\[Evaluation case: ([^\]]+)\]", plain_text)
    evidence_key = marker[1] if marker else plain_text
    if marker:
        with EVIDENCE_LOCK:
            EVIDENCE[evidence_key] = current

    calls: dict[str, str] = {}
    results: dict[str, dict] = {}
    result_events: list[tuple[str, dict]] = []
    for item in current:
        if item.get("type") == "function_call":
            calls[item["call_id"]] = item["name"]
        for call in item.get("tool_calls", []):
            calls[call["id"]] = call["function"]["name"]
        if item.get("type") == "function_call_output":
            name = calls[item["call_id"]]
            value = _parse_tool_output(
                item["output"], rejection=name.startswith("run_")
            )
        elif item.get("role") == "tool":
            name = calls[item["tool_call_id"]]
            value = _parse_tool_output(
                item["content"], rejection=name.startswith("run_")
            )
        else:
            continue
        results[name] = value
        result_events.append((name, value))

    def finish(
        message: str,
        unresolved: list[str],
        details: list[str] | None = None,
    ) -> tuple[None, dict]:
        return None, {
            "message": message,
            "details": details or [],
            "workflow_runs": [],
            "unresolved_items": unresolved,
        }

    execution = re.search(r"\[Model execution\]\s*(\{.*\})", plain_text, re.DOTALL)
    if execution is None and {"retrieve_evidence", "read_evidence"} & (tool_names | set(results)) and (
        "知识" in plain_text or "evidence" in plain_text.lower()
    ):
        with EVIDENCE_LOCK:
            EVIDENCE[evidence_key] = current
        time_aggregation = "时间聚合" in plain_text
        yaozhanbao_case = "腰站堡" in plain_text and any(
            word in plain_text for word in ("迁移学习", "工作流", "案例", "Y01")
        )
        if "retrieve_evidence" not in results:
            return "retrieve_evidence", {
                "query": (
                    "地下水时间聚合方法"
                    if time_aggregation
                    else "腰站堡迁移学习案例入口与执行说明"
                    if yaozhanbao_case
                    else plain_text
                ),
                **({"mode": "lexical"} if time_aggregation or yaozhanbao_case else {}),
            }
        retrieved = results["retrieve_evidence"]["data"]
        matches = retrieved if isinstance(retrieved, list) else retrieved["items"]
        if not matches:
            return finish("未找到相关知识。", ["未找到可引用证据"])
        if "read_evidence" not in results:
            selected = matches[:1]
            if time_aggregation:
                selected = [
                    item
                    for item in matches
                    if item.get("knowledgeId") == "gw-aggregation-choice"
                ][:2] or selected
            elif yaozhanbao_case:
                selected = [
                    item
                    for item in matches
                    if item.get("knowledgeId") == "gw-yaozhanbao-transfer-case-routing"
                ][:2] or selected
            return "read_evidence", {
                "refs": [
                    {
                        "version_id": match["versionId"],
                        "unit_id": match["unitId"],
                        "checksum": match["checksum"],
                    }
                    for match in selected
                ]
            }
        if time_aggregation:
            return finish(
                "已根据本轮完整读取的证据说明：地下水时间聚合需要同时选择时间频率和统计函数，应根据采样密度、异常程度与分析目的确定，没有适用于所有数据的唯一组合。",
                [],
                [
                    "**频率选择**：日频保留短期波动，但对噪声和缺测更敏感；月频适合观察季节变化和中期趋势；季度或年度适合长期概览，但可能隐藏峰谷与转折。稀疏人工观测不宜强行生成日序列。",
                    "**聚合函数**：`mean` 描述周期平均状态，但容易受尖峰和采样密度影响；`median` 对少量异常更稳健；`min`/`max` 用于关注极端状态。使用极值前要先确认指标方向，例如埋深越大通常表示水位越低。",
                    "**质量与限制**：比较多个井或时期时应使用相同规则，并保留每个周期的有效样本数。聚合不能修复连续缺测、单位混用、井基准变化或错误坐标，也不能单独证明变化原因。",
                ],
            )
        if yaozhanbao_case:
            return finish(
                "已命中腰站堡迁移学习案例知识：自然语言请求可定位到现有迁移学习工作流；执行前必须核验 19 项精确 Data 版本并由用户确认。",
                [],
                [
                    "**案例范围**：32 口源域井、14 口腰站堡目标域井，覆盖 2018-01 至 2024-12 共 84 个月。",
                    "**执行链路**：源域预训练、目标域微调、留出回测和空间成果整理；执行期间返回状态，完成后发布成果并生成解释。",
                    "**使用限制**：历史归档和标准化资料用于开发调试，不能据此宣称当前真实地下水状况。",
                ],
            )
        return finish("已根据本轮完整读取的证据说明。", [])

    if (
        execution is None
        and "腰站堡" in plain_text
        and any(word in plain_text for word in ("迁移学习", "工作流", "案例"))
        and "run_transfer_learning_analysis" in (tool_names | set(results))
    ):
        return _yaozhanbao_transfer_action(results, result_events, finish)

    if execution:
        requested = json.loads(execution[1])
        tool = requested["tool"]
        if tool in results:
            result = results[tool]
            if result["status"] == "REJECTED":
                return finish("已拒绝，未执行。", [])
            assert result["status"] == requested.get("expectedStatus", "COMPLETE"), (
                result
            )
            if result["status"] == "FAILED":
                assert (
                    result["error"] == "OUTPUT_FIELD_CONFLICT"
                    and result["outputs"] == []
                )
                _, final = finish("模型执行失败，没有产生结果。", [])
                final["workflow_runs"] = [result["workflow_run_id"]]
                return None, final
            assert {reference["kind"] for reference in result["outputs"]} <= {
                "data_version"
            }
            _, final = finish("模型执行完成，已接收结果引用。", [])
            final["workflow_runs"] = [result["workflow_run_id"]]
            return None, final
        assert tool in tool_names, "MODEL_TOOL_NOT_EXPOSED"
        if "inspect_data_version" in tool_names:
            requested_versions = list(
                dict.fromkeys(
                    value
                    for key, value in requested["arguments"].items()
                    if isinstance(value, str) and key.lower().endswith("versionid")
                )
            )
            inspected_versions = {
                response["data"]["versionId"]
                for name, response in result_events
                if name == "inspect_data_version"
            }
            missing_versions = [
                value for value in requested_versions if value not in inspected_versions
            ]
            if missing_versions:
                calls_to_make = [
                    ("inspect_data_version", {"version_id": value})
                    for value in missing_versions
                ]
                return (
                    ("__batch__", calls_to_make)
                    if len(calls_to_make) > 1
                    else calls_to_make[0]
                )
            queryable_versions = {
                response["data"]["versionId"]
                for name, response in result_events
                if name == "inspect_data_version"
                and any(
                    item["format"] in {"csv", "parquet"}
                    for item in response["data"].get("objects", [])
                )
            }
            inspected_tables = {
                response["data"]["versionId"]
                for name, response in result_events
                if name == "inspect_data_table"
            }
            missing_tables = [
                value
                for value in requested_versions
                if value in queryable_versions and value not in inspected_tables
            ]
            if missing_tables:
                calls_to_make = [
                    ("inspect_data_table", {"version_id": value})
                    for value in missing_tables
                ]
                return (
                    ("__batch__", calls_to_make)
                    if len(calls_to_make) > 1
                    else calls_to_make[0]
                )
        if "record_plan" not in results:
            return "record_plan", {
                "goal": "执行已指定的地下水模型",
                "steps": [
                    {"step_id": "execute", "tool": tool, "purpose": "计算并返回结果"}
                ],
                "reason": "参数和输入已明确，等待用户确认",
            }
        return tool, requested["arguments"]

    terminal = next(
        (
            (name, value)
            for name, value in reversed(result_events)
            if name.startswith("run_")
        ),
        None,
    )
    if terminal is not None:
        _, result = terminal
        if result["status"] == "REJECTED":
            return finish("已拒绝执行，没有创建计算任务。", [])
        if result["status"] == "FAILED":
            return finish("执行失败。", [str(result.get("error", "WORKER_FAILED"))])
        _, final = finish("模型执行完成，已接收结果引用。", [])
        if result.get("workflow_run_id"):
            final["workflow_runs"] = [result["workflow_run_id"]]
        return None, final

    if "discover_datasets" in tool_names:
        target = re.search(r"北区监测样本-[a-z_]+-[0-9]+", plain_text)
        target_name = target[0] if target else "北区监测样本"
        if "discover_datasets" not in results:
            return "discover_datasets", {
                "query": "不存在的监测样本" if "不存在" in plain_text else target_name,
                "tags": [],
                "after": None,
                "limit": 20,
            }
        candidates = results["discover_datasets"]["data"]["items"]
        exact = [item for item in candidates if item["title"] == target_name]
        if exact:
            candidates = exact
        if len(candidates) > 1 and "正式采样" in plain_text:
            candidates = [
                item for item in candidates if item.get("description") == "正式采样"
            ]
        if not candidates:
            return finish("未找到匹配的数据，请提供其他名称。", ["未找到数据"])
        if len(candidates) != 1:
            choices = "；".join(
                f"{item['title']}：{item.get('description', '')}" for item in candidates
            )
            return finish(f"找到多个候选，请选择：{choices}", ["请选择数据"])
        version_id = candidates[0]["currentVersion"]
        if "inspect_data_version" not in results:
            return "inspect_data_version", {"version_id": version_id}
        inspected = results["inspect_data_version"]["data"]
        assert inspected["status"] == "OK" and inspected["versionId"] == version_id
        if "inspect_data_table" not in results:
            return "inspect_data_table", {"version_id": version_id}
        table = results["inspect_data_table"]["data"]
        assert table["status"] == "OK" and table["versionId"] == version_id
        if "record_plan" not in results:
            return "record_plan", {
                "goal": "对选定数据版本的 number 列进行描述统计",
                "steps": [
                    {
                        "step_id": "summary",
                        "tool": "run_summary",
                        "purpose": "计算描述统计",
                    }
                ],
                "reason": "已搜索并检查精确数据版本，等待用户确认执行",
            }
        return "run_summary", {
            "versionId": version_id,
            "column": "number",
            "axis": "columns",
        }

    return finish("已完成当前请求。", [])


def _parse_tool_output(value: str, *, rejection: bool = False) -> dict:
    # SDK rejection_message is plain text, not an executed tool's JSON result.
    # An operator may enter any non-empty reason; executed tool results remain
    # structured objects and are validated below.
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            if rejection and value.strip():
                return {"status": "REJECTED"}
            raise
    assert isinstance(parsed, dict)
    return parsed


def _submit_reply(value, items):
    start = max(i for i, item in enumerate(items) if item.get("role") == "user")
    calls: dict[str, str] = {}
    reads: dict[tuple[str, str], dict] = {}
    execution_terminal = False
    for item in items[start:]:
        if item.get("type") == "function_call":
            calls[item["call_id"]] = item["name"]
        for call in item.get("tool_calls", []):
            calls[call["id"]] = call["function"]["name"]
        if item.get("type") == "function_call_output":
            name, content = calls[item["call_id"]], item["output"]
        elif item.get("role") == "tool":
            name, content = calls[item["tool_call_id"]], item["content"]
        else:
            continue
        result = _parse_tool_output(content, rejection=name.startswith("run_"))
        execution_terminal = execution_terminal or result.get("status") in {
            "REJECTED",
            "FAILED",
        }
        if name != "read_evidence":
            continue
        data = result.get("data", {})
        evidence_items = data.get("items", [data]) if isinstance(data, dict) else []
        for read in evidence_items:
            if not isinstance(read, dict) or not all(
                isinstance(read.get(field), str) and read[field]
                for field in ("versionId", "unitId", "checksum")
            ):
                continue
            key = (read["versionId"], read["unitId"])
            if read.get("status") != "OK":
                reads.pop(key, None)
            else:
                reads[key] = {
                    "version_id": read["versionId"],
                    "unit_id": read["unitId"],
                    "checksum": read["checksum"],
                }
    citations = list(reads.values())
    include_execution_status = bool(value["workflow_runs"] or execution_terminal)
    return {
        "conclusion": (
            None
            if include_execution_status or not value["message"]
            else {"text": value["message"], "citations": citations}
        ),
        "details": [
            {"text": detail, "citations": citations}
            for detail in value.get("details", [])
        ],
        "unresolved_items": [
            {"text": item, "citations": citations} for item in value["unresolved_items"]
        ],
        "referenced_workflow_runs": value["workflow_runs"],
        "include_execution_status": include_execution_status,
        "interaction_options": value.get("interaction_options", []),
        "workflow_draft": None,
    }


def _intent_router_action(body, *, chat):
    if body.get("model") != "test-router-model":
        return None
    assert body.get("tools", []) == [], "INTENT_ROUTER_TOOLS_FORBIDDEN"
    items = body["messages"] if chat else body["input"]
    user = next(item for item in reversed(items) if item.get("role") == "user")
    content = user.get("content", "")
    if isinstance(content, list):
        text = "".join(
            str(part.get("text", part.get("content", "")))
            for part in content
            if isinstance(part, dict)
        )
    else:
        text = str(content)
    if any(
        term in text
        for term in (
            "工作流草稿",
            "工作流",
            "编排",
            "生成草稿",
            "只生成草稿",
            "可复用流程",
            "导出流程",
        )
    ):
        decision = "WORKFLOW_DRAFT"
    elif any(term in text for term in ("不要执行", "无需执行", "先别执行")):
        # Explicit execution negation must win over the bare action verb below.
        # This keeps consultation prompts such as "先不要执行" read-only while
        # still allowing an explicitly requested workflow draft above.
        decision = "DIALOGUE"
        dialogue_policy = "READ_ONLY"
    elif any(
        term in text
        for term in (
            "应该怎么分析",
            "推荐方案",
            "推荐一种",
            "哪种分析",
            "什么方法",
            "比较分析方法",
        )
    ):
        decision = "DIALOGUE"
        dialogue_policy = "READ_ONLY"
    elif any(term in text for term in ("帮我处理一下", "弄一下这个")):
        decision = "NEEDS_CLARIFICATION"
        dialogue_policy = None
    elif any(
        term in text
        for term in (
            "执行",
            "运行",
            "计算",
            "生成",
            "绘制",
            "开始分析",
            "描述统计",
            "Model execution",
        )
    ):
        decision = "DIALOGUE"
        dialogue_policy = "EXECUTION_PROPOSAL"
    else:
        decision = "DIALOGUE"
        dialogue_policy = "READ_ONLY"
    if decision == "WORKFLOW_DRAFT":
        dialogue_policy = None
    semantic_intent = (
        "CREATE_WORKFLOW"
        if decision == "WORKFLOW_DRAFT"
        else "EXECUTE_ON_DATA"
        if dialogue_policy == "EXECUTION_PROPOSAL"
        else "UNKNOWN"
        if decision == "NEEDS_CLARIFICATION"
        else "CONSULT"
    )
    clarification = (
        "REQUEST_KIND_AMBIGUOUS" if decision == "NEEDS_CLARIFICATION" else "NONE"
    )
    # The V12 DeepSeek Router wire is deliberately smaller than
    # IntentDecisionV4. Kernel owns the mapping and authorization policy.
    return None, f"{semantic_intent}|NONE|{clarification}"


PROGRESS_OBJECTIVES = {
    "get_data_inventory": "了解当前项目的数据范围",
    "discover_datasets": "检索与当前请求匹配的数据版本",
    "inspect_data_version": "核验指定输入版本的对象、语义和 lineage",
    "inspect_data_table": "检查指定输入版本的字段、类型和样例",
    "retrieve_evidence": "检索与当前问题相关的知识证据",
    "read_evidence": "读取并核验已选择的知识证据",
    "lookup_knowledge": "核验指定知识条目的完整内容",
    "list_operators": "查找满足当前目标的分析算子",
    "get_operator": "核验所选分析算子的输入、参数和输出",
    "list_execution_facts": "读取当前会话的历史执行记录",
    "get_execution_fact": "核验指定工作流的执行事实",
}


def _progress_objective(tool_name: str, arguments: dict) -> str:
    """Author a concrete intent phrase as the deterministic test Agent."""

    objective = PROGRESS_OBJECTIVES.get(tool_name)
    if objective is not None:
        return objective
    return "调用当前能力推进请求处理"


def _add_progress_intent(
    name: str | None,
    value: dict | list,
    tools: list[dict],
    *,
    chat: bool,
) -> tuple[str | None, dict | list]:
    """Populate provider-authored progress from each advertised tool contract."""

    if name is None:
        return name, value
    if name == "__batch__":
        enriched = []
        for tool_name, arguments in value:
            enriched_name, enriched_arguments = _add_progress_intent(
                tool_name,
                arguments,
                tools,
                chat=chat,
            )
            assert enriched_name == tool_name
            enriched.append((tool_name, enriched_arguments))
        return name, enriched
    definitions = {
        tool["function"]["name"] if chat else tool["name"]: (
            tool["function"].get("parameters", {})
            if chat
            else tool.get("parameters", {})
        )
        for tool in tools
    }
    schema = definitions.get(name, {})
    progress = schema.get("properties", {}).get("progress_intent")
    required = schema.get("required", [])
    if (
        not isinstance(value, dict)
        or "progress_intent" not in required
        or not isinstance(progress, dict)
        or "progress_intent" in value
    ):
        return name, value
    properties = progress.get("properties", {})
    capability = properties.get("capability", {}).get("const")
    template_key = properties.get("template_key", {}).get("const")
    assert isinstance(capability, str) and isinstance(template_key, str), (
        "PROGRESS_INTENT_CONTRACT_INVALID"
    )
    return name, {
        **value,
        "progress_intent": {
            "capability": capability,
            "objective": _progress_objective(name, value),
            "template_key": template_key,
        },
    }


def provider_action(body, *, chat):
    """Deterministic provider policy for the current V12 tool surface."""
    routed = _intent_router_action(body, chat=chat)
    if routed is not None:
        return routed
    items = body["messages"] if chat else body["input"]
    instructions = items[0]["content"] if chat else body.get("instructions", "")
    tools = body.get("tools", [])
    names = {tool["function"]["name"] if chat else tool["name"] for tool in tools}
    policy_items = (
        items if chat else [{"role": "system", "content": instructions}, *items]
    )
    user_text = next(
        (
            item.get("content", "")
            for item in reversed(items)
            if item.get("role") == "user"
        ),
        "",
    )
    if (
        isinstance(user_text, str)
        and user_text.strip() == "你好"
        and "submit_reply" in names
    ):
        return "submit_reply", _submit_reply(
            {
                "message": "你好，我可以帮你整理资料、比较方法或准备分析方案。",
                "workflow_runs": [],
                "unresolved_items": [],
            },
            items,
        )
    if isinstance(user_text, str) and (
        "资料评审工作说明" in user_text
        or "保留原有交付和评审条件" in user_text
        or user_text.startswith("确认完成：是否可用")
    ):
        recent = items[
            next(
                i for i in reversed(range(len(items))) if items[i].get("role") == "user"
            )
            + 1 :
        ]
        if any(item.get("name") == "update_task" for item in recent):
            return "submit_reply", _submit_reply(
                {
                    "message": "工作说明：先列资料缺口，再评审资料是否可用。",
                    "workflow_runs": [],
                    "unresolved_items": [],
                },
                items,
            )
        if "资料评审工作说明" in user_text:
            return "update_task", {
                "expected_revision": 0,
                "objective_quote": user_text,
                "criteria": [
                    {
                        "id": "reply",
                        "text": "工作说明",
                        "source_quote": "工作说明",
                        "kind": "REPLY",
                    },
                    {
                        "id": "review",
                        "text": "是否可用",
                        "source_quote": "是否可用",
                        "kind": "USER_REVIEW",
                    },
                ],
                "evidence": [{"criterion_id": "reply"}],
            }
        if "保留原有交付和评审条件" in user_text:
            return "update_task", {
                "expected_revision": 1,
                "constraints": [
                    {
                        "id": "public",
                        "text": "仅使用公开资料",
                        "source_quote": "仅使用公开资料",
                    }
                ],
            }
        return "update_task", {
            "expected_revision": 2,
            "evidence": [
                {"criterion_id": "review", "review_quote": "确认完成：是否可用"},
                {
                    "criterion_id": "constraint:public",
                    "review_quote": "确认完成：仅使用公开资料",
                },
            ],
        }
    if (
        "This request is clarification-only." in instructions
        and "submit_reply" in names
    ):
        return "submit_reply", _submit_reply(
            {
                "message": "",
                "workflow_runs": [],
                "unresolved_items": [
                    "你希望我先解释资料、比较方法，还是整理可复用流程？"
                ],
                "interaction_options": [
                    {
                        "label": "解释资料",
                        "description": "说明现有资料内容与限制",
                        "message": "请先解释当前资料的内容与限制。",
                    },
                    {
                        "label": "比较方法",
                        "description": "比较可用方法与适用条件",
                        "message": "请比较当前资料可用的分析方法。",
                    },
                    {
                        "label": "整理流程",
                        "description": "整理成可复用的流程草稿",
                        "message": "请把当前需求整理成可复用流程。",
                    },
                ],
            },
            items,
        )
    # Keep routing on the latest user request during contract-repair turns.  The
    # final item is then a function result, not the user's original intent.
    current_text = json.dumps(user_text, ensure_ascii=False)
    if (
        "请说明这个附件的数据结构" in current_text
        and "不要执行计算" in current_text
        and "submit_reply" in names
    ):
        return "submit_reply", _submit_reply(
            {
                "message": "该附件是 CSV 表格资料，包含 well 和 value 字段；本轮仅说明数据结构，不执行计算。",
                "workflow_runs": [],
                "unresolved_items": [],
                "interaction_options": [],
            },
            items,
        )
    if (
        any(
            term in current_text
            for term in (
                "应该怎么分析",
                "比较当前资料可用的趋势分析方法",
            )
        )
        and "submit_reply" in names
    ):
        return "submit_reply", _submit_reply(
            {
                "message": "可以先比较趋势分析与空间分布分析；前者关注时间变化，后者依赖可靠坐标。这里只提供方法比较，不会启动计算。",
                "workflow_runs": [],
                "unresolved_items": [],
                "interaction_options": [],
            },
            items,
        )
    name, value = next_action(policy_items, names)
    if name is None and "submit_reply" in names:
        return "submit_reply", _submit_reply(value, items)
    return _add_progress_intent(name, value, tools, chat=chat)


def _log_provider_decision(body: dict, *, chat: bool, name: str | None) -> None:
    """Emit a prompt-free decision trace for failed Compose acceptance runs."""

    tools = body.get("tools", [])
    offered = sorted(
        tool["function"]["name"] if chat else tool["name"] for tool in tools
    )
    items = body.get("messages", []) if chat else body.get("input", [])
    print(
        json.dumps(
            {
                "event": "provider_decision",
                "model": body.get("model"),
                "protocol": "chat_completions" if chat else "responses",
                "offered_tools": offered,
                "tool_choice": body.get("tool_choice"),
                "selected_tool": name,
                "input_types": [
                    item.get("type", item.get("role", "unknown"))
                    for item in items
                    if isinstance(item, dict)
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(200, {"status": "UP"})
        elif self.path in {"/models", "/v1/models"}:
            self._json(
                200,
                {
                    "object": "list",
                    "data": [
                        {"id": "test-model", "object": "model"},
                        {"id": "test-router-model", "object": "model"},
                    ],
                },
            )
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", "0"))))
        if self.path == "/assertions":
            try:
                assertions = verify_evidence(body["key"], body["expected"])
            except (AssertionError, KeyError) as error:
                self._json(422, {"status": "FAILED", "assertion": str(error)})
            else:
                self._json(200, assertions)
        elif self.path == "/faults":
            global CHAT_DELAY_SECONDS
            assert body == {"chat_delay_seconds": 121}
            CHAT_DELAY_SECONDS = body["chat_delay_seconds"]
            self._json(200, {"status": "ARMED"})
        elif self.path == "/v1/embeddings":
            self._embeddings(body)
        elif self.path == "/v1/responses":
            self._responses(body)
        elif self.path == "/v1/chat/completions":
            self._chat(body)
        else:
            self._json(404, {"error": "not found"})

    def _embeddings(self, body: dict) -> None:
        inputs = body["input"]
        vector = [1.0] + [0.0] * (DIMENSIONS - 1)
        self._json(
            200,
            {
                "object": "list",
                "model": body.get("model", "test-embedding"),
                "data": [
                    {"object": "embedding", "index": i, "embedding": vector}
                    for i, _ in enumerate(inputs)
                ],
                "usage": {"prompt_tokens": len(inputs), "total_tokens": len(inputs)},
            },
        )

    def _chat(self, body: dict) -> None:
        global CHAT_DELAY_SECONDS
        with EVIDENCE_LOCK:
            delay, CHAT_DELAY_SECONDS = CHAT_DELAY_SECONDS, 0
        if delay:
            time.sleep(delay)
            return
        name, value = provider_action(body, chat=True)
        _log_provider_decision(body, chat=True, name=name)
        if name is None:
            message = {
                "role": "assistant",
                "content": (
                    value
                    if isinstance(value, str)
                    else json.dumps(value, ensure_ascii=False)
                ),
            }
        else:
            actions = value if name == "__batch__" else [(name, value)]
            sequence = len(body["messages"])
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call-{tool}-{sequence}-{index}",
                        "type": "function",
                        "function": {"name": tool, "arguments": json.dumps(arguments)},
                    }
                    for index, (tool, arguments) in enumerate(actions)
                ],
            }
        self._json(
            200,
            {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": body.get("model", "test-model"),
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": "stop" if name is None else "tool_calls",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )

    def _responses(self, body: dict) -> None:
        name, value = provider_action(body, chat=False)
        _log_provider_decision(body, chat=False, name=name)
        if name is None:
            output = [
                {
                    "id": "msg-final",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": (
                                value
                                if isinstance(value, str)
                                else json.dumps(value, ensure_ascii=False)
                            ),
                            "annotations": [],
                        }
                    ],
                }
            ]
        else:
            actions = value if name == "__batch__" else [(name, value)]
            sequence = len(body["input"])
            output = [
                {
                    "id": f"fc-{tool}-{sequence}-{index}",
                    "type": "function_call",
                    "status": "completed",
                    "call_id": f"call-{tool}-{sequence}-{index}",
                    "name": tool,
                    "arguments": json.dumps(arguments),
                }
                for index, (tool, arguments) in enumerate(actions)
            ]
        response = {
            "id": "resp-test",
            "object": "response",
            "created_at": time.time(),
            "status": "completed",
            "model": body.get("model", "test-model"),
            "output": output,
            "parallel_tool_calls": False,
            "tool_choice": "auto",
            "tools": [],
            "usage": {
                "input_tokens": 1,
                "input_tokens_details": {
                    "cached_tokens": 0,
                    "cache_write_tokens": 0,
                },
                "output_tokens": 1,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 2,
            },
        }
        if not body.get("stream"):
            self._json(200, response)
            return
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        sequence = 0

        def emit(kind, **values):
            nonlocal sequence
            sequence += 1
            payload = {"type": kind, "sequence_number": sequence, **values}
            self.wfile.write(
                (
                    "event: "
                    + kind
                    + "\ndata: "
                    + json.dumps(payload, ensure_ascii=False)
                    + "\n\n"
                ).encode()
            )
            self.wfile.flush()
            if STREAM_DELAY_SECONDS:
                time.sleep(STREAM_DELAY_SECONDS)

        emit(
            "response.created",
            response={**response, "status": "in_progress", "output": []},
        )
        for index, item in enumerate(output):
            emit(
                "response.output_item.added",
                output_index=index,
                item={
                    **item,
                    "status": "in_progress",
                    **({"arguments": ""} if item["type"] == "function_call" else {}),
                },
            )
            if item["type"] == "function_call":
                arguments = item["arguments"]
                for offset in range(0, len(arguments), 64):
                    emit(
                        "response.function_call_arguments.delta",
                        output_index=index,
                        item_id=item["id"],
                        delta=arguments[offset : offset + 64],
                    )
                emit(
                    "response.function_call_arguments.done",
                    output_index=index,
                    item_id=item["id"],
                    arguments=arguments,
                    name=item["name"],
                )
            emit("response.output_item.done", output_index=index, item=item)
        emit("response.completed", response=response)

    def _json(self, status: int, value: dict) -> None:
        content = (
            value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        ).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, _format: str, *_args: object) -> None:
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
