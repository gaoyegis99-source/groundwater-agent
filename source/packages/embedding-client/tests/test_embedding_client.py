from __future__ import annotations

import json
import math

import httpx
import pytest

from platform_embedding_client import (
    DIMENSIONS,
    EmbeddingClient,
    TransientUpstreamError,
    UpstreamServiceError,
)


def _response(vectors, indices=None):
    request = httpx.Request("POST", "http://embedding.test/v1/embeddings")
    indices = range(len(vectors)) if indices is None else indices
    payload = {
        "data": [
            {"index": index, "embedding": vector}
            for index, vector in zip(indices, vectors, strict=True)
        ]
    }
    return httpx.Response(
        200,
        request=request,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )


def _client(vectors, indices=None):
    return httpx.Client(
        transport=httpx.MockTransport(lambda _request: _response(vectors, indices))
    )


def test_embedding_response_is_normalized_and_ignores_environment_proxy(
    monkeypatch,
) -> None:
    observed = {}
    vector = [2] + [0] * (DIMENSIONS - 1)

    real_client = httpx.Client

    def client(*_args, **kwargs):
        observed.update(kwargs)
        return real_client(
            transport=httpx.MockTransport(lambda _request: _response([vector]))
        )

    monkeypatch.setattr("platform_embedding_client.client.httpx.Client", client)
    result = EmbeddingClient("http://embedding.test").embed(("query",))[0]
    assert observed["trust_env"] is False
    assert math.isclose(sum(value * value for value in result), 1.0)


@pytest.mark.parametrize(
    "vectors",
    [
        [],
        [[1.0] * (DIMENSIONS - 1)],
        [[0.0] * DIMENSIONS],
        [[True] + [0.0] * (DIMENSIONS - 1)],
        [["1.0"] + [0.0] * (DIMENSIONS - 1)],
        [[float("nan")] + [0.0] * (DIMENSIONS - 1)],
        [[float("inf")] + [0.0] * (DIMENSIONS - 1)],
    ],
)
def test_embedding_response_rejects_invalid_vectors(vectors) -> None:
    with pytest.raises(UpstreamServiceError, match="EMBEDDING_PROVIDER_RESPONSE_INVALID"):
        EmbeddingClient("http://embedding.test", _client(vectors)).embed(("query",))


@pytest.mark.parametrize("index", [False, "0", 0.0])
def test_embedding_response_rejects_non_strict_index(index) -> None:
    vector = [1.0] + [0.0] * (DIMENSIONS - 1)
    with pytest.raises(UpstreamServiceError, match="EMBEDDING_PROVIDER_RESPONSE_INVALID"):
        EmbeddingClient("http://embedding.test", _client([vector], [index])).embed(
            ("query",)
        )


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "EMBEDDING_PROVIDER_AUTHENTICATION_FAILED"),
        (403, "EMBEDDING_PROVIDER_AUTHENTICATION_FAILED"),
        (404, "EMBEDDING_PROVIDER_REQUEST_INVALID"),
        (429, "EMBEDDING_PROVIDER_UNAVAILABLE"),
        (500, "EMBEDDING_PROVIDER_UNAVAILABLE"),
    ],
)
def test_embedding_http_errors_have_fixed_codes_without_upstream_body(
    status: int, code: str
) -> None:
    client = httpx.Client(transport=httpx.MockTransport(
        lambda _request: httpx.Response(status, text="private-provider-body")
    ))
    with pytest.raises(UpstreamServiceError) as caught:
        EmbeddingClient("http://embedding.test", client).embed(("query",))
    assert caught.value.code == str(caught.value) == code
    assert "private-provider-body" not in repr(caught.value)


