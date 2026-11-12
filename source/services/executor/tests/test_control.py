from __future__ import annotations

from dataclasses import replace
import hashlib
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from platform_executor_service.app import create_app
from platform_executor_service.contracts import JobRequestV6, DataVersionRefV2
from platform_executor_service.artifacts import ArtifactStore
from platform_executor_service.control import ExecutorControl
from platform_executor_service.settings import load_executor_config
from platform_executor_service.database import (
    connect_database,
    initialize_database,
    runtime_metrics,
)
from platform_executor_service.errors import ExecutionServiceError
from platform_executor_service.results import ResultPublisher
from scripts.lib.assembly_paths import compiled_bundle


class Resources:
    def __init__(self):
        self.groups: dict[str, dict] = {}
        self.contents: dict[str, bytes] = {}
        self.stage_calls = 0
        self.commit_calls = 0

    def begin_publication(self, job_id, request, outputs, provenance):
        existing = self.groups.get(job_id)
        if existing is None:
            self.groups[job_id] = {
                "publicationId": job_id,
                "project_id": request.project_id,
                "workflow_run_id": request.request_id,
                "state": "OPEN",
                "declarations": {item["name"]: item for item in outputs},
                "provenance": dict(provenance),
                "staged": {},
                "receipt": None,
            }
        else:
            assert existing["project_id"] == request.project_id
            assert existing["declarations"] == {item["name"]: item for item in outputs}
        return self.publication_status(job_id, request)

    def publication_status(self, job_id, request):
        group = self.groups.get(job_id)
        if group is None:
            return None
        assert group["project_id"] == request.project_id
        return {
            key: value
            for key, value in group.items()
            if key
            not in {
                "declarations",
                "provenance",
                "staged",
            }
        }

    def stage_result(self, job_id, request, output_name, checksum, path):
        group = self.groups[job_id]
        assert group["state"] == "OPEN"
        assert output_name in group["declarations"]
        content = path.read_bytes()
        assert checksum == "sha256:" + hashlib.sha256(content).hexdigest()
        object_id = f"object-{job_id}-{output_name}"
        previous = group["staged"].get(output_name)
        metadata = {
            "checksum": checksum,
            "size_bytes": len(content),
            "object_id": object_id,
        }
        if previous is not None:
            assert previous == metadata
        else:
            group["staged"][output_name] = metadata
            self.contents[object_id] = content
            self.stage_calls += 1
        return metadata

    def prepare_publication(self, job_id, request):
        group = self.groups[job_id]
        assert set(group["staged"]) == set(group["declarations"])
        group["state"] = "READY"
        return self.publication_status(job_id, request)

    def commit_publication(self, job_id, request):
        group = self.groups[job_id]
        assert group["state"] == "READY"
        outputs = []
        for name, declaration in group["declarations"].items():
            outputs.append(
                {
                    "name": name,
                    "dataset_id": f"dataset-{job_id}-{name}",
                    "version_id": f"version-{job_id}-{name}",
                    "semantic_id": f"semantic-{job_id}-{name}",
                }
            )
        group["receipt"] = {
            "state": "PUBLISHED",
            "publication_id": job_id,
            "outputs": outputs,
        }
        group["state"] = "PUBLISHED"
        self.commit_calls += 1
        return group["receipt"]

    def output_references(self, receipt, request, declarations):
        references = []
        group = self.groups[receipt["publication_id"]]
        for output in receipt["outputs"]:
            name = output["name"]
            declaration = declarations[name]
            staged = group["staged"][name]
            references.append(
                DataVersionRefV2(
                    kind="data_version",
                    service="data-primary",
                    dataset_id=output["dataset_id"],
                    version_id=output["version_id"],
                    semantic_id=output["semantic_id"],
                    object_id=staged["object_id"],
                    media_type=declaration["mediaType"],
                    format_id=declaration["format"],
                    size_bytes=staged["size_bytes"],
                    checksum=staged["checksum"],
                    output_name=name,
                )
            )
        return tuple(references)

    def abort_publication(self, job_id, request):
        self.groups[job_id]["state"] = "ABORTED"
        return self.publication_status(job_id, request)

    def read_exact(self, project_id: str, reference):
        content = self.contents.get(reference.object_id, b"value\n1\n")
        assert reference.version_id
        return (
            {
                "checksum": reference.checksum,
                "media_type": reference.media_type,
                "format": reference.format_id,
                "size_bytes": reference.size_bytes,
            },
            BytesIO(content),
        )


def _control(tmp_path: Path) -> tuple[ExecutorControl, dict]:
    database = tmp_path / "state" / "executor.sqlite3"
    initialize_database(database)
    config = load_executor_config(compiled_bundle() / "executor-config.json")
    config["models"] = [
        model for model in config["models"] if model["modelId"] == "groundwater.summary"
    ]
    artifacts = ArtifactStore(tmp_path / "objects", tmp_path / "work")
    return ExecutorControl(database, artifacts, config, Resources(), "p"), config


def _remote_control(tmp_path: Path) -> tuple[ExecutorControl, dict, Resources]:
    database = tmp_path / "state" / "executor.sqlite3"
    initialize_database(database)
    config = load_executor_config(compiled_bundle() / "executor-config.json")
    config["models"] = [
        model
        for model in config["models"]
        if model["modelId"] == "groundwater.source-sustainability"
    ]
    resources = Resources()
    artifacts = ArtifactStore(tmp_path / "objects", tmp_path / "work")
    return (
        ExecutorControl(database, artifacts, config, resources, "p"),
        config,
        resources,
    )


def test_executor_database_uses_wal_and_thirty_second_busy_timeout(tmp_path) -> None:
    database = tmp_path / "executor.sqlite3"
    initialize_database(database)
    with connect_database(database) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 30_000


def _reference(
    version_id: str = "r",
    *,
    content: bytes = b"value\n1\n",
    media_type: str = "text/csv",
    format_id: str = "csv",
) -> DataVersionRefV2:
    return DataVersionRefV2(
        kind="data_version",
        service="data-primary",
        dataset_id=f"dataset-{version_id}",
        version_id=version_id,
        semantic_id=f"semantics-{version_id}",
        object_id=f"object-{version_id}",
        media_type=media_type,
        format_id=format_id,
        size_bytes=len(content),
        checksum="sha256:" + hashlib.sha256(content).hexdigest(),
    )


def _canonical_arguments(config: dict, **arguments: object) -> dict[str, object]:
    return {**config["models"][0]["defaultParameters"], **arguments}


def _summary_values(summary: dict | None = None) -> dict[str, object]:
    return {
        "summary": summary or {"count": 1},
        "details": {
            "schemaVersion": "statistics-describe-details.v1",
            "scope": {
                "basis": "FULL_STAGED_INPUT",
                "inputFormat": "csv",
                "sourceCount": 1,
                "sourceCountKind": "rows",
            },
            "selection": {
                "axis": "columns",
                "column": None,
                "groupField": None,
                "unit": "",
            },
            "series": [
                {
                    "field": {"kind": "table-column", "name": "value"},
                    "statistics": {},
                    "groups": [],
                }
            ],
            "conventions": {
                "declaredNulls": "excluded-and-counted",
                "nonFiniteValues": "excluded-and-counted",
                "stddev": "population-ddof-0",
                "quantiles": "linear-interpolation",
            },
            "interpretation": "Executor contract fixture.",
            "limitations": ["Synthetic test fixture."],
        },
    }


