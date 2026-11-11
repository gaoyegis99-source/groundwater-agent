from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from threading import Event
from types import SimpleNamespace

from fastapi.testclient import TestClient
import httpx
import pytest

from platform_executor_service.app import create_app
from platform_executor_service.contracts import canonical_json, sha256_bytes
from platform_executor_service.database import connect_database
from platform_executor_service.errors import ExecutionServiceError
from platform_executor_service.data_client import DataClient
from platform_executor_service.results import ResultPublisher
from platform_executor_service.settings import (
    definition_set_digest,
    load_executor_config,
)
from platform_executor_service.worker import Worker
from platform_executor_service.worker_contracts import lease_to_wire
from platform_executor_service.worker import _file_identity
from scripts.lib.assembly_paths import compiled_bundle
from services.executor.tests.test_control import (
    _canonical_arguments,
    _completed_job,
    _control,
    _request,
    _worker,
)


@pytest.mark.parametrize(
    "changes",
    [
        {"deadline_at": "not-a-date"},
        {"deadlineAt": "2099-01-01T00:00:00"},
        {"deadlineAt": "20990101T000000Z"},
        {"deadlineAt": "2099-02-30T00:00:00Z"},
        {"deadlineAt": "2099-01-01T00:00:00+00:99"},
        {"deadlineAt": "2099-01-01T00:00:00+24:00"},
        {"deadline_at": "9999-12-31T23:59:59-01:00"},
        {"arguments": {"versionId": "r", "axis": "unsupported"}},
        {"arguments": {"versionId": "r", "axis": "columns", "extra": 1}},
        {"arguments": {"versionId": "r"}},
    ],
)
def test_http_rejects_invalid_or_uncanonical_request_before_reading_inputs(
    tmp_path, changes
):
    control, config = _control(tmp_path)

    def unexpected_read(*args):
        raise AssertionError("invalid request reached Data")

    control.data_client.read_exact = unexpected_read
    client = TestClient(create_app(SimpleNamespace(control=control, project_id="p")))
    response = client.post(
        "/internal/execution/v6/jobs",
        json={**_request(config).as_dict(), **changes},
    )
    assert response.status_code == 422, response.text
    with connect_database(control.database) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM executor_jobs").fetchone()[0] == 0
        )
    assert not list(control.artifacts.object_root.rglob("*.part"))


@pytest.mark.parametrize("offset", ["+15:00", "+23:00", "-03:30"])
def test_absolute_deadline_preserves_request_and_uses_utc_for_scheduling(
    tmp_path, offset
):
    control, config = _control(tmp_path)
    body = {**_request(config).as_dict(), "deadlineAt": "2099-01-01T00:00:00" + offset}
    client = TestClient(create_app(SimpleNamespace(control=control, project_id="p")))
    first = client.post("/internal/execution/v6/jobs", json=body)
    assert first.status_code == 202
    assert (
        client.post("/internal/execution/v6/jobs", json=body).json()["jobId"]
        == first.json()["jobId"]
    )
    with connect_database(control.database) as connection:
        row = connection.execute(
            "SELECT request_json, deadline_at, julianday(deadline_at) FROM executor_jobs"
        ).fetchone()
        assert json.loads(row["request_json"])["deadline_at"] == body["deadlineAt"]
        assert row["deadline_at"].endswith("Z")
        assert row[2] is not None
    assert control.claim(_worker(config))["job_id"] == first.json()["jobId"]


def test_node_schema_rejects_argument_allowed_by_model_before_input_io(tmp_path):
    control, config = _control(tmp_path)
    # A callable model can intentionally expose a broader scalar schema than
    # its selected operator. The bound operator constraints must still apply.
    config["models"][0]["tool"]["inputSchema"]["properties"]["axis"] = {
        "type": "string"
    }
    with pytest.raises(ExecutionServiceError, match="MODEL_ARGUMENTS_INVALID"):
        control.submit(
            replace(
                _request(config),
                arguments=_canonical_arguments(
                    config, versionId="r", axis="unsupported"
                ),
            )
        )
    with connect_database(control.database) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM executor_jobs").fetchone()[0] == 0
        )


