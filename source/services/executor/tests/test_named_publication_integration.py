from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from platform_executor_service.contracts import DataVersionRefV2
from platform_executor_service.results import ResultPublisher
from services.executor.tests.test_control import (
    Resources,
    _completed_job,
    _control,
    _request,
    _worker,
)


def test_named_data_reference_requires_complete_exact_identity() -> None:
    incomplete = {
        "kind": "data_version", "service": "data-primary",
        "datasetId": "dataset", "versionId": "version",
        "semanticId": "semantics", "objectId": "object",
        "mediaType": None, "format": None, "sizeBytes": None,
        "checksum": None,
    }
    with pytest.raises(ValueError, match="REFERENCE_INVALID"):
        DataVersionRefV2.from_dict(incomplete)
    with pytest.raises(ValueError, match="REFERENCE_INVALID"):
        DataVersionRefV2.from_dict({**incomplete, "outputName": "summary"})


def test_two_named_outputs_publish_atomically_and_are_exactly_reusable(
    tmp_path: Path,
) -> None:
    control, config = _control(tmp_path)
    model = config["models"][0]
    output = model["workflow"]["outputs"][0]
    model["workflow"]["outputs"].append({**output, "name": "alternate"})
    model["finalOutputs"] = ["alternate", "summary"]
    model["outputPublications"]["alternate"] = {
        **model["outputPublications"]["summary"], "title": "Alternate Summary",
    }
    resources = Resources()
    control.data_client = resources
    job = control.submit(_request(config))
    lease = control.claim(_worker(config))
    control.complete(lease["lease_id"], {
        "lease_token": lease["lease_token"], "outputs": [],
        "values": {"summary": {"number": {"count": 3, "mean": 2.0}}},
        "stdout": "", "stderr": "",
    })
    publisher = ResultPublisher(control.database, control.artifacts, config, resources)
    publisher.publish_ready()
    assert control.get(job.job_id).status == "RUNNING"
    assert control.get(job.job_id).outputs == ()
    publisher.publish_ready()
    status = control.get(job.job_id)
    assert status.status == "SUCCEEDED"
    assert [item.output_name for item in status.outputs] == ["alternate", "summary"]
    assert len({item.version_id for item in status.outputs}) == 2
    assert resources.groups[job.job_id]["state"] == "PUBLISHED"

    model["workflow"]["inputs"][0]["mediaTypes"] = ["application/json"]
    model["workflow"]["inputs"][0]["formats"] = ["json"]
    model["workflow"]["inputs"][0]["formatPairs"] = [
        {"mediaType": "application/json", "format": "json"}
    ]
    source = status.outputs[0]
    later = control.submit(replace(
        _request(config, "run-2"),
        arguments={"versionId": source.version_id, "axis": "columns"},
        input_references={"versionId": source},
    ))
    later_lease = control.claim(_worker(config))
    assert later_lease["job_id"] == later.job_id
    assert later_lease["inputs"]["data"][0]["format"] == "json"


def test_published_receipt_recovery_does_not_repeat_execution(tmp_path: Path) -> None:
    control, config = _control(tmp_path)
    resources = Resources()
    control.data_client = resources
    job = _completed_job(control, config)
    publisher = ResultPublisher(control.database, control.artifacts, config, resources)
    publisher.publish_ready()
    publisher.publish_ready()
    assert control.get(job.job_id).status == "SUCCEEDED"
    stage_calls = resources.stage_calls
    control.recover()
    publisher.publish_ready()
    assert resources.stage_calls == stage_calls == 1
    assert control.get(job.job_id).status == "SUCCEEDED"