def _request(config: dict, request_id: str = "run-1") -> JobRequestV6:
    model = config["models"][0]
    workflow = model["workflow"]
    return JobRequestV6(
        request_id=request_id,
        project_id="p",
        model_id=model["modelId"],
        model_version=model["version"],
        model_checksum=model["modelChecksum"],
        workflow_id=workflow["workflowId"],
        workflow_version=workflow["version"],
        workflow_checksum=workflow["definitionChecksum"],
        executor_id=model["executorId"],
        arguments=_canonical_arguments(config, versionId="r"),
        input_references={"versionId": _reference()},
        deadline_at="2099-01-01T00:00:00Z",
    )


def _remote_request(config: dict, content: bytes) -> JobRequestV6:
    model = config["models"][0]
    workflow = model["workflow"]
    return JobRequestV6(
        request_id="remote-run-1",
        project_id="p",
        model_id=model["modelId"],
        model_version=model["version"],
        model_checksum=model["modelChecksum"],
        workflow_id=workflow["workflowId"],
        workflow_version=workflow["version"],
        workflow_checksum=workflow["definitionChecksum"],
        executor_id=model["executorId"],
        arguments={"assessmentVersionId": "r"},
        input_references={
            "assessmentVersionId": _reference(
                content=content,
                media_type="application/json",
                format_id="json",
            )
        },
        deadline_at="2099-01-01T00:00:00Z",
    )


def _worker(config: dict, runtime: str = "native") -> dict:
    capacity = config["workerRuntimes"][runtime]
    return {
        "worker_id": runtime,
        "runtime": runtime,
        "catalog_digest": config["operatorCatalogDigest"],
        "slots": capacity["slots"],
        "memory_bytes": capacity["memoryBytes"],
    }


def test_submit_is_idempotent_and_materializes_one_node_set(tmp_path) -> None:
    control, config = _control(tmp_path)
    first = control.submit(_request(config))
    second = control.submit(_request(config))
    assert first.job_id == second.job_id
    with connect_database(control.database) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM executor_jobs").fetchone()[0] == 1
        )
        assert connection.execute("SELECT COUNT(*) FROM job_nodes").fetchone()[0] == 1
        parameters = connection.execute(
            "SELECT parameters_json FROM job_nodes"
        ).fetchone()["parameters_json"]
    assert json.loads(parameters) == {"axis": "columns", "unit": ""}


@pytest.mark.parametrize("supplied", [True, False])
def test_model_parameter_binding_reaches_the_worker(tmp_path, supplied) -> None:
    control, config = _control(tmp_path)
    model = config["models"][0]
    model["parameterBindings"]["axis"] = "orientation"
    model["tool"]["inputSchema"]["properties"]["orientation"] = model["tool"][
        "inputSchema"
    ]["properties"].pop("axis")
    model["defaultParameters"] = {}
    arguments = {"versionId": "r"}
    if supplied:
        arguments["orientation"] = "flatten"
    control.submit(replace(_request(config), arguments=arguments))

    lease = control.claim(_worker(config))

    assert lease["parameters"] == ({"axis": "flatten"} if supplied else {})


def test_same_bytes_keep_each_resource_format_through_claim_and_result(
    tmp_path,
) -> None:
    control, config = _control(tmp_path)
    content = b"1\n"
    checksum = "sha256:" + hashlib.sha256(content).hexdigest()

    class SharedBytes(Resources):
        def read_exact(self, project_id: str, reference):
            media_type, format_id = {
                "csv-resource": ("text/csv", "csv"),
                "json-resource": ("application/json", "json"),
            }[reference.version_id]
            return (
                {
                    "checksum": checksum,
                    "media_type": media_type,
                    "format": format_id,
                    "size_bytes": len(content),
                },
                BytesIO(content),
            )

    resources = SharedBytes()
    control.data_client = resources
    first = control.submit(
        replace(
            _request(config, "csv-job"),
            arguments=_canonical_arguments(config, versionId="csv-resource"),
            input_references={
                "versionId": _reference(
                    "csv-resource",
                    content=content,
                    media_type="text/csv",
                    format_id="csv",
                )
            },
        )
    )
    csv_lease = control.claim(_worker(config))
    assert csv_lease["inputs"]["data"][0]["mediaType"] == "text/csv"
    assert csv_lease["inputs"]["data"][0]["format"] == "csv"
    control.complete(
        csv_lease["lease_id"],
        {
            "lease_token": csv_lease["lease_token"],
            "outputs": [],
            "values": _summary_values(),
        },
    )

    second = control.submit(
        replace(
            _request(config, "json-job"),
            arguments=_canonical_arguments(config, versionId="json-resource"),
            input_references={
                "versionId": _reference(
                    "json-resource",
                    content=content,
                    media_type="application/json",
                    format_id="json",
                )
            },
        )
    )
    with connect_database(control.database) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM content_objects").fetchone()[0]
            == 1
        )
    json_lease = control.claim(_worker(config))
    assert json_lease is not None
    assert json_lease["job_id"] == second.job_id
    assert json_lease["inputs"]["data"][0]["mediaType"] == "application/json"
    assert json_lease["inputs"]["data"][0]["format"] == "json"
    control.complete(
        json_lease["lease_id"],
        {
            "lease_token": json_lease["lease_token"],
            "outputs": [],
            "values": _summary_values(),
        },
    )
    publisher = ResultPublisher(control.database, control.artifacts, config, resources)
    publisher.publish_ready()
    publisher.publish_ready()
    assert control.get(first.job_id).status == "SUCCEEDED"
    assert control.get(second.job_id).status == "SUCCEEDED"
    assert first.job_id != second.job_id


def test_execution_metadata_is_durable_and_reaches_the_worker(tmp_path) -> None:
    control, config = _control(tmp_path)
    expected = {
        "dataContract": {
            "timezone": "Asia/Shanghai",
            "nullValues": ["", "NA"],
            "types": {"f1": "float64"},
            "fields": [],
        }
    }

    class SemanticResources(Resources):
        def read_exact(self, project_id: str, reference):
            details, source = super().read_exact(project_id, reference)
            return {**details, "execution_metadata": expected}, source

    control.data_client = SemanticResources()
    control.submit(_request(config))
    lease = control.claim(_worker(config))

    assert lease["inputs"]["data"][0]["metadata"] == expected


def test_existing_workflow_input_pin_keeps_its_metadata_without_new_pin(
    tmp_path,
) -> None:
    control, config = _control(tmp_path)
    job = control.submit(_request(config))
    with connect_database(control.database) as connection:
        connection.execute(
            "DELETE FROM job_object_pins WHERE job_id=? AND purpose LIKE 'workflow-input-metadata:%'",
            (job.job_id,),
        )
    lease = control.claim(_worker(config))
    assert lease is None
    assert control.get(job.job_id).error == "CHECKPOINT_MISSING"


