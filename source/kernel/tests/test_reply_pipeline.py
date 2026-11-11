from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace

from agents.testing import ScriptedModel, ModelStep, assistant_message, function_call
import httpx
import pytest

from kernel.tests.reply_testing import submit_reply as _submit
from platform_kernel.composition import PlatformApplication
from platform_kernel.database import connect_database, utc_now
from platform_kernel.errors import AgentRunError
from platform_kernel.agent_runtime.persistence import create_run
from platform_kernel.agent_runtime.mode import resolve_mode_capabilities
from platform_kernel.agent_runtime.reply import _execution_status
from platform_kernel.agent_runtime.tooling import (
    reply_submission as reply_submission_module,
)
from platform_kernel.agent_runtime.tooling.reply_submission import ReplySubmission
from platform_kernel.agent_runtime.tooling.replies import (
    _normalize_provider_reply_json,
)


def _citation(index=2, checksum=None):
    return {
        "version_id": "version-doc",
        "unit_id": f"unit-{index}",
        "checksum": checksum or "sha256:" + "b" * 64,
    }


def _wire_citation(citation):
    return {
        "versionId": citation["version_id"],
        "unitId": citation["unit_id"],
        "checksum": citation["checksum"],
    }


def _application(
    kernel_settings,
    steps,
    *,
    observation_handler=None,
    capabilities="knowledge",
    **settings_changes,
):
    model = ScriptedModel(steps)
    application = PlatformApplication(
        settings=replace(kernel_settings(capabilities), **settings_changes),
        model=model,
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                observation_handler or (lambda _: httpx.Response(500))
            )
        ),
    )
    with connect_database(application.database) as connection:
        connection.execute(
            "INSERT INTO sessions (session_id, project_id, goal_revision, agent_run_id, pending_agent_run_id, agent_start_state, owner_subject) VALUES ('s','p',1,NULL,NULL,NULL,'local-researcher')"
        )
    return application, model


async def _start(
    application,
    *,
    dialogue_policy="READ_ONLY",
    message="中文提问",
):
    return await application.agent_runtime.start(
        "s",
        "p",
        1,
        message,
        (),
        agent_run_id="a",
        dialogue_policy=dialogue_policy,
    )


def _knowledge_handler(status="OK"):
    def handle(_request):
        return httpx.Response(
            200,
            json={
                "data": {
                    "items": [
                        {
                            "status": status,
                            "bindingMatch": "EXACT",
                            "versionId": "version-doc",
                            "unitId": "unit-2",
                            "knowledgeId": "doc",
                            "locator": {"title": "方法说明"},
                            "content": "覆盖范围是点集凸包" if status == "OK" else None,
                            "checksum": "sha256:" + "b" * 64,
                        }
                    ]
                }
            },
        )

    return handle


def _read():
    return function_call(
        "read_evidence",
        {
            "refs": [_citation()],
        },
        call_id="read",
    )


def test_ordinary_answer_uses_one_model_request_and_no_reply_checkpoint(
    kernel_settings,
):
    app, model = _application(
        kernel_settings,
        [
            ModelStep(output=[_submit("中文正文", unresolved=[("请确认单位", ())])]),
        ],
    )
    run = asyncio.run(_start(app))
    assert run.status == "COMPLETE"
    assert run.output.message == "中文正文\n\n需要补充：\n请确认单位"
    assert run.output.unresolved_items == ["请确认单位"]
    assert run.output.presentation.model_dump() == {
        "conclusion": {"markdown": "中文正文", "source_indexes": []},
        "details": [],
        "execution_summary": None,
        "unresolved_items": [{"markdown": "请确认单位", "source_indexes": []}],
        "sources": [],
    }
    assert len(model.calls) == 1
    assert {tool.name for tool in model.calls[0].tools} >= {"submit_reply"}
    with connect_database(app.database) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM agent_run_state").fetchone()[0]
            == 0
        )


def test_comparison_reply_retries_until_named_methods_and_recommendation_are_visible(
    kernel_settings,
):
    app, model = _application(
        kernel_settings,
        [
            ModelStep(
                output=[_submit("建议优先做监测趋势分析。", call_id="incomplete")]
            ),
            ModelStep(
                output=[
                    _submit(
                        "监测趋势分析适合判断近期变化，地下水流场分析适合判断空间方向；"
                        "建议先做监测趋势分析，再按需要做地下水流场分析。",
                        call_id="complete",
                    )
                ]
            ),
        ],
    )

    run = asyncio.run(
        _start(
            app,
            message=(
                "比较监测趋势分析和地下水流场分析：我想判断近期变化并了解空间方向，"
                "请给出建议。"
            ),
        )
    )

    assert run.status == "COMPLETE"
    assert len(model.calls) == 2
    assert model.calls[1].model_settings.tool_choice == "submit_reply"
    assert "REPLY_COMPARISON_INCOMPLETE" in json.dumps(model.calls[1].input)


def test_enumerated_unit_reply_retries_until_every_named_item_is_visible(
    kernel_settings,
):
    app, model = _application(
        kernel_settings,
        [
            ModelStep(
                output=[
                    _submit(
                        "水头和高程使用米。",
                        call_id="incomplete",
                    )
                ]
            ),
            ModelStep(
                output=[
                    _submit(
                        "水头、高程和地下水埋深均使用米。",
                        call_id="complete",
                    )
                ]
            ),
        ],
    )

    run = asyncio.run(
        _start(
            app,
            message="请直接说明水头、高程和地下水埋深使用什么单位。",
        )
    )

    assert run.status == "COMPLETE"
    assert len(model.calls) == 2
    assert model.calls[1].model_settings.tool_choice == "submit_reply"
    assert "REPLY_REQUESTED_TERMS_INCOMPLETE" in json.dumps(model.calls[1].input)


def test_minimax_string_reply_segments_are_structurally_adapted(kernel_settings):
    compact = function_call(
        "submit_reply",
        {
            "conclusion": json.dumps(
                {
                    "text": "地下水位是地下水面相对于统一基准面的高程。",
                    "citations": [],
                },
                ensure_ascii=False,
            ),
            "details": ["应同时说明高程基准。"],
            "unresolved_items": [],
            "referenced_workflow_runs": [],
            "include_execution_status": False,
            "interaction_options": [],
            "workflow_draft": None,
        },
        call_id="reply",
    )
    app, model = _application(
        kernel_settings,
        [ModelStep(output=[compact])],
        agent_provider="minimax",
        agent_protocol="chat_completions",
    )

    run = asyncio.run(_start(app))

    assert run.status == "COMPLETE"
    assert run.output.message == (
        "地下水位是地下水面相对于统一基准面的高程。\n\n应同时说明高程基准。"
    )
    assert len(model.calls) == 1


def test_minimax_single_citations_and_missing_null_draft_are_adapted(kernel_settings):
    citation = _citation()
    compact = function_call(
        "submit_reply",
        {
            "conclusion": {
                "text": "结论",
                "citations": {
                    "item": json.dumps(citation),
                },
            },
            "details": [{"text": "依据", "citations": citation}],
            "unresolved_items": [],
            "referenced_workflow_runs": [],
            "include_execution_status": False,
            "interaction_options": [],
        },
        call_id="reply",
    )
    app, model = _application(
        kernel_settings,
        [ModelStep(output=[_read()]), ModelStep(output=[compact])],
        observation_handler=_knowledge_handler(),
        agent_provider="minimax",
        agent_protocol="chat_completions",
    )

    run = asyncio.run(_start(app))

    assert run.status == "COMPLETE"
    assert run.output.message == "结论\n\n依据\n\n依据：方法说明"
    assert len(model.calls) == 2


