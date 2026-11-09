from __future__ import annotations

import asyncio
from dataclasses import dataclass

from agents.models.interface import Model
import httpx
import httpx2
from openai import APIConnectionError, APIStatusError
import pytest

from platform_kernel.agent_runtime.availability import (
    AvailableProviderModel,
    HttpReadinessProbe,
    ProviderEndpoint,
    ProviderPool,
    ProviderUnavailableError,
)


class StubModel(Model):
    def __init__(self, value: str) -> None:
        self.value = value
        self.calls = 0

    async def get_response(self, *args, **kwargs):
        self.calls += 1
        return self.value

    def stream_response(self, *args, **kwargs):
        async def events():
            if False:
                yield None

        return events()


class FailingModel(StubModel):
    async def get_response(self, *args, **kwargs):
        self.calls += 1
        raise APIConnectionError(
            request=httpx.Request("POST", "http://provider.invalid/generate")
        )


class FailOnceModel(StubModel):
    async def get_response(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise APIConnectionError(
                request=httpx.Request("POST", "http://provider.invalid/generate")
            )
        return self.value


class FailOnceStatusModel(StubModel):
    def __init__(self, value: str, status_code: int) -> None:
        super().__init__(value)
        self.status_code = status_code

    async def get_response(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            request = httpx.Request("POST", "http://provider.invalid/generate")
            raise APIStatusError(
                "provider status", response=httpx.Response(
                    self.status_code, request=request
                ), body=None,
            )
        return self.value


class FailOnceStreamingModel(StubModel):
    def stream_response(self, *args, **kwargs):
        async def events():
            self.calls += 1
            if self.calls == 1:
                raise APIConnectionError(
                    request=httpx.Request(
                        "POST", "http://provider.invalid/generate"
                    )
                )
            yield self.value

        return events()


class FailOnceHttpx2StreamingModel(StubModel):
    def stream_response(self, *args, **kwargs):
        async def events():
            self.calls += 1
            if self.calls == 1:
                raise httpx2.ConnectError(
                    "connection unavailable",
                    request=httpx2.Request(
                        "POST", "http://provider.invalid/generate"
                    ),
                )
            yield self.value

        return events()


class EmitThenFailStreamingModel(StubModel):
    def stream_response(self, *args, **kwargs):
        async def events():
            self.calls += 1
            yield self.value
            raise APIConnectionError(
                request=httpx.Request("POST", "http://provider.invalid/generate")
            )

        return events()


@dataclass
class StubProvider:
    model: Model


def _probe(status: int, endpoint: str, requests: list[httpx.Request]):
    def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, request=request)

    return HttpReadinessProbe(
        base_url=f"http://{endpoint}.test/v1",
        path="/models",
        api_key="secret",
        endpoint=endpoint,
        timeout_seconds=1,
        success_cache_seconds=30,
        failure_cache_seconds=30,
        client=httpx.AsyncClient(transport=httpx.MockTransport(transport)),
    )


def _sequenced_probe(
    statuses: list[int], endpoint: str, requests: list[httpx.Request],
):
    remaining = iter(statuses)

    def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(next(remaining), request=request)

    return HttpReadinessProbe(
        base_url=f"http://{endpoint}.test/v1",
        path="/models",
        api_key="secret",
        endpoint=endpoint,
        timeout_seconds=1,
        success_cache_seconds=30,
        failure_cache_seconds=30,
        client=httpx.AsyncClient(transport=httpx.MockTransport(transport)),
    )


@pytest.mark.parametrize("transport_error", [httpx2.ConnectTimeout, httpx2.ConnectError])
def test_sdk_transport_failure_reports_provider_down_without_generation(transport_error):
    def timeout(request: httpx2.Request) -> httpx2.Response:
        raise transport_error("connection unavailable", request=request)

    async def scenario():
        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(timeout)
        ) as client:
            probe = HttpReadinessProbe(
                base_url="http://provider.test/v1",
                path="/models",
                api_key="secret",
                endpoint="primary",
                timeout_seconds=1,
                success_cache_seconds=30,
                failure_cache_seconds=30,
                client=client,
            )
            model = StubModel("unused")
            pool = ProviderPool((ProviderEndpoint(StubProvider(model), probe),))
            assert await pool.readiness() == {
                "status": "DOWN",
                "endpoints": [{
                    "status": "DOWN",
                    "endpoint": "primary",
                    "error": "PROVIDER_UNAVAILABLE",
                }],
            }
            with pytest.raises(ProviderUnavailableError, match="AGENT_PROVIDER_UNAVAILABLE"):
                await pool.model.get_response()
            assert model.calls == 0

    asyncio.run(scenario())


def test_provider_selection_uses_zero_token_probe_and_standby_before_call():
    requests: list[httpx.Request] = []
    primary = StubModel("primary")
    standby = StubModel("standby")
    model = AvailableProviderModel(
        (
            ProviderEndpoint(StubProvider(primary), _probe(503, "primary", requests)),
            ProviderEndpoint(StubProvider(standby), _probe(200, "standby", requests)),
        )
    )

    async def scenario():
        assert await model.get_response() == "standby"
        assert await model.get_response() == "standby"

    asyncio.run(scenario())
    assert primary.calls == 0
    assert standby.calls == 2
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/v1/models"),
        ("GET", "/v1/models"),
    ]


