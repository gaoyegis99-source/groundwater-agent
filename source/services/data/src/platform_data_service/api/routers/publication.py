import hashlib
import tempfile
import threading
from typing import Annotated

from fastapi import FastAPI, Header, Path, Request
from starlette.concurrency import run_in_threadpool

from ...composition import Application
from ...errors import DataProblemError
from ...operations import service as operations
from ...publication import service as publications
from ..models import BeginPublicationRequest
from ..responses import (
    ObjectDescriptor,
    OperationAccepted,
    OperationResponse,
    PublicationAbortResponse,
    PublicationReceipt,
    PublicationResponse,
)
from ..wire import to_wire, with_semantic_id
from ..audit import record as audit


def _publication_wire(value: dict) -> dict:
    return {
        "publication_id": value["id"],
        "state": value["state"],
        "manifest": value["manifest"],
        "uploads": value["uploads"],
        "operations": [
            {
                "operation_id": item["id"],
                "state": item["state"],
                "error": item["error"],
            }
            for item in value["operations"]
        ],
        "receipt": value["receipt"],
        "idempotency_key": value["idem"],
        "manifest_checksum": value["digest"],
        "created_at": value["created_at"],
        "events": [
            {
                "sequence": item["seq"],
                "state": item["state"],
                "created_at": item["created_at"],
            }
            for item in value["events"]
        ],
    }


def _operation_wire(value: dict) -> dict:
    return {
        "operation_id": value["id"],
        "kind": value["kind"],
        "target": value["target"],
        "state": value["state"],
        "generation": value["generation"],
        "result": value["result"],
        "error": value["error"],
        "actor": value["actor"],
        "created_at": value["created_at"],
        "updated_at": value["updated_at"],
        "events": [
            {
                "sequence": item["seq"],
                "state": item["state"],
                "generation": item["generation"],
                "createdAt": item["created_at"],
            }
            for item in value["events"]
        ],
    }


