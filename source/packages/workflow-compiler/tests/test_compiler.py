from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from platform_workflow_compiler import (
    WorkflowValidationError,
    compile_draft,
    compile_workflow,
    get_operator,
    list_operators,
    validate_catalog,
)


ROOT = Path(__file__).resolve().parents[3]
DIGEST = "sha256:" + "c" * 64


@pytest.fixture(scope="module")
def catalog():
    root = ROOT / "packages/operator-pack/catalog"
    lock = json.loads((root / "implementation-lock.json").read_text())
    operators = []
    for path in sorted((root / "operators").glob("*/*/manifest.json")):
        operator = json.loads(path.read_text())
        operator["implementationRevision"] = lock["operators"][
            f"{operator['id']}@{operator['version']}"
        ]["implementationRevision"]
        operators.append(operator)
    return validate_catalog(operators, DIGEST)


def graph_for(catalog, operator_id="statistics.describe"):
    operator = catalog[(operator_id, "1.0.0")]
    parameters = list(operator["parametersSchema"]["properties"])
    return {
        "inputs": [{key: port[key] for key in ("name", "cardinality", "mediaTypes", "formats")}
                   for port in operator["inputs"]],
        "parameters": parameters,
        "nodes": [{
            "nodeId": "analysis", "operatorId": operator_id, "operatorVersion": "1.0.0",
            "inputs": {port["name"]: {"workflowInput": port["name"]} for port in operator["inputs"]},
            "parameters": {name: {"workflowParameter": name} for name in parameters},
        }],
        "outputs": [{"name": "result", "nodeOutput": {"nodeId": "analysis", "port": operator["outputs"][0]["name"]}}],
    }


def draft(catalog, graph=None, values=None, sources=None, bindings=None):
    return compile_draft(
        graph_for(catalog) if graph is None else graph, catalog, workflow_id="draft.example",
        operator_catalog_digest=DIGEST, parameter_values={} if values is None else values,
        parameter_sources=[] if sources is None else sources,
        data_bindings=[] if bindings is None else bindings,
    )


def source(name, kind="USER"):
    return {"name": name, "source": kind, "explanation": "Explicit observed fact."}


def binding(name="data"):
    return {"input_name": name, "data_ref": {
        "kind": "data_version", "service": "data-primary",
        "dataset_id": "dataset-example", "version_id": "version-example",
        "semantic_id": "semantic-example", "object_id": "object-example",
        "checksum": "sha256:" + "a" * 64,
        "media_type": "text/csv", "format": "csv", "size_bytes": 123,
    }}


def convert_render_graph():
    return {
        "inputs": [{
            "name": "data", "cardinality": "one",
            "mediaTypes": ["text/csv"], "formats": ["csv"],
        }],
        "parameters": ["outputFormat"],
        "nodes": [
            {
                "nodeId": "convert", "operatorId": "data.convert",
                "operatorVersion": "1.0.0",
                "inputs": {"data": {"workflowInput": "data"}},
                "parameters": {
                    "outputFormat": {"workflowParameter": "outputFormat"},
                },
            },
            {
                "nodeId": "render", "operatorId": "cartography.render-png",
                "operatorVersion": "1.0.0",
                "inputs": {
                    "data": {"nodeOutput": {"nodeId": "convert", "port": "data"}},
                },
                "parameters": {},
            },
        ],
        "outputs": [{
            "name": "image", "nodeOutput": {"nodeId": "render", "port": "image"},
        }],
    }


def test_all_static_workflows_keep_model_pinned_checksums(catalog):
    directory = ROOT / "domains/groundwater/5.0.0"
    workflows = sorted((directory / "workflows").glob("*.workflow.json"))
    assert len(workflows) == 6
    for path in workflows:
        raw = json.loads(path.read_text())
        model = json.loads((directory / "models" / path.name.replace(".workflow", ".model")).read_text())
        assert compile_workflow(raw, catalog)["definitionChecksum"] == model["workflowRef"]["checksum"]


