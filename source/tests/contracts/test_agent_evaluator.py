from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
from threading import Thread

import httpx
import pytest


from scripts.evaluation import evaluate_agent as evaluator

stub_spec = importlib.util.spec_from_file_location(
    "compose_test_upstream",
    Path(__file__).resolve().parents[1] / "e2e/compose-stub/stub_server.py",
)
stub = importlib.util.module_from_spec(stub_spec)
stub_spec.loader.exec_module(stub)


def test_compose_stub_exposes_zero_token_provider_readiness(monkeypatch):
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:1")
    server = stub.ThreadingHTTPServer(("127.0.0.1", 0), stub.Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(trust_env=False) as client:
            response = client.get(
                f"http://127.0.0.1:{server.server_port}/v1/models", timeout=2
            )
        assert response.status_code == 200
        assert {item["id"] for item in response.json()["data"]} == {
            "test-model",
            "test-router-model",
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_compose_stub_answers_casual_greeting_without_data_or_task_tools():
    tool, reply = stub.provider_action(
        {
            "model": "test-model",
            "instructions": "",
            "input": [{"role": "user", "content": "你好"}],
            "tools": [
                {"name": "discover_datasets", "parameters": {}},
                {"name": "submit_reply", "parameters": {}},
                {"name": "update_task", "parameters": {}},
            ],
        },
        chat=False,
    )

    assert tool == "submit_reply"
    assert reply["conclusion"]["text"].startswith("你好")
    assert reply["referenced_workflow_runs"] == []
    assert reply["unresolved_items"] == []


def test_compose_stub_keeps_explicit_no_execute_comparison_read_only():
    message = "请比较当前资料可用的趋势分析方法，并说明各自限制，先不要执行。"
    router_action = stub.provider_action(
        {
            "model": "test-router-model",
            "instructions": "",
            "input": [{"role": "user", "content": message}],
            "tools": [],
        },
        chat=False,
    )

    assert router_action == (None, "CONSULT|NONE|NONE")


def test_compose_stub_preserves_read_only_intent_during_reply_repair():
    message = "请比较当前资料可用的趋势分析方法，并说明各自限制，先不要执行。"
    items = [
        {"role": "user", "content": message},
        {
            "type": "function_call",
            "call_id": "reply-1",
            "name": "submit_reply",
            "arguments": "{}",
        },
        {
            "type": "function_call_output",
            "call_id": "reply-1",
            "output": json.dumps({"status": "INVALID"}),
        },
    ]
    tool, reply = stub.provider_action(
        {
            "model": "test-model",
            "instructions": "",
            "input": items,
            "tools": [
                {"name": "discover_datasets", "parameters": {}},
                {"name": "submit_reply", "parameters": {}},
            ],
        },
        chat=False,
    )

    assert tool == "submit_reply"
    assert "不会启动计算" in reply["conclusion"]["text"]
    assert reply["referenced_workflow_runs"] == []
    assert reply["interaction_options"] == []


def test_compose_stub_describes_an_attachment_without_discovery_or_execution():
    message = "请说明这个附件的数据结构，不要执行计算。"
    router_action = stub.provider_action(
        {
            "model": "test-router-model",
            "instructions": "",
            "input": [{"role": "user", "content": message}],
            "tools": [],
        },
        chat=False,
    )
    tool, reply = stub.provider_action(
        {
            "model": "test-model",
            "instructions": "",
            "input": [{"role": "user", "content": message}],
            "tools": [
                {"name": "discover_datasets", "parameters": {}},
                {"name": "run_summary", "parameters": {}},
                {"name": "submit_reply", "parameters": {}},
            ],
        },
        chat=False,
    )

    assert router_action == (None, "CONSULT|NONE|NONE")
    assert tool == "submit_reply"
    assert "不执行计算" in reply["conclusion"]["text"]
    assert reply["referenced_workflow_runs"] == []
    assert reply["interaction_options"] == []


def test_submit_message_uses_unified_async_contract():
    def handler(request):
        assert request.method == "POST"
        assert request.url.path == "/api/v12/sessions/session/messages"
        body = json.loads(request.content)
        assert body["message"] == "请分析资料"
        assert body["requestedMode"] == "AUTO"
        assert set(body) == {"requestId", "message", "requestedMode"}
        return httpx.Response(202, json={"schemaVersion": "message-request.v5"})

    with httpx.Client(
        base_url="http://kernel.test",
        transport=httpx.MockTransport(handler),
    ) as client:
        receipt = evaluator.submit_message(client, "session", "请分析资料")
    assert receipt == {"schema_version": "message-request.v5"}


def test_submit_message_rejects_non_202_success():
    with httpx.Client(
        base_url="http://kernel.test",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={})),
    ) as client:
        with pytest.raises(AssertionError, match="expected 202"):
            evaluator.submit_message(client, "session", "请分析资料")


