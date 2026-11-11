import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from threading import Barrier

from agents.testing import ModelStep
import pytest

from kernel.tests.test_tasks import app_for, turn
from kernel.tests.reply_testing import submit_reply as _submit
from platform_kernel.database import connect_database, initialize_database
from platform_kernel.errors import ConfirmationConflictError
from platform_kernel.interaction.control_persistence import (
    claim_controls,
    finalize_stop,
    release_control,
    request_stop,
    validate_correction,
)
import platform_kernel.interaction.control_persistence as control_persistence
from platform_kernel.interaction.persistence import create_session
from platform_kernel.agent_runtime.preview import PreviewHub, ReplyPreview


def _finalize(database, control):
    claimed = claim_controls(database, "test-control-worker", limit=32)
    claim = next(
        item
        for item in claimed
        if item["session_id"] == control["session_id"]
        and item["request_id"] == control["request_id"]
    )
    return finalize_stop(database, claim)


def _control_store(tmp_path, count: int = 1):
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    create_session(database, "session", "project")
    with connect_database(database) as connection:
        for index in range(count):
            connection.execute(
                """INSERT INTO run_controls(
                    session_id,request_id,agent_run_id,requested_by,status,created_at
                ) VALUES (?,?,?,?,?,?)""",
                (
                    "session",
                    f"request-{index:03d}",
                    f"run-{index:03d}",
                    "operator",
                    "REQUESTED",
                    f"2026-10-24T00:00:{index:02d}.000Z",
                ),
            )
    return database


def test_control_claims_are_fifo_bounded_and_do_not_scan_an_unbounded_batch(tmp_path):
    database = _control_store(tmp_path, 40)

    first = claim_controls(database, "worker-a", limit=32)
    second = claim_controls(database, "worker-b", limit=32)

    assert [item["request_id"] for item in first] == [
        f"request-{index:03d}" for index in range(32)
    ]
    assert [item["request_id"] for item in second] == [
        f"request-{index:03d}" for index in range(32, 40)
    ]
    assert all(item["claim_generation"] == 1 for item in (*first, *second))


@pytest.mark.parametrize("state", ["empty", "COMPLETE", "PROCESSING"])
def test_idle_control_poll_does_not_wait_for_an_unrelated_writer(
    tmp_path, monkeypatch, state
):
    database = _control_store(tmp_path, count=0 if state == "empty" else 1)
    with connect_database(database) as connection:
        connection.execute(
            "UPDATE run_controls SET status=?,claim_until='2999-01-01T00:00:00.000Z'",
            (state,),
        )

    @contextmanager
    def short_timeout(path):
        with connect_database(path) as connection:
            connection.execute("PRAGMA busy_timeout=50")
            yield connection

    monkeypatch.setattr(control_persistence, "connect_database", short_timeout)
    # Auth and workflow writes share this WAL store. An idle control poll is
    # read-only and must keep returning while another writer owns its lock.
    with connect_database(database) as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE sessions SET title='uncommitted' WHERE session_id='session'")
        assert claim_controls(database, "dispatcher") == ()


def test_concurrent_control_claimers_recheck_candidates_under_the_write_lock(
    tmp_path, monkeypatch
):
    database = _control_store(tmp_path)
    contenders = Barrier(2)

    class PausedConnection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, *args):
            if sql == "BEGIN IMMEDIATE":
                contenders.wait(timeout=5)
            return self.connection.execute(sql, *args)

    @contextmanager
    def concurrent_connection(path):
        with connect_database(path) as connection:
            yield PausedConnection(connection)

    monkeypatch.setattr(control_persistence, "connect_database", concurrent_connection)
    with ThreadPoolExecutor(max_workers=2) as workers:
        claims = tuple(workers.map(lambda owner: claim_controls(database, owner), ("a", "b")))

    claimed = [item for batch in claims for item in batch]
    assert len(claimed) == 1
    assert claimed[0]["claim_generation"] == 1


def test_control_claim_lease_recovery_fences_an_old_worker(tmp_path):
    database = _control_store(tmp_path)
    first, = claim_controls(database, "worker-a", limit=1)
    with connect_database(database) as connection:
        connection.execute(
            "UPDATE run_controls SET claim_until='2000-01-01T00:00:00.000Z'"
        )

    replacement, = claim_controls(database, "worker-b", limit=1)

    assert replacement["claim_generation"] == first["claim_generation"] + 1
    assert not release_control(database, first)
    with connect_database(database) as connection:
        row = connection.execute(
            "SELECT status,claim_owner,claim_generation FROM run_controls"
        ).fetchone()
    assert tuple(row) == ("PROCESSING", "worker-b", replacement["claim_generation"])


