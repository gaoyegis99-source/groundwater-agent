from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from assembler_common import CHECKSUM_PATTERN, require_fields, require_string, resolve_inside


PROFILE_FIELDS = {
    "schemaVersion", "productId", "locale", "brand", "examples", "models",
    "parameterNames", "parameterStages", "outputs", "cases",
}
BRAND_FIELDS = {"productName", "workspaceName", "assistantName", "welcome", "icon"}
MODEL_FIELDS = {
    "displayName", "description", "applicability", "inputs",
    "parameterOverrides", "confirmationSummary",
}
PARAMETER_FIELDS = {"label", "group", "unit", "values"}
INPUT_FIELDS = {"label", "description", "formats"}
OUTPUT_FIELDS = {"label", "viewer"}
VIEWERS = {"auto", "table", "chart", "map", "image", "json", "metadata"}
ICON_NAMES = {"chart", "trend", "flow", "coverage", "compare", "map", "layers", "branch"}
SAFE_LOCALE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
CSS_URL = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)
_SCHEMA_PATHS = (
    Path(__file__).with_name("product-ui.v1.schema.json"),
    Path(sys.prefix) / "share/platform-assembler/product-ui.v1.schema.json",
)
PRODUCT_UI_SCHEMA = json.loads(
    next(path for path in _SCHEMA_PATHS if path.is_file()).read_text(encoding="utf-8")
)
PRODUCT_UI_VALIDATOR = Draft202012Validator(PRODUCT_UI_SCHEMA)


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _unsafe_svg_reference(value: str) -> bool:
    lowered = value.strip().lower()
    if any(
        marker in lowered for marker in ("@import", "http:", "https:", "data:", "//")
    ):
        return True
    if "url(" not in lowered:
        return False
    matches = list(CSS_URL.finditer(value))
    return not matches or any(not match.group(2).strip().startswith("#") for match in matches)


def _nonempty(value: object, code: str = "PRODUCT_UI_INVALID") -> str:
    text = require_string(value, code)
    if not text.strip() or len(text) > 1000:
        raise ValueError(code)
    return text


def _validate_svg(content: bytes) -> str:
    if len(content) > 64 * 1024:
        raise ValueError("PRODUCT_UI_ICON_INVALID")
    upper = content.upper()
    if (
        b"<!DOCTYPE" in upper
        or b"<!ENTITY" in upper
        or b"<?XML-STYLESHEET" in upper
    ):
        raise ValueError("PRODUCT_UI_ICON_INVALID")
    try:
        text = content.decode("utf-8")
        root = ET.fromstring(text)
    except (UnicodeDecodeError, ET.ParseError) as cause:
        raise ValueError("PRODUCT_UI_ICON_INVALID") from cause
    if root.tag.rsplit("}", 1)[-1] != "svg":
        raise ValueError("PRODUCT_UI_ICON_INVALID")
    for node in root.iter():
        name = node.tag.rsplit("}", 1)[-1].lower()
        if name in {"script", "foreignobject", "style"}:
            raise ValueError("PRODUCT_UI_ICON_INVALID")
        for value in (node.text, node.tail):
            if value is not None and _unsafe_svg_reference(value):
                raise ValueError("PRODUCT_UI_ICON_INVALID")
        for raw_name, value in node.attrib.items():
            attribute = raw_name.rsplit("}", 1)[-1].lower()
            lowered = value.strip().lower()
            if attribute.startswith("on") or _unsafe_svg_reference(value):
                raise ValueError("PRODUCT_UI_ICON_INVALID")
            if attribute in {"href", "src"} and not lowered.startswith("#"):
                raise ValueError("PRODUCT_UI_ICON_INVALID")
    return text


