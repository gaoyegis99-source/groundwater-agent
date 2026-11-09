import asyncio

import pytest

from platform_kernel.change_signal import CommitSignal
from platform_kernel.database import (
    append_event,
    connect_database,
    initialize_database,
    register_commit_listener,
    unregister_commit_listener,
)


def test_session_event_notifies_only_after_commit(tmp_path):
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    notifications = []

    def listener():
        notifications.append("committed")

    register_commit_listener(database, listener)
    try:
        with connect_database(database) as connection:
            connection.execute(
                "INSERT INTO sessions (session_id, project_id, goal_revision, agent_run_id, pending_agent_run_id, agent_start_state, owner_subject) VALUES ('session','project',0,NULL,NULL,NULL,'local-researcher')"
            )
            append_event(connection, "session", "session.created", {})
            assert notifications == []
        assert notifications == ["committed"]

        with pytest.raises(RuntimeError):
            with connect_database(database) as connection:
                append_event(connection, "session", "turn.accepted", {})
                raise RuntimeError("rollback")
        assert notifications == ["committed"]
    finally:
        unregister_commit_listener(database, listener)


def test_commit_signal_wakes_all_waiters():
    async def exercise():
        signal = CommitSignal()
        signal.bind()
        generation = signal.generation
        first = asyncio.create_task(signal.wait(generation, 1))
        second = asyncio.create_task(signal.wait(generation, 1))
        await asyncio.sleep(0)
        signal.notify()
        results = await asyncio.gather(first, second)
        signal.unbind()
        assert results == [generation + 1, generation + 1]

    asyncio.run(exercise())