def test_stop_is_durable_idempotent_and_fences_pending_start(kernel_settings):
    app, session, _ = app_for(kernel_settings, [])
    receipt = app.interaction.message(session, 'm', '请分析', 'DIALOGUE')
    target = receipt['agent_run_id']
    first = request_stop(app.database, session, app.settings.project_id, 'stop', target)
    assert request_stop(app.database, session, app.settings.project_id, 'stop', target)['status'] == 'REQUESTED'
    with pytest.raises(ConfirmationConflictError):
        request_stop(app.database, session, app.settings.project_id, 'stop', 'wrong')
    assert _finalize(app.database, first)
    assert app.interaction.requested_starts() == ()
    with connect_database(app.database) as c:
        assert c.execute('SELECT COUNT(*) FROM agent_runs').fetchone()[0] == 0
        assert c.execute('SELECT status FROM run_controls').fetchone()[0] == 'COMPLETE'
    assert request_stop(app.database, session, app.settings.project_id, 'stop', target)['status'] == 'COMPLETE'
    corrected = app.interaction.message(session, 'correction', '改为说明方法', 'DIALOGUE', corrects_agent_run_id=target)
    assert corrected['corrects_agent_run_id'] == target
    assert app.interaction.message(session, 'correction', '改为说明方法', 'DIALOGUE', corrects_agent_run_id=target) == corrected


def test_stop_preserves_completed_reply_and_correction_identity(kernel_settings):
    app, session, _ = app_for(kernel_settings, [ModelStep(output=[_submit('说明。')])])
    run = turn(app, session).session.agent_run_id
    assert _finalize(app.database, request_stop(app.database, session, app.settings.project_id, 'stop', run))
    assert app.agent_runtime.get(run).status == 'COMPLETE'
    with connect_database(app.database) as c:
        validate_correction(c, session, 'next', run)
        validate_correction(c, session, 'next', run, replay=True)
        with pytest.raises(ConfirmationConflictError):
            validate_correction(c, session, 'next', None, replay=True)
        with pytest.raises(ConfirmationConflictError):
            validate_correction(c, session, 'original', run, replay=True)


def test_preview_has_no_replay_and_discards_old_fragments():
    async def check():
        hub = PreviewHub()
        hub.publish('s', 'run', 'old secret')
        async with hub.subscribe('s') as queue:
            assert queue.empty()
            hub.publish('other', 'run', 'other project')
            assert queue.empty()
            hub.publish('s', 'run', 'partial')
            hub.publish('s', 'run', None)
            value = await queue.get()
            assert value['text'] is None
        assert hub._subscribers == {}
    asyncio.run(check())


def test_preview_only_extracts_reply_text():
    from types import SimpleNamespace as N
    seen = []
    preview = ReplyPreview(seen.append)
    def event(data):
        preview.feed(N(type='raw_response_event', data=data))
    event(N(type='response.output_item.added', item=N(type='function_call', name='update_task', id='other')))
    event(N(type='response.function_call_arguments.delta', item_id='other', delta='{"conclusion":{"text":"private"}}'))
    assert not seen
    event(N(type='response.output_item.added', item=N(type='function_call', name='submit_reply', id='reply')))
    event(N(type='response.function_call_arguments.delta', item_id='reply', delta='{"conclusion":{"text":"正在'))
    assert seen == ['正在']
    event(N(type='response.function_call_arguments.delta', item_id='reply', delta='分析","citations":[]}}'))
    assert seen[-1] == '正在分析'


def test_streaming_still_validates_and_persists_final_reply(kernel_settings):
    app, session, _ = app_for(kernel_settings, [ModelStep(output=[_submit('流式说明。')])])
    seen = []
    app.agent_runtime.sdk_execution = replace(app.agent_runtime.sdk_execution, publish_preview=lambda *args: seen.append(args))
    result = turn(app, session)
    assert result.session.agent_output.message == '流式说明。'
    assert seen[-1][2] is None
    with connect_database(app.database) as c:
        assert not c.execute("SELECT 1 FROM session_events WHERE event_type LIKE 'preview.%'").fetchone()


