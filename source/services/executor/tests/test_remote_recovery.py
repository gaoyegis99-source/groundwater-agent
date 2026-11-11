from __future__ import annotations

import asyncio

import httpx
import pytest

from platform_executor_service.database import connect_database
from platform_executor_service.errors import ExecutionServiceError
from platform_executor_service.remote_http import RemoteOperationError, RemoteOperationRunner
from platform_executor_service.worker import Worker
from platform_executor_service.worker_contracts import (
    LeaseCompletionV3,
    LeaseHeartbeatV3,
    lease_to_wire,
)
from services.executor.tests.test_control import _remote_control, _remote_request, _worker
from services.executor.tests.test_remote_http import _runner, _successful_handler


def _control_client(control):
    def handler(request):
        return httpx.Response(200, json=control.get(request.url.path.rsplit("/", 1)[-1]).as_dict())
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _worker_for_control(control):
    worker = object.__new__(Worker)
    worker.stop = asyncio.Event()
    worker.control_url = "http://control"

    async def post(path, payload):
        lease_id = path.rsplit("/", 1)[-1].split(":", 1)[0]
        body = LeaseHeartbeatV3.model_validate(payload).model_dump(by_alias=False)
        try:
            control.lease_heartbeat(lease_id, body)
        except ExecutionServiceError as error:
            return httpx.Response(409, json={"code": str(error)})
        return httpx.Response(204, request=httpx.Request("POST", worker.control_url + path))

    worker._post = post
    return worker


@pytest.mark.parametrize("recovery", ["control_restart", "lease_loss"])
def test_stale_remote_worker_preserves_operation_for_recovered_attempt(tmp_path, monkeypatch, recovery):
    control, config, resources = _remote_control(tmp_path / "control")
    resources.contents["object-r"] = content = b'{"source":"case-1"}'
    job = control.submit(_remote_request(config, content))
    first = control.claim(_worker(config, "remote_http"))
    wire = lease_to_wire(first)
    remote_root = tmp_path / "remote"
    remote_root.mkdir()
    normal, requests = _successful_handler(b'{"result":"accepted"}')
    recovered = None
    cancellations = []

    def remote_handler(request):
        nonlocal recovered
        if request.url.path.endswith("/cancel"):
            cancellations.append(request.url.path)
            return httpx.Response(200, json={"status": "cancelled", "remoteTerminationConfirmed": True})
        if request.method == "POST" and request.url.path == "/api/v1/operations":
            accepted = normal(request).json()
            return httpx.Response(202, json={**accepted, "status": "running"})
        if request.url.path == "/api/v1/operations/operation-1":
            if recovery == "control_restart":
                control.recover()
            else:
                with connect_database(control.database) as connection:
                    connection.execute("UPDATE node_attempts SET lease_expires_at=? WHERE lease_id=?", ("2000-01-01T00:00:00Z", first["lease_id"]))
                control.reconcile()
            recovered = control.claim(_worker(config, "remote_http"))
            return httpx.Response(200, json={"operationId": "operation-1", "status": "running"})
        if recovered and "/by-client-request/" in request.url.path:
            return httpx.Response(200, json={"operationId": "operation-1", "status": "succeeded"})
        return normal(request)

    runner, _ = _runner(remote_root, monkeypatch, remote_handler)
    runner.lease = wire
    runner.service["pollIntervalSeconds"] = 0

    async def exercise():
        worker = _worker_for_control(control)
        async with _control_client(control) as client:
            worker.client = client
            runner.persist_state = lambda state: worker._persist_remote_state(wire, state)
            # Exercise the Worker entry point as well as the runner's cancellation policy.
            monkeypatch.setattr("platform_executor_service.remote_http.RemoteOperationRunner", lambda *_args: runner)
            worker.config = runner.config
            with pytest.raises(asyncio.CancelledError):
                await worker._execute_attempt(wire)
            assert control.get(job.job_id).status == "RUNNING"
            assert recovered["attempt"] == 2
            assert recovered["remote_state"]["remote_task_id"] == "operation-1"
            assert cancellations == []
            resumed_wire = lease_to_wire(recovered)
            resumed = RemoteOperationRunner(runner.config, resumed_wire, lambda state: worker._persist_remote_state(resumed_wire, state))
            monkeypatch.setattr(resumed, "_client", runner._client)
            result = await resumed.run()
            body = LeaseCompletionV3.model_validate({
                "leaseToken": resumed_wire["leaseToken"], "outputs": result.outputs,
                "values": result.values, "stdout": "", "stderr": "", "remoteState": result.state,
            }).model_dump(by_alias=False)
            control.complete(resumed_wire["leaseId"], body)
            assert all(node["status"] == "SUCCEEDED" for node in control.node_diagnostics(job.job_id))
            assert sum(request.method == "POST" and request.url.path == "/api/v1/operations" for request in requests) == 1
            assert sum(request.method == "POST" and request.url.path == "/api/v1/uploads" for request in requests) == 1

    asyncio.run(exercise())


