"""Race and control-plane pressure scenarios."""
from __future__ import annotations
import asyncio
from pathlib import Path
import tempfile
import time
from platform_kernel.database import connect_database
from ..context import ProbeRecorder
from ..environment import PROJECT
from ..probe import _build_application, _wait_for_state

async def _run_race_probe(root: Path, probes: ProbeRecorder) -> dict[str, object]:
    """Probe idempotent message admission and confirmation races."""
    with tempfile.TemporaryDirectory(prefix="kernel-race-sandbox-") as temporary:
        application, _transport, client = _build_application(root, Path(temporary), probes)
        await application.start()
        try:
            session = application.interaction.create_session("race-user").session.session_id
            same_request = "race-request"
            receipts = await asyncio.gather(
                asyncio.to_thread(
                    application.interaction.message,
                    session, same_request, "sandbox-chat", "DIALOGUE",
                ),
                asyncio.to_thread(
                    application.interaction.message,
                    session, same_request, "sandbox-chat", "DIALOGUE",
                ),
                return_exceptions=True,
            )
            with connect_database(application.database) as connection:
                request_count = connection.execute(
                    "SELECT COUNT(*) FROM message_requests WHERE session_id=? AND request_id=?",
                    (session, same_request),
                ).fetchone()[0]
            if request_count != 1 or not any(isinstance(item, dict) for item in receipts):
                probes.fail(
                    "PROBE_DUPLICATE_MESSAGE",
                    phase="race",
                    operation="message_admission",
                    detail=f"count={request_count}",
                )

            different = await asyncio.gather(
                asyncio.to_thread(application.interaction.message, session, "race-a", "sandbox-chat", "DIALOGUE"),
                asyncio.to_thread(application.interaction.message, session, "race-b", "sandbox-chat", "DIALOGUE"),
                return_exceptions=True,
            )
            different_errors = [str(item) for item in different if isinstance(item, Exception)]

            confirm_session = application.interaction.create_session("confirm-race-user").session.session_id
            await asyncio.to_thread(
                application.interaction.message,
                confirm_session, "confirm-race-message", "sandbox-exec", "AUTO",
            )
            state = await _wait_for_state(application, confirm_session, {"WAITING_CONFIRMATION"})
            confirmation = state["view"].session.confirmation
            confirmation_id = confirmation.confirmation_id if confirmation else ""
            confirmation_results = await asyncio.gather(
                application.interaction.confirm(confirm_session, confirmation_id, "confirm-race-user"),
                application.interaction.confirm(confirm_session, confirmation_id, "confirm-race-user"),
                return_exceptions=True,
            )
            await _wait_for_state(application, confirm_session, {"COMPLETE"}, timeout=20)
            with connect_database(application.database) as connection:
                workflow_count = connection.execute(
                    "SELECT COUNT(*) FROM workflow_runs WHERE session_id=?", (confirm_session,)
                ).fetchone()[0]
            if workflow_count != 1 or all(isinstance(item, Exception) for item in confirmation_results):
                probes.fail(
                    "PROBE_DUPLICATE_CONFIRMATION",
                    phase="race",
                    operation="confirmation",
                    detail=f"workflowRuns={workflow_count}",
                )
            return {
                "status": "PASS" if not probes.failures else "FAILED",
                "sameRequestRows": request_count,
                "sameRequestResults": len(receipts),
                "differentRequestErrors": len(different_errors),
                "confirmationResults": len(confirmation_results),
                "workflowRuns": workflow_count,
                "criteria": {
                    "sameRequestSingleRow": request_count == 1,
                    "confirmationSingleWorkflow": workflow_count == 1,
                    "conflictIsObservable": bool(different_errors),
                },
            }
        finally:
            await application.close()
            await client.aclose()


