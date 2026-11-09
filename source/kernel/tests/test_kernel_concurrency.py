from __future__ import annotations


import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import gc
import threading

import pytest
from agents.testing import ScriptedModel, ModelStep, function_call
from kernel.tests.reply_testing import submit_reply
import httpx

from platform_kernel.composition import PlatformApplication
from platform_kernel.contracts import JobStatusV6
from platform_kernel.database import (
    connect_database,
    initialize_database,
    read_session_events,
    utc_now,
)
from platform_kernel.errors import ConfirmationConflictError
import platform_kernel.interaction.owner as interaction_owner_module
from platform_kernel.interaction.persistence import create_session, reserve_turn
from platform_kernel.runtime import KernelRuntime
from platform_kernel.settings import ExecutableModelDefinition, ServiceBinding
from platform_kernel.task_supervisor import KernelTaskSupervisor
from platform_kernel.workflow_execution.models import WorkflowTickResult
from platform_kernel.workflow_execution.execution_client import ExecutionClientError
from platform_kernel.workflow_execution.metrics import reconciliation_required_count
from platform_kernel.agent_runtime.persistence.provider_requests import (
    list_provider_requests,
    mark_provider_protocol_failure,
)
from platform_kernel.workflow_execution.owner import WorkflowExecutionOwner
from platform_kernel.workflow_execution.persistence.queue import service_target_digest


class _UnusedIntentRouter:
    async def route(self, **_kwargs):
        raise AssertionError("intent router must not run without route requests")


def _runtime(interaction, agent, workflow):
    if not hasattr(interaction, "requested_routes"):
        interaction.requested_routes = lambda _limit=None: ()
    if not hasattr(interaction, "has_pending_decision"):
        interaction.has_pending_decision = lambda: False
    return KernelRuntime(
        interaction, agent, workflow, _UnusedIntentRouter(), task_concurrency=8
    )


def test_kernel_task_supervisor_bounds_one_hundred_fifo_items() -> None:
    async def exercise() -> None:
        supervisor = KernelTaskSupervisor(8)
        started: list[int] = []
        completed = 0

        async def work(index: int) -> None:
            nonlocal completed
            started.append(index)
            await asyncio.sleep(0)
            completed += 1

        for index in range(100):
            assert supervisor.queue(
                "resume", str(index), lambda index=index: work(index)
            )
        assert not supervisor.queue("resume", "overflow", lambda: work(100))
        supervisor.pump()
        while completed < 100:
            assert sum(supervisor.counts().values()) <= 8
            await asyncio.sleep(0)
            supervisor.reap()
            supervisor.pump()
        assert started == list(range(100))
        await supervisor.close()

    asyncio.run(exercise())


def test_kernel_task_supervisor_shutdown_cancels_hung_work_after_grace() -> None:
    async def exercise() -> None:
        supervisor = KernelTaskSupervisor(1)
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def hung() -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        assert supervisor.queue("route", "hung", hung)
        supervisor.pump()
        await asyncio.wait_for(started.wait(), timeout=1)
        await supervisor.close(grace_seconds=0.01)
        assert cancelled.is_set()
        assert supervisor.counts() == {
            "route": 0, "start": 0, "resume": 0, "decision": 0,
        }

    asyncio.run(exercise())


def test_concurrency_one_round_robins_sustained_decisions_and_route_work() -> None:
    class Route:
        def __init__(self, agent_run_id: str) -> None:
            self.agent_run_id = agent_run_id

    class Interaction:
        def __init__(self) -> None:
            self.pending_decisions = 25
            self.routes = [Route("route-a"), Route("route-b")]

        def has_pending_decision(self) -> bool:
            return self.pending_decisions > 0

        def requested_routes(self, limit=None):
            selected = tuple(self.routes[:limit])
            del self.routes[:len(selected)]
            return selected

        def requested_starts(self, _limit=None):
            return ()

    class Agent:
        def prune_traces(self):
            return 0

    class Workflow:
        pass

    async def exercise() -> None:
        interaction = Interaction()
        runtime = KernelRuntime(
            interaction, Agent(), Workflow(), _UnusedIntentRouter(), task_concurrency=1
        )
        admitted: list[str] = []

        async def run_decision() -> None:
            admitted.append("decision")
            interaction.pending_decisions -= 1

        async def run_route(agent_run_id: str) -> None:
            admitted.append(agent_run_id)

        runtime._run_decision = run_decision
        runtime._run_route = run_route
        for _ in range(100):
            runtime._tasks.reap()
            runtime._schedule_fair_work()
            await asyncio.sleep(0)
            if (
                interaction.pending_decisions == 0
                and not interaction.routes
                and not runtime._tasks.counts()["decision"]
                and not runtime._tasks.counts()["route"]
            ):
                break
        runtime._tasks.reap()
        await runtime._tasks.close()
        assert admitted[:4] == ["route-a", "decision", "route-b", "decision"]
        assert admitted.count("decision") == 25
        assert admitted.count("route-a") == 1
        assert admitted.count("route-b") == 1

    asyncio.run(exercise())


def test_concurrent_turns_create_only_one_agent_run(kernel_settings) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def finish(_call):
        started.set()
        await release.wait()
        return ModelStep(
            output=[
                submit_reply('done')
            ]
        )

    model = ScriptedModel([ModelStep.respond(finish)])
    application = PlatformApplication(
        settings=kernel_settings("knowledge"),
        model=model,
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: None)),
    )
    session_id = application.interaction.create_session().session.session_id

    async def exercise() -> None:
        accepted = asyncio.create_task(
            application.interaction.turn(session_id, "first")
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        with pytest.raises(
            ConfirmationConflictError, match="AGENT_EXECUTION_IN_PROGRESS"
        ):
            await application.interaction.turn(session_id, "second")
        release.set()
        await accepted

    asyncio.run(exercise())
    with connect_database(application.database) as connection:
        run_count = connection.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0]
        messages = connection.execute(
            "SELECT role, content FROM messages ORDER BY message_id"
        ).fetchall()
    assert run_count == 1
    assert [tuple(row) for row in messages] == [
        ("user", "first"),
        ("assistant", "done"),
    ]


def test_concurrent_turn_reservations_have_a_single_transactional_claim(tmp_path) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    create_session(database, "session", "project")
    barrier = threading.Barrier(2)

    def reserve(index: int):
        barrier.wait()
        return reserve_turn(
            database,
            "session",
            f"message-{index}",
            f"run-{index}",
            "project",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(reserve, index) for index in range(2)]
    failures = [future.exception() for future in futures if future.exception()]
    assert len(failures) == 1
    assert isinstance(failures[0], ConfirmationConflictError)
    assert str(failures[0]) == "AGENT_EXECUTION_IN_PROGRESS"
    with connect_database(database) as connection:
        session = connection.execute(
            "SELECT goal_revision, pending_agent_run_id FROM sessions"
        ).fetchone()
        messages = connection.execute(
            "SELECT role, content FROM messages ORDER BY message_id"
        ).fetchall()
    assert session["goal_revision"] == 1
    assert session["pending_agent_run_id"] in {"run-0", "run-1"}
    assert len(messages) == 1
    assert messages[0]["role"] == "user"


