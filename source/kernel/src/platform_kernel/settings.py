from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import tempfile

from platform_protocol_contracts import LocalIdentity
from platform_workflow_compiler import validate_catalog

from .settings_environment import read_api_key as _read_api_key
from .settings_manifest import (
    _parse_execution_cases,
    _parse_models,
    _parse_services,
    _parse_tools,
)
from .settings_types import (
    ExecutableModelDefinition,
    ExecutionCaseDefinition,
    ExecutionCaseInputRole,
    ObservationToolDefinition,
    ServiceBinding,
)
from .settings_values import (
    _checksum,
    _digest,
    _health_path,
    _optional_url,
    _positive_integer,
    _text,
)


_DEFAULT_LOCAL_IDENTITY = LocalIdentity()


def _identity_mode(value: str) -> str:
    if value != "local":
        raise ValueError("PLATFORM_IDENTITY_MODE_INVALID")
    return value


def _bounded_positive(value: str | None, name: str, maximum: int) -> int:
    parsed = _positive_integer(value, name)
    if parsed > maximum:
        raise ValueError(f"{name}_INVALID")
    return parsed


@dataclass(frozen=True)
class ModelEndpointSettings:
    """Validated endpoint settings shared by Agent and Intent Router.

    The deployment still exposes the established ``PLATFORM_AGENT_*`` and
    ``PLATFORM_INTENT_ROUTER_*`` inputs.  Keeping their parsing here prevents
    one endpoint silently accepting a shape that the other rejects.
    """

    provider: str
    base_url: str
    model: str
    context_window_tokens: int
    max_output_tokens: int
    request_timeout_seconds: int
    segment_timeout_seconds: int
    api_key: str = field(repr=False)
    protocol: str
    standby_base_url: str | None = None
    health_path: str = "/models"

    @classmethod
    def from_values(
        cls,
        *,
        prefix: str,
        provider: str | None,
        base_url: str | None,
        model: str | None,
        api_key: str | None,
        context_window_tokens: str | int | None,
        max_output_tokens: str | int | None,
        request_timeout_seconds: str | int | None,
        segment_timeout_seconds: str | int | None,
        protocol: str | None,
        standby_base_url: str | None = None,
        health_path: str = "/models",
        missing_protocol_error: str | None = None,
    ) -> "ModelEndpointSettings":
        if provider not in {"deepseek", "minimax"}:
            raise ValueError(f"{prefix}_PROVIDER_INVALID")
        if protocol is None or protocol == "":
            # The two legacy environment surfaces classified an omitted
            # protocol differently.  Keep that public behavior at their
            # adapters while centralizing all actual endpoint validation.
            raise ValueError(missing_protocol_error or f"{prefix}_PROTOCOL_INVALID")
        if protocol not in {"responses", "chat_completions"}:
            raise ValueError(f"{prefix}_PROTOCOL_INVALID")
        if provider == "minimax" and protocol != "chat_completions":
            raise ValueError(f"{prefix}_PROTOCOL_INVALID")
        for value, error in (
            (base_url, f"{prefix}_BASE_URL_REQUIRED"),
            (model, f"{prefix}_MODEL_REQUIRED"),
            (api_key, f"{prefix}_API_KEY_REQUIRED"),
        ):
            if not value:
                raise ValueError(error)
            _text(value, error.replace("_REQUIRED", "_INVALID"))
        context_window = _positive_integer(
            context_window_tokens, f"{prefix}_CONTEXT_WINDOW_TOKENS"
        )
        max_output = _positive_integer(
            max_output_tokens, f"{prefix}_MAX_OUTPUT_TOKENS"
        )
        if max_output >= context_window:
            raise ValueError(f"{prefix}_TOKEN_LIMITS_INVALID")
        return cls(
            provider=provider,
            protocol=protocol,
            base_url=base_url,
            model=model,
            api_key=api_key,
            context_window_tokens=context_window,
            max_output_tokens=max_output,
            request_timeout_seconds=_positive_integer(
                request_timeout_seconds, f"{prefix}_REQUEST_TIMEOUT_SECONDS"
            ),
            segment_timeout_seconds=_positive_integer(
                segment_timeout_seconds, f"{prefix}_SEGMENT_TIMEOUT_SECONDS"
            ),
            standby_base_url=_optional_url(standby_base_url, f"{prefix}_STANDBY_BASE_URL"),
            health_path=_health_path(health_path, f"{prefix}_HEALTH_PATH"),
        )


