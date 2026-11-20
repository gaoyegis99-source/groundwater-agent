from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from platform_kernel.agent_runtime.observation_client import ObservationClient
from platform_kernel.agent_runtime.preparation_tool import build_preparation_tool
from platform_kernel.database import connect_database, utc_now
from platform_kernel.interaction.access import agent_owner_subject
from platform_kernel.interaction.persistence import create_session
from platform_kernel.settings_types import (
    ExecutableModelFieldRequirement,
    ExecutableModelSemanticRequirement,
)
from platform_kernel.tasks.preparation import get_preparation
from platform_kernel.workflow_selection import MODEL_FIELDS

SOURCE = Path(__file__).resolve().parents[2]
CHECKSUM = "sha256:" + "a" * 64
VERSION = {
    "kind": "data_version",
    "service": "data-primary",
    "dataset_id": "monitoring",
    "version_id": "observations",
    "semantic_id": "observations-semantics",
    "object_id": "observations-object",
    "media_type": "text/csv",
    "format": "csv",
    "size_bytes": 32,
    "checksum": CHECKSUM,
}
FIELDS = [
    {"fieldId": "id-station", "name": "station", "type": "string"},
    {"fieldId": "id-observed", "name": "observed", "type": "timestamp[ms]"},
    {"fieldId": "id-level", "name": "level", "type": "double"},
]
SEMANTICS = {
    "fields": {"id-level": {"meaning": "water_level_elevation", "unit": "m"}},
    "timeRange": "2020-01-01/2025-12-31",
    "timezone": "UTC",
}
PINNED = {
    "alpha": 0.05,
    "threshold": 3.5,
    "seasonalTrendEnabled": True,
    "thresholdEventsEnabled": True,
    "frequency": "month",
    "baselineStart": "2020-01-01",
    "baselineEnd": "2022-12-31",
    "comparisonStart": "2023-01-01",
    "comparisonEnd": "2025-12-31",
    "aggregation": "mean",
    "seasonFrequency": "month",
    "eventComparison": "above",
    "eventThreshold": 10.0,
}
REQUIRED = [
    "eventThreshold",
    "eventComparison",
    "groupField",
    "timeField",
    "valueField",
    "frequency",
    "expectedStart",
    "expectedEnd",
    "baselineStart",
    "baselineEnd",
    "comparisonStart",
    "comparisonEnd",
    "aggregation",
    "seasonFrequency",
]
METADATA = {
    "groupField": "GROUP_FIELD",
    "timeField": "TIME_FIELD",
    "valueField": "UNIT_FIELD",
    "expectedStart": "TIME_RANGE_START",
    "expectedEnd": "TIME_RANGE_END",
}


