"""Compile approved artifact compositions to the existing Worker contract."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from importlib.resources import files

from ..formats import MEDIA_TYPE_FORMATS, _port_pairs
from ..graph import _compile_workflow, input_members
from ..validation import WorkflowValidationError, checksum, pointer
from . import definition_digest, validate_composition, validate_operator_manifest, validate_vocabulary, validator_identity
from .documents import validate_document


VOCABULARY = {
    "schemaVersion": "composition-vocabulary.v1",
    "id": "artifact-composition",
    "version": "1.0.0",
    "quantities": [],
    "crs": {},
    "units": {},
}
TABLE_FORMATS = {"csv", "parquet", "arrow-ipc-file"}
RASTER_FORMATS = {"geotiff", "cog"}
REFERENCE_GRID_OPERATORS = {("raster.reproject", "1.1.0"), ("raster.align_grid", "1.0.0")}
RASTER_OPERATORS = REFERENCE_GRID_OPERATORS | {("raster.cell_statistics", "1.0.0"), ("raster.reclassify", "1.0.0")}
RULE_FORMATS = TABLE_FORMATS | {"json"}
TOOL = "run_composed_workflow"
VERSION = "0.1.0"


@cache
def _adapter_digest() -> str:
    return definition_digest({"source": files(__package__).joinpath("execution.py").read_text(encoding="utf-8")})


def _logical_type(formats):
    if formats <= RASTER_FORMATS:
        return "raster"
    if formats <= TABLE_FORMATS:
        return "table"
    if formats <= RULE_FORMATS:
        return "record"
    raise WorkflowValidationError("COMPOSITION_FORMAT_UNSUPPORTED")


def _port(contract, logical_type, formats=None):
    return {
        "name": contract["name"],
        "kind": "artifact",
        "logicalType": logical_type,
        "formatPairs": [
            {"mediaType": media, "format": fmt} for media, fmt in sorted(_port_pairs(contract))
            if formats is None or fmt in formats
        ],
    }


def _manifest(operator):
    identity = operator["id"], operator["version"]
    raster = identity in RASTER_OPERATORS
    inputs = []
    for item in operator["inputs"]:
        formats = set(item["formats"])
        if not item["required"]:
            raise WorkflowValidationError("COMPOSITION_OPERATOR_UNSUPPORTED")
        if identity == ("raster.reclassify", "1.0.0") and item["name"] == "rules":
            # GeoParquet is advertised by the legacy manifest but the operator
            # only parses JSON, CSV, Parquet and Arrow IPC rules.
            port = _port(item, "record", RULE_FORMATS)
        else:
            port = _port(item, _logical_type(formats))
        if item["cardinality"] != "one":
            if identity != ("raster.cell_statistics", "1.0.0") or item["name"] != "rasters":
                raise WorkflowValidationError("COMPOSITION_OPERATOR_UNSUPPORTED")
            port["cardinality"] = {"minItems": 2, "maxItems": item["maxItems"]}
        inputs.append(port)
    outputs = [
        _port(item, "table") for item in operator["outputs"]
        if item["kind"] == "artifact" and set(item["formats"]) <= TABLE_FORMATS
    ]
    if raster:
        outputs = [
            _port(item, "raster", {"geotiff"}) for item in operator["outputs"]
            if item["kind"] == "artifact" and set(item["formats"]) <= RASTER_FORMATS
        ]
    if operator["runtime"] != "native" or not outputs:
        raise WorkflowValidationError("COMPOSITION_OPERATOR_UNSUPPORTED")
    schema = operator["parametersSchema"]
    result = {
        key: operator[key] for key in (
            "id", "version", "title", "description", "domain", "runtime",
            "implementationRevision", "memoryLimitBytes", "timeoutSeconds",
        )
    }
    result.update(
        schemaVersion="operator-manifest.v2",
        inputs=inputs,
        outputs=outputs,
        parametersSchema=schema,
        classification={"reuseScope": "generic", "providerKind": "platform"},
        parameterPolicies={
            name: {"allowedSources": ["USER", "OPERATOR_DEFAULT"], "defaultPolicy": "declared"}
            for name, prop in schema.get("properties", {}).items() if "default" in prop
        },
    )
    if raster:
        _set_raster_contract(result)
    result["definitionDigest"] = definition_digest(result)
    validate_operator_manifest(result, VOCABULARY)
    return result


def _set_raster_contract(manifest):
    if (manifest["id"], manifest["version"]) in REFERENCE_GRID_OPERATORS:
        _set_reference_grid_contract(manifest)
        return
    across_cells = manifest["id"] == "raster.cell_statistics"
    port = "rasters" if across_cells else "raster"

    def field(key):
        return {"port": port, "property": key}

    checks = [
        ("selected-band", "rasterBandExists", [field("rasterBands"), {"parameter": "band"}]),
        ("input-nodata", "rasterMaskSupported", [field("rasterBands"), {"parameter": "band"}]),
    ]
    semantics = {"inherit": [{"inputPort": port, "facts": ["crs", "grid"]}]}
    if across_cells:
        checks[:0] = [
            ("same-crs", "sameCrs", [field("crs"), field("crs")]),
            ("same-grid", "sameGrid", [field("grid"), field("grid"), {"value": 0}]),
        ]
        semantics["fixed"] = {"rasterBands": [{"dtype": "float32", "nodata": "nan", "maskFlags": ["nodata"]}]}
    else:
        checks.append(("output-nodata", "rasterNoDataFits", [{"parameter": "noData"}, {"parameter": "outputType"}]))
        semantics["transform"] = [{"property": "nodata", "parameter": "noData", "rule": "parameterFact"}]
    manifest.update(
        portSemantics={port: {"requiredFacts": ["crs", "grid", "rasterBands"]}},
        outputSemantics={"raster": semantics},
        constraints=[
            {"id": key, "condition": {"predicate": predicate, "arguments": arguments},
             "severity": "error", "checkStage": "compile", "validatorRef": validator_identity()}
            for key, predicate, arguments in checks
        ],
    )


def _set_reference_grid_contract(manifest):
    constraints = [
        ("selected-band", "rasterBandExists"),
        ("input-nodata", "rasterMaskSupported"),
    ]
    manifest["portSemantics"] = {
        "raster": {"requiredFacts": ["crs", "grid", "rasterBands"]},
        "reference": {"requiredFacts": ["crs", "grid"]},
    }
    manifest["outputSemantics"] = {"raster": {
        "inherit": [{"inputPort": "reference", "facts": ["crs", "grid"]}],
        "fixed": {"rasterBands": [{"dtype": "float64", "nodata": "nan", "maskFlags": ["nodata"]}]},
    }}
    manifest["constraints"] = [
        {
            "id": name, "condition": {"predicate": predicate, "arguments": [
                {"port": "raster", "property": "rasterBands"}, {"parameter": "band"},
            ]},
            "severity": "error", "checkStage": "compile", "validatorRef": validator_identity(),
        }
        for name, predicate in constraints
    ]
    if manifest["id"] == "raster.align_grid":
        manifest["constraints"].insert(0, {
            "id": "same-crs", "condition": {"predicate": "sameCrs", "arguments": [
                {"port": "raster", "property": "crs"}, {"port": "reference", "property": "crs"},
            ]},
            "severity": "error", "checkStage": "compile", "validatorRef": validator_identity(),
        })


def raster_input_facts(descriptor, reference):
    if not isinstance(descriptor, dict) or descriptor.get("sourceChecksum") != reference["checksum"]:
        return []
    source = {"id": reference["versionId"], "version": "1.0.0", "digest": reference["checksum"]}
    return [
        {"subjectRef": source, "sourceRef": source, "verificationScope": "metadata",
         "property": key, "state": "known", "value": value}
        for key, value in (
            ("crs", descriptor.get("crs")), ("grid", descriptor.get("grid")),
            ("rasterBands", descriptor.get("bands")),
        ) if value is not None
    ]


def _workflow_vocabulary(workflow):
    # CRS equality compares canonical identities observed in the exact TIFFs.
    # This profile does not declare a metric-CRS conversion or inference.
    return {**VOCABULARY, "crs": {
        fact["value"]: {"metric": False}
        for item in workflow["inputs"].values() for fact in item["facts"]
        if fact["property"] == "crs" and fact["state"] == "known" and isinstance(fact["value"], str)
    }}


@dataclass(frozen=True)
class ArtifactComposition:
    """Assembly-selected capabilities and limits shared by Kernel and Executor."""

    operators: dict[tuple[str, str], dict]
    manifests: dict[tuple[str, str], dict]
    environment: dict
    limits: dict

    @classmethod
    def from_config(cls, config: dict) -> ArtifactComposition:
        if set(config) != {"operators", "resourceLimits", "catalogDigest"}:
            raise WorkflowValidationError("COMPOSITION_CONFIG_INVALID")
        operators = {(row["id"], row["version"]): row for row in config["operators"]}
        if not operators or len(operators) != len(config["operators"]):
            raise WorkflowValidationError("COMPOSITION_CONFIG_INVALID")
        manifests = {key: _manifest(operator) for key, operator in operators.items()}
        environment = {
            "id": "artifact-composition-environment",
            "version": "1.0.0",
            "digest": definition_digest({
                "profile": config, "validator": validator_identity(), "adapter": _adapter_digest(),
            }),
        }
        return cls(operators, manifests, environment, config["resourceLimits"])

    def candidate(self, draft: dict, project_id: str, semantic_checksums: dict[str, str], user_ref: dict,
                  raster_descriptors: dict[str, dict] | None = None) -> dict:
        """Bind the author's graph to exact observed inputs and trusted definitions."""
        raw = draft["workflow"]
        references = {item["input_name"]: item["data_ref"] for item in draft["data_bindings"]}
        record_inputs = {
            member["workflowInput"]
            for node in raw["nodes"]
            for port in self.manifests[node["operatorId"], node["operatorVersion"]]["inputs"]
            if port["logicalType"] == "record"
            for member in input_members(node["inputs"][port["name"]])
            if "workflowInput" in member
        }
        inputs = {}
        for name, ref in sorted(references.items()):
            fmt = ref["format"]
            logical_type = "record" if name in record_inputs else _logical_type({fmt})
            port = {
                "name": name, "kind": "artifact", "logicalType": logical_type,
                "formatPairs": [{"format": fmt, "mediaType": ref["media_type"]}],
            }
            inputs[name] = {
                "port": port, "facts": [],
                "dataRef": {
                    "kind": "data_version", "service": ref["service"], "datasetId": ref["dataset_id"],
                    "versionId": ref["version_id"], "semanticId": ref["semantic_id"],
                    "objectId": ref["object_id"], "mediaType": ref["media_type"], "format": fmt,
                    "sizeBytes": ref["size_bytes"], "checksum": ref["checksum"],
                    "semanticChecksum": semantic_checksums[ref["version_id"]],
                    **({"outputName": ref["output_name"]} if ref.get("output_name") else {}),
                },
            }
            if logical_type == "raster":
                inputs[name]["facts"] = raster_input_facts(
                    (raster_descriptors or {}).get(ref["version_id"]), inputs[name]["dataRef"],
                )
        nodes = []
        defaults = {}
        for node in raw["nodes"]:
            operator = self.manifests.get((node["operatorId"], node["operatorVersion"]))
            if operator is None:
                raise WorkflowValidationError("COMPOSITION_OPERATOR_UNSUPPORTED")
            nodes.append({
                "nodeId": node["nodeId"], "inputs": node["inputs"], "parameters": node["parameters"],
                "operator": {key: operator[key] for key in ("id", "version", "definitionDigest", "implementationRevision")},
            })
            for binding in node["parameters"].values():
                name = binding["workflowParameter"]
                defaults.setdefault(name, {
                    "id": operator["id"], "version": operator["version"], "digest": operator["definitionDigest"],
                })
        parameters = {}
        for item in draft["parameter_sources"]:
            name, kind = item["name"], item["source"]
            if kind not in {"USER", "OPERATOR_DEFAULT"}:
                raise WorkflowValidationError("PARAMETER_SOURCE_NOT_ALLOWED")
            parameters[name] = {
                "value": draft["parameter_values"][name],
                "source": {
                    "kind": kind, "ref": defaults[name] if kind == "OPERATOR_DEFAULT" else user_ref,
                    "path": pointer(["parameters", name]), "reason": item["explanation"],
                },
            }
        used_names = set(parameters) | set(inputs)
        for node in nodes:
            identity = node["operator"]["id"], node["operator"]["version"]
            if identity in RASTER_OPERATORS and "band" not in node["parameters"]:
                name = f"{node['nodeId']}.band"
                while name in used_names:
                    name += ".default"
                used_names.add(name)
                reference = node["operator"]
                parameters[name] = {
                    "value": self.manifests[identity]["parametersSchema"]["properties"]["band"]["default"],
                    "source": {"kind": "OPERATOR_DEFAULT",
                               "ref": {"id": reference["id"], "version": reference["version"], "digest": reference["definitionDigest"]},
                               "path": pointer(["parameters", name]), "reason": "Declared default band"},
                }
                node["parameters"] = {**node["parameters"], "band": {"workflowParameter": name}}
        outputs = {}
        by_node = {node["nodeId"]: node["operator"] for node in nodes}
        for item in raw["outputs"]:
            ref = item["nodeOutput"]
            operator = by_node[ref["nodeId"]]
            manifest = self.manifests[operator["id"], operator["version"]]
            output = next((port for port in manifest["outputs"] if port["name"] == ref["port"]), None)
            if output is None or len(output["formatPairs"]) != 1:
                raise WorkflowValidationError("COMPOSITION_OUTPUT_UNSUPPORTED")
            fmt = output["formatPairs"][0]["format"]
            outputs[item["name"]] = {
                "nodeOutput": ref,
                "goal": {"role": item["name"], "logicalType": output["logicalType"], "requires": {}},
                "publication": {
                    "title": item["name"], "format": fmt,
                    "policy": "FILE_QUERYABLE" if fmt in {"csv", "parquet"} else "FILE_ONLY",
                },
            }
        workflow = {
            "schemaVersion": "workflow.v7", "workflowId": raw["workflowId"], "version": VERSION,
            "projectId": project_id,
            "validatorRef": validator_identity(), "environmentRef": self.environment,
            "requiredFeatures": ["semantic-checks"], "resourceLimits": self.limits,
            "inputs": inputs, "parameters": parameters, "nodes": nodes, "outputs": outputs,
        }
        if any("collection" in binding for node in nodes for binding in node["inputs"].values()):
            workflow["requiredFeatures"].append("collection-inputs")
        workflow["registryRef"] = validate_vocabulary(_workflow_vocabulary(workflow))
        workflow["definitionDigest"] = definition_digest(workflow)
        return workflow

    def compile(self, workflow: dict, project_id: str) -> tuple[dict, dict]:
        """Revalidate a frozen definition and lower the supported graph without trusting a compiled plan."""
        if workflow.get("projectId") != project_id or workflow.get("resourceLimits") != self.limits:
            raise WorkflowValidationError("COMPOSITION_SCOPE_MISMATCH")
        validate_document("workflow.v7", workflow)
        report = validate_composition(workflow, self.manifests, _workflow_vocabulary(workflow), environment_ref=self.environment)
        if report["status"] != "VALIDATED":
            severity = "error" if report["status"] == "INVALID" else "missing"
            issue = next(item for item in report["diagnostics"] if item["severity"] == severity)
            raise WorkflowValidationError(issue["code"], issue["path"])
        if not workflow.get("definitionDigest") or any(check["state"] == "DEFERRED" for check in report["checks"]):
            raise WorkflowValidationError("COMPOSITION_EXECUTION_UNSUPPORTED")
        if workflow["version"] != VERSION or not workflow["workflowId"].startswith("draft."):
            raise WorkflowValidationError("COMPOSITION_WORKFLOW_ID_INVALID")
        if workflow["inputs"].keys() & workflow["parameters"].keys():
            raise WorkflowValidationError("COMPOSITION_PARAMETER_INPUT_COLLISION")
        nodes = []
        for node in workflow["nodes"]:
            if any(set(item) not in ({"workflowInput"}, {"nodeOutput"}, {"collection"}) for item in node["inputs"].values()):
                raise WorkflowValidationError("COMPOSITION_EXECUTION_UNSUPPORTED")
            if any(set(item) != {"workflowParameter"} for item in node["parameters"].values()):
                raise WorkflowValidationError("COMPOSITION_EXECUTION_UNSUPPORTED")
            if node["operator"]["id"] == "raster.cell_statistics":
                minimum = node["parameters"].get("minimumValidInputs")
                if minimum and workflow["parameters"][minimum["workflowParameter"]]["value"] > len(node["inputs"]["rasters"]["collection"]):
                    raise WorkflowValidationError("RASTER_MINIMUM_VALID_INPUTS_EXCEEDS_COLLECTION", "/nodes")
            nodes.append({
                "nodeId": node["nodeId"], "operatorId": node["operator"]["id"],
                "operatorVersion": node["operator"]["version"], "inputs": node["inputs"],
                "parameters": node["parameters"],
            })
        compiled = _compile_workflow({
            "schemaVersion": "workflow.v6", "workflowId": workflow["workflowId"], "version": workflow["version"],
            "inputs": [
                {"name": name, "cardinality": "one", "mediaTypes": [item["dataRef"]["mediaType"]],
                 "formats": [item["dataRef"]["format"]]}
                for name, item in workflow["inputs"].items()
            ],
            "parameters": list(workflow["parameters"]), "nodes": nodes,
            "outputs": [{"name": name, "nodeOutput": item["nodeOutput"]} for name, item in workflow["outputs"].items()],
        }, self.operators, parameter_values={key: item["value"] for key, item in workflow["parameters"].items()},
            collection_operators=self.manifests)
        return _execution_model(workflow, compiled, report), report


