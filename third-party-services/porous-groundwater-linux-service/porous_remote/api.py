"""FastAPI adapter for the V6 remote-operation protocol."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import fcntl
import hmac
import os
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import MEDIA_TYPES, PORTS, SERVICE_ID, VERSION
from .errors import AppError
from .packages import MAX_BYTES
from .queue import OperationQueue
from .uploads import UploadStore

PART_BYTES = 4 * 1024**2
DIGEST = r"^sha256:[a-f0-9]{64}$"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContentIdentity(Strict):
    sizeBytes: int = Field(strict=True, gt=0, le=MAX_BYTES)
    sha256: str = Field(pattern=DIGEST)
    mediaType: str = Field(min_length=1, max_length=128)
    format: str = Field(min_length=1, max_length=32)


class Upload(ContentIdentity):
    name: str = Field(min_length=1, max_length=200)


class Input(ContentIdentity):
    artifactId: str = Field(pattern=r"^artifact-[a-f0-9]{32}$")


class Operation(Strict):
    operationType: str = Field(max_length=64)
    clientRequestId: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,128}$")
    inputs: dict[str, Input]
    parameters: dict


def _secret(variable: str) -> Path:
    path = Path(os.environ[variable]).resolve(strict=True)
    if not path.is_file() or len(path.read_bytes().strip()) < 32:
        raise ValueError(f"INVALID_SECRET_FILE:{variable}")
    return path


@asynccontextmanager
async def lifecycle(app: FastAPI):
    root = Path(os.environ["POROUS_DATA_DIR"]).resolve()
    vendor = Path(os.environ["POROUS_VENDOR_DIR"]).resolve(strict=True)
    key = _secret("POROUS_API_KEY_FILE").read_bytes().strip()
    signing_key = _secret("POROUS_PACKAGE_KEY_FILE")
    root.mkdir(mode=0o700, exist_ok=True, parents=True)
    with (root / ".instance.lock").open("ab") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        (root / "jobs").mkdir(mode=0o700, exist_ok=True)
        app.state.api_key = key
        app.state.uploads = UploadStore(root / "uploads", MAX_BYTES, PART_BYTES)
        queue = OperationQueue(root, vendor, signing_key)
        app.state.queue = queue
        queue.start()
        try:
            yield
        finally:
            await queue.close()


app = FastAPI(title=SERVICE_ID, lifespan=lifecycle)


@app.middleware("http")
async def request_identity(request: Request, call_next):
    request.state.request_id = uuid4().hex
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


@app.exception_handler(AppError)
async def app_error(request: Request, error: AppError) -> JSONResponse:
    return JSONResponse({"error": {
        "code": error.code, "message": error.message, "details": error.details,
        "request_id": request.state.request_id,
    }}, status_code=error.status_code,
        headers={"WWW-Authenticate": "Bearer"} if error.status_code == 401 else None)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
    # Match the V6 remote Worker field-error envelope; never echo uploaded input.
    return await app_error(request, AppError(
        "REQUEST_VALIDATION_ERROR", "Request fields are invalid.", 422,
        details=[{"location": list(item["loc"]), "type": item["type"]}
                 for item in error.errors()],
    ))


def authorize(request: Request, authorization: Annotated[str, Header()] = "") -> None:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(
        token.encode("utf-8"), request.app.state.api_key
    ):
        raise AppError("AUTHENTICATION_REQUIRED", "Bearer authentication is required.", 401)


SECURED = [Depends(authorize)]


@app.get("/healthz")
async def health():
    return {"status": "ok"}


@app.get("/readyz")
async def ready():
    worker = app.state.queue.worker
    if worker is None or worker.done():
        raise AppError("OPERATION_WORKER_UNAVAILABLE", "Operation worker unavailable.", 503)
    return {"status": "ready"}


@app.get("/version")
async def version():
    return VERSION


@app.get("/api/v1/capabilities", dependencies=SECURED)
async def capabilities():
    return {"operations": list(PORTS), "uploads": app.state.uploads.capabilities()}


@app.post("/api/v1/uploads", status_code=201, dependencies=SECURED)
async def create_upload(payload: Upload):
    if MEDIA_TYPES.get(payload.format) != payload.mediaType:
        raise AppError("INPUT_FORMAT_INVALID", "Input format does not match its media type.", 422)
    return await asyncio.to_thread(app.state.uploads.create, payload.model_dump())


@app.put("/api/v1/uploads/{upload_id}/parts/{part_number}", dependencies=SECURED)
async def write_part(
    upload_id: str, part_number: int, request: Request,
    x_content_sha256: Annotated[str, Header()],
):
    buffer = bytearray()
    async for part in request.stream():
        if len(buffer) + len(part) > PART_BYTES:
            raise AppError("UPLOAD_PART_TOO_LARGE", "Upload part exceeds the limit.", 413)
        buffer.extend(part)
    return await asyncio.to_thread(
        app.state.uploads.write_part, upload_id, part_number, bytes(buffer), x_content_sha256
    )


@app.post("/api/v1/uploads/{upload_id}/complete", dependencies=SECURED)
async def complete(upload_id: str):
    return await asyncio.to_thread(app.state.uploads.complete, upload_id)


@app.post("/api/v1/operations", status_code=202, dependencies=SECURED)
async def submit(payload: Operation):
    specification = PORTS.get(payload.operationType)
    if specification is None:
        raise AppError("OPERATION_UNAVAILABLE", "Operation is unavailable.", 422)
    if set(payload.inputs) != set(specification) or (
        payload.parameters and (
            payload.operationType != "pest.calibrate"
            or set(payload.parameters) != {"includeCalibratedModel"}
            or payload.parameters["includeCalibratedModel"] is not True
        )
    ):
        raise AppError("OPERATION_INPUT_INVALID", "Operation ports do not match.", 422)
    inputs = {}
    paths = {}
    for port, format_ in specification.items():
        reference = payload.inputs[port]
        stable = reference.model_dump(exclude={"artifactId"})
        metadata, path = app.state.uploads.resolve(reference.artifactId)
        if (
            reference.format != format_ or reference.mediaType != MEDIA_TYPES[format_]
            or any(metadata[field] != value for field, value in stable.items())
        ):
            raise AppError("OPERATION_INPUT_INVALID", "Operation input identity does not match.", 422)
        inputs[port] = stable
        paths[port] = str(path)
    return app.state.queue.submit(payload.operationType, payload.clientRequestId, inputs, paths, payload.parameters)


@app.get("/api/v1/operations/by-client-request/{client_id}", dependencies=SECURED)
async def by_client_request(client_id: str):
    queue = app.state.queue
    return queue.status(queue.lookup(client_id, by_request=True))


@app.get("/api/v1/operations/{operation_id}", dependencies=SECURED)
async def operation(operation_id: str):
    queue = app.state.queue
    return queue.status(queue.lookup(operation_id))


@app.post("/api/v1/operations/{operation_id}/cancel", dependencies=SECURED)
async def cancel(operation_id: str):
    return app.state.queue.cancel(operation_id)


@app.get("/api/v1/operations/{operation_id}/results", dependencies=SECURED)
async def results(operation_id: str):
    return app.state.queue.result(operation_id)


@app.get("/api/v1/operations/{operation_id}/receipt", dependencies=SECURED)
async def receipt(operation_id: str):
    row = app.state.queue.lookup(operation_id)
    return {
        "schemaVersion": "third-party-run-receipt.v1",
        "serviceId": SERVICE_ID,
        **VERSION,
        "remoteTaskId": row["id"],
        "operationType": row["operation"],
        "status": row["status"],
        "requestSha256": row["request_digest"],
        "remoteTerminationConfirmed": row["status"] in {"succeeded", "failed", "cancelled"},
    }


@app.get("/api/v1/operations/{operation_id}/artifacts/{port}", dependencies=SECURED)
async def artifact(operation_id: str, port: str):
    queue = app.state.queue
    result = queue.result(operation_id)
    for item in result["artifacts"]:
        if item["port"] == port:
            return FileResponse(
                queue.root / "jobs" / result["operationId"] / f"{port}.{item['format']}",
                media_type=item["mediaType"],
            )
    raise AppError("ARTIFACT_NOT_FOUND", "Unknown result port.", 404)
