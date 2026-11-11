"""Independent FloPy/MODFLOW 6 analytical and Docker isolation spike.

The module is deliberately outside the Operator Catalog and every default
runtime lock.  FloPy and Pillow are imported lazily, while MODFLOW 6 is only
executed through a separately verified, network-disabled Docker image.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
import hashlib
import importlib
from importlib import metadata
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import subprocess
import tempfile
from typing import Any, Mapping
import uuid
import zipfile


FLOPY_VERSION = "3.12.0"
FLOPY_WHEEL_SHA256 = (
    "sha256:e3642d60ab9e94de8531200611c5c76e4d39e85416e0d81024d4b4d373ba0269"
)
MF6_VERSION = "6.8.1"
MF6_ARCHIVE_SIZE = 136_031_064
MF6_ARCHIVE_SHA256 = (
    "sha256:439a29f3e60f8e88b0ac083db10c92e3474b203d18ca6583863d8464b811ba90"
)
MF6_BINARY_SIZE = 49_554_232
MF6_BINARY_SHA256 = (
    "sha256:004e333a73605c6b6b048127ce8099e2130c0125e2b161812a49691a8fcdc8f3"
)
MF6_LIBRARY_SIZE = 52_968_792
MF6_LIBRARY_SHA256 = (
    "sha256:09fb8cf64d3b04a60ec1e52c9f7becef58dff85df0c27b3bfd683fc1d8147687"
)
MF6_CODE_JSON_SHA256 = (
    "sha256:cec1dacaa44dcfe4c9847631876bffae9cf1626da7c4619c585f3042596b2727"
)
MF6_LICENSE_SHA256 = (
    "sha256:8f7f9f0a052bd4b9883e2a6ad88ef52fde0fecf05e75abae674c4c7a072e3043"
)
MF6_DISCLAIMER_SHA256 = (
    "sha256:46ebbe1391ef4e52541f1ca9e97a198e35c8057aff5b8ae54f967c48fc96d37c"
)
CONFIG_SCHEMA = "groundwater-modflow6-spike-config.v1"
RESULT_SCHEMA = "groundwater-modflow6-spike-result.v1"
SOURCE_ROOT = Path(__file__).resolve().parents[3]
MF6_ARCHIVE_PREFIX = "mf6.8.1_linux"
MF6_BINARY_MEMBER = f"{MF6_ARCHIVE_PREFIX}/bin/mf6"
MF6_LIBRARY_MEMBER = f"{MF6_ARCHIVE_PREFIX}/bin/libmf6.so"
MF6_CODE_JSON_MEMBER = f"{MF6_ARCHIVE_PREFIX}/code.json"
LOCKED_RUNTIME_DISTRIBUTIONS = {
    "contourpy": "1.4.0",
    "cycler": "0.12.1",
    "flopy": FLOPY_VERSION,
    "fonttools": "4.68.0",
    "kiwisolver": "1.5.1",
    "matplotlib": "3.11.2",
    "numpy": "2.5.4",
    "packaging": "26.3",
    "pandas": "3.1.1",
    "pillow": "13.0.0",
    "pyparsing": "3.3.3",
    "python-dateutil": "4.0.0",
    "tzdata": "2026.6",
}


class Modflow6SpikeError(ValueError):
    """A stable refusal at the independent ADV-02 spike boundary."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _mapping(value: object, code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise Modflow6SpikeError(code)
    return dict(value)


def _exact_keys(value: Mapping[str, Any], expected: set[str], code: str) -> None:
    if set(value) != expected:
        raise Modflow6SpikeError(code)


