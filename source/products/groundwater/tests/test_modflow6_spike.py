from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pytest


PRODUCT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PRODUCT_ROOT.parents[1]
if str(PRODUCT_ROOT) not in sys.path:
    sys.path.insert(0, str(PRODUCT_ROOT))

from research.modflow6_spike import (  # noqa: E402
    FLOPY_VERSION,
    MF6_ARCHIVE_SHA256,
    MF6_BINARY_SHA256,
    MF6_VERSION,
    Modflow6SpikeError,
    analytical_darcy_flow,
    analytical_heads,
    load_spike_config,
    run_modflow6_spike,
    verify_mf6_release_inputs,
)


SPIKE_ROOT = PRODUCT_ROOT / "spikes/modflow6"
CONFIG = SPIKE_ROOT / "steady-linear.config.json"


def _config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def test_modflow6_config_freezes_the_analytical_boundary() -> None:
    config = load_spike_config(CONFIG)

    assert config.nlay == 1
    assert config.nrow == 3
    assert config.ncol == 11
    assert config.delr_m == 100.0
    assert config.boundary_head_distance_m == 1000.0
    assert config.hydraulic_conductivity_m_per_day == 10.0
    assert config.left_head_m == 10.0
    assert config.right_head_m == 9.0
    assert config.crs == "EPSG:32650"
    assert config.temporal_mode == "steady"
    assert config.random_seed is None

    expected = analytical_heads(config)
    assert expected.shape == (1, 3, 11)
    assert expected[0, 0].tolist() == pytest.approx(
        [10.0, 9.9, 9.8, 9.7, 9.6, 9.5, 9.4, 9.3, 9.2, 9.1, 9.0]
    )
    assert expected[0, 0].tolist() == pytest.approx(expected[0, 2].tolist())
    assert analytical_darcy_flow(config) == pytest.approx(30.0)

    transient = _config()
    transient["time"]["mode"] = "transient"
    with pytest.raises(Modflow6SpikeError, match="MF6_SPIKE_TIME_UNSUPPORTED"):
        load_spike_config(transient)

    unconfined = _config()
    unconfined["hydraulics"]["cell_type"] = "convertible"
    with pytest.raises(Modflow6SpikeError, match="MF6_SPIKE_CELL_TYPE_UNSUPPORTED"):
        load_spike_config(unconfined)

    hidden_distance = _config()
    hidden_distance["grid"]["boundary_head_distance_m"] = 1100.0
    with pytest.raises(Modflow6SpikeError, match="MF6_SPIKE_BOUNDARY_DISTANCE_INVALID"):
        load_spike_config(hidden_distance)

    reversed_heads = _config()
    reversed_heads["boundaries"]["right_head_m"] = 10.5
    with pytest.raises(Modflow6SpikeError, match="MF6_SPIKE_HEAD_BOUNDARY_INVALID"):
        load_spike_config(reversed_heads)


def test_flopy_is_locked_outside_every_default_runtime() -> None:
    audit = json.loads(
        (SPIKE_ROOT / "dependency-audit.json").read_text(encoding="utf-8")
    )

    assert FLOPY_VERSION == "3.12.0"
    assert audit["decision"] == "ACCEPTED_FOR_INDEPENDENT_ADV_02_SPIKE_ONLY"
    assert audit["default_runtime_changed"] is False
    assert audit["requested_extras"] == []
    assert audit["package"] == {
        "name": "flopy",
        "version": "3.12.0",
        "requires_python": ">=3.12",
        "license_expression": "CC0-1.0",
        "wheel": {
            "filename": "flopy-3.12.0-py3-none-any.whl",
            "python_tag": "py3-none-any",
            "sha256": "sha256:e3642d60ab9e94de8531200611c5c76e4d39e85416e0d81024d4b4d373ba0269",
            "size_bytes": 1528814,
        },
    }
    assert audit["declared_runtime_requirements"] == [
        "matplotlib>=1.4.0",
        "numpy<3.0,>=2.2.0",
        "pandas>=2.2.0",
    ]

    requirements = (SPIKE_ROOT / "requirements.lock").read_text(encoding="utf-8")
    assert "flopy==3.12.0" in requirements
    assert (
        "e3642d60ab9e94de8531200611c5c76e4d39e85416e0d81024d4b4d373ba0269"
        in requirements
    )
    spike_lock = (SPIKE_ROOT / "uv.lock").read_text(encoding="utf-8")
    assert 'name = "flopy"' in spike_lock
    assert 'version = "3.12.0"' in spike_lock

    default_dependency_inputs = (
        SOURCE_ROOT / "pyproject.toml",
        SOURCE_ROOT / "uv.lock",
        SOURCE_ROOT / "environments/native/pyproject.toml",
        SOURCE_ROOT / "environments/native/requirements.lock",
        SOURCE_ROOT / "environments/native/uv.lock",
        SOURCE_ROOT / "environments/qgis/pyproject.toml",
        SOURCE_ROOT / "environments/qgis/requirements.lock",
        SOURCE_ROOT / "environments/qgis/uv.lock",
    )
    assert all(
        "flopy" not in path.read_text(encoding="utf-8").lower()
        for path in default_dependency_inputs
    )