def test_metadata_pins_distinguish_overlapping_workflow_input_names(tmp_path) -> None:
    control, config = _control(tmp_path)
    model = config["models"][0]
    original_input = model["workflow"]["inputs"][0]
    model["workflow"]["inputs"] = [
        {**original_input, "name": "a"},
        {**original_input, "name": "a:b"},
    ]
    model["workflow"]["nodes"][0]["inputs"]["data"] = {"workflowInput": "a"}
    model["inputBindings"] = {"a": "csvResourceId", "a:b": "jsonResourceId"}
    schema = model["tool"]["inputSchema"]
    resource_schema = schema["properties"].pop("versionId")
    schema["properties"].update(
        csvResourceId=resource_schema, jsonResourceId=resource_schema
    )
    schema["required"] = ["csvResourceId", "jsonResourceId"]
    content = b"1\n"
    checksum = "sha256:" + hashlib.sha256(content).hexdigest()

    class SharedBytes(Resources):
        def read_exact(self, project_id: str, reference):
            media_type, format_id = {
                "csv-resource": ("text/csv", "csv"),
                "json-resource": ("application/json", "json"),
            }[reference.version_id]
            return (
                {
                    "checksum": checksum,
                    "media_type": media_type,
                    "format": format_id,
                    "size_bytes": len(content),
                },
                BytesIO(content),
            )

    resources = SharedBytes()
    control.data_client = resources
    request = replace(
        _request(config),
        arguments=_canonical_arguments(
            config,
            csvResourceId="csv-resource",
            jsonResourceId="json-resource",
        ),
        input_references={
            "csvResourceId": _reference(
                "csv-resource", content=content, media_type="text/csv", format_id="csv"
            ),
            "jsonResourceId": _reference(
                "json-resource",
                content=content,
                media_type="application/json",
                format_id="json",
            ),
        },
    )
    control.submit(request)
    with connect_database(control.database) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM content_objects").fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM job_object_pins WHERE purpose LIKE 'workflow-input-metadata:%'"
            ).fetchone()[0]
            == 2
        )
    lease = control.claim(_worker(config))
    assert lease["inputs"]["data"][0]["format"] == "csv"
    assert {
        ref.version_id: ref.format_id for ref in request.input_references.values()
    } == {
        "csv-resource": "csv",
        "json-resource": "json",
    }


def test_completion_only_checkpoints_and_does_not_publish_from_worker_request(
    tmp_path,
) -> None:
    control, config = _control(tmp_path)
    status = control.submit(_request(config))
    lease = control.claim(_worker(config))
    control.complete(
        lease["lease_id"],
        {
            "lease_token": lease["lease_token"],
            "outputs": [],
            "values": _summary_values({"count": 1, "mean": 1.5}),
            "stdout": "",
            "stderr": "",
        },
    )
    assert control.get(status.job_id).status == "RUNNING"
    assert control.node_diagnostics(status.job_id)[0]["status"] == "SUCCEEDED"


def test_completion_rejects_missing_value_ports_before_success(tmp_path) -> None:
    control, config = _control(tmp_path)
    status = control.submit(_request(config))
    lease = control.claim(_worker(config))
    with pytest.raises(ExecutionServiceError, match="NODE_OUTPUT_INVALID"):
        control.complete(
            lease["lease_id"],
            {
                "lease_token": lease["lease_token"],
                "outputs": [],
                "values": {},
                "stdout": "",
                "stderr": "",
            },
        )
    assert control.node_diagnostics(status.job_id)[0]["status"] == "LEASED"


def test_publication_after_recovery_and_new_job_reexecutes_worker(tmp_path) -> None:
    control, config = _control(tmp_path)
    resources = Resources()
    first = control.submit(_request(config))
    lease = control.claim(_worker(config))
    control.complete(
        lease["lease_id"],
        {
            "lease_token": lease["lease_token"],
            "outputs": [],
            "values": _summary_values(),
            "stdout": "",
            "stderr": "",
        },
    )
    recovered = ExecutorControl(
        control.database, control.artifacts, config, resources, "p"
    )
    recovered.recover()
    assert recovered.claim(_worker(config)) is None
    publisher = ResultPublisher(control.database, control.artifacts, config, resources)
    publisher.publish_ready()
    publisher.publish_ready()
    assert resources.stage_calls == 2
    assert recovered.get(first.job_id).status == "SUCCEEDED"

    second = recovered.submit(_request(config, "run-2"))
    second_lease = recovered.claim(_worker(config))
    assert second_lease["job_id"] == second.job_id
    recovered.complete(
        second_lease["lease_id"],
        {
            "lease_token": second_lease["lease_token"],
            "outputs": [],
            "values": _summary_values(),
            "stdout": "",
            "stderr": "",
        },
    )
    publisher.publish_ready()
    publisher.publish_ready()
    assert recovered.get(second.job_id).status == "SUCCEEDED"
    assert resources.stage_calls == 4
    published_contents = set(resources.contents.values())
    assert b'{"count":1}' in published_contents
    assert len(published_contents) == 2
    assert {item.output_name for item in recovered.get(second.job_id).outputs} == {
        "summary",
        "summary-details",
    }


def test_job_api_does_not_translate_internal_value_errors(
    tmp_path, monkeypatch
) -> None:
    control, config = _control(tmp_path)

    def broken_submit(_request):
        raise ValueError("internal invariant failed")

    monkeypatch.setattr(control, "submit", broken_submit)
    application = SimpleNamespace(control=control, project_id="p")
    client = TestClient(create_app(application), raise_server_exceptions=False)
    assert client.post("/internal/execution/v6/jobs", json={}).status_code == 422
    assert (
        client.post(
            "/internal/execution/v6/jobs",
            json=_request(config).as_dict(),
        ).status_code
        == 500
    )


def test_runtime_match_capacity_and_stale_token(tmp_path) -> None:
    control, config = _control(tmp_path)
    control.submit(_request(config))
    assert control.claim(_worker(config, "qgis")) is None
    lease = control.claim(_worker(config))["lease_id"]
    with pytest.raises(ExecutionServiceError, match="LEASE_STALE"):
        control.lease_heartbeat(lease, {"lease_token": "old"})


def test_first_lost_lease_retries_and_second_fails(tmp_path) -> None:
    control, config = _control(tmp_path)
    status = control.submit(_request(config))
    first = control.claim(_worker(config))
    with connect_database(control.database) as connection:
        connection.execute(
            "UPDATE node_attempts SET lease_expires_at = '2000-01-01T00:00:00Z' WHERE lease_id = ?",
            (first["lease_id"],),
        )
    control.reconcile()
    second = control.claim(_worker(config))
    with connect_database(control.database) as connection:
        connection.execute(
            "UPDATE node_attempts SET lease_expires_at = '2000-01-01T00:00:00Z' WHERE lease_id = ?",
            (second["lease_id"],),
        )
    control.reconcile()
    assert control.get(status.job_id).error == "WORKER_LOST"
    (diagnostic,) = control.node_diagnostics(status.job_id)
    assert diagnostic["attempt_count"] == 2


