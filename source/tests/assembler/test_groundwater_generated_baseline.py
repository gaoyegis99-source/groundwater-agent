import hashlib
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "platform" / "assembler"))
from assembler import compile_product  # noqa: E402


# Groundwater assembly with exact Workflow and Business Capability selections.
GENERATED_ARTIFACT_DIGESTS = {
    "kernel-config.json": "94cd93ef781588edf1f44e3ad7714adc15778405591f52b4ae7174b80472a10b",
    "executor-config.json": "2134daf0942342092fd83f151958204d069ef60ad378d3113c94b9fcd3033858",
    "compose.json": "78fd01ffab2246c88f238b91ce2b18bc06c5d3814b72784b2424e1d79bdfd052",
    "nginx.conf": "67eb04ebe6d61d377e7995a0abe181bdb199d7b865ba8a9af4a19a721418bd09",
    "product-ui.json": "c82739b8847402f0784a6dfe9131d36a9abea29cf4c57187348221a00751e901",
    "product-icon.svg": "e484a9befc9ff8ccabff776babccdcb0f0c8cf6adebc6da25bb64fccf5a0dfab",
    "assembly-lock.json": "a11e9c4f9e20f387a00cf37c7bb714786ada77208f89f431f7c368214211abc5",
    "workflow-platform.json": "4b43ac6d3f6395f45f63886b91cd066c8432bd67d9a63ad2207413a9a02f8852",
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
