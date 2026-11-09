from __future__ import annotations

import asyncio
from dataclasses import replace
import json

from agents.testing import ScriptedModel, ModelStep, function_call
import httpx
import pytest

from kernel.tests.reply_testing import submit_reply as _submit
from platform_kernel.composition import PlatformApplication
from platform_kernel.database import connect_database
from platform_kernel.errors import AgentRunError, ResourceNotFoundError
from platform_kernel.tasks.persistence import get_task


GOAL = '请整理研究资料并交付说明'


def update(**changes):
    payload = dict(expected_revision=0, objective_quote=GOAL, constraints=[],
                   criteria=[dict(id='reply', text='交付说明', source_quote='交付说明', kind='REPLY')],
                   evidence=[dict(criterion_id='reply')])
    return {**payload, **changes}


def app_for(kernel_settings, steps, **settings_changes):
    model = ScriptedModel(steps)
    app = PlatformApplication(settings=replace(
        kernel_settings('knowledge'), **settings_changes,
    ), model=model,
                              client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(500))))
    session = app.interaction.create_session().session.session_id
    return app, session, model


def turn(app, session, message=GOAL):
    return asyncio.run(app.interaction.turn(session, message))


def test_task_commits_with_reply_and_reopens_on_new_requirement(kernel_settings):
    app, session, model = app_for(kernel_settings, [
        ModelStep(output=[function_call('update_task', update(), call_id='task')]),
        ModelStep(output=[_submit('说明已整理。')]),
        ModelStep(output=[function_call('update_task', update(
            expected_revision=1, objective_quote=None,
            criteria=[dict(id='review', text='业务可用', source_quote='业务可用', kind='USER_REVIEW')], evidence=[]), call_id='revision')]),
        ModelStep(output=[_submit('请评审。')]),
        ModelStep(output=[function_call('update_task', update(
            expected_revision=2, objective_quote=None, criteria=[],
            evidence=[dict(criterion_id='review', review_quote='确认完成：业务可用')]), call_id='review')]),
        ModelStep(output=[_submit('已记录评审。')]),
    ])
    turn(app, session)
    task = get_task(app.database, session, app.settings.project_id)
    assert task['spec']['revision'] == 1
    assert task['progress']['lifecycle'] == 'COMPLETE'
    turn(app, session, '还要确认业务可用')
    task = get_task(app.database, session, app.settings.project_id)
    assert task['progress']['state'] == 'WAITING_REVIEW'
    assert len(task['spec']['criteria']) == 2
    turn(app, session, '确认完成：业务可用')
    assert get_task(app.database, session, app.settings.project_id)['progress']['lifecycle'] == 'COMPLETE'
    assert 'TaskContextV1' in str(model.calls[-1])
    assert 'Interpretations/suggestions are non-authoritative' in str(model.calls[-1])
    with connect_database(app.database) as c:
        assert c.execute('SELECT COUNT(*) FROM workflow_runs').fetchone()[0] == 0
        assert c.execute('SELECT COUNT(*) FROM confirmations').fetchone()[0] == 0
        assert c.execute('SELECT COUNT(*) FROM task_revisions').fetchone()[0] == 3
        assert c.execute('SELECT COUNT(*) FROM task_completions').fetchone()[0] == 2


def test_bad_reply_discards_staged_task(kernel_settings):
    app, session, _ = app_for(kernel_settings, [
        ModelStep(output=[function_call('update_task', update(), call_id='task')]),
        ModelStep(output=[function_call('submit_reply', {'conclusion': 'wrong shape'}, call_id='bad')]),
        ModelStep(output=[function_call('submit_reply', {'conclusion': 'still wrong'}, call_id='bad-again')]),
    ])
    with pytest.raises(AgentRunError):
        turn(app, session)
    assert get_task(app.database, session, app.settings.project_id) is None
    with connect_database(app.database) as c:
        assert c.execute('SELECT COUNT(*) FROM task_runs').fetchone()[0] == 0
        assert c.execute('SELECT COUNT(*) FROM task_update_calls').fetchone()[0] == 0