@dataclass(frozen=True)
class IntentRouterSettings(ModelEndpointSettings):
    """Named compatibility type for the separately configured router endpoint."""

    @classmethod
    def from_environment(cls) -> "IntentRouterSettings":
        return cls.from_values(
            provider=os.getenv("PLATFORM_INTENT_ROUTER_PROVIDER"),
            protocol=os.getenv("PLATFORM_INTENT_ROUTER_PROTOCOL"),
            base_url=os.getenv("PLATFORM_INTENT_ROUTER_BASE_URL"),
            model=os.getenv("PLATFORM_INTENT_ROUTER_MODEL"),
            api_key=_read_api_key("PLATFORM_INTENT_ROUTER_API_KEY_FILE"),
            context_window_tokens=os.getenv(
                "PLATFORM_INTENT_ROUTER_CONTEXT_WINDOW_TOKENS"
            ),
            max_output_tokens=os.getenv(
                "PLATFORM_INTENT_ROUTER_MAX_OUTPUT_TOKENS"
            ),
            request_timeout_seconds=os.getenv(
                "PLATFORM_INTENT_ROUTER_REQUEST_TIMEOUT_SECONDS"
            ),
            segment_timeout_seconds=os.getenv(
                "PLATFORM_INTENT_ROUTER_SEGMENT_TIMEOUT_SECONDS"
            ),
            standby_base_url=os.getenv("PLATFORM_INTENT_ROUTER_STANDBY_BASE_URL"),
            health_path=os.getenv("PLATFORM_INTENT_ROUTER_HEALTH_PATH", "/models"),
        )

    @classmethod
    def from_values(
        cls,
        *,
        provider: str | None,
        base_url: str | None,
        model: str | None,
        api_key: str | None,
        context_window_tokens: str | int | None,
        max_output_tokens: str | int | None,
        request_timeout_seconds: str | int | None,
        segment_timeout_seconds: str | int | None,
        protocol: str | None,
        standby_base_url: str | None = None,
        health_path: str = "/models",
    ) -> "IntentRouterSettings":
        return super().from_values(
            prefix="PLATFORM_INTENT_ROUTER",
            provider=provider,
            base_url=base_url,
            model=model,
            api_key=api_key,
            context_window_tokens=context_window_tokens,
            max_output_tokens=max_output_tokens,
            request_timeout_seconds=request_timeout_seconds,
            segment_timeout_seconds=segment_timeout_seconds,
            protocol=protocol,
            standby_base_url=standby_base_url,
            health_path=health_path,
            missing_protocol_error="PLATFORM_INTENT_ROUTER_PROTOCOL_REQUIRED",
        )


