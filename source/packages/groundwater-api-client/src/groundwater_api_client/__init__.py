"""Public Groundwater Gateway client for Public API v12."""

from __future__ import annotations

from collections.abc import Iterator

import httpx
from pydantic import BaseModel
from platform_protocol_contracts import HttpClient, Operation, ProtocolError
from platform_protocol_contracts.client import ClientType

from .generated import models, operations


class GroundwaterClient:
    """Use the same versioned Gateway contract as the WebUI."""

    def __init__(
        self,
        origin: str,
        *,
        client_type: ClientType = "sdk",
        timeout: float = 60,
        client: httpx.Client | None = None,
        api_token: str | None = None,
    ) -> None:
        self.http = HttpClient(
            origin, "/api/v12", client_type=client_type, timeout=timeout, client=client
        )
        self._authorization = f"Bearer {api_token}" if api_token else None
        self._csrf_token: str | None = None

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def close(self) -> None:
        self.http.close()

    def _headers(self, method: str) -> dict[str, str]:
        if self._authorization:
            return {"Authorization": self._authorization}
        if self._csrf_token and method not in {"GET", "HEAD", "OPTIONS"}:
            return {"X-CSRF-Token": self._csrf_token}
        return {}

    def call(
        self,
        operation: Operation,
        *,
        path: dict[str, object] | None = None,
        query: dict[str, object] | None = None,
        body: BaseModel | dict[str, object] | None = None,
    ) -> BaseModel | None:
        params = {name: value for name, value in (query or {}).items() if value is not None}
        return self.http.call(
            operation, path=path, query=params, body=body, headers=self._headers(operation.method)
        )

    def stream(
        self,
        operation: Operation,
        *,
        path: dict[str, object] | None = None,
        query: dict[str, object] | None = None,
    ) -> Iterator[bytes]:
        params = {name: value for name, value in (query or {}).items() if value is not None}
        return self.http.stream(
            operation, path=path, query=params, headers=self._headers(operation.method)
        )

    def login(self, username: str, password: str) -> models.KernelIdentityResponse:
        if self._authorization:
            raise ValueError("AUTH_MODE_CONFLICT")
        response = self.call(
            operations.AUTH_LOGIN_API_V12_AUTH_LOGIN_POST,
            body=models.KernelLoginRequest(username=username, password=password),
        )
        assert isinstance(response, models.KernelLoginResponse)
        self._csrf_token = response.csrf_token
        return response.current_user

    def capabilities(self) -> models.KernelCapabilitiesResponse:
        return self.call(operations.KERNEL_CAPABILITIES)  # type: ignore[return-value]

    def create_session(
        self, request: models.KernelSessionCreate
    ) -> models.KernelSessionResponse:
        return self.call(operations.KERNEL_CREATE_SESSION, body=request)  # type: ignore[return-value]

    def list_sessions(
        self,
        *,
        cursor: int | None = None,
        limit: int = 20,
        query: str | None = None,
        archived: bool | None = None,
    ) -> models.KernelSessionPage:
        return self.call(
            operations.KERNEL_LIST_SESSIONS,
            query={"cursor": cursor, "limit": limit, "query": query, "archived": archived},
        )  # type: ignore[return-value]

    def session(self, session_id: str) -> models.KernelSessionDetailResponse:
        return self.call(
            operations.KERNEL_GET_SESSION, path={"sessionId": session_id}
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

    def confirm(self, session_id: str, confirmation_id: str) -> models.KernelSessionResponse:
        return self.call(
            operations.KERNEL_CONFIRM,
            path={"sessionId": session_id},
            body=models.KernelConfirmationRequest(confirmationId=confirmation_id),
        )  # type: ignore[return-value]

    def reject(
        self, session_id: str, confirmation_id: str, reason: str
    ) -> models.KernelSessionResponse:
        return self.call(
            operations.KERNEL_REJECT,
            path={"sessionId": session_id},
            body=models.KernelRejectionRequest(confirmationId=confirmation_id, reason=reason),
        )  # type: ignore[return-value]

    def stop(
        self, session_id: str, request: models.KernelStopRequest
    ) -> models.KernelStopResponse:
        return self.call(
            operations.KERNEL_STOP_RUN,
            path={"sessionId": session_id},
            body=request,
        )  # type: ignore[return-value]

    def events(self, session_id: str, *, cursor: int | None = None) -> Iterator[bytes]:
        return self.stream(
            operations.KERNEL_EVENTS,
            path={"sessionId": session_id},
            query={"afterEventId": cursor},
        )

    def results(self, session_id: str, *, cursor: int = 0) -> models.KernelSessionResultsResponse:
        return self.call(
            operations.KERNEL_GET_SESSION_RESULTS,
            path={"sessionId": session_id},
            query={"cursor": cursor},
        )  # type: ignore[return-value]

    def list_datasets(
        self, *, query: str | None = None, cursor: str | None = None, limit: int = 20
    ) -> models.DataDatasetPage:
        return self.call(
            operations.DATA_LIST_DATASETS, query={"query": query, "cursor": cursor, "limit": limit}
        )  # type: ignore[return-value]

    def version(self, version_id: str) -> models.DataVersionResponse:
        return self.call(
            operations.DATA_GET_VERSION, path={"versionId": version_id}
        )  # type: ignore[return-value]

    def input_reference(
        self, version_id: str, object_id: str | None = None
    ) -> models.KernelMessageInputReference:
        version = self.version(version_id)
        if version.version_id != version_id:
            raise ProtocolError("DATA_VERSION_IDENTITY_MISMATCH")
        if object_id is None:
            obj = next(
                (item for item in version.objects if item.role == models.Role.raw),
                next(iter(version.objects), None),
            )
        else:
            obj = next((item for item in version.objects if item.object_id == object_id), None)
        if obj is None:
            raise ValueError("DATA_OBJECT_NOT_FOUND")
        if not obj.format or not obj.media_type:
            raise ProtocolError("DATA_OBJECT_DESCRIPTOR_INCOMPLETE")
        return models.KernelMessageInputReference(
            versionId=version.version_id,
            datasetId=version.dataset_id,
            semanticId=version.semantic_id,
            objectId=obj.object_id,
            checksum=obj.checksum,
            sizeBytes=obj.size,
            format=obj.format,
            mediaType=obj.media_type,
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

    def download_object(self, version_id: str, object_id: str) -> Iterator[bytes]:
        return self.stream(
            operations.DATA_DOWNLOAD_OBJECT,
            path={"versionId": version_id, "objectId": object_id},
        )

    def retrieve(
        self, request: models.KnowledgeNextRetrievalRequest
    ) -> models.KnowledgeNextEvidenceBundleV1:
        return self.call(operations.KNOWLEDGE_RETRIEVE, body=request)  # type: ignore[return-value]


__all__ = ["GroundwaterClient", "models", "operations"]
