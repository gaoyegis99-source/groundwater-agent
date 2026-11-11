from datetime import datetime
import hashlib
import io
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

from platform_data_service.ingestion.field_profile import TableFieldProfiler


def _parquet_profile_fixture() -> bytes:
    timezone = ZoneInfo("Asia/Shanghai")
    table = pa.Table.from_arrays(
        [
            pa.array(["001", "002", "003", "004", "005", "006"]),
            pa.array(["12.5", "13.75", "14", "15.25", "16", None]),
            pa.array([12.5, 13.75, None, 15.25, 16.0, 17.5]),
            pa.array(
                [
                    datetime(2026, 1, day, 8, tzinfo=timezone)
                    for day in range(1, 7)
                ],
                type=pa.timestamp("us", tz="Asia/Shanghai"),
            ),
        ],
        names=["well_id", "level_text", "level", "observed_at"],
    )
    stream = io.BytesIO()
    pq.write_table(table, stream)
    return stream.getvalue()


def _publish_profile_fixture(client, worker) -> dict:
    content = _parquet_profile_fixture()
    response = client.post(
        "/data/v3/publications",
        json={
            "idempotencyKey": "profile-publication",
            "outputs": [
                {
                    "name": "table",
                    "title": "Profiled observations",
                    "format": "parquet",
                    "policy": "FILE_QUERYABLE",
                    "semantics": {
                        "description": "Six groundwater observations",
                        "timezone": "Asia/Shanghai",
                        "timeRange": "2026-01-01/2026-01-06",
                        "fields": {
                            "f0": {"meaning": "Well identifier"},
                            "f1": {"meaning": "Unconverted water level", "unit": "m"},
                            "f2": {"meaning": "Water level", "unit": "m"},
                            "f3": {"meaning": "Observation time"},
                        },
                    },
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    publication_id = response.json()["publicationId"]
    response = client.put(
        f"/data/v3/publications/{publication_id}/items/table",
        content=content,
        headers={
            "X-Content-SHA256": "sha256:" + hashlib.sha256(content).hexdigest()
        },
    )
    assert response.status_code == 200, response.text
    response = client.post(f"/data/v3/publications/{publication_id}/prepare")
    assert response.status_code == 202, response.text
    assert worker.once() is True
    response = client.post(f"/data/v3/publications/{publication_id}/commit")
    assert response.status_code == 200, response.text
    version_id = response.json()["outputs"][0]["versionId"]
    response = client.get(f"/data/v3/versions/{version_id}")
    assert response.status_code == 200, response.text
    return response.json()


def test_queryable_ingestion_persists_bounded_whole_table_field_profile(
    client, worker
):
    detail = _publish_profile_fixture(client, worker)
    descriptor = detail["descriptor"]

    assert descriptor["profile"] == {
        "schemaVersion": "field-profile.v1",
        "statisticsScope": "all_rows",
        "sampleScope": "first_non_null_values",
        "sampleLimitPerField": 5,
    }
    fields = {field["name"]: field for field in descriptor["fields"]}

    assert fields["well_id"]["profile"] == {
        "nonNullCount": 6,
        "sampleValues": ["001", "002", "003", "004", "005"],
        "numericString": {
            "checkedCount": 6,
            "parsedCount": 6,
            "parseSuccessRate": 1.0,
            "identifierLike": True,
        },
    }
    assert fields["level_text"]["nullCount"] == 1
    assert fields["level_text"]["profile"]["numericString"] == {
        "checkedCount": 5,
        "parsedCount": 5,
        "parseSuccessRate": 1.0,
        "identifierLike": False,
        "suggestedType": "float64",
    }
    assert fields["level"]["profile"] == {
        "nonNullCount": 5,
        "sampleValues": [12.5, 13.75, 15.25, 16.0, 17.5],
        "valueRange": {"minimum": 12.5, "maximum": 17.5},
    }
    assert fields["observed_at"]["profile"]["temporal"] == {
        "kind": "timestamp",
        "checkedCount": 6,
        "parsedCount": 6,
        "parseSuccessRate": 1.0,
        "physicalTimezone": "Asia/Shanghai",
    }
    assert fields["observed_at"]["profile"]["valueRange"] == {
        "minimum": "2026-01-01T08:00:00+08:00",
        "maximum": "2026-01-06T08:00:00+08:00",
    }


def test_exact_version_tool_reuses_the_persisted_profile(client, worker):
    detail = _publish_profile_fixture(client, worker)

    response = client.post(
        "/internal/tools/v4/inspect_data_version",
        json={
            "callId": "inspect-persisted-profile",
            "projectId": "local-data",
            "arguments": {"version_id": detail["versionId"]},
        },
    )

    assert response.status_code == 200, response.text
    descriptor = response.json()["data"]["descriptor"]
    assert descriptor["profile"]["statisticsScope"] == "all_rows"
    assert descriptor["fields"][1]["profile"]["numericString"][
        "suggestedType"
    ] == "float64"
    assert descriptor["fields"][2]["profile"]["valueRange"] == {
        "minimum": 12.5,
        "maximum": 17.5,
    }


def test_numeric_string_hint_does_not_recommend_a_lossy_float_conversion():
    schema = pa.schema([pa.field("measurement", pa.string())])
    profiler = TableFieldProfiler(schema)
    profiler.consume(
        pa.RecordBatch.from_arrays(
            [pa.array(["123456789012345678901.25"])],
            schema=schema,
        )
    )

    evidence = profiler.field(0)["profile"]["numeric_string"]
    assert evidence["parsed_count"] == 1
    assert "suggested_type" not in evidence