def test_casual_question_has_no_task(kernel_settings):
    app, session, _ = app_for(kernel_settings, [ModelStep(output=[_submit('您好。')])])
    turn(app, session, '你好')
    assert get_task(app.database, session, app.settings.project_id) is None
    with pytest.raises(ResourceNotFoundError):
        get_task(app.database, session, 'other-project')


@pytest.mark.parametrize('changes', [
    {'objective_quote': 'not supplied by user'},
    {'expected_revision': 4},
    {'evidence': [{'criterion_id': 'reply', 'agent_run_id': 'fabricated'}]},
    {'steps': [{'id': 'a', 'title': 'cyclic', 'depends_on': ['a'], 'criterion_ids': ['reply'], 'gaps': []}]},
    {'criteria': [{'id': 'review', 'text': '业务可用', 'source_quote': '交付说明', 'kind': 'USER_REVIEW'}],
     'evidence': [{'criterion_id': 'review', 'review_quote': '交付说明'}]},
])
def test_untrusted_task_changes_fail_without_publication(kernel_settings, changes):
    invalid = update(**changes)
    app, session, model = app_for(kernel_settings, [
        ModelStep(output=[function_call(
            'update_task', invalid, call_id='task',
        )]),
        ModelStep(output=[function_call(
            'update_task', invalid, call_id='task-retry',
        )]),
    ])
    with pytest.raises(AgentRunError, match='AGENT_TASK_UPDATE_INVALID'):
        turn(app, session)
    assert len(model.calls) == 2
    assert model.calls[1].model_settings.tool_choice == 'update_task'
    assert get_task(app.database, session, app.settings.project_id) is None


def test_minimax_task_numeric_string_is_normalized_without_protocol_repair(
    kernel_settings,
):
    app, session, model = app_for(kernel_settings, [
        ModelStep(output=[function_call(
            'update_task', update(expected_revision='0'), call_id='normalized-task',
        )]),
        ModelStep(output=[_submit('说明已整理。')]),
    ], agent_provider='minimax', agent_protocol='chat_completions')

    turn(app, session)

    assert get_task(app.database, session, app.settings.project_id) is not None
    assert len(model.calls) == 2


@pytest.mark.parametrize('provider,protocol', [
    ('deepseek', 'responses'),
    ('minimax', 'chat_completions'),
])
def test_task_value_failure_gets_one_bounded_protocol_repair(
    kernel_settings, provider, protocol,
):
    app, session, model = app_for(kernel_settings, [
        ModelStep(output=[function_call(
            'update_task', update(), call_id='initial-task',
        )]),
        ModelStep(output=[function_call(
            'update_task', update(
                expected_revision=0,
                objective_quote=None,
                criteria=[],
                evidence=[],
            ), call_id='stale-task',
        )]),
        ModelStep(output=[function_call(
            'update_task', update(
                expected_revision=1,
                objective_quote=None,
                criteria=[],
                evidence=[],
            ), call_id='corrected-task',
        )]),
        ModelStep(output=[_submit('说明已整理。')]),
    ], agent_provider=provider, agent_protocol=protocol)

    turn(app, session)

    assert get_task(app.database, session, app.settings.project_id) is not None
    assert len(model.calls) == 4
    assert model.calls[2].model_settings.tool_choice == 'update_task'


def test_deepseek_task_schema_failure_gets_one_bounded_protocol_repair(
    kernel_settings,
):
    app, session, model = app_for(kernel_settings, [
        ModelStep(output=[function_call(
            'update_task', update(expected_revision='0'),
            call_id='invalid-task',
        )]),
        ModelStep(output=[function_call(
            'update_task', update(), call_id='repaired-task',
        )]),
        ModelStep(output=[_submit('说明已整理。')]),
    ])

    turn(app, session)

    assert get_task(app.database, session, app.settings.project_id) is not None
    assert len(model.calls) == 3
    assert model.calls[1].model_settings.tool_choice == 'update_task'


