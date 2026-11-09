"""Cross-entrypoint V6 probe using one temporary SQLite journal.

The probe records HTTP/CLI responses and checks their returned facts against
the Python runtime. Optional SDK and external-adapter contracts have their own
focused suites; this probe never contacts a model or a remote tool service.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sse_starlette")

from fastapi.testclient import TestClient

from platform_lightweight_agent import (
    AgentBudget,
    AgentDecision,
    FileWorkspace,
    LocalToolExecutor,
    LocalWebSecurity,
    NodeContract,
    ScriptedAgentDriver,
    SQLiteRunJournal,
    ToolExecutionUncertainError,
    ToolReference,
    ValueBinding,
    WorkflowEngine,
    agent_step,
    approval_step,
    create_web_app,
    tool_step,
    workflow,
)
from platform_lightweight_agent.domain import check_domain_pack
from platform_lightweight_agent.portable import export_bundle, import_bundle


_EMPTY = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}
_APPROVED = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {"approved": {"type": "boolean"}},
    "required": ["approved"],
    "additionalProperties": False,
}
_MESSAGE = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {"message": {"type": "string"}},
    "required": ["message"],
    "additionalProperties": False,
}
_TOKEN = "interface-probe-session"


def _approval_workflow():  # type: ignore[no-untyped-def]
    return workflow(
        "interface-approval",
        "1",
        [
            approval_step(
                "review", "approve?", contract=NodeContract(_EMPTY, _APPROVED, {})
            )
        ],
        schema_version="v6-light-workflow-v3",
        input_schema=_EMPTY,
        output_schema=_APPROVED,
        output_bindings={
            "/approved": ValueBinding("step_output", "/approved", "review")
        },
    )


def _cli(tmp_path: Path, *args: str) -> dict[str, object]:
    source = Path(__file__).resolve().parents[1] / "src"
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(source), env.get("PYTHONPATH", "")) if part
    )
    completed = subprocess.run(
        [sys.executable, "-m", "platform_lightweight_agent.cli", *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_v6_functional_interfaces_http_cli_python_probe(tmp_path: Path) -> None:
    observations: list[dict[str, object]] = []

    def record(
        domain: str,
        interface: str,
        status: int | str,
        result: object,
        *,
        latency_ms: float | None = None,
    ) -> None:
        observation = {
            "domain": domain,
            "interface": interface,
            "status": status,
            "return": result,
        }
        if latency_ms is not None:
            observation["latency_ms"] = latency_ms
        observations.append(observation)

    journal_path = tmp_path / "runs.sqlite"
    journal = SQLiteRunJournal(journal_path)
    executor = LocalToolExecutor()
    tool = ToolReference("probe.uncertain", "1", "sha256:" + "7" * 64)
    submissions: list[str] = []

    def uncertain(_arguments: dict[str, object]) -> dict[str, object]:
        submissions.append("dispatched")
        raise ToolExecutionUncertainError("receipt unavailable")

    executor.register(tool, uncertain)
    engine = WorkflowEngine(journal, executor, {})
    app = create_web_app(
        engine,
        security=LocalWebSecurity(
            allowed_hosts=frozenset({"testserver"}), session_token=_TOKEN
        ),
    )
    definition = _approval_workflow()
    definition_path = tmp_path / "workflow.json"
    definition_path.write_text(json.dumps(definition.to_mapping()), encoding="utf-8")

    checked = _cli(tmp_path, "check", str(definition_path))
    assert checked["workflow_schema_version"] == "v6-light-workflow-v3"
    record("authoring", "cli.check", 0, checked["definition_digest"])
    plan = _cli(tmp_path, "plan", str(definition_path))
    assert plan["schema_version"] == "v6-execution-plan-v1"
    assert plan["effects"] == "not_executed"
    record("authoring", "cli.plan", 0, plan["schema_version"])

    with TestClient(app, headers={"X-V6-Local-Session": _TOKEN}) as client:

        def request(domain: str, method: str, path: str, expected: int, **kwargs):  # type: ignore[no-untyped-def]
            started_at = time.perf_counter()
            response = client.request(method, path, **kwargs)
            assert response.status_code == expected, response.text
            body = (
                response.json()
                if response.headers.get("content-type", "").startswith(
                    "application/json"
                )
                else None
            )
            record(
                domain,
                f"{method} {path}",
                response.status_code,
                (
                    body.get("status", body.get("code", body.get("schema_version")))
                    if isinstance(body, dict)
                    else "body"
                    if response.content
                    else "empty"
                ),
                latency_ms=round((time.perf_counter() - started_at) * 1000, 1),
            )
            return response

        assert request("system", "GET", "/v2/healthz", 200).json()["status"] == "ok"
        assert "V6 local run console" in request("web", "GET", "/console", 200).text
        assert (
            "STEP_STARTED" in request("web", "GET", "/console/assets/app.js", 200).text
        )
        started = request(
            "execution",
            "POST",
            "/v2/runs",
            201,
            json={"workflow": definition.to_mapping(), "run_id": "approval-run"},
        ).json()
        assert started["status"] == "WAITING_APPROVAL"
        execution_id = started["execution_id"]
        approval_id = next(iter(started["state"]["v3"]["pending_approvals"]))

        listed = request("query", "GET", "/v2/runs", 200).json()
        assert any(item["run_id"] == "approval-run" for item in listed["items"])
        detail = request("query", "GET", "/v2/runs/approval-run/detail", 200).json()
        assert detail["execution_id"] == execution_id
        view = request("query", "GET", "/v2/runs/approval-run/view", 200).json()
        assert view["execution_status"] == "WAITING_APPROVAL"
        summary = request("query", "GET", "/v2/runs/approval-run/summary", 200).json()
        assert summary["status"] == "WAITING_APPROVAL"
        compact = request("query", "GET", "/v2/runs/approval-run", 200).json()
        assert compact["status"] == summary["status"]
        explanation = request(
            "query", "GET", "/v2/runs/approval-run/explanation", 200
        ).json()
        assert explanation["schema_version"] == "v6-run-explanation-v1"
        preview = request(
            "recovery", "GET", "/v2/runs/approval-run/recovery-preview", 200
        ).json()
        assert preview["schema_version"] == "v6-recovery-preview-v1"
        events = request("events", "GET", "/v2/runs/approval-run/events", 200).json()
        assert any(event["type"] == "APPROVAL_REQUESTED" for event in events)
        assert events[-1]["type"] == "RUN_WAITING_APPROVAL"

        invalid = request(
            "approval",
            "POST",
            "/v2/runs/approval-run/approval",
            422,
            json={"approved": True},
        ).json()
        assert invalid["code"] == "INVALID_REQUEST"
        approved = request(
            "approval",
            "POST",
            "/v2/runs/approval-run/approval",
            200,
            json={"approval_id": approval_id, "approved": True},
        ).json()
        assert approved["status"] == "SUCCEEDED"
        assert approved["state"]["v3"]["output"] == {"approved": True}
        replay = request("events", "GET", "/v2/runs/approval-run/events/stream", 200)
        assert "event: RUN_STARTED" in replay.text
        assert "event: APPROVAL_REQUESTED" in replay.text
        assert "event: v6-terminal" in replay.text
        final_cursor = engine.events("approval-run")[-1].cursor.to_token()
        request(
            "events",
            "GET",
            "/v2/runs/approval-run/events/stream",
            204,
            headers={"Last-Event-ID": final_cursor},
        )

        request(
            "identity",
            "GET",
            "/v2/runs/approval-run/detail",
            409,
            params={"expected_execution_id": "stale"},
        )
        request("validation", "GET", "/v2/runs/approval-run/events?cursor=bad", 422)
        request("query", "GET", "/v2/runs/not-present/detail", 404)

        cancelled = request(
            "cancellation",
            "POST",
            "/v2/runs",
            201,
            json={"workflow": definition.to_mapping(), "run_id": "cancel-run"},
        ).json()
        assert cancelled["status"] == "WAITING_APPROVAL"
        requested = request(
            "cancellation",
            "POST",
            "/v2/runs/cancel-run/cancel",
            202,
            json={},
        ).json()
        assert requested["status"] == "CANCEL_REQUESTED"

        uncertain_flow = workflow(
            "interface-uncertain",
            "1",
            [tool_step("dispatch", tool, {}, approval_required=False)],
            schema_version="v6-light-workflow-v1",
        )
        unknown = request(
            "tool",
            "POST",
            "/v2/runs",
            201,
            json={"workflow": uncertain_flow.to_mapping(), "run_id": "uncertain-run"},
        ).json()
        assert unknown["status"] == "RECONCILING"
        assert submissions == ["dispatched"]
        action_id = next(iter(unknown["state"]["actions"]))
        reconciled = request(
            "recovery",
            "POST",
            f"/v2/runs/uncertain-run/actions/{action_id}/reconcile",
            200,
            json={"succeeded": True, "output": {"confirmed": True}},
        ).json()
        assert reconciled["status"] == "SUCCEEDED"
        assert submissions == ["dispatched"]

    with TestClient(app) as untrusted:
        response = untrusted.post(
            "/v2/runs", json={"workflow": definition.to_mapping(), "run_id": "denied"}
        )
        assert response.status_code == 403
        assert response.json() == {"code": "LOCAL_SESSION_REQUIRED"}
        record(
            "security", "POST /v2/runs without session", 403, response.json()["code"]
        )
        bad_host = untrusted.get("/v2/healthz", headers={"Host": "attacker.invalid"})
        assert bad_host.status_code == 400
        assert bad_host.json() == {"code": "LOCAL_HOST_REJECTED"}
        record(
            "security", "GET /v2/healthz with bad Host", 400, bad_host.json()["code"]
        )
        bad_origin = untrusted.get(
            "/v2/healthz", headers={"Origin": "https://attacker.invalid"}
        )
        assert bad_origin.status_code == 403
        assert bad_origin.json() == {"code": "LOCAL_ORIGIN_REJECTED"}
        record(
            "security",
            "GET /v2/healthz with bad Origin",
            403,
            bad_origin.json()["code"],
        )
        assert journal.get("approval-run").status == "SUCCEEDED"

    restored = WorkflowEngine(SQLiteRunJournal(journal_path), LocalToolExecutor(), {})
    assert restored.get("approval-run").status == "SUCCEEDED"
    assert restored.get("uncertain-run").status == "SUCCEEDED"
    record("persistence", "python.reopen", "OK", "SUCCEEDED")
    cli_runs = _cli(tmp_path, "runs", "--journal", str(journal_path))
    assert {item["run_id"] for item in cli_runs["items"]} >= {
        "approval-run",
        "cancel-run",
        "uncertain-run",
    }
    record("query", "cli.runs", 0, len(cli_runs["items"]))
    cli_explain = _cli(
        tmp_path, "explain", "approval-run", "--journal", str(journal_path)
    )
    assert cli_explain["status"] == "SUCCEEDED"
    record("query", "cli.explain", 0, cli_explain["status"])
    cli_events = _cli(
        tmp_path, "events", "approval-run", "--journal", str(journal_path)
    )
    assert cli_events and cli_events[-1]["cursor"]["sequence"] >= 1
    record("events", "cli.events", 0, len(cli_events))
    cli_preview = _cli(
        tmp_path, "preview-recovery", "cancel-run", "--journal", str(journal_path)
    )
    assert cli_preview["effects"] == "not_executed"
    record("recovery", "cli.preview-recovery", 0, cli_preview["status"])
    cli_info = _cli(tmp_path, "journal-info", str(journal_path))
    assert cli_info["schema_version"]
    record("persistence", "cli.journal-info", 0, cli_info["schema_version"])

    agent_engine = WorkflowEngine(
        journal,
        LocalToolExecutor(),
        {
            "scripted": ScriptedAgentDriver(
                [AgentDecision("offline answer", output={"message": "offline answer"})]
            )
        },
    )
    agent_run = agent_engine.run(
        workflow(
            "interface-agent",
            "1",
            [
                agent_step(
                    "answer",
                    "scripted",
                    tools=(),
                    budget=AgentBudget(),
                    contract=NodeContract(_EMPTY, _MESSAGE, {}),
                )
            ],
            schema_version="v6-light-workflow-v4",
            input_schema=_EMPTY,
            output_schema=_MESSAGE,
            output_bindings={
                "/message": ValueBinding("step_output", "/message", "answer")
            },
        ),
        {},
        run_id="agent-run",
    )
    assert agent_run.status == "SUCCEEDED"
    assert agent_run.state["v3"]["output"] == {"message": "offline answer"}
    record("agent", "python.run", "OK", agent_run.status)

    workspace = FileWorkspace.under(tmp_path / "files")
    source = tmp_path / "source.txt"
    source.write_text("input", encoding="utf-8")
    staged = workspace.stage_input(source, "input.txt")
    assert workspace.resolve_input(staged).read_text(encoding="utf-8") == "input"
    output_path = workspace.action_output_path("probe-action", "summary.txt")
    output_path.write_text("result", encoding="utf-8")
    published = workspace.publish_output("summary.txt", action_id="probe-action")
    assert workspace.resolve_input(published).read_text(encoding="utf-8") == "result"
    assert workspace.published_output("probe-action", "summary.txt") == published
    record("files", "python.publish-and-resolve", "OK", published.sha256)

    bundle_path = tmp_path / "workflow.bundle"
    exported = export_bundle(
        bundle_path, definition, files_root=workspace.input_root, files=("input.txt",)
    )
    imported = import_bundle(bundle_path, tmp_path / "imported")
    assert imported.workflow.definition_digest == exported.workflow.definition_digest
    assert (tmp_path / "imported" / "files" / "input.txt").read_text() == "input"
    record("portability", "python.bundle-v1-roundtrip", "OK", imported.schema_version)

    example = (
        Path(__file__).resolve().parents[1]
        / "examples"
        / "domain-packs"
        / "csv-statistics"
    )
    pack = check_domain_pack(example)
    assert pack.domain_id == "csv-statistics"
    record("domain", "python.check-domain-pack", "OK", pack.domain_id)

    print(
        "V6_INTERFACE_PROBE="
        + json.dumps(
            {
                "schema_version": "v6-interface-probe-v1",
                "observation_count": len(observations),
                "observations": observations,
            },
            ensure_ascii=False,
        )
    )