def _nonempty_string(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Modflow6SpikeError(code)
    return value


def _positive_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise Modflow6SpikeError(code)
    return value


def _finite_float(value: object, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Modflow6SpikeError(code)
    result = float(value)
    if not math.isfinite(result):
        raise Modflow6SpikeError(code)
    return result


def _positive_float(value: object, code: str) -> float:
    result = _finite_float(value, code)
    if result <= 0:
        raise Modflow6SpikeError(code)
    return result


def _safe_filename(value: object, suffix: str, code: str) -> str:
    result = _nonempty_string(value, code)
    path = PurePosixPath(result)
    if path.name != result or result in {".", ".."} or not result.endswith(suffix):
        raise Modflow6SpikeError(code)
    return result


@dataclass(frozen=True)
class Modflow6SpikeConfig:
    experiment_id: str
    nlay: int
    nrow: int
    ncol: int
    delr_m: float
    delc_m: float
    top_m: float
    bottom_m: float
    x_origin_m: float
    y_origin_m: float
    crs: str
    boundary_head_distance_m: float
    cell_type: str
    hydraulic_conductivity_m_per_day: float
    left_head_m: float
    right_head_m: float
    temporal_mode: str
    time_units: str
    period_length_days: float
    time_steps: int
    time_step_multiplier: float
    solver_complexity: str
    outer_maximum: int
    inner_maximum: int
    outer_dvclose_m: float
    inner_dvclose_m: float
    rclose_m3_per_day: float
    linear_acceleration: str
    head_file: str
    budget_file: str
    listing_file: str
    head_raster_file: str
    head_unit: str
    raster_nodata: float
    contour_interval_m: float
    contour_offset_m: float
    maximum_head_absolute_error_m: float
    maximum_darcy_relative_error: float
    maximum_budget_percent_discrepancy: float
    input_kind: str
    random_seed: int | None
    calibration_status: str
    config_sha256: str
    raw: dict[str, Any] = field(repr=False)


def load_spike_config(
    source: str | Path | Mapping[str, Any],
) -> Modflow6SpikeConfig:
    """Load the only supported synthetic steady analytical contract."""

    if isinstance(source, Mapping):
        try:
            encoded = _canonical_bytes(source)
            raw = json.loads(encoded)
        except (TypeError, ValueError) as error:
            raise Modflow6SpikeError("MF6_SPIKE_CONFIG_INVALID") from error
    else:
        path = Path(source)
        if path.is_symlink() or not path.is_file():
            raise Modflow6SpikeError("MF6_SPIKE_CONFIG_INVALID")
        encoded = path.read_bytes()
        try:
            raw = json.loads(encoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise Modflow6SpikeError("MF6_SPIKE_CONFIG_INVALID") from error

    root = _mapping(raw, "MF6_SPIKE_CONFIG_INVALID")
    _exact_keys(
        root,
        {
            "schema_version",
            "experiment_id",
            "grid",
            "hydraulics",
            "boundaries",
            "time",
            "solver",
            "outputs",
            "acceptance",
            "provenance",
        },
        "MF6_SPIKE_CONFIG_FIELDS_INVALID",
    )
    if root["schema_version"] != CONFIG_SCHEMA:
        raise Modflow6SpikeError("MF6_SPIKE_CONFIG_SCHEMA_UNSUPPORTED")

    grid = _mapping(root["grid"], "MF6_SPIKE_GRID_INVALID")
    _exact_keys(
        grid,
        {
            "nlay",
            "nrow",
            "ncol",
            "delr_m",
            "delc_m",
            "top_m",
            "bottom_m",
            "x_origin_m",
            "y_origin_m",
            "crs",
            "boundary_head_distance_m",
        },
        "MF6_SPIKE_GRID_INVALID",
    )
    nlay = _positive_int(grid["nlay"], "MF6_SPIKE_GRID_INVALID")
    nrow = _positive_int(grid["nrow"], "MF6_SPIKE_GRID_INVALID")
    ncol = _positive_int(grid["ncol"], "MF6_SPIKE_GRID_INVALID")
    if nlay != 1 or nrow < 2 or ncol < 3:
        raise Modflow6SpikeError("MF6_SPIKE_GRID_UNSUPPORTED")
    delr_m = _positive_float(grid["delr_m"], "MF6_SPIKE_GRID_INVALID")
    delc_m = _positive_float(grid["delc_m"], "MF6_SPIKE_GRID_INVALID")
    top_m = _finite_float(grid["top_m"], "MF6_SPIKE_GRID_INVALID")
    bottom_m = _finite_float(grid["bottom_m"], "MF6_SPIKE_GRID_INVALID")
    if top_m <= bottom_m:
        raise Modflow6SpikeError("MF6_SPIKE_GRID_INVALID")
    x_origin_m = _finite_float(grid["x_origin_m"], "MF6_SPIKE_GRID_INVALID")
    y_origin_m = _finite_float(grid["y_origin_m"], "MF6_SPIKE_GRID_INVALID")
    crs = _nonempty_string(grid["crs"], "MF6_SPIKE_CRS_INVALID")
    if crs != "EPSG:32650":
        raise Modflow6SpikeError("MF6_SPIKE_CRS_UNSUPPORTED")
    boundary_distance = _positive_float(
        grid["boundary_head_distance_m"], "MF6_SPIKE_BOUNDARY_DISTANCE_INVALID"
    )
    expected_distance = (ncol - 1) * delr_m
    if not math.isclose(boundary_distance, expected_distance, rel_tol=0, abs_tol=1e-12):
        raise Modflow6SpikeError("MF6_SPIKE_BOUNDARY_DISTANCE_INVALID")

    hydraulics = _mapping(root["hydraulics"], "MF6_SPIKE_HYDRAULICS_INVALID")
    _exact_keys(
        hydraulics,
        {"cell_type", "hydraulic_conductivity_m_per_day"},
        "MF6_SPIKE_HYDRAULICS_INVALID",
    )
    if hydraulics["cell_type"] != "confined":
        raise Modflow6SpikeError("MF6_SPIKE_CELL_TYPE_UNSUPPORTED")
    hydraulic_conductivity = _positive_float(
        hydraulics["hydraulic_conductivity_m_per_day"],
        "MF6_SPIKE_HYDRAULIC_CONDUCTIVITY_INVALID",
    )

    boundaries = _mapping(root["boundaries"], "MF6_SPIKE_BOUNDARIES_INVALID")
    _exact_keys(
        boundaries,
        {
            "left_head_m",
            "right_head_m",
            "left_and_right_constant_head_all_rows",
            "other_boundaries",
        },
        "MF6_SPIKE_BOUNDARIES_INVALID",
    )
    left_head = _finite_float(
        boundaries["left_head_m"], "MF6_SPIKE_HEAD_BOUNDARY_INVALID"
    )
    right_head = _finite_float(
        boundaries["right_head_m"], "MF6_SPIKE_HEAD_BOUNDARY_INVALID"
    )
    if left_head <= right_head:
        raise Modflow6SpikeError("MF6_SPIKE_HEAD_BOUNDARY_INVALID")
    if boundaries["left_and_right_constant_head_all_rows"] is not True:
        raise Modflow6SpikeError("MF6_SPIKE_BOUNDARY_LAYOUT_UNSUPPORTED")
    if boundaries["other_boundaries"] != "no-flow":
        raise Modflow6SpikeError("MF6_SPIKE_BOUNDARY_LAYOUT_UNSUPPORTED")

    time = _mapping(root["time"], "MF6_SPIKE_TIME_INVALID")
    _exact_keys(
        time,
        {
            "mode",
            "time_units",
            "period_length_days",
            "time_steps",
            "time_step_multiplier",
        },
        "MF6_SPIKE_TIME_INVALID",
    )
    if time["mode"] != "steady":
        raise Modflow6SpikeError("MF6_SPIKE_TIME_UNSUPPORTED")
    if time["time_units"] != "DAYS":
        raise Modflow6SpikeError("MF6_SPIKE_TIME_UNSUPPORTED")
    period_length = _positive_float(
        time["period_length_days"], "MF6_SPIKE_TIME_INVALID"
    )
    time_steps = _positive_int(time["time_steps"], "MF6_SPIKE_TIME_INVALID")
    multiplier = _positive_float(time["time_step_multiplier"], "MF6_SPIKE_TIME_INVALID")
    if period_length != 1.0 or time_steps != 1 or multiplier != 1.0:
        raise Modflow6SpikeError("MF6_SPIKE_TIME_UNSUPPORTED")

    solver = _mapping(root["solver"], "MF6_SPIKE_SOLVER_INVALID")
    _exact_keys(
        solver,
        {
            "complexity",
            "outer_maximum",
            "inner_maximum",
            "outer_dvclose_m",
            "inner_dvclose_m",
            "rclose_m3_per_day",
            "linear_acceleration",
        },
        "MF6_SPIKE_SOLVER_INVALID",
    )
    if solver["complexity"] != "SIMPLE" or solver["linear_acceleration"] != "CG":
        raise Modflow6SpikeError("MF6_SPIKE_SOLVER_UNSUPPORTED")

    outputs = _mapping(root["outputs"], "MF6_SPIKE_OUTPUTS_INVALID")
    _exact_keys(
        outputs,
        {
            "head_file",
            "budget_file",
            "listing_file",
            "head_raster_file",
            "head_unit",
            "raster_nodata",
            "contour_interval_m",
            "contour_offset_m",
        },
        "MF6_SPIKE_OUTPUTS_INVALID",
    )
    head_file = _safe_filename(
        outputs["head_file"], ".hds", "MF6_SPIKE_OUTPUTS_INVALID"
    )
    budget_file = _safe_filename(
        outputs["budget_file"], ".cbc", "MF6_SPIKE_OUTPUTS_INVALID"
    )
    listing_file = _safe_filename(
        outputs["listing_file"], ".lst", "MF6_SPIKE_OUTPUTS_INVALID"
    )
    raster_file = _safe_filename(
        outputs["head_raster_file"], ".tif", "MF6_SPIKE_OUTPUTS_INVALID"
    )
    if outputs["head_unit"] != "m":
        raise Modflow6SpikeError("MF6_SPIKE_HEAD_UNIT_UNSUPPORTED")
    raster_nodata = _finite_float(outputs["raster_nodata"], "MF6_SPIKE_OUTPUTS_INVALID")
    contour_interval = _positive_float(
        outputs["contour_interval_m"], "MF6_SPIKE_OUTPUTS_INVALID"
    )
    contour_offset = _finite_float(
        outputs["contour_offset_m"], "MF6_SPIKE_OUTPUTS_INVALID"
    )

    acceptance = _mapping(root["acceptance"], "MF6_SPIKE_ACCEPTANCE_INVALID")
    _exact_keys(
        acceptance,
        {
            "maximum_head_absolute_error_m",
            "maximum_darcy_relative_error",
            "maximum_budget_percent_discrepancy",
        },
        "MF6_SPIKE_ACCEPTANCE_INVALID",
    )

    provenance = _mapping(root["provenance"], "MF6_SPIKE_PROVENANCE_INVALID")
    _exact_keys(
        provenance,
        {"input_kind", "random_seed", "calibration_status"},
        "MF6_SPIKE_PROVENANCE_INVALID",
    )
    if provenance["input_kind"] != "SYNTHETIC_ANALYTICAL_BENCHMARK":
        raise Modflow6SpikeError("MF6_SPIKE_PROVENANCE_INVALID")
    if provenance["random_seed"] is not None:
        raise Modflow6SpikeError("MF6_SPIKE_PROVENANCE_INVALID")
    if provenance["calibration_status"] != "UNCALIBRATED_SYNTHETIC":
        raise Modflow6SpikeError("MF6_SPIKE_PROVENANCE_INVALID")

    return Modflow6SpikeConfig(
        experiment_id=_nonempty_string(
            root["experiment_id"], "MF6_SPIKE_EXPERIMENT_ID_INVALID"
        ),
        nlay=nlay,
        nrow=nrow,
        ncol=ncol,
        delr_m=delr_m,
        delc_m=delc_m,
        top_m=top_m,
        bottom_m=bottom_m,
        x_origin_m=x_origin_m,
        y_origin_m=y_origin_m,
        crs=crs,
        boundary_head_distance_m=boundary_distance,
        cell_type="confined",
        hydraulic_conductivity_m_per_day=hydraulic_conductivity,
        left_head_m=left_head,
        right_head_m=right_head,
        temporal_mode="steady",
        time_units="DAYS",
        period_length_days=period_length,
        time_steps=time_steps,
        time_step_multiplier=multiplier,
        solver_complexity="SIMPLE",
        outer_maximum=_positive_int(
            solver["outer_maximum"], "MF6_SPIKE_SOLVER_INVALID"
        ),
        inner_maximum=_positive_int(
            solver["inner_maximum"], "MF6_SPIKE_SOLVER_INVALID"
        ),
        outer_dvclose_m=_positive_float(
            solver["outer_dvclose_m"], "MF6_SPIKE_SOLVER_INVALID"
        ),
        inner_dvclose_m=_positive_float(
            solver["inner_dvclose_m"], "MF6_SPIKE_SOLVER_INVALID"
        ),
        rclose_m3_per_day=_positive_float(
            solver["rclose_m3_per_day"], "MF6_SPIKE_SOLVER_INVALID"
        ),
        linear_acceleration="CG",
        head_file=head_file,
        budget_file=budget_file,
        listing_file=listing_file,
        head_raster_file=raster_file,
        head_unit="m",
        raster_nodata=raster_nodata,
        contour_interval_m=contour_interval,
        contour_offset_m=contour_offset,
        maximum_head_absolute_error_m=_positive_float(
            acceptance["maximum_head_absolute_error_m"],
            "MF6_SPIKE_ACCEPTANCE_INVALID",
        ),
        maximum_darcy_relative_error=_positive_float(
            acceptance["maximum_darcy_relative_error"],
            "MF6_SPIKE_ACCEPTANCE_INVALID",
        ),
        maximum_budget_percent_discrepancy=_positive_float(
            acceptance["maximum_budget_percent_discrepancy"],
            "MF6_SPIKE_ACCEPTANCE_INVALID",
        ),
        input_kind="SYNTHETIC_ANALYTICAL_BENCHMARK",
        random_seed=None,
        calibration_status="UNCALIBRATED_SYNTHETIC",
        config_sha256=_digest_bytes(encoded),
        raw=root,
    )


def analytical_heads(config: Modflow6SpikeConfig) -> Any:
    """Return the cell-centre one-dimensional linear analytical head field."""

    np = importlib.import_module("numpy")
    line = np.linspace(config.left_head_m, config.right_head_m, config.ncol)
    return np.broadcast_to(line, (config.nlay, config.nrow, config.ncol)).copy()


def analytical_darcy_flow(config: Modflow6SpikeConfig) -> float:
    """Return total positive x-direction Darcy flow in cubic metres per day."""

    thickness = config.top_m - config.bottom_m
    width = config.nrow * config.delc_m
    gradient = (
        config.left_head_m - config.right_head_m
    ) / config.boundary_head_distance_m
    return config.hydraulic_conductivity_m_per_day * thickness * width * gradient


def _regular_input(path: Path, code: str) -> Path:
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise Modflow6SpikeError(code)
    return path


def _archive_member_bytes(archive: zipfile.ZipFile, member: str, code: str) -> bytes:
    try:
        info = archive.getinfo(member)
    except KeyError as error:
        raise Modflow6SpikeError(code) from error
    if info.is_dir():
        raise Modflow6SpikeError(code)
    return archive.read(info)


def verify_mf6_release_inputs(
    archive_path: str | Path,
    license_path: str | Path,
    disclaimer_path: str | Path,
) -> dict[str, Any]:
    """Verify the exact USGS MF6 6.8.1 release inputs before image staging."""

    archive_path = _regular_input(Path(archive_path), "MF6_ARCHIVE_INVALID")
    license_path = _regular_input(Path(license_path), "MF6_LICENSE_INVALID")
    disclaimer_path = _regular_input(Path(disclaimer_path), "MF6_DISCLAIMER_INVALID")
    if archive_path.stat().st_size != MF6_ARCHIVE_SIZE:
        raise Modflow6SpikeError("MF6_ARCHIVE_SIZE_MISMATCH")
    if _file_digest(archive_path) != MF6_ARCHIVE_SHA256:
        raise Modflow6SpikeError("MF6_ARCHIVE_SHA256_MISMATCH")
    if (
        license_path.stat().st_size != 1650
        or _file_digest(license_path) != MF6_LICENSE_SHA256
    ):
        raise Modflow6SpikeError("MF6_LICENSE_SHA256_MISMATCH")
    if (
        disclaimer_path.stat().st_size != 652
        or _file_digest(disclaimer_path) != MF6_DISCLAIMER_SHA256
    ):
        raise Modflow6SpikeError("MF6_DISCLAIMER_SHA256_MISMATCH")

    license_text = license_path.read_text(encoding="utf-8")
    disclaimer_text = disclaimer_path.read_text(encoding="utf-8")
    if (
        "public domain" not in license_text.lower()
        or "cc0 1.0" not in license_text.lower()
    ):
        raise Modflow6SpikeError("MF6_LICENSE_CONTENT_INVALID")
    if (
        "U.S. Geological Survey" not in disclaimer_text
        or "No warranty" not in disclaimer_text
    ):
        raise Modflow6SpikeError("MF6_DISCLAIMER_CONTENT_INVALID")

    try:
        archive = zipfile.ZipFile(archive_path)
    except zipfile.BadZipFile as error:
        raise Modflow6SpikeError("MF6_ARCHIVE_INVALID") from error
    with archive:
        for info in archive.infolist():
            member = PurePosixPath(info.filename)
            if member.is_absolute() or ".." in member.parts:
                raise Modflow6SpikeError("MF6_ARCHIVE_MEMBER_UNSAFE")
        binary = _archive_member_bytes(archive, MF6_BINARY_MEMBER, "MF6_BINARY_MISSING")
        library = _archive_member_bytes(
            archive, MF6_LIBRARY_MEMBER, "MF6_LIBRARY_MISSING"
        )
        code_json = _archive_member_bytes(
            archive, MF6_CODE_JSON_MEMBER, "MF6_RELEASE_METADATA_MISSING"
        )

    if len(binary) != MF6_BINARY_SIZE or _digest_bytes(binary) != MF6_BINARY_SHA256:
        raise Modflow6SpikeError("MF6_BINARY_SHA256_MISMATCH")
    if len(library) != MF6_LIBRARY_SIZE or _digest_bytes(library) != MF6_LIBRARY_SHA256:
        raise Modflow6SpikeError("MF6_LIBRARY_SHA256_MISMATCH")
    if _digest_bytes(code_json) != MF6_CODE_JSON_SHA256:
        raise Modflow6SpikeError("MF6_RELEASE_METADATA_SHA256_MISMATCH")
    if len(binary) < 20 or binary[:4] != b"\x7fELF":
        raise Modflow6SpikeError("MF6_BINARY_ARCHITECTURE_INVALID")
    if binary[4] != 2 or binary[5] != 1 or struct.unpack("<H", binary[18:20])[0] != 62:
        raise Modflow6SpikeError("MF6_BINARY_ARCHITECTURE_INVALID")

    try:
        code_entries = json.loads(code_json)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise Modflow6SpikeError("MF6_RELEASE_METADATA_INVALID") from error
    if not isinstance(code_entries, list) or len(code_entries) != 1:
        raise Modflow6SpikeError("MF6_RELEASE_METADATA_INVALID")
    code_entry = _mapping(code_entries[0], "MF6_RELEASE_METADATA_INVALID")
    licenses = code_entry.get("permissions", {}).get("licenses", [])
    if (
        code_entry.get("version") != MF6_VERSION
        or code_entry.get("organization") != "U.S. Geological Survey"
        or not any(item.get("name") == "Public Domain, CC0-1.0" for item in licenses)
    ):
        raise Modflow6SpikeError("MF6_RELEASE_METADATA_INVALID")

    return {
        "version": MF6_VERSION,
        "architecture": "linux-x86_64",
        "license": "Public Domain, CC0-1.0",
        "archive_sha256": MF6_ARCHIVE_SHA256,
        "binary_sha256": MF6_BINARY_SHA256,
        "library_sha256": MF6_LIBRARY_SHA256,
        "code_json_sha256": MF6_CODE_JSON_SHA256,
        "license_sha256": MF6_LICENSE_SHA256,
        "disclaimer_sha256": MF6_DISCLAIMER_SHA256,
    }


def extract_verified_mf6_build_inputs(
    archive_path: str | Path,
    license_path: str | Path,
    disclaimer_path: str | Path,
    target: str | Path,
) -> dict[str, Any]:
    """Stage only the verified executable and evidence into an empty directory."""

    evidence = verify_mf6_release_inputs(archive_path, license_path, disclaimer_path)
    target = Path(target)
    if target.exists() or target.is_symlink() or not target.is_absolute():
        raise Modflow6SpikeError("MF6_BUILD_CONTEXT_INVALID")
    target.mkdir(mode=0o700)
    try:
        with zipfile.ZipFile(Path(archive_path)) as archive:
            (target / "mf6").write_bytes(archive.read(MF6_BINARY_MEMBER))
            (target / "code.json").write_bytes(archive.read(MF6_CODE_JSON_MEMBER))
        shutil.copyfile(Path(license_path), target / "LICENSE.md")
        shutil.copyfile(Path(disclaimer_path), target / "DISCLAIMER.md")
        (target / "mf6").chmod(0o555)
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return evidence


def _validate_output_directory(output_directory: str | Path) -> Path:
    target = Path(output_directory)
    if not target.is_absolute():
        raise Modflow6SpikeError("MF6_SPIKE_OUTPUT_MUST_BE_ABSOLUTE")
    if target.exists() or target.is_symlink():
        raise Modflow6SpikeError("MF6_SPIKE_OUTPUT_EXISTS")
    resolved = target.resolve(strict=False)
    if _is_within(resolved, SOURCE_ROOT):
        raise Modflow6SpikeError("MF6_SPIKE_OUTPUT_MUST_BE_EXTERNAL")
    parent = target.parent
    if parent.is_symlink() or not parent.is_dir():
        raise Modflow6SpikeError("MF6_SPIKE_OUTPUT_PARENT_INVALID")
    return target


def _load_scientific_dependencies() -> tuple[Any, Any, Any, Any]:
    for name, expected in LOCKED_RUNTIME_DISTRIBUTIONS.items():
        try:
            actual = metadata.version(name)
        except metadata.PackageNotFoundError as error:
            raise Modflow6SpikeError(f"MF6_SPIKE_DEPENDENCY_MISSING:{name}") from error
        if actual != expected:
            raise Modflow6SpikeError(
                f"MF6_SPIKE_DEPENDENCY_VERSION_MISMATCH:{name}:expected={expected}:actual={actual}"
            )
    np = importlib.import_module("numpy")
    flopy = importlib.import_module("flopy")
    image = importlib.import_module("PIL.Image")
    tiff = importlib.import_module("PIL.TiffImagePlugin")
    return np, flopy, image, tiff


def _validate_image_reference(image: str) -> str:
    if not isinstance(image, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._/@:-]{0,255}", image
    ):
        raise Modflow6SpikeError("MF6_SPIKE_DOCKER_IMAGE_INVALID")
    return image


def _docker_command(
    command: list[str], *, timeout: float
) -> subprocess.CompletedProcess[str]:
    if shutil.which("docker") is None:
        raise Modflow6SpikeError("MF6_SPIKE_DOCKER_UNAVAILABLE")
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise Modflow6SpikeError("MF6_SPIKE_DOCKER_TIMEOUT") from error
    except OSError as error:
        raise Modflow6SpikeError("MF6_SPIKE_DOCKER_UNAVAILABLE") from error


def _inspect_mf6_image(image: str) -> dict[str, str]:
    image = _validate_image_reference(image)
    inspected = _docker_command(
        ["docker", "image", "inspect", image, "--format", "{{json .}}"], timeout=30
    )
    if inspected.returncode != 0:
        raise Modflow6SpikeError("MF6_SPIKE_DOCKER_IMAGE_NOT_FOUND")
    try:
        details = json.loads(inspected.stdout)
    except json.JSONDecodeError as error:
        raise Modflow6SpikeError("MF6_SPIKE_DOCKER_IMAGE_INVALID") from error
    labels = details.get("Config", {}).get("Labels") or {}
    required_labels = {
        "org.opencontainers.image.version": MF6_VERSION,
        "org.opencontainers.image.licenses": "CC0-1.0",
        "io.groundwater.spike.scope": "INDEPENDENT_ADV_02_SPIKE_ONLY",
        "io.groundwater.mf6.archive.sha256": MF6_ARCHIVE_SHA256.removeprefix("sha256:"),
        "io.groundwater.mf6.binary.sha256": MF6_BINARY_SHA256.removeprefix("sha256:"),
    }
    if details.get("Architecture") != "amd64" or details.get("Os") != "linux":
        raise Modflow6SpikeError("MF6_SPIKE_DOCKER_IMAGE_ARCHITECTURE_INVALID")
    if any(labels.get(key) != value for key, value in required_labels.items()):
        raise Modflow6SpikeError("MF6_SPIKE_DOCKER_IMAGE_IDENTITY_INVALID")

    version = _docker_command(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "32",
            "--memory",
            "128m",
            "--cpus",
            "0.5",
            image,
            "-v",
        ],
        timeout=30,
    )
    combined = version.stdout + version.stderr
    if version.returncode != 0 or "mf6: 6.8.1" not in combined:
        raise Modflow6SpikeError("MF6_SPIKE_DOCKER_VERSION_MISMATCH")
    return {
        "image_reference": image,
        "image_id": str(details.get("Id", "")),
        "version_output": "mf6: 6.8.1",
    }


def verify_mf6_docker_image(image: str) -> dict[str, str]:
    """Verify architecture, immutable labels, and the executable version probe."""

    return _inspect_mf6_image(image)


def _build_simulation(
    workspace: Path, config: Modflow6SpikeConfig, np: Any, flopy: Any
) -> None:
    simulation = flopy.mf6.MFSimulation(
        sim_name="linear",
        sim_ws=workspace,
    )
    flopy.mf6.ModflowTdis(
        simulation,
        time_units=config.time_units,
        nper=1,
        perioddata=[
            (
                config.period_length_days,
                config.time_steps,
                config.time_step_multiplier,
            )
        ],
    )
    solver = flopy.mf6.ModflowIms(
        simulation,
        complexity=config.solver_complexity,
        outer_maximum=config.outer_maximum,
        inner_maximum=config.inner_maximum,
        outer_dvclose=config.outer_dvclose_m,
        inner_dvclose=config.inner_dvclose_m,
        rcloserecord=config.rclose_m3_per_day,
        linear_acceleration=config.linear_acceleration,
    )
    model = flopy.mf6.ModflowGwf(
        simulation,
        modelname="linear-gwf",
        save_flows=True,
    )
    simulation.register_ims_package(solver, [model.name])
    flopy.mf6.ModflowGwfdis(
        model,
        length_units="METERS",
        nlay=config.nlay,
        nrow=config.nrow,
        ncol=config.ncol,
        delr=config.delr_m,
        delc=config.delc_m,
        top=config.top_m,
        botm=config.bottom_m,
        xorigin=config.x_origin_m,
        yorigin=config.y_origin_m,
    )
    flopy.mf6.ModflowGwfic(model, strt=analytical_heads(config))
    flopy.mf6.ModflowGwfnpf(
        model,
        icelltype=0,
        k=config.hydraulic_conductivity_m_per_day,
        save_flows=True,
        save_specific_discharge=True,
    )
    constant_heads = [
        ((0, row, 0), config.left_head_m) for row in range(config.nrow)
    ] + [((0, row, config.ncol - 1), config.right_head_m) for row in range(config.nrow)]
    flopy.mf6.ModflowGwfchd(
        model,
        stress_period_data={0: constant_heads},
        save_flows=True,
        pname="CHD-1",
    )
    flopy.mf6.ModflowGwfoc(
        model,
        head_filerecord=config.head_file,
        budget_filerecord=config.budget_file,
        saverecord=[("HEAD", "ALL"), ("BUDGET", "ALL")],
        printrecord=[("HEAD", "ALL"), ("BUDGET", "ALL")],
    )
    simulation.write_simulation(silent=True)


def _run_mf6_container(workspace: Path, image: str) -> subprocess.CompletedProcess[str]:
    container_name = f"gw-mf6-spike-{uuid.uuid4().hex}"
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name,
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "128",
        "--memory",
        "512m",
        "--cpus",
        "1",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=16m",
        "--mount",
        f"type=bind,src={workspace},dst=/work",
        "--workdir",
        "/work",
        image,
    ]
    try:
        result = _docker_command(command, timeout=120)
    except Modflow6SpikeError as error:
        if str(error) == "MF6_SPIKE_DOCKER_TIMEOUT":
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                check=False,
                capture_output=True,
                timeout=30,
            )
        raise
    if result.returncode != 0:
        raise Modflow6SpikeError(
            f"MF6_SPIKE_EXECUTION_FAILED:returncode={result.returncode}"
        )
    return result