def _validate_parameter(
    value: object, schema: dict | None = None, *, exact_enum: bool = True
) -> None:
    if not isinstance(value, dict) or set(value) - PARAMETER_FIELDS:
        raise ValueError("PRODUCT_UI_PARAMETER_INVALID")
    if "label" not in value or "group" not in value:
        raise ValueError("PRODUCT_UI_PARAMETER_INVALID")
    _nonempty(value["label"], "PRODUCT_UI_PARAMETER_INVALID")
    _nonempty(value["group"], "PRODUCT_UI_PARAMETER_INVALID")
    if "unit" in value:
        _nonempty(value["unit"], "PRODUCT_UI_PARAMETER_INVALID")
    enum = schema.get("enum") if schema is not None else None
    values = value.get("values")
    if enum is None:
        if values is not None and exact_enum:
            raise ValueError("PRODUCT_UI_ENUM_INVALID")
        return
    expected = {str(item) for item in enum}
    if (
        not isinstance(values, dict)
        or (set(values) != expected if exact_enum else not expected <= set(values))
    ):
        raise ValueError("PRODUCT_UI_ENUM_INVALID")
    for label in values.values():
        _nonempty(label, "PRODUCT_UI_ENUM_INVALID")


def _parameter_presentation(profile: dict, model: dict, name: str) -> dict | None:
    override = model["parameterOverrides"].get(name)
    if override is not None:
        return override
    direct = profile["parameterNames"].get(name)
    if direct is not None:
        return direct
    if "." not in name:
        return None
    stage, base = name.split(".", 1)
    stage_value = profile["parameterStages"].get(stage)
    base_value = profile["parameterNames"].get(base)
    if stage_value is None or base_value is None:
        return None
    return {**base_value, "label": f"{stage_value['label']} · {base_value['label']}", "group": stage_value["group"]}


