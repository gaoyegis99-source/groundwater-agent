"""HTTP helpers for authenticated isolated-system acceptance clients."""

from __future__ import annotations

import os
from collections.abc import Mapping


TOKEN_ENVIRONMENT = "PLATFORM_E2E_API_TOKEN"


def public_api_headers(
    headers: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Add the ephemeral acceptance token without overriding caller headers."""

    result = dict(headers or {})
    token = os.environ.get(TOKEN_ENVIRONMENT, "").strip()
    if token:
        result.setdefault("Authorization", f"Bearer {token}")
    return result