def test_minimax_item_wrapped_citation_lists_are_adapted(kernel_settings):
    citation = _citation()
    compact = function_call(
        "submit_reply",
        {
            "conclusion": {
                "text": "结论",
                "citations": {
                    "item": [citation],
                },
            },
            "details": [
                {
                    "text": "依据",
                    "citations": {
                        "item": json.dumps([citation]),
                    },
                }
            ],
            "unresolved_items": [],
            "referenced_workflow_runs": [],
            "include_execution_status": False,
            "interaction_options": [],
            "workflow_draft": None,
        },
        call_id="reply",
    )
    app, _model = _application(
        kernel_settings,
        [ModelStep(output=[_read()]), ModelStep(output=[compact])],
        observation_handler=_knowledge_handler(),
        agent_provider="minimax",
        agent_protocol="chat_completions",
    )

    assert asyncio.run(_start(app)).output.message == ("结论\n\n依据\n\n依据：方法说明")


def test_minimax_citations_wrapped_citation_lists_are_adapted(kernel_settings):
    citation = _citation()
    compact = function_call(
        "submit_reply",
        {
            "conclusion": {
                "text": "结论",
                "citations": {
                    "citations": [citation],
                },
            },
            "details": [],
            "unresolved_items": [],
            "referenced_workflow_runs": [],
            "include_execution_status": False,
            "interaction_options": [],
            "workflow_draft": None,
        },
        call_id="reply",
    )
    app, _model = _application(
        kernel_settings,
        [ModelStep(output=[_read()]), ModelStep(output=[compact])],
        observation_handler=_knowledge_handler(),
        agent_provider="minimax",
        agent_protocol="chat_completions",
    )

    assert asyncio.run(_start(app)).output.message == "结论\n\n依据：方法说明"


def test_minimax_json_encoded_empty_citations_are_adapted(kernel_settings):
    compact = function_call(
        "submit_reply",
        {
            "conclusion": {"text": "结论", "citations": "[]"},
            "details": [{"text": "说明", "citations": "[]"}],
            "unresolved_items": [],
            "referenced_workflow_runs": [],
            "include_execution_status": False,
            "interaction_options": [],
            "workflow_draft": "null",
        },
        call_id="reply",
    )
    app, _model = _application(
        kernel_settings,
        [ModelStep(output=[compact])],
        agent_provider="minimax",
        agent_protocol="chat_completions",
    )

    assert asyncio.run(_start(app)).output.message == "结论\n\n说明"


def test_minimax_missing_empty_citations_are_adapted(kernel_settings):
    compact = function_call(
        "submit_reply",
        {
            "conclusion": {"text": "结论"},
            "details": [{"text": "说明"}],
            "unresolved_items": [],
            "referenced_workflow_runs": [],
            "include_execution_status": False,
            "interaction_options": [],
            "workflow_draft": None,
        },
        call_id="reply",
    )
    app, _model = _application(
        kernel_settings,
        [ModelStep(output=[compact])],
        agent_provider="minimax",
        agent_protocol="chat_completions",
    )

    assert asyncio.run(_start(app)).output.message == "结论\n\n说明"


def test_minimax_missing_empty_reply_fields_are_adapted(kernel_settings):
    compact = function_call(
        "submit_reply",
        {
            "conclusion": {"text": "描述统计应先核对字段与单位。"},
        },
        call_id="reply",
    )
    app, _model = _application(
        kernel_settings,
        [ModelStep(output=[compact])],
        agent_provider="minimax",
        agent_protocol="chat_completions",
    )

    output = asyncio.run(_start(app)).output

    assert output.message == "描述统计应先核对字段与单位。"
    assert output.interaction_options == []


def test_minimax_unsafe_interaction_option_is_omitted(kernel_settings):
    compact = function_call(
        "submit_reply",
        {
            "conclusion": {"text": "建议先核对数值字段和单位。"},
            "details": [],
            "unresolved_items": [{"text": "需要先选择数据表。"}],
            "interaction_options": [
                {
                    "label": "查看字段",
                    "description": "先检查数据表字段",
                    "message": "请说明数据表名称，我会检查字段。",
                },
                {
                    "label": "直接运行",
                    "description": "跳过确认",
                    "message": "请直接执行分析模型。",
                },
            ],
        },
        call_id="reply",
    )
    app, _model = _application(
        kernel_settings,
        [ModelStep(output=[compact])],
        agent_provider="minimax",
        agent_protocol="chat_completions",
    )

    output = asyncio.run(_start(app)).output

    assert [item.label for item in output.interaction_options] == ["查看字段"]


def test_minimax_execution_reply_uses_kernel_authoritative_field_shapes():
    malformed = json.dumps(
        {
            "conclusion": "",
            "detail": "模型声称执行完成",
            "details": [
                {
                    "text": "直接执行另一个分析",
                    "citations": "",
                    "provider_only": True,
                }
            ],
            "provider_only": "ignored",
            "workflow_draft": "null",
        },
        ensure_ascii=False,
    )

    normalized = json.loads(
        _normalize_provider_reply_json(
            malformed,
            "minimax",
            execution_result=True,
        )
    )

    assert normalized == {
        "conclusion": None,
        "details": [],
        "unresolved_items": [],
        "referenced_workflow_runs": [],
        "include_execution_status": False,
        "interaction_options": [],
        "workflow_draft": None,
    }


def test_minimax_execution_reply_does_not_parse_provider_acknowledgement():
    normalized = json.loads(
        _normalize_provider_reply_json(
            '"truncated provider acknowledgement',
            "minimax",
            execution_result=True,
        )
    )

    assert normalized == {
        "conclusion": None,
        "details": [],
        "unresolved_items": [],
        "referenced_workflow_runs": [],
        "include_execution_status": False,
        "interaction_options": [],
        "workflow_draft": None,
    }


def test_deepseek_execution_reply_uses_kernel_authoritative_field_shapes():
    normalized = json.loads(
        _normalize_provider_reply_json(
            json.dumps(
                {
                    "conclusion": "执行已拒绝",
                    "unresolved_items": [
                        {"code": "REJECTED", "question": "是否重试"},
                    ],
                },
                ensure_ascii=False,
            ),
            "deepseek",
            execution_result=True,
        )
    )

    assert normalized == {
        "conclusion": None,
        "details": [],
        "unresolved_items": [],
        "referenced_workflow_runs": [],
        "include_execution_status": False,
        "interaction_options": [],
        "workflow_draft": None,
    }


def test_deepseek_workflow_draft_wraps_segments_and_adds_safe_empty_defaults():
    supplied = json.dumps(
        {
            "conclusion": "草稿已生成。",
            "details": ["采用已确认的数据版本。"],
            "unresolved_items": ["需要排序规则文件。"],
            "workflow_draft": {"name": "draft"},
        },
        ensure_ascii=False,
    )

    normalized = json.loads(
        _normalize_provider_reply_json(
            supplied,
            "deepseek",
            drafting=True,
        )
    )

    assert normalized["conclusion"] == {
        "text": "草稿已生成。",
        "citations": [],
    }
    assert normalized["details"] == [
        {
            "text": "采用已确认的数据版本。",
            "citations": [],
        }
    ]
    assert normalized["unresolved_items"] == [
        {
            "text": "需要排序规则文件。",
            "citations": [],
        }
    ]
    assert normalized["referenced_workflow_runs"] == []
    assert normalized["include_execution_status"] is False
    assert normalized["interaction_options"] == []
    assert normalized["workflow_draft"] == {"name": "draft"}


def test_deepseek_non_draft_reply_normalizes_observed_segment_shorthand():
    supplied = json.dumps(
        {
            "conclusion": "保持严格边界。",
            "details": [],
            "unresolved_items": [],
        },
        ensure_ascii=False,
    )

    normalized = json.loads(
        _normalize_provider_reply_json(
            supplied,
            "deepseek",
            drafting=False,
        )
    )

    assert normalized["conclusion"] == {
        "text": "保持严格边界。",
        "citations": [],
    }


@pytest.mark.parametrize("dialogue_policy", ["READ_ONLY", "EXECUTION_PROPOSAL"])
def test_deepseek_reply_reaches_kernel_validation_boundary(
    kernel_settings,
    dialogue_policy,
):
    settings = kernel_settings()
    request_context = {"mode": "DIALOGUE", "dialogue_policy": dialogue_policy}
    capabilities = resolve_mode_capabilities(
        request_context,
        settings.models,
    )
    submission = ReplySubmission(
        settings.data_root / "kernel.sqlite3",
        request_context,
        "deepseek",
    )

    assert not submission.tool(capabilities).strict_json_schema


