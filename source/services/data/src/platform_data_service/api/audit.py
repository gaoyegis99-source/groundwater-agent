"""Content-free access audit records for project operations."""

import json
import logging


_LOGGER = logging.getLogger("platform_data_service.access_audit")
_LOGGER.setLevel(logging.INFO)


def record(request, action: str, **facts: object) -> None:
    actor = request.state.actor
    payload = {
        "action": action,
        "subject": actor["subject"],
        "displayName": actor["display_name"],
        "roles": actor["roles"],
        "client": actor["client"],
        "traceId": request.state.trace_id,
        "requestId": request.state.request_id,
        **facts,
    }
    _LOGGER.info(
        "data_access %s",
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
