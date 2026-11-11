import struct

import pytest

from porous_remote.quality import concentration_quality


def test_quality_checks_all_times_and_masks_without_changing_raw(tmp_path, monkeypatch):
    monkeypatch.setattr("porous_remote.quality._active", lambda *_: [[1, 1, 0, 1]])
    definition = {"grid": {"layers": 1, "rows": 1, "columns": 4},
                  "transport": {"concentrationUnit": "mg/L", "mt3d": {"btn": {"cinact": -999}}}}
    raw = b"".join(struct.pack("<iiif16siii4f", 1, 1, 1, t, b"CONCENTRATION", 4, 1, 1, *values)
                   for t, values in [(1, [2, -.25, -10, -999]), (2, [2, 0, -10, -999])])
    path = tmp_path / "transport.ucn"
    path.write_bytes(raw)
    quality = concentration_quality(path, definition, tmp_path)
    assert quality["status"] == "review_required"
    assert quality["minimum"] == -.25
    assert quality["negativeCellRecords"] == 1
    assert quality["records"][0]["worstCell"] == {"row": 1, "column": 2}
    assert [r["validCells"] for r in quality["records"]] == [2, 2]
    assert quality["records"][-1]["negativeCells"] == 0
    assert path.read_bytes() == raw
    definition["transport"]["mt3d"]["dsp"] = {"noCross": True}
    quality = concentration_quality(path, definition, tmp_path)
    assert {w["code"] for w in quality["warnings"]} == {"NEGATIVE_CONCENTRATION", "CROSS_DISPERSION_DISABLED"}


def test_empty_concentration_is_not_quality_passed(tmp_path, monkeypatch):
    monkeypatch.setattr("porous_remote.quality._active", lambda *_: [[1]])
    path = tmp_path / "empty.ucn"; path.write_bytes(b"")
    with pytest.raises(ValueError, match="TRANSPORT_OUTPUT_EMPTY"):
        concentration_quality(path, {"grid": {"layers": 1, "rows": 1, "columns": 1}, "transport": {}}, tmp_path)