@dataclass(frozen=True)
class KernelSettings:
    data_root: Path
    product_id: str
    project_id: str
    system_prompt: str
    services: tuple[ServiceBinding, ...]
    observation_tools: tuple[ObservationToolDefinition, ...]
    models: tuple[ExecutableModelDefinition, ...]
    execution_cases: tuple[ExecutionCaseDefinition, ...]
    operator_catalog: dict[tuple[str, str], dict]
    operator_catalog_digest: str
    manifest_checksum: str
    intent_router: IntentRouterSettings
    agent_provider: str
    agent_base_url: str
    agent_model: str
    agent_context_window_tokens: int
    agent_max_output_tokens: int
    agent_request_timeout_seconds: int
    agent_segment_timeout_seconds: int
    agent_api_key: str = field(repr=False)
    agent_protocol: str
    agent_preview_enabled: bool = False
    agent_standby_base_url: str | None = None
    agent_health_path: str = "/models"
    readiness_timeout_seconds: int = 5
    readiness_success_cache_seconds: int = 15
    readiness_failure_cache_seconds: int = 5
    identity_mode: str = "local"
    local_identity_subject: str = _DEFAULT_LOCAL_IDENTITY.subject
    local_identity_display_name: str = _DEFAULT_LOCAL_IDENTITY.display_name
    local_identity_roles: tuple[str, ...] = _DEFAULT_LOCAL_IDENTITY.roles
    research_auth_required: bool = False
    agent_concurrency: int = 8
    agent_queue_capacity: int = 100
    workflow_dispatch_concurrency: int = 8
    agent_shutdown_grace_seconds: int = 30

    @property
    def services_by_name(self) -> dict[str, ServiceBinding]:
        return {item.name: item for item in self.services}

    @property
    def definition_set_digest(self) -> str:
        """Identity shared with Executor for the complete executable definition set."""
        value = {
            "operatorCatalogDigest": self.operator_catalog_digest,
            "models": sorted(
                (
                    {
                        "modelId": item.model_id,
                        "version": item.model_version,
                        "modelChecksum": item.model_checksum,
                        "workflowId": item.workflow_id,
                        "workflowVersion": item.workflow_version,
                        "workflowChecksum": item.workflow_checksum,
                        "executorId": item.executor_id,
                    }
                    for item in self.models
                ),
                key=lambda item: (item["modelId"], item["version"]),
            ),
        }
        return _checksum(value)

    @classmethod
    def from_environment(cls) -> KernelSettings:
        local_identity = LocalIdentity.from_environment()
        identity_mode = _identity_mode(os.getenv("PLATFORM_IDENTITY_MODE", "local"))
        config_path = os.getenv("KERNEL_CONFIG_PATH")
        if not config_path:
            raise ValueError("KERNEL_CONFIG_REQUIRED")
        agent_api_key = _read_api_key("PLATFORM_AGENT_API_KEY_FILE")
        preview = os.getenv("PLATFORM_AGENT_PREVIEW_ENABLED", "true")
        if preview not in {"true", "false"}:
            raise ValueError("PLATFORM_AGENT_PREVIEW_ENABLED_INVALID")
        if "PLATFORM_AGENT_CONTEXT_POLICY" in os.environ:
            raise ValueError("PLATFORM_AGENT_CONTEXT_POLICY_REMOVED")
        from dataclasses import replace
        configured = cls.from_config(
            Path(config_path),
            data_root=(
                Path(os.environ["KERNEL_DATA_ROOT"])
                if os.getenv("KERNEL_DATA_ROOT")
                else Path(tempfile.mkdtemp(prefix="platform-kernel-"))
            ),
            agent_provider=os.getenv("PLATFORM_AGENT_PROVIDER"),
            agent_protocol=os.getenv("PLATFORM_AGENT_PROTOCOL"),
            agent_base_url=os.getenv("PLATFORM_AGENT_BASE_URL"),
            agent_model=os.getenv("PLATFORM_AGENT_MODEL"),
            agent_api_key=agent_api_key,
            agent_context_window_tokens=os.getenv(
                "PLATFORM_AGENT_CONTEXT_WINDOW_TOKENS"
            ),
            agent_max_output_tokens=os.getenv("PLATFORM_AGENT_MAX_OUTPUT_TOKENS"),
            agent_request_timeout_seconds=os.getenv(
                "PLATFORM_AGENT_REQUEST_TIMEOUT_SECONDS"
            ),
            agent_segment_timeout_seconds=os.getenv(
                "PLATFORM_AGENT_SEGMENT_TIMEOUT_SECONDS"
            ),
            intent_router=IntentRouterSettings.from_environment(),
            project_id=os.getenv("PLATFORM_PROJECT_ID"),
        )
        return replace(
            configured,
            agent_preview_enabled=preview == "true",
            agent_standby_base_url=_optional_url(
                os.getenv("PLATFORM_AGENT_STANDBY_BASE_URL"),
                "PLATFORM_AGENT_STANDBY_BASE_URL",
            ),
            agent_health_path=_health_path(
                os.getenv("PLATFORM_AGENT_HEALTH_PATH", "/models"),
                "PLATFORM_AGENT_HEALTH_PATH",
            ),
            readiness_timeout_seconds=_positive_integer(
                os.getenv("PLATFORM_READINESS_TIMEOUT_SECONDS", "5"),
                "PLATFORM_READINESS_TIMEOUT_SECONDS",
            ),
            readiness_success_cache_seconds=_positive_integer(
                os.getenv("PLATFORM_READINESS_SUCCESS_CACHE_SECONDS", "15"),
                "PLATFORM_READINESS_SUCCESS_CACHE_SECONDS",
            ),
            readiness_failure_cache_seconds=_positive_integer(
                os.getenv("PLATFORM_READINESS_FAILURE_CACHE_SECONDS", "5"),
                "PLATFORM_READINESS_FAILURE_CACHE_SECONDS",
            ),
            identity_mode=identity_mode,
            research_auth_required=(
                os.getenv("PLATFORM_RESEARCH_AUTH_REQUIRED", "true") == "true"
            ),
            agent_concurrency=_bounded_positive(
                os.getenv("PLATFORM_AGENT_CONCURRENCY", "8"),
                "PLATFORM_AGENT_CONCURRENCY",
                64,
            ),
            agent_queue_capacity=_bounded_positive(
                os.getenv("PLATFORM_AGENT_QUEUE_CAPACITY", "100"),
                "PLATFORM_AGENT_QUEUE_CAPACITY",
                10000,
            ),
            workflow_dispatch_concurrency=_bounded_positive(
                os.getenv("PLATFORM_WORKFLOW_DISPATCH_CONCURRENCY", "8"),
                "PLATFORM_WORKFLOW_DISPATCH_CONCURRENCY",
                64,
            ),
            agent_shutdown_grace_seconds=_bounded_positive(
                os.getenv("PLATFORM_AGENT_SHUTDOWN_GRACE_SECONDS", "30"),
                "PLATFORM_AGENT_SHUTDOWN_GRACE_SECONDS",
                300,
            ),
            local_identity_subject=local_identity.subject,
            local_identity_display_name=local_identity.display_name,
            local_identity_roles=local_identity.roles,
        )

    @classmethod
    def from_config(
        cls,
        path: Path,
        *,
        data_root: Path,
        agent_provider: str | None,
        agent_base_url: str | None,
        agent_model: str | None,
        agent_api_key: str | None,
        agent_context_window_tokens: str | int | None,
        agent_max_output_tokens: str | int | None,
        agent_request_timeout_seconds: str | int | None,
        agent_segment_timeout_seconds: str | int | None,
        intent_router: IntentRouterSettings,
        project_id: str | None,
        agent_protocol: str | None = None,
    ) -> KernelSettings:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("KERNEL_CONFIG_INVALID") from error
        required = {
            "schemaVersion",
            "productId",
            "publicApiVersion",
            "systemPrompt",
            "services",
            "observationTools",
            "models",
            "manifestChecksum",
            "workflowAuthoring",
            "executionCases",
        }
        if (
            not isinstance(raw, dict)
            or set(raw) != required
            or raw["schemaVersion"] != "kernel-config.v8"
            or raw["publicApiVersion"] != "v12"
        ):
            raise ValueError("KERNEL_CONFIG_INVALID")
        _text(raw["productId"], "KERNEL_CONFIG_INVALID")
        _text(raw["systemPrompt"], "KERNEL_CONFIG_INVALID")
        _digest(raw["manifestChecksum"])
        if not project_id:
            raise ValueError("PLATFORM_PROJECT_ID_REQUIRED")
        _text(project_id, "PLATFORM_PROJECT_ID_INVALID")
        services = _parse_services(raw["services"])
        tools = _parse_tools(raw["observationTools"], services)
        models = _parse_models(raw["models"], services)
        execution_cases = _parse_execution_cases(raw["executionCases"], models)
        authoring = raw["workflowAuthoring"]
        if not isinstance(authoring, dict) or set(authoring) != {
            "operatorCatalogDigest", "operators", "enabledOperators"
        }:
            raise ValueError("KERNEL_CONFIG_INVALID")
        _digest(authoring["operatorCatalogDigest"])
        complete_catalog = validate_catalog(
            authoring["operators"], authoring["operatorCatalogDigest"]
        )
        enabled = authoring["enabledOperators"]
        identities = {
            f"{operator_id}@{version}": (operator_id, version)
            for operator_id, version in complete_catalog
        }
        if (
            not isinstance(enabled, list)
            or not enabled
            or len(enabled) != len(set(enabled))
            or any(
                not isinstance(identity, str) or identity not in identities
                for identity in enabled
            )
        ):
            raise ValueError("KERNEL_CONFIG_INVALID")
        catalog = {
            identities[identity]: complete_catalog[identities[identity]]
            for identity in enabled
        }
        agent_endpoint = ModelEndpointSettings.from_values(
            prefix="PLATFORM_AGENT",
            provider=agent_provider,
            protocol=agent_protocol,
            base_url=agent_base_url,
            model=agent_model,
            api_key=agent_api_key,
            context_window_tokens=agent_context_window_tokens,
            max_output_tokens=agent_max_output_tokens,
            request_timeout_seconds=agent_request_timeout_seconds,
            segment_timeout_seconds=agent_segment_timeout_seconds,
        )
        return cls(
            data_root=data_root,
            product_id=raw["productId"],
            project_id=project_id,
            system_prompt=raw["systemPrompt"],
            services=services,
            observation_tools=tools,
            models=models,
            execution_cases=execution_cases,
            operator_catalog=catalog,
            operator_catalog_digest=authoring["operatorCatalogDigest"],
            manifest_checksum=raw["manifestChecksum"],
            intent_router=intent_router,
            agent_provider=agent_endpoint.provider,
            agent_protocol=agent_endpoint.protocol,
            agent_base_url=agent_endpoint.base_url,
            agent_model=agent_endpoint.model,
            agent_context_window_tokens=agent_endpoint.context_window_tokens,
            agent_max_output_tokens=agent_endpoint.max_output_tokens,
            agent_request_timeout_seconds=agent_endpoint.request_timeout_seconds,
            agent_segment_timeout_seconds=agent_endpoint.segment_timeout_seconds,
            agent_api_key=agent_endpoint.api_key,
        )


__all__ = [
    "ExecutableModelDefinition",
    "ExecutionCaseDefinition",
    "ExecutionCaseInputRole",
    "IntentRouterSettings",
    "KernelSettings",
    "ModelEndpointSettings",
    "ObservationToolDefinition",
    "ServiceBinding",
]
