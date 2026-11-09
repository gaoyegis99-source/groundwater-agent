"""Application fixtures and scripted models shared by pressure scenarios."""

from __future__ import annotations
import asyncio
from pathlib import Path
import threading
import time
from uuid import uuid4
import httpx
from agents.testing import ModelStep, ScriptedModel, assistant_message, function_call
from platform_kernel.composition import PlatformApplication
from platform_kernel.settings import IntentRouterSettings, KernelSettings
from .context import ProbeEvent, ProbeRecorder
from .environment import (
    PROJECT,
    VERSION,
    _contains,
    _digest,
    _jsonable,
    _kernel_config_path,
    _seen_tool,
)
from .faults import ProbeTransport


class ScenarioModel(ScriptedModel):
    """A deterministic, per-turn state machine safe for concurrent turns."""

    def __init__(self, probes: ProbeRecorder, *, steps: int = 512) -> None:
        self.probes = probes
        self._lock = threading.Lock()
        self.active = 0
        self.peak = 0
        super().__init__([ModelStep.respond(self._respond) for _ in range(steps)])

    async def _respond(self, call):
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        started = time.monotonic()
        try:
            await asyncio.sleep(0.025)
            is_execution = _contains(call.input, "sandbox-exec")
            is_knowledge = _contains(call.input, "sandbox-knowledge")
            if is_knowledge and not _seen_tool(call.input, "lookup_knowledge"):
                output = function_call(
                    "lookup_knowledge",
                    {
                        "key": "sandbox-key",
                        "binding": None,
                        "budget": {"max_chars": 100, "max_units": 1},
                    },
                    call_id=f"probe-knowledge-{uuid4().hex}",
                )
            elif not is_execution:
                output = _reply_call("sandbox read complete")
            else:
                seen_prepare = _seen_tool(call.input, "prepare_task_inputs")
                seen_plan = _seen_tool(call.input, "record_plan")
                seen_action = _seen_tool(call.input, "run_summary")
                seen_reply = _seen_tool(call.input, "submit_reply")
                if not seen_prepare:
                    output = function_call(
                        "prepare_task_inputs",
                        {
                            "target_tool": "run_summary",
                            "arguments": {"versionId": VERSION},
                        },
                        call_id=f"probe-prepare-{uuid4().hex}",
                    )
                elif not seen_plan:
                    output = function_call(
                        "record_plan",
                        {
                            "goal": "sandbox execution",
                            "steps": [
                                {
                                    "step_id": "summary",
                                    "tool": "run_summary",
                                    "purpose": "probe",
                                }
                            ],
                            "reason": "deterministic sandbox probe",
                        },
                        call_id=f"probe-plan-{uuid4().hex}",
                    )
                elif not seen_action:
                    output = function_call(
                        "run_summary",
                        {"versionId": VERSION},
                        call_id=f"probe-action-{uuid4().hex}",
                    )
                elif not seen_reply:
                    output = _reply_call("sandbox execution complete", status=True)
                else:
                    output = _reply_call("sandbox execution complete", status=True)
            self.probes.event(
                ProbeEvent(
                    phase="model",
                    operation="get_response",
                    direction="outbound",
                    route="provider://deterministic",
                    status="OK",
                    duration_ms=(time.monotonic() - started) * 1000,
                    field_names=("type", "name", "arguments"),
                    payload_digest=_digest(_jsonable(output)),
                )
            )
            return ModelStep(output=[output])
        finally:
            with self._lock:
                self.active -= 1


class RouterModel(ScriptedModel):
    """Return the v11 semantic literal without making external calls."""

    def __init__(self, *, steps: int = 128) -> None:
        super().__init__([ModelStep.respond(self._respond) for _ in range(steps)])

    async def _respond(self, call):
        await asyncio.sleep(0.005)
        literal = (
            "EXECUTE_ON_DATA|NONE|NONE"
            if _contains(call.input, "sandbox-exec")
            else "CONSULT|NONE|NONE"
        )
        return ModelStep(output=[assistant_message(literal)])


def _reply_call(text: str, *, status: bool = False):
    return function_call(
        "submit_reply",
        {
            "conclusion": {"text": text, "citations": []},
            "details": [],
            "unresolved_items": [],
            "referenced_workflow_runs": [],
            "include_execution_status": status,
            "interaction_options": [],
            "workflow_draft": None,
        },
        call_id=f"probe-reply-{uuid4().hex}",
    )


def _settings(root: Path, data_root: Path) -> KernelSettings:
    try:
        config = _kernel_config_path(root)
    except ValueError as error:
        raise RuntimeError("KERNEL_CONFIG_REQUIRED_RUN_COMPILE_FIRST") from error
    if not config.is_file():
        raise RuntimeError("KERNEL_CONFIG_REQUIRED_RUN_COMPILE_FIRST")
    router = IntentRouterSettings.from_values(
        provider="deepseek",
        protocol="responses",
        base_url="http://intent-router-sandbox/v1",
        model="sandbox-router",
        api_key="sandbox-key",
        context_window_tokens=8192,
        max_output_tokens=256,
        request_timeout_seconds=5,
        segment_timeout_seconds=10,
    )
    return KernelSettings.from_config(
        config,
        data_root=data_root,
        project_id=PROJECT,
        agent_provider="deepseek",
        agent_protocol="responses",
        agent_base_url="http://agent-sandbox/v1",
        agent_model="sandbox-agent",
        agent_api_key="sandbox-key",
        agent_context_window_tokens=131072,
        agent_max_output_tokens=4096,
        agent_request_timeout_seconds=5,
        agent_segment_timeout_seconds=30,
        intent_router=router,
    )


def _attach_readiness_transport(
    application: PlatformApplication, client: httpx.AsyncClient
) -> None:
    """Keep readiness inside the same closed MockTransport boundary as the app."""

    readiness = application.readiness
    readiness._client = client
    readiness._owns_client = False
    for probe in readiness.probes.values():
        probe.client = client
        probe._cached = None


def _build_application(
    root: Path,
    data_root: Path,
    probes: ProbeRecorder,
    *,
    fault: str | None = None,
    model_steps: int = 512,
) -> tuple[PlatformApplication, ProbeTransport, httpx.AsyncClient]:
    settings = _settings(root, data_root)
    transport = ProbeTransport(probes, settings.definition_set_digest, fault=fault)
    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    application = PlatformApplication(
        settings=settings,
        model=ScenarioModel(probes, steps=model_steps),
        client=client,
        intent_router_model=RouterModel(steps=model_steps),
    )
    _attach_readiness_transport(application, client)
    return application, transport, client


async def _wait_for_state(
    application: PlatformApplication,
    session_id: str,
    expected: set[str],
    timeout: float = 15,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        view = application.interaction.get(session_id)
        if view.agent_steps and view.agent_steps[-1] in expected:
            return {"status": view.agent_steps[-1], "view": view}
        await asyncio.sleep(0.02)
    raise TimeoutError(f"KERNEL_SANDBOX_STATE_TIMEOUT:{sorted(expected)}")
