from __future__ import annotations

import asyncio
from dataclasses import replace

from agents.testing import ScriptedModel, ModelStep, function_call
import pytest

from kernel.tests.test_interaction_recovery import _application, _recover_interaction
from platform_kernel.database import connect_database, read_session_events
from platform_kernel.errors import ConfirmationConflictError
from platform_kernel.agent_runtime.sdk_execution.usage_ports import CallbackActivityLog


@pytest.mark.parametrize("mode", ["DIALOGUE", "WORKFLOW_DRAFT"])
def test_capacity_wait_survives_restart_with_original_request_and_mode(kernel_settings, mode):
    async def exercise():
        settings = kernel_settings()
        entered, queued = asyncio.Event(), asyncio.Event()
        active = 0
        peak = 0

        async def blocked(_call):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                active -= 1

        first_model = ScriptedModel([ModelStep.respond(blocked) for _ in range(8)])
        first = _application(settings, first_model)
        sessions = [first.interaction.create_session().session.session_id for _ in range(9)]
        session_id = sessions[-1]
        dependencies = first.agent_runtime.sdk_execution

        def record(current_session, payload):
            dependencies.activity_log.record(current_session, payload)
            if current_session == session_id and payload["status"] == "QUEUED":
                queued.set()

        first.agent_runtime.sdk_execution = replace(
            dependencies,
            activity_log=CallbackActivityLog(record),
        )
        policy = "EXECUTION_PROPOSAL" if mode == "DIALOGUE" else "READ_ONLY"
        tasks = []
        try:
            # Establish eight live holders before exercising capacity recovery;
            # This synchronizes fixture startup, not a latency assertion. Allow
            # headroom when the complete suite shares a busy development host.
            for item in sessions[:-1]:
                entered.clear()
                tasks.append(asyncio.create_task(first.interaction.turn(
                    item, "hold", dialogue_policy=policy,
                )))
                await asyncio.wait_for(entered.wait(), 10)
            assert active == peak == 8
            if mode == "DIALOGUE":
                task = asyncio.create_task(first.interaction.turn(
                    session_id, "pending question", dialogue_policy=policy,
                ))
            else:
                first.interaction.draft_workflow(session_id, "durable-request", "pending question")
                task = asyncio.create_task(first.interaction.resume_pending_start(session_id))
            tasks.append(task)
            await asyncio.wait_for(queued.wait(), 10)
            assert len(first_model.calls) == peak == 8
            with connect_database(first.database) as connection:
                row = connection.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
                run_id = row["pending_agent_run_id"]
                assert run_id is not None and row["agent_run_id"] is None
                assert connection.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0] == 8
                assert connection.execute("SELECT 1 FROM agent_runs WHERE agent_run_id=?", (run_id,)).fetchone() is None
                assert connection.execute("SELECT mode FROM agent_requests WHERE agent_run_id=?", (run_id,)).fetchone()[0] == mode
                events = read_session_events(connection, session_id, "agent.activity")
            assert len(events) == 1 and events[0]["data"]["status"] == "QUEUED"
            activity_id = events[0]["data"]["activity_id"]
            turn = first.interaction.messages(session_id, None, 50)["turns"][0]
            assert turn["status"] == "QUEUED"
            assert [(item["activity_id"], item["status"]) for item in turn["activities"]] == [(activity_id, "QUEUED")]
            with pytest.raises(ConfirmationConflictError, match="AGENT_EXECUTION_IN_PROGRESS"):
                    await first.interaction.turn(
                        session_id, "duplicate", dialogue_policy=policy,
                    )
        finally:
            # Simulate process loss without completing the in-flight model calls.
            for task in tasks:
                task.cancel()
            assert all(isinstance(result, asyncio.CancelledError) for result in await asyncio.gather(*tasks, return_exceptions=True))
            await first.close()
            await first.client.aclose()

        def recovered_reply(call):
            names = {tool.name for tool in call.tools}
            if mode == "DIALOGUE":
                assert {"record_plan", "run_summary"} <= names
                return ModelStep(output=[function_call("submit_reply", {
                    "conclusion": {"text": "recovered", "citations": []},
                    "details": [],
                    "unresolved_items": [{"text": "Need the input resource.", "citations": []}],
                    "referenced_workflow_runs": [], "include_execution_status": False,
                    "interaction_options": [],
                    "workflow_draft": None,
                }, call_id="reply")])
            assert "record_plan" not in names and "run_summary" not in names
            assert ("get_operator" in names) == (mode == "WORKFLOW_DRAFT")
            return ModelStep(output=[function_call("submit_reply", {
                "conclusion": {"text": "recovered", "citations": []},
                "details": [],
                "unresolved_items": [{"text": "Need the input resource.", "citations": []}],
                "referenced_workflow_runs": [], "include_execution_status": False,
                "interaction_options": [],
                "workflow_draft": None,
            }, call_id="reply")])

        recovered_model = ScriptedModel([ModelStep.respond(recovered_reply)])
        restarted = _application(settings, recovered_model)
        try:
            await _recover_interaction(restarted)
            await _recover_interaction(restarted)
            view = restarted.interaction.get(session_id).session
            assert view.agent_run_id == run_id
            expected_message = "recovered\n\n需要补充：\nNeed the input resource."
            assert view.agent_output.message == expected_message
            assert restarted.agent_runtime.get(run_id).status == "COMPLETE"
            assert len(recovered_model.calls) == 1
            with connect_database(restarted.database) as connection:
                events = read_session_events(connection, session_id, "agent.activity")
                assert connection.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0] == 9
                assert connection.execute("SELECT COUNT(*) FROM confirmations").fetchone()[0] == 0
                assert connection.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0] == 0
                assert connection.execute("SELECT COUNT(*) FROM messages WHERE session_id=?", (session_id,)).fetchone()[0] == 2
                assert connection.execute("SELECT COUNT(*) FROM session_events WHERE session_id=? AND event_type='agent.running'", (session_id,)).fetchone()[0] == 1
            segments = [event["data"] for event in events if event["data"]["kind"] == "SEGMENT"]
            assert [item["activity_id"] for item in segments] == [activity_id] * 3
            assert [item["status"] for item in segments] == ["QUEUED", "RUNNING", "COMPLETE"]
        finally:
            await restarted.close()
            await restarted.client.aclose()

    asyncio.run(exercise())
