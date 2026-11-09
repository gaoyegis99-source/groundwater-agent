from __future__ import annotations

import json
import hashlib

import pytest

from kernel.tests.catalog_testing import authoring_fixture

from platform_kernel.settings import (
    IntentRouterSettings,
    KernelSettings,
    ModelEndpointSettings,
)


def _config(tmp_path):
    path = tmp_path / "kernel-config.json"
    path.write_text(
        json.dumps(
            {
                "schemaVersion": "kernel-config.v8",
                "workflowAuthoring": authoring_fixture()[0],
                "productId": "test",
                "publicApiVersion": "v12",
                "systemPrompt": "test prompt",
                "services": {},
                "observationTools": [],
                "models": [],
                "executionCases": [],
                "manifestChecksum": "sha256:" + "a" * 64,
            }
        ),
        encoding="utf-8",
    )
    return path


def _environment(monkeypatch, tmp_path) -> None:
    agent_key = tmp_path / "agent-key"
    agent_key.write_text("test-key\n")
    router_key = tmp_path / "intent-router-key"
    router_key.write_text("test-router-key\n")
    values = {
        "KERNEL_CONFIG_PATH": str(_config(tmp_path)),
        "KERNEL_DATA_ROOT": str(tmp_path / "data"),
        "PLATFORM_PROJECT_ID": "p",
        "PLATFORM_AGENT_PROVIDER": "deepseek",
        "PLATFORM_AGENT_PROTOCOL": "responses",
        "PLATFORM_AGENT_BASE_URL": "http://agent.test/v1",
        "PLATFORM_AGENT_MODEL": "test-model",
        "PLATFORM_AGENT_API_KEY_FILE": str(agent_key),
        "PLATFORM_AGENT_CONTEXT_WINDOW_TOKENS": "131072",
        "PLATFORM_AGENT_MAX_OUTPUT_TOKENS": "4096",
        "PLATFORM_AGENT_REQUEST_TIMEOUT_SECONDS": "120",
        "PLATFORM_AGENT_SEGMENT_TIMEOUT_SECONDS": "300",
        "PLATFORM_INTENT_ROUTER_PROVIDER": "deepseek",
        "PLATFORM_INTENT_ROUTER_PROTOCOL": "responses",
        "PLATFORM_INTENT_ROUTER_BASE_URL": "http://intent-router.test/v1",
        "PLATFORM_INTENT_ROUTER_MODEL": "test-router-model",
        "PLATFORM_INTENT_ROUTER_API_KEY_FILE": str(router_key),
        "PLATFORM_INTENT_ROUTER_CONTEXT_WINDOW_TOKENS": "8192",
        "PLATFORM_INTENT_ROUTER_MAX_OUTPUT_TOKENS": "256",
        "PLATFORM_INTENT_ROUTER_REQUEST_TIMEOUT_SECONDS": "30",
        "PLATFORM_INTENT_ROUTER_SEGMENT_TIMEOUT_SECONDS": "60",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def _intent_router() -> IntentRouterSettings:
    return IntentRouterSettings.from_values(
        provider="deepseek",
        protocol="responses",
        base_url="http://intent-router.test/v1",
        model="test-router-model",
        api_key="test-router-key",
        context_window_tokens=8192,
        max_output_tokens=256,
        request_timeout_seconds=30,
        segment_timeout_seconds=60,
    )


def test_agent_concurrency_has_validated_deployment_default(monkeypatch, tmp_path) -> None:
    _environment(monkeypatch, tmp_path)
    assert KernelSettings.from_environment().agent_concurrency == 8
    monkeypatch.setenv("PLATFORM_AGENT_CONCURRENCY", "12")
    assert KernelSettings.from_environment().agent_concurrency == 12


def test_workflow_dispatch_concurrency_has_a_bounded_default(monkeypatch, tmp_path) -> None:
    _environment(monkeypatch, tmp_path)
    assert KernelSettings.from_environment().workflow_dispatch_concurrency == 8
    monkeypatch.setenv("PLATFORM_WORKFLOW_DISPATCH_CONCURRENCY", "3")
    assert KernelSettings.from_environment().workflow_dispatch_concurrency == 3


@pytest.mark.parametrize("value", ["0", "65", "invalid"])
def test_workflow_dispatch_concurrency_rejects_invalid_values(
    monkeypatch, tmp_path, value
) -> None:
    _environment(monkeypatch, tmp_path)
    monkeypatch.setenv("PLATFORM_WORKFLOW_DISPATCH_CONCURRENCY", value)
    with pytest.raises(
        ValueError, match="PLATFORM_WORKFLOW_DISPATCH_CONCURRENCY_INVALID"
    ):
        KernelSettings.from_environment()


def test_local_identity_environment_is_shared_with_kernel_settings(monkeypatch, tmp_path) -> None:
    _environment(monkeypatch, tmp_path)
    monkeypatch.setenv("PLATFORM_LOCAL_IDENTITY_SUBJECT", "local:kernel")
    monkeypatch.setenv("PLATFORM_LOCAL_IDENTITY_DISPLAY_NAME", "Kernel User")
    monkeypatch.setenv(
        "PLATFORM_LOCAL_IDENTITY_ROLES", "researcher,admin"
    )

    settings = KernelSettings.from_environment()

    assert settings.local_identity_subject == "local:kernel"
    assert settings.local_identity_display_name == "Kernel User"
    assert settings.local_identity_roles == ("admin", "researcher")


def test_agent_and_intent_endpoints_share_validation_rules() -> None:
    kwargs = {
        "provider": "deepseek",
        "protocol": "responses",
        "base_url": "http://model.test/v1",
        "model": "model",
        "api_key": "secret",
        "context_window_tokens": 8192,
        "max_output_tokens": 256,
        "request_timeout_seconds": 30,
        "segment_timeout_seconds": 60,
    }
    agent = ModelEndpointSettings.from_values(prefix="PLATFORM_AGENT", **kwargs)
    router = IntentRouterSettings.from_values(**kwargs)
    assert agent.context_window_tokens == router.context_window_tokens
    assert agent.max_output_tokens == router.max_output_tokens

    kwargs["max_output_tokens"] = 8192
    with pytest.raises(ValueError, match="PLATFORM_AGENT_TOKEN_LIMITS_INVALID"):
        ModelEndpointSettings.from_values(prefix="PLATFORM_AGENT", **kwargs)
    with pytest.raises(
        ValueError, match="PLATFORM_INTENT_ROUTER_TOKEN_LIMITS_INVALID"
    ):
        IntentRouterSettings.from_values(**kwargs)


def test_agent_shutdown_grace_has_a_bounded_deployment_default(monkeypatch, tmp_path) -> None:
    _environment(monkeypatch, tmp_path)
    assert KernelSettings.from_environment().agent_shutdown_grace_seconds == 30
    monkeypatch.setenv("PLATFORM_AGENT_SHUTDOWN_GRACE_SECONDS", "1")
    assert KernelSettings.from_environment().agent_shutdown_grace_seconds == 1


@pytest.mark.parametrize("value", ["0", "301", "invalid"])
def test_agent_shutdown_grace_rejects_invalid_values(monkeypatch, tmp_path, value) -> None:
    _environment(monkeypatch, tmp_path)
    monkeypatch.setenv("PLATFORM_AGENT_SHUTDOWN_GRACE_SECONDS", value)
    with pytest.raises(ValueError, match="PLATFORM_AGENT_SHUTDOWN_GRACE_SECONDS_INVALID"):
        KernelSettings.from_environment()


@pytest.mark.parametrize("value", ["0", "65", "invalid"])
def test_agent_concurrency_rejects_invalid_values(monkeypatch, tmp_path, value) -> None:
    _environment(monkeypatch, tmp_path)
    monkeypatch.setenv("PLATFORM_AGENT_CONCURRENCY", value)
    with pytest.raises(ValueError, match="PLATFORM_AGENT_CONCURRENCY_INVALID"):
        KernelSettings.from_environment()


@pytest.mark.parametrize(
    "name",
    [
        "PLATFORM_AGENT_CONTEXT_WINDOW_TOKENS",
        "PLATFORM_AGENT_MAX_OUTPUT_TOKENS",
    ],
)
def test_agent_token_limits_are_required_deployment_settings(
    monkeypatch, tmp_path, name
) -> None:
    _environment(monkeypatch, tmp_path)
    settings = KernelSettings.from_environment()
    assert settings.agent_context_window_tokens == 131072
    assert settings.agent_max_output_tokens == 4096

    monkeypatch.delenv(name)
    with pytest.raises(ValueError, match=f"{name}_REQUIRED"):
        KernelSettings.from_environment()


@pytest.mark.parametrize("value", ["not-an-integer", "0", "-1"])
def test_agent_token_limits_reject_invalid_positive_integers(
    monkeypatch, tmp_path, value
) -> None:
    _environment(monkeypatch, tmp_path)
    monkeypatch.setenv("PLATFORM_AGENT_MAX_OUTPUT_TOKENS", value)
    with pytest.raises(ValueError, match="PLATFORM_AGENT_MAX_OUTPUT_TOKENS_INVALID"):
        KernelSettings.from_environment()


@pytest.mark.parametrize("maximum", ["8192", "9000"])
def test_agent_output_limit_must_be_below_context_window(
    monkeypatch, tmp_path, maximum
) -> None:
    _environment(monkeypatch, tmp_path)
    monkeypatch.setenv("PLATFORM_AGENT_CONTEXT_WINDOW_TOKENS", "8192")
    monkeypatch.setenv("PLATFORM_AGENT_MAX_OUTPUT_TOKENS", maximum)
    with pytest.raises(ValueError, match="PLATFORM_AGENT_TOKEN_LIMITS_INVALID"):
        KernelSettings.from_environment()


@pytest.mark.parametrize(
    "name",
    ["PLATFORM_AGENT_REQUEST_TIMEOUT_SECONDS", "PLATFORM_AGENT_SEGMENT_TIMEOUT_SECONDS"],
)
@pytest.mark.parametrize("value", [None, "", "1.5", "not-an-integer", "0", "-1"])
def test_agent_timeouts_require_positive_integers(monkeypatch, tmp_path, name, value):
    _environment(monkeypatch, tmp_path)
    if value is None:
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, value)
    expected = "REQUIRED" if value in (None, "") else "INVALID"
    with pytest.raises(ValueError, match=f"{name}_{expected}"):
        KernelSettings.from_environment()