def test_live_reports_unobservable_body_assertions_without_claiming_pass():
    report = {"project_id": "p"}
    evaluator.verify_observations(
        [
            {"name": "discover_datasets", "input": None, "output": None},
            {"name": "inspect_data_version", "input": None, "output": None},
        ],
        report,
        {"primary"},
        "primary",
        "selection",
        None,
    )
    assert set(report["observation_assertions"][0].values()) == {"NOT_OBSERVED"}


def test_wait_session_accepts_queued_dialogue(monkeypatch):
    responses = iter([{"agent_steps": []}, {"agent_steps": ["COMPLETE"]}])
    monkeypatch.setattr(evaluator, "request", lambda *args: next(responses))
    monkeypatch.setattr(evaluator.time, "sleep", lambda _seconds: None)
    assert evaluator.wait_session(None, "session", 1)["agent_steps"] == ["COMPLETE"]


def test_fixed_observations_use_case_identity_within_shared_project():
    keys = []

    def audit(key, _expected):
        keys.append(key)
        return {"observed": "PASS"}

    report = {
        "project_id": "groundwater-demo",
        "case_id": "selection-1",
        "workflow_run_id": "workflow",
    }
    evaluator.verify_observations(
        [{"name": "discover_datasets"}, {"name": "inspect_data_version"}],
        report,
        {"primary"},
        "primary",
        "selection",
        audit,
    )
    evaluator.verify_execution_replay(report, {"outputs": []}, audit)
    assert keys == ["selection-1", "selection-1"]


@pytest.mark.parametrize("assertions", [{}, {"execution_replay_references": "FAILED"}])
def test_failed_or_empty_replay_evidence_never_passes(assertions):
    report = {"project_id": "p", "case_id": "case", "workflow_run_id": "workflow"}
    with pytest.raises(AssertionError):
        evaluator.verify_execution_replay(
            report, {"outputs": []}, lambda *args: assertions
        )
    assert "execution_replay_assertions" not in report


def test_live_replay_evidence_is_not_observed():
    report = {"project_id": "p", "workflow_run_id": "workflow"}
    evaluator.verify_execution_replay(report, {"outputs": []}, None)
    assert report["execution_replay_assertions"] == {
        "execution_replay_references": "NOT_OBSERVED"
    }


def test_rejection_waits_for_decision_and_continuation(monkeypatch):
    states = [
        {
            "confirmation": {"confirmation_id": "confirmation", "status": status},
            "agent_steps": [step],
            "workflow_run_ids": [],
            "remote_outputs": [],
        }
        for status, step in (
            ("PENDING", "WAITING_CONFIRMATION"),
            ("REJECTED", "RUNNING"),
            ("PENDING", "COMPLETE"),
            ("REJECTED", "COMPLETE"),
        )
    ]
    calls = []

    def request(client, method, path):
        assert method == "GET" and path == "/api/v12/sessions/session"
        calls.append(path)
        return states[len(calls) - 1]

    monkeypatch.setattr(evaluator, "request", request)
    monkeypatch.setattr(evaluator.time, "sleep", lambda seconds: None)
    assert evaluator.wait_rejection(None, "session", "confirmation", 1) == states[-1]
    assert len(calls) == 4


