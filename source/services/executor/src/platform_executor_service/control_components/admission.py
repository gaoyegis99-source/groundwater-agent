from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from jsonschema import Draft202012Validator, ValidationError
from platform_protocol_contracts import parse_traceparent, trace_context_for_id
from platform_workflow_compiler import WorkflowValidationError
from platform_workflow_compiler.composition.execution import ArtifactComposition, execution_arguments, raster_input_facts
from platform_workflow_compiler.graph import input_members

from ..contracts import JobRequestV6, JobStatusV6, canonical_json
from ..database import connect_database, utc_now
from ..errors import ExecutionServiceError
from .shared import ControlComponent


class AdmissionControl(ControlComponent):
    def __init__(self, context):
        super().__init__(context)
        profile = self.config.get("workflowComposition")
        self.composition = None if profile is None else ArtifactComposition.from_config(profile)

    def _composed_model(self, request: JobRequestV6) -> dict:
        if self.composition is None:
            raise ExecutionServiceError("COMPOSITION_EXECUTION_UNSUPPORTED")
        definition = dict(request.composition)
        try:
            model, _ = self.composition.compile(definition, request.project_id)
        except WorkflowValidationError as error:
            raise ExecutionServiceError(error.code) from error
        arguments, references = execution_arguments(definition)
        if arguments != request.arguments or references != {
            name: ref.as_dict() for name, ref in request.input_references.items()
        }:
            raise ExecutionServiceError("COMPOSITION_INPUT_MISMATCH")
        return model

    def submit(
        self, request: JobRequestV6, traceparent: str | None = None
    ) -> JobStatusV6:
        traceparent = traceparent or trace_context_for_id(request.request_id).traceparent
        if parse_traceparent(traceparent) is None:
            raise ExecutionServiceError("TRACE_CONTEXT_INVALID")
        request = replace(request, traceparent=traceparent)
        if request.project_id != self.project_id:
            raise ExecutionServiceError("PROJECT_MISMATCH")
        model = (
            self._composed_model(request) if request.composition is not None
            else self.models.get((request.model_id, request.model_version))
        )
        if model is None:
            raise ExecutionServiceError("MODEL_NOT_CONFIGURED")
        workflow = model["workflow"]
        if (
            request.model_id != model["modelId"]
            or request.model_version != model["version"]
            or request.model_checksum != model["modelChecksum"]
            or request.workflow_id != workflow["workflowId"]
            or request.workflow_version != workflow["version"]
            or request.workflow_checksum != workflow["definitionChecksum"]
            or request.executor_id != model["executorId"]
        ):
            raise ExecutionServiceError("STATIC_DEFINITION_MISMATCH")
        try:
            Draft202012Validator(model["tool"]["inputSchema"]).validate(request.arguments)
            if not model["defaultParameters"].keys() <= request.arguments.keys():
                raise ExecutionServiceError("MODEL_ARGUMENTS_NOT_CANONICAL")
            for node in workflow["nodes"]:
                parameters = {
                    name: request.arguments[model["parameterBindings"][binding["workflowParameter"]]]
                    for name, binding in node["parameters"].items()
                    if model["parameterBindings"][binding["workflowParameter"]] in request.arguments
                }
                Draft202012Validator(node["parameterSchema"]).validate(parameters)
        except ValidationError as cause:
            raise ExecutionServiceError("MODEL_ARGUMENTS_INVALID") from cause
        request_json = canonical_json(request.as_storage_dict()).decode("utf-8")
        with connect_database(self.database) as connection:
            existing = connection.execute(
                "SELECT * FROM executor_jobs WHERE request_id = ?",
                (request.request_id,),
            ).fetchone()
            if existing is not None:
                if not _same_request(existing["request_json"], request_json):
                    raise ExecutionServiceError("JOB_REQUEST_ID_CONFLICT")
                return self._status(existing)
        with TemporaryDirectory(
            dir=self.artifacts.object_root, prefix=".inputs-"
        ) as staging:
            input_objects = self._import_inputs(request, model, Path(staging))
            with self.artifacts.object_publication():
                for item in input_objects.values():
                    self.artifacts.publish_object(item)
                return self._create_job(request, model, request_json, input_objects)

    def _create_job(self, request, model, request_json, input_objects) -> JobStatusV6:
        workflow = model["workflow"]
        workflow_parameters = {
            name: request.arguments[argument]
            for name, argument in model["parameterBindings"].items()
            if argument in request.arguments
        }
        job_id = str(uuid4())
        now = utc_now()
        with connect_database(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM executor_jobs WHERE request_id=?", (request.request_id,)
            ).fetchone()
            if existing is not None:
                if not _same_request(existing["request_json"], request_json):
                    raise ExecutionServiceError("JOB_REQUEST_ID_CONFLICT")
                return self._status(existing)
            deadline = datetime.fromisoformat(
                request.deadline_at.replace("Z", "+00:00")
            ).astimezone(timezone.utc)
            if deadline <= datetime.now(timezone.utc):
                raise ExecutionServiceError("JOB_DEADLINE_EXCEEDED")
            connection.execute(
                """INSERT INTO executor_jobs(
                       job_id, request_id, request_json, model_id, model_version,
                       model_checksum, workflow_id, workflow_version,
                       workflow_checksum, max_parallel_nodes, status, stage, error, outputs_json,
                       deadline_at, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'QUEUED', 'QUEUED',
                             NULL, '[]', ?, ?)""",
                (
                    job_id,
                    request.request_id,
                    request_json,
                    request.model_id,
                    request.model_version,
                    request.model_checksum,
                    request.workflow_id,
                    request.workflow_version,
                    request.workflow_checksum,
                    model["maxParallelNodes"],
                    deadline.isoformat(timespec="microseconds").replace("+00:00", "Z"),
                    now,
                ),
            )
            for input_name, object_info in input_objects.items():
                self.artifacts.record_object(connection, object_info)
                connection.execute(
                    "INSERT INTO job_object_pins(job_id, checksum, purpose) VALUES (?, ?, ?)",
                    (
                        job_id,
                        object_info["checksum"],
                        f"workflow-input:{input_name}",
                    ),
                )
                connection.execute(
                    "INSERT INTO job_object_pins(job_id, checksum, purpose) VALUES (?, ?, ?)",
                    (
                        job_id,
                        object_info["checksum"],
                        self.artifacts.workflow_input_metadata_purpose(
                            input_name, object_info
                        ),
                    ),
                )
            node_ids = {item["nodeId"] for item in workflow["nodes"]}
            for node in workflow["nodes"]:
                dependencies = sorted(
                    {
                        binding["nodeOutput"]["nodeId"]
                        for source in node["inputs"].values() for binding in input_members(source)
                        if "nodeOutput" in binding
                    }
                )
                if not set(dependencies) <= node_ids:
                    raise ExecutionServiceError("DAG_INVALID")
                parameters = {
                    name: workflow_parameters[binding["workflowParameter"]]
                    for name, binding in node["parameters"].items()
                    if binding["workflowParameter"] in workflow_parameters
                }
                connection.execute(
                    """INSERT INTO job_nodes(
                           job_id, node_id, operator_id, operator_version,
                           implementation_revision, runtime, memory_limit_bytes,
                           remote_binding_json, remote_state_json,
                           timeout_seconds, status,
                           dependencies_json, input_bindings_json, parameters_json,
                           output_contracts_json, outputs_json, values_json
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?, ?, ?, ?, ?, '[]', '{}')""",
                    (
                        job_id,
                        node["nodeId"],
                        node["operatorId"],
                        node["operatorVersion"],
                        node["implementationRevision"],
                        node["runtime"],
                        node["memoryLimitBytes"],
                        json.dumps(
                            node.get("remoteBinding", {}),
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        node["timeoutSeconds"],
                        "READY" if not dependencies else "WAITING",
                        json.dumps(dependencies, separators=(",", ":")),
                        json.dumps(
                            node["inputs"], sort_keys=True, separators=(",", ":")
                        ),
                        json.dumps(
                            parameters,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        json.dumps(
                            node["outputContracts"],
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    ),
                )
            connection.execute(
                "UPDATE executor_jobs SET status = 'RUNNING', stage = 'DAG', started_at = ? WHERE job_id = ?",
                (now, job_id),
            )
            row = connection.execute(
                "SELECT * FROM executor_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return self._status(row)

    def _import_inputs(
        self, request: JobRequestV6, model: dict, staging: Path
    ) -> dict[str, dict]:
        imported: dict[str, dict] = {}
        contracts = {item["name"]: item for item in model["workflow"]["inputs"]}
        for input_name, argument_name in model["inputBindings"].items():
            reference = request.input_references.get(argument_name)
            if reference is None or request.arguments.get(argument_name) != reference.version_id:
                raise ExecutionServiceError("MODEL_INPUT_INVALID")
            metadata, source = self.data_client.read_exact(
                request.project_id, reference
            )
            try:
                contract = contracts[input_name]
                if request.composition is not None:
                    frozen = request.composition["inputs"][input_name]
                    if metadata.get("semantic_checksum") != frozen["dataRef"]["semanticChecksum"]:
                        raise ExecutionServiceError("COMPOSITION_SEMANTIC_REVISION_MISMATCH")
                    if frozen["port"]["logicalType"] == "raster" and frozen["facts"] != raster_input_facts(
                        metadata.get("raster_descriptor"), frozen["dataRef"],
                    ):
                        raise ExecutionServiceError("RASTER_INPUT_FACTS_MISMATCH")
                if {
                    "mediaType": metadata["media_type"], "format": metadata["format"]
                } not in contract["formatPairs"]:
                    raise ExecutionServiceError("MODEL_INPUT_FORMAT_INVALID")
                imported[input_name] = self.artifacts.stage_stream(
                    source,
                    expected_checksum="sha256:" + metadata["checksum"].removeprefix("sha256:"),
                    expected_size=metadata["size_bytes"],
                    media_type=metadata["media_type"],
                    format_id=metadata["format"],
                    directory=staging,
                )
                imported[input_name]["execution_metadata"] = metadata.get(
                    "execution_metadata", {}
                )
            finally:
                source.close()
        return imported


def _same_request(left: str, right: str) -> bool:
    left_value = json.loads(left)
    right_value = json.loads(right)
    left_value.pop("traceparent", None)
    right_value.pop("traceparent", None)
    return left_value == right_value
