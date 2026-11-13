from __future__ import annotations

import asyncio
import sqlite3
from agents.testing import ModelStep, ScriptedModel, assistant_message, function_call
import httpx

from platform_kernel.api.app import create_app
from platform_kernel.composition import PlatformApplication
from platform_kernel.database import connect_database


def _router_model(decision: str) -> ScriptedModel:
    literal = {
        "DIALOGUE": "CONSULT|NONE|NONE",
        "WORKFLOW_DRAFT": "CREATE_WORKFLOW|NONE|NONE",
        "NEEDS_CLARIFICATION": "UNKNOWN|NONE|REQUEST_KIND_AMBIGUOUS",
    }[decision]
    return ScriptedModel(
        [
            ModelStep(
                output=[
                    assistant_message(literal)
                ]
            )
        ]
    )


def _reply_model() -> ScriptedModel:
    return ScriptedModel(
        [
            ModelStep(
                output=[
                    function_call(
                        "submit_reply",
                        {
                            "conclusion": {"text": "已经理解。", "citations": []},
                            "details": [],
                            "unresolved_items": [],
                            "referenced_workflow_runs": [],
                            "include_execution_status": False,
                            "interaction_options": [],
                            "workflow_draft": None,
                        },
                        call_id="reply",
                    )
                ]
            )
        ]
    )


def test_runtime_start_marks_stranded_provider_attempt_unknown(kernel_settings) -> None:
    application = PlatformApplication(
        settings=kernel_settings(),
        model=ScriptedModel([]),
        intent_router_model=ScriptedModel([]),
    )
    with connect_database(application.database) as connection:
        connection.execute(
            """INSERT INTO provider_requests(
                   provider_request_id,stage,provider,model,protocol,attempt,
                   outcome,input_tokens,output_tokens,total_tokens,latency_ms,created_at
               ) VALUES('interrupted','AGENT','provider','model','responses',1,
                        'STARTED',0,0,0,0,'2026-10-24T00:00:00Z')"""
        )

    async def boot() -> None:
        await application.start()
        await application.close()

    asyncio.run(boot())
    with connect_database(application.database) as connection:
        row = connection.execute(
            "SELECT outcome,error_class FROM provider_requests "
            "WHERE provider_request_id='interrupted'"
        ).fetchone()
    assert tuple(row) == ("UNKNOWN", "PROCESS_INTERRUPTED")
    metrics = application.metric_snapshot()
    assert metrics["provider_unknown"]["count"] == 1
    assert metrics["provider_unknown"]["oldest_age_seconds"] > 0
    assert "AGENT" not in metrics["providers"]


async def _run_until_complete(application: PlatformApplication, session_id: str) -> dict:
    await application.start()
    try:
        for _ in range(500):
            turn = application.interaction.messages(session_id, None, 50)["turns"][-1]
            if turn["status"] in {"COMPLETE", "FAILED"}:
                return turn
            await asyncio.sleep(0.01)
    finally:
        await application.close()
    raise AssertionError("recovered message did not terminate")


def test_runtime_restart_before_decision_reruns_only_uncommitted_router(
    kernel_settings,
) -> None:
    settings = kernel_settings()
    first = PlatformApplication(
        settings=settings,
        model=ScriptedModel([]),
        intent_router_model=ScriptedModel([]),
    )
    session_id = first.interaction.create_session().session.session_id
    receipt = first.interaction.message(
        session_id,
        "restart-before-decision",
        "请解释这个现象",
        "AUTO",
    )
    route_metrics = first.metric_snapshot()["intent_routing"]
    assert route_metrics["pending"] == 1 and route_metrics["running"] == 0
    assert route_metrics["oldest_wait_seconds"] >= 0
    claimed = first.interaction.claim_route(receipt["agent_run_id"])
    assert claimed is not None
    route_metrics = first.metric_snapshot()["intent_routing"]
    assert route_metrics == {"pending": 0, "running": 1, "oldest_wait_seconds": 0.0}
    asyncio.run(first.close())

    router_model = _router_model("DIALOGUE")
    agent_model = _reply_model()
    restarted = PlatformApplication(
        settings=settings,
        model=agent_model,
        intent_router_model=router_model,
    )
    turn = asyncio.run(_run_until_complete(restarted, session_id))

    assert turn["status"] == "COMPLETE"
    assert turn["intent_decision"] == "DIALOGUE"
    assert len(router_model.calls) == 1
    assert len(agent_model.calls) == 1
    with connect_database(restarted.database) as connection:
        route = connection.execute(
            "SELECT route_attempts,unknown_cost_attempts FROM message_requests "
            "WHERE agent_run_id=?",
            (receipt["agent_run_id"],),
        ).fetchone()
    assert tuple(route) == (2, 1)