def test_stub_handles_exact_sdk_rejection_message_without_execution():
    tool, final = stub.next_action(
        [
            {"role": "user", "content": "找到北区监测样本，对 number 列做描述统计。"},
            {
                "type": "function_call",
                "name": "run_summary",
                "call_id": "run",
                "arguments": "{}",
            },
            {
                "type": "function_call_output",
                "call_id": "run",
                "output": "试验拒绝执行",
            },
        ],
        {"run_summary"},
    )
    assert tool is None
    assert final["workflow_runs"] == []
    assert "拒绝" in final["message"]
    assert stub._parse_tool_output("前端填写的拒绝原因", rejection=True) == {
        "status": "REJECTED"
    }
    with pytest.raises(SyntaxError):
        stub._parse_tool_output("unexpected plain text")


def test_stub_handles_user_rejection_for_a_frozen_model_execution():
    tool, final = stub.next_action(
        [
            {
                "role": "user",
                "content": '[Model execution] {"tool":"run_summary","arguments":{}}',
            },
            {
                "type": "function_call",
                "name": "run_summary",
                "call_id": "run",
                "arguments": "{}",
            },
            {
                "type": "function_call_output",
                "call_id": "run",
                "output": "参数需要调整",
            },
        ],
        {"run_summary"},
    )
    assert tool is None
    assert final["workflow_runs"] == []
    assert "拒绝" in final["message"]


@pytest.mark.parametrize("failure", ["execution", "agent_failed", "new_confirmation"])
def test_rejection_poll_does_not_hide_failures(monkeypatch, failure):
    session = {
        "confirmation": {"confirmation_id": "confirmation", "status": "REJECTED"},
        "agent_steps": ["COMPLETE"],
        "workflow_run_ids": [],
        "remote_outputs": [],
    }
    if failure == "execution":
        session["workflow_run_ids"] = ["unexpected-run"]
    elif failure == "agent_failed":
        session["agent_steps"] = ["FAILED"]
    else:
        session["confirmation"]["confirmation_id"] = "new-confirmation"
    monkeypatch.setattr(evaluator, "request", lambda *args: session)
    with pytest.raises(AssertionError):
        evaluator.wait_rejection(None, "session", "confirmation", 1)


@pytest.mark.parametrize("encoding", [json.dumps, repr])
@pytest.mark.parametrize(
    "invalid",
    [None, "cross_project", "wrong_inspection", "missing_candidate", "nonempty"],
)
def test_fixed_observation_assertions_use_received_inputs_not_trace(invalid, encoding):
    ids = ["primary", "decoy"]
    if invalid == "cross_project":
        ids.append("another-project-resource")
    if invalid == "missing_candidate":
        ids.remove("decoy")
    inspected_id = "decoy" if invalid == "wrong_inspection" else "primary"
    stub.EVIDENCE["case"] = [
        {
            "type": "function_call",
            "call_id": "search",
            "name": "discover_datasets",
            "arguments": '{"query":"secret-query"}',
        },
        {
            "type": "function_call_output",
            "call_id": "search",
            "output": encoding(
                {"data": {"items": [{"currentVersion": item} for item in ids]}}
            ),
        },
        {
            "type": "function_call",
            "call_id": "inspect",
            "name": "inspect_data_version",
            "arguments": json.dumps(
                {
                    "version_id": inspected_id,
                    "progress_intent": {
                        "capability": "data.version.inspect",
                        "objective": "核验目标版本的对象和语义",
                        "template_key": "data.version.inspect",
                    },
                }
            ),
        },
        {
            "type": "function_call_output",
            "call_id": "inspect",
            "output": encoding({"data": {"status": "OK", "versionId": inspected_id}}),
        },
    ]
    expected = {
        "allowed_ids": ["primary", "decoy"],
        "primary_id": "primary",
        "empty": invalid == "nonempty",
        "ambiguous": invalid == "missing_candidate",
        "inspect": True,
    }
    if invalid is None:
        assertions = stub.verify_evidence("case", expected)
        assert set(assertions.values()) == {"PASS"}
        assert "primary" not in json.dumps(assertions) and "secret" not in json.dumps(
            assertions
        )
    else:
        with pytest.raises(AssertionError):
            stub.verify_evidence("case", expected)


