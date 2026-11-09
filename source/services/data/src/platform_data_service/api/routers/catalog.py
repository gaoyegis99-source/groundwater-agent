from typing import Annotated

from fastapi import FastAPI, Path, Query, Request

from ...catalog import service as catalog
from ... import access
from ...composition import Application
from ...errors import DataProblemError
from ...formats import FORMAT_MEDIA_TYPES
from ...repositories import http_queries
from ..models import DiscoveryRequest, ResourceAccessUpdateRequest
from ..responses import (
    DatasetGovernanceResponse,
    DatasetPage,
    DiscoveryResponse,
    InventoryResponse,
    OperatorActivityPage,
    VersionPage,
    VersionResponse,
    ResourceAccessResponse,
)
from ..wire import fields_with_semantic_ids, page_with_semantic_ids, to_wire, with_semantic_id
from .tools import discovery_arguments


def register_catalog_routes(app: FastAPI, settings, service: Application) -> None:
    @app.get("/data/v3/datasets", response_model=DatasetPage, operation_id="data_list_datasets")
    def datasets(
        request: Request,
        query: str = Query(default="", max_length=500), tag: str | None = None,
        format: str | None = None, cursor: str = "",
        limit: int = Query(default=20, ge=1, le=100),
    ):
        if format is not None and format not in FORMAT_MEDIA_TYPES:
            raise DataProblemError("FORMAT_UNSUPPORTED", 422, format=format)
        with service.db.transaction() as connection:
            value = catalog.list_datasets(
                connection,
                settings.project,
                query,
                tag,
                format,
                cursor,
                limit,
                request.state.actor,
            )
        return to_wire(page_with_semantic_ids(value, "dataset_id"))

    @app.get("/data/v3/datasets/{datasetId}/versions", response_model=VersionPage, operation_id="data_list_dataset_versions")
    def versions(
        request: Request,
        dataset_id: Annotated[str, Path(alias="datasetId")],
        cursor: str = "",
        limit: int = Query(default=20, ge=1, le=100),
    ):
        with service.db.transaction() as connection:
            access.require_dataset(
                connection, settings.project, dataset_id, request.state.actor
            )
        value = http_queries.list_versions(service.db, settings.project, dataset_id, cursor, limit)
        return to_wire(page_with_semantic_ids(value, "version_id"))

    @app.get(
        "/data/v3/datasets/{datasetId}/access",
        response_model=ResourceAccessResponse,
        operation_id="data_get_resource_access",
    )
    def get_resource_access(
        request: Request,
        dataset_id: Annotated[str, Path(alias="datasetId")],
    ):
        with service.db.transaction() as connection:
            return to_wire(
                access.get_access(
                    connection, settings.project, dataset_id, request.state.actor
                )
            )

    @app.put(
        "/data/v3/datasets/{datasetId}/access",
        response_model=ResourceAccessResponse,
        operation_id="data_replace_resource_access",
    )
    def replace_resource_access(
        request: Request,
        body: ResourceAccessUpdateRequest,
        dataset_id: Annotated[str, Path(alias="datasetId")],
    ):
        with service.db.transaction() as connection:
            return to_wire(
                access.replace_access(
                    connection,
                    settings.project,
                    dataset_id,
                    visibility=body.visibility,
                    actor=request.state.actor,
                )
            )

    @app.get(
        "/data/v3/datasets/{datasetId}/governance",
        response_model=DatasetGovernanceResponse,
        operation_id="data_get_dataset_governance",
    )
    def governance(
        request: Request,
        dataset_id: Annotated[str, Path(alias="datasetId")],
    ):
        if "admin" not in request.state.actor["roles"]:
            raise DataProblemError("ROLE_FORBIDDEN", 403)
        return to_wire(
            http_queries.get_dataset_governance(
                service.db, settings.project, dataset_id
            )
        )

    @app.get(
        "/data/v3/activity",
        response_model=OperatorActivityPage,
        operation_id="data_list_operator_activity",
    )
    def activity(
        request: Request, limit: int = Query(default=30, ge=1, le=100)
    ):
        if "admin" not in request.state.actor["roles"]:
            raise DataProblemError("ROLE_FORBIDDEN", 403)
        return to_wire(
            http_queries.list_operator_activity(service.db, settings.project, limit)
        )

    @app.get("/data/v3/versions/{versionId}", response_model=VersionResponse, operation_id="data_get_version")
    def describe(
        request: Request,
        version_id: Annotated[str, Path(alias="versionId")],
    ):
        with service.db.transaction() as connection:
            value = catalog.describe(
                connection, settings.project, version_id, request.state.actor
            )
        value = with_semantic_id(value, "version_id")
        value["objects"] = [with_semantic_id(item, "object_id") for item in value["objects"]]
        value["descriptor"] = fields_with_semantic_ids(value["descriptor"])
        return to_wire(value)

    @app.get("/data/v3/inventory", response_model=InventoryResponse, operation_id="data_inventory")
    def data_inventory(request: Request):
        with service.db.transaction() as connection:
            return to_wire(
                catalog.inventory(
                    connection, settings.project, request.state.actor
                )
            )

    @app.post("/data/v3/discovery", response_model=DiscoveryResponse, operation_id="data_discovery")
    def data_discovery(body: DiscoveryRequest, request: Request):
        arguments = discovery_arguments(body.model_dump(by_alias=True))
        with service.db.transaction() as connection:
            value = catalog.discover_datasets(
                connection,
                settings.project,
                **arguments,
                actor=request.state.actor,
            )
        value = page_with_semantic_ids(value, "dataset_id")
        for item in value["items"]:
            item["matched_fields"] = [
                with_semantic_id(field, "field_id")
                for field in item["matched_fields"]
            ]
        return to_wire(value)