def test_agent_request_and_segment_limits_are_independent(monkeypatch, tmp_path):
    _environment(monkeypatch, tmp_path)
    monkeypatch.setenv("PLATFORM_AGENT_REQUEST_TIMEOUT_SECONDS", "400")
    settings = KernelSettings.from_environment()
    assert settings.agent_request_timeout_seconds == 400
    assert settings.agent_segment_timeout_seconds == 300


def test_context_policy_environment_switch_is_removed(monkeypatch, tmp_path):
    _environment(monkeypatch, tmp_path)
    monkeypatch.delenv("PLATFORM_AGENT_CONTEXT_POLICY", raising=False)
    KernelSettings.from_environment()
    monkeypatch.setenv("PLATFORM_AGENT_CONTEXT_POLICY", "v1")
    with pytest.raises(ValueError, match="PLATFORM_AGENT_CONTEXT_POLICY_REMOVED"):
        KernelSettings.from_environment()


def test_agent_protocol_is_explicit_and_minimax_is_chat_completions(monkeypatch, tmp_path):
    _environment(monkeypatch, tmp_path)
    monkeypatch.delenv("PLATFORM_AGENT_PROTOCOL")
    with pytest.raises(ValueError, match="PLATFORM_AGENT_PROTOCOL_INVALID"):
        KernelSettings.from_environment()
    monkeypatch.setenv("PLATFORM_AGENT_PROVIDER", "minimax")
    monkeypatch.setenv("PLATFORM_AGENT_PROTOCOL", "responses")
    with pytest.raises(ValueError, match="PLATFORM_AGENT_PROTOCOL_INVALID"):
        KernelSettings.from_environment()
    monkeypatch.setenv("PLATFORM_AGENT_PROTOCOL", "chat_completions")
    assert KernelSettings.from_environment().agent_protocol == "chat_completions"


