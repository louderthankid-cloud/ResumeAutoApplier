import asyncio

from core.config import settings
from core.fair_pool import FairPool


class RunRegistry:
    def __init__(self) -> None:
        self._tasks: dict[int, asyncio.Task] = {}
        self._pool: FairPool | None = None

    def get_pool(self) -> FairPool:
        # ленивая инициализация — внутри работающего loop
        if self._pool is None:
            self._pool = FairPool(settings.PIPELINE_POOL_SIZE)
        return self._pool

    def is_running(self, tg_id: int) -> bool:
        task = self._tasks.get(tg_id)
        return task is not None and not task.done()

    def register(self, tg_id: int, task: asyncio.Task) -> None:
        self._tasks[tg_id] = task

    def request_stop(self, tg_id: int) -> bool:
        task = self._tasks.get(tg_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    def cleanup(self, tg_id: int) -> None:
        self._tasks.pop(tg_id, None)


registry = RunRegistry()
