import hashlib
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "platform" / "assembler"))
from assembler import compile_product  # noqa: E402


# Groundwater product assembly with the composition capability catalog.
GENERATED_ARTIFACT_DIGESTS = {
    "kernel-config.json": "deb81a1abc455ab29b500b6e7109a17047369af264bba56bbd81b8eaeec0a704",
    "executor-config.json": "cdd55c9aab424cd082e009ee9b03160aa0302d7fd43ecaf03a335cb37827e345",
    "compose.json": "0a49a99487fa4f815492fe704234910ad113d9e38379e7e6aec0ce9bfd13287b",
    "nginx.conf": "2921970a6397dec6f4b4f81c2afaa334e8bdf651be8d12d5b90f71f3a7919d23",
    "product-ui.json": "9b7295796133f112e00ec356650072ad1d8040d3b35f358d37c47d1e304bdf85",
    "product-icon.svg": "e484a9befc9ff8ccabff776babccdcb0f0c8cf6adebc6da25bb64fccf5a0dfab",
    "assembly-lock.json": "8d04cda61150fcb7732bc6ce0bd0c760d33028836bb5223a8d7e76125a2c3529"
}


def _baseline_bytes(output: Path, name: str) -> bytes:
    payload = (output / name).read_bytes()
    if name == "compose.json":
        # Compose bind mounts must use real absolute paths at deployment time.
        # Those two roots are location, not product configuration identity.
        for location, marker in (
            (output, b"${ASSEMBLY_ROOT}"),
            (ROOT, b"${SOURCE_ROOT}"),
        ):
            encoded = str(location).encode("utf-8")
            assert encoded in payload
            payload = payload.replace(encoded, marker)
    return payload


def test_groundwater_v12_generated_configuration_bytes_are_frozen() -> None:
    root = compile_product(ROOT, ROOT / "products/groundwater/product-manifest.json")
    actual = {
        name: hashlib.sha256(_baseline_bytes(root, name)).hexdigest()
        for name in GENERATED_ARTIFACT_DIGESTS
    }
    assert actual == GENERATED_ARTIFACT_DIGESTS


def test_groundwater_compose_baseline_is_location_independent(
    tmp_path: Path, monkeypatch
) -> None:
    compose = []
    for directory in ("first", "second"):
        monkeypatch.setenv("PLATFORM_GENERATED_ROOT", str(tmp_path / directory))
        output = compile_product(
            ROOT, ROOT / "products/groundwater/product-manifest.json"
        )
        compose.append(_baseline_bytes(output, "compose.json"))
    assert compose[0] == compose[1]
