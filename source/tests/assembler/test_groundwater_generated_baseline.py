import hashlib
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "platform" / "assembler"))
from assembler import compile_product  # noqa: E402


GENERATED_ARTIFACT_DIGESTS = {
    "kernel-config.json": "db225cfc57f1825a4759243b9cd347787987cdf20d7d4177f931ac01a7180e86",
    "executor-config.json": "e05858577baccc8b4c35755fdd2635ef0e864c3dca2edc46e0613d8bad07bb31",
    "compose.json": "654a44d54f0f79a73750e232a9b4546fb15b721e37ce7df36eafb6cd95b1d467",
    "nginx.conf": "301325455147fe4416b0031474f7512da574e5019932689e1e1008060726ba1b",
    "product-ui.json": "e59518d38aa3de22a2381171b9b953b4d35097b426b2e63d2d6bc15445a2115f",
    "product-icon.svg": "e484a9befc9ff8ccabff776babccdcb0f0c8cf6adebc6da25bb64fccf5a0dfab",
    "assembly-lock.json": "640ffe3c83887e3b3c431dd8479ed62e296304a2cecde54d4a11a7d6376dafae",
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
