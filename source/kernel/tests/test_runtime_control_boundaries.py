import asyncio
from contextlib import ExitStack, contextmanager
from dataclasses import replace
import sqlite3

from agents.testing import ModelStep, ScriptedModel, assistant_message
import httpx
import pytest

from kernel.tests.reply_testing import submit_reply
from kernel.tests.test_tasks import app_for
from platform_kernel.composition import PlatformApplication
from platform_kernel.database import connect_database
import platform_kernel.interaction.control_persistence as controls
import platform_kernel.workflow_execution.persistence.queue as workflow_queue


def _application(kernel_settings):
    model = ScriptedModel([ModelStep(output=[submit_reply("Existing reply")])])
    router = ScriptedModel([ModelStep(output=[assistant_message("CONSULT|NONE|NONE")])])
    app = PlatformApplication(
        settings=replace(kernel_settings("knowledge"), agent_shutdown_grace_seconds=0.01),
        model=model, intent_router_model=router,
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: httpx.Response(500))),
    )
    return app, app.interaction.create_session().session.session_id, model, router


@pytest.mark.parametrize("target, phase, mode", [
    ("idle", "claim", "DIALOGUE"), ("pending", "claim", "DIALOGUE"),
    ("complete", "claim", "DIALOGUE"), ("pending", "finalize", "DIALOGUE"),
    ("complete", "finalize", "DIALOGUE"), ("pending", "expired", "DIALOGUE"),
    ("pending", "expired", "AUTO"),
])
def test_runtime_survives_contended_stop_and_finishes_it_after_writer_releases(
    kernel_settings, monkeypatch, target, phase, mode
):
    async def exercise():
        app, session, model, router = _application(kernel_settings)
        attempted = asyncio.Event()
        if target == "complete":
            run_id = (await app.interaction.turn(session, "Explain the method")).session.agent_run_id
        await app.start()
        if target == "pending":
            run_id = app.interaction.message(session, "message", "Explain the method", mode)["agent_run_id"]
        if target != "idle":
            controls.request_stop(app.database, session, app.settings.project_id, "stop", run_id)

        @contextmanager
        def short_wait(path):
            with connect_database(path) as connection:
                connection.execute("PRAGMA busy_timeout=20")
                connection.set_trace_callback(
                    lambda statement: attempted.set() if statement == "BEGIN IMMEDIATE" else None
                )
                yield connection

        monkeypatch.setattr(controls, "connect_database", short_wait)
        monkeypatch.setattr(workflow_queue, "connect_database", short_wait)
        writer_scope = ExitStack()

        def hold_writer():
            writer = writer_scope.enter_context(connect_database(app.database))
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("UPDATE sessions SET title='another writer' WHERE session_id=?", (session,))
            if phase == "expired":
                writer.execute("UPDATE run_controls SET claim_until='2000-01-01T00:00:00.000Z'")

        if phase != "claim":
            original_claim = app.runtime.controls.claim

            def claim_before_contention(**kwargs):
                batch = original_claim(**kwargs)
                if batch:
                    hold_writer()
                    monkeypatch.setattr(app.runtime.controls, "claim", original_claim)
                return batch

            monkeypatch.setattr(app.runtime.controls, "claim", claim_before_contention)
        try:
            # Real contention before claim or after its durable lease is saved.
            # Shorten SQLite's wait, without fabricating a database exception.
            if phase == "claim":
                hold_writer()
            try:
                await asyncio.wait_for(attempted.wait(), 2)
                await asyncio.sleep(0)
                assert app.runtime.is_ready(), (
                    "a temporary writer killed the dispatcher: "
                    f"{app.runtime._task.exception()!r}"
                )
                if target != "idle":
                    with connect_database(app.database) as reader:
                        assert reader.execute("SELECT status FROM run_controls").fetchone()[0] == (
                            "REQUESTED" if phase == "claim" else "PROCESSING"
                        )
            finally:
                writer_scope.close()
            if target == "idle":
                run_id = app.interaction.message(session, "message", "Explain the method", "DIALOGUE")["agent_run_id"]
                controls.request_stop(app.database, session, app.settings.project_id, "stop", run_id)
            app.commit_signal.notify()

            async def completed():
                while True:
                    with connect_database(app.database) as connection:
                        if connection.execute("SELECT status FROM run_controls").fetchone()[0] == "COMPLETE":
                            return
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(completed(), 2)
            assert app.runtime.is_ready()
            with connect_database(app.database) as connection:
                assert connection.execute("SELECT COUNT(*) FROM session_events WHERE event_type='control.complete'").fetchone()[0] == 1
                assert connection.execute("SELECT claim_generation FROM run_controls").fetchone()[0] == (
                    2 if phase == "expired" else 1
                )
            if target != "complete":
                assert app.agent_runtime.find(run_id) is None
                assert len(model.calls) == 0
            else:
                assert app.agent_runtime.get(run_id).status == "COMPLETE"
            assert len(router.calls) == 0
        finally:
            closed = await asyncio.gather(app.close(), return_exceptions=True)
            await app.client.aclose()
        assert closed == [None]

    asyncio.run(exercise())


