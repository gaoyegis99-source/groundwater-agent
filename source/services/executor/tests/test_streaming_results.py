from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import httpx
import pytest

from platform_executor_service.artifacts import ArtifactStore
from platform_executor_service.contracts import DataVersionRefV2
from platform_executor_service.errors import ExecutionServiceError
from platform_executor_service.data_client import DataClient, DataIntegrityError


def test_data_conflict_preserves_problem_details_code():
    def handle(_request):
        return httpx.Response(
            409,
            headers={"content-type": "application/problem+json"},
            json={
                "type": "/api/v12/problems/publication-conflict",
                "title": "Publication conflict",
                "status": 409,
                "detail": "The publication payload conflicts with existing state.",
                "code": "PUBLICATION_CONTENT_CONFLICT",
                "requestId": "request-1",
                "traceId": "trace-1",
                "errors": [],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        data = DataClient("http://data", client, "p")
        with pytest.raises(DataIntegrityError, match="PUBLICATION_CONTENT_CONFLICT"):
            data.publication_status("publication", type("Request", (), {"project_id": "p"})())


def test_result_upload_streams_native_file_with_bounded_memory(tmp_path, record_property):
    repository = Path(__file__).resolve().parents[3]
    source_roots = (
        repository / "services/executor/src",
        repository / "services/data/client/src",
        repository / "packages/protocol-contracts/src",
    )
    script = '''
import hashlib, json, sys
from pathlib import Path
import httpx
from platform_executor_service.contracts import JobRequestV6
from platform_executor_service.data_client import DataClient
root = Path(sys.argv[1])
source = root / "large.bin"
chunk = bytes(1024 * 1024)
digest = hashlib.sha256()
with source.open("wb") as output:
    for _ in range(128):
        output.write(chunk)
        digest.update(chunk)
checksum = "sha256:" + digest.hexdigest()
def memory_kib(field):
    return int(next(line.split()[1] for line in Path("/proc/self/status").read_text().splitlines() if line.startswith(field + ":")))
class StreamingTransport(httpx.BaseTransport):
    def handle_request(self, request):
        assert request.url.path == "/data/v3/publications/publication/items/raster"
        assert request.headers["x-project-id"] == "p"
        assert request.headers["x-platform-subject"] == "platform-executor"
        assert request.headers["x-platform-display-name"] == "Platform Executor"
        assert request.headers["x-platform-roles"] == "admin"
        assert request.headers["x-content-sha256"] == checksum
        size = 0
        for packet in request.stream:
            size += len(packet)
        assert size == source.stat().st_size
        return httpx.Response(200, json={
            "objectId": "object", "size": source.stat().st_size,
            "checksum": checksum, "role": "raw", "format": "binary",
            "mediaType": "application/octet-stream",
        })
request = JobRequestV6(
    request_id="run", project_id="p", model_id="model", model_version="1.0.0",
    model_checksum="sha256:" + "0" * 64, workflow_id="workflow",
    workflow_version="1.0.0", workflow_checksum="sha256:" + "1" * 64,
    executor_id="workflow-dag", arguments={}, input_references={},
    deadline_at="2099-01-01T00:00:00Z",
)
with httpx.Client(transport=StreamingTransport()) as client:
    baseline = memory_kib("VmRSS")
    result = DataClient("http://data", client, "p").stage_result(
        "publication", request, "raster", checksum, source
    )
    peak = memory_kib("VmHWM")
assert result["objectId"] == "object"
print(json.dumps({"size_mib": 128, "peak_growth_kib": peak - baseline}))
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        env={**os.environ, "PYTHONPATH": os.pathsep.join(map(str, source_roots))},
        text=True, capture_output=True, check=True, timeout=120,
    )
    evidence = json.loads(result.stdout)
    record_property("result_upload_peak_growth_kib", evidence["peak_growth_kib"])
    assert evidence["peak_growth_kib"] <= 64 * 1024


@pytest.mark.parametrize("changed_field", [None, "datasetId", "semanticId", "objectId"])
def test_exact_read_accepts_only_the_frozen_data_version_reference(changed_field):
    content = b'{}'
    checksum = "sha256:" + hashlib.sha256(content).hexdigest()
    reference = DataVersionRefV2(
        kind="data_version", service="data-primary", dataset_id="dataset",
        version_id="version", semantic_id="semantics", object_id="object",
        media_type="application/json", format_id="json", size_bytes=len(content),
        checksum=checksum,
    )
    version = {
        "versionId": "version", "datasetId": "dataset", "currentVersion": "version",
        "publicationId": "publication", "semanticId": "semantics",
        "title": "fixture", "descriptor": {}, "semantics": {},
        "inputVersions": [], "createdAt": "2026-01-01T00:00:00Z",
        "tags": [], "project": "p",
        "objects": [{
            "objectId": "object", "role": "raw", "checksum": checksum,
            "mediaType": "application/json", "format": "json", "size": len(content),
        }],
    }
    if changed_field == "objectId":
        version["objects"][0]["objectId"] = "different-object"
    elif changed_field is not None:
        version[changed_field] = "different"

    def handle(request):
        if request.url.path.endswith("/content"):
            return httpx.Response(
                200,
                headers={"content-type": "application/octet-stream"},
                content=content,
            )
        return httpx.Response(200, json=version)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        data = DataClient("http://data", client, "p")
        if changed_field is not None:
            with pytest.raises(ExecutionServiceError, match="DATA_VERSION_REFERENCE_MISMATCH"):
                data.read_exact("p", reference)
        else:
            details, stream = data.read_exact("p", reference)
            try:
                assert details["checksum"] == checksum
                assert stream.read(len(content)) == content
            finally:
                stream.close()


def test_exact_read_projects_frozen_data_semantics_into_execution_metadata():
    content = b"station,time,value\nA,2024-01-01T00:00:00+08:00,NA\n"
    checksum = "sha256:" + hashlib.sha256(content).hexdigest()
    reference = DataVersionRefV2(
        kind="data_version",
        service="data-primary",
        dataset_id="dataset",
        version_id="version",
        semantic_id="semantics",
        object_id="raw-object",
        media_type="text/csv",
        format_id="csv",
        size_bytes=len(content),
        checksum=checksum,
    )
    version = {
        "versionId": "version",
        "datasetId": "dataset",
        "currentVersion": "version",
        "publicationId": "publication",
        "semanticId": "semantics",
        "title": "fixture",
        "descriptor": {
            "policy": "FILE_QUERYABLE",
            "fields": [
                {
                    "fieldId": "f0",
                    "name": "station",
                    "type": "string",
                    "nullable": True,
                },
                {
                    "fieldId": "f1",
                    "name": "time",
                    "type": "string",
                    "nullable": True,
                },
                {
                    "fieldId": "f2",
                    "name": "value",
                    "type": "double",
                    "nullable": True,
                },
            ],
        },
        "semantics": {"timezone": "Asia/Shanghai"},
        "inputVersions": [],
        "createdAt": "2026-01-01T00:00:00Z",
        "tags": [],
        "project": "p",
        "objects": [
            {
                "objectId": "raw-object",
                "role": "raw",
                "checksum": checksum,
                "mediaType": "text/csv",
                "format": "csv",
                "size": len(content),
            },
            {
                "objectId": "query-object",
                "role": "query",
                "checksum": "sha256:" + "1" * 64,
                "mediaType": "application/vnd.apache.parquet",
                "format": "parquet",
                "size": 100,
                "sourceChecksum": checksum,
                "converter": "data-service/1",
                "conversion": {
                    "types": {"f2": "float64"},
                    "nullValues": ["", "NA"],
                },
                "arrowVersion": "20.0.0",
            },
        ],
    }

    def handle(request):
        if request.url.path.endswith("/content"):
            return httpx.Response(200, content=content)
        return httpx.Response(200, json=version)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        details, stream = DataClient("http://data", client, "p").read_exact(
            "p", reference
        )
        stream.close()

    assert details["execution_metadata"] == {
        "dataContract": {
            "datasetId": "dataset",
            "versionId": "version",
            "semanticId": "semantics",
            "objectId": "raw-object",
            "conversionObjectId": "query-object",
            "timezone": "Asia/Shanghai",
            "nullValues": ["", "NA"],
            "types": {"f2": "float64"},
            "fields": version["descriptor"]["fields"],
        }
    }


@pytest.mark.parametrize("changed_field", ["datasetId", "versionId", "semanticId"])
def test_published_output_requires_one_consistent_immutable_identity(changed_field):
    checksum = "sha256:" + hashlib.sha256(b"{}").hexdigest()
    version = {
        "versionId": "version", "datasetId": "dataset",
        "currentVersion": "version", "publicationId": "publication",
        "semanticId": "semantics", "title": "fixture", "descriptor": {},
        "semantics": {}, "inputVersions": [],
        "createdAt": "2026-01-01T00:00:00Z", "tags": [], "project": "p",
        "objects": [{
            "objectId": "object", "role": "raw", "checksum": checksum,
            "mediaType": "application/json", "format": "json", "size": 2,
        }],
    }
    version[changed_field] = "different"
    receipt = {
        "state": "PUBLISHED",
        "outputs": [{
            "name": "summary", "datasetId": "dataset", "versionId": "version",
            "semanticId": "semantics",
        }],
    }
    declarations = {
        "summary": {"format": "json", "mediaType": "application/json"}
    }
    request = type("Request", (), {"project_id": "p"})()

    with httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=version))
    ) as client:
        with pytest.raises(DataIntegrityError, match="DATA_OUTPUT_IDENTITY_MISMATCH"):
            DataClient("http://data", client, "p").output_references(
                receipt, request, declarations
            )


def test_published_output_rejects_duplicate_names_before_version_reads():
    calls = 0

    def unexpected(_request):
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    output = {
        "name": "summary", "datasetId": "dataset", "versionId": "version",
        "semanticId": "semantics",
    }
    with httpx.Client(transport=httpx.MockTransport(unexpected)) as client:
        with pytest.raises(DataIntegrityError, match="DATA_OUTPUT_SET_MISMATCH"):
            DataClient("http://data", client, "p").output_references(
                {"state": "PUBLISHED", "outputs": [output, output]},
                type("Request", (), {"project_id": "p"})(),
                {"summary": {"format": "json", "mediaType": "application/json"}},
            )
    assert calls == 0


def test_worker_output_requires_an_allowed_media_format_pair(tmp_path):
    artifacts = ArtifactStore(tmp_path / "objects", tmp_path / "work")
    attempt = {"job_id": "job", "node_id": "node", "lease_id": "lease"}
    workspace = artifacts.attempt_workspace(attempt)
    (workspace / "outputs").mkdir(parents=True)
    path = workspace / "outputs" / "result"
    path.write_bytes(b"data")
    node = {"output_contracts_json": json.dumps([{
        "name": "result", "kind": "artifact",
        "mediaTypes": ["application/json", "text/csv"],
        "formats": ["json", "csv"],
        "formatPairs": [
            {"mediaType": "application/json", "format": "json"},
            {"mediaType": "text/csv", "format": "csv"},
        ],
    }])}
    output = {
        "port": "result", "path": "outputs/result",
        "media_type": "application/json", "format": "csv",
        "size_bytes": 4, "checksum": "sha256:" + hashlib.sha256(b"data").hexdigest(),
    }
    with pytest.raises(ExecutionServiceError, match="NODE_OUTPUT_INVALID"):
        artifacts.stage_outputs(node, attempt, [output], tmp_path / "staging")

    narrowed = json.loads(node["output_contracts_json"])
    narrowed[0]["formatPairs"] = [
        {"mediaType": "application/json", "format": "json"}
    ]
    node["output_contracts_json"] = json.dumps(narrowed)
    with pytest.raises(ExecutionServiceError, match="NODE_OUTPUT_INVALID"):
        artifacts.stage_outputs(
            node, attempt, [{**output, "media_type": "text/csv", "format": "csv"}],
            tmp_path / "staging",
        )
