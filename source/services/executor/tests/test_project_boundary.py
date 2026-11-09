from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from platform_executor_service.app import create_app


def test_executor_rejects_other_project_before_job_lookup():
    class Control:
        def get_by_request(self, _project_id, _request_id):
            raise AssertionError("other project reached job lookup")

    client = TestClient(create_app(SimpleNamespace(control=Control(), project_id="p")))
    response = client.get("/internal/execution/v6/jobs/by-request/run", params={"projectId": "other"})
    assert response.status_code == 404
    assert response.json()["code"] == "JOB_NOT_FOUND"