def test_runtime_finishes_the_rest_of_a_claimed_batch_after_a_writer_interrupts(
    kernel_settings, monkeypatch
):
    async def exercise():
        app, first_session, model, router = _application(kernel_settings)
        sessions = (first_session, app.interaction.create_session().session.session_id)
        await app.start()
        run_ids = []
        for session in sessions:
            run_id = app.interaction.message(session, "message", "Explain the method", "DIALOGUE")["agent_run_id"]
            run_ids.append(run_id)
            controls.request_stop(app.database, session, app.settings.project_id, "stop", run_id)
        writer_scope = ExitStack()
        writer_acquired = asyncio.Event()
        original_finalize = app.runtime.controls.finalize

        def finalize_before_contention(control):
            result = original_finalize(control)
            writer = writer_scope.enter_context(connect_database(app.database))
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("UPDATE sessions SET title='another writer' WHERE session_id=?", (first_session,))
            writer_acquired.set()
            monkeypatch.setattr(app.runtime.controls, "finalize", original_finalize)
            return result

        monkeypatch.setattr(app.runtime.controls, "finalize", finalize_before_contention)
        try:
            await asyncio.wait_for(writer_acquired.wait(), 2)
            assert app.runtime.is_ready()
            with connect_database(app.database) as connection:
                assert sorted(row[0] for row in connection.execute("SELECT status FROM run_controls")) == ["COMPLETE", "PROCESSING"]
            writer_scope.close()
            app.commit_signal.notify()

            async def completed():
                while True:
                    with connect_database(app.database) as connection:
                        if connection.execute("SELECT COUNT(*) FROM run_controls WHERE status='COMPLETE'").fetchone()[0] == 2:
                            return
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(completed(), 2)
            assert app.runtime.is_ready()
            assert len(model.calls) == len(router.calls) == 0
            assert all(app.agent_runtime.find(run_id) is None for run_id in run_ids)
            with connect_database(app.database) as connection:
                assert [row[0] for row in connection.execute("SELECT claim_generation FROM run_controls")] == [1, 1]
                assert connection.execute("SELECT COUNT(*) FROM session_events WHERE event_type='control.complete'").fetchone()[0] == 2
        finally:
            writer_scope.close()
            await app.close()
            await app.client.aclose()

    asyncio.run(exercise())


@pytest.mark.parametrize("mode", ["DIALOGUE", "AUTO"])
def test_stop_fences_a_start_or_route_claimed_from_an_earlier_inventory(kernel_settings, mode):
    async def exercise():
        app, session, model, router = _application(kernel_settings)
        run_id = app.interaction.message(session, "message", "Explain the method", mode)["agent_run_id"]
        if mode == "DIALOGUE":
            assert app.interaction.requested_starts() == (session,)
        else:
            assert [item.agent_run_id for item in app.interaction.requested_routes()] == [run_id]
        controls.request_stop(app.database, session, app.settings.project_id, "stop", run_id)
        try:
            # A scheduler can retain its earlier inventory. Admission itself
            # must recheck the stop after that inventory has become stale.
            if mode == "DIALOGUE":
                await app.interaction.resume_pending_start(session)
            else:
                await app.runtime._run_route(run_id)
            assert len(model.calls) == len(router.calls) == 0
            assert app.agent_runtime.find(run_id) is None
            assert app.interaction.requested_starts() == ()
            assert app.interaction.requested_routes() == ()
            with connect_database(app.database) as connection:
                assert connection.execute("SELECT status FROM run_controls").fetchone()[0] == "REQUESTED"
        finally:
            await app.close()
            await app.client.aclose()

    asyncio.run(exercise())