def test_confirm_winning_race_prevents_a_new_turn(
    kernel_settings, monkeypatch
) -> None:
    model = ScriptedModel(
        [
            ModelStep(output=[function_call(
                "inspect_data_version", {"version_id": "a"}, call_id="inspect",
            )]),
            ModelStep(
                output=[
                    function_call(
                        "record_plan",
                        {
                            "goal": "run",
                            "steps": [
                                {
                                    "step_id": "run",
                                    "tool": "run_summary",
                                    "purpose": "run",
                                }
                            ],
                            "reason": "requested",
                        },
                        call_id="plan",
                    )
                ]
            ),
            ModelStep(
                output=[
                    function_call(
                        "run_summary", {"resource_id": "a"}, call_id="action"
                    )
                ]
            ),
        ]
    )
    application = PlatformApplication(
        settings=kernel_settings(),
        model=model,
        client=httpx.AsyncClient(
                transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"data": {
                    "status": "OK", "versionId": "a",
                    "datasetId": "dataset-a", "semanticId": "semantics-a",
                    "objects": [{"objectId": "object-a", "role": "raw",
                        "mediaType": "text/csv", "format": "csv", "size": 8,
                    "checksum": "sha256:" + "a" * 64}],
            }}))
        ),
    )
    session = application.interaction.create_session().session
    waiting = asyncio.run(
        application.interaction.turn(
            session.session_id, "run", dialogue_policy="EXECUTION_PROPOSAL",
        )
    ).session
    confirmation_id = waiting.confirmation.confirmation_id
    turn_ready = threading.Event()
    release_turn = threading.Event()
    original_reserve_turn = interaction_owner_module.reserve_turn

    def delayed_reserve_turn(*args, **kwargs):
        turn_ready.set()
        assert release_turn.wait(timeout=2)
        return original_reserve_turn(*args, **kwargs)

    monkeypatch.setattr(
        interaction_owner_module, "reserve_turn", delayed_reserve_turn
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        turn = executor.submit(
            asyncio.run,
            application.interaction.turn(
                session.session_id, "replacement"
            ),
        )
        assert turn_ready.wait(timeout=2)
        confirmed = executor.submit(
            asyncio.run,
            application.interaction.confirm(
                session.session_id, confirmation_id
            ),
        )
        try:
            confirmed.result(timeout=2)
        finally:
            release_turn.set()
        with pytest.raises(
            ConfirmationConflictError, match="AGENT_EXECUTION_IN_PROGRESS"
        ):
            turn.result(timeout=2)

    current = application.interaction.get(session.session_id)
    assert current.session.goal_revision == 1
    assert current.session.agent_run_id == waiting.agent_run_id
    assert current.agent_steps[-1] == "WAITING_EXECUTION"
    assert len(current.session.workflow_run_ids) == 1
    with connect_database(application.database) as connection:
        messages = connection.execute(
            "SELECT role, content FROM messages ORDER BY message_id"
        ).fetchall()
        assert [tuple(row) for row in messages] == [("user", "run")]
        assert connection.execute(
            "SELECT COUNT(*) FROM workflow_runs"
        ).fetchone()[0] == 1


@pytest.mark.parametrize(
    ("dispatch_concurrency", "expected_peak"),
    [(8, 8), (3, 3)],
)
def test_dispatch_claims_32_and_respects_local_dispatch_capacity(
    tmp_path, dispatch_concurrency, expected_peak
) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    _seed_dispatches(database, 100)

    class Client:
        def __init__(self) -> None:
            self.active = 0
            self.peak = 0
            self.release = asyncio.Event()
            self.request_ids = []

        async def submit(self, _service, request):
            self.request_ids.append(request.request_id)
            self.active += 1
            self.peak = max(self.peak, self.active)
            if self.active == expected_peak:
                self.release.set()
            await self.release.wait()
            await asyncio.sleep(0)
            self.active -= 1
            return JobStatusV6(
                job_id=f"job-{request.request_id}",
                request_id=request.request_id,
                status="QUEUED",
            )

    client = Client()
    owner = WorkflowExecutionOwner(
        database,
        (_seed_model(),),
        (ServiceBinding("executor", "http://executor"),),
        client,
        dispatch_concurrency=dispatch_concurrency,
    )
    async def collect():
        return [result async for result in owner.tick()]

    results = asyncio.run(collect())

    assert results == [WorkflowTickResult(1, ())] * 32
    assert client.peak == expected_peak
    assert client.request_ids == [f"run-{index:03d}" for index in range(32)]
    with connect_database(database) as connection:
        states = dict(
            connection.execute(
                "SELECT state, COUNT(*) FROM workflow_dispatches GROUP BY state"
            )
        )
        claimed = connection.execute(
            "SELECT COUNT(*) FROM workflow_dispatches WHERE claim_state IS NOT NULL"
        ).fetchone()[0]
    assert states == {"REQUESTED": 68, "SUBMITTED": 32}
    assert claimed == 0


@pytest.mark.parametrize("dispatch_state", ["REQUESTED", "SUBMIT_UNKNOWN", "SUBMITTED"])
@pytest.mark.parametrize("aged", [False, True])
def test_frozen_execution_target_blocks_all_cross_endpoint_io_and_recovers(
    tmp_path, dispatch_state, aged
) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    _seed_dispatches(database, 1)
    if not aged:
        with connect_database(database) as connection:
            connection.execute(
                "UPDATE workflow_runs SET created_at=? WHERE run_id='run-000'",
                (utc_now(),),
            )
    if dispatch_state != "REQUESTED":
        with connect_database(database) as connection:
            connection.execute(
                "UPDATE workflow_dispatches SET state=?,remote_job_id=? "
                "WHERE workflow_run_id='run-000'",
                (
                    dispatch_state,
                    "job-run-000" if dispatch_state == "SUBMITTED" else None,
                ),
            )
            if dispatch_state == "SUBMITTED":
                connection.execute(
                    "UPDATE workflow_runs SET remote_job_id='job-run-000',status='RUNNING' "
                    "WHERE run_id='run-000'"
                )

    class Client:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def submit(self, _service, _request):
            self.calls.append("submit")
            return JobStatusV6("job-run-000", "run-000", "QUEUED")

        async def get_by_request(self, _service, _project_id, _run_id):
            self.calls.append("get_by_request")
            return JobStatusV6("job-run-000", "run-000", "QUEUED")

        async def get(self, _service, _job_id):
            self.calls.append("get")
            return JobStatusV6("job-run-000", "run-000", "QUEUED")

    client = Client()

    async def tick(service_url: str) -> None:
        owner = WorkflowExecutionOwner(
            database,
            (_seed_model(),),
            (ServiceBinding("executor", service_url),),
            client,
        )
        async for _ in owner.tick():
            pass

    asyncio.run(tick("http://different-executor"))
    assert client.calls == []
    with connect_database(database) as connection:
        dispatch = connection.execute(
            "SELECT state,last_communication_error FROM workflow_dispatches "
            "WHERE workflow_run_id='run-000'"
        ).fetchone()
        frozen = connection.execute(
            "SELECT target_digest FROM workflow_service_bindings "
            "WHERE workflow_run_id='run-000'"
        ).fetchone()[0]
        connection.execute(
            "UPDATE workflow_dispatches SET next_poll_at='0000' "
            "WHERE workflow_run_id='run-000'"
        )
    assert tuple(dispatch) == (
        dispatch_state,
        (
            "RECONCILIATION_REQUIRED"
            if aged and dispatch_state != "REQUESTED"
            else "WORKFLOW_EXECUTION_TARGET_MISMATCH"
        ),
    )
    assert frozen == service_target_digest(ServiceBinding("executor", "http://executor"))
    assert "http://" not in frozen

    asyncio.run(tick("http://executor"))
    assert client.calls == [{
        "REQUESTED": "submit",
        "SUBMIT_UNKNOWN": "get_by_request",
        "SUBMITTED": "get",
    }[dispatch_state]]
    with connect_database(database) as connection:
        assert connection.execute(
            "SELECT last_communication_error FROM workflow_dispatches "
            "WHERE workflow_run_id='run-000'"
        ).fetchone()[0] is None


def test_startup_rejects_v32_without_implicit_upgrade(tmp_path) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    _seed_dispatches(database, 1)
    with connect_database(database) as connection:
        connection.execute("DROP TABLE workflow_service_bindings")
        connection.execute("UPDATE schema_version SET version=32")

    with pytest.raises(ValueError, match="SCHEMA_VERSION_UNSUPPORTED"):
        initialize_database(database)

    with connect_database(database) as connection:
        assert connection.execute("SELECT version FROM schema_version").fetchone()[0] == 32
        assert connection.execute(
            "SELECT COUNT(*) FROM workflow_dispatches"
        ).fetchone()[0] == 1
    assert not list(tmp_path.glob("kernel.sqlite3.v32-preupgrade-*.sqlite3"))


@pytest.mark.parametrize("dispatch_state", ["SUBMIT_UNKNOWN", "SUBMITTED"])
def test_old_unknown_dispatch_exposes_reconciliation_without_busy_polling(
    tmp_path, dispatch_state
) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    _seed_dispatches(database, 1)
    with connect_database(database) as connection:
        connection.execute(
            "UPDATE workflow_dispatches SET state=?,remote_job_id=? "
            "WHERE workflow_run_id='run-000'",
            (dispatch_state, "job-run-000" if dispatch_state == "SUBMITTED" else None),
        )
        if dispatch_state == "SUBMITTED":
            connection.execute(
                "UPDATE workflow_runs SET remote_job_id='job-run-000',status='RUNNING' "
                "WHERE run_id='run-000'"
            )

    class Client:
        available = False

        async def get_by_request(self, *_):
            return self.status()

        async def get(self, *_):
            return self.status()

        def status(self):
            if not self.available:
                raise ExecutionClientError("offline")
            return JobStatusV6(
                job_id="job-run-000", request_id="run-000", status="SUCCEEDED"
            )

    client = Client()
    owner = WorkflowExecutionOwner(
        database, (_seed_model(),), (ServiceBinding("executor", "http://executor"),), client,
    )

    async def tick():
        return [result async for result in owner.tick()]

    assert asyncio.run(tick()) == [WorkflowTickResult(1, ())]
    with connect_database(database) as connection:
        row = connection.execute(
            "SELECT state,last_communication_error,next_poll_at FROM workflow_dispatches"
        ).fetchone()
        assert row["state"] == dispatch_state
        assert row["last_communication_error"] == "RECONCILIATION_REQUIRED"
        delay = (
            datetime.fromisoformat(row["next_poll_at"].replace("Z", "+00:00"))
            - datetime.now(timezone.utc)
        ).total_seconds()
        assert 25 < delay <= 30
        assert len(read_session_events(connection, "session-000", "workflow.communication_changed")) == 1
        connection.execute(
            "UPDATE workflow_dispatches SET next_poll_at='0000' WHERE workflow_run_id='run-000'"
        )
    assert reconciliation_required_count(database) == 1
    assert asyncio.run(tick()) == [WorkflowTickResult(1, ())]
    with connect_database(database) as connection:
        assert len(read_session_events(connection, "session-000", "workflow.communication_changed")) == 1
        connection.execute(
            "UPDATE workflow_dispatches SET next_poll_at='0000' WHERE workflow_run_id='run-000'"
        )

    client.available = True
    assert asyncio.run(tick()) == [WorkflowTickResult(1, ("agent-000",))]
    assert reconciliation_required_count(database) == 0
    with connect_database(database) as connection:
        events = read_session_events(connection, "session-000", "workflow.communication_changed")
    assert [event["data"]["error_code"] for event in events] == [
        "RECONCILIATION_REQUIRED", None,
    ]


def test_unknown_provider_attempt_does_not_project_placeholder_zero_usage(tmp_path) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    _seed_dispatches(database, 1)
    with connect_database(database) as connection:
        connection.execute(
            """INSERT INTO provider_requests(
                   provider_request_id,agent_run_id,stage,provider,model,protocol,
                   attempt,outcome,input_tokens,output_tokens,total_tokens,
                   latency_ms,created_at)
               VALUES('unknown','agent-000','AGENT','provider','model','responses',
                      1,'UNKNOWN',0,0,0,0,'2026-10-24T00:00:00Z')"""
        )
    (item,) = list_provider_requests(database, "agent-000", "project")
    assert item["outcome"] == "UNKNOWN"
    assert all(
        item[field] is None
        for field in ("input_tokens", "output_tokens", "total_tokens", "latency_ms")
    )
    assert mark_provider_protocol_failure(database, "agent-000") is None


def test_definition_mismatch_keeps_same_pending_run_and_resumes_without_attempt(
    tmp_path,
) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    _seed_dispatches(database, 1)

    class Client:
        digest = "sha256:" + "0" * 64
        submits = 0

        async def get_definition_set_digest(self, _service, _project_id):
            return self.digest

        async def submit(self, _service, request):
            self.submits += 1
            return JobStatusV6(
                job_id="job-1", request_id=request.request_id, status="QUEUED"
            )

    client = Client()
    expected = "sha256:" + "1" * 64
    owner = WorkflowExecutionOwner(
        database,
        (_seed_model(),),
        (ServiceBinding("executor", "http://executor"),),
        client,
        expected_definition_set_digest=expected,
    )

    async def collect():
        return [result async for result in owner.tick()]

    assert asyncio.run(collect()) == [WorkflowTickResult(1, ())]
    with connect_database(database) as connection:
        dispatch = connection.execute(
            "SELECT * FROM workflow_dispatches WHERE workflow_run_id='run-000'"
        ).fetchone()
        run = connection.execute(
            "SELECT status, attempt FROM workflow_runs WHERE run_id='run-000'"
        ).fetchone()
        assert dispatch["state"] == "REQUESTED"
        assert dispatch["claim_state"] is None
        assert dispatch["last_communication_error"] == "EXECUTOR_DEFINITION_SET_MISMATCH"
        delay = (
            datetime.fromisoformat(dispatch["next_poll_at"].replace("Z", "+00:00"))
            - datetime.now(timezone.utc)
        ).total_seconds()
        assert 25 < delay <= 30
        assert tuple(run) == ("QUEUED", 1)
        connection.execute(
            "UPDATE workflow_dispatches SET next_poll_at='0000' WHERE workflow_run_id='run-000'"
        )
    assert client.submits == 0

    client.digest = expected
    assert asyncio.run(collect()) == [WorkflowTickResult(1, ())]
    assert client.submits == 1
    with connect_database(database) as connection:
        dispatch = connection.execute(
            "SELECT state, last_communication_error FROM workflow_dispatches "
            "WHERE workflow_run_id='run-000'"
        ).fetchone()
        run = connection.execute(
            "SELECT status, attempt FROM workflow_runs WHERE run_id='run-000'"
        ).fetchone()
    assert tuple(dispatch) == ("SUBMITTED", None)
    assert tuple(run) == ("QUEUED", 1)


def test_unknown_submit_rechecks_definition_before_new_remote_action(tmp_path) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    _seed_dispatches(database, 1)
    with connect_database(database) as connection:
        connection.execute(
            "UPDATE workflow_dispatches SET state='SUBMIT_UNKNOWN' WHERE workflow_run_id='run-000'"
        )

    class Client:
        digest = "sha256:" + "0" * 64
        submits = 0
        lookups = 0

        async def get_by_request(self, _service, _project_id, _run_id):
            self.lookups += 1
            return None

        async def get_definition_set_digest(self, _service, _project_id):
            return self.digest

        async def submit(self, _service, request):
            self.submits += 1
            return JobStatusV6(
                job_id="job-1", request_id=request.request_id, status="QUEUED"
            )

    client = Client()
    owner = WorkflowExecutionOwner(
        database, (_seed_model(),), (ServiceBinding("executor", "http://executor"),), client,
        expected_definition_set_digest="sha256:" + "1" * 64,
    )

    async def collect():
        return [result async for result in owner.tick()]

    assert asyncio.run(collect()) == [WorkflowTickResult(1, ())]
    with connect_database(database) as connection:
        row = connection.execute(
            "SELECT state, last_communication_error FROM workflow_dispatches"
        ).fetchone()
        assert tuple(row) == ("SUBMIT_UNKNOWN", "EXECUTOR_DEFINITION_SET_MISMATCH")
        connection.execute("UPDATE workflow_dispatches SET next_poll_at='0000'")
    assert client.submits == 0

    client.digest = "sha256:" + "1" * 64
    assert asyncio.run(collect()) == [WorkflowTickResult(1, ())]
    assert client.lookups == 2
    assert client.submits == 1
    with connect_database(database) as connection:
        assert connection.execute(
            "SELECT state FROM workflow_dispatches"
        ).fetchone()[0] == "SUBMITTED"


@pytest.mark.parametrize("dispatch_state", ["REQUESTED", "SUBMIT_UNKNOWN"])
@pytest.mark.parametrize(
    "revocation",
    ["rejected", "superseded", "action_changed", "arguments_changed", "task_changed"],
)
def test_new_submit_rechecks_approved_action_and_task(
    tmp_path, dispatch_state, revocation
) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    _seed_dispatches(database, 1)
    _seed_dispatch_confirmation(database)
    with connect_database(database) as connection:
        connection.execute(
            "UPDATE workflow_dispatches SET state=? WHERE workflow_run_id='run-000'",
            (dispatch_state,),
        )
        if revocation == "rejected":
            connection.execute(
                "UPDATE confirmations SET status='REJECTED',decision='REJECT' "
                "WHERE confirmation_id='approval-000'"
            )
        elif revocation == "superseded":
            connection.execute(
                "UPDATE confirmations SET status='SUPERSEDED' "
                "WHERE confirmation_id='approval-000'"
            )
        elif revocation == "action_changed":
            connection.execute(
                "UPDATE confirmation_actions SET workflow_checksum='changed' "
                "WHERE confirmation_id='approval-000'"
            )
        elif revocation == "arguments_changed":
            connection.execute(
                "UPDATE confirmation_actions SET arguments_json='{\"changed\":true}' "
                "WHERE confirmation_id='approval-000'"
            )
        else:
            connection.execute(
                "UPDATE tasks SET revision=2 WHERE task_id='task-000'"
            )

    class Client:
        submits = 0

        async def get_by_request(self, _service, _project_id, _run_id):
            return None

        async def submit(self, _service, _request):
            self.submits += 1
            raise AssertionError("revoked approval must not create a Job")

    client = Client()
    owner = WorkflowExecutionOwner(
        database, (_seed_model(),), (ServiceBinding("executor", "http://executor"),),
        client,
    )

    async def tick():
        return [result async for result in owner.tick()]

    assert asyncio.run(tick()) == [WorkflowTickResult(1, ())]
    assert client.submits == 0
    with connect_database(database) as connection:
        dispatch = connection.execute(
            "SELECT state,last_communication_error,next_poll_at "
            "FROM workflow_dispatches WHERE workflow_run_id='run-000'"
        ).fetchone()
        assert dispatch["state"] == dispatch_state
        assert dispatch["last_communication_error"] == "WORKFLOW_CONFIRMATION_NOT_CURRENT"
        assert 25 < (
            datetime.fromisoformat(dispatch["next_poll_at"].replace("Z", "+00:00"))
            - datetime.now(timezone.utc)
        ).total_seconds() <= 30
        events = read_session_events(
            connection, "session-000", "workflow.communication_changed"
        )
    assert events[-1]["data"]["error_code"] == "WORKFLOW_CONFIRMATION_NOT_CURRENT"


@pytest.mark.parametrize(
    ("status", "decision_state"),
    [("PENDING", "RUNNING"), ("PENDING", "REQUESTED"), ("CONFIRMED", None)],
)
def test_new_submit_allows_approved_confirmation_processing_window(
    tmp_path, status, decision_state
) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    _seed_dispatches(database, 1)
    _seed_dispatch_confirmation(database, status=status, decision_state=decision_state)

    class Client:
        async def submit(self, _service, request):
            return JobStatusV6(
                job_id="job-1", request_id=request.request_id, status="QUEUED"
            )

    owner = WorkflowExecutionOwner(
        database, (_seed_model(),), (ServiceBinding("executor", "http://executor"),),
        Client(),
    )

    async def tick():
        return [result async for result in owner.tick()]

    assert asyncio.run(tick()) == [WorkflowTickResult(1, ())]
    with connect_database(database) as connection:
        assert connection.execute(
            "SELECT state FROM workflow_dispatches"
        ).fetchone()[0] == "SUBMITTED"


def test_approval_revoked_during_definition_lookup_prevents_submit(tmp_path) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    _seed_dispatches(database, 1)
    _seed_dispatch_confirmation(database)
    with connect_database(database) as connection:
        connection.execute(
            "UPDATE workflow_dispatches SET state='SUBMIT_UNKNOWN' "
            "WHERE workflow_run_id='run-000'"
        )

    class Client:
        async def get_by_request(self, _service, _project_id, _run_id):
            return None

        async def get_definition_set_digest(self, _service, _project_id):
            with connect_database(database) as connection:
                connection.execute(
                    "UPDATE confirmations SET status='SUPERSEDED' "
                    "WHERE confirmation_id='approval-000'"
                )
            return "sha256:" + "1" * 64

        async def submit(self, _service, _request):
            raise AssertionError("approval changed during I/O")

    owner = WorkflowExecutionOwner(
        database, (_seed_model(),), (ServiceBinding("executor", "http://executor"),),
        Client(), expected_definition_set_digest="sha256:" + "1" * 64,
    )

    async def tick():
        return [result async for result in owner.tick()]

    assert asyncio.run(tick()) == [WorkflowTickResult(1, ())]
    with connect_database(database) as connection:
        assert tuple(connection.execute(
            "SELECT state,last_communication_error FROM workflow_dispatches"
        ).fetchone()) == ("SUBMIT_UNKNOWN", "WORKFLOW_CONFIRMATION_NOT_CURRENT")


def test_old_unknown_submit_prioritizes_reconciliation_over_revoked_approval(
    tmp_path,
) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    _seed_dispatches(database, 1)
    _seed_dispatch_confirmation(database)
    with connect_database(database) as connection:
        connection.execute(
            "UPDATE workflow_dispatches SET state='SUBMIT_UNKNOWN' "
            "WHERE workflow_run_id='run-000'"
        )
        connection.execute(
            "UPDATE workflow_runs SET created_at='2026-01-01T00:00:00Z' "
            "WHERE run_id='run-000'"
        )
        connection.execute(
            "UPDATE confirmations SET status='SUPERSEDED' "
            "WHERE confirmation_id='approval-000'"
        )

    class Client:
        async def get_by_request(self, _service, _project_id, _run_id):
            return None

        async def submit(self, _service, _request):
            raise AssertionError("old unknown result must not be resubmitted")

    owner = WorkflowExecutionOwner(
        database, (_seed_model(),), (ServiceBinding("executor", "http://executor"),),
        Client(),
    )

    async def tick():
        return [result async for result in owner.tick()]

    assert asyncio.run(tick()) == [WorkflowTickResult(1, ())]
    with connect_database(database) as connection:
        assert tuple(connection.execute(
            "SELECT state,last_communication_error FROM workflow_dispatches"
        ).fetchone()) == ("SUBMIT_UNKNOWN", "RECONCILIATION_REQUIRED")
    assert reconciliation_required_count(database) == 1


@pytest.mark.parametrize("dispatch_state", ["REQUESTED", "SUBMIT_UNKNOWN"])
@pytest.mark.parametrize(
    "revocation",
    [
        "action_rejected", "arguments_changed", "arguments_corrupt",
        "confirmation_rejected", "task_changed",
    ],
)
def test_direct_confirmation_rechecks_persisted_action_before_submit(
    tmp_path, dispatch_state, revocation
) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    _seed_dispatches(database, 1)
    _seed_dispatch_confirmation(database, status="PENDING", decision_state=None)
    with connect_database(database) as connection:
        connection.execute(
            "UPDATE workflow_runs SET confirmation_id=NULL WHERE run_id='run-000'"
        )
        connection.execute(
            "UPDATE confirmations SET decision=NULL WHERE confirmation_id='approval-000'"
        )
        connection.execute(
            "UPDATE workflow_dispatches SET state=? WHERE workflow_run_id='run-000'",
            (dispatch_state,),
        )
        if revocation == "action_rejected":
            connection.execute(
                "UPDATE agent_actions SET status='REJECTED' WHERE agent_run_id='agent-000'"
            )
        elif revocation == "arguments_changed":
            connection.execute(
                "UPDATE agent_actions SET arguments_json='{\"changed\":true}' "
                "WHERE agent_run_id='agent-000'"
            )
        elif revocation == "arguments_corrupt":
            connection.execute(
                "UPDATE agent_actions SET arguments_json='{' "
                "WHERE agent_run_id='agent-000'"
            )
        elif revocation == "confirmation_rejected":
            connection.execute(
                "UPDATE confirmations SET status='REJECTED',decision='REJECT' "
                "WHERE confirmation_id='approval-000'"
            )
        else:
            connection.execute("UPDATE tasks SET revision=2 WHERE task_id='task-000'")

    class Client:
        async def get_by_request(self, _service, _project_id, _run_id):
            return None

        async def submit(self, _service, _request):
            raise AssertionError("revoked direct action must not be submitted")

    owner = WorkflowExecutionOwner(
        database, (_seed_model(),), (ServiceBinding("executor", "http://executor"),),
        Client(),
    )

    async def tick():
        return [result async for result in owner.tick()]

    assert asyncio.run(tick()) == [WorkflowTickResult(1, ())]
    with connect_database(database) as connection:
        assert tuple(connection.execute(
            "SELECT state,last_communication_error FROM workflow_dispatches"
        ).fetchone()) == (dispatch_state, "WORKFLOW_CONFIRMATION_NOT_CURRENT")


def test_direct_confirmation_pending_row_and_approved_action_can_submit(tmp_path) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    _seed_dispatches(database, 1)
    _seed_dispatch_confirmation(database, status="PENDING", decision_state=None)
    with connect_database(database) as connection:
        connection.execute(
            "UPDATE workflow_runs SET confirmation_id=NULL WHERE run_id='run-000'"
        )
        connection.execute(
            "UPDATE confirmations SET decision=NULL WHERE confirmation_id='approval-000'"
        )

    class Client:
        async def submit(self, _service, request):
            return JobStatusV6(job_id="job-1", request_id=request.request_id, status="QUEUED")

    owner = WorkflowExecutionOwner(
        database, (_seed_model(),), (ServiceBinding("executor", "http://executor"),),
        Client(),
    )

    async def tick():
        return [result async for result in owner.tick()]

    assert asyncio.run(tick()) == [WorkflowTickResult(1, ())]
    with connect_database(database) as connection:
        assert connection.execute(
            "SELECT state FROM workflow_dispatches"
        ).fetchone()[0] == "SUBMITTED"


def test_known_remote_job_reconciles_after_approval_revoked(tmp_path) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    _seed_dispatches(database, 1)
    _seed_dispatch_confirmation(database)
    with connect_database(database) as connection:
        connection.execute(
            "UPDATE workflow_dispatches SET state='SUBMIT_UNKNOWN' "
            "WHERE workflow_run_id='run-000'"
        )
        connection.execute(
            "UPDATE confirmations SET status='SUPERSEDED' "
            "WHERE confirmation_id='approval-000'"
        )

    class Client:
        async def get_by_request(self, _service, _project_id, run_id):
            return JobStatusV6(job_id="job-1", request_id=run_id, status="SUCCEEDED")

        async def submit(self, _service, _request):
            raise AssertionError("known Job must be reconciled, never resubmitted")

    owner = WorkflowExecutionOwner(
        database, (_seed_model(),), (ServiceBinding("executor", "http://executor"),),
        Client(),
    )

    async def tick():
        return [result async for result in owner.tick()]

    assert asyncio.run(tick()) == [WorkflowTickResult(1, ("agent-000",))]


@pytest.mark.parametrize("dispatch_state", ["REQUESTED", "SUBMIT_UNKNOWN"])
@pytest.mark.parametrize(
    "mismatch", ["missing", "model_version", "workflow_checksum", "executor_id", "service"]
)
def test_submit_rechecks_frozen_local_model_before_new_remote_action(
    tmp_path, dispatch_state, mismatch
) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    _seed_dispatches(database, 1)
    if dispatch_state == "SUBMIT_UNKNOWN":
        with connect_database(database) as connection:
            connection.execute("UPDATE workflow_dispatches SET state='SUBMIT_UNKNOWN'")

    class Client:
        submits = 0

        async def get_by_request(self, _service, _project_id, _run_id):
            return None

        async def submit(self, _service, request):
            self.submits += 1
            return JobStatusV6(
                job_id="job-1", request_id=request.request_id, status="QUEUED"
            )

    client = Client()
    if mismatch == "missing":
        incompatible = ()
    elif mismatch == "service":
        incompatible = (_seed_model(),)
    else:
        incompatible = (replace(_seed_model(), **{mismatch: "changed"}),)
    services = () if mismatch == "service" else (
        ServiceBinding("executor", "http://executor"),
    )

    async def tick(owner):
        return [result async for result in owner.tick()]

    blocked = WorkflowExecutionOwner(
        database, incompatible, services, client,
    )
    assert asyncio.run(tick(blocked)) == [WorkflowTickResult(1, ())]
    assert client.submits == 0
    with connect_database(database) as connection:
        row = connection.execute(
            "SELECT state, last_communication_error, next_poll_at "
            "FROM workflow_dispatches"
        ).fetchone()
        assert tuple(row)[:2] == (dispatch_state, "WORKFLOW_DEFINITION_UNAVAILABLE")
        delay = (
            datetime.fromisoformat(row["next_poll_at"].replace("Z", "+00:00"))
            - datetime.now(timezone.utc)
        ).total_seconds()
        assert 25 < delay <= 30
        connection.execute("UPDATE workflow_dispatches SET next_poll_at='0000'")

    compatible = WorkflowExecutionOwner(
        database, (_seed_model(),),
        (ServiceBinding("executor", "http://executor"),), client,
    )
    assert asyncio.run(tick(compatible)) == [WorkflowTickResult(1, ())]
    assert client.submits == 1
    with connect_database(database) as connection:
        assert connection.execute(
            "SELECT state FROM workflow_dispatches"
        ).fetchone()[0] == "SUBMITTED"


def test_known_remote_job_reconciles_even_without_local_model(tmp_path) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    _seed_dispatches(database, 1)
    with connect_database(database) as connection:
        connection.execute("UPDATE workflow_dispatches SET state='SUBMIT_UNKNOWN'")

    class Client:
        async def get_by_request(self, _service, _project_id, run_id):
            return JobStatusV6(
                job_id="job-1", request_id=run_id, status="SUCCEEDED"
            )

        async def submit(self, _service, _request):
            raise AssertionError("a known remote Job must not be re-submitted")

    owner = WorkflowExecutionOwner(
        database, (), (ServiceBinding("executor", "http://executor"),), Client(),
    )

    async def tick():
        return [result async for result in owner.tick()]

    assert asyncio.run(tick()) == [WorkflowTickResult(1, ("agent-000",))]
    with connect_database(database) as connection:
        assert connection.execute(
            "SELECT state FROM workflow_dispatches"
        ).fetchone()[0] == "SUCCEEDED"


def test_unknown_submit_cancellation_during_digest_check_prevents_reissue(tmp_path) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    _seed_dispatches(database, 1)
    with connect_database(database) as connection:
        connection.execute("UPDATE workflow_dispatches SET state='SUBMIT_UNKNOWN'")

    class Controls:
        stopping = False

        def requested(self, _agent_run_id):
            return self.stopping

    controls = Controls()

    class Client:
        submits = 0

        async def get_by_request(self, _service, _project_id, _run_id):
            return None

        async def get_definition_set_digest(self, _service, _project_id):
            controls.stopping = True
            return "sha256:" + "1" * 64

        async def submit(self, _service, _request):
            self.submits += 1
            raise AssertionError("submit after cancellation")

    client = Client()
    owner = WorkflowExecutionOwner(
        database, (_seed_model(),), (ServiceBinding("executor", "http://executor"),), client,
        controls=controls,
        expected_definition_set_digest="sha256:" + "1" * 64,
    )

    async def collect():
        return [result async for result in owner.tick()]

    assert asyncio.run(collect()) == [WorkflowTickResult(1, ())]
    assert client.submits == 0
    with connect_database(database) as connection:
        assert connection.execute(
            "SELECT state FROM workflow_dispatches"
        ).fetchone()[0] == "SUBMIT_UNKNOWN"


def test_slow_reject_resume_does_not_block_workflow_tick() -> None:
    decision_started = asyncio.Event()
    workflow_ticked = asyncio.Event()
    release = asyncio.Event()

    class Workflow:
        def recover_interrupted(self):
            return None

        async def tick(self):
            workflow_ticked.set()
            yield WorkflowTickResult(0, ())

    class Agent:
        def prune_traces(self):
            return 0

        def recover_interrupted(self):
            return None

        def reply_waiting_runs(self):
            return ()

        def execution_waiting_runs(self):
            return ()

    class Interaction:
        def requested_starts(self, _limit=None):
            return ()

        def prepare_recovery(self):
            return ()

        async def recover_control_proposals(self):
            return None

        def has_pending_decision(self):
            return True

        async def tick(self):
            decision_started.set()
            await release.wait()
            return True

    async def exercise() -> None:
        runtime = _runtime(Interaction(), Agent(), Workflow())
        await runtime.start()
        await asyncio.wait_for(decision_started.wait(), timeout=1)
        await asyncio.wait_for(workflow_ticked.wait(), timeout=1)
        assert runtime.is_ready()
        release.set()
        await runtime.stop()

    asyncio.run(exercise())


def test_agent_resume_pool_deduplicates_runs() -> None:
    run_ids = tuple(f"agent-{index}" for index in range(12))
    release = asyncio.Event()
    capacity_reached = asyncio.Event()
    all_finished = asyncio.Event()

    @dataclass(frozen=True)
    class Run:
        agent_run_id: str

    class Workflow:
        def __init__(self) -> None:
            self.first = True

        def recover_interrupted(self):
            return None

        async def tick(self):
            if self.first:
                self.first = False
                for run_id in (run_ids[0], *run_ids):
                    yield WorkflowTickResult(1, (run_id,))

    class Agent:
        def prune_traces(self):
            return 0

        def __init__(self) -> None:
            self.reconciled = []
            self.active = 0
            self.peak = 0
            self.finished = 0

        def recover_interrupted(self):
            return None

        def reply_waiting_runs(self):
            return ()

        def execution_waiting_runs(self):
            return ()

        def reconcile_execution(self, agent_run_id):
            self.reconciled.append(agent_run_id)
            return True

        async def resume_execution(self, agent_run_id):
            self.active += 1
            self.peak = max(self.peak, self.active)
            if self.active == 7:
                capacity_reached.set()
            await release.wait()
            self.active -= 1
            self.finished += 1
            if self.finished == len(run_ids):
                all_finished.set()
            return Run(agent_run_id)

    class Interaction:
        def requested_starts(self, _limit=None):
            return ()

        def __init__(self) -> None:
            self.projected = []

        def prepare_recovery(self):
            return ()

        async def recover_control_proposals(self):
            return None

        async def tick(self):
            return False

        def project(self, run):
            self.projected.append(run.agent_run_id)

    async def exercise() -> None:
        agent = Agent()
        interaction = Interaction()
        runtime = _runtime(interaction, agent, Workflow())
        await runtime.start()
        await asyncio.wait_for(capacity_reached.wait(), timeout=1)
        # Decision work is only admitted when it exists; idle decisions no
        # longer consume a slot from resumption work.
        assert agent.peak == 8
        assert sum(runtime.metric_counts().values()) == 8
        assert agent.reconciled == list(run_ids)
        release.set()
        await asyncio.wait_for(all_finished.wait(), timeout=3)
        await runtime.stop()
        assert sorted(interaction.projected) == sorted(run_ids)

    asyncio.run(exercise())


def test_unknown_workflow_failure_keeps_claim_until_restart_recovery(tmp_path) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    _seed_dispatches(database, 1)

    class Client:
        async def submit(self, _service, _request):
            raise RuntimeError("workflow failed")

    workflow = WorkflowExecutionOwner(
        database,
        (_seed_model(),),
        (ServiceBinding("executor", "http://executor"),),
        Client(),
    )

    class Agent:
        def prune_traces(self):
            return 0

        def recover_interrupted(self):
            return None

        def reply_waiting_runs(self):
            return ()

        def execution_waiting_runs(self):
            return ()

    class Interaction:
        def requested_starts(self, _limit=None):
            return ()

        def prepare_recovery(self):
            return ()

        async def recover_control_proposals(self):
            return None

        async def tick(self):
            return False

    async def exercise() -> None:
        runtime = _runtime(Interaction(), Agent(), workflow)
        await runtime.start()
        for _ in range(20):
            await asyncio.sleep(0)
            if not runtime.is_ready():
                break
        assert not runtime.is_ready()
        with connect_database(database) as connection:
            dispatch = connection.execute(
                "SELECT state, claim_state FROM workflow_dispatches"
            ).fetchone()
        assert tuple(dispatch) == ("SUBMITTING", "CLAIMED")
        with pytest.raises(Exception, match="unhandled errors in a TaskGroup") as error:
            await runtime.stop()
        assert type(error.value).__name__ == "ExceptionGroup"

    asyncio.run(exercise())
    workflow.recover_interrupted()
    with connect_database(database) as connection:
        recovered = connection.execute(
            "SELECT state, claim_state FROM workflow_dispatches"
        ).fetchone()
    assert tuple(recovered) == ("SUBMIT_UNKNOWN", None)


def test_fast_workflow_replays_before_slow_batch_peer_finishes(tmp_path) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    _seed_dispatches(database, 2)
    slow_started = asyncio.Event()
    release_slow = asyncio.Event()
    replayed = asyncio.Event()

    class Client:
        async def submit(self, _service, request):
            if request.request_id == "run-001":
                slow_started.set()
                await release_slow.wait()
            return JobStatusV6(
                job_id=f"job-{request.request_id}",
                request_id=request.request_id,
                status="SUCCEEDED",
            )

    workflow = WorkflowExecutionOwner(
        database, (_seed_model(),), (ServiceBinding("executor", "http://executor"),), Client()
    )

    class Agent:
        def prune_traces(self):
            return 0

        def recover_interrupted(self):
            return None

        def reply_waiting_runs(self):
            return ()

        def execution_waiting_runs(self):
            return ()

        def reconcile_execution(self, _agent_run_id):
            return True

        async def resume_execution(self, agent_run_id):
            if agent_run_id == "agent-000":
                replayed.set()
            return agent_run_id

    class Interaction:
        def requested_starts(self, _limit=None):
            return ()

        def prepare_recovery(self):
            return ()

        async def recover_control_proposals(self):
            return None

        async def tick(self):
            return False

        def project(self, _run):
            pass

    async def exercise():
        runtime = _runtime(Interaction(), Agent(), workflow)
        await runtime.start()
        try:
            await asyncio.wait_for(slow_started.wait(), timeout=1)
            await asyncio.wait_for(replayed.wait(), timeout=1)
            assert not release_slow.is_set()
        finally:
            release_slow.set()
            await runtime.stop()

    asyncio.run(exercise())


def test_reconcile_failure_closes_dispatch_iterator_without_unretrieved_errors(
    tmp_path,
) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    _seed_dispatches(database, 2)
    slow_started = asyncio.Event()
    slow_cancelled = asyncio.Event()
    unhandled = []

    class Client:
        async def submit(self, _service, request):
            if request.request_id == "run-001":
                slow_started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    slow_cancelled.set()
            return JobStatusV6(
                job_id=f"job-{request.request_id}",
                request_id=request.request_id,
                status="SUCCEEDED",
            )

    class Agent:
        def prune_traces(self):
            return 0

        def recover_interrupted(self):
            pass

        def reply_waiting_runs(self):
            return ()

        def execution_waiting_runs(self):
            return ()

        def reconcile_execution(self, _agent_run_id):
            raise RuntimeError("reconciliation bug")

    class Interaction:
        def requested_starts(self, _limit=None):
            return ()

        def prepare_recovery(self):
            return ()

        async def recover_control_proposals(self):
            return None

        async def tick(self):
            return False

    workflow = WorkflowExecutionOwner(
        database, (_seed_model(),), (ServiceBinding("executor", "http://executor"),), Client()
    )

    async def exercise():
        asyncio.get_running_loop().set_exception_handler(
            lambda _loop, context: unhandled.append(context)
        )
        runtime = _runtime(Interaction(), Agent(), workflow)
        await runtime.start()
        # Synchronize on dispatch, not filesystem speed during durable startup.
        await slow_started.wait()
        with pytest.raises(RuntimeError, match="reconciliation bug"):
            await runtime._task
        assert not runtime.is_ready()
        assert slow_cancelled.is_set()
        with pytest.raises(RuntimeError, match="reconciliation bug"):
            await runtime.stop()

    asyncio.run(exercise())
    gc.collect()
    assert slow_cancelled.is_set()
    assert unhandled == []
    with connect_database(database) as connection:
        unfinished = connection.execute(
            """SELECT state, claim_state FROM workflow_dispatches
               WHERE workflow_run_id = 'run-001'"""
        ).fetchone()
    assert tuple(unfinished) == ("SUBMITTING", "CLAIMED")


def test_pending_start_recovery_does_not_wait_for_model_before_polling(
    kernel_settings, monkeypatch
) -> None:
    model_started = asyncio.Event()
    release = asyncio.Event()
    polled = asyncio.Event()

    async def finish(_call):
        model_started.set()
        await release.wait()
        return ModelStep(output=[submit_reply('recovered')])

    application = PlatformApplication(
        settings=kernel_settings("knowledge"),
        model=ScriptedModel([ModelStep.respond(finish)]),
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: None)),
    )
    session = application.interaction.create_session().session
    reserve_turn(
        application.database, session.session_id, "hello", "pending-run",
        "p",
    )
    original_tick = application.workflow_execution.tick

    async def tick():
        polled.set()
        async for result in original_tick():
            yield result

    monkeypatch.setattr(application.workflow_execution, "tick", tick)

    async def exercise():
        await asyncio.wait_for(application.start(), timeout=1)
        try:
            await asyncio.wait_for(model_started.wait(), timeout=1)
            await asyncio.wait_for(polled.wait(), timeout=1)
            assert application.is_ready()
            assert not release.is_set()
        finally:
            release.set()
            await application.close()
            await application.client.aclose()
        assert application.agent_runtime.get("pending-run").status == "COMPLETE"

    asyncio.run(exercise())


