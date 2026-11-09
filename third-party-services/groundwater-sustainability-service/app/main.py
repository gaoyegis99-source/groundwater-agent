from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import BackgroundTasks, Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

from . import __version__
from .auth import bearer_dependency
from .config import Settings
from .errors import AppError
from .logging_config import configure_logging
from .repository import SustainabilityTaskRepository
from .operations import OperationRequest, UploadCreateRequest
from .schemas import (
    CancellationResult,
    ErrorDetail,
    ErrorResponse,
    TaskAccepted,
    TaskStatus,
    TaskView,
)
from .sustainability import (
    RESTRICTION_DECISION_RULE,
    RESTRICTION_GUIDANCE,
    SustainabilityEvaluationResult,
    SustainabilityInput,
    SustainabilityTaskInput,
    evaluate_sustainability,
)
from .sustainability_tasks import SustainabilityTaskService
from .uploads import UploadStore, canonical_json

LOGGER = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or Settings.from_env()
    runtime_settings.ensure_directories()
    configure_logging(runtime_settings)
    repository = SustainabilityTaskRepository(runtime_settings.database_path)
    service = SustainabilityTaskService(runtime_settings, repository)
    uploads = UploadStore(
        runtime_settings.effective_uploads_dir,
        runtime_settings.max_upload_bytes,
        runtime_settings.upload_part_bytes,
    )
    require_bearer = bearer_dependency(runtime_settings.api_key)
    secured = [Depends(require_bearer)]

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        repository.initialize()
        recovered = repository.recover_unfinished()
        if recovered:
            LOGGER.warning("recovered %s unfinished comprehensive evaluation tasks", recovered)
        yield

    app = FastAPI(
        title="地下水水源地可持续利用综合评估服务",
        version=__version__,
        description="仅提供孔隙型、裂隙型和岩溶型地下水水源地可持续利用综合评价接口。",
        lifespan=lifespan,
    )
    app.state.settings = runtime_settings
    app.state.sustainability_task_repository = repository
    app.state.sustainability_task_service = service
    app.state.upload_store = uploads

    def error_payload(error: AppError, request_id: str | None = None) -> dict:
        return ErrorResponse(
            error=ErrorDetail(
                code=error.code,
                message=error.message,
                details=error.details,
                request_id=request_id,
            )
        ).model_dump(mode="json")

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid4()))[:200]
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        response = JSONResponse(
            status_code=exc.status_code,
            content=error_payload(exc, getattr(request.state, "request_id", None)),
        )
        if exc.status_code == 401:
            response.headers["WWW-Authenticate"] = "Bearer"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        details = [
            {
                "location": [str(part) for part in item.get("loc", ())],
                "message": item.get("msg"),
                "type": item.get("type"),
            }
            for item in exc.errors()
        ]
        error = AppError(
            "REQUEST_VALIDATION_ERROR",
            "请求参数校验失败。",
            status_code=422,
            details=details,
        )
        return JSONResponse(
            status_code=422,
            content=error_payload(error, getattr(request.state, "request_id", None)),
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception):
        LOGGER.exception("unhandled request error")
        error = AppError("INTERNAL_ERROR", "服务内部错误。", status_code=500)
        return JSONResponse(
            status_code=500,
            content=error_payload(error, getattr(request.state, "request_id", None)),
        )

    def task_or_404(task_id: str) -> dict:
        task = repository.get(task_id)
        if task is None:
            raise AppError("TASK_NOT_FOUND", "综合评价任务不存在。", status_code=404)
        return task

    def task_urls(task_id: str) -> tuple[str, str, str]:
        base = f"/api/v1/sustainability/tasks/{task_id}"
        return base, f"{base}/result", f"{base}/files/result.json"

    def task_view(task: dict) -> TaskView:
        task_id = task["task_id"]
        _, result_url, download_url = task_urls(task_id)
        error = None
        if task["error_code"]:
            error = ErrorDetail(
                code=task["error_code"],
                message=task["error_message"],
                details=task["error_details"],
            )
        return TaskView(
            task_id=task_id,
            status=TaskStatus(task["status"]),
            created_at=task["created_at"],
            updated_at=task["updated_at"],
            started_at=task["started_at"],
            finished_at=task["finished_at"],
            error=error,
            result_url=result_url,
            download_url=download_url,
        )

    def accept_task(
        payload: SustainabilityTaskInput, background_tasks: BackgroundTasks
    ) -> TaskAccepted:
        try:
            task, replay = service.create_task(payload)
        except sqlite3.IntegrityError:
            task = (
                repository.get_by_client_request_id(payload.client_request_id)
                if payload.client_request_id
                else None
            )
            if task is None:
                raise
            if task["input"] != payload.model_dump(mode="json", exclude_none=True):
                raise AppError(
                    "IDEMPOTENCY_CONFLICT",
                    "相同 client_request_id 已用于不同的请求内容。",
                    status_code=409,
                    details={"client_request_id": payload.client_request_id},
                )
            replay = True
        if not replay:
            background_tasks.add_task(service.run_task, task["task_id"])
        status_url, result_url, download_url = task_urls(task["task_id"])
        return TaskAccepted(
            task_id=task["task_id"],
            status=TaskStatus(task["status"]),
            created_at=task["created_at"],
            status_url=status_url,
            result_url=result_url,
            download_url=download_url,
            idempotent_replay=replay,
        )

    @app.get("/healthz", tags=["服务"])
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/readyz", tags=["服务"])
    def ready() -> dict:
        try:
            repository.initialize()
            runtime_settings.results_dir.mkdir(parents=True, exist_ok=True)
            probe = runtime_settings.results_dir / ".ready-probe"
            probe.write_bytes(b"ready")
            probe.unlink()
        except (OSError, sqlite3.Error) as error:
            raise AppError("SERVICE_NOT_READY", "服务依赖尚未就绪。", 503) from error
        return {"status": "ready", "version": __version__}

    @app.get("/version", tags=["服务"])
    def version() -> dict:
        return {
            "service": "groundwater-sustainability",
            "serviceVersion": __version__,
            "sourceProductVersion": "0.6.4",
            "apiVersion": "remote-operation.v1",
            "modelVersion": "sustainability-evaluation.v1.1",
            "computeProfile": "cpu",
        }

    @app.get("/api/v1/capabilities", tags=["服务"], dependencies=secured)
    def capabilities() -> dict:
        return {
            "apiVersion": "remote-operation.v1",
            "operations": ["sustainability.evaluate"],
            "uploads": uploads.capabilities(),
            "cancellation": {"pending": True, "running": False},
        }

    @app.get("/sustainability", include_in_schema=False, dependencies=secured)
    def comprehensive_demo():
        return FileResponse(Path(__file__).parent / "static" / "sustainability.html")

    @app.get(
        "/api/v1/sustainability/restrictions",
        tags=["综合评价"],
        dependencies=secured,
    )
    def restrictions():
        return {
            "source": "《地下水水源地可持续利用评估技术指南建议稿》6.8节",
            "decision_rule": RESTRICTION_DECISION_RULE,
            "items": [dict(item) for item in RESTRICTION_GUIDANCE],
        }

    @app.post(
        "/api/v1/sustainability/evaluate",
        response_model=SustainabilityEvaluationResult,
        tags=["综合评价"],
        summary="同步执行综合评价",
        dependencies=secured,
    )
    def evaluate(payload: SustainabilityInput):
        try:
            return evaluate_sustainability(payload)
        except ValueError as exc:
            raise AppError("SUSTAINABILITY_INPUT_INVALID", str(exc), status_code=422) from exc

    @app.post(
        "/api/v1/sustainability/tasks",
        response_model=TaskAccepted,
        status_code=202,
        responses={409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
        tags=["综合评价任务"],
        summary="创建综合评价任务",
        dependencies=secured,
    )
    def create_task(payload: SustainabilityTaskInput, background_tasks: BackgroundTasks):
        return accept_task(payload, background_tasks)

    @app.get(
        "/api/v1/sustainability/tasks/{task_id}",
        response_model=TaskView,
        responses={404: {"model": ErrorResponse}},
        tags=["综合评价任务"],
        summary="查询综合评价任务状态",
        dependencies=secured,
    )
    def get_task(task_id: str):
        return task_view(task_or_404(task_id))

    @app.get(
        "/api/v1/sustainability/tasks/by-client-request/{client_request_id}",
        response_model=TaskView,
        tags=["综合评价任务"],
        dependencies=secured,
    )
    def get_task_by_client_request(client_request_id: str):
        task = repository.get_by_client_request_id(client_request_id)
        if task is None:
            raise AppError("TASK_NOT_FOUND", "综合评价任务不存在。", 404)
        return task_view(task)

    @app.post(
        "/api/v1/sustainability/tasks/{task_id}/cancel",
        response_model=CancellationResult,
        tags=["综合评价任务"],
        dependencies=secured,
    )
    def cancel_task(task_id: str):
        task_or_404(task_id)
        accepted = repository.cancel_pending(task_id)
        current = task_or_404(task_id)
        return CancellationResult(
            task_id=task_id,
            status=TaskStatus(current["status"]),
            cancellation_accepted=accepted,
            remote_termination_confirmed=accepted
            or current["status"] in {
                TaskStatus.succeeded.value,
                TaskStatus.failed.value,
                TaskStatus.cancelled.value,
            },
        )

    @app.get(
        "/api/v1/sustainability/tasks/{task_id}/result",
        response_model=SustainabilityEvaluationResult,
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
        tags=["综合评价任务"],
        summary="获取综合评价结构化结果",
        dependencies=secured,
    )
    def get_result(task_id: str):
        task = task_or_404(task_id)
        if task["status"] != TaskStatus.succeeded.value:
            raise AppError(
                "RESULT_NOT_READY",
                "结果尚不可用。请先查询任务状态。",
                status_code=409,
                details={"status": task["status"]},
            )
        return task["result"]

    @app.get(
        "/api/v1/sustainability/tasks/{task_id}/files/result.json",
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
        tags=["综合评价任务"],
        summary="下载综合评价JSON结果文件",
        dependencies=secured,
    )
    def download_result(task_id: str):
        task = task_or_404(task_id)
        if task["status"] != TaskStatus.succeeded.value:
            raise AppError(
                "RESULT_NOT_READY",
                "结果文件尚不可用。请先查询任务状态。",
                status_code=409,
                details={"status": task["status"]},
            )
        result_file = Path(task["result_file"])
        if not result_file.is_file():
            raise AppError("RESULT_FILE_NOT_FOUND", "结果记录存在，但结果文件缺失。", status_code=404)
        return FileResponse(
            result_file,
            media_type="application/json",
            filename=f"groundwater-sustainability-{task_id}.json",
        )

    @app.post("/api/v1/uploads", status_code=201, dependencies=secured)
    def create_upload(payload: UploadCreateRequest):
        return uploads.create(payload.model_dump(by_alias=True))

    @app.put("/api/v1/uploads/{upload_id}/parts/{part_number}", dependencies=secured)
    async def upload_part(
        upload_id: str,
        part_number: int,
        request: Request,
        x_content_sha256: str = Header(alias="X-Content-SHA256"),
    ):
        length = request.headers.get("content-length")
        if length is None or not length.isascii() or not length.isdigit():
            raise AppError("CONTENT_LENGTH_REQUIRED", "上传分片必须声明长度。", 411)
        if int(length) > runtime_settings.upload_part_bytes:
            raise AppError("UPLOAD_PART_TOO_LARGE", "上传分片过大。", 413)
        return uploads.write_part(
            upload_id, part_number, await request.body(), x_content_sha256
        )

    @app.post("/api/v1/uploads/{upload_id}/complete", dependencies=secured)
    def complete_upload(upload_id: str):
        return uploads.complete(upload_id)

    @app.post("/api/v1/operations", status_code=202, dependencies=secured)
    def create_operation(
        payload: OperationRequest, background_tasks: BackgroundTasks
    ) -> dict:
        if set(payload.inputs) != {"assessment_input"} or payload.parameters:
            raise AppError("OPERATION_INPUT_INVALID", "综合评价任务输入不符合契约。", 422)
        reference = payload.inputs["assessment_input"]
        metadata, source = uploads.resolve(reference.artifact_id)
        if any(
            metadata[name] != expected
            for name, expected in {
                "mediaType": "application/json",
                "format": "json",
                "sizeBytes": reference.size_bytes,
                "sha256": reference.sha256,
            }.items()
        ):
            raise AppError("OPERATION_INPUT_INVALID", "综合评价输入标识不匹配。", 422)
        try:
            document = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AppError("OPERATION_INPUT_INVALID", "综合评价输入不是有效JSON。", 422) from error
        if not isinstance(document, dict):
            raise AppError("OPERATION_INPUT_INVALID", "综合评价输入必须是JSON对象。", 422)
        document["client_request_id"] = payload.client_request_id
        accepted = accept_task(
            SustainabilityTaskInput.model_validate(document), background_tasks
        )
        return {
            "operationId": accepted.task_id,
            "status": accepted.status.value,
            "statusUrl": f"/api/v1/operations/{accepted.task_id}",
            "resultUrl": f"/api/v1/operations/{accepted.task_id}/results",
            "receiptUrl": f"/api/v1/operations/{accepted.task_id}/receipt",
            "idempotentReplay": accepted.idempotent_replay,
        }

    @app.get(
        "/api/v1/operations/by-client-request/{client_request_id}",
        dependencies=secured,
    )
    def reconcile_operation(client_request_id: str) -> dict:
        task = repository.get_by_client_request_id(client_request_id)
        if task is None:
            raise AppError("OPERATION_NOT_FOUND", "远程任务不存在。", 404)
        return operation_status(task)

    def operation_status(task: dict) -> dict:
        return {
            "operationId": task["task_id"],
            "status": task["status"],
            "createdAt": task["created_at"],
            "startedAt": task["started_at"],
            "finishedAt": task["finished_at"],
            "error": (
                None
                if not task["error_code"]
                else {
                    "code": task["error_code"],
                    "message": task["error_message"],
                    "details": task["error_details"],
                }
            ),
        }

    @app.get("/api/v1/operations/{operation_id}", dependencies=secured)
    def get_operation(operation_id: str) -> dict:
        return operation_status(task_or_404(operation_id))

    @app.post("/api/v1/operations/{operation_id}/cancel", dependencies=secured)
    def cancel_operation(operation_id: str) -> dict:
        result = cancel_task(operation_id)
        return {
            "operationId": operation_id,
            "status": result.status.value,
            "cancellationAccepted": result.cancellation_accepted,
            "remoteTerminationConfirmed": result.remote_termination_confirmed,
        }

    @app.get("/api/v1/operations/{operation_id}/results", dependencies=secured)
    def operation_results(operation_id: str) -> dict:
        task = task_or_404(operation_id)
        if task["status"] != TaskStatus.succeeded.value:
            raise AppError(
                "RESULT_NOT_READY",
                "结果尚不可用。请先查询任务状态。",
                409,
                {"status": task["status"]},
            )
        result_file = Path(task["result_file"])
        content = result_file.read_bytes()
        return {
            "operationId": operation_id,
            "serviceVersion": __version__,
            "modelVersion": "sustainability-evaluation.v1.1",
            "artifacts": [
                {
                    "port": "result_file",
                    "downloadUrl": task_urls(operation_id)[2],
                    "mediaType": "application/json",
                    "format": "json",
                    "sizeBytes": len(content),
                    "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
                }
            ],
            "values": {"sustainability_result": task["result"]},
        }

    @app.get("/api/v1/operations/{operation_id}/receipt", dependencies=secured)
    def operation_receipt(operation_id: str) -> dict:
        task = task_or_404(operation_id)
        receipt = {
            "schemaVersion": "third-party-run-receipt.v1",
            "serviceId": "groundwater-sustainability",
            "serviceVersion": __version__,
            "modelVersion": "sustainability-evaluation.v1.1",
            "remoteTaskId": operation_id,
            "requestSha256": "sha256:" + hashlib.sha256(
                canonical_json(task["input"])
            ).hexdigest(),
            "status": task["status"],
            "createdAt": task["created_at"],
            "startedAt": task["started_at"],
            "finishedAt": task["finished_at"],
            "remoteTerminationConfirmed": task["status"] in {
                TaskStatus.succeeded.value,
                TaskStatus.failed.value,
                TaskStatus.cancelled.value,
            },
        }
        return receipt

    return app


app = create_app()
