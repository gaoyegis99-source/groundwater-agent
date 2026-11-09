from fastapi import FastAPI
import re
from platform_protocol_contracts import decode_display_name_header

from ..adapters.database import identity
from .problems import problem_response


def install_boundary(app: FastAPI, settings) -> None:
    @app.middleware("http")
    async def context(request, call_next):
        request.state.trace_id = getattr(request.state, "trace_id", None) or (
            request.headers.get("X-Trace-ID") or identity()
        )
        request.state.request_id = request.headers.get("X-Request-ID") or identity()
        if request.url.path.startswith("/data/v3"):
            supplied_project = request.headers.get("x-project-id")
            client = request.headers.get("x-client-type", "web")
            if client not in {"web", "sdk", "agent", "service", "cli"}:
                return problem_response(
                    "CLIENT_TYPE_INVALID",
                    422,
                    "The client type is not supported.",
                    trace_id=request.state.trace_id,
                    request_id=request.state.request_id,
                    product_id=settings.product,
                )
            if settings.identity_mode == "gateway":
                subject = request.headers.get("x-platform-subject", "")
                display_name = decode_display_name_header(
                    request.headers.get("x-platform-display-name", "")
                )
                roles = {
                    value.strip()
                    for value in request.headers.get("x-platform-roles", "").split(",")
                    if value.strip()
                }
                if (
                    not supplied_project
                    or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}", subject)
                    is None
                    or not display_name
                    or len(display_name.encode("utf-8")) > 160
                    or any(
                        ord(character) < 32 or ord(character) == 127
                        for character in display_name
                    )
                    or not roles
                    or not roles <= {"researcher", "admin", "viewer"}
                ):
                    return problem_response(
                        "AUTHENTICATION_REQUIRED",
                        401,
                        "A trusted user identity is required.",
                        trace_id=request.state.trace_id,
                        request_id=request.state.request_id,
                        product_id=settings.product,
                    )
            else:
                subject = settings.local_identity.subject
                display_name = settings.local_identity.display_name
                roles = set(settings.local_identity.roles)
            if (supplied_project or settings.project) != settings.project:
                return problem_response(
                    "PROJECT_MISMATCH",
                    403,
                    "The request project differs from the deployment project.",
                    trace_id=request.state.trace_id,
                    request_id=request.state.request_id,
                    product_id=settings.product,
                )
            if not roles.intersection(
                {"researcher", "admin", "viewer"}
            ):
                return problem_response(
                    "ROLE_FORBIDDEN",
                    403,
                    "The authenticated user is not allowed to access project data.",
                    trace_id=request.state.trace_id,
                    request_id=request.state.request_id,
                    product_id=settings.product,
                )
            if (
                request.method not in {"GET", "HEAD", "OPTIONS"}
                and not (
                    request.method == "POST"
                    and request.url.path == "/data/v3/discovery"
                )
                and roles == {"viewer"}
            ):
                return problem_response(
                    "ROLE_FORBIDDEN",
                    403,
                    "The authenticated auditor has read-only access.",
                    trace_id=request.state.trace_id,
                    request_id=request.state.request_id,
                    product_id=settings.product,
                )
            is_semantic_write = request.method not in {
                "GET",
                "HEAD",
                "OPTIONS",
            } and request.url.path.endswith("/semantics")
            if is_semantic_write and "admin" not in roles:
                return problem_response(
                    "ROLE_FORBIDDEN",
                    403,
                    "The authenticated user is not allowed to perform this operation.",
                    trace_id=request.state.trace_id,
                    request_id=request.state.request_id,
                    product_id=settings.product,
                )
            request.state.actor = {
                "subject": subject,
                "display_name": display_name,
                "client": client,
                "roles": sorted(roles),
                "trace_id": request.state.trace_id,
                "request_id": request.state.request_id,
            }
            writes = request.method not in {"GET", "HEAD", "OPTIONS"} and (
                "/publications" in request.url.path
                or request.url.path.endswith("/semantics")
                or request.url.path.endswith("/access")
                or "/quality/" in request.url.path
                or request.url.path.endswith("/quality-binding")
                or "/ml/" in request.url.path
            )
            if writes and settings.read_only:
                return problem_response(
                    "WRITE_FORBIDDEN",
                    403,
                    "The Data service is read-only.",
                    trace_id=request.state.trace_id,
                    request_id=request.state.request_id,
                    product_id=settings.product,
                )
            if request.method in {"POST", "PUT"} and "/items/" not in request.url.path:
                data = bytearray()
                async for chunk in request.stream():
                    data.extend(chunk)
                    if len(data) > 1024**2:
                        return problem_response(
                            "METADATA_TOO_LARGE",
                            413,
                            "The request metadata exceeds the configured limit.",
                            trace_id=request.state.trace_id,
                            request_id=request.state.request_id,
                            product_id=settings.product,
                        )
                request._body = bytes(data)
        response = await call_next(request)
        response.headers["X-Trace-ID"] = request.state.trace_id
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
