from __future__ import annotations

import asyncio
from dataclasses import dataclass
import re
import time

import httpx

from .agent_runtime.availability import ProviderPool
from .settings import KernelSettings


@dataclass(frozen=True)
class ReadinessReport:
    ready: bool
    payload: dict[str, object]


class ServiceReadinessProbe:
    def __init__(
        self,
        *,
        name: str,
        url: str,
        project_id: str,
        client: httpx.AsyncClient,
        timeout_seconds: int,
        success_cache_seconds: int,
        failure_cache_seconds: int,
    ) -> None:
        self.name = name
        self.url = url
        self.project_id = project_id
        self.client = client
        self.timeout_seconds = timeout_seconds
        self.success_cache_seconds = success_cache_seconds
        self.failure_cache_seconds = failure_cache_seconds
        self._cached: tuple[float, dict[str, object]] | None = None
        self._lock = asyncio.Lock()

    async def check(self) -> dict[str, object]:
        now = time.monotonic()
        if self._cached is not None and self._cached[0] > now:
            return self._cached[1]
        async with self._lock:
            now = time.monotonic()
            if self._cached is not None and self._cached[0] > now:
                return self._cached[1]
            result = await self._request()
            ttl = (
                self.success_cache_seconds
                if result["status"] == "UP"
                else self.failure_cache_seconds
            )
            self._cached = (time.monotonic() + ttl, result)
            return result

    async def _request(self) -> dict[str, object]:
        try:
            response = await self.client.get(
                self.url,
                headers={"X-Project-ID": self.project_id},
                timeout=self.timeout_seconds,
            )
        except httpx.RequestError:
            return {"status": "DOWN", "error": "SERVICE_UNAVAILABLE"}
        payload: object = None
        try:
            payload = response.json()
        except ValueError:
            if 200 <= response.status_code < 300:
                return {"status": "DOWN", "error": "SERVICE_RESPONSE_INVALID"}
        components = _safe_components(payload)
        definition_set_digest = _safe_definition_set_digest(payload)
        if not 200 <= response.status_code < 300:
            result: dict[str, object] = {
                "status": "DOWN",
                "error": "SERVICE_NOT_READY",
            }
            if components is not None:
                result["components"] = components
            if definition_set_digest is not None:
                result["definition_set_digest"] = definition_set_digest
            return result

        if not isinstance(payload, dict):
            return {"status": "DOWN", "error": "SERVICE_RESPONSE_INVALID"}
        expected_status = {
            "data": "ready",
            "knowledge-next": "ready",
            "executor": "UP",
        }.get(self.name)
        status = payload.get("status")
        if not isinstance(status, str):
            return {"status": "DOWN", "error": "SERVICE_RESPONSE_INVALID"}
        if expected_status is None:
            if status not in {"UP", "ready"}:
                error = (
                    "SERVICE_NOT_READY"
                    if status in {"DOWN", "down"}
                    else "SERVICE_RESPONSE_INVALID"
                )
                return {"status": "DOWN", "error": error}
        elif status != expected_status:
            error = (
                "SERVICE_NOT_READY"
                if status in {"DOWN", "down"}
                else "SERVICE_RESPONSE_INVALID"
            )
            result = {"status": "DOWN", "error": error}
            if components is not None:
                result["components"] = components
            return result

        required_components = {
            "data": frozenset({"database", "objectStore"}),
            "knowledge-next": frozenset({"database", "embedding"}),
        }.get(self.name)
        if required_components is not None:
            if components is None or not required_components.issubset(components):
                return {"status": "DOWN", "error": "SERVICE_RESPONSE_INVALID"}
            if any(components[name] != "UP" for name in required_components):
                return {
                    "status": "DOWN",
                    "error": "SERVICE_NOT_READY",
                    "components": components,
                }
        elif "components" in payload and components is None:
            return {"status": "DOWN", "error": "SERVICE_RESPONSE_INVALID"}

        if self.name == "executor" and definition_set_digest is None:
            return {"status": "DOWN", "error": "SERVICE_RESPONSE_INVALID"}

        result = {"status": "UP"}
        if components is not None:
            result["components"] = components
        if definition_set_digest is not None:
            result["definition_set_digest"] = definition_set_digest
        return result


class PlatformReadiness:
    def __init__(
        self,
        settings: KernelSettings,
        *,
        agent_providers: ProviderPool | None,
        router_providers: ProviderPool | None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(trust_env=False)
        self.agent_providers = agent_providers
        self.router_providers = router_providers
        self.expected_executor_digest = settings.definition_set_digest
        bindings = settings.services_by_name
        self.service_names = tuple(sorted(bindings))
        self.probes = {
            name: ServiceReadinessProbe(
                name=name,
                url=bindings[name].internal_base_url + "/internal/health/ready",
                project_id=settings.project_id,
                client=self._client,
                timeout_seconds=settings.readiness_timeout_seconds,
                success_cache_seconds=settings.readiness_success_cache_seconds,
                failure_cache_seconds=settings.readiness_failure_cache_seconds,
            )
            for name in self.service_names
        }

    async def check(self, *, kernel_ready: bool) -> ReadinessReport:
        results = await asyncio.gather(
            *(self._service(name) for name in self.service_names),
            self._provider(self.agent_providers),
            self._provider(self.router_providers),
        )
        service_results = dict(zip(self.service_names, results[:-2], strict=True))
        agent, router = results[-2:]
        executor = service_results.get("executor")
        if (
            isinstance(executor, dict)
            and executor.get("status") == "UP"
            and executor.get("definition_set_digest") != self.expected_executor_digest
        ):
            service_results["executor"] = {
                **executor,
                "status": "DOWN",
                "error": "EXECUTOR_DEFINITION_SET_MISMATCH",
                "expected_definition_set_digest": self.expected_executor_digest,
            }
        components: dict[str, object] = {
            "kernel": {"status": "UP" if kernel_ready else "DOWN"},
            **service_results,
            "agent-provider": agent,
            "intent-router-provider": router,
        }
        knowledge = components.get("knowledge-next")
        if isinstance(knowledge, dict):
            nested = knowledge.get("components")
            if isinstance(nested, dict) and "embedding" in nested:
                components["embedding"] = {"status": nested["embedding"]}
            elif knowledge.get("status") == "DOWN":
                components["embedding"] = {"status": "DOWN"}
        ready = all(
            isinstance(value, dict) and value.get("status") == "UP"
            for value in components.values()
        )
        return ReadinessReport(
            ready,
            {"status": "UP" if ready else "DOWN", "components": components},
        )

    async def _service(self, name: str) -> dict[str, object]:
        probe = self.probes.get(name)
        if probe is None:
            return {"status": "DOWN", "error": "SERVICE_NOT_CONFIGURED"}
        return await probe.check()

    @staticmethod
    async def _provider(pool: ProviderPool | None) -> dict[str, object]:
        if pool is None:
            return {"status": "UP", "source": "INJECTED_MODEL"}
        return await pool.readiness()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _safe_components(payload: object) -> dict[str, str] | None:
    if not isinstance(payload, dict) or not isinstance(payload.get("components"), dict):
        return None
    components = payload["components"]
    if not components or any(
        not isinstance(name, str) or status not in {"UP", "DOWN"}
        for name, status in components.items()
    ):
        return None
    return dict(components)


def _safe_definition_set_digest(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get("definitionSetDigest")
    if not isinstance(value, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        return None
    return value
