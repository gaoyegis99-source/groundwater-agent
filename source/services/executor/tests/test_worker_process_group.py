from __future__ import annotations

import asyncio
import os
from pathlib import Path
import signal
import sys
from threading import Event

import httpx
import pytest

from platform_executor_service import operator_child, worker as module


PARENT_SCRIPT = """
import subprocess
import sys
import time

child = subprocess.Popen([sys.executable, "-c", sys.argv[2]])
with open(sys.argv[1], "w", encoding="utf-8") as target:
    target.write(str(child.pid))
while True:
    time.sleep(1)
"""
PARENT_SUCCESS_SCRIPT = """
import json
from pathlib import Path
import subprocess
import sys

child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(60)"],
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
with open(sys.argv[1], "w", encoding="utf-8") as target:
    target.write(str(child.pid))
output = {"kind": "value", "value": 42}
if len(sys.argv) == 4:
    Path(sys.argv[3]).write_bytes(b"artifact")
    output = {
        "kind": "artifact", "path": sys.argv[3],
        "mediaType": "application/octet-stream", "format": "bin",
    }
with open(sys.argv[2], "w", encoding="utf-8") as target:
    json.dump({"outputs": {"result": output}}, target)
"""
CHILD_SCRIPT = "import time; time.sleep(60)"
STUBBORN_CHILD_SCRIPT = (
    "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
    "time.sleep(60)"
)


def _lease(workspace: Path, lease_id: str, timeout: int = 60) -> dict:
    return {
        "workspace": str(workspace), "runtime": "native", "operatorId": "test",
        "operatorVersion": "1.0.0", "inputs": {}, "parameters": {},
        "timeoutSeconds": timeout, "memoryLimitBytes": 1024**3,
        "leaseId": lease_id, "leaseToken": "token",
    }


async def _child_pid(workspace: Path) -> int:
    target = workspace / "child.pid"
    for _ in range(100):
        if target.is_file():
            return int(target.read_text(encoding="utf-8"))
        await asyncio.sleep(0.02)
    raise AssertionError("operator child did not start")


def _running(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    return state.rsplit(")", 1)[1].split()[0] != "Z"


def _cleanup(processes: list[asyncio.subprocess.Process]) -> None:
    for process in processes:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_operator_child_applies_original_limits_before_exec(monkeypatch):
    limits, command = [], []
    monkeypatch.setattr(sys, "argv", ["operator_child", "17", "4096", "input", "output"])
    monkeypatch.setattr(operator_child.resource, "setrlimit", lambda *args: limits.append(args))
    monkeypatch.setattr(operator_child.os, "execv", lambda *args: command.append(args))
    operator_child.main()
    assert limits == [
        (operator_child.resource.RLIMIT_CPU, (17, 17)),
        (operator_child.resource.RLIMIT_AS, (4096, 4096)),
        (operator_child.resource.RLIMIT_NOFILE, (256, 256)),
        (operator_child.resource.RLIMIT_FSIZE, (4096, 4096)),
    ]
    assert command == [
        (sys.executable, [sys.executable, "-m", "moperators.runtime.task", "input", "output"])
    ]


@pytest.mark.skipif(sys.platform != "linux", reason="Linux process groups and /proc")
def test_timeout_kills_stubborn_descendant_before_reporting_failure(tmp_path, monkeypatch):
    async def exercise():
        worker = module.Worker.__new__(module.Worker)
        calls, processes = [], []
        real_spawn = asyncio.create_subprocess_exec

        async def spawn(*args, **kwargs):
            assert args[2] == "platform_executor_service.operator_child"
            assert kwargs.get("start_new_session") is True
            assert "preexec_fn" not in kwargs
            process = await real_spawn(
                sys.executable, "-c", PARENT_SCRIPT,
                str(Path(kwargs["cwd"]) / "child.pid"), STUBBORN_CHILD_SCRIPT,
                stdout=kwargs["stdout"], stderr=kwargs["stderr"],
                cwd=kwargs["cwd"], start_new_session=True,
            )
            processes.append(process)
            return process

        async def post(path, body):
            calls.append((path, body))
            return httpx.Response(204, request=httpx.Request("POST", "http://control" + path))

        monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn)
        worker._post = post
        try:
            await asyncio.wait_for(worker._execute_attempt(_lease(tmp_path, "lease", 1)), 6)
            child_pid = await _child_pid(tmp_path)
            assert not _running(processes[0].pid)
            assert not _running(child_pid)
            assert len(calls) == 1
            assert calls[0][0].endswith(":fail")
            assert calls[0][1]["errorCode"] == "OPERATOR_TIMEOUT"
        finally:
            _cleanup(processes)
            await asyncio.gather(*(process.wait() for process in processes))

    asyncio.run(exercise())


