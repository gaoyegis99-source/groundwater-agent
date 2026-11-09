from dataclasses import replace
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
import json
import ctypes
import fcntl
import logging
import multiprocessing
import os
from pathlib import Path
import shutil
import signal
import random
import threading
import time

from ..adapters.database import Database, canonical
from ..adapters.storage import LocalStore
from ..adapters.locking import runtime_lock
from ..errors import DataProblemError
from ..outbox_delivery import OutboxDispatcher
from ..ingestion.service import prepare
from ..query.execute import execute
from ..preview.execute import execute as execute_preview
from .service import claim, heartbeat, finish


class QueryStore(LocalStore):
    def __init__(self, root, output):
        super().__init__(root)
        self.output = LocalStore(output)

    def put(self, source, limit):
        return self.output.put(source, limit)


def child(op, settings, work_path):
    # Die with the supervisor; holding a shared root lock also fences offline maintenance.
    parent = os.getppid()
    if ctypes.CDLL(None).prctl(1, signal.SIGKILL) != 0:
        raise RuntimeError("PARENT_DEATH_SIGNAL_UNAVAILABLE")
    if os.getppid() != parent:
        return
    with runtime_lock(settings.root):
        _child_work(op, settings, work_path)


def _child_work(op, settings, work_path):
    # Spawned interpreter has no inherited database connections. Remove environment credentials.
    os.environ.clear()
    work = Path(work_path)
    try:
        if op["kind"] in {"query", "preview"}:
            store = QueryStore(settings.root, work / "output")
            run = execute if op["kind"] == "query" else execute_preview
            result = run(op["payload"], store, settings, work, op["id"])
        else:
            result = prepare(op["payload"], LocalStore(settings.root), settings, work)
        response = {"result": result}
    except DataProblemError as error:
        response = {"error": {"code": error.code, "details": error.details}}
    # Unexpected programming errors terminate visibly; parent records failure and stops.
    (work / "response.json").write_text(canonical(response))


class Worker:
    def __init__(self, settings):
        self.settings = settings
        self.db = Database(
            settings.dsn, pool_min=1, pool_max=4, pool_timeout=3
        )
        self.store = LocalStore(settings.root)
        self.outbox = OutboxDispatcher(settings, self.db)
        self.stopping = threading.Event()

    def stop(self, *_):
        self.stopping.set()

    def once(self):
        with self.db.transaction() as c:
            op = claim(c, self.settings.project, self.settings.lease_seconds)
        if not op:
            return False
        work = self.settings.root / "work" / f"{op['id']}-{op['generation']}"
        work.mkdir()
        child_settings = replace(self.settings, dsn="")
        process = multiprocessing.get_context("spawn").Process(
            target=child, args=(op, child_settings, str(work))
        )
        started = time.monotonic()
        deadline = (
            (
                self.settings.query_seconds
                if op["kind"] == "query"
                else self.settings.preview_seconds
            )
            if op["kind"] in {"query", "preview"}
            else self.settings.prepare_seconds
        )
        process.start()
        error = None
        stale = False
        try:
            while process.is_alive():
                process.join(timeout=min(0.25, self.settings.lease_seconds / 4))
                with self.db.transaction() as c:
                    live = heartbeat(
                        c, op["id"], op["generation"], self.settings.lease_seconds
                    )
                if not live or self.stopping.is_set():
                    stale = True
                    break
                if time.monotonic() - started > deadline:
                    error = {"code": "OPERATION_DEADLINE_EXCEEDED", "details": {}}
                    break
            if process.is_alive():
                process.terminate()
                process.join(2)
                if process.is_alive():
                    process.kill()
                    process.join()
            if stale:
                return True
            response_path = work / "response.json"
            unexpected = error is None and (
                process.exitcode != 0 or not response_path.exists()
            )
            if unexpected:
                error = {
                    "code": "WORKER_PROCESS_FAILED",
                    "details": {"exit_code": process.exitcode},
                }
            response = (
                {"error": error} if error else json.loads(response_path.read_text())
            )
            result = response.get("result")
            if result and op["kind"] in {"query", "preview"}:
                child_store = LocalStore(work / "output")
                with child_store.open(result["object"]) as source:
                    result["object"] = self.store.put(
                        source, self.settings.result_bytes
                    )
            with self.db.transaction() as c:
                finish(c, op, result, response.get("error"), self.settings.result_hours)
            if unexpected:
                raise RuntimeError(
                    "WORKER_PROCESS_FAILED; inspect child traceback before restarting"
                )
            return True
        finally:
            if process.is_alive():
                process.kill()
                process.join()
            shutil.rmtree(work)

    def run(self):
        # One supervisor owns the local root; slots only parallelize leased work
        # inside that fenced process.
        with (self.settings.root / ".worker.lock").open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("DATA_WORKER_ALREADY_RUNNING") from None
            self.db.open()
            try:
                logging.info(
                    "Data worker capacity configured slots=%d pool_max=%d",
                    self.settings.worker_slots,
                    4,
                )
                self._run()
            finally:
                self.stopping.set()
                self.outbox.close()
                self.db.close()

    def _run(self):
        self.db.check()
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        with ThreadPoolExecutor(
            max_workers=self.settings.worker_slots,
            thread_name_prefix="data-worker-slot",
        ) as executor:
            futures = {
                executor.submit(self._run_slot)
                for _ in range(self.settings.worker_slots)
            }
            while not self.stopping.is_set():
                done, _ = wait(futures, timeout=0.25, return_when=FIRST_EXCEPTION)
                for future in done:
                    error = future.exception()
                    if error is not None:
                        self.stopping.set()
                        raise error
                    if not self.stopping.is_set():
                        raise RuntimeError("DATA_WORKER_SLOT_STOPPED")
            wait(futures)
        logging.info("Worker stopped")

    def _run_slot(self):
        delay = 0.25
        while not self.stopping.is_set():
            if self.once():
                delay = 0.25
                continue
            if self.outbox.once():
                delay = 0.25
                continue
            self.stopping.wait(delay + random.uniform(0, delay * 0.1))
            delay = min(delay * 2, 2.0)