@pytest.mark.parametrize("tool", ["run_monitoring_trend", "run_flow_field"])
def test_groundwater_stub_requires_successful_sdk_replay_and_map(tool):
    user = {
        "role": "user",
        "content": "[Model execution] "
        + json.dumps({"tool": tool, "arguments": {"resourceId": "input"}}),
    }
    action, plan = stub.next_action([user], {tool, "record_plan"})
    assert action == "record_plan" and plan["steps"][0]["tool"] == tool
    results = [
        user,
        {
            "type": "function_call",
            "call_id": "execute",
            "name": tool,
            "arguments": "{}",
        },
        {
            "type": "function_call_output",
            "call_id": "execute",
            "output": json.dumps(
                {
                    "status": "COMPLETE",
                    "workflow_run_id": "workflow",
                    "outputs": [{"kind": "data_version"}],
                }
            ),
        },
    ]
    action, final = stub.next_action(results, {tool, "record_plan"})
    assert action is None and final["workflow_runs"] == ["workflow"]
    results[-1]["output"] = '{"status":"FAILED","outputs":[]}'
    with pytest.raises(AssertionError):
        stub.next_action(results, {tool, "record_plan"})


def test_flow_field_e2e_markers_keep_default_and_boundary_evidence_separate(
    monkeypatch,
):
    monkeypatch.setattr(stub, "EVIDENCE", {})
    tools = {"run_flow_field", "record_plan", "run_monitoring_coverage"}
    for marker, status in (
        ("groundwater.flow-field", "COMPLETE"),
        ("flow-field-custom", "COMPLETE"),
        ("flow-field-collision", "FAILED"),
    ):
        instruction = {"tool": "run_flow_field", "arguments": {"resourceId": "input"}}
        if status == "FAILED":
            instruction["expectedStatus"] = "FAILED"
        user = {
            "role": "user",
            "content": f"[Evaluation case: {marker}]\n[Model execution] "
            + json.dumps(instruction),
        }
        action, _ = stub.next_action([user], tools)
        assert action == "record_plan"
        replay = {
            "status": status,
            "workflow_run_id": marker,
            "outputs": [{"kind": "data_version"}] if status == "COMPLETE" else [],
            "error": "OUTPUT_FIELD_CONFLICT" if status == "FAILED" else None,
        }
        action, final = stub.next_action(
            [
                user,
                {
                    "type": "function_call",
                    "call_id": "flow",
                    "name": "run_flow_field",
                    "arguments": "{}",
                },
                {
                    "type": "function_call_output",
                    "call_id": "flow",
                    "output": json.dumps(replay),
                },
            ],
            tools,
        )
        assert action is None and final["workflow_runs"] == [marker]
    assert set(stub.EVIDENCE) == {
        "groundwater.flow-field",
        "flow-field-custom",
        "flow-field-collision",
    }
    for marker, evidence in stub.EVIDENCE.items():
        assert evidence[0]["content"].startswith(f"[Evaluation case: {marker}]")