def test_stop_fences_late_reply_and_staged_metadata_before_supervisor_tick(kernel_settings):
    from agents.testing import function_call
    from kernel.tests.test_tasks import update, GOAL
    from platform_kernel.tasks import get_task
    async def exercise():
        ready, release = asyncio.Event(), asyncio.Event()
        async def late(_call):
            ready.set()
            await release.wait()
            return ModelStep(output=[_submit('迟到的回复')])
        app, session, _ = app_for(kernel_settings, [
            ModelStep(output=[function_call('update_task', update(), call_id='staged')]), ModelStep.respond(late),
        ])
        work = asyncio.create_task(app.interaction.turn(session, GOAL))
        await asyncio.wait_for(ready.wait(), 3)
        run_id = app.interaction.messages(session, None, 50)['turns'][-1]['agent_run_id']
        control = request_stop(app.database, session, app.settings.project_id, 'stop', run_id)
        release.set()
        await work
        assert app.agent_runtime.get(run_id).status == 'CANCELLED'
        assert get_task(app.database, session, app.settings.project_id) is None
        assert _finalize(app.database, control)
        with connect_database(app.database) as c:
            assert not c.execute("SELECT 1 FROM messages WHERE role='assistant'").fetchone()
            assert not c.execute('SELECT 1 FROM confirmations').fetchone()
    asyncio.run(exercise())


@pytest.mark.parametrize('publish_wins', [False, True])
def test_executor_stop_uses_final_publication_fact(kernel_settings, publish_wins):
    from kernel.tests.test_workflow_progress import _batch
    from platform_kernel.contracts import JobStatusV6, DataVersionRefV2
    from platform_kernel.workflow_execution.owner import WorkflowExecutionOwner
    from platform_kernel.workflow_execution.models import DispatchWork
    from platform_kernel.workflow_execution.persistence import get_run
    from platform_kernel.interaction.control_persistence import ControlRequests
    settings = kernel_settings()
    database, (workflow,) = _batch(settings, count=1)
    with connect_database(database) as c:
        c.execute("UPDATE sessions SET goal_revision=1,agent_run_id='agent' WHERE session_id='session'")
    control = request_stop(database, 'session', 'p', 'stop', 'agent')
    assert not _finalize(database, control)
    published = DataVersionRefV2(
        kind='data_version', service='data-primary', dataset_id='dataset-results',
        version_id='result', semantic_id='semantics-results', object_id='object-result',
        media_type='application/json', format_id='json', size_bytes=4,
        checksum='sha256:' + 'b' * 64, output_name='summary',
    )
    class Executor:
        cancelled = False
        async def get(self, service, job_id):
            status = 'SUCCEEDED' if self.cancelled and publish_wins else 'CANCELLED' if self.cancelled else 'RUNNING'
            return JobStatusV6(job_id='job', request_id=workflow.run_id, status=status, outputs=(published,) if status == 'SUCCEEDED' else (), error=None)
        async def cancel(self, service, job_id):
            self.cancelled = True
    executor = Executor()
    owner = WorkflowExecutionOwner(database, settings.models, settings.services, executor, controls=ControlRequests(database))
    active = replace(workflow, remote_job_id='job')
    assert asyncio.run(owner._drive(DispatchWork(active, 'SUBMITTED', '2099-01-01T00:00:00Z')))
    saved = get_run(database, 'p', workflow.run_id)
    assert saved.status == ('SUCCEEDED' if publish_wins else 'CANCELLED')
    assert bool(saved.outputs) is publish_wins
    assert _finalize(database, control)
    assert get_run(database, 'p', workflow.run_id).outputs == saved.outputs


def test_stop_never_resubmits_an_unknown_submission(kernel_settings):
    from kernel.tests.test_workflow_progress import _batch
    from platform_kernel.workflow_execution.owner import WorkflowExecutionOwner
    from platform_kernel.workflow_execution.models import DispatchWork
    from platform_kernel.interaction.control_persistence import ControlRequests
    settings = kernel_settings()
    database, (workflow,) = _batch(settings, count=1)
    with connect_database(database) as c:
        c.execute("UPDATE sessions SET agent_run_id='agent' WHERE session_id='session'")
    request_stop(database, 'session', 'p', 'stop', 'agent')
    class Executor:
        async def get_by_request(self, *args):
            return None
        async def submit(self, *args):
            pytest.fail('stopped work must not be submitted')
    owner = WorkflowExecutionOwner(database, settings.models, settings.services, Executor(), controls=ControlRequests(database))
    assert not asyncio.run(owner._drive(DispatchWork(workflow, 'SUBMIT_UNKNOWN', '2099-01-01T00:00:00Z')))
    assert asyncio.run(owner._drive(DispatchWork(workflow, 'SUBMIT_UNKNOWN', '2000-01-01T00:00:00Z')))
    assert owner.get('p', workflow.run_id).status == 'CANCELLED'


