from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Event

import httpx
import pytest

from platform_executor_service.runtime import ExecutorRuntime


def test_owned_internal_clients_ignore_host_proxy(tmp_path, monkeypatch) -> None:
    from platform_executor_service import composition, worker

    config = {"productId": "test", "operatorCatalogDigest": "digest", "models": []}
    monkeypatch.setattr(composition, "load_executor_config", lambda _path: config)
    monkeypatch.setattr(worker, "load_executor_config", lambda _path: config)
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:1")
    monkeypatch.setenv("EXECUTOR_CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setenv("EXECUTOR_CONTROL_URL", "http://executor-control:8000")

    application = composition.ExecutorApplication(
        tmp_path / "config.json",
        tmp_path / "state",
        tmp_path / "objects",
        tmp_path / "work",
        "http://data-service:8000",
        "test-project",
    )
    try:
        assert application.client._trust_env is False
        instance = worker.Worker("native", 1, 256 * 1024**2)
        try:
            assert instance.client._trust_env is False
        finally:
            asyncio.run(instance.client.aclose())
    finally:
        application.client.close()


def test_worker_import_does_not_load_control_or_sqlite() -> None:
    root = Path(__file__).resolve().parents[3]
    sources = (
        root / "services/executor/src",
        root / "packages/protocol-contracts/src",
    )
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import platform_executor_service.worker; "
            "assert 'platform_executor_service.control' not in sys.modules; "
            "assert 'platform_executor_service.database' not in sys.modules; "
            "assert 'sqlite3' not in sys.modules",
        ],
        check=True,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(map(str, sources))},
    )


def test_supervisor_failure_is_visible_and_owned_client_is_closed() -> None:
    calls = []

    class Control:
        def recover(self):
            calls.append("recover")

        def reconcile(self):
            calls.append("reconcile")

        def ready(self):
            return True

    class Publisher:
        def publish_ready(self):
            calls.append("publish")
            raise ValueError("publication invariant failed")

    client = httpx.Client(trust_env=False)
    runtime = ExecutorRuntime(Control(), Publisher(), client)

    async def exercise():
        await runtime.start()
        with pytest.raises(ValueError, match="publication invariant failed"):
            await asyncio.wait_for(runtime._supervisor, timeout=2)
        assert not runtime.is_ready()
        with pytest.raises(ValueError, match="publication invariant failed"):
            await runtime.close()

    asyncio.run(exercise())
    assert calls == ["recover", "reconcile", "publish"]
    assert client.is_closed


def test_supervisor_recovers_from_transient_sqlite_busy() -> None:
    reconciled = Event()
    attempts = 0

    class Control:
        def recover(self):
            pass

        def reconcile(self):
            nonlocal attempts
            attempts += 1
            if attempts <= 2:
                raise sqlite3.OperationalError("database is locked")
            reconciled.set()

        def ready(self):
            return True

    class Publisher:
        def publish_ready(self):
            pass

    runtime = ExecutorRuntime(Control(), Publisher(), None)

    async def exercise():
        await runtime.start()
        assert await asyncio.wait_for(asyncio.to_thread(reconciled.wait, 2), timeout=3)
        assert runtime._supervisor is not None
        assert not runtime._supervisor.done()
        assert runtime.is_ready()
        await runtime.close()

    asyncio.run(exercise())
    assert attempts >= 3