@pytest.mark.parametrize("field", ["version_id", "checksum", "size_bytes"])
def test_fixed_evidence_checks_complete_replayed_data_version_references(field):
    reference = {
        "kind": "data_version",
        "service": "data-primary",
        "dataset_id": "dataset",
        "version_id": "result",
        "semantic_id": "semantic",
        "object_id": "object",
        "checksum": "sha256:" + "a" * 64,
        "size_bytes": 123,
        "format": "json",
        "media_type": "application/json",
    }
    replay = {
        "status": "COMPLETE",
        "workflow_run_id": "workflow",
        "outputs": [reference],
    }
    stub.EVIDENCE["execution"] = [
        {
            "type": "function_call",
            "name": "run_summary",
            "call_id": "execute",
            "arguments": "{}",
        },
        {
            "type": "function_call_output",
            "call_id": "execute",
            "output": json.dumps(replay),
        },
    ]
    expected = {
        "kind": "execution",
        "tool": "run_summary",
        "workflow_run_id": "workflow",
        "outputs": [reference],
    }
    assert set(stub.verify_evidence("execution", expected).values()) == {"PASS"}
    reference[field] = 456 if field == "size_bytes" else "wrong"
    with pytest.raises(AssertionError, match="REPLAY_DATA_VERSION_REFERENCE_MISMATCH"):
        stub.verify_evidence("execution", expected)


def test_execution_evidence_selects_requested_replay_from_shared_project():
    old = {
        "status": "COMPLETE",
        "workflow_run_id": "old",
        "outputs": [{"kind": "data_version", "version_id": "old"}],
    }
    current = {
        "status": "COMPLETE",
        "workflow_run_id": "current",
        "outputs": [{"kind": "data_version", "version_id": "current"}],
    }
    stub.EVIDENCE["shared-execution"] = [
        {
            "type": "function_call",
            "name": "run_flow_field",
            "call_id": "old",
            "arguments": "{}",
        },
        {"type": "function_call_output", "call_id": "old", "output": json.dumps(old)},
        {
            "type": "function_call",
            "name": "run_flow_field",
            "call_id": "current",
            "arguments": "{}",
        },
        {
            "type": "function_call_output",
            "call_id": "current",
            "output": json.dumps(current),
        },
    ]
    expected = {
        "kind": "execution",
        "tool": "run_flow_field",
        "workflow_run_id": "current",
        "outputs": current["outputs"],
    }
    assert set(stub.verify_evidence("shared-execution", expected).values()) == {"PASS"}


@pytest.mark.parametrize("field", ["versionId", "column", "unit", "model_checksum"])
def test_wrong_confirmation_is_never_approved(monkeypatch, field):
    model = {
        "model_id": "groundwater.summary",
        "model_version": "1.1.0",
        "model_checksum": "model-sha",
        "workflow_id": "summary",
        "workflow_version": "1.0.0",
        "workflow_checksum": "workflow-sha",
        "default_parameters": {"axis": "columns", "unit": ""},
        "final_outputs": ["summary", "summary-details"],
    }
    action = {
        "tool": "run_summary",
        "model_id": "groundwater.summary",
        "model_version": "1.1.0",
        "model_checksum": "model-sha",
        "workflow_id": "summary",
        "workflow_version": "1.0.0",
        "workflow_checksum": "workflow-sha",
        "arguments": {
            "versionId": "primary",
            "column": "number",
            "axis": "columns",
            "unit": "",
        },
    }
    if field in action["arguments"]:
        action["arguments"][field] = "wrong"
    else:
        action[field] = "wrong"
    session = {
        "session_id": "session",
        "confirmation": {"confirmation_id": "confirm", "actions": [action]},
        "workflow_run_ids": [],
        "remote_outputs": [],
    }

    def request(client, method, path, **kwargs):
        assert not path.endswith("/confirm"), "must not approve mismatched confirmation"
        if path == "/api/v12/capabilities":
            return {
                "product_id": "groundwater",
                "models": [model],
                "observation_tools": [{"name": name} for name in evaluator.TOOLS],
            }
        return session

    monkeypatch.setattr(evaluator, "request", request)
    monkeypatch.setattr(
        evaluator,
        "upload",
        lambda *a, **kw: {
            "project_id": "groundwater-demo",
            "dataset_id": "dataset",
            "version_id": "primary",
            "semantic_id": "semantic",
            "object_id": "object",
            "media_type": "text/csv",
            "format": "csv",
            "size_bytes": 18,
            "checksum": "sha256:" + "a" * 64,
        },
    )
    monkeypatch.setattr(evaluator, "wait_session", lambda *a: session)
    monkeypatch.setattr(
        evaluator,
        "inspect_run",
        lambda *a: (
            {},
            [{"name": "inspect_data_version", "input": '{"version_id":"primary"}'}],
        ),
    )
    monkeypatch.setattr(evaluator, "verify_search", lambda *a, **kw: {"primary"})
    # No HTTP client: reaching client.post would fail rather than approve.
    with pytest.raises(AssertionError, match="CONFIRMATION"):
        evaluator.run_case(
            None,
            "selection",
            {
                "project_id": "groundwater-demo",
                "case_id": "selection-1",
                "fixture_precision": 0,
            },
        )