def test_deepseek_non_draft_reply_normalizes_observed_citation_aliases():
    checksum = "sha256:" + "a" * 64
    supplied = json.dumps(
        {
            "conclusion": "无法执行。",
            "details": [
                {
                    "text": "输入数据版本不满足条件。",
                    "citations": [
                        {
                            "document_title": "provider display metadata",
                            "knowledge_version_id": "version-observed",
                            "unit_id": "unit-observed",
                            "checksum": checksum,
                        }
                    ],
                }
            ],
            "unresolved_items": ["请提供满足条件的数据版本。"],
            "referenced_workflow_runs": [],
            "include_execution_status": False,
            "interaction_options": [],
            "workflow_draft": None,
        },
        ensure_ascii=False,
    )

    normalized = json.loads(
        _normalize_provider_reply_json(
            supplied,
            "deepseek",
            drafting=False,
        )
    )

    assert normalized["conclusion"] == {"text": "无法执行。", "citations": []}
    assert normalized["unresolved_items"] == [
        {
            "text": "请提供满足条件的数据版本。",
            "citations": [],
        }
    ]
    assert normalized["details"][0]["citations"] == [
        {
            "version_id": "version-observed",
            "unit_id": "unit-observed",
            "checksum": checksum,
        }
    ]


def test_deepseek_draft_drops_prose_citations_without_trusted_read(
    kernel_settings,
):
    app, _model = _application(kernel_settings, [])
    citation = _citation()
    submission = ReplySubmission(
        app.database,
        {"mode": "WORKFLOW_DRAFT", "dialogue_policy": "READ_ONLY"},
        "deepseek",
    )
    context = SimpleNamespace(
        context={
            "agent_run_id": "a",
            "governance": {
                "finalization_reason": "BUDGET",
                "knowledge_evidence": [],
            },
        }
    )
    reply = asyncio.run(
        submission.submit_reply(
            context,
            json.dumps(
                {
                    "conclusion": {"text": "已形成草案。", "citations": [citation]},
                    "details": [
                        {"text": "候选资料未经完整读取。", "citations": [citation]}
                    ],
                    "unresolved_items": [
                        {"text": "请确认输出名称。", "citations": [citation]}
                    ],
                    "referenced_workflow_runs": [],
                    "include_execution_status": False,
                    "interaction_options": [],
                    "workflow_draft": None,
                },
                ensure_ascii=False,
            ),
        )
    )

    assert reply.conclusion.citations == []
    assert reply.details[0].citations == []
    assert reply.unresolved_items[0].citations == []


def test_deepseek_draft_keeps_strict_citation_validation_after_trusted_read(
    kernel_settings,
):
    app, _model = _application(kernel_settings, [])
    submission = ReplySubmission(
        app.database,
        {"mode": "WORKFLOW_DRAFT", "dialogue_policy": "READ_ONLY"},
        "deepseek",
    )
    context = SimpleNamespace(
        context={
            "agent_run_id": "a",
            "governance": {
                "finalization_reason": "BUDGET",
                "knowledge_evidence": [
                    [
                        _citation()["version_id"],
                        _citation()["unit_id"],
                        _citation()["checksum"],
                    ]
                ],
            },
        }
    )
    arguments = json.dumps(
        {
            "conclusion": {
                "text": "已形成草案。",
                "citations": [{**_citation(), "unit_id": "untrusted-unit"}],
            },
            "details": [],
            "unresolved_items": [{"text": "请确认输出名称。", "citations": []}],
            "referenced_workflow_runs": [],
            "include_execution_status": False,
            "interaction_options": [],
            "workflow_draft": None,
        },
        ensure_ascii=False,
    )

    with pytest.raises(AgentRunError) as failure:
        asyncio.run(submission.submit_reply(context, arguments))
    assert failure.value.diagnostics == {"reason": "REPLY_CITATION_INVALID"}


def test_minimax_execution_reply_uses_persisted_action_when_marker_is_stale(
    kernel_settings,
    monkeypatch,
):
    app, _model = _application(kernel_settings, [])
    monkeypatch.setattr(
        reply_submission_module,
        "execution_reply_facts",
        lambda _database, _run_id: {
            "has_action": True,
            "workflow_run_ids": ["workflow-run"],
        },
    )
    submission = ReplySubmission(
        app.database,
        {"mode": "DIALOGUE", "dialogue_policy": "EXECUTION_PROPOSAL"},
        "minimax",
    )
    context = SimpleNamespace(
        context={
            "agent_run_id": "a",
            "governance": {
                "finalization_reason": "BUDGET",
                "knowledge_evidence": [],
            },
        }
    )

    reply = asyncio.run(
        submission.submit_reply(
            context,
            '"truncated provider acknowledgement',
        )
    )

    assert reply.model_dump() == {
        "conclusion": None,
        "details": [],
        "unresolved_items": [],
        "referenced_workflow_runs": ["workflow-run"],
        "include_execution_status": True,
        "interaction_options": [],
        "workflow_draft": None,
    }


def test_knowledge_answer_checks_exact_full_read_citation_in_same_loop(kernel_settings):
    app, model = _application(
        kernel_settings,
        [
            ModelStep(output=[_read()]),
            ModelStep(
                output=[_submit("覆盖范围只是点集凸包。", citations=[_citation()])]
            ),
        ],
        observation_handler=_knowledge_handler(),
    )
    run = asyncio.run(_start(app))
    assert run.status == "COMPLETE"
    assert run.output.message == "覆盖范围只是点集凸包。\n\n依据：方法说明"
    assert run.output.presentation.model_dump() == {
        "conclusion": {"markdown": "覆盖范围只是点集凸包。", "source_indexes": [0]},
        "details": [],
        "execution_summary": None,
        "unresolved_items": [],
        "sources": [
            {
                "title": "方法说明",
                "version_id": "version-doc",
                "unit_id": "unit-2",
                "checksum": "sha256:" + "b" * 64,
            }
        ],
    }
    assert "doc" not in run.output.message
    assert "sha256:" not in run.output.message
    assert "/api/" not in run.output.message
    assert len(model.calls) == 2
    assert all(
        any(tool.name == "submit_reply" for tool in call.tools) for call in model.calls
    )


def test_read_only_evidence_hit_converges_to_read_then_submit(kernel_settings):
    citation = _citation()

    def handler(request):
        if request.url.path.endswith("retrieve_evidence"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            **_wire_citation(citation),
                            "status": "OK",
                            "knowledgeId": "doc",
                            "locator": {"title": "方法说明"},
                        }
                    ]
                },
            )
        return _knowledge_handler()(request)

    steps = [
        ModelStep(
            output=[
                function_call(
                    "retrieve_evidence",
                    {"query": "水头单位"},
                    call_id="retrieve",
                )
            ]
        ),
        ModelStep(output=[_read()]),
        ModelStep(output=[_submit("单位为米。", citations=[citation])]),
    ]
    app, model = _application(
        kernel_settings,
        steps,
        observation_handler=handler,
    )
    assert asyncio.run(_start(app)).status == "COMPLETE"
    second_tools = {tool.name for tool in model.calls[1].tools}
    assert "read_evidence" in second_tools
    assert "retrieve_evidence" not in second_tools
    assert [tool.name for tool in model.calls[2].tools] == ["submit_reply"]


