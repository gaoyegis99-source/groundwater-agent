import hashlib
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "platform" / "assembler"))
from assembler import compile_product  # noqa: E402


# Domain 6.1.0: both model bindings, ten porous operations and Product UI.
GENERATED_ARTIFACT_DIGESTS = {
    "kernel-config.json": "254a5f466137466bd2093ae86ed35f356393d2879b572956c16d5358a3da65e0",
    "executor-config.json": "5a62764f3382afaabde6f77f828c2121589ba6ab180d46401d8709c40b975a56",
    "compose.json": "0d542bd54633df8e514a0744f5d08cdaa2781a05c6977145f3bd783d3cdccd98",
    "nginx.conf": "301325455147fe4416b0031474f7512da574e5019932689e1e1008060726ba1b",
    "product-ui.json": "8629570bfac04fb36a555a66bc1df99c0d4710d10ba92c7ae2c861b3491b903f",
    "product-icon.svg": "e484a9befc9ff8ccabff776babccdcb0f0c8cf6adebc6da25bb64fccf5a0dfab",
    "assembly-lock.json": "48ef1c439b02a4f67d9a449cbda1a5ee1ef0aed82ce5b79636a107cf8ad4d19f"
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
