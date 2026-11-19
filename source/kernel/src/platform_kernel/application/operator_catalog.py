"""Project version-specific catalog facts for the API and Agent tools."""


def operator_metadata(operator: dict, settings) -> dict:
    key = f"{operator['id']}@{operator['version']}"
    evidence = settings.operator_validation.get(key)
    status = "NOT_RECORDED"
    if evidence is not None:
        status = (
            "PASSED" if evidence["implementationRevision"] == operator["implementationRevision"]
            else "STALE"
        )
    scientific_review = evidence.get("scientificReview") if status == "PASSED" else None
    return {
        "workflow_uses": settings.operator_uses.get(key, []),
        "validation": {
            "status": status,
            "coverage": evidence["coverage"] if evidence else None,
            "checked_on": evidence["checkedOn"] if evidence else None,
            "report_checksum": evidence["reportChecksum"] if evidence else None,
            "implementation_revision": evidence["implementationRevision"] if evidence else None,
        },
        "scientific_status": "REVIEW_EVIDENCE_ATTACHED" if scientific_review else "REQUIRES_CASE_AND_REVIEW",
        "scientific_review": scientific_review,
    }
