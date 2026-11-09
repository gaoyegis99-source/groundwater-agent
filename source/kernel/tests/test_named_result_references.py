from __future__ import annotations

import pytest

from platform_kernel.contracts import JobStatusV6, DataVersionRefV2


UNNAMED = {
    "kind": "data_version",
    "service": "data-primary",
    "datasetId": "dataset-results",
    "versionId": "old-result",
    "semanticId": "semantics-results",
    "objectId": "object-old-result",
    "mediaType": "application/zip",
    "format": "zip",
    "sizeBytes": 16,
    "checksum": "sha256:" + "a" * 64,
}


def test_named_and_unnamed_data_references_round_trip_through_job_status():
    named = {
        **UNNAMED,
        "versionId": "new-result",
        "objectId": "object-new-result",
        "mediaType": "application/json",
        "format": "json",
        "outputName": "summary",
    }
    status = JobStatusV6.from_dict({
        "jobId": "job-1",
        "requestId": "run-1",
        "status": "SUCCEEDED",
        "stage": None,
        "error": None,
        "outputs": [named, UNNAMED],
        "serviceVersion": None,
        "createdAt": None,
        "startedAt": None,
        "finishedAt": None,
    })
    assert status.outputs[0].output_name == "summary"
    assert status.as_dict()["outputs"] == [named, UNNAMED]


@pytest.mark.parametrize("invalid", [
    {**UNNAMED, "outputName": ""},
    {**UNNAMED, "outputName": "summary", "kind": "other"},
    {**UNNAMED, "outputName": "summary", "service": "other"},
    {**UNNAMED, "outputName": "summary", "mediaType": None},
    {**UNNAMED, "outputName": "summary", "format": None},
    {**UNNAMED, "outputName": "summary", "sizeBytes": None},
    {**UNNAMED, "outputName": "summary", "checksum": None},
    {**UNNAMED, "extra": 1},
])
def test_named_result_reference_rejects_invalid_wire_shape(invalid):
    with pytest.raises(ValueError, match="REFERENCE_INVALID"):
        DataVersionRefV2.from_dict(invalid)


def test_data_reference_requires_metadata():
    incomplete = {**UNNAMED, "mediaType": None, "format": None, "sizeBytes": None, "checksum": None}
    with pytest.raises(ValueError, match="REFERENCE_INVALID"):
        DataVersionRefV2.from_dict(incomplete)
