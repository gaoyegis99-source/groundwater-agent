"""Natural-language Groundwater evaluation in an isolated single-project deployment."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import traceback

import httpx

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.lib.wire_json import from_wire_json, to_wire_json
from scripts.lib.e2e_http import public_api_headers

from scripts.evaluation.evaluate_agent import (
    inspect_run,
    request,
    submit_message,
    upload,
    wait_rejection,
    wait_session,
)

SCENARIOS = ("trend", "flow", "flow_missing_unit", "ambiguity", "missing_unit", "wrong_dictionary", "conflict", "second_turn", "rejection", "version_freeze", "stale")
CLARIFICATIONS = {"ambiguity", "missing_unit", "flow_missing_unit", "wrong_dictionary", "conflict"}
SENTINEL = "semantic-sample-private-7182"


def semantic_key(case_id: str) -> str:
    digest = hashlib.sha256(case_id.encode()).hexdigest()
    return "".join(chr(0x9FE0 + int(character, 16)) for character in digest)


def fixture():
    fields = [f"附加字段{i}" for i in range(25)] + ["井号", "观测日", "水头", "东坐标", "北坐标", "基准高程", "地下水埋深", "备注"]
    lines = [",".join(fields)]
    for well in range(4):
        for month in range(1, 13):
            lines.append(",".join(["0"] * 25 + [f"w{well}", f"2025-{month:02}-01", str(20+well+month/5),
                str(500000+well%2*1000), str(3500000+well//2*1000), "100", str(10+well+month/10), SENTINEL]))
    analyses = {
        "run_monitoring_trend": {"bindings": {"groupField": "井号", "timeField": "观测日", "valueField": "水头"},
            "parameters": {"frequency": "month", "expectedStart": "2025-01-01", "expectedEnd": "2025-12-01",
                "baselineStart": "2025-01-01", "baselineEnd": "2025-06-01", "comparisonStart": "2025-07-01",
                "comparisonEnd": "2025-12-01", "aggregation": "mean"}},
        "run_flow_field": {"bindings": {"wellField": "井号", "timeField": "观测日", "xField": "东坐标", "yField": "北坐标",
                "referenceElevationField": "基准高程", "depthToWaterField": "地下水埋深"},
            "parameters": {"targetTime": "2025-06-01", "sourceCrs": "EPSG:32650", "targetCrs": "EPSG:32650",
                "pixelSize": 250, "neighborCount": 4, "hydraulicConductivityMetersPerDay": 2, "effectivePorosity": 0.25,
                "referenceElevationUnit": "m", "depthToWaterUnit": "m"}},
    }
    return ("\n".join(lines)+"\n").encode(), analyses


def dictionary(resource, analyses, unit="m"):
    return {"data": {key: resource[key] for key in (
                "dataset_id", "version_id", "semantic_id", "object_id", "checksum"
            )},
            "unit": unit, "meaning": "水头和高程以米计，地下水埋深为参考点到水面的向下距离；坐标为 EPSG:32650。",
            "analyses": analyses, "purpose": "合成测试资料；项目约定的分析方法与参数，不代表真实项目"}


def document(project, content, title="测区监测数据字典与项目约定"):
    return {"projectId": project, "contentVersion": "1.0.0", "language": "zh-CN", "title": title,
        "summary": "精确数据版本身份绑定的数据字典与方法参数；仅供隔离测试。",
        "sources": [{"sourceId": "synthetic-dictionary", "kind": "PROJECT_TEMPLATE", "title": "合成测区资料",
            "publisher": "Platform tests", "url": None, "version": "1.0.0", "publishedAt": "2026-09-28",
            "accessedAt": None, "reviewAfter": "2099-01-01", "validThrough": None, "lifecycleStatus": "ACTIVE"}],
        "chunks": [{"content": json.dumps(content, ensure_ascii=False), "sourceRefs": ["synthetic-dictionary"]}]}


def publish_document(knowledge, identifier, body, data_ref):
    source_names = {
        "sourceId": "source_id", "publishedAt": "published_at",
        "accessedAt": "accessed_at", "reviewAfter": "review_after",
        "validThrough": "valid_through", "lifecycleStatus": "lifecycle_status",
    }
    package = {
        "schema_version": "knowledge-package.v1",
        "project_id": body["projectId"],
        "idempotency_key": "groundwater-evaluation-" + identifier,
        "documents": [{
            "knowledge_id": identifier, "kind": "definition",
            "title": body["title"], "summary": body["summary"],
            "language": body["language"],
            "content_version": body["contentVersion"],
            "sources": [
                {source_names.get(key, key): value for key, value in source.items()}
                for source in body["sources"]
            ],
            "units": [{
                "unit_id": f"unit-{index}", "content": unit["content"],
                "source_refs": unit["sourceRefs"],
            } for index, unit in enumerate(body["chunks"])],
            "bindings": [{
                "service_binding": "data-primary",
                "project_id": data_ref["project_id"],
                "dataset_id": data_ref["dataset_id"],
                "version_id": data_ref["version_id"],
                "semantic_id": data_ref["semantic_id"],
            }],
            "provenance": {"purpose": "groundwater-agent-evaluation"},
        }],
    }
    headers = {"X-Project-ID": body["projectId"]}
    accepted = knowledge.post(
        "/api/v12/knowledge/imports", json=to_wire_json(package), headers=headers,
    )
    accepted.raise_for_status()
    import_id = from_wire_json(accepted.json())["import_id"]
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        response = knowledge.get(
            "/api/v12/knowledge/imports/" + import_id, headers=headers,
        )
        response.raise_for_status()
        job = from_wire_json(response.json())
        if job["state"] == "SUCCEEDED":
            return job
        if job["state"] == "FAILED":
            raise ValueError("KNOWLEDGE_IMPORT_FAILED")
        time.sleep(0.25)
    raise ValueError("KNOWLEDGE_IMPORT_TIMEOUT")


def prepare_directory(client):
    """Create one shared three-page dataset directory for the semantic cases."""
    for index in range(41):
        upload(
            client,
            "groundwater-demo",
            f"辅助资料{index}",
            "无关合成资料",
            b"note\n0\n",
        )


def _http_error_body(response: httpx.Response) -> dict:
    try:
        body = json.loads(response.content[:4096])
    except (ValueError, UnicodeError):
        return {"format": "non-json-or-truncated"}
    if not isinstance(body, dict):
        return {"format": "unexpected-json"}
    summary = {}
    code = body.get("code")
    if isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{1,80}", code):
        summary["code"] = code
    errors = body.get("errors")
    if isinstance(errors, list):
        summary["validation"] = [
            {"pointer": item["pointer"][:160], "code": item["code"][:80]}
            for item in errors[:4]
            if isinstance(item, dict)
            and isinstance(item.get("pointer"), str)
            and isinstance(item.get("code"), str)
            and re.fullmatch(r"(?:/[A-Za-z0-9_-]{1,80}){1,5}", item["pointer"])
            and re.fullmatch(r"[A-Za-z][A-Za-z0-9_:-]{0,79}", item["code"])
        ]
    detail = body.get("detail")
    if isinstance(detail, list) and "validation" not in summary:
        summary["validation"] = [
            {
                "loc": [
                    part for part in item["loc"][:5]
                    if isinstance(part, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", part)
                ],
                "type": item["type"][:80],
            }
            for item in detail[:4]
            if isinstance(item, dict)
            and isinstance(item.get("loc"), list)
            and isinstance(item.get("type"), str)
            and re.fullmatch(r"[A-Za-z][A-Za-z0-9_:-]{0,79}", item["type"])
        ]
    return summary or {"format": "details-omitted"}


def _assertion_detail(error: AssertionError) -> dict:
    value = error.args[0] if error.args else None
    if isinstance(value, dict) and isinstance(value.get("agent_steps"), list):
        output = value.get("agent_output") or {}
        confirmation = value.get("confirmation") or {}
        unresolved = output.get("unresolved_items") or []
        return {
            "agent_steps": value["agent_steps"][-6:],
            "confirmation_status": confirmation.get("status"),
            "workflow_run_ids": value.get("workflow_run_ids", [])[-3:],
            "unresolved_codes": [
                item for item in unresolved[:4]
                if isinstance(item, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{1,80}", item)
            ],
        }
    if isinstance(value, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{1,80}", value):
        return {"code": value}
    return {"format": "details-omitted"}


def run_case(client, scenario, report, publish, observe=None, audit=None, before_read=None):
    project = report["project_id"]
    case_id = report["case_id"]
    resource_name = f"测区监测-{case_id}"
    knowledge_id = "evaluation-dictionary-" + case_id
    knowledge_title = f"{resource_name}数据字典与项目约定"
    content, analyses = fixture()
    primary = upload(client, project, resource_name, f"合成试验资料；字段含义和分析方法参见{knowledge_title}。", content)
    if scenario == "ambiguity":
        upload(client, project, resource_name, "另一个同名候选", content)
    declared = dictionary(primary, analyses, None if scenario in {"missing_unit", "flow_missing_unit"} else "m")
    declared["semantic_key"] = semantic_key(case_id)
    if scenario in {"missing_unit", "flow_missing_unit"}:
        declared["meaning"] = "指标单位尚未核实。模型默认单位不能作为依据。"
    if scenario == "wrong_dictionary":
        declared["data"]["checksum"] = "sha256:" + "0" * 64
    publish(knowledge_id, document(project, declared, knowledge_title), primary)
    if scenario == "stale":
        replacement = document(project, declared, knowledge_title)
        replacement["contentVersion"] = "1.0.1"
        before_read(knowledge_id, replacement)
    if scenario == "conflict":
        other = dictionary(primary, analyses, "cm")
        other["semantic_key"] = declared["semantic_key"]
        other["meaning"] = "另一份资料声明高程与埋深单位为厘米；需要核实。"
        publish(
            knowledge_id + "-conflict",
            document(project, other, knowledge_title + "（冲突来源）"),
            primary,
        )
    tool = "run_flow_field" if scenario.startswith("flow") else "run_monitoring_trend"
    expected = {"versionId": primary["version_id"], **analyses[tool]["bindings"], **analyses[tool]["parameters"]}
    capabilities = request(client, "GET", "/api/v12/capabilities")
    model = next(m for m in capabilities["models"] if m["tool"] == tool)
    session = request(client, "POST", "/api/v12/sessions", json={"projectId": project})
    report["session_id"] = session["session_id"]
    path = f"/api/v12/sessions/{session['session_id']}"
    for turn in range(2 if scenario == "second_turn" else 1):
        prompt = f"请按项目约定执行「{resource_name}」的{'流场分析' if scenario.startswith('flow') else '变化趋势分析'}，先核实资料；如有缺失或冲突请向我澄清。（试验编号 {case_id}-{turn}）"
        started = time.monotonic()
        submit_message(client, report["session_id"], prompt)
        try:
            session = wait_session(client, report["session_id"], 300)
        except AssertionError as error:
            failed_session = error.args[0] if error.args else None
            if (
                isinstance(failed_session, dict)
                and failed_session.get("agent_run_id")
            ):
                inspect_run(client, failed_session, project, report)
            raise
        assert session["agent_steps"][-1] != "FAILED", session
        run, spans = inspect_run(client, session, project, report)
        if report["mode"] == "live":
            assert run["provider"] == os.environ["PLATFORM_AGENT_PROVIDER"]
            assert run["model"] == os.environ["PLATFORM_AGENT_MODEL"]
        report["proposal_seconds"] = time.monotonic() - started
        report["tool_count"] = len(spans)
        report["token_usage"] = run["token_usage"]
        clarification = scenario in CLARIFICATIONS
        if clarification:
            assert session["confirmation"] is None and session["agent_output"]["unresolved_items"]
            assert not session["workflow_run_ids"]
            if audit:
                audit(project, 0)
            report["clarification_correct"] = True
        else:
            assert session["confirmation"] is not None, session
            action, = session["confirmation"]["actions"]
            assert action["model_id"] == model["model_id"] and action["model_checksum"] == model["model_checksum"]
            assert all(action["arguments"].get(key) == value for key, value in expected.items()), action
            preparation = session["preparation"]
            assert preparation["status"] == "READY", preparation
            assert preparation["fresh_for_current_turn"] is True, preparation
            assert preparation["disposition"] == "WAITING_CONFIRMATION", preparation
            entry, = preparation["entries"]
            assert entry["target_id"] == model["model_id"], entry
            assert entry["inputs"]["versionId"]["version_id"] == primary["version_id"], entry
            prepared_arguments = {
                **entry["parameters"],
                **{
                    name: reference["version_id"]
                    for name, reference in entry["inputs"].items()
                },
            }
            assert all(
                prepared_arguments.get(key) == value
                for key, value in expected.items()
            ), entry
            report["preparation_correct"] = True
            report["preparation_entry_count"] = len(preparation["entries"])
            report["data_reference_correct"] = report["parameters_correct"] = True
            reason = run["plans"][-1]["reason"]
            report["reference_complete"] = all(value in reason for value in (
                primary["version_id"], primary["checksum"], "dictionary"
            ))
            assert report["reference_complete"]
        if observe:
            report["evidence_assertions"] = observe(prompt, {"kind": "semantics", "minimum_pages": 3,
                "clarification": scenario if clarification else None,
                "version_id": primary["version_id"],
                "checksum": primary["checksum"], "tool": tool, "arguments": expected, "stale": scenario == "stale"})
        else:
            report["evidence_assertions"] = {"tool_bodies": "NOT_OBSERVED", "cross_turn_body_absence": "NOT_OBSERVED"}
        trace = request(client, "GET", f"/api/v12/agent-runs/{run['agent_run_id']}/trace", params={"projectId": project})
        assert SENTINEL not in json.dumps(trace)
        assert all(span["span_data"].get("input") is None and span["span_data"].get("output") is None for span in trace["spans"])
        if clarification:
            return
        confirmation = session["confirmation"]
        if scenario == "rejection":
            request(client, "POST", path + "/reject", json={"confirmationId": confirmation["confirmation_id"], "reason": "试验拒绝执行"})
            wait_rejection(client, report["session_id"], confirmation["confirmation_id"], 300)
            if audit:
                audit(project, 0)
            return
        if scenario == "version_freeze":
            upload(
                client, project, resource_name, "新版本不能替换已确认的引用",
                content.replace(b"\n", b"\r\n"), primary["dataset_id"],
            )
        request(client, "POST", path + "/confirm", json={"confirmationId": confirmation["confirmation_id"]})
        session = wait_session(client, report["session_id"], 300)
        assert session["agent_steps"][-1] == "COMPLETE", session
        workflow = request(client, "GET", f"/api/v12/workflow-runs/{session['workflow_run_ids'][-1]}", params={"projectId": project})
        assert workflow["status"] == "SUCCEEDED"
        outputs = [
            item for item in workflow["outputs"]
            if item["kind"] == "data_version"
        ]
        assert outputs and len({item["output_name"] for item in outputs}) == len(outputs)
        for output in outputs:
            response = client.get(
                f"/api/v12/data/versions/{output['version_id']}/objects/{output['object_id']}/content",
                headers={"X-Project-ID": project, "X-Client-Type": "cli"},
            )
            response.raise_for_status()
            assert len(response.content) == output["size_bytes"]
            assert "sha256:" + hashlib.sha256(response.content).hexdigest() == output["checksum"]
            metadata = request(
                client, "GET", f"/api/v12/data/versions/{output['version_id']}",
                headers={"X-Project-ID": project, "X-Client-Type": "cli"},
            )
            assert metadata["version_id"] == output["version_id"]
            assert metadata["input_versions"] == [primary["version_id"]]
            assert metadata["descriptor"]["provenance"]["model_checksum"] == model["model_checksum"]
        report["output_names"] = [item["output_name"] for item in outputs]
        report["result_correct"] = True
        if audit:
            audit(project, turn + 1)


def run_suite(base_url, *, publish, mode, repetitions=3, observe=None, audit=None,
              before_read=None, scenarios=SCENARIOS):
    scenarios = tuple(scenarios)
    reports = []
    with httpx.Client(
        base_url=base_url,
        timeout=300,
        trust_env=False,
        headers=public_api_headers(),
    ) as client:
        if any(scenario != "stale" or before_read is not None for scenario in scenarios):
            prepare_directory(client)
        for scenario in scenarios:
            for iteration in range(repetitions):
                report = {"mode": mode, "scenario": scenario, "iteration": iteration+1,
                          "project_id": "groundwater-demo", "case_id": f"{scenario}-{iteration + 1}",
                          "agent_runs": {}}
                if scenario == "stale" and before_read is None:
                    report.update(status="NOT_RUN", reason="CONTROLLED_REPLACEMENT_HOOK_UNAVAILABLE")
                    reports.append(report)
                    print(json.dumps(report), flush=True)
                    continue
                start = time.monotonic()
                try:
                    run_case(client, scenario, report, publish, observe, audit, before_read)
                    report["status"] = "PASS"
                except (AssertionError, httpx.HTTPError, ValueError, KeyError) as error:
                    report["status"] = "FAILED"
                    report["error_type"] = type(error).__name__
                    if isinstance(error, httpx.HTTPStatusError):
                        report["error_status"] = error.response.status_code
                        report["error_body"] = _http_error_body(error.response)
                    elif isinstance(error, AssertionError):
                        report["assertion_detail"] = _assertion_detail(error)
                    last = traceback.extract_tb(error.__traceback__)[-1]
                    report["failure_location"] = {"function": last.name, "line": last.lineno}
                report["elapsed_seconds"] = round(time.monotonic()-start, 3)
                reports.append(report)
                print(json.dumps(report, ensure_ascii=False), flush=True)
    return reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", help="Isolated Groundwater Gateway")
    parser.add_argument("--knowledge-url", help="Trusted isolated Knowledge HTTP endpoint")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    required = ("PLATFORM_AGENT_PROVIDER", "PLATFORM_AGENT_BASE_URL", "PLATFORM_AGENT_MODEL", "PLATFORM_AGENT_API_KEY_FILE")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        report = {"status": "NOT_RUN", "reason": "MISSING_MODEL_CONFIGURATION", "missing": missing,
                  "planned_scenarios": list(SCENARIOS), "repetitions_per_scenario": 3}
        args.output.write_text(json.dumps(report, indent=2)+"\n")
        print(json.dumps(report))
        return
    if not args.base_url or not args.knowledge_url:
        parser.error("--base-url and --knowledge-url are required with model configuration")
    with httpx.Client(
        base_url=args.knowledge_url, timeout=60, trust_env=False,
    ) as knowledge:
        def publish(identifier, body, data_ref):
            return publish_document(knowledge, identifier, body, data_ref)
        reports = run_suite(args.base_url, publish=publish, mode="live")
    summary = {"mode": "live", "status": "FAILED" if any(r["status"] == "FAILED" for r in reports) else "PARTIAL" if any(r["status"] == "NOT_RUN" for r in reports) else "PASS",
               "cases": reports, "pass_rate": sum(r["status"] == "PASS" for r in reports)/len(reports),
               "provider": os.environ["PLATFORM_AGENT_PROVIDER"], "model": os.environ["PLATFORM_AGENT_MODEL"],
               "metrics": {key: {"observed": len(values), "correct": sum(values),
                                  "rate": sum(values)/len(values) if values else None}
                           for key in ("data_reference_correct", "parameters_correct", "preparation_correct", "clarification_correct", "reference_complete")
                           if (values := [r[key] for r in reports if key in r])},
               "unobserved": ["provider_tool_bodies", "cross_turn_initial_model_input", "model_restatement_leakage"]}
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n")
    raise SystemExit(1 if summary["status"] == "FAILED" else 0)


if __name__ == "__main__":
    main()
