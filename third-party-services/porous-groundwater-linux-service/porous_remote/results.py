"""Read standard solver records and export plain PNG, VTU and PVD."""

from __future__ import annotations

from collections.abc import Iterator
import json
import math
from pathlib import Path
from struct import Struct, pack
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile
import zlib

from .packages import extract_sealed, write_json

HEAD = Struct("<iiff16siii")
CONCENTRATION = Struct("<iiif16siii")


def _frames(
    path: Path, header: Struct, dimensions: tuple[int, int, int]
) -> Iterator[tuple[float, int, tuple[float, ...]]]:
    layers, rows, columns = dimensions
    values = Struct("<" + str(rows * columns) + "f")
    with path.open("rb") as stream:
        while raw := stream.read(header.size):
            if len(raw) != header.size:
                raise ValueError("SOLVER_RECORD_TRUNCATED")
            _, _, _, time, _, cols, record_rows, layer = header.unpack(raw)
            if (record_rows, cols) != (rows, columns) or not 1 <= layer <= layers:
                raise ValueError("SOLVER_RECORD_GRID_MISMATCH")
            data = stream.read(values.size)
            if len(data) != values.size:
                raise ValueError("SOLVER_RECORD_TRUNCATED")
            yield time, layer, values.unpack(data)


def _read_result(source: Path, job: Path, key: bytes) -> tuple[Path, dict, dict]:
    result = job / "inputs"
    extract_sealed(source, result, "results", key)
    return (
        result,
        json.loads((result / "model_definition.json").read_bytes()),
        json.loads((result / "calculation_summary.json").read_bytes()),
    )


def _fields(
    result: Path, definition: dict,
) -> Iterator[tuple[str, Iterator[tuple[float, int, tuple[float, ...]]]]]:
    grid = definition["grid"]
    dims = grid["layers"], grid["rows"], grid["columns"]
    yield "head", _frames(result / "raw/model.hds", HEAD, dims)
    transport = result / "raw/transport.ucn"
    if transport.is_file():
        yield "concentration", _frames(transport, CONCENTRATION, dims)


def _active(definition: dict, result: Path) -> list:
    from gw_platform.model_builder import _layer_values

    grid = definition["grid"]
    return _layer_values(
        grid["active"], grid["layers"], grid["rows"] * grid["columns"], result, "active"
    )


def _valid(value: float, active: float, name: str, no_flow: float) -> bool:
    return (
        active > 0 and math.isfinite(value) and abs(value) < 1e20
        and (name != "head" or not math.isclose(value, no_flow, abs_tol=0.01))
    )


def analyze(source: Path, job: Path, key: bytes) -> dict:
    result, definition, summary = _read_result(source, job, key)
    active = _active(definition, result)
    no_flow = float(definition["properties"].get("noFlowHead", -999.99))
    table = []
    for name, frames in _fields(result, definition):
        for time, layer, values in frames:
            valid = [
                value for value, activity in zip(values, active[layer - 1], strict=True)
                if _valid(value, activity, name, no_flow)
            ]
            table.append({
                "quantity": name, "time": time, "layer": layer, "validCells": len(valid),
                "min": min(valid) if valid else None, "max": max(valid) if valid else None,
                "mean": math.fsum(valid) / len(valid) if valid else None,
            })
    write_json(job / "analysis_table.json", table)
    return {"metadata": {
        "units": definition["units"], "rows": len(table), "waterBalance": summary["waterBalance"],
        "concentrationUnit": definition["transport"].get("concentrationUnit"),
        "concentrationQuality": summary.get("transport", {}).get("quality"),
    }}


def _png(path: Path, width: int, height: int, pixels: bytes) -> None:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return pack(">I", len(data)) + kind + data + pack(">I", zlib.crc32(kind + data))

    stride = width * 3
    scanlines = b"".join(b"\x00" + pixels[i:i + stride] for i in range(0, len(pixels), stride))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(scanlines)) + chunk(b"IEND", b"")
    )


