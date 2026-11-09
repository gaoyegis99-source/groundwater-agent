"""Public Groundwater Gateway client for Public API v12."""

from __future__ import annotations

import httpx
from pydantic import BaseModel
from platform_protocol_contracts import HttpClient, Operation

from .generated import models, operations


class GroundwaterClient:
    def __init__(
        self,
        origin: str,
        *,
        client_type: str = "sdk",
        timeout: float = 60,
        client: httpx.Client | None = None,
    ) -> None:
        self.http = HttpClient(
            origin, "/api/v12", client_type=client_type, timeout=timeout, client=client
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
        return self.http.call(operation, path=path, query=query, body=body)

    def stream(
        self,
        operation: Operation,
        *,
        path: dict[str, object] | None = None,
        query: dict[str, object] | None = None,
    ):
        return self.http.stream(operation, path=path, query=query)

    def create_session(
        self, request: models.KernelSessionCreate
    ) -> models.KernelSessionResponse:
        return self.call(operations.KERNEL_CREATE_SESSION, body=request)  # type: ignore[return-value]

    def list_sessions(
        self, *, cursor: int | None = None, limit: int = 20
    ) -> models.KernelSessionPage:
        return self.call(
            operations.KERNEL_LIST_SESSIONS,
            query={"cursor": cursor, "limit": limit},
        )  # type: ignore[return-value]

    def history(
        self, session_id: str, *, cursor: int | None = None, limit: int = 100
    ) -> models.KernelMessageHistoryResponse:
        return self.call(
            operations.KERNEL_GET_MESSAGES,
            path={"sessionId": session_id},
            query={"cursor": cursor, "limit": limit},
        )  # type: ignore[return-value]

    def send_message(
        self, session_id: str, request: models.KernelMessageCreate
    ) -> models.KernelMessageReceiptResponse:
        return self.call(
            operations.KERNEL_CREATE_MESSAGE,
            path={"sessionId": session_id},
            body=request,
        )  # type: ignore[return-value]

    def stop(
        self, session_id: str, request: models.KernelStopRequest
    ) -> models.KernelStopResponse:
        return self.call(
            operations.KERNEL_STOP_RUN,
            path={"sessionId": session_id},
            body=request,
        )  # type: ignore[return-value]

    def events(self, session_id: str, *, cursor: int | None = None):
        return self.stream(
            operations.KERNEL_EVENTS,
            path={"sessionId": session_id},
            query={"cursor": cursor},
        )

    def create_query(
        self, request: models.DataDataQueryRequest
    ) -> models.DataQueryAccepted:
        return self.call(operations.DATA_CREATE_QUERY, body=request)  # type: ignore[return-value]

    def query(
        self, query_id: str, *, offset: int = 0, limit: int = 100
    ) -> models.DataQueryResponse:
        return self.call(
            operations.DATA_GET_QUERY,
            path={"queryId": query_id},
            query={"offset": offset, "limit": limit},
        )  # type: ignore[return-value]

    def cancel_query(self, query_id: str) -> models.DataQueryCancelResponse:
        return self.call(
            operations.DATA_CANCEL_QUERY, path={"queryId": query_id}
        )  # type: ignore[return-value]

    def download_object(self, version_id: str, object_id: str):
        return self.stream(
            operations.DATA_DOWNLOAD_OBJECT,
            path={"versionId": version_id, "objectId": object_id},
        )

    def retrieve(
        self, request: models.KnowledgeNextRetrievalRequest
    ) -> models.KnowledgeNextEvidenceBundleV1:
        return self.call(operations.KNOWLEDGE_RETRIEVE, body=request)  # type: ignore[return-value]


__all__ = ["GroundwaterClient", "models", "operations"]