@pytest.mark.parametrize("failure", ["transport", "stream", 503, 429, 404])
def test_data_input_failure_has_correct_http_classification_and_no_job(
    tmp_path, failure
):
    control, config = _control(tmp_path)
    content = b"value\n1\n"

    class InterruptedBody(httpx.SyncByteStream):
        def __iter__(self):
            yield content[:3]
            raise httpx.ReadError("connection interrupted")

    def respond(request):
        if failure == "transport":
            raise httpx.ConnectError("Data unavailable", request=request)
        if isinstance(failure, int):
            return httpx.Response(
                failure,
                headers={"content-type": "application/problem+json"},
                json={
                    "type": "urn:platform:problem:data-test",
                    "title": "Data test failure",
                    "status": failure,
                    "detail": "The fixture rejected the request.",
                    "code": "DATA_TEST_FAILURE",
                    "requestId": "request-1",
                    "traceId": "trace-1",
                    "errors": [],
                },
            )
        if request.url.path.endswith("/content"):
            return httpx.Response(
                200,
                headers={"content-type": "application/octet-stream"},
                stream=InterruptedBody(),
            )
        return httpx.Response(
            200,
            json={
                "versionId": "r",
                "datasetId": "dataset-r",
                "currentVersion": "r",
                "publicationId": "publication-r",
                "semanticId": "semantics-r",
                "title": "fixture",
                "descriptor": {},
                "semantics": {},
                "inputVersions": [],
                "createdAt": "2026-01-01T00:00:00Z",
                "tags": [],
                "project": "p",
                "objects": [
                    {
                        "objectId": "object-r",
                        "role": "raw",
                        "mediaType": "text/csv",
                        "format": "csv",
                        "size": len(content),
                        "checksum": "sha256:" + hashlib.sha256(content).hexdigest(),
                    }
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as resource_http:
        control.data_client = DataClient("http://data", resource_http, "p")
        client = TestClient(
            create_app(SimpleNamespace(control=control, project_id="p"))
        )
        response = client.post(
            "/internal/execution/v6/jobs", json=_request(config).as_dict()
        )
    assert response.status_code == (422 if failure == 404 else 503)
    with connect_database(control.database) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM executor_jobs").fetchone()[0] == 0
        )
    assert not list(control.artifacts.object_root.rglob("*.part"))


def test_data_publication_maps_owned_semantics_without_renaming_field_keys(tmp_path):
    _control_instance, config = _control(tmp_path)
    observed = {}

    def respond(request: httpx.Request) -> httpx.Response:
        observed.update(json.loads(request.content))
        return httpx.Response(
            201,
            json={
                "publicationId": "publication-1",
                "state": "OPEN",
                "manifest": {},
                "uploads": [],
                "operations": [],
                "receipt": None,
                "idempotencyKey": "workflow-job-1",
                "manifestChecksum": "sha256:" + "0" * 64,
                "createdAt": "2026-01-01T00:00:00Z",
                "events": [],
            },
        )

    semantics = {
        "description": "summary output",
        "fields": {"water_level_m": {"meaning": "head", "unit": "m"}},
        "grain": None,
        "time_range": "2025-01/2025-12",
        "timezone": "UTC",
        "crs": None,
        "vertical_datum": "local",
        "limitations": [],
        "source": "workflow-declared",
    }
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        DataClient("http://data", client, "p").begin_publication(
            "job-1",
            _request(config),
            [
                {
                    "name": "summary",
                    "title": "Summary",
                    "format": "json",
                    "media_type": "application/json",
                    "policy": "FILE_ONLY",
                    "semantics": semantics,
                }
            ],
            {"workflow_id": "groundwater.summary"},
        )

    wire = observed["outputs"][0]["semantics"]
    assert wire["timeRange"] == "2025-01/2025-12"
    assert wire["verticalDatum"] == "local"
    assert wire["fields"] == semantics["fields"]
    assert "time_range" not in wire and "vertical_datum" not in wire
    assert observed["provenance"] == {"workflow_id": "groundwater.summary"}


@pytest.mark.parametrize("mode", ["cancel", "deadline", "failure"])
def test_terminal_attempt_cleanup_invalidates_lease_and_removes_workspace(
    tmp_path, mode
):
    control, config = _control(tmp_path)
    job = control.submit(_request(config))
    lease = control.claim(_worker(config))
    if mode == "cancel":
        control.cancel(job.job_id)
    elif mode == "deadline":
        with connect_database(control.database) as connection:
            connection.execute(
                "UPDATE executor_jobs SET deadline_at='2000-01-01T00:00:00Z'"
            )
    else:
        control.fail(
            lease["lease_id"],
            {"lease_token": lease["lease_token"], "error_code": "OPERATOR_FAILED"},
        )
    control.reconcile()
    assert not Path(lease["workspace"]).exists()
    with pytest.raises(ExecutionServiceError, match="LEASE_STALE"):
        control.lease_heartbeat(
            lease["lease_id"], {"lease_token": lease["lease_token"]}
        )
    with connect_database(control.database) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM content_objects").fetchone()[0]
            == 0
        )
    assert not [
        item for item in control.artifacts.object_root.rglob("*") if item.is_file()
    ]


def test_gc_rollback_preserves_pinned_bytes_and_restart_recovery(tmp_path):
    control, config = _control(tmp_path)
    job = control.submit(_request(config))
    with connect_database(control.database) as connection:
        checksum = connection.execute(
            "SELECT checksum FROM content_objects"
        ).fetchone()[0]
        connection.execute(
            "CREATE TRIGGER fail_gc BEFORE DELETE ON content_objects "
            "BEGIN SELECT RAISE(ABORT, 'injected GC failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected GC failure"):
        control.cancel(job.job_id)
    assert control.get(job.job_id).status == "RUNNING"
    assert control.artifacts.object_path(checksum).is_file()
    with connect_database(control.database) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM job_object_pins").fetchone()[0]
            == 2
        )
        connection.execute("DROP TRIGGER fail_gc")
    control.recover()
    assert control.claim(_worker(config))["job_id"] == job.job_id


