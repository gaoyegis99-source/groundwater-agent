"""Measure Executor file handling and concurrent heartbeats in a disposable store."""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from dataclasses import replace
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
from threading import Event, Thread
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
for source in (
    ROOT / "packages/protocol-contracts/src",
    ROOT / "services/data/client/src",
    ROOT / "services/executor/src",
):
    sys.path.insert(0, str(source))
from platform_executor_service import control as control_module  # noqa: E402
from platform_executor_service.artifacts import ArtifactStore  # noqa: E402
from platform_executor_service.contracts import DataVersionRefV2, JobRequestV6  # noqa: E402
from platform_executor_service.control_components import admission, completion, leases, recovery  # noqa: E402
from platform_executor_service.database import connect_database, initialize_database  # noqa: E402
from platform_executor_service.errors import ExecutionServiceError  # noqa: E402
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

    small = b"value\n1\n"

    def reference(version_id, size_bytes, checksum):
        return DataVersionRefV2(
            kind="data_version", service="data-primary", dataset_id="benchmark",
            version_id=version_id, semantic_id=f"{version_id}-semantics",
            object_id=f"{version_id}-object", media_type="text/csv", format_id="csv",
            size_bytes=size_bytes, checksum=checksum,
        )

    small_ref = reference("small", len(small), "sha256:" + hashlib.sha256(small).hexdigest())
    large_ref = reference("large", size, checksum)

    class Resources:
        def read_exact(self, project, resource):
            assert project == "benchmark" and resource in (small_ref, large_ref)
            metadata = {
                "checksum": resource.checksum, "size_bytes": resource.size_bytes,
                "media_type": resource.media_type, "format": resource.format_id,
            }
            return metadata, source.open("rb") if resource == large_ref else BytesIO(small)

    database = root / "executor.sqlite3"
    initialize_database(database)
    artifacts = ArtifactStore(root / "objects", root / "work")
    control = control_module.ExecutorControl(database, artifacts, config, Resources(), "benchmark")
    model = next(item for item in config["models"] if item["modelId"] == "groundwater.summary")
    input_name = model["inputBindings"]["data"]
    request = JobRequestV6(request_id="small", project_id="benchmark", model_id=model["modelId"], model_version=model["version"],
        model_checksum=model["modelChecksum"], workflow_id=model["workflow"]["workflowId"], workflow_version=model["workflow"]["version"],
        workflow_checksum=model["workflow"]["definitionChecksum"], executor_id=model["executorId"],
        arguments={**model["defaultParameters"], input_name: small_ref.version_id},
        input_references={input_name: small_ref},
        deadline_at="2099-01-01T00:00:00Z")
    capacity = config["workerRuntimes"]["native"]
    worker = {"worker_id": "benchmark", "runtime": "native", "slots": capacity["slots"],
              "memory_bytes": capacity["memoryBytes"], "catalog_digest": config["operatorCatalogDigest"]}
    control.submit(request)
    active = control.claim(worker)
    assert active is not None
    transactions, latencies, errors = [], [], []

    @contextmanager
    def measured(path):
        began = None

        class TimedConnection:
            def __getattr__(self, name):
                return getattr(connection, name)

            def execute(self, sql, parameters=()):
                nonlocal began
                cursor = connection.execute(sql, parameters)
                if sql.startswith("BEGIN"):
                    # Acquisition has returned; exclude time waiting for a writer.
                    began = time.perf_counter()
                return cursor

        try:
            with connect_database(path) as connection:
                yield TimedConnection()
        finally:
            # Include the commit/rollback and its fsync. Implicit transactions
            # without an explicit BEGIN are excluded from this lock-holding metric.
            if began is not None:
                transactions.append((time.perf_counter()-began)*1000)

    stop = Event()
    active_leases = [active]

    def heartbeats():
        while not stop.is_set():
            start = time.perf_counter()
            for current in list(active_leases):
                try:
                    control.lease_heartbeat(current["lease_id"], {"lease_token": current["lease_token"]})
                except ExecutionServiceError as error:
                    # A heartbeat may race the already committed completion.
                    diagnostics = control.node_diagnostics(current["job_id"])
                    if str(error) != "LEASE_STALE" or not any(n["node_id"] == current["node_id"] and n["status"] == "SUCCEEDED" for n in diagnostics):
                        errors.append(str(error))
                        return
            latencies.append((time.perf_counter()-start)*1000)
            stop.wait(.1)

    with ExitStack() as stack:
        for component in (admission, completion, leases, recovery):
            stack.enter_context(patch.object(component, "connect_database", measured))
        thread = Thread(target=heartbeats)
        thread.start()
        try:
            job = control.submit(replace(
                request, request_id="large", arguments={**request.arguments, input_name: large_ref.version_id},
                input_references={input_name: large_ref},
            ))
            with connect_database(database) as connection:
                connection.execute("UPDATE job_nodes SET output_contracts_json=? WHERE job_id=?",
                    (json.dumps([{"name": "summary", "kind": "value"}, {"name": "data", "kind": "artifact", "formatPairs": [{"mediaType": "text/csv", "format": "csv"}]}]), job.job_id))
            before = time.perf_counter()
            lease = control.claim({**worker, "worker_id": "benchmark-large"})
            claim_ms = (time.perf_counter()-before)*1000
            assert lease is not None and lease["job_id"] == job.job_id
            active_leases.append(lease)
            before = time.perf_counter()
            control.complete(lease["lease_id"], {"lease_token": lease["lease_token"], "values": {"summary": {"bytes": size}},
                "outputs": [{"port": "data", "path": "inputs/data", "media_type": "text/csv", "format": "csv",
                             "size_bytes": size, "checksum": checksum}]})
            complete_ms = (time.perf_counter()-before)*1000
        finally:
            stop.set()
            thread.join(5)
    assert not thread.is_alive() and not errors and latencies
    control.recover()
    node = control.node_diagnostics(job.job_id)[0]
    assert node["status"] == "SUCCEEDED"
    assert json.loads(node["outputs_json"])[0]["checksum"] == checksum
    stored = artifacts.object_path(checksum)
    assert stored.stat().st_size == size
    with stored.open("rb") as stream:
        assert "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest() == checksum
    return {"size_bytes": size, "status": "PASS", "claim_ms": round(claim_ms, 3), "complete_ms": round(complete_ms, 3),
            "explicit_write_transactions": len(transactions), "write_transaction_max_ms": round(max(transactions), 3),
            "concurrent_leases": len(active_leases), "heartbeat_count": len(latencies),
            "heartbeat_p95_ms": round(percentile(latencies, .95), 3),
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
