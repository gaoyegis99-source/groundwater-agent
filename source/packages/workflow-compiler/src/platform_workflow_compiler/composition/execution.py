"""Compile approved artifact compositions to the existing Worker contract."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from importlib.resources import files

from jsonschema import Draft202012Validator

from ..formats import MEDIA_TYPE_FORMATS, _port_pairs, logical_type
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
TOOL = "run_composed_workflow"
VERSION = "0.1.0"


@cache
def _adapter_digest() -> str:
    return definition_digest({"source": files(__package__).joinpath("execution.py").read_text(encoding="utf-8")})


def _port(contract):
    if contract.get("kind") == "value":
        return {**contract, "logicalType": "record"}
    port = {
        "name": contract["name"], "kind": "artifact",
        "logicalType": contract.get("logicalType") or logical_type(contract["formats"]),
        "formatPairs": [
            {"mediaType": media, "format": fmt} for media, fmt in sorted(_port_pairs(contract))
        ],
    }
    if contract.get("cardinality") == "many":
        port["cardinality"] = {"minItems": contract.get("minItems", 1), "maxItems": contract["maxItems"]}
    if cases := contract.get("parameterFormatCases"):
        port["formatCases"] = [
            {"parameter": case["parameter"], "values": case["values"],
             "formatPair": {"format": case["format"], "mediaType": case["mediaType"]}} for case in cases
        ]
    if cases := contract.get("formatCases"):
        port["inputFormatCases"] = [
            {"inputPort": case["inputPort"], "inputFormats": case["inputFormats"],
             "formatPair": {"format": case["format"], "mediaType": case["mediaType"]}} for case in cases
        ]
    return port


def _manifest(operator):
    schema = operator["parametersSchema"]
    remote = operator["runtime"] == "remote_http"
    result = {key: operator[key] for key in (
        "id", "version", "title", "description", "domain", "runtime", "tags",
        "implementationRevision", "memoryLimitBytes", "timeoutSeconds", "remoteBinding",
    ) if key in operator}
    semantics = operator.get("composition", {})
    if semantics.keys() - {"constraints", "portSemantics", "outputSemantics", "executionPolicy"}:
        raise WorkflowValidationError("COMPOSITION_CONFIG_INVALID")
    result.update(
        schemaVersion="operator-manifest.v2",
        inputs=[_port(item) for item in operator["inputs"]],
        outputs=[_port(item) for item in operator["outputs"]],
        parametersSchema=schema,
        classification={"reuseScope": "domain" if operator["domain"] == "groundwater" else "generic",
                        "providerKind": "external" if remote else "platform"},
        parameterPolicies={
            name: {"allowedSources": ["USER", *(["OPERATOR_DEFAULT"] if "default" in prop else [])],
                   "defaultPolicy": "declared" if "default" in prop else "none"}
            for name, prop in schema.get("properties", {}).items()
        },
        **semantics,
    )
    if "constraints" in semantics:
        result["constraints"] = [{**check, "validatorRef": validator_identity()} for check in semantics["constraints"]]
    result["definitionDigest"] = definition_digest(result)
    validate_operator_manifest(result, VOCABULARY)
    return result


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


def observed_input_facts(descriptor, semantics, reference):
    """Use immutable physical metadata and separately identified semantic declarations."""
    descriptor = descriptor or {}
    semantics = semantics or {}
    facts = raster_input_facts(descriptor.get("raster"), reference)
    subject = {"id": reference["versionId"], "version": "1.0.0", "digest": reference["checksum"]}
    revision = {"id": reference["semanticId"], "version": "1.0.0", "digest": reference["semanticChecksum"]}
    fields = {
        field["name"]: {"dtype": field["type"], "nullable": field["nullable"]}
        for field in descriptor.get("fields", [])
        if isinstance(field, dict) and isinstance(field.get("name"), str)
        and isinstance(field.get("type"), str) and type(field.get("nullable")) is bool
    }
    if fields:
        facts.append({"subjectRef": subject, "sourceRef": subject, "verificationScope": "metadata",
                      "property": "fields", "state": "known", "value": fields})
    declared = {key: semantics[key] for key in ("valueKind", "grain", "timezone", "verticalDatum")
                if semantics.get(key) is not None}
    if semantics.get("crs") and not any(fact["property"] == "crs" for fact in facts):
        declared["crs"] = semantics["crs"]
    for key, value in sorted(declared.items()):
        facts.append({"subjectRef": subject, "sourceRef": revision, "verificationScope": "declared",
                      "property": key, "state": "known", "value": value})
    return facts


def _workflow_vocabulary(workflow):
    # CRS equality compares canonical identities observed in the exact TIFFs.
    # This profile does not declare a metric-CRS conversion or inference.
    return {**VOCABULARY, "crs": {
        fact["value"]: {"metric": False}
        for item in workflow["inputs"].values() for fact in item["facts"]
        if fact["property"] == "crs" and fact["state"] == "known" and isinstance(fact["value"], str)
    }}


class CompositionValidationError(WorkflowValidationError):
    def __init__(self, diagnostic: dict) -> None:
        super().__init__(diagnostic["code"], diagnostic["path"])
        self.diagnostic = diagnostic


@dataclass(frozen=True)
class ArtifactComposition:
    """Assembly-selected capabilities and limits shared by Kernel and Executor."""

    operators: dict[tuple[str, str], dict]
    manifests: dict[tuple[str, str], dict]
    environment: dict
    limits: dict
    rule_schemas: dict[str, dict]

    @classmethod
    def from_config(cls, config: dict) -> ArtifactComposition:
        if set(config) != {"operators", "resourceLimits", "catalogDigest", "ruleSchemas"}:
            raise WorkflowValidationError("COMPOSITION_CONFIG_INVALID")
        operators = {(row["id"], row["version"]): row for row in config["operators"]}
        if not operators or len(operators) != len(config["operators"]):
            raise WorkflowValidationError("COMPOSITION_CONFIG_INVALID")
        manifests = {key: _manifest(operator) for key, operator in operators.items()}
        schemas = config["ruleSchemas"]
        referenced = {
            port["rulesSchema"]
            for operator in operators.values() for port in operator["inputs"]
            if "rulesSchema" in port
        }
        if not isinstance(schemas, dict) or set(schemas) != referenced:
            raise WorkflowValidationError("COMPOSITION_RULE_SCHEMAS_INVALID")
        for version, schema in schemas.items():
            Draft202012Validator.check_schema(schema)
            if schema.get("properties", {}).get("schemaVersion", {}).get("const") != version:
                raise WorkflowValidationError("COMPOSITION_RULE_SCHEMAS_INVALID")
        environment = {
            "id": "artifact-composition-environment",
            "version": "1.0.0",
            "digest": definition_digest({
                "profile": config, "validator": validator_identity(), "adapter": _adapter_digest(),
            }),
        }
        return cls(operators, manifests, environment, config["resourceLimits"], schemas)

    def candidate(self, draft: dict, project_id: str, semantic_checksums: dict[str, str], user_ref: dict,
                  data_descriptions: dict[str, dict] | None = None) -> dict:
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
            kind = "record" if name in record_inputs else logical_type({fmt})
            port = {
                "name": name, "kind": "artifact", "logicalType": kind,
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
            observed = (data_descriptions or {}).get(ref["version_id"], {})
            inputs[name]["facts"] = observed_input_facts(
                observed.get("descriptor"), observed.get("semantics"), inputs[name]["dataRef"],
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
        # Resolve concrete output formats using the same Worker graph compiler
        # that verifies bindings again on admission.
        narrowed = {
            **raw, "inputs": [
                {"name": name, "cardinality": "one", "formats": [ref["format"]], "mediaTypes": [ref["media_type"]]}
                for name, ref in sorted(references.items())
            ],
        }
        compiled = _compile_workflow(narrowed, self.operators, parameter_values=draft["parameter_values"],
                                     collection_operators=self.manifests)
        ports = {(node["nodeId"], port["name"]): port for node in compiled["nodes"] for port in node["outputContracts"]}
        outputs = {}
        for item in raw["outputs"]:
            ref = item["nodeOutput"]
            output = ports[ref["nodeId"], ref["port"]]
            if output["kind"] == "value":
                fmt, kind = "json", "record"
            else:
                if len(output["formatPairs"]) != 1:
                    raise WorkflowValidationError("COMPOSITION_OUTPUT_UNSUPPORTED")
                fmt = output["formatPairs"][0]["format"]
                kind = logical_type({fmt})
            outputs[item["name"]] = {
                "nodeOutput": ref,
                "goal": {"role": item["name"], "logicalType": kind, "requires": {}},
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
        report = validate_composition(workflow, self.manifests, _workflow_vocabulary(workflow), environment_ref=self.environment)
        for name, output in report["outputs"].items():
            workflow["outputs"][name]["goal"]["requires"] = {
                key: value for key, value in output["facts"].items() if key in _PUBLIC_SEMANTICS
            }
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
            raise CompositionValidationError(issue)
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


_PUBLIC_SEMANTICS = {
    "valueKind": "value_kind", "crs": "crs", "timezone": "timezone",
    "verticalDatum": "vertical_datum", "grain": "grain",
}


def publication_declarations(workflow: dict) -> dict:
    return {
        name: {
            **item["publication"],
            "mediaType": next(
                media for media, formats in MEDIA_TYPE_FORMATS.items() if item["publication"]["format"] in formats
            ),
            "semantics": {
                _PUBLIC_SEMANTICS[key]: value for key, value in item["goal"]["requires"].items()
                if key in _PUBLIC_SEMANTICS
            },
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
