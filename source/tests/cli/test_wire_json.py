from scripts.lib.wire_json import from_wire_json, to_wire_json


def test_wire_mapper_preserves_open_arguments_and_named_input_keys() -> None:
    internal = {
        "request_id": "request",
        "arguments": {"versionId": "version", "source_field": "head_m"},
        "inputs": {
            "versionId": {"version_id": "version", "semantic_id": "semantic"}
        },
    }

    wire = to_wire_json(internal)

    assert wire == {
        "requestId": "request",
        "arguments": {"versionId": "version", "source_field": "head_m"},
        "inputs": {
            "versionId": {"versionId": "version", "semanticId": "semantic"}
        },
    }
    assert from_wire_json(wire) == internal


def test_wire_mapper_converts_owned_objects_inside_named_field_arrays() -> None:
    internal = {
        "workflow": {
            "inputs": [
                {
                    "name": "rules",
                    "media_types": ["application/json"],
                    "format_pairs": [
                        {"media_type": "application/json", "format": "json"}
                    ],
                }
            ]
        },
        "rows": [{"source_field": "head_m", "versionId": "opaque"}],
    }

    wire = to_wire_json(internal)

    assert wire == {
        "workflow": {
            "inputs": [
                {
                    "name": "rules",
                    "mediaTypes": ["application/json"],
                    "formatPairs": [
                        {"mediaType": "application/json", "format": "json"}
                    ],
                }
            ]
        },
        "rows": [{"source_field": "head_m", "versionId": "opaque"}],
    }
    assert from_wire_json(wire) == internal
