from __future__ import annotations

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from platform_protocol_contracts import (
    ProblemDetailsV11,
    ValidationIssue,
    build_problem_details,
    validation_issues,
)
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_MEDIA_TYPE = "application/problem+json"
PROBLEM_RESPONSE = {
    "model": ProblemDetailsV11,
    "content": {PROBLEM_MEDIA_TYPE: {"schema": {"$ref": "#/components/schemas/ProblemDetailsV11"}}},
}


def problem_response(
    problem_type: str, title: str, status: int, detail: str, code: str, *,
    errors: list[ValidationIssue] | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
    product_id: str = "platform",
) -> JSONResponse:
    document = build_problem_details(
        code,
        status,
        detail,
        title=title,
        problem_type=problem_type,
        request_id=request_id,
        trace_id=trace_id,
        errors=errors or (),
        product_id=product_id,
    )
    return JSONResponse(
        status_code=status,
        content=document.model_dump(by_alias=True, exclude_none=True),
        media_type=PROBLEM_MEDIA_TYPE,
        headers={
            "X-Request-ID": document.request_id,
            "X-Trace-ID": document.trace_id,
        },
    )


def _identities(request: Request) -> dict[str, str | None]:
    return {
        "request_id": getattr(request.state, "request_id", None),
        "trace_id": getattr(request.state, "trace_id", None),
    }


def _product(request: Request) -> dict[str, str]:
    return {"product_id": request.app.state.product_id}


async def request_validation_problem(
    request: Request, error: RequestValidationError
) -> JSONResponse:
    issues = validation_issues(error.errors())
    return problem_response(
        "request-validation-failed", "Request validation failed", 422,
        "The request does not match the API contract.",
        "VALIDATION_ERROR", errors=issues, **_identities(request),
        **_product(request),
    )


async def http_problem(request: Request, error: StarletteHTTPException) -> JSONResponse:
    if error.status_code == 404:
        return problem_response(
            "resource-not-found", "Resource not found", 404,
            "The requested resource was not found.", "ROUTE_NOT_FOUND",
            **_identities(request),
            **_product(request),
        )
    if error.status_code == 405:
        return problem_response(
            "method-not-allowed", "Method not allowed", 405,
            "The HTTP method is not allowed for this resource.", "METHOD_NOT_ALLOWED",
            **_identities(request),
            **_product(request),
        )
    raise error
