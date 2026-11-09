from __future__ import annotations

import math

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    FiniteFloat,
    StrictInt,
    ValidationError,
    field_validator,
)

from .errors import TransientUpstreamError, UpstreamServiceError


MODEL = "qwen3-embedding-4b-q4"
DIMENSIONS = 1024


class EmbeddingItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    index: StrictInt
    embedding: list[FiniteFloat]

    @field_validator("embedding", mode="before")
    @classmethod
    def require_numeric_values(cls, value: object) -> object:
        if not isinstance(value, list) or any(
            isinstance(item, bool) or not isinstance(item, (int, float))
            for item in value
        ):
            raise ValueError("numeric embedding values required")
        return value


class EmbeddingResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    data: list[EmbeddingItem]


class EmbeddingClient:
    def __init__(
        self,
        base_url: str,
        client: httpx.Client | None = None,
        *,
        health_path: str = "/health",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        if not health_path.startswith("/") or health_path.startswith("//"):
            raise ValueError("EMBEDDING_HEALTH_PATH_INVALID")
        self.health_path = health_path
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=30, trust_env=False)

    def ready(self) -> None:
        try:
            response = self.client.get(f"{self.base_url}{self.health_path}")
        except httpx.RequestError:
            raise UpstreamServiceError("EMBEDDING_PROVIDER_UNAVAILABLE") from None
        if response.status_code in (401, 403):
            raise UpstreamServiceError("EMBEDDING_PROVIDER_AUTHENTICATION_FAILED")
        if not 200 <= response.status_code < 300:
            raise UpstreamServiceError("EMBEDDING_PROVIDER_UNAVAILABLE")

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        try:
            response = self.client.post(
                f"{self.base_url}/v1/embeddings",
                json={"model": MODEL, "input": list(texts), "dimensions": DIMENSIONS},
            )
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise TransientUpstreamError("EMBEDDING_PROVIDER_CONNECTION_FAILED") from None
        except (httpx.UnsupportedProtocol, httpx.InvalidURL):
            raise UpstreamServiceError("EMBEDDING_PROVIDER_CONNECTION_FAILED") from None
        except (httpx.LocalProtocolError, httpx.TooManyRedirects):
            raise UpstreamServiceError("EMBEDDING_PROVIDER_REQUEST_INVALID") from None
        except (httpx.RemoteProtocolError, httpx.DecodingError):
            raise UpstreamServiceError("EMBEDDING_PROVIDER_RESPONSE_INVALID") from None
        except httpx.RequestError:
            raise TransientUpstreamError("EMBEDDING_PROVIDER_UNAVAILABLE") from None
        if response.status_code in (401, 403):
            raise UpstreamServiceError("EMBEDDING_PROVIDER_AUTHENTICATION_FAILED")
        if response.status_code in (408, 429) or response.status_code >= 500:
            raise TransientUpstreamError(
                "EMBEDDING_PROVIDER_UNAVAILABLE",
                retry_after=response.headers.get("retry-after"),
            )
        if not 200 <= response.status_code < 300:
            raise UpstreamServiceError("EMBEDDING_PROVIDER_REQUEST_INVALID")
        try:
            payload = EmbeddingResponse.model_validate(response.json())
        except (ValueError, ValidationError):
            raise UpstreamServiceError("EMBEDDING_PROVIDER_RESPONSE_INVALID") from None
        if [item.index for item in payload.data] != list(range(len(texts))):
            raise UpstreamServiceError("EMBEDDING_PROVIDER_RESPONSE_INVALID")
        vectors: list[tuple[float, ...]] = []
        for item in payload.data:
            if len(item.embedding) != DIMENSIONS:
                raise UpstreamServiceError("EMBEDDING_PROVIDER_RESPONSE_INVALID")
            scale = max(abs(value) for value in item.embedding)
            if scale == 0:
                raise UpstreamServiceError("EMBEDDING_PROVIDER_RESPONSE_INVALID")
            scaled = tuple(value / scale for value in item.embedding)
            norm = math.sqrt(sum(value * value for value in scaled))
            vectors.append(tuple(value / norm for value in scaled))
        return tuple(vectors)

    def close(self) -> None:
        if self._owns_client:
            self.client.close()
