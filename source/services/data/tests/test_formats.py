from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import io

import pyarrow as pa
import pyarrow.parquet as pq
from fastapi.testclient import TestClient

from platform_data_service.api.app import create_app
from platform_data_service.adapters.readers import CSVReader
from platform_data_service.errors import DataProblemError
from platform_data_service.models import Conversion
import pytest
from .test_service import start, upload, ready, publish, query


def test_parquet_physical_types_filters_and_order(client, worker):
    stream = io.BytesIO()
    pq.write_table(
        pa.table(
            {
                "well": ["001", "002", "003"],
                "value": pa.array([4, 2, 3], type=pa.int64()),
            }
        ),
        stream,
    )
    output = publish(
        client,
        worker,
        [
            {
                "name": "table",
                "title": "Parquet",
                "format": "parquet",
                "policy": "FILE_QUERYABLE",
            }
        ],
        stream.getvalue(),
    )
    result = query(
        client,
        worker,
        output["versionId"],
        fields=["f0", "f1"],
        filters=[{"field": "f1", "op": "ge", "value": 3}],
        sort=[{"field": "f1", "direction": "desc"}],
    )
    assert result["result"]["rows"] == [["001", 4], ["003", 3]]
    assert result["receipt"]["coverage"]["matchedRows"] == 2


def test_csv_date_error_and_explicit_nulls():
    reader = CSVReader()
    with pytest.raises(DataProblemError) as error:
        list(
            reader.read(
                io.BytesIO(b"date\n2024-02-30\n"), Conversion(types={"f0": "date"})
            )
        )
    assert error.value.details == {"row": 2, "field": "f0", "target_type": "date"}
    batches = list(
        reader.read(io.BytesIO(b"id,day\n001,\n"), Conversion(null_values=[""]))
    )
    assert batches[1].to_pydict() == {"f0": ["001"], "f1": [None]}
    with pytest.raises(DataProblemError, match="CSV_COLUMN_COUNT"):
        list(reader.read(io.BytesIO(b"x,y\n1\n"), Conversion()))


def test_csv_water_head_conversion_preserves_well_identifiers():
    raw = b"well,time,hydraulic_head_m\n001,2025-01-01,81.2\n002,2025-01-01,82.4\n"
    (_, schema), batch = CSVReader().read(
        io.BytesIO(raw), Conversion(types={"f2": "float64"})
    )
    assert schema.field("f2").type == pa.float64()
    assert batch.to_pydict() == {
        "f0": ["001", "002"],
        "f1": ["2025-01-01", "2025-01-01"],
        "f2": [81.2, 82.4],
    }


def test_commit_abort_race_has_one_winner(client, worker):
    pid = start(client)
    upload(client, pid)
    ready(client, worker, pid)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda action: client.post(f"/data/v3/publications/{pid}/{action}"),
                ["commit", "abort"],
            )
        )
    assert sorted(r.status_code for r in responses) == [200, 409]
    state = client.get(f"/data/v3/publications/{pid}").json()["state"]
    assert state in {"PUBLISHED", "ABORTED"}
    assert len(client.get("/data/v3/datasets").json()["items"]) == (
        1 if state == "PUBLISHED" else 0
    )


def test_server_readonly_is_independent_of_client_type(settings):
    with TestClient(create_app(replace(settings, read_only=True))) as client:
        for client_type in ["web", "agent", "cli"]:
            response = client.post(
                "/data/v3/publications", headers={"X-Client-Type": client_type}, json={}
            )
            assert response.status_code == 403
        capabilities = client.get("/data/v3/capabilities").json()
        assert capabilities["writeEnabled"] is False
        assert capabilities["permissions"]["managedPublicationWrite"] is False
        assert capabilities["permissions"]["semanticWrite"] is False
        assert capabilities["permissions"]["userUploadCreate"] is False


def test_history_semantics_and_object_version_binding(client, worker):
    first = publish(client, worker)
    second = publish(client, worker)
    descriptor = client.get("/data/v3/versions/" + first["versionId"]).json()
    oid = descriptor["objects"][0]["objectId"]
    assert (
        client.get(
            f"/data/v3/versions/{second['versionId']}/objects/{oid}/content"
        ).status_code
        == 404
    )
    response = client.post(
        "/data/v3/versions/" + first["versionId"] + "/semantics",
        json={
            "expectedSemanticId": first["semanticId"],
            "semantics": {"description": "new interpretation"},
        },
    )
    assert response.status_code == 200
    page = client.get(
        f"/data/v3/versions/{first['versionId']}/semantics?limit=1"
    )
    assert page.status_code == 200
    first_page = page.json()
    assert len(first_page["items"]) == 1
    assert first_page["nextCursor"]
    assert set(first_page["items"][0]) == {"semanticId", "createdAt", "actor"}
    second_page = client.get(
        f"/data/v3/versions/{first['versionId']}/semantics"
        f"?limit=1&cursor={first_page['nextCursor']}"
    ).json()
    assert len(second_page["items"]) == 1
    assert second_page["items"][0]["semanticId"] != first_page["items"][0]["semanticId"]
    assert second_page["nextCursor"] is None
    historical = client.get(
        f"/data/v3/versions/{first['versionId']}/semantics/{first['semanticId']}"
    )
    assert (
        historical.status_code == 200 and historical.json()["body"]["description"] == ""
    )
    result = query(
        client,
        worker,
        first["versionId"],
        semantic_ids={first["versionId"]: first["semanticId"]},
    )
    assert result["receipt"]["inputs"][0]["semanticId"] == first["semanticId"]
