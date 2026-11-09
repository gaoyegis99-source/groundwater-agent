from dataclasses import dataclass
import json
from typing import Literal

from agents import ModelBehaviorError, ModelRetrySettings, ModelSettings, RetryDecision
from agents.models.interface import Model
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.models.openai_responses import OpenAIResponsesModel
import httpx
import httpx2
from openai import AsyncOpenAI
from openai.types.shared import Reasoning
from pydantic import ValidationError

from ..settings import IntentRouterSettings, KernelSettings


@dataclass(frozen=True)
class AgentProvider:
    model: Model
    client: AsyncOpenAI
    http_client: httpx.AsyncClient | httpx2.AsyncClient

    async def close(self) -> None:
        await self.client.close()


@dataclass(frozen=True)
class ProviderWireContract:
    protocol: Literal["responses", "chat_completions"]
    router_output: Literal["SDK_STRUCTURED", "EXACT_LITERAL"]
    force_tool_call: bool


def provider_wire_contract(provider: str, protocol: str) -> ProviderWireContract:
    if provider == "minimax":
        if protocol != "chat_completions":
            raise ValueError("PLATFORM_AGENT_PROTOCOL_INVALID")
        return ProviderWireContract("chat_completions", "EXACT_LITERAL", True)
    if provider == "deepseek":
        if protocol == "responses":
            return ProviderWireContract("responses", "SDK_STRUCTURED", False)
        if protocol == "chat_completions":
            return ProviderWireContract("chat_completions", "SDK_STRUCTURED", False)
    raise ValueError("PLATFORM_AGENT_PROTOCOL_INVALID")


def build_agent_provider(settings: KernelSettings) -> AgentProvider:
    return _build_provider(
        provider=settings.agent_provider,
        protocol=settings.agent_protocol,
        base_url=settings.agent_base_url,
        model=settings.agent_model,
        api_key=settings.agent_api_key,
    )


def build_intent_router_provider(settings: IntentRouterSettings) -> AgentProvider:
    return _build_provider(
        provider=settings.provider,
        protocol=settings.protocol,
        base_url=settings.base_url,
        model=settings.model,
        api_key=settings.api_key,
    )


def build_model_settings(settings: KernelSettings) -> ModelSettings:
    contract = provider_wire_contract(settings.agent_provider, settings.agent_protocol)
    if contract.force_tool_call:
        return ModelSettings(
            tool_choice="required",
            parallel_tool_calls=False,
            # OpenAI Agents only enables streamed usage automatically for an
            # official OpenAI endpoint. MiniMax supports include_usage on its
            # compatible endpoint, so request it explicitly.
            include_usage=True,
            max_tokens=settings.agent_max_output_tokens,
            timeout=settings.agent_request_timeout_seconds,
            retry=ModelRetrySettings(max_retries=0, policy=protocol_retry_policy),
            extra_body=_minimax_agent_extra_body(settings.agent_model),
        )
    # Flash requires non-thinking mode when a tool call is mandatory.
    force_tools = (
        settings.agent_model == "deepseek-flash"
        and settings.agent_protocol == "responses"
    )
    return ModelSettings(
        tool_choice="required" if force_tools else None,
        parallel_tool_calls=False if force_tools else None,
        max_tokens=settings.agent_max_output_tokens,
        timeout=settings.agent_request_timeout_seconds,
        retry=ModelRetrySettings(max_retries=0, policy=protocol_retry_policy),
        reasoning=Reasoning(effort="none" if force_tools else "high"),
        extra_body={"thinking": {"type": "enabled"}}
        if settings.agent_protocol == "chat_completions"
        else None,
    )


def build_intent_router_model_settings(
    settings: IntentRouterSettings,
) -> ModelSettings:
    deepseek_literal = (
        settings.provider == "deepseek" and settings.protocol == "responses"
    )
    minimax_preview = (
        settings.provider == "minimax"
        and settings.model == "MiniMax-M3.1-Flash-Preview"
    )
    return ModelSettings(
        # The DeepSeek Router emits only three literals.  Disabling reasoning
        # and bounding the output avoids spending its budget on prose that the
        # strict parser must reject.
        max_tokens=(
            min(settings.max_output_tokens, 64)
            if deepseek_literal
            else settings.max_output_tokens
        ),
        timeout=settings.request_timeout_seconds,
        retry=ModelRetrySettings(max_retries=0, policy=protocol_retry_policy),
        reasoning=(
            Reasoning(effort="none")
            if deepseek_literal
            # M3.1 reasoning cannot be disabled.  Its default effort is max
            # and consumes the same output budget as the three-field Router
            # literal, so use the Provider-supported low effort here.
            else Reasoning(effort="low")
            if minimax_preview
            else None
        ),
        include_usage=True if settings.provider == "minimax" else None,
        extra_body=_minimax_extra_body(settings.model)
        if settings.provider == "minimax"
        else None,
    )


def _minimax_extra_body(model: str) -> dict[str, object]:
    body: dict[str, object] = {"reasoning_split": True}
    if model == "MiniMax-M3":
        body["thinking"] = {"type": "disabled"}
    return body


def _minimax_agent_extra_body(model: str) -> dict[str, object]:
    if model == "MiniMax-M3":
        # MiniMax requires the complete assistant response to be replayed for
        # M3's interleaved tool reasoning.  The Agents chat-completions adapter
        # preserves native content but does not replay MiniMax's non-standard
        # reasoning_details field, so keep thinking in content for Agent turns.
        return {"reasoning_split": False}
    return {"reasoning_split": True}


def protocol_retry_policy(context) -> RetryDecision:
    """Retry only malformed model protocol/schema output, never transport failures."""
    current: BaseException | None = context.error
    while current is not None:
        if isinstance(
            current, (ModelBehaviorError, json.JSONDecodeError, ValidationError)
        ):
            return RetryDecision(
                retry=True,
                delay=0,
                reason="PROTOCOL_OR_SCHEMA_INVALID",
                approve_unsafe_replay=True,
            )
        current = current.__cause__ or current.__context__
    return RetryDecision(retry=False, reason="ERROR_CLASS_NOT_RETRYABLE")


def _build_provider(
    *,
    provider: str,
    protocol: str,
    base_url: str,
    model: str,
    api_key: str,
) -> AgentProvider:
    contract = provider_wire_contract(provider, protocol)
    # Provider endpoints are an explicit instance contract.  Never inherit an
    # operator-shell proxy (especially SOCKS) or its undeclared dependencies.
    http_client = httpx2.AsyncClient(trust_env=False)
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        max_retries=0,
        http_client=http_client,
    )
    configured: Model
    if contract.protocol == "responses":
        configured = OpenAIResponsesModel(model, client)
    else:
        configured = OpenAIChatCompletionsModel(
            model, client, should_replay_reasoning_content=lambda _: True
        )
    return AgentProvider(configured, client, http_client)