def test_runtime_prunes_traces_at_start_and_when_hourly_deadline_is_due() -> None:
    pruned_again = asyncio.Event()

    class Agent:
        count = 0

        def prune_traces(self):
            self.count += 1
            if self.count == 2:
                pruned_again.set()

        def recover_interrupted(self):
            pass

        def reply_waiting_runs(self):
            return ()

        def execution_waiting_runs(self):
            return ()

    class Interaction:
        def requested_starts(self, _limit=None):
            return ()

        def prepare_recovery(self):
            return ()

        async def recover_control_proposals(self):
            return None

        async def tick(self):
            return False

    class Workflow:
        def recover_interrupted(self):
            pass

        async def tick(self):
            yield WorkflowTickResult(0, ())

    async def exercise():
        agent = Agent()
        runtime = _runtime(Interaction(), agent, Workflow())
        await runtime.start()
        assert agent.count == 1
        assert runtime._next_trace_prune_at > asyncio.get_running_loop().time()
        runtime._next_trace_prune_at = 0
        try:
            await asyncio.wait_for(pruned_again.wait(), timeout=1)
            assert agent.count == 2
        finally:
            await runtime.stop()

    asyncio.run(exercise())


def _seed_model() -> ExecutableModelDefinition:
    return ExecutableModelDefinition(
        model_id="model",
        model_version="1.0.0",
        model_checksum="sha256:" + "0" * 64,
        workflow_id="workflow",
        workflow_version="1.0.0",
        tool_name="model",
        tool_description="test model",
        input_schema={"type": "object", "additionalProperties": False},
        default_parameters={},
        execution_service="executor",
        executor_id="executor",
        deadline_seconds=60,
        workflow_checksum="sha256:" + "1" * 64,
        input_schema_checksum="sha256:" + "2" * 64,
        final_outputs=(),
        data_input_parameters=(),
    )


