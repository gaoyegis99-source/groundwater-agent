from __future__ import annotations

import httpx
import pytest

from scripts.evaluation.reply_quality import runner


def test_option_followup_waits_for_its_agent_run(monkeypatch) -> None:
    receipt = {
        "schema_version": "message-request.v5",
        "request_id": "request-1",
        "agent_run_id": "agent-run-1",
        "requested_mode": "AUTO",
        "request": {
            "message": "继续核对",
            "requested_mode": "AUTO",
            "corrects_request_id": None,
        },
    }
    waits: list[str] = []
    terminal = {"status": "COMPLETE", "error_code": None}
    monkeypatch.setattr(runner, "submit_message", lambda *_args, **_kwargs: receipt)
    monkeypatch.setattr(
        runner,
        "_wait_agent_run_terminal",
        lambda _client, agent_run_id: (
            waits.append(agent_run_id),
            terminal,
        )[1],
    )

    result = runner._submit_option_followup(
        object(), "session-1", {"message": "继续核对"}
    )

    assert result == (receipt, terminal)
    assert waits == ["agent-run-1"]


def test_run_case_rejects_failed_option_followup(monkeypatch) -> None:
    terminal = {"status": "FAILED", "error_code": "AGENT_OBSERVATION_LOOP"}
    monkeypatch.setattr(
        runner,
        "_submit_option_followup",
        lambda *_args, **_kwargs: (
            {
                "request_id": "request-1",
                "requested_mode": "AUTO",
                "request": {"message": "继续核对"},
            },
            terminal,
        ),
    )

    receipt, observed = runner._submit_option_followup(
        object(), "session-1", {"message": "继续核对"}
    )
    record = {
        "request_id": receipt["request_id"],
        "terminal_status": observed["status"],
        "error_code": observed.get("error_code"),
    }

    assert record == {
        "request_id": "request-1",
        "terminal_status": "FAILED",
        "error_code": "AGENT_OBSERVATION_LOOP",
    }
    with pytest.raises(AssertionError, match="OPTION_AUTO_FOLLOWUP_TERMINAL_INVALID"):
        runner._require_completed_option_followup(observed)


def test_published_knowledge_accepts_current_prefixed_checksum() -> None:
    identifier = "reply-quality-fixture"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"importId": "import-1"})
        if request.url.path.endswith("/imports/import-1"):
            return httpx.Response(
                200,
                json={
                    "state": "SUCCEEDED",
                    "result": {
                        "versions": [
                            {
                                "knowledgeId": identifier,
                                "versionId": "version-1",
                            }
                        ]
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "documentId": identifier,
                "versionId": "version-1",
                "checksum": "sha256:" + "a" * 64,
                "body": {"units": [{"unitId": "unit-0"}]},
            },
        )

    body = {
        "projectId": "groundwater-demo",
        "title": "fixture",
        "summary": "fixture",
        "language": "zh-CN",
        "contentVersion": "1",
        "sources": [],
        "chunks": [
            {"content": "fixture", "sourceRefs": []},
        ],
    }
    data_ref = {
        "project_id": "groundwater-demo",
        "dataset_id": "dataset-1",
        "version_id": "data-version-1",
        "semantic_id": "semantic-1",
    }
    with httpx.Client(
        base_url="http://knowledge.test",
        transport=httpx.MockTransport(handler),
    ) as client:
        receipt = runner._publish_document(client, identifier, body, data_ref)

    assert receipt == {
        "knowledge_id": identifier,
        "version_id": "version-1",
        "checksum": "sha256:" + "a" * 64,
        "unit_ids": ["unit-0"],
    }
