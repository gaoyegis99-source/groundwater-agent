import hashlib
import json

import pytest

from scripts.interoperability.cwl_export import (
    CwlExportError,
    export_cwl_package,
    write_cwl_package,
)


def definitions():
    workflow = {
        "schemaVersion": "workflow.v6",
        "workflowId": "portable.copy",
        "version": "1.0.0",
        "inputs": [{"name": "source", "cardinality": "one"}],
        "parameters": [],
        "nodes": [
            {
                "nodeId": "copy",
                "operatorId": "file.copy",
                "operatorVersion": "1.0.0",
                "inputs": {"source": {"workflowInput": "source"}},
                "parameters": {},
            }
        ],
        "outputs": [
            {
                "name": "copied",
                "nodeOutput": {"nodeId": "copy", "port": "copied"},
            }
        ],
    }
    model = {
        "workflowRef": {
            "workflowId": "portable.copy",
            "version": "1.0.0",
            "checksum": "sha256:" + "a" * 64,
        },
        "tool": {
            "inputSchema": {
                "type": "object",
                "properties": {"versionId": {"type": "string"}},
                "required": ["versionId"],
            }
        },
        "inputBindings": {"source": "versionId"},
        "parameterBindings": {},
        "defaultParameters": {},
    }
    registry = {
        "schemaVersion": "v6-cwl-portability.v1",
        "operators": [
            {
                "id": "file.copy",
                "version": "1.0.0",
                "kind": "command",
                "baseCommand": ["/bin/cp"],
                "commandChecksum": "sha256:" + "b" * 64,
                "arguments": [{"position": 2, "valueFrom": "copied.bin"}],
                "inputBindings": {"source": {"position": 1}},
                "parameterBindings": {},
                "outputGlobs": {"copied": {"glob": "copied.bin"}},
            }
        ],
    }
    return workflow, model, registry


def catalog(tmp_path):
    path = tmp_path / "catalog/operators/file.copy/1.0.0"
    path.mkdir(parents=True, exist_ok=True)
    (path / "manifest.json").write_text(
        json.dumps(
            {
                "id": "file.copy",
                "version": "1.0.0",
                "inputs": [
                    {
                        "name": "source",
                        "cardinality": "one",
                        "required": True,
                    }
                ],
                "outputs": [{"name": "copied", "kind": "artifact"}],
            }
        ),
        encoding="utf-8",
    )
    return tmp_path / "catalog"


def test_static_command_workflow_exports_as_content_identified_cwl_package(tmp_path):
    workflow, model, registry = definitions()
    files = export_cwl_package(workflow, model, registry, catalog(tmp_path))
    assert set(files) == {
        "workflow.cwl",
        "tools/copy.cwl",
        "export-manifest.json",
    }
    document = json.loads(files["workflow.cwl"])
    tool = json.loads(files["tools/copy.cwl"])
    assert document["cwlVersion"] == "v1.2"
    assert document["steps"]["copy"]["in"] == {"input__source": "source"}
    assert document["outputs"]["copied"]["outputSource"] == "copy/copied"
    assert tool["class"] == "CommandLineTool"
    assert tool["hints"]["v6:ImplementationIdentity"]["checksum"] == (
        "sha256:" + "b" * 64
    )
    manifest = json.loads(files["export-manifest.json"])
    for path, checksum in manifest["files"].items():
        assert checksum == "sha256:" + hashlib.sha256(files[path]).hexdigest()

    output = tmp_path / "export"
    write_cwl_package(files, output)
    assert (output / "workflow.cwl").read_bytes() == files["workflow.cwl"]
    with pytest.raises(CwlExportError, match="CWL_OUTPUT_DIRECTORY_NOT_EMPTY"):
        write_cwl_package(files, output)


def test_export_fails_closed_for_unregistered_python_or_unpinned_container(tmp_path):
    workflow, model, registry = definitions()
    registry["operators"] = []
    with pytest.raises(CwlExportError, match="CWL_OPERATOR_NOT_PORTABLE"):
        export_cwl_package(workflow, model, registry, catalog(tmp_path))

    _, _, registry = definitions()
    registry["operators"][0].update(
        {"kind": "container", "containerImage": "example/tool:latest"}
    )
    with pytest.raises(
        CwlExportError, match="CWL_CONTAINER_IMAGE_NOT_DIGEST_PINNED"
    ):
        export_cwl_package(workflow, model, registry, catalog(tmp_path))

    workflow["agent"] = {"prompt": "unsupported"}
    _, _, registry = definitions()
    with pytest.raises(CwlExportError, match="CWL_WORKFLOW_SEMANTICS_UNSUPPORTED"):
        export_cwl_package(workflow, model, registry, catalog(tmp_path))


def test_export_rejects_cycles_and_incomplete_port_mappings(tmp_path):
    workflow, model, registry = definitions()
    workflow["nodes"][0]["inputs"]["source"] = {
        "nodeOutput": {"nodeId": "copy", "port": "copied"}
    }
    with pytest.raises(CwlExportError, match="CWL_WORKFLOW_CYCLE"):
        export_cwl_package(workflow, model, registry, catalog(tmp_path))

    workflow, model, registry = definitions()
    registry["operators"][0]["outputGlobs"] = {}
    with pytest.raises(CwlExportError, match="CWL_OUTPUT_BINDINGS_INCOMPLETE"):
        export_cwl_package(workflow, model, registry, catalog(tmp_path))
