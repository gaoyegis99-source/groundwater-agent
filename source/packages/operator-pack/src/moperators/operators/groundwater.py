from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from moperators.operators import darcy_velocity, hydraulic_gradient
from moperators.operators.groundwater_common import RasterInput


def run_groundwater_operator(
    operator_id: str,
    inputs: Mapping[str, Sequence[RasterInput]],
    parameters: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any] | None:
    if operator_id == "groundwater.drastic.evaluate":
        from moperators.operators import drastic

        return drastic.run(inputs, parameters, output_dir)
    if operator_id == "groundwater.hydraulic_gradient":
        return hydraulic_gradient.run(inputs["head"][0], parameters, output_dir)
    if operator_id == "groundwater.darcy_velocity":
        return darcy_velocity.run(
            inputs["gradientMagnitude"][0],
            inputs["flowDirection"][0],
            parameters,
            output_dir,
        )
    return None
