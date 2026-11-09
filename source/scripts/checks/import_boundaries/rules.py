from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Package:
    root: Path
    allowed_internal: frozenset[str]


PACKAGES = {
    "groundwater_api_client": Package(
        ROOT / "packages" / "groundwater-api-client" / "src",
        frozenset({"platform_protocol_contracts"}),
    ),
    "platform_lightweight_agent": Package(
        ROOT / "packages" / "lightweight-agent-core" / "src", frozenset()
    ),
    "platform_knowledge_next": Package(
        ROOT / "services" / "knowledge-next" / "src",
        frozenset(
            {
                "platform_data_client",
                "platform_embedding_client",
                "platform_protocol_contracts",
            }
        ),
    ),
    "platform_knowledge_client": Package(
        ROOT / "services" / "knowledge-next" / "client" / "src",
        frozenset({"platform_protocol_contracts"}),
    ),
    "platform_data_service": Package(
        ROOT / "services" / "data" / "src",
        frozenset({"platform_protocol_contracts"}),
    ),
    "platform_data_client": Package(
        ROOT / "services" / "data" / "client" / "src",
        frozenset({"platform_protocol_contracts"}),
    ),
    "platform_protocol_contracts": Package(
        ROOT / "packages" / "protocol-contracts" / "src", frozenset()
    ),
    "platform_kernel": Package(
        ROOT / "kernel" / "src",
        frozenset(
            {
                "platform_executor_client",
                "platform_protocol_contracts",
                "platform_workflow_compiler",
            }
        ),
    ),
    "platform_workflow_compiler": Package(
        ROOT / "packages" / "workflow-compiler" / "src", frozenset()
    ),
    "platform_executor_service": Package(
        ROOT / "services" / "executor" / "src",
        frozenset({"platform_data_client", "platform_protocol_contracts"}),
    ),
    "platform_executor_client": Package(
        ROOT / "services" / "executor" / "client" / "src",
        frozenset({"platform_protocol_contracts"}),
    ),
    "platform_embedding_client": Package(
        ROOT / "packages" / "embedding-client" / "src", frozenset()
    ),
    "moperators": Package(ROOT / "packages" / "operator-pack" / "src", frozenset()),
}
INTERNAL_PACKAGES = frozenset(PACKAGES)
CURRENT_SOURCE_DIRECTORIES = (
    ROOT / "packages" / "groundwater-api-client" / "src",
    ROOT / "packages" / "lightweight-agent-core" / "src",
    ROOT / "kernel" / "src",
    ROOT / "services" / "data" / "src",
    ROOT / "services" / "executor" / "src",
    ROOT / "services" / "executor" / "client" / "src",
    ROOT / "services" / "knowledge-next" / "src",
    ROOT / "packages" / "protocol-contracts" / "src",
    ROOT / "packages" / "workflow-compiler" / "src",
    ROOT / "packages" / "embedding-client" / "src",
    ROOT / "packages" / "operator-pack" / "src",
    ROOT / "platform" / "assembler",
)
WORKFLOW_COMPILER_FORBIDDEN_IMPORTS = frozenset(
    {
        "fastapi",
        "httpx",
        "moperators",
        "openai",
        "pathlib",
        "requests",
        "sqlite3",
        "sqlalchemy",
    }
)
KERNEL_MODULE_IMPORTS = {
    "": {"platform_kernel.api", "platform_kernel.composition"},
    "agent_runtime": {
        "platform_kernel.application",
        "platform_kernel.tasks",
        "platform_kernel.contracts",
        "platform_kernel.database",
        "platform_kernel.errors",
        "platform_kernel.settings",
        "platform_kernel.tool_progress",
        "platform_kernel.workflow_execution",
    },
    "api": {
        "platform_kernel.access",
        "platform_kernel.tasks",
        "platform_kernel.interaction",
        "platform_kernel.agent_runtime.api_projection",
        "platform_kernel.composition",
        "platform_kernel.errors",
        "platform_kernel.interaction.api_projection",
        "platform_kernel.workflow_execution.api_projection",
    },
    "composition": {
        "platform_kernel.application",
        "platform_kernel.agent_runtime",
        "platform_kernel.agent_runtime.availability",
        "platform_kernel.agent_runtime.observation_client",
        "platform_kernel.agent_runtime.persistence",
        "platform_kernel.agent_runtime.preview",
        "platform_kernel.agent_runtime.provider",
        "platform_kernel.agent_runtime.sdk_execution",
        "platform_kernel.change_signal",
        "platform_kernel.database",
        "platform_kernel.interaction",
        "platform_kernel.intent_routing",
        "platform_kernel.runtime",
        "platform_kernel.readiness",
        "platform_kernel.settings",
        "platform_kernel.workflow_execution",
        "platform_kernel.workflow_execution.execution_client",
    },
    "tasks": {
        "platform_kernel.contracts",
        "platform_kernel.database",
        "platform_kernel.errors",
    },
    "contracts": set(),
    "change_signal": set(),
    "database": {"platform_kernel.schema"},
    "errors": set(),
    "interaction": {
        "platform_kernel.application",
        "platform_kernel.tasks",
        "platform_kernel.agent_runtime",
        "platform_kernel.contracts",
        "platform_kernel.database",
        "platform_kernel.errors",
        "platform_kernel.workflow_execution",
    },
    "intent_routing": {
        "platform_kernel.agent_runtime",
        "platform_kernel.agent_runtime.availability",
        "platform_kernel.agent_runtime.context_policy",
        "platform_kernel.agent_runtime.context_usage",
        "platform_kernel.agent_runtime.memory",
        "platform_kernel.agent_runtime.provider",
        "platform_kernel.agent_runtime.sdk_execution.activity",
        "platform_kernel.errors",
        "platform_kernel.settings",
    },
    "main": {"platform_kernel.api"},
    "runtime": {
        "platform_kernel.tasks",
        "platform_kernel.agent_runtime",
        "platform_kernel.change_signal",
        "platform_kernel.errors",
        "platform_kernel.interaction",
        "platform_kernel.intent_routing",
        "platform_kernel.task_supervisor",
        "platform_kernel.workflow_execution",
    },
    "readiness": {
        "platform_kernel.agent_runtime.availability",
        "platform_kernel.settings",
    },
    "schema": set(),
    "settings": {
        "platform_kernel.settings_environment",
        "platform_kernel.settings_manifest",
        "platform_kernel.settings_types",
        "platform_kernel.settings_values",
    },
    "settings_environment": set(),
    "settings_manifest": {
        "platform_kernel.settings_types",
        "platform_kernel.settings_values",
        "platform_kernel.tool_progress",
    },
    "settings_types": set(),
    "settings_values": set(),
    "task_supervisor": set(),
    # Stateless validation and presentation policy shared by manifest parsing
    # and Agent execution. It owns no persistence or lifecycle state.
    "tool_progress": {
        "platform_kernel.errors",
        "platform_kernel.settings_types",
    },
    "workflow_execution": {
        "platform_kernel.application",
        "platform_kernel.contracts",
        "platform_kernel.database",
        "platform_kernel.errors",
        "platform_kernel.settings",
    },
    "application": {
        "platform_kernel.agent_runtime",
        "platform_kernel.contracts",
        "platform_kernel.database",
        "platform_kernel.errors",
        "platform_kernel.interaction",
        "platform_kernel.tasks",
        "platform_kernel.workflow_execution",
    },
    # HTTP identity parsing and route policy composition are deliberately a
    # small, read-only layer.  It can call the existing public ownership
    # guards, but does not become an owner of those resources.
    "access": {
        "platform_kernel.database",
        "platform_kernel.errors",
        "platform_kernel.interaction.access",
    },
}
KERNEL_FORBIDDEN_IMPORTS = {
    "platform_kernel.composition": frozenset(
        {
            "openai",
            "agents.models.openai_chatcompletions",
            "agents.models.openai_responses",
        }
    ),
    "platform_kernel.agent_runtime.owner": frozenset({"agents", "openai"}),
    "platform_kernel.agent_runtime.run_start": frozenset(
        {"agents", "openai", "httpx", "sqlite3"}
    ),
    "platform_kernel.agent_runtime.run_resume": frozenset(
        {"agents", "openai", "httpx", "sqlite3"}
    ),
    "platform_kernel.agent_runtime.run_finalization": frozenset(
        {"agents", "openai", "httpx", "sqlite3"}
    ),
    "platform_kernel.intent_routing.decision": frozenset(
        {
            "agents",
            "openai",
            "httpx",
            "sqlite3",
            "platform_kernel.database",
            "platform_kernel.agent_runtime.persistence",
        }
    ),
    "platform_kernel.workflow_execution.owner": frozenset({"httpx"}),
    "platform_kernel.agent_runtime.sdk_execution.execution": frozenset(
        {"sqlite3", "platform_kernel.database", "platform_kernel.workflow_execution"}
    ),
    "platform_kernel.agent_runtime.sdk_execution.execution_builders": frozenset(
        {"sqlite3", "platform_kernel.database", "platform_kernel.workflow_execution"}
    ),
    "platform_kernel.agent_runtime.sdk_execution.initial_execution": frozenset(
        {"sqlite3", "platform_kernel.database", "platform_kernel.workflow_execution"}
    ),
    "platform_kernel.agent_runtime.sdk_execution.resumed_execution": frozenset(
        {"sqlite3", "platform_kernel.database", "platform_kernel.workflow_execution"}
    ),
    "platform_kernel.agent_runtime.sdk_execution.segment_runner": frozenset(
        {"sqlite3", "platform_kernel.database", "platform_kernel.workflow_execution"}
    ),
    "platform_kernel.agent_runtime.sdk_execution.context": frozenset(
        {
            "sqlite3",
            "platform_kernel.database",
            "platform_kernel.agent_runtime.persistence",
            "platform_kernel.workflow_execution",
        }
    ),
    "platform_kernel.agent_runtime.sdk_execution.activity": frozenset(
        {
            "sqlite3",
            "platform_kernel.database",
            "platform_kernel.agent_runtime.persistence",
            "platform_kernel.workflow_execution",
        }
    ),
    "platform_kernel.agent_runtime.sdk_execution.activity_projection": frozenset(
        {
            "sqlite3",
            "platform_kernel.database",
            "platform_kernel.agent_runtime.persistence",
        }
    ),
    "platform_kernel.agent_runtime.sdk_execution.context_envelope": frozenset(
        {
            "sqlite3",
            "platform_kernel.database",
            "platform_kernel.agent_runtime.persistence",
        }
    ),
    "platform_kernel.agent_runtime.sdk_execution.usage": frozenset(
        {
            "sqlite3",
            "platform_kernel.database",
            "platform_kernel.agent_runtime.persistence",
        }
    ),
    "platform_kernel.agent_runtime.sdk_execution.usage_capture": frozenset(
        {
            "sqlite3",
            "platform_kernel.database",
            "platform_kernel.agent_runtime.persistence",
        }
    ),
    "platform_kernel.agent_runtime.sdk_execution.types": frozenset(
        {
            "sqlite3",
            "platform_kernel.database",
            "platform_kernel.agent_runtime.persistence",
            "platform_kernel.workflow_execution",
        }
    ),
    "platform_kernel.agent_runtime.reply": frozenset(
        {"sqlite3", "platform_kernel.database", "platform_kernel.workflow_execution"}
    ),
    "platform_kernel.agent_runtime.result_processing": frozenset(
        {
            "httpx",
            "sqlite3",
            "platform_kernel.agent_runtime.persistence",
            "platform_kernel.workflow_execution",
        }
    ),
    "platform_kernel.agent_runtime.memory": frozenset(
        {
            "agents",
            "openai",
            "sqlite3",
            "platform_kernel.database",
            "platform_kernel.agent_runtime.persistence",
        }
    ),
    "platform_kernel.agent_runtime.context_policy": frozenset(
        {
            "agents",
            "openai",
            "httpx",
            "sqlite3",
            "platform_kernel.database",
            "platform_kernel.agent_runtime.persistence",
        }
    ),
    "platform_kernel.agent_runtime.context_models": frozenset(
        {"agents", "openai", "httpx", "sqlite3", "platform_kernel.database"}
    ),
    "platform_kernel.agent_runtime.context_projection": frozenset(
        {"agents", "openai", "httpx", "sqlite3", "platform_kernel.database"}
    ),
    "platform_kernel.agent_runtime.context_values": frozenset(
        {"agents", "openai", "httpx", "sqlite3", "platform_kernel.database"}
    ),
    "platform_kernel.agent_runtime.mode": frozenset(
        {
            "agents",
            "openai",
            "httpx",
            "sqlite3",
            "platform_kernel.database",
            "platform_kernel.agent_runtime.persistence",
            "platform_kernel.workflow_execution",
        }
    ),
    "platform_kernel.agent_runtime.evidence": frozenset(
        {
            "agents",
            "openai",
            "httpx",
            "sqlite3",
            "platform_kernel.database",
            "platform_kernel.agent_runtime.persistence",
            "platform_kernel.workflow_execution",
        }
    ),
}
# The SDK adapter and final reply renderer use only the Agent persistence
# functions listed here. Raw SQL remains in the Agent persistence owner.
SDK_PERSISTENCE_IMPORTS = {
    "platform_kernel.agent_runtime.sdk_execution.initial_execution": frozenset(
        {"load_initial_context_sources"}
    ),
    "platform_kernel.agent_runtime.sdk_execution.resumed_execution": frozenset(
        {"load_request_context"}
    ),
    "platform_kernel.agent_runtime.reply": frozenset(
        {
            "allowed_workflow_run_ids",
            "get_run",
            "load_execution_context",
            "load_request_context",
        }
    ),
}
SQL_FREE_KERNEL_MODULES = frozenset(
    {
        "platform_kernel.interaction.owner",
        "platform_kernel.agent_runtime.owner",
        "platform_kernel.agent_runtime.run_start",
        "platform_kernel.agent_runtime.run_resume",
        "platform_kernel.agent_runtime.run_finalization",
        "platform_kernel.agent_runtime.memory",
        "platform_kernel.agent_runtime.context_policy",
        "platform_kernel.agent_runtime.context_models",
        "platform_kernel.agent_runtime.context_projection",
        "platform_kernel.agent_runtime.context_values",
        "platform_kernel.agent_runtime.mode",
        "platform_kernel.agent_runtime.evidence",
        "platform_kernel.agent_runtime.result_processing",
        "platform_kernel.agent_runtime.sdk_execution.execution",
        "platform_kernel.agent_runtime.sdk_execution.execution_builders",
        "platform_kernel.agent_runtime.sdk_execution.initial_execution",
        "platform_kernel.agent_runtime.sdk_execution.resumed_execution",
        "platform_kernel.agent_runtime.sdk_execution.segment_runner",
        "platform_kernel.agent_runtime.sdk_execution.context",
        "platform_kernel.agent_runtime.sdk_execution.activity",
        "platform_kernel.agent_runtime.sdk_execution.activity_projection",
        "platform_kernel.agent_runtime.sdk_execution.context_envelope",
        "platform_kernel.agent_runtime.sdk_execution.usage",
        "platform_kernel.agent_runtime.sdk_execution.usage_capture",
        "platform_kernel.agent_runtime.sdk_execution.types",
        "platform_kernel.agent_runtime.reply",
        "platform_kernel.agent_runtime.tooling",
        "platform_kernel.agent_runtime.tooling.factory",
        "platform_kernel.agent_runtime.tooling.catalog_tools",
        "platform_kernel.agent_runtime.tooling.task_tools",
        "platform_kernel.agent_runtime.tooling.reply_submission",
        "platform_kernel.agent_runtime.tooling.observations",
        "platform_kernel.agent_runtime.tooling.read_governance",
        "platform_kernel.agent_runtime.tooling.replies",
        "platform_kernel.agent_runtime.model_tool",
        "platform_kernel.workflow_execution.owner",
        "platform_kernel.intent_routing.decision",
    }
)
EXCLUSIVE_TABLE_MODULES = {
    "agent_actions": (
        "platform_kernel.agent_runtime.persistence.context",
        "platform_kernel.agent_runtime.persistence.projection",
        "platform_kernel.agent_runtime.persistence.run_lifecycle",
        "platform_kernel.agent_runtime.persistence.run_queries",
        "platform_kernel.agent_runtime.persistence.run_recovery",
    ),
    "agent_memory": "platform_kernel.agent_runtime.persistence.context",
}
TABLE_OWNERS = {
    "platform_kernel": {
        "schema_version": "platform_kernel.database",
        "research_users": "platform_kernel.access.research_auth",
        "research_project_memberships": "platform_kernel.access.research_auth",
        "research_sessions": "platform_kernel.access.research_auth",
        "research_api_tokens": "platform_kernel.access.research_auth",
        "tasks": (
            "platform_kernel.tasks",
            "platform_kernel.interaction.control_proposals",
        ),
        "task_revisions": "platform_kernel.tasks",
        "task_runs": "platform_kernel.tasks",
        "task_update_calls": "platform_kernel.tasks",
        "task_completions": "platform_kernel.tasks",
        "task_preparations": "platform_kernel.tasks",
        "task_preparation_revisions": "platform_kernel.tasks",
        "confirmation_tasks": (
            "platform_kernel.tasks",
            "platform_kernel.interaction.control_proposals",
        ),
        "run_controls": "platform_kernel.interaction",
        "message_corrections": "platform_kernel.interaction",
        "control_proposals": "platform_kernel.interaction",
        "sessions": "platform_kernel.interaction",
        "messages": (
            "platform_kernel.interaction",
            "platform_kernel.agent_runtime.persistence.context",
        ),
        "message_requests": (
            "platform_kernel.interaction",
            "platform_kernel.agent_runtime.persistence",
        ),
        "confirmations": (
            "platform_kernel.interaction",
            "platform_kernel.agent_runtime.persistence",
        ),
        "confirmation_actions": "platform_kernel.interaction",
        "agent_runs": (
            "platform_kernel.agent_runtime",
            "platform_kernel.application.route_commands",
            "platform_kernel.interaction.message_requests",
            "platform_kernel.interaction.control_proposals",
            "platform_kernel.interaction.workflow_drafts",
        ),
        "agent_requests": (
            "platform_kernel.interaction",
            "platform_kernel.agent_runtime.persistence",
        ),
        "workflow_drafts": (
            "platform_kernel.interaction",
            "platform_kernel.agent_runtime.persistence",
        ),
        "agent_plans": "platform_kernel.agent_runtime",
        "agent_actions": "platform_kernel.agent_runtime",
        "agent_run_state": "platform_kernel.agent_runtime",
        "agent_memory": "platform_kernel.agent_runtime",
        "workflow_runs": (
            "platform_kernel.workflow_execution",
            "platform_kernel.agent_runtime.persistence",
            "platform_kernel.interaction.control_proposals",
        ),
        "workflow_dispatches": "platform_kernel.workflow_execution",
        "workflow_service_bindings": "platform_kernel.workflow_execution",
        "session_events": "platform_kernel.interaction",
        "agent_traces": "platform_kernel.agent_runtime",
        "agent_spans": "platform_kernel.agent_runtime",
        "context_views": "platform_kernel.agent_runtime.persistence.context_views",
        "provider_requests": "platform_kernel.agent_runtime.persistence.provider_requests",
    },
    "platform_executor_service": {
        "schema_version": "platform_executor_service.database",
        "executor_jobs": (
            "platform_executor_service.control_components",
            "platform_executor_service.results",
            "platform_executor_service.result_finalization",
            "platform_executor_service.result_publication",
            "platform_executor_service.result_recovery",
            "platform_executor_service.task_execution",
        ),
        "job_nodes": (
            "platform_executor_service.control_components",
            "platform_executor_service.artifacts",
            "platform_executor_service.results",
            "platform_executor_service.result_recovery",
            "platform_executor_service.task_execution",
        ),
        "node_attempts": (
            "platform_executor_service.control_components",
            "platform_executor_service.task_execution",
        ),
        "worker_heartbeats": "platform_executor_service.control_components",
        "content_objects": (
            "platform_executor_service.artifacts",
            "platform_executor_service.results",
        ),
        "job_object_pins": (
            "platform_executor_service.control_components",
            "platform_executor_service.artifacts",
            "platform_executor_service.results",
            "platform_executor_service.result_finalization",
        ),
    },
}
CROSS_OWNER_TRANSACTION_COMMANDS = {
    "platform_kernel.application.route_commands": {
        "resolve_intent_route": frozenset(
            {
                "agent_requests",
                "workflow_drafts",
                "confirmations",
                "message_requests",
                "sessions",
                "session_events",
            }
        ),
        "fail_intent_route": frozenset(
            {"message_requests", "agent_runs", "sessions", "session_events"}
        ),
    },
}
SQL_TABLE_PATTERN = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE(?!\s+(?:OF|ON)\b)|TABLE(?:\s+IF\s+NOT\s+EXISTS)?)"
    r"\s+([a-z_][a-z0-9_]*)",
    re.IGNORECASE,
)
