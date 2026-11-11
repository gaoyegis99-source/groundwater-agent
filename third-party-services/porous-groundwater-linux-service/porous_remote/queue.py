"""Durable local operation queue. Native work runs in a separate process group."""

from __future__ import annotations

import asyncio
from contextlib import closing, suppress
import hashlib
import json
import logging
import os
from pathlib import Path
import signal
import sqlite3
import sys
from uuid import uuid4

from . import MEDIA_TYPES, VERSION
from .errors import AppError
from .packages import checksum, encode

_TERMINAL = {"succeeded", "failed", "cancelled"}
_OUTPUTS = {
    "model.compile": {"model_package": "zip", "validation_report": "json"},
    "flow_transport.simulate": {"result_package": "zip"},
    "result.analyze": {"analysis_table": "json"},
    "result.render": {"rendered_figure": "png", "vtu_pvd_package": "zip"},
    "scenario.compare": {"scenario_table": "json", "result_package": "zip"},
    "map.prepare": {"geospatial_output": "geotiff"},
    "pest.calibrate": {"calibration_package": "zip", "calibrated_model_package": "zip"},
    "inversion.train": {"weights": "binary", "report_package": "zip"},
    "inversion.sample": {"posterior_samples": "npy", "report_package": "zip"},
    "agri.invert": {"posterior_arrays": "npy", "report_package": "zip"},
}


