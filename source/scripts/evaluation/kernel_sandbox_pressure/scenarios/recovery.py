"""Crash and persistence recovery pressure scenarios."""
from __future__ import annotations
import asyncio
from pathlib import Path
import tempfile
import httpx
from platform_kernel.composition import PlatformApplication
from platform_kernel.database import connect_database
from ..context import ProbeRecorder
from ..faults import ProbeTransport
from ..probe import RouterModel, ScenarioModel, _build_application, _settings, _wait_for_state

async def _run_recovery_probe(root: Path, probes: ProbeRecorder) -> dict[str, object]:
    """Restart the runtime at durable Agent and Workflow checkpoints."""
    agent_run_recovered = False
    confirmation_recovered = False
    workflow_recovered = False
    workflow_count = 0
    with tempfile.TemporaryDirectory(prefix="kernel-recovery-sandbox-") as temporary:
        data_root = Path(temporary)
        settings = _settings(root, data_root)
        first_client = httpx.AsyncClient(
            transport=httpx.MockTransport(ProbeTransport(probes, settings.definition_set_digest))
        )
        first = PlatformApplication(
            settings=settings,
            model=ScenarioModel(probes),
            client=first_client,
            intent_router_model=RouterModel(),
        )
        await first.start()
        session = first.interaction.create_session("recovery-user").session.session_id
        await asyncio.to_thread(first.interaction.message, session, "recovery-message", "sandbox-exec", "AUTO")
        state = await _wait_for_state(first, session, {"WAITING_CONFIRMATION"})
        confirmation_recovered = state["view"].session.confirmation is not None
        agent_run_id = state["view"].session.agent_run_id
        await first.close()
        await first_client.aclose()

        second, _transport, second_client = _build_application(root, data_root, probes)
        await second.start()
        try:
            restored = second.interaction.get(session)
            restored_confirmation = restored.session.confirmation
            agent_run_recovered = (
                isinstance(agent_run_id, str)
                and restored.session.agent_run_id == agent_run_id
            )
            if restored_confirmation is None:
                probes.fail(
                    "PROBE_AGENT_RECOVERY_LOST_CONFIRMATION",
                    phase="recovery",
                    operation="agent_run",
                    detail="confirmation missing after restart",
                )
            else:
                await second.interaction.confirm(session, restored_confirmation.confirmation_id, "recovery-user")
                await _wait_for_state(second, session, {"COMPLETE"}, timeout=20)
            confirmation_recovered = confirmation_recovered and restored_confirmation is not None
            if not agent_run_recovered:
                probes.fail(
                    "PROBE_AGENT_RECOVERY_LOST_RUN",
                    phase="recovery",
                    operation="agent_run",
                    detail="AgentRun identity was not restored",
                )
        finally:
            await second.close()
            await second_client.aclose()

    with tempfile.TemporaryDirectory(prefix="kernel-workflow-recovery-") as temporary:
        data_root = Path(temporary)
        settings = _settings(root, data_root)
        held_transport = ProbeTransport(probes, settings.definition_set_digest, fault="executor_hold")
        held_client = httpx.AsyncClient(transport=httpx.MockTransport(held_transport))
        held = PlatformApplication(
            settings=settings,
            model=ScenarioModel(probes),
            client=held_client,
            intent_router_model=RouterModel(),
        )
        await held.start()
        workflow_session = held.interaction.create_session("workflow-recovery-user").session.session_id
        await asyncio.to_thread(held.interaction.message, workflow_session, "workflow-recovery", "sandbox-exec", "AUTO")
        workflow_state = await _wait_for_state(held, workflow_session, {"WAITING_CONFIRMATION"})
        workflow_confirmation = workflow_state["view"].session.confirmation
        await held.interaction.confirm(workflow_session, workflow_confirmation.confirmation_id, "workflow-recovery-user")
        await _wait_for_state(held, workflow_session, {"WAITING_EXECUTION", "COMPLETE"}, timeout=20)
        await held.close()
        await held_client.aclose()

        resumed, _transport, resumed_client = _build_application(root, data_root, probes)
        await resumed.start()
        try:
            await _wait_for_state(resumed, workflow_session, {"COMPLETE"}, timeout=20)
            with connect_database(resumed.database) as connection:
                workflow_count = connection.execute(
                    "SELECT COUNT(*) FROM workflow_runs WHERE session_id=?", (workflow_session,)
                ).fetchone()[0]
            workflow_recovered = workflow_count == 1
            if not workflow_recovered:
                probes.fail(
                    "PROBE_WORKFLOW_RECOVERY_DUPLICATE",
                    phase="recovery",
                    operation="workflow_run",
                    detail=f"count={workflow_count}",
                )
        finally:
            await resumed.close()
            await resumed_client.aclose()
    return {
        "status": "PASS" if not probes.failures else "FAILED",
        "agentRunRecovery": agent_run_recovered,
        "agentConfirmationRecovery": confirmation_recovered,
        "workflowRecovery": workflow_recovered,
        "workflowRuns": workflow_count,
        "criteria": {
            "agentRunSurvivesRestart": agent_run_recovered,
            "confirmationSurvivesRestart": confirmation_recovered,
            "workflowSurvivesRestart": workflow_recovered,
            "noDuplicateWorkflow": workflow_count == 1,
        },
    }