def test_provider_selection_fails_before_generation_when_all_endpoints_down():
    requests: list[httpx.Request] = []
    delegate = StubModel("unused")
    model = AvailableProviderModel(
        (ProviderEndpoint(StubProvider(delegate), _probe(503, "primary", requests)),)
    )
    async def scenario():
        with pytest.raises(ProviderUnavailableError, match="AGENT_PROVIDER_UNAVAILABLE"):
            await model.get_response()

    asyncio.run(scenario())
    assert delegate.calls == 0


def test_failed_generation_opens_endpoint_circuit_for_the_next_request():
    requests: list[httpx.Request] = []
    primary_model = FailingModel("unused")
    primary = StubProvider(primary_model)
    standby = StubModel("standby")
    model = AvailableProviderModel(
        (
            ProviderEndpoint(primary, _probe(200, "primary", requests)),
            ProviderEndpoint(StubProvider(standby), _probe(200, "standby", requests)),
        )
    )

    async def scenario():
        with pytest.raises(APIConnectionError):
            await model.get_response()
        assert await model.get_response() == "standby"

    asyncio.run(scenario())
    assert primary_model.calls == 1
    assert standby.calls == 1


def test_failed_generation_is_not_replayed_after_unknown_transport_outcome():
    requests: list[httpx.Request] = []
    delegate = FailOnceModel("recovered")
    model = AvailableProviderModel((
        ProviderEndpoint(
            StubProvider(delegate), _probe(200, "primary", requests),
        ),
    ))

    async def scenario():
        with pytest.raises(APIConnectionError):
            await model.get_response()
        return model.attempt_ledger

    ledger = asyncio.run(scenario())
    assert delegate.calls == 1
    assert len(requests) == 1
    assert [
        (attempt.attempt, attempt.endpoint, attempt.outcome, attempt.error_code)
        for attempt in ledger
    ] == [
        (1, "primary", "FAILED", "PROVIDER_UNAVAILABLE"),
    ]


@pytest.mark.parametrize("status_code", [401, 403, 429, 503])
def test_status_failures_are_never_replayed(status_code):
    requests: list[httpx.Request] = []
    delegate = FailOnceStatusModel("recovered", status_code)
    model = AvailableProviderModel((
        ProviderEndpoint(
            StubProvider(delegate), _probe(200, "primary", requests),
        ),
    ))

    async def scenario():
        with pytest.raises(APIStatusError):
            await model.get_response()
        return model.attempt_ledger

    ledger = asyncio.run(scenario())
    assert delegate.calls == 1
    assert len(ledger) == 1
    assert ledger[0].error_code == (
        "PROVIDER_AUTHENTICATION_FAILED"
        if status_code in {401, 403}
        else "PROVIDER_UNAVAILABLE"
    )


def test_failed_stream_is_not_replayed_before_the_first_event():
    requests: list[httpx.Request] = []
    delegate = FailOnceStreamingModel("recovered-event")
    model = AvailableProviderModel((
        ProviderEndpoint(
            StubProvider(delegate), _probe(200, "primary", requests),
        ),
    ))

    async def collect():
        with pytest.raises(APIConnectionError):
            return [event async for event in model.stream_response()]

    assert asyncio.run(collect()) is None
    assert delegate.calls == 1
    assert len(requests) == 1


def test_failed_stream_does_not_force_a_second_readiness_probe():
    requests: list[httpx.Request] = []
    delegate = FailOnceStreamingModel("recovered-event")
    model = AvailableProviderModel((
        ProviderEndpoint(
            StubProvider(delegate),
            _sequenced_probe([200, 503], "primary", requests),
        ),
    ))

    async def collect():
        with pytest.raises(APIConnectionError):
            return [event async for event in model.stream_response()]

    assert asyncio.run(collect()) is None
    assert delegate.calls == 1
    assert len(requests) == 1


def test_raw_httpx2_stream_failure_is_not_replayed_before_first_event():
    requests: list[httpx.Request] = []
    delegate = FailOnceHttpx2StreamingModel("recovered-event")
    model = AvailableProviderModel((
        ProviderEndpoint(
            StubProvider(delegate), _probe(200, "primary", requests),
        ),
    ))

    async def collect():
        with pytest.raises(httpx2.ConnectError):
            return [event async for event in model.stream_response()]

    assert asyncio.run(collect()) is None
    assert delegate.calls == 1


def test_failed_stream_never_retries_after_first_event():
    requests: list[httpx.Request] = []
    delegate = EmitThenFailStreamingModel("first-event")
    model = AvailableProviderModel((
        ProviderEndpoint(
            StubProvider(delegate), _probe(200, "primary", requests),
        ),
    ))

    async def collect():
        received = []
        with pytest.raises(APIConnectionError):
            async for event in model.stream_response():
                received.append(event)
        return received, model.attempt_ledger

    received, ledger = asyncio.run(collect())
    assert received == ["first-event"]
    assert delegate.calls == 1
    assert len(ledger) == 1
    assert ledger[0].emitted_event is True
    assert ledger[0].outcome == "FAILED"