def execution_arguments(workflow: dict) -> tuple[dict, dict]:
    references = {
        name: {key: value for key, value in item["dataRef"].items() if key != "semanticChecksum"}
        for name, item in workflow["inputs"].items()
    }
    return {
        **{name: reference["versionId"] for name, reference in references.items()},
        **{name: item["value"] for name, item in workflow["parameters"].items()},
    }, references


def publication_declarations(workflow: dict) -> dict:
    return {
        name: {
            **item["publication"],
            "mediaType": next(
                media for media, formats in MEDIA_TYPE_FORMATS.items() if item["publication"]["format"] in formats
            ),
            "semantics": {},
        }
        for name, item in sorted(workflow["outputs"].items())
    }


def _execution_model(workflow, compiled, report):
    arguments, _ = execution_arguments(workflow)
    schema = {
        "type": "object", "properties": {name: {"const": value} for name, value in arguments.items()},
        "required": sorted(arguments), "additionalProperties": False,
    }
    digest = report["definitionDigest"]
    return {
        "modelId": workflow["workflowId"], "version": workflow["version"],
        "modelChecksum": digest, "inputSchemaChecksum": checksum(schema),
        "workflowRef": {"workflowId": workflow["workflowId"], "version": workflow["version"], "checksum": digest},
        "workflow": {**compiled, "definitionChecksum": digest},
        "tool": {"name": TOOL, "description": "Execute the confirmed composed workflow.", "inputSchema": schema},
        "executionService": "executor", "executorId": "workflow-dag",
        "deadlineSeconds": sum(node["timeoutSeconds"] for node in compiled["nodes"]),
        "maxParallelNodes": workflow["resourceLimits"]["maxParallelNodes"],
        "inputBindings": {name: name for name in workflow["inputs"]},
        "parameterBindings": {name: name for name in workflow["parameters"]},
        "defaultParameters": {}, "finalOutputs": sorted(workflow["outputs"]),
        "outputPublications": publication_declarations(workflow),
    }