@pytest.mark.parametrize("status", [408, 429, 500, 503])
def test_retryable_embedding_response_preserves_only_retry_hint(status: int) -> None:
    client = httpx.Client(transport=httpx.MockTransport(
        lambda _request: httpx.Response(
            status, headers={"Retry-After": "30"}, text="private-provider-body"
        )
    ))
    with pytest.raises(TransientUpstreamError) as caught:
        EmbeddingClient("http://embedding.test", client).embed(("query",))
    assert caught.value.operation == "embedding"
    assert caught.value.retry_after == "30"
    assert "private-provider-body" not in repr(caught.value)


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (httpx.ConnectError, "EMBEDDING_PROVIDER_CONNECTION_FAILED"),
        (httpx.ConnectTimeout, "EMBEDDING_PROVIDER_CONNECTION_FAILED"),
        (httpx.LocalProtocolError, "EMBEDDING_PROVIDER_REQUEST_INVALID"),
        (httpx.RemoteProtocolError, "EMBEDDING_PROVIDER_RESPONSE_INVALID"),
        (httpx.DecodingError, "EMBEDDING_PROVIDER_RESPONSE_INVALID"),
        (httpx.ReadTimeout, "EMBEDDING_PROVIDER_UNAVAILABLE"),
    ],
)
def test_embedding_transport_errors_hide_upstream_message(
    failure: type[httpx.RequestError], code: str
) -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        raise failure("private-provider-url?key=secret", request=request)

    client = httpx.Client(transport=httpx.MockTransport(transport))
    with pytest.raises(UpstreamServiceError) as caught:
        EmbeddingClient("http://embedding.test", client).embed(("query",))
    assert caught.value.code == str(caught.value) == code
    assert caught.value.__cause__ is None
    assert "secret" not in repr(caught.value)


@pytest.mark.parametrize(
    "body",
    [b"private-provider-invalid-json", b'{"data": [{"index": 0, "embedding": []}]}'],
)
def test_embedding_invalid_success_is_response_error(body: bytes) -> None:
    client = httpx.Client(transport=httpx.MockTransport(
        lambda _request: httpx.Response(200, content=body)
    ))
    with pytest.raises(UpstreamServiceError) as caught:
        EmbeddingClient("http://embedding.test", client).embed(("query",))
    assert caught.value.code == "EMBEDDING_PROVIDER_RESPONSE_INVALID"
    assert body.decode() not in str(caught.value)


def test_embedding_error_rejects_untrusted_message() -> None:
    with pytest.raises(ValueError, match="Unknown embedding provider error code"):
        UpstreamServiceError("private-provider-url?key=secret")


def test_terminal_embedding_error_cannot_be_marked_transient() -> None:
    with pytest.raises(ValueError, match="not retryable"):
        TransientUpstreamError("EMBEDDING_PROVIDER_RESPONSE_INVALID")


@pytest.mark.parametrize(
    "vector",
    [[1e308] * DIMENSIONS, [5e-324] + [0.0] * (DIMENSIONS - 1)],
)
def test_embedding_response_normalizes_extreme_finite_values(
    vector,
) -> None:
    result = EmbeddingClient("http://embedding.test", _client([vector])).embed(
        ("query",)
    )[0]
    assert all(math.isfinite(value) for value in result)
    assert math.isclose(sum(value * value for value in result), 1.0)


def test_embedding_client_lifecycle_respects_ownership() -> None:
    owned = EmbeddingClient("http://embedding.test")
    owned_http = owned.client
    owned.close()
    assert owned_http.is_closed

    injected_http = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(500))
    )
    injected = EmbeddingClient("http://embedding.test", injected_http)
    injected.close()
    assert not injected_http.is_closed
    injected_http.close()


def test_embedding_readiness_uses_health_endpoint_without_embedding_request() -> None:
    requests = []

    def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, request=request, json={"status": "ok"})

    client = httpx.Client(transport=httpx.MockTransport(transport))
    EmbeddingClient("http://embedding.test", client).ready()
    assert [request.url.path for request in requests] == ["/health"]


def test_embedding_readiness_maps_failure_without_exposing_body() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                503, request=request, text="private provider failure"
            )
        )
    )
    with pytest.raises(UpstreamServiceError) as caught:
        EmbeddingClient("http://embedding.test", client).ready()
    assert caught.value.code == "EMBEDDING_PROVIDER_UNAVAILABLE"
    assert "private provider failure" not in str(caught.value)
