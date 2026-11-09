from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from moperators.fixture_inspection import inspect_fixture
from moperators.operators.native import StagedInput
from moperators.static_implementations import execute


def run_payload(payload: dict[str, Any]) -> dict[str, Any]:
    os.environ["OGR_CURRENT_DATE"] = "1980-01-01T00:00:00.000Z"
    spatial_formats = {
        "geojson",
        "geopackage",
        "flatgeobuf",
        "geoparquet",
        "geotiff",
        "cog",
        "las",
        "laz",
        "copc",
    }
    inputs = {
        port: [
            StagedInput(
                path=Path(item["path"]),
                media_type=item["mediaType"],
                format_id=item["format"],
                metadata=_input_metadata(item, spatial_formats),
            )
            for item in items
        ]
        for port, items in payload["inputs"].items()
    }
    outputs = execute(
        payload["operatorId"],
        payload["operatorVersion"],
        inputs,
        payload["parameters"],
        Path(payload["outputDirectory"]),
    )
    return {"outputs": outputs}


def _input_metadata(item: dict[str, Any], spatial_formats: set[str]) -> dict[str, Any]:
    declared = item.get("metadata", {})
    if not isinstance(declared, dict):
        raise ValueError("INPUT_METADATA_INVALID")
    inspected = (
        inspect_fixture(Path(item["path"]), item["format"])
        if item["format"] in spatial_formats
        else {}
    )
    return {**declared, **inspected}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("payload")
    parser.add_argument("result")
    args = parser.parse_args()
    payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
    try:
        result = run_payload(payload)
    except ValueError as exc:
        code = str(exc).split(":", 1)[0] or "OPERATOR_FAILED"
        result = {"error": {"code": code, "detail": f"Operator failed with {code}."}}
    Path(args.result).write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