def test_task_and_reply_rollback_together(kernel_settings, monkeypatch):
    app, session, _ = app_for(kernel_settings, [
        ModelStep(output=[function_call('update_task', update(), call_id='task')]),
        ModelStep(output=[_submit('已整理。')]),
    ])
    from platform_kernel.tasks import persistence
    original = persistence.append_event
    def fail(c, sid, event, payload):
        if event == 'task.created':
            raise RuntimeError('injected commit failure')
        return original(c, sid, event, payload)
    monkeypatch.setattr(persistence, 'append_event', fail)
    with pytest.raises(RuntimeError, match='injected'):
        turn(app, session)
    with connect_database(app.database) as c:
        assert c.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM messages WHERE role='assistant'").fetchone()[0] == 0


def test_task_context_survives_reconstruction(kernel_settings):
    app, session, _ = app_for(kernel_settings, [
        ModelStep(output=[function_call('update_task', update(), call_id='task')]), ModelStep(output=[_submit('说明。')]),
    ])
    turn(app, session)
    before = get_task(app.database, session, app.settings.project_id)
    restored = PlatformApplication(settings=replace(app.settings), model=ScriptedModel([]), client=app.client)
    assert get_task(restored.database, session, app.settings.project_id) == before
    with connect_database(app.database) as c:
        events = c.execute("SELECT payload_json FROM session_events WHERE event_type LIKE 'task.%'").fetchall()
    assert all(GOAL not in json.loads(row[0]).values() for row in events)


@pytest.mark.parametrize('job_status,has_output,agent_status,expected', [
    ('SUCCEEDED', True, 'COMPLETE', True),
    ('SUCCEEDED', False, 'COMPLETE', False),
    ('FAILED', True, 'COMPLETE', False),
    ('SUCCEEDED', True, 'FAILED', False),
    ('RUNNING', False, 'WAITING_EXECUTION', False),
])
def test_result_requires_success_named_publication_and_no_unfinished_work(kernel_settings, job_status, has_output, agent_status, expected):
    from kernel.tests.test_workflow_progress import _batch, _save
    from platform_kernel.agent_runtime.persistence import get_run
    from platform_kernel.contracts import DataVersionRefV2
    from platform_kernel.tasks import TaskUpdate, commit_updates, stage_update
    settings = kernel_settings()
    database, (workflow,) = _batch(settings, count=1)
    with connect_database(database) as c:
        c.execute("UPDATE sessions SET goal_revision=1,agent_run_id='agent' WHERE session_id='session'")
        c.execute("INSERT INTO messages(session_id,goal_revision,role,content) VALUES ('session',1,'user',?)", (GOAL,))
    context = {'agent_run_id': 'agent'}
    stage_update(database, context, 'task', TaskUpdate.model_validate(update(
        criteria=[dict(id='result', text='交付说明', source_quote='交付说明', kind='RESULT', model_id=settings.models[0].model_id, output_names=['summary'])],
        evidence=[dict(criterion_id='result', workflow_run_id=workflow.run_id)],
    )), settings.models)
    with connect_database(database) as c:
        commit_updates(c, get_run(database, 'agent'), context['task_updates'])
    outputs = (DataVersionRefV2(
        kind='data_version', service='data-primary', dataset_id='dataset-results',
        version_id='published', semantic_id='semantics-results', object_id='object-published',
        media_type='application/json', format_id='json', size_bytes=4,
        checksum='sha256:' + 'a' * 64, output_name='summary',
    ),) if has_output else ()
    _save(database, workflow, job_status, outputs=outputs)
    with connect_database(database) as c:
        c.execute("UPDATE agent_runs SET status=? WHERE agent_run_id='agent'", (agent_status,))
    task = get_task(database, 'session', 'p')
    assert (task['progress']['lifecycle'] == 'COMPLETE') is expected


