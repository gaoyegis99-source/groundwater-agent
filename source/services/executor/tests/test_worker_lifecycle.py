from __future__ import annotations

import asyncio
import hashlib
import json
from threading import Event
from types import SimpleNamespace

import httpx
import pytest

from platform_executor_service import worker as module


@pytest.mark.parametrize("completion_code", [200, 409])
@pytest.mark.parametrize("stale_during_hash", [False, True])
def test_heartbeat_covers_hash_and_completion_then_worker_is_reusable(tmp_path, monkeypatch, completion_code, stale_during_hash):
    async def exercise():
        worker = module.Worker.__new__(module.Worker)
        worker.stop = asyncio.Event()
        pulses = asyncio.Queue()
        hash_release = Event()
        completion_heartbeat = asyncio.Event()
        phase, heartbeats = [], []
        loop = asyncio.get_running_loop()
        original_wait = asyncio.wait_for

        async def controlled_wait(awaitable, timeout):
            if timeout != 10:
                return await original_wait(awaitable, timeout)
            try:
                await pulses.get()
            finally:
                awaitable.close()
            raise TimeoutError

        monkeypatch.setattr(module.asyncio, "wait_for", controlled_wait)

        async def wait():
            return 0

        async def spawn(*args, **kwargs):
            stdout = asyncio.StreamReader()
            stderr = asyncio.StreamReader()
            stdout.feed_eof()
            stderr.feed_eof()
            return SimpleNamespace(returncode=0, wait=wait, stdout=stdout, stderr=stderr)

        monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn)
        async def terminate(_process):
            assert _process.returncode == 0

        monkeypatch.setattr(module, "_terminate_process_group", terminate)
        original_hash = module._file_identity

        def blocked_hash(path):
            phase.append("hash")
            loop.call_soon_threadsafe(pulses.put_nowait, None)
            assert hash_release.wait(3), "event loop was blocked by file hashing"
            return original_hash(path)

        monkeypatch.setattr(module, "_file_identity", blocked_hash)

        async def post(path, body):
            if path.endswith(":heartbeat"):
                heartbeats.append(phase[-1])
                if phase[-1] == "hash":
                    if stale_during_hash:
                        output.unlink()
                    hash_release.set()
                else:
                    completion_heartbeat.set()
                return httpx.Response(409 if stale_during_hash else 204, json={"code": "LEASE_STALE"}, request=httpx.Request("POST", "http://control" + path))
            assert path.endswith(":complete")
            assert body["outputs"][0]["checksum"] == "sha256:" + hashlib.sha256(b"output").hexdigest()
            phase.append("completion")
            pulses.put_nowait(None)
            await original_wait(completion_heartbeat.wait(), 3)
            # Wake the heartbeat waiter so it can see done after this response.
            pulses.put_nowait(None)
            return httpx.Response(completion_code, json={"code": "LEASE_STALE"}, request=httpx.Request("POST", "http://control" + path))

        worker._post = post
        for index in range(2):
            hash_release.clear()
            completion_heartbeat.clear()
            workspace = tmp_path / str(index)
            workspace.mkdir()
            output = workspace / "output.bin"
            output.write_bytes(b"output")
            (workspace / "operator-result.json").write_text(json.dumps({"outputs": {
                "result": {"kind": "artifact", "path": str(output), "mediaType": "application/octet-stream", "format": "bin"},
            }}))
            lease = {"workspace": str(workspace), "runtime": "native", "operatorId": "test",
                     "operatorVersion": "1.0.0", "inputs": {}, "parameters": {},
                     "timeoutSeconds": 60, "memoryLimitBytes": 1024**3, "leaseId": str(index), "leaseToken": "token"}
            await original_wait(worker._execute(lease), 5)
        assert heartbeats.count("hash") == 2
        if stale_during_hash:
            assert heartbeats.count("completion") == 0
        else:
            assert heartbeats.count("completion") >= 2
        assert not worker.stop.is_set()

    asyncio.run(exercise())


def test_only_explicit_stale_lease_is_accepted_as_normal_failure():
    async def exercise():
        worker = module.Worker.__new__(module.Worker)

        async def post(path, body):
            return httpx.Response(409, json={"code": "UNEXPECTED_CONFLICT"}, request=httpx.Request("POST", "http://control"))

        worker._post = post
        with pytest.raises(httpx.HTTPStatusError):
            await worker._fail({"leaseId": "lease", "leaseToken": "token"}, "OPERATOR_TIMEOUT", b"", b"")

    asyncio.run(exercise())


