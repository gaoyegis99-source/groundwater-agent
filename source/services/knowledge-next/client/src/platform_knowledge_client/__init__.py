"""Strict client for the Knowledge HTTP v3 service contract."""

from __future__ import annotations

import httpx
from pydantic import BaseModel
from platform_protocol_contracts import HttpClient, Operation

from .generated import models, operations


class KnowledgeClient:
    def __init__(
        self,
        origin: str,
        *,
        writes: bool = False,
        client_type: str = "sdk",
        timeout: float = 60,
        client: httpx.Client | None = None,
    ) -> None:
        self.writes = writes
        self.http = HttpClient(
            origin,
            "/knowledge/v3",
            client_type=client_type,
            timeout=timeout,
            client=client,
        )

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def close(self) -> None:
        self.http.close()

    def call(
        self,
        operation: Operation,
        *,
        path: dict[str, object] | None = None,
        query: dict[str, object] | None = None,
        body: BaseModel | dict[str, object] | None = None,
    ) -> BaseModel | None:
        if operation.side_effecting and not self.writes and operation.operation_id not in {
            "knowledge_retrieve",
            "knowledge_read_evidence",
            "knowledge_validate_evidence",
            "knowledge_lookup",
        }:
            raise ValueError("WRITES_NOT_ENABLED")
        return self.http.call(operation, path=path, query=query, body=body)

    def retrieve(self, request: models.RetrievalRequest) -> models.EvidenceBundleV1:
        return self.call(operations.KNOWLEDGE_RETRIEVE, body=request)  # type: ignore[return-value]

    def capabilities(self) -> models.CapabilitiesResponse:
        return self.call(operations.KNOWLEDGE_CAPABILITIES)  # type: ignore[return-value]

    def submit_import(self, request: models.KnowledgePackageRequest) -> models.ImportResponse:
        return self.call(operations.KNOWLEDGE_SUBMIT_IMPORT, body=request)  # type: ignore[return-value]

    def get_import(self, import_id: str) -> models.ImportResponse:
        return self.call(
            operations.KNOWLEDGE_GET_IMPORT, path={"importId": import_id}
        )  # type: ignore[return-value]

    def retry_import(
        self, import_id: str, request: models.RetryImportRequest
    ) -> models.ImportResponse:
        return self.call(
            operations.KNOWLEDGE_RETRY_IMPORT,
            path={"importId": import_id},
            body=request,
        )  # type: ignore[return-value]

    def read_evidence(self, request: models.EvidenceReadRequest) -> models.EvidenceBundleV1:
        return self.call(operations.KNOWLEDGE_READ_EVIDENCE, body=request)  # type: ignore[return-value]

    def lookup(self, request: models.LookupRequest) -> models.EvidenceBundleV1:
        return self.call(operations.KNOWLEDGE_LOOKUP, body=request)  # type: ignore[return-value]

    def read_version(self, version_id: str) -> models.VersionResponse:
        return self.call(
            operations.KNOWLEDGE_GET_VERSION, path={"versionId": version_id}
        )  # type: ignore[return-value]

    def update_lifecycle(
        self, version_id: str, request: models.LifecycleUpdateRequest
    ) -> models.LifecycleResponse:
        return self.call(
            operations.KNOWLEDGE_UPDATE_LIFECYCLE,
            path={"versionId": version_id},
            body=request,
        )  # type: ignore[return-value]


__all__ = ["KnowledgeClient", "models", "operations"]