def test_recovery_metrics_observe_lost_lease_and_late_workspace_gc(tmp_path) -> None:
    control, config = _control(tmp_path)
    control.submit(_request(config))
    lease = control.claim(_worker(config))
    orphan = control.artifacts.object_root / "ff" / "orphan"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"unreferenced")
    with connect_database(control.database) as connection:
        connection.execute(
            "UPDATE node_attempts SET lease_expires_at='2000-01-01T00:00:00Z' "
            "WHERE lease_id=?",
            (lease["lease_id"],),
        )
    assert runtime_metrics(control.database)["expired_leases"] == 1

    control.reconcile()
    assert not Path(lease["workspace"]).exists()
    assert not orphan.exists()
    with pytest.raises(ExecutionServiceError, match="LEASE_STALE"):
        control.lease_heartbeat(
            lease["lease_id"], {"lease_token": lease["lease_token"]}
        )
    assert runtime_metrics(control.database)["expired_leases"] == 0
    assert runtime_metrics(control.database)["lost_leases"] == 1
    assert control.recovery.metrics() == {
        "lease_lost": 1,
        "attempt_workspace_removed": 1,
        "orphan_object_removed": 1,
    }
    control.reconcile()
    assert control.recovery.metrics()["lease_lost"] == 1
    assert control.recovery.metrics()["attempt_workspace_removed"] == 1

    application = SimpleNamespace(
        control=control, database=control.database, project_id="p"
    )
    client = TestClient(create_app(application))
    try:
        metrics = client.get("/internal/metrics")
    finally:
        client.close()
    assert metrics.status_code == 200
    assert 'platform_executor_lease_lost_total{service="executor"} 1' in metrics.text
    assert (
        'platform_executor_lease_attempts{service="executor",state="lost_retained"} 1'
        in metrics.text
    )
    assert (
        'platform_executor_gc_removed_total{kind="attempt_workspace",service="executor"} 1'
        in metrics.text
    )
    assert (
        'platform_executor_gc_removed_total{kind="orphan_object",service="executor"} 1'
        in metrics.text
    )
    assert lease["lease_id"] not in metrics.text


def test_concurrent_identical_submissions_return_one_job(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    control, config = _control(tmp_path)
    barrier = Barrier(2)
    original = control.data_client.read_exact

    def synchronized_read(*args):
        barrier.wait(timeout=5)
        return original(*args)

    monkeypatch.setattr(control.data_client, "read_exact", synchronized_read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(control.submit, _request(config)) for _ in range(2)]
        jobs = [future.result(timeout=5) for future in futures]
    assert jobs[0].job_id == jobs[1].job_id
    assert len(control.node_diagnostics(jobs[0].job_id)) == 1
    assert not list(control.artifacts.object_root.glob("*.part"))


def test_expired_submission_creates_no_job(tmp_path):
    control, config = _control(tmp_path)
    with pytest.raises(ExecutionServiceError, match="JOB_DEADLINE_EXCEEDED"):
        control.submit(replace(_request(config), deadline_at="2000-01-01T00:00:00Z"))
    with connect_database(control.database) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM executor_jobs").fetchone()[0] == 0
        )


def test_finalizing_cancel_is_resolved_by_publication_abort(tmp_path):
    control, config = _control(tmp_path)
    job = control.submit(_request(config))
    with connect_database(control.database) as connection:
        connection.execute(
            "UPDATE executor_jobs SET stage='FINALIZING', deadline_at='2000-01-01T00:00:00Z'"
        )
    assert control.cancel(job.job_id).status == "RUNNING"
    control.reconcile()
    assert control.get(job.job_id).status == "RUNNING"
    ResultPublisher(
        control.database, control.artifacts, config, control.data_client
    ).publish_ready()
    assert control.get(job.job_id).status == "CANCELLED"


def _completed_job(control, config, request_id="run-1"):
    job = control.submit(_request(config, request_id))
    lease = control.claim(_worker(config))
    control.complete(
        lease["lease_id"],
        {
            "lease_token": lease["lease_token"],
            "outputs": [],
            "values": _summary_values(),
            "stdout": "",
            "stderr": "",
        },
    )
    return job


class DurableResults(Resources):
    def __init__(self):
        super().__init__()
        self.lose_response = False

    def commit_publication(self, job_id, request):
        from platform_executor_service.data_client import DataUnavailableError

        result = super().commit_publication(job_id, request)
        if self.lose_response:
            raise DataUnavailableError("DATA_SERVICE_UNAVAILABLE")
        return result


def test_finalizing_cancel_during_named_output_stage_aborts(tmp_path, monkeypatch):
    control, config = _control(tmp_path)
    job = _completed_job(control, config)
    resources = DurableResults()
    stage = resources.stage_result

    def check_cancel(*args):
        assert control.cancel(job.job_id).status == "RUNNING"
        return stage(*args)

    monkeypatch.setattr(resources, "stage_result", check_cancel)
    publisher = ResultPublisher(control.database, control.artifacts, config, resources)
    publisher.publish_ready()
    publisher.publish_ready()
    assert resources.stage_calls == 2
    assert control.get(job.job_id).status == "CANCELLED"


def test_lost_commit_response_recovers_after_deadline(tmp_path):
    import shutil

    control, config = _control(tmp_path)
    job = _completed_job(control, config)
    resources = DurableResults()
    control.data_client = resources
    resources.lose_response = True
    publisher = ResultPublisher(control.database, control.artifacts, config, resources)
    publisher.publish_ready()
    assert control.get(job.job_id).status == "RUNNING"
    publisher.publish_ready()
    assert control.get(job.job_id).status == "RUNNING"
    with connect_database(control.database) as connection:
        assert (
            connection.execute(
                "SELECT stage FROM executor_jobs WHERE job_id=?", (job.job_id,)
            ).fetchone()[0]
            == "COMMIT_PENDING"
        )
    assert control.cancel(job.job_id).status == "RUNNING"
    with connect_database(control.database) as connection:
        connection.execute(
            "UPDATE executor_jobs SET deadline_at='2000-01-01T00:00:00Z'"
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM job_object_pins WHERE job_id=?", (job.job_id,)
            ).fetchone()[0]
            > 0
        )
    shutil.rmtree(control.artifacts.work_root)
    control.recover()
    control.reconcile()
    assert control.get(job.job_id).status == "RUNNING"
    resources.lose_response = False
    ResultPublisher(
        control.database, control.artifacts, config, resources
    ).publish_ready()
    assert control.get(job.job_id).status == "SUCCEEDED"
    assert resources.stage_calls == 2
    assert len(control.node_diagnostics(job.job_id)) == 1


def test_remote_success_local_commit_failure_recovers_once(tmp_path):
    import sqlite3

    control, config = _control(tmp_path)
    job = _completed_job(control, config)
    resources = DurableResults()
    control.data_client = resources
    publisher = ResultPublisher(control.database, control.artifacts, config, resources)
    with connect_database(control.database) as connection:
        connection.execute(
            "CREATE TRIGGER reject_success BEFORE UPDATE OF status ON executor_jobs WHEN NEW.status='SUCCEEDED' BEGIN SELECT RAISE(ABORT, 'injected commit failure'); END"
        )
    publisher.publish_ready()
    with pytest.raises(sqlite3.IntegrityError, match="injected commit failure"):
        publisher.publish_ready()
    assert control.get(job.job_id).status == "RUNNING"
    with connect_database(control.database) as connection:
        assert (
            connection.execute(
                "SELECT stage FROM executor_jobs WHERE job_id=?", (job.job_id,)
            ).fetchone()[0]
            == "COMMIT_PENDING"
        )
    with connect_database(control.database) as connection:
        connection.execute("DROP TRIGGER reject_success")
    publisher.publish_ready()
    assert resources.stage_calls == 2
    assert control.get(job.job_id).status == "SUCCEEDED"


