from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Generic, Literal, TypeVar
from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel, ValidationError

from .problem_v11 import ProblemDetailsV11
from .tracing import inject_trace_headers

PathT = TypeVar("PathT")
QueryT = TypeVar("QueryT")
BodyT = TypeVar("BodyT")
ResponseT = TypeVar("ResponseT", bound=BaseModel)

ClientType = Literal["web", "sdk", "agent", "service", "cli"]
CLIENT_TYPES = frozenset({"web", "sdk", "agent", "service", "cli"})


@dataclass(frozen=True)
class Operation(Generic[PathT, QueryT, BodyT, ResponseT]):
    operation_id: str
    method: str
    path: str
    response_model: type[ResponseT] | None
    success_statuses: frozenset[int]
    media_type: str = "application/json"
    side_effecting: bool = False


class ApiProblem(RuntimeError):  # noqa: N818 - public SDK contract name
    def __init__(self, problem: ProblemDetailsV11, headers: Mapping[str, str]) -> None:
        super().__init__(problem.code)
        self.problem = problem
        self.headers = dict(headers)


class TransportError(RuntimeError):
    pass


class ProtocolError(RuntimeError):
    pass


def validate_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("SERVICE_ORIGIN_INVALID")
    return f"{parsed.scheme}://{parsed.netloc}"


class HttpClient:
    def __init__(
        self,
        origin: str,
        prefix: str,
        *,
        client_type: ClientType,
        timeout: float | httpx.Timeout = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.origin = validate_origin(origin)
        if not prefix.startswith("/") or prefix.endswith("/"):
            raise ValueError("SERVICE_PREFIX_INVALID")
        self.prefix = prefix
        if client_type not in CLIENT_TYPES:
            raise ValueError("CLIENT_TYPE_INVALID")
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout, trust_env=False)
        self.client_type = client_type

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def call(
        self,
        operation: Operation,
        *,
        path: Mapping[str, object] | None = None,
        query: Mapping[str, object] | None = None,
        body: BaseModel | Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
        content: bytes | Iterable[bytes] | None = None,
    ) -> BaseModel:
        route = operation.path
        for name, value in (path or {}).items():
            route = route.replace("{" + name + "}", quote(str(value), safe=""))
        if "{" in route:
            raise ProtocolError("PATH_PARAMETERS_MISSING")
        payload = (
            body.model_dump(mode="json", by_alias=True, exclude_none=True)
            if isinstance(body, BaseModel)
            else body
        )
        request_headers = inject_trace_headers(headers)
        request_headers["X-Client-Type"] = self.client_type
        try:
            response = self._client.request(
                operation.method,
                self.origin + self.prefix + route,
                params=query,
                json=payload if content is None else None,
                content=content,
                headers=request_headers,
            )
        except httpx.HTTPError as error:
            raise TransportError("HTTP_TRANSPORT_FAILED") from error
        self._raise_for_problem(response)
        if response.status_code not in operation.success_statuses:
            raise ProtocolError("SUCCESS_STATUS_UNDECLARED")
        self._validate_success_media_type(response, operation.media_type)
        if operation.response_model is None:
            raise ProtocolError("RESPONSE_MODEL_MISSING")
        try:
            return operation.response_model.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise ProtocolError("RESPONSE_CONTRACT_INVALID") from error

    def stream(
        self,
        operation: Operation,
        *,
        path: Mapping[str, object] | None = None,
        query: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Iterator[bytes]:
        route = operation.path
        for name, value in (path or {}).items():
            route = route.replace("{" + name + "}", quote(str(value), safe=""))
        if "{" in route:
            raise ProtocolError("PATH_PARAMETERS_MISSING")
        request_headers = inject_trace_headers(headers)
        request_headers["X-Client-Type"] = self.client_type
        try:
            with self._client.stream(
                operation.method,
                self.origin + self.prefix + route,
                params=query,
                headers=request_headers,
            ) as response:
                if response.status_code >= 400:
                    response.read()
                self._raise_for_problem(response)
                if response.status_code not in operation.success_statuses:
                    raise ProtocolError("SUCCESS_STATUS_UNDECLARED")
                self._validate_success_media_type(response, operation.media_type)
                yield from response.iter_bytes()
        except httpx.HTTPError as error:
            raise TransportError("HTTP_TRANSPORT_FAILED") from error

    @staticmethod
    def _raise_for_problem(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        if response.headers.get("content-type", "").split(";", 1)[0] != "application/problem+json":
            raise ProtocolError("PROBLEM_CONTENT_TYPE_INVALID")
        try:
            problem = ProblemDetailsV11.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise ProtocolError("PROBLEM_CONTRACT_INVALID") from error
        raise ApiProblem(problem, response.headers)

    @staticmethod
    def _validate_success_media_type(response: httpx.Response, expected: str) -> None:
        actual = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if actual != expected:
            raise ProtocolError("RESPONSE_CONTENT_TYPE_INVALID")


class AsyncHttpClient:
    """Async equivalent used by service consumers with an existing event loop."""

    def __init__(
        self,
        origin: str,
        prefix: str,
        *,
        client_type: ClientType,
        timeout: float | httpx.Timeout = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.origin = validate_origin(origin)
        if not prefix.startswith("/") or prefix.endswith("/"):
            raise ValueError("SERVICE_PREFIX_INVALID")
        if client_type not in CLIENT_TYPES:
            raise ValueError("CLIENT_TYPE_INVALID")
        self.prefix = prefix
        self.client_type = client_type
        self.timeout = timeout
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout, trust_env=False)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def call(
        self,
        operation: Operation,
        *,
        path: Mapping[str, object] | None = None,
        query: Mapping[str, object] | None = None,
        body: BaseModel | Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> BaseModel:
        route = operation.path
        for name, value in (path or {}).items():
            route = route.replace("{" + name + "}", quote(str(value), safe=""))
        if "{" in route:
            raise ProtocolError("PATH_PARAMETERS_MISSING")
        payload = (
            body.model_dump(mode="json", by_alias=True, exclude_none=True)
            if isinstance(body, BaseModel)
            else body
        )
        request_headers = inject_trace_headers(headers)
        request_headers["X-Client-Type"] = self.client_type
        try:
            response = await self._client.request(
                operation.method,
                self.origin + self.prefix + route,
                params=query,
                json=payload,
                headers=request_headers,
                timeout=self.timeout,
            )
        except httpx.HTTPError as error:
            raise TransportError("HTTP_TRANSPORT_FAILED") from error
        HttpClient._raise_for_problem(response)
        if response.status_code not in operation.success_statuses:
            raise ProtocolError("SUCCESS_STATUS_UNDECLARED")
        HttpClient._validate_success_media_type(response, operation.media_type)
        if operation.response_model is None:
            raise ProtocolError("RESPONSE_MODEL_MISSING")
        try:
            return operation.response_model.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise ProtocolError("RESPONSE_CONTRACT_INVALID") from error