@pytest.fixture
def capability_preparation(kernel_settings):
    settings = kernel_settings()
    database = settings.data_root / "kernel.sqlite3"
    create_session(
        database, "session", settings.project_id, owner_subject="researcher-1"
    )
    with connect_database(database) as connection:
        connection.execute(
            "UPDATE sessions SET goal_revision=1 WHERE session_id='session'"
        )
        connection.execute(
            """INSERT INTO agent_runs(agent_run_id,session_id,project_id,goal_revision,
               provider,model,status,steps_json,input_tokens,output_tokens,total_tokens,
               trace_id,created_at,started_at)
               VALUES ('run','session',?,1,'fixed','model','RUNNING','[]',0,0,0,'trace',?,?)""",
            (settings.project_id, utc_now(), utc_now()),
        )
    document = json.loads(
        (
            SOURCE / "domains/groundwater/6.2.0/models/monitoring-trend.model.json"
        ).read_bytes()
    )
    model = replace(
        settings.models[0],
        model_id=document["modelId"],
        tool_name=document["tool"]["name"],
        input_schema=document["tool"]["inputSchema"],
        default_parameters=document["defaultParameters"],
        data_input_parameters=("versionId",),
        field_requirements=tuple(
            ExecutableModelFieldRequirement(
                parameter=item["parameter"],
                input_parameter=item["inputParameter"],
                identifier=item["identifier"],
                allowed_types=tuple(item["allowedTypes"]),
            )
            for item in document["fieldRequirements"]
        ),
        semantic_requirements=tuple(
            ExecutableModelSemanticRequirement(
                kind=item["kind"],
                input_parameter=item.get("inputParameter"),
                field_parameter=item.get("fieldParameter"),
                start_parameter=item.get("startParameter"),
                end_parameter=item.get("endParameter"),
            )
            for item in document["semanticRequirements"]
        ),
    )

    async def invoke(
        *,
        pinned=None,
        supplied=None,
        metadata=None,
        fields=None,
        semantics=None,
        checksum=CHECKSUM,
        attached=True,
        threshold_unit=None,
        conflicts=(),
    ):
        calls = []

        def inspect(request):
            calls.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "data": {
                        "versionId": VERSION["version_id"],
                        "datasetId": VERSION["dataset_id"],
                        "currentVersion": "unselected-newer-head",
                        "semanticId": VERSION["semantic_id"],
                        "descriptor": {"fields": FIELDS if fields is None else fields},
                        "semantics": SEMANTICS if semantics is None else semantics,
                        "objects": [
                            {
                                "objectId": VERSION["object_id"],
                                "role": "raw",
                                "mediaType": VERSION["media_type"],
                                "format": VERSION["format"],
                                "size": VERSION["size_bytes"],
                                "checksum": checksum,
                            }
                        ],
                    }
                },
            )

        parameters = PINNED if pinned is None else pinned
        sources = dict.fromkeys(parameters, "USER")
        sources.update(
            {
                name: "MODEL_DEFAULT"
                for name in ("alpha", "threshold")
                if name in parameters
            }
        )
        sources.update(
            {
                name: "WORKFLOW_PROFILE"
                for name in ("seasonalTrendEnabled", "thresholdEventsEnabled")
                if name in parameters
            }
        )
        selection = {
            **{name: getattr(model, name) for name in MODEL_FIELDS},
            "inputs": {"versionId": VERSION} if attached else {},
            "parameters": parameters,
            "business_capability": {
                "requiredParameters": [
                    {"name": name, "label": name} for name in REQUIRED
                ],
                "metadataParameters": METADATA if metadata is None else metadata,
                "requiredChecks": [
                    "FIELD_METADATA",
                    "FIELD_UNIT",
                    "TIME_FIELD",
                    "TIME_RANGE",
                ],
                "nonOverlappingTimeRanges": [["baselineStart", "comparisonStart"]],
                "parameterConflicts": list(conflicts),
                "thresholdUnit": threshold_unit,
                "thresholdParameter": "eventThreshold",
                "argumentSources": sources,
            },
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(inspect)) as client:
            tool = build_preparation_tool(
                database,
                observations=settings.observation_tools,
                services_by_name=settings.services_by_name,
                models=(model,),
                observation_client=ObservationClient(
                    client,
                    actor_subject_resolver=lambda run: agent_owner_subject(
                        database, run
                    ),
                ),
                workflow_selection=selection,
            )
            result = await tool.on_invoke_tool(
                SimpleNamespace(
                    context={
                        "project_id": settings.project_id,
                        "trace_id": "trace",
                        "agent_run_id": "run",
                        "governance": {"knowledge_evidence": []},
                    },
                    tool_call_id="preparation",
                ),
                json.dumps(
                    {
                        "target_tool": model.tool_name,
                        "arguments": {} if supplied is None else supplied,
                        "selection_policy": "CURRENT_REQUIRED",
                    }
                ),
            )
        persisted = get_preparation(database, "session", settings.project_id)
        return result, persisted, calls

    return lambda **kwargs: asyncio.run(invoke(**kwargs))