def test_completed_evidence_answer_omits_provider_navigation_options(kernel_settings):
    citation = _citation()

    def handler(request):
        if request.url.path.endswith("retrieve_evidence"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            **_wire_citation(citation),
                            "status": "OK",
                            "knowledgeId": "doc",
                            "locator": {"title": "方法说明"},
                        }
                    ]
                },
            )
        return _knowledge_handler()(request)

    provider_reply = function_call(
        "submit_reply",
        {
            "conclusion": {"text": "单位为米。", "citations": [citation]},
            "details": [],
            "unresolved_items": [{"text": "可再选择其他分析。", "citations": []}],
            "referenced_workflow_runs": [],
            "include_execution_status": False,
            "interaction_options": [
                {
                    "label": "补充背景",
                    "description": "说明其他判断背景",
                    "message": "请说明其他判断背景。",
                },
                {
                    "label": "查看方法",
                    "description": "了解其他可选方法",
                    "message": "请介绍其他可选方法。",
                },
            ],
            "workflow_draft": None,
        },
        call_id="reply",
    )
    app, _model = _application(
        kernel_settings,
        [
            ModelStep(
                output=[
                    function_call(
                        "retrieve_evidence",
                        {"query": "水头单位"},
                        call_id="retrieve",
                    )
                ]
            ),
            ModelStep(output=[_read()]),
            ModelStep(output=[provider_reply]),
        ],
        observation_handler=handler,
    )

    run = asyncio.run(_start(app))

    assert run.status == "COMPLETE"
    assert run.output.interaction_options == []
    assert run.output.unresolved_items == ["可再选择其他分析。"]


def test_deepseek_replayed_disabled_retrieval_uses_current_evidence_tool(
    kernel_settings,
):
    citation = _citation()

    def handler(request):
        if request.url.path.endswith("retrieve_evidence"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            **_wire_citation(citation),
                            "status": "OK",
                            "knowledgeId": "doc",
                            "locator": {"title": "方法说明"},
                        }
                    ]
                },
            )
        return _knowledge_handler()(request)

    retrieve = function_call(
        "retrieve_evidence",
        {"query": "水头单位"},
        call_id="retrieve",
    )
    replay = function_call(
        "retrieve_evidence",
        {"query": "水头单位"},
        call_id="replayed-retrieve",
    )
    app, model = _application(
        kernel_settings,
        [
            ModelStep(output=[retrieve]),
            ModelStep(output=[replay]),
            ModelStep(output=[_read()]),
            ModelStep(output=[_submit("单位为米。", citations=[citation])]),
        ],
        observation_handler=handler,
    )

    assert asyncio.run(_start(app)).status == "COMPLETE"
    assert len(model.calls) == 4
    assert "OBSERVATION_TOOL_ALREADY_CONSUMED" in json.dumps(model.calls[2].input)
    assert "read_evidence" in {tool.name for tool in model.calls[2].tools}
    assert model.calls[2].model_settings.tool_choice != "submit_reply"
    with connect_database(app.database) as connection:
        attempts = [
            tuple(row)
            for row in connection.execute(
                "SELECT stage,attempt,outcome,error_class FROM provider_requests "
                "ORDER BY created_at,rowid"
            )
        ]
    assert attempts[1:3] == [
        ("AGENT_INITIAL:2", 1, "FAILED", "PROTOCOL_OR_SCHEMA_INVALID"),
        ("AGENT_INITIAL:2", 2, "SUCCEEDED", None),
    ]


def test_minimax_knowledge_chain_uses_compact_schema_and_kernel_budget(
    kernel_settings,
):
    citation = _citation()
    service_arguments = []

    def handler(request):
        service_arguments.append(json.loads(request.content)["arguments"])
        if request.url.path.endswith("retrieve_evidence"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            **_wire_citation(citation),
                            "status": "OK",
                            "knowledgeId": "doc",
                            "locator": {"title": "方法说明"},
                        }
                    ]
                },
            )
        return _knowledge_handler()(request)

    app, model = _application(
        kernel_settings,
        [
            ModelStep(
                output=[
                    function_call(
                        "retrieve_evidence",
                        {"query": "地下水时间聚合", "budget": "16000"},
                        call_id="retrieve",
                    )
                ]
            ),
            ModelStep(
                output=[
                    function_call(
                        "read_evidence",
                        {"refs": [citation], "budget": 8},
                        call_id="read",
                    )
                ]
            ),
            ModelStep(output=[_submit("应按目的选择聚合频率。", citations=[citation])]),
        ],
        observation_handler=handler,
        agent_provider="minimax",
        agent_protocol="chat_completions",
    )

    run = asyncio.run(_start(app))

    assert run.status == "COMPLETE"
    assert service_arguments == [
        {
            "query": "地下水时间聚合",
        },
        {
            "refs": [citation],
        },
    ]
    first_tools = {tool.name: tool for tool in model.calls[0].tools}
    assert "lookup_knowledge" not in first_tools
    assert (
        "budget"
        not in first_tools["retrieve_evidence"].params_json_schema["properties"]
    )
    second_tools = {tool.name: tool for tool in model.calls[1].tools}
    assert "read_evidence" in second_tools
    assert "retrieve_evidence" not in second_tools
    assert "lookup_knowledge" not in second_tools
    assert (
        "budget" not in second_tools["read_evidence"].params_json_schema["properties"]
    )


def test_minimax_plain_final_text_is_submitted_with_read_evidence(kernel_settings):
    citation = _citation()

    def handler(request):
        if request.url.path.endswith("retrieve_evidence"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            **_wire_citation(citation),
                            "status": "OK",
                            "knowledgeId": "doc",
                            "locator": {"title": "方法说明"},
                        }
                    ]
                },
            )
        return _knowledge_handler()(request)

    app, model = _application(
        kernel_settings,
        [
            ModelStep(
                output=[
                    function_call(
                        "retrieve_evidence",
                        {"query": "地下水时间聚合"},
                        call_id="retrieve",
                    )
                ]
            ),
            ModelStep(output=[_read()]),
            ModelStep(output=[assistant_message("应按目的选择聚合频率。")]),
        ],
        observation_handler=handler,
        agent_provider="minimax",
        agent_protocol="chat_completions",
    )

    run = asyncio.run(_start(app))

    assert run.status == "COMPLETE"
    assert run.output.message == "应按目的选择聚合频率。\n\n依据：方法说明"
    assert model.calls[-1].model_settings.tool_choice == "submit_reply"
    assert model.calls[-1].model_settings.parallel_tool_calls is False


def test_minimax_finalization_repairs_one_disabled_declared_tool(kernel_settings):
    citation = _citation()

    def handler(request):
        if request.url.path.endswith("retrieve_evidence"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            **_wire_citation(citation),
                            "status": "OK",
                            "knowledgeId": "doc",
                            "locator": {"title": "方法说明"},
                        }
                    ]
                },
            )
        return _knowledge_handler()(request)

    app, model = _application(
        kernel_settings,
        [
            ModelStep(
                output=[
                    function_call(
                        "retrieve_evidence",
                        {"query": "地下水时间聚合"},
                        call_id="retrieve",
                    )
                ]
            ),
            ModelStep(output=[_read()]),
            ModelStep(
                output=[
                    function_call(
                        "retrieve_evidence",
                        {"query": "重复调用"},
                        call_id="disabled-final-tool",
                    )
                ]
            ),
            ModelStep(output=[_submit("应按目的选择聚合频率。", citations=[citation])]),
        ],
        observation_handler=handler,
        agent_provider="minimax",
        agent_protocol="chat_completions",
    )

    run = asyncio.run(_start(app))

    assert run.status == "COMPLETE"
    assert len(model.calls) == 4
    assert model.calls[-2].model_settings.tool_choice == "submit_reply"
    assert model.calls[-1].model_settings.tool_choice == "submit_reply"


def test_minimax_plain_final_json_is_validated_as_submit_reply(kernel_settings):
    citation = _citation()

    def handler(request):
        if request.url.path.endswith("retrieve_evidence"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            **_wire_citation(citation),
                            "status": "OK",
                            "knowledgeId": "doc",
                            "locator": {"title": "方法说明"},
                        }
                    ]
                },
            )
        return _knowledge_handler()(request)

    payload = {
        "conclusion": {"text": "应按目的选择聚合频率。", "citations": [citation]},
        "details": [{"text": "先核验采样密度。", "citations": [citation]}],
        "unresolved_items": [],
        "referenced_workflow_runs": [],
        "include_execution_status": False,
        "interaction_options": [],
        "workflow_draft": None,
    }

    app, _model = _application(
        kernel_settings,
        [
            ModelStep(
                output=[
                    function_call(
                        "retrieve_evidence",
                        {"query": "地下水时间聚合"},
                        call_id="retrieve",
                    )
                ]
            ),
            ModelStep(output=[_read()]),
            ModelStep(
                output=[assistant_message(json.dumps(payload, ensure_ascii=False))]
            ),
        ],
        observation_handler=handler,
        agent_provider="minimax",
        agent_protocol="chat_completions",
    )

    run = asyncio.run(_start(app))

    assert run.status == "COMPLETE"
    assert run.output.presentation.conclusion.markdown == "应按目的选择聚合频率。"
    assert run.output.presentation.details[0].markdown == "先核验采样密度。"
    assert run.output.message == (
        "应按目的选择聚合频率。\n\n先核验采样密度。\n\n依据：方法说明"
    )


