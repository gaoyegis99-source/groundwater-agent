"""Project version-specific catalog facts for the API and Agent tools."""


def operator_metadata(operator: dict, settings) -> dict:
    key = f"{operator['id']}@{operator['version']}"
    evidence = settings.operator_validation.get(key, {})
    revision = operator["implementationRevision"]
    technical_revision = evidence.get("implementationRevision")
    review_revision = evidence.get("scientificReviewImplementationRevision", technical_revision)
    status = "NOT_RECORDED"
    if technical_revision is not None:
        status = "PASSED" if technical_revision == revision else "STALE"
    scientific_review = evidence.get("scientificReview") if review_revision == revision else None
    return {
        "workflow_uses": settings.operator_uses.get(key, []),
        "validation": {
            "status": status,
            "coverage": evidence.get("coverage"),
            "checked_on": evidence.get("checkedOn"),
            "report_checksum": evidence.get("reportChecksum"),
            "implementation_revision": technical_revision,
        },
        "scientific_status": "REVIEW_EVIDENCE_ATTACHED" if scientific_review else "REQUIRES_CASE_AND_REVIEW",
        "scientific_review": scientific_review,
    }
