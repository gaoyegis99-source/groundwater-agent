import hashlib
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "platform" / "assembler"))
from assembler import compile_product  # noqa: E402


# Groundwater product assembly with the composition capability catalog.
GENERATED_ARTIFACT_DIGESTS = {
    "kernel-config.json": "1390999416df7c11875126202910aefcac92d601955314d8064849a92b8c597a",
    "executor-config.json": "48ee106cf00009cd7c742bddc8f1283aaa26657109ceb8f35691c6bc13bf15b1",
    "compose.json": "78fd01ffab2246c88f238b91ce2b18bc06c5d3814b72784b2424e1d79bdfd052",
    "nginx.conf": "c81e59fd7defcb52d99c9879c0a844abb0048167281f3278fcf802de63f51ecf",
    "product-ui.json": "a5424dfafd27d95e6e5d4c63e00074aed24ebfd8e05c47bbe7dd3a19d6b97bfd",
    "product-icon.svg": "e484a9befc9ff8ccabff776babccdcb0f0c8cf6adebc6da25bb64fccf5a0dfab",
    "assembly-lock.json": "b4394051bd0e39dd54590aadb3003f46d2bc36b3d419358728ce5d4942057331"
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
