"""Build read-only operator use and validation projections at assembly."""

from datetime import date
from functools import cache
import hashlib
import json
from pathlib import Path
import sys

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from assembler_common import CHECKSUM_PATTERN, _checksum, load_json, require_fields, resolve_inside


VALIDATION_ERROR = "PRODUCT_OPERATOR_VALIDATION_INVALID"
VALIDATION_SCHEMA = "operator-validation.v2.schema.json"


def operator_uses(workflows, models):
    models_by_workflow = {}
    for model in models:
        ref = model["workflowRef"]
        models_by_workflow.setdefault((ref["workflowId"], ref["version"]), []).append({
            "modelId": model["modelId"], "version": model["version"],
        })
    uses = {}
    for workflow in workflows:
        identity = workflow["workflowId"], workflow["version"]
        reference = {
            "workflowId": identity[0], "version": identity[1],
            "models": sorted(models_by_workflow.get(identity, []), key=lambda item: (item["modelId"], item["version"])),
        }
        for node in workflow["nodes"]:
            key = f"{node['operatorId']}@{node['operatorVersion']}"
            entries = uses.setdefault(key, [])
            if reference not in entries:
                entries.append(reference)
    return uses


def _verified_file(product_directory, reference):
    if not isinstance(reference, dict):
        raise ValueError(VALIDATION_ERROR)
    require_fields(reference, {"path", "checksum"}, VALIDATION_ERROR)
    if not isinstance(reference["path"], str):
        raise ValueError(VALIDATION_ERROR)
    try:
        path = resolve_inside(product_directory, reference["path"], VALIDATION_ERROR)
        data = path.read_bytes()
    except (OSError, ValueError) as cause:
        raise ValueError(VALIDATION_ERROR) from cause
    if "sha256:" + hashlib.sha256(data).hexdigest() != reference["checksum"]:
        raise ValueError(VALIDATION_ERROR)
    return path, data


def _verified_json(product_directory, reference):
    _, data = _verified_file(product_directory, reference)
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as cause:
        raise ValueError(VALIDATION_ERROR) from cause
    if not isinstance(document, dict):
        raise ValueError(VALIDATION_ERROR)
    return document


def _verified_review(product_directory, review):
    case_path, _ = _verified_file(product_directory, review["benchmark"])
    review_path, _ = _verified_file(product_directory, review["domainReview"])
    if case_path == review_path:
        raise ValueError(VALIDATION_ERROR)


def _legacy_validation(product_directory, document):
    error = VALIDATION_ERROR
    require_fields(document, {"schemaVersion", "checkedOn", "reportChecksum", "operators"}, error)
    if (document["schemaVersion"] != "operator-validation.v1"
            or not CHECKSUM_PATTERN.fullmatch(str(document["reportChecksum"]))
            or not isinstance(document["operators"], dict)):
        raise ValueError(error)
    try:
        checked_on = date.fromisoformat(document["checkedOn"]).isoformat()
    except (TypeError, ValueError) as cause:
        raise ValueError(error) from cause
    result = {}
    for key, row in document["operators"].items():
        if not isinstance(row, dict):
            raise ValueError(error)
        require_fields(row, {"implementationRevision", "coverage"} | ({"scientificReview"} & row.keys()), error)
        if (not CHECKSUM_PATTERN.fullmatch(str(row["implementationRevision"]))
                or row["coverage"] not in ("BROWSER_SINGLE_NODE", "WORKER_SINGLE_OPERATOR", "REMOTE_PRESET_NODE")):
            raise ValueError(error)
        if "scientificReview" in row:
            review = row["scientificReview"]
            if not isinstance(review, dict):
                raise ValueError(error)
            require_fields(review, {"benchmark", "domainReview"}, error)
            _verified_review(product_directory, review)
        result[key] = {**row, "checkedOn": checked_on, "reportChecksum": document["reportChecksum"]}
    return result


@cache
def _validation_v2_validator():
    paths = (
        Path(__file__).with_name(VALIDATION_SCHEMA),
        Path(sys.prefix) / "share/platform-assembler" / VALIDATION_SCHEMA,
    )
    path = next((path for path in paths if path.is_file()), None)
    if path is None:
        raise ValueError("PRODUCT_OPERATOR_VALIDATION_SCHEMA_MISSING")
    schema = load_json(path, VALIDATION_ERROR)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _verified_technical_checks(product_directory, key, technical, operator, manifest_digest):
    identity = {name: value for name, value in technical.items() if name != "checks"}
    if (
        identity["runtime"] != operator["runtime"]
        or (
            identity["implementationRevision"] == operator["implementationRevision"]
            and identity["manifestDigest"] != manifest_digest
        )
    ):
        raise ValueError(VALIDATION_ERROR)
    for layer, check in technical["checks"].items():
        if check["status"] == "NOT_ASSESSED":
            continue
        receipt = _verified_json(product_directory, check["artifact"])
        candidates = receipt.get("technicalChecks")
        proof = candidates.get(key) if isinstance(candidates, dict) else None
        if (
            receipt.get("schemaVersion") != "operator-evidence-receipt.v1"
            or not isinstance(proof, dict)
            or any(proof.get(name) != value for name, value in identity.items())
            or not isinstance(proof.get("results"), dict)
            or proof["results"].get(layer) != {k: v for k, v in check.items() if k != "artifact"}
        ):
            raise ValueError(VALIDATION_ERROR)


def _validation_v2(product_directory, document, operators):
    if operators is None:
        raise ValueError(VALIDATION_ERROR)
    try:
        _validation_v2_validator().validate(document)
    except ValidationError as cause:
        raise ValueError(VALIDATION_ERROR) from cause

    result = {}
    if reference := document.get("legacyTechnicalEvidence"):
        legacy = _verified_json(product_directory, reference)
        result = _legacy_validation(product_directory, legacy)
        for row in result.values():
            row.pop("scientificReview", None)

    known = {
        f"{operator_id}@{version}": operator
        for (operator_id, version), operator in operators.items()
    }
    if not (result.keys() | document["operators"].keys()) <= known.keys():
        raise ValueError(VALIDATION_ERROR)
    for key, row in document["operators"].items():
        operator = known[key]
        manifest_digest = _checksum({
            name: value for name, value in operator.items() if name != "implementationRevision"
        })
        if technical := row.get("technicalVerification"):
            _verified_technical_checks(product_directory, key, technical, operator, manifest_digest)
        review = row["scientificReview"]
        if review["status"] != "REVIEWED":
            continue
        _verified_review(product_directory, review)
        if review["benchmark"]["checksum"] == review["domainReview"]["checksum"]:
            raise ValueError(VALIDATION_ERROR)
        if manifest_digest != review["manifestDigest"]:
            continue
        result.setdefault(key, {}).update(
            scientificReviewImplementationRevision=review["implementationRevision"],
            scientificReview={"benchmark": review["benchmark"], "domainReview": review["domainReview"]},
        )
    return result


def load_operator_validation(product_directory, reference, *, operators=None):
    """Validate product evidence and return the existing catalog read projection."""
    document = _verified_json(product_directory, reference)
    if document.get("schemaVersion") == "operator-validation.v2":
        return _validation_v2(product_directory, document, operators)
    return _legacy_validation(product_directory, document)