def test_durable_stop_projects_an_admitted_run_before_cancelling_its_coroutine(kernel_settings):
    from platform_kernel.agent_runtime.persistence import create_run
    from platform_kernel.database import utc_now
    app, session, _ = app_for(kernel_settings, [])
    receipt = app.interaction.message(session, 'message', '请分析', 'DIALOGUE')
    run_id = receipt['agent_run_id']
    create_run(app.database, agent_run_id=run_id, session_id=session,
               project_id=app.settings.project_id, goal_revision=1, provider='test',
               model='test', trace_id='trace', started_at=utc_now())
    assert app.interaction.get(session).session.agent_run_id is None
    control = request_stop(app.database, session, app.settings.project_id, 'stop', run_id)
    assert _finalize(app.database, control)
    snapshot = app.interaction.get(session)
    assert snapshot.session.agent_run_id == run_id
    assert snapshot.agent_steps[-1] == 'CANCELLED'
    assert snapshot.session.agent_output is None


@pytest.mark.parametrize('payload', [
    '{"conclusion":{"citations":[],"text":"正文"}}',
    '{"details":[{"text":"不应展示"}],"conclusion":{"citations":[{"text":"引用"}],"text":"正文\\n\\u4e2d\\\"文"}}',
])
def test_preview_accepts_reordered_fields_and_arbitrary_fragments(payload):
    import json
    from types import SimpleNamespace as N
    from platform_kernel.agent_runtime.preview import ReplyPreview
    for width in (1, 2, 7, len(payload)):
        output = []
        preview = ReplyPreview(output.append)
        preview.feed(N(type='raw_response_event', data=N(type='response.output_item.added',
            item=N(type='function_call', name='submit_reply', id='reply'))))
        for start in range(0, len(payload), width):
            preview.feed(N(type='raw_response_event', data=N(type='response.function_call_arguments.delta',
                item_id='reply', delta=payload[start:start + width])))
        assert output[-1] == json.loads(payload)['conclusion']['text']
        assert all('不应展示' not in value and '引用' not in value for value in output)


def test_preview_bounds_and_tool_call_isolation():
    from types import SimpleNamespace as N
    seen = []
    preview = ReplyPreview(seen.append)
    def start(identifier, name='submit_reply'):
        preview.feed(N(type='raw_response_event', data=N(type='response.output_item.added',
            item=N(type='function_call', name=name, id=identifier))))
    def delta(identifier, text):
        preview.feed(N(type='raw_response_event', data=N(type='response.function_call_arguments.delta',
            item_id=identifier, delta=text)))
    start('other', 'inspect_resource')
    delta('other', '{"conclusion":{"text":"不应展示"}}')
    assert seen == []
    start('one')
    delta('one', '{"conclusion":{"text":"第一段')
    start('two')
    delta('two', '{"conclusion":{"citations":[],"text":"第二段"}}')
    assert seen[-1] == '第二段'
    delta('one', 'x' * 65000)
    assert seen[-1] == '第二段'
    assert len(preview.parsers['one'].token) <= 64000


def test_preview_does_not_publish_unchanged_text_for_later_fields():
    from types import SimpleNamespace as N
    from platform_kernel.agent_runtime.preview import ReplyPreview
    seen = []
    preview = ReplyPreview(seen.append)
    preview.feed(N(type='raw_response_event', data=N(type='response.output_item.added',
        item=N(type='function_call', name='submit_reply', id='reply'))))
    preview.feed(N(type='raw_response_event', data=N(type='response.function_call_arguments.delta',
        item_id='reply', delta='{"conclusion":{"text":"简要结论"}')))
    count = len(seen)
    preview.feed(N(type='raw_response_event', data=N(type='response.function_call_arguments.delta',
        item_id='reply', delta=',"details":[{"text":"详细说明"}]}')))
    assert seen[-1] == '简要结论'
    assert len(seen) == count


def test_preview_stops_at_excessive_nesting_without_unbounded_paths():
    from platform_kernel.agent_runtime.preview import _TextParser
    parser = _TextParser()
    assert parser.feed('[' * 10000) is None
    assert parser.disabled
    assert len(parser.stack) == 64
    assert all(frame['path'] is None or len(frame['path']) <= 2 for frame in parser.stack)
