from __future__ import annotations

import json

import pytest

import scripts.development.dev as dev


def test_dev_capability_inventory_targets_owned_reference(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(dev, "activate", lambda **_kwargs: None)

    def generate_capability_inventory(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {
            "schemaVersion": "capability-inventory-generation.v1",
            "status": "PASS",
            "outputPath": str(kwargs["output_path"]),
        }

    monkeypatch.setattr(
        dev,
        "generate_capability_inventory",
        generate_capability_inventory,
    )

    dev.main(["capability-inventory", "--verify"])

    assert calls == [
        {
            "repository_root": dev.ROOT,
            "product_manifest_path": (
                dev.ROOT / "products/groundwater/product-manifest.json"
            ),
            "output_path": dev.ROOT
            / "docs/development/current-capability-inventory.md",
            "check": True,
        }
    ]
    assert json.loads(capsys.readouterr().out)["status"] == "PASS"


def test_dev_capability_inventory_returns_stable_drift_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts.development.capability_inventory import CapabilityInventoryRefusal

    monkeypatch.setattr(dev, "activate", lambda **_kwargs: None)
    monkeypatch.setattr(
        dev,
        "generate_capability_inventory",
        lambda **_kwargs: (_ for _ in ()).throw(
            CapabilityInventoryRefusal("CAPABILITY_INVENTORY_OUT_OF_DATE")
        ),
    )

    with pytest.raises(SystemExit, match="2"):
        dev.main(["capability-inventory", "--verify"])

    assert json.loads(capsys.readouterr().err) == {
        "code": "CAPABILITY_INVENTORY_OUT_OF_DATE",
        "status": "FAILED",
    }
