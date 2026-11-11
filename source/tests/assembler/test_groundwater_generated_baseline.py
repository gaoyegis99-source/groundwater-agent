import hashlib
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "platform" / "assembler"))
from assembler import compile_product  # noqa: E402


GENERATED_ARTIFACT_DIGESTS = {
    "kernel-config.json": "76d0b149a73bfb20e557e1ab4ae99fcf297e5ee11de295ab04adb5b57287b6be",
    "executor-config.json": "3f5a8efee314045969d6a8378db3866c68dfbe77d8adacf557b71fdb6a292037",
    "compose.json": "654a44d54f0f79a73750e232a9b4546fb15b721e37ce7df36eafb6cd95b1d467",
    "nginx.conf": "301325455147fe4416b0031474f7512da574e5019932689e1e1008060726ba1b",
    "product-ui.json": "abb88199e9e7be44bd9b9c30bda2a15889c435ea15648bea23fcb5c3f068eb0b",
    "product-icon.svg": "e484a9befc9ff8ccabff776babccdcb0f0c8cf6adebc6da25bb64fccf5a0dfab",
    "assembly-lock.json": "e231ae6e5f569d29a8b92acff8c0623a73bba054010f5c6f1395413ac4292219",
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