def test_compilation_state_is_isolated_after_a_failed_graph(catalog):
    raw = {"schemaVersion": "workflow.v6", "workflowId": "test.isolation",
           "version": "1.0.0", **graph_for(catalog)}
    original_raw, original_catalog = deepcopy(raw), deepcopy(catalog)
    expected = compile_workflow(raw, catalog)
    invalid = deepcopy(raw)
    invalid["outputs"][0]["nodeOutput"]["port"] = "missing"
    with pytest.raises(WorkflowValidationError) as caught:
        compile_workflow(invalid, catalog)
    assert (caught.value.code, caught.value.path) == ("WORKFLOW_OUTPUT_INVALID", "/outputs/0")
    assert compile_workflow(raw, catalog) == expected
    assert raw == original_raw
    assert catalog == original_catalog


def test_parameter_format_selection_does_not_leak_to_next_compilation(catalog):
    from platform_workflow_compiler.graph import _compile_workflow

    raw = {"schemaVersion": "workflow.v6", "workflowId": "test.formats",
           "version": "1.0.0", **graph_for(catalog, "data.convert")}
    for output_format in ("geojson", "csv", "geojson"):
        result = _compile_workflow(raw, catalog, parameter_values={"outputFormat": output_format})
        pairs = result["nodes"][0]["outputContracts"][0]["formatPairs"]
        assert {pair["format"] for pair in pairs} == {output_format}
    unbound = compile_workflow(raw, catalog)
    assert len(unbound["nodes"][0]["outputContracts"][0]["formatPairs"]) > 1


def test_draft_parameter_format_case_allows_geojson_render(catalog):
    compiled = draft(
        catalog,
        convert_render_graph(),
        {"outputFormat": "geojson"},
        [source("outputFormat")],
        [binding()],
    )

    assert compiled["missing_inputs"] == []
    assert compiled["parameter_values"] == {"outputFormat": "geojson"}
    assert compiled["execution_allowed"] is False


@pytest.mark.parametrize("output_format", ["csv", "geopackage"])
def test_draft_parameter_format_case_rejects_non_renderable_output(
    catalog, output_format
):
    with pytest.raises(WorkflowValidationError, match="WORKFLOW_PORT_INCOMPATIBLE"):
        draft(
            catalog,
            convert_render_graph(),
            {"outputFormat": output_format},
            [source("outputFormat")],
            [binding()],
        )


def test_missing_selector_and_static_compile_remain_conservative(catalog):
    graph = convert_render_graph()
    with pytest.raises(WorkflowValidationError, match="WORKFLOW_PORT_INCOMPATIBLE"):
        draft(catalog, graph, bindings=[binding()])

    raw = {
        "schemaVersion": "workflow.v6",
        "workflowId": "draft.example",
        "version": "0.1.0",
        **graph,
    }
    with pytest.raises(WorkflowValidationError, match="WORKFLOW_PORT_INCOMPATIBLE"):
        compile_workflow(raw, catalog)