def test_worker_sends_only_bounded_attempt_log_tails(tmp_path, monkeypatch):
    stdout = b"a" * 100_000 + b"stdout-end"
    stderr = b"b" * 100_000 + b"stderr-end"

    async def exercise():
        worker = module.Worker.__new__(module.Worker)
        captured = {}

        class Process:
            returncode = 1

            def __init__(self):
                self.stdout = asyncio.StreamReader()
                self.stderr = asyncio.StreamReader()
                self.stdout.feed_data(stdout)
                self.stderr.feed_data(stderr)
                self.stdout.feed_eof()
                self.stderr.feed_eof()

            async def wait(self):
                return self.returncode

        async def spawn(*args, **kwargs):
            return Process()

        async def post(path, body):
            assert path.endswith(":fail")
            captured.update(body)
            return httpx.Response(204, request=httpx.Request("POST", "http://control" + path))

        monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn)
        async def terminate(_process):
            assert _process.returncode == 1

        monkeypatch.setattr(module, "_terminate_process_group", terminate)
        worker._post = post
        await worker._execute_attempt({
            "workspace": str(tmp_path), "runtime": "native", "operatorId": "test",
            "operatorVersion": "1.0.0", "inputs": {}, "parameters": {},
            "timeoutSeconds": 60, "memoryLimitBytes": 1024**3,
            "leaseId": "lease", "leaseToken": "token",
        })
        assert captured["errorCode"] == "OPERATOR_PROCESS_FAILED"
        assert captured["stdout"] == stdout[-module.LOG_TAIL_BYTES:].decode()
        assert captured["stderr"] == stderr[-module.LOG_TAIL_BYTES:].decode()
        assert len(captured["stdout"].encode()) <= module.LOG_TAIL_BYTES
        assert len(captured["stderr"].encode()) <= module.LOG_TAIL_BYTES

    asyncio.run(exercise())


def test_worker_timeout_kills_process_and_sends_bounded_logs(tmp_path, monkeypatch):
    async def exercise():
        worker = module.Worker.__new__(module.Worker)
        captured = {}

        class Process:
            returncode = None

            def __init__(self):
                self.done = asyncio.Event()
                self.killed = False
                self.stdout = asyncio.StreamReader()
                self.stderr = asyncio.StreamReader()
                self.stdout.feed_data(b"x" * (module.LOG_TAIL_BYTES * 2))
                self.stderr.feed_data(b"y" * (module.LOG_TAIL_BYTES * 2))
                self.stdout.feed_eof()
                self.stderr.feed_eof()

            async def wait(self):
                await self.done.wait()
                return self.returncode

            def kill(self):
                self.killed = True
                self.returncode = -9
                self.done.set()

        process = Process()

        async def spawn(*args, **kwargs):
            return process

        async def post(path, body):
            assert path.endswith(":fail")
            captured.update(body)
            return httpx.Response(204, request=httpx.Request("POST", "http://control" + path))

        monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn)
        async def terminate(_process):
            _process.kill()
            await _process.wait()

        monkeypatch.setattr(module, "_terminate_process_group", terminate)
        worker._post = post
        await worker._execute_attempt({
            "workspace": str(tmp_path), "runtime": "native", "operatorId": "test",
            "operatorVersion": "1.0.0", "inputs": {}, "parameters": {},
            "timeoutSeconds": 0.01, "memoryLimitBytes": 1024**3,
            "leaseId": "lease", "leaseToken": "token",
        })
        assert process.killed
        assert captured["errorCode"] == "OPERATOR_TIMEOUT"
        assert captured["stdout"] == "x" * module.LOG_TAIL_BYTES
        assert captured["stderr"] == "y" * module.LOG_TAIL_BYTES

    asyncio.run(exercise())


def test_long_file_requests_have_no_read_timeout():
    async def exercise():
        timeouts = []

        def handle(request):
            timeouts.append(request.extensions["timeout"]["read"])
            return httpx.Response(200)

        worker = module.Worker.__new__(module.Worker)
        worker.stop = asyncio.Event()
        worker.control_url = "http://control"
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            worker.client = client
            for path in ("/nodes:claim", "/leases/id:complete", "/leases/id:heartbeat"):
                await worker._post(path, {})
        assert timeouts == [None, None, 30.0]

    asyncio.run(exercise())
