from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from uuid import uuid4

from pydantic import Field

from .http import CamelModel


class ValidationIssue(CamelModel):
    pointer: str
    message: str


class ProblemDetailsV11(CamelModel):
    type: str
    title: str
    status: int = Field(ge=400, le=599)
    detail: str
    code: str
    request_id: str
    trace_id: str
    errors: list[ValidationIssue]


def build_problem_details(
    code: str,
    status: int,
    detail: str,
    *,
    title: str | None = None,
    problem_type: str | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
    errors: Sequence[ValidationIssue] = (),
    product_id: str = "platform",
) -> ProblemDetailsV11:
    """Build the shared wire document without depending on an HTTP framework."""
    if re.fullmatch(r"[a-z][a-z0-9_.-]*", product_id) is None:
        raise ValueError("PROBLEM_PRODUCT_ID_INVALID")
    return ProblemDetailsV11.model_validate(
        {
            "type": f"urn:{product_id}:problem:"
            + (problem_type or code.lower().replace("_", "-")),
            "title": title or code.replace("_", " ").title(),
            "status": status,
            "detail": detail,
            "code": code,
            "requestId": request_id or uuid4().hex,
            "traceId": trace_id or uuid4().hex,
            "errors": list(errors),
        }
    )


def validation_issues(
    errors: Sequence[Mapping[str, object]],
) -> list[ValidationIssue]:
    """Normalize framework validation errors into the public v11 contract."""
    return [
        ValidationIssue.model_validate(
            {
                "pointer": json_pointer(item.get("loc", ())),
                "message": str(item.get("msg", "Validation failed")),
            }
        )
        for item in errors
    ]


def json_pointer(location: Sequence[object]) -> str:
    parts = list(location)
    if parts and parts[0] in {"body", "path", "query", "header"}:
        parts.pop(0)
    if not parts:
        return ""
    escaped = [str(part).replace("~", "~0").replace("/", "~1") for part in parts]
    return "/" + "/".join(escaped)


def enforce_problem_media_type(document: dict[str, object]) -> dict[str, object]:
    """Keep Problem documentation stable across supported Python runtimes."""
    paths = document.get("paths")
    if not isinstance(paths, dict):
        return document
    for path_item in paths.values():
        if not isinstance(path_item, dict):
            continue
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            responses = operation.get("responses")
            if not isinstance(responses, dict):
                continue
            for status, response in responses.items():
                if not isinstance(response, dict):
                    continue
                content = response.get("content")
                if isinstance(content, dict) and "application/problem+json" in content:
                    content.pop("application/json", None)
                    # HTTPStatus renamed these phrases in Python 3.13. Preserve
                    # the published contract when serving it from Python 3.12.
                    phrases = {
                        "413": ("Request Entity Too Large", "Content Too Large"),
                        "422": ("Unprocessable Entity", "Unprocessable Content"),
                    }
                    if status in phrases:
                        previous, current = phrases[status]
                        if response.get("description") == previous:
                            response["description"] = current
    return document
