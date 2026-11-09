from __future__ import annotations

from math import hypot
from pathlib import Path
from struct import pack, unpack_from
from typing import Any

import numpy as np
import rasterio
from rasterio.windows import Window

D8 = (
    (1, 0, 1),
    (2, 1, 1),
    (4, 1, 0),
    (8, 1, -1),
    (16, 0, -1),
    (32, -1, -1),
    (64, -1, 0),
    (128, -1, 1),
)
D8_OFFSETS = {code: (row, column) for code, row, column in D8}
VALID_DIRECTION_CODES = np.array([0, 1, 2, 4, 8, 16, 32, 64, 128, 255], dtype=np.uint8)


def _profile(source: rasterio.DatasetReader, dtype: str, nodata: int) -> dict[str, object]:
    profile = source.profile.copy()
    profile.update(driver="GTiff", count=1, dtype=dtype, nodata=nodata, compress="deflate")
    return profile


def _write_memmap(
    output: Path,
    source: rasterio.DatasetReader,
    values: np.memmap,
    dtype: str,
    nodata: int,
) -> None:
    with rasterio.open(output, "w", **_profile(source, dtype, nodata)) as target:
        for _, window in target.block_windows(1):
            rows, columns = window.toslices()
            target.write(values[rows, columns], 1, window=window)


def flow_direction(dem: Path, output: Path, band: int) -> None:
    with rasterio.open(dem) as source:
        profile = _profile(source, "uint8", 255)
        with rasterio.open(output, "w", **profile) as target:
            for row in range(0, source.height, 512):
                for column in range(0, source.width, 512):
                    height = min(512, source.height - row)
                    width = min(512, source.width - column)
                    expanded = Window.from_slices(
                        (row - 1, row + height + 1),
                        (column - 1, column + width + 1),
                        boundless=True,
                    )
                    elevation = source.read(
                        band,
                        window=expanded,
                        boundless=True,
                        masked=True,
                    )
                    mask = np.ma.getmaskarray(elevation)
                    data = np.asarray(elevation.data, dtype=np.float64)
                    core = data[1 : height + 1, 1 : width + 1]
                    core_mask = mask[1 : height + 1, 1 : width + 1]
                    if np.any(~core_mask & ~np.isfinite(core)):
                        raise ValueError("RASTER_INVALID")
                    valid = ~core_mask
                    boundary = np.zeros((height, width), dtype=bool)
                    best_slope = np.zeros((height, width), dtype=np.float64)
                    result = np.full((height, width), 255, dtype=np.uint8)
                    result[valid] = 0
                    for code, row_offset, column_offset in D8:
                        neighbor = data[
                            1 + row_offset : height + 1 + row_offset,
                            1 + column_offset : width + 1 + column_offset,
                        ]
                        neighbor_mask = mask[
                            1 + row_offset : height + 1 + row_offset,
                            1 + column_offset : width + 1 + column_offset,
                        ]
                        boundary |= valid & neighbor_mask
                        delta_x = (
                            source.transform.a * column_offset + source.transform.b * row_offset
                        )
                        delta_y = (
                            source.transform.d * column_offset + source.transform.e * row_offset
                        )
                        distance = hypot(delta_x, delta_y)
                        slope = np.divide(
                            core - neighbor,
                            distance,
                            out=np.zeros_like(core),
                            where=valid & ~neighbor_mask,
                        )
                        better = slope > best_slope
                        result[better] = code
                        best_slope[better] = slope[better]
                    unresolved = valid & (result == 0) & ~boundary
                    if np.any(unresolved):
                        raise ValueError("DEM_NOT_CONDITIONED")
                    target.write(
                        result,
                        1,
                        window=Window.from_slices(
                            (row, row + height),
                            (column, column + width),
                        ),
                    )