def test_intent_router_uses_independent_provider_and_limits(monkeypatch, tmp_path):
    _environment(monkeypatch, tmp_path)
    settings = KernelSettings.from_environment().intent_router
    assert (
        settings.provider,
        settings.protocol,
        settings.base_url,
        settings.model,
        settings.api_key,
        settings.context_window_tokens,
        settings.max_output_tokens,
        settings.request_timeout_seconds,
        settings.segment_timeout_seconds,
    ) == (
        "deepseek",
        "responses",
        "http://intent-router.test/v1",
        "test-router-model",
        "test-router-key",
        8192,
        256,
        30,
        60,
    )


@pytest.mark.parametrize(
    "name",
    [
        "PLATFORM_INTENT_ROUTER_PROTOCOL",
        "PLATFORM_INTENT_ROUTER_BASE_URL",
        "PLATFORM_INTENT_ROUTER_MODEL",
        "PLATFORM_INTENT_ROUTER_API_KEY_FILE",
        "PLATFORM_INTENT_ROUTER_CONTEXT_WINDOW_TOKENS",
        "PLATFORM_INTENT_ROUTER_MAX_OUTPUT_TOKENS",
        "PLATFORM_INTENT_ROUTER_REQUEST_TIMEOUT_SECONDS",
        "PLATFORM_INTENT_ROUTER_SEGMENT_TIMEOUT_SECONDS",
    ],
)
def test_intent_router_settings_are_required(monkeypatch, tmp_path, name):
    _environment(monkeypatch, tmp_path)
    monkeypatch.delenv(name)
    with pytest.raises(ValueError, match=f"{name}_REQUIRED"):
        KernelSettings.from_environment()