@pytest.mark.skipif(sys.platform != "linux", reason="Linux process groups and /proc")
def test_success_reaps_descendant_that_closed_pipes_before_callback(tmp_path, monkeypatch):
    async def exercise():
        worker = module.Worker.__new__(module.Worker)
        processes, calls = [], []
        real_spawn = asyncio.create_subprocess_exec

        async def spawn(*args, **kwargs):
            process = await real_spawn(
                sys.executable, "-c", PARENT_SUCCESS_SCRIPT,
                str(tmp_path / "child.pid"), args[-1],
                stdout=kwargs["stdout"], stderr=kwargs["stderr"],
                cwd=kwargs["cwd"], start_new_session=kwargs["start_new_session"],
            )
            processes.append(process)
            return process

        async def post(path, body):
            child_pid = await _child_pid(tmp_path)
            assert not _running(child_pid)
            assert path.endswith(":complete")
            calls.append(body)
            return httpx.Response(204, request=httpx.Request("POST", "http://control" + path))

        monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn)
        worker._post = post
        try:
            await asyncio.wait_for(worker._execute_attempt(_lease(tmp_path, "lease")), 5)
            assert processes[0].returncode == 0
            assert len(calls) == 1
            assert calls[0]["values"] == {"result": 42}
        finally:
            _cleanup(processes)
            await asyncio.gather(*(process.wait() for process in processes))

    asyncio.run(exercise())


@pytest.mark.skipif(sys.platform != "linux", reason="Linux process groups and /proc")
def test_stale_lease_during_hash_has_already_reaped_descendant(tmp_path, monkeypatch):
    async def exercise():
        worker = module.Worker.__new__(module.Worker)
        worker.stop = asyncio.Event()
        processes = []
        entered, release = asyncio.Event(), Event()
        loop = asyncio.get_running_loop()
        real_spawn = asyncio.create_subprocess_exec
        real_hash = module._file_identity

        async def spawn(*args, **kwargs):
            process = await real_spawn(
                sys.executable, "-c", PARENT_SUCCESS_SCRIPT,
                str(tmp_path / "child.pid"), args[-1], str(tmp_path / "output.bin"),
                stdout=kwargs["stdout"], stderr=kwargs["stderr"],
                cwd=kwargs["cwd"], start_new_session=kwargs["start_new_session"],
            )
            processes.append(process)
            return process

        def held_hash(path):
            assert not _running(int((tmp_path / "child.pid").read_text()))
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(3)
            return real_hash(path)

        async def heartbeats(_lease):
            await entered.wait()
            return True

        async def unexpected_post(_path, _body):
            raise AssertionError("stale lease sent a terminal callback")

        worker._lease_heartbeats = heartbeats
        worker._post = unexpected_post
        monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn)
        monkeypatch.setattr(module, "_file_identity", held_hash)
        try:
            await asyncio.wait_for(worker._execute(_lease(tmp_path, "lease")), 5)
            assert entered.is_set()
            child_pid = await _child_pid(tmp_path)
            assert not _running(child_pid)
        finally:
            release.set()
            _cleanup(processes)
            await asyncio.gather(*(process.wait() for process in processes))

    asyncio.run(exercise())


