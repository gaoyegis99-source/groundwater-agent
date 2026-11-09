from __future__ import annotations

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from platform_protocol_contracts import (
    ProblemDetailsV11,
    ValidationIssue,
    build_problem_details,
    validation_issues,
)

PROBLEM_MEDIA_TYPE = "application/problem+json"
PROBLEM_RESPONSE = {
    "model": ProblemDetailsV11,
    "content": {PROBLEM_MEDIA_TYPE: {"schema": {"$ref": "#/components/schemas/ProblemDetailsV11"}}},
}


def problem_response(
    code: str,
    status: int,
    detail: str,
    *,
    title: str | None = None,
    trace_id: str | None = None,
    request_id: str | None = None,
    errors: list[ValidationIssue] | None = None,
    product_id: str = "platform",
) -> JSONResponse:
    document = build_problem_details(
        code,
        status,
        detail,
        title=title,
        request_id=request_id,
        trace_id=trace_id,
        errors=errors or (),
        product_id=product_id,
    )
    headers = {
        "X-Trace-ID": document.trace_id,
        "X-Request-ID": document.request_id,
    }
    return JSONResponse(
        status_code=status,
        content=document.model_dump(by_alias=True, exclude_none=True),
        media_type=PROBLEM_MEDIA_TYPE,
        headers=headers,
    )


def validation_problem(
    error: RequestValidationError, *, trace_id: str | None = None,
    request_id: str | None = None,
    product_id: str = "platform",
) -> JSONResponse:
    issues = validation_issues(error.errors())
    return problem_response(
        "VALIDATION_ERROR",
        422,
        "The request does not match the API contract.",
        trace_id=trace_id,
        request_id=request_id,
        errors=issues,
        product_id=product_id,
    )