def _load_direction(path: Path, work_dir: Path) -> tuple[rasterio.DatasetReader, np.memmap]:
    source = rasterio.open(path)
    direction = np.memmap(
        work_dir / "direction.dat",
        mode="w+",
        dtype=np.uint8,
        shape=(source.height, source.width),
    )
    for _, window in source.block_windows(1):
        rows, columns = window.toslices()
        values = source.read(1, window=window, masked=True)
        data = np.asarray(values.data)
        mask = np.ma.getmaskarray(values)
        if np.any(
            ~mask
            & (
                ~np.isfinite(data)
                | (data != np.floor(data))
                | ~np.isin(data, VALID_DIRECTION_CODES[:-1])
            )
        ):
            source.close()
            raise ValueError("FLOW_DIRECTION_INVALID")
        converted = data.astype(np.uint8, copy=False)
        direction[rows, columns] = np.where(mask, 255, converted)
    direction.flush()
    return source, direction


def _source_target_slices(
    rows: int, columns: int, row_offset: int, column_offset: int
) -> tuple[tuple[slice, slice], tuple[slice, slice]]:
    source_rows = slice(max(0, -row_offset), min(rows, rows - row_offset))
    source_columns = slice(max(0, -column_offset), min(columns, columns - column_offset))
    target_rows = slice(max(0, row_offset), min(rows, rows + row_offset))
    target_columns = slice(max(0, column_offset), min(columns, columns + column_offset))
    return (source_rows, source_columns), (target_rows, target_columns)


def _downstream_index(index: int, code: int, columns: int) -> int:
    row_offset, column_offset = D8_OFFSETS[code]
    return index + row_offset * columns + column_offset


def _topology(direction: np.memmap, work_dir: Path) -> tuple[np.memmap, np.memmap, int]:
    rows, columns = direction.shape
    indegree = np.memmap(
        work_dir / "indegree.dat", mode="w+", dtype=np.uint8, shape=direction.shape
    )
    indegree[:] = 0
    for code, row_offset, column_offset in D8:
        source_slice, target_slice = _source_target_slices(rows, columns, row_offset, column_offset)
        active = direction[source_slice] == code
        if int(np.count_nonzero(active)) != int(np.count_nonzero(direction == code)):
            raise ValueError("FLOW_DIRECTION_INVALID")
        if np.any(active & (direction[target_slice] == 255)):
            raise ValueError("FLOW_DIRECTION_INVALID")
        indegree[target_slice] += active.astype(np.uint8)

    valid_count = int(np.count_nonzero(direction != 255))
    queue = np.memmap(
        work_dir / "queue.dat", mode="w+", dtype=np.int64, shape=(max(valid_count, 1),)
    )
    tail = 0
    flat_direction = direction.reshape(-1)
    flat_indegree = indegree.reshape(-1)
    for start in range(0, flat_direction.size, 1_000_000):
        stop = min(start + 1_000_000, flat_direction.size)
        seeds = np.flatnonzero(
            (flat_direction[start:stop] != 255) & (flat_indegree[start:stop] == 0)
        )
        queue[tail : tail + seeds.size] = seeds + start
        tail += int(seeds.size)

    head = 0
    while head < tail:
        index = int(queue[head])
        head += 1
        code = int(flat_direction[index])
        if code == 0:
            continue
        downstream = _downstream_index(index, code, columns)
        flat_indegree[downstream] -= 1
        if flat_indegree[downstream] == 0:
            queue[tail] = downstream
            tail += 1
    if tail != valid_count:
        raise ValueError("FLOW_CYCLE")
    queue.flush()
    return indegree, queue, valid_count


def flow_accumulation(direction_path: Path, output: Path, work_dir: Path) -> None:
    source, direction = _load_direction(direction_path, work_dir)
    try:
        indegree, queue, valid_count = _topology(direction, work_dir)
        del indegree
        accumulation = np.memmap(
            work_dir / "accumulation.dat",
            mode="w+",
            dtype=np.uint64,
            shape=direction.shape,
        )
        accumulation[:] = direction != 255
        flat_direction = direction.reshape(-1)
        flat_accumulation = accumulation.reshape(-1)
        for position in range(valid_count):
            index = int(queue[position])
            code = int(flat_direction[index])
            if code:
                downstream = _downstream_index(index, code, source.width)
                flat_accumulation[downstream] += flat_accumulation[index]
        accumulation.flush()
        _write_memmap(output, source, accumulation, "uint64", 0)
    finally:
        source.close()