def test_task_draft_completion_uses_exportable_validated_payload(kernel_settings):
    from kernel.tests.test_workflow_drafts import _app, _create, _drain, _steps
    app, client, _ = _app(kernel_settings, [
        ModelStep(output=[function_call('update_task', update(
            objective_quote='创建排序后统计草稿',
            criteria=[dict(id='draft', text='草稿', source_quote='草稿', kind='DRAFT')],
            evidence=[dict(criterion_id='draft')],
        ), call_id='task')]), *_steps(),
    ])
    session, path, _ = _create(client)
    _drain(app, session)
    assert client.get(path + '/export').status_code == 200
    assert get_task(app.database, session, app.settings.project_id)['progress']['lifecycle'] == 'COMPLETE'


def test_task_update_idempotency_and_confirmation_revision_binding(kernel_settings):
    from platform_kernel.tasks import TaskUpdate, stage_update, commit_updates, require_current_confirmation, bind_confirmation
    from platform_kernel.errors import ConfirmationConflictError
    app, session, _ = app_for(kernel_settings, [ModelStep(output=[_submit('说明。')])])
    run = app.agent_runtime.get(turn(app, session).session.agent_run_id)
    context = {'agent_run_id': run.agent_run_id}
    payload = TaskUpdate.model_validate(update())
    assert stage_update(app.database, context, 'once', payload, ()) == stage_update(app.database, context, 'once', payload, ())
    assert len(context['task_updates']) == 1
    with connect_database(app.database) as c:
        commit_updates(c, run, context['task_updates'])
        commit_updates(c, run, context['task_updates'])
        # Confirmation owner normally creates this for pending actions. Its exact
        # revision check also protects restored confirmations at dispatch.
        c.execute("INSERT INTO confirmations(confirmation_id,session_id,goal_revision,agent_run_id,status,kind,revision) VALUES ('confirmation',?,?,?,'PENDING','INITIAL',1)", (session, run.goal_revision, run.agent_run_id))
        bind_confirmation(c, 'confirmation', session)
        require_current_confirmation(c, 'confirmation')
        c.execute('UPDATE tasks SET revision=revision+1')
        with pytest.raises(ConfirmationConflictError, match='CONFIRMATION_TASK_CHANGED'):
            require_current_confirmation(c, 'confirmation')


def test_task_requirements_remain_pinned_when_history_compacts(kernel_settings):
    from agents.testing import assistant_message
    from kernel.tests.test_memory import _summary
    app, session, model = app_for(kernel_settings, [
        ModelStep(output=[function_call('update_task', update(
            constraints=[dict(id='scope', text='公开资料', source_quote='研究资料')],
            criteria=[dict(id='review', text='说明可用', source_quote='交付说明', kind='USER_REVIEW')], evidence=[]), call_id='task')]),
        ModelStep(output=[_submit('说明。')]),
        ModelStep(output=[assistant_message(json.dumps(_summary()))]),
        ModelStep(output=[_submit('已保留原要求。')]),
    ])
    turn(app, session)
    settings = replace(app.settings, agent_context_window_tokens=7000, agent_max_output_tokens=500)
    app.agent_runtime.settings = settings
    app.agent_runtime.sdk_execution = replace(app.agent_runtime.sdk_execution, settings=settings)
    with connect_database(app.database) as c:
        for revision in range(2, 11):
            for role in ('user', 'assistant'):
                c.execute('INSERT INTO messages(session_id,goal_revision,role,content) VALUES (?,?,?,?)', (session, revision, role, '辅助对话 ' * 75))
        c.execute('UPDATE sessions SET goal_revision=10 WHERE session_id=?', (session,))
    turn(app, session, '继续，保留原要求')
    assert len(model.calls) == 4
    assert model.calls[-2].tools == []
    assert 'TaskContextV1' in model.calls[-1].system_instructions
    assert '公开资料' in model.calls[-1].system_instructions
    assert get_task(app.database, session, app.settings.project_id)['spec']['revision'] == 1


