"""Measure Executor file handling and concurrent heartbeats in a disposable store."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import replace
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
from threading import Event, Thread
import time

ROOT = Path(__file__).resolve().parents[2]
for source in (
    ROOT / "packages/protocol-contracts/src",
    ROOT / "services/data/client/src",
    ROOT / "services/executor/src",
):
    sys.path.insert(0, str(source))
from platform_executor_service import control as control_module  # noqa: E402
from platform_executor_service.artifacts import ArtifactStore  # noqa: E402
from platform_executor_service.contracts import JobRequestV6  # noqa: E402
from platform_executor_service.database import initialize_database  # noqa: E402
from platform_executor_service.settings import load_executor_config  # noqa: E402


def percentile(values, fraction):
    return sorted(values)[min(len(values)-1, int(len(values)*fraction))] if values else None


def benchmark(config, root, size):
    source = root / "source.csv"
    block = b"123456789012345\n" * (1024 * 64)
    digest = hashlib.sha256()
    with source.open("wb") as stream:
        for _ in range(size // len(block)):
            stream.write(block)
            digest.update(block)
    checksum = "sha256:" + digest.hexdigest()

    class Resources:
        def read_exact(self, project, resource):
            data = b"value\n1\n"
            large = resource == "large"
            return ({"checksum": checksum if large else "sha256:" + hashlib.sha256(data).hexdigest(),
                     "size_bytes": size if large else len(data), "media_type": "text/csv",
                     "format": "csv"}, source.open("rb") if large else BytesIO(data))

    database = root / "executor.sqlite3"
    initialize_database(database)
    artifacts = ArtifactStore(root / "objects", root / "work")
    control = control_module.ExecutorControl(database, artifacts, config, Resources(), "benchmark")
    model = next(item for item in config["models"] if item["modelId"] == "groundwater.summary")
    request = JobRequestV6(request_id="small", project_id="benchmark", model_id=model["modelId"], model_version=model["version"],
        model_checksum=model["modelChecksum"], workflow_id=model["workflow"]["workflowId"], workflow_version=model["workflow"]["version"],
        workflow_checksum=model["workflow"]["definitionChecksum"], executor_id=model["executorId"],
        arguments={"resourceId": "small", "axis": "columns"}, input_references={},
        deadline_at="2099-01-01T00:00:00Z")
    worker = {"worker_id": "benchmark", "runtime": "native", "slots": 2, "memory_bytes": 16*1024**3,
              "catalog_digest": config["operatorCatalogDigest"]}
    control.submit(request)
    active = control.claim(worker)
    original_connect = control_module.connect_database
    transactions, latencies, errors = [], [], []

    @contextmanager
    def measured(path):
        began = None

        class TimedConnection:
            def execute(self, sql, parameters=()):
                nonlocal began
                cursor = connection.execute(sql, parameters)
                if sql.startswith("BEGIN"):
                    # Acquisition has returned; exclude time waiting for a writer.
                    began = time.perf_counter()
                return cursor

        try:
            with original_connect(path) as connection:
                yield TimedConnection()
        finally:
            # Include the commit/rollback and its fsync. Implicit transactions
            # without an explicit BEGIN are excluded from this lock-holding metric.
            if began is not None:
                transactions.append((time.perf_counter()-began)*1000)

    stop = Event()
    leases = [active]

    def heartbeats():
        while not stop.is_set():
            start = time.perf_counter()
            for current in list(leases):
                try:
                    control.lease_heartbeat(current["lease_id"], {"lease_token": current["lease_token"]})
                except control_module.ExecutionServiceError as error:
                    # A heartbeat may race the already committed completion.
                    diagnostics = control.node_diagnostics(current["job_id"])
                    if str(error) != "LEASE_STALE" or not any(n["node_id"] == current["node_id"] and n["status"] == "SUCCEEDED" for n in diagnostics):
                        errors.append(str(error))
                        return
            latencies.append((time.perf_counter()-start)*1000)
            stop.wait(.1)

    control_module.connect_database = measured
    thread = Thread(target=heartbeats)
    thread.start()
    try:
        job = control.submit(replace(request, request_id="large", arguments={"resourceId": "large", "axis": "columns"}))
        with original_connect(database) as connection:
            connection.execute("UPDATE job_nodes SET output_contracts_json=? WHERE job_id=?",
                (json.dumps([{"name": "summary", "kind": "value"}, {"name": "data", "kind": "artifact", "formatPairs": [{"mediaType": "text/csv", "format": "csv"}]}]), job.job_id))
        before = time.perf_counter()
        lease = control.claim(worker)
        claim_ms = (time.perf_counter()-before)*1000
        leases.append(lease)
        before = time.perf_counter()
        control.complete(lease["lease_id"], {"lease_token": lease["lease_token"], "values": {"summary": {"bytes": size}},
            "outputs": [{"port": "data", "path": "inputs/data", "media_type": "text/csv", "format": "csv",
                         "size_bytes": size, "checksum": checksum}]})
        complete_ms = (time.perf_counter()-before)*1000
    finally:
        stop.set()
        thread.join(5)
        control_module.connect_database = original_connect
    assert not thread.is_alive() and not errors and latencies
    control.recover()
    assert control.node_diagnostics(job.job_id)[0]["status"] == "SUCCEEDED"
    stored = artifacts.object_path(checksum)
    assert stored.stat().st_size == size
    with stored.open("rb") as stream:
        assert "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest() == checksum
    return {"size_bytes": size, "status": "PASS", "claim_ms": round(claim_ms, 3), "complete_ms": round(complete_ms, 3),
            "explicit_write_transactions": len(transactions), "write_transaction_max_ms": round(max(transactions), 3),
            "heartbeat_count": len(latencies), "heartbeat_p95_ms": round(percentile(latencies, .95), 3),
            "heartbeat_max_ms": round(max(latencies), 3),
            "heartbeat_p99_ms": round(percentile(latencies, .99), 3), "unexpected_lease_losses": len(errors)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = load_executor_config(args.config)
    reports = []
    for size in (64*1024**2, 1024**3):
        with tempfile.TemporaryDirectory(prefix="executor-io-benchmark-") as directory:
            report = benchmark(config, Path(directory), size)
            reports.append(report)
            print(json.dumps(report), flush=True)
    args.output.write_text(json.dumps(reports, indent=2)+"\n")


if __name__ == "__main__":
    main()