def test_preparing_inputs_holds_no_write_lock_and_cancel_wins(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    control, config = _control(tmp_path)
    first = control.submit(_request(config, "first"))
    active = control.claim(_worker(config))
    second = control.submit(_request(config, "second"))
    entered, release = Event(), Event()
    prepare = control.artifacts.prepare_workspace

    def paused(*args):
        entered.set()
        assert release.wait(5)
        prepare(*args)

    monkeypatch.setattr(control.artifacts, "prepare_workspace", paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(control.claim, _worker(config))
        assert entered.wait(5)
        try:
            pool.submit(
                control.lease_heartbeat,
                active["lease_id"],
                {"lease_token": active["lease_token"]},
            ).result(2)
            pool.submit(control.cancel, second.job_id).result(2)
        finally:
            release.set()
        assert pending.result(5) is None
    assert control.get(first.job_id).status == "RUNNING"
    assert control.get(second.job_id).status == "CANCELLED"


def test_staging_outputs_holds_no_write_lock_and_stale_completion_is_rejected(
    tmp_path, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    control, config = _control(tmp_path)
    job = control.submit(_request(config))
    lease = control.claim(_worker(config))
    entered, release = Event(), Event()
    stage = control.artifacts.stage_outputs

    def paused(*args):
        entered.set()
        assert release.wait(5)
        return stage(*args)

    monkeypatch.setattr(control.artifacts, "stage_outputs", paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(
            control.complete,
            lease["lease_id"],
            {
                "lease_token": lease["lease_token"],
                "outputs": [],
                "values": _summary_values(),
            },
        )
        assert entered.wait(5)
        try:
            pool.submit(
                control.lease_heartbeat,
                lease["lease_id"],
                {"lease_token": lease["lease_token"]},
            ).result(2)
            pool.submit(control.cancel, job.job_id).result(2)
        finally:
            release.set()
        with pytest.raises(ExecutionServiceError, match="LEASE_STALE"):
            pending.result(5)
    assert control.get(job.job_id).status == "CANCELLED"


def test_request_lookup_and_conflict_are_http_contracts(tmp_path):
    control, config = _control(tmp_path)
    client = TestClient(create_app(SimpleNamespace(control=control, project_id="p")))
    body = _request(config).as_dict()
    first = client.post("/internal/execution/v6/jobs", json=body)
    assert first.status_code == 202
    url = "/internal/execution/v6/jobs/by-request/run-1"
    assert (
        client.get(url, params={"projectId": "p"}).json()["jobId"]
        == first.json()["jobId"]
    )
    assert client.get(url, params={"projectId": "other"}).status_code == 404
    assert client.get(url + "-missing", params={"projectId": "p"}).status_code == 404
    body["arguments"]["axis"] = "flatten"
    assert client.post("/internal/execution/v6/jobs", json=body).status_code == 409


def test_foreign_stored_job_cannot_be_read_cancelled_or_diagnosed(tmp_path):
    control, config = _control(tmp_path)
    job = control.submit(_request(config))
    with connect_database(control.database) as connection:
        row = connection.execute(
            "SELECT request_json FROM executor_jobs WHERE job_id = ?", (job.job_id,)
        ).fetchone()
        request = json.loads(row["request_json"])
        request["project_id"] = "other"
        connection.execute(
            "UPDATE executor_jobs SET request_json = ? WHERE job_id = ?",
            (json.dumps(request), job.job_id),
        )
    client = TestClient(create_app(SimpleNamespace(control=control, project_id="p")))
    base = f"/internal/execution/v6/jobs/{job.job_id}"
    assert client.get(base).status_code == 404
    assert client.post(base + "/cancel").status_code == 404
    assert client.get(base + "/nodes").status_code == 404
    assert (
        client.get(
            "/internal/execution/v6/jobs/by-request/run-1", params={"projectId": "p"}
        ).status_code
        == 404
    )
    with connect_database(control.database) as connection:
        assert (
            connection.execute(
                "SELECT status FROM executor_jobs WHERE job_id = ?", (job.job_id,)
            ).fetchone()["status"]
            != "CANCELLED"
        )


def test_cancelled_lease_releases_worker_capacity_and_does_not_recover(tmp_path):
    control, config = _control(tmp_path)
    config["workerRuntimes"]["native"]["slots"] = 1
    worker = _worker(config)
    job = control.submit(_request(config))
    first = control.claim(worker)
    assert control.cancel(job.job_id).status == "CANCELLED"
    control.recover()
    control.reconcile()
    assert control.node_diagnostics(job.job_id)[0]["status"] == "CANCELLED"
    next_job = control.submit(_request(config, "next-run"))
    second = control.claim(worker)
    assert second["job_id"] == next_job.job_id
    assert second["workspace"] != first["workspace"]
    with pytest.raises(ExecutionServiceError, match="LEASE_STALE"):
        control.lease_heartbeat(
            first["lease_id"], {"lease_token": first["lease_token"]}
        )


def test_concurrent_different_payloads_conflict_without_deleting_winner_inputs(
    tmp_path, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    control, config = _control(tmp_path)
    barrier = Barrier(2)
    original = control.data_client.read_exact

    def read(*args):
        barrier.wait(3)
        return original(*args)

    monkeypatch.setattr(control.data_client, "read_exact", read)
    requests = [
        _request(config),
        replace(
            _request(config),
            arguments=_canonical_arguments(config, versionId="r", axis="flatten"),
        ),
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(control.submit, request) for request in requests]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result(15))
            except ExecutionServiceError as error:
                outcomes.append(str(error))
    assert outcomes.count("JOB_REQUEST_ID_CONFLICT") == 1
    winner = next(item for item in outcomes if not isinstance(item, str))
    assert control.claim(_worker(config))["job_id"] == winner.job_id
    assert not list(control.artifacts.object_root.glob(".inputs-*"))


def test_publication_identity_conflict_keeps_unknown_creation_nonterminal(tmp_path):
    control, config = _control(tmp_path)
    job = _completed_job(control, config)

    class ConflictingResources(Resources):
        def begin_publication(self, job_id, request, outputs, provenance):
            from platform_executor_service.data_client import DataIntegrityError

            super().begin_publication(job_id, request, outputs, provenance)
            raise DataIntegrityError("DATA_PUBLICATION_CONFLICT")

    resources = ConflictingResources()
    ResultPublisher(
        control.database, control.artifacts, config, resources
    ).publish_ready()
    status = control.get(job.job_id)
    assert status.status == "RUNNING"
    assert job.job_id in resources.groups
    assert status.outputs == ()


def test_lost_publication_begin_response_is_resolved_before_cancel(tmp_path):
    from platform_executor_service.data_client import DataUnavailableError

    control, config = _control(tmp_path)
    job = _completed_job(control, config)

    class LostBeginResponse(Resources):
        def __init__(self):
            super().__init__()
            self.begin_calls = 0

        def begin_publication(self, job_id, request, outputs, provenance):
            self.begin_calls += 1
            result = super().begin_publication(job_id, request, outputs, provenance)
            if self.begin_calls == 1:
                raise DataUnavailableError("DATA_SERVICE_UNAVAILABLE")
            return result

    resources = LostBeginResponse()
    publisher = ResultPublisher(control.database, control.artifacts, config, resources)
    publisher.publish_ready()
    with connect_database(control.database) as connection:
        state = connection.execute(
            "SELECT stage, publication_id FROM executor_jobs WHERE job_id=?",
            (job.job_id,),
        ).fetchone()
    assert (state["stage"], state["publication_id"]) == ("FINALIZING", None)
    assert resources.groups[job.job_id]["state"] == "OPEN"

    assert control.cancel(job.job_id).status == "RUNNING"
    publisher.publish_ready()

    assert control.get(job.job_id).status == "CANCELLED"
    assert resources.groups[job.job_id]["state"] == "ABORTED"
    assert resources.begin_calls == 2


def test_missing_remote_publication_does_not_make_finalizing_cancel_terminal(tmp_path):
    control, config = _control(tmp_path)
    job = _completed_job(control, config)
    resources = Resources()
    with connect_database(control.database) as connection:
        connection.execute(
            "UPDATE executor_jobs SET stage='FINALIZING', publication_id=? WHERE job_id=?",
            ("missing-publication", job.job_id),
        )
    assert control.cancel(job.job_id).status == "RUNNING"

    ResultPublisher(
        control.database, control.artifacts, config, resources
    ).publish_ready()

    assert control.get(job.job_id).status == "RUNNING"


def test_published_remote_result_is_not_reported_as_cancelled(tmp_path):
    control, config = _control(tmp_path)
    job = _completed_job(control, config)
    resources = Resources()
    resources.groups[job.job_id] = {
        "publicationId": job.job_id,
        "project_id": "p",
        "state": "PUBLISHED",
    }
    with connect_database(control.database) as connection:
        connection.execute(
            "UPDATE executor_jobs SET stage='FINALIZING', publication_id=? WHERE job_id=?",
            (job.job_id, job.job_id),
        )
    assert control.cancel(job.job_id).status == "RUNNING"

    ResultPublisher(
        control.database, control.artifacts, config, resources
    ).publish_ready()

    assert control.get(job.job_id).status == "RUNNING"


def _artifact_complete(control, lease, content=b"value\n1\n"):
    output = Path(lease["workspace"]) / "outputs" / "result.csv"
    output.write_bytes(content)
    control.complete(
        lease["lease_id"],
        {
            "lease_token": lease["lease_token"],
            "values": {},
            "outputs": [
                {
                    "port": "data",
                    "path": "outputs/result.csv",
                    "media_type": "text/csv",
                    "format": "csv",
                    "size_bytes": len(content),
                    "checksum": "sha256:" + hashlib.sha256(content).hexdigest(),
                }
            ],
        },
    )


def test_parallel_branches_join_and_successful_nodes_survive_restart(tmp_path):
    from copy import deepcopy

    control, config = _control(tmp_path)
    model = config["models"][0]
    original = model["workflow"]["nodes"][0]
    branches = []
    for name in ("left", "right"):
        node = deepcopy(original)
        node.update(
            nodeId=name,
            outputContracts=[
                {
                    "name": "data",
                    "kind": "artifact",
                    "mediaTypes": ["text/csv"],
                    "formats": ["csv"],
                    "formatPairs": [{"mediaType": "text/csv", "format": "csv"}],
                }
            ],
        )
        branches.append(node)
    join = deepcopy(original)
    join.update(
        nodeId="join",
        inputs={
            name: {"nodeOutput": {"nodeId": name, "port": "data"}}
            for name in ("left", "right")
        },
    )
    model["workflow"]["nodes"] = [*branches, join]
    model["maxParallelNodes"] = 2
    job = control.submit(_request(config))
    left, right = control.claim(_worker(config)), control.claim(_worker(config))
    assert {left["node_id"], right["node_id"]} == {"left", "right"}
    assert left["operator_id"] == right["operator_id"]
    assert left["workspace"] != right["workspace"]
    assert control.claim(_worker(config)) is None
    _artifact_complete(control, left, b"value\n1\n")
    assert control.claim(_worker(config)) is None
    control.recover()
    assert (
        next(
            row
            for row in control.node_diagnostics(job.job_id)
            if row["node_id"] == left["node_id"]
        )["status"]
        == "SUCCEEDED"
    )
    resumed = control.claim(_worker(config))
    assert resumed["node_id"] == right["node_id"]
    _artifact_complete(control, resumed, b"value\n2\n")
    joined = control.claim(_worker(config))
    assert joined["node_id"] == "join"
    assert {
        Path(items[0]["path"]).read_bytes() for items in joined["inputs"].values()
    } == {b"value\n1\n", b"value\n2\n"}
    control.complete(
        joined["lease_id"],
        {
            "lease_token": joined["lease_token"],
            "outputs": [],
            "values": _summary_values({"count": 2}),
        },
    )
    assert all(
        row["status"] == "SUCCEEDED" for row in control.node_diagnostics(job.job_id)
    )


def test_submission_still_downloading_cannot_create_after_post_deadline_absence(
    tmp_path, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime, timezone
    from threading import Event
    from platform_executor_service.control_components import (
        admission as admission_module,
    )
    from platform_executor_service.errors import JobNotFoundError

    control, config = _control(tmp_path)
    entered, release = Event(), Event()
    read = control.data_client.read_exact
    instant = datetime(2029, 1, 1, tzinfo=timezone.utc)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant

    def download(*args):
        entered.set()
        assert release.wait(5)
        return read(*args)

    monkeypatch.setattr(admission_module, "datetime", Clock)
    monkeypatch.setattr(control.data_client, "read_exact", download)
    request = replace(_request(config), deadline_at="2030-01-01T00:00:00Z")
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(control.submit, request)
        assert entered.wait(5)
        try:
            instant = datetime(2031, 1, 1, tzinfo=timezone.utc)
            with pytest.raises(JobNotFoundError):
                control.get_by_request("p", request.request_id)
        finally:
            release.set()
        with pytest.raises(ExecutionServiceError, match="JOB_DEADLINE_EXCEEDED"):
            pending.result(5)
    with connect_database(control.database) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM executor_jobs").fetchone()[0] == 0
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM content_objects").fetchone()[0]
            == 0
        )


def test_deadline_before_finalizing_prevents_publication_claim(tmp_path):
    control, config = _control(tmp_path)
    job = _completed_job(control, config)
    resources = DurableResults()
    with connect_database(control.database) as connection:
        connection.execute(
            "UPDATE executor_jobs SET deadline_at='2000-01-01T00:00:00Z'"
        )
    ResultPublisher(
        control.database, control.artifacts, config, resources
    ).publish_ready()
    assert resources.groups == {}
    with connect_database(control.database) as connection:
        assert (
            connection.execute(
                "SELECT stage FROM executor_jobs WHERE job_id=?", (job.job_id,)
            ).fetchone()[0]
            == "DAG"
        )
    control.reconcile()
    assert control.get(job.job_id).status == "TIMED_OUT"


@pytest.mark.parametrize("operation", ["reconcile", "recover"])
def test_failed_parallel_job_cannot_revive_another_attempt_from_old_snapshot(
    tmp_path, operation
):
    from copy import deepcopy

    control, config = _control(tmp_path)
    model = config["models"][0]
    original = model["workflow"]["nodes"][0]
    model["workflow"]["nodes"] = [
        dict(deepcopy(original), nodeId=name) for name in ("a", "b")
    ]
    model["maxParallelNodes"] = 2
    job = control.submit(_request(config))
    control.claim(_worker(config))
    control.claim(_worker(config))
    with connect_database(control.database) as connection:
        connection.execute("UPDATE job_nodes SET lost_count=1 WHERE node_id='a'")
        connection.execute(
            "UPDATE node_attempts SET lease_expires_at='2000-01-01T00:00:00Z'"
        )
    getattr(control, operation)()
    assert control.get(job.job_id).status == "FAILED"
    assert [node["status"] for node in control.node_diagnostics(job.job_id)] == [
        "FAILED",
        "CANCELLED",
    ]


def _completion(lease):
    return {
        "lease_token": lease["lease_token"],
        "outputs": [],
        "values": _summary_values(),
        "stdout": "",
        "stderr": "",
    }


@pytest.mark.parametrize("phase", ["prepare_workspace", "stage_outputs"])
def test_slow_file_io_does_not_hold_write_lock(tmp_path, monkeypatch, phase):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    control, config = _control(tmp_path)
    first = control.submit(_request(config))
    lease = control.claim(_worker(config))
    control.submit(_request(config, "second"))
    entered, release = Event(), Event()
    original = getattr(control.artifacts, phase)

    def slow(*args, **kwargs):
        if not entered.is_set():
            entered.set()
            assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(control.artifacts, phase, slow)
    with ThreadPoolExecutor(max_workers=3) as pool:
        operation = (
            (lambda: control.complete(lease["lease_id"], _completion(lease)))
            if phase == "stage_outputs"
            else lambda: control.claim(_worker(config))
        )
        pending = pool.submit(operation)
        try:
            assert entered.wait(5)
            # Both a write and an independent claim must finish before I/O resumes.
            pool.submit(control.heartbeat, _worker(config)).result(timeout=5)
            pool.submit(
                control.lease_heartbeat,
                lease["lease_id"],
                {"lease_token": lease["lease_token"]},
            ).result(timeout=5)
            other = pool.submit(
                control.claim, {**_worker(config), "worker_id": "peer"}
            ).result(timeout=5)
            assert other is not None and other["job_id"] != first.job_id
        finally:
            release.set()
        pending.result(timeout=5)
    with connect_database(control.database) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM job_object_pins WHERE purpose LIKE 'prepare:%'"
            ).fetchone()[0]
            == 0
        )


def test_two_workers_sustain_heartbeat_and_reconcile_pressure(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import sqlite3
    from threading import Barrier, Lock

    control, config = _control(tmp_path)
    workers = [
        {**_worker(config, "native"), "worker_id": "native-a"},
        {**_worker(config, "qgis"), "worker_id": "qgis-a"},
        {**_worker(config, "remote_http"), "worker_id": "remote-http-a"},
    ]
    original = control.recovery._reconcile
    calls = 0
    calls_lock = Lock()

    def occasionally_busy():
        nonlocal calls
        with calls_lock:
            calls += 1
            current = calls
        if current % 11 == 0:
            raise sqlite3.OperationalError("database is locked")
        original()

    monkeypatch.setattr(control.recovery, "_reconcile", occasionally_busy)
    start = Barrier(4)

    def worker_loop(worker):
        start.wait(timeout=5)
        for _ in range(100):
            control.heartbeat(worker)
            control.reconcile()

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(worker_loop, worker) for worker in workers]
        start.wait(timeout=5)
        for _ in range(100):
            control.reconcile()
        for future in futures:
            future.result(timeout=20)

    assert calls > 0
    control.reconcile()
    assert control.ready()
    with connect_database(control.database) as connection:
        assert (
            connection.execute("SELECT count(*) FROM worker_heartbeats").fetchone()[0]
            == 3
        )


@pytest.mark.parametrize("phase", ["prepare_workspace", "stage_outputs"])
@pytest.mark.parametrize("change", ["cancel", "deadline"])
def test_state_changes_during_io_cannot_commit_success(
    tmp_path, monkeypatch, phase, change
):
    control, config = _control(tmp_path)
    job = control.submit(_request(config))
    lease = control.claim(_worker(config)) if phase == "stage_outputs" else None
    original = getattr(control.artifacts, phase)

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        if change == "cancel":
            control.cancel(job.job_id)
        else:
            with connect_database(control.database) as connection:
                connection.execute(
                    "UPDATE executor_jobs SET deadline_at='2000-01-01T00:00:00Z' WHERE job_id=?",
                    (job.job_id,),
                )
        return result

    monkeypatch.setattr(control.artifacts, phase, changed)
    if lease:
        with pytest.raises(ExecutionServiceError, match="LEASE_STALE"):
            control.complete(lease["lease_id"], _completion(lease))
    else:
        assert control.claim(_worker(config)) is None
    assert control.node_diagnostics(job.job_id)[0]["status"] != "SUCCEEDED"
    control.reconcile()
    with connect_database(control.database) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM content_objects").fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM job_object_pins WHERE purpose LIKE 'prepare:%'"
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize("change", ["expire", "recover", "duplicate"])
def test_completion_rechecks_lease_after_io(tmp_path, monkeypatch, change):
    control, config = _control(tmp_path)
    control.submit(_request(config))
    lease = control.claim(_worker(config))
    original = control.artifacts.stage_outputs

    def changed(*args):
        prepared = original(*args)
        monkeypatch.setattr(control.artifacts, "stage_outputs", original)
        if change == "expire":
            with connect_database(control.database) as connection:
                connection.execute(
                    "UPDATE node_attempts SET lease_expires_at='2000-01-01T00:00:00Z' WHERE lease_id=?",
                    (lease["lease_id"],),
                )
        elif change == "recover":
            control.recover()
        else:
            control.complete(lease["lease_id"], _completion(lease))
        return prepared

    monkeypatch.setattr(control.artifacts, "stage_outputs", changed)
    with pytest.raises(ExecutionServiceError, match="LEASE_STALE"):
        control.complete(lease["lease_id"], _completion(lease))
    with connect_database(control.database) as connection:
        expected = 1 if change == "duplicate" else 0
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM node_attempts WHERE status='SUCCEEDED'"
            ).fetchone()[0]
            == expected
        )


