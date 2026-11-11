from __future__ import annotations

from pathlib import Path
from typing import Any

from moperators.operators.analysis_common import TableInput, field, number, rows, write


def run(source: TableInput, parameters: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    source_rows = rows(source)
    reference_field = parameters["referenceElevationField"]
    depth_field = parameters["depthToWaterField"]
    output_field = parameters.get("outputField", "hydraulic_head_m")
    factors = {"m": 1.0, "cm": 0.01, "mm": 0.001}
    try:
        reference_factor = factors[parameters["referenceElevationUnit"]]
        depth_factor = factors[parameters["depthToWaterUnit"]]
    except KeyError as exc:
        raise ValueError("UNIT_UNSUPPORTED") from exc
    converted: list[dict[str, Any]] = []
    for row in source_rows:
        if output_field in row:
            raise ValueError("OUTPUT_FIELD_CONFLICT")
        reference = number(field(row, reference_field)) * reference_factor
        depth = number(field(row, depth_field)) * depth_factor
        if depth < 0:
            raise ValueError("NEGATIVE_DEPTH")
        converted.append({**row, output_field: reference - depth})
    return {
        "heads": write(converted, output_dir / "heads.parquet"),
        "summary": {
            "kind": "value",
            "value": {
                "rowCount": len(converted),
                "outputField": output_field,
                "outputUnit": "m",
                "referenceElevationField": reference_field,
                "referenceElevationUnit": parameters["referenceElevationUnit"],
                "depthToWaterField": depth_field,
                "depthToWaterUnit": parameters["depthToWaterUnit"],
                "calculation": (
                    f"{output_field} = reference elevation - depth to water, after "
                    "converting both inputs to metres"
                ),
                "depthPositiveDirection": "downward from the reference surface",
                "resultKind": "COMPUTED_HEAD_ELEVATION",
                "interpretation": (
                    "Each output value is a computed hydraulic-head elevation in metres, "
                    "not a direct flow-velocity observation."
                ),
                "limitations": [
                    "Reference elevation and depth to water must use the same reference "
                    "surface and same vertical datum.",
                    "The calculation does not correct surveying, datum, casing-reference, "
                    "or depth-measurement errors.",
                ],
            },
        },
    }