@pytest.mark.parametrize("phase", ["hash", "complete"])
def test_worker_heartbeats_cover_postprocess_and_completion(tmp_path, monkeypatch, phase):
    import json
    from threading import Event
    from platform_executor_service import worker as module

    async def exercise():
        instance = module.Worker.__new__(module.Worker)
        instance.stop = asyncio.Event()
        ticks = []
        entered, release = Event(), Event()
        original_sleep = asyncio.sleep

        original_wait = asyncio.wait_for

        async def fast_wait(awaitable, timeout):
            return await original_wait(awaitable, 0.001 if timeout == 10 else timeout)

        monkeypatch.setattr(module.asyncio, "wait_for", fast_wait)
        (tmp_path / "outputs").mkdir()
        (tmp_path / "outputs/data").write_bytes(b"x")
        (tmp_path / "operator-result.json").write_text(json.dumps({"outputs": {
            "data": {"kind": "artifact", "path": str(tmp_path / "outputs/data"),
                     "mediaType": "application/octet-stream", "format": "bin"}}}))

        class Process:
            returncode = None

            def __init__(self):
                self.stdout = asyncio.StreamReader()
                self.stderr = asyncio.StreamReader()
                self.stdout.feed_eof()
                self.stderr.feed_eof()

            async def wait(self):
                self.returncode = 0
                return 0

        async def subprocess(*args, **kwargs):
            return Process()

        async def terminate(process):
            assert process.returncode == 0

        original_hash = module._file_identity

        def slow_hash(path):
            if phase == "hash":
                entered.set()
                assert release.wait(5)
            return original_hash(path)

        async def post(path, payload):
            if path.endswith(":heartbeat"):
                ticks.append(path)
            elif phase == "complete":
                entered.set()
                while not release.is_set():
                    await original_sleep(0.001)
            return httpx.Response(204, request=httpx.Request("POST", "http://control" + path))

        monkeypatch.setattr(module.asyncio, "create_subprocess_exec", subprocess)
        monkeypatch.setattr(module, "_terminate_process_group", terminate)
        monkeypatch.setattr(module, "_file_identity", slow_hash)
        instance._post = post
        lease = {"leaseId": "lease", "leaseToken": "token", "workspace": str(tmp_path),
                 "runtime": "native", "operatorId": "test", "operatorVersion": "1.0.0",
                 "inputs": {}, "parameters": {}, "timeoutSeconds": 60, "memoryLimitBytes": 1024}
        task = asyncio.create_task(instance._execute(lease))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            start = len(ticks)
            await original_sleep(0.03)
            assert len(ticks) > start
        finally:
            release.set()
        await asyncio.wait_for(task, 5)
        count = len(ticks)
        await original_sleep(0.02)
        assert len(ticks) == count

    asyncio.run(exercise())


def test_stale_heartbeat_stops_only_current_attempt():
    from platform_executor_service.worker import Worker

    async def exercise():
        instance = Worker.__new__(Worker)
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def execute(lease):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def stale(lease):
            await entered.wait()

        instance._execute_attempt, instance._lease_heartbeats = execute, stale
        await asyncio.wait_for(instance._execute({}), 1)
        assert cancelled.is_set()

    asyncio.run(exercise())


def test_slow_publication_does_not_block_lease_reconciliation():
    from threading import Event

    uploading, release, reconciled = Event(), Event(), Event()
    calls = []

    class Control:
        def recover(self):
            pass

        def reconcile(self):
            assert uploading.wait(3)
            calls.append("reconcile")
            if len(calls) >= 2:
                reconciled.set()

        def ready(self):
            return True

    class Publisher:
        def publish_ready(self):
            uploading.set()
            assert release.wait(5)

    runtime = ExecutorRuntime(Control(), Publisher(), None)

    async def exercise():
        await runtime.start()
        try:
            assert await asyncio.to_thread(reconciled.wait, 3)
            assert runtime.is_ready()
        finally:
            release.set()
            await runtime.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("lease_stale", [False, True])
def test_finished_hash_error_racing_with_stale_heartbeat(lease_stale):
    from platform_executor_service.worker import Worker

    async def exercise():
        instance = Worker.__new__(Worker)

        async def missing_output(lease):
            raise FileNotFoundError("attempt workspace removed")

        async def heartbeat(lease):
            return lease_stale

        async def post(path, payload):
            return httpx.Response(204, request=httpx.Request("POST", "http://control" + path))

        instance._execute_attempt = missing_output
        instance._lease_heartbeats = heartbeat
        instance._post = post
        lease = {"leaseId": "lease", "leaseToken": "token"}
        if lease_stale:
            await instance._execute(lease)
        else:
            with pytest.raises(FileNotFoundError):
                await instance._execute(lease)

    asyncio.run(exercise())