def test_mf6_binary_lock_and_dockerfile_reject_unapproved_supply_paths() -> None:
    lock = json.loads((SPIKE_ROOT / "mf6-binary.lock.json").read_text(encoding="utf-8"))

    assert MF6_VERSION == "6.8.1"
    assert lock["decision"] == "ACCEPTED_FOR_INDEPENDENT_ADV_02_DOCKER_SPIKE_ONLY"
    assert lock["default_runtime_changed"] is False
    assert lock["release"]["authority"] == "U.S. Geological Survey"
    assert lock["release"]["version"] == "6.8.1"
    assert lock["release"]["architecture"] == "linux-x86_64"
    assert lock["release"]["license"] == "Public Domain, CC0-1.0"
    assert lock["archive"]["sha256"] == MF6_ARCHIVE_SHA256
    assert lock["binary"]["sha256"] == MF6_BINARY_SHA256
    assert lock["binary"]["elf_class"] == "ELF64"
    assert lock["binary"]["elf_machine"] == "x86-64"

    approved_urls = [
        lock["release"]["metadata_url"],
        lock["archive"]["url"],
        lock["license_evidence"]["license_url"],
        lock["license_evidence"]["disclaimer_url"],
    ]
    assert all(url.startswith("https://code.usgs.gov/") for url in approved_urls)
    assert all("github.com" not in url.lower() for url in approved_urls)

    dockerfile = (SPIKE_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM ubuntu@sha256:" in dockerfile
    assert "COPY --chmod=0555 mf6 /usr/local/bin/mf6" in dockerfile
    assert 'ENTRYPOINT ["/usr/local/bin/mf6"]' in dockerfile
    lowered = dockerfile.lower()
    assert "curl " not in lowered
    assert "wget " not in lowered
    assert "apt-get" not in lowered
    assert "github.com" not in lowered
    assert "http://" not in lowered
    assert "https://" not in lowered


def test_official_mf6_release_archive_matches_every_locked_identity() -> None:
    archive = os.environ.get("GROUNDWATER_MF6_681_ARCHIVE")
    license_path = os.environ.get("GROUNDWATER_MF6_681_LICENSE")
    disclaimer_path = os.environ.get("GROUNDWATER_MF6_681_DISCLAIMER")
    if not archive or not license_path or not disclaimer_path:
        pytest.skip("official MF6 6.8.1 release inputs were not provided")

    evidence = verify_mf6_release_inputs(
        Path(archive), Path(license_path), Path(disclaimer_path)
    )
    assert evidence["version"] == "6.8.1"
    assert evidence["architecture"] == "linux-x86_64"
    assert evidence["license"] == "Public Domain, CC0-1.0"
    assert evidence["archive_sha256"] == MF6_ARCHIVE_SHA256
    assert evidence["binary_sha256"] == MF6_BINARY_SHA256


def test_modflow6_spike_refuses_runtime_state_inside_source_tree(
    tmp_path: Path,
) -> None:
    image = "groundwater-mf6-spike:6.8.1"
    forbidden_output = SOURCE_ROOT / f".mf6-spike-forbidden-{tmp_path.name}"

    with pytest.raises(
        Modflow6SpikeError,
        match="MF6_SPIKE_OUTPUT_MUST_BE_ABSOLUTE",
    ):
        run_modflow6_spike(CONFIG, Path("relative-output"), image)

    with pytest.raises(
        Modflow6SpikeError,
        match="MF6_SPIKE_OUTPUT_MUST_BE_EXTERNAL",
    ):
        run_modflow6_spike(CONFIG, forbidden_output, image)


def test_flopy_312_and_mf6_681_solve_the_dockerized_linear_benchmark(
    tmp_path: Path,
) -> None:
    flopy = pytest.importorskip(
        "flopy", reason="independent FloPy spike environment is not active"
    )
    image = os.environ.get("GROUNDWATER_MF6_SPIKE_IMAGE")
    if not image:
        pytest.skip("independent MF6 Docker spike image was not selected")
    assert flopy.__version__ == "3.12.0"

    output = tmp_path / "modflow6-steady-linear"
    manifest = run_modflow6_spike(CONFIG, output, image)

    assert output.is_dir()
    assert manifest["schema_version"] == "groundwater-modflow6-spike-result.v1"
    assert manifest["scope"] == "INDEPENDENT_ADV_02_SPIKE_ONLY"
    assert manifest["model"]["calibration_status"] == "UNCALIBRATED_SYNTHETIC"
    assert manifest["runtime"]["flopy_version"] == "3.12.0"
    assert manifest["runtime"]["mf6_version"] == "6.8.1"
    assert manifest["runtime"]["container_network"] == "none"
    assert manifest["runtime"]["container_root_filesystem"] == "read-only"
    assert manifest["convergence"]["normal_termination"] is True

    acceptance = manifest["acceptance"]
    assert acceptance["scientificStatus"]["status"] == "PASS"
    assert acceptance["engineeringStatus"]["status"] == "NOT_ASSESSED"
    assert acceptance["agentExperienceStatus"]["status"] == "NOT_ASSESSED"

    metrics = manifest["metrics"]
    assert metrics["maximum_head_absolute_error_m"] <= 1.0e-8
    assert metrics["analytical_total_flow_m3_per_day"] == pytest.approx(30.0)
    assert metrics["simulated_inflow_m3_per_day"] == pytest.approx(30.0, rel=1.0e-7)
    assert metrics["simulated_outflow_m3_per_day"] == pytest.approx(30.0, rel=1.0e-7)
    assert metrics["darcy_relative_error"] <= 1.0e-7
    assert metrics["budget_percent_discrepancy"] <= 1.0e-6

    required = {
        "mfsim.nam",
        "linear-gwf.hds",
        "linear-gwf.cbc",
        "linear-gwf.lst",
        "mf6-stdout.txt",
        "heads.csv",
        "head.tif",
        "contour-handoff.json",
        "result.json",
    }
    assert all((output / file_name).is_file() for file_name in required)
    assert required - {"result.json"} <= set(manifest["files"])
    assert "result.json" not in manifest["files"]
    assert all(
        metadata["sha256"].startswith("sha256:")
        for metadata in manifest["files"].values()
    )

    handoff = json.loads((output / "contour-handoff.json").read_text(encoding="utf-8"))
    assert handoff["operator"] == {"id": "raster.contour", "version": "1.0.0"}
    assert handoff["input"] == {
        "file": "head.tif",
        "format": "geotiff",
        "mediaType": "image/tiff",
        "crs": "EPSG:32650",
        "unit": "m",
    }
    assert handoff["parameters"] == {
        "band": 1,
        "intervalMeters": 0.1,
        "offsetMeters": 0.0,
    }

    from PIL import Image

    with Image.open(output / "head.tif") as raster:
        assert raster.size == (11, 3)
        assert tuple(raster.tag_v2[33550]) == pytest.approx((100.0, 100.0, 0.0))
        assert 32650 in tuple(raster.tag_v2[34735])

    persisted = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert persisted == manifest
    assert any(
        "not a calibrated site prediction" in item for item in manifest["limitations"]
    )
