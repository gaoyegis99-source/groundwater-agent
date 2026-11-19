import hashlib
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "platform" / "assembler"))
from assembler import compile_product  # noqa: E402


# Groundwater product assembly with the composition capability catalog.
GENERATED_ARTIFACT_DIGESTS = {
    "kernel-config.json": "d1e0a9c515bfa08a6aabdc1656e077d7e88d8617532b754641bf61424948c0ce",
    "executor-config.json": "cdd55c9aab424cd082e009ee9b03160aa0302d7fd43ecaf03a335cb37827e345",
    "compose.json": "78fd01ffab2246c88f238b91ce2b18bc06c5d3814b72784b2424e1d79bdfd052",
    "nginx.conf": "c81e59fd7defcb52d99c9879c0a844abb0048167281f3278fcf802de63f51ecf",
    "product-ui.json": "cb970a637a5424bb8495dd0a125c3b21b4ad88d89c14cf5f406e1821bf5f31d2",
    "product-icon.svg": "e484a9befc9ff8ccabff776babccdcb0f0c8cf6adebc6da25bb64fccf5a0dfab",
    "assembly-lock.json": "6cb499edf0e5cc77e071afed36ded5a6552f3cc4c0462db5fc648a796bc48798"
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