def _write_heads_csv(
    path: Path,
    heads: Any,
    expected: Any,
    config: Modflow6SpikeConfig,
) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "layer",
                "row",
                "column",
                "x_center_m",
                "y_center_m",
                "head_m",
                "analytical_head_m",
                "absolute_error_m",
            ],
        )
        writer.writeheader()
        for layer in range(config.nlay):
            for row in range(config.nrow):
                for column in range(config.ncol):
                    value = float(heads[layer, row, column])
                    analytical = float(expected[layer, row, column])
                    writer.writerow(
                        {
                            "layer": layer + 1,
                            "row": row + 1,
                            "column": column + 1,
                            "x_center_m": config.x_origin_m
                            + (column + 0.5) * config.delr_m,
                            "y_center_m": config.y_origin_m
                            + (config.nrow - row - 0.5) * config.delc_m,
                            "head_m": value,
                            "analytical_head_m": analytical,
                            "absolute_error_m": abs(value - analytical),
                        }
                    )


def _write_head_geotiff(
    path: Path,
    heads: Any,
    config: Modflow6SpikeConfig,
    np: Any,
    image: Any,
    tiff: Any,
) -> None:
    raster = np.asarray(heads[0], dtype=np.float32)
    ifd = tiff.ImageFileDirectory_v2()
    ifd.tagtype[33550] = 12
    ifd[33550] = (config.delr_m, config.delc_m, 0.0)
    ifd.tagtype[33922] = 12
    ifd[33922] = (
        0.0,
        0.0,
        0.0,
        config.x_origin_m,
        config.y_origin_m + config.nrow * config.delc_m,
        0.0,
    )
    epsg = int(config.crs.removeprefix("EPSG:"))
    geokeys = (
        1,
        1,
        0,
        4,
        1024,
        0,
        1,
        1,
        1025,
        0,
        1,
        1,
        3072,
        0,
        1,
        epsg,
        3076,
        0,
        1,
        9001,
    )
    ifd.tagtype[34735] = 3
    ifd[34735] = geokeys
    ifd.tagtype[42113] = 2
    ifd[42113] = str(config.raster_nodata)
    ifd.tagtype[270] = 2
    ifd[270] = "Synthetic uncalibrated MODFLOW 6 hydraulic head; unit=m"
    image.fromarray(raster).save(path, format="TIFF", compression="raw", tiffinfo=ifd)


