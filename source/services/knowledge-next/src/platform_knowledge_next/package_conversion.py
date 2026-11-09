"""Explicit import conversion; no dependency on the assembled knowledge service."""

from .encoding import digest
from .models import KnowledgePackageV1


def convert_domain_package(body, *, project, idempotency_key, prefix=""):
    if body.get("schemaVersion") != "domain-knowledge.v2":
        raise ValueError("UNSUPPORTED_LEGACY_SCHEMA")
    names = {
        "sourceId": "source_id",
        "publishedAt": "published_at",
        "accessedAt": "accessed_at",
        "reviewAfter": "review_after",
        "validThrough": "valid_through",
        "lifecycleStatus": "lifecycle_status",
    }
    sources = {
        s["sourceId"]: {names.get(k, k): v for k, v in s.items()}
        for s in body["sources"]
    }
    documents = []
    for d in body["documents"]:
        refs = {s for u in d["chunks"] for s in u["sourceRefs"]}
        documents.append(
            {
                "knowledge_id": prefix + d["knowledgeId"],
                "title": d["title"],
                "summary": d["summary"],
                "content_version": body["contentVersion"],
                "language": body["language"],
                "sources": [s for k, s in sources.items() if k in refs],
                "units": [
                    {
                        "unit_id": f"chunk-{i}",
                        "content": u["content"],
                        "source_refs": u["sourceRefs"],
                    }
                    for i, u in enumerate(d["chunks"])
                ],
                "provenance": {
                    "schema": body["schemaVersion"],
                    "package_checksum": digest(body),
                },
            }
        )
    return KnowledgePackageV1(
        project_id=project, idempotency_key=idempotency_key, documents=documents
    )
