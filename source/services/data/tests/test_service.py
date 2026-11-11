from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import sqlite3
import struct
import time
import threading
import zipfile

from openpyxl import Workbook
import pytest
from shapely import to_wkb
from shapely.geometry import Point

from platform_data_service.adapters.database import Database, identity
from platform_data_service.adapters.storage import LocalStore
from platform_data_service.operations.service import claim, finish
from platform_data_service.operations.worker import Worker
from platform_data_service import maintenance
from platform_protocol_contracts import to_camel


def start(client, outputs=None, key=None, **kwargs):
    response = client.post(
        "/data/v3/publications",
        json={
            "idempotencyKey": key or identity(),
            "outputs": outputs
            or [
                {
                    "name": "table",
                    "title": "监测数据",
                    "format": "csv",
                    "policy": "FILE_QUERYABLE",
                }
            ],
            **{to_camel(key): value for key, value in kwargs.items()},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["publicationId"]


def upload(client, pid, name="table", content=b"well,value\n001,2\n002,4\n"):
    response = client.put(
        f"/data/v3/publications/{pid}/items/{name}",
        content=content,
        headers={"X-Content-SHA256": "sha256:" + hashlib.sha256(content).hexdigest()},
    )
    assert response.status_code == 200, response.text
    return response.json()


def ready(client, worker, pid):
    response = client.post(f"/data/v3/publications/{pid}/prepare")
    assert response.status_code == 202, response.text
    worker.once()
    response = client.get(f"/data/v3/publications/{pid}").json()
    assert response["state"] == "READY", response


def publish(client, worker, outputs=None, content=None):
    pid = start(client, outputs)
    upload(client, pid, content=content or b"well,value\n001,2\n002,4\n")
    ready(client, worker, pid)
    response = client.post(f"/data/v3/publications/{pid}/commit")
    assert response.status_code == 200, response.text
    return response.json()["outputs"][0]


def query(client, worker, version, **kwargs):
    response = client.post(
        "/data/v3/queries",
        json={
            "idempotencyKey": identity(),
            "versionIds": [version],
            **{to_camel(key): value for key, value in kwargs.items()},
        },
    )
    assert response.status_code == 202, response.text
    qid = response.json()["queryId"]
    worker.once()
    result = client.get("/data/v3/queries/" + qid).json()
    assert result["state"] == "SUCCEEDED", result
    return result


def preview(client, worker, detail, kind, **selector):
    raw = next(item for item in detail["objects"] if item["role"] == "raw")
    body = {
        "idempotencyKey": identity(),
        "kind": kind,
        "versionId": detail["versionId"],
        "semanticId": detail["semanticId"],
        "objectId": raw["objectId"],
        **{to_camel(key): value for key, value in selector.items()},
    }
    response = client.post("/data/v3/previews", json=body)
    assert response.status_code == 202, response.text
    preview_id = response.json()["previewId"]
    worker.once()
    result = client.get(f"/data/v3/previews/{preview_id}").json()
    assert result["state"] == "SUCCEEDED", result
    return result


def workbook_bytes():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "观测"
    sheet.append(["井号", "水位", "公式"])
    sheet.append(["W01", 12.5, "=B2*2"])
    sheet.append(["W02", 13.0, None])
    hidden = workbook.create_sheet("内部")
    hidden.sheet_state = "hidden"
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def workbook_with_forbidden_member(name):
    stream = io.BytesIO(workbook_bytes())
    with zipfile.ZipFile(stream, "a") as archive:
        archive.writestr(name, b"forbidden")
    return stream.getvalue()


def workbook_with_external_relationship():
    stream = io.BytesIO(workbook_bytes())
    with zipfile.ZipFile(stream, "a") as archive:
        archive.writestr(
            "custom/_rels/source.rels",
            b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            b'<Relationship Id="rId1" Type="test" Target="https://example.invalid/data" TargetMode="External"/>'
            b"</Relationships>",
        )
    return stream.getvalue()


def geopackage_bytes(tmp_path):
    path = tmp_path / "points.gpkg"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        PRAGMA application_id=1196444487;
        CREATE TABLE gpkg_spatial_ref_sys (
          srs_name TEXT, srs_id INTEGER PRIMARY KEY, organization TEXT,
          organization_coordsys_id INTEGER, definition TEXT, description TEXT
        );
        INSERT INTO gpkg_spatial_ref_sys VALUES
          ('WGS 84',4326,'EPSG',4326,'EPSG:4326','');
        CREATE TABLE gpkg_contents (
          table_name TEXT PRIMARY KEY, data_type TEXT, identifier TEXT,
          description TEXT, last_change TEXT, min_x REAL, min_y REAL,
          max_x REAL, max_y REAL, srs_id INTEGER
        );
        INSERT INTO gpkg_contents VALUES
          ('wells','features','监测井','',CURRENT_TIMESTAMP,116,39,117,40,4326);
        CREATE TABLE gpkg_geometry_columns (
          table_name TEXT, column_name TEXT, geometry_type_name TEXT,
          srs_id INTEGER, z INTEGER, m INTEGER
        );
        INSERT INTO gpkg_geometry_columns VALUES ('wells','geom','POINT',4326,0,0);
        CREATE TABLE wells (id INTEGER PRIMARY KEY, name TEXT, value REAL, geom BLOB);
        """
    )
    for identifier, name, value, point in (
        (1, "W01", 12.5, Point(116.2, 39.8)),
        (2, "W02", 13.0, Point(116.4, 39.9)),
    ):
        geometry = b"GP" + bytes((0, 1)) + struct.pack("<i", 4326) + to_wkb(point)
        connection.execute(
            "INSERT INTO wells VALUES (?,?,?,?)",
            (identifier, name, value, geometry),
        )
    connection.commit()
    connection.close()
    return path.read_bytes()


def test_readiness_checks_database_and_object_store(client):
    response = client.get("/internal/health/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "components": {"database": "UP", "objectStore": "UP"},
    }


def test_group_is_atomic_and_replayable(client, worker):
    outputs = [
        {
            "name": "table",
            "title": "预测明细",
            "format": "csv",
            "policy": "FILE_QUERYABLE",
        },
        {
            "name": "scores",
            "title": "模型指标",
            "format": "json",
            "policy": "FILE_AND_METRICS",
        },
        {"name": "image", "title": "图", "format": "png", "policy": "FILE_ONLY"},
    ]
    pid = start(client, outputs, key="same-key")
    assert start(client, outputs, key="same-key") == pid
    assert (
        client.post(
            "/data/v3/publications",
            json={"idempotencyKey": "same-key", "outputs": outputs[:1]},
        ).status_code
        == 409
    )
    upload(client, pid)
    assert client.post(f"/data/v3/publications/{pid}/prepare").status_code == 409
    upload(
        client,
        pid,
        "scores",
        json.dumps(
            {
                "metrics": [
                    {
                        "name": "RMSE",
                        "definition": "sqrt(mean(square(error)))",
                        "unit": "m",
                        "evaluationScope": "same-wells-2024",
                        "value": 1.2,
                    }
                ]
            }
        ).encode(),
    )
    upload(client, pid, "image", b"png-example")
    ready(client, worker, pid)
    assert client.get("/data/v3/datasets").json()["items"] == []
    response = client.post(f"/data/v3/publications/{pid}/commit")
    assert response.status_code == 200
    assert client.post(f"/data/v3/publications/{pid}/commit").json() == response.json()
    assert len(client.get("/data/v3/datasets").json()["items"]) == 3
    assert client.post(f"/data/v3/publications/{pid}/abort").status_code == 409


def test_file_only_json_must_be_structurally_valid_before_ready(client, worker):
    output = [
        {
            "name": "table",
            "title": "JSON document",
            "format": "json",
            "policy": "FILE_ONLY",
        }
    ]
    invalid = start(client, output)
    upload(client, invalid, content=b'{"workflowId":"safe","workflowId":"overridden"}')
    operation = client.post(f"/data/v3/publications/{invalid}/prepare").json()
    worker.once()
    failed = client.get(f"/data/v3/publications/{invalid}").json()
    assert failed["state"] == "FAILED"
    error = client.get(
        f"/data/v3/operations/{operation['operationId']}"
    ).json()["error"]
    assert error["code"] == "JSON_DUPLICATE_KEY"
    assert error["details"] == {}
    assert client.post(f"/data/v3/publications/{invalid}/commit").status_code == 409

    valid = publish(client, worker, output, b'{"workflowId":"safe"}')
    detail = client.get(f"/data/v3/versions/{valid['versionId']}").json()
    assert detail["descriptor"]["quality"] == {
        "scope": "whole_file",
        "checks": ["utf8", "json_syntax", "duplicate_keys", "finite_numbers"],
    }


def test_table_query_preserves_ids_and_evidence(client, worker):
    out = publish(client, worker)
    detail = client.get("/data/v3/versions/" + out["versionId"]).json()
    assert detail["currentVersion"] == out["versionId"]
    assert detail["semantics"]["verticalDatum"] is None
    assert detail["descriptor"]["fields"][0]["type"] == "string"
    result = query(client, worker, out["versionId"])
    assert result["result"]["rows"] == [["001", "2"], ["002", "4"]]
    assert result["receipt"]["coverage"]["matchedRows"] == 2
    assert result["receipt"]["inputs"][0]["semanticId"] == out["semanticId"]
    qid = result["queryId"]
    first = client.get(f"/data/v3/queries/{qid}?limit=1").json()
    second = client.get(f"/data/v3/queries/{qid}?offset=1&limit=1").json()
    assert first["result"]["nextOffset"] == 1
    assert second["result"]["rows"] == [["002", "4"]]
    assert client.get(f"/data/v3/queries/{qid}/content").status_code == 200
    raw = next(x for x in detail["objects"] if x["role"] == "raw")
    download = client.get(
        f"/data/v3/versions/{out['versionId']}/objects/{raw['objectId']}/content"
    )
    assert download.content.startswith(b"well,value")
    assert download.headers["content-type"] == "application/octet-stream"


def test_object_content_supports_single_byte_ranges(client, worker):
    out = publish(client, worker)
    detail = client.get("/data/v3/versions/" + out["versionId"]).json()
    raw = next(item for item in detail["objects"] if item["role"] == "raw")
    path = f"/data/v3/versions/{out['versionId']}/objects/{raw['objectId']}/content"
    partial = client.get(path, headers={"Range": "bytes=0-3"})
    assert partial.status_code == 206
    assert partial.content == b"well"
    assert partial.headers["content-type"] == "application/octet-stream"
    assert partial.headers["content-range"] == f"bytes 0-3/{raw['size']}"
    assert partial.headers["accept-ranges"] == "bytes"
    assert client.get(path, headers={"Range": "bytes=0-1,3-4"}).status_code == 416


def test_xlsx_preview_is_bounded_and_keeps_formula_as_text(client, worker):
    out = publish(
        client,
        worker,
        [{"name": "table", "title": "工作簿", "format": "xlsx"}],
        workbook_bytes(),
    )
    detail = client.get("/data/v3/versions/" + out["versionId"]).json()
    result = preview(client, worker, detail, "xlsx_table")
    assert result["result"]["metadata"]["selected_sheet"] == "观测"
    assert [item["name"] for item in result["result"]["fields"]] == [
        "井号",
        "水位",
        "公式",
    ]
    assert result["result"]["rows"] == [
        ["W01", 12.5, "=B2*2"],
        ["W02", 13, None],
    ]
    assert result["receipt"]["input"]["checksum"].startswith("sha256:")


def test_geopackage_preview_returns_layers_table_and_wgs84_features(
    client, worker, tmp_path
):
    out = publish(
        client,
        worker,
        [{"name": "table", "title": "监测井", "format": "geopackage"}],
        geopackage_bytes(tmp_path),
    )
    detail = client.get("/data/v3/versions/" + out["versionId"]).json()
    result = preview(client, worker, detail, "geopackage_layer")
    page = result["result"]
    assert page["metadata"]["selected_layer"] == "wells"
    assert page["metadata"]["feature_count"] == 2
    assert page["metadata"]["geometry_types"] == ["Point"]
    assert page["metadata"]["crs"] == "EPSG:4326"
    assert page["metadata"]["bbox_wgs84"] == [116.2, 39.8, 116.4, 39.9]
    assert len(page["features"]) == 2
    assert page["features"][0]["geometry"]["coordinates"] == [116.2, 39.8]
    assert page["rows"][0][:3] == [1, "W01", 12.5]


def test_preview_exact_identity_idempotency_and_cancel(client, worker):
    out = publish(
        client,
        worker,
        [{"name": "table", "title": "工作簿", "format": "xlsx"}],
        workbook_bytes(),
    )
    detail = client.get("/data/v3/versions/" + out["versionId"]).json()
    raw = next(item for item in detail["objects"] if item["role"] == "raw")
    body = {
        "idempotencyKey": "same-preview",
        "kind": "xlsx_table",
        "versionId": detail["versionId"],
        "semanticId": detail["semanticId"],
        "objectId": raw["objectId"],
    }
    first = client.post("/data/v3/previews", json=body)
    assert first.status_code == 202
    assert client.post("/data/v3/previews", json=body).json() == first.json()
    preview_id = first.json()["previewId"]
    assert client.post(f"/data/v3/previews/{preview_id}/cancel").json()["state"] == "CANCELLED"
    assert not worker.once()
    assert client.post(
        "/data/v3/previews", json={**body, "sheetName": "观测"}
    ).status_code == 409
    assert client.post(
        "/data/v3/previews",
        json={**body, "idempotencyKey": "wrong", "semanticId": "missing"},
    ).status_code == 409


def test_preview_rejects_unsafe_workbooks_and_wrong_format(client, worker):
    for name, code in (
        ("xl/vbaProject.bin", "PREVIEW_MACRO_FORBIDDEN"),
        ("xl/externalLinks/externalLink1.xml", "PREVIEW_EXTERNAL_LINK_FORBIDDEN"),
    ):
        out = publish(
            client,
            worker,
            [{"name": "table", "title": "不安全工作簿", "format": "xlsx"}],
            workbook_with_forbidden_member(name),
        )
        detail = client.get("/data/v3/versions/" + out["versionId"]).json()
        raw = next(item for item in detail["objects"] if item["role"] == "raw")
        accepted = client.post(
            "/data/v3/previews",
            json={
                "idempotencyKey": identity(),
                "kind": "xlsx_table",
                "versionId": detail["versionId"],
                "semanticId": detail["semanticId"],
                "objectId": raw["objectId"],
            },
        ).json()
        worker.once()
        result = client.get("/data/v3/previews/" + accepted["previewId"]).json()
        assert result["error"]["code"] == code
    csv_out = publish(client, worker)
    csv_detail = client.get("/data/v3/versions/" + csv_out["versionId"]).json()
    raw = next(item for item in csv_detail["objects"] if item["role"] == "raw")
    assert client.post(
        "/data/v3/previews",
        json={
            "idempotencyKey": identity(),
            "kind": "xlsx_table",
            "versionId": csv_detail["versionId"],
            "semanticId": csv_detail["semanticId"],
            "objectId": raw["objectId"],
        },
    ).status_code == 409


def test_preview_rejects_external_relationships_outside_standard_link_folder(
    client, worker
):
    out = publish(
        client,
        worker,
        [{"name": "table", "title": "外部关系工作簿", "format": "xlsx"}],
        workbook_with_external_relationship(),
    )
    detail = client.get("/data/v3/versions/" + out["versionId"]).json()
    raw = next(item for item in detail["objects"] if item["role"] == "raw")
    accepted = client.post(
        "/data/v3/previews",
        json={
            "idempotencyKey": identity(),
            "kind": "xlsx_table",
            "versionId": detail["versionId"],
            "semanticId": detail["semanticId"],
            "objectId": raw["objectId"],
        },
    ).json()
    worker.once()
    failed = client.get("/data/v3/previews/" + accepted["previewId"]).json()
    assert failed["error"]["code"] == "PREVIEW_EXTERNAL_LINK_FORBIDDEN"


def test_preview_selector_and_expired_result_are_explicit(client, worker, settings):
    out = publish(
        client,
        worker,
        [{"name": "table", "title": "工作簿", "format": "xlsx"}],
        workbook_bytes(),
    )
    detail = client.get("/data/v3/versions/" + out["versionId"]).json()
    raw = next(item for item in detail["objects"] if item["role"] == "raw")
    accepted = client.post(
        "/data/v3/previews",
        json={
            "idempotencyKey": identity(),
            "kind": "xlsx_table",
            "versionId": detail["versionId"],
            "semanticId": detail["semanticId"],
            "objectId": raw["objectId"],
            "sheetName": "不存在",
        },
    ).json()
    worker.once()
    failed = client.get("/data/v3/previews/" + accepted["previewId"]).json()
    assert failed["error"]["code"] == "PREVIEW_SELECTOR_NOT_FOUND"
    succeeded = preview(client, worker, detail, "xlsx_table")
    with Database(settings.dsn).transaction() as c:
        c.execute(
            "UPDATE previews SET expires_at=now()-interval '1 second' WHERE id=%s",
            (succeeded["previewId"],),
        )
    expired = client.get("/data/v3/previews/" + succeeded["previewId"]).json()
    assert expired["receipt"] is not None
    assert expired["resultAvailable"] is False
    assert expired["result"] is None


def test_discovery_tool_rejects_null_optional_arguments(client, worker):
    publish(client, worker)
    response = client.post(
        "/internal/tools/v4/discover_datasets",
        json={
            "callId": "call-1",
            "projectId": "local-data",
            "arguments": {
                "query": "监测",
                "tags": None,
                "cursor": None,
                "limit": None,
            },
        },
    )
    assert response.status_code == 422, response.text


def test_inventory_and_structured_discovery_explain_current_catalog_matches(
    client, worker
):
    output = publish(client, worker)
    changed = client.post(
        "/data/v3/versions/" + output["versionId"] + "/semantics",
        json={
            "expectedSemanticId": output["semanticId"],
            "semantics": {
                "description": "地下水位月度监测",
                "fields": {"f1": {"meaning": "水位", "unit": "m"}},
                "timeRange": "2025-01/2025-12",
            },
        },
    )
    assert changed.status_code == 200, changed.text

    inventory = client.get("/data/v3/inventory").json()
    assert inventory["datasetCount"] == 1
    assert inventory["describedDatasetCount"] == 1
    assert inventory["formats"] == [
        {"name": "csv", "count": 1},
        {"name": "parquet", "count": 1},
    ]
    assert inventory["projectionBasis"] == "current_data_catalog"

    response = client.post(
        "/internal/tools/v4/discover_datasets",
        json={
            "callId": "discover-1",
            "projectId": "local-data",
            "arguments": {
                "query": "地下水 水位",
                "formats": ["csv"],
                "field_terms": ["f1"],
                "units": ["m"],
                "limit": 10,
            },
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["totalMatches"] == 1
    assert result["items"][0]["currentVersion"] == output["versionId"]
    assert result["items"][0]["matchedFields"] == [{
        "fieldId": "f1", "name": "value", "meaning": "水位", "unit": "m",
    }]
    assert "semantics" not in result["items"][0]
    assert "descriptor" not in result["items"][0]
    assert {item["kind"] for item in result["items"][0]["matchReasons"]} >= {
        "SEMANTICS", "FORMAT_FILTER", "FIELD_FILTER", "UNIT_FILTER"
    }
    assert result["matchBasis"] == "structured_current_catalog_projection.v1"
    public_result = client.post(
        "/data/v3/discovery",
        json={"query": "地下水 水位", "units": ["m"]},
    )
    assert public_result.status_code == 200, public_result.text
    public_item, = public_result.json()["items"]
    assert public_item["datasetId"] == result["items"][0]["datasetId"]
    assert public_item["currentVersion"] == output["versionId"]
    assert "descriptor" not in public_item and "semantics" not in public_item

    governance = client.get(
        f"/data/v3/datasets/{output['datasetId']}/governance"
    )
    assert governance.status_code == 200, governance.text
    governance_body = governance.json()
    assert governance_body["currentVersion"] == output["versionId"]
    assert governance_body["ownerSubject"]
    assert governance_body["qualityTrend"][-1]["versionId"] == output["versionId"]
    assert any(
        item["kind"] == "semantic_revision"
        for item in governance_body["activity"]
    )

    query_result = query(client, worker, output["versionId"])
    activity = client.get("/data/v3/activity")
    assert activity.status_code == 200, activity.text
    items = activity.json()["items"]
    assert items
    assert {item["service"] for item in items} == {"data"}
    publication_item = next(item for item in items if item["kind"] == "publication")
    assert publication_item["href"] == (
        "/data/?view=publication&publication=" + publication_item["operationId"]
    )
    prepare_item = next(item for item in items if item["kind"] == "prepare")
    assert prepare_item["href"] == publication_item["href"]
    query_item = next(item for item in items if item["kind"] == "query")
    assert query_item["operationId"] == query_result["queryId"]
    assert query_item["href"] == (
        "/data/?view=query&query=" + query_result["queryId"]
    )


def test_table_inspection_uses_query_worker_and_returns_bounded_sample(
    client, worker
):
    output = publish(
        client, worker,
        content=b"well,value\n001,2\n002,4\n003,6\n004,8\n005,10\n006,12\n",
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            client.post,
            "/internal/tools/v4/inspect_data_table",
            json={
                "callId": "inspect-table-sample",
                "projectId": "local-data",
                "arguments": {"version_id": output["versionId"]},
            },
        )
        for _ in range(200):
            if worker.once():
                break
            time.sleep(0.01)
        else:
            raise AssertionError("inspection query was not queued")
        response = pending.result(timeout=10)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "OK"
    assert data["versionId"] == output["versionId"]
    assert [field["name"] for field in data["fields"]] == ["well", "value"]
    assert data["sampleRows"] == [
        ["001", "2"], ["002", "4"], ["003", "6"], ["004", "8"],
        ["005", "10"],
    ]
    assert data["sampleLimit"] == 5
    assert data["sampleTruncated"] is True


def test_version_inspection_exposes_physical_field_id_at_the_tool_boundary(
    client, worker
):
    output = publish(client, worker)

    response = client.post(
        "/internal/tools/v4/inspect_data_version",
        json={
            "callId": "inspect-version-fields",
            "projectId": "local-data",
            "arguments": {"version_id": output["versionId"]},
        },
    )

    assert response.status_code == 200, response.text
    fields = response.json()["data"]["descriptor"]["fields"]
    assert fields[0]["fieldId"] == "f0"
    assert fields[0]["name"] == "well"
    assert "id" not in fields[0]


def test_discovery_rejects_unknown_or_malformed_filters(client):
    body = {
        "callId": "discover-invalid",
        "projectId": "local-data",
        "arguments": {"query": "x", "unexpected": True},
    }
    assert client.post(
        "/internal/tools/v4/discover_datasets", json=body
    ).status_code == 422
    assert client.post(
        "/data/v3/discovery", json={"tags": "not-a-list"}
    ).status_code == 422


@pytest.mark.parametrize(
    "path,arguments",
    [
        ("discover_datasets", {}),
        ("discover_datasets", {"query": "x" * 501}),
        ("inspect_data_version", {"version_id": ""}),
        ("inspect_data_table", {"version_id": ""}),
    ],
)
def test_agent_tool_routes_enforce_the_published_input_schema(
    client, path, arguments
):
    response = client.post(
        f"/internal/tools/v4/{path}",
        json={
            "callId": "schema-negative",
            "projectId": "local-data",
            "arguments": arguments,
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "TOOL_ARGUMENTS_INVALID"


def test_conversion_duplicates_aggregation_and_semantic_revision(client, worker):
    out = publish(
        client,
        worker,
        [
            {
                "name": "table",
                "title": "duplicates",
                "format": "csv",
                "policy": "FILE_QUERYABLE",
                "conversion": {"types": {"f1": "float64"}, "nullValues": [""]},
            }
        ],
        b"id,id\n001,2\n001,4\n002,\n",
    )
    result = query(
        client,
        worker,
        out["versionId"],
        group_by=["f0"],
        aggregates=[{"function": "avg", "field": "f1", "alias": "average"}],
    )
    assert result["result"]["rows"] == [["001", 3.0], ["002", None]]
    changed = client.post(
        "/data/v3/versions/" + out["versionId"] + "/semantics",
        json={
            "expectedSemanticId": out["semanticId"],
            "semantics": {"description": "地下水位", "fields": {"f1": {"unit": "m"}}},
        },
    )
    assert changed.status_code == 200
    assert client.get("/data/v3/datasets?query=地下水").json()["items"]
    assert result["receipt"]["inputs"][0]["semanticId"] == out["semanticId"]
    assert (
        client.post(
            "/data/v3/versions/" + out["versionId"] + "/semantics",
            json={"expectedSemanticId": out["semanticId"], "semantics": {}},
        ).status_code
        == 409
    )


def test_prepare_error_location_and_hidden_outputs(client, worker):
    pid = start(
        client,
        [
            {
                "name": "table",
                "title": "bad",
                "format": "csv",
                "policy": "FILE_QUERYABLE",
                "conversion": {"types": {"f1": "int64"}},
            }
        ],
    )
    upload(client, pid, content=b"x,y\n001,no\n")
    oid = client.post(f"/data/v3/publications/{pid}/prepare").json()["operationId"]
    worker.once()
    error = client.get("/data/v3/operations/" + oid).json()["error"]
    assert error["code"] == "CONVERSION_INVALID"
    assert error["details"]["row"] == 2 and error["details"]["field"] == "f1"
    assert client.get("/data/v3/datasets").json()["items"] == []
    assert client.post(f"/data/v3/publications/{pid}/commit").status_code == 409


def test_update_cas_and_old_versions(client, worker):
    first = publish(client, worker)
    pids = []
    for i in range(2):
        pid = start(
            client,
            [
                {
                    "name": "table",
                    "title": "updated",
                    "format": "csv",
                    "policy": "FILE_QUERYABLE",
                    "datasetId": first["datasetId"],
                    "expectedVersion": first["versionId"],
                }
            ],
        )
        upload(client, pid)
        ready(client, worker, pid)
        pids.append(pid)
    assert client.post(f"/data/v3/publications/{pids[0]}/commit").status_code == 200
    assert client.post(f"/data/v3/publications/{pids[1]}/commit").status_code == 409
    assert client.get("/data/v3/versions/" + first["versionId"]).status_code == 200
    assert (
        len(
            client.get("/data/v3/datasets/" + first["datasetId"] + "/versions").json()[
                "items"
            ]
        )
        == 2
    )


def test_fencing_restart_and_abort(client, worker, settings):
    pid = start(client)
    upload(client, pid)
    client.post(f"/data/v3/publications/{pid}/prepare")
    db = Database(settings.dsn)
    with db.transaction() as c:
        old = claim(c, settings.project, 30)
        c.execute(
            "UPDATE operations SET lease_until=now()-interval '1 second' WHERE id=%s",
            (old["id"],),
        )
    assert worker.once()
    with db.transaction() as c:
        assert not finish(c, old, result=[])
    assert client.get(f"/data/v3/publications/{pid}").json()["state"] == "READY"
    assert client.post(f"/data/v3/publications/{pid}/abort").status_code == 200
    assert client.post(f"/data/v3/publications/{pid}/commit").status_code == 409


def test_two_worker_slots_claim_distinct_operations(client, settings):
    operation_ids = []
    for index in range(2):
        publication_id = start(client, key=f"two-slot-{index}")
        upload(client, publication_id)
        operation_ids.append(
            client.post(f"/data/v3/publications/{publication_id}/prepare").json()[
                "operationId"
            ]
        )
    barrier = threading.Barrier(2)

    def claim_while_holding_transaction():
        with Database(settings.dsn).transaction() as connection:
            operation = claim(connection, settings.project, 30)
            barrier.wait(timeout=5)
            return operation["id"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(lambda _: claim_while_holding_transaction(), range(2)))

    assert set(claimed) == set(operation_ids)


def test_query_cancel_expiry_and_budget(client, worker, settings):
    out = publish(client, worker)
    body = {"idempotencyKey": "cancel-query", "versionIds": [out["versionId"]]}
    qid = client.post("/data/v3/queries", json=body).json()["queryId"]
    assert client.post("/data/v3/queries", json=body).json()["queryId"] == qid
    assert client.post(f"/data/v3/queries/{qid}/cancel").json()["state"] == "CANCELLED"
    assert not worker.once()
    limited = Worker(replace(settings, result_rows=1))
    result = query(client, limited, out["versionId"])
    assert result["result"]["truncated"] is True
    assert result["receipt"]["coverage"]["matchedRows"] == 2
    with Database(settings.dsn).transaction() as c:
        c.execute(
            "UPDATE queries SET expires_at=now()-interval '1 second' WHERE id=%s",
            (result["queryId"],),
        )
    expired = client.get("/data/v3/queries/" + result["queryId"]).json()
    assert expired["receipt"] and expired["result"] is None
    assert (
        client.get("/data/v3/queries/" + result["queryId"] + "/content").status_code
        == 410
    )
    qid = client.post(
        "/data/v3/queries", json={**body, "idempotencyKey": "tiny-budget"}
    ).json()["queryId"]
    Worker(replace(settings, result_bytes=10)).once()
    assert (
        client.get("/data/v3/queries/" + qid).json()["error"]["code"]
        == "QUERY_RESULT_BUDGET"
    )


def test_metric_compatibility(client, worker):
    versions = []
    for unit in ["m", "m", "cm"]:
        pid = start(
            client,
            [
                {
                    "name": "scores",
                    "title": "scores",
                    "format": "json",
                    "policy": "FILE_AND_METRICS",
                }
            ],
        )
        upload(
            client,
            pid,
            "scores",
            json.dumps(
                {
                    "metrics": [
                        {
                            "name": "RMSE",
                            "definition": "root mean squared error",
                            "unit": unit,
                            "evaluationScope": "all wells",
                            "value": 1.2,
                        }
                    ]
                }
            ).encode(),
        )
        ready(client, worker, pid)
        versions.append(
            client.post(f"/data/v3/publications/{pid}/commit").json()["outputs"][0][
                "versionId"
            ]
        )
    body = {
        "idempotencyKey": identity(),
        "kind": "metrics",
        "versionIds": versions[:2],
        "metricName": "RMSE",
    }
    qid = client.post("/data/v3/queries", json=body).json()["queryId"]
    worker.once()
    assert len(client.get("/data/v3/queries/" + qid).json()["result"]["rows"]) == 2
    response = client.post(
        "/data/v3/queries",
        json={**body, "idempotencyKey": identity(), "versionIds": versions},
    )
    assert (
        response.status_code == 409
        and response.json()["code"] == "METRIC_INCOMPATIBLE"
    )


def test_corruption_and_orphan_audit(client, worker, settings):
    out = publish(client, worker)
    detail = client.get("/data/v3/versions/" + out["versionId"]).json()
    store = LocalStore(settings.root)
    orphan = store.put(io.BytesIO(b"orphan"), 100)
    assert orphan["id"] in maintenance.audit(settings)["unreferenced_objects"]
    raw = next(x for x in detail["objects"] if x["role"] == "raw")
    store.path({"id": raw["objectId"]}).write_bytes(b"corrupt")
    assert (
        client.get(
            f"/data/v3/versions/{out['versionId']}/objects/{raw['objectId']}/content"
        ).status_code
        == 409
    )


def test_boundary_rejections(client, worker):
    assert (
        client.get("/data/v3/datasets", headers={"X-Project-ID": "another"}).status_code
        == 403
    )
    pid = start(client)
    assert (
        client.put(
            f"/data/v3/publications/{pid}/items/table",
            content=b"x",
            headers={"X-Content-SHA256": "0" * 64},
        ).status_code
        == 422
    )
    assert client.post("/data/v3/queries", json={"sql": "SELECT 1"}).status_code == 422
