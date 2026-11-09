from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable


TaskFactory = Callable[[], Awaitable[None]]


class KernelTaskSupervisor:
    """Bounded owner for Kernel background tasks.

    Queueing stores factories rather than coroutine objects, so work that is
    waiting for admission does not allocate an asyncio Task and cannot emit
    un-awaited coroutine warnings during shutdown.
    """

    def __init__(self, limit: int, max_queue: int = 100) -> None:
        if limit < 1:
            raise ValueError("KERNEL_TASK_CONCURRENCY_INVALID")
        self.limit = limit
        if max_queue < 1:
            raise ValueError("KERNEL_TASK_QUEUE_INVALID")
        self.max_queue = max_queue
        self._active: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._queued: dict[tuple[str, str], TaskFactory] = {}
        self._order: deque[tuple[str, str]] = deque()
        self._closed = False
        self._rejected: dict[str, int] = {}

    @property
    def available(self) -> int:
        return max(0, self.limit - len(self._active))

    def contains(self, kind: str, key: str) -> bool:
        identity = (kind, key)
        return identity in self._active or identity in self._queued

    def queue(
        self, kind: str, key: str, factory: TaskFactory, *, priority: bool = False
    ) -> bool:
        identity = (kind, key)
        if self._closed or self.contains(kind, key) or len(self._queued) >= self.max_queue:
            self._rejected[kind] = self._rejected.get(kind, 0) + 1
            return False
        self._queued[identity] = factory
        if priority:
            self._order.appendleft(identity)
        else:
            self._order.append(identity)
        return True

    def pump(self) -> None:
        if self._closed:
            return
        while self.available and self._order:
            identity = self._order.popleft()
            factory = self._queued.pop(identity, None)
            if factory is None:
                continue
            self._active[identity] = asyncio.create_task(
                factory(), name=f"kernel:{identity[0]}:{identity[1]}"
            )

    async def cancel(self, kind: str, key: str) -> None:
        identity = (kind, key)
        self._queued.pop(identity, None)
        task = self._active.pop(identity, None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.pump()

    def reap(self) -> None:
        completed = [
            (identity, task)
            for identity, task in self._active.items()
            if task.done()
        ]
        for identity, task in completed:
            del self._active[identity]
            task.result()

    def failed(self) -> bool:
        return any(
            task.done() and not task.cancelled() and task.exception() is not None
            for task in self._active.values()
        )

    def counts(self) -> dict[str, int]:
        counts = {"route": 0, "start": 0, "resume": 0, "decision": 0}
        for kind, _key in self._active:
            counts[kind] = counts.get(kind, 0) + 1
        return counts

    def metrics(self) -> dict[str, dict[str, int]]:
        """Return active, queued, and refused admissions by task kind.

        ``counts()`` remains the narrow active-task compatibility surface used by
        existing health callers.  Operators need the other two dimensions to
        distinguish a healthy busy worker from a growing backlog.
        """

        kinds = {"route", "start", "resume", "decision"}
        kinds.update(kind for kind, _key in self._queued)
        kinds.update(kind for kind, _key in self._active)
        kinds.update(self._rejected)
        active = {kind: 0 for kind in sorted(kinds)}
        queued = {kind: 0 for kind in sorted(kinds)}
        rejected = {kind: self._rejected.get(kind, 0) for kind in sorted(kinds)}
        for kind, _key in self._active:
            active[kind] += 1
        for kind, _key in self._queued:
            queued[kind] += 1
        return {"active": active, "queued": queued, "rejected_total": rejected}

    async def close(self, *, grace_seconds: float = 30) -> None:
        """Stop admissions and bound shutdown even when a provider hangs.

        The durable Kernel state is deliberately not changed here: cancelled
        in-process work is recovered from that state on the next startup.
        """

        self._closed = True
        self._queued.clear()
        self._order.clear()
        tasks = tuple(self._active.values())
        if tasks:
            _done, pending = await asyncio.wait(tasks, timeout=grace_seconds)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            for task in tasks:
                if not task.cancelled():
                    task.result()
        self._active.clear()
