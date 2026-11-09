"""Strict client for the Data HTTP v3 service contract."""

from __future__ import annotations

import hashlib
from typing import BinaryIO

import httpx
from pydantic import BaseModel
from platform_protocol_contracts import HttpClient, Operation

from .generated import models, operations


_READ_COMPUTE_OPERATIONS = frozenset(
    {
        "data_discovery",
        "data_create_preview",
        "data_cancel_preview",
        "data_create_query",
        "data_cancel_query",
    }
)


class DataClient:
    def __init__(
        self,
        origin: str,
        project: str = "local-data",
        *,
        writes: bool = False,
        client_type: str = "sdk",
        timeout: float = 120,
        client: httpx.Client | None = None,
        identity_headers: dict[str, str] | None = None,
    ) -> None:
        self.project = project
        self.writes = writes
        self.identity_headers = dict(identity_headers or {})
        self.http = HttpClient(
            origin, "/data/v3", client_type=client_type, timeout=timeout, client=client
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
        content: bytes | object | None = None,
        headers: dict[str, str] | None = None,
    ) -> BaseModel | None:
        if (
            operation.side_effecting
            and not self.writes
            and operation.operation_id not in _READ_COMPUTE_OPERATIONS
        ):
            raise ValueError("WRITES_NOT_ENABLED")
        return self.http.call(
            operation,
            path=path,
            query=query,
            body=body,
            content=content,
            headers={
                **(headers or {}),
                **self.identity_headers,
                "X-Project-ID": self.project,
            },
        )

    def inventory(self) -> models.InventoryResponse:
        return self.call(operations.DATA_INVENTORY)  # type: ignore[return-value]

    def list_datasets(
        self, *, query: str = "", cursor: str | None = None, limit: int = 20
    ) -> models.DatasetPage:
        return self.call(
            operations.DATA_LIST_DATASETS,
            query={"query": query, "cursor": cursor, "limit": limit},
        )  # type: ignore[return-value]

    def list_versions(
        self, dataset_id: str, *, cursor: str | None = None, limit: int = 20
    ) -> models.VersionPage:
        return self.call(
            operations.DATA_LIST_DATASET_VERSIONS,
            path={"datasetId": dataset_id},
            query={"cursor": cursor, "limit": limit},
        )  # type: ignore[return-value]

    def discover(self, request: models.DiscoveryRequest) -> models.DiscoveryResponse:
        return self.call(operations.DATA_DISCOVERY, body=request)  # type: ignore[return-value]

    def read_version(self, version_id: str) -> models.VersionResponse:
        return self.call(operations.DATA_GET_VERSION, path={"versionId": version_id})  # type: ignore[return-value]

    def create_query(self, request: models.DataQueryRequest) -> models.QueryAccepted:
        return self.call(operations.DATA_CREATE_QUERY, body=request)  # type: ignore[return-value]

    def query(
        self, query_id: str, *, offset: int = 0, limit: int = 100
    ) -> models.QueryResponse:
        return self.call(
            operations.DATA_GET_QUERY,
            path={"queryId": query_id},
            query={"offset": offset, "limit": limit},
        )  # type: ignore[return-value]

    def cancel_query(self, query_id: str) -> models.QueryCancelResponse:
        return self.call(operations.DATA_CANCEL_QUERY, path={"queryId": query_id})  # type: ignore[return-value]

    def download_object(self, version_id: str, object_id: str):
        return self.http.stream(
            operations.DATA_DOWNLOAD_OBJECT,
            path={"versionId": version_id, "objectId": object_id},
            headers={**self.identity_headers, "X-Project-ID": self.project},
        )

    def begin_publication(
        self, request: models.BeginPublicationRequest
    ) -> models.PublicationResponse:
        return self.call(operations.DATA_BEGIN_PUBLICATION, body=request)  # type: ignore[return-value]

    def upload(
        self, publication_id: str, name: str, source: BinaryIO
    ) -> models.ObjectDescriptor:
        if not self.writes:
            raise ValueError("WRITES_NOT_ENABLED")
        checksum = "sha256:" + hashlib.file_digest(source, "sha256").hexdigest()
        source.seek(0)
        return self.call(
            operations.DATA_UPLOAD_PUBLICATION_ITEM,
            path={"publicationId": publication_id, "name": name},
            content=iter(lambda: source.read(1024 * 1024), b""),
            headers={"X-Content-SHA256": checksum},
        )  # type: ignore[return-value]

    def prepare_publication(self, publication_id: str) -> models.OperationAccepted:
        return self.call(
            operations.DATA_PREPARE_PUBLICATION,
            path={"publicationId": publication_id},
        )  # type: ignore[return-value]

    def get_publication(self, publication_id: str) -> models.PublicationResponse:
        return self.call(
            operations.DATA_GET_PUBLICATION,
            path={"publicationId": publication_id},
        )  # type: ignore[return-value]

    def abort_publication(self, publication_id: str) -> models.PublicationAbortResponse:
        return self.call(
            operations.DATA_ABORT_PUBLICATION,
            path={"publicationId": publication_id},
        )  # type: ignore[return-value]

    def commit_publication(self, publication_id: str) -> models.PublicationReceipt:
        return self.call(
            operations.DATA_COMMIT_PUBLICATION,
            path={"publicationId": publication_id},
        )  # type: ignore[return-value]


__all__ = ["DataClient", "models", "operations"]
