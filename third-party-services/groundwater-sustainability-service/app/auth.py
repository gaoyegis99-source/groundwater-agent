from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .errors import AppError


_BEARER = HTTPBearer(auto_error=False)


def bearer_dependency(api_key: str):
    """Return a strict dependency without ever placing the key in app state output."""

    def require_bearer(
        credentials: Annotated[
            HTTPAuthorizationCredentials | None, Security(_BEARER)
        ],
    ) -> None:
        supplied = credentials.credentials if credentials is not None else ""
        if not hmac.compare_digest(supplied.encode(), api_key.encode()):
            raise AppError("UNAUTHORIZED", "需要有效的 Bearer API 密钥。", status_code=401)

    return require_bearer
