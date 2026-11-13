from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import pytest

from assembler import compile_product


ROOT = Path(__file__).resolve().parents[2]


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


@pytest.fixture
def source(tmp_path: Path) -> Path:
    ignored = shutil.ignore_patterns(
        ".generated", ".pytest_cache", ".ruff_cache", ".venv", "__pycache__",
        "build", "dist", "node_modules",
    )
    for name in ("kernel", "services", "products", "packages", "domains"):
        shutil.copytree(ROOT / name, tmp_path / name, ignore=ignored)
    shutil.copy2(ROOT / "system-deps.lock.json", tmp_path / "system-deps.lock.json")
    return tmp_path


def _profile(source: Path) -> tuple[Path, Path, dict]:
    product_path = source / "products/groundwater/product-manifest.json"
    product = json.loads(product_path.read_text())
    profile_path = product_path.parent / product["uiProfile"]["path"]
    return product_path, profile_path, json.loads(profile_path.read_text())


def _save_profile(source: Path, profile: dict) -> None:
    product_path, profile_path, _ = _profile(source)
    _write(profile_path, profile)
    product = json.loads(product_path.read_text())
    product["uiProfile"]["checksum"] = "sha256:" + hashlib.sha256(profile_path.read_bytes()).hexdigest()
    _write(product_path, product)


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (lambda value: value["models"].pop("groundwater.summary"), "PRODUCT_UI_MODELS_INVALID"),
        (lambda value: value.update(theme={"accent": "url(/external)"}), "PRODUCT_UI_SCHEMA_INVALID"),
        (lambda value: value.update(theme={"unknown": "#123456"}), "PRODUCT_UI_SCHEMA_INVALID"),
        (lambda value: value["parameterNames"].pop("alpha"), "PRODUCT_UI_PARAMETER_MISSING"),
        (lambda value: value["parameterNames"]["axis"]["values"].pop("flatten"), "PRODUCT_UI_ENUM_INVALID"),
        (lambda value: value["parameterNames"].update(unknown={"label": "未知", "group": "未知"}), "PRODUCT_UI_PARAMETER_UNKNOWN"),
        (lambda value: value["parameterNames"].update(data={"label": "悬空参数", "group": "输入资料"}), "PRODUCT_UI_PARAMETER_UNKNOWN"),
        (lambda value: value["parameterStages"].update(unknown={"label": "未知", "group": "未知"}), "PRODUCT_UI_PARAMETER_UNKNOWN"),
        (lambda value: value["parameterNames"]["axis"]["values"].update(unknown="未知"), "PRODUCT_UI_ENUM_INVALID"),
        (lambda value: value["parameterNames"]["alpha"].update(unit=1), "PRODUCT_UI_PARAMETER_INVALID"),
        (lambda value: value["parameterNames"]["axis"]["values"].update(flatten=""), "PRODUCT_UI_ENUM_INVALID"),
        (lambda value: value["outputs"].pop("summary"), "PRODUCT_UI_OUTPUTS_INVALID"),
        (lambda value: value["cases"].pop("yaozhanbao-transfer-case"), "PRODUCT_UI_CASES_INVALID"),
        (lambda value: value["models"]["groundwater.summary"].update(unknown="value"), "PRODUCT_UI_MODEL_FIELDS_INVALID"),
    ],
)
def test_product_ui_must_completely_and_strictly_cover_domain(source: Path, mutate, error: str) -> None:
    _, _, profile = _profile(source)
    mutate(profile)
    _save_profile(source, profile)
    with pytest.raises(ValueError, match=error):
        compile_product(source, source / "products/groundwater/product-manifest.json")
    assert not (source / ".generated").exists()


def test_product_ui_source_checksum_is_verified_before_output(source: Path) -> None:
    _, profile_path, profile = _profile(source)
    profile["brand"]["welcome"] += " changed"
    _write(profile_path, profile)
    with pytest.raises(ValueError, match="PRODUCT_UI_CHECKSUM_MISMATCH"):
        compile_product(source, source / "products/groundwater/product-manifest.json")
    assert not (source / ".generated").exists()


def test_product_ui_reference_cannot_escape_product(source: Path) -> None:
    product_path, profile_path, _ = _profile(source)
    outside = source / "products/outside.json"
    shutil.copy2(profile_path, outside)
    product = json.loads(product_path.read_text())
    product["uiProfile"] = {
        "path": "../outside.json",
        "checksum": "sha256:" + hashlib.sha256(outside.read_bytes()).hexdigest(),
    }
    _write(product_path, product)
    with pytest.raises(ValueError, match="PRODUCT_UI_REFERENCE_INVALID"):
        compile_product(source, product_path)


@pytest.mark.parametrize(
    "svg",
    [
        '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg"><path onclick="alert(1)"/></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg"><foreignObject/></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.test/a.png"/></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg"><style>@import url(https://example.test/a.css)</style></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg"><path style="@import \'https://example.test/a.css\'"/></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg"><use href="//example.test/a.svg#x"/></svg>',
        '<?xml-stylesheet href="https://example.test/a.css"?><svg xmlns="http://www.w3.org/2000/svg"/>',
        '<!DOCTYPE svg [<!ENTITY x "test">]><svg xmlns="http://www.w3.org/2000/svg">&x;</svg>',
    ],
)
def test_product_icon_rejects_active_or_external_svg(source: Path, svg: str) -> None:
    _, _, profile = _profile(source)
    icon_path = source / "products/groundwater/ui/product-icon.svg"
    icon_path.write_text(svg)
    profile["brand"]["icon"]["checksum"] = "sha256:" + hashlib.sha256(icon_path.read_bytes()).hexdigest()
    _save_profile(source, profile)
    with pytest.raises(ValueError, match="PRODUCT_UI_ICON_INVALID"):
        compile_product(source, source / "products/groundwater/product-manifest.json")


def test_product_icon_allows_local_fragment_references(source: Path) -> None:
    _, _, profile = _profile(source)
    icon_path = source / "products/groundwater/ui/product-icon.svg"
    icon_path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><defs><linearGradient id="g"/></defs>'
        '<path fill="url(#g)" d="M0 0h1v1z"/></svg>'
    )
    profile["brand"]["icon"]["checksum"] = (
        "sha256:" + hashlib.sha256(icon_path.read_bytes()).hexdigest()
    )
    _save_profile(source, profile)
    output = compile_product(
        source, source / "products/groundwater/product-manifest.json"
    )
    assert "url(#g)" in (output / "product-icon.svg").read_text()


def test_ui_artifacts_lock_gateway_and_no_cache_are_one_assembly(source: Path) -> None:
    output = compile_product(source, source / "products/groundwater/product-manifest.json")
    lock = json.loads((output / "assembly-lock.json").read_text())
    compose = json.loads((output / "compose.json").read_text())
    nginx = (output / "nginx.conf").read_text()
    assert lock["schemaVersion"] == "assembly-lock.v8"
    assert lock["productUi"]["profileOutputChecksum"].startswith("sha256:")
    assert lock["productUi"]["iconChecksum"].startswith("sha256:")
    volumes = compose["services"]["gateway"]["volumes"]
    assert any("product-ui.json:/usr/share/nginx/html/product-ui.json:ro" in item for item in volumes)
    assert any("product-icon.svg:/usr/share/nginx/html/product-icon.svg:ro" in item for item in volumes)
    for path in ("/product-ui.json", "/product-icon.svg"):
        assert f"location = {path}" in nginx
        location = next(
            line for line in nginx.splitlines() if f"location = {path}" in line
        )
        assert "auth_request off;" in location
    assert 'add_header Cache-Control "no-store" always;' in nginx
