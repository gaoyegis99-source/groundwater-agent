"""Dispatch a Worker request while keeping the original operator path intact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from moperators.additional_implementations import IMPLEMENTATIONS
from moperators.operators.native import StagedInput
from moperators.runtime.task import _input_metadata, run_payload as run_original


def run_payload(payload: dict) -> dict:
    key = payload["operatorId"], payload["operatorVersion"]
    implementation = IMPLEMENTATIONS.get(key)
    if implementation is None:
        return run_original(payload)
    inputs = {
        port: [
            StagedInput(
                path=Path(item["path"]),
                media_type=item["mediaType"],
                format_id=item["format"],
                metadata=_input_metadata(item, set()),
            )
            for item in items
        ]
        for port, items in payload["inputs"].items()
    }
    return {
        "outputs": implementation(inputs, payload["parameters"], Path(payload["outputDirectory"]))
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("payload", type=Path)
    parser.add_argument("result", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.payload.read_text(encoding="utf-8"))
    try:
        result = run_payload(payload)
    except ValueError as error:
        code = str(error).split(":", 1)[0] or "OPERATOR_FAILED"
        result = {"error": {"code": code, "detail": f"Operator failed with {code}."}}
    args.result.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
