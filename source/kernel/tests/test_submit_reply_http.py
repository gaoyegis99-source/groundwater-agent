from __future__ import annotations

import asyncio
from dataclasses import replace
import json

import httpx
import httpx2
from agents.models.openai_responses import OpenAIResponsesModel
from openai import AsyncOpenAI

from platform_kernel.composition import PlatformApplication
from platform_kernel.database import connect_database


def test_responses_provider_submits_answer_in_one_request(kernel_settings):
    requests = []

    def provider(request):
        body = json.loads(request.content)
        requests.append(body)
        output = [{
            "type": "function_call", "id": "reply", "call_id": "reply",
            "name": "submit_reply", "arguments": json.dumps({
                "conclusion": {"text": "正式正文。", "citations": []},
                "details": [],
                "unresolved_items": [], "referenced_workflow_runs": [],
                "include_execution_status": False,
                "interaction_options": [], "workflow_draft": None,
            }, ensure_ascii=False),
        }]
        return httpx2.Response(200, json={
            "id": "response-1", "object": "response", "created_at": 1,
            "status": "completed", "model": "test-model", "output": output,
            "usage": {"input_tokens": 5, "output_tokens": 3, "total_tokens": 8},
        })

    async def run():
        async with AsyncOpenAI(
            api_key="test", base_url="http://agent.test/v1", max_retries=0,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(provider)),
        ) as client:
            app = PlatformApplication(
                settings=replace(kernel_settings("knowledge"), agent_protocol="responses"),
                model=OpenAIResponsesModel("test-model", client),
                client=httpx.AsyncClient(transport=httpx.MockTransport(
                    lambda _: httpx.Response(500)
                )),
            )
            with connect_database(app.database) as connection:
                connection.execute("INSERT INTO sessions (session_id, project_id, goal_revision, agent_run_id, pending_agent_run_id, agent_start_state, owner_subject) VALUES ('s','p',1,NULL,NULL,NULL,'local-researcher')")
            return await app.agent_runtime.start(
                "s", "p", 1, "问题", (), agent_run_id="a"
            )

    result = asyncio.run(run())
    assert result.status == "COMPLETE" and result.output.message == "正式正文。"
    assert result.total_tokens == 8
    assert len(requests) == 1
    assert requests[0]["reasoning"] == {"effort": "high"}
    assert "submit_reply" in {tool["name"] for tool in requests[0]["tools"]}
    assert "prepare_reply" not in {tool["name"] for tool in requests[0]["tools"]}
