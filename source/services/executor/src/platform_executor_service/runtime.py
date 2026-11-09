from __future__ import annotations

import asyncio
import logging

import httpx

from .control import ExecutorControl
from .database import is_sqlite_busy_error
from .results import ResultPublisher

logger = logging.getLogger(__name__)


class ExecutorRuntime:
    def __init__(
        self,
        control: ExecutorControl,
        publisher: ResultPublisher,
        owned_client: httpx.Client | None,
    ) -> None:
        self.control = control
        self.publisher = publisher
        self.owned_client = owned_client
        self._stop = asyncio.Event()
        self._supervisor: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self.control.recover()
        self._stop.clear()
        self._supervisor = asyncio.create_task(self._run_supervisor())

    async def close(self) -> None:
        self._stop.set()
        try:
            if self._supervisor is not None:
                await self._supervisor
        finally:
            if self.owned_client is not None:
                self.owned_client.close()

    async def _run_supervisor(self) -> None:
        async def loop(operation):
            consecutive_busy = 0
            while not self._stop.is_set():
                try:
                    await asyncio.to_thread(operation)
                except BaseException as error:
                    if not is_sqlite_busy_error(error):
                        raise
                    consecutive_busy += 1
                    logger.warning(
                        "executor supervisor operation deferred: sqlite busy",
                        extra={
                            "operation": getattr(operation, "__name__", "unknown"),
                            "consecutive_busy": consecutive_busy,
                        },
                    )
                    delay = min(1.0, 0.05 * (2 ** min(consecutive_busy - 1, 5)))
                else:
                    consecutive_busy = 0
                    delay = 1.0
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                except TimeoutError:
                    pass

        tasks = [
            asyncio.create_task(loop(self.control.reconcile)),
            asyncio.create_task(loop(self.publisher.publish_ready)),
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            self._stop.set()
            await asyncio.gather(*tasks, return_exceptions=True)

    def is_ready(self) -> bool:
        return (
            self._supervisor is not None
            and not self._stop.is_set()
            and not self._supervisor.done()
        )
