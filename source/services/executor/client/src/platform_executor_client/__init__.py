"""Strict clients for Executor HTTP v6."""

from __future__ import annotations

import httpx
from platform_protocol_contracts import AsyncHttpClient, HttpClient

from .generated import models, operations


class ExecutorClient:
    def __init__(
        self,
        origin: str,
        *,
        client_type: str = "service",
        timeout: float = 30,
        client: httpx.Client | None = None,
    ) -> None:
        self.http = HttpClient(
            origin,
            "/internal/execution/v6",
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

    def submit(self, request: models.JobRequestBodyV6) -> models.JobStatusResponseV6:
        return self.http.call(operations.EXECUTOR_SUBMIT_JOB, body=request)  # type: ignore[return-value]

    def get(self, job_id: str) -> models.JobStatusResponseV6:
        return self.http.call(
            operations.EXECUTOR_GET_JOB, path={"jobId": job_id}
        )  # type: ignore[return-value]

    def get_by_request(
        self, request_id: str, project_id: str
    ) -> models.JobStatusResponseV6:
        return self.http.call(
            operations.EXECUTOR_GET_JOB_BY_REQUEST,
            path={"requestId": request_id},
            query={"projectId": project_id},
        )  # type: ignore[return-value]

    def cancel(self, job_id: str) -> models.JobStatusResponseV6:
        return self.http.call(
            operations.EXECUTOR_CANCEL_JOB, path={"jobId": job_id}
        )  # type: ignore[return-value]


class AsyncExecutorClient:
    def __init__(
        self,
        origin: str,
        *,
        client_type: str = "service",
        timeout: float = 30,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.http = AsyncHttpClient(
            origin,
            "/internal/execution/v6",
            client_type=client_type,
            timeout=timeout,
            client=client,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        await self.close()

    async def close(self) -> None:
        await self.http.close()

    async def submit(
        self, request: models.JobRequestBodyV6
    ) -> models.JobStatusResponseV6:
        return await self.http.call(
            operations.EXECUTOR_SUBMIT_JOB, body=request
        )  # type: ignore[return-value]

    async def get(self, job_id: str) -> models.JobStatusResponseV6:
        return await self.http.call(
            operations.EXECUTOR_GET_JOB, path={"jobId": job_id}
        )  # type: ignore[return-value]

    async def get_nodes(self, job_id: str) -> models.NodeDiagnosticsResponse:
        return await self.http.call(
            operations.EXECUTOR_GET_JOB_NODES, path={"jobId": job_id}
        )  # type: ignore[return-value]

    async def get_by_request(
        self, request_id: str, project_id: str
    ) -> models.JobStatusResponseV6:
        return await self.http.call(
            operations.EXECUTOR_GET_JOB_BY_REQUEST,
            path={"requestId": request_id},
            query={"projectId": project_id},
        )  # type: ignore[return-value]

    async def cancel(self, job_id: str) -> models.JobStatusResponseV6:
        return await self.http.call(
            operations.EXECUTOR_CANCEL_JOB, path={"jobId": job_id}
        )  # type: ignore[return-value]


__all__ = ["AsyncExecutorClient", "ExecutorClient", "models", "operations"]
