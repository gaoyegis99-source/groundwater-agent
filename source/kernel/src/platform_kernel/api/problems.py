from __future__ import annotations

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from platform_protocol_contracts import (
    ValidationIssue,
    build_problem_details,
    validation_issues,
)

from ..errors import (
    AuthenticationRequiredError,
    AuthorizationDeniedError,
    CapacityLimitError,
    ConfirmationConflictError,
    KernelUnavailableError,
    ResourceNotFoundError,
    AgentRunError,
    UpstreamServiceError,
)
PROBLEM_MEDIA_TYPE = "application/problem+json"


def problem_response(
    problem_type: str,
    title: str,
    status: int,
    detail: str,
    code: str,
    *,
    errors: list[ValidationIssue] | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
    product_id: str = "platform",
) -> JSONResponse:
    problem = build_problem_details(
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
        content=problem.model_dump(by_alias=True, exclude_none=True),
        media_type=PROBLEM_MEDIA_TYPE,
        headers={
            "X-Request-ID": problem.request_id,
            "X-Trace-ID": problem.trace_id,
        },
    )


def _identities(request: Request) -> dict[str, str | None]:
    return {
        "request_id": getattr(request.state, "request_id", None),
        "trace_id": getattr(request.state, "trace_id", None),
    }


def _product(request: Request) -> dict[str, str]:
    return {"product_id": request.app.state.platform.settings.product_id}


async def request_validation_problem(
    request: Request, error: RequestValidationError
) -> JSONResponse:
    issues = validation_issues(error.errors())
    return problem_response(
        "request-validation-failed",
        "Request validation failed",
        422,
        "The request does not match the API contract.",
        "VALIDATION_ERROR",
        errors=issues,
        **_identities(request),
        **_product(request),
    )


async def http_problem(request: Request, error: StarletteHTTPException) -> JSONResponse:
    if error.status_code == 404:
        return problem_response(
            "resource-not-found",
            "Resource not found",
            404,
            "The requested resource was not found.",
            "ROUTE_NOT_FOUND",
            **_identities(request),
            **_product(request),
        )
    if error.status_code == 405:
        return problem_response(
            "method-not-allowed",
            "Method not allowed",
            405,
            "The HTTP method is not allowed for this resource.",
            "METHOD_NOT_ALLOWED",
            **_identities(request),
            **_product(request),
        )
    raise error


async def resource_not_found_problem(
    request: Request, error: ResourceNotFoundError
) -> JSONResponse:
    return problem_response(
        "resource-not-found",
        "Resource not found",
        404,
        "The requested resource was not found.",
        str(error),
        **_identities(request),
        **_product(request),
    )


async def confirmation_problem(
    request: Request, error: ConfirmationConflictError
) -> JSONResponse:
    return problem_response(
        "confirmation-conflict",
        "Confirmation conflict",
        409,
        "The confirmation is not current for this session.",
        str(error),
        **_identities(request),
        **_product(request),
    )


async def upstream_problem(
    request: Request, error: UpstreamServiceError
) -> JSONResponse:
    return problem_response(
        "upstream-service-unavailable",
        "Upstream service unavailable",
        503,
        "A required upstream service is unavailable.",
        str(error),
        **_identities(request),
        **_product(request),
    )


async def agent_run_problem(request: Request, error: AgentRunError) -> JSONResponse:
    return problem_response(
        "agent-run-failed",
        "Agent run failed",
        500 if str(error) == "AGENT_SDK_CONFIGURATION_INVALID" else 422,
        "The agent run could not be completed.",
        str(error),
        **_identities(request),
        **_product(request),
    )


async def kernel_unavailable_problem(
    request: Request, error: KernelUnavailableError
) -> JSONResponse:
    return problem_response(
        "kernel-unavailable",
        "Kernel unavailable",
        503,
        "The Kernel dispatcher or local store is unavailable.",
        str(error),
        **_identities(request),
        **_product(request),
    )


async def authentication_problem(
    request: Request, error: AuthenticationRequiredError
) -> JSONResponse:
    return problem_response(
        "authentication-required", "Authentication required", 401,
        "A trusted user identity is required.", str(error),
        **_identities(request), **_product(request),
    )


async def authorization_problem(
    request: Request, error: AuthorizationDeniedError
) -> JSONResponse:
    return problem_response(
        "authorization-denied", "Authorization denied", 403,
        "The authenticated user is not allowed to perform this operation.",
        str(error), **_identities(request), **_product(request),
    )


async def capacity_problem(
    request: Request, error: CapacityLimitError
) -> JSONResponse:
    response = problem_response(
        "capacity-limit", "Capacity limit reached", 429,
        "Finish the current operation before starting another one.", str(error),
        **_identities(request), **_product(request),
    )
    response.headers["Retry-After"] = "5"
    return response
