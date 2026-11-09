from typing import Annotated

from fastapi import FastAPI, Path, Request, Response

from ...composition import Application
from ...errors import DataProblemError
from ... import mlops
from ..models import (
    MlExperimentRequest,
    MlRunRequest,
    ModelAliasUpdateRequest,
    ModelRegistryVersionRequest,
    ModelBenchmarkUpdateRequest,
)
from ..responses import (
    MlExperimentResponse,
    MlRunResponse,
    ModelAliasResponse,
    ModelRegistryVersionResponse,
    ResolvedModelResponse,
)
from ..wire import to_wire


def _require_operator(request: Request) -> None:
    if "admin" not in request.state.actor["roles"]:
        raise DataProblemError("ROLE_FORBIDDEN", 403)


def register_mlops_routes(app: FastAPI, settings, service: Application) -> None:
    @app.post(
        "/data/v3/ml/experiments",
        response_model=MlExperimentResponse,
        operation_id="data_create_ml_experiment",
    )
    def create_experiment(
        body: MlExperimentRequest, request: Request, response: Response
    ):
        _require_operator(request)
        with service.db.transaction() as connection:
            value, created = mlops.create_experiment(
                connection, settings.project, body, request.state.actor
            )
        response.status_code = 201 if created else 200
        return to_wire(value)

    @app.post(
        "/data/v3/ml/runs",
        response_model=MlRunResponse,
        operation_id="data_record_ml_run",
    )
    def record_run(body: MlRunRequest, request: Request, response: Response):
        _require_operator(request)
        with service.db.transaction() as connection:
            value, created = mlops.record_run(
                connection, settings.project, body, request.state.actor
            )
        response.status_code = 201 if created else 200
        return to_wire(value)

    @app.get(
        "/data/v3/ml/runs/{experimentRunId}",
        response_model=MlRunResponse,
        operation_id="data_get_ml_run",
    )
    def get_run(
        request: Request,
        experiment_run_id: Annotated[str, Path(alias="experimentRunId")],
    ):
        with service.db.transaction() as connection:
            return to_wire(
                mlops.get_run(
                    connection,
                    settings.project,
                    experiment_run_id,
                    request.state.actor,
                )
            )

    @app.post(
        "/data/v3/ml/models/{modelId}/versions",
        response_model=ModelRegistryVersionResponse,
        operation_id="data_register_model_version",
    )
    def register_model_version(
        body: ModelRegistryVersionRequest,
        request: Request,
        response: Response,
        model_id: Annotated[str, Path(alias="modelId")],
    ):
        _require_operator(request)
        with service.db.transaction() as connection:
            value, created = mlops.register_model_version(
                connection, settings.project, model_id, body, request.state.actor
            )
        response.status_code = 201 if created else 200
        return to_wire(value)

    @app.get(
        "/data/v3/ml/models/{modelId}/versions/{registryVersion}",
        response_model=ModelRegistryVersionResponse,
        operation_id="data_get_model_registry_version",
    )
    def get_model_version(
        request: Request,
        model_id: Annotated[str, Path(alias="modelId")],
        registry_version: Annotated[int, Path(alias="registryVersion", ge=1)],
    ):
        with service.db.transaction() as connection:
            return to_wire(
                mlops.get_model_version(
                    connection,
                    settings.project,
                    model_id,
                    registry_version,
                    request.state.actor,
                )
            )

    @app.post(
        "/data/v3/ml/models/{modelId}/versions/{registryVersion}/benchmark",
        response_model=ModelRegistryVersionResponse,
        operation_id="data_update_model_benchmark",
    )
    def update_model_benchmark(
        body: ModelBenchmarkUpdateRequest,
        request: Request,
        model_id: Annotated[str, Path(alias="modelId")],
        registry_version: Annotated[int, Path(alias="registryVersion", ge=1)],
    ):
        _require_operator(request)
        with service.db.transaction() as connection:
            return to_wire(
                mlops.update_model_benchmark(
                    connection,
                    settings.project,
                    model_id,
                    registry_version,
                    body,
                    request.state.actor,
                )
            )

    @app.put(
        "/data/v3/ml/models/{modelId}/aliases/{alias}",
        response_model=ModelAliasResponse,
        operation_id="data_set_model_alias",
    )
    def set_alias(
        body: ModelAliasUpdateRequest,
        request: Request,
        model_id: Annotated[str, Path(alias="modelId")],
        alias: Annotated[
            str, Path(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
        ],
    ):
        _require_operator(request)
        with service.db.transaction() as connection:
            return to_wire(
                mlops.set_alias(
                    connection,
                    settings.project,
                    model_id,
                    alias,
                    body,
                    request.state.actor,
                )
            )

    @app.get(
        "/data/v3/ml/models/{modelId}/aliases/{alias}:resolve",
        response_model=ResolvedModelResponse,
        operation_id="data_resolve_model_alias",
    )
    def resolve_alias(
        request: Request,
        model_id: Annotated[str, Path(alias="modelId")],
        alias: Annotated[
            str, Path(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
        ],
    ):
        with service.db.transaction() as connection:
            return to_wire(
                mlops.resolve_alias(
                    connection,
                    settings.project,
                    model_id,
                    alias,
                    request.state.actor,
                )
            )