def test_reconcile_only_cleans_existing_terminal_workspaces(tmp_path, monkeypatch):
    control, config = _control(tmp_path)
    first = control.submit(_request(config))
    terminal = control.claim(_worker(config))
    control.cancel(first.job_id)
    control.reconcile()
    second = control.submit(_request(config, "second"))
    active = control.claim(_worker(config))
    assert active["job_id"] == second.job_id

    def unexpected_cleanup(attempt):
        raise AssertionError("already removed or active workspace was selected")

    with monkeypatch.context() as patch:
        patch.setattr(control.artifacts, "remove_attempt_workspace", unexpected_cleanup)
        control.reconcile()
        control.reconcile()
    assert not Path(terminal["workspace"]).exists()
    assert Path(active["workspace"]).is_dir()
    control.lease_heartbeat(active["lease_id"], {"lease_token": active["lease_token"]})

    # A failed deletion remains discoverable without scanning history.
    control.cancel(second.job_id)
    with monkeypatch.context() as patch:
        patch.setattr(
            control.artifacts, "remove_attempt_workspace", lambda attempt: None
        )
        control.reconcile()
    assert Path(active["workspace"]).is_dir()
    control.reconcile()
    assert not Path(active["workspace"]).exists()


def test_reconcile_preserves_workspace_before_lease_commit(tmp_path, monkeypatch):
    control, config = _control(tmp_path)
    control.submit(_request(config))
    prepare = control.artifacts.prepare_workspace
    rename = os.replace
    observed = []

    def preparing(inputs, staging):
        prepare(inputs, staging)
        control.reconcile()
        assert (staging / "inputs/data").is_file()
        observed.append("preparing")

    def before_commit(source, target):
        rename(source, target)
        with connect_database(control.database) as connection:
            assert (
                connection.execute("SELECT COUNT(*) FROM node_attempts").fetchone()[0]
                == 0
            )
        control.reconcile()
        assert (target / "inputs/data").is_file()
        observed.append("renamed")

    monkeypatch.setattr(control.artifacts, "prepare_workspace", preparing)
    monkeypatch.setattr(
        "platform_executor_service.control_components.leases.os.replace",
        before_commit,
    )
    lease = control.claim(_worker(config))
    assert observed == ["preparing", "renamed"]
    control.lease_heartbeat(lease["lease_id"], {"lease_token": lease["lease_token"]})
    assert Path(lease["workspace"]).is_dir()


