from __future__ import annotations

import asyncio

import httpx
import pytest

from platform_executor_service.worker import Worker


def test_database_busy_retries_the_same_completion_payload():
    seen = []

    async def exercise():
        worker = object.__new__(Worker)
        worker.stop = asyncio.Event()
        worker.control_url = "http://control"

        def handler(request):
            seen.append(request.content)
            if len(seen) == 1:
                return httpx.Response(503, json={"code": "EXECUTOR_DATABASE_BUSY"})
            return httpx.Response(204)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            worker.client = client
            response = await worker._post(
                "/internal/workers/v3/leases/lease-1:complete",
                {"leaseToken": "token", "values": {"summary": 2}},
            )
        assert response.status_code == 204
        assert len(seen) == 2 and seen[0] == seen[1]

    asyncio.run(exercise())


@pytest.mark.parametrize("body", [b"[]", b"null", b"not json", b'{"code":"OTHER_FAILURE"}'])
def test_unrecognized_503_retains_the_http_failure(body):
    async def exercise():
        worker = object.__new__(Worker)
        worker.stop = asyncio.Event()
        worker.control_url = "http://control"
        calls = 0

        def handler(_request):
            nonlocal calls
            calls += 1
            return httpx.Response(503, headers={"content-type": "application/json"}, content=body)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            worker.client = client
            response = await worker._post("/internal/workers/v3/heartbeat", {})
        assert response.status_code == 503
        assert response.content == body
        assert calls == 1
        with pytest.raises(httpx.HTTPStatusError):
            response.raise_for_status()

    asyncio.run(exercise())


def test_stale_remote_checkpoint_keeps_other_slot_alive_and_accepts_next_task():
    async def exercise():
        worker = object.__new__(Worker)
        worker.stop = asyncio.Event()
        worker.slots = 2
        worker.runtime = "remote_http"
        worker.worker_id = "remote-test"
        worker.memory_bytes = 1024 ** 3
        worker.catalog_digest = "sha256:" + "a" * 64
        started = asyncio.Event()
        next_completed = asyncio.Event()
        completed = []
        leases = [
            {"leaseId": name, "leaseToken": "token", "runtime": "remote_http"}
            for name in ("stale", "other", "next")
        ]

        async def post(path, _payload):
            if path.endswith("nodes:claim"):
                return httpx.Response(200, json={"lease": leases.pop(0) if leases else None}, request=httpx.Request("POST", "http://control" + path))
            return httpx.Response(409, json={"code": "LEASE_STALE"})

        async def execute(lease):
            if lease["leaseId"] == "stale":
                await started.wait()
                await worker._persist_remote_state(lease, {})
            elif lease["leaseId"] == "other":
                started.set()
                await next_completed.wait()
                completed.append("other")
                worker.stop.set()
            else:
                completed.append("next")
                next_completed.set()

        async def heartbeat(_lease=None):
            await worker.stop.wait()
            return False

        worker._post = post
        worker._execute_attempt = execute
        worker._heartbeats = heartbeat
        worker._lease_heartbeats = heartbeat
        await asyncio.wait_for(worker.run(), 2)
        assert completed == ["next", "other"]
        assert not leases

    asyncio.run(exercise())