def test_failed_live_cases_are_reported_once_without_retry(monkeypatch, capsys):
    calls = []

    def fail(client, scenario, report, *args):
        calls.append((scenario, report["iteration"]))
        raise httpx.ConnectError("provider unavailable")

    monkeypatch.setattr(evaluator, "run_case", fail)
    reports = evaluator.run_suite(
        "http://unused.test",
        mode="live",
        repetitions=3,
        scenarios=evaluator.LIVE_SCENARIOS,
    )
    assert len(calls) == len(reports) == len(capsys.readouterr().out.splitlines()) == 12
    assert len(set(calls)) == 12
    assert all(report["status"] == "FAILED" for report in reports)
    assert {report["project_id"] for report in reports} == {"groundwater-demo"}
    assert len({report["case_id"] for report in reports}) == 12


def test_compose_compiles_temporary_sources_and_cleans_up(monkeypatch):
    script = Path(__file__).resolve().parents[2] / "scripts/checks/check_compose_e2e.py"
    from scripts.checks.compose_e2e import command
    from scripts.checks.compose_e2e import scenarios as runner

    commands = []
    configs = []
    source_roots = []

    def run(command, **kwargs):
        commands.append(command)
        if "-f" not in command:
            assert command[1:4] == ["run", "--rm", "--network"]
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        path = Path(command[command.index("-f") + 1])
        configs.append(path)
        compose = json.loads(path.read_text())
        assert compose["networks"]["default"]["ipam"]["config"] == [
            {"subnet": "10.253.252.0/24"}
        ]
        assert set(compose["services"]) == {
            "gateway",
            "kernel-service",
            "data-db",
            "data-migrate",
            "data-service",
            "data-worker",
            "knowledge-next-db",
            "knowledge-next-migrate",
            "knowledge-next-service",
            "knowledge-next-worker",
            "knowledge-bootstrap",
            "executor-control",
            "executor-native-worker",
            "executor-qgis-worker",
            "executor-remote_http-worker",
            "test-upstream",
        }
        assert compose["services"]["test-upstream"]["networks"] == {"v6-egress": {}}
        published = []
        for service in ("gateway",):
            ports = compose["services"][service]["ports"]
            assert len(ports) == 1
            match = re.fullmatch(r"127\.0\.0\.1:([1-9][0-9]*):8080", ports[0])
            assert match is not None
            published.append(int(match.group(1)))
        assert len(set(published)) == 1
        assert command[command.index("-p") + 1].startswith("platform-groundwater-e2e-")
        kernel = compose["services"]["kernel-service"]
        embedding_services = [
            service
            for name, service in compose["services"].items()
            if name.startswith("knowledge-next")
            and "PLATFORM_EMBEDDING_BASE_URL" in service.get("environment", {})
        ]
        assert embedding_services
        assert {
            service["environment"]["PLATFORM_EMBEDDING_BASE_URL"]
            for service in embedding_services
        } == {"http://embedding.test:38173"}
        assert all(
            "test-upstream" not in service.get("depends_on", {})
            for service in embedding_services
        )
        source = Path(kernel["build"]["context"])
        source_roots.append(source)
        config = Path(
            next(
                volume.split(":/config/kernel-config.json:ro")[0]
                for volume in kernel["volumes"]
                if isinstance(volume, str)
                and volume.endswith(":/config/kernel-config.json:ro")
            )
        )
        payload = json.loads(config.read_text())
        assert evaluator.TOOLS <= {tool["name"] for tool in payload["observationTools"]}
        assert payload["productId"] == "groundwater"
        assert source != runner.ROOT and "platform-groundwater-e2e-source-" in str(
            source
        )
        assert not (source / ".env").exists()
        assert not list(source.rglob(".venv"))
        assert not list(source.rglob("node_modules"))
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    def fail_bootstrap(*args):
        raise RuntimeError("injected bootstrap failure")

    monkeypatch.setattr(runner, "_run", run)
    monkeypatch.setattr(runner, "_verify_bootstrap_failure_isolated", fail_bootstrap)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(script),
            "--product",
            "groundwater",
            "--embedding-base-url",
            "http://embedding.test:38173/",
            "--network-subnet",
            "10.253.252.0/24",
            "--transfer-evidence",
            str(Path.cwd() / ".generated/test-compose-cleanup"),
        ],
    )
    with pytest.raises(RuntimeError, match="injected bootstrap failure"):
        command.main()
    assert commands[-2][-5:] == ["down", "-v", "--remove-orphans", "--rmi", "local"]
    assert commands[-1][1:4] == ["run", "--rm", "--network"]
    assert all(not path.exists() for path in [*configs, *source_roots])