def test_runtime_restart_after_decision_never_reclassifies(
    kernel_settings,
) -> None:
    settings = kernel_settings()
    first = PlatformApplication(
        settings=settings,
        model=ScriptedModel([]),
        intent_router_model=ScriptedModel([]),
    )
    session_id = first.interaction.create_session().session.session_id
    receipt = first.interaction.message(
        session_id,
        "restart-after-decision",
        "请解释这个现象",
        "AUTO",
    )
    assert first.interaction.claim_route(receipt["agent_run_id"]) is not None
    first.interaction.resolve_route(
        receipt["agent_run_id"],
        "DIALOGUE",
        "READ_ONLY",
        provider="deepseek",
        model="test-router-model",
        trace_id="trace_committed_route",
        input_tokens=8,
        output_tokens=2,
        total_tokens=10,
        provider_requests=1,
    )
    asyncio.run(first.close())

    router_model = ScriptedModel([])
    agent_model = _reply_model()
    restarted = PlatformApplication(
        settings=settings,
        model=agent_model,
        intent_router_model=router_model,
    )
    turn = asyncio.run(_run_until_complete(restarted, session_id))

    assert turn["status"] == "COMPLETE"
    assert turn["intent_decision"] == "DIALOGUE"
    assert router_model.calls == ()
    assert len(agent_model.calls) == 1


def test_runtime_recovers_route_claim_after_readonly_sqlite_without_restart(
    kernel_settings, monkeypatch, caplog,
) -> None:
    async def exercise() -> None:
        router = _router_model("DIALOGUE")
        reply = _reply_model()
        application = PlatformApplication(
            settings=kernel_settings(), model=reply, intent_router_model=router,
        )
        session_id = application.interaction.create_session().session.session_id
        original_connect = sqlite3.connect
        database_readonly = False
        readonly_claim_opened = asyncio.Event()

        def connect(path, *args, **kwargs):
            if database_readonly:
                try:
                    task = asyncio.current_task()
                except RuntimeError:
                    task = None
                if task is not None:
                    task_name = task.get_name()
                    if task_name.startswith("kernel:route:") or task_name == "kernel:dispatcher":
                        if task_name.startswith("kernel:route:"):
                            readonly_claim_opened.set()
                        return original_connect(f"{path.as_uri()}?mode=ro", *args, uri=True, **kwargs)
            return original_connect(path, *args, **kwargs)

        await application.start()
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(application=application)),
            base_url="http://isolated-kernel",
        )
        try:
            monkeypatch.setattr(sqlite3, "connect", connect)
            database_readonly = True
            response = await client.post(
                f"/api/v12/sessions/{session_id}/messages",
                json={
                    "requestId": "temporary-readonly",
                    "message": "请解释这个现象",
                    "requestedMode": "AUTO",
                },
            )
            assert response.status_code == 202
            request = response.json()
            await asyncio.wait_for(readonly_claim_opened.wait(), timeout=3)
            for _ in range(200):
                if "Kernel scheduler write store is read-only" in caplog.text:
                    break
                await asyncio.sleep(0.01)
            assert "Kernel scheduler write store is read-only" in caplog.text
            assert not application.is_ready()
            assert (await client.get("/api/v12/health")).status_code == 200
            assert (await client.get("/internal/health/ready")).status_code == 503
            assert application.interaction.route_metrics()["pending"] == 1
            assert not router.calls
            assert not reply.calls

            database_readonly = False
            for _ in range(300):
                turn = application.interaction.messages(session_id, None, 50)["turns"][-1]
                if turn["status"] == "COMPLETE":
                    break
                await asyncio.sleep(0.01)
            assert turn["status"] == "COMPLETE"
            assert application.is_ready()
            assert (await client.get("/internal/health/ready")).status_code == 200
            assert len(router.calls) == len(reply.calls) == 1
            assert application.interaction.route_metrics()["pending"] == 0
            with connect_database(application.database) as connection:
                row = connection.execute(
                    "SELECT route_attempts FROM message_requests WHERE agent_run_id=?",
                    (request["agentRunId"],),
                ).fetchone()
            assert row[0] == 1
        finally:
            await client.aclose()
            await application.close()

    asyncio.run(exercise())


def test_composition_shares_one_eight_slot_gate_between_router_and_agents(
    kernel_settings,
) -> None:
    application = PlatformApplication(
        settings=kernel_settings(),
        model=ScriptedModel([]),
        intent_router_model=ScriptedModel([]),
    )
    gate = application.agent_runtime.sdk_execution.agent_slots
    assert application.intent_router.agent_slots is gate
    assert gate._value == application.settings.agent_concurrency == 8
    asyncio.run(application.close())
