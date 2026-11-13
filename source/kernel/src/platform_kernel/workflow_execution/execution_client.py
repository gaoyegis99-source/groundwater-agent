from __future__ import annotations

import httpx
from platform_executor_client import AsyncExecutorClient
from platform_executor_client.generated import models as executor_models
from platform_protocol_contracts import (
    ApiProblem,
    ProtocolError,
    TransportError,
    inject_trace_headers,
)

from ..contracts import JobRequestV6, JobStatusV6
from ..settings import ServiceBinding
from .node_diagnostics import WorkflowNodeDiagnostic


class ExecutionClientError(RuntimeError):
    pass


class ExecutionRejectedError(RuntimeError):
    pass


class ExecutionClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def get_definition_set_digest(
        self, service: ServiceBinding, project_id: str
    ) -> str:
        try:
            response = await self.client.get(
                f"{service.internal_base_url}/internal/health/ready",
                headers=inject_trace_headers({"X-Project-ID": project_id}),
                timeout=10.0,
            )
            response.raise_for_status()
            payload = response.json()
            digest = payload.get("definitionSetDigest") if isinstance(payload, dict) else None
            if (
                not isinstance(digest, str)
                or not digest.startswith("sha256:")
                or len(digest) != 71
            ):
                raise ValueError("EXECUTOR_DEFINITION_SET_DIGEST_INVALID")
            return digest
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise ExecutionClientError(str(error)) from error

    async def submit(self, service: ServiceBinding, request: JobRequestV6) -> JobStatusV6:
        try:
            api = AsyncExecutorClient(service.internal_base_url, client=self.client)
            body = executor_models.JobRequestBodyV6.model_validate(request.as_dict())
            result = await api.submit(body)
            return JobStatusV6.from_dict(result.model_dump(mode="json", by_alias=True))
        except ApiProblem as error:
            if 400 <= error.problem.status < 500 and error.problem.status not in {408, 429}:
                raise ExecutionRejectedError(error.problem.code) from error
            raise ExecutionClientError(error.problem.code) from error
        except (TransportError, ProtocolError, ValueError, TypeError) as error:
            raise ExecutionClientError(str(error)) from error

    async def get(self, service: ServiceBinding, job_id: str) -> JobStatusV6:
        try:
            result = await AsyncExecutorClient(
                service.internal_base_url, client=self.client
            ).get(job_id)
            return JobStatusV6.from_dict(result.model_dump(mode="json", by_alias=True))
        except (ApiProblem, TransportError, ProtocolError, ValueError, TypeError) as error:
            raise ExecutionClientError(str(error)) from error

    async def get_by_request(
        self, service: ServiceBinding, project_id: str, request_id: str
    ) -> JobStatusV6 | None:
        try:
            result = await AsyncExecutorClient(
                service.internal_base_url, client=self.client
            ).get_by_request(request_id, project_id)
            return JobStatusV6.from_dict(result.model_dump(mode="json", by_alias=True))
        except ApiProblem as error:
            if error.problem.status == 404:
                return None
            raise ExecutionClientError(error.problem.code) from error
        except (TransportError, ProtocolError, ValueError, TypeError) as error:
            raise ExecutionClientError(str(error)) from error

    async def cancel(self, service: ServiceBinding, job_id: str) -> None:
        try:
            await AsyncExecutorClient(service.internal_base_url, client=self.client).cancel(job_id)
        except (ApiProblem, TransportError, ProtocolError) as error:
            raise ExecutionClientError(str(error)) from error

    async def get_nodes(
        self, service: ServiceBinding, job_id: str,
    ) -> list[WorkflowNodeDiagnostic] | None:
        try:
            result = await AsyncExecutorClient(
                service.internal_base_url, timeout=5, client=self.client,
            ).get_nodes(job_id)
            return [WorkflowNodeDiagnostic.from_executor(row) for row in result.nodes]
        except ApiProblem as error:
            if error.problem.status == 404:
                return None
            raise ExecutionClientError(error.problem.code) from error
        except (TransportError, ProtocolError, ValueError, TypeError) as error:
            raise ExecutionClientError(str(error)) from error


    async def get_graph(
        self, service: ServiceBinding, job_id: str,
    ) -> executor_models.JobGraphResponse | None:
        try:
            return await AsyncExecutorClient(
                service.internal_base_url, timeout=5, client=self.client,
            ).get_graph(job_id)
        except ApiProblem as error:
            if error.problem.status == 404:
                return None
            raise ExecutionClientError(error.problem.code) from error
        except (TransportError, ProtocolError) as error:
            raise ExecutionClientError(str(error)) from error
