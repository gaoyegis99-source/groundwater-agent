"""Compile product-owned business goals to exact Workflow Platform entries."""

from functools import cache
import json
from pathlib import Path
import re
import sys

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from assembler_common import _checksum, _validate_value
from assembler_workflow_platform import _identity, _pinned_json, _ref


SCHEMA = "business-capabilities.v1.schema.json"
ERROR = "BUSINESS_CAPABILITY_INVALID"
_SEMANTIC_CHECKS = {
    "fieldUnit": "FIELD_UNIT",
    "timeField": "TIME_FIELD",
    "timeRange": "TIME_RANGE",
    "crs": "CRS",
    "crsParameter": "CRS_PARAMETER",
    "verticalBasis": "VERTICAL_BASIS",
}


@cache
def _validator():
    path = Path(__file__).with_name(SCHEMA)
    if not path.is_file():
        path = Path(sys.prefix) / "share/platform-assembler" / SCHEMA
    schema = json.loads(path.read_bytes())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _terms_match(message: str, groups: list[list[str]]) -> bool:
    value = message.casefold()
    return all(any(term.casefold() in value for term in group) for group in groups)


def _model_arguments(profile, model):
    schema = profile["inputSchema"]
    input_names = set(model["inputBindings"].values())
    known = {**profile["modelDefaults"], **profile["fixedParameters"], **dict.fromkeys(input_names, "version")}
    required = {
        name
        for issue in Draft202012Validator(schema).iter_errors(known)
        if issue.validator == "required"
        for name in issue.validator_value
        if name not in known
    }
    return schema["properties"], required


def _validate_patterns(patterns, argument_schema, *, numeric_threshold):
    value_type = argument_schema.get("type")
    if value_type not in {"string", "integer", "number"}:
        raise ValueError(f"{ERROR}: text parameter type")
    for item in patterns:
        try:
            pattern = re.compile(item["pattern"], re.IGNORECASE)
        except re.error as error:
            raise ValueError(f"{ERROR}: invalid text pattern") from error
        fields = set(pattern.groupindex)
        fixed = "value" in item
        if (
            pattern.search("")
            or pattern.groups != len(fields)
            or fields - ({"value", "unit"} if numeric_threshold else {"value"})
            or fixed == ("value" in fields)
            or (fixed and "values" in item)
            or ("values" in item and value_type != "string")
        ):
            raise ValueError(f"{ERROR}: invalid text capture")
        for value in ([item["value"]] if fixed else item.get("values", {}).values()):
            _validate_value(argument_schema, value, f"{ERROR}: invalid text value")


def _validate_metadata(mapping, model):
    fields = {item["parameter"] for item in model.get("fieldRequirements", [])}
    semantic = model.get("semanticRequirements", [])
    anchors = {
        "TIME_FIELD": {item["fieldParameter"] for item in semantic if item["kind"] == "timeField"},
        "UNIT_FIELD": {item["fieldParameter"] for item in semantic if item["kind"] == "fieldUnit"},
        "TIME_RANGE_START": {item["startParameter"] for item in semantic if item["kind"] == "timeRange"},
        "TIME_RANGE_END": {item["endParameter"] for item in semantic if item["kind"] == "timeRange"},
    }
    anchors["GROUP_FIELD"] = fields - anchors["TIME_FIELD"] - anchors["UNIT_FIELD"]
    if (
        len(set(mapping.values())) != len(mapping)
        or any(parameter not in anchors[role] for parameter, role in mapping.items())
    ):
        raise ValueError(f"{ERROR}: invalid trusted metadata mapping")
    ranges = {
        name: parameter for parameter, name in mapping.items()
        if name in {"TIME_RANGE_START", "TIME_RANGE_END"}
    }
    if ranges and not any(
        item["kind"] == "timeRange"
        and item["startParameter"] == ranges.get("TIME_RANGE_START")
        and item["endParameter"] == ranges.get("TIME_RANGE_END")
        for item in semantic
    ):
        raise ValueError(f"{ERROR}: invalid time range mapping")


