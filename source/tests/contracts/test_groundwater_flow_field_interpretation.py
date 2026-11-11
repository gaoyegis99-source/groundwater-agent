from __future__ import annotations

import json
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[2]
MODEL = SOURCE / "domains/groundwater/6.0.0/models/flow-field.model.json"
UI = SOURCE / "products/groundwater/ui/product-ui.json"
OPERATORS = SOURCE / "packages/operator-pack/catalog/operators"

OUTPUTS = {
    "snapshot",
    "snapshot-diagnostics",
    "head-raster",
    "gradient",
    "flow-direction",
    "darcy-flux",
    "seepage-velocity",
    "summary",
}


def test_flow_field_keeps_outputs_and_publishes_scientific_semantics() -> None:
    model = json.loads(MODEL.read_text())

    assert model["version"] == "3.1.0"
    assert set(model["finalOutputs"]) == OUTPUTS
    assert set(model["outputPublications"]) == OUTPUTS
    for name, publication in model["outputPublications"].items():
        semantics = publication["semantics"]
        assert semantics["description"]
        assert not semantics["description"].startswith("groundwater.flow-field output")
        assert semantics["limitations"], name

    head = model["outputPublications"]["head-raster"]["semantics"]
    assert "interpolat" in head["description"].lower()
    assert "reference elevation" in head["description"].lower()
    assert "vertical datum" in str(head["vertical_datum"]).lower()
    assert "targetcrs" in str(head["crs"]).lower()

    direction = model["outputPublications"]["flow-direction"]["semantics"]
    assert "grid north" in direction["description"].lower()
    assert "decreasing hydraulic head" in direction["description"].lower()

    flux = model["outputPublications"]["darcy-flux"]["semantics"]
    seepage = model["outputPublications"]["seepage-velocity"]["semantics"]
    assert "specific discharge" in flux["description"].lower()
    assert "effective porosity" in seepage["description"].lower()
    combined = " ".join(flux["limitations"] + seepage["limitations"]).lower()
    assert "not an observed" in combined
    assert "isotropic" in combined


def test_flow_field_preflight_and_ui_preserve_basis_and_derived_wording() -> None:
    model = json.loads(MODEL.read_text())
    requirements = model["semanticRequirements"]
    assert any(item["kind"] == "verticalBasis" for item in requirements)
    assert any(
        item["kind"] == "crsParameter"
        and item["parameter"] == "targetCrs"
        and item["requirement"] == "projectedMetric"
        for item in requirements
    )

    ui = json.loads(UI.read_text())
    assert "推导" in ui["outputs"]["darcy-flux"]["label"]
    assert "推导" in ui["outputs"]["seepage-velocity"]["label"]
    description = ui["models"]["groundwater.flow-field"]["description"]
    assert "观测" in description and "推导" in description


def test_flow_operator_summary_schemas_name_units_interpretation_and_limits() -> None:
    expected = {
        "groundwater.head.calculate": {
            "resultKind",
            "calculation",
            "outputUnit",
            "interpretation",
            "limitations",
        },
        "groundwater.hydraulic_gradient": {
            "resultKind",
            "gradientUnit",
            "directionConvention",
            "horizontalCrs",
            "interpretation",
            "limitations",
        },
        "groundwater.darcy_velocity": {
            "resultKind",
            "darcyFluxUnit",
            "seepageVelocityUnit",
            "darcyFluxFormula",
            "seepageVelocityFormula",
            "interpretation",
            "limitations",
        },
    }
    for operator_id, fields in expected.items():
        manifest = json.loads(
            (OPERATORS / operator_id / "1.0.0/manifest.json").read_text()
        )
        summary = next(
            item for item in manifest["outputs"] if item["name"] == "summary"
        )
        assert fields <= set(summary["schema"]["properties"])
