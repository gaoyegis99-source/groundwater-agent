from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
from dataclasses import replace
import time
from typing import Any

from agents.models.interface import Model
from agents import ModelTimeoutError
import httpx
import httpx2
from openai import APIConnectionError, APIStatusError, APITimeoutError

from ..settings import IntentRouterSettings, KernelSettings
from .provider import (
    AgentProvider,
    build_agent_provider,
    build_intent_router_provider,
)


class ProviderUnavailableError(RuntimeError):
    """Raised before generation when no configured endpoint is ready."""


@dataclass(frozen=True)
class ProviderAttempt:
    attempt: int
    endpoint: str
    outcome: str
    error_code: str | None = None
    emitted_event: bool = False


@dataclass(frozen=True)
class ProbeResult:
    available: bool
    code: str | None
    endpoint: str

    def public(self) -> dict[str, str]:
        result = {"status": "UP" if self.available else "DOWN", "endpoint": self.endpoint}
        if self.code is not None:
            result["error"] = self.code
        return result


class HttpReadinessProbe:
    """Cached, zero-generation-token readiness probe for one HTTP endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        path: str,
        api_key: str | None,
        endpoint: str,
        timeout_seconds: int,
        success_cache_seconds: int,
        failure_cache_seconds: int,
        client: httpx.AsyncClient | httpx2.AsyncClient | None = None,
    ) -> None:
        self.url = base_url.rstrip("/") + path
        self.endpoint = endpoint
        self.headers = (
            {"Authorization": f"Bearer {api_key}"} if api_key is not None else {}
        )
        self.timeout_seconds = timeout_seconds
        self.success_cache_seconds = success_cache_seconds
        self.failure_cache_seconds = failure_cache_seconds
        self._client = client or httpx.AsyncClient(trust_env=False)
        self._owns_client = client is None
        self._cached: tuple[float, ProbeResult] | None = None
        self._lock = asyncio.Lock()

    async def check(self, *, force: bool = False) -> ProbeResult:
        now = time.monotonic()
        if not force and self._cached is not None and self._cached[0] > now:
            return self._cached[1]
        async with self._lock:
            now = time.monotonic()
            if not force and self._cached is not None and self._cached[0] > now:
                return self._cached[1]
            result = await self._request()
            ttl = (
                self.success_cache_seconds
                if result.available
                else self.failure_cache_seconds
            )
            self._cached = (time.monotonic() + ttl, result)
            return result

    async def _request(self) -> ProbeResult:
        try:
            response = await self._client.get(
                self.url,
                headers=self.headers,
                timeout=self.timeout_seconds,
            )
        except (httpx.RequestError, httpx2.RequestError):
            return ProbeResult(False, "PROVIDER_UNAVAILABLE", self.endpoint)
        if response.status_code in {401, 403}:
            return ProbeResult(False, "PROVIDER_AUTHENTICATION_FAILED", self.endpoint)
        if not 200 <= response.status_code < 300:
            return ProbeResult(False, "PROVIDER_UNAVAILABLE", self.endpoint)
        return ProbeResult(True, None, self.endpoint)

    def mark_unavailable(self, code: str = "PROVIDER_UNAVAILABLE") -> None:
        self._cached = (
            time.monotonic() + self.failure_cache_seconds,
            ProbeResult(False, code, self.endpoint),
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


@dataclass(frozen=True)
class ProviderEndpoint:
    provider: AgentProvider
    probe: HttpReadinessProbe


class AvailableProviderModel(Model):
    """Select one ready endpoint before generation and never replay that call."""

    def __init__(self, endpoints: tuple[ProviderEndpoint, ...]) -> None:
        if not endpoints:
            raise ValueError("PROVIDER_ENDPOINT_REQUIRED")
        self.endpoints = endpoints
        self._attempt_ledger: ContextVar[tuple[ProviderAttempt, ...]] = ContextVar(
            f"provider_attempt_ledger_{id(self)}", default=()
        )

    @property
    def attempt_ledger(self) -> tuple[ProviderAttempt, ...]:
        """Attempts for the current async context, without prompts or responses."""
        return self._attempt_ledger.get()

    def _record(
        self,
        *,
        attempt: int,
        endpoint: ProviderEndpoint,
        outcome: str,
        error_code: str | None = None,
        emitted_event: bool = False,
    ) -> None:
        self._attempt_ledger.set(
            self._attempt_ledger.get()
            + (
                ProviderAttempt(
                    attempt=attempt,
                    endpoint=endpoint.probe.endpoint,
                    outcome=outcome,
                    error_code=error_code,
                    emitted_event=emitted_event,
                ),
            )
        )

    @staticmethod
    def _classify(error: BaseException) -> str | None:
        if isinstance(error, APIStatusError):
            if error.status_code in {401, 403}:
                return "PROVIDER_AUTHENTICATION_FAILED"
            if error.status_code in {408, 429} or error.status_code >= 500:
                return "PROVIDER_UNAVAILABLE"
            return None
        if isinstance(
            error,
            (
                APIConnectionError,
                APITimeoutError,
                ModelTimeoutError,
                httpx.RequestError,
                httpx2.RequestError,
            ),
        ):
            return "PROVIDER_UNAVAILABLE"
        return None

    def _failed_attempt(
        self,
        *,
        attempt: int,
        endpoint: ProviderEndpoint,
        error: BaseException,
        emitted_event: bool = False,
    ) -> None:
        circuit_code = self._classify(error)
        if circuit_code is not None:
            endpoint.probe.mark_unavailable(circuit_code)
        self._record(
            attempt=attempt,
            endpoint=endpoint,
            outcome="FAILED",
            error_code=circuit_code or type(error).__name__,
            emitted_event=emitted_event,
        )

    async def _select(self) -> ProviderEndpoint:
        results = await asyncio.gather(
            *(endpoint.probe.check() for endpoint in self.endpoints)
        )
        for endpoint, result in zip(self.endpoints, results, strict=True):
            if result.available:
                return endpoint
        raise ProviderUnavailableError("AGENT_PROVIDER_UNAVAILABLE")

    async def get_response(self, *args: Any, **kwargs: Any):
        self._attempt_ledger.set(())
        endpoint = await self._select()
        try:
            result = await endpoint.provider.model.get_response(*args, **kwargs)
            self._record(attempt=1, endpoint=endpoint, outcome="SUCCEEDED")
            return result
        except (
            APIStatusError, APIConnectionError, APITimeoutError,
            ModelTimeoutError, httpx.RequestError, httpx2.RequestError,
        ) as error:
            self._failed_attempt(attempt=1, endpoint=endpoint, error=error)
            raise

    def stream_response(self, *args: Any, **kwargs: Any):
        async def events():
            self._attempt_ledger.set(())
            endpoint = await self._select()
            yielded = False
            try:
                async for event in endpoint.provider.model.stream_response(
                    *args, **kwargs
                ):
                    yielded = True
                    yield event
                self._record(
                    attempt=1, endpoint=endpoint, outcome="SUCCEEDED",
                    emitted_event=yielded,
                )
            except (
                APIStatusError, APIConnectionError, APITimeoutError,
                ModelTimeoutError, httpx.RequestError, httpx2.RequestError,
            ) as error:
                self._failed_attempt(
                    attempt=1, endpoint=endpoint, error=error,
                    emitted_event=yielded,
                )
                raise

        return events()

    async def _cleanup_on_run_end(self, owner: object) -> None:
        for endpoint in self.endpoints:
            await endpoint.provider.model._cleanup_on_run_end(owner)


class ProviderPool:
    def __init__(self, endpoints: tuple[ProviderEndpoint, ...]) -> None:
        self.endpoints = endpoints
        self.model = AvailableProviderModel(endpoints)

    async def readiness(self) -> dict[str, object]:
        results = await asyncio.gather(
            *(endpoint.probe.check() for endpoint in self.endpoints)
        )
        return {
            "status": "UP" if any(result.available for result in results) else "DOWN",
            "endpoints": [result.public() for result in results],
        }

    async def refresh_readiness(self) -> bool:
        """Force a zero-token probe for a later independent request."""
        results = await asyncio.gather(
            *(endpoint.probe.check(force=True) for endpoint in self.endpoints)
        )
        return any(result.available for result in results)

    async def close(self) -> None:
        errors: list[BaseException] = []
        for endpoint in self.endpoints:
            try:
                await endpoint.provider.close()
            except BaseException as error:
                errors.append(error)
            try:
                await endpoint.probe.close()
            except BaseException as error:
                errors.append(error)
        if errors:
            raise errors[0]


def build_agent_provider_pool(settings: KernelSettings) -> ProviderPool:
    urls = [settings.agent_base_url]
    if settings.agent_standby_base_url is not None:
        urls.append(settings.agent_standby_base_url)
    endpoints = []
    for index, base_url in enumerate(urls):
        configured = replace(settings, agent_base_url=base_url)
        provider = build_agent_provider(configured)
        endpoints.append(
            ProviderEndpoint(
                provider,
                _provider_probe(
                    base_url=base_url,
                    path=settings.agent_health_path,
                    api_key=settings.agent_api_key,
                    endpoint="primary" if index == 0 else "standby",
                    timeout=settings.readiness_timeout_seconds,
                    success_cache=settings.readiness_success_cache_seconds,
                    failure_cache=settings.readiness_failure_cache_seconds,
                    client=provider.http_client,
                ),
            )
        )
    return ProviderPool(tuple(endpoints))


def build_intent_router_provider_pool(
    settings: IntentRouterSettings,
    *,
    timeout_seconds: int = 5,
    success_cache_seconds: int = 15,
    failure_cache_seconds: int = 5,
) -> ProviderPool:
    urls = [settings.base_url]
    if settings.standby_base_url is not None:
        urls.append(settings.standby_base_url)
    endpoints = []
    for index, base_url in enumerate(urls):
        configured = replace(settings, base_url=base_url)
        provider = build_intent_router_provider(configured)
        endpoints.append(
            ProviderEndpoint(
                provider,
                _provider_probe(
                    base_url=base_url,
                    path=settings.health_path,
                    api_key=settings.api_key,
                    endpoint="primary" if index == 0 else "standby",
                    timeout=timeout_seconds,
                    success_cache=success_cache_seconds,
                    failure_cache=failure_cache_seconds,
                    client=provider.http_client,
                ),
            )
        )
    return ProviderPool(tuple(endpoints))


def _provider_probe(
    *,
    base_url: str,
    path: str,
    api_key: str,
    endpoint: str,
    timeout: int,
    success_cache: int,
    failure_cache: int,
    client: httpx.AsyncClient | httpx2.AsyncClient,
) -> HttpReadinessProbe:
    return HttpReadinessProbe(
        base_url=base_url,
        path=path,
        api_key=api_key,
        endpoint=endpoint,
        timeout_seconds=timeout,
        success_cache_seconds=success_cache,
        failure_cache_seconds=failure_cache,
        client=client,
    )
