"""Read-only toward Data; creates and withdraws an explicit Knowledge test record."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import httpx
from platform_knowledge_client import KnowledgeClient, models
from platform_knowledge_next.models import KnowledgePackageV1

from evaluate import wait


def as_dict(value):
    return value.model_dump(mode="json", by_alias=True)


def retrieve(client, payload):
    return as_dict(client.retrieve(models.RetrievalRequest.model_validate({
        "mode": "hybrid", "purpose": "data-integration", **payload,
    })))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-url", default="http://127.0.0.1:8110")
    parser.add_argument("--knowledge-url", default="http://127.0.0.1:8120")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with httpx.Client(base_url=args.data_url + "/data/v3", trust_env=False) as data:
        rows = data.get("/datasets", params={"limit": 1}).json()["items"]
        if not rows:
            raise ValueError("Publish a Data fixture before this integration check")
        dataset = rows[0]
        version = data.get("/versions/" + dataset["currentVersion"]).json()
    binding = {
        "serviceBinding": "data-primary",
        "projectId": dataset["project"],
        "datasetId": dataset["datasetId"],
        "versionId": version["versionId"],
        "semanticId": version["semanticId"],
        "fieldId": None,
    }
    now = datetime.now(timezone.utc)
    identity = "data-integration-" + now.strftime("%Y%m%d%H%M%S")
    client = KnowledgeClient(args.knowledge_url, writes=True)
    package = {
        "project_id": dataset["project"],
        "idempotency_key": identity,
        "documents": [
            {
                "knowledge_id": identity,
                "title": "Data 绑定集成测试",
                "content_version": "1.0.0",
                "sources": [
                    {
                        "source_id": "test",
                        "kind": "DEMO_ASSUMPTION",
                        "title": "集成测试",
                        "publisher": "Test harness",
                        "review_after": "2099-01-01",
                    }
                ],
                "units": [
                    {
                        "unit_id": "test",
                        "content": "本条仅验证固定版本的数据引用，不声明任何实际数据结论。",
                        "source_refs": ["test"],
                    }
                ],
                "bindings": [binding],
            }
        ],
    }
    package["documents"][0]["title"] = identity
    general = {
        **package["documents"][0],
        "knowledge_id": identity + "-general",
        "bindings": [],
    }
    package["documents"].append(general)
    package = KnowledgePackageV1.model_validate(package).model_dump(
        mode="json", by_alias=True
    )
    accepted = as_dict(client.submit_import(
        models.KnowledgePackageRequest.model_validate(package)
    ))
    state = wait(client, accepted["importId"])
    vid = state["result"]["versions"][0]["versionId"]
    checks = []
    try:
        ordinary = retrieve(client, {"query": identity})
        assert all(i["scope"] == "GENERAL" for i in ordinary["items"])
        combined = retrieve(client, {"query": identity, "binding": binding})
        assert {identity, identity + "-general"} <= {
            i["knowledgeId"] for i in combined["items"]
        }
        assert any(i["bindingMatch"] == "EXACT" for i in combined["items"])
        for changes in (
            {"semanticId": "different-revision"},
            {"versionId": "different-version"},
        ):
            rejected = retrieve(client, {
                    "query": identity,
                    "scope": "bound",
                    "binding": {**binding, **changes},
                })
            assert rejected["status"] == "NO_MATCH"
        field_request = retrieve(client, {
            "query": identity,
            "scope": "bound",
            "binding": {**binding, "fieldId": "f999"},
        })
        assert field_request["status"] == "OK"
        assert identity in {
            item["knowledgeId"] for item in field_request["items"]
        }
        assert any(
            item["knowledgeId"] == identity and item["bindingMatch"] == "EXACT"
            for item in field_request["items"]
        )
        checks = [
            "real embedding general-only default",
            "real embedding combined includes both scopes",
            "bound excludes wrong version/semantic",
            "version-wide binding applies to requested field",
            "strict binding lookup",
        ]
        result = as_dict(client.lookup(models.LookupRequest(key=identity, binding=models.DataBinding.model_validate(binding))))
        assert result["items"][0]["bindingsVerifiedAt"]
        assert result["items"][0]["versionId"] == vid
        assert (
            as_dict(client.lookup(models.LookupRequest.model_validate({
                    "key": identity,
                    "binding": {**binding, "semanticId": "different-revision"},
                }
            )))["status"]
            == "NO_MATCH"
        )
    finally:
        for version in state["result"]["versions"]:
            client.update_lifecycle(
                version["versionId"],
                models.LifecycleUpdateRequest.model_validate({
                    "expectedRevision": 1,
                    "status": "WITHDRAWN",
                    "reviewAfter": "2099-01-01",
                    "reason": "integration check completed",
                }),
            )
        client.close()
    args.output.write_text(
        json.dumps(
            {
                "status": "PASS",
                "time": now.isoformat(),
                "retrieval_timings_ms": combined["timingsMs"],
                "checks": checks
                + [
                    "Data fixed-version HTTP",
                    "historical semantic HTTP",
                    "worker import binding validation",
                    "exact binding lookup",
                    "different semantic rejected",
                ],
                "data_writes": 0,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
