"""Resolve product-owned sources through the pinned product/domain identity."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ASSEMBLER_ROOT = REPOSITORY_ROOT / "platform/assembler"
if str(ASSEMBLER_ROOT) not in sys.path:
    sys.path.insert(0, str(ASSEMBLER_ROOT))

from assembler_common import DomainPackage  # noqa: E402
from assembler_manifests import _load_manifest, _selected_domain  # noqa: E402


class ProductSelectionRefusal(ValueError):  # noqa: N818
    """A product did not resolve to one exact, internally consistent source."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


@dataclass(frozen=True)
class SelectedProduct:
    manifest_path: Path
    manifest: dict[str, Any]
    domain: DomainPackage


def load_selected_product(
    repository_root: Path, product_manifest_path: Path
) -> SelectedProduct:
    """Load and identity-check the domain selected by one product manifest."""

    repository = repository_root.resolve()
    try:
        manifest_path, manifest = _load_manifest(repository, product_manifest_path)
        domain = _selected_domain(repository, manifest["domainPackage"])
    except (KeyError, OSError, TypeError, ValueError) as error:
        raise ProductSelectionRefusal(
            "PRODUCT_SELECTION_INVALID", str(error)
        ) from error
    return SelectedProduct(manifest_path, manifest, domain)


def load_selected_product_by_id(
    repository_root: Path, product_id: str
) -> SelectedProduct:
    if not product_id or Path(product_id).name != product_id:
        raise ProductSelectionRefusal("PRODUCT_ID_INVALID")
    return load_selected_product(
        repository_root,
        repository_root / "products" / product_id / "product-manifest.json",
    )


def resolve_selected_workflow(
    selected: SelectedProduct, workflow_id: object
) -> tuple[dict[str, Any], Path]:
    """Resolve a workflow ID only from the selected domain manifest."""

    if not isinstance(workflow_id, str) or not workflow_id:
        raise ProductSelectionRefusal("PRODUCT_WORKFLOW_ID_INVALID")
    matches: list[tuple[dict[str, Any], Path]] = []
    for relative in selected.domain.manifest["workflowDefinitions"]:
        definition = selected.domain.definitions.get(relative)
        if isinstance(definition, dict) and definition.get("workflowId") == workflow_id:
            matches.append((definition, selected.domain.root / relative))
    if not matches:
        raise ProductSelectionRefusal("PRODUCT_WORKFLOW_NOT_FOUND", workflow_id)
    if len(matches) != 1:
        raise ProductSelectionRefusal("PRODUCT_WORKFLOW_DUPLICATE", workflow_id)
    definition, path = matches[0]
    if path.is_symlink() or not path.is_file():
        raise ProductSelectionRefusal("PRODUCT_WORKFLOW_PATH_INVALID", str(path))
    return definition, path