def _compile_option(raw, capability, profiles, contracts, models, ui):
    profile = profiles.get(_identity(raw["profileRef"]))
    if profile is None or raw["profileRef"] != profile["ref"]:
        raise ValueError(f"{ERROR}: Workflow Profile pin")
    roles = sorted(set(capability["roles"]) & set(profile["visibleRoles"]))
    if not roles:
        raise ValueError(f"{ERROR}: no authorized Profile role")
    model = models[_identity(profile["modelRef"])]
    if not {"data", model["executionService"]} <= set(capability["requiredServices"]):
        raise ValueError(f"{ERROR}: missing runtime dependency declaration")
    supported_checks = {
        _SEMANTIC_CHECKS[requirement["kind"]]
        for requirement in model.get("semanticRequirements", [])
        if requirement["kind"] in _SEMANTIC_CHECKS
    }
    if model.get("fieldRequirements"):
        supported_checks.add("FIELD_METADATA")
    if "TIME_FIELD" in supported_checks:
        supported_checks.add("UTC_TIME_FIELD")
    if not set(raw["requiredChecks"]) <= supported_checks:
        raise ValueError(f"{ERROR}: unsupported mandatory preparation check")
    contract = contracts[_identity(profile["resultContractRef"])]
    properties, required = _model_arguments(profile, model)
    specs = {item["name"]: item for item in raw["textParameters"]}
    if (
        len(specs) != len(raw["textParameters"])
        or not (specs.keys() | raw["metadataParameters"].keys()) <= set(profile["adjustableParameters"])
        or not required <= specs.keys()
    ):
        raise ValueError(f"{ERROR}: unsupported or missing user parameter")
    threshold = raw.get("thresholdParameter")
    if threshold is not None and (threshold not in required or properties[threshold].get("type") != "number"):
        raise ValueError(f"{ERROR}: invalid threshold parameter")
    for name, item in specs.items():
        _validate_patterns(
            item["patterns"] + item.get("replyPatterns", []), properties[name],
            numeric_threshold=name == threshold,
        )
        try:
            for pattern in item.get("unresolvedPatterns", []):
                if re.compile(pattern, re.IGNORECASE).search(""):
                    raise ValueError(f"{ERROR}: empty unresolved pattern")
        except re.error as error:
            raise ValueError(f"{ERROR}: invalid unresolved pattern") from error
    _validate_metadata(raw["metadataParameters"], model)
    model_ui = ui["models"].get(model["modelId"], {})
    inputs = [
        {
            "label": model_ui.get("inputs", {}).get(spec["modelParameter"], {}).get("label", name),
            "meaning": spec["meaning"], "preconditions": spec["preconditions"],
            "formats": sorted({pair["format"] for pair in spec["formatPairs"]}),
        }
        for name, spec in profile["inputRoles"].items()
    ]
    display = {
        **{name: profile[name] for name in ("modelRef", "workflowRef", "resultContractRef", "label")},
        "profileRef": profile["ref"], "roles": roles, "inputs": inputs,
        "outputs": [
            {
                key: output[key]
                for key in ("name", "role", "meaning", "format", "mediaType", "unit", "qualitySource")
                if key in output
            }
            for output in contract["outputs"]
        ],
        "limitations": list(dict.fromkeys(capability["limitations"] + profile["limitations"])),
    }
    runtime = {
        **{key: display[key] for key in (
            "profileRef", "modelRef", "workflowRef", "resultContractRef", "roles", "limitations",
        )},
        "matchTerms": raw["matchTerms"],
        "inputRoles": {
            name: {key: spec[key] for key in ("modelParameter", "formatPairs")}
            for name, spec in profile["inputRoles"].items()
        },
        "nonOverlappingTimeRanges": profile["nonOverlappingTimeRanges"],
        "fixedParameters": profile["fixedParameters"],
        "modelDefaults": profile["modelDefaults"],
        "thresholdParameter": threshold,
        "metadataParameters": raw["metadataParameters"], "requiredChecks": raw["requiredChecks"],
        "expectedOutputs": [
            {key: output[key] for key in ("name", "meaning")}
            for output in display["outputs"] if output["role"] == "PRIMARY_RESULT"
        ],
        "textParameters": [{**item, "type": properties[item["name"]]["type"]} for item in raw["textParameters"]],
        "requiredParameters": [
            {key: item[key] for key in ("name", "label")}
            for item in raw["textParameters"] if item["name"] in required
        ],
    }
    return runtime, display


