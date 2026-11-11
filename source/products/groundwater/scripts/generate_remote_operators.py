"""Generate the reviewed fixed-port Groundwater remote operator manifests."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CATALOG = ROOT / "packages/operator-pack/catalog/operators"
VERSION = "1.0.0"

JSON = ("application/json", "json")
ZIP = ("application/zip", "zip")
BINARY = ("application/octet-stream", "binary")
GEOTIFF = ("image/tiff", "geotiff")
NPY = ("application/x-npy", "npy")
PNG = ("image/png", "png")


def artifact(name: str, pair: tuple[str, str]) -> dict:
    return {
        "name": name,
        "kind": "artifact",
        "mediaTypes": [pair[0]],
        "formats": [pair[1]],
    }


def value(name: str) -> dict:
    return {"name": name, "kind": "value", "schema": {"type": "object"}}


SPECS = [
    ("groundwater.porous.model_compile", "porous-groundwater", "model.compile",
     [("project_package", ZIP)],
     [artifact("model_package", ZIP), artifact("validation_report", JSON), artifact("run_receipt", JSON)]),
    ("groundwater.porous.flow_transport_simulate", "porous-groundwater", "flow_transport.simulate",
     [("model_package", ZIP)],
     [value("calculation_summary"), value("effective_parameters"), artifact("result_package", ZIP), artifact("run_receipt", JSON)]),
    ("groundwater.porous.pest_calibrate", "porous-groundwater", "pest.calibrate",
     [("model_package", ZIP), ("observations", JSON)],
     [value("calibrated_parameters"), value("residual_diagnostics"), artifact("calibration_package", ZIP), artifact("run_receipt", JSON)]),
    ("groundwater.porous.scenario_compare", "porous-groundwater", "scenario.compare",
     [("scenario_package", ZIP)],
     [artifact("scenario_table", JSON), value("comparison_summary"), artifact("result_package", ZIP), artifact("run_receipt", JSON)]),
    ("groundwater.porous.result_analyze", "porous-groundwater", "result.analyze",
     [("result_package", ZIP)],
     [artifact("analysis_table", JSON), value("metadata"), artifact("run_receipt", JSON)]),
    ("groundwater.porous.result_render", "porous-groundwater", "result.render",
     [("result_package", ZIP)],
     [artifact("rendered_figure", PNG), artifact("vtu_pvd_package", ZIP), value("metadata"), artifact("run_receipt", JSON)]),
    ("groundwater.porous.map_prepare", "porous-groundwater", "map.prepare",
     [("map_input", GEOTIFF)],
     [artifact("geospatial_output", GEOTIFF), value("crs_attribution_metadata"), artifact("run_receipt", JSON)]),
    ("groundwater.porous.inversion_train", "porous-groundwater", "inversion.train",
     [("training_data", ZIP)],
     [artifact("weights", BINARY), value("metrics"), artifact("report_package", ZIP), artifact("run_receipt", JSON)]),
    ("groundwater.porous.inversion_sample", "porous-groundwater", "inversion.sample",
     [("weights", BINARY), ("sample_config", JSON)],
     [artifact("posterior_samples", NPY), value("metrics"), artifact("report_package", ZIP), artifact("run_receipt", JSON)]),
    ("groundwater.porous.agri_invert", "porous-groundwater", "agri.invert",
     [("agricultural_data", ZIP)],
     [artifact("posterior_arrays", NPY), value("metrics"), artifact("report_package", ZIP), artifact("run_receipt", JSON)]),
    ("groundwater.source_sustainability.evaluate", "groundwater-sustainability", "sustainability.evaluate",
     [("assessment_input", JSON)],
     [value("sustainability_result"), artifact("result_file", JSON), artifact("run_receipt", JSON)]),
]


def write(
    operator_id: str, service_id: str, operation: str, inputs: list, outputs: list,
    *, version: str = VERSION,
) -> None:
    timeout = 21600 if operation == "inversion.train" else 7200
    parameters = {"includeCalibratedModel": True} if operation == "pest.calibrate" and version == "1.1.0" else {}
    manifest = {
        "id": operator_id,
        "version": version,
        "title": operation.replace("_", " ").replace(".", " ").title(),
        "description": (
            "Delegate the fixed sustainability.evaluate operation to the external deployment binding groundwater-sustainability. The executor enforces service/API/model/operation allowlists, verifies input and result integrity, and writes a secret-free provenance receipt; the platform does not reproduce the external scoring algorithm."
            if operation == "sustainability.evaluate"
            else f"Execute fixed third-party operation {operation} through deployment service binding {service_id}."
        ),
        "domain": "groundwater",
        "tags": ["groundwater", "third-party", "remote-http", operation],
        "runtime": "remote_http",
        "remoteBinding": {"serviceId": service_id, "operationType": operation},
        "timeoutSeconds": timeout,
        "memoryLimitBytes": 1073741824,
        "inputs": [
            {"name": name, "cardinality": "one", "required": True,
             "mediaTypes": [pair[0]], "formats": [pair[1]]}
            for name, pair in inputs
        ],
        "parametersSchema": {
            "type": "object", "additionalProperties": False,
            "properties": {key: {"type": "boolean", "const": value} for key, value in parameters.items()},
            "required": list(parameters),
        },
        "outputs": outputs,
        "errorCodes": [
            *(["OPERATOR_TIMEOUT"] if operation == "sustainability.evaluate" else []),
            "REMOTE_AUTHENTICATION_FAILED", "REMOTE_BINDING_INVALID",
            "REMOTE_INTEGRITY_FAILED", "REMOTE_OPERATION_FAILED",
            "REMOTE_PROTOCOL_INVALID", "REMOTE_SUBMISSION_UNCERTAIN",
            "REMOTE_VERSION_MISMATCH",
        ],
        "examples": [{
            "schemaVersion": "operator-execution-request.v1",
            "clientRequestId": f"example-{operator_id.replace('.', '-')}",
            "operator": {"id": operator_id, "version": version},
            "mode": "ASYNC",
            "inputs": {
                name: {"artifactId": f"art_example_{index:02d}"}
                for index, (name, _) in enumerate(inputs, start=1)
            },
            "parameters": parameters,
        }],
    }
    smoke = {
        "schemaVersion": "operator-smoke-test.v1",
        "operator": {"id": operator_id, "version": version},
        "inputs": {
            name: {"fixtureId": "transfer.fields"}
            for name, _ in inputs
        },
        "parameters": parameters,
        "expectedOutputs": {
            item["name"]: (
                {"kind": "value"}
                if item["kind"] == "value"
                else {
                    "kind": "artifact", "mediaType": item["mediaTypes"][0],
                    "format": item["formats"][0],
                }
            )
            for item in outputs
        },
    }
    directory = CATALOG / operator_id / version
    directory.mkdir(parents=True, exist_ok=True)
    for name, document in (("manifest.json", manifest), ("smoke.json", smoke)):
        (directory / name).write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def main() -> None:
    for spec in SPECS:
        write(*spec)
    pest = next(spec for spec in SPECS if spec[2] == "pest.calibrate")
    write(*pest[:-1], [*pest[-1], artifact("calibrated_model_package", ZIP)], version="1.1.0")
    print(f"Generated {len(SPECS) + 1} remote groundwater operator versions")


if __name__ == "__main__":
    main()