def test_compose_port_falls_back_to_running_container_inspection(monkeypatch):
    from scripts.checks.compose_e2e import topology as runner

    compose = ["docker", "compose", "-p", "isolated", "-f", "compose.json"]

    def run(command, **kwargs):
        if command[-3:] == ["port", "gateway", "8080"]:
            return subprocess.CompletedProcess(
                command, 1, stdout="", stderr="dependency failed"
            )
        if command[-3:] == ["ps", "-q", "gateway"]:
            return subprocess.CompletedProcess(
                command, 0, stdout="container-id\n", stderr=""
            )
        assert Path(command[0]).name == "docker"
        assert command[1:] == ["inspect", "container-id"]
        payload = [
            {
                "NetworkSettings": {
                    "Ports": {
                        "8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "32791"}]
                    }
                }
            }
        ]
        return subprocess.CompletedProcess(
            command, 0, stdout=json.dumps(payload), stderr=""
        )

    monkeypatch.setattr(runner, "_run", run)
    assert (
        runner._published_service_port(compose, "gateway", "8080") == "127.0.0.1:32791"
    )


def test_compose_retired_contract_probes_follow_selected_topology(monkeypatch):
    from scripts.checks.compose_e2e import assertions as runner

    fixture = json.loads(
        (Path(__file__).parent / "fixtures/retired_paths.json").read_text()
    )
    counts = {
        "kernel-service": len(fixture["kernel"]),
        "data-service": len(fixture["data"]),
        "knowledge-next-service": len(fixture["knowledge"]),
        "executor-control": len(fixture["executor"]),
    }
    probed = []

    class Response:
        status_code = 404

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def get(self, _):
            return Response()

    monkeypatch.setattr(runner.httpx, "Client", lambda **_: Client())
    monkeypatch.setattr(
        runner,
        "_exec_python",
        lambda _command, service, _source: (
            probed.append(service) or ",".join(["404"] * counts[service])
        ),
    )
    runner._check_retired_contracts([], "http://unused.test", {"data"})
    assert probed == ["kernel-service", "data-service"]