def stream_extract(accumulation_path: Path, output: Path, minimum_cells: int) -> None:
    with (
        rasterio.open(accumulation_path) as source,
        rasterio.open(output, "w", **_profile(source, "uint8", 255)) as target,
    ):
        for _, window in source.block_windows(1):
            values = source.read(1, window=window, masked=True)
            data = np.asarray(values.data)
            mask = np.ma.getmaskarray(values)
            if np.any(~mask & (~np.isfinite(data) | (data < 1) | (data != np.floor(data)))):
                raise ValueError("ACCUMULATION_INVALID")
            streams = np.full(data.shape, 255, dtype=np.uint8)
            streams[~mask] = (data[~mask] >= minimum_cells).astype(np.uint8)
            target.write(streams, 1, window=window)


def stream_order(direction_path: Path, streams_path: Path, output: Path, work_dir: Path) -> None:
    source, direction = _load_direction(direction_path, work_dir)
    try:
        streams = np.memmap(
            work_dir / "streams.dat", mode="w+", dtype=np.uint8, shape=direction.shape
        )
        with rasterio.open(streams_path) as stream_source:
            for _, window in stream_source.block_windows(1):
                rows, columns = window.toslices()
                values = stream_source.read(1, window=window, masked=True)
                data = np.asarray(values.data)
                mask = np.ma.getmaskarray(values)
                if np.any(~mask & ~np.isin(data, [0, 1])):
                    raise ValueError("STREAM_MASK_INVALID")
                streams[rows, columns] = np.where(mask, 255, data).astype(np.uint8)
        if np.any((direction == 255) != (streams == 255)):
            raise ValueError("STREAM_MASK_INVALID")
        indegree, queue, valid_count = _topology(direction, work_dir)
        del indegree
        order = np.memmap(
            work_dir / "stream-order.dat",
            mode="w+",
            dtype=np.uint16,
            shape=direction.shape,
        )
        order[:] = np.where(direction == 255, 65535, np.where(streams == 1, 1, 0))
        maximum = np.memmap(
            work_dir / "stream-max.dat", mode="w+", dtype=np.uint16, shape=direction.shape
        )
        count = np.memmap(
            work_dir / "stream-count.dat", mode="w+", dtype=np.uint8, shape=direction.shape
        )
        maximum[:] = 0
        count[:] = 0
        flat_direction = direction.reshape(-1)
        flat_streams = streams.reshape(-1)
        flat_order = order.reshape(-1)
        flat_maximum = maximum.reshape(-1)
        flat_count = count.reshape(-1)
        for position in range(valid_count):
            index = int(queue[position])
            code = int(flat_direction[index])
            if not code:
                continue
            downstream = _downstream_index(index, code, source.width)
            if flat_streams[index] != 1 or flat_streams[downstream] != 1:
                continue
            value = int(flat_order[index])
            if value > flat_maximum[downstream]:
                flat_maximum[downstream] = value
                flat_count[downstream] = 1
            elif value == flat_maximum[downstream]:
                flat_count[downstream] += 1
            flat_order[downstream] = flat_maximum[downstream] + (
                1 if flat_count[downstream] >= 2 else 0
            )
        order.flush()
        _write_memmap(output, source, order, "uint16", 65535)
    finally:
        source.close()


def basins(direction_path: Path, output: Path, work_dir: Path) -> None:
    source, direction = _load_direction(direction_path, work_dir)
    try:
        indegree, queue, valid_count = _topology(direction, work_dir)
        del indegree
        labels = np.memmap(
            work_dir / "basins.dat", mode="w+", dtype=np.uint32, shape=direction.shape
        )
        labels[:] = 0
        flat_direction = direction.reshape(-1)
        flat_labels = labels.reshape(-1)
        outlets = np.flatnonzero(flat_direction == 0)
        flat_labels[outlets] = np.arange(1, outlets.size + 1, dtype=np.uint32)
        for position in range(valid_count - 1, -1, -1):
            index = int(queue[position])
            code = int(flat_direction[index])
            if code:
                flat_labels[index] = flat_labels[_downstream_index(index, code, source.width)]
        labels.flush()
        _write_memmap(output, source, labels, "uint32", 0)
    finally:
        source.close()


