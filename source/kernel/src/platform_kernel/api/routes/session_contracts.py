"""Shared wire and OpenAPI contracts for the Session route family."""

from __future__ import annotations

from ...interaction.api_projection import session_response
from ..schemas import PROBLEM_RESPONSE
from ..wire import to_wire


PROBLEM_RESPONSES = {status: PROBLEM_RESPONSE for status in (401, 403, 404, 409, 422, 429, 500)}
SSE_RESPONSE = {
    200: {
        "content": {
            "text/event-stream": {
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "event": {"type": "string"},
                        "id": {"type": ["integer", "null"]},
                        "data": {"type": "object"},
                    },
                    "required": ["event", "data"],
                }
            }
        }
    }
}


def session_wire(view: object) -> dict[str, object]:
    return to_wire(session_response(view))


__all__ = ("PROBLEM_RESPONSES", "SSE_RESPONSE", "session_wire")