@pytest.mark.parametrize(
    ("provider", "protocol"),
    [("minimax", "chat_completions"), ("deepseek", "responses")],
)
def test_execution_proposal_keeps_knowledge_tools_for_preparation(
    kernel_settings,
    provider,
    protocol,
):
    data_settings = kernel_settings("summary")
    knowledge_settings = kernel_settings("knowledge")
    app, model = _application(
        kernel_settings,
        [
            ModelStep(
                output=[
                    _submit(
                        "尚不能形成执行提案。",
                        unresolved=[("缺少输入数据版本。", ())],
                    )
                ]
            )
        ],
        capabilities="summary",
        observation_tools=(
            *data_settings.observation_tools,
            *knowledge_settings.observation_tools,
        ),
        agent_provider=provider,
        agent_protocol=protocol,
    )

    run = asyncio.run(_start(app, dialogue_policy="EXECUTION_PROPOSAL"))

    assert run.status == "COMPLETE"
    names = {tool.name for tool in model.calls[0].tools}
    assert {"retrieve_evidence", "read_evidence"} <= names
    assert "inspect_data_version" in names
    assert "prepare_task_inputs" in names
    assert "run_summary" in names


def test_project_convention_execution_requires_full_knowledge_before_preparation(
    kernel_settings,
):
    citation = _citation()

    def handler(request):
        item = {
            **_wire_citation(citation),
            "status": "OK",
            "bindingMatch": "EXACT",
            "knowledgeId": "project-method",
            "locator": {"title": "项目方法约定"},
            "content": "参数取值以本约定为准。",
        }
        return httpx.Response(
            200,
            json={
                "data": [item]
                if request.url.path.endswith("retrieve_evidence")
                else {"items": [item]},
            },
        )

    data_settings = kernel_settings("summary")
    knowledge_settings = kernel_settings("knowledge")
    app, model = _application(
        kernel_settings,
        [
            ModelStep(
                output=[
                    function_call(
                        "retrieve_evidence",
                        {"query": "项目方法约定"},
                        call_id="retrieve",
                    )
                ]
            ),
            ModelStep(output=[_read()]),
            ModelStep(
                output=[
                    _submit(
                        "尚不能形成执行提案。",
                        citations=[citation],
                        unresolved=[("缺少输入数据版本。", ())],
                    )
                ]
            ),
        ],
        capabilities="summary",
        observation_tools=(
            *data_settings.observation_tools,
            *knowledge_settings.observation_tools,
        ),
        observation_handler=handler,
    )

    run = asyncio.run(
        _start(
            app,
            dialogue_policy="EXECUTION_PROPOSAL",
            message="请按项目约定执行汇总分析。",
        )
    )

    assert run.status == "COMPLETE"
    assert "prepare_task_inputs" not in {tool.name for tool in model.calls[0].tools}
    assert "prepare_task_inputs" not in {tool.name for tool in model.calls[1].tools}
    assert "read_evidence" in {tool.name for tool in model.calls[1].tools}
    assert "prepare_task_inputs" in {tool.name for tool in model.calls[2].tools}


def test_reply_keeps_business_identifiers_and_cited_questions(kernel_settings):
    text = "测试井号前缀为 GWTEST-0A5D92EF。"
    question = "需要使用哪个监测时段？"
    app, _ = _application(
        kernel_settings,
        [
            ModelStep(output=[_read()]),
            ModelStep(
                output=[
                    _submit(
                        text,
                        citations=[_citation()],
                        unresolved=[(question, [_citation()])],
                    )
                ]
            ),
        ],
        observation_handler=_knowledge_handler(),
    )
    run = asyncio.run(_start(app))
    assert run.output.message.startswith(text + "\n\n需要补充：")
    assert "需要补充：\n" + question in run.output.message
    assert run.output.unresolved_items == [question]
    assert run.output.presentation.conclusion.source_indexes == [0]
    assert run.output.presentation.unresolved_items[0].source_indexes == [0]
    assert len(run.output.presentation.sources) == 1
    assert run.output.message.endswith("依据：方法说明")


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("结论" * 81, "REPLY_CONCLUSION_TOO_LONG"),
        ("该答复来自 DeepSeek。", "REPLY_TECHNICAL_IDENTIFIER_VISIBLE"),
        ("请调用 submit_reply。", "REPLY_TECHNICAL_IDENTIFIER_VISIBLE"),
        ("请核对 checksum 后继续。", "REPLY_TECHNICAL_IDENTIFIER_VISIBLE"),
        ("内部版本 aquifer.analysis@1.2.3。", "REPLY_TECHNICAL_IDENTIFIER_VISIBLE"),
        (
            "详情见 https://internal.invalid/item。",
            "REPLY_TECHNICAL_IDENTIFIER_VISIBLE",
        ),
    ],
)
def test_visible_reply_policy_repairs_once_then_fails_closed(
    kernel_settings,
    text,
    reason,
):
    first = _submit(text)
    retry = function_call(
        "submit_reply",
        json.loads(first.arguments),
        call_id="reply-retry",
    )
    app, _ = _application(
        kernel_settings,
        [ModelStep(output=[first]), ModelStep(output=[retry])],
    )

    with pytest.raises(AgentRunError) as failure:
        asyncio.run(_start(app))

    assert failure.value.diagnostics == {"reason": reason}


@pytest.mark.parametrize(
    ("provider", "drafting", "unresolved"),
    [
        ("deepseek", False, []),
        ("minimax", False, []),
        ("deepseek", True, [{"text": "仍缺规则。", "citations": []}]),
        ("minimax", True, [{"text": "仍缺规则。", "citations": []}]),
    ],
)
def test_provider_reply_discards_unrequested_navigation(
    provider,
    drafting,
    unresolved,
):
    normalized = _normalize_provider_reply_json(
        json.dumps(
            {
                "conclusion": {"text": "回答完成。", "citations": []},
                "details": [],
                "unresolved_items": unresolved,
                "referenced_workflow_runs": [],
                "include_execution_status": False,
                "interaction_options": [
                    {
                        "label": "继续",
                        "description": "查看其他内容",
                        "message": "请介绍其他内容",
                    }
                ],
                "workflow_draft": None,
            }
        ),
        provider,
        drafting=drafting,
    )

    assert json.loads(normalized)["interaction_options"] == []


def test_non_clarification_question_only_requires_conclusion(kernel_settings):
    app, _ = _application(
        kernel_settings,
        [
            ModelStep(output=[_submit(unresolved=[("请提供监测时段", ())])]),
        ],
    )
    with pytest.raises(AgentRunError) as failure:
        asyncio.run(_start(app))
    assert failure.value.diagnostics == {"reason": "REPLY_CONCLUSION_REQUIRED"}


def test_multiline_answer_segment_is_preserved(kernel_settings):
    text = "第一行结论。\n第二行说明。"
    app, _ = _application(
        kernel_settings,
        [
            ModelStep(output=[_submit(text)]),
        ],
    )
    assert asyncio.run(_start(app)).output.message == text


def test_details_limit_accepts_three_without_truncation(kernel_settings):
    details = [(f"依据 {index}", ()) for index in range(3)]
    app, _ = _application(
        kernel_settings,
        [
            ModelStep(output=[_submit("结论", details=details)]),
        ],
    )
    output = asyncio.run(_start(app)).output
    assert [item.markdown for item in output.presentation.details] == [
        text for text, _citations in details
    ]
    assert output.message == "\n\n".join(
        [
            "结论",
            *(text for text, _citations in details),
        ]
    )


