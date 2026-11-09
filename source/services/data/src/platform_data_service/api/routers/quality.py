from typing import Annotated

from fastapi import FastAPI, Path, Query, Request, Response

from ...composition import Application
from ...errors import DataProblemError
from ... import quality
from ..models import (
    QualityBindingUpdateRequest,
    QualityRunRequest,
    QualitySuiteVersionRequest,
)
from ..responses import (
    QualityAlertPage,
    QualityBindingResponse,
    QualityRunPage,
    QualityRunResponse,
    QualitySuiteVersionResponse,
)
from ..wire import to_wire


def _require_operator(request: Request) -> None:
    if "admin" not in request.state.actor["roles"]:
        raise DataProblemError("ROLE_FORBIDDEN", 403)


def register_quality_routes(app: FastAPI, settings, service: Application) -> None:
    @app.post(
        "/data/v3/quality/suites/{suiteId}/versions",
        response_model=QualitySuiteVersionResponse,
        operation_id="data_create_quality_suite_version",
    )
    def create_suite_version(
        body: QualitySuiteVersionRequest,
        request: Request,
        response: Response,
        suite_id: Annotated[
            str, Path(alias="suiteId", pattern=r"^[A-Za-z0-9_-]{1,128}$")
        ],
    ):
        _require_operator(request)
        with service.db.transaction() as connection:
            value, created = quality.create_suite_version(
                connection, settings.project, suite_id, body, request.state.actor
            )
        response.status_code = 201 if created else 200
        return to_wire(value)

    @app.get(
        "/data/v3/quality/suite-versions/{suiteVersionId}",
        response_model=QualitySuiteVersionResponse,
        operation_id="data_get_quality_suite_version",
    )
    def get_suite_version(
        suite_version_id: Annotated[str, Path(alias="suiteVersionId")],
    ):
        with service.db.transaction() as connection:
            return to_wire(
                quality.get_suite_version(
                    connection, settings.project, suite_version_id
                )
            )

    @app.get(
        "/data/v3/datasets/{datasetId}/quality-binding",
        response_model=QualityBindingResponse,
        operation_id="data_get_quality_binding",
    )
    def get_binding(
        request: Request,
        dataset_id: Annotated[str, Path(alias="datasetId")],
    ):
        with service.db.transaction() as connection:
            return to_wire(
                quality.get_binding(
                    connection, settings.project, dataset_id, request.state.actor
                )
            )

    @app.put(
        "/data/v3/datasets/{datasetId}/quality-binding",
        response_model=QualityBindingResponse,
        operation_id="data_replace_quality_binding",
    )
    def replace_binding(
        body: QualityBindingUpdateRequest,
        request: Request,
        dataset_id: Annotated[str, Path(alias="datasetId")],
    ):
        _require_operator(request)
        with service.db.transaction() as connection:
            return to_wire(
                quality.replace_binding(
                    connection,
                    settings.project,
                    dataset_id,
                    body,
                    request.state.actor,
                )
            )

    @app.post(
        "/data/v3/quality/runs",
        response_model=QualityRunResponse,
        operation_id="data_submit_quality_run",
    )
    def submit_run(body: QualityRunRequest, request: Request, response: Response):
        _require_operator(request)
        with service.db.transaction() as connection:
            value, created = quality.submit_run(
                connection, settings.project, body, request.state.actor
            )
        response.status_code = 201 if created else 200
        return to_wire(value)

    @app.get(
        "/data/v3/quality/runs/{qualityRunId}",
        response_model=QualityRunResponse,
        operation_id="data_get_quality_run",
    )
    def get_run(
        request: Request,
        quality_run_id: Annotated[str, Path(alias="qualityRunId")],
    ):
        with service.db.transaction() as connection:
            return to_wire(
                quality.get_run(
                    connection, settings.project, quality_run_id, request.state.actor
                )
            )

    @app.get(
        "/data/v3/datasets/{datasetId}/quality-runs",
        response_model=QualityRunPage,
        operation_id="data_list_quality_runs",
    )
    def list_runs(
        request: Request,
        dataset_id: Annotated[str, Path(alias="datasetId")],
        cursor: str = Query(default="0", pattern=r"^[0-9]+$"),
        limit: int = Query(default=20, ge=1, le=100),
    ):
        with service.db.transaction() as connection:
            return to_wire(
                quality.list_runs(
                    connection,
                    settings.project,
                    dataset_id,
                    request.state.actor,
                    int(cursor),
                    limit,
                )
            )

    @app.get(
        "/data/v3/datasets/{datasetId}/quality-alerts",
        response_model=QualityAlertPage,
        operation_id="data_list_quality_alerts",
    )
    def list_alerts(
        request: Request,
        dataset_id: Annotated[str, Path(alias="datasetId")],
        cursor: str = Query(default="0", pattern=r"^[0-9]+$"),
        limit: int = Query(default=20, ge=1, le=100),
    ):
        with service.db.transaction() as connection:
            return to_wire(
                quality.list_alerts(
                    connection,
                    settings.project,
                    dataset_id,
                    request.state.actor,
                    int(cursor),
                    limit,
                )
            )