def test_recovery_discards_workspace_and_temporary_pins_but_keeps_checkpoints(tmp_path):
    control, config = _control(tmp_path)
    job = control.submit(_request(config))
    lease = control.claim(_worker(config))
    control.complete(lease["lease_id"], _completion(lease))
    staging = control.artifacts.object_root / ".staging/orphan"
    staging.mkdir(parents=True)
    (staging / "bytes").write_bytes(b"partial")
    with connect_database(control.database) as connection:
        connection.execute(
            "INSERT INTO job_object_pins SELECT job_id,checksum,'prepare:orphan' FROM job_object_pins LIMIT 1"
        )
    control.recover()
    assert not staging.exists()
    assert not Path(lease["workspace"]).exists()
    assert control.recovery.metrics()["attempt_workspace_removed"] == 1
    assert control.node_diagnostics(job.job_id)[0]["status"] == "SUCCEEDED"
    with connect_database(control.database) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM job_object_pins WHERE purpose LIKE 'prepare:%'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM content_objects").fetchone()[0] > 0
        )


def test_competing_preparations_create_one_attempt_and_release_only_own_pins(
    tmp_path, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    control, config = _control(tmp_path)
    job = control.submit(_request(config))
    barrier = Barrier(2)
    prepare = control.artifacts.prepare_workspace

    def simultaneous(inputs, workspace):
        prepare(inputs, workspace)
        barrier.wait(timeout=5)

    monkeypatch.setattr(control.artifacts, "prepare_workspace", simultaneous)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(control.claim, _worker(config)) for _ in range(2)]
        leases = [future.result(timeout=10) for future in futures]
    assert sum(lease is not None for lease in leases) == 1
    with connect_database(control.database) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM node_attempts WHERE job_id=?", (job.job_id,)
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM job_object_pins WHERE purpose LIKE 'prepare:%'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM job_object_pins WHERE purpose LIKE 'workflow-input:%'"
            ).fetchone()[0]
            == 1
        )