@pytest.mark.parametrize(
    ("name", "value", "error"),
    [
        ("PLATFORM_INTENT_ROUTER_PROVIDER", "other", "PROVIDER_INVALID"),
        ("PLATFORM_INTENT_ROUTER_PROTOCOL", "other", "PROTOCOL_INVALID"),
        ("PLATFORM_INTENT_ROUTER_CONTEXT_WINDOW_TOKENS", "0", "CONTEXT_WINDOW_TOKENS_INVALID"),
        ("PLATFORM_INTENT_ROUTER_MAX_OUTPUT_TOKENS", "0", "MAX_OUTPUT_TOKENS_INVALID"),
        ("PLATFORM_INTENT_ROUTER_REQUEST_TIMEOUT_SECONDS", "0", "REQUEST_TIMEOUT_SECONDS_INVALID"),
        ("PLATFORM_INTENT_ROUTER_SEGMENT_TIMEOUT_SECONDS", "0", "SEGMENT_TIMEOUT_SECONDS_INVALID"),
    ],
)
def test_intent_router_rejects_invalid_configuration(
    monkeypatch, tmp_path, name, value, error,
):
    _environment(monkeypatch, tmp_path)
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=f"PLATFORM_INTENT_ROUTER_{error}"):
        KernelSettings.from_environment()


def test_intent_router_output_limit_must_be_below_window(monkeypatch, tmp_path):
    _environment(monkeypatch, tmp_path)
    monkeypatch.setenv("PLATFORM_INTENT_ROUTER_CONTEXT_WINDOW_TOKENS", "256")
    with pytest.raises(ValueError, match="PLATFORM_INTENT_ROUTER_TOKEN_LIMITS_INVALID"):
        KernelSettings.from_environment()


@pytest.mark.parametrize("field", ["agent_request_timeout_seconds", "agent_segment_timeout_seconds"])
@pytest.mark.parametrize("value", [True, False])
def test_agent_timeouts_reject_booleans_at_config_boundary(tmp_path, field, value):
    timeouts = {"agent_request_timeout_seconds": 120, "agent_segment_timeout_seconds": 300}
    timeouts[field] = value
    with pytest.raises(ValueError, match=f"PLATFORM_{field.upper()}_INVALID"):
        KernelSettings.from_config(
            _config(tmp_path),
            data_root=tmp_path / "data",
            agent_provider="deepseek",
            agent_protocol="responses",
            agent_base_url="http://agent.test/v1",
            agent_model="test-model",
            agent_api_key="test-key",
            project_id="p",
            agent_context_window_tokens=131072,
            agent_max_output_tokens=4096,
            intent_router=_intent_router(),
            **timeouts,
        )


