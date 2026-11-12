"""Execute every assembled Groundwater model through the V12 confirmation chain."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time
from uuid import uuid4

import httpx

from scripts.lib.e2e_http import public_api_headers
from scripts.lib.wire_json import from_wire_json, to_wire_json


PROJECT = "groundwater-demo"
TERMINAL = {"COMPLETE", "FAILED", "CANCELLED"}
SYNTHETIC_TEST_ONLY = "SYNTHETIC_TEST_ONLY"
REAL_DERIVED_TEST_SAMPLE = "REAL_DERIVED_TEST_SAMPLE"
REAL_OBSERVED = "REAL_OBSERVED"


def _data_nature_text(data_nature: str) -> tuple[str, list[str], str]:
    if data_nature == SYNTHETIC_TEST_ONLY:
        return (
            "固定 seed 20260928 的开发验收输入",
            [SYNTHETIC_TEST_ONLY],
            "SYNTHETIC_TEST_ONLY：以下是六模型开发验收的合成输入，非真实业务资料。",
        )
    if data_nature == REAL_DERIVED_TEST_SAMPLE:
        return (
            "从受治理真实数据源确定性衍生的测试样本",
            [REAL_DERIVED_TEST_SAMPLE, "NOT_COMPLETE_SOURCE_DATASET"],
            "REAL_DERIVED_TEST_SAMPLE：以下是受治理真实来源的确定性衍生测试样本，"
            "不代表完整原始数据集。",
        )
    if data_nature == REAL_OBSERVED:
        return (
            "受治理真实观测输入",
            [],
            "REAL_OBSERVED：以下是受治理真实观测资料，按绑定版本及其数据契约解释。",
        )
    raise ValueError("DATA_NATURE_UNSUPPORTED")


def _publication_output(
    *,
    name: str,
    format_: str,
    media_type: str,
    data_nature: str,
    source: str,
) -> dict:
    description, limitations, _ = _data_nature_text(data_nature)
    return {
        "name": "input",
        "title": name,
        "format": format_,
        "media_type": media_type,
        "policy": "FILE_QUERYABLE" if format_ in {"csv", "parquet"} else "FILE_ONLY",
        "tags": [data_nature, "six-model-lifecycle"],
        "semantics": {
            "description": description,
            "fields": {},
            "grain": None,
            "time_range": None,
            "timezone": None,
            "crs": None,
            "vertical_datum": None,
            "limitations": limitations,
            "source": source,
        },
    }


def _execution_message(
    *, model_id: str, tool: str, arguments: dict, data_nature: str
) -> str:
    _, _, nature_text = _data_nature_text(data_nature)
    instruction = (
        "请执行静态制图：使用 render_map 把给定的 GeoJSON 监测点绘制为 PNG。"
        "点数据包含参数指定的 level 字段；先提出方案，等用户确认后执行。\n"
        if tool == "render_map"
        else f"请使用工具 {tool}，工具名必须逐字一致，不添加或替换前缀。\n"
    )
    return (
        instruction
        + "[Evaluation case: six-model-"
        + model_id
        + "]\n"
        + nature_text
        + "请使用指定模型和完整参数提出执行方案，等待用户确认，确认后报告执行结果。"
        "输入版本已经明确，请直接检查给定版本，不要搜索或替换为其他数据。"
        "多个独立版本可在同一轮并行检查。\n[Model execution] "
        + json.dumps({"tool": tool, "arguments": arguments}, ensure_ascii=False)
    )


def _request(client: httpx.Client, method: str, path: str, **kwargs):
    if "json" in kwargs and kwargs["json"] is not None:
        kwargs["json"] = to_wire_json(kwargs["json"])
    if "params" in kwargs and isinstance(kwargs["params"], dict):
        kwargs["params"] = to_wire_json(kwargs["params"])
    response = client.request(method, path, **kwargs)
    response.raise_for_status()
    return from_wire_json(response.json())


def _publish(
    client: httpx.Client,
    *,
    name: str,
    content: bytes,
    format_: str,
    media_type: str,
    data_nature: str,
    source: str,
    semantics: dict | None = None,
) -> dict:
    headers = {"X-Project-ID": PROJECT, "X-Client-Type": "cli"}
    output = _publication_output(
        name=name, format_=format_, media_type=media_type,
        data_nature=data_nature, source=source,
    )
    field_meanings = {}
    if semantics:
        field_meanings = semantics.get("fields", {})
        output["semantics"].update(
            {key: value for key, value in semantics.items() if key != "fields"}
        )
    accepted = _request(
        client,
        "POST",
        "/api/v12/data/publications",
        headers=headers,
        json={
            "idempotency_key": "six-model-" + uuid4().hex,
            "outputs": [output],
            "input_versions": [],
            "provenance": {"purpose": "v12-six-model-lifecycle"},
        },
    )
    publication_id = accepted["publication_id"]
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    uploaded = client.put(
        f"/api/v12/data/publications/{publication_id}/items/input",
        headers={**headers, "X-Content-SHA256": digest},
        content=content,
    )
    uploaded.raise_for_status()
    _request(
        client,
        "POST",
        f"/api/v12/data/publications/{publication_id}/prepare",
        headers=headers,
    )
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        state = _request(
            client,
            "GET",
            f"/api/v12/data/publications/{publication_id}",
            headers=headers,
        )
        if state["state"] == "READY":
            break
        if state["state"] in {"FAILED", "ABORTED"}:
            raise AssertionError(state)
        time.sleep(0.1)
    else:
        raise AssertionError("DATA_PREPARE_TIMEOUT")
    committed = _request(
        client,
        "POST",
        f"/api/v12/data/publications/{publication_id}/commit",
        headers=headers,
    )["outputs"][0]
    detail = _request(
        client,
        "GET",
        "/api/v12/data/versions/" + committed["version_id"],
        headers=headers,
    )
    semantic_id = committed["semantic_id"]
    if field_meanings:
        # Data assigns immutable IDs when it profiles the uploaded columns.
        field_ids = {
            field["name"]: field["field_id"] for field in detail["descriptor"]["fields"]
        }
        revision = _request(
            client,
            "POST",
            f"/api/v12/data/versions/{committed['version_id']}/semantics",
            headers=headers,
            json={
                "expected_semantic_id": semantic_id,
                "semantics": {
                    **output["semantics"],
                    "fields": {field_ids[name]: meaning for name, meaning in field_meanings.items()},
                },
            },
        )
        semantic_id = revision["semantic_id"]
    raw = next(item for item in detail["objects"] if item["role"] == "raw")
    return {
        "kind": "data_version",
        "service": "data-primary",
        "project_id": PROJECT,
        "dataset_id": committed["dataset_id"],
        "version_id": committed["version_id"],
        "semantic_id": semantic_id,
        "object_id": raw["object_id"],
        "format": raw["format"],
        "media_type": raw["media_type"],
        "size_bytes": raw["size"],
        "checksum": raw["checksum"],
    }


def _wait_session(client: httpx.Client, session_id: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = _request(client, "GET", f"/api/v12/sessions/{session_id}")
        except httpx.TransportError:
            time.sleep(0.5)
            continue
        if value["agent_steps"] and value["agent_steps"][-1] in {
            "WAITING_CONFIRMATION",
            *TERMINAL,
        }:
            return value
        time.sleep(0.25)
    raise AssertionError("SIX_MODEL_SESSION_TIMEOUT")


def _failure_diagnostics(client: httpx.Client, session: dict, model_id: str) -> dict:
    run_id = session["agent_run_id"]
    return {
        "model": model_id,
        "session": session,
        "agent_run": _request(
            client,
            "GET",
            f"/api/v12/agent-runs/{run_id}",
            params={"projectId": PROJECT},
        ),
        "provider_requests": _request(
            client,
            "GET",
            f"/api/v12/agent-runs/{run_id}/provider-requests",
            params={"projectId": PROJECT},
        ),
    }


def _execute(
    client: httpx.Client,
    capabilities: dict,
    *,
    model_id: str,
    tool: str,
    arguments: dict,
    input_versions: set[str],
    data_nature: str,
    timeout: float = 900,
    on_confirmed=None,
    cancelled: bool = False,
    output_directory: Path | None = None,
) -> dict:
    model = next(
        item for item in capabilities["models"] if item["model_id"] == model_id
    )
    session_id = _request(
        client, "POST", "/api/v12/sessions", json={"projectId": PROJECT}
    )["session_id"]
    accepted = _request(
        client,
        "POST",
        f"/api/v12/sessions/{session_id}/messages",
        json={
            "requestId": str(uuid4()),
            "requestedMode": "AUTO",
            "message": _execution_message(
                model_id=model_id,
                tool=tool,
                arguments=arguments,
                data_nature=data_nature,
            ),
        },
    )
    waiting = _wait_session(client, session_id, 300)
    assert waiting["goal_revision"] == accepted["goal_revision"]
    if waiting["agent_steps"][-1] != "WAITING_CONFIRMATION":
        raise AssertionError(_failure_diagnostics(client, waiting, model_id))
    assert not waiting["workflow_run_ids"] and not waiting["remote_outputs"]
    confirmation = waiting["confirmation"]
    assert confirmation and len(confirmation["actions"]) == 1
    action = confirmation["actions"][0]
    expected_arguments = {**model["default_parameters"], **arguments}
    assert action["tool"] == tool and action["arguments"] == expected_arguments, {
        "model": model_id,
        "expected_arguments": expected_arguments,
        "actual_action": action,
    }
    assert action["model_id"] == model_id
    assert action["model_checksum"] == model["model_checksum"]
    assert action["workflow_checksum"] == model["workflow_checksum"]
    _request(
        client,
        "POST",
        f"/api/v12/sessions/{session_id}/confirm",
        json={"confirmationId": confirmation["confirmation_id"]},
    )
    if on_confirmed is not None:
        on_confirmed(session_id, waiting["agent_run_id"])
    completed = _wait_session(client, session_id, timeout)
    if cancelled:
        assert completed["agent_steps"][-1] == "CANCELLED" and not completed["remote_outputs"], completed
        return {"session_id": session_id, "status": "CANCELLED"}
    if completed["agent_steps"][-1] != "COMPLETE":
        raise AssertionError(_failure_diagnostics(client, completed, model_id))
    assert len(completed["workflow_run_ids"]) == 1
    workflow = _request(
        client,
        "GET",
        "/api/v12/workflow-runs/" + completed["workflow_run_ids"][0],
        params={"projectId": PROJECT},
    )
    assert workflow["status"] == "SUCCEEDED", workflow
    assert workflow["outputs"] == completed["remote_outputs"]
    assert [item["output_name"] for item in workflow["outputs"]] == model[
        "final_outputs"
    ]
    headers = {"X-Project-ID": PROJECT, "X-Client-Type": "cli"}
    publication_ids = set()
    output_references = []
    for output in workflow["outputs"]:
        assert output["kind"] == "data_version" and output["service"] == "data-primary"
        detail = _request(
            client,
            "GET",
            "/api/v12/data/versions/" + output["version_id"],
            headers=headers,
        )
        assert set(detail["input_versions"]) == input_versions
        assert (
            detail["descriptor"]["provenance"]["model_checksum"]
            == model["model_checksum"]
        )
        publication_ids.add(detail["publication_id"])
        downloaded = client.get(
            f"/api/v12/data/versions/{output['version_id']}/objects/{output['object_id']}/content",
            headers=headers,
        )
        downloaded.raise_for_status()
        assert len(downloaded.content) == output["size_bytes"]
        assert (
            "sha256:" + hashlib.sha256(downloaded.content).hexdigest()
            == output["checksum"]
        )
        if output_directory is not None:
            name = output["output_name"]
            assert name == Path(name).name and name not in {".", ".."}
            output_directory.mkdir(parents=True, exist_ok=True)
            (output_directory / name).write_bytes(downloaded.content)
        output_references.append(
            {
                key: output[key]
                for key in (
                    "output_name",
                    "dataset_id",
                    "version_id",
                    "semantic_id",
                    "object_id",
                    "checksum",
                )
            }
        )
    assert len(publication_ids) == 1
    with httpx.Client(
        base_url=client.base_url,
        timeout=300,
        trust_env=False,
        headers=public_api_headers(),
    ) as restored_client:
        restored = _request(restored_client, "GET", f"/api/v12/sessions/{session_id}")
    assert restored["agent_steps"][-1] == "COMPLETE"
    assert restored["workflow_run_ids"] == completed["workflow_run_ids"]
    assert restored["remote_outputs"] == completed["remote_outputs"]
    assert restored["goal_revision"] == completed["goal_revision"]
    return {
        "model": model_id,
        "session_id": session_id,
        "workflow_run_id": workflow["run_id"],
        "job_id": workflow["remote_job_id"],
        "outputs": len(workflow["outputs"]),
        "input_version_ids": sorted(input_versions),
        "output_references": output_references,
        "publication_group": next(iter(publication_ids)),
        "status": "PASS",
    }


def _run_model_cases(client, capabilities: dict, cases: list[tuple]) -> list[dict]:
    """Run every independent model case and retain one terminal report per case.

    A failed model must not prevent the remaining assembled models from being
    exercised.  The caller still decides whether any FAIL report fails the
    overall gate, but it always receives the complete diagnostic matrix.
    """

    reports = []
    for model_id, tool, arguments, inputs, timeout in cases:
        started = time.monotonic()
        print(f"six-model START {model_id}", flush=True)
        try:
            report = _execute(
                client,
                capabilities,
                model_id=model_id,
                tool=tool,
                arguments=arguments,
                input_versions=inputs,
                data_nature=SYNTHETIC_TEST_ONLY,
                timeout=timeout,
            )
        except Exception as error:
            report = {
                "model": model_id,
                "status": "FAIL",
                "error_type": type(error).__name__,
                "error": str(error) or type(error).__name__,
            }
            print(
                f"six-model FAIL {model_id} "
                f"error={report['error_type']}:{report['error']}",
                flush=True,
            )
        else:
            print(
                f"six-model PASS {model_id} workflow={report['workflow_run_id']}",
                flush=True,
            )
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        reports.append(report)
    return reports


def _coverage_geojson() -> bytes:
    return json.dumps(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [116.4, 39.9],
                    },
                    "properties": {"level": 12.5, "well": "w1"},
                },
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [116.5, 40.0],
                    },
                    "properties": {"level": 15.0, "well": "w2"},
                },
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [116.45, 40.02],
                    },
                    "properties": {"level": 13.8, "well": "w3"},
                },
            ],
        },
        ensure_ascii=False,
    ).encode()


def run_suite(base_url: str, transfer_case: Path) -> list[dict]:
    manifest = json.loads((transfer_case / "inputs.json").read_text())
    with httpx.Client(
        base_url=base_url,
        timeout=300,
        trust_env=False,
        headers=public_api_headers(),
    ) as client:
        capabilities = _request(client, "GET", "/api/v12/capabilities")
        observations = (
            "well,time,hydraulic_head_m,x,y,elevation,depth\n"
            + "".join(
                f"w{well},2025-{month:02}-01,{80 + well + month / 10},"
                f"{500000 + (well % 2) * 1000},{3500000 + (well // 2) * 1000},"
                f"100,{10 + well + month / 10}\n"
                for well in range(4)
                for month in range(1, 13)
            )
        ).encode()
        table = _publish(
            client,
            name="六模型生命周期观测表",
            content=observations,
            format_="csv",
            media_type="text/csv",
            data_nature=SYNTHETIC_TEST_ONLY,
            source="v12-six-model-lifecycle",
        )
        points = _publish(
            client,
            name="六模型井点",
            content=_coverage_geojson(),
            format_="geojson",
            media_type="application/geo+json",
            data_nature=SYNTHETIC_TEST_ONLY,
            source="v12-six-model-lifecycle",
        )
        transfer_refs = {}
        for key, item in manifest["resources"].items():
            path = Path(item["path"])
            assert (
                "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
                == item["checksum"]
            )
            transfer_refs[key] = _publish(
                client,
                name="六模型迁移输入 " + key,
                content=path.read_bytes(),
                format_=item["format"],
                media_type=item["mediaType"],
                data_nature=SYNTHETIC_TEST_ONLY,
                source="v12-six-model-lifecycle",
            )

        cases = [
            (
                "groundwater.summary",
                "run_summary",
                {
                    "versionId": table["version_id"],
                    "column": "hydraulic_head_m",
                    "axis": "columns",
                },
                {table["version_id"]},
                900,
            ),
            (
                "groundwater.monitoring-coverage",
                "run_monitoring_coverage",
                {"versionId": points["version_id"], "outputFormat": "geojson"},
                {points["version_id"]},
                900,
            ),
            (
                "groundwater.monitoring-trend",
                "run_monitoring_trend",
                {
                    "versionId": table["version_id"],
                    "groupField": "well",
                    "timeField": "time",
                    "valueField": "hydraulic_head_m",
                    "frequency": "month",
                    "expectedStart": "2025-01-01",
                    "expectedEnd": "2025-12-01",
                    "baselineStart": "2025-01-01",
                    "baselineEnd": "2025-06-01",
                    "comparisonStart": "2025-07-01",
                    "comparisonEnd": "2025-12-01",
                    "aggregation": "mean",
                    "alpha": 0.05,
                    "threshold": 3.5,
                },
                {table["version_id"]},
                1200,
            ),
            (
                "groundwater.flow-field",
                "run_flow_field",
                {
                    "versionId": table["version_id"],
                    "wellField": "well",
                    "timeField": "time",
                    "valueField": "computed_head_m",
                    "xField": "x",
                    "yField": "y",
                    "referenceElevationField": "elevation",
                    "depthToWaterField": "depth",
                    "referenceElevationUnit": "m",
                    "depthToWaterUnit": "m",
                    "targetTime": "2025-06-01",
                    "selection": "latestAtOrBefore",
                    "maxAgeDays": 30,
                    "sourceCrs": "EPSG:32650",
                    "targetCrs": "EPSG:32650",
                    "pixelSize": 250,
                    "power": 2,
                    "neighborCount": 4,
                    "hydraulicConductivityMetersPerDay": 2,
                    "effectivePorosity": 0.25,
                },
                {table["version_id"]},
                1200,
            ),
        ]
        transfer_arguments = dict(manifest["parameters"])
        transfer_arguments.update(
            {key + "VersionId": ref["version_id"] for key, ref in transfer_refs.items()}
        )
        cases.append(
            (
                "groundwater.transfer-learning-analysis",
                "run_transfer_learning_analysis",
                transfer_arguments,
                {ref["version_id"] for ref in transfer_refs.values()},
                7200,
            )
        )
        cases.append(
            (
                "groundwater.render-map",
                "render_map",
                {
                    "sourceVersionId": points["version_id"],
                    "title": "Monitoring wells",
                    "valueField": "level",
                },
                {points["version_id"]},
                900,
            )
        )
        reports = _run_model_cases(client, capabilities, cases)
    print("v12 six-model lifecycle " + json.dumps(reports, ensure_ascii=False))
    return reports