def _seed_dispatch_confirmation(
    database, *, status: str = "CONFIRMED", decision_state: str | None = None
) -> None:
    with connect_database(database) as connection:
        connection.execute(
            """INSERT INTO confirmations(
                   confirmation_id,session_id,goal_revision,agent_run_id,status,
                   decision,decision_subject,decision_state)
               VALUES('approval-000','session-000',1,'agent-000',?,
                      'CONFIRM','local-researcher',?)""",
            (status, decision_state),
        )
        connection.execute(
            """INSERT INTO confirmation_actions VALUES(
                   'approval-000',0,'call-000','model','model','1.0.0',
                   'sha256:0000000000000000000000000000000000000000000000000000000000000000',
                   'workflow','1.0.0',
                   'sha256:1111111111111111111111111111111111111111111111111111111111111111',
                   'sha256:2222222222222222222222222222222222222222222222222222222222222222',
                   'fingerprint','{}','{}')"""
        )
        connection.execute(
            "INSERT INTO tasks VALUES('task-000','session-000',1,'ACTIVE','2026-01-01T00:00:00Z')"
        )
        connection.execute(
            "INSERT INTO confirmation_tasks VALUES('approval-000','task-000',1)"
        )
        connection.execute(
            "UPDATE workflow_runs SET confirmation_id='approval-000',created_at=? "
            "WHERE run_id='run-000'",
            (
                datetime.now(timezone.utc).isoformat(timespec="milliseconds")
                .replace("+00:00", "Z"),
            ),
        )


