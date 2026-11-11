"""Report numerical undershoot from raw solver output without modifying it."""

import math
from pathlib import Path

from .results import CONCENTRATION, _active, _frames, _valid


def concentration_quality(path: Path, definition: dict, project: Path) -> dict:
    grid = definition["grid"]
    dims = tuple(grid[name] for name in ("layers", "rows", "columns"))
    active = _active(definition, project)
    cinact = float(definition["transport"].get("mt3d", {}).get("btn", {}).get("cinact", -1e30))
    records = []
    for time, layer, values in _frames(path, CONCENTRATION, dims):
        valid = [(index, value) for index, (value, flag) in
                 enumerate(zip(values, active[layer - 1], strict=True))
                 if _valid(value, flag, "concentration", 0) and value != cinact]
        negative = [(index, value) for index, value in valid if value < 0]
        worst = min(negative, key=lambda pair: pair[1]) if negative else None
        records.append({
            "time": time, "layer": layer, "validCells": len(valid),
            "minimum": min((v for _, v in valid), default=None),
            "negativeCells": len(negative),
            "negativeMagnitudeSum": math.fsum(-v for _, v in negative),
            "worstCell": {"row": worst[0] // dims[2] + 1, "column": worst[0] % dims[2] + 1}
                         if worst else None,
        })
    if not records:
        raise ValueError("TRANSPORT_OUTPUT_EMPTY")
    count = sum(item["negativeCells"] for item in records)
    no_cross = definition["transport"].get("mt3d", {}).get("dsp", {}).get("noCross", False)
    warnings = []
    if count:
        warnings.append({"code": "NEGATIVE_CONCENTRATION", "message":
            "有效网格存在非物理负浓度；原始值保留，浓度解释及后续使用需复核。检查交叉弥散、网格及时间离散。"})
    if no_cross:
        warnings.append({"code": "CROSS_DISPERSION_DISABLED", "message":
            "显式启用了 NOCROSS 诊断，交叉弥散项被省略；结果不能视为原完整弥散模型的等价解。"})
    return {
        "schemaVersion": "porous-concentration-quality.v1",
        "status": "review_required" if warnings else "passed",
        "rawValuesModified": False, "crossDispersion": "disabled" if no_cross else "retained",
        "unit": definition["transport"].get("concentrationUnit"),
        "negativeCellRecords": count,
        "minimum": min((item["minimum"] for item in records if item["minimum"] is not None), default=None),
        "records": records, "warnings": warnings,
    }
