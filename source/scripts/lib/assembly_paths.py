"""Resolve immutable assembler bundles through regular JSON current pointers."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
ASSEMBLER_ROOT = ROOT / "platform" / "assembler"
if str(ASSEMBLER_ROOT) not in sys.path:
    sys.path.insert(0, str(ASSEMBLER_ROOT))

from assembler_publication import current_bundle, generated_root  # noqa: E402


def compiled_bundle(root: Path = ROOT, product_id: str = "groundwater") -> Path:
    """Return the current immutable bundle selected for ``product_id``."""
    return current_bundle(generated_root(root), product_id)


def bundle_from_pointer_file(pointer: Path) -> Path:
    """Resolve a regular ``<product>.current.json`` file to its release directory."""
    suffix = ".current.json"
    if not pointer.name.endswith(suffix):
        raise ValueError("OUTPUT_POINTER_INVALID")
    return current_bundle(pointer.parent, pointer.name.removesuffix(suffix))
