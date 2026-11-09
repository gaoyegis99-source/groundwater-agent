from __future__ import annotations

import argparse
from contextlib import suppress
import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import random
import signal
import sys
import time

import httpx
from platform_protocol_contracts import (
    activate_trace_context,
    current_trace_context,
    exporter_from_environment,
    inject_trace_headers,
    internal_span_payload,
    reset_trace_context,
    server_trace_context,
    trace_context_for_id,
)

from .settings import load_executor_config
from .remote_http import RemoteOperationError, execute_remote_operation


LOG_TAIL_BYTES = 64 * 1024
LOGGER = logging.getLogger(__name__)


async def _stream_tail(stream: asyncio.StreamReader) -> bytes:
    tail = bytearray()
    while chunk := await stream.read(LOG_TAIL_BYTES):
        tail.extend(chunk)
        if len(tail) > LOG_TAIL_BYTES:
            del tail[:-LOG_TAIL_BYTES]
    return bytes(tail)


class LeaseAttempt:
    """Own one execution task and its lease heartbeat lifecycle."""

    def __init__(self, worker: Worker, lease: dict) -> None:
        self.worker = worker
        self.lease = lease
        self.execution: asyncio.Task[None] | None = None
        self.heartbeat: asyncio.Task[bool] | None = None

    async def run(self) -> None:
        self.execution = asyncio.create_task(
            self.worker._execute_attempt(self.lease), name="executor:lease:execute"
        )
        self.heartbeat = asyncio.create_task(
            self.worker._lease_heartbeats(self.lease), name="executor:lease:heartbeat"
        )
        try:
            try:
                await asyncio.wait(
                    (self.execution, self.heartbeat),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if self.execution.done():
                    await self.execution
                else:
                    # A stale lease belongs to this attempt. Any unexpected
                    # heartbeat failure remains visible to the Worker.
                    await self.heartbeat
            finally:
                for task in (self.execution, self.heartbeat):
                    task.cancel()
                for task in (self.execution, self.heartbeat):
                    with suppress(asyncio.CancelledError):
                        await task
        except FileNotFoundError as error:
            await self._accept_only_if_stale(error)

    async def _accept_only_if_stale(self, error: FileNotFoundError) -> None:
        assert self.heartbeat is not None
        if (
            self.heartbeat.done()
            and not self.heartbeat.cancelled()
            and self.heartbeat.exception() is None
            and self.heartbeat.result() is True
        ):
            return
        response = await self.worker._post(
            f"/internal/workers/v3/leases/{self.lease['leaseId']}:heartbeat",
            {"leaseToken": self.lease["leaseToken"]},
        )
        if (
            response.status_code == 409
            and response.json().get("code") == "LEASE_STALE"
        ):
            return
        response.raise_for_status()
        raise error


class Worker:
    def __init__(self, runtime: str, slots: int, memory_bytes: int) -> None:
        if not 1 <= slots <= 64:
            raise ValueError("EXECUTOR_WORKER_SLOTS_INVALID")
        if not 256 * 1024**2 <= memory_bytes <= 1024**4:
            raise ValueError("EXECUTOR_WORKER_MEMORY_INVALID")
        self.runtime = runtime
        self.slots = slots
        self.memory_bytes = memory_bytes
        self.worker_id = f"{runtime}-worker"
        self.control_url = os.environ["EXECUTOR_CONTROL_URL"].rstrip("/")
        self.config = load_executor_config(Path(os.environ["EXECUTOR_CONFIG_PATH"]))
        self.catalog_digest = self.config["operatorCatalogDigest"]
        self.client = httpx.AsyncClient(timeout=30.0, trust_env=False)
        self.otlp_exporter = exporter_from_environment()
        self.stop = asyncio.Event()

    @property
    def identity(self) -> dict:
        return {
            "workerId": self.worker_id,
            "runtime": self.runtime,
            "catalogDigest": self.catalog_digest,
            "slots": self.slots,
            "memoryBytes": self.memory_bytes,
        }

    async def run(self) -> None:
        LOGGER.info(
            "Executor worker capacity configured runtime=%s slots=%d memory_bytes=%d",
            getattr(self, "runtime", "test"),
            self.slots,
            getattr(self, "memory_bytes", 0),
        )
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, self.stop.set)
        loop.add_signal_handler(signal.SIGINT, self.stop.set)
        work = asyncio.gather(
            self._heartbeats(), *(self._slot() for _ in range(self.slots))
        )
        stopping = asyncio.create_task(self.stop.wait())
        try:
            done, _ = await asyncio.wait(
                (work, stopping), return_when=asyncio.FIRST_COMPLETED
            )
            if work in done:
                await work
        finally:
            self.stop.set()
            if not work.done():
                work.cancel()
                with suppress(asyncio.CancelledError):
                    await work
            stopping.cancel()
            with suppress(asyncio.CancelledError):
                await stopping
            loop.remove_signal_handler(signal.SIGTERM)
            loop.remove_signal_handler(signal.SIGINT)
            client = getattr(self, "client", None)
            if client is not None:
                await client.aclose()
            exporter = getattr(self, "otlp_exporter", None)
            if exporter is not None:
                await asyncio.to_thread(exporter.close)

    async def _heartbeats(self) -> None:
        while not self.stop.is_set():
            response = await self._post("/internal/workers/v3/heartbeat", self.identity)
            response.raise_for_status()
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=10)
            except TimeoutError:
                pass

    async def _slot(self) -> None:
        delay = 0.25
        while not self.stop.is_set():
            response = await self._post(
                "/internal/workers/v3/nodes:claim", self.identity
            )
            response.raise_for_status()
            lease = response.json()["lease"]
            if lease is None:
                try:
                    await asyncio.wait_for(
                        self.stop.wait(), timeout=delay + random.uniform(0, delay * 0.1)
                    )
                except TimeoutError:
                    pass
                delay = min(delay * 2, 1.0)
                continue
            delay = 0.25
            await self._execute(lease)

    async def _execute(self, lease: dict) -> None:
        context = server_trace_context({"traceparent": _lease_traceparent(lease)})
        token = activate_trace_context(context)
        started_ns = time.time_ns()
        failed = True
        try:
            await LeaseAttempt(self, lease).run()
            failed = False
        finally:
            exporter = getattr(self, "otlp_exporter", None)
            if exporter is not None:
                exporter.export(
                    internal_span_payload(
                        service="executor-worker",
                        context=context,
                        name="execute operator",
                        attributes={
                            "executor.runtime": lease["runtime"],
                            "operator.id": lease["operatorId"],
                            "operator.version": lease["operatorVersion"],
                            "operator.attempt": lease["attempt"],
                        },
                        error=failed,
                        started_ns=started_ns,
                        ended_ns=time.time_ns(),
                    )
                )
            reset_trace_context(token)

    async def _execute_attempt(self, lease: dict) -> None:
        if lease["runtime"] == "remote_http":
            try:
                result = await execute_remote_operation(
                    self.config,
                    lease,
                    lambda state: self._persist_remote_state(lease, state),
                )
            except RemoteOperationError as error:
                await self._fail(
                    lease,
                    error.code,
                    b"",
                    str(error).encode(),
                    remote_state=error.state,
                )
                return
            response = await self._post(
                f"/internal/workers/v3/leases/{lease['leaseId']}:complete",
                {
                    "leaseToken": lease["leaseToken"],
                    "outputs": result.outputs,
                    "values": result.values,
                    "stdout": "",
                    "stderr": "",
                    "remoteState": result.state,
                },
            )
            if not (
                response.status_code == 409
                and response.json().get("code") == "LEASE_STALE"
            ):
                response.raise_for_status()
            return
        payload_path = Path(lease["workspace"]) / "operator-request.json"
        result_path = Path(lease["workspace"]) / "operator-result.json"
        payload = {
            "runtime": lease["runtime"],
            "operatorId": lease["operatorId"],
            "operatorVersion": lease["operatorVersion"],
            "inputs": lease["inputs"],
            "parameters": lease["parameters"],
            "outputDirectory": str(Path(lease["workspace"]) / "outputs"),
        }
        payload_path.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        spawn = asyncio.create_task(
            asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "platform_executor_service.operator_child",
                str(lease["timeoutSeconds"]),
                str(lease["memoryLimitBytes"]),
                str(payload_path),
                str(result_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=lease["workspace"],
                env={
                    **os.environ,
                    "TRACEPARENT": (
                        current_trace_context().traceparent
                        if current_trace_context() is not None
                        else _lease_traceparent(lease)
                    ),
                },
                start_new_session=True,
            )
        )
        try:
            process = await asyncio.shield(spawn)
        except asyncio.CancelledError:
            process = await spawn
            await _terminate_process_group(process)
            raise
        stdout_task = asyncio.create_task(_stream_tail(process.stdout))
        stderr_task = asyncio.create_task(_stream_tail(process.stderr))
        timed_out = False
        streams_closed = False
        try:
            try:
                await asyncio.wait_for(process.wait(), timeout=lease["timeoutSeconds"])
            except TimeoutError:
                timed_out = True
            if not timed_out and process.returncode == 0:
                _, pending = await asyncio.wait(
                    (stdout_task, stderr_task), timeout=2
                )
                streams_closed = not pending
        finally:
            await _terminate_process_group(process)
            stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
        if timed_out:
            await self._fail(lease, "OPERATOR_TIMEOUT", stdout, stderr)
            return
        if process.returncode != 0 or not streams_closed or not result_path.is_file():
            await self._fail(lease, "OPERATOR_PROCESS_FAILED", stdout, stderr)
            return
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if "error" in result:
            await self._fail(lease, result["error"]["code"], stdout, stderr)
            return
        outputs, values = [], {}
        for port, item in result["outputs"].items():
            if item["kind"] == "value":
                values[port] = item["value"]
                continue
            path = Path(item["path"])
            checksum, size = await asyncio.to_thread(_file_identity, path)
            outputs.append(
                {
                    "port": port,
                    "path": str(path.relative_to(Path(lease["workspace"]))),
                    "mediaType": item["mediaType"],
                    "format": item["format"],
                    "sizeBytes": size,
                    "checksum": checksum,
                }
            )
        response = await self._post(
            f"/internal/workers/v3/leases/{lease['leaseId']}:complete",
            {
                "leaseToken": lease["leaseToken"],
                "outputs": outputs,
                "values": values,
                "stdout": stdout.decode(errors="ignore"),
                "stderr": stderr.decode(errors="ignore"),
            },
        )
        if not (
            response.status_code == 409 and response.json().get("code") == "LEASE_STALE"
        ):
            response.raise_for_status()

    async def _lease_heartbeats(self, lease: dict) -> bool:
        while not self.stop.is_set():
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=10)
                return False
            except TimeoutError:
                pass
            response = await self._post(
                f"/internal/workers/v3/leases/{lease['leaseId']}:heartbeat",
                {"leaseToken": lease["leaseToken"]},
            )
            if (
                response.status_code == 409
                and response.json().get("code") == "LEASE_STALE"
            ):
                return True
            response.raise_for_status()
        return False

    async def _persist_remote_state(self, lease: dict, state: dict) -> None:
        response = await self._post(
            f"/internal/workers/v3/leases/{lease['leaseId']}:heartbeat",
            {"leaseToken": lease["leaseToken"], "remoteState": state},
        )
        if response.status_code == 409 and response.json().get("code") == "LEASE_STALE":
            raise asyncio.CancelledError
        response.raise_for_status()

    async def _fail(
        self,
        lease: dict,
        code: str,
        stdout: bytes,
        stderr: bytes,
        *,
        remote_state: dict | None = None,
    ) -> None:
        payload = {
            "leaseToken": lease["leaseToken"],
            "errorCode": code,
            "stdout": stdout.decode(errors="ignore"),
            "stderr": stderr.decode(errors="ignore"),
        }
        if remote_state is not None:
            payload["remoteState"] = remote_state
        response = await self._post(
            f"/internal/workers/v3/leases/{lease['leaseId']}:fail",
            payload,
        )
        if not (
            response.status_code == 409 and response.json().get("code") == "LEASE_STALE"
        ):
            response.raise_for_status()

    async def _post(self, path: str, payload: dict) -> httpx.Response:
        while not self.stop.is_set():
            try:
                timeout = (
                    httpx.Timeout(30.0, read=None)
                    if path.endswith(("nodes:claim", ":complete"))
                    else httpx.Timeout(30.0)
                )
                return await self.client.post(
                    f"{self.control_url}{path}",
                    json=payload,
                    timeout=timeout,
                    headers=inject_trace_headers(),
                )
            except httpx.TransportError:
                try:
                    await asyncio.wait_for(self.stop.wait(), timeout=1)
                except TimeoutError:
                    pass
        raise asyncio.CancelledError


async def _terminate_process_group(process: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 2
    while _group_exists(process.pid) and loop.time() < deadline:
        await asyncio.sleep(0.05)
    if _group_exists(process.pid):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    await process.wait()


def _lease_traceparent(lease: dict) -> str:
    supplied = lease.get("traceparent")
    if isinstance(supplied, str):
        return supplied
    identity = str(lease.get("jobId") or lease.get("leaseId") or "worker")
    return trace_context_for_id(identity).traceparent


def _group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


def _file_identity(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return "sha256:" + digest.hexdigest(), size


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--runtime", choices=("native", "qgis", "remote_http"), required=True
    )
    parser.add_argument("--slots", type=int, required=True)
    parser.add_argument("--memory-bytes", type=int, required=True)
    args = parser.parse_args()
    asyncio.run(Worker(args.runtime, args.slots, args.memory_bytes).run())


if __name__ == "__main__":
    main()
