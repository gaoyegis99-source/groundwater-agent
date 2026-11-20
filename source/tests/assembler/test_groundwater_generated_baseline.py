import hashlib
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "platform" / "assembler"))
from assembler import compile_product  # noqa: E402


# Groundwater assembly with exact Workflow and Business Capability selections.
GENERATED_ARTIFACT_DIGESTS = {
    "kernel-config.json": "cf1273702cb8061a4f5c46bac8bffa79e847e0f1f77af8c4ac5e28a96f6c4111",
    "executor-config.json": "48ee106cf00009cd7c742bddc8f1283aaa26657109ceb8f35691c6bc13bf15b1",
    "compose.json": "78fd01ffab2246c88f238b91ce2b18bc06c5d3814b72784b2424e1d79bdfd052",
    "nginx.conf": "67eb04ebe6d61d377e7995a0abe181bdb199d7b865ba8a9af4a19a721418bd09",
    "product-ui.json": "4e1eff274ec9662f0f0398d48c2335630b54fd01f4cd78d03134dd74e18c2250",
    "product-icon.svg": "e484a9befc9ff8ccabff776babccdcb0f0c8cf6adebc6da25bb64fccf5a0dfab",
    "assembly-lock.json": "a5a7d344e673615b528f0890612c87d367a2734f2053683b099bc82ce9918e07",
    "workflow-platform.json": "9f986fca88a8f8da70cae7db9c9ac8e4e5ad4cc23c40eee5bc1b8b8bc1de4ffc",
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