def _write_contour_handoff(path: Path, config: Modflow6SpikeConfig) -> None:
    payload = {
        "schema_version": "groundwater-mf6-contour-handoff.v1",
        "input": {
            "file": config.head_raster_file,
            "format": "geotiff",
            "mediaType": "image/tiff",
            "crs": config.crs,
            "unit": config.head_unit,
        },
        "operator": {"id": "raster.contour", "version": "1.0.0"},
        "parameters": {
            "band": 1,
            "intervalMeters": config.contour_interval_m,
            "offsetMeters": config.contour_offset_m,
        },
        "execution": {
            "status": "HANDOFF_ONLY",
            "reason": "The independent spike does not mutate or bypass the existing platform workflow and Executor.",
        },
    }
    path.write_bytes(_canonical_bytes(payload) + b"\n")


def _analyse_outputs(
    workspace: Path,
    config: Modflow6SpikeConfig,
    np: Any,
    flopy: Any,
) -> tuple[Any, dict[str, float]]:
    head_path = workspace / config.head_file
    budget_path = workspace / config.budget_file
    listing_path = workspace / config.listing_file
    for path in (head_path, budget_path, listing_path):
        if not path.is_file():
            raise Modflow6SpikeError("MF6_SPIKE_REQUIRED_OUTPUT_MISSING")

    heads = flopy.utils.HeadFile(head_path).get_data()
    expected = analytical_heads(config)
    if heads.shape != expected.shape or not bool(np.isfinite(heads).all()):
        raise Modflow6SpikeError("MF6_SPIKE_HEAD_OUTPUT_INVALID")
    maximum_head_error = float(np.max(np.abs(heads - expected)))

    cell_budget = flopy.utils.CellBudgetFile(budget_path, precision="double")
    chd_records = cell_budget.get_data(text="CHD")
    spdis_records = cell_budget.get_data(text="DATA-SPDIS")
    if not chd_records or not spdis_records:
        raise Modflow6SpikeError("MF6_SPIKE_BUDGET_OUTPUT_INVALID")
    boundary_flows = np.asarray(chd_records[-1]["q"], dtype=float)
    inflow = float(boundary_flows[boundary_flows > 0].sum())
    outflow = float(-boundary_flows[boundary_flows < 0].sum())
    discharge = spdis_records[-1]
    expected_gradient = (
        config.left_head_m - config.right_head_m
    ) / config.boundary_head_distance_m
    expected_specific_discharge = (
        config.hydraulic_conductivity_m_per_day * expected_gradient
    )
    maximum_specific_discharge_error = float(
        np.max(
            np.abs(
                np.asarray(discharge["qx"], dtype=float) - expected_specific_discharge
            )
        )
    )
    maximum_transverse_discharge = float(
        max(
            np.max(np.abs(np.asarray(discharge["qy"], dtype=float))),
            np.max(np.abs(np.asarray(discharge["qz"], dtype=float))),
        )
    )

    list_budget = flopy.utils.Mf6ListBudget(listing_path).get_incremental()
    if list_budget is None or len(list_budget) != 1:
        raise Modflow6SpikeError("MF6_SPIKE_LISTING_BUDGET_INVALID")
    budget_percent_discrepancy = abs(float(list_budget[-1]["PERCENT_DISCREPANCY"]))
    expected_flow = analytical_darcy_flow(config)
    darcy_relative_error = max(
        abs(inflow - expected_flow) / expected_flow,
        abs(outflow - expected_flow) / expected_flow,
    )
    return heads, {
        "maximum_head_absolute_error_m": maximum_head_error,
        "analytical_hydraulic_gradient": expected_gradient,
        "analytical_specific_discharge_m_per_day": expected_specific_discharge,
        "maximum_specific_discharge_absolute_error_m_per_day": maximum_specific_discharge_error,
        "maximum_transverse_specific_discharge_m_per_day": maximum_transverse_discharge,
        "analytical_total_flow_m3_per_day": expected_flow,
        "simulated_inflow_m3_per_day": inflow,
        "simulated_outflow_m3_per_day": outflow,
        "darcy_relative_error": darcy_relative_error,
        "budget_balance_residual_m3_per_day": abs(inflow - outflow),
        "budget_percent_discrepancy": budget_percent_discrepancy,
    }


