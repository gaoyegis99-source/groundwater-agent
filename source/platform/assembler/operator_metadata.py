"""Build read-only operator use and validation projections at assembly."""

from datetime import date
import hashlib

from assembler_common import CHECKSUM_PATTERN, load_json, require_fields, resolve_inside


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
    error = "PRODUCT_OPERATOR_VALIDATION_INVALID"
    if not isinstance(reference, dict):
        raise ValueError(error)
    require_fields(reference, {"path", "checksum"}, error)
    if not isinstance(reference["path"], str):
        raise ValueError(error)
    path = resolve_inside(product_directory, reference["path"], error)
    if "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() != reference["checksum"]:
        raise ValueError(error)
    return path


def load_operator_validation(product_directory, reference):
    error = "PRODUCT_OPERATOR_VALIDATION_INVALID"
    document = load_json(_verified_file(product_directory, reference))
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
                or row["coverage"] not in {"BROWSER_SINGLE_NODE", "WORKER_SINGLE_OPERATOR", "REMOTE_PRESET_NODE"}):
            raise ValueError(error)
        if "scientificReview" in row:
            review = row["scientificReview"]
            if not isinstance(review, dict):
                raise ValueError(error)
            require_fields(review, {"benchmark", "domainReview"}, error)
            case_path = _verified_file(product_directory, review["benchmark"])
            review_path = _verified_file(product_directory, review["domainReview"])
            if case_path == review_path:
                raise ValueError(error)
        result[key] = {**row, "checkedOn": checked_on, "reportChecksum": document["reportChecksum"]}
    return result