class OperationQueue:
    def __init__(self, root: Path, vendor: Path, signing_key: Path) -> None:
        self.root, self.vendor, self.signing_key = root, vendor, signing_key
        self.database = root / "operations.sqlite3"
        self.waiting: asyncio.Queue[str] = asyncio.Queue()
        self.running: dict[str, asyncio.subprocess.Process] = {}
        self.cancelled: set[str] = set()
        self.worker: asyncio.Task | None = None

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def start(self) -> None:
        with closing(self._connect()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS operations (
                id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL,
                request_digest TEXT NOT NULL, operation TEXT NOT NULL,
                input_path TEXT NOT NULL, status TEXT NOT NULL,
                error TEXT, result TEXT
            )""")
            db.execute(
                "UPDATE operations SET status='failed', error='SERVICE_INTERRUPTED' "
                "WHERE status='running'"
            )
            for item in db.execute("SELECT id FROM operations WHERE status='pending' ORDER BY rowid"):
                self.waiting.put_nowait(item["id"])
        self.worker = asyncio.create_task(self._drain())

    async def close(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
            with suppress(asyncio.CancelledError):
                await self.worker

    def lookup(self, identifier: str, *, by_request: bool = False) -> sqlite3.Row:
        with closing(self._connect()) as db:
            field = "request_id" if by_request else "id"
            row = db.execute(
                f"SELECT * FROM operations WHERE {field}=?", (identifier,)
            ).fetchone()
        if row is None:
            raise AppError("OPERATION_NOT_FOUND", "Unknown operation.", 404)
        return row

    def submit(
        self, operation: str, request_id: str, stable: dict,
        sources: dict[str, str], parameters: dict,
    ) -> dict:
        identity = "sha256:" + hashlib.sha256(encode({
            "operationType": operation, "clientRequestId": request_id,
            "inputs": stable, "parameters": parameters,
        })).hexdigest()
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT * FROM operations WHERE request_id=?", (request_id,)
            ).fetchone()
            if prior is not None:
                if prior["request_digest"] != identity:
                    raise AppError("IDEMPOTENCY_CONFLICT", "Request identifier already used.", 409)
                return self.status(prior) | {"idempotentReplay": True}
            pending = db.execute("SELECT count(*) FROM operations WHERE status='pending'").fetchone()[0]
            if pending >= 16:
                raise AppError("QUEUE_FULL", "Operation queue is full.", 429)
            identifier = uuid4().hex
            db.execute(
                "INSERT INTO operations VALUES (?,?,?,?,?,'pending',NULL,NULL)",
                (identifier, request_id, identity, operation, encode({
                    "artifacts": sources, "parameters": parameters,
                }).decode()),
            )
        self.waiting.put_nowait(identifier)
        return {"operationId": identifier, "status": "pending", "idempotentReplay": False}

    @staticmethod
    def status(row: sqlite3.Row) -> dict:
        return {
            "operationId": row["id"], "status": row["status"],
            "error": None if row["error"] is None else {"code": row["error"]},
        }

    def _update(self, identifier: str, status: str, error: str | None = None, result=None) -> None:
        with closing(self._connect()) as db, db:
            db.execute(
                "UPDATE operations SET status=?,error=?,result=? WHERE id=?",
                (status, error, encode(result).decode() if result is not None else None, identifier),
            )

    @staticmethod
    def _terminate(process: asyncio.subprocess.Process) -> None:
        # Also reaches MODFLOW/MT3D descendants launched by the upstream worker.
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)

    def cancel(self, identifier: str) -> dict:
        row = self.lookup(identifier)
        status = row["status"]
        accepted = status not in _TERMINAL
        if status == "pending":
            self._update(identifier, "cancelled")
            status = "cancelled"
        elif status == "running":
            self.cancelled.add(identifier)
            if process := self.running.get(identifier):
                self._terminate(process)
        return {
            "operationId": identifier, "status": status,
            "cancellationAccepted": accepted,
            "remoteTerminationConfirmed": status in _TERMINAL,
        }

    def result(self, identifier: str) -> dict:
        row = self.lookup(identifier)
        if row["status"] != "succeeded":
            raise AppError("RESULT_NOT_READY", "Operation result is unavailable.", 409)
        return json.loads(row["result"])

    async def _drain(self) -> None:
        while True:
            identifier = await self.waiting.get()
            try:
                row = self.lookup(identifier)
                if row["status"] == "pending":
                    await self._execute(row)
            finally:
                self.waiting.task_done()

    async def _execute(self, row: sqlite3.Row) -> None:
        identifier, operation = row["id"], row["operation"]
        self._update(identifier, "running")
        job = self.root / "jobs" / identifier
        process = None
        try:
            job.mkdir(mode=0o700)
            stored = row["input_path"]
            if stored.startswith("{"):
                entry = json.loads(stored)
                if "artifacts" in entry:
                    stored, parameters = encode(entry["artifacts"]).decode(), entry["parameters"]
                else:
                    parameters = {"includeCalibratedModel": True} if operation == "pest.calibrate" else {}
            else:
                parameters = {}
            with (job / "operation.log").open("wb") as log:
                process = await asyncio.create_subprocess_exec(
                    sys.executable, "-B", "-m", "porous_remote.runner",
                    operation, stored, str(job), str(self.vendor), str(self.signing_key),
                    cwd=Path(__file__).resolve().parents[1],
                    start_new_session=True, stdout=log, stderr=log,
                    env={name: os.environ[name] for name in (
                        "PATH", "LD_LIBRARY_PATH", "LANG", "GDAL_DRIVER_PATH", "PROJ_NETWORK",
                        "POROUS_DEVICE", "CUDA_VISIBLE_DEVICES", "CUBLAS_WORKSPACE_CONFIG",
                    )
                         if name in os.environ},
                )
            self.running[identifier] = process
            if identifier in self.cancelled:
                self._terminate(process)
            async with asyncio.timeout(21600 if operation == "inversion.train" else 7200):
                code = await process.wait()
            self.running.pop(identifier, None)
            if identifier in self.cancelled:
                self._update(identifier, "cancelled")
            elif code != 0:
                self._update(identifier, "failed", "MODEL_OPERATION_FAILED")
            else:
                result = await asyncio.to_thread(self._materialize, job, operation, parameters)
                if identifier in self.cancelled:
                    self._update(identifier, "cancelled")
                else:
                    self._update(identifier, "succeeded", result=result)
        except (TimeoutError, asyncio.CancelledError) as error:
            if process is not None:
                self._terminate(process)
                await process.wait()
            self._update(identifier, "failed", "OPERATION_TIMEOUT" if isinstance(error, TimeoutError)
                         else "SERVICE_INTERRUPTED")
            if isinstance(error, asyncio.CancelledError):
                raise
        except (OSError, ValueError):
            logging.exception("Could not execute operation %s", identifier)
            self._update(identifier, "failed", "OPERATION_RUNTIME_ERROR")
        finally:
            self.running.pop(identifier, None)
            self.cancelled.discard(identifier)

    @staticmethod
    def _materialize(job: Path, operation: str, parameters: dict) -> dict:
        outputs = []
        for port, format_ in _OUTPUTS[operation].items():
            if port == "calibrated_model_package" and parameters.get("includeCalibratedModel") is not True:
                continue
            path = job / f"{port}.{format_}"
            outputs.append({
                "port": port, "format": format_, "mediaType": MEDIA_TYPES[format_],
                "sha256": checksum(path), "sizeBytes": path.stat().st_size,
                "downloadUrl": f"/api/v1/operations/{job.name}/artifacts/{port}",
            })
        return {
            "operationId": job.name, **VERSION, "artifacts": outputs,
            "values": json.loads((job / "values.json").read_bytes()),
        }
