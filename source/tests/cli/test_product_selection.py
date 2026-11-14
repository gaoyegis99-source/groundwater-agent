from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.development.product_selection import (
    ProductSelectionRefusal,
    SelectedProduct,
    load_selected_product_by_id,
    resolve_selected_workflow,
)


ROOT = Path(__file__).resolve().parents[2]


def test_product_selection_resolves_current_groundwater_workflow() -> None:
    selected = load_selected_product_by_id(ROOT, "groundwater")

    workflow, path = resolve_selected_workflow(selected, "groundwater.flow-field")

    assert selected.domain.identity["version"] == "6.2.0"
    assert workflow["workflowId"] == "groundwater.flow-field"
    assert path.is_relative_to(ROOT / "domains/groundwater/6.2.0")


def test_product_selection_rejects_unknown_workflow() -> None:
    selected = load_selected_product_by_id(ROOT, "groundwater")

    with pytest.raises(ProductSelectionRefusal, match="PRODUCT_WORKFLOW_NOT_FOUND"):
        resolve_selected_workflow(selected, "groundwater.unknown")


def test_product_selection_rejects_duplicate_workflow_ids(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text("{}\n", encoding="utf-8")
    second.write_text("{}\n", encoding="utf-8")
    relative_paths = [first.name, second.name]
    domain = SimpleNamespace(
        root=tmp_path,
        manifest={"workflowDefinitions": relative_paths},
        definitions={
            first.name: {"workflowId": "groundwater.duplicate"},
            second.name: {"workflowId": "groundwater.duplicate"},
        },
    )
    selected = SelectedProduct(tmp_path / "product.json", {}, domain)

    with pytest.raises(ProductSelectionRefusal, match="PRODUCT_WORKFLOW_DUPLICATE"):
        resolve_selected_workflow(selected, "groundwater.duplicate")
