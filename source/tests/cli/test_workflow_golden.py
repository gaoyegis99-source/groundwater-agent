from __future__ import annotations

from scripts.development.workflow_golden import golden_differences, topological_node_ids


def test_golden_diff_pinpoints_changed_numeric_value() -> None:
    expected = {
        "outputs": {"summary": {"kind": "value", "sha256": "sha256:old"}},
        "keyValues": {
            "summary": {
                "meanSeepageVelocityMetersPerDay": 1.25,
                "validCellCount": 16,
            }
        },
    }
    actual = {
        "outputs": {"summary": {"kind": "value", "sha256": "sha256:old"}},
        "keyValues": {
            "summary": {
                "meanSeepageVelocityMetersPerDay": 1.5,
                "validCellCount": 16,
            }
        },
    }

    assert golden_differences(expected, actual) == [
        {
            "actual": 1.5,
            "expected": 1.25,
            "kind": "VALUE_CHANGED",
            "path": "/keyValues/summary/meanSeepageVelocityMetersPerDay",
        }
    ]


def test_golden_diff_reports_missing_fields_with_escaped_pointer() -> None:
    assert golden_differences({"a/b": {"x~y": 1}}, {"a/b": {}}) == [
        {
            "expected": 1,
            "kind": "FIELD_MISSING",
            "path": "/a~1b/x~0y",
        }
    ]


def test_workflow_nodes_are_ordered_by_dependencies_not_source_order() -> None:
    nodes = [
        {
            "nodeId": "downstream",
            "inputs": {
                "source": {"nodeOutput": {"nodeId": "upstream", "port": "result"}}
            },
        },
        {
            "nodeId": "independent",
            "inputs": {"source": {"workflowInput": "observations"}},
        },
        {
            "nodeId": "upstream",
            "inputs": {"source": {"workflowInput": "observations"}},
        },
    ]

    assert topological_node_ids(nodes) == ("independent", "upstream", "downstream")
