from __future__ import annotations

import hashlib
import json

import pytest

from kernel.tests.catalog_testing import authoring_fixture

from platform_kernel.settings import (
    ExecutableModelDefinition,
    IntentRouterSettings,
    KernelSettings,
    ObservationToolDefinition,
    ServiceBinding,
)
from platform_kernel.composition import install_trace_processors
from platform_kernel.database import initialize_database


@pytest.fixture(autouse=True)
def isolated_trace_processors():
    """Never construct the SDK's networked default exporter in unit tests."""

    install_trace_processors([])
    yield
    install_trace_processors([])


def _checksum(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@pytest.fixture
def kernel_settings(tmp_path):
    services = tuple(
        ServiceBinding(name, f"http://{name}-service:8000")
        for name in ("data", "knowledge-next", "executor")
    )
    search_schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    }
    inspect_schema = {
        "type": "object",
        "properties": {"version_id": {"type": "string"}},
        "required": ["version_id"],
        "additionalProperties": False,
    }
    array_schema = {"type": "array", "items": {"type": "object"}}
    inspect_result_schema = {"type": "object"}
    knowledge_result_schema = {"type": "object"}

    def build(capabilities: str = "summary") -> KernelSettings:
        observation_tools = ()
        models = ()
        if capabilities == "summary":
            observation_tools = (
                ObservationToolDefinition(
                    "inspect_data_version",
                    "Inspect exact Data version metadata.",
                    "data",
                    "/internal/tools/v3/inspect_data_version",
                    inspect_schema,
                    inspect_result_schema,
                    _checksum(inspect_schema),
                    _checksum(inspect_result_schema),
                    10,
                ),
            )
            input_schema = {
                "type": "object",
                "properties": {
                    "resource_id": {"type": "string"},
                    "axis": {"type": "string"},
                },
                "required": ["resource_id"],
                "additionalProperties": False,
            }
            models = (
                ExecutableModelDefinition(
                    model_id="groundwater.summary",
                    model_version="1.1.0",
                    model_checksum="sha256:0000000000000000000000000000000000000000000000000000000000000000",
                    workflow_id="groundwater.summary",
                    workflow_version="1.0.0",
                    tool_name="run_summary",
                    tool_description="Run summary statistics.",
                    input_schema=input_schema,
                    default_parameters={"axis": "columns"},
                    execution_service="executor",
                    executor_id="workflow-dag",
                    deadline_seconds=60,
                    workflow_checksum="sha256:1111111111111111111111111111111111111111111111111111111111111111",
                    input_schema_checksum=_checksum(input_schema),
                    final_outputs=("summary",),
                    data_input_parameters=("resource_id",),
                ),
            )
        else:
            read_schema = {
                "type": "object",
                "properties": {"refs": {"type": "array"}},
                "required": ["refs"],
                "additionalProperties": False,
            }
            observation_tools = (
                ObservationToolDefinition(
                    "retrieve_evidence",
                    "Search product knowledge. On STALE, call retrieve_evidence again; never reuse an old checksum.",
                    "knowledge-next",
                    "/internal/tools/v4/retrieve_evidence",
                    search_schema,
                    array_schema,
                    _checksum(search_schema),
                    _checksum(array_schema),
                    10,
                ),
                ObservationToolDefinition(
                    "read_evidence",
                    "Read product knowledge.",
                    "knowledge-next",
                    "/internal/tools/v4/read_evidence",
                    read_schema,
                    knowledge_result_schema,
                    _checksum(read_schema),
                    _checksum(knowledge_result_schema),
                    10,
                ),
            )
        settings = KernelSettings(
            data_root=tmp_path / capabilities,
            product_id="groundwater",
            project_id="p",
            system_prompt="Test product prompt.",
            services=services,
            observation_tools=observation_tools,
            models=models,
            execution_cases=(),
            operator_catalog=authoring_fixture()[1],
            operator_catalog_digest=authoring_fixture()[0]["operatorCatalogDigest"],
            manifest_checksum="sha256:2222222222222222222222222222222222222222222222222222222222222222",
            intent_router=IntentRouterSettings(
                provider="deepseek",
                protocol="responses",
                base_url="http://intent-router.test/v1",
                model="test-router-model",
                context_window_tokens=8192,
                max_output_tokens=256,
                request_timeout_seconds=120,
                segment_timeout_seconds=300,
                api_key="test-router-key",
            ),
            agent_provider="deepseek",
            agent_protocol="responses",
            agent_base_url="http://agent.test/v1",
            agent_model="test-model",
            agent_context_window_tokens=131072,
            agent_max_output_tokens=4096,
            agent_request_timeout_seconds=120,
            agent_segment_timeout_seconds=300,
            agent_api_key="test-key",
        )
        settings.data_root.mkdir(parents=True, exist_ok=True)
        database = settings.data_root / "kernel.sqlite3"
        initialize_database(database)
        return settings

    return build