def test_directory_pages_all_106_and_marks_four_many_operators(catalog):
    items, cursor = [], None
    while True:
        page = list_operators(catalog, after=cursor, limit=20)
        items.extend(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(items) == len({
        (item["operator_id"], item["operator_version"]) for item in items
    }) == 106
    assert {item["operator_id"] for item in items if not item["composition_supported"]} == {
        "raster.algebra", "raster.cell_statistics", "raster.mosaic", "vector.merge",
    }
    assert all("parametersSchema" not in item for item in items)
    assert list_operators(catalog, query="no such operator")["items"] == []
    assert "table.sort" in {
        item["operator_id"]
        for item in list_operators(
            catalog, query="请基于规则文件完成稳定排序后再分析",
        )["items"]
    }
    assert "statistics.describe" in {
        item["operator_id"]
        for item in list_operators(
            catalog, query="计算各数值列的描述统计结果",
        )["items"]
    }
    assert all(item["domain"] == "groundwater" for item in list_operators(catalog, domain="groundwater")["items"])
    assert [
        item["operator_id"]
        for item in list_operators(
            catalog, capability="descriptive-statistics",
        )["items"]
    ] == ["statistics.describe"]
    assert get_operator(catalog, "statistics.describe", "1.0.0")["binding_rules"]["value_outputs_feed_nodes"] is False
    assert "examples" not in get_operator(catalog, "statistics.describe", "1.0.0")


def test_directory_cursor_uses_identity_order_for_prefix_ids(catalog):
    operator = catalog[("statistics.describe", "1.0.0")]
    entries = {(name, "1.0.0"): {**operator, "id": name} for name in ("a", "a.child", "b")}
    first = list_operators(entries, limit=1)
    second = list_operators(entries, after=first["next_cursor"], limit=1)
    assert second["items"][0]["operator_id"] == "a.child"


@pytest.mark.parametrize("kwargs,code", [
    ({"limit": True}, "OPERATOR_PAGE_LIMIT_INVALID"),
    ({"limit": 51}, "OPERATOR_PAGE_LIMIT_INVALID"),
    ({"after": "missing@1.0.0"}, "OPERATOR_CURSOR_INVALID"),
])
def test_directory_rejects_invalid_paging(catalog, kwargs, code):
    with pytest.raises(WorkflowValidationError, match=code):
        list_operators(catalog, **kwargs)


def test_catalog_rejects_changed_shape_duplicates_and_external_schema(catalog):
    operators = list(deepcopy(catalog).values())
    with pytest.raises(WorkflowValidationError, match="OPERATOR_CATALOG_SIZE_INVALID"):
        validate_catalog(operators[:-1], DIGEST)
    operators[1] = deepcopy(operators[0])
    with pytest.raises(WorkflowValidationError, match="OPERATOR_IDENTITY_DUPLICATE"):
        validate_catalog(operators, DIGEST)
    operators = list(deepcopy(catalog).values())
    operators[0]["parametersSchema"]["properties"]["external"] = {"$ref": "https://invalid.example/schema"}
    with pytest.raises(WorkflowValidationError, match="OPERATOR_SCHEMA_REFERENCE_UNSUPPORTED"):
        validate_catalog(operators, DIGEST)


@pytest.mark.parametrize(
    "defect,code",
    [
        ("unknown_parameter", "OPERATOR_PARAMETER_FORMAT_CASE_INVALID"),
        ("overlap", "OPERATOR_PARAMETER_FORMAT_CASE_INVALID"),
        ("missing_enum_value", "OPERATOR_PARAMETER_FORMAT_CASE_INVALID"),
        ("selector_without_string_enum", "OPERATOR_PARAMETER_FORMAT_CASE_INVALID"),
        ("wrong_pair", "OPERATOR_PARAMETER_FORMAT_CASE_INVALID"),
        ("mixed_discriminators", "OPERATOR_FORMAT_CASE_INVALID"),
        ("value_output", "OPERATOR_MANIFEST_INVALID"),
    ],
)
def test_catalog_rejects_invalid_parameter_format_cases(catalog, defect, code):
    operators = list(deepcopy(catalog).values())
    operator = next(item for item in operators if item["id"] == "data.convert")
    output = operator["outputs"][0]
    cases = output["parameterFormatCases"]
    if defect == "unknown_parameter":
        for case in cases:
            case["parameter"] = "unknown"
    elif defect == "overlap":
        cases[1]["values"].append(cases[0]["values"][0])
    elif defect == "missing_enum_value":
        cases.pop()
    elif defect == "selector_without_string_enum":
        operator["parametersSchema"]["properties"]["outputFormat"] = {
            "type": "number", "enum": [1, 2],
        }
    elif defect == "wrong_pair":
        cases[0].update(mediaType="image/png", format="png")
    elif defect == "mixed_discriminators":
        output["formatCases"] = [{
            "inputPort": "data", "inputFormats": ["csv"],
            "mediaType": "text/csv", "format": "csv",
        }]
    else:
        output["kind"] = "value"
        output["schema"] = {}

    with pytest.raises(WorkflowValidationError, match=code):
        validate_catalog(operators, DIGEST)


@pytest.mark.parametrize("mutation", [
    lambda g: g.update({"workflowId": "model-chosen"}),
    lambda g: g["nodes"][0].update({"runtime": "native"}),
    lambda g: g["nodes"][0].update({"operatorId": {"invalid": "type"}}),
    lambda g: g["inputs"][0].update({"formatPairs": [{"mediaType": "text/csv", "format": "csv"}]}),
    lambda g: g["nodes"][0]["inputs"]["data"].update({"url": "https://invalid.example/data"}),
    lambda g: g["nodes"][0]["parameters"].update({"axis": {"literal": "columns"}}),
])
def test_raw_graph_rejects_untrusted_nested_fields_and_types(catalog, mutation):
    graph = graph_for(catalog)
    mutation(graph)
    with pytest.raises(WorkflowValidationError, match="WORKFLOW_FIELDS_INVALID") as error:
        draft(catalog, graph)
    assert error.value.path.startswith("/graph")


@pytest.mark.parametrize("mutation,code,path", [
    (lambda g: g["nodes"][0].update(operatorId="unknown.operator"),
     "WORKFLOW_OPERATOR_UNKNOWN", "/graph/nodes/0/operatorId"),
    (lambda g: g["nodes"][0]["inputs"].update(data={"nodeOutput": {"nodeId": "analysis", "port": "unknown"}}),
     "WORKFLOW_PORT_INCOMPATIBLE", "/graph/nodes/0/inputs/data/nodeOutput"),
    (lambda g: g["nodes"][0]["inputs"].update(data={"workflowInput": "missing"}),
     "WORKFLOW_PORT_INCOMPATIBLE", "/graph/nodes/0/inputs/data/workflowInput"),
    (lambda g: g["nodes"][0]["parameters"].update(axis={"workflowParameter": "missing"}),
     "WORKFLOW_PARAMETER_BINDING_INVALID", "/graph/nodes/0/parameters/axis"),
    (lambda g: g["outputs"][0]["nodeOutput"].update(port="missing"),
     "WORKFLOW_OUTPUT_INVALID", "/graph/outputs/0"),
])
def test_draft_validation_pointer_resolves_against_submitted_graph(catalog, mutation, code, path):
    graph = graph_for(catalog)
    mutation(graph)
    with pytest.raises(WorkflowValidationError, match=code) as error:
        draft(catalog, graph)
    assert error.value.path == path
    value = {"graph": graph}
    for component in path.split("/")[1:]:
        value = value[int(component)] if isinstance(value, list) else value[component]


def test_draft_value_and_data_pointers_use_input_fields_and_array_indexes(catalog):
    with pytest.raises(WorkflowValidationError, match="DRAFT_PARAMETER_INVALID") as error:
        draft(catalog, values={"axis": "unknown"}, sources=[source("axis")])
    assert error.value.path == "/parameter_values/axis"
    reference = binding()
    reference["data_ref"].update(media_type="image/png", format="png")
    with pytest.raises(WorkflowValidationError, match="DRAFT_DATA_FORMAT_INCOMPATIBLE") as error:
        draft(catalog, bindings=[reference])
    assert error.value.path == "/data_bindings/0/data_ref"


def test_graph_rejects_unknown_operator_cycle_unreachable_and_value_input(catalog):
    graph = graph_for(catalog)
    graph["nodes"][0]["operatorId"] = "unknown.operator"
    with pytest.raises(WorkflowValidationError, match="WORKFLOW_OPERATOR_UNKNOWN"):
        draft(catalog, graph)
    graph = graph_for(catalog, "groundwater.head.calculate")
    graph["nodes"][0]["inputs"]["observations"] = {"nodeOutput": {"nodeId": "analysis", "port": "heads"}}
    with pytest.raises(WorkflowValidationError, match="WORKFLOW_CYCLE"):
        draft(catalog, graph)
    graph = graph_for(catalog)
    graph["nodes"].append({**deepcopy(graph["nodes"][0]), "nodeId": "orphan"})
    with pytest.raises(WorkflowValidationError, match="WORKFLOW_NODE_UNREACHABLE"):
        draft(catalog, graph)
    graph["nodes"][0]["inputs"]["data"] = {"nodeOutput": {"nodeId": "orphan", "port": "summary"}}
    with pytest.raises(WorkflowValidationError, match="WORKFLOW_PORT_INCOMPATIBLE"):
        draft(catalog, graph)


@pytest.mark.parametrize("operator_id", ["raster.algebra", "raster.cell_statistics", "raster.mosaic", "vector.merge"])
def test_many_inputs_remain_unsupported(catalog, operator_id):
    with pytest.raises(WorkflowValidationError, match="WORKFLOW_CARDINALITY_UNSUPPORTED"):
        draft(catalog, graph_for(catalog, operator_id))


def test_missing_inputs_are_derived_and_no_defaults_are_inserted(catalog):
    value = draft(catalog, graph_for(catalog, "groundwater.head.calculate"))
    missing = {(item["kind"], item["name"]) for item in value["missing_inputs"]}
    assert missing == {
        ("DATA", "observations"), ("PARAMETER", "referenceElevationField"),
        ("PARAMETER", "depthToWaterField"), ("PARAMETER", "referenceElevationUnit"),
        ("PARAMETER", "depthToWaterUnit"), ("PARAMETER", "outputField"),
    }
    assert value["parameter_values"] == {}
    assert value["execution_allowed"] is False
    assert value["operators"][0]["implementation_revision"] == catalog[("groundwater.head.calculate", "1.0.0")]["implementationRevision"]


def test_explicit_defaults_and_data_facts_freeze_without_aliasing(catalog):
    graph = graph_for(catalog)
    graph["parameters"].remove("column")
    del graph["nodes"][0]["parameters"]["column"]
    bindings = [binding()]
    values = {"axis": "columns"}
    compiled = draft(catalog, graph, values, [source("axis", "OPERATOR_DEFAULT")], bindings)
    assert compiled["missing_inputs"] == []
    assert compiled["execution_allowed"] is False
    assert compiled["workflow"]["workflowId"] == "draft.example"
    graph["nodes"][0]["operatorId"] = "changed"
    bindings[0]["data_ref"]["version_id"] = "changed"
    values["axis"] = "flatten"
    assert compiled["workflow"]["nodes"][0]["operatorId"] == "statistics.describe"
    assert compiled["data_bindings"][0]["data_ref"]["version_id"] == "version-example"
    assert compiled["parameter_values"] == {"axis": "columns"}


@pytest.mark.parametrize("values,sources,code", [
    ({"axis": "flatten"}, [source("axis", "OPERATOR_DEFAULT")], "DRAFT_OPERATOR_DEFAULT_INVALID"),
    ({"column": "x"}, [source("column", "OPERATOR_DEFAULT")], "DRAFT_OPERATOR_DEFAULT_INVALID"),
    ({"axis": "invalid"}, [source("axis")], "DRAFT_PARAMETER_INVALID"),
    ({"unknown": 1}, [source("unknown")], "DRAFT_PARAMETER_UNKNOWN"),
    ({"axis": "columns"}, [], "DRAFT_PARAMETER_SOURCES_INVALID"),
    ({}, [source("axis")], "DRAFT_PARAMETER_SOURCES_INVALID"),
    ({"axis": "columns"}, [source("axis"), source("axis")], "DRAFT_PARAMETER_SOURCES_INVALID"),
    ({"axis": float("nan")}, [source("axis")], "WORKFLOW_JSON_INVALID"),
    ({"axis": "\ud800"}, [source("axis")], "WORKFLOW_JSON_INVALID"),
])
def test_parameter_failures_are_explicit(catalog, values, sources, code):
    with pytest.raises(WorkflowValidationError, match=code):
        draft(catalog, values=values, sources=sources)


def test_nested_parameter_values_are_complete_when_supplied(catalog):
    catalog = deepcopy(catalog)
    schema = catalog[("statistics.describe", "1.0.0")]["parametersSchema"]
    schema["required"] = ["axis"]
    schema["properties"]["axis"] = {
        "type": "object", "properties": {"threshold": {"type": "number", "minimum": 0}},
        "required": ["threshold"], "additionalProperties": False,
    }
    assert any(item["name"] == "axis" for item in draft(catalog)["missing_inputs"])
    for value in ({}, {"threshold": -1}, {"threshold": 0, "extra": 1}, {"threshold": float("inf")}):
        with pytest.raises(WorkflowValidationError):
            draft(catalog, values={"axis": value}, sources=[source("axis")])
    assert draft(catalog, values={"axis": {"threshold": 0}}, sources=[source("axis")])["parameter_values"] == {"axis": {"threshold": 0}}


def test_shared_parameter_is_checked_against_every_operator(catalog):
    graph = graph_for(catalog)
    slope = graph_for(catalog, "raster.slope")
    graph["inputs"].extend(slope["inputs"])
    graph["parameters"].extend(slope["parameters"])
    node = slope["nodes"][0]
    node["nodeId"] = "slope"
    node["parameters"]["band"] = {"workflowParameter": "axis"}
    graph["parameters"].remove("band")
    graph["nodes"].append(node)
    graph["outputs"].append({
        "name": "slope", "nodeOutput": {"nodeId": "slope", "port": slope["outputs"][0]["nodeOutput"]["port"]},
    })
    # Valid for statistics.describe.axis; invalid for raster.slope.band.
    with pytest.raises(WorkflowValidationError, match="DRAFT_PARAMETER_INVALID"):
        draft(catalog, graph, {"axis": "columns"}, [source("axis")])


def test_optional_bound_parameter_is_still_a_draft_gap(catalog):
    compiled = draft(catalog, bindings=[binding()])
    assert {(item["kind"], item["name"]) for item in compiled["missing_inputs"]} == {
        ("PARAMETER", "axis"), ("PARAMETER", "column"),
    }


def test_unused_declarations_are_rejected_only_by_draft_boundary(catalog):
    graph = graph_for(catalog)
    graph["parameters"].append("unused")
    with pytest.raises(WorkflowValidationError, match="DRAFT_PARAMETER_UNBOUND"):
        draft(catalog, graph)
    graph = graph_for(catalog)
    graph["inputs"].append({**graph["inputs"][0], "name": "unused"})
    with pytest.raises(WorkflowValidationError, match="DRAFT_INPUT_UNBOUND"):
        draft(catalog, graph)


def test_rasterize_exclusive_burn_parameters_are_validated_as_a_node(catalog):
    graph = graph_for(catalog, "raster.rasterize")
    with pytest.raises(WorkflowValidationError, match="DRAFT_PARAMETER_INVALID"):
        draft(catalog, graph, {"burnField": "value", "burnValue": 1},
              [source("burnField"), source("burnValue")])
    for name in ("burnField", "burnValue"):
        graph["parameters"].remove(name)
        del graph["nodes"][0]["parameters"][name]
    with pytest.raises(WorkflowValidationError, match="DRAFT_PARAMETER_BINDING_REQUIRED"):
        draft(catalog, graph)


def test_threshold_conditional_gap_waits_for_discriminator_then_checks_binding(catalog):
    graph = graph_for(catalog, "timeseries.threshold_events")
    graph["parameters"].remove("thresholdValue")
    del graph["nodes"][0]["parameters"]["thresholdValue"]
    compiled = draft(catalog, graph)
    assert "thresholdSource" in {item["name"] for item in compiled["missing_inputs"]}
    with pytest.raises(WorkflowValidationError, match="DRAFT_PARAMETER_BINDING_REQUIRED"):
        draft(catalog, graph, {"thresholdSource": "scalar"}, [source("thresholdSource")])
    assert "thresholdField" in {
        item["name"] for item in draft(catalog, graph, {"thresholdSource": "field"},
                                      [source("thresholdSource")])["missing_inputs"]
    }


@pytest.mark.parametrize("mutation,code", [
    (lambda r: r.update({"input_name": "unknown"}), "DRAFT_DATA_BINDINGS_INVALID"),
    (lambda r: r["data_ref"].update({"version_id": True}), "DRAFT_DATA_BINDINGS_INVALID"),
    (lambda r: r["data_ref"].update({"version_id": 1.0}), "DRAFT_DATA_BINDINGS_INVALID"),
    (lambda r: r["data_ref"].update({"checksum": "wrong"}), "DRAFT_DATA_BINDINGS_INVALID"),
    (lambda r: r["data_ref"].update({"kind": "resource"}), "DRAFT_DATA_BINDINGS_INVALID"),
    (lambda r: r["data_ref"].update({"media_type": "image/png", "format": "png"}), "DRAFT_DATA_FORMAT_INCOMPATIBLE"),
])
def test_data_bindings_are_exact_and_format_checked(catalog, mutation, code):
    reference = binding()
    mutation(reference)
    with pytest.raises(WorkflowValidationError, match=code):
        draft(catalog, bindings=[reference])


def test_duplicate_data_input_bindings_rejected(catalog):
    with pytest.raises(WorkflowValidationError, match="DRAFT_DATA_BINDINGS_INVALID"):
        draft(catalog, bindings=[binding(), binding()])
