from __future__ import annotations

from importlib import import_module

import pytest


REQUEST = {
    "requestId": "workflow-1",
    "projectId": "project-1",
    "modelId": "groundwater.summary",
    "modelVersion": "1.1.0",
    "modelChecksum": "sha256:" + "0" * 64,
    "workflowId": "groundwater.summary",
    "workflowVersion": "1.0.0",
    "workflowChecksum": "sha256:" + "1" * 64,
    "executorId": "workflow-dag",
    "arguments": {"versionId": "version-1"},
    "inputReferences": {
        "input": {
            "kind": "data_version",
            "service": "data-primary",
            "datasetId": "dataset-1",
            "versionId": "version-1",
            "semanticId": "semantic-1",
            "objectId": "object-1",
            "mediaType": "text/csv",
            "format": "csv",
            "sizeBytes": 123,
            "checksum": "sha256:" + "a" * 64,
        }
    },
    "deadlineAt": "2026-09-24T12:00:00Z",
}

STATUS = {
    "jobId": "job-1",
    "requestId": "workflow-1",
    "status": "SUCCEEDED",
    "stage": "COMPLETE",
    "error": None,
    "outputs": [
        {
            "kind": "data_version",
            "service": "data-primary",
            "datasetId": "result-dataset",
            "versionId": "result-version",
            "semanticId": "result-semantic",
            "objectId": "result-object",
            "mediaType": "application/geo+json",
            "format": "geojson",
            "sizeBytes": 123,
            "checksum": "sha256:" + "b" * 64,
            "outputName": "summary",
        }
    ],
    "serviceVersion": "reference-summary/0.1.0",
    "createdAt": "2026-09-24T11:59:58Z",
    "startedAt": "2026-09-24T11:59:59Z",
    "finishedAt": "2026-09-24T12:00:00Z",
}


def test_kernel_job_request_is_readable_by_executor() -> None:
    kernel = import_module("platform_kernel.contracts")
    executor = import_module("platform_executor_service.contracts")

    encoded = kernel.JobRequestV6.from_dict(REQUEST).as_dict()

    assert encoded == REQUEST
    assert executor.JobRequestV6.from_dict(encoded).as_dict() == REQUEST


@pytest.mark.parametrize("module", ["platform_kernel.contracts", "platform_executor_service.contracts"])
def test_job_arguments_accept_finite_json_values(module: str) -> None:
    contract = import_module(module)
    arguments = {"threshold": 1.5, "options": [None, True, 2, -0.25, "text", {"ratio": 0.0}]}
    request = {**REQUEST, "arguments": arguments}

    assert contract.JobRequestV6.from_dict(request).as_dict() == request


@pytest.mark.parametrize("module", ["platform_kernel.contracts", "platform_executor_service.contracts"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_job_arguments_reject_nonfinite_json_values(module: str, value: float) -> None:
    contract = import_module(module)

    with pytest.raises(ValueError, match="JOB_REQUEST_INVALID"):
        contract.JobRequestV6.from_dict({**REQUEST, "arguments": {"thresholds": [value]}})


def test_executor_job_status_is_readable_by_kernel() -> None:
    kernel = import_module("platform_kernel.contracts")
    executor = import_module("platform_executor_service.contracts")

    encoded = executor.JobStatusV6.from_dict(STATUS).as_dict()

    assert encoded == STATUS
    assert kernel.JobStatusV6.from_dict(encoded).as_dict() == STATUS


def test_data_version_reference_is_readable_by_executor_and_kernel() -> None:
    kernel = import_module("platform_kernel.contracts")
    executor = import_module("platform_executor_service.contracts")
    payload = STATUS["outputs"][0]

    encoded = executor.DataVersionRefV2.from_dict(payload).as_dict()

    assert encoded == payload
    assert executor.DataVersionRefV2.from_dict(encoded).as_dict() == payload
    assert kernel.DataVersionRefV2.from_dict(encoded).as_dict() == payload


def test_contract_copies_reject_unknown_fields() -> None:
    kernel = import_module("platform_kernel.contracts")
    executor = import_module("platform_executor_service.contracts")
    invalid = {**REQUEST, "unexpected": True}

    for contract in (kernel.JobRequestV6, executor.JobRequestV6):
        try:
            contract.from_dict(invalid)
        except ValueError as error:
            assert str(error) == "JOB_REQUEST_INVALID"
        else:
            raise AssertionError(f"{contract.__module__} accepted an unknown field")