def test_negative_execution_statement_is_not_confirmation_bypass(kernel_settings):
    app, _ = _application(
        kernel_settings,
        [
            ModelStep(
                output=[
                    _submit(
                        "无法直接执行：缺少确切的数据版本。",
                        details=[("已核对资料，我不会绕过确认，当前未执行分析。", ())],
                    )
                ]
            ),
        ],
    )
    output = asyncio.run(_start(app)).output
    assert output.message == (
        "无法直接执行：缺少确切的数据版本。\n\n"
        "已核对资料，我不会绕过确认，当前未执行分析。"
    )


def test_negative_then_positive_execution_statement_remains_forbidden(kernel_settings):
    invalid = _submit("当前不能立即执行；请直接执行地下水分析。")
    retry = function_call(
        "submit_reply", json.loads(invalid.arguments), call_id="reply-retry"
    )
    app, _ = _application(
        kernel_settings,
        [
            ModelStep(output=[invalid]),
            ModelStep(output=[retry]),
        ],
    )

    with pytest.raises(AgentRunError) as failure:
        asyncio.run(_start(app))

    assert failure.value.diagnostics == {
        "reason": "REPLY_SCHEMA_INVALID",
        "issues": [{"path": "/conclusion", "type": "value_error"}],
    }


def test_direct_execution_instruction_remains_forbidden(kernel_settings):
    invalid = _submit("请直接执行地下水分析。")
    retry = function_call(
        "submit_reply", json.loads(invalid.arguments), call_id="reply-retry"
    )
    app, _ = _application(
        kernel_settings,
        [
            ModelStep(output=[invalid]),
            ModelStep(output=[retry]),
        ],
    )
    with pytest.raises(AgentRunError) as failure:
        asyncio.run(_start(app))
    assert failure.value.diagnostics == {
        "reason": "REPLY_SCHEMA_INVALID",
        "issues": [{"path": "/conclusion", "type": "value_error"}],
    }


def test_details_limit_rejects_four(kernel_settings):
    details = [(f"依据 {index}", ()) for index in range(4)]
    invalid = _submit("结论", details=details)
    retry = function_call(
        "submit_reply", json.loads(invalid.arguments), call_id="reply-retry"
    )
    app, _ = _application(
        kernel_settings,
        [
            ModelStep(output=[invalid]),
            ModelStep(output=[retry]),
        ],
    )
    with pytest.raises(AgentRunError, match="AGENT_MODEL_OUTPUT_INVALID"):
        asyncio.run(_start(app))


@pytest.mark.parametrize(
    ("submit", "diagnostics"),
    [
        (
            function_call(
                "submit_reply",
                {
                    "conclusion": {"text": " ", "citations": []},
                    "details": [],
                    "unresolved_items": [],
                    "referenced_workflow_runs": [],
                    "include_execution_status": False,
                    "interaction_options": [],
                    "workflow_draft": None,
                },
                call_id="reply",
            ),
            {
                "reason": "REPLY_SCHEMA_INVALID",
                "issues": [{"path": "/conclusion/text", "type": "string_too_short"}],
            },
        ),
        (
            _submit("不能完成。", unresolved=[(" ", ())]),
            {
                "reason": "REPLY_SCHEMA_INVALID",
                "issues": [
                    {"path": "/unresolved_items/0/text", "type": "string_too_short"}
                ],
            },
        ),
        (
            _submit("不能完成。", unresolved=[("第一行\n第二行", ())]),
            {"reason": "UNRESOLVED_ITEM_FORMAT_INVALID"},
        ),
    ],
)
def test_reply_semantic_failures_report_precise_reason(
    kernel_settings, submit, diagnostics
):
    steps = [ModelStep(output=[submit])]
    if diagnostics["reason"] == "REPLY_SCHEMA_INVALID":
        steps.append(
            ModelStep(
                output=[
                    function_call(
                        "submit_reply",
                        json.loads(submit.arguments),
                        call_id="reply-retry",
                    )
                ]
            )
        )
    app, _ = _application(kernel_settings, steps)
    with pytest.raises(AgentRunError) as failure:
        asyncio.run(_start(app))
    assert str(failure.value) == "AGENT_MODEL_OUTPUT_INVALID"
    assert failure.value.diagnostics == diagnostics


def test_citation_label_escaping_does_not_change_exact_identity(kernel_settings):
    citation = {**_citation(), "version_id": "method card(中文)&v2"}
    title = r"方法 [A]\B"

    def handler(_request):
        return httpx.Response(
            200,
            json={
                "data": {
                    "items": [
                        {
                            **_wire_citation(citation),
                            "status": "OK",
                            "bindingMatch": "EXACT",
                            "knowledgeId": "method-card",
                            "locator": {"title": title},
                            "content": "当前资料正文",
                        }
                    ]
                }
            },
        )

    app, _ = _application(
        kernel_settings,
        [
            ModelStep(
                output=[
                    function_call(
                        "read_evidence",
                        {
                            "refs": [citation],
                        },
                        call_id="read",
                    )
                ]
            ),
            ModelStep(output=[_submit("结论", citations=[citation, citation])]),
        ],
        observation_handler=handler,
    )
    output = asyncio.run(_start(app)).output
    assert output.message == "结论\n\n" + r"依据：方法 \[A\]\\B"
    assert output.presentation.conclusion.source_indexes == [0]
    assert [source.model_dump() for source in output.presentation.sources] == [
        {
            "title": title,
            "version_id": citation["version_id"],
            "unit_id": citation["unit_id"],
            "checksum": citation["checksum"],
        }
    ]
    assert citation["version_id"] not in output.message
    assert citation["checksum"] not in output.message


def test_sources_are_globally_deduplicated_in_first_section_order(kernel_settings):
    first = _citation(index=3, checksum="sha256:" + "c" * 64)
    second = _citation(index=4, checksum="sha256:" + "d" * 64)

    def read(citation, call_id):
        return function_call("read_evidence", {"refs": [citation]}, call_id=call_id)

    def handler(request):
        citation = json.loads(request.content)["arguments"]["refs"][0]
        return httpx.Response(
            200,
            json={
                "data": {
                    "items": [
                        {
                            **_wire_citation(citation),
                            "status": "OK",
                            "bindingMatch": "EXACT",
                            "knowledgeId": "doc",
                            "locator": {"title": f"来源 {citation['unit_id']}"},
                            "content": "supporting evidence",
                        }
                    ]
                }
            },
        )

    app, _ = _application(
        kernel_settings,
        [
            ModelStep(output=[read(first, "read-first")]),
            ModelStep(output=[read(second, "read-second")]),
            ModelStep(
                output=[
                    _submit(
                        "结论",
                        citations=[second, second],
                        details=[("依据", (first, second))],
                        unresolved=[("还需确认范围？", (first,))],
                    )
                ]
            ),
        ],
        observation_handler=handler,
    )
    presentation = asyncio.run(_start(app)).output.presentation
    assert [source.unit_id for source in presentation.sources] == ["unit-4", "unit-3"]
    assert presentation.conclusion.source_indexes == [0]
    assert presentation.details[0].source_indexes == [1, 0]
    assert presentation.unresolved_items[0].source_indexes == [1]


def test_details_without_conclusion_are_rejected(kernel_settings):
    app, _ = _application(
        kernel_settings,
        [
            ModelStep(output=[_submit(details=[("仅有依据", ())])]),
        ],
    )
    with pytest.raises(AgentRunError) as failure:
        asyncio.run(_start(app))
    assert failure.value.diagnostics == {"reason": "REPLY_CONCLUSION_REQUIRED"}


def test_pure_execution_status_has_only_kernel_owned_summary(
    kernel_settings,
    monkeypatch,
):
    action = SimpleNamespace(
        goal_revision=1,
        workflow_run_id=None,
        status="COMPLETE",
        outputs=(),
    )
    monkeypatch.setattr(
        "platform_kernel.agent_runtime.reply.load_execution_context",
        lambda *_args, **_kwargs: SimpleNamespace(actions=(action,)),
    )
    app, _ = _application(
        kernel_settings,
        [
            ModelStep(output=[_submit(status=True)]),
        ],
    )
    output = asyncio.run(_start(app)).output
    assert output.message == "已完成 1 项分析。"
    assert output.presentation.conclusion is None
    assert output.presentation.details == []
    assert output.presentation.execution_summary.model_dump() == {
        "markdown": "已完成 1 项分析。",
        "source_indexes": [],
    }
    assert output.presentation.unresolved_items == []
    assert output.presentation.sources == []


