class UpstreamServiceError(RuntimeError):
    CODES = frozenset({
        "EMBEDDING_PROVIDER_AUTHENTICATION_FAILED",
        "EMBEDDING_PROVIDER_CONNECTION_FAILED",
        "EMBEDDING_PROVIDER_REQUEST_INVALID",
        "EMBEDDING_PROVIDER_RESPONSE_INVALID",
        "EMBEDDING_PROVIDER_UNAVAILABLE",
    })

    def __init__(self, code: str) -> None:
        if code not in self.CODES:
            raise ValueError("Unknown embedding provider error code")
        self.code = code
        super().__init__(code)


class TransientUpstreamError(UpstreamServiceError):
    TRANSIENT_CODES = frozenset({
        "EMBEDDING_PROVIDER_CONNECTION_FAILED",
        "EMBEDDING_PROVIDER_UNAVAILABLE",
    })

    def __init__(self, code: str, *, retry_after: str | None = None) -> None:
        if code not in self.TRANSIENT_CODES:
            raise ValueError("Embedding provider error is not retryable")
        super().__init__(code)
        self.operation = "embedding"
        self.retry_after = retry_after