def load_product_ui(
    product_dir: Path,
    reference: dict[str, str],
    product_id: str,
    models: list[dict],
    cases: tuple[dict, ...],
) -> tuple[str, str, dict]:
    path = resolve_inside(product_dir, reference["path"], "PRODUCT_UI_REFERENCE_INVALID")
    raw = path.read_bytes()
    if _sha256(raw) != reference["checksum"]:
        raise ValueError("PRODUCT_UI_CHECKSUM_MISMATCH")
    try:
        profile = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as cause:
        raise ValueError("PRODUCT_UI_INVALID") from cause
    if not isinstance(profile, dict):
        raise ValueError("PRODUCT_UI_INVALID")
    require_fields(profile, PROFILE_FIELDS, "PRODUCT_UI_FIELDS_INVALID")
    if profile["schemaVersion"] != "product-ui.v1" or profile["productId"] != product_id:
        raise ValueError("PRODUCT_UI_IDENTITY_INVALID")
    if SAFE_LOCALE.fullmatch(_nonempty(profile["locale"])) is None:
        raise ValueError("PRODUCT_UI_INVALID")
    brand = profile["brand"]
    if not isinstance(brand, dict):
        raise ValueError("PRODUCT_UI_INVALID")
    require_fields(brand, BRAND_FIELDS, "PRODUCT_UI_FIELDS_INVALID")
    for key in BRAND_FIELDS - {"icon"}:
        _nonempty(brand[key])
    icon = brand["icon"]
    if not isinstance(icon, dict):
        raise ValueError("PRODUCT_UI_ICON_INVALID")
    require_fields(icon, {"path", "checksum", "mediaType"}, "PRODUCT_UI_ICON_INVALID")
    if icon != {"path": "product-icon.svg", "checksum": icon.get("checksum"), "mediaType": "image/svg+xml"} or CHECKSUM_PATTERN.fullmatch(str(icon.get("checksum"))) is None:
        raise ValueError("PRODUCT_UI_ICON_INVALID")
    icon_path = resolve_inside(path.parent, "product-icon.svg", "PRODUCT_UI_ICON_INVALID")
    icon_raw = icon_path.read_bytes()
    if _sha256(icon_raw) != icon["checksum"]:
        raise ValueError("PRODUCT_UI_ICON_CHECKSUM_MISMATCH")
    icon_text = _validate_svg(icon_raw)

    examples = profile["examples"]
    if not isinstance(examples, list) or not 1 <= len(examples) <= 12:
        raise ValueError("PRODUCT_UI_EXAMPLES_INVALID")
    example_ids: set[str] = set()
    for example in examples:
        if not isinstance(example, dict):
            raise ValueError("PRODUCT_UI_EXAMPLES_INVALID")
        require_fields(example, {"id", "title", "prompt", "icon", "featured"}, "PRODUCT_UI_EXAMPLES_INVALID")
        example_id = _nonempty(example["id"], "PRODUCT_UI_EXAMPLES_INVALID")
        if example_id in example_ids or example["icon"] not in ICON_NAMES or not isinstance(example["featured"], bool):
            raise ValueError("PRODUCT_UI_EXAMPLES_INVALID")
        example_ids.add(example_id)
        _nonempty(example["title"], "PRODUCT_UI_EXAMPLES_INVALID")
        _nonempty(example["prompt"], "PRODUCT_UI_EXAMPLES_INVALID")

    parameter_names = profile["parameterNames"]
    parameter_stages = profile["parameterStages"]
    if not isinstance(parameter_names, dict) or not isinstance(parameter_stages, dict):
        raise ValueError("PRODUCT_UI_PARAMETER_INVALID")
    used_parameter_names: set[str] = set()
    used_parameter_stages: set[str] = set()
    shared_enum_values: dict[str, set[str]] = {}
    model_profiles = profile["models"]
    if not isinstance(model_profiles, dict) or set(model_profiles) != {item["modelId"] for item in models}:
        raise ValueError("PRODUCT_UI_MODELS_INVALID")
    for model in models:
        presentation = model_profiles[model["modelId"]]
        if not isinstance(presentation, dict):
            raise ValueError("PRODUCT_UI_MODELS_INVALID")
        require_fields(presentation, MODEL_FIELDS, "PRODUCT_UI_MODEL_FIELDS_INVALID")
        for key in ("displayName", "description", "applicability"):
            _nonempty(presentation[key])
        for key in ("inputs", "parameterOverrides"):
            if not isinstance(presentation[key], dict):
                raise ValueError("PRODUCT_UI_MODELS_INVALID")
        properties = model["tool"]["inputSchema"]["properties"]
        data_parameters = set(model["inputBindings"].values())
        if set(presentation["inputs"]) != data_parameters or set(presentation["parameterOverrides"]) - set(properties):
            raise ValueError("PRODUCT_UI_INPUTS_INVALID")
        workflow_inputs = {item["name"]: item for item in model["workflow"]["inputs"]}
        reverse_bindings = {parameter: input_name for input_name, parameter in model["inputBindings"].items()}
        for parameter, value in presentation["inputs"].items():
            if not isinstance(value, dict):
                raise ValueError("PRODUCT_UI_INPUTS_INVALID")
            require_fields(value, INPUT_FIELDS, "PRODUCT_UI_INPUTS_INVALID")
            _nonempty(value["label"])
            _nonempty(value["description"])
            expected = workflow_inputs[reverse_bindings[parameter]]["formats"]
            if value["formats"] != expected:
                raise ValueError("PRODUCT_UI_INPUTS_INVALID")
        for name, schema in properties.items():
            value = _parameter_presentation(profile, presentation, name)
            if not isinstance(value, dict):
                raise ValueError("PRODUCT_UI_PARAMETER_MISSING")
            overridden = name in presentation["parameterOverrides"]
            _validate_parameter(value, schema, exact_enum=overridden)
            if not overridden:
                if name in parameter_names:
                    shared_name = name
                else:
                    stage, base = name.split(".", 1)
                    used_parameter_stages.add(stage)
                    shared_name = base
                used_parameter_names.add(shared_name)
                if "enum" in schema:
                    shared_enum_values.setdefault(shared_name, set()).update(
                        str(item) for item in schema["enum"]
                    )
        summaries = presentation["confirmationSummary"]
        if not isinstance(summaries, list):
            raise ValueError("PRODUCT_UI_CONFIRMATION_INVALID")
        for summary in summaries:
            if not isinstance(summary, dict):
                raise ValueError("PRODUCT_UI_CONFIRMATION_INVALID")
            require_fields(summary, {"label", "parameters"}, "PRODUCT_UI_CONFIRMATION_INVALID")
            _nonempty(summary["label"])
            if not isinstance(summary["parameters"], list) or not summary["parameters"] or set(summary["parameters"]) - set(properties):
                raise ValueError("PRODUCT_UI_CONFIRMATION_INVALID")

    if (
        set(parameter_names) != used_parameter_names
        or set(parameter_stages) != used_parameter_stages
    ):
        raise ValueError("PRODUCT_UI_PARAMETER_UNKNOWN")
    for name in parameter_names:
        # Enum-specific validation above uses the concrete model schema. This pass
        # validates shared entries that may only contribute to a composed name.
        value = parameter_names[name]
        if not isinstance(value, dict):
            raise ValueError("PRODUCT_UI_PARAMETER_INVALID")
        if "values" in value:
            if not isinstance(value["values"], dict):
                raise ValueError("PRODUCT_UI_ENUM_INVALID")
            for label in value["values"].values():
                _nonempty(label, "PRODUCT_UI_ENUM_INVALID")
            if set(value["values"]) != shared_enum_values.get(name, set()):
                raise ValueError("PRODUCT_UI_ENUM_INVALID")
        elif name in shared_enum_values:
            raise ValueError("PRODUCT_UI_ENUM_INVALID")
        if set(value) - PARAMETER_FIELDS or "label" not in value or "group" not in value:
            raise ValueError("PRODUCT_UI_PARAMETER_INVALID")
        _nonempty(value["label"], "PRODUCT_UI_PARAMETER_INVALID")
        _nonempty(value["group"], "PRODUCT_UI_PARAMETER_INVALID")
        if "unit" in value:
            _nonempty(value["unit"], "PRODUCT_UI_PARAMETER_INVALID")
    for name in used_parameter_stages:
        value = parameter_stages[name]
        if not isinstance(value, dict):
            raise ValueError("PRODUCT_UI_PARAMETER_INVALID")
        require_fields(value, {"label", "group"}, "PRODUCT_UI_PARAMETER_INVALID")
        _nonempty(value["label"], "PRODUCT_UI_PARAMETER_INVALID")
        _nonempty(value["group"], "PRODUCT_UI_PARAMETER_INVALID")

    outputs = profile["outputs"]
    expected_outputs = {name for model in models for name in model["finalOutputs"]}
    if not isinstance(outputs, dict) or set(outputs) != expected_outputs:
        raise ValueError("PRODUCT_UI_OUTPUTS_INVALID")
    for value in outputs.values():
        if not isinstance(value, dict):
            raise ValueError("PRODUCT_UI_OUTPUTS_INVALID")
        require_fields(value, OUTPUT_FIELDS, "PRODUCT_UI_OUTPUTS_INVALID")
        _nonempty(value["label"])
        if value["viewer"] not in VIEWERS:
            raise ValueError("PRODUCT_UI_OUTPUTS_INVALID")

    expected_cases = {item["caseId"]: set(item["profiles"]) for item in cases}
    ui_cases = profile["cases"]
    if not isinstance(ui_cases, dict) or set(ui_cases) != set(expected_cases):
        raise ValueError("PRODUCT_UI_CASES_INVALID")
    for case_id, profiles in expected_cases.items():
        value = ui_cases[case_id]
        if not isinstance(value, dict):
            raise ValueError("PRODUCT_UI_CASES_INVALID")
        require_fields(value, {"label", "profiles"}, "PRODUCT_UI_CASES_INVALID")
        _nonempty(value["label"])
        if not isinstance(value["profiles"], dict) or set(value["profiles"]) != profiles:
            raise ValueError("PRODUCT_UI_CASES_INVALID")
        for label in value["profiles"].values():
            _nonempty(label)

    try:
        PRODUCT_UI_VALIDATOR.validate(profile)
    except ValidationError as cause:
        raise ValueError("PRODUCT_UI_SCHEMA_INVALID") from cause

    rendered = json.dumps(profile, ensure_ascii=False, indent=2) + "\n"
    identity = {
        "schemaVersion": "product-ui.v1",
        "profileSourceChecksum": reference["checksum"],
        "profileOutputChecksum": _sha256(rendered.encode("utf-8")),
        "iconChecksum": icon["checksum"],
    }
    return rendered, icon_text, identity