def load_business_capabilities(directory, reference, *, platform, models, ui, service_names):
    raw = _pinned_json(directory, reference, ERROR)
    try:
        _validator().validate(raw)
    except ValidationError as error:
        raise ValueError(f"{ERROR} at {error.json_path}") from error
    if platform is None:
        raise ValueError(f"{ERROR}: Workflow Platform required")
    profiles = {_identity(item["ref"]): item for item in platform["profiles"]}
    contracts = {_identity(item["ref"]): item for item in platform["resultContracts"]}
    index = {(item["modelId"], item["version"]): item for item in models}
    examples = {item["id"] for item in ui["examples"]}
    runtime, presentation = [], []
    identities = set()
    for item in raw["capabilities"]:
        identity = _identity(item)
        prompt = item["prompt"]
        if (
            identity in identities
            or not set(item["requiredServices"]) <= service_names
            or ("exampleId" in item and item["exampleId"] not in examples)
            or (item["defaultVisible"] and (item["tier"], item["validationStatus"]) != ("CORE", "TECHNICALLY_VALIDATED"))
            or not _terms_match(prompt, item["matchTerms"])
            or any(term.casefold() in prompt.casefold() for term in item.get("excludeTerms", []))
        ):
            raise ValueError(f"{ERROR}: invalid capability entry")
        identities.add(identity)
        if (
            len({_identity(option["profileRef"]) for option in item["profiles"]}) != len(item["profiles"])
            or sum(_terms_match(prompt, option["matchTerms"]) for option in item["profiles"]) != 1
        ):
            raise ValueError(f"{ERROR}: ambiguous Profile options")
        options = [
            _compile_option(option, item, profiles, contracts, index, ui)
            for option in item["profiles"]
        ]
        if item["defaultVisible"]:
            prompt_option, = [option for option, _ in options if _terms_match(prompt, option["matchTerms"])]
            if any(set(option["roles"]) - set(prompt_option["roles"]) for option, _ in options):
                raise ValueError(f"{ERROR}: default prompt has an ineligible role")
        shared = {
            "ref": _ref(item, "id", _checksum(item)),
            **{key: item[key] for key in (
                "title", "description", "scenario", "tier", "defaultVisible", "requiredServices", "validationStatus",
            )},
            "roles": sorted({role for option, _ in options for role in option["roles"]}),
        }
        runtime.append({
            **shared, "matchTerms": item["matchTerms"], "excludeTerms": item.get("excludeTerms", []),
            "profiles": [option for option, _ in options],
        })
        presentation.append({
            **shared, "prompt": prompt, **({"exampleId": item["exampleId"]} if "exampleId" in item else {}),
            "profiles": [option for _, option in options],
        })
    return runtime, presentation


def link_business_capabilities(platform, capabilities):
    by_model = {}
    for capability in capabilities:
        for profile in capability["profiles"]:
            ref = profile["modelRef"]
            entries = by_model.setdefault((ref["id"], ref["version"], ref["checksum"]), [])
            entries.append({
                "kind": "BUSINESS_CAPABILITY", "ref": capability["ref"], "profileRef": profile["profileRef"],
            })
    for workflow in platform["workflows"]:
        for model in workflow["models"]:
            ref = model["modelRef"]
            model["entries"].extend(by_model.get((ref["id"], ref["version"], ref["checksum"]), []))
