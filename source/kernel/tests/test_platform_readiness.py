from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx

from platform_kernel.readiness import PlatformReadiness


def test_platform_readiness_aggregates_services_embedding_and_injected_models(
    kernel_settings,
) -> None:
    settings = kernel_settings("summary")

    def transport(request: httpx.Request) -> httpx.Response:
        components = {
            "data-service": {"database": "UP", "objectStore": "UP"},
            "knowledge-next-service": {"database": "UP", "embedding": "UP"},
        }.get(request.url.host)
        body = {
            "status": "UP" if request.url.host == "executor-service" else "ready"
        }
        if request.url.host == "executor-service":
            body["definitionSetDigest"] = settings.definition_set_digest
        if components is not None:
            body["components"] = components
        return httpx.Response(200, request=request, json=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    readiness = PlatformReadiness(
        settings,
        agent_providers=None,
        router_providers=None,
        client=client,
    )
    report = asyncio.run(readiness.check(kernel_ready=True))
    asyncio.run(client.aclose())
    assert report.ready
    assert report.payload["components"]["embedding"] == {"status": "UP"}


def test_platform_readiness_returns_down_for_unready_worker_control(
    kernel_settings,
) -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        if request.url.host == "executor-service":
            return httpx.Response(503, request=request, json={"status": "down"})
        components = (
            {"database": "UP", "embedding": "UP"}
            if request.url.host == "knowledge-next-service"
            else {"database": "UP", "objectStore": "UP"}
        )
        return httpx.Response(
            200, request=request, json={"status": "ready", "components": components}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    readiness = PlatformReadiness(
        kernel_settings("summary"),
        agent_providers=None,
        router_providers=None,
        client=client,
    )
    report = asyncio.run(readiness.check(kernel_ready=True))
    asyncio.run(client.aclose())
    assert not report.ready
    executor = report.payload["components"]["executor"]
    assert executor["status"] == "DOWN"
    assert executor["error"] == "SERVICE_NOT_READY"


def test_platform_readiness_rejects_executor_definition_mismatch(
    kernel_settings,
) -> None:
    settings = kernel_settings("summary")

    def transport(request: httpx.Request) -> httpx.Response:
        body: dict[str, object] = {
            "status": "UP" if request.url.host == "executor-service" else "ready"
        }
        if request.url.host == "executor-service":
            body["definitionSetDigest"] = "sha256:" + "0" * 64
        elif request.url.host == "knowledge-next-service":
            body["components"] = {"database": "UP", "embedding": "UP"}
        else:
            body["components"] = {"database": "UP", "objectStore": "UP"}
        return httpx.Response(200, request=request, json=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    readiness = PlatformReadiness(
        settings,
        agent_providers=None,
        router_providers=None,
        client=client,
    )
    report = asyncio.run(readiness.check(kernel_ready=True))
    asyncio.run(client.aclose())
    assert not report.ready
    executor = report.payload["components"]["executor"]
    assert executor["status"] == "DOWN"
    assert executor["error"] == "EXECUTOR_DEFINITION_SET_MISMATCH"
    assert executor["definition_set_digest"] == "sha256:" + "0" * 64
    assert executor["expected_definition_set_digest"] == settings.definition_set_digest


def test_platform_readiness_only_requires_assembled_services(kernel_settings) -> None:
    settings = kernel_settings("summary")
    settings = replace(
        settings,
        services=tuple(item for item in settings.services if item.name == "data"),
    )
    requested: list[str] = []

    def transport(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.host or "")
        return httpx.Response(
            200,
            request=request,
            json={
                "status": "ready",
                "components": {"database": "UP", "objectStore": "UP"},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    readiness = PlatformReadiness(
        settings,
        agent_providers=None,
        router_providers=None,
        client=client,
    )
    report = asyncio.run(readiness.check(kernel_ready=True))
    asyncio.run(client.aclose())

    assert report.ready
    assert requested == ["data-service"]
    assert set(report.payload["components"]) == {
        "kernel",
        "data",
        "agent-provider",
        "intent-router-provider",
    }


def test_platform_readiness_rejects_non_json_success_from_data(kernel_settings) -> None:
    base = kernel_settings("summary")
    settings = replace(
        base,
        services=(
            next(item for item in base.services if item.name == "data"),
        ),
    )

    def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, text="<html>upstream error</html>")

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    readiness = PlatformReadiness(
        settings,
        agent_providers=None,
        router_providers=None,
        client=client,
    )
    report = asyncio.run(readiness.check(kernel_ready=True))
    asyncio.run(client.aclose())

    assert not report.ready
    assert report.payload["components"]["data"] == {
        "status": "DOWN",
        "error": "SERVICE_RESPONSE_INVALID",
    }


def test_platform_readiness_rejects_false_success_status_from_knowledge(
    kernel_settings,
) -> None:
    base = kernel_settings("summary")
    settings = replace(
        base,
        services=(
            next(
                item
                for item in base.services
                if item.name == "knowledge-next"
            ),
        ),
    )

    def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "status": "down",
                "components": {"database": "UP", "embedding": "DOWN"},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    readiness = PlatformReadiness(
        settings,
        agent_providers=None,
        router_providers=None,
        client=client,
    )
    report = asyncio.run(readiness.check(kernel_ready=True))
    asyncio.run(client.aclose())

    assert not report.ready
    knowledge = report.payload["components"]["knowledge-next"]
    assert knowledge["status"] == "DOWN"
    assert knowledge["error"] == "SERVICE_NOT_READY"
    assert report.payload["components"]["embedding"] == {"status": "DOWN"}


def test_platform_readiness_rejects_missing_required_component(kernel_settings) -> None:
    base = kernel_settings("summary")
    settings = replace(
        base,
        services=(
            next(item for item in base.services if item.name == "data"),
        ),
    )

    def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={"status": "ready", "components": {"database": "UP"}},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    readiness = PlatformReadiness(
        settings,
        agent_providers=None,
        router_providers=None,
        client=client,
    )
    report = asyncio.run(readiness.check(kernel_ready=True))
    asyncio.run(client.aclose())

    assert not report.ready
    assert report.payload["components"]["data"]["error"] == (
        "SERVICE_RESPONSE_INVALID"
    )
