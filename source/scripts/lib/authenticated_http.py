"""Project header for local Resource uploads."""

from __future__ import annotations


def gateway_headers(project_id: str | None = None) -> dict[str, str]:
    return {} if project_id is None else {"X-Project-ID": project_id}
