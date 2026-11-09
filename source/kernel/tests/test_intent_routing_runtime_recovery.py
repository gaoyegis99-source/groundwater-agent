from __future__ import annotations

import asyncio
from agents.testing import ModelStep, ScriptedModel, assistant_message, function_call

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
    claimed = first.interaction.claim_route(receipt["agent_run_id"])
    assert claimed is not None
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