def test_gc_preserves_an_object_reused_before_file_collection(tmp_path):
    control, config = _control(tmp_path)
    first = control.submit(_request(config))
    with connect_database(control.database) as connection:
        checksum = connection.execute(
            "SELECT checksum FROM content_objects"
        ).fetchone()[0]
    control.cancel(first.job_id)
    second = control.submit(_request(config, "second"))
    control.reconcile()
    assert control.artifacts.object_path(checksum).is_file()
    assert control.claim(_worker(config))["job_id"] == second.job_id


def test_workspace_cleanup_before_periodic_heartbeat_keeps_worker_alive(tmp_path):
    control, config = _control(tmp_path)
    job = control.submit(_request(config))
    lease = control.claim(_worker(config))
    hashing, release = Event(), Event()

    def delayed_hash():
        hashing.set()
        assert release.wait(5)
        return _file_identity(Path(lease["workspace"]) / "inputs/data")

    async def exercise():
        worker = Worker.__new__(Worker)
        worker.stop = asyncio.Event()
        heartbeats = []

        async def attempt(_lease):
            await asyncio.to_thread(delayed_hash)

        async def post(path, body):
            heartbeats.append(path)
            try:
                control.lease_heartbeat(
                    lease["lease_id"], {"lease_token": body["leaseToken"]}
                )
            except ExecutionServiceError as error:
                assert str(error) == "LEASE_STALE"
                return httpx.Response(409, json={"code": "LEASE_STALE"})
            raise AssertionError("cancelled lease was accepted")

        worker._execute_attempt = attempt
        worker._post = post
        pending = asyncio.create_task(worker._execute(lease_to_wire(lease)))
        try:
            assert await asyncio.to_thread(hashing.wait, 5)
            assert heartbeats == []
            control.cancel(job.job_id)
            control.reconcile()
        finally:
            release.set()
        await asyncio.wait_for(pending, 5)
        assert len(heartbeats) == 1
        assert not worker.stop.is_set()

    asyncio.run(exercise())


@pytest.mark.parametrize("phase", ["stage", "commit-intent"])
def test_expired_unreleased_publication_aborts_without_first_commit(
    tmp_path, monkeypatch, phase
):
    control, config = _control(tmp_path)
    job = _completed_job(control, config)
    resources = control.data_client
    publisher = ResultPublisher(control.database, control.artifacts, config, resources)

    def expire():
        with connect_database(control.database) as connection:
            connection.execute(
                "UPDATE executor_jobs SET deadline_at='2000-01-01T00:00:00Z'"
            )
        control.reconcile()

    if phase == "stage":
        stage = resources.stage_result

        def delayed_stage(*args):
            result = stage(*args)
            expire()
            return result

        monkeypatch.setattr(resources, "stage_result", delayed_stage)
        publisher.publish_ready()
        publisher.publish_ready()
    else:

        def interrupted_commit(*args):
            raise RuntimeError("Control stopped after commit intent")

        publisher.publish_ready()
        with monkeypatch.context() as patch:
            patch.setattr(publisher.finalizer, "commit", interrupted_commit)
            with pytest.raises(RuntimeError, match="Control stopped"):
                publisher.publish_ready()
        expire()
        control.recover()
        publisher.publish_ready()
    assert resources.commit_calls == 0
    assert resources.groups[job.job_id]["state"] == "ABORTED"
    status = control.get(job.job_id)
    assert status.status == "TIMED_OUT"
    assert status.error == "JOB_DEADLINE_EXCEEDED"
    assert status.outputs == ()


@pytest.mark.parametrize(
    "change",
    [
        "model-checksum",
        "workflow-checksum",
        "runtime",
        "memory",
        "operator",
        "parameter-schema",
    ],
)
def test_executor_config_rejects_damaged_compiled_identity_or_node(tmp_path, change):
    config = json.loads((compiled_bundle() / "executor-config.json").read_text())
    model = config["models"][0]
    node = model["workflow"]["nodes"][0]
    if change == "model-checksum":
        model["modelChecksum"] = "sha256:" + "0" * 64
    elif change == "workflow-checksum":
        model["workflow"]["definitionChecksum"] = "sha256:" + "0" * 64
    elif change == "runtime":
        node["runtime"] = "invalid"
    elif change == "memory":
        node["memoryLimitBytes"] = -1
    elif change == "operator":
        del node["operatorId"]
    else:
        node["parameterSchema"]["properties"] = 3
    path = tmp_path / "executor-config.json"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="EXECUTOR_CONFIG_INVALID"):
        load_executor_config(path)