async def _run_control_probe(root: Path, probes: ProbeRecorder) -> dict[str, object]:
    """Exercise natural-language controls, rejection, stop and executor cancel."""
    natural_control_complete = False
    executor_cancelled = False
    executor_cancel_calls = 0
    reject_completed = False
    with tempfile.TemporaryDirectory(prefix="kernel-control-sandbox-") as temporary:
        application, _transport, client = _build_application(root, Path(temporary), probes)
        await application.start()
        try:
            reject_session = application.interaction.create_session("reject-user").session.session_id
            await asyncio.to_thread(application.interaction.message, reject_session, "reject-message", "sandbox-exec", "AUTO")
            reject_state = await _wait_for_state(application, reject_session, {"WAITING_CONFIRMATION"})
            reject_confirmation = reject_state["view"].session.confirmation
            await application.interaction.reject(
                reject_session, reject_confirmation.confirmation_id, "sandbox rejection", "reject-user"
            )
            await _wait_for_state(application, reject_session, {"COMPLETE"})
            reject_completed = True

            stop_session = application.interaction.create_session("stop-user").session.session_id
            await asyncio.to_thread(application.interaction.message, stop_session, "stop-message", "sandbox-exec", "AUTO")
            stop_state = await _wait_for_state(application, stop_session, {"WAITING_CONFIRMATION"})
            stop_confirmation = stop_state["view"].session.confirmation
            run_id = stop_confirmation.agent_run_id
            from platform_kernel.interaction.control_persistence import request_stop

            first = request_stop(application.database, stop_session, PROJECT, "stop-request", run_id, "stop-user")
            second = request_stop(application.database, stop_session, PROJECT, "stop-request", run_id, "stop-user")
            await asyncio.sleep(0.2)
            with connect_database(application.database) as connection:
                workflow_count = connection.execute(
                    "SELECT COUNT(*) FROM workflow_runs WHERE session_id IN (?, ?)",
                    (reject_session, stop_session),
                ).fetchone()[0]
            if first.get("status") != "REQUESTED" or second.get("status") != "REQUESTED" or workflow_count != 0:
                probes.fail(
                    "PROBE_CONTROL_NOT_IDEMPOTENT",
                    phase="control",
                    operation="stop",
                    detail=f"workflowRuns={workflow_count}",
                )
            natural_session = application.interaction.create_session("natural-control-user").session.session_id
            # Both writes occur before yielding to the runtime supervisor, so
            # the STOP proposal targets the durable pending AgentRun.
            await asyncio.to_thread(
                application.interaction.message,
                natural_session, "natural-start", "sandbox-exec", "AUTO",
            )
            await asyncio.to_thread(
                application.interaction.message,
                natural_session, "natural-stop", "停止", "AUTO",
            )
            proposals = application.interaction.get(natural_session).session.control_proposals
            if len(proposals) == 1 and proposals[0].get("status") == "PENDING":
                await application.interaction.confirm_control_proposal(
                    natural_session,
                    str(proposals[0]["proposal_id"]),
                    "natural-control-confirm",
                    "natural-control-user",
                )
                natural_control_complete = True

        finally:
            await application.close()
            await client.aclose()

    # A second isolated runtime holds the remote job long enough for STOP to
    # exercise the Executor cancel contract, rather than only cancelling a
    # pre-confirmation AgentRun.
    with tempfile.TemporaryDirectory(prefix="kernel-cancel-sandbox-") as temporary:
        cancel_app, cancel_transport, cancel_client = _build_application(
            root, Path(temporary), probes, fault="executor_hold"
        )
        await cancel_app.start()
        try:
            cancel_session = cancel_app.interaction.create_session("cancel-user").session.session_id
            await asyncio.to_thread(
                cancel_app.interaction.message,
                cancel_session, "cancel-start", "sandbox-exec", "AUTO",
            )
            cancel_state = await _wait_for_state(cancel_app, cancel_session, {"WAITING_CONFIRMATION"})
            cancel_confirmation = cancel_state["view"].session.confirmation
            await cancel_app.interaction.confirm(
                cancel_session, cancel_confirmation.confirmation_id, "cancel-user"
            )
            await _wait_for_state(cancel_app, cancel_session, {"WAITING_EXECUTION"}, timeout=20)
            submit_deadline = time.monotonic() + 10
            while cancel_transport.calls["submit"] < 1 and time.monotonic() < submit_deadline:
                await asyncio.sleep(0.02)
            if cancel_transport.calls["submit"] < 1:
                probes.fail(
                    "PROBE_EXECUTOR_SUBMIT_MISSING",
                    phase="control",
                    operation="cancel",
                    detail="remote job was not submitted before cancellation",
                )
            cancel_run_id = cancel_confirmation.agent_run_id
            from platform_kernel.interaction.control_persistence import request_stop

            request_stop(
                cancel_app.database, cancel_session, PROJECT,
                "cancel-request", cancel_run_id, "cancel-user",
            )
            await _wait_for_state(cancel_app, cancel_session, {"CANCELLED"}, timeout=20)
            with connect_database(cancel_app.database) as connection:
                cancelled = connection.execute(
                    "SELECT COUNT(*) FROM workflow_runs WHERE session_id=? AND status='CANCELLED'",
                    (cancel_session,),
                ).fetchone()[0]
            executor_cancel_calls = cancel_transport.calls["cancel"]
            executor_cancelled = cancelled == 1 and executor_cancel_calls >= 1
            if not executor_cancelled:
                probes.fail(
                    "PROBE_EXECUTOR_CANCEL_MISSING",
                    phase="control",
                    operation="cancel",
                    detail=f"cancelled={cancelled},calls={executor_cancel_calls}",
                )
        finally:
            await cancel_app.close()
            await cancel_client.aclose()

    return {
        "status": "PASS" if not probes.failures else "FAILED",
        "reject": "COMPLETE",
        "naturalLanguageControl": natural_control_complete,
        "stopRequestStatus": [first.get("status"), second.get("status")],
        "executorCancelCalls": executor_cancel_calls,
        "workflowRuns": workflow_count,
        "criteria": {
            "rejectCompletes": reject_completed,
            "stopIdempotent": first.get("status") == second.get("status") == "REQUESTED",
            "noWorkflowAfterControls": workflow_count == 0,
            "naturalLanguageControlCompletes": natural_control_complete,
            "executorCancelCompletes": executor_cancelled,
        },
    }
