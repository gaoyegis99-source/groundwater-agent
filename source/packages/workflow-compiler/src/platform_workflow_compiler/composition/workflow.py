"""Validate a candidate DAG and propagate its declared output facts."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from importlib.resources import files

from jsonschema.exceptions import ValidationError

from ..graph import _named_list, _topological_order
from ..validation import WorkflowValidationError, pointer
from .documents import SCHEMA_NAMES, definition_digest, validate_document, validate_parameter_schema
from .manifest import (
    NO_PARAMETERS,
    SINGLE_ITEM,
    USER_PARAMETER,
    format_pairs,
    validate_operator_manifest,
    validate_port,
    validate_requirements,
)
from .semantics import FULL_SCOPE, Fact, evaluate, input_facts, validate_fact_value, validate_vocabulary


FEATURES = {"collection-inputs", "value-bindings", "semantic-checks"}


@cache
def _validator_digest() -> str:
    package = files(__package__)
    names = ["documents.py", "manifest.py", "semantics.py", "workflow.py"]
    schemas = [f"schemas/{name}.schema.json" for name in SCHEMA_NAMES]
    shared = files("platform_workflow_compiler")
    contents = {name: package.joinpath(name).read_text(encoding="utf-8") for name in [*names, *schemas]}
    contents.update({
        f"../{name}": shared.joinpath(name).read_text(encoding="utf-8")
        for name in ("formats.py", "graph.py", "validation.py")
    })
    return definition_digest(contents)


def validator_identity() -> dict:
    return {"id": "workflow-composition", "version": "1.0.0", "digest": _validator_digest()}


@dataclass
class Port:
    contract: dict
    facts: dict[str, Fact]
    measured: frozenset[str] = frozenset()


class _Validation:
    def __init__(self, workflow, operators, vocabulary):
        self.workflow = workflow
        self.operators = operators
        self.vocabulary = vocabulary
        self.inputs = {}
        self.outputs = {}
        self.used_parameters = set()
        self.used_inputs = set()
        self.report = {
            "schemaVersion": "composition-validation.v1", "status": "VALIDATED",
            "definitionDigest": None, "topologicalOrder": [], "diagnostics": [],
            "checks": [], "outputs": {},
        }

    def issue(self, code, path, *, expected=None, actual=None, severity="error", sources=(), repair=()):
        self.report["diagnostics"].append({
            "code": code, "path": path, "expected": expected, "actual": actual,
            "severity": severity, "factSources": list(sources), "repairOptions": list(repair),
        })
        if severity == "error":
            self.report["status"] = "INVALID"
        elif severity == "missing" and self.report["status"] == "VALIDATED":
            self.report["status"] = "INCOMPLETE"

    def validate(self, environment_ref):
        workflow = self.workflow
        validate_document("workflow.v7", workflow)
        digest = definition_digest(workflow)
        if "definitionDigest" in workflow and workflow["definitionDigest"] != digest:
            raise WorkflowValidationError("COMPOSITION_DEFINITION_DIGEST_MISMATCH", "/definitionDigest")
        self.report["definitionDigest"] = digest
        if workflow["registryRef"] != validate_vocabulary(self.vocabulary):
            raise WorkflowValidationError("COMPOSITION_REGISTRY_MISMATCH", "/registryRef")
        if workflow["validatorRef"] != validator_identity():
            raise WorkflowValidationError("COMPOSITION_VALIDATOR_MISMATCH", "/validatorRef")
        if workflow["environmentRef"] != environment_ref:
            raise WorkflowValidationError("COMPOSITION_ENVIRONMENT_MISMATCH", "/environmentRef")
        if set(workflow["requiredFeatures"]) - FEATURES:
            raise WorkflowValidationError("FEATURE_UNSUPPORTED", "/requiredFeatures")
        limits = workflow["resourceLimits"]
        if len(workflow["nodes"]) > limits["maxNodes"] or limits["maxParallelNodes"] > limits["maxNodes"]:
            raise WorkflowValidationError("COMPOSITION_RESOURCE_LIMIT", "/resourceLimits")
        for name, item in sorted(workflow["inputs"].items()):
            self._input(name, item)
        nodes = _named_list(workflow["nodes"], "nodeId", "WORKFLOW_NODE_INVALID", collection="nodes")
        indices = {name: i for i, name in enumerate(nodes)}
        dependencies = {}
        for name, node in nodes.items():
            deps = set()
            for binding in [*node["inputs"].values(), *node["parameters"].values()]:
                for source in binding.get("collection", [binding]):
                    if "nodeOutput" in source:
                        deps.add(source["nodeOutput"]["nodeId"])
            if not deps <= nodes.keys():
                raise WorkflowValidationError("WORKFLOW_PORT_INCOMPATIBLE", pointer(["nodes", indices[name]]))
            dependencies[name] = deps
        order = _topological_order(dependencies)
        self.report["topologicalOrder"] = order
        final_nodes = {output["nodeOutput"]["nodeId"] for output in workflow["outputs"].values()}
        reachable = set(final_nodes)
        for name in reversed(order):
            if name in reachable:
                reachable.update(dependencies[name])
        if set(nodes) - reachable:
            raise WorkflowValidationError("WORKFLOW_NODE_UNREACHABLE", "/nodes")
        verified = set()
        for name in order:
            node = nodes[name]
            path = pointer(["nodes", indices[name]])
            reference = node["operator"]
            key = (reference["id"], reference["version"])
            manifest = self.operators.get(key)
            if manifest is None:
                raise WorkflowValidationError("WORKFLOW_OPERATOR_UNKNOWN", path + "/operator")
            if key not in verified:
                validate_operator_manifest(manifest, self.vocabulary)
                verified.add(key)
            if any(manifest[field] != value for field, value in reference.items()):
                raise WorkflowValidationError("COMPOSITION_OPERATOR_REFERENCE_MISMATCH", path + "/operator")
            if manifest["memoryLimitBytes"] > limits["maxNodeMemoryBytes"] or manifest["timeoutSeconds"] > limits["maxNodeTimeoutSeconds"]:
                raise WorkflowValidationError("COMPOSITION_RESOURCE_LIMIT", path + "/operator")
            self._node(node, manifest, path)
        for name, output in sorted(workflow["outputs"].items()):
            self._output(name, output)
        for key, used in (("parameters", self.used_parameters), ("inputs", self.used_inputs)):
            if workflow[key].keys() - used:
                raise WorkflowValidationError("COMPOSITION_UNUSED_BINDING", "/" + key)

    def _input(self, name, item):
        path = pointer(["inputs", name])
        port = item["port"]
        validate_port(port, path + "/port")
        if port["kind"] != "artifact" or port["name"] != name or port.get("cardinality", SINGLE_ITEM)["maxItems"] != 1:
            raise WorkflowValidationError("WORKFLOW_INPUT_BINDING_INVALID", path + "/port")
        if "selector" in port or "formatCases" in port:
            raise WorkflowValidationError("WORKFLOW_INPUT_BINDING_INVALID", path + "/port")
        if ref := item.get("dataRef"):
            pair = (ref["mediaType"], ref["format"])
            if pair not in format_pairs(port):
                raise WorkflowValidationError("WORKFLOW_PORT_INCOMPATIBLE", path + "/dataRef")
            port = {**port, "formatPairs": [{"mediaType": pair[0], "format": pair[1]}]}
        else:
            self.issue("COMPOSITION_DATA_REQUIRED", path + "/dataRef", severity="missing", repair=("bind-exact-data-version",))
        self.inputs[name] = Port(port, input_facts(item, self.vocabulary, path))

    def _upstream(self, reference, path):
        result = self.outputs.get((reference["nodeId"], reference["port"]))
        if result is None:
            raise WorkflowValidationError("WORKFLOW_PORT_INCOMPATIBLE", path)
        return result

    def _bind_input(self, target, binding, path):
        if binding.get("absent"):
            bindings = []
        elif "collection" in binding:
            bindings = binding["collection"]
        else:
            bindings = [binding]
        limit = target.get("cardinality", SINGLE_ITEM)
        if not limit["minItems"] <= len(bindings) <= limit["maxItems"]:
            raise WorkflowValidationError("COLLECTION_CARDINALITY_INVALID", path)
        if "collection" not in binding and "absent" not in binding and limit["maxItems"] > 1:
            raise WorkflowValidationError("COLLECTION_CARDINALITY_INVALID", path)
        if not limit.get("duplicates", False) and len({self._binding_identity(item) for item in bindings}) != len(bindings):
            raise WorkflowValidationError("COLLECTION_DUPLICATE_INPUT", path)
        result = []
        for index, item in enumerate(bindings):
            location = path + pointer(["collection", index]) if "collection" in binding else path
            if "workflowInput" in item:
                name = item["workflowInput"]
                source = self.inputs.get(name)
                if source is None:
                    raise WorkflowValidationError("WORKFLOW_PORT_INCOMPATIBLE", location)
                self.used_inputs.add(name)
            else:
                source = self._upstream(item["nodeOutput"], location)
            actual = source.contract
            cardinality = actual.get("cardinality", SINGLE_ITEM)
            if (
                actual["kind"] != target["kind"] or actual["logicalType"] != target["logicalType"]
                or cardinality["maxItems"] != 1
                or ("nodeOutput" in item and cardinality["minItems"] != 1)
                or not format_pairs(actual) <= format_pairs(target)
                or ("schema" in target and actual.get("schema") != target["schema"])
            ):
                raise WorkflowValidationError("WORKFLOW_PORT_INCOMPATIBLE", location)
            result.append(source)
        return result

    def _binding_identity(self, binding):
        name = binding.get("workflowInput")
        reference = self.workflow["inputs"].get(name, {}).get("dataRef")
        if reference is not None:
            return ("data", reference["service"], reference["versionId"])
        return ("binding", definition_digest(binding))

    def _parameters(self, node, manifest, path):
        schema = manifest.get("parametersSchema", NO_PARAMETERS)
        policies = manifest.get("parameterPolicies", {})
        properties = schema.get("properties", {})
        bindings = node["parameters"]
        values = {}
        dynamic = set()
        if bindings.keys() - properties.keys():
            raise WorkflowValidationError("COMPOSITION_PARAMETER_UNKNOWN", path)
        for name, binding in sorted(bindings.items()):
            location = path + pointer([name])
            policy = policies.get(name, USER_PARAMETER)
            if "nodeOutput" in binding:
                port = self._upstream(binding["nodeOutput"], location).contract
                cardinality = port.get("cardinality", SINGLE_ITEM)
                if (
                    not policy.get("allowNodeOutput", False) or "DERIVED" not in policy["allowedSources"]
                    or port["kind"] != "value" or port["schema"] != properties[name]
                    or cardinality["minItems"] != 1 or cardinality["maxItems"] != 1
                ):
                    raise WorkflowValidationError("WORKFLOW_PARAMETER_BINDING_INVALID", location)
                dynamic.add(name)
                continue
            key = binding["workflowParameter"]
            if key not in self.workflow["parameters"]:
                raise WorkflowValidationError("COMPOSITION_PARAMETER_UNKNOWN", location)
            self.used_parameters.add(key)
            parameter = self.workflow["parameters"][key]
            source = parameter["source"]
            kind = source["kind"]
            if kind not in policy["allowedSources"]:
                raise WorkflowValidationError("PARAMETER_SOURCE_NOT_ALLOWED", location)
            if kind == "OPERATOR_DEFAULT":
                definition = properties[name]
                if (
                    policy.get("defaultPolicy", "none") != "declared"
                    or source["ref"] != {
                        "id": manifest["id"], "version": manifest["version"], "digest": manifest["definitionDigest"],
                    }
                    or definition_digest({"value": parameter["value"]}) != definition_digest({"value": definition["default"]})
                ):
                    raise WorkflowValidationError("COMPOSITION_PARAMETER_DEFAULT_INVALID", location)
            values[name] = parameter["value"]
        missing = set(schema.get("required", [])) - bindings.keys()
        if missing:
            self.issue("PARAMETER_SOURCE_MISSING", path, expected=sorted(missing), severity="missing", repair=("bind-parameter-with-source",))
        validator = validate_parameter_schema(schema, path)
        try:
            for name, value in values.items():
                validator.evolve(schema=properties[name]).validate(value)
            if not missing and not dynamic:
                validator.validate(values)
        except ValidationError as cause:
            raise WorkflowValidationError("WORKFLOW_PARAMETER_INVALID", path + pointer(cause.absolute_path)) from cause
        if dynamic:
            # Local value schemas are provable. Cross-parameter conditions require
            # all values to be frozen in this release.
            if schema.keys() - {"type", "properties", "required", "additionalProperties", "title", "description", "$schema"}:
                raise WorkflowValidationError("FEATURE_UNSUPPORTED", path)
        return values, dynamic

    def _requirements(self, required, names, port, path):
        for name in sorted(set(names) | required.keys()):
            fact = port.facts.get(name)
            location = path + pointer([name])
            if fact is None:
                code = "UNIT_UNKNOWN" if name == "unit" else "COMPOSITION_FACT_REQUIRED"
                self.issue(code, location, expected=required.get(name), severity="missing", repair=("supply-verified-fact",))
            elif name in required and fact.value != required[name]:
                self._mismatch(name, fact, required[name], location)

    def _mismatch(self, name, fact, expected, path):
        codes = {
            "quantityKind": "SEMANTIC_QUANTITY_MISMATCH", "unit": "UNIT_MISMATCH",
            "verticalDatum": "VERTICAL_DATUM_MISMATCH", "crs": "CRS_MISMATCH", "grid": "GRID_MISMATCH",
            "featureOrder": "MODEL_FEATURE_ORDER_MISMATCH",
        }
        code = codes.get(name, "COMPOSITION_FACT_MISMATCH")
        repair = ()
        if name == "unit":
            units = self.vocabulary["units"]
            if units[fact.value] == units[expected]:
                return
            if units[fact.value]["dimension"] == units[expected]["dimension"]:
                code = "ADAPTER_REQUIRED"
                repair = ("select-versioned-unit-conversion",)
        self.issue(code, path, expected=expected, actual=fact.value, sources=fact.sources, repair=repair)

    def _node(self, node, manifest, path):
        targets = {item["name"]: item for item in manifest["inputs"]}
        if node["inputs"].keys() != targets.keys():
            raise WorkflowValidationError("WORKFLOW_INPUT_BINDING_INVALID", path + "/inputs")
        inputs = {
            name: self._bind_input(target, node["inputs"][name], path + pointer(["inputs", name]))
            for name, target in sorted(targets.items())
        }
        values, dynamic = self._parameters(node, manifest, path + "/parameters")
        for name, ports in inputs.items():
            semantics = manifest.get("portSemantics", {}).get(name, {})
            location = path + pointer(["inputs", name])
            selector = targets[name].get("selector")
            if ports and selector and selector["parameter"] not in values:
                self.issue("PARAMETER_SOURCE_MISSING", location + "/selector", severity="missing")
            for port in ports:
                self._requirements(semantics.get("requires", {}), semantics.get("requiredFacts", []), port, location)
                for role, binding in sorted(semantics.get("fieldBindings", {}).items()):
                    field_path = location + pointer([role])
                    fields = port.facts.get("fields")
                    selected = values.get(binding["parameter"])
                    if fields is None or selected is None:
                        self.issue("FIELD_ROLE_UNBOUND", field_path, severity="missing")
                    elif not isinstance(selected, str) or selected not in fields.value:
                        self.issue("FIELD_ROLE_UNBOUND", field_path, actual=selected, sources=fields.sources)
                    elif required := binding.get("requires"):
                        selected_facts = {
                            key: Fact(value, fields.sources) for key, value in fields.value[selected].items()
                        }
                        self._requirements(required, [], Port(port.contract, selected_facts), field_path)
        output_semantics = manifest.get("outputSemantics", {})
        produced = {
            port["name"]: [self._produce(port, output_semantics.get(port["name"], {}), inputs, values, dynamic, path)]
            for port in manifest["outputs"]
        }
        for name, ports in produced.items():
            self.outputs[node["nodeId"], name] = ports[0]
        for check in manifest.get("constraints", []):
            self._check(node, check, inputs, produced, values, path)

    def _produce(self, port, semantics, inputs, values, dynamic, path):
        name = port["name"]
        location = path + pointer(["outputs", name])
        result = {key: Fact(value) for key, value in semantics.get("fixed", {}).items()}
        measured = set(semantics.get("measured", []))
        for rule in semantics.get("inherit", []):
            sources = inputs[rule["inputPort"]]
            for key in rule["facts"]:
                facts = [source.facts.get(key) for source in sources]
                if facts and facts[0] is not None and all(fact is not None and fact.value == facts[0].value for fact in facts):
                    provenance = []
                    for fact in facts:
                        provenance.extend(ref for ref in fact.sources if ref not in provenance)
                    result[key] = Fact(facts[0].value, tuple(provenance))
        for rule in semantics.get("transform", []):
            parameter = rule["parameter"]
            if parameter not in values:
                if parameter in dynamic:
                    raise WorkflowValidationError("FEATURE_UNSUPPORTED", location)
                continue
            value = values[parameter]
            key = rule["property"]
            validate_fact_value(key, value, self.vocabulary, location + pointer([key]))
            provenance = ()
            if rule["rule"] == "convertUnit":
                source = inputs[rule["inputPort"]]
                for key_name in ("quantityKind", "measurementBasis", "verticalDatum"):
                    fact = result.get(key_name)
                    if fact is not None and any(
                        item.facts.get(key_name) is None or item.facts[key_name].value != fact.value
                        for item in source
                    ):
                        raise WorkflowValidationError("COMPOSITION_TRANSFORM_INVALID", location)
                units = [item.facts.get("unit") for item in source]
                if not units or any(unit is None for unit in units):
                    self.issue("UNIT_UNKNOWN", location + "/unit", severity="missing")
                    continue
                registry = self.vocabulary["units"]
                if any(registry[unit.value]["dimension"] != registry[value]["dimension"] for unit in units):
                    self.issue("UNIT_MISMATCH", location + "/unit", expected=value)
                    continue
                refs = []
                for unit in units:
                    refs.extend(ref for ref in unit.sources if ref not in refs)
                provenance = tuple(refs)
            result[key] = Fact(value, provenance)
        if cases := port.get("formatCases"):
            parameter = cases[0]["parameter"]
            if parameter in values:
                token = definition_digest({"value": values[parameter]})
                selected = [case["formatPair"] for case in cases if token in {definition_digest({"value": value}) for value in case["values"]}]
                if not selected:
                    raise WorkflowValidationError("COMPOSITION_FORMAT_CASE_INVALID", location)
                port = {**port, "formatPairs": selected}
        return Port(port, result, frozenset(measured))

    def _check(self, node, check, inputs, outputs, values, path):
        if check["validatorRef"] != self.workflow["validatorRef"]:
            raise WorkflowValidationError("COMPOSITION_VALIDATOR_MISMATCH", path + "/operator")
        stage = check["checkStage"]
        available = {("input", name): ports for name, ports in inputs.items()}
        if stage == "afterNode":
            available.update({("output", name): ports for name, ports in outputs.items()})
        evaluation = evaluate(
            check["condition"], {name: [port.facts for port in ports] for name, ports in available.items()},
            values, {
                (*name, key) for name, ports in available.items() for key in FULL_SCOPE
                if ports and all(key in port.facts or key in port.measured for port in ports)
            },
            self.vocabulary,
        )
        state = evaluation.state
        if state == "UNKNOWN" and evaluation.can_defer and stage != "compile":
            state = "DEFERRED"
        self.report["checks"].append({
            "nodeId": node["nodeId"], "id": check["id"], "state": state, "stage": stage,
            "validatorRef": check["validatorRef"],
            **({"reason": "condition-not-applicable"} if state == "SKIPPED" else {}),
        })
        if state in {"FAIL", "UNKNOWN"}:
            severity = "warning" if check["severity"] == "warning" else "error" if state == "FAIL" else "missing"
            self.issue(
                evaluation.code, path + pointer(["checks", check["id"]]),
                expected=check["condition"], actual=list(evaluation.values),
                severity=severity, sources=evaluation.sources,
                repair=("supply-verified-fact",) if state == "UNKNOWN" else (),
            )

    def _output(self, name, output):
        path = pointer(["outputs", name])
        source = self._upstream(output["nodeOutput"], path + "/nodeOutput")
        goal = output["goal"]
        if source.contract["logicalType"] != goal["logicalType"]:
            raise WorkflowValidationError("OUTPUT_CONTRACT_VIOLATION", path + "/goal/logicalType")
        validate_requirements(goal["requires"], self.vocabulary, path + "/goal/requires")
        self._requirements(goal["requires"], [], source, path + "/goal")
        publication = output["publication"]
        cardinality = source.contract.get("cardinality", SINGLE_ITEM)
        if (
            source.contract["kind"] != "artifact"
            or cardinality["minItems"] != 1
            or cardinality["maxItems"] != 1
            or {format_id for _, format_id in format_pairs(source.contract)} != {publication["format"]}
            or (publication["policy"] == "FILE_QUERYABLE" and publication["format"] not in {"csv", "parquet"})
            or (publication["policy"] == "FILE_AND_METRICS" and publication["format"] != "json")
        ):
            raise WorkflowValidationError("OUTPUT_CONTRACT_VIOLATION", path + "/publication")
        self.report["outputs"][name] = {
            "logicalType": source.contract["logicalType"],
            "formatPairs": source.contract.get("formatPairs", []),
            "facts": {key: fact.value for key, fact in source.facts.items()},
        }


def validate_composition(workflow: dict, operators: dict, vocabulary: dict, *, environment_ref: dict) -> dict:
    """Check an offline candidate against trusted catalog, vocabulary and environment inputs."""
    validation = _Validation(workflow, operators, vocabulary)
    try:
        validation.validate(environment_ref)
    except WorkflowValidationError as error:
        validation.issue(error.code, error.path)
    validate_document("composition-validation.v1", validation.report)
    return validation.report
