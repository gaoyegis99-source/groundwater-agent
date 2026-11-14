"""Evaluate composition constraints against explicit, versioned facts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from itertools import product
from struct import pack, unpack
import re

from ..validation import WorkflowValidationError, pointer
from .documents import FORMAT_CHECKER, definition_digest, validate_document


def _number(value):
    return type(value) in (int, float)


def _text(value):
    return isinstance(value, str) and bool(value)


def _texts(value):
    return isinstance(value, list) and all(_text(item) for item in value)


def _integer(value):
    return type(value) is int and value >= 0


def _grid(value):
    return (
        isinstance(value, dict)
        and set(value) == {"crs", "width", "height", "transform"}
        and _text(value["crs"])
        and all(_integer(value[key]) and value[key] > 0 for key in ("width", "height"))
        and isinstance(value["transform"], list)
        and len(value["transform"]) == 6
        and all(_number(number) for number in value["transform"])
    )


def _interval(value):
    if not isinstance(value, list) or len(value) != 2 or not all(
        _text(item) and FORMAT_CHECKER.conforms(item, "date-time") for item in value
    ):
        return False
    try:
        start, end = [datetime.fromisoformat(item.upper()) for item in value]
        return start.utcoffset() is not None and end.utcoffset() is not None and start < end
    except ValueError:
        return False


def _fields(value):
    return isinstance(value, dict) and all(
        _text(key) and isinstance(item, dict) and "dtype" in item and _text(item["dtype"])
        and item.keys() <= {"dtype", "unit", "quantityKind", "role", "nullable"}
        and all(type(v) is bool if k == "nullable" else _text(v) for k, v in item.items())
        for key, item in value.items()
    )


def _shape(value):
    return isinstance(value, list) and bool(value) and all(_integer(size) for size in value)


def _identity(value):
    return (
        isinstance(value, dict) and set(value) == {"id", "version", "digest"}
        and all(_text(value[key]) for key in value)
        and re.fullmatch(r"[a-z][a-z0-9_.-]*", value["id"]) is not None
        and re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value["version"]) is not None
        and re.fullmatch(r"sha256:[0-9a-f]{64}", value["digest"]) is not None
    )


RASTER_MASK_FLAGS = {"all_valid", "nodata", "alpha", "per_dataset"}
INTEGER_RASTER_RANGES = {
    name: (-(2 ** (bits - 1)), 2 ** (bits - 1) - 1) if signed else (0, 2**bits - 1)
    for bits in (8, 16, 32) for signed in (True, False)
    for name in [("int" if signed else "uint") + str(bits)]
}


def _raster_bands(value):
    return isinstance(value, list) and bool(value) and all(
        isinstance(band, dict) and set(band) == {"dtype", "nodata", "maskFlags"}
        and _text(band["dtype"]) and _texts(band["maskFlags"])
        and (band["nodata"] in (None, "nan", "infinite") or _number(band["nodata"]))
        for band in value
    )


FACT_TYPES: dict[str, Callable] = {
    "quantityKind": _text, "unit": _text, "verticalDatum": _text,
    "measurementBasis": _text, "crs": _text, "grid": _grid, "axes": _texts,
    "dtype": _text, "shape": _shape, "featureOrder": _texts, "fields": _fields,
    "keys": _texts, "rowOrder": _texts, "grain": _text, "timeField": _text,
    "timezone": _text, "calendar": _text, "frequency": _text, "aggregation": _text,
    "timeAxis": _texts, "timeInterval": _interval, "geometryType": _text,
    "dimensions": _integer, "maskPolicy": _text, "nodata": lambda v: v is None or _number(v),
    "rasterBands": _raster_bands,
    "valueKind": _text, "target": _text, "preprocessor": _identity,
    "structureDigest": _text, "weightFormat": _text, "methodId": _text,
    "trainingInterval": _interval, "evaluationInterval": _interval,
    "trainingEntities": _texts, "evaluationEntities": _texts,
    "rowCount": _integer, "uniqueKeys": _texts, "finite": lambda v: type(v) is bool,
    "coverage": lambda v: _number(v) and 0 <= v <= 1,
    "language": _text, "encoding": _text, "extractionMethod": _text,
    "truncated": lambda v: type(v) is bool, "sampleIndex": _identity,
    "normalization": _text, "pageCount": _integer, "schemaId": _text, "legend": _texts,
}
FULL_SCOPE = {"rowCount", "uniqueKeys", "finite", "coverage"}


def validate_vocabulary(document: dict) -> dict:
    validate_document("composition-vocabulary.v1", document)
    return {"id": document["id"], "version": document["version"], "digest": definition_digest(document)}


def validate_fact_value(property_name, value, vocabulary, path):
    if property_name not in FACT_TYPES:
        raise WorkflowValidationError("COMPOSITION_FACT_UNSUPPORTED", path)
    if not FACT_TYPES[property_name](value):
        raise WorkflowValidationError("COMPOSITION_FACT_VALUE_INVALID", path)
    registry = {"unit": "units", "quantityKind": "quantities", "crs": "crs"}
    if property_name in registry and value not in vocabulary[registry[property_name]]:
        raise WorkflowValidationError("COMPOSITION_VOCABULARY_REFERENCE_UNKNOWN", path)
    if property_name == "grid":
        validate_fact_value("crs", value["crs"], vocabulary, path + "/crs")
    if property_name == "fields":
        for field, record in value.items():
            for key in record.keys() & registry.keys():
                validate_fact_value(key, record[key], vocabulary, path + pointer([field, key]))


@dataclass(frozen=True)
class Fact:
    value: object
    sources: tuple[dict, ...] = ()


def input_facts(document, vocabulary, path):
    reference = document.get("dataRef")
    result = {}
    seen = set()
    for index, fact in enumerate(document["facts"]):
        location = path + pointer(["facts", index])
        property_name = fact["property"]
        if property_name in seen:
            raise WorkflowValidationError("COMPOSITION_FACT_DUPLICATE", location)
        seen.add(property_name)
        if property_name not in FACT_TYPES:
            raise WorkflowValidationError("COMPOSITION_FACT_UNSUPPORTED", location + "/property")
        subject = fact["subjectRef"]
        if reference is None or subject["id"] != reference["versionId"] or subject["digest"] != reference["checksum"]:
            raise WorkflowValidationError("COMPOSITION_FACT_SUBJECT_MISMATCH", location + "/subjectRef")
        if fact["verificationScope"] == "declared" and (
            fact["sourceRef"]["id"] != reference["semanticId"]
            or fact["sourceRef"]["digest"] != reference["semanticChecksum"]
        ):
            raise WorkflowValidationError("COMPOSITION_FACT_SOURCE_MISMATCH", location + "/sourceRef")
        if fact["state"] != "known":
            continue
        validate_fact_value(property_name, fact["value"], vocabulary, location + "/value")
        scope = fact["verificationScope"]
        if scope == "sample" or (property_name in FULL_SCOPE and scope != "full"):
            continue
        result[property_name] = Fact(fact["value"], (fact["sourceRef"],))
    return result


def _equals(values, _vocabulary):
    return values[0] == values[1]


def _metric(values, vocabulary):
    return vocabulary["crs"][values[0]]["metric"]


def _grid_matches(values, _vocabulary):
    left, right, tolerance = values
    return (
        all(left[key] == right[key] for key in ("crs", "width", "height"))
        and all(abs(a - b) <= tolerance for a, b in zip(left["transform"], right["transform"], strict=True))
    )


def _unit(values, vocabulary):
    left, right = (vocabulary["units"][name] for name in values)
    return left == right


def _nonoverlap(values, _vocabulary):
    left, right = [[datetime.fromisoformat(bound.upper()) for bound in interval] for interval in values]
    return left[1] <= right[0] or right[1] <= left[0]


def _entity_disjoint(values, _vocabulary):
    return set(values[0]).isdisjoint(values[1])


def _field_exists(values, _vocabulary):
    return values[1] in values[0]


def _minimum(values, _vocabulary):
    return values[0] >= values[1]


def _finite(values, _vocabulary):
    return values[0]


def _keys(values, _vocabulary):
    return set(values[0]) <= set(values[1])


def _raster_band_exists(values, _vocabulary):
    bands, band = values
    return type(band) is int and 1 <= band <= len(bands)


def _raster_mask_supported(values, vocabulary):
    if not _raster_band_exists(values, vocabulary):
        return False
    bands, band = values
    selected = bands[band - 1]
    flags = set(selected["maskFlags"])
    return (flags <= RASTER_MASK_FLAGS and selected["nodata"] != "infinite"
            and not selected["dtype"].startswith("complex")
            and ("nodata" not in flags or selected["nodata"] is not None))


def _raster_nodata_fits(values, _vocabulary):
    value, dtype = values
    if not _number(value):
        return False
    if dtype in INTEGER_RASTER_RANGES:
        lower, upper = INTEGER_RASTER_RANGES[dtype]
        return lower <= value <= upper and int(value) == value
    if dtype == "float64":
        return True
    if dtype == "float32":
        try:
            return unpack("f", pack("f", value))[0] == value
        except OverflowError:
            return False
    return False


# Signatures bind each position to a fact type, including literals and parameters.
# A tuple of property names permits comparable facts from distinct roles.
PREDICATES = {
    "sameQuantity": (("quantityKind", "quantityKind"), _equals, "SEMANTIC_QUANTITY_MISMATCH"),
    "sameDatum": (("verticalDatum", "verticalDatum"), _equals, "VERTICAL_DATUM_MISMATCH"),
    "sameMeasurementBasis": (("measurementBasis", "measurementBasis"), _equals, "MEASUREMENT_BASIS_MISMATCH"),
    "sameCrs": (("crs", "crs"), _equals, "CRS_MISMATCH"),
    "sameGrid": (("grid", "grid", "nonnegativeNumber"), _grid_matches, "GRID_MISMATCH"),
    "rasterBandExists": (("rasterBands", "nonnegativeNumber"), _raster_band_exists, "BAND_INVALID"),
    "rasterMaskSupported": (("rasterBands", "nonnegativeNumber"), _raster_mask_supported, "NODATA_MASK_UNSUPPORTED"),
    "rasterNoDataFits": (("nodata", "text"), _raster_nodata_fits, "NODATA_VALUE_INVALID"),
    "unitCompatible": (("unit", "unit"), _unit, "UNIT_MISMATCH"),
    "metricCrs": (("crs",), _metric, "METRIC_CRS_REQUIRED"),
    "fieldExists": (("fields", "text"), _field_exists, "FIELD_ROLE_UNBOUND"),
    "dtypeCompatible": (("dtype", "dtype"), _equals, "DTYPE_MISMATCH"),
    "uniqueKeys": (("uniqueKeys", "keys"), _keys, "KEYS_NOT_UNIQUE"),
    "tensorShape": (("shape", "shape"), _equals, "TENSOR_SHAPE_MISMATCH"),
    "geometryAllowed": (("geometryType", "geometryType"), _equals, "GEOMETRY_TYPE_MISMATCH"),
    "timeAxisPolicy": (("timeAxis", "timeAxis"), _equals, "TIME_AXIS_MISMATCH"),
    "calendarCompatible": (("calendar", "calendar"), _equals, "CALENDAR_MISMATCH"),
    "nonOverlappingIntervals": (("timeInterval", "timeInterval"), _nonoverlap, "TIME_INTERVAL_OVERLAP"),
    "featureOrderMatches": (("featureOrder", "featureOrder"), _equals, "MODEL_FEATURE_ORDER_MISMATCH"),
    "preprocessorMatches": (("preprocessor", "preprocessor"), _equals, "MODEL_PREPROCESSOR_MISMATCH"),
    "trainingDisjointFromEvaluation": (("trainingInterval", "evaluationInterval"), _nonoverlap, "TRAINING_EVALUATION_OVERLAP"),
    "trainingEntitiesDisjoint": (("trainingEntities", "evaluationEntities"), _entity_disjoint, "TRAINING_EVALUATION_OVERLAP"),
    "finiteValues": (("finite",), _finite, "NON_FINITE_VALUES"),
    "minimumRows": (("rowCount", "rowCount"), _minimum, "MINIMUM_ROWS_NOT_MET"),
    "coverageAtLeast": (("coverage", "coverage"), _minimum, "COVERAGE_BELOW_MINIMUM"),
}


def _operand_value(expected, value, vocabulary, path):
    if expected in FACT_TYPES:
        validate_fact_value(expected, value, vocabulary, path)
    elif expected == "text":
        if not _text(value):
            raise WorkflowValidationError("COMPOSITION_PREDICATE_ARGUMENT_INVALID", path)
    elif not _number(value) or value < 0:
        raise WorkflowValidationError("COMPOSITION_PREDICATE_ARGUMENT_INVALID", path)


def validate_condition(condition, ports, parameters, vocabulary, path):
    if "predicate" in condition:
        name = condition["predicate"]
        if name not in PREDICATES:
            raise WorkflowValidationError("COMPOSITION_PREDICATE_UNSUPPORTED", path + "/predicate")
        signature, _, _ = PREDICATES[name]
        arguments = condition["arguments"]
        if len(arguments) != len(signature):
            raise WorkflowValidationError("COMPOSITION_PREDICATE_ARGUMENT_INVALID", path + "/arguments")
        for index, (expected, argument) in enumerate(zip(signature, arguments, strict=True)):
            location = path + pointer(["arguments", index])
            if "port" in argument and (
                argument["port"] not in ports[argument.get("direction", "input")] or argument["property"] != expected
            ):
                raise WorkflowValidationError("COMPOSITION_PREDICATE_ARGUMENT_INVALID", location)
            if "parameter" in argument and argument["parameter"] not in parameters:
                raise WorkflowValidationError("COMPOSITION_PARAMETER_UNKNOWN", location)
            if "value" in argument:
                _operand_value(expected, argument["value"], vocabulary, location)
    elif "when" in condition:
        for key in ("when", "then"):
            validate_condition(condition[key], ports, parameters, vocabulary, path + "/" + key)
    else:
        key = "all" if "all" in condition else "any"
        for index, child in enumerate(condition[key]):
            validate_condition(child, ports, parameters, vocabulary, path + pointer([key, index]))


@dataclass(frozen=True)
class Evaluation:
    state: str
    code: str = ""
    values: tuple = ()
    sources: tuple[dict, ...] = ()
    can_defer: bool = False


def evaluate(condition, ports, parameters, measured, vocabulary):
    if "when" in condition:
        guard = evaluate(condition["when"], ports, parameters, measured, vocabulary)
        if guard.state == "FAIL":
            return Evaluation("SKIPPED")
        if guard.state == "UNKNOWN":
            return guard
        return evaluate(condition["then"], ports, parameters, measured, vocabulary)
    if "predicate" not in condition:
        key = "all" if "all" in condition else "any"
        results = [evaluate(item, ports, parameters, measured, vocabulary) for item in condition[key]]
        decisive = "FAIL" if key == "all" else "PASS"
        if match := next((item for item in results if item.state == decisive), None):
            return match
        unknown = [item for item in results if item.state == "UNKNOWN"]
        if unknown:
            return Evaluation("UNKNOWN", unknown[0].code, can_defer=all(item.can_defer for item in unknown))
        applicable = [item for item in results if item.state != "SKIPPED"]
        return applicable[0] if applicable else Evaluation("SKIPPED")
    signature, compare, code = PREDICATES[condition["predicate"]]
    values = []
    sources = []
    missing = []
    for expected, argument in zip(signature, condition["arguments"], strict=True):
        if "port" in argument:
            port = (argument.get("direction", "input"), argument["port"])
            property_name = argument["property"]
            facts = ports.get(port, [])
            if not facts or any(property_name not in item for item in facts):
                key = (*port, property_name)
                missing.append(key in measured)
                continue
            items = [item[property_name] for item in facts]
        elif "parameter" in argument:
            if argument["parameter"] not in parameters:
                missing.append(False)
                continue
            items = [Fact(parameters[argument["parameter"]])]
        else:
            items = [Fact(argument["value"])]
        for item in items:
            _operand_value(expected, item.value, vocabulary, "")
            sources.extend(source for source in item.sources if source not in sources)
        values.append([item.value for item in items])
    if missing:
        return Evaluation("UNKNOWN", "COMPOSITION_FACT_REQUIRED", sources=tuple(sources), can_defer=all(missing))
    for operands in product(*values):
        if not compare(operands, vocabulary):
            return Evaluation("FAIL", code, operands, tuple(sources))
    return Evaluation("PASS", sources=tuple(sources))
