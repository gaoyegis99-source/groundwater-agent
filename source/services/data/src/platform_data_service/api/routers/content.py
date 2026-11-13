import re
from typing import Annotated

from fastapi import FastAPI, Header, Path, Request
from fastapi.responses import StreamingResponse

from ... import access
from ...composition import Application
from ...errors import DataProblemError
from ...query import service as queries
from ...repositories import http_queries
from ..audit import record as audit


_WORKFLOW_FILE_EXTENSIONS = {
    "arrow-ipc-file": "arrow", "binary": "bin", "cog": "tif", "flatgeobuf": "fgb",
    "geopackage": "gpkg", "geoparquet": "parquet", "geotiff": "tif",
    "markdown": "md", "pytorch-state-dict": "pt", "text": "txt",
}


def download(service: Application, obj, filename, range_header=None):
    handle = service.store.open(obj)
    stream = handle.__enter__()
    start, end, status_code = 0, obj["size"] - 1, 200
    if range_header:
        try:
            if not range_header.startswith("bytes=") or "," in range_header:
                raise ValueError
            left, right = range_header[6:].split("-", 1)
            if left:
                start, end = int(left), int(right) if right else end
            else:
                suffix = int(right)
                if suffix <= 0:
                    raise ValueError
                start = max(0, obj["size"] - suffix)
            if start < 0 or end < start or start >= obj["size"]:
                raise ValueError
            end, status_code = min(end, obj["size"] - 1), 206
        except (ValueError, TypeError):
            handle.__exit__(None, None, None)
            raise DataProblemError("RANGE_NOT_SATISFIABLE", 416, size=obj["size"]) from None
    stream.seek(start)
    remaining = end - start + 1

    def chunks():
        nonlocal remaining
        try:
            while remaining and (block := stream.read(min(1024 * 1024, remaining))):
                remaining -= len(block)
                yield block
        finally:
            handle.__exit__(None, None, None)

    return StreamingResponse(
        chunks(), media_type="application/octet-stream", status_code=status_code,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(end - start + 1), "Accept-Ranges": "bytes",
            "ETag": f'"{obj["checksum"]}"',
            **({"Content-Range": f"bytes {start}-{end}/{obj['size']}"} if status_code == 206 else {}),
        },
    )


def register_content_routes(app: FastAPI, settings, service: Application) -> None:
    binary_responses = {
        status: {
            "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
            "headers": {
                "Accept-Ranges": {"schema": {"type": "string"}},
                "ETag": {"schema": {"type": "string"}},
                "Content-Range": {"schema": {"type": "string"}},
            },
        }
        for status in (200, 206)
    }

    @app.get("/data/v3/queries/{queryId}/content", response_class=StreamingResponse, responses=binary_responses, operation_id="data_download_query_result")
    def query_content(query_id: Annotated[str, Path(alias="queryId")], request: Request, range_header: str | None = Header(default=None, alias="Range")):
        with service.db.transaction() as connection:
            row = queries.get_owned(
                connection, settings.project, query_id, request.state.actor
            )
        if not row["result_available"]:
            raise DataProblemError("QUERY_RESULT_UNAVAILABLE", 410)
        audit(request, "download.query", queryId=query_id, checksum=row["result_object"]["checksum"])
        return download(service, row["result_object"], f"{query_id}.json", range_header)

    @app.get(
        "/data/v3/versions/{versionId}/objects/{objectId}/content",
        response_class=StreamingResponse,
        responses=binary_responses,
        operation_id="data_download_object",
    )
    def content(version_id: Annotated[str, Path(alias="versionId")], object_id: Annotated[str, Path(alias="objectId")], request: Request, range_header: str | None = Header(default=None, alias="Range")):
        with service.db.transaction() as connection:
            access.require_version(
                connection,
                settings.project,
                version_id,
                request.state.actor,
            )
        obj = http_queries.get_object_descriptor(
            service.db, settings.project, version_id, object_id
        )
        if not obj:
            raise DataProblemError("OBJECT_NOT_FOUND", 404)
        audit(
            request, "download.object", versionId=version_id, objectId=object_id,
            checksum=obj["descriptor"]["checksum"],
        )
        format_ = obj["descriptor"]["format"]
        filename = f"{object_id}.{format_}"
        output_name = obj.get("output_name")
        if isinstance(output_name, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", output_name):
            filename = f"{output_name}.{_WORKFLOW_FILE_EXTENSIONS.get(format_, format_)}"
        return download(
            service, obj["descriptor"], filename, range_header,
        )