def _model_outputs_config(monkeypatch, tmp_path, outputs, *, missing=False):
    _environment(monkeypatch, tmp_path)
    path = tmp_path / "kernel-config.json"
    config = json.loads(path.read_text())
    model = {
        "modelId": "groundwater.summary",
        "version": "1.2.0",
        "modelChecksum": "sha256:" + "b" * 64,
        "workflowRef": {
            "workflowId": "groundwater.summary", "version": "1.0.0",
            "checksum": "sha256:" + "c" * 64,
        },
        "tool": {
            "name": "run_summary", "description": "Run summary.",
            "inputSchema": {"type": "object", "additionalProperties": False},
        },
        "defaultParameters": {},
        "executionService": "executor",
        "executorId": "workflow-dag",
        "deadlineSeconds": 60,
        "finalOutputs": [],
        "dataInputParameters": [],
        "inputSchemaChecksum": "",
    }
    if missing:
        del model["finalOutputs"]
    else:
        model["finalOutputs"] = outputs

    def checksum(value):
        data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        return "sha256:" + hashlib.sha256(data).hexdigest()

    model["inputSchemaChecksum"] = checksum(model["tool"]["inputSchema"])
    config["services"] = {"executor": {"internalBaseUrl": "http://executor-service:8000"}}
    config["models"] = [model]
    path.write_text(json.dumps(config))


@pytest.mark.parametrize("outputs", [["z-result", "a-result"], ["统计结果"], []])
def test_compiled_final_output_names_are_preserved_in_exact_order(monkeypatch, tmp_path, outputs):
    _model_outputs_config(monkeypatch, tmp_path, outputs)
    model, = KernelSettings.from_environment().models
    assert model.final_outputs == tuple(outputs)
    assert isinstance(model.final_outputs, tuple)


@pytest.mark.parametrize("outputs", [None, "coverage", {}, [None], [2], [""]])
def test_final_output_projection_rejects_invalid_names_at_config_boundary(monkeypatch, tmp_path, outputs):
    _model_outputs_config(monkeypatch, tmp_path, outputs)
    with pytest.raises(ValueError, match="KERNEL_CONFIG_INVALID"):
        KernelSettings.from_environment()


def test_missing_compiled_final_outputs_is_not_replaced_with_empty_names(monkeypatch, tmp_path):
    _model_outputs_config(monkeypatch, tmp_path, None, missing=True)
    with pytest.raises(ValueError, match="KERNEL_CONFIG_INVALID"):
        KernelSettings.from_environment()


@pytest.mark.parametrize("field,value", [
    ("modelChecksum", "sha256:bad"),
    ("executionService", []),
    ("maxParallelNodes", 4),
    ("inputBindings", {}),
])
def test_kernel_model_projection_rejects_invalid_or_executor_only_fields(
    monkeypatch, tmp_path, field, value,
):
    _model_outputs_config(monkeypatch, tmp_path, ["summary"])
    path = tmp_path / "kernel-config.json"
    config = json.loads(path.read_text())
    config["models"][0][field] = value
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="KERNEL_CONFIG_INVALID"):
        KernelSettings.from_environment()


@pytest.mark.parametrize("version", ["kernel-config.v2", "kernel-config.v3"])
def test_old_kernel_config_is_rejected_without_compatibility_path(monkeypatch, tmp_path, version):
    _environment(monkeypatch, tmp_path)
    path = tmp_path / "kernel-config.json"
    config = json.loads(path.read_text())
    config["schemaVersion"] = version
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="KERNEL_CONFIG_INVALID"):
        KernelSettings.from_environment()


@pytest.mark.parametrize(
    "change", ["missing", "count", "revision", "enabled", "extra"]
)
def test_kernel_config_requires_the_complete_frozen_operator_catalog(monkeypatch, tmp_path, change):
    _environment(monkeypatch, tmp_path)
    path = tmp_path / "kernel-config.json"
    config = json.loads(path.read_text())
    if change == "missing":
        del config["workflowAuthoring"]
    elif change == "count":
        config["workflowAuthoring"]["operators"].pop()
    elif change == "revision":
        config["workflowAuthoring"]["operators"][0]["implementationRevision"] = "changed"
    elif change == "enabled":
        config["workflowAuthoring"]["enabledOperators"] = ["unknown@1.0.0"]
    else:
        config["workflowAuthoring"]["installUrl"] = "http://untrusted.invalid"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError):
        KernelSettings.from_environment()