def test_execution_status_keeps_explanation_beside_kernel_owned_summary(
    kernel_settings,
    monkeypatch,
):
    action = SimpleNamespace(
        goal_revision=1,
        workflow_run_id="workflow-run-1",
        status="COMPLETE",
        outputs=({"output_name": "metrics"},),
    )
    monkeypatch.setattr(
        "platform_kernel.agent_runtime.reply.load_execution_context",
        lambda *_args, **_kwargs: SimpleNamespace(actions=(action,)),
    )
    app, _ = _application(
        kernel_settings,
        [
            ModelStep(
                output=[
                    _submit(
                        details=[("本次完成迁移学习回测，结果仅用于开发调试。", ())],
                        status=True,
                    )
                ]
            ),
        ],
    )
    output = asyncio.run(_start(app)).output
    assert output.presentation.execution_summary.markdown == (
        "已完成 1 项分析；生成 1 份结果，可在分析结果中查看或下载。"
    )
    assert [item.markdown for item in output.presentation.details] == [
        "本次完成迁移学习回测，结果仅用于开发调试。"
    ]


@pytest.mark.parametrize(
    "submit",
    [
        _submit("模型结论", status=True),
        _submit(unresolved=[("模型问题？", ())], status=True),
    ],
)
def test_execution_status_rejects_model_authored_sections(
    kernel_settings,
    submit,
):
    app, _ = _application(kernel_settings, [ModelStep(output=[submit])])
    with pytest.raises(AgentRunError) as failure:
        asyncio.run(_start(app))
    assert failure.value.diagnostics == {"reason": "REPLY_EXECUTION_STATUS_INVALID"}


def test_execution_summary_keeps_mixed_outcomes_without_internal_identities():
    actions = [
        SimpleNamespace(
            status="COMPLETE",
            outputs=(
                {"resource_id": "private-result-1"},
                {"resource_id": "private-result-2"},
            ),
        ),
        SimpleNamespace(status="FAILED", outputs=()),
        SimpleNamespace(status="REJECTED", outputs=()),
        SimpleNamespace(status="CANCELLED", outputs=()),
    ]
    assert _execution_status(actions) == (
        "已完成 1 项分析；未完成（执行失败） 1 项分析；已拒绝 1 项分析；已取消 1 项分析；"
        "生成 2 份结果，可在分析结果中查看或下载。"
    )


@pytest.mark.parametrize(
    "statuses,error", [([], "MISSING"), (["RUNNING"], "NOT_TERMINAL")]
)
def test_execution_summary_does_not_claim_missing_or_running_results(statuses, error):
    with pytest.raises(ValueError, match=f"EXECUTION_STATUS_{error}"):
        _execution_status(
            [SimpleNamespace(status=status, outputs=()) for status in statuses]
        )


@pytest.mark.parametrize(
    "invalid",
    [
        _citation(index=0),
        _citation(checksum="sha256:" + "a" * 64),
        {**_citation(), "version_id": "other-version"},
    ],
)
def test_wrong_knowledge_citation_retries_once_then_fails(kernel_settings, invalid):
    app, model = _application(
        kernel_settings,
        [
            ModelStep(output=[_read()]),
            ModelStep(output=[_submit("资料支持结论。", citations=[invalid])]),
            ModelStep(
                output=[
                    _submit(
                        "资料支持结论。",
                        citations=[invalid],
                        call_id="reply-retry",
                    )
                ]
            ),
        ],
        observation_handler=_knowledge_handler(),
    )
    with pytest.raises(AgentRunError, match="AGENT_MODEL_OUTPUT_INVALID") as failure:
        asyncio.run(_start(app))
    assert failure.value.diagnostics == {"reason": "REPLY_CITATION_INVALID"}
    assert len(model.calls) == 3
    assert app.agent_runtime.get("a").status == "FAILED"


def test_full_read_allows_uncited_unresolved_item_when_body_has_valid_citation(
    kernel_settings,
):
    app, model = _application(
        kernel_settings,
        [
            ModelStep(output=[_read()]),
            ModelStep(
                output=[
                    _submit(
                        "正文", citations=[_citation()], unresolved=[("单位？", ())]
                    )
                ]
            ),
        ],
        observation_handler=_knowledge_handler(),
    )
    run = asyncio.run(_start(app))
    assert run.status == "COMPLETE"
    assert run.output.unresolved_items == ["单位？"]
    assert len(model.calls) == 2


def test_full_read_allows_uncited_body_paragraph_when_another_body_paragraph_is_cited(
    kernel_settings,
):
    reply = function_call(
        "submit_reply",
        {
            "conclusion": {"text": "资料支持的结论。", "citations": [_citation()]},
            "details": [{"text": "请根据实际情况继续核对。", "citations": []}],
            "unresolved_items": [],
            "referenced_workflow_runs": [],
            "include_execution_status": False,
            "interaction_options": [],
            "workflow_draft": None,
        },
        call_id="reply",
    )
    app, _ = _application(
        kernel_settings,
        [
            ModelStep(output=[_read()]),
            ModelStep(output=[reply]),
        ],
        observation_handler=_knowledge_handler(),
    )
    assert asyncio.run(_start(app)).status == "COMPLETE"


def test_full_read_still_requires_conclusion_for_question_only_reply(kernel_settings):
    app, _ = _application(
        kernel_settings,
        [
            ModelStep(output=[_read()]),
            ModelStep(output=[_submit(unresolved=[("请补充单位？", ())])]),
        ],
        observation_handler=_knowledge_handler(),
    )
    with pytest.raises(AgentRunError) as failure:
        asyncio.run(_start(app))
    assert failure.value.diagnostics == {"reason": "REPLY_CONCLUSION_REQUIRED"}


def test_full_read_rejects_body_when_all_paragraphs_are_uncited(kernel_settings):
    app, model = _application(
        kernel_settings,
        [
            ModelStep(output=[_read()]),
            ModelStep(output=[_submit("正文")]),
            ModelStep(output=[_submit("正文", call_id="reply-retry")]),
        ],
        observation_handler=_knowledge_handler(),
    )
    with pytest.raises(AgentRunError, match="AGENT_MODEL_OUTPUT_INVALID") as failure:
        asyncio.run(_start(app))
    assert failure.value.diagnostics == {"reason": "REPLY_CITATION_REQUIRED"}
    assert len(model.calls) == 3


def test_invalid_citation_repairs_with_current_turn_evidence(kernel_settings):
    app, model = _application(
        kernel_settings,
        [
            ModelStep(output=[_read()]),
            ModelStep(
                output=[
                    _submit(
                        "资料支持结论。",
                        citations=[_citation(checksum="sha256:" + "a" * 64)],
                    )
                ]
            ),
            ModelStep(
                output=[
                    _submit(
                        "资料支持结论。",
                        citations=[_citation()],
                        call_id="reply-retry",
                    )
                ]
            ),
        ],
        observation_handler=_knowledge_handler(),
    )
    run = asyncio.run(_start(app))
    assert run.status == "COMPLETE"
    assert len(model.calls) == 3
    retry_input = json.dumps(model.calls[2].input, ensure_ascii=False)
    assert "allowed_citations" in retry_input
    assert all(value in retry_input for value in _citation().values())
    assert "copy all three fields without alteration" in retry_input
    with connect_database(app.database) as connection:
        rows = connection.execute(
            "SELECT stage,attempt,outcome,error_class FROM provider_requests "
            "WHERE agent_run_id='a' ORDER BY created_at"
        ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("AGENT_INITIAL:1", 1, "SUCCEEDED", None),
        ("AGENT_INITIAL:2", 1, "FAILED", "PROTOCOL_OR_SCHEMA_INVALID"),
        ("AGENT_INITIAL:2", 2, "SUCCEEDED", None),
    ]