def register_publication_routes(
    app: FastAPI,
    settings,
    service: Application,
    upload_slots: threading.BoundedSemaphore,
) -> None:
    @app.post(
        "/data/v3/publications",
        status_code=201,
        response_model=PublicationResponse,
        operation_id="data_begin_publication",
    )
    def begin(body: BeginPublicationRequest, request: Request):
        with service.db.transaction() as connection:
            value = to_wire(
                _publication_wire(
                    publications.begin(
                        connection, settings.project, body, request.state.actor
                    )
                )
            )
        audit(request, "publication.begin", publicationId=value["publicationId"])
        return value

    @app.get(
        "/data/v3/publications/{publicationId}",
        response_model=PublicationResponse,
        operation_id="data_get_publication",
    )
    def publication(
        publication_id: Annotated[str, Path(alias="publicationId")], request: Request
    ):
        with service.db.transaction() as connection:
            return to_wire(
                _publication_wire(
                    publications.public(
                        connection,
                        settings.project,
                        publication_id,
                        request.state.actor,
                    )
                )
            )

    @app.put(
        "/data/v3/publications/{publicationId}/items/{name}",
        response_model=ObjectDescriptor,
        operation_id="data_upload_publication_item",
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/octet-stream": {
                        "schema": {"type": "string", "format": "binary"}
                    }
                },
            }
        },
    )
    async def upload(
        publication_id: Annotated[str, Path(alias="publicationId")],
        name: str,
        request: Request,
        x_content_sha256: str = Header(),
    ):
        if (
            len(x_content_sha256) != 71
            or not x_content_sha256.startswith("sha256:")
            or any(
                character not in "0123456789abcdef"
                for character in x_content_sha256[7:]
            )
        ):
            raise DataProblemError("CHECKSUM_INVALID")
        with service.db.transaction() as connection:
            publication_row = publications.get_owned(
                connection, settings.project, publication_id, request.state.actor
            )
            if publication_row["state"] != "OPEN":
                raise DataProblemError("PUBLICATION_FROZEN", 409)
            if name not in {
                item["name"] for item in publication_row["manifest"]["outputs"]
            }:
                raise DataProblemError("UNKNOWN_OUTPUT", 404)
        audit(request, "upload.start", publicationId=publication_id, itemName=name)
        if not upload_slots.acquire(blocking=False):
            raise DataProblemError("PROJECT_UPLOAD_LIMIT", 429)
        obj = None
        try:
            with tempfile.TemporaryFile(dir=settings.root / "work") as stream:
                sha, size = hashlib.sha256(), 0
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > settings.upload_bytes:
                        raise DataProblemError("OBJECT_TOO_LARGE", 413)
                    sha.update(chunk)
                    await run_in_threadpool(stream.write, chunk)
                if "sha256:" + sha.hexdigest() != x_content_sha256:
                    raise DataProblemError("CHECKSUM_MISMATCH", 409)
                stream.seek(0)
                obj = await run_in_threadpool(
                    service.store.put, stream, settings.upload_bytes
                )
            try:
                with service.db.transaction() as connection:
                    attached = publications.attach(
                        connection,
                        settings.project,
                        publication_id,
                        name,
                        obj,
                        request.state.actor,
                    )
            except BaseException:
                # The object has a fresh random identity and is not visible until
                # the upload row commits. Cancellation may abort the publication
                # while the request is finishing, so remove this unpublished file.
                service.store.delete(obj)
                raise
            if attached["id"] != obj["id"]:
                service.store.delete(obj)
            audit(
                request,
                "upload.complete",
                publicationId=publication_id,
                itemName=name,
                sizeBytes=attached["size"],
                checksum=attached["checksum"],
            )
            return to_wire(with_semantic_id(attached, "object_id"))
        finally:
            upload_slots.release()

    @app.post(
        "/data/v3/publications/{publicationId}/prepare",
        status_code=202,
        response_model=OperationAccepted,
        operation_id="data_prepare_publication",
    )
    def prepare(
        publication_id: Annotated[str, Path(alias="publicationId")], request: Request
    ):
        with service.db.transaction() as connection:
            return to_wire(
                publications.prepare(
                    connection, settings.project, publication_id, request.state.actor
                )
            )

    @app.post(
        "/data/v3/publications/{publicationId}/commit",
        response_model=PublicationReceipt,
        operation_id="data_commit_publication",
    )
    def commit(
        publication_id: Annotated[str, Path(alias="publicationId")], request: Request
    ):
        with service.db.transaction() as connection:
            row = publications.get_owned(
                connection, settings.project, publication_id, request.state.actor
            )
        if row["state"] == "READY":
            for item in row["prepared"]:
                for obj in item["objects"]:
                    service.store.verify(obj)
        with service.db.transaction() as connection:
            return to_wire(
                publications.commit(
                    connection, settings.project, publication_id, request.state.actor
                )
            )

    @app.post(
        "/data/v3/publications/{publicationId}/abort",
        response_model=PublicationAbortResponse,
        operation_id="data_abort_publication",
    )
    def abort(
        publication_id: Annotated[str, Path(alias="publicationId")], request: Request
    ):
        with service.db.transaction() as connection:
            value = to_wire(
                with_semantic_id(
                    publications.abort(
                        connection,
                        settings.project,
                        publication_id,
                        request.state.actor,
                    ),
                    "publication_id",
                )
            )
        audit(request, "upload.abort", publicationId=publication_id)
        return value

    @app.get(
        "/data/v3/operations/{operationId}",
        response_model=OperationResponse,
        operation_id="data_get_operation",
    )
    def operation(
        operation_id: Annotated[str, Path(alias="operationId")], request: Request
    ):
        with service.db.transaction() as connection:
            return to_wire(
                _operation_wire(
                    operations.get_owned(
                        connection,
                        settings.project,
                        operation_id,
                        request.state.actor,
                    )
                )
            )