@pytest.mark.parametrize("forbidden", ["url", "bearerToken", "caBundle"])
def test_remote_operator_binding_cannot_embed_deployment_secrets_or_endpoints(
    tmp_path, forbidden
) -> None:
    config = json.loads((compiled_bundle() / "executor-config.json").read_text())
    remote_node = next(
        node
        for model in config["models"]
        for node in model["workflow"]["nodes"]
        if node["runtime"] == "remote_http"
    )
    remote_node["remoteBinding"][forbidden] = "must-not-be-accepted"
    path = tmp_path / "executor-config.json"
    path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(ValueError, match="EXECUTOR_CONFIG_INVALID"):
        load_executor_config(path)


def test_definition_set_digest_is_order_independent_and_covers_model_identity():
    config = load_executor_config(compiled_bundle() / "executor-config.json")
    expected = definition_set_digest(config)
    reordered = {**config, "models": list(reversed(config["models"]))}
    assert definition_set_digest(reordered) == expected
    changed = json.loads(json.dumps(config))
    changed["models"][0]["modelChecksum"] = "sha256:" + "0" * 64
    assert definition_set_digest(changed) != expected


def test_executor_config_validates_v6_semantic_requirements(tmp_path):
    source = compiled_bundle() / "executor-config.json"
    loaded = load_executor_config(source)
    flow = next(
        model
        for model in loaded["models"]
        if model["modelId"] == "groundwater.flow-field"
    )
    assert any(
        requirement["kind"] == "verticalBasis"
        for requirement in flow["semanticRequirements"]
    )

    config = json.loads(source.read_text())
    flow = next(
        model
        for model in config["models"]
        if model["modelId"] == "groundwater.flow-field"
    )
    crs_requirement = next(
        requirement
        for requirement in flow["semanticRequirements"]
        if requirement["kind"] == "crsParameter"
    )
    crs_requirement["requirement"] = "geographic"
    model_source = {
        key: value
        for key, value in flow.items()
        if key not in {"modelChecksum", "inputSchemaChecksum", "workflow"}
    }
    flow["modelChecksum"] = sha256_bytes(canonical_json(model_source))
    path = tmp_path / "executor-config.json"
    path.write_text(json.dumps(config))

    with pytest.raises(ValueError, match="EXECUTOR_CONFIG_INVALID"):
        load_executor_config(path)


def test_executor_readiness_exposes_definition_set_digest(tmp_path):
    control, config = _control(tmp_path)
    expected = definition_set_digest(config)
    application = SimpleNamespace(
        control=control,
        project_id="p",
        database=control.database,
        runtime=SimpleNamespace(is_ready=lambda: True),
        temp_root=tmp_path,
        object_root=tmp_path,
        definition_set_digest=expected,
    )
    response = TestClient(create_app(application)).get("/internal/health/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "UP",
        "definitionSetDigest": expected,
        "components": {
            "controlPlane": "UP",
            "native": "UNAVAILABLE",
            "qgis": "UNAVAILABLE",
            "remote_http": "UNAVAILABLE",
        },
    }


def test_executor_readiness_reports_partial_runtime_degradation(tmp_path, monkeypatch):
    control, config = _control(tmp_path)
    monkeypatch.setattr(
        control,
        "runtime_statuses",
        lambda: {
            "native": "UP",
            "qgis": "UNAVAILABLE",
            "remote_http": "UNAVAILABLE",
        },
    )
    application = SimpleNamespace(
        control=control,
        project_id="p",
        database=control.database,
        runtime=SimpleNamespace(is_ready=lambda: True),
        temp_root=tmp_path,
        object_root=tmp_path,
        definition_set_digest=definition_set_digest(config),
    )

    response = TestClient(create_app(application)).get("/internal/health/ready")

    assert response.status_code == 200
    assert response.json()["components"] == {
        "controlPlane": "UP",
        "native": "UP",
        "qgis": "UNAVAILABLE",
        "remote_http": "UNAVAILABLE",
    }