def test_exact_retrieval_then_historical_bound_read_is_citable(kernel_settings):
    citation = _citation()

    def handler(request):
        retrieval = request.url.path.endswith("retrieve_evidence")
        item = {
            **_wire_citation(citation),
            "status": "OK",
            "bindingMatch": "EXACT" if retrieval else "NOT_CHECKED",
            "knowledgeId": "bound-dictionary",
            "locator": {"title": "当前资料数据字典"},
            "content": "水头、高程和地下水埋深均使用米。",
        }
        return httpx.Response(
            200,
            json={
                "data": [item] if retrieval else {"items": [item]},
            },
        )

    app, model = _application(
        kernel_settings,
        [
            ModelStep(
                output=[
                    function_call(
                        "retrieve_evidence",
                        {"query": "当前资料单位"},
                        call_id="retrieve",
                    )
                ]
            ),
            ModelStep(output=[_read()]),
            ModelStep(
                output=[
                    _submit(
                        "单位为米。",
                        citations=[_citation(checksum="sha256:" + "a" * 64)],
                    )
                ]
            ),
            ModelStep(
                output=[
                    _submit(
                        "单位为米。",
                        citations=[citation],
                        call_id="reply-retry",
                    )
                ]
            ),
        ],
        observation_handler=handler,
    )

    run = asyncio.run(_start(app))

    assert run.status == "COMPLETE"
    assert len(model.calls) == 4
    assert run.output.presentation.conclusion.source_indexes == [0]
    assert [source.model_dump() for source in run.output.presentation.sources] == [
        {
            "title": "当前资料数据字典",
            **citation,
        }
    ]


def test_stale_read_cannot_be_cited(kernel_settings):
    app, _ = _application(
        kernel_settings,
        [
            ModelStep(output=[_read()]),
            ModelStep(output=[_submit("旧资料结论", citations=[_citation()])]),
            ModelStep(
                output=[
                    _submit(
                        "旧资料结论",
                        citations=[_citation()],
                        call_id="reply-retry",
                    )
                ]
            ),
        ],
        observation_handler=_knowledge_handler("STALE"),
    )
    with pytest.raises(AgentRunError, match="AGENT_MODEL_OUTPUT_INVALID"):
        asyncio.run(_start(app))


@pytest.mark.parametrize(
    "text",
    [
        "自然语言正文",
        '{"message":"done","workflow_runs":[],"unresolved_items":[]}',
    ],
)
def test_plain_text_without_submit_reply_repairs_once_then_fails(
    kernel_settings,
    text,
):
    app, model = _application(
        kernel_settings,
        [
            ModelStep(output=[assistant_message(text)]),
            ModelStep(output=[assistant_message(text)]),
        ],
    )
    with pytest.raises(AgentRunError, match="AGENT_MODEL_OUTPUT_INVALID"):
        asyncio.run(_start(app))
    assert len(model.calls) == 2
    assert "protocol_repair_required" in json.dumps(model.calls[1].input)


def test_v4_submit_reply_shape_does_not_publish_answer(kernel_settings):
    invalid = function_call(
        "submit_reply",
        {
            "segments": [],
            "unresolved_items": [],
            "referenced_workflow_runs": ["foreign-run"],
            "include_execution_status": False,
            "interaction_options": [],
            "workflow_draft": None,
        },
        call_id="reply",
    )
    app, model = _application(
        kernel_settings,
        [
            ModelStep(output=[invalid]),
            ModelStep(
                output=[
                    function_call(
                        "submit_reply",
                        json.loads(invalid.arguments),
                        call_id="reply-retry",
                    )
                ]
            ),
        ],
    )
    with pytest.raises(AgentRunError, match="AGENT_MODEL_OUTPUT_INVALID"):
        asyncio.run(_start(app))
    assert len(model.calls) == 2
    assert app.agent_runtime.get("a").output is None


def test_reply_schema_failure_retries_once_from_the_submit_checkpoint(kernel_settings):
    invalid = function_call(
        "submit_reply",
        {
            "conclusion": {"text": "资料不存在。", "citations": []},
            "details": None,
            "unresolved_items": [],
            "referenced_workflow_runs": [],
            "include_execution_status": False,
            "interaction_options": [],
            "workflow_draft": None,
        },
        call_id="invalid-reply",
    )
    app, model = _application(
        kernel_settings,
        [
            ModelStep(output=[invalid]),
            ModelStep(output=[_submit("资料不存在。")]),
        ],
    )
    run = asyncio.run(_start(app))
    assert run.status == "COMPLETE"
    assert len(model.calls) == 2
    with connect_database(app.database) as connection:
        rows = connection.execute(
            "SELECT stage,attempt,outcome,error_class FROM provider_requests "
            "WHERE agent_run_id='a' ORDER BY created_at"
        ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("AGENT_INITIAL:1", 1, "FAILED", "PROTOCOL_OR_SCHEMA_INVALID"),
        ("AGENT_INITIAL:1", 2, "SUCCEEDED", None),
    ]


def test_execution_claim_without_action_retries_as_a_real_missing_input_reply(
    kernel_settings,
):
    app, model = _application(
        kernel_settings,
        [
            ModelStep(
                output=[
                    _submit(
                        "已提交确认，请确认后执行。",
                        call_id="invalid-claim",
                    )
                ]
            ),
            ModelStep(
                output=[
                    _submit(
                        "尚不能形成执行提案。",
                        unresolved=[("缺少排序规则 Data 版本。", ())],
                        call_id="missing-input",
                    )
                ]
            ),
        ],
    )

    run = asyncio.run(_start(app, dialogue_policy="EXECUTION_PROPOSAL"))

    assert run.status == "COMPLETE"
    assert run.output.unresolved_items == ["缺少排序规则 Data 版本。"]
    assert len(model.calls) == 2
    with connect_database(app.database) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM agent_actions").fetchone()[0] == 0
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM confirmations").fetchone()[0] == 0
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0] == 0
        )
        rows = connection.execute(
            "SELECT stage,attempt,outcome,error_class FROM provider_requests "
            "WHERE agent_run_id='a' ORDER BY created_at"
        ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("AGENT_INITIAL:1", 1, "FAILED", "PROTOCOL_OR_SCHEMA_INVALID"),
        ("AGENT_INITIAL:1", 2, "SUCCEEDED", None),
    ]


def test_read_tool_argument_failure_retries_without_calling_the_service(
    kernel_settings,
):
    service_calls = []

    def observe(request):
        service_calls.append(request)
        return httpx.Response(500)

    app, model = _application(
        kernel_settings,
        [
            ModelStep(
                output=[
                    function_call(
                        "retrieve_evidence",
                        {"query": None},
                        call_id="invalid-read",
                    )
                ]
            ),
            ModelStep(output=[_submit("参数已修正；本轮未读取或修改数据。")]),
        ],
        observation_handler=observe,
    )
    run = asyncio.run(_start(app))
    assert run.status == "COMPLETE"
    assert len(model.calls) == 2
    assert service_calls == []
    with connect_database(app.database) as connection:
        rows = connection.execute(
            "SELECT stage,attempt,outcome,error_class FROM provider_requests "
            "WHERE agent_run_id='a' ORDER BY created_at"
        ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("AGENT_INITIAL:1", 1, "FAILED", "PROTOCOL_OR_SCHEMA_INVALID"),
        ("AGENT_INITIAL:1", 2, "SUCCEEDED", None),
    ]


def test_process_crash_during_answer_fails_without_resending_request(kernel_settings):
    app, model = _application(kernel_settings, [])
    create_run(
        app.database,
        agent_run_id="a",
        session_id="s",
        project_id="p",
        goal_revision=1,
        provider="deepseek",
        model="test",
        trace_id="trace_" + "a" * 32,
        started_at=utc_now(),
    )
    app.agent_runtime.recover_interrupted()
    run = app.agent_runtime.get("a")
    assert run.status == "FAILED" and run.error == "PROCESS_INTERRUPTED"
    assert model.calls == ()
    with connect_database(app.database) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM agent_run_state").fetchone()[0]
            == 0
        )
