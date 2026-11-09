from __future__ import annotations

import json
from pathlib import Path
import runpy

import httpx
import pytest

PROBE = runpy.run_path(
    str(Path(__file__).parent / "defect_discovery/external_live/deepseek_smoke.py")
)


@pytest.mark.parametrize(
    "unsupported",
    [None, "CgroupVersion", "CgroupDriver", "MemoryLimit", "CpuCfsQuota", "PidsLimit"],
)
def test_container_flags_do_not_establish_resource_enforcement(tmp_path, unsupported):
    container = runpy.run_path(
        str(Path(__file__).parent / "defect_discovery/external_live/container_smoke.py")
    )
    runner = container["ReviewedRunner"](tmp_path / "unused-wrapper")
    info = {
        "CgroupVersion": "2",
        "CgroupDriver": "systemd",
        "MemoryLimit": True,
        "CpuCfsQuota": True,
        "PidsLimit": True,
    }
    if unsupported is not None:
        info[unsupported] = (
            "1"
            if unsupported == "CgroupVersion"
            else "none"
            if unsupported == "CgroupDriver"
            else False
        )
    calls = []

    def docker(*arguments, **kwargs):
        calls.append(arguments)
        assert arguments[0] == "info", "must refuse before creating a container"
        return json.dumps(info)

    runner.docker = docker
    if unsupported is None:
        runner.check_resources()
        assert runner.resource_facts == info
        return
    with pytest.raises(
        container["ResourceBoundaryError"], match="ENFORCEMENT_UNAVAILABLE"
    ):
        runner.run(
            image=container["IMAGE"],
            command=container["COMMAND"],
            arguments={},
            action_id="synthetic",
            timeout_seconds=20,
            resource_limits=container["LIMITS"],
        )
    assert len(calls) == 1
    assert runner.container_ids == []


@pytest.mark.anyio
async def test_real_provider_transport_limits_are_enforced_before_dispatch(tmp_path):
    sent = []

    def respond(request):
        sent.append(request)
        return httpx.Response(
            200, json={"usage": {"prompt_tokens": 20, "completion_tokens": 10}}
        )

    transport = PROBE["BudgetTransport"](tmp_path)
    transport.delegate = httpx.MockTransport(respond)
    payload = {
        "model": "deepseek-flash",
        "max_tokens": 256,
        "thinking": {"type": "disabled"},
    }
    async with httpx.AsyncClient(transport=transport) as client:
        for _ in range(3):
            await client.post("https://api.deepseek.com/chat/completions", json=payload)
        with pytest.raises(RuntimeError, match="BUDGET_EXHAUSTED"):
            await client.post("https://api.deepseek.com/chat/completions", json=payload)
        with pytest.raises(RuntimeError, match="OUTSIDE_APPROVED_SCOPE"):
            await client.post("https://example.com/chat/completions", json=payload)
    assert len(sent) == 3
    assert len(list(tmp_path.glob("attempt-*.json"))) == 3
    assert 3 * PROBE["RESERVE"] < PROBE["CAP"]


@pytest.mark.anyio
async def test_live_probe_can_complete_offline_without_using_environment_credentials(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-offline-credential")
    transport_type = PROBE["BudgetTransport"]
    original_init = transport_type.__init__
    seen = []

    def respond(request):
        payload = json.loads(request.content)
        seen.append(payload)
        name = (
            "echo"
            if len(seen) == 1
            else next(
                t["function"]["name"]
                for t in payload["tools"]
                if t["function"]["name"] != "echo"
            )
        )
        arguments = {"value": "alpha"} if len(seen) == 1 else {"answer": "echo:alpha"}
        return httpx.Response(
            200,
            json={
                "id": "synthetic",
                "object": "chat.completion",
                "created": 1,
                "model": "deepseek-flash",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-" + str(len(seen)),
                                    "type": "function",
                                    "function": {
                                        "name": name,
                                        "arguments": json.dumps(arguments),
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 10,
                    "total_tokens": 30,
                },
            },
        )

    def initialize(self, root):
        original_init(self, root)
        self.delegate = httpx.MockTransport(respond)

    monkeypatch.setattr(transport_type, "__init__", initialize)
    report = await PROBE["collect"](tmp_path)
    assert report["result"] == "PASS", json.dumps(report)
    assert report["transport_attempts"] == 2
    assert all(report["sdk_versions"].values())
    assert len(seen) == 2
