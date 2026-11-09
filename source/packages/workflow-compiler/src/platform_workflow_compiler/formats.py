"""Pure artifact format validation and conditional output propagation."""
from __future__ import annotations

from .validation import WorkflowValidationError

MEDIA_TYPE_FORMATS = {
    "application/geopackage+sqlite3": {"geopackage"},
    "application/geo+json": {"geojson"},
    "application/flatgeobuf": {"flatgeobuf"},
    "application/vnd.apache.parquet": {"parquet", "geoparquet"},
    "image/tiff": {"geotiff", "cog"},
    "image/png": {"png"},
    "application/vnd.las": {"las"},
    "application/vnd.laz": {"laz", "copc"},
    "text/csv": {"csv"},
    "application/json": {"json"},
    "application/vnd.apache.arrow.file": {"arrow-ipc-file"},
    "application/x-npy": {"npy"},
    "application/vnd.pytorch": {"pytorch-state-dict"},
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {"xlsx"},
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {"docx"},
    "application/pdf": {"pdf"},
    "text/plain": {"text"},
    "text/markdown": {"markdown"},
    "application/zip": {"zip"},
    "application/octet-stream": {"binary"},
}

def _port_pairs(port: dict) -> set[tuple[str, str]]:
    if "formatPairs" in port:
        return {(item["mediaType"], item["format"]) for item in port["formatPairs"]}
    media_types, formats = port.get("mediaTypes", []), port.get("formats", [])
    if (
        not isinstance(media_types, list)
        or not isinstance(formats, list)
        or any(not isinstance(item, str) for item in [*media_types, *formats])
        or len(media_types) != len(set(media_types))
        or len(formats) != len(set(formats))
    ):
        raise WorkflowValidationError("WORKFLOW_PORT_FORMAT_INVALID")
    pairs = {
        (media_type, format_id)
        for media_type in media_types
        for format_id in formats
        if format_id in MEDIA_TYPE_FORMATS.get(media_type, set())
    }
    if not pairs or {item[0] for item in pairs} != set(media_types) or {
        item[1] for item in pairs
    } != set(formats):
        raise WorkflowValidationError("WORKFLOW_PORT_FORMAT_INVALID")
    return pairs


def _compiled_port(port: dict, pairs: set[tuple[str, str]]) -> dict:
    return {
        **port,
        "formatPairs": [
            {"mediaType": media_type, "format": format_id}
            for media_type, format_id in sorted(pairs)
        ],
    }


def _formats_compatible(source: dict, target: dict) -> bool:
    return _port_pairs(source) <= _port_pairs(target)