def test_budget_final_reply_does_not_complete_task(kernel_settings):
    from platform_kernel.database import append_event
    from platform_kernel.tasks import refresh_task
    app, session, _ = app_for(kernel_settings, [
        ModelStep(output=[function_call('update_task', update(), call_id='task')]), ModelStep(output=[_submit('说明。')]),
    ])
    run = turn(app, session).session.agent_run_id
    with connect_database(app.database) as c:
        # Remove the earlier completion to exercise a terminal run limited by its
        # real persisted governance fact, independent of its successful reply.
        c.execute('DELETE FROM task_completions')
        append_event(c, session, 'agent.complete', {'agent_run_id': run, 'continuation_stop': 'BUDGET'})
        refresh_task(c, session)
    progress = get_task(app.database, session, app.settings.project_id)['progress']
    assert progress['criteria'][0]['satisfied']
    assert progress['lifecycle'] == 'ACTIVE'
    assert progress['blocking_reasons'] == ['AGENT_BUDGET_EXCEEDED']


@pytest.mark.parametrize('reason,expected_state,expected_reasons', [
    ('EXECUTION_RESULT', 'ACTIVE', []),
    ('EVIDENCE', 'ACTIVE', []),
    ('KERNEL_CASE_FINALIZED', 'ACTIVE', []),
    ('BUDGET', 'BLOCKED', ['AGENT_BUDGET_EXCEEDED']),
    ('DUPLICATE', 'BLOCKED', ['AGENT_OBSERVATION_LOOP']),
])
def test_continuation_stop_only_blocks_for_actual_limits(
    kernel_settings, reason, expected_state, expected_reasons,
):
    from platform_kernel.database import append_event
    from platform_kernel.tasks import refresh_task

    app, session, _ = app_for(kernel_settings, [
        ModelStep(output=[function_call(
            'update_task', update(evidence=[]), call_id='task',
        )]),
        ModelStep(output=[_submit('说明。')]),
    ])
    run = turn(app, session).session.agent_run_id
    with connect_database(app.database) as connection:
        append_event(connection, session, 'agent.complete', {
            'agent_run_id': run, 'continuation_stop': reason,
        })
        refresh_task(connection, session)

    progress = get_task(app.database, session, app.settings.project_id)['progress']
    assert progress['state'] == expected_state
    assert progress['lifecycle'] == 'ACTIVE'
    assert progress['blocking_reasons'] == expected_reasons
    assert progress['criteria'][0]['satisfied'] is False


@pytest.mark.parametrize('message,allowed', [
    ('确认完成：交付说明', True),
    ('不要确认完成：交付说明', False),
    ('示例是“确认完成：交付说明”，我还没有评审。', False),
])
def test_review_requires_user_decision_not_quoted_or_negated_phrase(kernel_settings, message, allowed):
    review = update(expected_revision=1, objective_quote=None, criteria=[], evidence=[dict(criterion_id='review', review_quote='确认完成：交付说明')])
    review_steps = [
        ModelStep(output=[function_call('update_task', review, call_id='review')]),
    ]
    if not allowed:
        review_steps.append(ModelStep(output=[function_call(
            'update_task', review, call_id='review-retry',
        )]))
    app, session, _ = app_for(kernel_settings, [
        ModelStep(output=[function_call('update_task', update(criteria=[dict(id='review', text='业务可用', source_quote='交付说明', kind='USER_REVIEW')], evidence=[]), call_id='create')]),
        ModelStep(output=[_submit('待评审。')]),
        *review_steps,
        ModelStep(output=[_submit('已记录。')]),
    ])
    turn(app, session)
    if allowed:
        turn(app, session, message)
    else:
        with pytest.raises(AgentRunError, match='AGENT_TASK_UPDATE_INVALID'):
            turn(app, session, message)
    task = get_task(app.database, session, app.settings.project_id)
    assert task['spec']['revision'] == (2 if allowed else 1)
    assert (task['progress']['lifecycle'] == 'COMPLETE') is allowed