def _point_coordinates(wkb: bytes) -> tuple[float, float]:
    byte_order = "<" if wkb[0] == 1 else ">"
    geometry_type = unpack_from(f"{byte_order}I", wkb, 1)[0]
    if geometry_type != 1:
        raise ValueError("GEOMETRY_TYPE_MISMATCH")
    return unpack_from(f"{byte_order}dd", wkb, 5)


def _point_wkb(x: float, y: float) -> bytes:
    return pack("<BIdd", 1, 1, x, y)


def _read_points(
    path: Path, layer: str | None
) -> tuple[dict[str, Any], np.ndarray, list[np.ndarray]]:
    try:
        from pyogrio import raw
    except ImportError as exc:
        raise ValueError("BLOCKED_BY_ENVIRONMENT") from exc
    metadata, _, geometries, fields = raw.read(path, layer=layer)
    return metadata, geometries, fields


def _candidate_window(
    source: rasterio.DatasetReader, x: float, y: float, radius: float
) -> Window | None:
    inverse = ~source.transform
    corners = [
        inverse * (x - radius, y - radius),
        inverse * (x - radius, y + radius),
        inverse * (x + radius, y - radius),
        inverse * (x + radius, y + radius),
    ]
    columns = [point[0] for point in corners]
    rows = [point[1] for point in corners]
    column_start = max(0, int(np.floor(min(columns))))
    column_stop = min(source.width, int(np.ceil(max(columns))))
    row_start = max(0, int(np.floor(min(rows))))
    row_stop = min(source.height, int(np.ceil(max(rows))))
    if column_start >= column_stop or row_start >= row_stop:
        return None
    return Window.from_slices(
        (row_start, row_stop),
        (column_start, column_stop),
    )


def snap_pour_points(
    points_path: Path,
    accumulation_path: Path,
    output: Path,
    layer: str | None,
    max_distance: float,
) -> None:
    try:
        from pyogrio import raw
    except ImportError as exc:
        raise ValueError("BLOCKED_BY_ENVIRONMENT") from exc
    metadata, geometries, fields = _read_points(points_path, layer)
    snapped_geometries: list[bytes] = []
    source_x: list[float] = []
    source_y: list[float] = []
    snapped_x: list[float] = []
    snapped_y: list[float] = []
    distances: list[float] = []
    accumulation_cells: list[int] = []
    with rasterio.open(accumulation_path) as source:
        for geometry in geometries:
            x, y = _point_coordinates(geometry)
            window = _candidate_window(source, x, y, max_distance)
            if window is None:
                raise ValueError("POUR_POINT_NOT_FOUND")
            values = source.read(1, window=window, masked=True)
            data = np.asarray(values.data)
            mask = np.ma.getmaskarray(values)
            if np.any(~mask & (~np.isfinite(data) | (data < 1) | (data != np.floor(data)))):
                raise ValueError("ACCUMULATION_INVALID")
            local_rows, local_columns = np.indices(data.shape)
            rows = local_rows + int(window.row_off)
            columns = local_columns + int(window.col_off)
            center_x, center_y = rasterio.transform.xy(
                source.transform, rows, columns, offset="center"
            )
            center_x = np.asarray(center_x).reshape(data.shape)
            center_y = np.asarray(center_y).reshape(data.shape)
            distance = np.hypot(center_x - x, center_y - y)
            candidates = (~mask) & (distance <= max_distance)
            if not np.any(candidates):
                raise ValueError("POUR_POINT_NOT_FOUND")
            candidate_rows = rows[candidates]
            candidate_columns = columns[candidates]
            candidate_distance = distance[candidates]
            candidate_values = data[candidates]
            selected = int(
                np.lexsort(
                    (
                        candidate_columns,
                        candidate_rows,
                        candidate_distance,
                        -candidate_values.astype(np.float64),
                    )
                )[0]
            )
            selected_x = float(center_x[candidates][selected])
            selected_y = float(center_y[candidates][selected])
            snapped_geometries.append(_point_wkb(selected_x, selected_y))
            source_x.append(x)
            source_y.append(y)
            snapped_x.append(selected_x)
            snapped_y.append(selected_y)
            distances.append(float(candidate_distance[selected]))
            accumulation_cells.append(int(candidate_values[selected]))

    feature_count = len(geometries)
    output_fields = [*fields]
    output_fields.extend(
        [
            np.arange(1, feature_count + 1, dtype=np.int64),
            np.asarray(source_x, dtype=np.float64),
            np.asarray(source_y, dtype=np.float64),
            np.asarray(snapped_x, dtype=np.float64),
            np.asarray(snapped_y, dtype=np.float64),
            np.asarray(distances, dtype=np.float64),
            np.asarray(accumulation_cells, dtype=np.int64),
        ]
    )
    raw.write(
        output,
        np.asarray(snapped_geometries, dtype=object),
        output_fields,
        [
            *metadata["fields"],
            "hydro_id",
            "source_x",
            "source_y",
            "snapped_x",
            "snapped_y",
            "snap_distance_m",
            "accumulation_cells",
        ],
        layer="pour_points",
        driver="GPKG",
        geometry_type="Point",
        crs=metadata["crs"],
    )