def _conditional_output_pairs(
    output: dict,
    operator: dict,
    bound_inputs: dict[str, set[tuple[str, str]]],
    bound_parameters: dict[str, object] | None = None,
) -> set[tuple[str, str]]:
    declared = _port_pairs(output)
    cases = output.get("formatCases")
    parameter_cases = output.get("parameterFormatCases")
    if cases is not None and parameter_cases is not None:
        raise WorkflowValidationError("OPERATOR_FORMAT_CASE_INVALID")
    if parameter_cases is not None:
        return _parameter_output_pairs(
            parameter_cases, declared, operator, bound_parameters
        )
    if cases is None:
        return declared
    if (
        not isinstance(cases, list)
        or not cases
        or any(not isinstance(item, dict) for item in cases)
        or any(not isinstance(item.get("inputPort"), str) for item in cases)
    ):
        raise WorkflowValidationError("OPERATOR_FORMAT_CASE_INVALID")
    input_ports = {item["name"]: item for item in operator["inputs"]}
    case_inputs = {item.get("inputPort") for item in cases}
    if len(case_inputs) != 1 or next(iter(case_inputs)) not in input_ports:
        raise WorkflowValidationError("OPERATOR_FORMAT_CASE_INVALID")
    input_name = next(iter(case_inputs))
    declared_inputs = {format_id for _, format_id in _port_pairs(input_ports[input_name])}
    covered: set[str] = set()
    case_pairs: set[tuple[str, str]] = set()
    possible: set[tuple[str, str]] = set()
    selected_formats = {format_id for _, format_id in bound_inputs[input_name]}
    for case in cases:
        if not isinstance(case, dict) or set(case) != {
            "inputPort", "inputFormats", "mediaType", "format"
        } or not isinstance(case["inputFormats"], list) or any(
            not isinstance(item, str) for item in case["inputFormats"]
        ) or not isinstance(case["mediaType"], str) or not isinstance(case["format"], str):
            raise WorkflowValidationError("OPERATOR_FORMAT_CASE_INVALID")
        formats = set(case["inputFormats"])
        if (
            not formats
            or len(formats) != len(case["inputFormats"])
            or covered & formats
            or not formats <= declared_inputs
        ):
            raise WorkflowValidationError("OPERATOR_FORMAT_CASE_INVALID")
        covered |= formats
        pair = (case["mediaType"], case["format"])
        if pair not in declared:
            raise WorkflowValidationError("OPERATOR_FORMAT_CASE_INVALID")
        case_pairs.add(pair)
        if selected_formats & formats:
            possible.add(pair)
    if covered != declared_inputs or case_pairs != declared or not possible:
        raise WorkflowValidationError("OPERATOR_FORMAT_CASE_INVALID")
    return possible


def _parameter_output_pairs(
    cases: object,
    declared: set[tuple[str, str]],
    operator: dict,
    bound_parameters: dict[str, object] | None,
) -> set[tuple[str, str]]:
    if (
        not isinstance(cases, list)
        or not cases
        or any(not isinstance(item, dict) for item in cases)
    ):
        raise WorkflowValidationError("OPERATOR_PARAMETER_FORMAT_CASE_INVALID")
    selectors = {item.get("parameter") for item in cases}
    if len(selectors) != 1 or not isinstance(next(iter(selectors)), str):
        raise WorkflowValidationError("OPERATOR_PARAMETER_FORMAT_CASE_INVALID")
    selector = next(iter(selectors))
    schema = operator.get("parametersSchema", {}).get("properties", {}).get(selector)
    enum = schema.get("enum") if isinstance(schema, dict) else None
    if (
        not isinstance(schema, dict)
        or schema.get("type") != "string"
        or not isinstance(enum, list)
        or not enum
        or any(not isinstance(value, str) for value in enum)
        or len(enum) != len(set(enum))
    ):
        raise WorkflowValidationError("OPERATOR_PARAMETER_FORMAT_CASE_INVALID")
    covered: set[str] = set()
    selected_pair: tuple[str, str] | None = None
    selected_value = (
        bound_parameters.get(selector) if bound_parameters is not None else None
    )
    for case in cases:
        if set(case) != {"parameter", "values", "mediaType", "format"} or not isinstance(
            case["values"], list
        ) or any(not isinstance(value, str) for value in case["values"]) or not isinstance(
            case["mediaType"], str
        ) or not isinstance(case["format"], str):
            raise WorkflowValidationError("OPERATOR_PARAMETER_FORMAT_CASE_INVALID")
        values = set(case["values"])
        pair = case["mediaType"], case["format"]
        if (
            not values
            or len(values) != len(case["values"])
            or covered & values
            or not values <= set(enum)
            or pair not in declared
        ):
            raise WorkflowValidationError("OPERATOR_PARAMETER_FORMAT_CASE_INVALID")
        covered |= values
        if selected_value in values:
            selected_pair = pair
    if covered != set(enum):
        raise WorkflowValidationError("OPERATOR_PARAMETER_FORMAT_CASE_INVALID")
    if bound_parameters is None or selector not in bound_parameters:
        return declared
    if selected_pair is None:
        raise WorkflowValidationError("OPERATOR_PARAMETER_FORMAT_CASE_INVALID")
    return {selected_pair}