@pytest.mark.parametrize('message,allowed', [('将研究资料改为公开资料', True), ('不要将研究资料改为公开资料', False)])
def test_user_can_replace_constraint_but_agent_cannot_invert_user_instruction(kernel_settings, message, allowed):
    replacement = update(expected_revision=1, objective_quote=None, criteria=[], constraints=[dict(id='public', text='公开资料', source_quote='公开资料', replaces='scope')], evidence=[])
    replacement_steps = [
        ModelStep(output=[function_call(
            'update_task', replacement, call_id='replace',
        )]),
    ]
    if not allowed:
        replacement_steps.append(ModelStep(output=[function_call(
            'update_task', replacement, call_id='replace-retry',
        )]))
    app, session, _ = app_for(kernel_settings, [
        ModelStep(output=[function_call('update_task', update(constraints=[dict(id='scope', text='研究资料', source_quote='研究资料')]), call_id='create')]),
        ModelStep(output=[_submit('说明。')]),
        *replacement_steps,
        ModelStep(output=[_submit('已更新范围。')]),
    ])
    turn(app, session)
    if allowed:
        turn(app, session, message)
    else:
        with pytest.raises(AgentRunError, match='AGENT_TASK_UPDATE_INVALID'):
            turn(app, session, message)
    spec = get_task(app.database, session, app.settings.project_id)['spec']
    assert spec['constraints'][0]['id'] == ('public' if allowed else 'scope')


def test_new_constraint_reopens_completed_task_without_discarding_successful_evidence(kernel_settings):
    app, session, _ = app_for(kernel_settings, [
        ModelStep(output=[function_call('update_task', update(), call_id='create')]), ModelStep(output=[_submit('说明。')]),
        ModelStep(output=[function_call('update_task', update(expected_revision=1, objective_quote=None, constraints=[dict(id='public', text='仅使用公开资料', source_quote='仅使用公开资料')], criteria=[], evidence=[]), call_id='scope')]),
        ModelStep(output=[_submit('请评审新要求。')]),
        ModelStep(output=[function_call('update_task', update(expected_revision=2, objective_quote=None, criteria=[], evidence=[dict(criterion_id='constraint:public', review_quote='确认完成：仅使用公开资料')]), call_id='review')]),
        ModelStep(output=[_submit('已记录。')]),
    ])
    turn(app, session)
    assert get_task(app.database, session, app.settings.project_id)['progress']['lifecycle'] == 'COMPLETE'
    turn(app, session, '仅使用公开资料')
    progress = get_task(app.database, session, app.settings.project_id)['progress']
    assert progress['state'] == 'WAITING_REVIEW'
    assert next(c for c in progress['criteria'] if c['id'] == 'reply')['satisfied']
    turn(app, session, '确认完成：仅使用公开资料')
    assert get_task(app.database, session, app.settings.project_id)['progress']['lifecycle'] == 'COMPLETE'


def test_task_source_text_is_authoritative_and_interpretation_is_separate(kernel_settings):
    app, session, _ = app_for(kernel_settings, [
        ModelStep(output=[function_call('update_task', update(criteria=[dict(
            id='reply', text='无需交付说明', source_quote='交付说明', kind='REPLY')]), call_id='create')]),
        ModelStep(output=[_submit('说明。')]),
    ])
    turn(app, session)
    criterion = get_task(app.database, session, app.settings.project_id)['spec']['criteria'][0]
    assert criterion['text'] == '交付说明'
    assert criterion['interpretation'] == '无需交付说明'
    assert criterion['source_message_id'] > 0


