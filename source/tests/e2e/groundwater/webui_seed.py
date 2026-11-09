"""Publish the minimal valid Data V12 formats required by browser acceptance."""
from __future__ import annotations

from base64 import b64decode
from io import BytesIO
import hashlib
import json
from pathlib import Path
import time

import httpx

from scripts.lib.e2e_http import public_api_headers
from scripts.lib.wire_json import from_wire_json, to_wire_json
import pyarrow as pa
import pyarrow.parquet as pq
from openpyxl import Workbook


PROJECT = "groundwater-demo"
HEADERS = {"X-Project-ID": PROJECT, "X-Client-Type": "cli"}
FIXTURE_ROOT = (
    Path(__file__).resolve().parents[3]
    / "packages/operator-pack/catalog/fixtures/data"
)


def _request(client: httpx.Client, method: str, path: str, **kwargs) -> dict:
    if "json" in kwargs and kwargs["json"] is not None:
        kwargs["json"] = to_wire_json(kwargs["json"])
    if "params" in kwargs and isinstance(kwargs["params"], dict):
        kwargs["params"] = to_wire_json(kwargs["params"])
    response = client.request(method, path, **kwargs)
    response.raise_for_status()
    return from_wire_json(response.json())


def _publish(
    client: httpx.Client,
    *,
    title: str,
    format_: str,
    media_type: str,
    content: bytes,
) -> None:
    accepted = _request(
        client,
        "POST",
        "/api/v12/data/publications",
        headers=HEADERS,
        json={
            "idempotency_key": f"v12-webui-{format_}",
            "outputs": [
                {
                    "name": "input",
                    "title": title,
                    "format": format_,
                    "media_type": media_type,
                    "policy": (
                        "FILE_QUERYABLE"
                        if format_ in {"csv", "parquet"}
                        else "FILE_ONLY"
                    ),
                    "tags": ["SYNTHETIC_TEST_ONLY", "v12-browser-acceptance"],
                    "semantics": {
                        "description": "v12 浏览器验收的最小合成夹具",
                        "fields": {},
                        "grain": None,
                        "time_range": None,
                        "timezone": None,
                        "crs": None,
                        "vertical_datum": None,
                        "limitations": ["SYNTHETIC_TEST_ONLY"],
                        "source": "v12-browser-acceptance",
                    },
                }
            ],
            "input_versions": [],
            "provenance": {"purpose": "v12-browser-acceptance"},
        },
    )
    publication_id = accepted["publication_id"]
    uploaded = client.put(
        f"/api/v12/data/publications/{publication_id}/items/input",
        headers={
            **HEADERS,
            "X-Content-SHA256": "sha256:" + hashlib.sha256(content).hexdigest(),
        },
        content=content,
    )
    uploaded.raise_for_status()
    _request(
        client,
        "POST",
        f"/api/v12/data/publications/{publication_id}/prepare",
        headers=HEADERS,
    )
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        state = _request(
            client,
            "GET",
            f"/api/v12/data/publications/{publication_id}",
            headers=HEADERS,
        )
        if state["state"] == "READY":
            break
        if state["state"] in {"FAILED", "ABORTED"}:
            raise AssertionError(state)
        time.sleep(0.1)
    else:
        raise AssertionError("WEBUI_DATA_PREPARE_TIMEOUT")
    _request(
        client,
        "POST",
        f"/api/v12/data/publications/{publication_id}/commit",
        headers=HEADERS,
    )


def _observations_csv() -> bytes:
    return (
        "well,time,hydraulic_head_m,x,y,elevation,depth\n"
        "w1,2025-01-01,81.2,500000,3500000,100,18.8\n"
        "w2,2025-01-01,82.4,501000,3501000,101,18.6\n"
    ).encode()


def _parquet() -> bytes:
    output = pa.BufferOutputStream()
    pq.write_table(pa.table({"well": ["w1", "w2"], "level": [81.2, 82.4]}), output)
    return output.getvalue().to_pybytes()


def _xlsx() -> bytes:
    output = BytesIO()
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "observations"
    sheet.append(["well", "level"])
    sheet.append(["w1", 81.2])
    sheet.append(["w2", 82.4])
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _geojson() -> bytes:
    return json.dumps(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
                    "properties": {"well": "w1", "level": 81.2},
                },
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [116.5, 40.0]},
                    "properties": {"well": "w2", "level": 82.4},
                },
            ],
        },
        ensure_ascii=False,
    ).encode()


def _geopackage() -> bytes:
    return (FIXTURE_ROOT / "point.gpkg").read_bytes()


def _geotiff() -> bytes:
    return (FIXTURE_ROOT / "raster.tif").read_bytes()


def _fixtures() -> dict[str, tuple[str, str, bytes]]:
    raster = _geotiff()
    return {
        "csv": ("六模型观测表", "text/csv", _observations_csv()),
        "parquet": ("浏览器验收 Parquet", "application/vnd.apache.parquet", _parquet()),
        "xlsx": (
            "浏览器验收工作簿",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            _xlsx(),
        ),
        "json": ("浏览器验收 JSON", "application/json", b'{"status":"ok","count":2}'),
        "pytorch-state-dict": (
            "浏览器验收模型权重",
            "application/vnd.pytorch",
            b"synthetic-state-dict-metadata-only",
        ),
        "geojson": ("浏览器验收 GeoJSON", "application/geo+json", _geojson()),
        "geopackage": (
            "浏览器验收 GeoPackage",
            "application/geopackage+sqlite3",
            _geopackage(),
        ),
        "geotiff": ("浏览器验收 GeoTIFF", "image/tiff", raster),
        "cog": (
            "浏览器验收 COG",
            "image/tiff",
            raster,
        ),
        "png": (
            "浏览器验收 PNG",
            "image/png",
            b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
                "/wcAAgAB/2lXWQAAAABJRU5ErkJggg=="
            ),
        ),
    }


def seed_browser_data(base_url: str) -> None:
    fixtures = _fixtures()
    with httpx.Client(
        base_url=base_url,
        timeout=300,
        trust_env=False,
        headers=public_api_headers(),
    ) as client:
        for format_, (title, media_type, content) in fixtures.items():
            params = {"format": format_, "limit": 20}
            if format_ == "csv":
                params["query"] = title
            items = _request(
                client,
                "GET",
                "/api/v12/data/datasets",
                params=params,
                headers=HEADERS,
            )["items"]
            if format_ == "csv":
                exists = any(item["title"] == title for item in items)
            else:
                exists = bool(items)
            if not exists:
                _publish(
                    client,
                    title=title,
                    format_=format_,
                    media_type=media_type,
                    content=content,
                )
    print("v12 WebUI Data fixtures PASS", flush=True)