@pytest.mark.skipif(sys.platform != "linux", reason="Linux process groups and /proc")
def test_two_slots_isolate_stale_lease_and_shutdown(tmp_path, monkeypatch):
    async def exercise():
        worker = module.Worker.__new__(module.Worker)
        worker.stop = asyncio.Event()
        stale = asyncio.Event()
        processes = []
        real_spawn = asyncio.create_subprocess_exec

        async def spawn(*args, **kwargs):
            process = await real_spawn(
                sys.executable, "-c", PARENT_SCRIPT,
                str(Path(kwargs["cwd"]) / "child.pid"), CHILD_SCRIPT,
                stdout=kwargs["stdout"], stderr=kwargs["stderr"],
                cwd=kwargs["cwd"], start_new_session=kwargs["start_new_session"],
            )
            processes.append(process)
            return process

        async def heartbeats(lease):
            if lease["leaseId"] == "stale":
                await stale.wait()
                return True
            await worker.stop.wait()
            return False

        async def unexpected_post(_path, _body):
            raise AssertionError("cancelled attempt sent a terminal callback")

        worker._lease_heartbeats = heartbeats
        worker._post = unexpected_post
        monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn)
        workspaces = [tmp_path / name for name in ("stale", "shutdown")]
        for workspace in workspaces:
            workspace.mkdir()
        tasks = [
            asyncio.create_task(worker._execute(_lease(workspace, workspace.name)))
            for workspace in workspaces
        ]
        try:
            child_pids = [await _child_pid(workspace) for workspace in workspaces]
            assert len(processes) == 2
            stale.set()
            await asyncio.wait_for(tasks[0], 5)
            assert not _running(child_pids[0])
            assert _running(child_pids[1])
            worker.stop.set()
            await asyncio.wait_for(tasks[1], 5)
            assert not _running(child_pids[1])
            assert all(process.returncode is not None for process in processes)
        finally:
            worker.stop.set()
            for task in tasks:
                task.cancel()
            _cleanup(processes)
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.gather(*(process.wait() for process in processes))

    asyncio.run(exercise())


@pytest.mark.skipif(sys.platform != "linux", reason="Linux process groups")
def test_cancel_during_spawn_reaps_new_process(tmp_path, monkeypatch):
    async def exercise():
        worker = module.Worker.__new__(module.Worker)
        entered, release = asyncio.Event(), asyncio.Event()
        processes = []
        real_spawn = asyncio.create_subprocess_exec

        async def spawn(*_args, **kwargs):
            entered.set()
            await release.wait()
            process = await real_spawn(
                sys.executable, "-c", "import time; time.sleep(60)",
                stdout=kwargs["stdout"], stderr=kwargs["stderr"],
                start_new_session=kwargs["start_new_session"],
            )
            processes.append(process)
            return process

        monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn)
        task = asyncio.create_task(worker._execute_attempt(_lease(tmp_path, "lease")))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
            assert len(processes) == 1
            assert processes[0].returncode is not None
            assert not _running(processes[0].pid)
        finally:
            release.set()
            _cleanup(processes)
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.gather(*(process.wait() for process in processes))

    asyncio.run(exercise())


def test_worker_run_cancels_all_slots_when_stopped():
    async def exercise():
        worker = module.Worker.__new__(module.Worker)
        worker.stop = asyncio.Event()
        worker.slots = 2
        started, cancelled = asyncio.Queue(), []

        async def heartbeats():
            started.put_nowait("heartbeat")
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append("heartbeat")

        async def slot():
            started.put_nowait("slot")
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append("slot")

        worker._heartbeats = heartbeats
        worker._slot = slot
        task = asyncio.create_task(worker.run())
        assert {await started.get() for _ in range(3)} == {"heartbeat", "slot"}
        worker.stop.set()
        await asyncio.wait_for(task, 2)
        assert sorted(cancelled) == ["heartbeat", "slot", "slot"]

    asyncio.run(exercise())
