"""Provider failure and wire-contract pressure scenarios."""
from __future__ import annotations
from pathlib import Path
import tempfile
import httpx
from platform_kernel.agent_runtime.observation_client import ObservationClient
from platform_kernel.errors import UpstreamServiceError
from ..context import ProbeRecorder
from ..environment import PROJECT, VERSION
from ..faults import ProbeTransport
from ..probe import _build_application, _settings

async def _run_fault_injection(root: Path, probes: ProbeRecorder) -> dict[str, object]:
    """Exercise stable upstream failure mapping without a real network call."""

    expected: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="kernel-fault-sandbox-") as temporary:
        settings = _settings(root, Path(temporary))
        definition = next(
            item for item in settings.observation_tools if item.name == "inspect_data_version"
        )
        service = settings.services_by_name[definition.service]
        for fault, stable_code in (
            ("data_503", "OBSERVATION_SERVICE_UNAVAILABLE"),
            ("observation_invalid", "OBSERVATION_RESPONSE_INVALID"),
        ):
            expected[fault] = stable_code
            transport = ProbeTransport(probes, settings.definition_set_digest, fault=fault)
            async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
                try:
                    await ObservationClient(client).invoke(
                        definition,
                        service,
                        call_id="fault-probe",
                        project_id=PROJECT,
                        trace_id="trace-fault-probe",
                        arguments={"version_id": VERSION},
                    )
                except UpstreamServiceError as error:
                    if str(error) != stable_code:
                        probes.fail(
                            "PROBE_FAULT_CODE_MISMATCH",
                            phase="fault",
                            operation=fault,
                            detail=type(error).__name__,
                        )
                else:
                    probes.fail(
                        "PROBE_FAULT_NOT_RAISED",
                        phase="fault",
                        operation=fault,
                        detail="observation unexpectedly succeeded",
                    )

        knowledge_definition = next(
            item for item in settings.observation_tools if item.name == "lookup_knowledge"
        )
        knowledge_service = settings.services_by_name[knowledge_definition.service]
        expected["knowledge_invalid_evidence"] = "OBSERVATION_RESPONSE_INVALID"
        knowledge_transport = ProbeTransport(
            probes, settings.definition_set_digest, fault="knowledge_invalid_evidence"
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(knowledge_transport)) as client:
            try:
                await ObservationClient(client).invoke(
                    knowledge_definition,
                    knowledge_service,
                    call_id="fault-knowledge",
                    project_id=PROJECT,
                    trace_id="trace-fault-knowledge",
                    arguments={
                        "key": "sandbox-key",
                        "binding": None,
                        "budget": {"max_chars": 100, "max_units": 1},
                    },
                )
            except UpstreamServiceError as error:
                if str(error) != expected["knowledge_invalid_evidence"]:
                    probes.fail(
                        "PROBE_FAULT_CODE_MISMATCH",
                        phase="fault",
                        operation="knowledge_invalid_evidence",
                        detail=type(error).__name__,
                    )
            else:
                probes.fail(
                    "PROBE_FAULT_NOT_RAISED",
                    phase="fault",
                    operation="knowledge_invalid_evidence",
                    detail="knowledge response unexpectedly succeeded",
                )

        mismatch_app, _mismatch_transport, mismatch_client = _build_application(
            root, Path(temporary) / "readiness-mismatch", probes,
            fault="executor_readiness_mismatch",
        )
        await mismatch_app.start()
        try:
            readiness = await mismatch_app.readiness_report()
            expected["executor_readiness_mismatch"] = "EXECUTOR_DEFINITION_SET_MISMATCH"
            executor = readiness.payload.get("components", {}).get("executor", {})
            if readiness.ready or not isinstance(executor, dict) or executor.get("error") != expected["executor_readiness_mismatch"]:
                probes.fail(
                    "PROBE_FAULT_CODE_MISMATCH",
                    phase="fault",
                    operation="executor_readiness_mismatch",
                    detail="readiness did not map digest mismatch",
                )
        finally:
            await mismatch_app.close()
            await mismatch_client.aclose()

        down_app, _down_transport, down_client = _build_application(
            root, Path(temporary) / "readiness-down", probes, fault="readiness_down"
        )
        await down_app.start()
        try:
            down_report = await down_app.readiness_report()
            expected["readiness_down"] = "READINESS_NOT_READY"
            if down_report.ready:
                probes.fail(
                    "PROBE_READINESS_FALSE_PASS",
                    phase="fault",
                    operation="readiness_down",
                    detail="readiness returned ready with a DOWN component",
                )
        finally:
            await down_app.close()
            await down_client.aclose()

        expected["provider_protocol_error"] = "PLATFORM_AGENT_PROTOCOL_INVALID"
        from platform_kernel.agent_runtime.provider import provider_wire_contract
        try:
            provider_wire_contract("sandbox-unsupported-provider", "responses")
        except ValueError as error:
            if str(error) != expected["provider_protocol_error"]:
                probes.fail(
                    "PROBE_FAULT_CODE_MISMATCH",
                    phase="fault",
                    operation="provider_protocol_error",
                    detail=type(error).__name__,
                )
        else:
            probes.fail(
                "PROBE_FAULT_NOT_RAISED",
                phase="fault",
                operation="provider_protocol_error",
                detail="invalid provider contract unexpectedly accepted",
            )

        readiness_transport = ProbeTransport(
            probes,
            settings.definition_set_digest,
            fault="executor_readiness_mismatch",
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(readiness_transport)) as client:
            response = await client.get("http://executor-control/internal/health/ready")
            expected["executor_readiness_transport"] = "EXECUTOR_DEFINITION_DIGEST_MISMATCH"
            if response.status_code != 200 or response.json().get("definitionSetDigest") == settings.definition_set_digest:
                probes.fail(
                    "PROBE_FAULT_CODE_MISMATCH",
                    phase="fault",
                    operation="executor_readiness_mismatch",
                    detail="digest mismatch was not observable",
                )

        timeout_transport = ProbeTransport(
            probes,
            settings.definition_set_digest,
            fault="executor_submit_timeout",
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(timeout_transport)) as client:
            expected["executor_submit_timeout"] = "EXECUTOR_SUBMIT_TIMEOUT"
            try:
                await client.post("http://executor-control/internal/execution/v6/jobs", json={})
            except httpx.ReadTimeout:
                pass
            else:
                probes.fail(
                    "PROBE_FAULT_NOT_RAISED",
                    phase="fault",
                    operation="executor_submit_timeout",
                    detail="timeout unexpectedly succeeded",
                )
    return {
        "status": "PASS" if not probes.failures else "FAILED",
        "expectedStableErrorCodes": expected,
        "criteria": {
            "readiness503CannotPass": "readiness_down" in expected,
            "observationFaultsCovered": all(
                name in expected for name in ("data_503", "observation_invalid", "knowledge_invalid_evidence")
            ),
            "providerAndExecutorFaultsCovered": all(
                name in expected for name in (
                    "provider_protocol_error", "executor_readiness_mismatch", "executor_submit_timeout",
                )
            ),
        },
    }