@pytest.mark.parametrize("window", ["before_commit", "after_commit"])
def test_claim_restart_windows_do_not_resume_disposable_workspaces(
    tmp_path, monkeypatch, window
):
    control, config = _control(tmp_path)
    job = control.submit(_request(config))
    with monkeypatch.context() as patch:
        if window == "before_commit":
            import os

            rename = os.replace

            def interrupted_rename(path, destination):
                rename(path, destination)
                raise RuntimeError("simulated process interruption")

            patch.setattr(os, "replace", interrupted_rename)
        else:

            def interrupted_response(*args):
                raise RuntimeError("simulated process interruption")

            patch.setattr(control.artifacts, "workspace_inputs", interrupted_response)
        with pytest.raises(RuntimeError, match="simulated process interruption"):
            control.claim(_worker(config))
    control.recover()
    lease = control.claim(_worker(config))
    assert lease["attempt"] == (1 if window == "before_commit" else 2)
    assert control.node_diagnostics(job.job_id)[0]["lost_count"] == (
        0 if window == "before_commit" else 1
    )


@pytest.mark.parametrize("window", ["before_commit", "after_commit"])
def test_completion_restart_windows_use_only_committed_checkpoints(
    tmp_path, monkeypatch, window
):
    import shutil

    control, config = _control(tmp_path)
    job = control.submit(_request(config))
    lease = control.claim(_worker(config))
    with monkeypatch.context() as patch:
        if window == "before_commit":
            advance = control.completion._advance

            def interrupted_advance(*args):
                advance(*args)
                raise RuntimeError("simulated process interruption")

            patch.setattr(control.completion, "_advance", interrupted_advance)
        else:

            def interrupted_cleanup(path, **kwargs):
                raise RuntimeError("simulated process interruption")

            patch.setattr(shutil, "rmtree", interrupted_cleanup)
        with pytest.raises(RuntimeError, match="simulated process interruption"):
            control.complete(lease["lease_id"], _completion(lease))
    control.recover()
    expected = "READY" if window == "before_commit" else "SUCCEEDED"
    assert control.node_diagnostics(job.job_id)[0]["status"] == expected
    with connect_database(control.database) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM job_object_pins WHERE purpose LIKE 'prepare:%'"
            ).fetchone()[0]
            == 0
        )
    with pytest.raises(ExecutionServiceError, match="LEASE_STALE"):
        control.complete(lease["lease_id"], _completion(lease))