def test_capability_uses_one_exact_data_read_and_ignores_provider_guesses(
    capability_preparation,
):
    result, persisted, calls = capability_preparation(
        supplied={
            "versionId": "invented",
            "groupField": "bad",
            "eventThreshold": 999,
            "seasonalTrendEnabled": False,
            "frequency": "year",
        },
    )

    assert result["status"] == "READY", result
    assert result["selection_policy"] == "PINNED"
    assert result["data_references"] == {"versionId": VERSION}
    assert result["parameters"] == {
        **PINNED,
        "groupField": "station",
        "timeField": "observed",
        "valueField": "level",
        "expectedStart": "2020-01-01",
        "expectedEnd": "2025-12-31",
    }
    for name in METADATA:
        assert result["parameter_sources"][name] == "DATA"
    assert result["parameter_sources"]["eventThreshold"] == "USER"
    assert result["parameter_sources"]["seasonalTrendEnabled"] == "WORKFLOW_PROFILE"
    assert result["parameter_sources"]["alpha"] == "MODEL_DEFAULT"
    assert persisted["entries"][0]["parameters"] == result["parameters"]
    assert len(calls) == 1
    assert calls[0]["arguments"] == {"version_id": "observations"}
    assert calls[0]["actor"]["subject"] == "researcher-1"


def test_provider_cannot_fill_in_a_scientific_choice(capability_preparation):
    result, persisted, _ = capability_preparation(
        pinned={
            name: value for name, value in PINNED.items() if name != "eventThreshold"
        },
        supplied={"eventThreshold": 999},
    )

    assert result["status"] == "BLOCKED", result
    assert "eventThreshold" not in result["parameters"]
    assert result["blocking_issues"][0]["parameter"] == "eventThreshold"
    assert result["blocking_issues"][0]["kind"] == "MISSING_PARAMETER"
    assert persisted["status"] == "BLOCKED"


def test_declared_threshold_unit_accepts_its_chinese_equivalent(capability_preparation):
    result, _, _ = capability_preparation(threshold_unit="米")

    assert result["status"] == "READY", result
    assert any(
        check["kind"] == "THRESHOLD_UNIT" and check["status"] == "PASS"
        for check in result["checks"]
    )


@pytest.mark.parametrize(
    "changes,code,parameter",
    [
        ({"attached": False}, "PREPARATION_DATA_INPUT_MISSING", None),
        ({"checksum": "sha256:" + "b" * 64}, "WORKFLOW_SELECTION_DATA_CHANGED", None),
        (
            {
                "fields": [
                    *FIELDS,
                    {"fieldId": "id-well", "name": "well", "type": "string"},
                ]
            },
            "CAPABILITY_METADATA_AMBIGUOUS",
            "groupField",
        ),
        (
            {
                "semantics": {**SEMANTICS, "timeRange": None},
                "pinned": {
                    **PINNED,
                    "expectedStart": "2020-01-01",
                    "expectedEnd": "2025-12-31",
                },
            },
            "CAPABILITY_DATA_CHECK_REQUIRED",
            "timeField",
        ),
        (
            {
                "semantics": {**SEMANTICS, "fields": {}},
                "pinned": {**PINNED, "valueField": "level"},
            },
            "CAPABILITY_DATA_CHECK_REQUIRED",
            "valueField",
        ),
        (
            {"threshold_unit": "cm"},
            "CAPABILITY_THRESHOLD_UNIT_MISMATCH",
            "eventThreshold",
        ),
        (
            {"pinned": {**PINNED, "baselineEnd": "2023-01-01"}},
            "CAPABILITY_TIME_RANGES_OVERLAP",
            "comparisonStart",
        ),
        (
            {"conflicts": ["eventThreshold"]},
            "CAPABILITY_PARAMETER_CONFLICT",
            "eventThreshold",
        ),
    ],
)
def test_capability_blocks_unverified_inputs_and_parameters(
    capability_preparation,
    changes,
    code,
    parameter,
):
    result, _, calls = capability_preparation(**changes)

    assert result["status"] == "BLOCKED", result
    assert result["blocking_reasons"][0] == code
    assert result["blocking_issues"][0].get("parameter") == parameter
    if not changes.get("attached", True):
        assert calls == []
