from fastapi import FastAPI

from ... import access
from ...catalog import service as catalog
from ...composition import Application
from ...errors import DataProblemError
from ...table_inspection import TableInspector
from ..models import ToolRequest
from ..responses import ToolResponse
from ..wire import page_with_semantic_ids, to_wire, with_semantic_id


def tool_arguments(body: dict, project: str) -> dict:
    if (
        not isinstance(body, dict)
        or set(body) != {"callId", "projectId", "arguments", "actor"}
        or body["projectId"] != project
        or not isinstance(body["callId"], str)
        or not body["callId"]
        or not isinstance(body["arguments"], dict)
    ):
        raise DataProblemError("TOOL_REQUEST_INVALID", 422)
    return body["arguments"]


def tool_actor(body: ToolRequest) -> dict:
    if body.actor is None:
        return access.INTERNAL_ACTOR
    return {
        "subject": body.actor.subject,
        "display_name": body.actor.subject,
        "client": "agent",
        "roles": body.actor.roles,
        "trace_id": body.call_id,
    }


def discovery_arguments(arguments: dict, *, tool: bool = False) -> dict:
    field_terms_key = "field_terms" if tool else "fieldTerms"
    allowed = {"query", "tags", "formats", field_terms_key, "units", "after", "limit"}
    if (
        not isinstance(arguments, dict)
        or set(arguments) - allowed
        or tool
        and "query" not in arguments
    ):
        raise DataProblemError("TOOL_ARGUMENTS_INVALID", 422)
    values = {
        "query": arguments.get("query", ""), "tags": arguments.get("tags", []),
        "formats": arguments.get("formats", []),
        "field_terms": arguments.get(field_terms_key, []), "units": arguments.get("units", []),
        "after": arguments.get("after"), "limit": arguments.get("limit", 20),
    }
    if (
        not isinstance(values["query"], str)
        or len(values["query"]) > 500
        or any(
            not isinstance(values[name], list) or len(values[name]) > 20
            or any(not isinstance(item, str) or not item for item in values[name])
            for name in ("tags", "formats", "field_terms", "units")
        )
        or values["after"] is not None
        and (not isinstance(values["after"], str) or not values["after"].isdigit())
        or isinstance(values["limit"], bool) or not isinstance(values["limit"], int)
        or not 1 <= values["limit"] <= 100
    ):
        raise DataProblemError("TOOL_ARGUMENTS_INVALID", 422)
    return values


def register_tool_routes(app: FastAPI, settings, service: Application) -> None:
    table_inspector = TableInspector(settings, service)

    @app.post("/internal/tools/v4/get_data_inventory", response_model=ToolResponse, operation_id="data_tool_inventory")
    def get_data_inventory(body: ToolRequest):
        arguments = tool_arguments(body.model_dump(by_alias=True), settings.project)
        if arguments:
            raise DataProblemError("TOOL_ARGUMENTS_INVALID", 422)
        with service.db.transaction() as connection:
            data = catalog.inventory(connection, settings.project, tool_actor(body))
            return to_wire({
                "data": data,
                "outcome": {
                    "outcome_code": "inventory.loaded",
                    "facts": {
                        "datasetCount": data["dataset_count"],
                        "describedDatasetCount": data["described_dataset_count"],
                    },
                },
            })

    @app.post("/internal/tools/v4/discover_datasets", response_model=ToolResponse, operation_id="data_tool_discovery")
    def discover_datasets(body: ToolRequest):
        arguments = discovery_arguments(tool_arguments(body.model_dump(by_alias=True), settings.project), tool=True)
        with service.db.transaction() as connection:
            value = catalog.discover_datasets(
                connection,
                settings.project,
                **arguments,
                actor=tool_actor(body),
            )
        value = page_with_semantic_ids(value, "dataset_id")
        for item in value["items"]:
            item["matched_fields"] = [
                with_semantic_id(field, "field_id")
                for field in item["matched_fields"]
            ]
        count = len(value["items"])
        outcome = (
            {"outcome_code": "candidates.empty", "facts": {}}
            if count == 0
            else {
                "outcome_code": "candidates.found",
                "facts": {
                    "candidateCount": count,
                    "totalMatches": value["total_matches"],
                },
            }
        )
        return to_wire({"data": value, "outcome": outcome})

    def inspect_version(body: ToolRequest, *, table_only: bool):
        arguments = tool_arguments(body.model_dump(by_alias=True), settings.project)
        if (
            set(arguments) != {"version_id"}
            or not isinstance(arguments["version_id"], str)
            or not arguments["version_id"]
        ):
            raise DataProblemError("TOOL_ARGUMENTS_INVALID", 422)
        with service.db.transaction() as connection:
            data = catalog.describe(
                connection,
                settings.project,
                arguments["version_id"],
                tool_actor(body),
            )
        if table_only and data["descriptor"].get("policy") != "FILE_QUERYABLE":
            return to_wire({
                "data": {"status": "NOT_QUERYABLE", "version_id": data["id"], "dataset_id": data["dataset_id"]},
                "outcome": {"outcome_code": "table.not_queryable", "facts": {}},
            })
        value = with_semantic_id(data, "version_id")
        value["objects"] = [with_semantic_id(item, "object_id") for item in value["objects"]]
        return to_wire({
            "data": {"status": "OK", **value},
            "outcome": {
                "outcome_code": "version.inspected",
                "facts": {"objectCount": len(value["objects"])},
            },
        })

    @app.post("/internal/tools/v4/inspect_data_version", response_model=ToolResponse, operation_id="data_tool_inspect_version")
    def inspect_data_version(body: ToolRequest):
        return inspect_version(body, table_only=False)

    @app.post("/internal/tools/v4/inspect_data_table", response_model=ToolResponse, operation_id="data_tool_inspect_table")
    def inspect_data_table(body: ToolRequest):
        wire = body.model_dump(by_alias=True)
        arguments = tool_arguments(wire, settings.project)
        if (
            set(arguments) != {"version_id"}
            or not isinstance(arguments["version_id"], str)
            or not arguments["version_id"]
        ):
            raise DataProblemError("TOOL_ARGUMENTS_INVALID", 422)
        data = table_inspector.inspect(
            call_id=wire["callId"],
            version_id=arguments["version_id"],
            actor=tool_actor(body),
        )
        if data["status"] == "NOT_QUERYABLE":
            return to_wire({
                "data": data,
                "outcome": {"outcome_code": "table.not_queryable", "facts": {}},
            })
        value = with_semantic_id(data, "version_id")
        value["objects"] = [
            with_semantic_id(item, "object_id") for item in value["objects"]
        ]
        return to_wire({
            "data": value,
            "outcome": {
                "outcome_code": "table.inspected",
                "facts": {
                    "fieldCount": len(value.get("fields", [])),
                    "sampleRowCount": len(value.get("sample_rows", [])),
                },
            },
        })