def _artifact_role(name: str) -> str:
    roles = {
        "linear-gwf.hds": "head_binary",
        "linear-gwf.cbc": "cell_budget_binary",
        "linear-gwf.lst": "model_listing",
        "mfsim.lst": "simulation_listing",
        "mf6-stdout.txt": "execution_stdout",
        "heads.csv": "head_table",
        "head.tif": "head_geotiff",
        "contour-handoff.json": "existing_contour_operator_handoff",
    }
    return roles.get(name, "mf6_replay_input_or_output")


def _collect_files(workspace: Path) -> dict[str, dict[str, Any]]:
    files: dict[str, dict[str, Any]] = {}
    for path in sorted(workspace.iterdir(), key=lambda item: item.name):
        if path.is_file() and path.name != "result.json":
            files[path.name] = {
                "sha256": _file_digest(path),
                "size_bytes": path.stat().st_size,
                "role": _artifact_role(path.name),
            }
    return files


def run_modflow6_spike(
    config_source: str | Path | Mapping[str, Any],
    output_directory: str | Path,
    docker_image: str,
) -> dict[str, Any]:
    """Run the locked analytical case and atomically publish inspectable evidence."""

    config = load_spike_config(config_source)
    target = _validate_output_directory(output_directory)
    image_reference = _validate_image_reference(docker_image)
    np, flopy, image, tiff = _load_scientific_dependencies()
    image_identity = _inspect_mf6_image(image_reference)
    workspace = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.staging-", dir=target.parent)
    )
    try:
        _build_simulation(workspace, config, np, flopy)
        execution = _run_mf6_container(workspace, image_reference)
        (workspace / "mf6-stdout.txt").write_text(execution.stdout, encoding="utf-8")
        if execution.stderr:
            (workspace / "mf6-stderr.txt").write_text(
                execution.stderr, encoding="utf-8"
            )
        normal_termination = "Normal termination of simulation." in execution.stdout
        heads, metrics = _analyse_outputs(workspace, config, np, flopy)
        expected = analytical_heads(config)
        _write_heads_csv(workspace / "heads.csv", heads, expected, config)
        _write_head_geotiff(
            workspace / config.head_raster_file,
            heads,
            config,
            np,
            image,
            tiff,
        )
        _write_contour_handoff(workspace / "contour-handoff.json", config)

        checks = [
            {
                "id": "linear_head_solution",
                "status": "PASS"
                if metrics["maximum_head_absolute_error_m"]
                <= config.maximum_head_absolute_error_m
                else "FAIL",
                "observed": metrics["maximum_head_absolute_error_m"],
                "maximum": config.maximum_head_absolute_error_m,
                "unit": "m",
            },
            {
                "id": "darcy_total_flow",
                "status": "PASS"
                if metrics["darcy_relative_error"]
                <= config.maximum_darcy_relative_error
                else "FAIL",
                "observed": metrics["darcy_relative_error"],
                "maximum": config.maximum_darcy_relative_error,
                "unit": "relative",
            },
            {
                "id": "budget_balance",
                "status": "PASS"
                if metrics["budget_percent_discrepancy"]
                <= config.maximum_budget_percent_discrepancy
                else "FAIL",
                "observed": metrics["budget_percent_discrepancy"],
                "maximum": config.maximum_budget_percent_discrepancy,
                "unit": "percent",
            },
            {
                "id": "normal_termination",
                "status": "PASS" if normal_termination else "FAIL",
                "observed": normal_termination,
                "required": True,
            },
        ]
        scientific_status = (
            "PASS" if all(check["status"] == "PASS" for check in checks) else "FAIL"
        )
        files = _collect_files(workspace)
        manifest: dict[str, Any] = {
            "schema_version": RESULT_SCHEMA,
            "experiment_id": config.experiment_id,
            "scope": "INDEPENDENT_ADV_02_SPIKE_ONLY",
            "config_sha256": config.config_sha256,
            "model": {
                "kind": "steady_confined_homogeneous_linear_head",
                "grid_shape": [config.nlay, config.nrow, config.ncol],
                "calibration_status": config.calibration_status,
                "input_kind": config.input_kind,
                "random_seed": config.random_seed,
                "crs": config.crs,
                "head_unit": config.head_unit,
                "flow_unit": "m3/day",
            },
            "runtime": {
                "python": f"{os.sys.version_info.major}.{os.sys.version_info.minor}.{os.sys.version_info.micro}",
                "flopy_version": FLOPY_VERSION,
                "mf6_version": MF6_VERSION,
                "mf6_archive_sha256": MF6_ARCHIVE_SHA256,
                "mf6_binary_sha256": MF6_BINARY_SHA256,
                "docker_image_reference": image_identity["image_reference"],
                "docker_image_id": image_identity["image_id"],
                "container_network": "none",
                "container_root_filesystem": "read-only",
                "container_memory_limit_bytes": 536_870_912,
                "container_cpu_limit": 1.0,
            },
            "convergence": {
                "normal_termination": normal_termination,
                "process_returncode": execution.returncode,
                "solver": {
                    "complexity": config.solver_complexity,
                    "outer_maximum": config.outer_maximum,
                    "inner_maximum": config.inner_maximum,
                    "outer_dvclose_m": config.outer_dvclose_m,
                    "inner_dvclose_m": config.inner_dvclose_m,
                    "rclose_m3_per_day": config.rclose_m3_per_day,
                    "linear_acceleration": config.linear_acceleration,
                },
            },
            "metrics": metrics,
            "acceptance": {
                "scientificStatus": {
                    "status": scientific_status,
                    "checks": checks,
                },
                "engineeringStatus": {
                    "status": "NOT_ASSESSED",
                    "reason": "The isolated Docker execution passed, but full platform Compose, snapshot restore, and archive rebuild were not run for this non-integrated spike.",
                },
                "agentExperienceStatus": {
                    "status": "NOT_ASSESSED",
                    "reason": "No provider or human experience review is part of this numerical dependency spike.",
                },
            },
            "files": files,
            "lineage": {
                "analytical_solution": "h(x)=h_left-(h_left-h_right)*x/L at DIS cell centres",
                "darcy_flow": "Q=K*thickness*width*(h_left-h_right)/L",
                "contour_handoff": "head.tif -> raster.contour@1.0.0",
            },
            "limitations": [
                "This uncalibrated synthetic benchmark is not a calibrated site prediction.",
                "The homogeneous, isotropic, confined, steady, source-free assumptions are analytical test conditions, not field suitability findings.",
                "Transient flow, WEL, RCH, OBS, PRT, calibration, uncertainty, and modflowapi are outside this ADV-02 spike.",
                "The contour JSON is a contract handoff to the existing workflow; this spike does not register or execute a parallel platform operator.",
                "A scientific PASS here establishes only the fixed analytical case; engineering and agent-experience acceptance remain NOT_ASSESSED.",
            ],
        }
        (workspace / "result.json").write_text(
            json.dumps(
                manifest, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(workspace, target)
        return manifest
    except Exception:
        shutil.rmtree(workspace, ignore_errors=True)
        raise


__all__ = [
    "FLOPY_VERSION",
    "MF6_ARCHIVE_SHA256",
    "MF6_BINARY_SHA256",
    "MF6_VERSION",
    "Modflow6SpikeConfig",
    "Modflow6SpikeError",
    "analytical_darcy_flow",
    "analytical_heads",
    "extract_verified_mf6_build_inputs",
    "load_spike_config",
    "run_modflow6_spike",
    "verify_mf6_docker_image",
    "verify_mf6_release_inputs",
]