def _write_vtu(
    path: Path, result: Path, definition: dict, active: list, field: str, layers: dict
) -> None:
    from gw_platform.model_builder import _field_values, _layer_values

    grid = definition["grid"]
    rows, cols = grid["rows"], grid["columns"]
    tops = _field_values(grid["top"], rows * cols, result, "top")
    bottoms = _layer_values(grid["bottoms"], grid["layers"], rows * cols, result, "bottoms")
    width, height = grid["cellWidth"], grid["cellHeight"]
    angle = math.radians(grid.get("rotation", 0))
    cosine, sine = math.cos(angle), math.sin(angle)
    no_flow = float(definition["properties"].get("noFlowHead", -999.99))
    points, values = [], []
    for layer, (_, frame) in sorted(layers.items()):
        upper = tops if layer == 1 else bottoms[layer - 2]
        lower = bottoms[layer - 1]
        for index, value in enumerate(frame):
            if not _valid(value, active[layer - 1][index], field, no_flow):
                continue
            row, col = divmod(index, cols)
            for z in (lower[index], upper[index]):
                for y in ((rows - row - 1) * height, (rows - row) * height):
                    for x in (col * width, (col + 1) * width):
                        points.extend((
                            grid["xll"] + x * cosine - y * sine,
                            grid["yll"] + x * sine + y * cosine,
                            z,
                        ))
            values.append(value)
    count = len(values)

    def data(type_: str, name: str, content, components: int = 1) -> str:
        return (
            f'<DataArray type="{type_}" Name="{escape(name)}" '
            f'NumberOfComponents="{components}" format="ascii">'
            + " ".join(map(str, content)) + "</DataArray>"
        )

    path.write_text(
        '<VTKFile type="UnstructuredGrid" version="0.1" byte_order="LittleEndian">'
        f'<UnstructuredGrid><Piece NumberOfPoints="{count * 8}" NumberOfCells="{count}">'
        f'<Points>{data("Float64", "Points", points, 3)}</Points><Cells>'
        + data("Int32", "connectivity", (
            base + offset for base in range(0, count * 8, 8) for offset in (0, 1, 3, 2, 4, 5, 7, 6)
        ))
        + data("Int32", "offsets", range(8, (count + 1) * 8, 8))
        + data("UInt8", "types", (12 for _ in range(count)))
        + f'</Cells><CellData>{data("Float32", field, values)}</CellData>'
        '</Piece></UnstructuredGrid></VTKFile>',
        encoding="ascii",
    )


def render(source: Path, job: Path, key: bytes) -> dict:
    result, definition, _ = _read_result(source, job, key)
    grid = definition["grid"]
    active = _active(definition, result)
    no_flow = float(definition["properties"].get("noFlowHead", -999.99))
    panels, metadata, datasets = [], {}, []
    with ZipFile(job / "vtu_pvd_package.zip", "x", compression=ZIP_DEFLATED) as archive:
        for name, frames in _fields(result, definition):
            layers = {}
            for time, layer, values in frames:
                layers[layer] = (time, values)
            if not layers:
                raise ValueError("SOLVER_RECORDS_MISSING")
            time, values = layers[1]
            valid = [
                value for index, value in enumerate(values)
                if _valid(value, active[0][index], name, no_flow)
            ]
            lo, hi = (min(valid), max(valid)) if valid else (0, 0)
            pixels = bytearray()
            for index, value in enumerate(values):
                if not _valid(value, active[0][index], name, no_flow):
                    pixels.extend((225, 225, 225))
                else:
                    tone = round(255 * ((value - lo) / (hi - lo) if hi != lo else 0.5))
                    pixels.extend((tone, 75, 255 - tone))
            panels.append(bytes(pixels))
            metadata[name] = {
                "time": time, "pngLayer": 1, "min": lo if valid else None,
                "max": hi if valid else None,
            }
            vtu = job / f"{name}.vtu"
            _write_vtu(vtu, result, definition, active, name, layers)
            archive.write(vtu, vtu.name)
            datasets.append(
                f'<DataSet timestep="{time}" group="{name}" part="0" file="{vtu.name}"/>'
            )
        archive.writestr(
            "final.pvd", '<VTKFile type="Collection" version="0.1" byte_order="LittleEndian">'
            "<Collection>" + "".join(datasets) + "</Collection></VTKFile>",
        )
    stride = grid["columns"] * 3
    _png(
        job / "rendered_figure.png", grid["columns"] * len(panels), grid["rows"],
        b"".join(panel[row * stride:(row + 1) * stride] for row in range(grid["rows"]) for panel in panels),
    )
    return {"metadata": {"panels": metadata, "crs": definition.get("crs"), "finalFrames": True}}
