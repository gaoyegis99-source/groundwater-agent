from __future__ import annotations

import asyncio
from threading import Lock


class CommitSignal:
    """Broadcast a process-local hint after durable Kernel state changes."""

    def __init__(self) -> None:
        self._generation = 0
        self._lock = Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._waiters: set[asyncio.Future[int]] = set()

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def bind(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise RuntimeError("COMMIT_SIGNAL_ALREADY_BOUND")
        self._loop = loop

    def unbind(self) -> None:
        self._loop = None
        for waiter in self._waiters:
            if not waiter.done():
                waiter.cancel()
        self._waiters.clear()

    def notify(self) -> None:
        with self._lock:
            self._generation += 1
            generation = self._generation
            loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(self._wake, generation)

    async def wait(self, generation: int, timeout: float) -> int:
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._generation != generation:
                return self._generation
            waiter: asyncio.Future[int] = loop.create_future()
            self._waiters.add(waiter)
        try:
            return await asyncio.wait_for(asyncio.shield(waiter), timeout)
        except TimeoutError:
            return self.generation
        finally:
            self._waiters.discard(waiter)
            if not waiter.done():
                waiter.cancel()

    def _wake(self, generation: int) -> None:
        waiters = tuple(self._waiters)
        self._waiters.clear()
        for waiter in waiters:
            if not waiter.done():
                waiter.set_result(generation)