def test_new_short_job_gets_the_next_suitable_worker_slot(tmp_path):
    from copy import deepcopy

    control, config = _control(tmp_path)
    model = config["models"][0]
    original = model["workflow"]["nodes"][0]
    model["workflow"]["nodes"] = [
        dict(deepcopy(original), nodeId=f"long-{index:02d}") for index in range(58)
    ]
    model["maxParallelNodes"] = 58
    long_job = control.submit(_request(config, "long"))
    assert control.claim(_worker(config))["job_id"] == long_job.job_id

    model["workflow"]["nodes"] = [dict(deepcopy(original), nodeId="short")]
    model["maxParallelNodes"] = 1
    short_job = control.submit(_request(config, "short"))

    lease = control.claim(_worker(config))
    assert lease["job_id"] == short_job.job_id
    assert lease["node_id"] == "short"


def test_cancelled_remote_attempt_records_termination_evidence_without_reviving_lease(
    tmp_path,
) -> None:
    control, config, resources = _remote_control(tmp_path)
    content = b'{"source":"case-1"}'
    resources.contents["object-r"] = content
    job = control.submit(_remote_request(config, content))
    lease = control.claim(_worker(config, "remote_http"))

    control.cancel(job.job_id)
    state = {
        "remote_task_id": "operation-1",
        "request_sha256": "sha256:" + "1" * 64,
        "idempotency_key": "stable-key",
        "remote_status": "running",
        "last_polled_at": "2026-11-04T00:00:00.000Z",
        "remote_termination_confirmed": False,
    }
    control.lease_heartbeat(
        lease["lease_id"],
        {"lease_token": lease["lease_token"], "remote_state": state},
    )

    diagnostics = control.node_diagnostics(job.job_id)[0]
    assert diagnostics["status"] == "CANCELLED"
    assert json.loads(diagnostics["remote_state_json"]) == state
    metrics = runtime_metrics(control.database)
    assert metrics["remote_statuses"] == {"running": 1}
    assert metrics["remote_unconfirmed_terminations"] == 1
    with pytest.raises(ExecutionServiceError, match="LEASE_STALE"):
        control.complete(
            lease["lease_id"],
            {"lease_token": lease["lease_token"]},
        )


def test_remote_task_state_survives_worker_loss_and_is_delivered_to_retry(
    tmp_path,
) -> None:
    control, config, resources = _remote_control(tmp_path)
    content = b'{"source":"case-1"}'
    resources.contents["object-r"] = content
    job = control.submit(_remote_request(config, content))
    first = control.claim(_worker(config, "remote_http"))
    state = {
        "remote_task_id": "operation-1",
        "request_sha256": "sha256:" + "2" * 64,
        "idempotency_key": "stable-key",
        "remote_status": "running",
        "last_polled_at": "2026-11-04T00:00:00.000Z",
        "remote_termination_confirmed": None,
    }
    control.lease_heartbeat(
        first["lease_id"],
        {"lease_token": first["lease_token"], "remote_state": state},
    )
    with connect_database(control.database) as connection:
        connection.execute(
            "UPDATE node_attempts SET lease_expires_at=? WHERE lease_id=?",
            ("2000-01-01T00:00:00.000Z", first["lease_id"]),
        )

    control.reconcile()
    second = control.claim(_worker(config, "remote_http"))

    assert second["attempt"] == 2
    assert second["remote_state"] == state
    assert control.node_diagnostics(job.job_id)[0]["lost_count"] == 1