def watershed(
    direction_path: Path,
    points_path: Path,
    output: Path,
    work_dir: Path,
    layer: str | None,
    id_field: str,
) -> None:
    metadata, geometries, fields = _read_points(points_path, layer)
    field_names = list(metadata["fields"])
    if id_field not in field_names:
        raise ValueError("POUR_POINT_ID_INVALID")
    identifiers = fields[field_names.index(id_field)]
    if not np.issubdtype(identifiers.dtype, np.integer):
        raise ValueError("POUR_POINT_ID_INVALID")
    ids = identifiers.astype(np.int64, copy=False)
    if np.any(ids <= 0) or np.any(ids > np.iinfo(np.uint32).max):
        raise ValueError("POUR_POINT_ID_INVALID")
    if np.unique(ids).size != ids.size:
        raise ValueError("POUR_POINT_ID_INVALID")

    source, direction = _load_direction(direction_path, work_dir)
    try:
        indegree, queue, valid_count = _topology(direction, work_dir)
        del indegree
        labels = np.memmap(
            work_dir / "watershed.dat", mode="w+", dtype=np.uint32, shape=direction.shape
        )
        labels[:] = 0
        is_pour_point = np.memmap(
            work_dir / "pour-points.dat", mode="w+", dtype=np.bool_, shape=direction.shape
        )
        is_pour_point[:] = False
        for geometry, identifier in zip(geometries, ids, strict=True):
            x, y = _point_coordinates(geometry)
            row, column = source.index(x, y)
            if not (0 <= row < source.height and 0 <= column < source.width):
                raise ValueError("POUR_POINT_NOT_FOUND")
            if direction[row, column] == 255:
                raise ValueError("POUR_POINT_NOT_FOUND")
            if is_pour_point[row, column]:
                raise ValueError("POUR_POINT_CONFLICT")
            labels[row, column] = int(identifier)
            is_pour_point[row, column] = True

        flat_direction = direction.reshape(-1)
        flat_labels = labels.reshape(-1)
        flat_is_pour_point = is_pour_point.reshape(-1)
        for position in range(valid_count - 1, -1, -1):
            index = int(queue[position])
            code = int(flat_direction[index])
            if code and not flat_is_pour_point[index]:
                flat_labels[index] = flat_labels[_downstream_index(index, code, source.width)]
        labels.flush()
        _write_memmap(output, source, labels, "uint32", 0)
    finally:
        source.close()
