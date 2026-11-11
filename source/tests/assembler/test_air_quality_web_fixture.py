from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

from assembler import compile_product


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "build_air_quality_web_fixture",
    ROOT / "tests/e2e/build_air_quality_web_fixture.py",
)
assert SPEC and SPEC.loader
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)


def test_air_quality_fixture_assembles_when_source_product_uses_another_ui_profile(
    tmp_path: Path,
) -> None:
    FIXTURE._copy_assembly_inputs(tmp_path)
    source_product = tmp_path / "products/groundwater"
    source_profile = source_product / "ui/alternate-profile.v2.json"
    shutil.copy2(source_product / "ui/product-ui.json", source_profile)
    source_manifest = source_product / "product-manifest.json"
    manifest = json.loads(source_manifest.read_text(encoding="utf-8"))
    manifest["uiProfile"] = {
        "path": "ui/alternate-profile.v2.json",
        "checksum": "sha256:" + hashlib.sha256(source_profile.read_bytes()).hexdigest(),
    }
    source_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    original_manifest = source_manifest.read_bytes()
    original_profile = source_profile.read_bytes()

    air_manifest = FIXTURE._create_air_quality_product(tmp_path)
    output = compile_product(tmp_path, air_manifest)
    profile = json.loads((output / "product-ui.json").read_text(encoding="utf-8"))
    kernel = json.loads((output / "kernel-config.json").read_text(encoding="utf-8"))

    assert profile["productId"] == kernel["productId"] == "air-quality"
    assert set(profile["models"]) == {"air-quality.summary"}
    assert profile["examples"][0]["prompt"] == "请汇总 PM2.5 观测。"
    assert source_manifest.read_bytes() == original_manifest
    assert source_profile.read_bytes() == original_profile