@pytest.mark.parametrize("termination", ["cancel", "deadline", "failed"])
def test_authoritative_job_termination_still_cancels_remote_operation(tmp_path, monkeypatch, termination):
    control, config, resources = _remote_control(tmp_path / "control")
    resources.contents["object-r"] = content = b'{"source":"case-1"}'
    job = control.submit(_remote_request(config, content))
    lease = control.claim(_worker(config, "remote_http"))
    wire = lease_to_wire(lease)
    remote_root = tmp_path / "remote"
    remote_root.mkdir()
    cancelled = []

    def handler(request):
        cancelled.append(request.url.path)
        return httpx.Response(200, json={"status": "cancelled", "remoteTerminationConfirmed": True})

    runner, _ = _runner(remote_root, monkeypatch, handler)
    runner.state["remoteTaskId"] = "operation-1"

    async def exercise():
        worker = _worker_for_control(control)
        worker.config = runner.config
        async with _control_client(control) as client:
            worker.client = client

            async def terminated(_client):
                if termination == "cancel":
                    control.cancel(job.job_id)
                elif termination == "deadline":
                    with connect_database(control.database) as connection:
                        connection.execute("UPDATE executor_jobs SET deadline_at=? WHERE job_id=?", ("2000-01-01T00:00:00Z", job.job_id))
                    control.reconcile()
                else:
                    control.fail(lease["lease_id"], {"lease_token": lease["lease_token"], "error_code": "UPSTREAM_FAILED"})
                raise asyncio.CancelledError

            monkeypatch.setattr(runner, "_run", terminated)
            monkeypatch.setattr("platform_executor_service.remote_http.RemoteOperationRunner", lambda *_args: runner)
            with pytest.raises(asyncio.CancelledError):
                await worker._execute_attempt(wire)
        assert cancelled == ["/api/v1/operations/operation-1/cancel"]

    asyncio.run(exercise())


def test_operator_timeout_cancels_remote_even_when_job_remains_running(tmp_path, monkeypatch):
    cancelled = []

    def handler(request):
        cancelled.append(request.url.path)
        return httpx.Response(200, json={"status": "cancelled", "remoteTerminationConfirmed": True})

    runner, _ = _runner(tmp_path, monkeypatch, handler)
    runner.state["remoteTaskId"] = "operation-1"
    runner.lease["timeoutSeconds"] = 0.01

    async def pending(_client):
        await asyncio.sleep(10)

    async def job_still_running():
        return False

    monkeypatch.setattr(runner, "_run", pending)
    with pytest.raises(RemoteOperationError, match="OPERATOR_TIMEOUT"):
        asyncio.run(runner.run(cancellation_required=job_still_running))
    assert cancelled == ["/api/v1/operations/operation-1/cancel"]