@pytest.mark.parametrize("mode", ["DIALOGUE", "AUTO"])
def test_new_turn_waits_for_old_cancellation_without_being_cancelled(kernel_settings, mode):
    async def exercise():
        entered, cancelling, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def old_request(_call):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelling.set()
                await release.wait()
                raise

        new_reply = ModelStep(output=[submit_reply("New reply")])
        model = ScriptedModel(
            [ModelStep.respond(old_request), new_reply] if mode == "DIALOGUE" else [new_reply]
        )
        router = ScriptedModel([ModelStep.respond(old_request)])
        app = PlatformApplication(
            settings=replace(kernel_settings("knowledge"), agent_shutdown_grace_seconds=0.01),
            model=model, intent_router_model=router,
            client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: httpx.Response(500))),
        )
        session = app.interaction.create_session().session.session_id
        await app.start()
        old_run = app.interaction.message(session, "old", "Old question", mode)["agent_run_id"]
        try:
            await asyncio.wait_for(entered.wait(), 2)
            controls.request_stop(app.database, session, app.settings.project_id, "stop-old", old_run)
            await asyncio.wait_for(cancelling.wait(), 2)
            with connect_database(app.database) as connection:
                assert connection.execute("SELECT status FROM run_controls").fetchone()[0] == "COMPLETE"

            # A real new message can arrive while cancellation awaits provider
            # cleanup. The single dispatcher admits it after that cleanup.
            new_run = app.interaction.message(session, "new", "New question", "DIALOGUE")["agent_run_id"]
            await asyncio.sleep(0)
            assert app.interaction.requested_starts() == (session,)
            assert app.agent_runtime.find(new_run) is None
            release.set()

            async def completed():
                while (run := app.agent_runtime.find(new_run)) is None or run.status != "COMPLETE":
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(completed(), 2)
            assert app.runtime.is_ready()
            assert app.interaction.get(session).session.agent_run_id == new_run
            assert len(model.calls) == (2 if mode == "DIALOGUE" else 1)
            assert len(router.calls) == (1 if mode == "AUTO" else 0)
            if mode == "DIALOGUE":
                assert app.agent_runtime.get(old_run).status == "CANCELLED"
            else:
                assert app.agent_runtime.find(old_run) is None
        finally:
            release.set()
            await app.close()
            await app.client.aclose()

    asyncio.run(exercise())


def test_dispatcher_does_not_hide_a_non_contention_sqlite_failure(kernel_settings):
    async def exercise():
        app, _session, _model = app_for(kernel_settings, [])
        await app.start()
        with connect_database(app.database) as connection:
            connection.execute("DROP TABLE workflow_dispatches")
        try:
            await asyncio.sleep(0)
            assert not app.runtime.is_ready()
            error = app.runtime._task.exception()
            assert isinstance(error, sqlite3.OperationalError)
            assert error.sqlite_errorcode == sqlite3.SQLITE_ERROR
            assert "no such table" in str(error)
        finally:
            closed = await asyncio.gather(app.close(), return_exceptions=True)
            await app.client.aclose()
        assert closed == [error]

    asyncio.run(exercise())


def test_stale_control_lease_cannot_cancel_a_new_turn_in_the_same_session(
    kernel_settings, monkeypatch
):
    async def exercise():
        entered, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def new_reply(_call):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return ModelStep(output=[submit_reply("New reply")])

        app, session, _ = app_for(
            kernel_settings, [ModelStep.respond(new_reply)],
            agent_shutdown_grace_seconds=0.01,
        )
        old_run = app.interaction.message(session, "old", "Old question", "DIALOGUE")["agent_run_id"]
        controls.request_stop(app.database, session, app.settings.project_id, "stop-old", old_run)
        old_claim, = controls.claim_controls(app.database, "old-worker")
        with connect_database(app.database) as connection:
            connection.execute("UPDATE run_controls SET claim_until='2000-01-01T00:00:00.000Z'")
        replacement, = controls.claim_controls(app.database, "replacement-worker")
        assert controls.finalize_stop(app.database, replacement)
        new_run = app.interaction.message(session, "new", "New question", "DIALOGUE")["agent_run_id"]
        app.runtime._schedule_start(session)
        app.runtime._tasks.pump()
        monkeypatch.setattr(app.runtime.controls, "claim", lambda **_kwargs: (old_claim,))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            await app.runtime._process_controls()
            assert not cancelled.is_set(), "an obsolete lease cancelled the new turn"
            assert app.runtime._tasks.contains("start", session)
            release.set()
            await app.runtime._tasks.close(grace_seconds=2)
            assert app.agent_runtime.get(new_run).status == "COMPLETE"
            assert app.interaction.get(session).session.agent_run_id == new_run
            with connect_database(app.database) as connection:
                assert connection.execute("SELECT claim_generation FROM run_controls").fetchone()[0] == replacement["claim_generation"]
                assert connection.execute("SELECT COUNT(*) FROM session_events WHERE event_type='control.complete'").fetchone()[0] == 1
        finally:
            release.set()
            await app.close()
            await app.client.aclose()

    asyncio.run(exercise())