def test_replacing_criterion_rebinds_steps_and_drops_old_evidence(kernel_settings):
    app, session, _ = app_for(kernel_settings, [
        ModelStep(output=[function_call('update_task', update(steps=[dict(
            id='deliver', title='交付', depends_on=[], criterion_ids=['reply'], gaps=[])]), call_id='create')]),
        ModelStep(output=[_submit('说明。')]),
        ModelStep(output=[function_call('update_task', update(expected_revision=1, objective_quote=None,
            criteria=[dict(id='new', text='交付简报', source_quote='交付简报', kind='REPLY', replaces='reply')],
            evidence=[]), call_id='replace')]),
        ModelStep(output=[_submit('已调整。')]),
    ])
    turn(app, session)
    turn(app, session, '将交付说明改为交付简报')
    task = get_task(app.database, session, app.settings.project_id)
    assert task['spec']['steps'][0]['criterion_ids'] == ['new']
    assert task['progress']['criteria'] == [dict(id='new', satisfied=False, evidence=None)]
    assert task['progress']['lifecycle'] == 'ACTIVE'


def test_task_usage_excludes_unrelated_dialogue_and_links_continuations(kernel_settings):
    app, session, _ = app_for(kernel_settings, [
        ModelStep(output=[function_call('update_task', update(), call_id='create')]), ModelStep(output=[_submit('说明。')]),
        ModelStep(output=[_submit('普通咨询。')]),
        ModelStep(output=[function_call('update_task', {'expected_revision': 1}, call_id='link')]), ModelStep(output=[_submit('继续解释。')]),
    ])
    turn(app, session)
    turn(app, session, '无关问题')
    turn(app, session, '继续解释报告')
    with connect_database(app.database) as c:
        runs = c.execute('SELECT agent_run_id FROM agent_runs WHERE session_id=? ORDER BY goal_revision', (session,)).fetchall()
        for row, tokens in zip(runs, [10, 1000, 20]):
            c.execute('UPDATE agent_runs SET total_tokens=? WHERE agent_run_id=?', (tokens, row[0]))
        assert c.execute('SELECT COUNT(*) FROM task_runs').fetchone()[0] == 2
        assert c.execute('SELECT COUNT(*) FROM task_revisions').fetchone()[0] == 1
    task = get_task(app.database, session, app.settings.project_id)
    assert task['progress']['metrics']['total_tokens'] == 30
    assert task['spec']['revision'] == 1


def test_metadata_only_association_replay_is_durable_after_later_revision(kernel_settings):
    from platform_kernel.tasks import TaskUpdate, commit_updates
    app, session, _ = app_for(kernel_settings, [
        ModelStep(output=[function_call('update_task', update(), call_id='create')]), ModelStep(output=[_submit('说明。')]),
        ModelStep(output=[function_call('update_task', {'expected_revision': 1}, call_id='link')]), ModelStep(output=[_submit('解释。')]),
        ModelStep(output=[function_call('update_task', {'expected_revision': 1, 'constraints': [dict(id='public', text='公开资料', source_quote='公开资料')]}, call_id='revise')]), ModelStep(output=[_submit('已补充。')]),
    ])
    turn(app, session)
    turn(app, session, '继续解释')
    with connect_database(app.database) as c:
        run_id = c.execute('SELECT agent_run_id FROM sessions WHERE session_id=?', (session,)).fetchone()[0]
    run = app.agent_runtime.get(run_id)
    turn(app, session, '公开资料')
    task = get_task(app.database, session, app.settings.project_id)
    with connect_database(app.database) as c:
        commit_updates(c, run, [{'call_id': 'link', 'task_id': task['spec']['task_id'], 'update': TaskUpdate(expected_revision=1).model_dump()}])
        assert c.execute('SELECT COUNT(*) FROM task_runs').fetchone()[0] == 3
    assert get_task(app.database, session, app.settings.project_id)['spec'] == task['spec']


def test_review_parser_matches_whole_entries_and_multiline_quotes():
    from platform_kernel.tasks.persistence import _review_entries
    short = '确认完成：报告'
    long = '确认完成：报告合格'
    multiline = '确认完成：第一项\n第二项'
    assert _review_entries(long, {short, long}) == {long}
    assert _review_entries(multiline + '\n' + long, {short, long, multiline}) == {multiline, long}
    assert _review_entries('不要' + long, {short, long}) == set()
    assert _review_entries(long + '\n但暂未同意', {short, long}) == set()
