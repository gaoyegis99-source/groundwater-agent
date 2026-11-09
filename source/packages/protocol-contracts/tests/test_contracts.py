from __future__ import annotations

import pytest

from platform_protocol_contracts import (
    DataVersionRefV2,
    JobRequestV6,
    LocalIdentity,
    build_problem_details,
    enforce_problem_media_type,
    json_pointer,
    to_wire,
    validate_camel_json,
    validation_issues,
)


def reference() -> DataVersionRefV2:
    return DataVersionRefV2(
        kind="data_version", service="data-primary", dataset_id="dataset",
        version_id="version", semantic_id="semantic", object_id="object",
        media_type="text/csv", format_id="csv", size_bytes=1,
        checksum="sha256:" + "0" * 64,
    )


def test_execution_v6_uses_only_camel_case_wire_fields() -> None:
    payload = JobRequestV6(
        request_id="request", project_id="project", model_id="model",
        model_version="1", model_checksum="sha256:" + "1" * 64,
        workflow_id="workflow", workflow_version="1",
        workflow_checksum="sha256:" + "2" * 64, executor_id="executor",
        arguments={"operator_parameter": 1}, input_references={"input_name": reference()},
        deadline_at="2026-10-14T10:00:00Z",
    ).as_dict()
    assert "requestId" in payload and "request_id" not in payload
    assert payload["arguments"] == {"operator_parameter": 1}
    assert JobRequestV6.from_dict(payload).input_references["input_name"] == reference()
    with pytest.raises(ValueError, match="JOB_REQUEST_INVALID"):
        JobRequestV6.from_dict({**payload, "request_id": payload["requestId"]})


def test_json_pointer_is_an_rfc_6901_pointer() -> None:
    assert json_pointer(("body", "input/value", 0)) == "/input~1value/0"


def test_problem_openapi_has_one_media_type() -> None:
    document = {
        "paths": {"/example": {"get": {"responses": {"422": {"content": {
            "application/json": {"schema": {"$ref": "#/components/schemas/ProblemDetailsV11"}},
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetailsV11"}},
        }}}}}}
    }
    result = enforce_problem_media_type(document)
    content = result["paths"]["/example"]["get"]["responses"]["422"]["content"]
    assert set(content) == {"application/problem+json"}


@pytest.mark.parametrize(("status", "description", "expected"), [
    ("413", "Request Entity Too Large", "Content Too Large"),
    ("422", "Unprocessable Entity", "Unprocessable Content"),
    ("422", "Unprocessable Content", "Unprocessable Content"),
    ("422", "Custom validation failure", "Custom validation failure"),
])
def test_problem_descriptions_preserve_published_contract(status, description, expected):
    response = {
        "description": description,
        "content": {"application/problem+json": {"schema": {}}},
    }
    document = {"paths": {"/example": {"post": {"responses": {status: response}}}}}
    enforce_problem_media_type(document)
    assert response["description"] == expected


def test_problem_builder_normalizes_shared_fields_and_validation_issues() -> None:
    issues = validation_issues(
        [{"loc": ("body", "input/value"), "type": "missing", "msg": "Required"}]
    )
    problem = build_problem_details(
        "VALIDATION_ERROR",
        422,
        "The request is invalid.",
        request_id="request",
        trace_id="trace",
        errors=issues,
    ).model_dump(by_alias=True)
    assert problem["type"] == "urn:platform:problem:validation-error"
    assert problem["requestId"] == "request"
    assert problem["traceId"] == "trace"
    assert problem["errors"] == [
        {"pointer": "/input~1value", "message": "Required"}
    ]


def test_problem_builder_derives_type_namespace_from_product_identity() -> None:
    problem = build_problem_details(
        "NOT_FOUND", 404, "Missing", product_id="sample-product"
    )
    assert problem.type == "urn:sample-product:problem:not-found"
    with pytest.raises(ValueError, match="PROBLEM_PRODUCT_ID_INVALID"):
        build_problem_details("INVALID", 400, "Invalid", product_id="Bad Product")


def test_camel_json_validation_preserves_only_declared_open_maps() -> None:
    validate_camel_json(
        {
            "requestId": "request",
            "arguments": {"operator_parameter": 1},
            "fields": {"source_name": {"fieldId": "f0"}},
        },
        opaque_keys=frozenset({"arguments"}),
        named_map_keys=frozenset({"fields"}),
    )
    with pytest.raises(ValueError, match="WIRE_FIELD_CASE_INVALID:request_id"):
        validate_camel_json({"request_id": "request"})
    with pytest.raises(ValueError, match="WIRE_FIELD_CASE_INVALID:field_id"):
        validate_camel_json(
            {"fields": {"source_name": {"field_id": "f0"}}},
            named_map_keys=frozenset({"fields"}),
        )


def test_to_wire_converts_owned_values_and_preserves_declared_maps() -> None:
    opaque = {"provider_key": {"nested_key": 1}}
    source = {
        "request_id": "request",
        "items": ({"field_name": "name"},),
        "metadata": opaque,
        "fields": {"source_name": {"field_id": "f0"}},
    }

    mapped = to_wire(
        source,
        opaque_keys=frozenset({"metadata"}),
        named_map_keys=frozenset({"fields"}),
    )

    assert mapped == {
        "requestId": "request",
        "items": [{"fieldName": "name"}],
        "metadata": opaque,
        "fields": {"source_name": {"fieldId": "f0"}},
    }
    assert source["items"] == ({"field_name": "name"},)
    assert source["metadata"] is opaque


def test_local_identity_is_validated_once_and_normalizes_role_order() -> None:
    identity = LocalIdentity.from_environment(
        {
            "PLATFORM_LOCAL_IDENTITY_SUBJECT": "local:one",
            "PLATFORM_LOCAL_IDENTITY_DISPLAY_NAME": "Local One",
            "PLATFORM_LOCAL_IDENTITY_ROLES": "admin,researcher",
        }
    )

    assert identity.subject == "local:one"
    assert identity.display_name == "Local One"
    assert identity.roles == ("admin", "researcher")
    assert identity.roles_header == "admin,researcher"


@pytest.mark.parametrize(
    "values,code",
    [
        ({"PLATFORM_LOCAL_IDENTITY_SUBJECT": "not a subject"}, "SUBJECT"),
        ({"PLATFORM_LOCAL_IDENTITY_DISPLAY_NAME": "bad\nname"}, "DISPLAY_NAME"),
        ({"PLATFORM_LOCAL_IDENTITY_ROLES": "researcher,researcher"}, "ROLES"),
        ({"PLATFORM_LOCAL_IDENTITY_ROLES": "operator"}, "ROLES"),
    ],
)
def test_local_identity_rejects_untrusted_environment_values(values, code) -> None:
    with pytest.raises(ValueError, match=f"PLATFORM_LOCAL_IDENTITY_{code}_INVALID"):
        LocalIdentity.from_environment(values)