def _seed_dispatches(database, count: int) -> None:
    with connect_database(database) as connection:
        for index in range(count):
            suffix = f"{index:03d}"
            session_id = f"session-{suffix}"
            agent_run_id = f"agent-{suffix}"
            run_id = f"run-{suffix}"
            connection.execute(
                "INSERT INTO sessions (session_id, project_id, goal_revision, agent_run_id, pending_agent_run_id, agent_start_state, owner_subject) VALUES (?, 'project', 1, ?, NULL, NULL, 'local-researcher')",
                (session_id, agent_run_id),
            )
            connection.execute(
                "INSERT INTO agent_runs VALUES "
                "(?, ?, 'project', 1, 'deepseek', 'model', 'WAITING_EXECUTION', "
                "'[\"RUNNING\",\"WAITING_EXECUTION\"]', NULL, 0, 0, 0, ?, NULL, "
                "?, ?, NULL, NULL)",
                (
                    agent_run_id,
                    session_id,
                    f"trace-{suffix}",
                    f"2026-01-01T00:00:00.{suffix}Z",
                    f"2026-01-01T00:00:00.{suffix}Z",
                ),
            )
            connection.execute(
                """INSERT INTO agent_actions(
                       tool_call_id,agent_run_id,position,tool,model_id,
                       model_version,model_checksum,workflow_id,workflow_version,
                       workflow_checksum,plan_revision,arguments_json,
                       input_references_json,schema_checksum,fingerprint,status)
                   VALUES(?,?,0,'model','model','1.0.0',
                          'sha256:0000000000000000000000000000000000000000000000000000000000000000',
                          'workflow','1.0.0',
                          'sha256:1111111111111111111111111111111111111111111111111111111111111111',
                          1,'{}','{}',
                          'sha256:2222222222222222222222222222222222222222222222222222222222222222',
                          ?,'APPROVED')""",
                (f"call-{suffix}", agent_run_id, f"fingerprint-{suffix}"),
            )
            connection.execute(
                "INSERT INTO workflow_runs(run_id,agent_run_id,session_id,tool_call_id,position,project_id,"
                "model_id,model_version,model_checksum,workflow_id,workflow_version,workflow_checksum,"
                "execution_service,executor_id,arguments_json,status,remote_job_id,outputs_json,error,"
                "created_at,started_at,finished_at,duration_ms,input_references_json) VALUES "
                "(?, ?, ?, ?, 0, 'project', 'model', '1.0.0', 'sha256:0000000000000000000000000000000000000000000000000000000000000000', "
                "'workflow', '1.0.0', 'sha256:1111111111111111111111111111111111111111111111111111111111111111', "
                "'executor', 'executor', '{}', 'QUEUED', NULL, '[]', NULL, "
                "?, NULL, NULL, NULL, '{}')",
                (
                    run_id, agent_run_id, session_id, f"call-{suffix}",
                    f"2026-01-01T00:00:00.{suffix}Z",
                ),
            )
            connection.execute(
                "INSERT INTO workflow_service_bindings(workflow_run_id,target_digest) "
                "VALUES (?,?)",
                (run_id, service_target_digest(ServiceBinding("executor", "http://executor"))),
            )
            connection.execute(
                "INSERT INTO workflow_dispatches VALUES "
                "(?, 'REQUESTED', NULL, '2030-01-01T00:00:00Z', "
                "'0000', NULL, NULL)",
                (run_id,),
            )
